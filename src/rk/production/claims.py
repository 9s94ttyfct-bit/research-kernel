"""P1's rule for a claim that is not supported (spec §5.3, §9.2): delete it, or rewrite it narrower and weaker as a new
claim that must verify as supported. Never a vote, never "kept as unproven".

``narrow_or_withdraw`` asks the Arguer once for the rewrite (``NarrowedClaim``, a ``ClaimDraft``: no statistical kind,
so a model never rewrites a kernel-written ``stat_result``, which is withdrawn directly). The kernel stores the rewrite
with ``narrowed_from`` and the original's evidence, verifies it (V-SUPPORT through the stage's task executor, its
judges excluding the Arguer's vendor, with V-NUM; then V-HEDGE) and withdraws the original either way: replaced by its
supported rewrite, or, when the rewrite is not supported either, together with it. ``claim_limitations`` lists both
for the limitations input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rk.argument import ArgumentService, ClaimDraft, ClaimIn, EvidenceIn
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import StageContext
from rk.verify.support import check_claim_hedge, evidence_texts, verify_support

ROLE = "arguer"
NARROW_TASK = (
    "The claim below could not be verified as supported by its evidence. Rewrite it narrower and weaker so that the "
    "given evidence passages support it as written: a smaller population, setting, exposure or outcome, a hedged "
    "rather than categorical verb, no universal word (all, every, never, always, none) the passages do not state, and "
    "no number the passages do not contain. Keep its kind unless a weaker kind fits better. Give only the rewritten "
    "claim; the kernel links the same evidence and verifies it again. Follow the guidance when there is any.")


class NarrowedClaim(ClaimDraft):
    """The Arguer's narrower, weaker rewrite of a claim; the kernel sets ``narrowed_from`` and links the evidence."""


@dataclass(frozen=True)
class NarrowOutcome:
    """What ``narrow_or_withdraw`` did to ``claim_id``: ``narrowed`` (into the supported ``new_claim_id``) or
    ``withdrawn`` (with its rewrite, when there is one); ``reason`` says why."""
    claim_id: str
    status: Literal["narrowed", "withdrawn"]
    new_claim_id: str | None
    reason: str


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _verdict_reason(claim: dict) -> str:
    return f"its verification gave {claim['status']}"


def narrow_or_withdraw(ctx: StageContext, claim_id: str) -> NarrowOutcome:
    """P1 only (``ValueError`` in P2, where the human decides): narrow the not-supported claim into a supported
    rewrite, or withdraw it (see the module docstring). A supported or withdrawn claim is refused (``ValueError``)."""
    ps = ctx.ps
    if (ps.meta("production_mode") or "collab") != "auto":
        raise ValueError("narrow_or_withdraw is P1's rule: in P2 the researcher decides on an unsupported claim")
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    if claim["status"] in ("supported", "withdrawn"):
        raise ValueError(f"claim {claim_id} is {claim['status']}: there is nothing to narrow or withdraw")
    why = _verdict_reason(claim)
    if claim.get("kind") == "stat_result":
        reason = f"{why}; a kernel-written statistical result is never rewritten by a model"
        svc.withdraw_claim(claim_id, reason=reason, actor=ctx.actor)
        return NarrowOutcome(claim_id, "withdrawn", None, reason)
    inputs = {"claim": {"text": claim["text"], "kind": claim["kind"], "scope_limits": claim.get("scope_limits") or []},
              "evidence_passages": evidence_texts(claim), "why": why}
    if ctx.guidance:
        inputs["guidance"] = ctx.guidance
    r = ctx.executor.run(ROLE, NARROW_TASK, inputs, NarrowedClaim, stage=ctx.stage, attempt=ctx.attempt, tools=[])
    if not r.ok or r.output is None or _same(r.output.text, claim["text"]):
        reason = f"{why}; the Arguer gave " + ("no narrower rewrite" + (f" ({r.error})" if r.error else "")
                                               if not r.ok or r.output is None else "the same text again")
        svc.withdraw_claim(claim_id, reason=reason, actor=ctx.actor)
        return NarrowOutcome(claim_id, "withdrawn", None, reason)
    new = svc.add_claim(ClaimIn(text=r.output.text, kind=r.output.kind, scope_limits=r.output.scope_limits,
                                narrowed_from=claim_id),
                        generator_vendor=vendor_of(r.provider, r.model), actor=ctx.actor,
                        origin=claim.get("origin") or "argument")
    for e in claim["evidence"]:
        svc.link_evidence(EvidenceIn(claim_id=new, note_ids=e["note_ids"], criteria=e.get("criteria") or {},
                                     interpretation=e.get("interpretation") or ""), actor=ctx.actor,
                          actor_kind="kernel")

    def run(*, role: str, instructions: str, inputs: dict, output_schema, **task):
        return ctx.executor.run(role, instructions, inputs, output_schema, stage=ctx.stage, attempt=ctx.attempt,
                                **task)

    res = verify_support(ctx.kernel, ctx.registry, ps, new, actor=ctx.actor, checkpoint=False, run=run)
    hedge = check_claim_hedge(ps, new, actor=ctx.actor)
    if res.verdict == "supported" and hedge.ok:
        svc.withdraw_claim(claim_id, reason=f"{why}; narrowed to {new}, which is supported", actor=ctx.actor)
        return NarrowOutcome(claim_id, "narrowed", new, f"narrowed to {new}")
    failed = res.verdict if res.verdict != "supported" else "V-HEDGE overstated"
    svc.withdraw_claim(new, reason=f"narrower rewrite of {claim_id} not supported ({failed})", actor=ctx.actor)
    reason = f"{why}; its narrower rewrite {new} was not supported either ({failed})"
    svc.withdraw_claim(claim_id, reason=reason, actor=ctx.actor)
    return NarrowOutcome(claim_id, "withdrawn", new, reason)


def claim_limitations(ps, claim_ids: list[str]) -> dict:
    """The limitations input of ``claim_ids`` and their rewrites (followed through ``narrowed_from``): every withdrawn
    one with its reason, and every rewrite with the claim it narrows and its status."""
    svc = ArgumentService(ps)
    ids = list(dict.fromkeys(claim_ids))
    every = svc.list_claims(origin=("argument", "gaps", "synthesis"))
    while True:
        more = [c["id"] for c in every if c.get("narrowed_from") in ids and c["id"] not in ids]
        if not more:
            break
        ids += more
    claims = [svc.get_claim(c) for c in ids]
    return {"withdrawn": [{"claim_id": c["id"], "text": c["text"], "reason": c.get("withdrawn_reason")}
                          for c in claims if c["status"] == "withdrawn"],
            "narrowed": [{"claim_id": c["id"], "narrowed_from": c["narrowed_from"], "text": c["text"],
                          "status": c["status"]} for c in claims if c.get("narrowed_from")]}
