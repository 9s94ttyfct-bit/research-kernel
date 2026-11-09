"""``export`` (spec §7.1 export row; §9.1, the last paragraph of V-COVER; Plan 5c Task 6): the kernel turns the
manuscript the audit passed into ``paper.docx``, ``paper.tex`` and ``paper.pdf`` through pandoc + citeproc + tectonic,
and packs a reproducible package. No role task, no model.

1. **What is exported** (``audited_manuscript``): the latest version of every required section, which must be the
   version the passed audit attempt's latest report audited (``audit.reported``'s ``sections``); that report passed
   and was made after the final review approved the manuscript. A section changed since then is refused ("manuscript
   changed since audit"): run the audit again.
2. **Markers** (``to_pandoc_markdown``, per target, because the figure differs):
   - ``[[clm:x]]`` → ``[@key1; @key2]``: the distinct items behind claim x (``rk.production.audit.cited_items``: its
     evidence notes' items and the studies pooled into its artifacts); the key is the item id, the ``id`` that
     ``rk.library.formats.to_csl`` gives the item;
   - ``[[stat:]]``/``[[fig:]]`` → ``@tbl:<id>`` / ``@fig:<id>``: a forest plot is a figure, a pool or an evidence map
     a table. Each is emitted once, at the end of Results (where the paper type has no Results: of the section that
     cites it first). ``docx`` shows the stored SVG; ``latex`` the TikZ picture ``rk.stats.plots.forest_tikz`` draws
     from the same values, as a raw LaTeX block (no SVG converter is installed). A pool's table cites its studies;
   - ``[[run:]]``/``[[plan:]]`` → a reference to the Methods section (``[Methods](#methods)``; the Disclosure, which
     records what ran, where the paper type has no Methods), dropped inside that section itself and in the Disclosure
     (its sentences cite the events they report; ``disclosure.md`` in the package keeps the markers);
   - ``[[inf]]`` and ``[[ck]]`` are removed (the wording carries them). Adjacent markers make one citation and one
     parenthesised reference.
3. **Bibliography** (``check_csl``): every cited item needs a title, an issued year and an author or editor; one that
   lacks them is named before pandoc runs, never printed as "n.d." or "???" (Review Focus 4). ``refs.json`` is the
   items' CSL JSON (``to_csl``), ``refs.bib`` their BibTeX keyed by the same ids.
4. **Pandoc** (``_render``; argv lists, never a shell, in a temporary directory, each with a timeout): the reader is
   ``markdown-raw_tex`` (a backslash in the prose is text, only the kernel's raw ``{=latex}`` blocks are LaTeX; a
   section holding a raw ``{=format}`` block or span is refused before conversion); the
   kernel's ``crossref.lua`` filter resolves ``@fig:``/``@tbl:`` before ``--citeproc`` (pandoc-crossref is not
   installed). ``paper.md`` → ``paper.docx`` (``docx+native_numbering``, so the captions are numbered as the
   references); ``paper-latex.md`` → ``paper.tex`` (standalone) and ``paper.pdf`` (``--pdf-engine=tectonic``,
   ``--untrusted``). The round trip: for each markdown, the citation keys pandoc reads equal the bibliography
   entries citeproc rendered, and a citeproc or crossref warning on stderr is a failure; other warnings (e.g. no
   PNG fallback for the SVG in Word) are reported.
5. **Package** (``build_package``): ``artifacts/export/package-<sha8>.zip`` with the exports (checked to be the export
   of the audited manuscript), ``disclosure.md``, the audit report, the frozen protocol (JSON), read_extract's
   extraction table (CSV), every cited artifact (its file and its record: code path and version, input hashes,
   values) and ``MANIFEST.json`` (sha256 per file, the event log's head hash; ``sha8`` is its own hash). It is built
   from that list only, so ``config.toml``, keys and the browser profile are never in it, and it is refused when any
   file's content (a docx's parts and a PDF's streams included) holds the project root, the home directory, RK_HOME
   or the temporary directory.

The worker has no back-edge: an export failure is a defect, reported as ``failed`` with pandoc's error. Its gate: the
attempt's package exists, its files match its manifest, and the manuscript is still the audited one."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.canon import canonical_json, sha256_hex
from rk.config import rk_home
from rk.library.formats import export_bibtex, to_csl
from rk.library.service import Library
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import stage_row
from rk.production.artifacts import artifact
from rk.production.audit import audit_notes, audit_reports, cited_items
from rk.production.disclosure import DISCLOSURE_PATH
from rk.production.disclosure import SECTION as DISCLOSURE
from rk.production.draft import DraftInputs, latest_version, load_inputs
from rk.production.manuscript import Marker, source_markers
from rk.production.sections import SectionSpec
from rk.production.store import extractions, frozen_protocol
from rk.stats.meta import Pooled
from rk.stats.plots import forest_tikz, interval_rows, pooled_name, tex_escape
from rk.store.events import NewEvent
from rk.store.project import ProjectStore
from rk.verify.stats import POOL

STAGE = "export"
EXPORT_DIR = "artifacts/export"
FIGURES = "figures"
BUILT = "export.built"
TARGETS = ("latex", "docx")
SOURCES = ("paper.md", "paper-latex.md", "refs.json", "refs.bib")
OUTPUTS = ("paper.docx", "paper.tex", "paper.pdf")
FOREST = "rk.stats.plots:forest_svg"
MAP_TABLES = ("rk.stats.plots:map_table", "rk.verify.stats:map_markdown")
READER = "markdown-raw_tex"
FILTER = Path(__file__).with_name("crossref.lua")
TIMEOUT, PDF_TIMEOUT = 120, 600  # seconds; tectonic may fetch its package bundle on first use
METHODS = "methods"
MANIFEST = "MANIFEST.json"
_MARKUP = frozenset("\\`*_[]{}<>|@$~^#&")
_RAW = re.compile(r"\{\s*=\s*[A-Za-z]")  # a raw attribute: ```{=latex} or `...`{=latex}
_LATEX_PREAMBLE = ("header-includes:\n  - |\n    ```{=latex}\n    \\usepackage{tikz}\n    \\usepackage{graphicx}\n"
                   "    ```\n")


class ExportResult(BaseModel):
    """``files``: what the export wrote (name → path under ``artifacts/export``); ``failures``: why it is not ``ok``
    (pandoc's error, a missed round trip, an item without CSL metadata); ``warnings``: what pandoc reported besides."""
    files: dict[str, Path]
    warnings: list[str]
    failures: list[str]
    ok: bool


# ---- what is exported ----
def audited_manuscript(ps: ProjectStore) -> tuple[DraftInputs, dict, list[tuple[SectionSpec, str]]] | str:
    """The manuscript the passed audit attempt audited: the draft inputs, the attempt's latest report and every
    required section with its markdown; else why it cannot be exported."""
    row = stage_row(ps, "audit")
    attempt = row["passed_attempt"] if row is not None and row["passed_attempt"] else None
    if attempt is None:
        return ("audit has not passed: only a manuscript that passed the audit and that the final review approved is "
                "exported")
    mine = [r for r in audit_reports(ps) if r["attempt"] == attempt]
    if not mine:
        return f"audit's passed attempt {attempt} has no audit report"
    rep = mine[-1]
    if not (rep["ok"] and rep["approved"] and audit_notes(ps, attempt, kind="approved")):
        return (f"the audit report v{rep['version']} of audit's passed attempt {attempt} did not pass after the final "
                "review approved the manuscript")
    s = load_inputs(ps)
    if isinstance(s, str):
        return f"the manuscript cannot be read: {s}"
    out, changed = [], []
    for spec in s.sections:
        got = latest_version(ps, spec.id)
        if isinstance(got, str):
            return got
        seen = rep["sections"].get(spec.id)
        if got[0]["version"] != seen:
            changed.append(f"section {spec.id} v{got[0]['version']} (the audit report v{rep['version']} audited "
                           + (f"v{seen})" if seen is not None else "no version of it)"))
        out.append((spec, got[1]))
    if changed:
        return "manuscript changed since audit: " + "; ".join(changed) + "; run the audit again"
    return s, rep, out


# ---- markdown ----
def _md(s: str) -> str:
    """Free text as pandoc markdown text, on one line: the characters markdown reads as markup (emphasis, code,
    links, attributes, raw HTML, tables, citations, math, entities) backslash-escaped."""
    return "".join(f"\\{c}" if c in _MARKUP else c for c in " ".join(str(s).split()))


def _cell(s: str) -> str:
    return _md(s) or " "


def _pooled_name(values: dict) -> str:
    return pooled_name(Pooled(**values["pooled"]))


def _groups(md: str, markers: list[Marker]) -> list[list[Marker]]:
    """Markers next to each other (only spaces between them) in groups, in order."""
    out: list[list[Marker]] = []
    for m in markers:
        if out and md[out[-1][-1].span[1]:m.span[0]].strip(" \t") == "":
            out[-1].append(m)
        else:
            out.append([m])
    return out


class _Paper:
    """One conversion: the citation keys, the cross-references and where each table and figure goes."""

    def __init__(self, ps: ProjectStore, sections: list[tuple[SectionSpec, str]], target: str):
        self.ps, self.target, self.lib = ps, target, Library(ps)
        ids = [spec.id for spec, _ in sections]
        self.results = "results" if "results" in ids else None
        ref = next((spec for spec, _ in sections if spec.id == METHODS), None) or \
            next((spec for spec, _ in sections if spec.id == DISCLOSURE), None)
        self.ref = ref
        self.keys: list[str] = []
        self.claims: dict[str, list[str]] = {}
        self.xrefs: dict[str, str] = {}  # artifact id -> "fig:<id>" / "tbl:<id>"
        self.placed: dict[str, list[str]] = {}  # section id -> the artifacts emitted at its end

    def _key(self, item_id: str) -> str:
        if item_id not in self.keys:
            self.keys.append(item_id)
        return item_id

    def _claim(self, cid: str) -> list[str]:
        if cid not in self.claims:
            self.claims[cid] = [self._key(i) for i in cited_items(self.ps, [cid])]
        return self.claims[cid]

    def _xref(self, aid: str, kind: str, section: str) -> str:
        if aid not in self.xrefs:
            a = artifact(self.ps, aid)
            if a is None:
                raise ValueError(f"[[{kind}:{aid}]] in section {section} is no artifact")
            self.xrefs[aid] = f"{'fig' if a['code_path'] == FOREST else 'tbl'}:{aid}"
            self.placed.setdefault(self.results or section, []).append(aid)
        return self.xrefs[aid]

    def section(self, spec: SectionSpec, md: str) -> str:
        if _RAW.search(md):  # the reader keeps raw {=format} blocks and spans: only the kernel's figures may use them
            raise ValueError(f"section {spec.id} holds a raw {{=format}} block or span; raw output (LaTeX, HTML) in "
                             "the paper is the kernel's alone")
        # a reference to Methods is dropped in Methods itself and in the Disclosure, whose sentences cite the events
        # they report (``disclosure.md`` in the package keeps them)
        link = (f"[{_md(self.ref.title)}](#{self.ref.id})"
                if self.ref is not None and spec.id not in (self.ref.id, DISCLOSURE) else None)
        out, pos = [], 0
        for g in _groups(md, source_markers(md)):
            start, end = g[0].span[0], g[-1].span[1]
            while start > pos and md[start - 1] in " \t":
                start -= 1
            cites, refs, ref = [], [], False
            for m in g:
                if m.kind == "clm":
                    cites += [k for k in self._claim(m.target) if k not in cites]
                elif m.kind in ("stat", "fig"):
                    if (x := "@" + self._xref(m.target, m.kind, spec.id)) not in refs:
                        refs.append(x)
                elif m.kind in ("run", "plan"):
                    ref = True
            if ref and link:
                refs.append(link)
            parts = ([f"[{'; '.join('@' + k for k in cites)}]"] if cites else []) + \
                ([f"({'; '.join(refs)})"] if refs else [])
            out += [md[pos:start], (" " + " ".join(parts)) if parts else ""]
            pos = end
        out.append(md[pos:])
        return "".join(out).strip("\n")

    # the tables and figures
    def block(self, aid: str) -> str:
        a = artifact(self.ps, aid)
        v, xref = a["values"], self.xrefs[aid]
        if a["code_path"] == FOREST:
            return self._figure(aid, v, xref)
        if a["code_path"] == POOL:
            return self._pool_table(v, xref)
        if a["code_path"] in MAP_TABLES:
            return self._map_table(v, xref)
        raise ValueError(f"artifact {aid} ({a['code_path']}) has no table or figure form")

    def _figure(self, aid: str, v: dict, xref: str) -> str:
        p = Pooled(**v["pooled"]) if v.get("pooled") else None
        pooled = (f", and the {p.model}{' (HKSJ)' if p.hksj else ''} pooled estimate over {p.k} studies"
                  if p is not None else "")
        caption = f"Forest plot of the {v['measure']}: each study's estimate with its 95% CI{pooled}."
        if self.target == "docx":
            return f"![{_md(caption)}]({FIGURES}/{aid}.svg){{#{xref}}}"
        return ("```{=latex}\n\\begin{figure}[htbp]\n\\centering\n\\resizebox{\\linewidth}{!}{%\n"
                f"{forest_tikz(v)}}}\n\\caption{{{tex_escape(caption)}}}\n\\label{{{xref}}}\n\\end{{figure}}\n```")

    def _pool_table(self, v: dict, xref: str) -> str:
        lines = [f"| Study | {_md(v['measure'])} \\[95% CI\\] |", "|---|---|"]
        for item_id, text in interval_rows(v):
            label = f"@{self._key(item_id)}" if item_id is not None else _md(_pooled_name(v))
            lines.append(f"| {label} | {_cell(text)} |")
        lines += [f"| @{self._key(u['item_id'])} | {_cell('not computable: ' + u['reason'])} |"
                  for u in v.get("uncomputable") or []]
        d = v.get("display") or {}
        het = f"; I² {d['i2_percent']:.0f}%" if "i2_percent" in d else ""
        caption = f"Pooled {v['measure']}: each study's estimate with its 95% CI, then the pooled estimate{het}."
        return "\n".join(lines) + f"\n\n: {_md(caption)} {{#{xref}}}"

    @staticmethod
    def _map_table(v: dict, xref: str) -> str:
        t = v["table"]
        lines = ["| Field | Category | Studies |", "|---|---|---|"]
        for field, counts in t["fields"].items():
            lines += [f"| {_cell(field)} | {_cell(c)} | {n} |" for c, n in counts.items()]
        cross = t.get("cross")
        if cross:
            pair = f"{cross['rows']} × {cross['cols']}"
            lines += [f"| {_cell(pair)} | {_cell(f'{r} × {c}')} | {n} |"
                      for r, counts in cross["counts"].items() for c, n in counts.items()]
        caption = f"Evidence map of {t['n']} studies: the number of studies in each category."
        return "\n".join(lines) + f"\n\n: {_md(caption)} {{#{xref}}}"


def to_pandoc_markdown(ps: ProjectStore, sections: list[tuple[SectionSpec, str]], *,
                       target: Literal["latex", "docx"]) -> tuple[str, list[dict], dict]:
    """The manuscript ``sections`` (each spec with its markdown, in order) as one pandoc markdown document for
    ``target`` (see the module docstring), its bibliography as CSL JSON items (keyed by item id, in order of first
    citation) and ``refmap``: ``claims`` (claim id → its citation keys), ``figures`` / ``tables`` (artifact id → its
    cross-reference id) and ``keys`` (every citation key). ``ValueError`` names a marker without its artifact."""
    if target not in TARGETS:
        raise ValueError(f"unknown export target {target!r}; expected one of {', '.join(TARGETS)}")
    paper = _Paper(ps, sections, target)
    bodies = [(spec, paper.section(spec, md)) for spec, md in sections]
    s = load_inputs(ps)
    head = ["---"]
    if not isinstance(s, str):
        head.append(f"title: {json.dumps(s.question)}")
    head += ["lang: en", "reference-section-title: References", "link-citations: true"]
    if target == "latex":
        head.append(_LATEX_PREAMBLE.rstrip("\n"))
    out = ["\n".join([*head, "---"])]
    for spec, body in bodies:
        out.append(f"# {_md(spec.title)} {{#{spec.id}}}")
        if body.strip():
            out.append(body)
        out += [paper.block(aid) for aid in paper.placed.get(spec.id, [])]
    markdown = "\n\n".join(out) + "\n"
    items = [paper.lib.get(k) for k in paper.keys]
    refmap = {"claims": paper.claims,
              "figures": {a: x for a, x in paper.xrefs.items() if x.startswith("fig:")},
              "tables": {a: x for a, x in paper.xrefs.items() if x.startswith("tbl:")},
              "keys": list(paper.keys)}
    return markdown, [to_csl(i) for i in items], refmap


def check_csl(items: list[dict]) -> list[str]:
    """What keeps each CSL item from rendering in full (Review Focus 4): no title, no issued year, no author or
    editor; each problem names the item."""
    out = []
    for c in items:
        title = " ".join(str(c.get("title") or "").split())
        name = f"item {c.get('id')} “{title}”"
        if not title:
            out.append(f"{name}: no title")
        parts = (c.get("issued") or {}).get("date-parts") or []
        if not (parts and parts[0] and str(parts[0][0]).strip()):
            out.append(f"{name}: no issued year")
        people = [*(c.get("author") or []), *(c.get("editor") or [])]
        if not any(str(p.get("family") or p.get("literal") or "").strip() for p in people):
            out.append(f"{name}: no author or editor")
    return out


# ---- pandoc ----
def _clean(text: str, work: Path) -> str:
    """Pandoc's stderr without the temporary directory's path, trimmed to its last 2000 characters."""
    for p in {str(work.resolve()), str(work)}:
        text = text.replace(p, ".")
    text = text.strip()
    return text if len(text) <= 2000 else "…" + text[-2000:]


def _run(argv: list[str], work: Path, what: str, timeout: int) -> tuple[subprocess.CompletedProcess | None, str | None]:
    try:
        r = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"{what} timed out after {timeout} s"
    except OSError as e:
        return None, f"{what} could not run: {e.strerror or e}"
    if r.returncode != 0:
        return r, f"{what} failed (exit {r.returncode}): {_clean(r.stderr, work) or 'no error output'}"
    return r, None


def _warnings(stderr: str, what: str, work: Path) -> tuple[list[str], list[str]]:
    """Pandoc's ``[WARNING]`` lines: citeproc's and the crossref filter's are failures, the others warnings."""
    fails, warns = [], []
    for line in _clean(stderr, work).splitlines():
        if line.startswith("[WARNING]"):
            text = f"{what}: {line.removeprefix('[WARNING]').strip()}"
            (fails if line.startswith(("[WARNING] Citeproc", "[WARNING] crossref")) else warns).append(text)
    return fails, warns


def _walk(node, found) -> None:
    if isinstance(node, dict):
        found(node)
        for v in node.values():
            _walk(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, found)


def _cited(ast: dict) -> set[str]:
    """The bibliographic citation keys of a pandoc AST (not the ``fig:``/``tbl:`` cross-references)."""
    keys: set[str] = set()

    def found(n: dict) -> None:
        if n.get("t") == "Cite":
            keys.update(c["citationId"] for c in n["c"][0] if not c["citationId"].startswith(("fig:", "tbl:")))
    _walk(ast, found)
    return keys


def _rendered(ast: dict) -> set[str]:
    """The keys of the bibliography entries citeproc rendered (the ``ref-<key>`` divs of its ``refs`` div)."""
    keys: set[str] = set()

    def found(n: dict) -> None:
        if n.get("t") == "Div" and n["c"][0][0] == "refs":
            keys.update(b["c"][0][0].removeprefix("ref-") for b in n["c"][1]
                        if b.get("t") == "Div" and b["c"][0][0].startswith("ref-"))
    _walk(ast, found)
    return keys


def _render(out_dir: Path, refmap: dict) -> tuple[list[str], list[str]]:
    """Run pandoc on the export's sources in ``out_dir`` (in a temporary directory) and copy ``paper.docx``,
    ``paper.tex`` and ``paper.pdf`` back; the failures (pandoc's error, a missed round trip) and the warnings."""
    pandoc, tectonic = shutil.which("pandoc"), shutil.which("tectonic")
    if pandoc is None or tectonic is None:
        missing = "pandoc" if pandoc is None else "tectonic"
        return [f"{missing} is not installed (not found on PATH): the export needs pandoc 3 and tectonic"], []
    failures: list[str] = []
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rk-export-") as d:
        work = Path(d)
        for name in ("paper.md", "paper-latex.md", "refs.json"):
            shutil.copyfile(out_dir / name, work / name)
        if (out_dir / FIGURES).is_dir():
            shutil.copytree(out_dir / FIGURES, work / FIGURES)
        shutil.copyfile(FILTER, work / FILTER.name)
        opts = ["--from", READER, "--lua-filter", FILTER.name, "--citeproc", "--bibliography", "refs.json"]
        # the round trip: the keys pandoc reads are the entries citeproc renders, without a warning
        for src in ("paper.md", "paper-latex.md"):
            plain, err = _run([pandoc, src, "--from", READER, "--to", "json"], work, f"pandoc ({src} → AST)", TIMEOUT)
            full, err2 = _run([pandoc, src, *opts, "--to", "json"], work, f"pandoc ({src} → citeproc)", TIMEOUT)
            if err or err2:
                failures += [e for e in (err, err2) if e]
                continue
            f, w = _warnings(full.stderr, f"{src} (citeproc)", work)
            failures += f
            warnings += w
            cited, rendered = _cited(json.loads(plain.stdout)), _rendered(json.loads(full.stdout))
            if cited != rendered or cited != set(refmap["keys"]):
                failures.append(f"{src}: the citation keys do not round-trip: cited {sorted(cited)}, rendered by "
                                f"citeproc {sorted(rendered)}, in refs.json {sorted(refmap['keys'])}")
        if failures:
            return failures, warnings
        steps = [("paper.docx", [pandoc, "paper.md", *opts, "--to", "docx+native_numbering", "-o", "paper.docx"],
                  TIMEOUT),
                 ("paper.tex", [pandoc, "paper-latex.md", *opts, "--standalone", "-o", "paper.tex"], TIMEOUT),
                 ("paper.pdf", [pandoc, "paper-latex.md", *opts, f"--pdf-engine={tectonic}",
                                "--pdf-engine-opt=--untrusted", "-o", "paper.pdf"], PDF_TIMEOUT)]
        for name, argv, timeout in steps:
            r, err = _run(argv, work, f"pandoc ({name})", timeout)
            if err:
                failures.append(err)
                continue
            f, w = _warnings(r.stderr, name, work)
            failures += f
            warnings += w
            if not (work / name).is_file():
                failures.append(f"pandoc ({name}) wrote no {name}")
                continue
            tmp = out_dir / f".{name}.tmp"
            shutil.copyfile(work / name, tmp)
            os.replace(tmp, out_dir / name)
    return failures, list(dict.fromkeys(warnings))


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _refs_json(csl: list[dict]) -> str:
    return json.dumps(csl, ensure_ascii=False, indent=2) + "\n"


def _artifact_file(ps: ProjectStore, aid: str) -> bytes:
    a = artifact(ps, aid)
    try:
        data = (ps.root / a["path"]).read_bytes()
    except OSError as e:
        raise ValueError(f"the file of artifact {aid} ({a['path']}) cannot be read: {e.strerror or e}") from e
    if sha256_hex(data) != a["sha256"]:
        raise ValueError(f"the file of artifact {aid} ({a['path']}) no longer has its recorded sha256")
    return data


def _failed(why: str) -> ExportResult:
    return ExportResult(files={}, warnings=[], failures=[why], ok=False)


def export(ps: ProjectStore) -> ExportResult:
    """Export the audited manuscript (see the module docstring): ``artifacts/export/`` gets ``paper.md`` (the docx
    target, the human-readable markdown), ``paper-latex.md``, ``refs.json``, ``refs.bib``, the figures' SVGs and pandoc's
    ``paper.docx``, ``paper.tex`` and ``paper.pdf``. An earlier export's files are removed first, so a failed export
    leaves none that look current."""
    out_dir = ps.root / EXPORT_DIR
    for name in (*SOURCES, *OUTPUTS):
        (out_dir / name).unlink(missing_ok=True)
    shutil.rmtree(out_dir / FIGURES, ignore_errors=True)
    got = audited_manuscript(ps)
    if isinstance(got, str):
        return _failed(got)
    sections = got[2]
    try:
        md, csl, refmap = to_pandoc_markdown(ps, sections, target="docx")
        latex, _, _ = to_pandoc_markdown(ps, sections, target="latex")
        svgs = {aid: _artifact_file(ps, aid) for aid in refmap["figures"]}
    except ValueError as e:
        return _failed(f"the manuscript cannot be converted: {e}")
    if problems := check_csl(csl):
        return _failed("the bibliography cannot be rendered without inventing metadata (checked before pandoc ran): "
                       + "; ".join(problems))
    lib = Library(ps)
    files = {"paper.md": md.encode(), "paper-latex.md": latex.encode(), "refs.json": _refs_json(csl).encode(),
             "refs.bib": export_bibtex([lib.get(k) for k in refmap["keys"]], keys=refmap["keys"]).encode(),
             **{f"{FIGURES}/{aid}.svg": data for aid, data in svgs.items()}}
    for name, data in files.items():
        _write(out_dir / name, data)
    failures, warnings = _render(out_dir, refmap)
    written = {name: out_dir / name for name in (*files, *OUTPUTS) if (out_dir / name).is_file()}
    missing = [n for n in OUTPUTS if n not in written]
    if missing and not failures:
        failures.append(f"pandoc wrote no {', '.join(missing)}")
    return ExportResult(files=written, warnings=warnings, failures=failures, ok=not failures)


# ---- the package ----
def _contents(data: bytes) -> list[bytes]:
    """A file's bytes and, for a zip (a docx), each part's; for a PDF, each stream inflated where it is compressed."""
    out = [data]
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                out += [z.read(n) for n in z.namelist()]
        except (zipfile.BadZipFile, OSError):
            pass
    if data.startswith(b"%PDF"):
        at = 0
        while (start := data.find(b"stream", at)) != -1:
            body = start + len(b"stream")
            body += 2 if data[body:body + 2] == b"\r\n" else 1
            end = data.find(b"endstream", body)
            if end == -1:
                break
            try:
                out.append(zlib.decompressobj().decompress(data[body:end]))
            except zlib.error:
                pass
            at = end + len(b"endstream")
    return out


def _needles(ps: ProjectStore) -> list[str]:
    """The absolute paths no packaged file may contain: the project root, the home directory, RK_HOME and the
    temporary directory (not a bare system one)."""
    paths = {ps.root, ps.root.resolve(), Path.home(), rk_home(), rk_home().resolve()}
    tmp = Path(tempfile.gettempdir())
    if str(tmp) not in ("/tmp", "/var/tmp"):
        paths |= {tmp, tmp.resolve()}
    return sorted({str(p) for p in paths if len(str(p)) > 1})


def _leaks(files: dict[str, bytes], needles: list[str]) -> list[str]:
    forms = [form for n in needles for form in (n.encode(), n.encode("utf-16-be"), json.dumps(n)[1:-1].encode())]
    return [name for name, data in files.items() if any(f in text for text in _contents(data) for f in forms)]


def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "item_id", "field", "value", "note_id", "sufficient", "problem", "attempt"])
    for r in rows:
        w.writerow([r["id"], r["item_id"], r["field"], canonical_json(r["value"]), r["note_id"] or "",
                    "yes" if r["sufficient"] else "low", r["problem"] or "", r["attempt"]])
    return buf.getvalue().encode()


