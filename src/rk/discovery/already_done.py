"""The already-done check (spec §8.3): does published work already answer the research question?

The Scout writes 3-5 English queries; each runs on the first two discovery sources (logged, purpose ``already_done``);
the hits are fused by reciprocal rank (RRF) and the top 10 judged by ``judge`` and ``judge_2`` (``AnsweredOut``):
does each work already answer the RQ, with a quote from its title or abstract (the judge sees the first
``ABSTRACT_CHARS`` of the abstract). A judgement is valid only if its judge's vendor is admitted (known, not human, not
the RQ author's and not the other judge's: ``admitted_judges``) and its quote is verbatim in the title or in the part of
the abstract the judge saw (``span_in``); an invalid judgement counts as missing.

Result: ``already_done`` when some work is judged answered by two valid judges (of different vendors);
``not_found_in_scope`` when every work is judged not answered by two such judges; ``split`` otherwise, including when
fewer than two judge vendors gave valid judgements and when the RQ author's vendor is unknown (no judge can then be
shown to differ from it; ``split_reason`` says so). "Not found" holds only within the searched scope: nothing here
says the RQ is novel. Cost: 1 scout task + at most 2 judge tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from rk.discovery.models import RQBody
from rk.discovery.store import hypotheses, rq_row
from rk.llm.drivers import DriverSpec
from rk.verify.judge import span_in
from rk.verify.novelty import admitted_judges, search_closest, work_inputs

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext

QUERIES_MIN, QUERIES_MAX = 3, 5
TOP_K = 10
VOTES = 2  # valid judges of different vendors needed for "already done" and for "not found"
ABSTRACT_CHARS = 1500
Result = Literal["already_done", "not_found_in_scope", "split"]
UNKNOWN_AUTHOR = "the RQ author's vendor is unknown, so no judge can be shown to differ from it"

QUERIES_TASK = (
    "Write 3 to 5 search queries in English that retrieve published works that may already answer this research "
    "question (the same question, population, exposure or intervention, and outcome). The kernel runs the queries; "
    "do not search yourself.")
ANSWERED_TASK = (
    "For each given work, judge whether it already answers the research question: `answered` is true only when the "
    "work studies the same question and reports an answer to it. Give each work's item id exactly as given and a "
    "quote copied character for character from that work's title or abstract that your judgement rests on; a "
    "judgement without a verbatim quote is discarded. Use only the given titles and abstracts.")


class AnsweredItem(BaseModel):
    item_id: str
    answered: bool
    quote: str  # verbatim from the work's title or abstract


class AnsweredOut(BaseModel):
    """A judge's verdicts: whether each given work already answers the research question, each with a verbatim quote
    from the work's title or abstract."""
    items: list[AnsweredItem]


class Judgement(BaseModel):
    answered: bool
    quote: str
    ok_quote: bool  # the quote is verbatim in the work's title or in the part of its abstract the judge saw


class JudgedWork(BaseModel):
    item_id: str
    title: str
    year: int | None = None
    judgements: dict[str, Judgement] = {}  # judge vendor -> judgement

    def valid(self) -> dict[str, Judgement]:
        """The judgements that count: those of admitted judges (the only ones recorded) with a verbatim quote."""
        return {v: j for v, j in self.judgements.items() if j.ok_quote}


class AlreadyDone(BaseModel):
    """``found``: distinct works the searches returned (``works`` holds the top 10); ``judged``: vendors whose
    judgements were recorded; ``skipped``: judge role -> why it gave none; ``split_reason``: why the result is ``split``
    whatever the judges said (else ``""``); ``problems``: why no check ran (the result then means nothing and the RQ is
    checked again)."""
    result: Result = "split"
    queries: list[str] = []
    sources_used: list[str] = []
    works: list[JudgedWork] = []
    found: int = 0
    author_vendor: str | None = None
    judged: list[str] = []
    skipped: dict[str, str] = {}
    split_reason: str = ""
    problems: list[str] = []

    def answering(self) -> list[JudgedWork]:
        """The works at least one valid judgement says already answer the RQ."""
        return [w for w in self.works if any(j.answered for j in w.valid().values())]

    def answered_by_both(self) -> list[JudgedWork]:
        """The works ``VOTES`` valid judgements (of different vendors) say already answer the RQ."""
        return [w for w in self.works if sum(j.answered for j in w.valid().values()) >= VOTES]


