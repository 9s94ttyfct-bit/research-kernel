"""Resuming an interrupted attempt of an enumerated stage (``search_screen``, later ``read_extract``).

A rate cap, a budget or a crash interrupts an attempt (it has no ``stage.ran``); the engine then runs the stage again
as a new attempt. An enumerated stage's work grows with its records, so the hourly call caps would interrupt every
attempt of a large one at about the same point and it would never finish. The new attempt therefore carries over the
work of the interrupted attempts right before it, item by item, instead of doing it again:

- only the attempts immediately before it that were interrupted (``carry_attempts``): an attempt that ran to
  completion (a rerun after a gate failure, a back-edge) ends the chain, so its work is never carried over;
- only those made under the same frozen protocol (``mark_protocol``: the hash an attempt works under, written before
  its work starts); an interrupted attempt under another protocol ends the chain too;
- keyed by library item, not record (records are attempt-keyed; the search runs again and gives the same items), the
  newest attempt first.

A carried row keeps the attempt it was made in (``carried_from``), so PRISMA and audits can tell it apart."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from rk.orchestrator.state import attempt_ran
from rk.production.models import ScreeningPhase
from rk.production.store import decisions, finals, records
from rk.store.events import NewEvent
from rk.store.project import ProjectStore


def mark_protocol(ps: ProjectStore, stage: str, attempt: int, protocol_hash: str, *, actor: str) -> None:
    """The frozen protocol (its hash) the attempt works under; written before the attempt's work starts."""
    ps.apply(NewEvent(type="attempt.protocol", entity_id=None, actor_kind="kernel", actor=actor,
                      payload={"stage": stage, "attempt": attempt, "protocol_hash": protocol_hash}))


def protocol_of(ps: ProjectStore, stage: str, attempt: int) -> str | None:
    """The protocol hash the attempt worked under, or ``None`` when it never got that far."""
    r = ps.db.execute("SELECT payload FROM events WHERE type='attempt.protocol' AND json_extract(payload, '$.stage')=? "
                      "AND json_extract(payload, '$.attempt')=? ORDER BY seq DESC LIMIT 1", (stage, attempt)).fetchone()
    return json.loads(r["payload"])["protocol_hash"] if r else None


def carry_attempts(ps: ProjectStore, stage: str, attempt: int, protocol_hash: str) -> list[int]:
    """The attempts whose work ``attempt`` carries over, newest first: the interrupted attempts right before it under
    ``protocol_hash``, up to the first attempt that ran to completion or worked under another protocol. An interrupted
    attempt that never marked its protocol did no work and is passed over."""
    out: list[int] = []
    for a in range(attempt - 1, 0, -1):
        if attempt_ran(ps, stage, a):
            break
        h = protocol_of(ps, stage, a)
        if h is not None and h != protocol_hash:
            break
        if h is not None:
            out.append(a)
    return out


@dataclass
class Carried:
    """What the interrupted attempts decided on one item in a phase: each screener's decision row and the final row
    (each with ``carried_from``, the attempt it was made in)."""
    decisions: dict[str, dict] = field(default_factory=dict)
    final: dict | None = None


def carried_screening(ps: ProjectStore, stage: str, attempt: int, protocol_hash: str,
                      phase: ScreeningPhase) -> dict[str, Carried]:
    """item id -> what ``attempt`` carries over of the screening of ``phase`` (``carry_attempts``), the newest
    decision of each screener and the newest final."""
    out: dict[str, Carried] = {}
    for a in carry_attempts(ps, stage, attempt, protocol_hash):
        items = {r["id"]: r["item_id"] for r in records(ps, a)}
        for d in decisions(ps, a, phase=phase):
            c = out.setdefault(items[d["record_id"]], Carried())
            c.decisions.setdefault(d["screener"], {**d, "carried_from": d["carried_from"] or a})
        for f in finals(ps, a, phase=phase):
            c = out.setdefault(items[f["record_id"]], Carried())
            if c.final is None:
                c.final = {**f, "carried_from": f["carried_from"] or a}
    return out
