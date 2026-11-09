"""Plan 5c Task 6: the export stage. The audited manuscript becomes pandoc markdown (``[[clm:]]`` → citeproc
citations of the items behind the claim, ``[[stat:]]``/``[[fig:]]`` → ``@tbl:``/``@fig:`` cross-references to the
tables and figures emitted in Results, ``[[run:]]``/``[[plan:]]`` → a reference to the Methods section, ``[[inf]]`` and
``[[ck]]`` removed), then ``paper.docx``, ``paper.tex`` and ``paper.pdf`` through pandoc + citeproc + tectonic, and a
reproducible package with its manifest. The pandoc/tectonic tests are marked ``binaries`` and skip when a binary (or
tectonic's cached package bundle) is missing; the conversion and the package are tested without them."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES
from rk.production import export as export_mod
from rk.production.artifacts import artifact
from rk.production.audit import AuditWorker, cited_items
from rk.production.draft import DraftWorker
from rk.production.export import (
    ExportWorker,
    build_package,
    check_csl,
    export,
    to_pandoc_markdown,
)
from rk.production.manuscript import latest_draft, write_draft
from rk.production.revise import ReviseWorker
from rk.production.sections import SectionSpec
from rk.production.store import add_extraction, extractions
from rk.production.synthesis import synthesis_claims
from rk.stats.effects import BINARY_FIELDS
from rk.stats.plots import forest_tikz
from rk.store.events import NewEvent
from rk.verify.stats import make_forest_artifact, make_stats_artifact
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_audit import _ctx as audit_ctx
from tests.test_audit import _judges, _passed
from tests.test_draft import _ready, writes
from tests.test_synthesis import Exec

MARKER = "rk-private-marker-q7Zx"  # in the project root's path: no packaged file may contain it
OUTPUTS = ("paper.docx", "paper.tex", "paper.pdf")


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http,
                                        **{"paper_type": "systematic_review", **getattr(request, "param", {})})
    yield k, ps, lib
    k.close()


@pytest.fixture
def marked(tmp_path, monkeypatch, replay_http):
    """A project whose root (and RK_HOME, with its config.toml) lies under a directory named ``MARKER``."""
    home = tmp_path / MARKER / "rkhome"
    home.mkdir(parents=True)
    monkeypatch.setenv("RK_HOME", str(home))
    k, ps, lib, _ = make_frozen_project(home, replay_http, paper_type="systematic_review")
    assert MARKER in str(ps.root) and (home / "config.toml").is_file()
    yield k, ps, lib
    k.close()


def _pool(env) -> tuple[str, str]:
    """A pooled RR (``stt``) and its forest plot (``fig``) of studies A and B, made from extraction rows of
    read_extract's passed attempt 1, as artifacts of synthesize's passed attempt 1."""
    _, ps, lib = env
    ids = [next(i.id for i in lib.list() if i.title == t) for t in ("A trial", "B trial")]
    rows = [{"id": add_extraction(ps, 1, iid, f, v, None, True, actor="tester")}
            for iid, vals in zip(ids, ((12, 100, 20, 100), (8, 80, 15, 82)), strict=True)
            for f, v in zip(BINARY_FIELDS, vals, strict=True)]
    stt = make_stats_artifact(ps, rows, "RR", item_ids=ids, stage="synthesize", attempt=1, actor="kernel")
    fig = make_forest_artifact(ps, rows, "RR", item_ids=ids, labels={ids[0]: "A 1950", ids[1]: "B 1951"},
                               stage="synthesize", attempt=1, actor="kernel")
    return stt, fig


def audited(env) -> tuple[str, str]:
    """argue passed; the pooled RR and its forest plot made; every section drafted (each ending with a sentence citing
    both), revised without questions; the audit passed, approved by the P1 vote. Returns ``(stt, fig)``."""
    k, ps, lib = env
    _ready(env)
    stt, fig = _pool(env)
    extra = f"The pooled risk ratio and its forest plot are shown [[stat:{stt}]] [[fig:{fig}]]."
    ex = Exec({("writer", "SectionDraft"): [writes(extra=extra)] * 10})
    out = DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft"), guidance=""))
    assert out.status == "done", out.reason
    assert DraftWorker().gate(ps, 1).ok, DraftWorker().gate(ps, 1).failures
    _passed(ps, "draft")
    ex = Exec({("simreader", "ReaderQuestions"): [{"questions": []}]})
    assert ReviseWorker().run(replace(production_ctx(k, ps, lib, ex, stage="revise"), guidance="")).status == "done"
    _passed(ps, "revise")
    out = AuditWorker().run(audit_ctx(env, _judges("approve")))
    assert out.status == "done", out.reason
    _passed(ps, "audit")
    return stt, fig


def _spec(sid: str, title: str) -> SectionSpec:
    return SectionSpec(id=sid, title=title, purpose="")


def _fake_render(out_dir: Path, refmap: dict) -> tuple[list[str], list[str]]:
    """Stands in for pandoc and tectonic: writes the three outputs (a docx is a zip, a pdf has a compressed stream)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document>A paper.</w:document>")
    (out_dir / "paper.docx").write_bytes(buf.getvalue())
    (out_dir / "paper.tex").write_text("\\documentclass{article}\\begin{document}A paper.\\end{document}\n")
    (out_dir / "paper.pdf").write_bytes(b"%PDF-1.5\n1 0 obj << /Filter /FlateDecode >>\nstream\n"
                                        + zlib.compress(b"BT (A paper.) Tj ET") + b"\nendstream\nendobj\n%%EOF\n")
    return [], []


