"""The P1 multi-vendor vote: where P2 asks the human, P1 asks the judges (``rk.roles.policy.JUDGE_ROLES``) the same
question. Only admitted judges vote: distinct known, non-human vendors, none the generator's (the shared admission of
``rk.verify.judge.admitted_judges``). The decision is the choice shared by every admitted judge; fewer than two
admitted judges, any disagreement, or a failed or invalid answer gives the conservative option. A caller may ask for a
majority instead (``majority``): the choice of more than half of the admitted judges, else the conservative option
(your decision, 2026-11-14: the full-text screening vote). Every vote is recorded
(``vote.decided``) as not human-reviewed.

A vote may ask for a richer answer (``output``, a ``VoteOut`` with more fields, e.g. the protocol judges' points, or
the full-text record vote's ``unsettled``: ``rk.production.screening.RecordVoteOut``): each opinion then stores those
fields too. A caller may also tighten a judge's choice (``tighten``): a non-conservative choice the caller rules out
counts as the conservative option (the opinion's ``effective`` choice), never the other way. Before that, a caller
may ask a judge one follow-up question about its answer (``follow_up``, e.g. the protocol judge's marks it left out),
whose answer the caller merges into the judge's. Every other vote asks and stores exactly
``{role, vendor, choice, reason}``."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from rk.production.store import add_vote, recorded_vote
from rk.verify.judge import admitted_judges

if TYPE_CHECKING:
    from rk.llm.drivers import DriverSpec
    from rk.llm.types import TaskResult
    from rk.orchestrator.engine import StageContext

VOTE_TASK = (
    "Answer the question about the subject with exactly one of the options, written as given, and give a short "
    "reason grounded in the subject. Do not search or use tools.")


class VoteOut(BaseModel):
    """A judge's answer: one of the options, and why."""
    choice: str
    reason: str = ""


@dataclass(frozen=True)
class FollowUp:
    """One more question to a judge in the same vote (``vote``'s ``follow_up``): the ``task`` with its ``inputs``,
    answered as ``output``; ``merge(answer, out)`` gives the judge's answer with the follow-up's ``out`` merged in and
    the fields the opinion also records about it."""
    task: str
    inputs: dict
    output: type[BaseModel]
    merge: Callable[[VoteOut, BaseModel], tuple[VoteOut, dict]]


@dataclass(frozen=True)
class VoteResult:
    """``opinions``: each judge that ran, as ``{role, vendor, choice, reason}`` (``choice`` is None when the judge
    failed or answered outside the options), plus the extra fields of the vote's ``output`` and, for a tightened
    choice, ``effective``; ``reason`` says why the conservative option was used and why a choice was tightened, if
    either happened."""
    id: str
    decision: str
    conservative_used: bool
    opinions: list[dict]
    reason: str = ""


