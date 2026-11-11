"""Reading notes with immediate verification, plus text read/find over snapshots."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from rk.canon import canonical_json, sha256_hex
from rk.fulltext import latest_snapshot
from rk.ids import new_id
from rk.library.service import Library, fts_append
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.text.layer import Page, TextLayer, build_norm_keys, normalize_query
from rk.verify.hedge import hedge_level
from rk.verify.paraphrase import check_paraphrase
from rk.verify.quote import verify_quote, verify_quote_at

NoteKind = Literal["quote", "paraphrase", "summary", "own"]
RoleInSource = Literal["main", "sub", "concession", "qualification", "summary_of_other"]
PARA_CONTEXT = 300  # characters on each side of the located span checked for copying


class Locator(BaseModel):
    page: int
    start: int
    end: int


class NoteIn(BaseModel):
    source_id: str
    kind: NoteKind
    text: str
    locator: Locator | None = None
    role_in_source: RoleInSource | None = None
    keywords: list[str] = Field(default_factory=list)
    source_span: str | None = None
    # what a quote or paraphrase is verified against: the item's text layer, its abstract (an item read on its
    # abstract alone, e.g. a study with no full text; an abstract locator is on page 1 of the abstract), or its
    # bibliographic record (``record_text``; only the kernel's extraction of a record field quotes it)
    against: Literal["text", "abstract", "record"] = "text"


class NoteResult(BaseModel):
    note_id: str
    locator: Locator | None
    verified: dict


class NoteRejected(Exception):
    def __init__(self, reason: str, **details):
        super().__init__(f"note rejected: {reason}")
        self.reason = reason
        self.details = details


def _source(ps: ProjectStore, lib: Library, note: NoteIn) -> tuple[str | None, TextLayer]:
    """What a quote or paraphrase is verified against: ``(snapshot sha, text layer)`` of the item's full text, or
    ``(None, its abstract)``, or ``(None, its record)``. An item with a full text is never verified against its
    abstract."""
    if note.against == "record":
        return None, record_layer(lib.get(note.source_id))
    snap = latest_snapshot(ps, note.source_id)
    if note.against == "abstract":
        if snap is not None:
            raise NoteRejected("has_full_text", hint="the item has a full text: quote it (against 'text')")
        layer = abstract_layer(lib.get(note.source_id).abstract)
        if layer is None:
            raise NoteRejected("no_abstract", hint="the item has no abstract to verify against")
        return None, layer
    if snap is None:
        raise NoteRejected("no_snapshot", hint="fetch full text first (rk fetch)")
    return snap


def locate_in_layer(note: NoteIn, layer: TextLayer) -> tuple[Locator, str]:
    """V-QUOTE of the quote (or the paraphrase's source span) in ``layer``: its locator and how it was found (``given``:
    the given locator holds it; ``search``: found by searching, a given locator that does not hold it is replaced).
    Raises ``NoteRejected`` when it is not there. Also the audit's re-verification of a stored quote note."""
    target = note.text if note.kind == "quote" else note.source_span
    q = None
    if note.kind == "quote" and note.locator is not None:  # a known position must hold an acceptable occurrence
        loc = note.locator
        q = verify_quote_at(target, layer, loc.page, loc.start, loc.end)
    located = "given" if q is not None and q.ok else "search"
    if located == "search":
        q = verify_quote(target, layer)
    if not q.ok:
        raise NoteRejected("quote_not_found" if note.kind == "quote" else "span_not_found",
                           best_snippet=q.best_snippet, best_score=q.best_score, page=q.page)
    return Locator(page=q.page, start=q.start, end=q.end), located


def locate_quote(ps: ProjectStore, lib: Library, note: NoteIn) -> tuple[str | None, Locator]:
    """V-QUOTE of a quote note without storing it: ``(snapshot sha, locator)`` as ``add_note`` would store them;
    raises ``NoteRejected`` as ``add_note`` does."""
    snapshot_sha, layer = _source(ps, lib, note)
    return snapshot_sha, locate_in_layer(note, layer)[0]


def add_note(ps: ProjectStore, lib: Library, note: NoteIn, *, actor: str,
             actor_kind: Literal["human", "agent"] = "agent") -> NoteResult:
    lib.get(note.source_id)
    if not note.text.strip():
        raise ValueError("note text is empty")
    if note.kind in ("quote", "paraphrase") and note.role_in_source is None:
        raise ValueError(f"role_in_source is required for {note.kind} notes")
    verified: dict = {}
    locator = note.locator
    snapshot_sha = None
    if note.kind in ("quote", "paraphrase"):
        snapshot_sha, layer = _source(ps, lib, note)
        if note.kind == "paraphrase" and not (note.source_span or "").strip():
            raise ValueError("paraphrase notes need source_span (the original passage)")
        locator, located = locate_in_layer(note, layer)
        verified["quote"] = {"ok": True, "page": locator.page, "located": located}
        if note.against != "text":
            verified["quote"]["against"] = note.against
        if note.kind == "paraphrase":
            if verify_quote(note.text, layer).ok:
                raise NoteRejected("too_close", reason_detail="paraphrase text occurs verbatim in the source")
            page_text = next(p.text for p in layer.pages if p.n == locator.page)
            context = page_text[max(0, locator.start - PARA_CONTEXT) : locator.end + PARA_CONTEXT]
            p = check_paraphrase(note.text, note.source_span, context=context)
            if not p.ok:
                raise NoteRejected("too_close", **p.model_dump())
            verified["paraphrase"] = p.model_dump()
    level = None
    if note.kind != "own":
        level = hedge_level(note.source_span if note.kind == "paraphrase" else note.text)
    nid = new_id("nte")
    data = {
        "id": nid, "item_id": note.source_id, "kind": note.kind, "text": note.text,
        "locator": locator.model_dump() if locator else None, "role_in_source": note.role_in_source,
        "keywords": note.keywords, "source_span": note.source_span, "hedge_level": level,
        "snapshot_sha": snapshot_sha, "verified": verified,
    }
    ps.apply(NewEvent(type="note.added", entity_id=note.source_id, payload={"note": data}, actor_kind=actor_kind,
                      actor=actor))
    return NoteResult(note_id=nid, locator=locator, verified=verified)


