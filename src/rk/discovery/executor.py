"""The role-task facade of the discovery stages: ``TaskExecutor`` and ``KernelExecutor`` over ``run_task``, with the
per-stage task budget of a run session (spec §7.2) and the hourly call caps surfaced as ``RateCapped``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from rk.llm.runner import run_task
from rk.llm.types import Provider, TaskResult
from rk.orchestrator.state import record_run_task, tasks_used

# Stages whose role tasks count against the per-session budget (spec §7.2).
OPEN_STAGES = frozenset({"landscape", "gaps", "topics"})


class BudgetExceeded(Exception):
    """The stage used its role-task budget for this run session."""

    def __init__(self, stage: str, used: int, limit: int):
        super().__init__(f"stage {stage!r} used {used} of {limit} role tasks in this run session")
        self.stage, self.used, self.limit = stage, used, limit


class RateCapped(Exception):
    """A role task hit its driver's hourly call cap; ``wait_s`` is the time until a call frees up."""

    def __init__(self, driver: str, wait_s: float):
        super().__init__(f"driver {driver!r} is at its hourly call cap; retry in {wait_s:.0f} s")
        self.driver, self.wait_s = driver, wait_s


class TaskExecutor(Protocol):
    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None) -> TaskResult: ...


class KernelExecutor:
    """Runs role tasks with ``run_task`` and links each to its stage attempt (event ``run.task``). With a ``run_id``
    (D2) a task in an open stage first checks the stage budget of the run session; without one (D1) there is no
    budget. ``provider_for`` (role -> provider) replaces the configured drivers, e.g. in tests."""

    def __init__(self, kernel, registry, project: str, *, run_id: str | None, session: int | None,
                 max_tasks_per_stage: int = 40, provider_for: Callable[[str], Provider] | None = None,
                 actor: str = "kernel"):
        self.kernel, self.registry, self.project = kernel, registry, project
        self.run_id, self.session, self.max_tasks_per_stage = run_id, session, max_tasks_per_stage
        self.provider_for, self.actor = provider_for, actor

    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None) -> TaskResult:
        ps, _ = self.kernel.project(self.project)
        if self.run_id is not None and stage in OPEN_STAGES:
            used = tasks_used(ps, self.run_id, self.session, stage)
            if used >= self.max_tasks_per_stage:
                raise BudgetExceeded(stage, used, self.max_tasks_per_stage)
        provider = self.provider_for(role) if self.provider_for else None
        result = run_task(self.kernel, self.registry, project=self.project, role=role, instructions=instructions,
                          inputs=inputs, output_schema=output_schema, provider=provider, tools=tools, actor=self.actor)
        record_run_task(ps, task_id=result.task_id, run_id=self.run_id, session=self.session, stage=stage,
                        attempt=attempt, actor=self.actor)
        if result.error == "rate_capped":
            raise RateCapped(result.provider, self.kernel.caps.wait_s(result.provider))
        return result