def vote(ctx: StageContext, subject_type: str, subject_id: str, question: str, options: list[str], conservative: str,
         inputs: dict, *, generator_vendors: Collection[str | None] = (), reuse: bool = False,
         output: type[VoteOut] = VoteOut, per_judge: Callable[[str], dict] | None = None,
         tighten: Callable[[str, VoteOut], str] | None = None,
         follow_up: Callable[[str, VoteOut], FollowUp | None] | None = None, majority: bool = False) -> VoteResult:
    """Ask every admitted judge ``question`` about the subject (``inputs``, what they see of it) through
    ``ctx.executor``. ``generator_vendors`` are the vendors that generated the subject (e.g. the Ideator's and the
    Scout's for a protocol), empty when it is no generated text (e.g. a screened record). With ``reuse``, a vote
    already recorded on the subject in the attempt (one interrupted before its outcome was noted) is returned instead
    of asking again.

    ``output`` is the answer's model (default ``VoteOut``); each opinion also stores its fields beyond ``choice`` and
    ``reason``. ``per_judge(role)`` adds task inputs for that judge (e.g. which judge it is). ``follow_up(role, answer)``
    gives the one more question (``FollowUp``) a judge with a valid answer is asked, else ``None``: it runs as the
    judge's own task does (same role, driver, stage and attempt, so the same rate caps and task accounting), at most
    once per judge and vote, and its merged answer is the judge's answer from then on, with the fields ``merge`` gives
    recorded in the opinion; a failed follow-up leaves the answer and the opinion as they were. Since the merged
    answer is recorded with the vote, a reused vote asks nothing again. ``tighten(role, answer)`` says why a judge's
    valid, non-conservative choice counts as the conservative option (``""``: it does not); the opinion keeps the
    ``choice`` as given with that ``effective`` choice, the decision is taken over the effective choices, and the
    vote's ``reason`` names each tightened judge.

    With ``majority`` the decision is the (effective) choice of more than half of the admitted judges, a failed or
    invalid answer counting for no option, and the vote's ``reason`` names each judge that chose otherwise; with no
    such choice it is the conservative option. Fewer than two admitted judges still give the conservative option.

    Calibration (P1 protocol rerun 2, 2026-11-12): ``follow_up``, opt-in like ``output``, ``per_judge`` and
    ``tighten``. Your decision (2026-11-14, after srrepro10/11: 2 of the reviews' included studies lost at the
    full-text vote had two of three judges choosing include, one excluding on a misread criterion): ``majority``."""
    if not options or len(set(options)) != len(options):
        raise ValueError(f"options must be distinct and non-empty: {options!r}")
    if conservative not in options:
        raise ValueError(f"the conservative option {conservative!r} is not one of {options!r}")
    if reuse and (done := recorded_vote(ctx.ps, subject_type, subject_id, stage=ctx.stage,
                                        attempt=ctx.attempt)) is not None:
        return VoteResult(done["id"], done["decision"], done["conservative_used"], done["opinions"], done["reason"])
    admitted, skipped = admitted_judges(ctx, generator_vendors, None)
    opinions: list[dict] = []
    tightened: list[str] = []
    if len(admitted) < 2:
        reason = f"fewer than two admitted judges (skipped: {skipped})"
    else:
        task_inputs = {"question": question, "options": options, "subject": inputs}
        for role, spec, vendor in admitted:
            r = _ask(ctx, role, spec, VOTE_TASK, task_inputs | (per_judge(role) if per_judge else {}), output)
            if not r.ok or r.output is None:
                opinions.append({"role": role, "vendor": vendor, "choice": None, "reason": r.error or "failed"})
                continue
            answer = r.output
            if answer.choice not in options:
                opinions.append({"role": role, "vendor": vendor, "choice": None, "reason": (
                    f"answered {answer.choice!r}, not one of the options: {answer.reason}"), **_extra(answer)})
                continue
            recorded: dict = {}
            if follow_up is not None and (more := follow_up(role, answer)) is not None:
                f = _ask(ctx, role, spec, more.task, more.inputs, more.output)
                if f.ok and f.output is not None:
                    answer, recorded = more.merge(answer, f.output)
            opinion = {"role": role, "vendor": vendor, "choice": answer.choice, "reason": answer.reason,
                       **_extra(answer), **recorded}
            if answer.choice != conservative and tighten is not None and (why := tighten(role, answer)):
                opinion["effective"] = conservative
                tightened.append(f"{role} ({vendor}) {why}")
            opinions.append(opinion)
        effective = [o.get("effective", o["choice"]) for o in opinions]
        won = next((c for c in options if effective.count(c) * 2 > len(effective)), None) if majority else None
        if majority and won is None:
            reason = "no choice has a majority of the judges"
        elif majority:
            reason = "; ".join(f"by majority: {o['role']} ({o['vendor']}) chose {o['choice'] or 'nothing valid'}"
                               for o, c in zip(opinions, effective, strict=True) if c != won)
        elif None in effective:
            reason = "a judge gave no valid choice"
        elif len(set(effective)) > 1:
            reason = "the judges disagree"
        else:
            reason = ""
    conservative_used = won is None if majority and len(admitted) >= 2 else bool(reason)
    decision = (conservative if conservative_used
                else won if majority else opinions[0].get("effective", opinions[0]["choice"]))
    reason = "; ".join(r for r in (reason, *tightened) if r)
    vid = add_vote(ctx.ps, subject_type=subject_type, subject_id=subject_id, question=question, options=options,
                   conservative=conservative, decision=decision, conservative_used=conservative_used, reason=reason,
                   opinions=opinions, skipped=skipped, stage=ctx.stage, attempt=ctx.attempt, actor=ctx.actor)
    return VoteResult(vid, decision, conservative_used, opinions, reason)


def _ask(ctx: StageContext, role: str, spec: DriverSpec, task: str, inputs: dict,
         model: type[BaseModel]) -> TaskResult:
    """One judge task of the vote (its answer or its follow-up) through ``ctx.executor`` on the judge's driver."""
    return ctx.executor.run(role, task, inputs, model, stage=ctx.stage, attempt=ctx.attempt, tools=[], driver=spec,
                            max_turns=4, timeout_s=300)


def _extra(answer: VoteOut) -> dict:
    """The answer's fields beyond ``choice`` and ``reason`` (those of a richer ``output``)."""
    return answer.model_dump(mode="json", exclude=set(VoteOut.model_fields))