def _cited_artifacts(ps: ProjectStore, sections: list[tuple[SectionSpec, str]]) -> list[str]:
    """The artifacts the manuscript cites: its ``stat``/``fig`` markers and the artifacts of its cited claims."""
    markers = [m for _, md in sections for m in source_markers(md) if m.target]
    out = [m.target for m in markers if m.kind in ("stat", "fig")]
    svc = ArgumentService(ps)
    for cid in dict.fromkeys(m.target for m in markers if m.kind == "clm"):
        out += svc.get_claim(cid).get("artifact_ids") or []
    return list(dict.fromkeys(out))


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in [MANIFEST, *sorted(n for n in files if n != MANIFEST)]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def build_package(ps: ProjectStore) -> Path:
    """``artifacts/export/package-<sha8>.zip`` (see the module docstring); ``ValueError`` names what is missing, stale
    or would leak an absolute path."""
    got = audited_manuscript(ps)
    if isinstance(got, str):
        raise ValueError(got)  # noqa: TRY004 - the reason it cannot be packaged, not a wrong argument type
    s, rep, sections = got
    out_dir = ps.root / EXPORT_DIR
    md, csl, refmap = to_pandoc_markdown(ps, sections, target="docx")
    latex, _, _ = to_pandoc_markdown(ps, sections, target="latex")
    expected = {"paper.md": md, "paper-latex.md": latex, "refs.json": _refs_json(csl)}
    files: dict[str, bytes] = {}
    for name in (*SOURCES, *OUTPUTS):
        try:
            data = (out_dir / name).read_bytes()
        except OSError as e:
            raise ValueError(f"the export has no readable {name} ({e.strerror or e}): export first") from e
        if name in expected and data != expected[name].encode():
            raise ValueError(f"{name} is not the export of the audited manuscript: export again")
        files[f"export/{name}"] = data
    for aid in refmap["figures"]:
        data = (out_dir / FIGURES / f"{aid}.svg").read_bytes() if (out_dir / FIGURES / f"{aid}.svg").is_file() else b""
        if data != _artifact_file(ps, aid):
            raise ValueError(f"the export's {FIGURES}/{aid}.svg is not the figure's file: export again")
        files[f"export/{FIGURES}/{aid}.svg"] = data
    try:
        files["disclosure.md"] = (ps.root / DISCLOSURE_PATH).read_bytes()
        for key, path in (("md", rep["md_path"]), ("json", rep["json_path"])):
            data = (ps.root / path).read_bytes()
            if sha256_hex(data) != rep["sha256"][key]:
                raise ValueError(f"the audit report {path} no longer has its recorded sha256")
            files[f"audit/{Path(path).name}"] = data
    except OSError as e:
        raise ValueError(f"a file of the audit cannot be read: {e.strerror or e}") from e
    fp = frozen_protocol(ps)
    if fp is None:
        raise ValueError("there is no frozen protocol to package")
    files["protocol.json"] = (json.dumps({k: fp[k] for k in ("id", "version", "frozen_hash", "frozen_at", "body")},
                                         ensure_ascii=False, indent=2) + "\n").encode()
    row = stage_row(ps, "read_extract")
    files["extraction.csv"] = _csv(extractions(ps, row["passed_attempt"]) if row and row["passed_attempt"] else [])
    for aid in _cited_artifacts(ps, sections):
        a = artifact(ps, aid)
        if a is None:
            raise ValueError(f"the cited artifact {aid} does not exist")
        name = Path(a["path"]).name
        files[f"artifacts/{name}"] = _artifact_file(ps, aid)
        record = {k: a[k] for k in ("id", "kind", "stage", "attempt", "code_path", "code_version", "input_hashes",
                                    "values", "sha256")}
        files[f"artifacts/{aid}.record.json"] = (json.dumps({**record, "file": name}, ensure_ascii=False, indent=2)
                                                 + "\n").encode()
    if leaks := _leaks(files, _needles(ps)):
        raise ValueError("the package would hold an absolute path of this machine (the project root, the home "
                         f"directory, RK_HOME or the temporary directory) in: {', '.join(leaks)}")
    head = ps.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    manifest = {"format": "rk-package/1", "paper_type": s.paper_type, "audit_report": rep["version"],
                "event_log_head": head["hash"] if head else None,
                "files": {n: sha256_hex(files[n]) for n in sorted(files)}}
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path = out_dir / f"package-{sha256_hex(files[MANIFEST])[:8]}.zip"
    _write(path, _zip(files))
    return path


