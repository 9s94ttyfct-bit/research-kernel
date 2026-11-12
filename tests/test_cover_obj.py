"""The ``[[obj:obj_…]]`` marker: a sentence cites one objection of argue's passed attempt, i.e. what its source (an
outside work, not an included study) says. Calibration (critical3 final review): sentences about the benchmark a
Skeptic objection came from had to cite a claim that never mentions it, and were exported as citations of included
studies."""

import pytest

from rk.argument import ArgumentService, ObjectionIn
from rk.library.models import Creator, ItemIn
from rk.library.service import Library
from rk.production.argue import _note as argue_note
from rk.production.manuscript import parse_section
from rk.production.synthesis import synthesis_claims
from rk.verify.cover import target_state, verify_sentence
from tests.production_fakes import make_frozen_project
from tests.test_design import _argued

TEXT = "SESR-Eval shows lower accuracy in 412 reviews."


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="critical_review")
    _argued((k, ps, lib))
    item = lib.add_item(ItemIn(title="SESR-Eval", year=2024, creators=[Creator(family="Ng")]), added_by="agent",
                        actor="tester").item_id
    yield ps, synthesis_claims(ps, 1)[0], item
    k.close()


def objection(ps, target: str, text: str, source_id: str | None, *, status: str | None = "conceded",
              in_argument: bool = True) -> str:
    """A Skeptic objection on ``target`` from ``source_id``, answered ``status`` (``None``: left open), one of argue's
    attempt 1 when ``in_argument``."""
    svc = ArgumentService(ps)
    oid = svc.add_objection(ObjectionIn(target_id=target, category="counterexample", priority=2, source_id=source_id,
                                        text=text), actor="tester", actor_kind="kernel")
    if status is not None:
        svc.respond_objection(oid, response_claim_id=None, status=status, note="it holds", actor="tester")
    if in_argument:
        argue_note(ps, 1, "critique", actor="tester", round=1, objection_ids=[oid])
    return oid


def _one(ps, text: str, **kw):
    (s,) = parse_section(text)
    return verify_sentence(ps, s, paper_type="critical_review", **kw)


def _fails(ps, text: str, wanted: str) -> bool:
    return any(wanted in p for p in _one(ps, text).problems)


def test_obj_marker_cites_a_sourced_objection_of_argues_passed_attempt(env):
    ps, finding, item = env
    oid = objection(ps, finding, TEXT, item)
    sentence = f"SESR-Eval shows lower accuracy in 412 reviews [[obj:{oid}]]."
    assert _one(ps, sentence).ok, _one(ps, sentence).problems
    # a statement source like a claim: it stands outside the sections that describe the research operations too
    assert _one(ps, sentence, operations=False).ok
    unsourced = objection(ps, finding, TEXT, None)
    assert _fails(ps, f"SESR-Eval shows lower accuracy [[obj:{unsourced}]].", f"objection {unsourced} has no source")
    gone = objection(ps, finding, TEXT, "itm_nowhere")
    assert _fails(ps, f"SESR-Eval shows lower accuracy [[obj:{gone}]].", "itm_nowhere, which is not in the library")
    outside = objection(ps, finding, TEXT, item, in_argument=False)
    assert _fails(ps, f"SESR-Eval shows lower accuracy [[obj:{outside}]].",
                  f"objection {outside} is not one of the objections of argue's passed attempt")
    assert _fails(ps, "SESR-Eval shows lower accuracy [[obj:obj_nope]].", "no objection obj_nope")
    assert _fails(ps, "SESR-Eval shows lower accuracy [[obj]].", "[[obj]] needs a target")
    # the bare id is told the obj form
    assert _fails(ps, f"SESR-Eval shows lower accuracy [[{oid}]].", f"write [[obj:{oid}]]")


def test_obj_marker_pools_the_objections_numbers_and_caps_the_wording(env):
    ps, finding, item = env
    oid = objection(ps, finding, TEXT, item)
    assert _fails(ps, f"SESR-Eval shows lower accuracy in 413 reviews [[obj:{oid}]].",
                  "the number 413 is not in any marker's target")
    tentative = objection(ps, finding, "SESR-Eval may show lower accuracy in 412 reviews.", item)
    assert _fails(ps, f"SESR-Eval shows lower accuracy in 412 reviews [[obj:{tentative}]].", "(V-HEDGE)")
    assert _one(ps, f"SESR-Eval may show lower accuracy in 412 reviews [[obj:{tentative}]].").ok
    # with a strong objection beside it, the tentative one does not lower the ceiling: the strongest text counts
    assert _one(ps, f"SESR-Eval shows lower accuracy in 412 reviews [[obj:{tentative}]] [[obj:{oid}]].").ok


def test_an_objection_whose_source_is_retracted_cannot_be_cited(env):
    """A retracted or withdrawn work is never cited (V-RETRACT): the sentence fails, so the audit's G-REF failure on
    such a source goes back to draft, which can drop the citation."""
    ps, finding, item = env
    oid = objection(ps, finding, TEXT, item)
    Library(ps).update(item, retraction="retracted", actor="tester")
    assert _fails(ps, f"SESR-Eval shows lower accuracy in 412 reviews [[obj:{oid}]].",
                  f"objection {oid} names the source {item}, which is retracted")


def test_an_objections_text_is_never_quoted(env):
    """The objection's text is the Skeptic's wording, never checked against its source: quoting it would present that
    wording as the source's own words, so it may only be paraphrased."""
    ps, finding, item = env
    oid = objection(ps, finding, 'SESR-Eval reports "no gain in recall" across its reviews.', item)
    assert _one(ps, f"SESR-Eval reports no gain in recall [[obj:{oid}]].").ok
    assert _fails(ps, f"SESR-Eval reports “no gain in recall” [[obj:{oid}]].", "is not a verified quote note")


def test_obj_target_state_changes_with_the_objection(env):
    """A kept verdict (revise) is reused only while each marker's target state is unchanged: an objection answered
    since changes it."""
    ps, finding, item = env
    oid = objection(ps, finding, TEXT, item, status=None)
    (m,) = parse_section(f"SESR-Eval shows lower accuracy [[obj:{oid}]].")[0].markers
    before = target_state(ps, m, paper_type="critical_review")
    ArgumentService(ps).respond_objection(oid, response_claim_id=None, status="conceded", note="it holds",
                                          actor="tester")
    assert target_state(ps, m, paper_type="critical_review") != before
