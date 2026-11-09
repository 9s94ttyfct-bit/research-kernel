"""Plan 5c Task 6: the export stage. The audited manuscript becomes pandoc markdown (``[[clm:]]`` → citeproc
citations of the items behind the claim, ``[[stat:]]``/``[[fig:]]`` → ``@tbl:``/``@fig:`` cross-references to the
tables and figures emitted in Results, ``[[run:]]``/``[[plan:]]`` → a reference to the Methods section, ``[[inf]]`` and
``[[ck]]`` removed), then ``paper.docx``, ``paper.tex`` and ``paper.pdf`` through pandoc + citeproc + tectonic, and a
reproducible package with its manifest. The pandoc/tectonic tests are marked ``binaries`` and skip when a binary (or
tectonic's cached package bundle) is missing; the conversion and the package are tested without them."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import time
import unicodedata
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES
from rk.orchestrator.state import record_attempt
from rk.production import export as export_mod
from rk.production import figures
from rk.production.artifacts import artifact
from rk.production.audit import AuditWorker, cited_items
from rk.production.draft import DraftWorker, load_inputs
from rk.production.export import (
    ExportWorker,
    build_package,
    check_csl,
    export,
    to_pandoc_markdown,
)
from rk.production.figures import forest_tikz
from rk.production.manuscript import latest_draft, write_draft
from rk.production.revise import ReviseWorker
from rk.production.sections import SectionSpec
from rk.production.store import add_extraction, extractions
from rk.production.synthesis import synthesis_claims
from rk.stats import plots, version_files
from rk.stats.effects import BINARY_FIELDS
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


def audited(env, note: str = "") -> tuple[str, str]:
    """argue passed; the pooled RR and its forest plot made; every section drafted (each ending with a sentence citing
    both, with ``note`` before its markers), revised without questions; the audit passed, approved by the P1 vote.
    Returns ``(stt, fig)``."""
    k, ps, lib = env
    _ready(env)
    stt, fig = _pool(env)
    extra = f"The pooled risk ratio and its forest plot are shown{note} [[stat:{stt}]] [[fig:{fig}]]."
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


def _crafted(env, monkeypatch, sections: list, question: str | None = None) -> None:
    """The export reads ``sections`` (and ``question`` as the research question) as the audited manuscript."""
    _, ps, _ = env
    _ready(env)
    s = load_inputs(ps)
    if question is not None:
        s = replace(s, question=question)
    monkeypatch.setattr(export_mod, "audited_manuscript", lambda ps: (s, {}, sections))


def _escaped(s: str) -> str:
    """``s`` with markdown's markup characters backslash-escaped (the kernel's rule for free text)."""
    return "".join(f"\\{c}" if c in "\\`*_[]{}<>|@$~^#&" else c for c in s)


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
    # the same axis: the tick labels and the axis label, in order
    svg_ticks = re.findall(r'text-anchor="middle">([^<]+)</text>', svg)
    tikz_ticks = re.findall(r"\\node\[anchor=north\] at \([^)]*\) \{([^}]*)\}", tikz)
    assert len(svg_ticks) >= 3 and svg_ticks == tikz_ticks, (svg_ticks, tikz_ticks)


def test_the_tikz_rendering_lives_outside_the_stats_version():
    """``STATS_VERSION`` hashes ``rk/stats``: the export's rendering (it computes nothing) lives in
    ``rk.production.figures``, so adding it leaves the validated stats version and every artifact as they were."""
    assert not hasattr(plots, "forest_tikz")
    assert Path(figures.__file__).resolve() not in {p.resolve() for p in version_files()}


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


def test_raw_output_in_a_section_or_the_title_is_refused_or_escaped(env, monkeypatch):
    """Only the kernel's figures are raw LaTeX: a Writer's raw block or span (which the reader would pass to LaTeX,
    e.g. to read a local file into the PDF) is refused, naming the section; a backslash in prose stays text. The title
    (the research question, which an agent may have proposed) goes only into the kernel's metadata, every markup
    character escaped."""
    _, ps, _ = env
    for raw in ("```{=latex}\n\\input{/etc/hosts}\n```\n", "See `\\input{x}`{= latex} here [[ck]].\n"):
        with pytest.raises(ValueError, match="section discussion holds a raw"):
            to_pandoc_markdown(ps, [(_spec("discussion", "Discussion"), raw)], target="latex")
    md, _, _ = to_pandoc_markdown(ps, [(_spec("discussion", "Discussion"), "A \\textbf{b} c [[ck]].\n")],
                                  target="latex")
    assert "A \\textbf{b} c." in md  # the reader (markdown-raw_tex) keeps it text
    title = "Q `\\input{/etc/hosts}`{=latex} and $\\input{/etc/hosts}$ <b>x</b>"
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    _crafted(env, monkeypatch, [(_spec("discussion", "Discussion"), "Text [[ck]].\n")], question=title)
    res = export(ps)
    assert res.ok, res.failures
    out = ps.root / "artifacts/export"
    for name in ("paper.md", "paper-latex.md"):
        assert "input{" not in (out / name).read_text(encoding="utf-8"), name
    meta = json.loads((out / "metadata.yaml").read_text(encoding="utf-8"))  # JSON is YAML
    assert meta["title"] == _escaped(title)
    assert set(meta) == {"title", "lang", "reference-section-title", "link-citations"}


def test_the_ast_allow_list():
    """Before pandoc renders, its AST may hold no math, no raw output but the kernel's own figure blocks, no image but
    the kernel's figures and no metadata but the kernel's keys."""
    def para(*inlines):
        return {"t": "Para", "c": list(inlines)}

    def image(src):
        return {"t": "Image", "c": [["", [], []], [], [src, ""]]}

    math = {"t": "Math", "c": [{"t": "InlineMath"}, "\\input{x}"]}
    ast = {"pandoc-api-version": [1, 23],
           "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "T"}, math]},
                    "csl": {"t": "MetaInlines", "c": [{"t": "Str", "c": "/etc/x.csl"}]}},
           "blocks": [para(math), {"t": "RawBlock", "c": ["latex", "\\input{x}"]},
                      {"t": "RawBlock", "c": ["latex", "KERNEL FIGURE"]}, para(image("/etc/hosts")),
                      para(image("figures/fig_a.svg")), para({"t": "RawInline", "c": ["html", "<x>"]})]}
    problems = export_mod.ast_problems(ast, raw={"KERNEL FIGURE"}, images={"figures/fig_a.svg"})
    assert len(problems) == 6, problems
    assert sum("math" in p for p in problems) == 2 and sum("raw" in p for p in problems) == 2
    assert any("/etc/hosts" in p for p in problems) and any("csl" in p for p in problems)
    assert not any("fig_a" in p or "KERNEL" in p for p in problems)
    clean = {"meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "T"}]}},
             "blocks": [{"t": "RawBlock", "c": ["latex", "KERNEL FIGURE\n"]}, para(image("figures/fig_a.svg"))]}
    assert export_mod.ast_problems(clean, raw={"KERNEL FIGURE"}, images={"figures/fig_a.svg"}) == []
    # each kernel figure is inserted once: a second image of it (the Writer's copy) is refused
    twice = {**clean, "blocks": [*clean["blocks"], para(image("figures/fig_a.svg"))]}
    problems = export_mod.ast_problems(twice, raw={"KERNEL FIGURE"}, images={"figures/fig_a.svg"})
    assert len(problems) == 1 and "figures/fig_a.svg" in problems[0], problems
    # the latex source has no image at all: its figures are raw TikZ blocks
    assert export_mod.ast_problems(clean, raw={"KERNEL FIGURE"}, images=set()) != []


def test_the_ast_allow_list_takes_only_the_kernels_headings_and_no_note():
    """I1 of the final review (defence in depth): a heading is the kernel's section title (level 1, a section's id,
    each once) or, inside the Disclosure, one of its level-3 parts; a footnote (``Note``) is never allowed."""
    def head(level, ident, text="T"):
        return {"t": "Header", "c": [level, [ident, [], []], [{"t": "Str", "c": text}]]}

    def para(*inlines):
        return {"t": "Para", "c": list(inlines)}

    note = {"t": "Note", "c": [para({"t": "Str", "c": "Melatonin cures 90% of cancers"})]}
    ok = [head(1, "methods"), para({"t": "Str", "c": "x"}), head(1, "disclosure"), head(3, "models-used"),
          para({"t": "Str", "c": "y"})]
    assert export_mod.ast_problems({"blocks": ok}, raw=set(), images=set(), sections=["methods", "disclosure"]) == []
    bad = [head(1, "methods"), head(2, "melatonin-halves-mortality", "Melatonin halves mortality"),
           head(3, "x"), para({"t": "Str", "c": "x"}, note), head(1, "methods"), head(1, "intro"),
           {"t": "BlockQuote", "c": [head(1, "disclosure")]}, head(1, "disclosure")]
    problems = export_mod.ast_problems({"blocks": bad}, raw=set(), images=set(), sections=["methods", "disclosure"])
    assert sum("heading" in p for p in problems) == 5, problems
    assert sum("footnote" in p for p in problems) == 1 and any("Melatonin halves" in p for p in problems), problems


def test_tex_escape_and_figure_labels_collapse_whitespace(env, monkeypatch):
    """A library label with newlines cannot put a line of its own into the raw LaTeX block (so it can never close the
    raw fence): ``tex_escape`` collapses whitespace as ``_md`` does."""
    _, ps, _ = env
    assert figures.tex_escape("a\n\n```\n\\input{x}\t b") == "a ``` \\textbackslash{}input\\{x\\} b"
    _ready(env)
    _, fig = _pool(env)
    a = artifact(ps, fig)
    bad = json.loads(json.dumps(a["values"]))
    bad["labels"] = {k: f"{v}\n\n```\n\\input{{/etc/hosts}}\n```\n" for k, v in bad["labels"].items()}
    orig = export_mod.artifact
    monkeypatch.setattr(export_mod, "artifact", lambda ps, aid: {**a, "values": bad} if aid == fig else orig(ps, aid))
    latex, _, refmap = to_pandoc_markdown(ps, [(_spec("results", "Results"), f"Shown [[fig:{fig}]].\n")],
                                          target="latex")
    (raw,) = refmap["raw"]
    assert f"```{{=latex}}\n{raw}\n```" in latex
    assert not [line for line in raw.splitlines() if line.lstrip().startswith("```")], raw
    assert [line for line in latex.splitlines() if line.startswith("```")] == ["```{=latex}", "```"]  # the kernel's


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


def test_export_needs_the_audit_passed_now_and_removes_earlier_packages(env, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    audited(env)
    assert export(ps).ok
    first = build_package(ps)
    first.with_name("package-00000000.zip").write_bytes(b"an earlier package")
    assert export(ps).ok
    assert list((ps.root / "artifacts/export").glob("package-*.zip")) == []  # none looks current before packaging
    # an audit run again (here: a new attempt, still running) keeps its old passed attempt, which no longer counts
    record_attempt(ps, "audit", 2, run_id=None, guidance="", requested=None, actor="tester")
    res = export(ps)
    assert not res.ok and "audit has not passed" in res.failures[0], res.failures


def test_a_timed_out_command_is_killed_with_the_processes_it_started(tmp_path):
    r, err = export_mod._run(["sh", "-c", "sleep 30 & echo $! > child.pid; wait"], tmp_path, "sh", 1)
    assert r is None and "timed out" in err, err
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        pytest.fail(f"the child {pid} of the timed-out command still runs")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


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


SECRET = "SECRETLEAKTOKEN"


def _pdf_text(pdf: Path, tex: Path) -> str:
    """The PDF's text (``pdftotext``), else the LaTeX it was compiled from, NFKC-normalised and without whitespace
    (math italic letters typeset a word as 𝑆𝐸𝐶 … with spaces)."""
    tool = shutil.which("pdftotext")
    text = tex.read_text(encoding="utf-8") if tool is None else subprocess.run(
        [tool, str(pdf), "-"], capture_output=True, text=True, timeout=60, check=True).stdout
    return "".join(unicodedata.normalize("NFKC", text).split())


@pytest.mark.binaries
@pytest.mark.parametrize("case", ["math", "title", "image", "yaml"])
def test_writer_text_and_the_title_cannot_read_a_local_file_into_the_outputs(env, tmp_path, monkeypatch, case):
    """Critical 1 of the review: dollar math, a raw attribute or math in the title, an image path and a YAML metadata
    block in a section are rendered as inert text or refused; the file's content reaches no output."""
    _binaries(tmp_path)
    _, ps, _ = env
    secret = tmp_path / "secret.tex"
    secret.write_text(SECRET + "\n")
    body, question = "Plain text [[ck]].\n", None
    if case == "math":
        body = f"Hello $\\input{{{secret}}}$ and $$\\input{{{secret}}}$$ world [[ck]].\n"
    elif case == "title":
        question = f"Q `\\input{{{secret}}}`{{=latex}} and $\\input{{{secret}}}$"
    elif case == "image":
        body = f"See ![x]({secret}) here [[ck]].\n"
    else:
        body = (f"---\nheader-includes: '$\\input{{{secret}}}$'\nreference-section-title: $\\input{{{secret}}}$\n"
                f"csl: {secret}\n---\n\nPlain text [[ck]].\n")
    _crafted(env, monkeypatch, [(_spec("discussion", "Discussion"), body)], question=question)
    res = export(ps)
    out = ps.root / "artifacts/export"
    if case == "image":
        assert not res.ok and any("image" in f and str(secret) in f for f in res.failures), res.failures
        assert not any((out / n).exists() for n in OUTPUTS)
    else:
        assert res.ok, res.failures
        assert SECRET not in _pdf_text(out / "paper.pdf", out / "paper.tex")
    for name in OUTPUTS:
        if (out / name).is_file():
            for text in _texts((out / name).read_bytes()):
                assert SECRET.encode() not in text, name