def package_problems(path: Path) -> list[str]:
    """Why a package does not match its manifest: unreadable, a file missing, changed or unlisted, or a name that is
    not its manifest's hash."""
    try:
        with zipfile.ZipFile(path) as z:
            files = {n: z.read(n) for n in z.namelist()}
    except (OSError, zipfile.BadZipFile) as e:
        return [f"the package {path.name} cannot be read: {e}"]
    if MANIFEST not in files:
        return [f"the package {path.name} has no {MANIFEST}"]
    try:
        listed = json.loads(files[MANIFEST])["files"]
    except (ValueError, KeyError, TypeError) as e:
        return [f"the package {path.name}'s {MANIFEST} cannot be read: {e}"]
    out = []
    if path.name != f"package-{sha256_hex(files[MANIFEST])[:8]}.zip":
        out.append(f"the package's name {path.name} is not its manifest's hash")
    out += [f"{n}: missing from the package" for n in listed if n not in files]
    out += [f"{n}: its sha256 does not match the manifest" for n, sha in listed.items()
            if n in files and sha256_hex(files[n]) != sha]
    out += [f"{n}: not in the manifest" for n in files if n != MANIFEST and n not in listed]
    return out


def built(ps: ProjectStore, attempt: int) -> dict | None:
    """The attempt's latest ``export.built`` event: the package's path and sha256, the manifest's files."""
    r = ps.db.execute("SELECT payload FROM events WHERE type=? AND json_extract(payload, '$.attempt')=? "
                      "ORDER BY seq DESC LIMIT 1", (BUILT, attempt)).fetchone()
    return json.loads(r["payload"]) if r else None


