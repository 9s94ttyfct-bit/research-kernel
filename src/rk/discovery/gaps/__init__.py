"""The gaps stage: the GapsWorker fetches the full text of each landscape cluster's representative works, runs every
gap miner over the clusters and stores each gap with its checks (``verified`` only when every check passed). Gate: at
least one verified gap in the attempt. A verified gap means the evidence chain was checked; a claim of absence is
worded "not found within the searched scope", never "novel"."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from pydantic import ValidationError

from rk.argument import ArgumentService, ClaimIn, EvidenceIn
from rk.discovery.boundary import per_item
from rk.discovery.models import Gap
from rk.discovery.sources import log_and_search
from rk.discovery.store import add_gap, clusters, gaps, intake_of
from rk.fulltext import latest_snapshot
from rk.library.service import Library
from rk.llm.drivers import DriverSpec
from rk.notes import Locator, NoteIn, NoteRejected, add_note
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import stage_row
from rk.store.project import ProjectStore
from rk.verify.judge import span_in
from rk.verify.support import SupportResult, verify_support

FULLTEXT_PER_CLUSTER = 3  # representative works per cluster whose full text is fetched before mining
GAP_CHECK_SOURCES = 2  # a gap-check search runs on the first two sources
GAP_CHECK_LIMIT = 10
ABSTRACT_CHARS = 600
READ_TOOLS = ["rk_text_read", "rk_text_find"]  # the gapminer reads the full texts it mines
SUPPORT_JUDGES = 2  # V-SUPPORT in gap mining: at least two admitted cross-vendor judges, all agreeing
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

    def failed(self, name: str, reason: str, *, value: str = FAIL) -> None:
        """``value`` names the failure when a more specific word fits (e.g. ``"missing"``); never ``"pass"``."""
        self.values[name] = value
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
    """Run a gap-check search (purpose ``gap_check``, logged) on ``gap_check_sources``; the hit ids, de-duplicated.
    A source error propagates: a failed search is never "nothing found" (callers use ``checked_search``)."""
    found: list[str] = []
    for source in gap_check_sources(ctx):
        ids = log_and_search(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose="gap_check",
                             query=query, source=source, limit=GAP_CHECK_LIMIT, actor=ctx.actor, after_year=after_year)
        found += [i for i in ids if i not in found]
    return found


def checked_search(ctx: StageContext, checks: Checks, name: str, what: str, query: str, *,
                   after_year: int | None = None) -> list[str] | None:
    """``gap_check_search`` behind the per-item boundary: the hit ids with check ``name`` passed, or ``None`` with it
    failed when the search raised (``what`` names the search in the reason)."""
    found, err = per_item(what, lambda: gap_check_search(ctx, query, after_year=after_year))
    if err:
        checks.failed(name, err)
        return None
    checks.passed(name)
    return found


def cluster_of(clusters: list[dict]) -> dict[str, str]:
    """Each cluster item's (first) cluster id."""
    out: dict[str, str] = {}
    for c in clusters:
        for iid in c["item_ids"]:
            out.setdefault(iid, c["id"])
    return out


def fulltext_clusters(ctx: StageContext, clusters: list[dict], *, min_works: int = 1) -> list[dict]:
    """Clusters as role-task input with their representative works that have a text layer (id, title, year, page
    count); only clusters with at least ``min_works`` such works."""
    out = []
    for c in clusters:
        items = []
        for iid in c["representative_ids"]:
            snap = latest_snapshot(ctx.ps, iid)
            if snap is not None:
                it = ctx.lib.get(iid)
                items.append({"item_id": iid, "title": it.title, "year": it.year, "pages": len(snap[1].pages)})
        if len(items) >= min_works:
            out.append({"cluster_id": c["id"], "label": c["label"], "description": c["description"], "works": items})
    return out


