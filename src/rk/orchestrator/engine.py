"""The stage engine (spec §7): one attempt of a discovery stage at a time, its gate, reruns and back-edges with their
per-project limit (§7.2), checkpoint waits, the per-session budgets and wall clock, and the hourly rate caps.

``run_stage`` / ``advance`` are guided discovery (D1: you decide when to run a stage again); ``step`` is one unit of
an autonomous run (D2). P1 (``production_mode == "auto"``) degrades at an edge limit, P2 (``collab``) asks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from rk.checkpoints import create_checkpoint, get_checkpoint
from rk.discovery.executor import (
    BudgetExceeded,
    KernelExecutor,
    RateCapped,
    TaskExecutor,
)
from rk.discovery.models import Intake
from rk.discovery.sources import DiscoverySources, KernelSources
from rk.discovery.store import guidance as round_guidance
from rk.discovery.store import intake_of
from rk.orchestrator.graph import BACK_EDGES, DISCOVERY, EDGE_LIMIT, edge_name
from rk.orchestrator.state import (
    TERMINAL_RUN_STATES,
    current_stage,
    edge_row,
    latest_attempt,
    record_attempt,
    record_edge_allowed,
    record_edge_taken,
    record_gate,
    record_passed,
    record_reopened,
    record_run_status,
    record_waiting,
    run_row,
    stage_row,
)
from rk.store.project import ProjectStore
from rk.tools.registry import ToolError

SESSION_WALL_S = 12 * 3600  # a run session pauses after 12 h of wall clock (§7.2)
BLOCKED_CHOICES = ["allow_one_more", "stop"]


@dataclass(frozen=True)
class GateResult:
    ok: bool
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StageOutcome:
    status: Literal["done", "waiting_human", "rerun", "back_edge", "failed"]
    checkpoint_id: str | None = None
    to: str | None = None
    reason: str = ""


@dataclass
class StageContext:
    kernel: Any
    registry: Any
    ps: ProjectStore
    lib: Any
    project: str
    stage: str
    attempt: int
    run_id: str | None
    guidance: str
    executor: TaskExecutor
    sources: DiscoverySources
    actor: str


class Worker(Protocol):
    def run(self, ctx: StageContext) -> StageOutcome: ...

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome: ...

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult: ...

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 only, at an edge limit: delete or narrow what fails, re-validate and continue (spec §5.3/§7.2). Writes
        ``stage.degraded`` for the disclosure; ``done`` goes to the gate, ``failed`` fails the run."""
        ...


class IntakeWorker:
    """The intake stage: the intake itself is recorded by ``rk project intake``; the stage only checks it."""

    def run(self, ctx: StageContext) -> StageOutcome:
        if ctx.ps.meta("discovery_mode") is None:
            return StageOutcome("failed", reason="intake missing")
        return StageOutcome("done")

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        return StageOutcome("done")

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        values = {k: ps.meta(k) for k in Intake.model_fields}
        missing = [f"intake field {k!r} is missing" for k, v in values.items() if v in (None, "")]
        if missing:
            return GateResult(False, missing)
        try:
            Intake.model_validate(values)
        except ValidationError as e:
            return GateResult(False, [f"intake field {'.'.join(map(str, err['loc']))!r}: {err['msg']}"
                                      for err in e.errors()])
        return GateResult(True, [])

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        return StageOutcome("failed", reason="intake incomplete: " + "; ".join(failures))


