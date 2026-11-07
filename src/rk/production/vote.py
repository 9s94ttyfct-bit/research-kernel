"""The P1 multi-vendor vote: where P2 asks the human, P1 asks the judges (``rk.roles.policy.JUDGE_ROLES``) the same
question. Only admitted judges vote: distinct known, non-human vendors, none the generator's (the shared admission of
``rk.verify.judge.admitted_judges``). The decision is the choice shared by every admitted judge; fewer than two
admitted judges, any disagreement, or a failed or invalid answer gives the conservative option. Every vote is recorded
(``vote.decided``) as not human-reviewed."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from rk.production.store import add_vote
from rk.verify.judge import admitted_judges

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext

VOTE_TASK = (
    "Answer the question about the subject with exactly one of the options, written as given, and give a short "
    "reason grounded in the subject. Do not search or use tools.")


class VoteOut(BaseModel):
    """A judge's answer: one of the options, and why."""
    choice: str
    reason: str = ""


@dataclass(frozen=True)
class VoteResult:
    """``opinions``: each judge that ran, as ``{role, vendor, choice, reason}`` (``choice`` is None when the judge
    failed or answered outside the options); ``reason`` says why the conservative option was used, if it was."""
    id: str
    decision: str
    conservative_used: bool
    opinions: list[dict]
    reason: str = ""


def vote(ctx: StageContext, subject_type: str, subject_id: str, question: str, options: list[str], conservative: str,
         inputs: dict, *, generator_vendors: Collection[str | None] = ()) -> VoteResult:
    """Ask every admitted judge ``question`` about the subject (``inputs``, what they see of it) through
    ``ctx.executor``. ``generator_vendors`` are the vendors that generated the subject (e.g. the Ideator's and the
    Scout's for a protocol), empty when it is no generated text (e.g. a screened record)."""
    if not options or len(set(options)) != len(options):
        raise ValueError(f"options must be distinct and non-empty: {options!r}")
    if conservative not in options:
        raise ValueError(f"the conservative option {conservative!r} is not one of {options!r}")
    admitted, skipped = admitted_judges(ctx, generator_vendors, None)
    opinions: list[dict] = []
    if len(admitted) < 2:
        reason = f"fewer than two admitted judges (skipped: {skipped})"
    else:
        task_inputs = {"question": question, "options": options, "subject": inputs}
        for role, spec, vendor in admitted:
            r = ctx.executor.run(role, VOTE_TASK, task_inputs, VoteOut, stage=ctx.stage, attempt=ctx.attempt,
                                 tools=[], driver=spec, max_turns=4, timeout_s=300)
            if not r.ok or r.output is None:
                opinions.append({"role": role, "vendor": vendor, "choice": None, "reason": r.error or "failed"})
            elif r.output.choice not in options:
                opinions.append({"role": role, "vendor": vendor, "choice": None,
                                 "reason": f"answered {r.output.choice!r}, not one of the options: {r.output.reason}"})
            else:
                opinions.append({"role": role, "vendor": vendor, "choice": r.output.choice,
                                 "reason": r.output.reason})
        choices = {o["choice"] for o in opinions}
        if None in choices:
            reason = "a judge gave no valid choice"
        elif len(choices) > 1:
            reason = "the judges disagree"
        else:
            reason = ""
    decision = opinions[0]["choice"] if not reason else conservative
    vid = add_vote(ctx.ps, subject_type=subject_type, subject_id=subject_id, question=question, options=options,
                   conservative=conservative, decision=decision, conservative_used=bool(reason), reason=reason,
                   opinions=opinions, skipped=skipped, stage=ctx.stage, attempt=ctx.attempt, actor=ctx.actor)
    return VoteResult(vid, decision, bool(reason), opinions, reason)
