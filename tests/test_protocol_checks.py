"""The protocol's in-attempt checks (P1 protocol diagnosis 2026-11-12, rules R1b/c, R2, R5-R8): what the judges kept
raising that a rule finds in the body, so a try fails in the attempt with a message naming the fix instead of spending
a judge round, and P1 never votes on a protocol whose probe shows it above the record cap (its lower bound; P1 protocol
rerun 2, 2026-11-12)."""

from __future__ import annotations

import pytest

from rk.checkpoints import get_checkpoint
from rk.config import save_settings
from rk.discovery.store import intake_of
from rk.production import store
from rk.production.models import (
    ExtractionField,
    OutcomeSpec,
    SearchStrategy,
    SynthesisPlan,
)
from rk.production.protocol import (
    KERNEL_PRISMA_P,
    PRISMA_P_TEXT,
    ProtocolWorker,
    check_protocol,
    check_strategy,
    dropped_prisma_p,
    render_protocol,
    with_kernel_facts,
)
from tests.discovery_fakes import FakeSources, ScriptedExecutor
from tests.production_fakes import (
    a_protocol,
    make_frozen_project,
    production_ctx,
    votes_on,
)

SR_ITEMS = {"rationale": "Guidelines stop at 80.", "objectives": "Estimate benefit and harm.",
            "selection_process": "Two screeners, title/abstract then full text.",
            "data_collection_process": "One reader extracts, a second checks.",
            "risk_of_bias": "RoB 2 for trials, ROBINS-I otherwise.",
            "meta_bias": "Funnel plots when ten or more studies report an outcome.",
            "certainty_of_evidence": "GRADE for each outcome.",
            "data_management": "Records and extracted values are kept with their quotes.",
            "outcomes_prioritisation": "Stroke first, then major bleeding."}
STUDY_ID = ExtractionField(name="study_id", description="First author and year", type="text", source="record")
NEW_ITEMS = ("meta_bias", "certainty_of_evidence", "data_management", "outcomes_prioritisation")
AVAILABLE = ["openalex", "europepmc", "pubmed", "arxiv"]
QUERIES = ["aspirin stroke elderly", "aspirin primary prevention octogenarians", "antiplatelet stroke over 80"]
BINARY = ("events_treatment", "n_treatment", "events_control", "n_control")


def sr_body(**overrides):
    """A systematic review's protocol that passes every check."""
    return a_protocol(prisma_p=dict(SR_ITEMS), extraction_schema=[STUDY_ID, *a_protocol().extraction_schema],
                      synthesis_plan=SynthesisPlan(method="narrative_swim", outcomes=[
                          OutcomeSpec(name="stroke"), OutcomeSpec(name="major bleeding")])).model_copy(update=overrides)


def sr(body) -> list[str]:
    return check_protocol(body, "systematic_review")


def with_items(**items) -> dict[str, str]:
    return {**SR_ITEMS, **items}


def test_a_sound_systematic_review_passes_with_or_without_the_kernel_facts():
    assert set(NEW_ITEMS) <= set(PRISMA_P_TEXT)
    assert sr(sr_body()) == []
    assert sr(with_kernel_facts(sr_body(), "systematic_review")) == []  # kernel text is not the Ideator's


# ---- R1c: placeholders ----
def test_placeholder_text_fails():
    for placeholder in ("to be completed by the researcher", "[TO BE ADDED]",
                        "will be registered before data extraction"):
        (failure,) = sr(sr_body(prisma_p=with_items(data_management=f"Records are kept; {placeholder}.")))
        assert "prisma_p.data_management" in failure, failure
        body = sr_body()
        body.eligibility.include[0].text = f"People over 80 ({placeholder})"
        (failure,) = sr(body)
        assert "eligibility.include.I1" in failure, failure
    assert sr(sr_body(prisma_p=with_items(selection_process="Studies will be screened by two screeners."))) == []
    # the kernel's own text (registration, the language exclusion) is not checked as the Ideator's
    forged = with_kernel_facts(sr_body(prisma_p=with_items(registration="to be completed by the researcher")),
                               "systematic_review")
    assert forged.prisma_p["registration"] == KERNEL_PRISMA_P["registration"] and sr(forged) == []


