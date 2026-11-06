"""V-SUPPORT (spec §9.1): does the linked evidence support the claim?

supported      p ≥ τ_hi and ≥ min_judges cross-vendor judges, all "supported" (with verbatim quotes)
unsupported /  p ≤ τ_lo and ≥ min_judges judges, all "unsupported"/"contradicted"
contradicted
uncertain      everything else, including a missing layer 1, missing evidence, or too few eligible judges

V-NUM runs alongside: a number in the claim that its evidence does not contain keeps the claim from "supported".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.checkpoints import create_checkpoint
from rk.llm.drivers import DriverSpec
from rk.verify.judge import JudgeOpinion, run_judges
from rk.verify.minicheck import Layer1Unavailable, get_minicheck
from rk.verify.numbers import Num, check_numbers
from rk.verify.records import record_verification

Verdict = Literal["supported", "uncertain", "unsupported", "contradicted"]


class SupportResult(BaseModel):
    verdict: Verdict
    p: float | None
    layer1_reason: str | None = None
    n_chunks: int | None = None
    opinions: list[JudgeOpinion] = []
    numbers_ok: bool = True
    ungrounded_numbers: list[Num] = []
    tau_hi: float
    tau_lo: float
    min_judges: int
    reason: str | None = None


def combine(p: float | None, opinions: list[JudgeOpinion], *, tau_hi: float, tau_lo: float,
            min_judges: int) -> Verdict:
    valid = [o for o in opinions if o.error is None]
    labels = {o.label for o in valid}
    if p is None or len(valid) < min_judges:
        return "uncertain"
    if p >= tau_hi and labels == {"supported"}:
        return "supported"
    if p <= tau_lo and labels and labels <= {"unsupported", "contradicted"}:
        return "contradicted" if labels == {"contradicted"} else "unsupported"
    return "uncertain"


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


def verify_support(kernel, registry, ps, claim_id: str, *, actor: str, layer1=None,
                   judge_drivers: list[DriverSpec] | None = None, judge_providers: dict | None = None,
                   min_judges: int | None = None) -> SupportResult:
    s = kernel.settings()
    tau_hi, tau_lo = s.support_tau_hi, s.support_tau_lo
    k = s.support_min_judges if min_judges is None else min_judges
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    texts = evidence_texts(claim)
    base = {"tau_hi": tau_hi, "tau_lo": tau_lo, "min_judges": k}
    if not texts:
        result = SupportResult(verdict="uncertain", p=None, reason="no_evidence", **base)
    else:
        doc = "\n\n".join(texts)
        p = n_chunks = reason = None
        try:
            score = (layer1 or get_minicheck(kernel.home / "models", s.support_model)).score(doc, claim["text"])
            p, n_chunks = score.p, score.n_chunks
        except Layer1Unavailable as e:
            reason = f"layer1_unavailable: {e}"
        opinions = run_judges(kernel, registry, project=_slug(ps), claim_text=claim["text"], evidence_text=doc,
                              generator_vendor=claim.get("generator_vendor"), drivers=judge_drivers,
                              providers=judge_providers)
        verdict = combine(p, opinions, tau_hi=tau_hi, tau_lo=tau_lo, min_judges=k)
        nums = check_numbers(claim["text"], texts)
        if verdict == "supported" and not nums.ok:
            verdict = "uncertain"
        result = SupportResult(verdict=verdict, p=p, layer1_reason=reason, n_chunks=n_chunks, opinions=opinions,
                               numbers_ok=nums.ok, ungrounded_numbers=nums.ungrounded, **base)
        record_verification(ps, "claim", claim_id, "V-NUM", "pass" if nums.ok else "ungrounded_number",
                            {"ungrounded": [n.model_dump() for n in nums.ungrounded]}, actor=actor)
    vid = record_verification(ps, "claim", claim_id, "V-SUPPORT", result.verdict, result.model_dump(mode="json"),
                              actor=actor)
    svc.set_claim_status(claim_id, result.verdict, verification_id=vid, actor=actor)
    if result.verdict == "uncertain" and (ps.meta("production_mode") or "collab") == "collab":
        create_checkpoint(ps, "uncertain_support", f"Is this claim supported by its evidence?\n\n{claim['text']}",
                          {"claim_id": claim_id, "p": result.p, "opinions": [o.model_dump() for o in result.opinions],
                           "reason": result.reason or result.layer1_reason}, stage="argue", actor=actor)
    return result


def _slug(ps) -> str:
    return ps.meta("slug") or ps.root.name