@pytest.mark.binaries
def test_a_writer_image_is_refused_in_both_targets(env, tmp_path, monkeypatch):
    """An image the Writer wrote is refused even when it shows a kernel figure: the latex source holds no image (its
    figures are TikZ), and the docx keeps only the figure the kernel inserted itself."""
    _binaries(tmp_path)
    _, ps, _ = env
    _ready(env)
    _, fig = _pool(env)
    s = load_inputs(ps)
    body = f"The forest plot shows the pool [[fig:{fig}]].\n\n![A copy](figures/{fig}.svg)\n"
    monkeypatch.setattr(export_mod, "audited_manuscript", lambda ps: (s, {}, [(_spec("results", "Results"), body)]))
    res = export(ps)
    assert not res.ok
    for src in ("paper.md", "paper-latex.md"):
        assert any(f.startswith(f"{src}:") and f"figures/{fig}.svg" in f for f in res.failures), (src, res.failures)
    assert not any((ps.root / "artifacts/export" / n).exists() for n in OUTPUTS)


@pytest.mark.binaries
@pytest.mark.parametrize("case", ["heading", "footnote", "inline_note"])
def test_a_writer_heading_or_footnote_never_reaches_the_paper(env, tmp_path, monkeypatch, case):
    """I1 of the final review, end to end: the reviewer's probes (a Writer heading; a footnote defined as a reference
    definition; an inline note) are refused by pandoc's AST before anything is rendered."""
    _binaries(tmp_path)
    _, ps, _ = env
    body = {"heading": "## Melatonin halves mortality by 50% in all patients\n\nSleep matters for health [[ck]].\n",
            "footnote": "Sleep matters for health [[ck]].[^1]\n\n[^1]: <Melatonin cures 90% of cancers>\n",
            "inline_note": "Sleep matters for health [[ck]].^[Melatonin cures 90% of cancers.]\n"}[case]
    _crafted(env, monkeypatch, [(_spec("discussion", "Discussion"), body)])
    res = export(ps)
    assert not res.ok
    what = "heading" if case == "heading" else "footnote"
    for src in ("paper.md", "paper-latex.md"):
        assert any(f.startswith(f"{src}:") and what in f for f in res.failures), (src, res.failures)
    assert not any((ps.root / "artifacts/export" / n).exists() for n in OUTPUTS)


