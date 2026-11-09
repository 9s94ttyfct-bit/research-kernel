"""The role-task facade of the discovery stages: ``TaskExecutor`` and ``KernelExecutor`` over ``run_task``, with the
per-stage task budget of a run session (spec §7.2), and the hourly call caps and the subscriptions' usage limits
surfaced as ``RateCapped`` (``SessionLimited``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from rk.llm.drivers import DriverSpec
from rk.llm.limits import SESSION_LIMITED
from rk.llm.runner import run_task
from rk.llm.types import Provider, TaskResult
from rk.orchestrator.graph import OPEN_STAGES, STAGE_TASK_BUDGET
from rk.orchestrator.state import record_run_task, tasks_used


class BudgetExceeded(Exception):
    """The stage used its role-task budget for this run session."""

    def __init__(self, stage: str, used: int, limit: int):
        super().__init__(f"stage {stage!r} used {used} of {limit} role tasks in this run session")
        self.stage, self.used, self.limit = stage, used, limit


class RateCapped(Exception):
    """A role task hit its driver's hourly call cap; ``wait_s`` is the time until a call frees up."""
    why = "is at its hourly call cap"

    def __init__(self, driver: str, wait_s: float):
        super().__init__(f"driver {driver!r} {self.why}; retry in {wait_s:.0f} s")
        self.driver, self.wait_s = driver, wait_s


class SessionLimited(RateCapped):
    """A role task's driver answered that its subscription hit a session or usage limit (``rk.llm.limits``): the run
    waits for the reset as at an hourly cap; ``wait_s`` is the time until then."""
    why = "hit its subscription's session or usage limit"


class TaskExecutor(Protocol):
    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None, driver: DriverSpec | None = None,
            provider: Provider | None = None, max_turns: int | None = None,
            timeout_s: int | None = None) -> TaskResult:
        """``driver`` (and ``provider``) replace the role's configured driver, e.g. a V-SUPPORT judge; ``max_turns`` and
        ``timeout_s`` default to ``run_task``'s."""
        ...


class KernelExecutor:
    """Runs role tasks with ``run_task`` and links each to its stage attempt (event ``run.task``) as soon as it is
    created, so a task cut off by a restart (``interrupted``, spec §11.6) still counts against the stage budget. With
    a ``run_id`` (D2) a task in an open stage (``OPEN_STAGES``) first checks the stage budget of the run session; the
    other stages, including those that work through every record or included study, have none, and neither has D1
    (no ``run_id``). The hourly call caps apply to every task. ``provider_for`` (role -> provider) replaces the
    configured drivers, e.g. in tests; a ``provider`` given to ``run`` takes precedence."""

    def __init__(self, kernel, registry, project: str, *, run_id: str | None, session: int | None,
                 max_tasks_per_stage: int = STAGE_TASK_BUDGET, provider_for: Callable[[str], Provider] | None = None,
                 actor: str = "kernel"):
        self.kernel, self.registry, self.project = kernel, registry, project
        self.run_id, self.session, self.max_tasks_per_stage = run_id, session, max_tasks_per_stage
        self.provider_for, self.actor = provider_for, actor

    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None, driver: DriverSpec | None = None,
            provider: Provider | None = None, max_turns: int | None = None,
            timeout_s: int | None = None) -> TaskResult:
        ps, _ = self.kernel.project(self.project)
        if self.run_id is not None and stage in OPEN_STAGES:
            used = tasks_used(ps, self.run_id, self.session, stage)
            if used >= self.max_tasks_per_stage:
                raise BudgetExceeded(stage, used, self.max_tasks_per_stage)
        if provider is None and self.provider_for:
            provider = self.provider_for(role)
        limits = {k: v for k, v in (("max_turns", max_turns), ("timeout_s", timeout_s)) if v is not None}

        def link(task_id: str) -> None:
            record_run_task(ps, task_id=task_id, run_id=self.run_id, session=self.session, stage=stage,
                            attempt=attempt, actor=self.actor)

        result = run_task(self.kernel, self.registry, project=self.project, role=role, instructions=instructions,
                          inputs=inputs, output_schema=output_schema, driver=driver, provider=provider, tools=tools,
                          actor=self.actor, on_created=link, **limits)
        if result.error == "rate_capped":
            raise RateCapped(result.provider, self.kernel.caps.wait_s(result.provider))
        if result.error == SESSION_LIMITED:
            raise SessionLimited(result.provider, max(self.kernel.caps.wait_s(result.provider), result.wait_s or 0.0))
        return result