# ---- the worker ----
class ExportWorker:
    """Kernel only (no role task): ``export`` + ``build_package``. An interrupted attempt runs again as a new one (it
    writes everything afresh). No back-edge: a failure is a defect, reported with pandoc's error."""

    def run(self, ctx: StageContext) -> StageOutcome:
        ps = ctx.ps
        res = export(ps)
        if not res.ok:
            return StageOutcome("failed", reason="the export failed (a defect: export has no back-edge): "
                                                 + "; ".join(res.failures))
        try:
            path = build_package(ps)
        except ValueError as e:
            return StageOutcome("failed", reason=f"the package cannot be built: {e}")
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read(MANIFEST))
        rel = path.relative_to(ps.root).as_posix()
        ps.apply(NewEvent(type=BUILT, entity_id=None, actor_kind="kernel", actor=ctx.actor,
                          payload={"attempt": ctx.attempt, "package": rel, "sha256": sha256_hex(path.read_bytes()),
                                   "files": manifest["files"], "event_log_head": manifest["event_log_head"],
                                   "warnings": res.warnings}))
        note = f"; pandoc warned: {'; '.join(res.warnings)}" if res.warnings else ""
        return StageOutcome("done", reason=f"exported paper.docx, paper.tex and paper.pdf and packaged {rel}{note}")

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        return StageOutcome("failed", reason=f"export opens no checkpoint; it cannot apply {checkpoint['id']}")

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        b = built(ps, attempt)
        if b is None:
            return GateResult(False, [f"export attempt {attempt} built no package"])
        path = ps.root / b["package"]
        if not path.is_file():
            return GateResult(False, [f"the package {b['package']} is gone"])
        failures = package_problems(path)
        if sha256_hex(path.read_bytes()) != b["sha256"]:
            failures.append(f"the package {b['package']} is not the one export attempt {attempt} built")
        got = audited_manuscript(ps)
        if isinstance(got, str):
            failures.append(got)
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: an export deletes nothing; its failure is a defect, so the run fails with it."""
        return StageOutcome("failed", reason="the export cannot be degraded (a defect): "
                                             + ("; ".join(failures) or "no reason given"))
