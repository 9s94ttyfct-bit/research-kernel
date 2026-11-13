"""Calibration (synthesis across studies, 2026-11-13; docs/eval/synthesis-2026-11-13.md): in srrepro9 every project
that reached argue narrowed its main claim to one or a few studies. The requests a role or judge receives, the
artifacts the kernel computes from the extraction rows and the claims it writes from them."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rk
from rk.argument import ArgumentService, ClaimIn, NumberBinding
from rk.orchestrator.state import (
    record_attempt,
    record_fixed_stages,
    record_passed,
    record_ran,
)
from rk.production import argue as argue_module
from rk.production import synthesis
from rk.production.argue import (
    SYNTHESIS_QUESTION,
    ArgueWorker,
    ArgumentDraft,
    argue_notes,
    argument_claims,
    argument_roles,
    main_claim,
)
from rk.production.artifacts import artifacts
from rk.production.models import ExtractionField, OutcomeSpec, SynthesisPlan
from rk.production.protocol import DRAFT_TASK as IDEATOR_TASK
from rk.production.synthesis import (
    CITATION_RULE,
    NARRATIVE_TASK,
    RANDOM_EFFECTS_RULE,
    SynthesizeWorker,
    synthesis_claims,
    synthesis_limitations,
    synthesis_notes,
)
from rk.verify.judge import INSTRUCTIONS
from rk.verify.stats import DIRECTION, check_stats
from rk.verify.support import verify_support
from tests import test_argue as ta
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_protocol import TOPIC_WORDS
from tests.test_protocol_checks import sr, sr_body
from tests.test_synthesis import (
    BCG,
    BINARY,
    DESIGN,
    META,
    SWIM,
    _ctx,
    _enable_stats,
    _exec,
    _group,
    _setup,
    _votes_pool,
    assess,
    judged,
)
from tests.test_synthesis import Exec as _Exec

TEXT = [ExtractionField(name=f, description=f, type="text") for f in ("intervention", "control")]
ARMS = {"A": {"design": "rct", "intervention": "aspirin", "control": "placebo"},
        "B": {"design": "cohort", "intervention": "aspirin", "control": "no aspirin"}}
ACROSS = "Aspirin was compared with no aspirin in older people."


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _topic_free(text: str) -> None:
    """Its examples come from none of the six evaluation topics."""
    assert not [w for w in TOPIC_WORDS if w in text.lower()], text


# ---- Task 1: pooling: clinical diversity is the random-effects model's job ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
@pytest.mark.parametrize("model", ["DL", "REML", "FE"])
def test_the_random_effects_rule_reaches_the_planner_and_the_pool_vote(env, model):
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META.model_copy(update={"pool_model": model}))
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    (planned,) = ex.of("arguer", "PoolingPlan")
    (voted,) = [c for c in ex.calls if c["schema"] == "VoteOut" and c["role"] == "judge"]
    random_effects = model != "FE"
    assert (RANDOM_EFFECTS_RULE in planned["instructions"]) is random_effects
    assert (RANDOM_EFFECTS_RULE in voted["inputs"]["question"]) is random_effects
    _topic_free(RANDOM_EFFECTS_RULE)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_the_note_on_studies_the_kernel_added_does_not_read_as_unassessed(env):
    """Both srrepro9 no_pool votes on the 45 computable studies rested on the kernel's note that the studies it added
    "were not assessed for comparability by the planner"."""
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[_group([ids["Aronson"]])], votes=_votes_pool())
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    subject = next(c for c in ex.calls if c["schema"] == "VoteOut")["inputs"]["subject"]
    assert subject["added_by_kernel"] == ["Ferguson 1951", "Rosenthal 1952"]
    assert "not assessed" not in subject["note"]
    assert "same outcome and comparison" in subject["note"]
    _topic_free(subject["note"])


# ---- Task 2: V-SUPPORT judges see each passage's study ----
def _across(inputs):
    """A finding citing two notes of study A and one of study B."""
    a, b = inputs["studies"]

    def note(study, word):
        return next(n["id"] for n in study["notes"] if word in n["text"])

    return {"groups": [], "findings": [{"text": ACROSS, "kind": "fact", "evidence": [
                {"note_ids": [note(a, "design"), note(b, "design")], "interpretation": "Both compared arms."},
                {"note_ids": [note(a, "control")], "interpretation": "A's comparator."}]}],
            "representativeness": f"The {inputs['included_k']} included studies were selected by the criteria.",
            "heterogeneity_note": "Designs differed.", "swim": SWIM}


def _judges_split(first: str, second: str) -> dict:
    return {("judge", "JudgeVerdict"): [judged(first)] * 4, ("judge_2", "JudgeVerdict"): [judged(second)] * 4}


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
@pytest.mark.parametrize(("labels", "verdict"), [(("supported", "supported"), "supported"),
                                                 (("supported", "partial"), "uncertain")])
def test_the_judges_see_each_passage_under_its_study(env, labels, verdict):
    """srrepro9: 76 of 106 non-supported opinions on cross-study findings turned on breadth the judges could not count
    from bare passages, and "the evidence text never names the groups". The judge request groups the passages by
    study, each group headed by the study's label, title, year, design and arms; the verdict rule is unchanged."""
    _, ps, _ = env
    _setup(env, ARMS, schema=[DESIGN, *TEXT], plan=SynthesisPlan(method="thematic"))
    ex = _Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_across],
                ("arguer", "NarrowedClaim"): [{"text": ACROSS, "kind": "fact"}], **_judges_split(*labels)})
    SynthesizeWorker().run(_ctx(env, ex))
    first = ex.of("judge", "JudgeVerdict")[0]["inputs"]
    assert first["claim"] == ACROSS
    doc = first["evidence"]
    heads = [line for line in doc.splitlines() if line.startswith("Study ")]
    assert len(heads) == 2
    assert heads[0].startswith("Study 1 of 2: A 1950") and "A trial" in heads[0] and "design rct" in heads[0]
    assert "intervention aspirin" in heads[0] and "control placebo" in heads[0]
    assert heads[1].startswith("Study 2 of 2: B 1951") and "design cohort" in heads[1] and "no aspirin" in heads[1]
    # each study's passages, unchanged, under its heading
    order = [doc.index(t) for t in (heads[0], "The design was rct.", "The control was placebo.", heads[1],
                                    "The design was cohort.")]
    assert order == sorted(order)
    assert doc.count("The design was rct.") == 1
    claim = next(c for c in synthesis_claims(ps, 1) if ArgumentService(ps).get_claim(c)["text"] == ACROSS)
    (row,) = ps.db.execute("SELECT verdict FROM verifications WHERE target_id=? AND verifier='V-SUPPORT'", (claim,))
    assert row["verdict"] == verdict
    assert "grouped by study" in INSTRUCTIONS
    _topic_free(INSTRUCTIONS)


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_a_supported_opinion_quoting_only_a_heading_is_not_valid(env):
    """Fix round 1: the headings are the kernel's, written from the record and the extraction, not evidence. A
    judge's quoted spans are checked against the passages alone, so a "supported" resting on a heading counts as
    uncertain, and the rule stays as strict as before."""
    _, ps, _ = env
    _setup(env, ARMS, schema=[DESIGN, *TEXT], plan=SynthesisPlan(method="thematic"))

    def heading(inputs):
        return {"label": "supported", "quoted_spans": [inputs["evidence"].splitlines()[0]]}

    ex = _Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_across],
                ("arguer", "NarrowedClaim"): [{"text": ACROSS, "kind": "fact"}],
                ("judge", "JudgeVerdict"): [heading] * 4, ("judge_2", "JudgeVerdict"): [heading] * 4})
    SynthesizeWorker().run(_ctx(env, ex))
    assert ex.of("judge", "JudgeVerdict")[0]["inputs"]["evidence"].startswith("Study 1 of 2: A 1950")
    claim = next(c for c in synthesis_claims(ps, 1) if ArgumentService(ps).get_claim(c)["text"] == ACROSS)
    (row,) = ps.db.execute("SELECT verdict, details FROM verifications WHERE target_id=? AND verifier='V-SUPPORT'",
                           (claim,))
    opinions = [o for o in json.loads(row["details"])["opinions"] if o["error"] is None]  # the admitted judges
    assert row["verdict"] == "uncertain"
    assert [(o["raw_label"], o["spans_ok"], o["label"]) for o in opinions] == [("supported", False, "uncertain")] * 2


