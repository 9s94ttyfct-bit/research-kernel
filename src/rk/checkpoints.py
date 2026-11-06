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

# Spec §5.3: the AI-weak-spot checkpoints plus login/captcha waits. In P2 every one of them needs a human; the
# vote path (P1) arrives with the orchestrator and may never answer these.
HARD_HUMAN_KINDS = frozenset({
    "topic_review", "rq_approval", "captcha_wait", "login_wait", "uncertain_support", "protocol_approval",
    "screening_disagreements", "evidence_conflict", "significance_novelty", "final_review",
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
        if cp["status"] != "open":
            raise ValueError(f"checkpoint {chk_id} is {cp['status']}, not open")
        if answered_by == "vote" and cp["kind"] in HARD_HUMAN_KINDS:
            raise ValueError(f"{cp['kind']} checkpoints can only be answered by a human")
        ps.apply(NewEvent(type="checkpoint.answered", entity_id=chk_id,
                          actor_kind="human" if answered_by == "human" else "kernel", actor=actor,
                          payload={"answer": answer, "answered_by": answered_by, "answered_at": _now()}))


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
