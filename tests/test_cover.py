"""Plan 5c Task 1: every sentence of a draft section verified against spec §9.1 — V-COVER (a marker, targets that
exist with matching hashes), V-NUM (every number covered by a marker target), V-HEDGE (no stronger than the claims it
cites; ``[[inf]]`` worded tentatively), V-QUOTE (quoted text is a verified quote note), G-SUPPORT (cited claims are
supported) and the ``[[ck]]`` / ``[[plan:]]`` rules."""

import pytest

from rk.argument import ArgumentService, ClaimIn, EvidenceIn
from rk.config import Settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.notes import NoteIn, add_note
from rk.production.artifacts import ArtifactIn, add_artifact, artifact
from rk.production.manuscript import parse_section
from rk.verify.cover import verify_section, verify_sentence
from rk.workspace import Kernel

from .conftest import DOCS

QUOTE = "about copper catalysts for CO2 reduction"


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    ps = k.create_project("cov", "Cov", actor="t")
    _, lib = k.project("cov")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    quote = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=QUOTE, role_in_source="main"), actor="t")
    own = add_note(ps, lib, NoteIn(source_id=iid, kind="own", text="The yield was 0.62 at 25 °C in 12 runs."),
                   actor="t")
    svc = ArgumentService(ps)

    def claim(text: str, *, status: str = "supported") -> str:
        cid = svc.add_claim(ClaimIn(text=text), generator_vendor="anthropic", actor="a")
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[quote.note_id, own.note_id]), actor="a")
        if status != "draft":
            svc.set_claim_status(cid, status, verification_id="ver_X")
        return cid

    yield ps, claim
    k.close()


def _one(ps, text: str, paper_type: str = "systematic_review"):
    (s,) = parse_section(text)
    return verify_sentence(ps, s, paper_type=paper_type)


def test_sentence_without_marker_fails(env):
    ps, _ = env
    v = _one(ps, "Copper catalysts were studied.")
    assert not v.ok and v.problems == ["the sentence has no source marker"]


def test_unknown_or_hash_mismatched_target_fails(env):
    ps, _ = env
    assert any("unknown claim clm_nope" in p for p in _one(ps, "It works [[clm:clm_nope]].").problems)
    assert any("no event evt_nope" in p for p in _one(ps, "We searched [[run:evt_nope]].").problems)
    assert any("no statistic stt_nope" in p for p in _one(ps, "Pooled [[stat:stt_nope]].").problems)
    assert any("needs a target" in p for p in _one(ps, "It works [[clm]].").problems)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values={"display": {"estimate": 0.6213}}, output_path=None),
                       stage="synthesize", attempt=1, actor="k")
    assert _one(ps, f"The estimate was 0.62 [[stat:{sid}]].").ok
    (ps.root / artifact(ps, sid)["path"]).write_text("{}")
    assert any("no longer has its recorded sha256" in p for p in _one(ps, f"It was 0.62 [[stat:{sid}]].").problems)
    assert any("is a statistic, not a figure" in p for p in _one(ps, f"See [[fig:{sid}]].").problems)


def test_number_must_be_covered_by_a_marker_target(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, f"The yield may be 0.62 [[clm:{cid}]].").ok  # in the claim's evidence
    v = _one(ps, f"The yield may be 0.6 [[clm:{cid}]].")
    assert not v.ok and any("0.6" in p and "not in any marker's target" in p for p in v.problems)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values={"display": {"estimate": 0.6213, "k": 4}}, output_path=None),
                       stage="synthesize", attempt=1, actor="k")
    assert _one(ps, f"The pooled estimate was 0.62 from 4 studies [[stat:{sid}]].").ok  # rounded as written
    assert not _one(ps, f"The pooled estimate was 0.7 [[stat:{sid}]].").ok
    assert not _one(ps, "Twelve runs gave 12 results [[inf]].").ok  # an inference covers no number


def test_ck_share_over_five_percent_fails_section(env):
    ps, _ = env
    body = " ".join(f"Point {chr(65 + i)} may hold [[inf]]." for i in range(19))
    ok = verify_section(ps, "discussion", body + " Water is wet [[ck]].", paper_type="systematic_review")
    assert ok.ok and ok.ck_share == pytest.approx(1 / 20)
    bad = verify_section(ps, "discussion", body + " Water is wet [[ck]]. Ice is cold [[ck]].",
                         paper_type="systematic_review")
    assert not bad.ok and bad.ck_share == pytest.approx(2 / 21)
    assert all(s.ok for s in bad.sentences)  # the share fails the section, not a sentence


def test_ck_sentence_with_number_fails(env):
    ps, _ = env
    v = _one(ps, "Water boils at 100 °C [[ck]].")
    assert not v.ok and any("common knowledge" in p and "number" in p for p in v.problems)


def test_plan_marker_outside_proposal_fails(env):
    ps, _ = env
    v = _one(ps, "We will measure it [[plan:hyp_X]].")
    assert any("only in a research proposal" in p for p in v.problems)
    w = _one(ps, "We will measure it [[plan:hyp_X]].", paper_type="research_proposal")
    assert not any("only in a research proposal" in p for p in w.problems)
    assert any("no hypothesis hyp_X" in p for p in w.problems)


def test_inf_sentence_must_be_hedged(env):
    ps, _ = env
    assert not _one(ps, "This proves copper works [[inf]].").ok
    assert _one(ps, "This may suggest copper works [[inf]].").ok


def test_sentence_stronger_than_its_claims_fails(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    v = _one(ps, f"Copper catalysts reduce CO2 [[clm:{cid}]].")
    assert not v.ok and any("stronger than the claims it cites" in p for p in v.problems)
    assert _one(ps, f"Copper catalysts may reduce CO2 [[clm:{cid}]].").ok


def test_quoted_text_must_be_a_verified_quote_note(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, f"Li may write “{QUOTE}” [[clm:{cid}]].").ok
    v = _one(ps, f"Li may write “about nickel catalysts” [[clm:{cid}]].")
    assert not v.ok and any("quoted text" in p and "verified quote note" in p for p in v.problems)
    w = _one(ps, f"Li may write \"{QUOTE}\" [[inf]].")
    assert not w.ok and any("quoted text" in p for p in w.problems)  # a quote needs the claim whose note it is


def test_unsupported_or_withdrawn_claim_marker_fails(env):
    ps, claim = env
    uncertain = claim("Copper catalysts may reduce CO2.", status="uncertain")
    assert any(f"claim {uncertain} is uncertain, not supported" in p
               for p in _one(ps, f"Copper may reduce CO2 [[clm:{uncertain}]].").problems)
    gone = claim("Copper catalysts may reduce CO2.")
    ArgumentService(ps).withdraw_claim(gone, reason="test", actor="t")
    assert any(f"claim {gone} is withdrawn" in p for p in _one(ps, f"Copper may reduce CO2 [[clm:{gone}]].").problems)