def _texts(data: bytes) -> list[bytes]:
    """The bytes of a file and, for a zip (docx), of each member; for a PDF, of each Flate stream."""
    out = [data]
    if data.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            out += [z.read(n) for n in z.namelist()]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        try:
            out.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return out


def _binaries(tmp_path: Path) -> None:
    """Skip unless pandoc and tectonic are installed and tectonic's package bundle is cached (it fetches the bundle on
    first use; tests never use the network): a small document with the packages the export uses compiles offline."""
    pandoc, tectonic = shutil.which("pandoc"), shutil.which("tectonic")
    if pandoc is None or tectonic is None:
        pytest.skip(f"{'pandoc' if pandoc is None else 'tectonic'} is not installed")
    doc = tmp_path / "warm.md"
    doc.write_text("---\nheader-includes:\n  - |\n    ```{=latex}\n    \\usepackage{tikz}\n    \\usepackage{graphicx}\n"
                   "    ```\n---\n\nWarm [@k].\n\n```{=latex}\n\\begin{tikzpicture}\\draw (0,0) -- (1,1);"
                   "\\end{tikzpicture}\n```\n")
    r = subprocess.run([pandoc, str(doc), "-o", str(tmp_path / "warm.pdf"), f"--pdf-engine={tectonic}",
                        "--pdf-engine-opt=--only-cached"], capture_output=True, text=True, timeout=300, check=False)
    if r.returncode != 0:
        pytest.skip("tectonic's package bundle is not cached (tectonic fetches it on first use): warm it once with "
                    f"network, e.g. `pandoc x.md -o x.pdf --pdf-engine=tectonic` ({r.stderr.strip()[-200:]})")


# ---- conversion ----
def test_export_is_a_default_worker_without_a_back_edge():
    assert isinstance(default_workers().get("export"), ExportWorker)
    assert not [e for e in BACK_EDGES if e[0] == "export"]


