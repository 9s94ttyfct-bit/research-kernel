"""Plan 5c Task 1: every sentence of a draft section verified against spec §9.1 — V-COVER (a marker, targets that
exist with matching hashes), V-NUM (every number covered by a marker target), V-HEDGE (no stronger than the claims it
cites; ``[[inf]]`` worded tentatively), V-QUOTE (quoted text is a verified quote note), G-SUPPORT (cited claims are
supported) and the ``[[ck]]`` / ``[[plan:]]`` rules."""

import json

import pytest

from rk.argument import ArgumentService, ClaimIn, EvidenceIn, ObjectionIn
from rk.canon import canonical_json, sha256_hex
from rk.config import Settings, save_settings
from rk.discovery.store import hypotheses, log_search
from rk.fulltext import attach_pdf, attach_pdf_bytes
from rk.library.models import Creator, ItemIn
from rk.notes import NoteIn, add_note
from rk.production import store
from rk.production.artifacts import ArtifactIn, add_artifact, artifact
from rk.production.manuscript import parse_section
from rk.production.models import DesignIn
from rk.store.events import NewEvent
from rk.verify.cover import verify_section, verify_sentence
from rk.workspace import Kernel

from .browser_sites import tiny_pdf
from .conftest import DOCS
from .discovery_fakes import freeze_an_rq
from .production_fakes import a_protocol

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

    def claim(text: str, *, status: str = "supported", quote_text: str | None = None) -> str:
        """A claim on the quote and own notes; ``quote_text``: instead of the quote, a verified quote note of that
        text on another source (a PDF of it)."""
        q = quote
        if quote_text is not None:
            other = lib.add_item(ItemIn(title="Other", year=2022, creators=[Creator(family="Wu")]),
                                 added_by="human", actor="t").item_id
            attach_pdf_bytes(ps, lib, other, tiny_pdf(quote_text), origin="user_upload", actor="t",
                             actor_kind="human")
            q = add_note(ps, lib, NoteIn(source_id=other, kind="quote", text=quote_text, role_in_source="main"),
                         actor="t")
        cid = svc.add_claim(ClaimIn(text=text), generator_vendor="anthropic", actor="a")
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[q.note_id, own.note_id]), actor="a")
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
    assert _one(ps, f"Li may write \"{QUOTE}\" [[clm:{cid}]].").ok  # straight quotes
    # a sentence citing no claim (or protocol) is not checked: V-QUOTE is a rule of claim-citing sentences
    assert _one(ps, "Li may write \"about nickel catalysts\" [[inf]].").ok


def test_unsupported_or_withdrawn_claim_marker_fails(env):
    ps, claim = env
    uncertain = claim("Copper catalysts may reduce CO2.", status="uncertain")
    assert any(f"claim {uncertain}" in p and "status uncertain (not supported)" in p
               for p in _one(ps, f"Copper may reduce CO2 [[clm:{uncertain}]].").problems)
    gone = claim("Copper catalysts may reduce CO2.")
    ArgumentService(ps).withdraw_claim(gone, reason="test", actor="t")
    assert any(f"claim {gone} is withdrawn" in p for p in _one(ps, f"Copper may reduce CO2 [[clm:{gone}]].").problems)


# ---- fix round 1 ----
def _stt(ps, values: dict) -> str:
    return add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                       values=values, output_path=None), stage="synthesize", attempt=1, actor="k")


