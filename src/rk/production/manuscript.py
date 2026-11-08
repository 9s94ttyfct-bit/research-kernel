"""A draft section's markdown as sentences with their source markers (spec §9.1 V-COVER), and the draft store.

Marker grammar (one grammar for the whole kernel; the Workbench preview imports it): ``[[clm:clm_…]]``,
``[[run:evt_…]]`` / ``[[run:prt_…]]``, ``[[fig:fig_…]]`` / ``[[stat:stt_…]]``, ``[[plan:dsg_…]]`` /
``[[plan:hyp_…]]``, ``[[inf]]`` and ``[[ck]]``; a sentence may carry several. A marker is the literal grammar in the
source markdown (``source_markers``), outside code spans and code blocks and not escaped by a backslash: an escaped,
entity-encoded or emphasis-split form, or one broken by a line break, is no marker. The preview substitutes exactly
these.

``parse_section`` reads the markdown with markdown-it (raw HTML off, so it stays text) and keeps the prose of
paragraphs and list items; headings, tables, table captions and code blocks are not prose. A table or a pandoc
caption line (``Table: …`` / ``: …``) is a section problem (``section_problems``): tables come from code artifacts at
export. A paragraph inside a block quote is one sentence of quoted text (``quoted``). A sentence's ``text`` is its
rendered prose (emphasis and link markup gone, code spans kept with their backticks); its ``prose`` is that text with
every code span blanked, which is what every prose check reads; its ``span`` and its markers' spans are offsets into
the section markdown.

Sentences end at ``.``, ``?`` or ``!`` (with any closing quotes or brackets, and any markers right after them, which
belong to the sentence they follow) followed by whitespace and anything but a marker (a capital, a digit, a lowercase
or non-ASCII letter, a quote or a bracket alike), except after an abbreviation (``e.g.``, ``i.e.``, ``et al.``,
``vs.``, ``Fig.``, ``Eq.``, …) or an initial: a capital letter standing alone before a capitalised name that is no
common sentence opener, and not after a lowercase word other than one that leads a name (``by J. Smith``, but not
``vitamin D. The``, ``group A. Next`` or ``Figure 2A. Then``). Decimals never split.

Drafts (``draft.written``, ``draft.verified``; projection ``drafts``): version ``N`` of section ``S`` is the file
``artifacts/drafts/S.vN.md`` with its sha256; the file is synced and put in place before the event, and a file that no
longer has its recorded sha256 is refused, not read as the draft. A file without a ``drafts`` row is no draft."""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Literal, NamedTuple

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
_ABBREVIATIONS = frozenset({"e.g", "i.e", "al", "vs", "fig", "figs", "eq", "eqs", "cf", "approx", "ref", "refs",
                            "dr", "mr", "ms", "st", "ca", "resp", "suppl", "vol", "pp", "p"})
# terminal punctuation, closing quotes or brackets, the markers right after it; then anything but a marker
_END = re.compile(r"[.?!][\"”’'»)\]]*(?:\s*\[\[[^\[\]\n]*\]\])*(?=\s+(?!\[\[)\S)")
_CAPTION = re.compile(r"(?:[Tt]able)?:(?:\s|$)")
_SENTENCE_OPENERS = frozenset({
    "A", "After", "All", "Also", "Although", "Among", "An", "As", "At", "Because", "Before", "Both", "But", "By",
    "Each", "Finally", "First", "For", "From", "Further", "Furthermore", "However", "If", "In", "It", "Its", "Many",
    "Moreover", "Most", "Next", "No", "None", "Not", "Of", "On", "One", "Only", "Our", "Overall", "Second", "Several",
    "Since", "So", "Some", "Such", "That", "The", "Then", "There", "These", "This", "Those", "Thus", "Therefore", "To",
    "We", "When", "While", "With", "Yet"})
_NAME_LEADS = frozenset({"by", "and", "of", "with", "from", "to", "see", "per", "cf", "after", "for", "as", "&"})


class Marker(BaseModel):
    kind: MarkerKind
    target: str | None
    span: tuple[int, int]  # offsets into the section markdown


class Sentence(BaseModel):
    index: int
    text: str  # rendered prose: emphasis and link markup gone, code spans kept with their backticks
    prose: str  # ``text`` with its code spans blanked: what every prose check reads
    markers: list[Marker]
    span: tuple[int, int]  # offsets into the section markdown
    quoted: bool = False  # a block quote's paragraph: quoted text as a whole