def rq_input(rq: dict, hyps: list[dict]) -> dict:
    """What the scout and the judges see of a stored RQ: its three-step sentence, problem and hypotheses."""
    body = RQBody.model_validate(rq["body"])
    return {"question": body.three_step.sentence(), "condition": body.problem.condition,
            "pico": body.pico.model_dump() if body.clinical and body.pico else None,
            "hypotheses": [h["statement"] for h in hyps]}


def verdict(report: AlreadyDone) -> Result:
    if report.answered_by_both():
        return "already_done"
    if report.works and all(sum(not j.answered for j in w.valid().values()) >= VOTES for w in report.works):
        return "not_found_in_scope"
    return "split"


def already_done(ctx: StageContext, rq_id: str, *, author_vendor: str | None,
                 judges: list[DriverSpec] | None = None) -> AlreadyDone:
    """Check the stored RQ ``rq_id`` (see the module doc). ``author_vendor`` is the vendor of the ideator task that wrote
    it; ``judges`` replaces the configured judge drivers (tests)."""
    rq = rq_row(ctx.ps, rq_id)
    if rq is None:
        raise KeyError(f"no rq {rq_id}")
    question = rq_input(rq, hypotheses(ctx.ps, rq_id))
    found = search_closest(ctx, f"{question['question']}\n{question['condition']}", task=QUERIES_TASK,
                           purpose="already_done", queries_min=QUERIES_MIN, queries_max=QUERIES_MAX)
    report = AlreadyDone(queries=found.queries, sources_used=found.sources_used, found=len(found.ranked),
                         author_vendor=author_vendor)
    if found.problem:
        report.problems.append(found.problem)
        return report
    if not found.ranked:
        report.problems.append(f"the searches found no works ({len(found.queries)} queries on "
                               f"{', '.join(found.sources_used)})")
        return report
    report.works = [JudgedWork(item_id=i, title=(it := ctx.lib.get(i)).title, year=it.year)
                    for i in found.ranked[:TOP_K]]
    _judge(ctx, question, report, judges)
    if author_vendor is None:
        report.split_reason = UNKNOWN_AUTHOR
    report.result = "split" if report.split_reason else verdict(report)
    return report


def _judge(ctx: StageContext, question: dict, report: AlreadyDone, judges: list[DriverSpec] | None) -> None:
    works = work_inputs(ctx, [w.item_id for w in report.works], abstract_chars=ABSTRACT_CHARS)
    seen = {w["item_id"]: w for w in works}  # what the judges see of each work: quotes count only from it
    by_id = {w.item_id: w for w in report.works}
    admitted, report.skipped = admitted_judges(ctx, report.author_vendor, judges)
    for role, spec, vendor in admitted:
        r = ctx.executor.run(role, ANSWERED_TASK, {"rq": question, "works": works}, AnsweredOut, stage=ctx.stage,
                             attempt=ctx.attempt, tools=[], driver=spec, max_turns=4, timeout_s=300)
        if not r.ok or r.output is None:
            report.skipped[role] = r.error or "failed"
            continue
        for item in r.output.items:
            work = by_id.get(item.item_id)
            if work is None or vendor in work.judgements:  # an unknown work, or a second verdict on one: ignored
                continue
            shown = seen[work.item_id]
            ok = span_in(shown["title"], item.quote) or span_in(shown["abstract"], item.quote)
            work.judgements[vendor] = Judgement(answered=item.answered, quote=item.quote, ok_quote=ok)
        report.judged.append(vendor)