def abstract_layer(abstract: str | None) -> TextLayer | None:
    """An abstract as a one-page text layer (in memory only, never a snapshot), or ``None`` when there is none."""
    if not (abstract or "").strip():
        return None
    return TextLayer(source_kind="abstract", pages=[Page(n=1, text=abstract)], sha256=sha256_hex(abstract))


ITEM_TYPE_WORDS = {"journalArticle": "journal article", "preprint": "preprint", "book": "book",
                   "bookSection": "book section", "conferencePaper": "conference paper", "thesis": "thesis",
                   "report": "report", "dataset": "dataset", "webpage": "web page"}


def record_text(item) -> str:
    """An item's bibliographic record as the reader is shown it and its record quotes are verified against: one line
    each for its title, authors, venue, year, publication type and DOI (a line only when the record has it). These
    fields of an item never change once it is added (``rk.library.models.UPDATABLE_FIELDS``)."""
    authors = "; ".join(", ".join(x for x in (c.family, c.given) if x) for c in item.creators)
    lines = [("Title", item.title), ("Authors", authors), ("Venue", item.venue),
             ("Year", str(item.year) if item.year is not None else None),
             ("Publication type", ITEM_TYPE_WORDS.get(item.item_type, item.item_type)), ("DOI", item.ids.doi)]
    return "\n".join(f"{k}: {v}" for k, v in lines if v)


def record_layer(item) -> TextLayer:
    """An item's bibliographic record (``record_text``) as a one-page text layer, in memory only."""
    text = record_text(item)
    return TextLayer(source_kind="record", pages=[Page(n=1, text=text)], sha256=sha256_hex(text))


def list_notes(ps: ProjectStore, source_id: str | None = None) -> list[dict]:
    import json

    q = "SELECT data FROM notes" + (" WHERE item_id=?" if source_id else "") + " ORDER BY id"
    return [json.loads(r["data"]) for r in ps.db.execute(q, (source_id,) if source_id else ())]


class TextSlice(BaseModel):
    page: int
    start: int
    end: int
    text: str
    snapshot_sha: str


class TextMatch(BaseModel):
    page: int
    start: int
    end: int
    snippet: str


def _layer(ps: ProjectStore, source_id: str):
    snap = latest_snapshot(ps, source_id)
    if snap is None:
        raise LookupError(f"no snapshot for {source_id}; fetch full text first")
    return snap


def text_read(ps: ProjectStore, source_id: str, page: int | None = None, start: int | None = None,
              end: int | None = None) -> TextSlice:
    sha, layer = _layer(ps, source_id)
    pg = next((p for p in layer.pages if p.n == (page or 1)), None)
    if pg is None:
        raise ValueError(f"page {page} out of range (1..{len(layer.pages)})")
    s = max(0, start or 0)
    e = min(len(pg.text), end if end is not None else len(pg.text))
    return TextSlice(page=pg.n, start=s, end=e, text=pg.text[s:e], snapshot_sha=sha)


def _folded(key) -> tuple[str, list[int]]:
    """Unicode-casefold a norm key character by character, keeping the offset map aligned ('ß' -> 'ss' maps both
    characters back to the one source character)."""
    chars: list[str] = []
    mp: list[int] = []
    for c, m in zip(key.key, key.map, strict=True):
        for fc in c.casefold():
            chars.append(fc)
            mp.append(m)
    return "".join(chars), mp


def text_find(ps: ProjectStore, source_id: str, query: str, limit: int = 10) -> list[TextMatch]:
    _, layer = _layer(ps, source_id)
    q = normalize_query(query).casefold()
    if not q:
        return []
    out: list[TextMatch] = []
    for pg in layer.pages:
        seen: set[tuple[int, int]] = set()
        for key in build_norm_keys(pg.text):  # dehyphenated and hyphen-joined
            hay, mp = _folded(key)
            i = hay.find(q)
            while i >= 0 and len(out) < limit:
                s, e = mp[i], mp[i + len(q) - 1] + 1
                if (s, e) not in seen:
                    seen.add((s, e))
                    out.append(TextMatch(page=pg.n, start=s, end=e, snippet=pg.text[max(0, s - 80) : e + 80]))
                i = hay.find(q, i + 1)
        if len(out) >= limit:
            break
    return out


@projection("note.added")
def _note_added(conn: sqlite3.Connection, ev: Event) -> None:
    n = ev.payload["note"]
    conn.execute("INSERT OR REPLACE INTO notes(id, item_id, data) VALUES (?,?,?)",
                 (n["id"], n["item_id"], canonical_json(n)))
    fts_append(conn, n["item_id"], "notes", " ".join([n["text"], *n.get("keywords", [])]))
