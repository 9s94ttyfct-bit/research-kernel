"""Event-sourced orchestrator state: stage attempts, gates and waits; back-edge uses; runs and their sessions; and
which run, session, stage and attempt each role task belongs to."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from rk.discovery.store import intake_of
from rk.ids import new_id
from rk.orchestrator.graph import DISCOVERY
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.tools.registry import ToolError

# Run states (Plan 4): `waiting_human` is reserved for runs with work besides the wait; the discovery stages run one at
# a time, so a wait pauses the run (`paused_waiting_human`).
TERMINAL_RUN_STATES = frozenset({"done", "failed", "cancelled"})
RUN_STATES = frozenset({"running", "waiting_human", "paused_waiting_human", "paused_budget", "paused"}) \
    | TERMINAL_RUN_STATES


def _emit(ps: ProjectStore, type_: str, entity_id: str, payload: dict, actor: str) -> None:
    ps.apply(NewEvent(type=type_, entity_id=entity_id, actor_kind="kernel", actor=actor, payload=payload))


# ---- role tasks ----
def record_run_task(ps: ProjectStore, *, task_id: str, run_id: str | None, session: int | None, stage: str,
                    attempt: int, actor: str = "kernel") -> None:
    """Link a finished role task to its stage attempt (and, in D2, to its run session)."""
    _emit(ps, "run.task", task_id, {"task_id": task_id, "run_id": run_id, "session": session, "stage": stage,
                                    "attempt": attempt}, actor)


def tasks_used(ps: ProjectStore, run_id: str, session: int | None, stage: str) -> int:
    """Role tasks counted against the stage budget of one run session. A task that came back ``rate_capped`` made no
    model call and is not counted."""
    return ps.db.execute(
        "SELECT COUNT(*) FROM run_tasks r LEFT JOIN tasks t ON t.id = r.task_id WHERE r.run_id=? AND r.session IS ? "
        "AND r.stage=? AND COALESCE(t.error, '') != 'rate_capped'", (run_id, session, stage)).fetchone()[0]


# ---- stage events ----
def record_attempt(ps: ProjectStore, stage: str, attempt: int, *, run_id: str | None, guidance: str,
                   requested: str | None, actor: str) -> None:
    """A new attempt of ``stage``. ``guidance`` is everything the worker gets; ``requested`` the part asked for this
    attempt (gate failures, a rerun reason, the caller's guidance), kept to rerun an interrupted attempt."""
    _emit(ps, "stage.attempt", stage, {"stage": stage, "attempt": attempt, "run_id": run_id, "guidance": guidance,
                                       "requested": requested}, actor)


def record_gate(ps: ProjectStore, stage: str, attempt: int, ok: bool, failures: list[str], *, actor: str) -> None:
    _emit(ps, "stage.gate", stage, {"stage": stage, "attempt": attempt, "ok": ok, "failures": list(failures)}, actor)


def record_passed(ps: ProjectStore, stage: str, attempt: int, *, actor: str) -> None:
    _emit(ps, "stage.passed", stage, {"stage": stage, "attempt": attempt}, actor)


def record_reopened(ps: ProjectStore, stage: str, *, by_edge: str, guidance: str | None, actor: str) -> None:
    """``stage`` needs a new attempt (it is the target of ``by_edge`` or comes after it); ``guidance`` goes to it."""
    _emit(ps, "stage.reopened", stage, {"stage": stage, "by_edge": by_edge, "guidance": guidance}, actor)


def record_waiting(ps: ProjectStore, stage: str, attempt: int, checkpoint_id: str | None, *, actor: str) -> None:
    """The attempt waits on ``checkpoint_id`` (checkpoints have no attempt, so this link is the only one); ``None``
    ends the wait once the answer was handled."""
    _emit(ps, "stage.waiting", stage, {"stage": stage, "attempt": attempt, "checkpoint_id": checkpoint_id}, actor)


def record_degraded(ps: ProjectStore, *, stage: str, attempt: int, removed: list[str], reason: str,
                    actor: str) -> None:
    """P1 deleted or narrowed content to get past an edge limit (spec §5.3/§7.2); disclosed as a limitation."""
    _emit(ps, "stage.degraded", stage, {"stage": stage, "attempt": attempt, "removed": list(removed),
                                        "reason": reason}, actor)


def record_edge_taken(ps: ProjectStore, edge: str, count: int, *, actor: str) -> None:
    _emit(ps, "edge.taken", edge, {"edge": edge, "count": count}, actor)


def record_edge_allowed(ps: ProjectStore, edge: str, *, actor: str) -> None:
    _emit(ps, "edge.allowed", edge, {"edge": edge}, actor)


# ---- runs ----
def start_run(ps: ProjectStore, *, until: str, actor: str, clock: Callable[[], float]) -> str:
    """Start a D2 run of the discovery stages up to ``until``. One run per project at a time."""
    if until not in DISCOVERY:
        raise ToolError("stage_unavailable", f"{until!r} is not a discovery stage; runs end at one of {DISCOVERY}")
    with ps.lock:
        active = active_run(ps)
        if active is not None:
            raise ToolError("run_active", f"run {active['id']} is {active['status']}", {"run_id": active["id"]})
        intake = intake_of(ps)
        run_id = new_id("run")
        _emit(ps, "run.started", run_id, {"until": until, "discovery_mode": intake.discovery_mode if intake else None,
                                          "at": clock()}, actor)
    return run_id


def record_run_status(ps: ProjectStore, run_id: str, status: str, *, reason: str = "", actor: str,
                      clock: Callable[[], float]) -> None:
    if status not in RUN_STATES:
        raise ValueError(f"unknown run status {status!r}")
    _emit(ps, "run.status", run_id, {"status": status, "reason": reason, "at": clock()}, actor)


def record_run_session(ps: ProjectStore, run_id: str, n: int, *, actor: str, clock: Callable[[], float]) -> None:
    """Session ``n`` of the run starts now: the wall clock and the stage budgets restart."""
    _emit(ps, "run.session", run_id, {"n": n, "at": clock()}, actor)


# ---- queries ----
def _stage_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["last_gate"] = json.loads(d["last_gate"]) if d["last_gate"] else None
    return d


def stage_row(ps: ProjectStore, stage: str) -> dict[str, Any] | None:
    r = ps.db.execute("SELECT * FROM stages WHERE stage=?", (stage,)).fetchone()
    return _stage_dict(r) if r else None


def stage_rows(ps: ProjectStore) -> list[dict[str, Any]]:
    """One row per discovery stage, in order; a stage that never ran is ``pending`` with attempt 0."""
    rows = {r["stage"]: _stage_dict(r) for r in ps.db.execute("SELECT * FROM stages")}
    return [rows.get(s) or {"stage": s, "status": "pending", "attempt": 0, "passed_attempt": None, "last_gate": None,
                            "waiting_checkpoint": None, "guidance": None, "updated": None}
            for s in DISCOVERY]


def current_stage(ps: ProjectStore) -> str | None:
    """The first discovery stage that has not passed; ``None`` once ``question`` passed."""
    passed = {r["stage"] for r in ps.db.execute("SELECT stage FROM stages WHERE status='passed'")}
    return next((s for s in DISCOVERY if s not in passed), None)


def latest_attempt(ps: ProjectStore, stage: str) -> int:
    r = ps.db.execute("SELECT attempt FROM stages WHERE stage=?", (stage,)).fetchone()
    return r["attempt"] if r else 0


def edge_row(ps: ProjectStore, edge: str) -> dict[str, int]:
    r = ps.db.execute("SELECT count, allowed FROM back_edges WHERE edge=?", (edge,)).fetchone()
    return {"count": r["count"], "allowed": r["allowed"]} if r else {"count": 0, "allowed": 0}


def edge_count(ps: ProjectStore, edge: str) -> int:
    return edge_row(ps, edge)["count"]


def run_row(ps: ProjectStore, run_id: str) -> dict[str, Any] | None:
    r = ps.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def active_run(ps: ProjectStore) -> dict[str, Any] | None:
    """The project's run that is not done, failed or cancelled (at most one)."""
    marks = ",".join("?" * len(TERMINAL_RUN_STATES))
    r = ps.db.execute(f"SELECT * FROM runs WHERE status NOT IN ({marks}) ORDER BY started DESC, id DESC LIMIT 1",
                      tuple(sorted(TERMINAL_RUN_STATES))).fetchone()
    return dict(r) if r else None


def degradations(ps: ProjectStore, stage: str | None = None) -> list[dict[str, Any]]:
    """Every ``stage.degraded`` event (for the disclosure and the limitations section), oldest first."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='stage.degraded' ORDER BY seq").fetchall()
    out = [json.loads(r["payload"]) for r in rows]
    return [d for d in out if stage is None or d["stage"] == stage]


# ---- projections ----
@projection("run.task")
def _run_task(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO run_tasks(task_id, run_id, session, stage, attempt) VALUES (?,?,?,?,?)",
                 (p["task_id"], p["run_id"], p["session"], p["stage"], p["attempt"]))


def _set_stage(conn: sqlite3.Connection, ev: Event, **cols: Any) -> None:
    stage = ev.payload["stage"]
    conn.execute("INSERT OR IGNORE INTO stages(stage, status, attempt, updated) VALUES (?, 'pending', 0, ?)",
                 (stage, ev.ts))
    sets = ", ".join(f"{c}=?" for c in cols)
    conn.execute(f"UPDATE stages SET {sets}, updated=? WHERE stage=?", (*cols.values(), ev.ts, stage))


@projection("stage.attempt")
def _attempt(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    _set_stage(conn, ev, status="running", attempt=p["attempt"], guidance=p["requested"], waiting_checkpoint=None)


@projection("stage.gate")
def _gate(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    _set_stage(conn, ev, last_gate=json.dumps({"attempt": p["attempt"], "ok": p["ok"], "failures": p["failures"]}))


@projection("stage.passed")
def _passed(conn: sqlite3.Connection, ev: Event) -> None:
    _set_stage(conn, ev, status="passed", passed_attempt=ev.payload["attempt"], waiting_checkpoint=None)


@projection("stage.reopened")
def _reopened(conn: sqlite3.Connection, ev: Event) -> None:
    _set_stage(conn, ev, status="reopened", guidance=ev.payload["guidance"], waiting_checkpoint=None)


@projection("stage.waiting")
def _waiting(conn: sqlite3.Connection, ev: Event) -> None:
    cid = ev.payload["checkpoint_id"]
    _set_stage(conn, ev, status="waiting" if cid else "running", waiting_checkpoint=cid)


@projection("edge.taken")
def _edge_taken(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT INTO back_edges(edge, count) VALUES (?, ?) ON CONFLICT(edge) DO UPDATE SET count=excluded.count",
                 (p["edge"], p["count"]))


@projection("edge.allowed")
def _edge_allowed(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("INSERT INTO back_edges(edge, allowed) VALUES (?, 1) "
                 "ON CONFLICT(edge) DO UPDATE SET allowed=allowed + 1", (ev.payload["edge"],))


@projection("run.started")
def _run_started(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO runs(id, status, until, session, session_started, started, ended, reason) "
                 "VALUES (?, 'running', ?, 1, ?, ?, NULL, '')", (ev.entity_id, p["until"], p["at"], p["at"]))


@projection("run.status")
def _run_status(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    ended = p["at"] if p["status"] in TERMINAL_RUN_STATES else None
    conn.execute("UPDATE runs SET status=?, reason=?, ended=? WHERE id=?",
                 (p["status"], p["reason"], ended, ev.entity_id))


@projection("run.session")
def _run_session(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE runs SET session=?, session_started=? WHERE id=?",
                 (ev.payload["n"], ev.payload["at"], ev.entity_id))
