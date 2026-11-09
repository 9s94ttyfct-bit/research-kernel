"""V-BALANCE (spec §9.1: "a claim marked ``contested``, or one with a sourced objection: its section states the
opposing view and cites the response"; Plan 5c Task 3). The balance facts of a claim (``balance_requirements``, read
from argue's passed attempt) are the one definition both ``check_balance`` and the Writer's claim inputs use. Per
section, for every claim the section cites (a ``[[clm:]]`` marker):

- **the counterpart** of each of its conflicts (``contested_by``: ``rk.production.argue.conflict_counterparts``, the
  other claim of a ``both_contested`` decision's conflict objection) is cited in the section after the claim's first
  citation (the same sentence counts). For the two claims of one conflict (each contested by the other) either order
  gives both sides: when the counterpart is said first, the claim itself follows it. A one-way counterpart cited only
  before the claim fails. A contested claim with no conflict recorded fails (nothing tells which view opposes it); a
  withdrawn counterpart is not asked for (the conflict is resolved, and citing it fails G-SUPPORT);
- **the response claim** of each answered objection on it or on its evidence (``answered_by``: ``responded``, among
  the attempt's objections, ``argument_objection_ids``; a conflict objection included: the opposing view and the
  answer to it) is cited in the section after the claim's first citation. An objection whose response claim was
  withdrawn since (``unanswered``) fails: nothing the section could cite answers it any more.

The accepted gap: a conceded or rejected objection, sourced or not, asks for nothing, as V-COVER has no marker for an
objection or a source, so no sentence could cite it (a conceded priority-1 objection already fails its claim's
G-SUPPORT until the claim is narrowed or withdrawn). The conceded sourced ones (``conceded_sourced``) go to the Writer,
who qualifies the claim, and to the audit's disclosure. Only the given section's sentences count: a counterpart in
another section balances nothing here. Without a passed argue attempt nothing can be checked: one problem says so.

Nothing here calls a model."""

from __future__ import annotations

from dataclasses import dataclass, field

from rk.argument import ArgumentService
from rk.orchestrator.state import passed_attempt
from rk.production.argue import argument_objection_ids, conflict_counterparts
from rk.production.common import entity_row
from rk.production.manuscript import Sentence
from rk.store.project import ProjectStore

NOT_ARGUED = "argue has not passed"
# in the problem of an objection whose response claim was withdrawn: argue must answer it again (the audit routes it
# there), no draft can
UNANSWERED = "the objection is unanswered"


@dataclass(frozen=True)
class Argued:
    """argue's passed attempt as V-BALANCE reads it: its conflict objections' counterparts and its objections."""
    attempt: int
    counterpart: dict[str, str]  # conflict objection id -> the claim it says contradicts its target
    objections: list[dict]


@dataclass(frozen=True)
class BalanceRequirements:
    contested: bool
    traced: bool  # a conflict is recorded for it (a contested claim without one is one-sided by itself)
    contested_by: list[str]  # the live counterparts of its conflicts
    answered_by: list[dict]  # {objection_id, response_claim_id}: responded, on it or its evidence, response live
    unanswered: list[dict]  # {objection_id, response_claim_id}: responded by a claim withdrawn since
    conceded_sourced: list[dict]  # {objection_id, text, source_id}: conceded, with a source (the accepted gap)
    targets: dict[str, str] = field(default_factory=dict)  # objection id -> its target (the claim or an evidence)


def argued(ps: ProjectStore) -> Argued:
    """argue's passed attempt for V-BALANCE; ``ValueError`` when argue has not passed."""
    attempt = passed_attempt(ps, "argue")
    if attempt is None:
        raise ValueError(NOT_ARGUED)
    return Argued(attempt, conflict_counterparts(ps, attempt),
                  [entity_row(ps, "objections", oid) for oid in argument_objection_ids(ps, attempt)])


def _live(svc: ArgumentService, cid: str | None) -> bool:  # an unknown claim is V-COVER's problem
    return bool(cid) and svc.exists(cid) and svc.get_claim(cid)["status"] != "withdrawn"


