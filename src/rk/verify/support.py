"""V-SUPPORT (spec §9.1, rule amended 2026-11-07: cloud judges only, no layer-1 model): does the linked evidence
support the claim?

supported      ≥ min_judges valid judges, and every valid judge says "supported" (with verbatim quotes)
unsupported /  ≥ min_judges valid judges, all sharing that one label (a split between the two is not agreement)
contradicted
uncertain      everything else, including missing evidence or too few valid judges

A judge is valid when it ran and its vendor is admitted (``rk.verify.judge.run_judges``: a known, non-human vendor
that is neither the claim author's nor another judge's); a failed or skipped judge does not count. V-NUM runs
alongside: a number in the claim that its evidence does not contain keeps the claim from "supported".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.checkpoints import create_checkpoint
from rk.llm.drivers import DriverSpec
from rk.verify.judge import JudgeOpinion, JudgeRunner, run_judges
from rk.verify.numbers import Num, check_numbers
from rk.verify.records import record_verification

Verdict = Literal["supported", "uncertain", "unsupported", "contradicted"]


class SupportResult(BaseModel):
    verdict: Verdict
    opinions: list[JudgeOpinion] = []
    numbers_ok: bool = True
    ungrounded_numbers: list[Num] = []
    min_judges: int
    reason: str | None = None


def combine(opinions: list[JudgeOpinion], *, min_judges: int) -> Verdict:
    """Spec §9.1 (amended 2026-11-07): the verdict is the one label shared by every valid judge (``error is None``)
    when there are at least ``min_judges`` of them and that label is a verdict; anything else is ``uncertain``."""
    valid = [o for o in opinions if o.error is None]
    labels = {o.label for o in valid}
    if not valid or len(valid) < min_judges or len(labels) != 1:
        return "uncertain"
    (label,) = labels
    return label if label in ("supported", "unsupported", "contradicted") else "uncertain"


def evidence_texts(claim: dict) -> list[str]:
    """The verified passages behind a claim: quote notes' text, paraphrase notes' original span."""
    out = []
    for ev in claim["evidence"]:
        for n in ev["notes"]:
            if n is None:
                continue
            if n["kind"] == "quote":
                out.append(n["text"])
            elif n["kind"] == "paraphrase" and n.get("source_span"):
                out.append(n["source_span"])
    return out


def verify_support(kernel, registry, ps, claim_id: str, *, actor: str,
                   judge_drivers: list[DriverSpec] | None = None, judge_providers: dict | None = None,
                   min_judges: int | None = None, checkpoint: bool = True,
                   run: JudgeRunner | None = None) -> SupportResult:
    """Check the claim against its linked evidence and record V-NUM, V-SUPPORT and the claim status. In collab mode an
    ``uncertain`` verdict opens an ``uncertain_support`` checkpoint unless ``checkpoint`` is false (the gap miners:
    their claims are evidence checks of a candidate gap, not claims of the argument). ``run`` replaces ``run_task`` for
    the judge tasks (``run_judges``), e.g. a stage's task executor; its exceptions propagate, and no verification of
    the claim is recorded then."""
    k = kernel.settings().support_min_judges if min_judges is None else min_judges
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    texts = evidence_texts(claim)
    if not texts:
        result = SupportResult(verdict="uncertain", min_judges=k, reason="no_evidence")
    else:
        doc = "\n\n".join(texts)
        opinions = run_judges(kernel, registry, project=_slug(ps), claim_text=claim["text"], evidence_text=doc,
                              generator_vendor=claim.get("generator_vendor"), drivers=judge_drivers,
                              providers=judge_providers, run=run)
        verdict = combine(opinions, min_judges=k)
        nums = check_numbers(claim["text"], texts)
        if verdict == "supported" and not nums.ok:
            verdict = "uncertain"
        result = SupportResult(verdict=verdict, opinions=opinions, numbers_ok=nums.ok,
                               ungrounded_numbers=nums.ungrounded, min_judges=k)
        record_verification(ps, "claim", claim_id, "V-NUM", "pass" if nums.ok else "ungrounded_number",
                            {"ungrounded": [n.model_dump() for n in nums.ungrounded]}, actor=actor)
    vid = record_verification(ps, "claim", claim_id, "V-SUPPORT", result.verdict, result.model_dump(mode="json"),
                              actor=actor)
    svc.set_claim_status(claim_id, result.verdict, verification_id=vid, actor=actor)
    if checkpoint and result.verdict == "uncertain" and (ps.meta("production_mode") or "collab") == "collab":
        create_checkpoint(ps, "uncertain_support", f"Is this claim supported by its evidence?\n\n{claim['text']}",
                          {"claim_id": claim_id, "opinions": [o.model_dump() for o in result.opinions],
                           "reason": result.reason}, stage="argue", actor=actor)
    return result


def _slug(ps) -> str:
    return ps.meta("slug") or ps.root.name
