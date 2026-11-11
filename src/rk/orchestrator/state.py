"""Event-sourced orchestrator state: stage attempts, gates and waits; back-edge uses; runs and their sessions; and
which run, session, stage and attempt each role task belongs to."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from rk.discovery.store import intake_of
from rk.ids import new_id
from rk.orchestrator.graph import DISCOVERY, PRODUCTION, stages_of
from rk.roles.policy import JUDGE_ROLES
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
    """Link a role task to its stage attempt (and, in D2, to its run session). ``KernelExecutor`` links it as soon as
    it is created, so a task cut off by a restart is linked too."""
    _emit(ps, "run.task", task_id, {"task_id": task_id, "run_id": run_id, "session": session, "stage": stage,
                                    "attempt": attempt}, actor)


def tasks_used(ps: ProjectStore, run_id: str, session: int | None, stage: str) -> int:
    """Role tasks counted against the stage budget of one run session, those still running or ``interrupted`` by a
    restart included (their call may have used quota). A task that came back ``rate_capped`` made no model call, and
    one that came back ``session_limited`` did no work (its driver's usage limit was reached): neither is counted. Nor
    is a judge's task (``JUDGE_ROLES``: V-SUPPORT, V-NOVELTY, already-done and the P1 votes): the budget caps the
    stage's exploration, not the checks of what it produced (your decision, 2026-11-11, after argue spent three run
    sessions' budgets mostly on judges)."""
    marks = ", ".join("?" * len(JUDGE_ROLES))
    return ps.db.execute(
        "SELECT COUNT(*) FROM run_tasks r LEFT JOIN tasks t ON t.id = r.task_id WHERE r.run_id=? AND r.session IS ? "
        f"AND r.stage=? AND COALESCE(t.error, '') NOT IN ('rate_capped', 'session_limited') "
        f"AND COALESCE(t.role, '') NOT IN ({marks})",
        (run_id, session, stage, *JUDGE_ROLES)).fetchone()[0]


# ---- stage events ----
def record_attempt(ps: ProjectStore, stage: str, attempt: int, *, run_id: str | None, guidance: str,
                   requested: str | None, actor: str) -> None:
    """A new attempt of ``stage``. ``guidance`` is everything the worker gets; ``requested`` the part asked for this
    attempt (gate failures, a rerun reason, the caller's guidance), kept to rerun an interrupted attempt."""
    _emit(ps, "stage.attempt", stage, {"stage": stage, "attempt": attempt, "run_id": run_id, "guidance": guidance,
                                       "requested": requested}, actor)


def record_ran(ps: ProjectStore, stage: str, attempt: int, status: str, reason: str, *, actor: str) -> None:
    """The worker's ``run`` of the attempt returned ``status`` (with ``reason``). An attempt without this event was
    interrupted (a rate cap, a budget or a crash) and is run again with the same guidance: in place
    (``record_resumed``) when its worker resumes in place, else as a new attempt."""
    _emit(ps, "stage.ran", stage, {"stage": stage, "attempt": attempt, "status": status, "reason": reason}, actor)


def record_resumed(ps: ProjectStore, stage: str, attempt: int, *, run_id: str | None, actor: str) -> None:
    """The interrupted ``attempt`` (no ``stage.ran``) runs again in place: its worker resumes from what the attempt
    stored (``Worker.resumes_in_place``)."""
    _emit(ps, "stage.resumed", stage, {"stage": stage, "attempt": attempt, "run_id": run_id}, actor)


MAX_STALLED_RESUMES = 3  # resumes in a row that crashed without storing anything before an attempt is given up
_NO_PROGRESS = ("stage.resumed", "stage.crashed", "run.started", "run.status", "run.session")


def record_crashed(ps: ProjectStore, stage: str, attempt: int, error: str, *, actor: str) -> None:
    """The worker's run of the attempt ended in an exception that is no wait or pause (not a rate cap, a budget or a
    transient source error): a crash, which ``stalled_resumes`` counts."""
    _emit(ps, "stage.crashed", stage, {"stage": stage, "attempt": attempt, "error": error[:500]}, actor)


def stalled_resumes(ps: ProjectStore, stage: str, attempt: int) -> str | None:
    """Why the interrupted ``attempt`` is not resumed again: each of its last ``MAX_STALLED_RESUMES`` resumes crashed
    (``stage.crashed``) without storing anything in between (no task, note or other event: the same crash each time,
    in one run or across new runs); else ``None``. A resume that ended waiting (a rate cap, a source outage, a budget
    pause) is no crash, so an attempt is never given up for waiting. A killed process writes no ``stage.crashed``, so
    a kill/restart loop is bounded only by the stage budget (``tasks_used``) where the stage has one; each restart
    needs a human."""
    rows = ps.db.execute("SELECT seq, type FROM events WHERE type IN ('stage.resumed', 'stage.crashed') AND "
                         "json_extract(payload, '$.stage')=? AND json_extract(payload, '$.attempt')=? ORDER BY seq",
                         (stage, attempt)).fetchall()
    marks = ",".join("?" * len(_NO_PROGRESS))
    stalled = 0
    for i, r in enumerate(rows):
        if r["type"] != "stage.resumed":
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        crashed = nxt is not None and nxt["type"] == "stage.crashed" and ps.db.execute(
            f"SELECT 1 FROM events WHERE seq>? AND seq<? AND type NOT IN ({marks}) LIMIT 1",
            (r["seq"], nxt["seq"], *_NO_PROGRESS)).fetchone() is None
        stalled = stalled + 1 if crashed else 0
    if stalled < MAX_STALLED_RESUMES:
        return None
    return (f"{stage!r} attempt {attempt} was resumed {stalled} times in a row and crashed each time without storing "
            f"anything: a new attempt starts")


def record_resume_abandoned(ps: ProjectStore, stage: str, attempt: int, reason: str, *, actor: str) -> None:
    """The interrupted ``attempt`` is not resumed again (``stalled_resumes``); a new attempt replaces it."""
    _emit(ps, "stage.resume_abandoned", stage, {"stage": stage, "attempt": attempt, "reason": reason}, actor)


def attempt_ran(ps: ProjectStore, stage: str, attempt: int) -> bool:
    """Whether the worker's ``run`` of the attempt returned (see ``record_ran``)."""
    return ps.db.execute("SELECT 1 FROM events WHERE type='stage.ran' AND json_extract(payload, '$.stage')=? AND "
                         "json_extract(payload, '$.attempt')=? LIMIT 1", (stage, attempt)).fetchone() is not None


def attempt_guidance(ps: ProjectStore, stage: str, attempt: int) -> str:
    """Everything the worker got as guidance for the attempt (``record_attempt``)."""
    r = ps.db.execute("SELECT payload FROM events WHERE type='stage.attempt' AND json_extract(payload, '$.stage')=? "
                      "AND json_extract(payload, '$.attempt')=? ORDER BY seq DESC LIMIT 1", (stage, attempt)).fetchone()
    return (json.loads(r["payload"])["guidance"] or "") if r else ""


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
                    actor: str, questions: list[str] | None = None) -> None:
    """P1 deleted or narrowed content to get past an edge limit (spec §5.3/§7.2); disclosed as a limitation.
    ``questions``: the reader questions (revise) whose content the deletion removed, which the gate then accepts."""
    payload = {"stage": stage, "attempt": attempt, "removed": list(removed), "reason": reason}
    if questions:
        payload["questions"] = list(questions)
    _emit(ps, "stage.degraded", stage, payload, actor)


