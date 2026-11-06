"""Hash-chained, append-only event log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from rk.canon import canonical_json, sha256_hex
from rk.ids import new_id

ActorKind = Literal["human", "agent", "kernel"]


class NewEvent(BaseModel):
    type: str
    entity_id: str | None = None
    payload: dict[str, Any]
    actor_kind: ActorKind
    actor: str


class Event(NewEvent):
    seq: int
    evt_id: str
    ts: str
    project_id: str
    prev_hash: str | None
    hash: str


class ChainReport(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None = None


_COLUMNS = ("seq", "evt_id", "ts", "project_id", "actor_kind", "actor", "type", "entity_id", "payload", "prev_hash")


def _row_hash(row: dict[str, Any]) -> str:
    body = {k: row[k] for k in _COLUMNS}
    return sha256_hex((row["prev_hash"] or "") + canonical_json(body))


def _to_event(r: sqlite3.Row) -> Event:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return Event.model_validate(d)


class EventLog:
    def __init__(self, conn: sqlite3.Connection, project_id: str):
        self.conn = conn
        self.project_id = project_id

    def append(self, e: NewEvent) -> Event:
        """Append inside the caller's open transaction."""
        last = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        row: dict[str, Any] = {
            "seq": (last["seq"] + 1) if last else 1,
            "evt_id": new_id("evt"),
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "project_id": self.project_id,
            "actor_kind": e.actor_kind,
            "actor": e.actor,
            "type": e.type,
            "entity_id": e.entity_id,
            "payload": canonical_json(e.payload),
            "prev_hash": last["hash"] if last else None,
        }
        row["hash"] = _row_hash(row)
        cols = ",".join(row)
        self.conn.execute(f"INSERT INTO events({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return Event.model_validate({**row, "payload": e.payload})

    def iter(self) -> Iterator[Event]:
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            yield _to_event(r)

    def verify_chain(self) -> ChainReport:
        prev: str | None = None
        n = 0
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            d = dict(r)
            n += 1
            if d["prev_hash"] != prev or _row_hash(d) != d["hash"]:
                return ChainReport(ok=False, checked=n, first_bad_seq=d["seq"])
            prev = d["hash"]
        return ChainReport(ok=True, checked=n)