# ---- Task 3: the narrative contract ----
@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_the_narrative_request_carries_the_citation_rule(env):
    """srrepro9: cross-study findings cited a median of 3 studies (0.6-12.5% of the included ones) and 5 of 63 were
    supported; 0 of 8 evidence-state findings were."""
    _setup(env, ARMS, schema=[DESIGN, *TEXT], plan=SynthesisPlan(method="thematic"))
    ex = _exec()
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    (asked,) = ex.of("arguer", "NarrativeSynthesis")
    assert CITATION_RULE in asked["instructions"]
    assert "notes_capped" not in asked["inputs"]  # a small input is given whole
    assert all(len(s["notes"]) == 3 for s in asked["inputs"]["studies"])
    _topic_free(CITATION_RULE)
    arguer = (Path(rk.__file__).parent / "roles" / "arguer.md").read_text()
    assert "study count" not in arguer and "scope_limits" in arguer and "every study it counts" in arguer
    _topic_free(arguer)


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_a_large_input_caps_the_notes_per_study_and_lists_every_study(env, monkeypatch):
    """srrepro9's 321-study narrative input was about 880k characters; its findings cited 0-5 studies. Over
    ``NARRATIVE_NOTES`` notes, each study keeps its assessment's main notes first, up to an equal share, and the input
    says what was left out; every study stays listed with its label and extracted values."""
    monkeypatch.setattr(synthesis, "NARRATIVE_NOTES", 4)
    studies = {t: {"design": "rct", "intervention": "aspirin", "control": "placebo"} for t in ("A", "B", "C")}
    ids = _setup(env, studies, schema=[DESIGN, *TEXT], plan=SynthesisPlan(method="thematic"))

    def main_note_last(inputs):  # the assessment names the control note as the study's main evidence
        out = assess()(inputs)
        for a, s in zip(out["assessments"], inputs["studies"], strict=True):
            a["note_ids"] = [n["id"] for n in s["notes"] if "control" in n["text"]]
        return out

    ex = _Exec({("arguer", "AssessmentOut"): [main_note_last], ("arguer", "NarrativeSynthesis"): [ta._synth_narrative(
        ["Aspirin was studied."])], ("arguer", "NarrowedClaim"): [], **ta._judges()})
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    (asked,) = ex.of("arguer", "NarrativeSynthesis")
    given = asked["inputs"]["studies"]
    assert [s["item_id"] for s in given] == list(ids.values())
    assert [s["label"] for s in given] == ["A 1950", "B 1951", "C 1952"]
    for s in given:
        assert [n["text"] for n in s["notes"]] == ["The control was placebo."]  # 4 // 3 = 1 note, the main one
        assert s["notes_left_out"] == 2 and len(s["values"]) == 3
    capped = asked["inputs"]["notes_capped"]
    assert "9 notes" in capped and "6 notes" in capped and "at most 1 note" in capped
    _topic_free(capped)


