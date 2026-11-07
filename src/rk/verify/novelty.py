"""V-NOVELTY (spec §9.1): the works closest to a text, each with how the text differs from it, as a reference for the
person who reviews it. It is never a novelty verdict: nothing here says, or may say, that the text is novel.

The Scout writes 2-3 English queries; each runs on the first two discovery sources (logged, purpose ``novelty``); the
hits are fused by reciprocal rank (RRF) and the top ``k`` kept. Then each judge (``JUDGE_ROLES``) describes how the
text differs from every closest work, keyed by the judge's vendor; a judge whose vendor fails the ``run_judges`` rules
(unknown, human, the generator's, or another judge's) is skipped. Cost: 1 scout task + at most 3 judge tasks.

``search_closest`` and ``work_inputs`` are shared with the already-done check (``rk.discovery.already_done``); the
judges are admitted by ``rk.verify.judge.admitted_judges``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from rk.discovery.models import ClosestWork, LandscapeQueries
from rk.discovery.sources import log_and_search
from rk.llm.drivers import DriverSpec
from rk.verify.judge import admitted_judges

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext

NOTE = "reference only; never a novelty verdict"
QUERIES_MIN, QUERIES_MAX = 2, 3
SOURCES_USED = 2
SEARCH_LIMIT = 10
RRF_K = 60  # the usual reciprocal-rank-fusion constant: score = sum of 1 / (RRF_K + rank)
ABSTRACT_CHARS = 600
# A novelty claim, which no difference and no RQ's finer.novel may make: "novel"/"novelty" (so "novel anticoagulants"
# is caught too, by design) and the common phrasings of "nobody did this before".
NOVEL = re.compile(
    r"\bnovel"
    r"|\bfirst\s+(?:study|work|paper|report|time|to)\b"
    r"|\bnever\s+(?:been\s+)?(?:studied|investigated|examined|reported|done|addressed)\b"
    r"|\bno\s+(?:prior|previous|earlier)\s+(?:study|studies|work|research)\b"
    r"|\bunprecedented\b|\bunexplored\b"
    r"|\bnot\s+yet\s+(?:been\s+)?(?:studied|investigated|explored)\b",
    re.IGNORECASE)


def novelty_claims(texts: list[tuple[str, str]]) -> list[str]:
    """One failure per named text (``(name, text)``) that claims novelty; ``[]`` when none does. Every generated
    text of a topic or an RQ goes through it: the result is a rerun reason."""
    return [f"{name} claims novelty; say what was not found within the searched scope"
            for name, text in texts if NOVEL.search(text or "")]

QUERIES_TASK = (
    "Write 2 or 3 search queries in English that retrieve the published works closest to this text (same question, "
    "population, method or data). The kernel runs the queries; do not search yourself.")
DIFFERENCES_TASK = (
    "For each given work, state in one or two sentences how the text differs from it: its question, population, "
    "method, data or scope. Use only the given titles and abstracts; give each work's item id exactly as given. "
    "Describe differences only: never say or judge whether the text is novel, new or original.")


class DifferenceOut(BaseModel):
    item_id: str
    difference: str


class DifferencesOut(BaseModel):
    """A judge's differences between a text and its closest works; differences only, never a novelty verdict."""
    items: list[DifferenceOut]


class NoveltyReport(BaseModel):
    """``found``: distinct works the searches returned (``closest`` is shorter than ``k`` only when it is smaller);
    ``judged``: the vendors whose differences were kept; ``skipped``: judge role -> why it gave none; ``problems``:
    what made the reference unusable (no searches ran)."""
    closest: list[ClosestWork]
    queries: list[str]
    sources_used: list[str]
    found: int = 0
    judged: list[str] = []
    skipped: dict[str, str] = {}
    problems: list[str] = []
    note: str = NOTE