class Orchestrator:
    def __init__(self, kernel, registry, *, workers: dict[str, Worker] | None = None,
                 executor_factory: Callable[[str, str | None, int | None], TaskExecutor] | None = None,
                 sources_factory: Callable[[str], DiscoverySources] | None = None,
                 clock: Callable[[], float] = time.time):
        self.kernel, self.registry, self.clock = kernel, registry, clock
        self.workers: dict[str, Worker] = {"intake": IntakeWorker(), **(workers or {})}
        self.executor_factory = executor_factory or (
            lambda project, run_id, session: KernelExecutor(kernel, registry, project, run_id=run_id, session=session))
        self.sources_factory = sources_factory or (lambda project: KernelSources(kernel, project))

    # ---- D1 ----
    def run_stage(self, project: str, stage: str, *, run_id: str | None = None, guidance: str | None = None,
                  actor: str) -> StageOutcome:
        """Run a new attempt of the current stage. ``guidance`` is asked for this attempt; without it the stage
        gets the guidance it was reopened with (or that of the attempt it replaces). In D1 (no ``run_id``) a
        back-edge reopens its target at once; a run settles every outcome in ``step``."""
        ps, lib = self.kernel.project(project)
        worker = self._worker(stage)
        current = current_stage(ps)
        if stage != current:
            why = "it already passed" if current is None or DISCOVERY.index(stage) < DISCOVERY.index(current) \
                else f"{current!r} has not passed"
            raise ToolError("stage_unavailable", f"cannot run {stage!r}: {why}", {"current": current})
        _, outcome = self._attempt(project, ps, lib, stage, worker, run_id=run_id, guidance=guidance, actor=actor)
        if outcome.status == "back_edge" and run_id is None:
            self._take_edge(ps, stage, outcome.to, [outcome.reason] if outcome.reason else [], actor=actor)
        return outcome

    def _attempt(self, project: str, ps: ProjectStore, lib, stage: str, worker: Worker, *, run_id: str | None,
                 guidance: str | None, actor: str, session: int | None = None) -> tuple[StageContext, StageOutcome]:
        """Write ``stage.attempt`` (attempt + 1), run the worker and link a checkpoint wait to the attempt."""
        row = stage_row(ps, stage)
        requested = guidance if guidance is not None else (row["guidance"] if row else None)
        attempt = latest_attempt(ps, stage) + 1
        composed = self._guidance(ps, stage, requested)
        record_attempt(ps, stage, attempt, run_id=run_id, guidance=composed, requested=requested, actor=actor)
        ctx = self._ctx(project, ps, lib, stage, attempt, run_id, composed, actor, session=session)
        outcome = worker.run(ctx)
        self._check(stage, outcome)
        if outcome.status == "waiting_human":
            record_waiting(ps, stage, attempt, outcome.checkpoint_id, actor=actor)
        return ctx, outcome

    def advance(self, project: str, *, actor: str) -> GateResult:
        """Apply the answer the current attempt waits on, then gate the attempt; a pass writes ``stage.passed``."""
        ps, lib = self.kernel.project(project)
        stage = current_stage(ps)
        if stage is None:
            raise ToolError("stage_unavailable", "every discovery stage passed")
        worker = self._worker(stage)
        row = stage_row(ps, stage)
        if row is None or row["status"] == "reopened":
            return GateResult(False, [f"{stage!r} has no attempt to gate; run the stage first"])
        attempt = row["attempt"]
        if row["waiting_checkpoint"]:
            cp = get_checkpoint(ps, row["waiting_checkpoint"])
            if cp["status"] == "open":
                return GateResult(False, [f"checkpoint {cp['id']} ({cp['kind']}) is open"])
            if cp["kind"] == "stage_blocked":
                return GateResult(False, [self._resolve_blocked(ps, None, stage, attempt, cp, actor=actor)])
            ctx = self._ctx(project, ps, lib, stage, attempt, None, self._guidance(ps, stage, row["guidance"]), actor)
            outcome = self._answer(ps, worker, ctx, cp)
            if outcome.status == "waiting_human":
                return GateResult(False, [f"checkpoint {outcome.checkpoint_id} is open"])
            if outcome.status == "back_edge":
                self._take_edge(ps, stage, outcome.to, [outcome.reason] if outcome.reason else [], actor=actor)
            if outcome.status != "done":
                return GateResult(False, [outcome.reason or f"{stage!r} answered with {outcome.status}"])
        return self._gate(ps, worker, stage, attempt, actor=actor)

    # ---- D2 ----
    def step(self, project: str, run_id: str, *, actor: str) -> str:
        """One unit of a D2 run: start (or rerun) the current stage's attempt, or apply the answer it waits on, and
        settle the outcome. Returns the run status, or ``waiting_rate`` when a driver is at its hourly cap (the run
        stays ``running`` and the next step reruns the attempt)."""
        ps, lib = self.kernel.project(project)
        run = run_row(ps, run_id)
        if run is None:
            raise ToolError("not_found", f"no run {run_id!r}")
        if run["status"] in TERMINAL_RUN_STATES or run["status"] in ("paused", "paused_budget"):
            return run["status"]
        stage = current_stage(ps)
        if stage is None or DISCOVERY.index(stage) > DISCOVERY.index(run["until"]):
            return self._set_run(ps, run, "done", actor=actor)
        worker = self._worker(stage)
        row = stage_row(ps, stage)
        cp = get_checkpoint(ps, row["waiting_checkpoint"]) if row and row["waiting_checkpoint"] else None
        if cp is not None and cp["status"] == "open":
            return self._set_run(ps, run, "paused_waiting_human", actor=actor)
        if self.clock() - run["session_started"] > SESSION_WALL_S:
            return self._set_run(ps, run, "paused_budget", f"run session {run['session']} reached its 12 h wall "
                                                           f"clock", actor=actor)
        self._set_run(ps, run, "running", actor=actor)
        try:
            if cp is not None and cp["kind"] == "stage_blocked":
                self._resolve_blocked(ps, run, stage, row["attempt"], cp, actor=actor)
                return run_row(ps, run_id)["status"]
            if cp is not None:
                ctx = self._ctx(project, ps, lib, stage, row["attempt"], run_id,
                                self._guidance(ps, stage, row["guidance"]), actor, session=run["session"])
                outcome = self._answer(ps, worker, ctx, cp)
            else:
                ctx, outcome = self._attempt(project, ps, lib, stage, worker, run_id=run_id, guidance=None,
                                             actor=actor, session=run["session"])
            return self._settle(ps, run, worker, ctx, outcome)
        except BudgetExceeded as e:
            return self._set_run(ps, run, "paused_budget", str(e), actor=actor)
        except RateCapped:
            return "waiting_rate"

    # ---- internals ----
    def _worker(self, stage: str) -> Worker:
        if stage not in DISCOVERY:
            raise ToolError("stage_unavailable", f"{stage!r} is not a discovery stage; the orchestrator runs only "
                                                 f"{', '.join(DISCOVERY)}")
        worker = self.workers.get(stage)
        if worker is None:
            raise ToolError("stage_unavailable", f"stage {stage!r} has no worker")
        return worker

    def _ctx(self, project: str, ps: ProjectStore, lib, stage: str, attempt: int, run_id: str | None,
             guidance: str, actor: str, *, session: int | None = None) -> StageContext:
        if run_id is not None and session is None:
            session = run_row(ps, run_id)["session"]
        return StageContext(kernel=self.kernel, registry=self.registry, ps=ps, lib=lib, project=project, stage=stage,
                            attempt=attempt, run_id=run_id, guidance=guidance,
                            executor=self.executor_factory(project, run_id, session),
                            sources=self.sources_factory(project), actor=actor)

    @staticmethod
    def _guidance(ps: ProjectStore, stage: str, requested: str | None) -> str:
        """What the worker gets: the guidance requested for the attempt, then every exploration round's feedback."""
        return "\n".join(g for g in (requested, round_guidance(ps, stage)) if g)

    @staticmethod
    def _check(stage: str, outcome: StageOutcome) -> None:
        if outcome.status == "waiting_human" and not outcome.checkpoint_id:
            raise ValueError(f"{stage}: waiting_human without a checkpoint id")
        if outcome.status == "back_edge" and (stage, outcome.to) not in BACK_EDGES:
            raise ValueError(f"{stage}: no back-edge to {outcome.to!r}")

    def _answer(self, ps: ProjectStore, worker: Worker, ctx: StageContext, cp: dict) -> StageOutcome:
        """Hand the answered checkpoint to the worker; the wait ends unless it waits on a new checkpoint."""
        outcome = worker.on_answer(ctx, cp)
        self._check(ctx.stage, outcome)
        record_waiting(ps, ctx.stage, ctx.attempt,
                       outcome.checkpoint_id if outcome.status == "waiting_human" else None, actor=ctx.actor)
        return outcome

    def _gate(self, ps: ProjectStore, worker: Worker, stage: str, attempt: int, *, actor: str) -> GateResult:
        res = worker.gate(ps, attempt)
        record_gate(ps, stage, attempt, res.ok, res.failures, actor=actor)
        if res.ok:
            record_passed(ps, stage, attempt, actor=actor)
        return res

    def _settle(self, ps: ProjectStore, run: dict, worker: Worker, ctx: StageContext, outcome: StageOutcome) -> str:
        stage, actor = ctx.stage, ctx.actor
        if outcome.status == "done":
            res = self._gate(ps, worker, stage, ctx.attempt, actor=actor)
            if res.ok:
                return self._after_pass(ps, run, stage, actor=actor)
            return self._edge(ps, run, worker, ctx, stage, res.failures)
        if outcome.status == "waiting_human":
            return self._set_run(ps, run, "paused_waiting_human", actor=actor)
        if outcome.status in ("rerun", "back_edge"):
            to = stage if outcome.status == "rerun" else outcome.to
            return self._edge(ps, run, worker, ctx, to, [outcome.reason] if outcome.reason else [])
        return self._set_run(ps, run, "failed", outcome.reason or f"{stage!r} failed", actor=actor)

    def _after_pass(self, ps: ProjectStore, run: dict, stage: str, *, actor: str) -> str:
        status = "done" if DISCOVERY.index(stage) >= DISCOVERY.index(run["until"]) else "running"
        return self._set_run(ps, run, status, actor=actor)

    def _edge(self, ps: ProjectStore, run: dict, worker: Worker, ctx: StageContext, to: str,
              failures: list[str]) -> str:
        """Take ``stage->to`` unless its uses are spent (§7.2); then P2 asks with ``stage_blocked``, P1 degrades."""
        stage, actor = ctx.stage, ctx.actor
        edge = edge_name(stage, to)
        uses = edge_row(ps, edge)
        if uses["count"] < EDGE_LIMIT + uses["allowed"]:
            self._take_edge(ps, stage, to, failures, actor=actor)
            return self._set_run(ps, run, "running", actor=actor)
        intake = intake_of(ps)
        if intake is not None and intake.production_mode == "collab":
            granted = f" + {uses['allowed']} allowed" if uses["allowed"] else ""
            cid = create_checkpoint(
                ps, "stage_blocked",
                f"{stage!r} wants {edge} again, but it was used {uses['count']} times (limit {EDGE_LIMIT}{granted}). "
                f"Allow one more use, or stop the run?",
                {"choices": BLOCKED_CHOICES, "edge": edge, "to": to, "failures": list(failures)},
                stage=stage, actor=actor)
            record_waiting(ps, stage, ctx.attempt, cid, actor=actor)
            return self._set_run(ps, run, "paused_waiting_human", actor=actor)
        out = worker.degrade(ctx, list(failures))
        if out.status == "failed":
            return self._set_run(ps, run, "failed", out.reason or f"{stage!r} could not degrade at {edge}",
                                 actor=actor)
        if out.status != "done":
            return self._set_run(ps, run, "failed", f"{stage!r} degrade at {edge} returned {out.status}",
                                 actor=actor)
        res = self._gate(ps, worker, stage, ctx.attempt, actor=actor)
        if res.ok:
            return self._after_pass(ps, run, stage, actor=actor)
        return self._set_run(ps, run, "failed", f"{stage!r} failed its gate after degrading at {edge}: "
                                                f"{'; '.join(res.failures)}", actor=actor)

    def _take_edge(self, ps: ProjectStore, stage: str, to: str, failures: list[str], *, actor: str) -> None:
        """Count a use of ``stage->to`` and reopen ``to`` (with the failures as guidance) and every later stage that
        ran."""
        edge = edge_name(stage, to)
        record_edge_taken(ps, edge, edge_row(ps, edge)["count"] + 1, actor=actor)
        for s in DISCOVERY[DISCOVERY.index(to):]:
            if s == to or latest_attempt(ps, s) > 0:
                record_reopened(ps, s, by_edge=edge, guidance="\n".join(failures) if s == to and failures else None,
                                actor=actor)

    def _resolve_blocked(self, ps: ProjectStore, run: dict | None, stage: str, attempt: int, cp: dict, *,
                         actor: str) -> str:
        """Apply an answered ``stage_blocked``: ``allow_one_more`` grants and takes one more use of the edge;
        anything else stops (the run fails). Returns what happened."""
        opts = cp["options"] or {}
        edge = opts.get("edge", "?")
        if (cp["answer"] or {}).get("decision") == "allow_one_more":
            record_edge_allowed(ps, edge, actor=actor)
            self._take_edge(ps, stage, opts["to"], opts.get("failures") or [], actor=actor)
            if run is not None:
                self._set_run(ps, run, "running", actor=actor)
            return f"one more use of {edge} allowed; {opts['to']!r} reopened"
        record_waiting(ps, stage, attempt, None, actor=actor)
        reason = f"stopped at {edge}: its limit of {EDGE_LIMIT} uses was reached (checkpoint {cp['id']})"
        if run is not None:
            self._set_run(ps, run, "failed", reason, actor=actor)
        return reason

    def _set_run(self, ps: ProjectStore, run: dict, status: str, reason: str = "", *, actor: str) -> str:
        current = run_row(ps, run["id"])
        if (current["status"], current["reason"] or "") != (status, reason):
            record_run_status(ps, run["id"], status, reason=reason, actor=actor, clock=self.clock)
        return status
