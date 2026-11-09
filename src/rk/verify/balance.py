"""V-BALANCE (spec §9.1: "a claim marked ``contested``, or one with a sourced objection: its section states the
opposing view and cites the response"; Plan 5c Task 3). Per section, for every claim the section cites (a
``[[clm:]]`` marker) that argue's passed attempt left contested or objected to:

- **the counterpart** of each of its conflicts (``rk.production.argue.conflict_counterparts``: the other claim of a
  ``both_contested`` decision's conflict objection) is cited in the section after the claim's first citation (the
  same sentence counts). The counterpart of a conflict is contested by the claim in turn, so when the counterpart is
  said first the claim itself follows it: either order gives both sides. A contested claim with no conflict recorded
  fails (nothing tells which view opposes it);
- **the response claim** of each answered objection on it or on its evidence (``responded``, among the attempt's
  objections: ``argument_objection_ids``; a conflict objection included: the opposing view and the answer to it) is
  cited in the section after the claim's first citation.

A withdrawn counterpart or response is not asked for (citing it fails G-SUPPORT; argue's gate and its limitations
cover the objection it leaves). An objection with no response claim (conceded, rejected as invalid), sourced or not,
leaves nothing a sentence could cite: conceded ones are in the limitations input, a conceded priority-1 one fails its
claim's G-SUPPORT. Only the given section's sentences count: a counterpart in another section balances nothing here.

Nothing here calls a model."""

from __future__ import annotations

from rk.argument import ArgumentService
from rk.orchestrator.state import stage_row
from rk.production.argue import argument_objection_ids, conflict_counterparts
from rk.production.common import entity_row
from rk.production.manuscript import Sentence
from rk.store.project import ProjectStore


def _argued_attempt(ps: ProjectStore) -> int | None:
    row = stage_row(ps, "argue")
    return row["passed_attempt"] if row is not None and row["passed_attempt"] else None


def check_balance(ps: ProjectStore, section: str, sentences: list[Sentence]) -> list[str]:
    """V-BALANCE's problems for the section's sentences (see the module docstring), one per claim and missing
    counterpart or response, naming both; ``[]`` passes."""
    first: dict[str, int] = {}  # claim -> index of the first sentence citing it
    cited_at: dict[str, list[int]] = {}
    for s in sentences:
        for m in s.markers:
            if m.kind == "clm" and m.target:
                first.setdefault(m.target, s.index)
                cited_at.setdefault(m.target, []).append(s.index)
    if not first:
        return []
    svc = ArgumentService(ps)
    attempt = _argued_attempt(ps)
    counterpart = conflict_counterparts(ps, attempt) if attempt is not None else {}
    objections = [entity_row(ps, "objections", oid)
                  for oid in (argument_objection_ids(ps, attempt) if attempt is not None else [])]
    contested_by: dict[str, set[str]] = {}  # claim -> its conflicts' counterparts
    for o in objections:
        if o["id"] in counterpart:
            contested_by.setdefault(o["target_id"], set()).add(counterpart[o["id"]])

    def live(cid: str) -> bool:  # an unknown target is V-COVER's problem, a withdrawn one G-SUPPORT's
        return svc.exists(cid) and svc.get_claim(cid)["status"] != "withdrawn"

    def after(cid: str, other: str) -> bool:  # ``other`` cited in or after the first sentence citing ``cid``
        return any(i >= first[cid] for i in cited_at.get(other, []))

    out: list[str] = []
    for cid in first:
        if not live(cid):
            continue
        claim = svc.get_claim(cid)
        evidence = {e["id"] for e in svc.list_evidence(cid)}
        if claim.get("contested"):
            if cid not in contested_by:
                out.append(f"section {section} is one-sided: {cid} is contested, but argue recorded no conflict "
                           f"naming its counterpart, so the opposing view cannot be cited")
            for other in sorted(contested_by.get(cid, ())):
                mutual = cid in contested_by.get(other, set())
                if live(other) and not (after(cid, other) or (mutual and other in first)):
                    out.append(f"section {section} is one-sided: {cid} is contested by {other}, and the section "
                               f"does not cite that counterpart [[clm:{other}]] after its first citation of {cid}")
        for o in objections:
            rc = o.get("response_claim_id")
            if (o["target_id"] != cid and o["target_id"] not in evidence) or o.get("status") != "responded" \
                    or not rc or not live(rc) or after(cid, rc):
                continue
            on = "it" if o["target_id"] == cid else f"its evidence {o['target_id']}"
            out.append(f"section {section} is one-sided: the objection {o['id']} to {on} was answered by {rc}, and "
                       f"the section does not cite that response [[clm:{rc}]] after its first citation of {cid}")
    return list(dict.fromkeys(out))
