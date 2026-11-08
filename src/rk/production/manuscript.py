"""A draft section's markdown as sentences with their source markers (spec §9.1 V-COVER), and the draft store.

Marker grammar (one grammar for the whole kernel; the Workbench preview imports it): ``[[clm:clm_…]]``,
``[[run:evt_…]]`` / ``[[run:prt_…]]``, ``[[fig:fig_…]]`` / ``[[stat:stt_…]]``, ``[[plan:dsg_…]]`` /
``[[plan:hyp_…]]``, ``[[inf]]`` and ``[[ck]]``; a sentence may carry several.

``parse_section`` reads the markdown with markdown-it (raw HTML off, so it stays text) and keeps the prose of
paragraphs and list items; headings, tables and code blocks are not prose. Inside prose a code span is text whose
markers do not count, and a line break inside a marker breaks it (no marker). Sentences end at ``.``, ``?`` or ``!``
(with any closing quotes or brackets) followed by whitespace and a capital letter (or an opening quote or bracket and
one), except after an abbreviation (``e.g.``, ``i.e.``, ``et al.``, ``vs.``, ``Fig.``, ``Eq.``, …) or an initial;
decimals never split. A sentence's ``span`` is its place in the section's prose (its paragraphs joined by a blank line).

Drafts (``draft.written``, ``draft.verified``; projection ``drafts``): version ``N`` of section ``S`` is the file
``artifacts/drafts/S.vN.md`` with its sha256; the file is put in place before the event, and a file that no longer has
its recorded sha256 is refused, not read as the draft."""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel

from rk.canon import canonical_json, sha256_hex
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.store.query import query_rows

MarkerKind = Literal["clm", "run", "fig", "stat", "plan", "inf", "ck"]
MARKER = re.compile(r"\[\[(clm|run|fig|stat|plan|inf|ck)(?::([A-Za-z0-9_]+))?\]\]")
SECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DRAFTS_DIR = "artifacts/drafts"
_ABBREVIATIONS = frozenset({"e.g", "i.e", "al", "vs", "fig", "figs", "eq", "eqs", "cf", "approx", "no", "ref", "refs",
                            "dr", "mr", "ms", "st", "ca", "resp", "suppl", "vol", "pp", "p"})
_END = re.compile(r"[.?!][\"”’')\]]*(?=\s+[\"“‘(\[]?[A-Z])")


class Marker(BaseModel):
    kind: MarkerKind
    target: str | None
    span: tuple[int, int]  # in the sentence's text


class Sentence(BaseModel):
    index: int
    text: str
    markers: list[Marker]
    span: tuple[int, int]  # in the section's prose


def _markdown() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def _inline_prose(token) -> tuple[str, list[tuple[int, int]]]:
    """An inline token's prose and the ranges of its code spans (markers there do not count)."""
    out, code = [], []
    pos = 0
    for child in token.children or []:
        if child.type == "text":
            piece = child.content
        elif child.type == "code_inline":
            piece = f"`{child.content}`"
            code.append((pos, pos + len(piece)))
        elif child.type in ("softbreak", "hardbreak"):
            piece = " "
        elif child.type == "image":
            piece = child.content
        else:  # emphasis, links: their markup is not prose, their text comes as text children
            piece = ""
        out.append(piece)
        pos += len(piece)
    return "".join(out), code