def quote_note(ctx: StageContext, checks: Checks, name: str, source_id: str, quote: str, *, role_in_source: str,
               keywords: list[str], locator: Locator | None = None) -> str | None:
    """Keep ``quote`` as a quote note of the source (V-QUOTE; a ``locator`` that holds the quote is kept); the check
    ``name`` records whether it passed. The note id, or ``None``."""
    try:
        note = add_note(ctx.ps, ctx.lib, NoteIn(source_id=source_id, kind="quote", text=quote, locator=locator,
                                                role_in_source=role_in_source, keywords=keywords),
                        actor=ctx.actor, actor_kind="agent")
    except NoteRejected as e:
        checks.failed(name, e.reason)
        return None
    except (KeyError, ValueError) as e:  # an unknown source or an empty quote
        checks.failed(name, str(e))
        return None
    checks.passed(name)
    return note.note_id


def title_year(lib: Library, item_id: str) -> str:
    it = lib.get(item_id)
    return f"{it.title} ({it.year or 'n.d.'})"


class SupportMiner:
    """A miner that checks a quoted statement against evidence notes with V-SUPPORT (at least ``SUPPORT_JUDGES``
    cross-vendor judges, all agreeing; no ``uncertain_support`` checkpoint). The judge tasks run through the stage's task
    executor: linked to the run but not counted against the stage budget (``tasks_used``), and a rate-capped judge
    pauses the run (``RateCapped`` propagates). ``judge_drivers`` and ``judge_providers`` replace the configured judges
    (tests)."""

    def __init__(self, judge_drivers: list[DriverSpec] | None = None, judge_providers: dict | None = None):
        self.judge_drivers, self.judge_providers = judge_drivers, judge_providers

    def check_support(self, ctx: StageContext, text: str, note_ids: list[str], *, generator_vendor: str | None,
                      checks: Checks, data: dict) -> SupportResult | None:
        """Store ``text`` as a gap-mining claim (written by ``generator_vendor``) with the notes as its evidence and run
        V-SUPPORT on it; records ``claim_id``, ``verdict`` and ``opinions`` in ``data``. The check
        ``support`` is left to the caller (which verdict passes depends on the gap)."""

        def run(*, role: str, instructions: str, inputs: dict, output_schema, **task):
            return ctx.executor.run(role, instructions, inputs, output_schema, stage=ctx.stage, attempt=ctx.attempt,
                                    **task)

        svc = ArgumentService(ctx.ps)
        try:
            claim = ClaimIn(text=text)
        except ValidationError as e:  # longer than a claim may be
            checks.failed("support", f"the quote cannot be checked as a claim ({e.errors()[0]['msg']})")
            return None
        cid = svc.add_claim(claim, generator_vendor=generator_vendor, actor=ctx.actor, origin="gaps")
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=note_ids), actor=ctx.actor)
        res = verify_support(ctx.kernel, ctx.registry, ctx.ps, cid, actor=ctx.actor,
                             judge_drivers=self.judge_drivers, judge_providers=self.judge_providers,
                             min_judges=SUPPORT_JUDGES, checkpoint=False, run=run)
        data.update(claim_id=cid, verdict=res.verdict, opinions=[o.model_dump(mode="json") for o in res.opinions])
        return res


def default_miners() -> list[Miner]:
    """The gap miners in the order the worker runs them."""
    from rk.discovery.gaps import (  # the miners import this module
        contradiction,
        distortion,
        limitation,
        maphole,
        retraction,
    )

    return [limitation.mine, retraction.mine, maphole.mine, contradiction.ContradictionMiner(),
            distortion.DistortionMiner()]


class GapsWorker:
    def __init__(self, miners: list[Miner] | None = None):
        self.miners = list(miners) if miners is not None else default_miners()

    def run(self, ctx: StageContext) -> StageOutcome:
        landscape = stage_row(ctx.ps, "landscape")
        if landscape is None or landscape["passed_attempt"] is None:
            return StageOutcome("failed", reason="landscape has not passed")
        cls = clusters(ctx.ps, landscape["passed_attempt"])
        problems: list[str] = []
        for c in cls:
            for iid in c["representative_ids"][:FULLTEXT_PER_CLUSTER]:
                _, err = per_item(f"full text of {iid}", lambda iid=iid: ctx.sources.fulltext(iid))
                if err:  # the work has no full text for the miners; the others are still fetched
                    problems.append(err)
        verified = 0
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