def test_markers_convert_to_citations_and_cross_references(env):
    _, ps, _ = env
    _ready(env)
    stt, fig = _pool(env)
    f1 = synthesis_claims(ps, 1)[0]
    keys = list(cited_items(ps, [f1]))
    sections = [
        (_spec("introduction", "Introduction"), f"Aspirin was studied in older people [[clm:{f1}]].\n"),
        (_spec("methods", "Methods"), "We searched two sources [[run:evt_search]].\n"),
        (_spec("results", "Results"),
         f"Studies were found [[run:evt_search]] [[clm:{f1}]]. The pool is shown [[stat:{stt}]] [[fig:{fig}]].\n"),
        (_spec("discussion", "Discussion"), "The design follows [[plan:dsg_x]].\n"),
        (_spec("disclosure", "Disclosure"), "The search ran [[run:evt_search]].\n"),
    ]
    md, csl, refmap = to_pandoc_markdown(ps, sections, target="docx")
    assert "[[" not in md
    cite = "[" + "; ".join(f"@{k}" for k in keys) + "]"
    assert f"Aspirin was studied in older people {cite}." in md
    assert f"Studies were found {cite} ([Methods](#methods))." in md  # citations first, then the reference
    assert "We searched two sources." in md  # in Methods itself the reference to Methods is dropped
    assert "The design follows ([Methods](#methods))." in md
    assert "The search ran." in md  # the disclosure's sentences cite events, not Methods
    assert f"The pool is shown (@tbl:{stt}; @fig:{fig})." in md
    for sid, title in (("introduction", "Introduction"), ("methods", "Methods"), ("results", "Results")):
        assert f"# {title} {{#{sid}}}" in md
    # the table and the figure are emitted in Results, after its text and before the next section
    at, end = md.index("# Results {#results}"), md.index("# Discussion {#discussion}")
    assert at < md.index("The pool is shown") < md.index(f"{{#tbl:{stt}}}") < end
    assert at < md.index(f"{{#fig:{fig}}}") < end
    # the bibliography: the claim's items and the studies the table cites, each a CSL item keyed by its item id
    pooled = artifact(ps, stt)["values"]["item_ids"]
    assert [c["id"] for c in csl] == list(dict.fromkeys([*keys, *pooled]))
    assert all(c["title"] and c["issued"] and c["author"] for c in csl)
    assert refmap["claims"] == {f1: keys}
    assert refmap["tables"] == {stt: f"tbl:{stt}"} and refmap["figures"] == {fig: f"fig:{fig}"}
    assert set(refmap["keys"]) == {c["id"] for c in csl}
    # a paper without Results: the table and the figure follow the section that cites them first
    md, _, _ = to_pandoc_markdown(ps, [sections[0], (sections[2][0].model_copy(update={"id": "theme-1"}),
                                                     sections[2][1]), sections[3]], target="docx")
    at, end = md.index("{#theme-1}"), md.index("# Discussion {#discussion}")
    assert at < md.index(f"{{#tbl:{stt}}}") < end and at < md.index(f"{{#fig:{fig}}}") < end


def test_latex_and_docx_targets_carry_only_their_own_figure(env):
    _, ps, _ = env
    _ready(env)
    _, fig = _pool(env)
    sections = [(_spec("results", "Results"), f"The forest plot shows the pool [[fig:{fig}]].\n")]
    docx, _, _ = to_pandoc_markdown(ps, sections, target="docx")
    latex, _, _ = to_pandoc_markdown(ps, sections, target="latex")
    a = artifact(ps, fig)
    tikz = forest_tikz(a["values"])
    # docx: the stored SVG as an image, no LaTeX; latex: the TikZ picture as a raw block, no image
    assert f"](figures/{fig}.svg){{#fig:{fig}}}" in docx
    assert "tikzpicture" not in docx and "{=latex}" not in docx and "\\label" not in docx
    assert tikz.startswith("\\begin{tikzpicture}") and tikz in latex and "```{=latex}" in latex
    assert f"\\label{{fig:{fig}}}" in latex and ".svg" not in latex and "![" not in latex
    # the same study labels, estimates and intervals in both renderings
    svg = (ps.root / a["path"]).read_text(encoding="utf-8")
    texts = re.findall(r">([^<]+)</text>", svg)
    values = [t for t in texts if re.fullmatch(r"-?\d+\.\d\d \[-?\d+\.\d\d, -?\d+\.\d\d\]", t)]
    assert len(values) == len(a["values"]["studies"]) + 1, texts  # each study and the pooled row
    for v in values:
        assert v in tikz, v
    for label in a["values"]["labels"].values():
        assert label in texts and label in tikz, label
    pooled = next(t for t in texts if t.startswith("Pooled ("))
    assert pooled in tikz
    assert forest_tikz(a["values"]) == tikz  # deterministic