def test_stat_pool_is_the_artifacts_reported_values(env):
    """A pooled statistic covers its displayed values and k, never its log-scale study rows, its pooled internals or
    its bookkeeping (the review's probes: RR 0.5, a log ``yi`` 0.12, "2 studies" from ``extraction_attempt``)."""
    ps, _ = env
    sid = _stt(ps, {"measure": "rr", "options": {"level": 0.95}, "row_ids": ["ext_1"], "extraction_attempt": 2,
                    "studies": [{"item_id": "itm_1", "yi": 0.12, "vi": 0.52}], "uncomputable": [],
                    "pooled": {"estimate": -0.29, "ci_low": 0.49, "se": 0.07, "tau2": 0.31, "k": 3},
                    "display": {"estimate": 0.75, "ci_low": 0.61, "ci_high": 0.92, "k": 3, "i2_percent": 41.3,
                                "ci_level_percent": 95.0}})
    assert _one(ps, f"The pooled RR was 0.75 (95% CI 0.61 to 0.92; I² 41%) from 3 studies [[stat:{sid}]].").ok
    for bad in ("an RR of 0.5", "an RR of 0.12", "2 studies", "a tau² of 0.31"):
        v = _one(ps, f"It gave {bad} [[stat:{sid}]].")
        assert not v.ok and any("not in any marker's target" in p for p in v.problems), bad


def test_map_table_pool_is_its_counts(env):
    ps, _ = env
    sid = _stt(ps, {"fields": ["design"], "row_ids": ["ext_1"], "extraction_attempt": 4,
                    "table": {"n": 7, "fields": {"design": {"rct": 5, "cohort": 2}},
                              "cross": {"rows": "design", "cols": "setting", "counts": {"rct": {"icu": 3}}}}})
    assert _one(ps, f"Of 7 studies, 5 were trials, 2 cohorts and 3 trials ran in an ICU [[stat:{sid}]].").ok
    assert not _one(ps, f"There were 4 studies [[stat:{sid}]].").ok


def test_event_pool_is_a_whitelist_of_named_fields(env):
    """A ``search.logged`` event covers its years, dropped entries and number of hits; never its bookkeeping
    (``attempt``, ``limit``, ``offset``); an event type without a whitelist covers nothing."""
    ps, _ = env
    sid = log_search(ps, stage="search_screen", attempt=4, purpose="records", source="openalex", query="melatonin",
                     limit=50, offset=100, item_ids=["itm_1", "itm_2", "itm_3"], date_from=2000, date_to=2024,
                     actor="k")
    evt = ps.db.execute("SELECT evt_id FROM events WHERE entity_id=?", (sid,)).fetchone()[0]
    assert _one(ps, f"OpenAlex searched works from 2000 to 2024 and returned 3 hits [[run:{evt}]].").ok
    for bad in ("4", "50", "100"):
        v = _one(ps, f"The value was {bad} [[run:{evt}]].")
        assert not v.ok and any(f"the number {bad} is not" in p for p in v.problems), bad
    other = ps.apply(NewEvent(type="run.session", entity_id="run_x", actor_kind="kernel", actor="k",
                              payload={"n": 7, "at": 1.8e9})).evt_id
    assert not _one(ps, f"Session 7 ran [[run:{other}]].").ok


def test_protocol_and_design_pools(env):
    ps, _ = env
    pid = store.add_protocol(ps, 1, a_protocol(), version=1, actor="t")
    store.freeze_protocol(ps, pid, actor="t")
    assert _one(ps, f"We searched works published since 2000 [[run:{pid}]].").ok
    assert not _one(ps, f"We searched works published since 2001 [[run:{pid}]].").ok
    did = _frozen_design(ps)
    assert _one(ps, f"We will test 40 cells per arm [[plan:{did}]].", "research_proposal").ok
    assert not _one(ps, f"We will test 41 cells per arm [[plan:{did}]].", "research_proposal").ok


def _frozen_design(ps) -> str:
    (h,) = hypotheses(ps, freeze_an_rq(ps))
    did = store.set_design(ps, DesignIn.model_validate({
        "items": [{"id": "D1", "kind": "experiment", "sample": "40 cells per arm", "controls": ["bare carbon"],
                   "measurements": [{"id": "M1", "what": "Faradaic efficiency", "instrument": "GC", "unit": "%"}],
                   "analysis": "two-sided t test", "data_source": "new measurements", "hypothesis_ids": [h["id"]]}],
        "expected_results": "Copper beats nickel.", "alternative_explanations": ["surface area differs"]}),
        stage="design", attempt=1, actor="ideator")
    store.freeze_design(ps, did, actor="kernel")
    return did