@pytest.mark.binaries
def test_html_looking_prose_is_text_in_the_export(env, tmp_path, monkeypatch):
    """The pandoc reader has ``-raw_html`` (as the audit's markdown-it has ``html: False``): HTML in the prose is
    text, never raw output, so it exports and is typeset as the characters written."""
    _binaries(tmp_path)
    _, ps, _ = env
    body = "Raw <b>bold</b> and <img src=x onerror=alert(1)> stay text [[ck]].\n"
    _crafted(env, monkeypatch, [(_spec("discussion", "Discussion"), body)])
    res = export(ps)
    assert res.ok, res.failures
    tex = res.files["paper.tex"].read_text(encoding="utf-8")
    assert "\\textbf{bold}" not in tex and "\\textless b\\textgreater bold\\textless/b\\textgreater" in tex
    assert "\\textless img src=x onerror=alert(1)\\textgreater" in tex


@pytest.mark.binaries
def test_a_path_written_in_a_section_is_refused_through_the_pdf_sources(marked, tmp_path):
    """Review Important 2: a real PDF's body text is not plain bytes, so a path typeset into it is caught in the
    sources the PDF is compiled from (``paper-latex.md``, ``paper.tex``, LaTeX-escaped there)."""
    _binaries(tmp_path)
    _, ps, _ = marked
    audited(marked, note=f" (stored under `{ps.root}`)")
    res = export(ps)
    assert res.ok, res.failures
    with pytest.raises(ValueError) as e:
        build_package(ps)
    for name in ("export/paper-latex.md", "export/paper.tex", "export/paper.md", "export/paper.docx"):
        assert name in str(e.value), (name, str(e.value))


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
    assert {f"export/{n}" for n in ("paper.md", "paper-latex.md", "metadata.yaml", "preamble.tex", "refs.json",
                                    "refs.bib", *OUTPUTS)} <= set(files)
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
    # a rendered output that holds the project's path is refused, naming the file (also inside a docx); a real PDF's
    # body text is checked through the sources it is compiled from (test_a_path_written_in_a_section_…)
    good = (ps.root / "artifacts/export/paper.tex").read_bytes()
    (ps.root / "artifacts/export/paper.tex").write_bytes(str(ps.root).encode())
    with pytest.raises(ValueError, match=re.escape("paper.tex")):
        build_package(ps)
    (ps.root / "artifacts/export/paper.tex").write_bytes(good)
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


def test_the_export_gate_fails_once_the_manuscript_changes_after_the_export(env, monkeypatch):
    k, ps, lib = env
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    audited(env)
    w = ExportWorker()
    assert w.run(replace(production_ctx(k, ps, lib, Exec({}), stage="export"), guidance="")).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    _, md = latest_draft(ps, "conclusion")
    write_draft(ps, "conclusion", md + "\nA new sentence.\n", stage="revise", attempt=2, round=1, by_task=None,
                actor="tester")
    gate = w.gate(ps, 1)
    assert not gate.ok and any("manuscript changed since audit" in f for f in gate.failures), gate.failures
