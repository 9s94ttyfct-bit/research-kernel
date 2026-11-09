"""Plan 5c Task 3: V-BALANCE (spec §9.1). A section citing a contested claim, or a claim with a sourced objection,
cites the counterpart of its conflict and the objection's response claim after its first citation, in that section."""

from __future__ import annotations

import pytest

from rk.argument import ArgumentService, ObjectionIn
from rk.production.argue import _note as argue_note
from rk.production.argue import argument_claims, main_claim
from rk.production.manuscript import parse_section
from rk.production.synthesis import synthesis_claims
from rk.verify.balance import check_balance
from tests.production_fakes import make_frozen_project
from tests.test_design import _argued


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="critical_review")
    yield k, ps, lib
    k.close()


def _claims(env) -> tuple[str, str, str]:
    """argue's passed attempt 1: its main claim, a synthesis finding and the reason (another argument claim)."""
    _, ps, _ = env
    _argued(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]
    finding = synthesis_claims(ps, 1)[0]
    return main, finding, reason


def contest(ps, claim: str, counterpart: str, response: str) -> str:
    """As argue's ``both_contested`` decision: ``claim`` marked contested, with a conflict objection naming
    ``counterpart``, answered by ``response``."""
    svc = ArgumentService(ps)
    svc.set_contested(claim, reason=f"contradicted by {counterpart}", actor="tester")
    oid = svc.add_objection(ObjectionIn(target_id=claim, category="counterexample", priority=2,
                                        text=f"{counterpart} contradicts it"), actor="tester", actor_kind="kernel")
    svc.respond_objection(oid, response_claim_id=response, status="responded", note="reconciled", actor="tester")
    argue_note(ps, 1, "conflict_decision", actor="tester", round=1, index=0, decision="both_contested", by="vote",
               objection_ids=[oid], conflict_objections={oid: counterpart})
    return oid


def object_to(ps, target: str, *, response: str | None = None, source_id: str | None = None,
              status: str = "responded") -> str:
    """A Skeptic objection of argue's attempt 1 on ``target``, answered as given."""
    svc = ArgumentService(ps)
    oid = svc.add_objection(ObjectionIn(target_id=target, category="counterexample", priority=2, source_id=source_id,
                                        text="another trial found otherwise"), actor="tester", actor_kind="kernel")
    svc.respond_objection(oid, response_claim_id=response if status == "responded" else None, status=status,
                          note="answered", actor="tester")
    argue_note(ps, 1, "critique", actor="tester", round=1, objection_ids=[oid])
    return oid


def _check(ps, *cited: str) -> list[str]:
    md = " ".join(f"Sentence {i} [[clm:{c}]]." for i, c in enumerate(cited)) + "\n"
    return check_balance(ps, "introduction", parse_section(md))


def test_contested_claim_without_counterpart_is_one_sided(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    contest(ps, main, finding, reason)
    (problem,) = _check(ps, main, reason)
    assert "one-sided" in problem and main in problem and finding in problem and "counterpart" in problem
    assert _check(ps, main, finding, reason) == []
    # the counterpart said before the contested claim: both sides are still given (the counterpart, contested by it in
    # turn, is said first)
    contest(ps, finding, main, reason)
    assert _check(ps, finding, main, reason) == []
    assert _check(ps, main, finding, reason) == []


def test_answered_objection_needs_its_response_cited(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    object_to(ps, finding, response=reason)
    (problem,) = _check(ps, finding)
    assert "one-sided" in problem and finding in problem and reason in problem and "response" in problem
    # the response must come after the first citation of the claim
    (late,) = _check(ps, reason, finding)
    assert finding in late and reason in late
    assert _check(ps, finding, reason) == []
    assert _check(ps, reason, finding, reason) == []
    # an objection on the claim's evidence counts as one on the claim
    evd = ArgumentService(ps).list_evidence(main)[0]["id"]
    object_to(ps, evd, response=reason)
    (on_evidence,) = _check(ps, main)
    assert main in on_evidence and reason in on_evidence
    # a contested claim needs the response to its conflict objection too (and the finding still lacks its response)
    contest(ps, main, finding, reason)
    problems = _check(ps, main, finding)
    assert len(problems) == 3 and sum(f"first citation of {main}" in p and reason in p for p in problems) == 2, problems


def test_counterpart_in_another_section_does_not_count(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    contest(ps, main, finding, reason)
    intro = parse_section(f"The main finding holds [[clm:{main}]]. It is reconciled [[clm:{reason}]].\n")
    other = parse_section(f"Another trial disagrees [[clm:{finding}]].\n")
    (problem,) = check_balance(ps, "introduction", intro)
    assert "introduction" in problem and main in problem and finding in problem
    assert check_balance(ps, "theme-1", other) == []  # the finding is not contested itself


def test_uncontested_claims_pass(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    assert _check(ps, main, finding) == []
    assert _check(ps) == []
    # objections with nothing to cite: conceded or rejected (no response claim), unsourced or sourced
    object_to(ps, finding, status="rejected_invalid")
    object_to(ps, finding, status="conceded", source_id="itm_source")
    assert _check(ps, main, finding) == []
    # a withdrawn response claim cannot be cited (G-SUPPORT): it asks for nothing
    object_to(ps, main, response=reason)
    ArgumentService(ps).withdraw_claim(reason, reason="test", actor="tester")
    assert _check(ps, main, finding) == []
