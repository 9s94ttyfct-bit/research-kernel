"""Reading notes with immediate verification, plus text read/find over snapshots."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from rk.canon import canonical_json
from rk.fulltext import latest_snapshot
from rk.ids import new_id
from rk.library.service import Library, fts_append
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.text.layer import build_norm_keys, normalize_query
from rk.verify.hedge import hedge_level
from rk.verify.paraphrase import check_paraphrase
from rk.verify.quote import verify_quote

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


class NoteResult(BaseModel):
    note_id: str
    locator: Locator | None
    verified: dict


class NoteRejected(Exception):
    def __init__(self, reason: str, **details):
        super().__init__(f"note rejected: {reason}")
        self.reason = reason
        self.details = details


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
        snap = latest_snapshot(ps, note.source_id)
        if snap is None:
            raise NoteRejected("no_snapshot", hint="fetch full text first (rk fetch)")
        snapshot_sha, layer = snap
        target = note.text if note.kind == "quote" else note.source_span
        if note.kind == "paraphrase" and not (note.source_span or "").strip():
            raise ValueError("paraphrase notes need source_span (the original passage)")
        q = verify_quote(target, layer)
        if not q.ok:
            raise NoteRejected("quote_not_found" if note.kind == "quote" else "span_not_found",
                               best_snippet=q.best_snippet, best_score=q.best_score, page=q.page)
        locator = Locator(page=q.page, start=q.start, end=q.end)
        verified["quote"] = {"ok": True, "page": q.page}
        if note.kind == "paraphrase":
            if verify_quote(note.text, layer).ok:
                raise NoteRejected("too_close", reason_detail="paraphrase text occurs verbatim in the source")
            page_text = next(p.text for p in layer.pages if p.n == q.page)
            context = page_text[max(0, q.start - PARA_CONTEXT) : q.end + PARA_CONTEXT]
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