def record_edge_taken(ps: ProjectStore, edge: str, count: int, *, actor: str) -> None:
    _emit(ps, "edge.taken", edge, {"edge": edge, "count": count}, actor)


def record_edge_allowed(ps: ProjectStore, edge: str, *, actor: str) -> None:
    _emit(ps, "edge.allowed", edge, {"edge": edge}, actor)


# ---- guided (D1) stage calls ----
def record_d1_finished(ps: ProjectStore, outcome: dict, *, actor: str) -> None:
    """A guided stage call that ran in the background finished: ``outcome`` holds its action, stage, start and end
    times, the call's result (``outcome``) or its ``error``. Kept as an event so ``rk_stage_status`` shows it after a
    restart too."""
    _emit(ps, "d1.finished", ps.project_id, outcome, actor)


def last_d1(ps: ProjectStore) -> dict[str, Any] | None:
    """The last guided stage call that finished (``record_d1_finished``), or ``None``."""
    r = ps.db.execute("SELECT payload FROM events WHERE type='d1.finished' ORDER BY seq DESC LIMIT 1").fetchone()
    return json.loads(r["payload"]) if r else None


# ---- runs ----
def start_run(ps: ProjectStore, *, until: str, actor: str, clock: Callable[[], float]) -> str:
    """Start a run of the project's stages from the current one up to ``until`` (a stage of its paper type, not one
    that already passed). One run per project at a time. Whether every stage up to ``until`` has a worker, and the
    discovery mode, are the caller's checks (``RunManager.start``)."""
    stages = stages_of(ps)
    if until not in stages:
        raise ToolError("stage_unavailable", f"{until!r} is not a stage of this project; its stages are "
                                             f"{', '.join(stages)}")
    with ps.lock:
        active = active_run(ps)
        if active is not None:
            raise ToolError("run_active", f"run {active['id']} is {active['status']}", {"run_id": active["id"]})
        current = current_stage(ps)
        if current is None or stages.index(until) < stages.index(current):
            raise ToolError("stage_unavailable", f"{until!r} already passed; the current stage is {current!r}",
                            {"current": current})
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


