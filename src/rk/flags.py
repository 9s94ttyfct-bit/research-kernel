"""Suspected prompt injections reported by agents (spec §9.3, ``rk_flag_injection``)."""

from __future__ import annotations

import sqlite3

from rk.ids import new_id
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection


def flag_injection(ps: ProjectStore, source_id: str, snippet: str, reason: str, *, actor: str,
                   actor_kind: str = "agent") -> str:
    fid = new_id("ver")
    ps.apply(NewEvent(type="injection.flagged", entity_id=fid, actor_kind=actor_kind, actor=actor,
                      payload={"item_id": source_id, "snippet": snippet[:2000], "reason": reason[:500]}))
    return fid


def list_flags(ps: ProjectStore) -> list[dict]:
    return [dict(r) for r in ps.db.execute("SELECT * FROM injection_flags ORDER BY ts")]


@projection("injection.flagged")
def _flagged(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO injection_flags(id, item_id, snippet, reason, actor, ts) VALUES (?,?,?,?,?,?)",
                 (ev.entity_id, p["item_id"], p["snippet"], p["reason"], ev.actor, ev.ts))
