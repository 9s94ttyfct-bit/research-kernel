"""``run_task``: the one entry point for role tasks. Resolves the driver, enforces call caps, scopes a task token,
retries (spec §7.2: at most 3 attempts) and writes every attempt to the project's event log (spec §8.2). A driver's
usage-limit reply (``SESSION_LIMITED``, ``rk.llm.limits``) is not retried: it blocks the driver in the call caps until
the limit resets. A chat API's HTTP 429 is one too: at once when its ``Retry-After`` or body says how long, else
once every retry was a 429 as well (``DEFAULT_WAIT_S``)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace

from pydantic import BaseModel

from rk.ids import new_id
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.limits import DEFAULT_WAIT_S, SESSION_LIMITED, http_limit
from rk.llm.types import Provider, RunContext, TaskResult, TaskSpec
from rk.roles.policy import role_tools
from rk.store.events import Event, NewEvent
from rk.store.projections import projection

MAX_ATTEMPTS = 3
RAW_LIMIT = 64 * 1024
NO_RETRY = frozenset({"waiting_human", "rate_capped", SESSION_LIMITED})


class FreeText(BaseModel):
    text: str


def _clip(raw: str) -> str:
    return raw if len(raw) <= RAW_LIMIT else raw[:RAW_LIMIT] + f"\n…[truncated {len(raw) - RAW_LIMIT} chars]"


def run_task(kernel, registry, *, project: str, role: str, instructions: str, inputs: dict | None = None,
             output_schema: type[BaseModel] = FreeText, driver: DriverSpec | None = None,
             provider: Provider | None = None, tools: list[str] | None = None, max_turns: int = 12,
             timeout_s: int = 600, actor: str = "kernel") -> TaskResult:
    settings = kernel.settings()
    spec_driver = driver or DriverSpec.parse(settings.drivers[role])
    prov = provider or build_provider(spec_driver, settings)
    ps, _ = kernel.project(project)
    granted = role_tools(role, registry) if tools is None else tools
    # Tasks are bound to their project, so only project-scoped tools are offered.
    granted = [t for t in granted if "project" in registry.get(t).input_model.model_fields]
    task_id = new_id("tsk")
    spec = TaskSpec(task_id=task_id, role=role, project=project, instructions=instructions, inputs=inputs or {},
                    output_schema=output_schema, tools=granted, max_turns=max_turns, timeout_s=timeout_s)
    ps.apply(NewEvent(type="task.created", entity_id=task_id, actor_kind="kernel", actor=actor, payload={
        "role": role, "driver": str(spec_driver), "vendor": prov.vendor, "instructions": instructions,
        "inputs": spec.inputs, "tools": granted, "output_schema": output_schema.model_json_schema()}))

    token = None
    if prov.kind == "agentic_cli" and granted:
        token = kernel.tokens.issue_task(task_id, role, project, granted, ttl_s=timeout_s * MAX_ATTEMPTS + 60)
    ctx = RunContext(kernel=kernel, registry=registry, task_token=token, actor=f"{role}@{spec_driver}")
    cap = settings.calls_per_hour.get(spec_driver.provider)
    result: TaskResult | None = None
    try:
        for n in range(1, MAX_ATTEMPTS + 1):
            if not kernel.caps.acquire(spec_driver.provider, cap):
                result = TaskResult(task_id, False, None, "", spec_driver.provider, spec_driver.model, 0.0,
                                    "rate_capped")
            else:
                try:
                    result = prov.run(spec, ctx)
                except Exception as e:  # noqa: BLE001 - a driver bug becomes a failed attempt, recorded below
                    result = TaskResult(task_id, False, None, "", spec_driver.provider, spec_driver.model, 0.0,
                                        f"{type(e).__name__}: {e}")
            if not result.ok and result.error and (limit := http_limit(result.error, time.time())) is not None:
                # a chat API's 429: a usage limit at once when it says how long; a plain one after every retry
                at_once, wait = limit
                if at_once or n == MAX_ATTEMPTS:
                    result = replace(result, error=SESSION_LIMITED, wait_s=wait, raw=result.raw or result.error)
            ps.apply(NewEvent(type="task.attempt", entity_id=task_id, actor_kind="agent", actor=ctx.actor, payload={
                "n": n, "ok": result.ok, "error": result.error, "raw": _clip(result.raw), "model": result.model,
                "duration_s": result.duration_s, "tool_calls": result.tool_calls, "session_id": result.session_id,
                "wait_s": result.wait_s}))
            if result.error == SESSION_LIMITED:
                kernel.caps.block_for(spec_driver.provider, result.wait_s or DEFAULT_WAIT_S)
            if result.ok or result.error in NO_RETRY:
                break
    finally:
        if token:
            kernel.tokens.revoke(task_id)
    status = "done" if result.ok else ("waiting_human" if result.error == "waiting_human" else "failed")
    ps.apply(NewEvent(type="task.finished", entity_id=task_id, actor_kind="kernel", actor=actor, payload={
        "status": status, "output": result.output.model_dump(mode="json") if result.output else None,
        "error": result.error}))
    return result


def task_row(ps, task_id: str) -> dict:
    r = ps.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if r is None:
        raise KeyError(task_id)
    d = dict(r)
    d["output"] = json.loads(d["output"]) if d["output"] else None
    return d


@projection("task.created")
def _created(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO tasks(id, role, driver, vendor, status, attempts, created) "
                 "VALUES (?,?,?,?, 'running', 0, ?)", (ev.entity_id, p["role"], p["driver"], p["vendor"], ev.ts))


@projection("task.attempt")
def _attempt(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE tasks SET attempts=? WHERE id=?", (ev.payload["n"], ev.entity_id))


@projection("task.finished")
def _finished(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("UPDATE tasks SET status=?, output=?, error=?, finished=? WHERE id=?",
                 (p["status"], json.dumps(p["output"]) if p["output"] is not None else None, p["error"], ev.ts,
                  ev.entity_id))