def test_reported_or_recorded_is_a_placeholder_only_as_a_deferral():
    for ordinary in ("Outcomes to be reported: stroke, then bleeding.", "Events to be recorded per arm."):
        assert sr(sr_body(prisma_p=with_items(outcomes_prioritisation=ordinary))) == [], ordinary
    for deferral in ("Funding sources to be reported later.", "To be recorded.",
                     "Deviations to be recorded at a later stage."):
        (failure,) = sr(sr_body(prisma_p=with_items(data_management=deferral)))
        assert "prisma_p.data_management" in failure and "placeholder" in failure, deferral


# ---- R1b: the extended PRISMA-P items ----
def test_systematic_review_needs_the_extended_prisma_p_items():
    for key in NEW_ITEMS:
        body = sr_body(prisma_p={k: v for k, v in SR_ITEMS.items() if k != key})
        assert sr(body) == [f"PRISMA-P item {key!r} is missing"]
        assert check_protocol(body, "scoping_review") == []
        assert check_protocol(body, "critical_review") == []


# ---- R5: PRISMA-P text agrees with the strategy ----
def test_prisma_p_sources_agree_with_the_strategy():
    sources = sr_body().search_strategy.sources
    assert sources == ["openalex", "europepmc"]
    (failure,) = sr(sr_body(prisma_p=with_items(information_sources="We search OpenAlex, Europe PMC and arXiv.")))
    assert "prisma_p.information_sources" in failure and "arXiv" in failure
    assert sr(sr_body(prisma_p=with_items(information_sources="We search OpenAlex and Europe PMC."))) == []
    assert sr(sr_body(prisma_p=with_items(information_sources="We search OpenAlex and Europe PMC; PubMed is not "
                                                              "searched."))) == []
    for promise in ("We also search the grey literature.", "Two reviewers hand-search key journals.",
                    "We screen the reference lists of included studies.", "We search ClinicalTrials.gov.",
                    "We will try contacting authors for missing data."):
        (failure,) = sr(sr_body(prisma_p=with_items(information_sources=f"We search OpenAlex. {promise}")))
        assert "prisma_p.information_sources" in failure, promise
    assert sr(sr_body(prisma_p=with_items(information_sources="We search OpenAlex. Grey literature is not "
                                                              "searched."))) == []


# ---- R6: outcomes and diagnostic accuracy ----
def test_systematic_review_lists_outcomes_for_every_plan():
    no_outcomes = sr_body(synthesis_plan=SynthesisPlan(method="narrative_swim"))
    (failure,) = sr(no_outcomes)
    assert failure.startswith("synthesis_plan.outcomes")
    assert check_protocol(no_outcomes, "scoping_review") == []
    twice = SynthesisPlan(method="thematic", outcomes=[OutcomeSpec(name="stroke"), OutcomeSpec(name=" Stroke")])
    assert "synthesis_plan.outcomes: outcome 'Stroke' is given twice" in sr(sr_body(synthesis_plan=twice))
    assert "synthesis_plan.outcomes: outcome 'Stroke' is given twice" in check_protocol(
        a_protocol(synthesis_plan=twice), "critical_review")


def _meta(*names: str) -> SynthesisPlan:
    return SynthesisPlan(method="meta_analysis", effect_measure="RR", pool_model="DL", outcomes=[
        OutcomeSpec(name=n, fields={r: r for r in BINARY}) for n in names])


def test_diagnostic_accuracy_meta_analysis_fails_with_the_reason():
    schema = [STUDY_ID, *(ExtractionField(name=r, description=r, type="number") for r in BINARY)]
    assert sr(sr_body(extraction_schema=schema, synthesis_plan=_meta("stroke"))) == []
    assert sr(sr_body(extraction_schema=schema, synthesis_plan=_meta("insulin sensitivity"))) == []
    dta_question = "What is the diagnostic accuracy of MRI for prostate cancer?"
    for body in (sr_body(extraction_schema=schema, synthesis_plan=_meta("sensitivity")),
                 sr_body(extraction_schema=schema, synthesis_plan=_meta("AUC")),
                 sr_body(extraction_schema=schema, synthesis_plan=_meta("specificity")),
                 sr_body(extraction_schema=schema, synthesis_plan=_meta("cancer detected"), question=dta_question)):
        (failure,) = sr(body)
        assert failure.startswith("synthesis_plan") and "rk.stats" in failure and "diagnostic" in failure
        assert "narrative" in failure and "limitation" in failure
    assert sr(sr_body(question=dta_question)) == []  # a narrative synthesis of a diagnostic question passes


