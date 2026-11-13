"""Calibration (synthesis across studies, 2026-11-13; docs/eval/synthesis-2026-11-13.md): in srrepro9 every project
that reached argue narrowed its main claim to one or a few studies. The requests a role or judge receives, the
artifacts the kernel computes from the extraction rows and the claims it writes from them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import rk
from rk.argument import ArgumentService
from rk.orchestrator.state import record_fixed_stages
from rk.production import synthesis
from rk.production.argue import SYNTHESIS_QUESTION, ArgueWorker, argue_notes
from rk.production.models import ExtractionField, SynthesisPlan
from rk.production.synthesis import (
    CITATION_RULE,
    RANDOM_EFFECTS_RULE,
    SynthesizeWorker,
    synthesis_claims,
)
from rk.verify.judge import INSTRUCTIONS
from tests import test_argue as ta
from tests.production_fakes import make_frozen_project
from tests.test_protocol import TOPIC_WORDS
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
