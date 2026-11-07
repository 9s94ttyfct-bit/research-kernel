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

from rk.checkpoints import create_checkpoint, get_checkpoint, supersede_open
from rk.discovery.boundary import error_text, transient_source_error
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
from rk.net.http import BudgetExhausted
from rk.orchestrator.graph import BACK_EDGES, DISCOVERY, EDGE_LIMIT, edge_name
from rk.orchestrator.state import (
    TERMINAL_RUN_STATES,
    attempt_ran,
    current_stage,
    edge_row,
    latest_attempt,
    record_attempt,
    record_edge_allowed,
    record_edge_taken,
    record_gate,
    record_passed,
    record_ran,
    record_reopened,
    record_run_session,
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


@dataclass(frozen=True)
class _EdgeResult:
    """What a use of an edge came to: taken, blocked on a checkpoint (P2: ``stage_blocked``; P1: one the degrade
    opened), or degraded and then passed or failed (P1)."""
    kind: Literal["taken", "blocked", "passed", "failed"]
    checkpoint_id: str | None = None
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

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        """Apply the answer of the checkpoint the attempt waits on. Must be idempotent: after ``RateCapped``,
        ``BudgetExceeded`` or a crash the wait is still linked, so the next step calls it again with the same
        checkpoint."""
        ...

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult: ...

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 only, at an edge limit: delete or narrow what fails, re-validate and continue (spec §5.3/§7.2). Writes
        ``stage.degraded`` for the disclosure; ``done`` goes to the gate, ``failed`` fails the run, ``waiting_human``
        waits on the checkpoint it opened (a review that is human in every mode, e.g. ``topic_review``)."""
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


def default_workers() -> dict[str, Worker]:
    """The worker of every discovery stage that has one."""
    from rk.discovery.gaps import GapsWorker  # the workers import this module
    from rk.discovery.landscape import LandscapeWorker
    from rk.discovery.question import QuestionWorker
    from rk.discovery.topics import TopicsWorker

    return {"intake": IntakeWorker(), "landscape": LandscapeWorker(), "gaps": GapsWorker(), "topics": TopicsWorker(),
            "question": QuestionWorker()}


class Orchestrator:
    def __init__(self, kernel, registry, *, workers: dict[str, Worker] | None = None,
                 executor_factory: Callable[[str, str | None, int | None], TaskExecutor] | None = None,
                 sources_factory: Callable[[str], DiscoverySources] | None = None,
                 clock: Callable[[], float] = time.time):
        self.kernel, self.registry, self.clock = kernel, registry, clock
        self.workers: dict[str, Worker] = {**default_workers(), **(workers or {})}
        self.executor_factory = executor_factory or (
            lambda project, run_id, session: KernelExecutor(kernel, registry, project, run_id=run_id, session=session))
        self.sources_factory = sources_factory or (lambda project: KernelSources(kernel, project))

    # ---- D1 ----
    def run_stage(self, project: str, stage: str, *, run_id: str | None = None, guidance: str | None = None,
                  actor: str) -> StageOutcome:
        """Run a new attempt of the current stage. ``guidance`` is asked for this attempt; without it the stage
        gets the guidance it was reopened with (or that of the attempt it replaces).

        In D1 (no ``run_id``) running a stage whose last attempt finished is a rerun, a use of the edge ``X->X``,
        and a ``back_edge`` outcome uses its edge at once; both obey the per-project limit (§7.2): at the limit P2
        returns ``waiting_human`` on a ``stage_blocked`` checkpoint, P1 degrades (``done`` after the gate passes,
        else ``failed``). An attempt that was interrupted (a rate cap or a crash) is not a rerun: it runs again as a
        new attempt with the same guidance and uses no edge. With a ``run_id``, ``step`` settles every outcome."""
        ps, lib = self.kernel.project(project)
        worker = self.check_stage(project, stage)
        if run_id is None:
            early = self._d1_rerun(project, ps, lib, stage, worker, actor=actor)
            if early is not None:
                return early
        ctx, outcome = self._attempt(project, ps, lib, stage, worker, run_id=run_id, guidance=guidance, actor=actor)
        if outcome.status == "back_edge" and run_id is None:
            return self._d1_edge(ps, worker, ctx, outcome.to, [outcome.reason] if outcome.reason else []) or outcome
        return outcome

    def check_stage(self, project: str, stage: str) -> Worker:
        """The worker of ``stage`` when it is the current stage (what ``run_stage`` runs), else ``stage_unavailable``;
        a quick check before a guided stage call goes to the background."""
        ps, _ = self.kernel.project(project)
        worker = self._worker(stage)
        current = current_stage(ps)
        if stage != current:
            why = "it already passed" if current is None or DISCOVERY.index(stage) < DISCOVERY.index(current) \
                else f"{current!r} has not passed"
            raise ToolError("stage_unavailable", f"cannot run {stage!r}: {why}", {"current": current})
        return worker

    def _d1_rerun(self, project: str, ps: ProjectStore, lib, stage: str, worker: Worker, *,
                  actor: str) -> StageOutcome | None:
        """Count a D1 rerun of ``stage`` as ``X->X`` (a stage reopened by an edge already counted it, and an
        interrupted attempt is no rerun). Returns ``None`` to go on with the attempt, else the outcome to return
        instead: while the attempt waits on an open checkpoint (a review, an approval, ``stage_blocked``) it is
        ``waiting_human`` on it, since a new attempt would leave the researcher answering a checkpoint that is never
        applied."""
        row = stage_row(ps, stage)
        if row is None or row["status"] == "reopened" or not attempt_ran(ps, stage, row["attempt"]):
            return None
        cid = row["waiting_checkpoint"]
        cp = get_checkpoint(ps, cid) if cid else None
        if cp is not None and cp["status"] == "open":
            return StageOutcome("waiting_human", checkpoint_id=cid,
                                reason=f"checkpoint {cid} ({cp['kind']}) is open; the researcher answers it first, "
                                       f"then advance the stage")
        if cp is not None and cp["kind"] == "stage_blocked":
            msg = self._resolve_blocked(ps, None, stage, row["attempt"], cp, actor=actor)
            if current_stage(ps) != stage:
                return StageOutcome("back_edge", to=cp["options"]["to"], reason=msg)
            return None if stage_row(ps, stage)["status"] == "reopened" else StageOutcome("failed", reason=msg)
        gate = row["last_gate"]
        failures = gate["failures"] if gate and gate["attempt"] == row["attempt"] and not gate["ok"] else []
        ctx = self._ctx(project, ps, lib, stage, row["attempt"], None, self._guidance(ps, stage, row["guidance"]),
                        actor)
        return self._d1_edge(ps, worker, ctx, stage, failures)

    def _attempt(self, project: str, ps: ProjectStore, lib, stage: str, worker: Worker, *, run_id: str | None,
                 guidance: str | None, actor: str, session: int | None = None) -> tuple[StageContext, StageOutcome]:
        """Write ``stage.attempt`` (attempt + 1), run the worker, record that the run returned (``stage.ran``; an
        attempt without it was interrupted and is replaced by a new one) and link a checkpoint wait to the attempt.
        An open checkpoint of the stage left by an attempt this one replaces is superseded."""
        row = stage_row(ps, stage)
        requested = guidance if guidance is not None else (row["guidance"] if row else None)
        attempt = latest_attempt(ps, stage) + 1
        composed = self._guidance(ps, stage, requested)
        record_attempt(ps, stage, attempt, run_id=run_id, guidance=composed, requested=requested, actor=actor)
        supersede_open(ps, stage, attempt, actor=actor)
        ctx = self._ctx(project, ps, lib, stage, attempt, run_id, composed, actor, session=session)
        outcome = worker.run(ctx)
        self._check(stage, outcome)
        record_ran(ps, stage, attempt, outcome.status, outcome.reason, actor=actor)
        if outcome.status == "waiting_human":
            record_waiting(ps, stage, attempt, outcome.checkpoint_id, actor=actor)
        return ctx, outcome

    def advance(self, project: str, *, actor: str) -> GateResult:
        """Apply the answer the current attempt waits on, then gate the attempt; a pass writes ``stage.passed``. An
        attempt that was interrupted (no ``stage.ran``) is never gated: its artifacts may be partial."""
        ps, lib = self.kernel.project(project)
        stage = current_stage(ps)
        if stage is None:
            raise ToolError("stage_unavailable", "every discovery stage passed")
        worker = self._worker(stage)
        row = stage_row(ps, stage)
        if row is None or row["status"] == "reopened":
            return GateResult(False, [f"{stage!r} has no attempt to gate; run the stage first"])
        attempt = row["attempt"]
        if not attempt_ran(ps, stage, attempt):
            why = f"{stage!r} attempt {attempt} was interrupted before it finished; run the stage again"
            return GateResult(False, [why])
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
                early = self._d1_edge(ps, worker, ctx, outcome.to, [outcome.reason] if outcome.reason else [])
                if early is None:
                    return GateResult(False, [f"{stage!r} sent back to {outcome.to!r}: {outcome.reason}"])
                if early.status == "waiting_human":
                    kind = get_checkpoint(ps, early.checkpoint_id)["kind"]
                    return GateResult(False, [f"checkpoint {early.checkpoint_id} ({kind}) is open"])
                return GateResult(True, []) if early.status == "done" else GateResult(False, [early.reason])
            if outcome.status != "done":
                return GateResult(False, [outcome.reason or f"{stage!r} answered with {outcome.status}"])
        return self._gate(ps, worker, stage, attempt, actor=actor)

    # ---- D2 ----
    def step(self, project: str, run_id: str, *, actor: str) -> str:
        """One unit of a D2 run: start (or rerun) the current stage's attempt, or apply the answer it waits on, and
        settle the outcome. Returns the run status, or ``waiting_rate`` when a driver is at its hourly cap, or
        ``waiting_source`` when a source failed in a way that may go away (HTTP 429, 5xx, a transport error, an
        unreadable body; the run's reason says which). In both the run stays ``running``; the next step runs the
        interrupted attempt again as a new attempt with the same guidance, or applies the answer again. A stage budget
        (``BudgetExceeded``) or a source's API budget (``BudgetExhausted``, spec §11.3) running out pauses the run
        (``paused_budget``, the budget named in the reason); resuming it runs the interrupted attempt again."""
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
            if cp is None:
                return self._set_run(ps, run, "paused_budget", f"run session {run['session']} reached its 12 h wall "
                                                               f"clock", actor=actor)
            # the run waited for the researcher, who answered after the session's 12 h: waiting is no work, so the
            # answer starts a new session (wall clock and stage budgets restart) instead of pausing the run (§5.3)
            record_run_session(ps, run_id, run["session"] + 1, actor=actor, clock=self.clock)
            run = run_row(ps, run_id)
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
        except (BudgetExceeded, BudgetExhausted) as e:
            return self._set_run(ps, run, "paused_budget", str(e), actor=actor)
        except RateCapped:
            return "waiting_rate"
        except Exception as e:
            if not transient_source_error(e):
                raise
            self._set_run(ps, run, "running", f"a source failed ({error_text(e)}); retrying after a backoff",
                          actor=actor)
            return "waiting_source"

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
        """What the worker gets: the guidance requested for the attempt, then every exploration round's feedback; a
        line already given (a review comment that is both the rerun reason and a round) is given once."""
        lines = [ln for g in (requested, round_guidance(ps, stage)) if g for ln in g.split("\n")]
        return "\n".join(dict.fromkeys(lines))

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
        res = self._use_edge(ps, worker, ctx, to, failures)
        if res.kind == "taken":
            return self._set_run(ps, run, "running", actor=ctx.actor)
        if res.kind == "blocked":
            return self._set_run(ps, run, "paused_waiting_human", actor=ctx.actor)
        if res.kind == "passed":
            return self._after_pass(ps, run, ctx.stage, actor=ctx.actor)
        return self._set_run(ps, run, "failed", res.reason, actor=ctx.actor)

    def _use_edge(self, ps: ProjectStore, worker: Worker, ctx: StageContext, to: str,
                  failures: list[str]) -> _EdgeResult:
        """Take ``stage->to`` unless its uses are spent (§7.2, in D1 and D2 alike). At the limit P2 opens a
        ``stage_blocked`` checkpoint linked to the attempt; P1 degrades, then gates the attempt, or waits on the
        checkpoint the degrade opened."""
        stage, actor = ctx.stage, ctx.actor
        edge = edge_name(stage, to)
        uses = edge_row(ps, edge)
        if uses["count"] < EDGE_LIMIT + uses["allowed"]:
            self._take_edge(ps, stage, to, failures, actor=actor)
            return _EdgeResult("taken")
        intake = intake_of(ps)
        if intake is not None and intake.production_mode == "collab":
            granted = f" + {uses['allowed']} allowed" if uses["allowed"] else ""
            cid = create_checkpoint(
                ps, "stage_blocked",
                f"{stage!r} wants {edge} again, but it was used {uses['count']} times (limit {EDGE_LIMIT}{granted}). "
                f"Allow one more use, or stop?",
                {"choices": BLOCKED_CHOICES, "edge": edge, "to": to, "failures": list(failures)},
                stage=stage, actor=actor)
            record_waiting(ps, stage, ctx.attempt, cid, actor=actor)
            return _EdgeResult("blocked", checkpoint_id=cid)
        out = worker.degrade(ctx, list(failures))
        self._check(stage, out)
        if out.status == "waiting_human":
            record_waiting(ps, stage, ctx.attempt, out.checkpoint_id, actor=actor)
            return _EdgeResult("blocked", checkpoint_id=out.checkpoint_id)
        if out.status == "failed":
            return _EdgeResult("failed", reason=out.reason or f"{stage!r} could not degrade at {edge}")
        if out.status != "done":
            return _EdgeResult("failed", reason=f"{stage!r} degrade at {edge} returned {out.status}")
        res = self._gate(ps, worker, stage, ctx.attempt, actor=actor)
        if res.ok:
            return _EdgeResult("passed")
        return _EdgeResult("failed", reason=f"{stage!r} failed its gate after degrading at {edge}: "
                                            f"{'; '.join(res.failures)}")

    def _d1_edge(self, ps: ProjectStore, worker: Worker, ctx: StageContext, to: str,
                 failures: list[str]) -> StageOutcome | None:
        """A D1 use of ``stage->to`` under the same limit; ``None`` once taken, else what the caller returns. A P1
        degrade that fails is a dead end, and the reason says so: running the stage again only degrades again."""
        res = self._use_edge(ps, worker, ctx, to, failures)
        edge = edge_name(ctx.stage, to)
        if res.kind == "blocked":
            return StageOutcome("waiting_human", checkpoint_id=res.checkpoint_id, reason=f"{edge} reached its limit")
        if res.kind == "passed":
            return StageOutcome("done", reason="passed its gate after degrading")
        if res.kind == "failed":
            return StageOutcome("failed", reason=(
                f"dead end: {edge} reached its limit of {EDGE_LIMIT} uses and degrading {ctx.stage!r} (P1) did not "
                f"get it through: {res.reason}; running {ctx.stage!r} again only degrades it again (no new attempt) "
                f"and is unlikely to get it through. Only the researcher can change this: with production mode collab "
                f"(P2, recorded in the intake) the stage asks them at an edge limit instead of degrading."))
        return None

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