def test_inf_and_ck_markers_are_removed(env):
    _, ps, _ = env
    _ready(env)
    f1 = synthesis_claims(ps, 1)[0]
    body = ("This may suggest a benefit [[inf]]. Aspirin is a common drug [[ck]]. "
            f"It was studied [[inf]] [[clm:{f1}]].\n")
    md, csl, _ = to_pandoc_markdown(ps, [(_spec("discussion", "Discussion"), body)], target="latex")
    keys = "; ".join(f"@{k}" for k in cited_items(ps, [f1]))
    assert f"This may suggest a benefit. Aspirin is a common drug. It was studied [{keys}].\n" in md
    assert "[[" not in md and [c["id"] for c in csl] == list(cited_items(ps, [f1]))


def test_raw_output_in_a_section_is_refused(env):
    """Only the kernel's figures are raw LaTeX: a Writer's raw block or span (which the reader would pass to LaTeX,
    e.g. to read a local file into the PDF) is refused, naming the section; a backslash in prose stays text."""
    _, ps, _ = env
    for raw in ("```{=latex}\n\\input{/etc/hosts}\n```\n", "See `\\input{x}`{= latex} here [[ck]].\n"):
        with pytest.raises(ValueError, match="section discussion holds a raw"):
            to_pandoc_markdown(ps, [(_spec("discussion", "Discussion"), raw)], target="latex")
    md, _, _ = to_pandoc_markdown(ps, [(_spec("discussion", "Discussion"), "A \\textbf{b} c [[ck]].\n")],
                                  target="latex")
    assert "A \\textbf{b} c." in md  # the reader (markdown-raw_tex) keeps it text


def test_export_refuses_item_without_csl_metadata(env, monkeypatch):
    """Review Focus 4: an item with no year or no author is named before pandoc runs, never printed as "n.d."."""
    item = {"id": "src_a", "type": "article-journal", "title": "A trial", "author": [{"family": "A"}],
            "issued": {"date-parts": [[1950]]}}
    assert check_csl([item]) == []
    assert check_csl([{**item, "author": [], "editor": [{"family": "E"}]}]) == []
    problems = check_csl([{k: v for k, v in item.items() if k != "issued"},
                          {k: v for k, v in item.items() if k != "author"}, {**item, "title": " "}])
    assert len(problems) == 3 and all("src_a" in p for p in problems), problems
    assert "year" in problems[0] and "author" in problems[1] and "title" in problems[2]

    _, ps, lib = env
    audited(env)
    (a,) = [i for i in lib.list() if i.title == "A trial"]
    ps.apply(NewEvent(type="item.updated", entity_id=a.id, actor_kind="human", actor="tester",
                      payload={"fields": {"year": None}}))

    def no_pandoc(*args, **kwargs):
        raise AssertionError("pandoc ran")

    monkeypatch.setattr(export_mod, "_render", no_pandoc)
    res = export(ps)
    assert not res.ok
    (failure,) = res.failures
    assert a.id in failure and "A trial" in failure and "year" in failure and "before pandoc" in failure
    assert not any((ps.root / "artifacts/export" / f).exists() for f in OUTPUTS)