# ---- R7: query syntax and the study identifier ----
def test_query_syntax():
    def failures(query: str) -> list[str]:
        return check_strategy(SearchStrategy(sources=["openalex", "europepmc"], queries=[query, *QUERIES[1:]]),
                              AVAILABLE)

    assert failures("(aspirin OR acetylsalicylic) AND stroke") == []
    assert failures('"aspirin AND stroke" OR bleeding') == []  # inside quotes: no operators
    assert failures("aspirin and stroke or bleeding") == []  # lowercase: words, not operators
    for unbalanced in ("(aspirin AND stroke", "aspirin) AND (stroke"):
        (failure,) = failures(unbalanced)
        assert repr(unbalanced) in failure and "parenthes" in failure
    (failure,) = failures("aspirin AND stroke OR bleeding")
    assert "'aspirin AND stroke OR bleeding'" in failure and "AND" in failure and "OR" in failure
    assert failures("(aspirin AND stroke) OR (aspirin AND bleeding)") == []


def test_systematic_review_extraction_has_a_study_id():
    no_id = sr_body(extraction_schema=a_protocol().extraction_schema)
    (failure,) = sr(no_id)
    assert failure.startswith("extraction_schema") and "study" in failure
    assert check_protocol(no_id, "scoping_review") == [] and check_protocol(no_id, "critical_review") == []
    for name in ("study_id", "StudyID", "first_author", "citation", "reference"):
        field = ExtractionField(name=name, description="Which study", type="text")
        assert sr(sr_body(extraction_schema=[field, *a_protocol().extraction_schema])) == [], name


# ---- R8: no silent removal ----
@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _draft(**prisma) -> dict:
    return {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}], "exclude": []},
            "extraction_schema": [{"name": "study_id", "description": "First author and year", "type": "text",
                                   "source": "record"},
                                  {"name": "n", "description": "Participants", "type": "number"}],
            "synthesis_plan": {"method": "narrative_swim", "outcomes": [{"name": "stroke"}]},
            "prisma_p": prisma}


def _strategy(queries=QUERIES, sources=("openalex", "europepmc")) -> dict:
    return {"queries": list(queries), "sources": list(sources), "date_from": 2000, "date_to": None}


def _votes(*choices: str) -> dict:
    return {("judge", "ProtocolVoteOut"): [{"choice": c, "reason": f"judge: {c}"} for c in choices],
            ("judge_2", "ProtocolVoteOut"): [{"choice": c, "reason": f"judge_2: {c}"} for c in choices]}


def _ctx(env, ex, *, attempt: int = 1, sources=None):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="protocol", attempt=attempt, sources=sources or FakeSources({}))


def test_redraft_may_not_drop_prisma_p_items():
    without = a_protocol(prisma_p={"funding": "x"})
    (failure,) = dropped_prisma_p({"prisma_p": {"funding": "x", "rationale": "y", "registration": "z"}}, without)
    assert "rationale" in failure and "registration" not in failure  # a kernel key is not the Ideator's to keep
    assert dropped_prisma_p(None, without) == []
    assert dropped_prisma_p({"prisma_p": None}, without) == []
    assert dropped_prisma_p({"prisma_p": {"funding": "x"}}, without) == []
    # an item that held a placeholder may go: the placeholder message tells the Ideator to drop or rewrite it
    assert dropped_prisma_p({"prisma_p": {"funding": "to be completed by the researcher"}},
                            a_protocol(prisma_p={"rationale": "y"})) == []


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_a_redraft_that_drops_an_item_is_drafted_again_in_the_attempt(env):
    _, ps, _ = env
    full = _draft(**SR_ITEMS, dissemination="A preprint and a journal article.")
    # both judges revise with a reason only: each reason is a point the redrafts answer and its judge closes
    answers = [{"point_id": p, "how": "rebutted", "note": "kept"} for p in ("p1.judge.1", "p1.judge_2.1")]
    votes = _votes("revise", "approve")
    for judge in ("judge", "judge_2"):
        votes[(judge, "ProtocolVoteOut")][1]["resolved"] = [{"point_id": f"p1.{judge}.1", "resolved": True}]
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [full, {**_draft(**SR_ITEMS), "answers": answers},
                                                              {**full, "answers": answers}],
                           ("scout", "SearchStrategyOut"): [_strategy(), _strategy()], **votes})
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "rerun"  # the judges ask for a revision
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    first, second = store.protocols(ps, 2)
    assert "dissemination" not in first["body"]["prisma_p"] and "dissemination" in second["body"]["prisma_p"]
    retry = [c for c in ex.calls if c["role"] == "ideator"][2]["inputs"]
    assert "dissemination" in retry["guidance"]
    assert store.frozen_protocol(ps)["id"] == second["id"]


