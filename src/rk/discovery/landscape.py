"""The landscape stage: the Scout's queries run on the first two sources (every search logged), each new hit
verified, then the Scout's clusters over the hits, each with the publication trend of its query. Gate: at least one
cluster, and every cluster has >= 5 verified items, all from this attempt's search log."""

from __future__ import annotations

from rk.discovery.models import Cluster, ClusterPlan, LandscapeQueries
from rk.discovery.sources import log_and_search
from rk.discovery.store import (
    add_cluster,
    clusters,
    intake_of,
    remove_cluster,
    searches,
)
from rk.library.service import Library
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import record_degraded
from rk.store.project import ProjectStore

QUERIES_MIN, QUERIES_MAX = 3, 8
SOURCES_PER_QUERY = 2
SEARCH_LIMIT = 25
MIN_VERIFIED = 5
ABSTRACT_CHARS = 600

QUERIES_TASK = (
    "Map the literature for this project. Write 3 to 8 search queries in English (whatever the project language) "
    "that together cover its main subtopics and the opposing or competing views, not only the mainstream position. "
    "Follow the guidance when there is any. The kernel runs the queries; do not search yourself.")
CLUSTER_TASK = (
    "Group the works into clusters of related research. Use only the item ids given in `works`; never invent or "
    "alter an id. For each cluster give a short label, a one-sentence description, a search query in English that "
    "retrieves works like these (used for its publication trend), its item ids (at least 5 where the works allow) and "
    "1 to 3 representative ids from its own items. Follow the guidance when there is any.")


def _unique(values: list[str]) -> list[str]:
    """Drop empty and repeated (case-insensitive) values, keeping the first spelling and the order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in (v.strip() for v in values):
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _cluster_failures(ps: ProjectStore, attempt: int) -> list[tuple[dict, str]]:
    """Each cluster of the attempt that fails the gate, with why."""
    lib = Library(ps)
    searched = {i for s in searches(ps, "landscape", attempt) for i in s["item_ids"]}
    out: list[tuple[dict, str]] = []
    for c in clusters(ps, attempt):
        outside = [i for i in c["item_ids"] if i not in searched]
        if outside:
            out.append((c, f"cluster {c['label']!r}: {len(outside)} items not in this attempt's search log"))
            continue
        verified = sum(lib.get(i).ref_status == "pass" for i in c["item_ids"])
        if verified < MIN_VERIFIED:
            out.append((c, f"cluster {c['label']!r}: {verified} verified sources (need {MIN_VERIFIED})"))
    return out


class LandscapeWorker:
    def run(self, ctx: StageContext) -> StageOutcome:
        intake = intake_of(ctx.ps)
        if intake is None:
            return StageOutcome("failed", reason="intake missing")
        context = {"project": {"title": ctx.ps.meta("title"), "field": ctx.ps.meta("field")},
                   "intake": {"audience": intake.audience, "paper_type": intake.paper_type,
                              "language": intake.language}}
        if ctx.guidance:
            context["guidance"] = ctx.guidance

        res = ctx.executor.run("scout", QUERIES_TASK, context, LandscapeQueries, stage=ctx.stage,
                               attempt=ctx.attempt, tools=[])
        if not res.ok or res.output is None:
            return StageOutcome("rerun", reason=f"the scout gave no queries ({res.error})")
        queries = _unique(res.output.queries)[:QUERIES_MAX]
        if len(queries) < QUERIES_MIN:
            return StageOutcome("rerun", reason=f"the scout gave {len(queries)} queries (need "
                                                f"{QUERIES_MIN}-{QUERIES_MAX})")

        found: list[str] = []
        for query in queries:
            for source in ctx.sources.sources()[:SOURCES_PER_QUERY]:
                ids = log_and_search(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose="landscape",
                                     query=query, source=source, limit=SEARCH_LIMIT, actor=ctx.actor)
                found += [i for i in ids if i not in found]
        if not found:
            return StageOutcome("rerun", reason="the landscape searches found no works")
        for iid in found:
            if ctx.lib.get(iid).ref_status == "unverified":
                ctx.sources.verify(iid)

        works = []
        for iid in found:
            it = ctx.lib.get(iid)
            works.append({"item_id": iid, "title": it.title, "year": it.year,
                          "abstract": (it.abstract or "")[:ABSTRACT_CHARS]})
        res = ctx.executor.run("scout", CLUSTER_TASK, {**context, "queries": queries, "works": works}, ClusterPlan,
                               stage=ctx.stage, attempt=ctx.attempt, tools=[])
        if not res.ok or res.output is None:
            return StageOutcome("rerun", reason=f"the scout gave no clusters ({res.error})")

        searched = set(found)
        for c in res.output.clusters:
            items = [i for i in dict.fromkeys(c.item_ids) if i in searched]
            if not items:
                continue
            reps = [i for i in dict.fromkeys(c.representative_ids) if i in items]
            add_cluster(ctx.ps, ctx.attempt,
                        Cluster(label=c.label, description=c.description, query=c.query, item_ids=items,
                                representative_ids=reps, year_counts=ctx.sources.year_counts(c.query)),
                        actor=ctx.actor)
        return StageOutcome("done")

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        return StageOutcome("done")  # landscape opens no checkpoint

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        if not clusters(ps, attempt):
            return GateResult(False, ["no cluster"])
        failures = [why for _, why in _cluster_failures(ps, attempt)]
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1: drop the clusters that fail the gate; ``done`` while one is left."""
        weak = _cluster_failures(ctx.ps, ctx.attempt)
        for c, why in weak:
            remove_cluster(ctx.ps, c["id"], reason=why, actor=ctx.actor)
        if weak:
            record_degraded(ctx.ps, stage=ctx.stage, attempt=ctx.attempt, removed=[c["label"] for c, _ in weak],
                            reason="; ".join(why for _, why in weak), actor=ctx.actor)
        if not clusters(ctx.ps, ctx.attempt):
            return StageOutcome("failed", reason=f"no cluster with {MIN_VERIFIED} verified sources")
        return StageOutcome("done")
