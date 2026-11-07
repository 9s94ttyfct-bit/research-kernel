"""Checkpoints: questions only a human (or, for non-hard kinds in P1, a multi-vendor vote) may answer (spec §5.3).

No agent role has a tool to answer a checkpoint; answering goes through the human CLI (with confirmation) or the
Workbench."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from rk.ids import new_id
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection

# Spec §5.3: the AI-weak-spot checkpoints plus login/captcha waits, and stage_blocked (only the researcher may allow
# one more use of an edge). In P2 every one of them needs a human; the vote path (P1) may never answer these.
HARD_HUMAN_KINDS = frozenset({
    "topic_review", "rq_approval", "captcha_wait", "login_wait", "uncertain_support", "protocol_approval",
    "screening_disagreements", "evidence_conflict", "significance_novelty", "final_review", "stage_blocked",
})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_checkpoint(ps: ProjectStore, kind: str, prompt: str, options: list | dict | None = None, *,
                      task_id: str | None = None, stage: str | None = None, actor: str) -> str:
    cid = new_id("chk")
    ps.apply(NewEvent(type="checkpoint.created", entity_id=cid, actor_kind="kernel", actor=actor,
                      payload={"kind": kind, "stage": stage, "prompt": prompt, "options": options, "task_id": task_id,
                               "created": _now()}))
    return cid


def get_checkpoint(ps: ProjectStore, chk_id: str) -> dict:
    r = ps.db.execute("SELECT * FROM checkpoints WHERE id=?", (chk_id,)).fetchone()
    if r is None:
        raise KeyError(chk_id)
    d = dict(r)
    d["options"] = json.loads(d["options"]) if d["options"] else None
    d["answer"] = json.loads(d["answer"]) if d["answer"] else None
    return d


def list_checkpoints(ps: ProjectStore, status: str | None = None) -> list[dict]:
    q = "SELECT id FROM checkpoints" + (" WHERE status=?" if status else "") + " ORDER BY created, id"
    return [get_checkpoint(ps, r["id"]) for r in ps.db.execute(q, (status,) if status else ())]


def answer_checkpoint(ps: ProjectStore, chk_id: str, answer: dict, *, answered_by: Literal["human", "vote"],
                      actor: str) -> None:
    with ps.lock:
        cp = get_checkpoint(ps, chk_id)
        if cp["status"] == "superseded":
            raise ValueError(_superseded_message(ps, chk_id))
        if cp["status"] != "open":
            raise ValueError(f"checkpoint {chk_id} is {cp['status']}, not open")
        if answered_by == "vote" and cp["kind"] in HARD_HUMAN_KINDS:
            raise ValueError(f"{cp['kind']} checkpoints can only be answered by a human")
        ps.apply(NewEvent(type="checkpoint.answered", entity_id=chk_id,
                          actor_kind="human" if answered_by == "human" else "kernel", actor=actor,
                          payload={"answer": answer, "answered_by": answered_by, "answered_at": _now()}))


def answered_by_actor(ps: ProjectStore, chk_id: str) -> str | None:
    """Who answered the checkpoint (the actor of its latest answer), or ``None`` while it is unanswered."""
    r = ps.db.execute("SELECT actor FROM events WHERE type='checkpoint.answered' AND entity_id=? ORDER BY seq DESC "
                      "LIMIT 1", (chk_id,)).fetchone()
    return r["actor"] if r else None


def supersede_open(ps: ProjectStore, stage: str, attempt: int, *, actor: str) -> list[str]:
    """Mark every open checkpoint of ``stage`` superseded: attempt ``attempt`` replaces the attempts they belong to
    (an attempt that was interrupted after opening one, or a stage reopened while one was open). A superseded
    checkpoint leaves the open list, and an answer to it is refused (it would never be applied). Returns their ids."""
    with ps.lock:
        ids = [r["id"] for r in ps.db.execute("SELECT id FROM checkpoints WHERE stage=? AND status='open'", (stage,))]
        for cid in ids:
            ps.apply(NewEvent(type="checkpoint.superseded", entity_id=cid, actor_kind="kernel", actor=actor,
                              payload={"stage": stage, "attempt": attempt, "at": _now()}))
    return ids


def _superseded_message(ps: ProjectStore, chk_id: str) -> str:
    r = ps.db.execute("SELECT payload FROM events WHERE type='checkpoint.superseded' AND entity_id=? ORDER BY seq DESC "
                      "LIMIT 1", (chk_id,)).fetchone()
    p = json.loads(r["payload"]) if r else {"stage": "?", "attempt": "?"}
    return (f"checkpoint {chk_id} was superseded: attempt {p['attempt']} of {p['stage']!r} replaced the attempt it "
            f"belonged to; answer the open checkpoint of {p['stage']!r} instead")


def opened_in_place_of(ps: ProjectStore, kind: str, chk_id: str) -> str | None:
    """The checkpoint of ``kind`` a worker opened in answer to ``chk_id`` (``options.replaces``), if any; a worker
    handling the same answer again finds it here instead of opening a second one."""
    r = ps.db.execute("SELECT id FROM checkpoints WHERE kind=? AND json_extract(options, '$.replaces')=? "
                      "ORDER BY created, id LIMIT 1", (kind, chk_id)).fetchone()
    return r["id"] if r else None


@projection("checkpoint.created")
def _created(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO checkpoints(id, kind, stage, prompt, options, task_id, status, created) "
                 "VALUES (?,?,?,?,?,?, 'open', ?)",
                 (ev.entity_id, p["kind"], p["stage"], p["prompt"], json.dumps(p["options"]), p["task_id"],
                  p["created"]))


@projection("checkpoint.answered")
def _answered(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("UPDATE checkpoints SET status='answered', answer=?, answered_by=?, answered_at=? WHERE id=?",
                 (json.dumps(p["answer"]), p["answered_by"], p["answered_at"], ev.entity_id))


@projection("checkpoint.superseded")
def _superseded(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE checkpoints SET status='superseded', answered_at=? WHERE id=? AND status='open'",
                 (ev.payload["at"], ev.entity_id))
