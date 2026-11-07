"""Read helpers shared by the event-sourced artifact stores (discovery, production)."""

from __future__ import annotations

import json
import sqlite3

from rk.store.project import ProjectStore


def require_row(ps: ProjectStore, table: str, entity_id: str) -> sqlite3.Row:
    """The row of ``table`` with id ``entity_id``; ``KeyError`` when there is none."""
    r = ps.db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    if r is None:
        raise KeyError(f"no {table[:-1]} {entity_id}")
    return r


def query_rows(ps: ProjectStore, sql: str, params: tuple, json_cols: tuple[str, ...] = ()) -> list[dict]:
    """The rows of ``sql`` as dicts, with the JSON columns ``json_cols`` decoded (NULL stays None)."""
    out = []
    for r in ps.db.execute(sql, params):
        d = dict(r)
        for c in json_cols:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        out.append(d)
    return out