def test_export_refuses_a_manuscript_changed_since_the_audit_or_not_approved(env, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    assert "audit has not passed" in export(ps).failures[0]
    audited(env)
    assert export(ps).ok
    v, md = latest_draft(ps, "conclusion")
    write_draft(ps, "conclusion", md + "\nA new sentence.\n", stage="revise", attempt=2, round=1, by_task=None,
                actor="tester")
    res = export(ps)
    assert not res.ok and "manuscript changed since audit" in res.failures[0]
    assert f"conclusion v{v + 1}" in res.failures[0] and f"v{v}" in res.failures[0]


# ---- pandoc + citeproc + tectonic ----
@pytest.mark.binaries
def test_citekeys_round_trip_or_export_fails(env, tmp_path, monkeypatch):
    _binaries(tmp_path)
    _, ps, _ = env
    audited(env)
    res = export(ps)
    assert res.ok, res.failures
    md = res.files["paper.md"].read_text(encoding="utf-8")
    keys = set(re.findall(r"@(src_[A-Za-z0-9]+)", md))
    csl = json.loads(res.files["refs.json"].read_text(encoding="utf-8"))
    assert keys and keys == {c["id"] for c in csl}
    bib = res.files["refs.bib"].read_text(encoding="utf-8")
    assert set(re.findall(r"^@\w+\{(src_[A-Za-z0-9]+),", bib, re.MULTILINE)) == keys
    # a bibliography that lacks a cited item: citeproc's warning fails the export, naming the key
    convert = export_mod.to_pandoc_markdown

    def drop_first(*args, **kwargs):
        md, items, refmap = convert(*args, **kwargs)
        return md, items[1:], refmap

    monkeypatch.setattr(export_mod, "to_pandoc_markdown", drop_first)
    res = export(ps)
    assert not res.ok
    missing = sorted(keys - {c["id"] for c in json.loads(res.files["refs.json"].read_text(encoding="utf-8"))})
    assert len(missing) == 1 and any(missing[0] in f and "citeproc" in f.lower() for f in res.failures), res.failures


@pytest.mark.binaries
def test_pdf_docx_tex_are_produced(env, tmp_path):
    _binaries(tmp_path)
    _, ps, _ = env
    stt, fig = audited(env)
    res = export(ps)
    assert res.ok, res.failures
    assert set(res.files) >= {"paper.md", "paper-latex.md", "refs.json", "refs.bib", *OUTPUTS}
    for f in res.files.values():
        assert f.is_file() and f.stat().st_size > 0 and f.is_relative_to(ps.root / "artifacts/export"), f
    assert res.files["paper.pdf"].read_bytes().startswith(b"%PDF")
    tex = res.files["paper.tex"].read_text(encoding="utf-8")
    assert "\\begin{document}" in tex and "\\begin{tikzpicture}" in tex
    assert f"\\label{{fig:{fig}}}" in tex and f"Figure~\\ref{{fig:{fig}}}" in tex
    assert f"\\label{{tbl:{stt}}}" in tex and f"Table~\\ref{{tbl:{stt}}}" in tex
    assert "References" in tex and "[[" not in tex
    with zipfile.ZipFile(res.files["paper.docx"]) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        assert any(n.startswith("word/media/") and n.endswith(".svg") for n in z.namelist())
    plain = re.sub(r"<[^>]+>", "", doc)
    assert "Figure 1" in plain and "Table 1" in plain and "References" in plain
    assert "[[" not in plain and "@src_" not in plain and "@fig:" not in plain
    assert not [w for w in res.warnings if "citeproc" in w.lower()]
    # the real outputs hold no absolute path of this machine: they package
    assert build_package(ps).is_file()


# ---- the reproducible package ----
def test_package_manifest_hashes_match_and_contains_no_secrets_or_paths(marked, monkeypatch):
    _, ps, _ = marked
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    stt, fig = audited(marked)
    res = export(ps)
    assert res.ok, res.failures
    head = ps.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()["hash"]
    pkg = build_package(ps)
    assert pkg.parent == ps.root / "artifacts/export"
    with zipfile.ZipFile(pkg) as z:
        names = z.namelist()
        manifest_bytes = z.read("MANIFEST.json")
        manifest = json.loads(manifest_bytes)
        assert pkg.name == f"package-{hashlib.sha256(manifest_bytes).hexdigest()[:8]}.zip"
        assert manifest["event_log_head"] == head
        assert set(names) == {*manifest["files"], "MANIFEST.json"}
        for name, sha in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == sha, name
        files = {n: z.read(n) for n in names}
    assert {f"export/{n}" for n in ("paper.md", "paper-latex.md", "refs.json", "refs.bib", *OUTPUTS)} <= set(files)
    assert f"export/figures/{fig}.svg" in files
    assert {"disclosure.md", "protocol.json", "extraction.csv"} <= set(files)
    assert any(re.fullmatch(r"audit/report\.v\d+\.md", n) for n in files)
    assert any(re.fullmatch(r"audit/report\.v\d+\.json", n) for n in files)
    for aid in (stt, fig):
        meta = json.loads(files[f"artifacts/{aid}.record.json"])
        a = artifact(ps, aid)
        assert meta["code_version"] == a["code_version"] and meta["input_hashes"] == a["input_hashes"]
        assert hashlib.sha256(files[f"artifacts/{Path(a['path']).name}"]).hexdigest() == a["sha256"]
    protocol = json.loads(files["protocol.json"])
    assert protocol["body"]["question"] and protocol["frozen_hash"]
    rows = files["extraction.csv"].decode("utf-8").splitlines()
    assert rows[0].startswith("id,item_id,field,value") and len(rows) == 1 + len(extractions(ps, 1))
    # nothing personal or secret: no config, key or browser profile, no absolute path in any file's content
    assert not [n for n in names if re.search(r"config\.toml|key|browser|profile", n, re.IGNORECASE)]
    needles = {MARKER, str(ps.root), str(Path.home()), os.environ["RK_HOME"]}
    for name, data in files.items():
        for text in _texts(data):
            for needle in needles:
                assert needle.encode() not in text, (name, needle)
    # a rendered output that holds the project's path is refused, naming the file (also inside a docx or a PDF stream)
    for out, leak in (("paper.tex", str(ps.root).encode()),
                      ("paper.pdf", b"%PDF-1.5\nstream\n" + zlib.compress(str(ps.root).encode()) + b"\nendstream\n")):
        good = (ps.root / "artifacts/export" / out).read_bytes()
        (ps.root / "artifacts/export" / out).write_bytes(leak)
        with pytest.raises(ValueError, match=re.escape(out)):
            build_package(ps)
        (ps.root / "artifacts/export" / out).write_bytes(good)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", f"<w:t>{Path.home()}</w:t>")
    (ps.root / "artifacts/export/paper.docx").write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="paper.docx"):
        build_package(ps)