class _Block(NamedTuple):
    kind: Literal["prose", "heading", "table"]
    quoted: bool
    line: int  # its first source line (0-based)
    text: str  # rendered
    ranges: list[tuple[int, int]]  # each character's source range
    code: list[tuple[int, int]]  # its code spans' source ranges
    code_text: list[tuple[int, int]]  # its code spans' ranges in ``text``


class _Doc(NamedTuple):
    blocks: list[_Block]
    code_blocks: list[tuple[int, int]]  # source ranges of fenced and indented code
    tables: list[int]  # each table's first source line

    def code(self) -> list[tuple[int, int]]:
        return self.code_blocks + [r for b in self.blocks for r in b.code]


def _markdown() -> MarkdownIt:
    # text_join off: escapes and entities stay text_special tokens with their source form, for the source map
    return MarkdownIt("commonmark", {"html": False}).enable("table").disable("text_join")


def _line_starts(src: str) -> list[int]:
    return [0] + [i + 1 for i, ch in enumerate(src) if ch == "\n"]


def _content_offsets(content: str, src: str, starts: list[int], first: int, cursor: dict[int, int]) -> list[int]:
    """The source offset of each character of an inline token's content, and one past its end. Content line ``i`` is
    source line ``first + i`` with its block prefix (quote markers, bullets, indentation) cut and its ends trimmed; a
    line that holds several inline tokens (table cells) is searched from where the previous one ended (``cursor``)."""
    lines = content.split("\n")
    offs: list[int] = []
    for i, cl in enumerate(lines):
        n = first + i
        a = starts[n] if n < len(starts) else len(src)
        b = starts[n + 1] - 1 if n + 1 < len(starts) else len(src)
        line = src[a:b].rstrip("\r")
        core, lead = cl.strip(), len(cl) - len(cl.lstrip())
        tail, at = line.rstrip(), cursor.get(n, 0)
        if core and tail.endswith(core) and len(tail) - len(core) >= at:
            pos = len(tail) - len(core)
        else:
            found = line.find(core, at) if core else -1
            pos = found if found >= 0 else at
        cursor[n] = pos + len(core)
        base = a + pos - lead
        offs += [max(a, base + j) for j in range(len(cl))]
        offs.append(b if i < len(lines) - 1 else base + len(cl))
    return offs


def _bracket_end(c: str, k: int) -> int:
    """The index just past the ``]`` closing the ``[`` at ``k`` (nesting and backslash escapes respected)."""
    depth, i = 0, k
    while i < len(c):
        if c[i] == "\\":
            i += 2
            continue
        depth += {"[": 1, "]": -1}.get(c[i], 0)
        if depth == 0:
            return i + 1
        i += 1
    return len(c)


def _link_tail(c: str, k: int) -> int:
    """The index just past a link's ``(destination "title")`` or ``[label]`` starting at ``k`` (``k`` without one)."""
    if c[k:k + 1] == "[":
        return _bracket_end(c, k)
    if c[k:k + 1] != "(":
        return k
    depth, i = 0, k
    while i < len(c):
        if c[i] == "\\":
            i += 2
            continue
        depth += {"(": 1, ")": -1}.get(c[i], 0)
        if depth == 0:
            return i + 1
        i += 1
    return len(c)


