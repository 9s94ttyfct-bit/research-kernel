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
    "hypotheses": (("rival_of", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'proposed'"), ("prereg_hash", "TEXT")),
    "protocol_deviations": (("kind", "TEXT NOT NULL DEFAULT 'version'"),),
}
_BACKFILL: dict[str, str] = {
    "search_log": "UPDATE search_log SET after_year=(SELECT json_extract(e.payload, '$.after_year') FROM events e "
                  "WHERE e.type='search.logged' AND e.entity_id=search_log.id)",
}


def _missing(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """table -> its ``_ADDED_COLUMNS`` the table does not have yet."""
    out = {}
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if missing := [(c, decl) for c, decl in cols if c not in have]:
            out[table] = missing
    return out


def _add_columns(conn: sqlite3.Connection) -> None:
    """Add the missing columns and backfill them in one write transaction (``BEGIN IMMEDIATE``, the columns read again
    inside it): another process opening the project at the same time finds them added, and a crash adds none, so
    the next open migrates again."""
    if not _missing(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, missing in _missing(conn).items():
            for c, decl in missing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {decl}")
            if table in _BACKFILL:
                conn.execute(_BACKFILL[table])
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def open_project_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")  # first: switching a new file to WAL waits for another opener too
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(files("rk.store").joinpath("schema.sql").read_text(encoding="utf-8"))
    _add_columns(conn)
    return conn