# ---- R2: P1 does not vote above the cap (calibration: the probe's lower bound decides, P1 protocol rerun 2) ----
SOMEWHAT = ["aspirin stroke elderly trial", *QUERIES[1:]]
COUNTS = {(QUERIES[0], "openalex"): 3100, (QUERIES[0], "europepmc"): 900,  # 4000, at least 3100: over the cap 3000
          (SOMEWHAT[0], "openalex"): 3200, (SOMEWHAT[0], "europepmc"): 300}  # 3500, at least 3200: still over
BETWEEN = {**COUNTS, (SOMEWHAT[0], "openalex"): 2000, (SOMEWHAT[0], "europepmc"): 1500}  # 3500, at least 2000
KEEPS = {**COUNTS, (QUERIES[0], "openalex"): 2500, (QUERIES[0], "europepmc"): 1500}  # v1 4000, at least 2500


@pytest.mark.parametrize("env", [{"production_mode": "auto"}, {"production_mode": "collab"}], indirect=True,
                         ids=["P1", "P2"])
def test_p1_does_not_vote_above_the_cap(env):
    """Both versions' largest single counts (3100, then 3200) are above the cap: the search nearly always finds more
    distinct records than the cap, so P1 spends no judge round on the best (the lower total, 3500)."""
    k, ps, _ = env
    cfg = k.settings()
    cfg.protocol_narrow_tries = 1
    save_settings(cfg)
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [_draft()],
                           ("scout", "SearchStrategyOut"): [_strategy(), _strategy(SOMEWHAT)],
                           **_votes("approve")})
    out = ProtocolWorker().run(_ctx(env, ex, sources=FakeSources({}, record_counts=COUNTS)))
    _, best = store.protocols(ps, 1)
    assert [c["role"] for c in ex.calls] == ["ideator", "scout", "scout"]  # no judge task in either mode
    if intake_of(ps).production_mode == "auto":  # P1: the attempt reruns with the cap failure as its reason
        assert out.status == "rerun"
        assert "at least 3200 records" in out.reason and "3500" in out.reason and "record cap 3000" in out.reason
        assert "narrow" in out.reason
        assert votes_on(ps, "protocol", best["id"]) == [] and store.frozen_protocol(ps) is None
    else:  # P2: you may raise the cap, so your approval opens on the best version
        assert out.status == "waiting_human"
        assert get_checkpoint(ps, out.checkpoint_id)["options"]["protocol_id"] == best["id"]


def test_the_best_version_is_one_p1_may_vote_on(env):
    """v1 (4000 records, at least 2500) and the narrowing's v2 (3500, at least 3200): v2's lower total does not make
    it the best, since its largest single count is above the cap and v1's is not; v1 goes to the vote."""
    k, ps, _ = env
    cfg = k.settings()
    cfg.protocol_narrow_tries = 2
    save_settings(cfg)
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [_draft()],
                           ("scout", "SearchStrategyOut"): [_strategy(), _strategy(SOMEWHAT), _strategy(SOMEWHAT)],
                           **_votes("approve")})
    out = ProtocolWorker().run(_ctx(env, ex, sources=FakeSources({}, record_counts=KEEPS)))
    v1 = store.protocols(ps, 1)[0]
    assert out.status == "done" and store.frozen_protocol(ps)["id"] == v1["id"]
    (failed,) = [c for c in ex.calls if c["role"] == "scout"][2]["inputs"]["narrowing"]["failed_tries"]
    assert failed["version"] == 2 and "largest single count, 3200, is above the record cap 3000" in failed["why"]
    (judged, _) = [c for c in ex.calls if c["schema"] == "ProtocolVoteOut"]
    seen = judged["inputs"]["subject"]["record_probe"]
    assert (seen["total"], seen["lower"]) == (4000, 2500)