@dataclass(frozen=True)
class ClosestSearch:
    """The Scout's queries, the sources they ran on and every distinct hit fused by RRF (best first); ``problem`` says
    why no search ran (too few usable queries)."""
    queries: list[str]
    sources_used: list[str]
    ranked: list[str]
    problem: str = ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in (v.strip() for v in values):
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def rrf(rankings: list[list[str]]) -> list[str]:
    """Reciprocal-rank fusion of ranked id lists; ties keep the order of first appearance."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, iid in enumerate(dict.fromkeys(ranking), 1):
            score[iid] = score.get(iid, 0.0) + 1.0 / (RRF_K + rank)
    order = {iid: n for n, iid in enumerate(score)}
    return sorted(score, key=lambda i: (-score[i], order[i]))


def search_closest(ctx: StageContext, text: str, *, task: str, purpose: str, queries_min: int,
                   queries_max: int) -> ClosestSearch:
    """The Scout writes queries for ``text`` (``task``); the first ``queries_max`` distinct ones run on the first two
    discovery sources, each search logged with ``purpose``; the hits are fused by RRF. Fewer than ``queries_min``
    usable queries: no search runs and ``problem`` says why."""
    sources = ctx.sources.sources()[:SOURCES_USED]
    res = ctx.executor.run("scout", task, {"text": text}, LandscapeQueries, stage=ctx.stage, attempt=ctx.attempt,
                           tools=[])
    queries = _unique(res.output.queries)[:queries_max] if res.ok and res.output is not None else []
    if len(queries) < queries_min:
        why = res.error if not res.ok else f"{len(queries)} queries"
        return ClosestSearch(queries, sources, [],
                             problem=f"the scout gave no usable queries ({why}; need {queries_min}-{queries_max})")
    rankings = [log_and_search(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose=purpose, query=q,
                               source=s, limit=SEARCH_LIMIT, actor=ctx.actor)
                for q in queries for s in sources]
    return ClosestSearch(queries, sources, rrf(rankings))


def work_inputs(ctx: StageContext, item_ids: list[str], *, abstract_chars: int = ABSTRACT_CHARS) -> list[dict]:
    """What a judge sees of each work: item id, title, year and the start of the abstract."""
    out = []
    for iid in item_ids:
        it = ctx.lib.get(iid)
        out.append({"item_id": iid, "title": it.title, "year": it.year,
                    "abstract": (it.abstract or "")[:abstract_chars]})
    return out


def novelty_reference(ctx: StageContext, text: str, *, k: int = 5, generator_vendor: str | None = None,
                      judges: list[DriverSpec] | None = None) -> NoveltyReport:
    """The ``k`` works closest to ``text`` with each judge's differences (see the module doc). ``generator_vendor`` is
    the vendor of the role that wrote ``text``; ``judges`` replaces the configured judge drivers (tests)."""
    found = search_closest(ctx, text, task=QUERIES_TASK, purpose="novelty", queries_min=QUERIES_MIN,
                           queries_max=QUERIES_MAX)
    if found.problem:
        return NoveltyReport(closest=[], queries=found.queries, sources_used=found.sources_used,
                             problems=[found.problem])
    closest = [ClosestWork(item_id=i, title=(it := ctx.lib.get(i)).title, year=it.year) for i in found.ranked[:k]]
    report = NoveltyReport(closest=closest, queries=found.queries, sources_used=found.sources_used,
                           found=len(found.ranked))
    if closest:
        _judge(ctx, text, report, generator_vendor, judges)
    return report


def _judge(ctx: StageContext, text: str, report: NoveltyReport, generator_vendor: str | None,
           judges: list[DriverSpec] | None) -> None:
    works = work_inputs(ctx, [c.item_id for c in report.closest])
    by_id = {c.item_id: c for c in report.closest}
    admitted, report.skipped = admitted_judges(ctx, {generator_vendor}, judges)
    for role, spec, vendor in admitted:
        r = ctx.executor.run(role, DIFFERENCES_TASK, {"text": text, "works": works}, DifferencesOut, stage=ctx.stage,
                             attempt=ctx.attempt, tools=[], driver=spec, max_turns=4, timeout_s=300)
        if not r.ok or r.output is None:
            report.skipped[role] = r.error or "failed"
            continue
        for d in r.output.items:
            work, diff = by_id.get(d.item_id), d.difference.strip()
            if work is not None and diff and vendor not in work.differences and not NOVEL.search(diff):
                work.differences[vendor] = diff
        report.judged.append(vendor)
