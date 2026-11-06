"""The gaps stage: the GapsWorker fetches the full text of each landscape cluster's representative works, runs every
gap miner over the clusters and stores each gap with its checks (``verified`` only when every check passed). Gate: at
least one verified gap in the attempt. A verified gap means the evidence chain was checked, worded "not found within
the searched scope", never "novel"."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from rk.discovery.models import Gap
from rk.discovery.sources import log_and_search
from rk.discovery.store import add_gap, clusters, gaps, intake_of
from rk.library.service import Library
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import stage_row
from rk.store.project import ProjectStore
from rk.verify.judge import span_in

FULLTEXT_PER_CLUSTER = 3  # representative works per cluster whose full text is fetched before mining
GAP_CHECK_SOURCES = 2  # a gap-check search runs on the first two sources
GAP_CHECK_LIMIT = 10
ABSTRACT_CHARS = 600
PASS, FAIL, SKIPPED = "pass", "fail", "skipped"


@dataclass
class MinerResult:
    """A miner's gaps as ``(gap, verified, checks)``, plus ``problems`` that kept it from mining all it could (e.g. a
    role task that failed); the worker reruns the stage with them when no verified gap was stored, else returns them
    as the reason of its ``done``."""
    gaps: list[tuple[Gap, bool, dict]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


Miner = Callable[[StageContext, list[dict]], MinerResult]


class Checks:
    """A gap's checks: each named check is ``"pass"``, ``"fail"`` or ``"skipped"`` (not run because an earlier check
    failed); ``reasons`` names why a check failed. ``ok`` only when every check passed."""

    def __init__(self, names: Iterable[str]):
        self.values: dict[str, str] = dict.fromkeys(names, SKIPPED)
        self.reasons: dict[str, str] = {}

    def passed(self, name: str) -> None:
        self.values[name] = PASS

    def failed(self, name: str, reason: str) -> None:
        self.values[name] = FAIL
        self.reasons[name] = reason

    @property
    def ok(self) -> bool:
        return all(v == PASS for v in self.values.values())

    def dump(self, **data) -> dict:
        """The ``checks`` dict stored with the gap: the check values, extra ``data`` and ``reasons``."""
        return {**self.values, **data, "reasons": dict(self.reasons)}


def task_context(ctx: StageContext) -> dict:
    """The project, the intake and the guidance, as inputs of a gap-mining role task."""
    out: dict = {"project": {"title": ctx.ps.meta("title"), "field": ctx.ps.meta("field")}}
    intake = intake_of(ctx.ps)
    if intake is not None:
        out["intake"] = {"audience": intake.audience, "paper_type": intake.paper_type, "language": intake.language}
    if ctx.guidance:
        out["guidance"] = ctx.guidance
    return out


def work(lib: Library, item_id: str) -> dict:
    """A work as role-task input: id, title, year and the start of the abstract."""
    it = lib.get(item_id)
    return {"item_id": item_id, "title": it.title, "year": it.year, "abstract": (it.abstract or "")[:ABSTRACT_CHARS]}


def in_record(lib: Library, item_id: str, quote: str) -> bool:
    """Whether ``quote`` occurs verbatim in the work's title or abstract."""
    it = lib.get(item_id)
    return span_in(f"{it.title}\n{it.abstract or ''}", quote)


def gap_check_sources(ctx: StageContext) -> list[str]:
    """The sources a gap-check search runs on: the first ones in preference order."""
    return ctx.sources.sources()[:GAP_CHECK_SOURCES]


def gap_check_search(ctx: StageContext, query: str, *, after_year: int | None = None) -> list[str]:
    """Run a gap-check search (purpose ``gap_check``, logged) on ``gap_check_sources``; the hit ids, de-duplicated."""
    found: list[str] = []
    for source in gap_check_sources(ctx):
        ids = log_and_search(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose="gap_check",
                             query=query, source=source, limit=GAP_CHECK_LIMIT, actor=ctx.actor, after_year=after_year)
        found += [i for i in ids if i not in found]
    return found


def default_miners() -> list[Miner]:
    """The gap miners in the order the worker runs them."""
    from rk.discovery.gaps import (  # the miners import this module
        limitation,
        maphole,
        retraction,
    )

    return [limitation.mine, retraction.mine, maphole.mine]


class GapsWorker:
    def __init__(self, miners: list[Miner] | None = None):
        self.miners = list(miners) if miners is not None else default_miners()

    def run(self, ctx: StageContext) -> StageOutcome:
        landscape = stage_row(ctx.ps, "landscape")
        if landscape is None or landscape["passed_attempt"] is None:
            return StageOutcome("failed", reason="landscape has not passed")
        cls = clusters(ctx.ps, landscape["passed_attempt"])
        for c in cls:
            for iid in c["representative_ids"][:FULLTEXT_PER_CLUSTER]:
                ctx.sources.fulltext(iid)
        verified, problems = 0, []
        for miner in self.miners:
            result = miner(ctx, cls)
            for gap, ok, checks in result.gaps:
                add_gap(ctx.ps, ctx.attempt, gap, verified=ok, checks=checks, actor=ctx.actor)
                verified += ok
            problems += result.problems
        reason = "; ".join(problems)
        if not verified and problems:  # a miner that could not mine may still find the verified gap on a rerun
            return StageOutcome("rerun", reason=reason)
        return StageOutcome("done", reason=reason)

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        return StageOutcome("done")  # gaps opens no checkpoint

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        if gaps(ps, attempt, verified=True):
            return GateResult(True, [])
        return GateResult(False, ["no verified gap"])

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1: an unverified gap cannot be narrowed into a verified one, so the run fails."""
        return StageOutcome("failed", reason="no verified gap within the searched scope")