@pytest.mark.parametrize("env", [{"production_mode": "auto"}, {"production_mode": "collab"}], indirect=True,
                         ids=["P1", "P2"])
def test_a_version_between_the_bounds_goes_to_the_vote(env):
    """The narrowing runs out with the total (3500, an upper bound) above the cap and the largest single count (2000,
    a lower bound) within it: the probe does not show the cap exceeded, so P1 votes on the version, the judges seeing
    both bounds; P2 opens your approval as before."""
    k, ps, _ = env
    cfg = k.settings()
    cfg.protocol_narrow_tries = 1
    save_settings(cfg)
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [_draft()],
                           ("scout", "SearchStrategyOut"): [_strategy(), _strategy(SOMEWHAT)],
                           **_votes("approve")})
    out = ProtocolWorker().run(_ctx(env, ex, sources=FakeSources({}, record_counts=BETWEEN)))
    _, best = store.protocols(ps, 1)
    if intake_of(ps).production_mode == "collab":
        assert out.status == "waiting_human" and [c["role"] for c in ex.calls] == ["ideator", "scout", "scout"]
        cp = get_checkpoint(ps, out.checkpoint_id)
        assert cp["options"]["protocol_id"] == best["id"]
        assert "3500 records" in cp["prompt"] and "at least 2000" in cp["prompt"]
        return
    assert out.status == "done" and store.frozen_protocol(ps)["id"] == best["id"]
    (judged, _) = [c for c in ex.calls if c["schema"] == "ProtocolVoteOut"]
    seen = judged["inputs"]["subject"]["record_probe"]
    assert (seen["total"], seen["lower"], seen["record_cap"]) == (3500, 2000, 3000)
    assert "upper bound" in seen["note"] and "lower bound" in seen["note"] and "deduplicated" in seen["note"]
    shown = judged["inputs"]["subject"]["protocol"]
    assert "at least 2000" in shown and "deduplicated" in shown


def test_a_narrowing_try_must_keep_the_sources_the_prisma_p_texts_name(env):
    """The draft's PRISMA-P text names arXiv: a narrowing that drops it fails R5, and the Scout is told (before and
    in the failed try's reason) to keep every source the PRISMA-P texts name, not the Ideator's fix."""
    k, _, _ = env
    cfg = k.settings()
    cfg.protocol_narrow_tries = 2
    save_settings(cfg)
    named = _draft(information_sources="We search OpenAlex, Europe PMC and arXiv.")
    with_arxiv = ["openalex", "europepmc", "arxiv"]
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [named],
                           ("scout", "SearchStrategyOut"): [_strategy(sources=with_arxiv),
                                                            _strategy(SOMEWHAT),  # drops arXiv
                                                            _strategy(SOMEWHAT, sources=with_arxiv)]})
    ProtocolWorker().run(_ctx(env, ex, sources=FakeSources({}, record_counts=COUNTS)))
    first, second = [c["inputs"] for c in ex.calls if c["role"] == "scout"][1:]
    assert first["narrowing"]["keep_sources"] == with_arxiv and "arxiv" in first["guidance"]
    (dropped,) = second["narrowing"]["failed_tries"]
    assert "arXiv" in dropped["why"] and "keep every source the PRISMA-P texts name" in dropped["why"]
    assert "name only the strategy's sources" not in dropped["why"]  # the Ideator's remedy is not the Scout's


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_rendered_narrative_plan_shows_its_outcomes(env):
    _, ps, _ = env
    row = {"id": "prt_none", "version": 1, "body": sr_body().model_dump(mode="json")}
    text = "\n".join(render_protocol(ps, row))
    assert "- outcomes: stroke; major bleeding" in text
    assert not any(f"- {k}:" in text for k in ("hksj", "pi_df", "pool_model", "min_studies"))
