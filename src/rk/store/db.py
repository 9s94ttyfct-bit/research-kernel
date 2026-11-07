"""SQLite connection setup for project databases."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

# Columns added to a projection table after it first shipped: ``CREATE TABLE IF NOT EXISTS`` leaves an older table as
# it was, so ``open_project_db`` adds the missing ones (table -> (column, declaration)), then ``_BACKFILL`` fills them
# from the events of the rows already there.
_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "search_log": (("after_year", "INTEGER"), ("offset_n", "INTEGER NOT NULL DEFAULT 0"), ("date_from", "INTEGER"),
                   ("date_to", "INTEGER"), ("incomplete", "TEXT"), ("stopped_at_cap", "INTEGER NOT NULL DEFAULT 0"),
                   ("dropped", "INTEGER NOT NULL DEFAULT 0")),
    "screening": (("carried_from", "INTEGER"),),
    "screening_final": (("carried_from", "INTEGER"),),
    "extractions": (("carried_from", "INTEGER"),),
}
_BACKFILL: dict[str, str] = {
    "search_log": "UPDATE search_log SET after_year=(SELECT json_extract(e.payload, '$.after_year') FROM events e "
                  "WHERE e.type='search.logged' AND e.entity_id=search_log.id)",
}


def _add_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing = [(c, decl) for c, decl in cols if c not in have]
        for c, decl in missing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {decl}")
        if missing and table in _BACKFILL:
            conn.execute(_BACKFILL[table])


def open_project_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(files("rk.store").joinpath("schema.sql").read_text(encoding="utf-8"))
    _add_columns(conn)
    return conn