def test_export_worker_packages_and_gates_on_the_manifest(env, monkeypatch):
    k, ps, lib = env
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    audited(env)
    w = ExportWorker()
    ctx = replace(production_ctx(k, ps, lib, Exec({}), stage="export"), guidance="")
    out = w.run(ctx)
    assert out.status == "done", out.reason
    gate = w.gate(ps, 1)
    assert gate.ok, gate.failures
    (pkg,) = (ps.root / "artifacts/export").glob("package-*.zip")
    assert pkg.name in out.reason
    # a package whose file no longer matches its manifest fails the gate
    with zipfile.ZipFile(pkg) as z:
        members = {n: z.read(n) for n in z.namelist()}
    members["export/paper.md"] += b"\nchanged\n"
    with zipfile.ZipFile(pkg, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    gate = w.gate(ps, 1)
    assert not gate.ok and any("export/paper.md" in f for f in gate.failures), gate.failures
    pkg.unlink()
    assert not w.gate(ps, 1).ok
    # an export failure is a defect: failed with pandoc's error, no back-edge
    monkeypatch.setattr(export_mod, "_render", lambda out_dir, refmap: (["pandoc failed (exit 64): bad input"], []))
    out = w.run(replace(ctx, attempt=2))
    assert (out.status, out.to) == ("failed", None) and "bad input" in out.reason, (out.status, out.reason)
    assert w.degrade(ctx, ["x"]).status == "failed"