def _decode(token, offs: list[int]) -> tuple[str, list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """An inline token's rendered text with each character's source range, and its code spans (source ranges and
    ranges in the text). Its children are found in order in its content: text as written, escapes and entities by
    their source form, emphasis delimiters, link and image syntax (whose destination is not text) and code spans."""
    c = token.content
    text: list[str] = []
    ranges: list[tuple[int, int]] = []
    code_src: list[tuple[int, int]] = []
    code_text: list[tuple[int, int]] = []
    p = 0

    def at(i: int) -> int:
        return offs[max(0, min(i, len(offs) - 1))]

    def find(s: str, start: int) -> int:
        i = c.find(s, start)
        return start if i < 0 else i

    def put(piece: str, a: int, b: int) -> None:  # the piece stands for content[a:b] as a whole
        text.extend(piece)
        ranges.extend([(at(a), at(b - 1) + 1)] * len(piece))

    for ch in token.children or []:
        t = ch.type
        if t == "text" and ch.content:
            i = find(ch.content, p)
            text.extend(ch.content)
            ranges.extend((at(i + j), at(i + j) + 1) for j in range(len(ch.content)))
            p = i + len(ch.content)
        elif t == "text_special":
            i = find(ch.markup, p)
            put(ch.content, i, i + len(ch.markup))
            p = i + len(ch.markup)
        elif t == "code_inline":
            i = find(ch.markup, p)
            close = re.compile(rf"(?<!`){re.escape(ch.markup)}(?!`)").search(c, i + len(ch.markup))
            end = close.end() if close else len(c)
            piece = f"`{ch.content}`"
            code_src.append((at(i), at(end - 1) + 1))
            code_text.append((len(text), len(text) + len(piece)))
            put(piece, i, end)
            p = end
        elif t in ("softbreak", "hardbreak"):
            i = find("\n", p)
            put(" ", i, i + 1)
            p = i + 1
        elif t == "link_open":
            p = find("<" if ch.markup == "autolink" else "[", p) + 1
        elif t == "link_close":
            p = find(">", p) + 1 if ch.markup == "autolink" else _link_tail(c, find("]", p) + 1)
        elif t == "image":
            i = find("![", p)
            end = _link_tail(c, _bracket_end(c, i + 1))
            put(ch.content, i, end)
            p = end
        elif ch.markup and t.endswith(("_open", "_close")):  # emphasis delimiters
            p = find(ch.markup, p) + len(ch.markup)
    return "".join(text), ranges, code_src, code_text


def _parse(markdown: str) -> _Doc:
    starts = _line_starts(markdown)
    cursor: dict[int, int] = {}
    blocks: list[_Block] = []
    code_blocks: list[tuple[int, int]] = []
    tables: list[int] = []
    heading = table = quote = 0
    for t in _markdown().parse(markdown):
        if t.type == "heading_open":
            heading += 1
        elif t.type == "heading_close":
            heading -= 1
        elif t.type == "table_open":
            table += 1
            tables.append(t.map[0])
        elif t.type == "table_close":
            table -= 1
        elif t.type == "blockquote_open":
            quote += 1
        elif t.type == "blockquote_close":
            quote -= 1
        elif t.type in ("fence", "code_block") and t.map:
            end = starts[t.map[1]] if t.map[1] < len(starts) else len(markdown)
            code_blocks.append((starts[t.map[0]], end))
        elif t.type == "inline" and t.map:
            offs = _content_offsets(t.content, markdown, starts, t.map[0], cursor)
            kind = "heading" if heading else "table" if table else "prose"
            blocks.append(_Block(kind, quote > 0, t.map[0], *_decode(t, offs)))
    return _Doc(blocks, code_blocks, tables)


def _markers(markdown: str, code: list[tuple[int, int]]) -> list[Marker]:
    out = []
    for m in MARKER.finditer(markdown):
        if any(m.start() < e and m.end() > s for s, e in code):
            continue
        before = markdown[:m.start()]
        if (len(before) - len(before.rstrip("\\"))) % 2:  # an escaped first bracket
            continue
        out.append(Marker(kind=m[1], target=m[2], span=(m.start(), m.end())))
    return out


def source_markers(markdown: str) -> list[Marker]:
    """Every marker of the section markdown, in order, with its source span (see the module docstring)."""
    return _markers(markdown, _parse(markdown).code())


def _is_caption(b: _Block) -> bool:
    return b.kind == "prose" and _CAPTION.match(b.text.lstrip()) is not None


def section_problems(markdown: str) -> list[str]:
    """What the section may not contain at all: a markdown table, or a pandoc table caption line."""
    doc = _parse(markdown)
    out = [f"line {n + 1}: a markdown table is not allowed in a draft: tables come from code artifacts at export "
           "(cite a [[stat:…]] or [[fig:…]] instead)" for n in doc.tables]
    out += [f"line {b.line + 1}: a table caption (“{b.text.strip()[:60]}”) is not a sentence and is not allowed in a "
            "draft: tables and their captions come from code artifacts at export" for b in doc.blocks if _is_caption(b)]
    return out


def _abbreviation_before(text: str, end: int) -> bool:
    """Whether the ``.`` at ``end`` closes an abbreviation or an initial rather than a sentence."""
    word = re.search(r"(?<![0-9A-Za-z])([A-Za-z][A-Za-z.]*)$", text[:end])
    if word is None:
        return False
    w = word.group(1)
    if w.lower() in _ABBREVIATIONS:
        return True
    if len(w) != 1 or not w.isupper():
        return False
    after = re.match(r"\s+([A-Z][a-z]+|[A-Z]\.)", text[end + 1:])
    if after is None or after.group(1) in _SENTENCE_OPENERS:
        return False
    before = re.search(r"(\S+)\s+$", text[:word.start(1)])
    return before is None or not before.group(1)[0].islower() or before.group(1).strip("(,;:") in _NAME_LEADS


def _cuts(prose: str) -> list[tuple[int, int]]:
    cuts, start = [], 0
    for m in _END.finditer(prose):
        if prose[m.start()] == "." and _abbreviation_before(prose, m.start()):
            continue
        cuts.append((start, m.end()))
        start = m.end()
    cuts.append((start, len(prose)))
    return cuts


def parse_section(markdown: str) -> list[Sentence]:
    """The section's sentences, in order, with their markers (see the module docstring)."""
    doc = _parse(markdown)
    markers = _markers(markdown, doc.code())
    sentences: list[Sentence] = []
    for b in doc.blocks:
        if b.kind != "prose" or _is_caption(b):
            continue
        prose = list(b.text)
        for s, e in b.code_text:
            prose[s:e] = " " * (e - s)
        prose = "".join(prose)
        for a, z in [(0, len(prose))] if b.quoted else _cuts(prose):
            while a < z and b.text[a].isspace():
                a += 1
            while z > a and b.text[z - 1].isspace():
                z -= 1
            if a == z:
                continue
            span = (b.ranges[a][0], b.ranges[z - 1][1])
            sentences.append(Sentence(index=len(sentences), text=b.text[a:z], prose=prose[a:z], span=span,
                                      markers=[m for m in markers if span[0] <= m.span[0] < span[1]],
                                      quoted=b.quoted))
    return sentences


# ---- the draft store ----
def write_draft(ps: ProjectStore, section: str, markdown: str, *, stage: str, attempt: int, round: int,
                by_task: str | None, actor: str) -> int:
    """Store the next version of ``section``; returns its number. The file is written aside, synced and renamed into
    place before the event is recorded (a crash leaves an unrecorded file, never a recorded draft without its file).

    One writer per project: the version number is assigned under ``ps.lock``, an in-process lock, so two processes
    writing drafts of one project at once could assign the same version. The kernel runs a project's stages in one
    process (one run at a time), which this relies on."""
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
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)  # the rename itself
        finally:
            os.close(fd)
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
    """Record a version's verification (``draft.verified``): ``verdict`` is a ``rk.verify.cover.SectionVerdict`` (or
    its dict), validated as one, of this section and of this version (or of none)."""
    from rk.verify.cover import SectionVerdict  # cover imports this module

    raw = verdict.model_dump(mode="json") if isinstance(verdict, BaseModel) else verdict
    v = SectionVerdict.model_validate(raw)
    if v.section != section:
        raise ValueError(f"the verdict is of section {v.section!r}, not {section!r}")
    if v.version not in (None, version):
        raise ValueError(f"the verdict is of version {v.version}, not {version}")
    if ps.db.execute("SELECT 1 FROM drafts WHERE section=? AND version=?", (section, version)).fetchone() is None:
        raise KeyError(f"{section} v{version}")
    data = v.model_copy(update={"version": version}).model_dump(mode="json")
    ps.apply(NewEvent(type="draft.verified", entity_id=f"{section}.v{version}", actor_kind="kernel", actor=actor,
                      payload={"section": section, "version": version, "verdict": data}))


