"""Discovery tools: D2 runs (``rk serve``'s RunManager), D1 stage runs, exploration rounds and the discovery
artifacts (gaps, topics, the research question). Registered by ``build_registry``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from rk.discovery.store import add_round, gaps, hypotheses, intake_of, rqs, topics
from rk.orchestrator.graph import EDGE_LIMIT
from rk.orchestrator.state import (
    active_run,
    attempt_ran,
    current_stage,
    edge_rows,
    latest_attempt,
    stage_row,
    stage_rows,
)
from rk.store.project import ProjectStore
from rk.tools.registry import CallContext, ToolError

if TYPE_CHECKING:
    from rk.orchestrator.runs import RunManager

DiscoveryStage = Literal["intake", "landscape", "gaps", "topics", "question"]  # rk.orchestrator.graph.DISCOVERY


class ProjectArg(BaseModel):
    project: str


class RunStart(BaseModel):
    project: str
    until: Literal["topics", "question"]


class StageRun(BaseModel):
    project: str
    stage: DiscoveryStage
    guidance: str | None = None


class RoundArgs(BaseModel):
    project: str
    stage: DiscoveryStage
    feedback: str


class GapList(BaseModel):
    project: str
    verified: bool | None = None


def _shown_attempt(ps: ProjectStore, stage: str) -> int | None:
    """The attempt whose artifacts a stage shows: the one that passed, else the latest that ran (an interrupted
    attempt, without ``stage.ran``, may be partial); ``None`` before any ran."""
    row = stage_row(ps, stage)
    if row is None:
        return None
    if row["status"] == "passed":
        return row["passed_attempt"]
    return next((n for n in range(row["attempt"], 0, -1) if attempt_ran(ps, stage, n)), None)


def register_discovery_tools(tool: Callable, kernel, runs: Callable[[], RunManager | None]) -> None:
    """``tool(name, summary, input_model, writes)`` is ``build_registry``'s decorator; ``runs()`` returns
    ``rk serve``'s RunManager (whose ``orchestrator`` also runs the D1 stages), or None."""

    def project(slug: str) -> ProjectStore:
        try:
            ps, _ = kernel.project(slug)
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e
        return ps

    def manager() -> RunManager:
        m = runs()
        if m is None:
            raise ToolError("unavailable", "runs and stages are driven by rk serve's orchestrator, which is not "
                                           "available here", status=503)
        return m

    def d1_only(ps: ProjectStore, slug: str) -> None:
        """Guided stage calls are for D1 projects: a D2 project's stages run in a D2 run."""
        intake = intake_of(ps)
        if intake is not None and intake.discovery_mode == "d2":
            raise ToolError("not_d1", f"{slug} uses autonomous discovery (d2): its stages run in a D2 run (uv run rk "
                                      f"run {slug} --until topics|question), not through guided stage calls")

    # ---- D2 runs (the human only) ----
    @tool("rk_run_start", "Start an autonomous (D2) run of the discovery stages up to topics or question; it runs "
          "in rk serve.", RunStart, True)
    def _run_start(ctx: CallContext, a: RunStart) -> dict:
        run_id = manager().start(a.project, a.until, actor=ctx.actor)
        return {"project": a.project, "run_id": run_id, "until": a.until}

    @tool("rk_run_pause", "Pause the project's run between steps (a role task in flight finishes first).",
          ProjectArg, True)
    def _run_pause(ctx: CallContext, a: ProjectArg) -> dict:
        return manager().pause(a.project, actor=ctx.actor)

    @tool("rk_run_resume", "Resume the project's paused run in a new session (its budgets and wall clock restart); "
          "on a failed or cancelled run, start a new run with the same end stage.",
          ProjectArg, True)
    def _run_resume(ctx: CallContext, a: ProjectArg) -> dict:
        return manager().resume(a.project, actor=ctx.actor)

    @tool("rk_run_cancel", "Cancel the project's run between steps.", ProjectArg, True)
    def _run_cancel(ctx: CallContext, a: ProjectArg) -> dict:
        return manager().cancel(a.project, actor=ctx.actor)

    @tool("rk_run_status", "The project's latest run and the status of every discovery stage.", ProjectArg, False)
    def _run_status(ctx: CallContext, a: ProjectArg) -> dict:
        return manager().status(a.project)

    # ---- stages ----
    @tool("rk_stage_status", "The discovery stages: status, attempts, last gate, waits; the current stage, the "
          "back-edges used, and the guided (D1) stage call: d1.running while one runs in the background, d1.last "
          "with the outcome and reason (or error) of the last one.", ProjectArg, False)
    def _stage_status(ctx: CallContext, a: ProjectArg) -> dict:
        ps = project(a.project)
        m = runs()
        return {"project": a.project, "current": current_stage(ps), "stages": stage_rows(ps), "edges": edge_rows(ps),
                "edge_limit": EDGE_LIMIT, "d1": m.d1_status(a.project) if m is not None else None}

    def started(a: ProjectArg, action: str, stage: str, attempt: int, out: dict) -> dict:
        return {"project": a.project, **out, "attempt": attempt,
                "message": f"{action} of {stage!r} (attempt {attempt}) started in the background; poll "
                           "rk_stage_status until d1.running is null, then read d1.last. Do not start it again while "
                           "it runs."}

    @tool("rk_stage_run", "Guided discovery (D1): start a new attempt of the current stage, with optional guidance. "
          "Returns at once (status running); the stage runs in the background (minutes): poll rk_stage_status for "
          "its outcome. Refused while a D2 run is active or another stage call runs (stage_busy).", StageRun, True)
    def _stage_run(ctx: CallContext, a: StageRun) -> dict:
        ps = project(a.project)
        m = manager()
        d1_only(ps, a.project)
        m.orchestrator.check_stage(a.project, a.stage)
        attempt = latest_attempt(ps, a.stage) + 1

        def run() -> dict:
            outcome = m.orchestrator.run_stage(a.project, a.stage, guidance=a.guidance, actor=ctx.actor)
            return {"stage": a.stage, **asdict(outcome), "current": current_stage(ps)}

        return started(a, "run", a.stage, attempt, m.d1_start(a.project, "run", a.stage, run,
                                                              check=lambda ps: d1_only(ps, a.project)))

    @tool("rk_stage_advance", "Guided discovery (D1): apply the answer the current stage waits on, then gate it; a "
          "pass moves on to the next stage. Returns at once (status running); poll rk_stage_status for the gate "
          "result. Refused while a D2 run is active or another stage call runs (stage_busy).", ProjectArg, True)
    def _stage_advance(ctx: CallContext, a: ProjectArg) -> dict:
        ps = project(a.project)
        m = manager()
        d1_only(ps, a.project)
        stage = current_stage(ps)
        if stage is None:
            raise ToolError("stage_unavailable", "every discovery stage passed")

        def advance() -> dict:
            res = m.orchestrator.advance(a.project, actor=ctx.actor)
            return {"stage": stage, **asdict(res), "current": current_stage(ps)}

        return started(a, "advance", stage, latest_attempt(ps, stage),
                       m.d1_start(a.project, "advance", stage, advance, check=lambda ps: d1_only(ps, a.project)))

    @tool("rk_exploration_round", "Record feedback on a stage's output; it guides the next runs of the stages. "
          "Refused to agents while a D2 run is active (only the researcher guides a run).", RoundArgs, True)
    def _round(ctx: CallContext, a: RoundArgs) -> dict:
        ps = project(a.project)
        if ctx.role != "cli" and (run := active_run(ps)) is not None:
            raise ToolError("run_active", f"run {run['id']} is {run['status']}: an agent's feedback would guide every "
                                          "later stage of it; only the researcher records rounds during a run",
                            {"run_id": run["id"]})
        rid = add_round(ps, a.stage, a.feedback, actor=ctx.actor, actor_kind="human" if ctx.role == "cli" else "agent")
        return {"project": a.project, "round_id": rid, "stage": a.stage}

    # ---- discovery artifacts ----
    @tool("rk_gap_list", "The gaps of the gaps stage (the attempt that passed, else the latest that ran); verified "
          "means only "
          "that the evidence chain was checked.", GapList, False)
    def _gap_list(ctx: CallContext, a: GapList) -> dict:
        ps = project(a.project)
        attempt = _shown_attempt(ps, "gaps")
        return {"project": a.project, "attempt": attempt,
                "gaps": gaps(ps, attempt, verified=a.verified) if attempt else []}

    @tool("rk_topic_list", "The candidate topics of the topics stage (the attempt that passed, else the latest that "
          "ran) with "
          "their gaps, closest works and status.", ProjectArg, False)
    def _topic_list(ctx: CallContext, a: ProjectArg) -> dict:
        ps = project(a.project)
        attempt = _shown_attempt(ps, "topics")
        return {"project": a.project, "attempt": attempt, "topics": topics(ps, attempt) if attempt else []}

    @tool("rk_rq_get", "The research question of the question stage (the frozen one, else the latest of the latest "
          "attempt that ran) with its "
          "hypotheses and already-done result.", ProjectArg, False)
    def _rq_get(ctx: CallContext, a: ProjectArg) -> dict:
        ps = project(a.project)
        attempt = _shown_attempt(ps, "question")
        rows = rqs(ps, attempt) if attempt else []
        frozen = [r for r in rows if r["status"] == "frozen"]
        rq = (frozen or rows or [None])[-1]
        return {"project": a.project, "attempt": attempt, "rq": rq,
                "hypotheses": hypotheses(ps, rq["id"]) if rq else []}
