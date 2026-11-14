"""Per-project library: items, identifiers, tags, collections, full-text search."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel

from rk.canon import canonical_json
from rk.ids import new_id
from rk.library.models import UPDATABLE_FIELDS, Ids, Item, ItemIn, normalize_title
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection

DEFAULT_COLLECTIONS = ("Search hits", "Included", "Excluded", "Counter-evidence", "Retracted / EoC", "Needs you")

ActorKind = Literal["human", "agent", "kernel"]


class AddResult(BaseModel):
    item_id: str
    created: bool


class ItemHit(BaseModel):
    item_id: str
    title: str
    snippet: str
    rank: float


def fts_query(text: str) -> str | None:
    toks = re.findall(r"\w+", text)
    return " ".join(f'"{t}"' for t in toks) if toks else None


class Library:
    def __init__(self, ps: ProjectStore):
        self.ps = ps

    @property
    def db(self):
        return self.ps.db  # per-thread connection

    # ---- reads ----
    def find_by_ids(self, ids: Ids) -> str | None:
        """The item holding any of these identifiers (normalized), or ``None``."""
        for kind, value in ids.normalized().pairs():
            r = self.db.execute("SELECT item_id FROM item_ids WHERE kind=? AND value=?", (kind, value)).fetchone()
            if r:
                return r["item_id"]
        return None

    def _find_existing(self, item: ItemIn) -> str | None:
        found = self.find_by_ids(item.ids)
        if found:
            return found
        tn = normalize_title(item.title)
        if tn and item.year is not None:  # registries reuse generic titles: a different NCT is a different study
            nct = item.ids.nct  # add_item normalized the ids
            r = self.db.execute("SELECT id FROM items WHERE title_norm=? AND year=? AND (? IS NULL OR "
                                "json_extract(data, '$.ids.nct') IS NULL OR json_extract(data, '$.ids.nct')=?)",
                                (tn, item.year, nct, nct)).fetchone()
            if r:
                return r["id"]
        if not item.ids.pairs() and item.ids.url:  # a web page with no identifier: the page address is its identity
            r = self.db.execute("SELECT id FROM items WHERE json_extract(data, '$.ids.url')=?",
                                (item.ids.url,)).fetchone()
            if r:
                return r["id"]
        return None

    def get(self, item_id: str) -> Item:
        r = self.db.execute("SELECT data FROM items WHERE id=?", (item_id,)).fetchone()
        if not r:
            raise KeyError(item_id)
        d = json.loads(r["data"])
        d["tags"] = [t["tag"] for t in self.db.execute("SELECT tag FROM tags WHERE item_id=? ORDER BY tag", (item_id,))]
        d["collection_ids"] = [
            c["collection_id"]
            for c in self.db.execute(
                "SELECT collection_id FROM collection_items WHERE item_id=? ORDER BY collection_id", (item_id,)
            )
        ]
        return Item.model_validate(d)

    def list(self, collection_id: str | None = None) -> list[Item]:
        if collection_id is None:
            ids = [r["id"] for r in self.db.execute("SELECT id FROM items ORDER BY id")]
        else:
            col = self.db.execute("SELECT query FROM collections WHERE id=?", (collection_id,)).fetchone()
            if col is None:
                raise KeyError(collection_id)
            if col["query"]:
                ids = [h.item_id for h in self.search(col["query"], limit=10_000)]
            else:
                ids = [
                    r["item_id"]
                    for r in self.db.execute(
                        "SELECT item_id FROM collection_items WHERE collection_id=? ORDER BY item_id", (collection_id,)
                    )
                ]
        return [self.get(i) for i in ids]

    def collections(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, name, parent_id, query FROM collections ORDER BY name")]

    def collection_by_name(self, name: str) -> str | None:
        r = self.db.execute("SELECT id FROM collections WHERE name=?", (name,)).fetchone()
        return r["id"] if r else None

    def search(self, query: str, limit: int = 50) -> list[ItemHit]:
        q = fts_query(query)
        if not q:
            return []
        rows = self.db.execute(
            "SELECT item_id, title, snippet(items_fts, -1, '[', ']', '…', 12) AS snip, bm25(items_fts) AS rank "
            "FROM items_fts WHERE items_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, limit),
        )
        return [ItemHit(item_id=r["item_id"], title=r["title"], snippet=r["snip"], rank=r["rank"]) for r in rows]

    # ---- writes ----
    def _ev(self, t: str, entity: str | None, payload: dict, actor: str, kind: ActorKind) -> Event:
        return self.ps.apply(NewEvent(type=t, entity_id=entity, payload=payload, actor_kind=kind, actor=actor))

    def add_item(
        self, item: ItemIn, *, added_by: Literal["human", "agent"], actor: str, actor_kind: ActorKind | None = None,
        provenance: dict | None = None,
    ) -> AddResult:
        """``provenance`` is kept in the ``item.added`` event (e.g. the browser's ``page_metadata_diff``).

        A hit of a work already in the library adds, in an ``item.merged``, what the item lacks: identifiers,
        open-access URLs, the language and (calibration, search and screening, 2026-11-12) the abstract. Nothing the
        item already has is replaced."""
        kind: ActorKind = actor_kind or ("human" if added_by == "human" else "agent")
        item = item.model_copy(update={"ids": item.ids.normalized()})
        with self.ps.lock:  # find-then-add must not interleave with another add of the same work
            existing = self._find_existing(item)
            if existing:
                cur = self.get(existing)
                new_ids = {k: v for k, v in item.ids.model_dump().items() if v and not getattr(cur.ids, k)}
                new_urls = [u for u in item.oa_urls if u not in cur.oa_urls]
                new_lang = item.language if item.language and not cur.language else None
                fills = (item.abstract or "").strip() and not (cur.abstract or "").strip()
                new_abstract = item.abstract if fills else None
                if new_ids or new_urls or new_lang or new_abstract:
                    payload = {"ids": new_ids, "oa_urls": new_urls, **({"language": new_lang} if new_lang else {}),
                               **({"abstract": new_abstract} if new_abstract else {})}
                    self._ev("item.merged", existing, payload, actor, kind)
                return AddResult(item_id=existing, created=False)
            full = Item(**item.model_dump(), id=new_id("src"), added_by=added_by)
            self._ev("item.added", full.id, {"item": full.model_dump(mode="json"), **(provenance or {})}, actor, kind)
        return AddResult(item_id=full.id, created=True)

    def fill_abstract(self, item_id: str, abstract: str, *, source: str, actor: str,
                      actor_kind: ActorKind = "kernel") -> bool:
        """Give an item without an abstract the one a source returned (an ``item.merged`` naming the ``source``).

        An existing abstract is never replaced; returns whether the item took this one."""
        with self.ps.lock:
            if not abstract.strip() or (self.get(item_id).abstract or "").strip():
                return False
            self._ev("item.merged", item_id, {"ids": {}, "oa_urls": [], "abstract": abstract, "source": source},
                     actor, actor_kind)
        return True

    def update(self, item_id: str, *, actor: str, actor_kind: ActorKind = "kernel", **fields) -> None:
        bad = set(fields) - UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        self.get(item_id)
        Item.model_validate({**self.get(item_id).model_dump(), **fields})  # validate values
        self._ev("item.updated", item_id, {"fields": fields}, actor, actor_kind)

    def tag(
        self, item_id: str, add: list[str] | None = None, remove: list[str] | None = None, *, actor: str,
        actor_kind: ActorKind = "human",
    ) -> None:
        self.get(item_id)
        add = sorted({t.strip() for t in (add or []) if t.strip()})
        remove = sorted({t.strip() for t in (remove or []) if t.strip()})
        if add or remove:
            self._ev("item.tagged", item_id, {"add": add, "remove": remove}, actor, actor_kind)

    def create_collection(
        self, name: str, parent_id: str | None = None, query: str | None = None, *, actor: str,
        actor_kind: ActorKind = "human",
    ) -> str:
        with self.ps.lock:
            if self.collection_by_name(name):
                raise ValueError(f"collection exists: {name}")
            cid = new_id("col")
            self._ev("collection.created", cid, {"name": name, "parent_id": parent_id, "query": query}, actor,
                     actor_kind)
        return cid

    def collect(self, item_id: str, collection_id: str, *, actor: str, actor_kind: ActorKind = "human") -> None:
        self.get(item_id)
        col = self.db.execute("SELECT query FROM collections WHERE id=?", (collection_id,)).fetchone()
        if col is None:
            raise KeyError(collection_id)
        if col["query"]:
            raise ValueError("cannot add items to a smart collection")
        self._ev("item.collected", item_id, {"collection_id": collection_id}, actor, actor_kind)

    def ensure_default_collections(self, *, actor: str) -> dict[str, str]:
        out = {}
        with self.ps.lock:
            for name in DEFAULT_COLLECTIONS:
                out[name] = self.collection_by_name(name) or self.create_collection(name, actor=actor,
                                                                                    actor_kind="kernel")
        return out


# ---- projections ----
def _write_item(conn: sqlite3.Connection, item: dict) -> None:
    data = {k: v for k, v in item.items() if k not in ("tags", "collection_ids")}
    conn.execute(
        "INSERT OR REPLACE INTO items(id, data, title_norm, year) VALUES (?, ?, ?, ?)",
        (item["id"], canonical_json(data), normalize_title(item["title"]), item.get("year")),
    )
    for kind, value in Ids.model_validate(item["ids"]).pairs():
        conn.execute("INSERT OR IGNORE INTO item_ids(kind, value, item_id) VALUES (?, ?, ?)", (kind, value, item["id"]))
    creators = " ".join(f"{c.get('given') or ''} {c['family']}".strip() for c in item.get("creators", []))
    conn.execute("DELETE FROM items_fts WHERE item_id=?", (item["id"],))
    conn.execute(
        "INSERT INTO items_fts(item_id, title, abstract, creators, notes, fulltext) VALUES (?, ?, ?, ?, '', '')",
        (item["id"], item["title"], item.get("abstract") or "", creators),
    )


def _load(conn: sqlite3.Connection, item_id: str) -> dict:
    return json.loads(conn.execute("SELECT data FROM items WHERE id=?", (item_id,)).fetchone()["data"])


@projection("item.added")
def _item_added(conn: sqlite3.Connection, ev: Event) -> None:
    _write_item(conn, ev.payload["item"])


@projection("item.merged")
def _item_merged(conn: sqlite3.Connection, ev: Event) -> None:
    d = _load(conn, ev.entity_id)
    d["ids"].update(ev.payload["ids"])
    d["oa_urls"] = d.get("oa_urls", []) + ev.payload["oa_urls"]
    if ev.payload.get("language"):  # a source's language for an item that had none
        d["language"] = ev.payload["language"]
    if (ev.payload.get("abstract") or "").strip() and not (d.get("abstract") or "").strip():  # a source's abstract for an item that had none
        d["abstract"] = ev.payload["abstract"]
    notes = conn.execute("SELECT notes, fulltext FROM items_fts WHERE item_id=?", (ev.entity_id,)).fetchone()
    _write_item(conn, d)
    if notes:
        conn.execute(
            "UPDATE items_fts SET notes=?, fulltext=? WHERE item_id=?", (notes["notes"], notes["fulltext"], ev.entity_id)
        )


@projection("item.updated")
def _item_updated(conn: sqlite3.Connection, ev: Event) -> None:
    d = _load(conn, ev.entity_id)
    d.update(ev.payload["fields"])
    conn.execute("UPDATE items SET data=? WHERE id=?", (canonical_json(d), ev.entity_id))


@projection("item.tagged")
def _item_tagged(conn: sqlite3.Connection, ev: Event) -> None:
    for t in ev.payload["add"]:
        conn.execute("INSERT OR IGNORE INTO tags(item_id, tag) VALUES (?, ?)", (ev.entity_id, t))
    for t in ev.payload["remove"]:
        conn.execute("DELETE FROM tags WHERE item_id=? AND tag=?", (ev.entity_id, t))


@projection("collection.created")
def _collection_created(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO collections(id, name, parent_id, query) VALUES (?, ?, ?, ?)",
        (ev.entity_id, p["name"], p["parent_id"], p["query"]),
    )


@projection("item.collected")
def _item_collected(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO collection_items(collection_id, item_id) VALUES (?, ?)",
        (ev.payload["collection_id"], ev.entity_id),
    )


def fts_append(conn: sqlite3.Connection, item_id: str, column: Literal["notes", "fulltext"], text: str) -> None:
    """Append text to an item's FTS column (used by notes and full-text projections)."""
    r = conn.execute(f"SELECT {column} FROM items_fts WHERE item_id=?", (item_id,)).fetchone()
    if r is None:
        return
    joined = (r[column] + "\n" + text).strip() if r[column] else text
    conn.execute(f"UPDATE items_fts SET {column}=? WHERE item_id=?", (joined, item_id))