def draft_versions(ps: ProjectStore, section: str) -> list[dict]:
    """Every stored version of ``section``, oldest first, with its verdict (``None`` until verified)."""
    return query_rows(ps, "SELECT * FROM drafts WHERE section=? ORDER BY version", (section,), ("verdict",))


def drafted_sections(ps: ProjectStore) -> list[dict]:
    """``{section, versions}`` of every section with a stored draft (the ``drafts`` projection), by section."""
    rows = ps.db.execute("SELECT section, version FROM drafts ORDER BY section, version").fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:
        out.setdefault(r["section"], []).append(r["version"])
    return [{"section": s, "versions": v} for s, v in out.items()]


def draft_text(ps: ProjectStore, section: str, version: int) -> str:
    """The markdown of a stored version. ``KeyError`` when it was never stored; ``ValueError`` when its file is
    missing or no longer has its recorded sha256."""
    r = ps.db.execute("SELECT path, sha256 FROM drafts WHERE section=? AND version=?", (section, version)).fetchone()
    if r is None:
        raise KeyError(f"{section} v{version}")
    path = ps.root / r["path"]
    if not path.is_file():
        raise ValueError(f"the draft file {r['path']} is missing")
    data = path.read_bytes()
    if sha256_hex(data) != r["sha256"]:
        raise ValueError(f"the draft file {r['path']} no longer has its recorded sha256")
    return data.decode("utf-8")


def latest_draft(ps: ProjectStore, section: str) -> tuple[int, str] | None:
    """``(version, markdown)`` of the section's latest version, else ``None``; ``ValueError`` as ``draft_text``."""
    r = ps.db.execute("SELECT MAX(version) FROM drafts WHERE section=?", (section,)).fetchone()
    if r[0] is None:
        return None
    return r[0], draft_text(ps, section, r[0])


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