def balance_requirements(ps: ProjectStore, claim_id: str, *, facts: Argued | None = None) -> BalanceRequirements:
    """The claim's balance facts in argue's passed attempt (see the module docstring); ``facts`` when the caller
    already read them (``argued``). ``ValueError`` when argue has not passed."""
    facts = facts if facts is not None else argued(ps)
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    on = {claim_id, *(e["id"] for e in svc.list_evidence(claim_id))}
    mine = [o for o in facts.objections if o["target_id"] in on]
    conflicts = [facts.counterpart[o["id"]] for o in mine if o["id"] in facts.counterpart and o["target_id"] == claim_id]
    responded = [{"objection_id": o["id"], "response_claim_id": o["response_claim_id"]} for o in mine
                 if o.get("status") == "responded" and o.get("response_claim_id")]
    return BalanceRequirements(
        contested=bool(claim.get("contested")), traced=bool(conflicts),
        contested_by=list(dict.fromkeys(c for c in conflicts if _live(svc, c))),
        answered_by=[r for r in responded if _live(svc, r["response_claim_id"])],
        unanswered=[r for r in responded if not _live(svc, r["response_claim_id"])],
        conceded_sourced=[{"objection_id": o["id"], "text": o["text"], "source_id": o["source_id"]} for o in mine
                          if o.get("status") == "conceded" and o.get("source_id")],
        targets={o["id"]: o["target_id"] for o in mine})


def check_balance(ps: ProjectStore, section: str, sentences: list[Sentence]) -> list[str]:
    """V-BALANCE's problems for the section's sentences (see the module docstring), one per claim and missing
    counterpart or response, naming both; ``[]`` passes."""
    try:
        facts = argued(ps)
    except ValueError:
        return [f"section {section}: V-BALANCE cannot be checked: {NOT_ARGUED}"]
    first: dict[str, int] = {}  # claim -> index of the first sentence citing it
    cited_at: dict[str, list[int]] = {}
    for s in sentences:
        for m in s.markers:
            if m.kind == "clm" and m.target:
                first.setdefault(m.target, s.index)
                cited_at.setdefault(m.target, []).append(s.index)
    svc = ArgumentService(ps)
    reqs: dict[str, BalanceRequirements] = {}

    def req(cid: str) -> BalanceRequirements:
        if cid not in reqs:
            reqs[cid] = balance_requirements(ps, cid, facts=facts)
        return reqs[cid]

    def after(cid: str, other: str) -> bool:  # ``other`` cited in or after the first sentence citing ``cid``
        return any(i >= first[cid] for i in cited_at.get(other, []))

    out: list[str] = []
    for cid in first:
        if not _live(svc, cid):
            continue
        r = req(cid)
        if r.contested and not r.traced:
            out.append(f"section {section} is one-sided: {cid} is contested, but argue recorded no conflict naming "
                       f"its counterpart, so the opposing view cannot be cited")
        for other in r.contested_by:
            if not (after(cid, other) or (other in first and cid in req(other).contested_by)):
                out.append(f"section {section} is one-sided: {cid} is contested by {other}, and the section does "
                           f"not cite that counterpart [[clm:{other}]] after its first citation of {cid}")
        for a in r.answered_by:
            oid, rc = a["objection_id"], a["response_claim_id"]
            if not after(cid, rc):
                out.append(f"section {section} is one-sided: the objection {oid} to {_to(r, cid, oid)} was answered "
                           f"by {rc}, and the section does not cite that response [[clm:{rc}]] after its first "
                           f"citation of {cid}")
        for a in r.unanswered:
            oid = a["objection_id"]
            out.append(f"section {section} is one-sided: the objection {oid} to {_to(r, cid, oid)} was answered by "
                       f"{a['response_claim_id']}, which is withdrawn: {UNANSWERED}, and nothing the "
                       f"section could cite answers it for {cid}")
    return list(dict.fromkeys(out))


def _to(r: BalanceRequirements, cid: str, oid: str) -> str:
    """What an objection on ``cid`` is on, for a problem: ``it`` or ``its evidence <id>``."""
    target = r.targets[oid]
    return "it" if target == cid else f"its evidence {target}"