def _blocks(markdown: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """The prose blocks of the section: paragraphs (also inside list items and block quotes), not headings, tables or
    code."""
    blocks, skip = [], 0
    for t in _markdown().parse(markdown):
        if t.type in ("heading_open", "table_open"):
            skip += 1
        elif t.type in ("heading_close", "table_close"):
            skip -= 1
        elif t.type == "inline" and not skip:
            text, code = _inline_prose(t)
            if text.strip():
                blocks.append((text, code))
    return blocks


def _abbreviation_before(text: str, end: int) -> bool:
    """Whether the ``.`` at ``end`` closes an abbreviation or an initial rather than a sentence."""
    word = re.search(r"([A-Za-z][A-Za-z.]*)$", text[:end])
    if word is None:
        return False
    w = word.group(1).lower()
    return w in _ABBREVIATIONS or (len(w) == 1 and text[end - 1].isupper())


def _split(text: str) -> list[tuple[int, int]]:
    cuts, start = [], 0
    for m in _END.finditer(text):
        if text[m.start()] == "." and _abbreviation_before(text, m.start()):
            continue
        cuts.append((start, m.end()))
        start = m.end()
    cuts.append((start, len(text)))
    out = []
    for a, b in cuts:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            out.append((a, b))
    return out


def parse_section(markdown: str) -> list[Sentence]:
    """The section's sentences, in order, with their markers (see the module docstring)."""
    sentences: list[Sentence] = []
    offset = 0
    for text, code in _blocks(markdown):
        for a, b in _split(text):
            body = text[a:b]
            markers = [Marker(kind=m[1], target=m[2], span=(m.start(), m.end())) for m in MARKER.finditer(body)
                       if not any(a + m.start() < ce and a + m.end() > cs for cs, ce in code)]
            sentences.append(Sentence(index=len(sentences), text=body, markers=markers,
                                      span=(offset + a, offset + b)))
        offset += len(text) + 2
    return sentences


# ---- the draft store ----
def write_draft(ps: ProjectStore, section: str, markdown: str, *, stage: str, attempt: int, round: int,
                by_task: str | None, actor: str) -> int:
    """Store the next version of ``section``; returns its number. The file is written aside and renamed into place
    before the event is recorded (a crash leaves an unrecorded file, never a recorded draft without its file)."""
    if not SECTION_ID.match(section):
        raise ValueError(f"not a section id: {section!r}")
    data = markdown.encode("utf-8")
    with ps.lock:
        row = ps.db.execute("SELECT MAX(version) FROM drafts WHERE section=?", (section,)).fetchone()
        version = (row[0] or 0) + 1
        rel = f"{DRAFTS_DIR}/{section}.v{version}.md"
        dest = ps.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        try:
            ps.apply(NewEvent(type="draft.written", entity_id=f"{section}.v{version}", actor_kind="kernel",
                              actor=actor, payload={"section": section, "version": version, "stage": stage,
                                                    "attempt": attempt, "round": round, "path": rel,
                                                    "sha256": sha256_hex(data), "by_task": by_task}))
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
    return version


def record_section_verdict(ps: ProjectStore, section: str, version: int, verdict: BaseModel | dict, *,
                           actor: str) -> None:
    """Record a version's verification (``draft.verified``; a ``SectionVerdict`` or its dict)."""
    data = verdict.model_dump(mode="json") if isinstance(verdict, BaseModel) else dict(verdict)
    if ps.db.execute("SELECT 1 FROM drafts WHERE section=? AND version=?", (section, version)).fetchone() is None:
        raise KeyError(f"{section} v{version}")
    ps.apply(NewEvent(type="draft.verified", entity_id=f"{section}.v{version}", actor_kind="kernel", actor=actor,
                      payload={"section": section, "version": version, "verdict": data}))


def draft_versions(ps: ProjectStore, section: str) -> list[dict]:
    """Every stored version of ``section``, oldest first, with its verdict (``None`` until verified)."""
    return query_rows(ps, "SELECT * FROM drafts WHERE section=? ORDER BY version", (section,), ("verdict",))


def latest_draft(ps: ProjectStore, section: str) -> tuple[int, str] | None:
    """``(version, markdown)`` of the section's latest version, else ``None``. Raises ``ValueError`` when its file
    is missing or no longer has its recorded sha256."""
    rows = draft_versions(ps, section)
    if not rows:
        return None
    r = rows[-1]
    path = ps.root / r["path"]
    if not path.is_file():
        raise ValueError(f"the draft file {r['path']} is missing")
    data = path.read_bytes()
    if sha256_hex(data) != r["sha256"]:
        raise ValueError(f"the draft file {r['path']} no longer has its recorded sha256")
    return r["version"], data.decode("utf-8")


# ---- projections ----
@projection("draft.written")
def _written(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO drafts(section, version, stage, attempt, round, path, sha256, by_task, "
                 "verdict, created) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                 (p["section"], p["version"], p["stage"], p["attempt"], p["round"], p["path"], p["sha256"],
                  p["by_task"], ev.ts))


@projection("draft.verified")
def _verified(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("UPDATE drafts SET verdict=? WHERE section=? AND version=?",
                 (canonical_json(p["verdict"]), p["section"], p["version"]))