def test_protocol_design_and_preregistration_hash_mismatches_fail(env):
    ps, _ = env
    pid = store.add_protocol(ps, 1, a_protocol(), version=1, actor="t")
    store.freeze_protocol(ps, pid, actor="t")
    ps.db.execute("UPDATE protocols SET body=? WHERE id=?", (json.dumps({**a_protocol().model_dump(mode="json"),
                                                                          "question": "Another?"}), pid))
    assert any("no longer matches its frozen hash" in p for p in _one(ps, f"We searched [[run:{pid}]].").problems)

    did = _frozen_design(ps)
    (h,) = hypotheses(ps, ps.db.execute("SELECT id FROM rqs WHERE status='frozen'").fetchone()[0])
    frozen = store.design(ps, did)["frozen_hash"]
    from rk.discovery.store import set_hypothesis_status
    set_hypothesis_status(ps, h["id"], "tested_by_design", prereg_hash=frozen, actor="kernel")
    assert _one(ps, f"We will test it [[plan:{did}]] [[plan:{h['id']}]].", "research_proposal").ok
    body = {**store.design(ps, did)["body"], "expected_results": "Nickel beats copper."}
    ps.db.execute("UPDATE designs SET body=? WHERE id=?", (json.dumps(body), did))
    assert sha256_hex(canonical_json(body)) != frozen
    problems = _one(ps, f"We will test it [[plan:{did}]] [[plan:{h['id']}]].", "research_proposal").problems
    assert any(f"design {did} no longer matches its frozen hash" in p for p in problems)
    assert any(f"hypothesis {h['id']}" in p and "pre-registered" in p for p in problems)
    ps.db.execute("UPDATE hypotheses SET prereg_hash='0000' WHERE id=?", (h["id"],))
    assert any("no frozen design has the hash 0000" in p
               for p in _one(ps, f"We will test it [[plan:{h['id']}]].", "research_proposal").problems)


def test_g_support_is_the_claim_gate(env):
    """A cited claim fails the sentence when the claim gate fails it, not only on its status: an open or a conceded
    priority-1 objection, V-HEDGE overstated, a V-STATS mismatch of a ``stat_result``."""
    ps, claim = env
    svc = ArgumentService(ps)
    objected = claim("Copper catalysts may reduce CO2.")
    o = svc.add_objection(ObjectionIn(target_id=objected, category="counterexample", text="Nickel too."),
                          actor="skeptic")
    assert any(f"open priority-1 objection {o}" in p
               for p in _one(ps, f"Copper catalysts may reduce CO2 [[clm:{objected}]].").problems)
    conceded = claim("Copper catalysts may reduce CO2.")
    c = svc.add_objection(ObjectionIn(target_id=conceded, category="counterexample", text="Nickel too."),
                          actor="skeptic")
    svc.respond_objection(c, response_claim_id=None, status="conceded", note="it holds", actor="arguer")
    assert any(f"conceded priority-1 objection {c}" in p
               for p in _one(ps, f"Copper catalysts may reduce CO2 [[clm:{conceded}]].").problems)
    overstated = claim("Copper catalysts prove CO2 reduction.")
    assert any("V-HEDGE overstated" in p
               for p in _one(ps, f"Copper catalysts prove CO2 reduction [[clm:{overstated}]].").problems)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version="v", input_hashes=[],
                                      values={"k": 3}, output_path=None), stage="synthesize", attempt=1, actor="k")
    stat = svc.add_claim(ClaimIn(text="Pooled RR 0.62 (3 studies).", kind="stat_result", artifact_ids=[sid],
                                 number_bindings=[{"artifact_id": sid, "key": "estimate", "text": "0.62"},
                                                  {"artifact_id": sid, "key": "k", "text": "3"}]),
                         generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")
    svc.set_claim_status(stat, "supported", verification_id="ver_X")
    assert any(f"V-STATS mismatch {sid}" in p for p in _one(ps, f"Pooled RR 0.62 (3 studies) [[clm:{stat}]].").problems)


def test_quotes_are_checked_in_sentences_citing_a_claim_or_the_protocol(env):
    ps, _ = env
    pid = store.add_protocol(ps, 1, a_protocol(), version=1, actor="t")
    store.freeze_protocol(ps, pid, actor="t")
    assert _one(ps, f"We searched “aspirin stroke elderly” [[run:{pid}]].").ok  # a search string of the protocol
    v = _one(ps, f"We searched “aspirin heart failure” [[run:{pid}]].")
    assert not v.ok and any("quoted text" in p for p in v.problems)


@pytest.mark.parametrize(("left", "right"), [("‘", "’"), ("'", "'"), ("“", '"'), ("„", "“"), ("«", "»")],
                         ids=["curly-single", "straight-single", "mixed", "german", "french"])
def test_single_mixed_and_other_quote_pairs_are_quotations(env, left, right):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, f"Li may write {left}{QUOTE}{right} [[clm:{cid}]].").ok
    v = _one(ps, f"Li may write {left}about nickel catalysts{right} [[clm:{cid}]].")
    assert not v.ok and any("quoted text" in p and "nickel" in p for p in v.problems)


