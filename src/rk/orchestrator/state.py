"""Event-sourced orchestrator state: which run, session, stage and attempt each role task belongs to."""

from __future__ import annotations

import sqlite3

from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection


def record_run_task(ps: ProjectStore, *, task_id: str, run_id: str | None, session: int | None, stage: str,
                    attempt: int, actor: str = "kernel") -> None:
    """Link a finished role task to its stage attempt (and, in D2, to its run session)."""
    ps.apply(NewEvent(type="run.task", entity_id=task_id, actor_kind="kernel", actor=actor,
                      payload={"task_id": task_id, "run_id": run_id, "session": session, "stage": stage,
                               "attempt": attempt}))


def tasks_used(ps: ProjectStore, run_id: str, session: int | None, stage: str) -> int:
    """Role tasks counted against the stage budget of one run session. A task that came back ``rate_capped`` made no
    model call and is not counted."""
    return ps.db.execute(
        "SELECT COUNT(*) FROM run_tasks r LEFT JOIN tasks t ON t.id = r.task_id WHERE r.run_id=? AND r.session IS ? "
        "AND r.stage=? AND COALESCE(t.error, '') != 'rate_capped'", (run_id, session, stage)).fetchone()[0]


@projection("run.task")
def _run_task(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO run_tasks(task_id, run_id, session, stage, attempt) VALUES (?,?,?,?,?)",
                 (p["task_id"], p["run_id"], p["session"], p["stage"], p["attempt"]))
