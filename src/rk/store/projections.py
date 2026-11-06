"""Projection registry: event type -> handlers that update read tables."""

from __future__ import annotations

import importlib
import sqlite3
from collections import defaultdict
from collections.abc import Callable

from rk.store.events import Event, EventLog, NewEvent, _to_event

Handler = Callable[[sqlite3.Connection, Event], None]

PROJECTION_TABLES: tuple[str, ...] = (
    "meta", "items", "item_ids", "tags", "collections", "collection_items",
    "attachments", "snapshots", "notes", "verifications", "items_fts", "injection_flags", "checkpoints", "tasks",
    "claims", "evidence", "edges", "objections", "browse_log",
)

# Modules that register projections; imported before replay so rebuild sees every handler.
PROJECTION_MODULES: tuple[str, ...] = (
    "rk.store.project", "rk.library.service", "rk.fulltext", "rk.notes", "rk.verify.records", "rk.flags",
    "rk.checkpoints", "rk.llm.runner", "rk.argument", "rk.browser.log",
)

_HANDLERS: dict[str, list[Handler]] = defaultdict(list)


def projection(event_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if fn not in _HANDLERS[event_type]:
            _HANDLERS[event_type].append(fn)
        return fn

    return deco


def _load_modules() -> None:
    for m in PROJECTION_MODULES:
        importlib.import_module(m)


def _project(conn: sqlite3.Connection, ev: Event) -> None:
    for fn in _HANDLERS.get(ev.type, ()):
        fn(conn, ev)


def apply(conn: sqlite3.Connection, log: EventLog, e: NewEvent) -> Event:
    """Append the event and run its projections in one transaction."""
    _load_modules()
    conn.execute("BEGIN IMMEDIATE")
    try:
        ev = log.append(e)
        _project(conn, ev)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return ev


def rebuild(conn: sqlite3.Connection) -> int:
    _load_modules()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for t in PROJECTION_TABLES:
            conn.execute(f"DELETE FROM {t}")
        n = 0
        for r in conn.execute("SELECT * FROM events ORDER BY seq").fetchall():
            _project(conn, _to_event(r))
            n += 1
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return n