def test_apostrophes_are_not_quotation_marks(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, f"The catalysts' yields may differ, and Li's data don't settle it [[clm:{cid}]].").ok
    assert _one(ps, f"The catalysts’ yields may differ, and Li’s data don’t settle it [[clm:{cid}]].").ok


def test_block_quote_content_is_quoted_text(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, f"> {QUOTE} [[clm:{cid}]]").ok
    v = _one(ps, f"> about nickel catalysts for CO2 reduction [[clm:{cid}]]")
    assert not v.ok and any("block quote" in p for p in v.problems)


def test_verified_quoted_words_do_not_count_for_v_hedge(env):
    ps, claim = env
    strong = "Encapsulation proves durable in humid air"
    cid = claim("Copper catalysts reduce CO2.", quote_text=strong)
    assert _one(ps, f"Wu writes that “{strong}” [[clm:{cid}]].").ok
    v = _one(ps, f"Wu writes that {strong.lower()} [[clm:{cid}]].")
    assert not v.ok and any("stronger than the claims it cites" in p for p in v.problems)


def test_code_spans_are_masked_from_every_prose_check(env):
    ps, claim = env
    cid = claim("Copper catalysts may reduce CO2.")
    assert _one(ps, "The constant `0.99` may matter [[inf]].").ok  # a number in code needs no coverage
    assert _one(ps, f"Li may write `\"about nickel catalysts\"` [[clm:{cid}]].").ok  # nor is a quote in code one
    assert _one(ps, "The flag `--proves` may matter [[inf]].").ok
    (s,) = parse_section("The constant `0.99` may matter [[inf]].")
    assert "`0.99`" in s.text  # the rendered text keeps it


def test_a_table_or_a_caption_fails_the_section(env):
    ps, _ = env
    body = " ".join(f"Point {chr(65 + i)} may hold [[inf]]." for i in range(3))
    v = verify_section(ps, "results", body + "\n\n| a | b |\n|---|---|\n| 0.99 | melatonin halves delirium |\n",
                       paper_type="systematic_review")
    assert not v.ok and all(s.ok for s in v.sentences) and any("table" in p for p in v.problems)
    w = verify_section(ps, "results", body + "\n\nTable: Results 42.\n", paper_type="systematic_review")
    assert not w.ok and len(w.sentences) == 3 and any("caption" in p for p in w.problems)