def passed_attempt(ps: ProjectStore, stage: str) -> int | None:
    """The stage's passed attempt (kept while it is reopened or runs again), else ``None``."""
    row = stage_row(ps, stage)
    return row["passed_attempt"] if row is not None and row["passed_attempt"] else None


def stage_rows(ps: ProjectStore) -> list[dict[str, Any]]:
    """One row per stage of the project, in order; a stage that never ran is ``pending`` with attempt 0."""
    rows = {r["stage"]: _stage_dict(r) for r in ps.db.execute("SELECT * FROM stages")}
    return [rows.get(s) or {"stage": s, "status": "pending", "attempt": 0, "passed_attempt": None, "last_gate": None,
                            "waiting_checkpoint": None, "guidance": None, "updated": None}
            for s in stages_of(ps)]


def shown_attempt(ps: ProjectStore, stage: str) -> int | None:
    """The attempt whose artifacts a stage shows (the read tools, the Workbench): the one that passed, else the latest
    that ran (an interrupted attempt, without ``stage.ran``, may be partial); ``None`` before any ran."""
    row = stage_row(ps, stage)
    if row is None:
        return None
    if row["status"] == "passed":
        return row["passed_attempt"]
    return next((n for n in range(row["attempt"], 0, -1) if attempt_ran(ps, stage, n)), None)


def current_stage(ps: ProjectStore) -> str | None:
    """The first stage of the project that has not passed; ``None`` once every stage passed."""
    passed = {r["stage"] for r in ps.db.execute("SELECT stage FROM stages WHERE status='passed'")}
    return next((s for s in stages_of(ps) if s not in passed), None)


def discovery_passed(ps: ProjectStore) -> bool:
    """Whether every discovery stage passed (question included)."""
    return current_stage(ps) not in DISCOVERY


def production_began(ps: ProjectStore) -> bool:
    """Whether a production stage (protocol on) ever had an attempt. A latch: it stays true when a back-edge reopens a
    discovery stage (protocol->question)."""
    marks = ",".join("?" * len(PRODUCTION))
    return ps.db.execute(f"SELECT 1 FROM stages WHERE stage IN ({marks}) AND attempt > 0 LIMIT 1",
                         PRODUCTION).fetchone() is not None


def orchestrator_drives(ps: ProjectStore) -> bool:
    """Whether the project's stages are the orchestrator's from now on (spec §8.1): every discovery stage passed, or
    production began. Then runs drive the stages in D1 and D2 projects alike, guided stage calls are refused and the
    Lead only reads, also after a back-edge reopened a discovery stage."""
    return discovery_passed(ps) or production_began(ps)


def run_drives(project: str) -> ToolError:
    """The refusal of a guided (D1) stage call once ``orchestrator_drives``."""
    return ToolError("stage_unavailable", f"from protocol on, the orchestrator drives the stages of {project} in a run "
                                          f"(rk run {project} --until <stage>), not guided stage calls")


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


def latest_run(ps: ProjectStore) -> dict[str, Any] | None:
    """The project's most recent run, whatever its status."""
    r = ps.db.execute("SELECT * FROM runs ORDER BY started DESC, id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def rate_capped_driver(ps: ProjectStore, run_id: str) -> str | None:
    """The driver (``provider[:model]``) of the run's latest role task that came back ``rate_capped`` or
    ``session_limited`` (``CallCaps.wait_s`` then says how long it waits), if any."""
    r = ps.db.execute("SELECT t.driver FROM run_tasks r JOIN tasks t ON t.id = r.task_id WHERE r.run_id=? AND "
                      "t.error IN ('rate_capped', 'session_limited') ORDER BY r.rowid DESC LIMIT 1",
                      (run_id,)).fetchone()
    return r["driver"] if r else None


def edge_rows(ps: ProjectStore) -> dict[str, dict[str, int]]:
    """Every back-edge (or rerun ``X->X``) used or granted so far: its uses and the extra uses you allowed."""
    return {r["edge"]: {"count": r["count"], "allowed": r["allowed"]}
            for r in ps.db.execute("SELECT edge, count, allowed FROM back_edges ORDER BY edge")}


def degradations(ps: ProjectStore, stage: str | None = None) -> list[dict[str, Any]]:
    """Every ``stage.degraded`` event (for the disclosure and the limitations section), oldest first."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='stage.degraded' ORDER BY seq").fetchall()
    out = [json.loads(r["payload"]) for r in rows]
    return [d for d in out if stage is None or d["stage"] == stage]


def degraded_questions(ps: ProjectStore, stage: str, attempt: int) -> set[str]:
    """The questions whose content the stage's P1 degrade deleted in the attempt (``stage.degraded``'s
    ``questions``)."""
    return {q for d in degradations(ps, stage) if d.get("attempt") == attempt for q in d.get("questions") or []}


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