NEED = "A finding counting the studies with a higher value in the treated arm."


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_the_rq_back_edge_carries_the_arguers_requests(env):
    """srrepro9: when the RQ vote split, the back edge's guidance was only "the judges disagree"; the Arguer's requests
    for the missing cross-study finding never reached synthesize."""
    _, ps, _ = env
    ta._synthesize(env)
    record_fixed_stages(ps, ["question"], ta.SEED, actor="tester")
    ex = ta._exec([ta.draft(needs_synthesis=NEED)], [ta.critique()],
                  votes=ta._votes(["proceed", "does_not_answer"], ["proceed", "answers"]))
    out = ArgueWorker().run(ta._ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "synthesize")
    assert "does not answer the fixed research question" in out.reason and NEED in out.reason
    (sent,) = argue_notes(ps, 1, kind="back_edge")
    assert sent["reason"] == out.reason


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_the_synthesis_request_vote_sees_how_many_studies_have_values(env):
    """srrepro9: all 13 synthesis-request decisions were proceed: "the subject gives no extracted values"."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct", "intervention": "aspirin"},
                 "B": {"design": "cohort", "intervention": "not_reported"}}, schema=[DESIGN, *TEXT],
           plan=SynthesisPlan(method="narrative_swim", outcomes=[{"name": "arms", "fields": {
               "intervention": "intervention", "comparator": "control"}}]))
    subject = ArgueWorker._synthesis_subject(ps, SimpleNamespace(question="q", synthesis=[]), [])
    assert subject["usable_values"] == {
        "included_k": 2,
        "fields": {"design": 2, "intervention": 1, "control": 0},
        "outcomes": [{"outcome": "arms", "fields": ["intervention", "control"], "studies": 0}]}
    assert "usable_values" in SYNTHESIS_QUESTION


# ---- Task 4: kernel direction counts for systematic reviews ----
PAIR = OutcomeSpec(name="response", fields={"intervention": "response_new", "comparator": "response_standard"})
PAIRED = [DESIGN, *(ExtractionField(name=f, description=f, type="number") for f in PAIR.fields.values())]
SWIM_PLAN = SynthesisPlan(method="narrative_swim", outcomes=[PAIR])
# usable values, a not_reported one (D) and a missing one (E: no row)
RESPONSES = {"A": {"design": "rct", "response_new": 0.9, "response_standard": 0.8},
             "B": {"design": "cohort", "response_new": 0.7, "response_standard": 0.75},
             "C": {"design": "cohort", "response_new": 0.8, "response_standard": 0.8},
             "D": {"design": "rct", "response_new": 0.85, "response_standard": "not_reported"},
             "E": {"design": "rct", "response_standard": 0.6}}
COUNTED = ("Of the 3 studies with extracted values for response, 1 report a higher value with response_new than with "
           "response_standard, 1 a lower value and 1 the same value (vote counting by direction of effect, not a "
           "pooled estimate; no test of significance).")


def _direction(ps) -> tuple[dict, dict]:
    (note,) = synthesis_notes(ps, 1, kind="direction")
    return note, ArgumentService(ps).get_claim(note["claim_id"])


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_the_kernel_counts_the_studies_by_direction_of_effect(env):
    """srrepro9 §3: the extraction rows gave a cross-study answer the kernel never computed (e.g. 79 of 81 studies
    higher). The artifact holds per study its label, both values and the direction; the kernel's claim states the
    counts, verified by V-STATS and V-NUM; the narrative request carries it."""
    _, ps, _ = env
    ids = _setup(env, RESPONSES, schema=PAIRED, plan=SWIM_PLAN)
    ex = _exec()
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    note, claim = _direction(ps)
    assert note["item_ids"] == [ids[t] for t in "ABC"]
    (a,) = [x for x in artifacts(ps, "synthesize", 1) if x["code_path"] == DIRECTION]
    assert a["kind"] == "stt" and a["id"] == note["stt"]
    assert a["values"]["table"] == [
        {"item_id": ids["A"], "label": "A 1950", "intervention": 0.9, "comparator": 0.8, "direction": "higher"},
        {"item_id": ids["B"], "label": "B 1951", "intervention": 0.7, "comparator": 0.75, "direction": "lower"},
        {"item_id": ids["C"], "label": "C 1952", "intervention": 0.8, "comparator": 0.8, "direction": "same"}]
    assert a["values"]["display"] == {"k": 3, "higher": 1, "lower": 1, "same": 1}
    assert claim["text"] == COUNTED and claim["kind"] == "stat_result" and claim["origin"] == "synthesis"
    assert claim["status"] == "supported" and claim["artifact_ids"] == [a["id"]]
    assert claim["id"] in synthesis_claims(ps, 1)
    assert any("vote counting by direction of effect" in x for x in claim["scope_limits"])
    assert ("V-STATS", "pass") in [tuple(r) for r in ps.db.execute(
        "SELECT verifier, verdict FROM verifications WHERE target_id=?", (a["id"],))]
    (asked,) = ex.of("arguer", "NarrativeSynthesis")
    assert asked["inputs"]["direction_counts"] == [{"claim_id": claim["id"], "text": COUNTED, "outcome": "response"}]
    assert "direction_counts" in NARRATIVE_TASK
    assert synthesis_limitations(ps, 1)["directions"] == {"not_counted": [], "left_out": []}
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    _topic_free(COUNTED)
    _topic_free(NARRATIVE_TASK)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_the_count_is_verified_against_its_artifact_and_an_altered_count_fails(env):
    k, ps, _ = env
    _setup(env, RESPONSES, schema=PAIRED, plan=SWIM_PLAN)
    assert SynthesizeWorker().run(_ctx(env, _exec())).status == "done"
    _, claim = _direction(ps)
    stt = claim["artifact_ids"][0]
    assert check_stats(ps, stt).ok
    assert verify_support(k, None, ps, claim["id"], actor="tester").verdict == "supported"
    svc = ArgumentService(ps)
    altered = svc.add_claim(ClaimIn(
        text=COUNTED.replace("1 report", "2 report"), kind="stat_result", artifact_ids=[stt],
        number_bindings=[NumberBinding(**{**b, "text": "2"}) if b["key"] == "higher" else NumberBinding(**b)
                         for b in claim["number_bindings"]]),
        generator_vendor=None, actor="tester", actor_kind="kernel", origin="synthesis")
    result = verify_support(k, None, ps, altered, actor="tester")
    assert (result.verdict, result.reason) == ("unsupported", "ungrounded_number")
    assert [n.text for n in result.ungrounded_numbers] == ["2"]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_a_meta_analysis_outcome_counts_the_risks_of_its_arms(env):
    """An RR outcome's pair is its arms' risks (events / n); counted also when the pooling falls back."""
    _, ps, _ = env
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)  # statistics not validated: no pool
    assert SynthesizeWorker().run(_ctx(env, _exec())).status == "done"
    _, claim = _direction(ps)
    assert claim["text"].startswith("Of the 3 studies with extracted values for tuberculosis, 0 report a higher value "
                                    "with events_treatment/n_treatment than with events_control/n_control, 3 a lower "
                                    "value and 0 the same value")
    assert claim["status"] == "supported"


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_an_outcome_without_a_field_pair_gets_no_count(env):
    """A protocol frozen before the pair: no count, and nothing refused."""
    _, ps, _ = env
    _setup(env, RESPONSES, schema=PAIRED, plan=SynthesisPlan(method="narrative_swim", outcomes=["response"]))
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec())).status == "done"
    assert synthesis_notes(ps, 1, kind="direction") == []
    assert not [a for a in artifacts(ps, "synthesize", 1) if a["code_path"] == DIRECTION]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_fewer_studies_than_the_minimum_count_nothing_and_say_so(env):
    _, ps, _ = env
    _setup(env, {t: RESPONSES[t] for t in "ADE"}, schema=PAIRED, plan=SWIM_PLAN)
    assert SynthesizeWorker().run(_ctx(env, _exec())).status == "done"
    (note,) = synthesis_notes(ps, 1, kind="direction")
    assert note["claim_id"] is None and "fewer than 2 studies" in note["reason"]
    assert synthesis_limitations(ps, 1)["directions"]["not_counted"] == [{"outcome": "response",
                                                                          "reason": note["reason"]}]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_argue_is_given_the_direction_count_as_a_kernel_finding(env):
    k, ps, lib = env
    _setup(env, RESPONSES, schema=PAIRED, plan=SWIM_PLAN)
    ex = _Exec({("arguer", "AssessmentOut"): [assess()],
                ("arguer", "NarrativeSynthesis"): [ta._synth_narrative([ta.F1, ta.F2])], **ta._judges()})
    record_attempt(ps, "synthesize", 1, run_id=None, guidance="", requested=None, actor="tester")
    assert SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize")).status == "done"
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    _, claim = _direction(ps)
    ex = ta._exec([ta.draft()], [ta.critique()])
    ArgueWorker().run(ta._ctx(env, ex))
    first = ex.of("arguer", "ArgumentDraft")[0]
    given = {c["claim_id"]: c for c in first["inputs"]["synthesis"]}
    assert given[claim["id"]]["text"] == COUNTED and given[claim["id"]]["kind"] == "stat_result"
    assert "count of the included studies by the direction of their effect" in first["instructions"]
    _topic_free(argue_module.DRAFT_TASK)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_the_main_claim_may_reuse_the_direction_count(env):
    """Fix round 1: the main claim may reuse a supported synthesis claim by claim_id, as reasons can, e.g. the kernel's
    direction count (verified by V-STATS and V-NUM, never judged again). It is the main claim: the reasons support it,
    the RQ vote judges its text, the gate passes. A claim_id that is no supported finding is asked again; a draft giving
    both a main claim and a main_claim_id is invalid."""
    k, ps, lib = env
    _setup(env, RESPONSES, schema=PAIRED, plan=SWIM_PLAN)
    ex = _Exec({("arguer", "AssessmentOut"): [assess()],
                ("arguer", "NarrativeSynthesis"): [ta._synth_narrative([ta.F1, ta.F2])], **ta._judges()})
    record_attempt(ps, "synthesize", 1, run_id=None, guidance="", requested=None, actor="tester")
    assert SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize")).status == "done"
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    _, counted = _direction(ps)
    assert counted["status"] == "supported"

    def verified() -> list[tuple]:
        return [tuple(r) for r in ps.db.execute("SELECT verifier, verdict FROM verifications WHERE target_id=? "
                                                "ORDER BY rowid", (counted["id"],))]
    before = verified()

    def reusing(main_id):
        def out(inputs):
            return {"main_claim_id": main_id, "answers_rq": ta.SELF_CHECK,
                    "reasons": [{"ref": "r1", "claim": ta.claim(inputs, ta.REASON, "rct"), "supports": "main",
                                 "warrant": ta.WARRANT}]}
        return out

    ex = ta._exec([reusing("clm_nonesuch"), reusing(counted["id"])], [ta.critique()])
    w = ArgueWorker()
    out = w.run(ta._ctx(env, ex))
    assert out.status == "done", out.reason
    second = ex.of("arguer", "ArgumentDraft")[1]
    assert "clm_nonesuch is not a supported claim of the synthesis" in second["inputs"]["guidance"]
    assert main_claim(ps, 1) == counted["id"]
    assert argument_roles(ps, 1, synthesis_claims(ps, 1))[counted["id"]] == "main"
    (reason,) = argument_claims(ps, 1)  # the main claim is the finding, not a new claim
    (link,) = ArgumentService(ps).list_edges(type="reason_of")
    assert (link["from_id"], link["to_id"]) == (reason, counted["id"])
    (check,) = argue_notes(ps, 1, kind="rq_check")
    assert (check["main_claim_id"], check["decision"]) == (counted["id"], "answers")
    assert all(v["inputs"]["subject"]["main_claim"] == COUNTED for v in ex.of("judge", "VoteOut")
               if "main_claim" in v["inputs"].get("subject", {}))
    assert any("main_claim" in v["inputs"].get("subject", {}) for v in ex.of("judge", "VoteOut"))
    assert verified() == before  # verified by V-STATS and V-NUM in synthesize, never judged again
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    with pytest.raises(ValueError, match="exactly one of claim and claim_id"):
        ArgumentDraft.model_validate({"main_claim": {"text": ta.MAIN, "kind": "fact"}, "main_claim_id": counted["id"]})
    assert "main_claim_id" in argue_module.DRAFT_TASK
    assert "`main_claim_id`" in (Path(rk.__file__).parent / "roles" / "arguer.md").read_text()


def test_the_protocol_maps_a_field_pair_for_every_outcome():
    """The Ideator is told to map every outcome; the check refuses a pair naming a field the schema lacks."""
    other = ExtractionField(name="n_other", description="Participants, comparator", type="number")
    schema = [*sr_body().extraction_schema, other]

    def plan(fields):
        return SynthesisPlan(method="narrative_swim", outcomes=[OutcomeSpec(name="stroke", fields=fields)])

    assert sr(sr_body(extraction_schema=schema, synthesis_plan=plan({"intervention": "n", "comparator": "n_other"}))) \
        == []
    failures = sr(sr_body(extraction_schema=schema,
                          synthesis_plan=plan({"intervention": "n", "comparator": "n_missing"})))
    assert failures == [("synthesis_plan.outcomes: outcome 'stroke': comparator -> 'n_missing' is not a field of the "
                         "extraction schema")]
    assert sr(sr_body(extraction_schema=schema, synthesis_plan=plan({"intervention": "n", "comparator": "design"}))) \
        == ["synthesis_plan.outcomes: outcome 'stroke': comparator -> 'design' is not a number field"]
    assert sr(sr_body()) == []  # outcomes without a pair are not refused
    assert '"intervention"' in IDEATOR_TASK and '"comparator"' in IDEATOR_TASK
    ideator = (Path(rk.__file__).parent / "roles" / "ideator.md").read_text()
    assert '"intervention"' in ideator
    _topic_free(IDEATOR_TASK)
