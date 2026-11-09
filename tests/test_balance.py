"""Plan 5c Task 3: V-BALANCE (spec §9.1). A section citing a contested claim, or a claim with a sourced objection,
cites the counterpart of its conflict and the objection's response claim after its first citation, in that section."""

from __future__ import annotations

import pytest

from rk.argument import ArgumentService, ObjectionIn
from rk.production.argue import _note as argue_note
from rk.production.argue import argument_claims, main_claim
from rk.production.manuscript import parse_section
from rk.production.synthesis import synthesis_claims
from rk.verify.balance import balance_requirements, check_balance
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
    main, finding, _ = _claims(env)
    assert _check(ps, main, finding) == []
    assert _check(ps) == []
    # objections with nothing to cite: rejected or conceded (no response claim), unsourced
    object_to(ps, finding, status="rejected_invalid")
    object_to(ps, finding, status="conceded")
    assert _check(ps, main, finding) == []


def test_conceded_sourced_objection_passes_balance_and_is_listed(env):
    """The accepted gap (controller ruling): no marker cites an objection or a source, so a conceded sourced objection
    asks a sentence for nothing; it is listed for the Writer (to qualify the claim) and the audit's disclosure."""
    _, ps, _ = env
    main, finding, _ = _claims(env)
    oid = object_to(ps, finding, status="conceded", source_id="itm_source")
    evd = ArgumentService(ps).list_evidence(main)[0]["id"]
    on_evidence = object_to(ps, evd, status="conceded", source_id="itm_other")
    object_to(ps, main, status="conceded")  # unsourced: not listed
    assert _check(ps, main, finding) == []
    req = balance_requirements(ps, finding)
    assert req.conceded_sourced == [{"objection_id": oid, "text": "another trial found otherwise",
                                     "source_id": "itm_source"}]
    assert [c["objection_id"] for c in balance_requirements(ps, main).conceded_sourced] == [on_evidence]


def test_requirements_name_counterparts_and_responses(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    assert balance_requirements(ps, main).contested_by == [] and balance_requirements(ps, main).answered_by == []
    cid = contest(ps, main, finding, reason)
    oid = object_to(ps, finding, response=main)
    req = balance_requirements(ps, main)
    assert req.contested and req.contested_by == [finding]
    assert req.answered_by == [{"objection_id": cid, "response_claim_id": reason}]
    assert balance_requirements(ps, finding).answered_by == [{"objection_id": oid, "response_claim_id": main}]
    assert balance_requirements(ps, finding).contested_by == []


def test_withdrawn_response_leaves_its_objection_unanswered(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    oid = object_to(ps, main, response=reason)
    ArgumentService(ps).withdraw_claim(reason, reason="test", actor="tester")
    (problem,) = _check(ps, main, finding)
    assert "one-sided" in problem and oid in problem and reason in problem and "withdrawn" in problem
    assert "unanswered" in problem and main in problem
    assert balance_requirements(ps, main).answered_by == []  # nothing the Writer could cite


def test_withdrawn_counterpart_is_not_asked_for(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    contest(ps, main, finding, reason)
    ArgumentService(ps).withdraw_claim(finding, reason="test", actor="tester")
    assert _check(ps, main, reason) == []
    assert balance_requirements(ps, main).contested_by == []


def test_without_a_passed_argue_attempt_balance_fails(env):
    _, ps, _ = env
    assert check_balance(ps, "introduction", parse_section("A finding [[clm:clm_x]].\n")) == [
        "section introduction: V-BALANCE cannot be checked: argue has not passed"]
    with pytest.raises(ValueError, match="argue has not passed"):
        balance_requirements(ps, "clm_x")


def test_order_of_a_one_way_counterpart_and_same_sentence_citations(env):
    _, ps, _ = env
    main, finding, reason = _claims(env)
    contest(ps, main, finding, reason)  # only the main claim is contested: the finding is not contested by it
    (before,) = _check(ps, finding, main, reason)
    assert "one-sided" in before and main in before and finding in before and "counterpart" in before

    def one(md: str) -> list[str]:
        return check_balance(ps, "introduction", parse_section(md + "\n"))

    # the counterpart and the response in the claim's own sentence count as after its first citation
    assert one(f"Trials disagree [[clm:{main}]] [[clm:{finding}]] [[clm:{reason}]].") == []
    assert one(f"The finding holds [[clm:{main}]] [[clm:{reason}]]. Another trial disagrees [[clm:{finding}]].") == []
