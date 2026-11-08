"""Plan 5b Task 4: the synthesize stage. The Arguer assesses every usable study and drafts the synthesis; the kernel
computes every number (a pre-specified meta-analysis only when comparable, approved and the statistics module is
validated; else a narrative synthesis and a recorded analysis deviation), stores and verifies the findings, and gates
on what it stored."""

from __future__ import annotations

import json
import math

import pytest

from rk.argument import CRITERIA, ArgumentService
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.config import load_settings, save_settings
from rk.fulltext import latest_snapshot
from rk.library.models import Creator, Ids, ItemIn
from rk.notes import NoteIn, add_note
from rk.orchestrator.state import record_attempt, record_passed, record_ran
from rk.production import store
from rk.production.artifacts import artifact, artifacts
from rk.production.claims import narrow_or_withdraw
from rk.production.models import ExtractionField, OutcomeSpec, SynthesisPlan
from rk.production.protocol import protocol_prereg_ok
from rk.production.synthesis import (
    STATS_NOT_VALIDATED,
    SynthesizeWorker,
    synthesis_claims,
    synthesis_limitations,
    synthesis_notes,
)
from rk.stats import STATS_VERSION
from rk.stats.effects import BINARY_FIELDS
from rk.verify.stats import stat_claim_text
from tests.discovery_fakes import ScriptedExecutor, add_text_layer
from tests.production_fakes import (
    a_protocol,
    make_frozen_project,
    production_ctx,
    votes_on,
)

TA, FT = "title_abstract", "full_text"
SR_ITEMS = {"rationale": "Guidelines stop at 80.", "objectives": "Estimate benefit and harm.",
            "selection_process": "Two screeners.", "data_collection_process": "One reader.",
            "risk_of_bias": "RoB 2."}
BINARY = [ExtractionField(name=f, description=f, type="number") for f in BINARY_FIELDS]
AE_FIELDS = [f"ae_{f}" for f in BINARY_FIELDS]  # the adverse-event outcome's own fields
BINARY_AE = BINARY + [ExtractionField(name=f, description=f, type="number") for f in AE_FIELDS]
DESIGN = ExtractionField(name="design", description="Study design", type="category", categories=["rct", "cohort"])
TB = OutcomeSpec(name="tuberculosis", fields={r: r for r in BINARY_FIELDS})
AE = OutcomeSpec(name="adverse events", fields={r: f"ae_{r}" for r in BINARY_FIELDS})
# the free-text model and heterogeneity fields are descriptive only: the typed fields set the pool options
META = SynthesisPlan(method="meta_analysis", effect_measure="RR", model="random effects (REML would be the default)",
                     heterogeneity="I2 and tau2; no HKSJ", pool_model="DL", pi_df="k-2", outcomes=[TB])
META2 = META.model_copy(update={"outcomes": [TB, AE]})
# BCG trials 1-4 (Viechtbauer 2010)
BCG = {"Aronson": {"events_treatment": 4, "n_treatment": 123, "events_control": 11, "n_control": 139},
       "Ferguson": {"events_treatment": 6, "n_treatment": 306, "events_control": 29, "n_control": 303},
       "Rosenthal": {"events_treatment": 3, "n_treatment": 231, "events_control": 11, "n_control": 220},
       "Hart": {"events_treatment": 62, "n_treatment": 13598, "events_control": 248, "n_control": 12867}}
# the same trials with an adverse-event outcome of their own (illustrative counts)
BCG_AE = {t: {**v, **dict(zip(AE_FIELDS, ae, strict=True))} for (t, v), ae in zip(
    list(BCG.items())[:3], [(10, 123, 12, 139), (20, 306, 18, 303), (5, 231, 9, 220)], strict=True)}
SWIM = {k: f"SWiM {k}." for k in ("grouping", "standardised_metric", "synthesis_method", "prioritisation",
                                   "heterogeneity", "certainty", "data_presentation", "results", "limitations")}
FINDING = "Aspirin was studied in older people."


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _enable_stats() -> None:
    save_settings(load_settings().model_copy(update={"stats_validated": {"version": STATS_VERSION,
                                                                          "report": "docs/eval/stats-x.md"}}))


def _setup(env, studies: dict[str, dict], *, schema, plan, abstract: frozenset = frozenset()) -> dict[str, str]:
    """Freeze a protocol with ``schema`` and ``plan``, include ``studies`` (title -> {field: value}) in a passed
    search_screen and extract them in a passed read_extract attempt 1, each value with a verified quote note (from the
    full text; from the abstract for the titles in ``abstract``). Returns title -> item id."""
    _, ps, lib = env
    pid = store.add_protocol(ps, 1, a_protocol(extraction_schema=schema, synthesis_plan=plan, prisma_p=SR_ITEMS),
                             version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    ids = {}
    for i, (title, values) in enumerate(studies.items()):
        text = "Methods section. " + " ".join(f"The {f} was {v}." for f, v in values.items() if v != "not_reported")
        is_abs = title in abstract
        iid = lib.add_item(ItemIn(title=f"{title} trial", year=1950 + i, creators=[Creator(family=title)],
                                  ids=Ids(doi=f"10.1000/s{i}"), abstract=text if is_abs else "Abstract."),
                           added_by="agent", actor="tester").item_id
        rid = store.add_record(ps, 1, iid, language="en", actor="tester")
        for phase in (TA, FT):
            store.set_final(ps, rid, 1, phase, "yes", "single", "fits", actor="tester")
        store.set_record(ps, rid, actor="tester", retrieved=not is_abs, abstract_only=is_abs)
        if not is_abs:
            add_text_layer(ps, iid, [text])
        for f, v in values.items():
            quote = "Methods section." if v == "not_reported" else f"The {f} was {v}."
            nid = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=quote, keywords=["extraction", f],
                                           role_in_source="main", against="abstract" if is_abs else "text"),
                           actor="tester").note_id
            store.add_extraction(ps, 1, iid, f, v, nid, not is_abs, actor="tester")
        store.add_extraction_note(ps, 1, "study", actor="tester", item_id=iid, record_id=rid,
                                  retraction={"status": "none", "checked": "recorded"}, flagged=False,
                                  text="abstract" if is_abs else "full_text",
                                  snapshot=None if is_abs else latest_snapshot(ps, iid)[0],
                                  sufficient="low" if is_abs else "full", notes=[], problems=[], carried_from=None)
        ids[title] = iid
    record_passed(ps, "search_screen", 1, actor="tester")
    record_attempt(ps, "read_extract", 1, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, "read_extract", 1, "done", "", actor="tester")
    record_passed(ps, "read_extract", 1, actor="tester")
    return ids


class Exec(ScriptedExecutor):
    """``ScriptedExecutor`` whose queued outputs may be callables of the task inputs."""

    def run(self, role, instructions, inputs, output_schema, **kw):
        q = self.script.get((role, output_schema.__name__))
        if q and callable(q[0]):
            q[0] = q[0](inputs)
        return super().run(role, instructions, inputs, output_schema, **kw)

    def of(self, role: str, schema: str | None = None) -> list[dict]:
        return [c for c in self.calls if c["role"] == role and (schema is None or c["schema"] == schema)]


def assess(score: int = 2):
    def out(inputs):
        return {"assessments": [{"item_id": s["item_id"],
                                 "criteria": {c: {"score": score, "reason": f"{c} ok"} for c in CRITERIA},
                                 "note_ids": [n["id"] for n in s["notes"]]} for s in inputs["studies"]]}
    return out


def narrative(*, representativeness: str | None = None, swim: bool = True, text: str = FINDING):
    def out(inputs):
        studies = inputs["studies"]
        k = inputs["included_k"]
        return {"groups": [{"theme_or_outcome": "stroke", "item_ids": [s["item_id"] for s in studies],
                            "direction_summary": "Fewer strokes.", "certainty_note": "Low certainty."}],
                "findings": [{"text": text, "kind": "fact",
                              "evidence": [{"note_ids": [s["notes"][0]["id"] for s in studies],
                                            "interpretation": "The studies enrolled people over 80."}]}],
                "representativeness": (f"The {k} included studies were selected by the protocol's eligibility "
                                       f"criteria." if representativeness is None else representativeness),
                "heterogeneity_note": "Designs differed.", "swim": SWIM if swim else None}
    return out


def judged(label: str = "supported"):
    def out(inputs):
        return {"label": label, "quoted_spans": [inputs["evidence"].split("\n\n")[0]]}
    return out


def _judges(label: str = "supported", n: int = 6) -> dict:
    return {("judge", "JudgeVerdict"): [judged(label) for _ in range(n)],
            ("judge_2", "JudgeVerdict"): [judged(label) for _ in range(n)]}


def _exec(*, plans=(), narratives=None, votes=None, judges=None, narrowed=()) -> Exec:
    return Exec({("arguer", "AssessmentOut"): [assess() for _ in range(5)],
                 ("arguer", "PoolingPlan"): list(plans),
                 ("arguer", "NarrativeSynthesis"): list(narratives or [narrative()]),
                 ("arguer", "NarrowedClaim"): list(narrowed),
                 **(judges if judges is not None else _judges()), **(votes or {})})


def _group(item_ids, *, comparable: bool = True, reason: str = "same PICO", measure: str = "RR") -> dict:
    return {"groups": [{"outcome": "tuberculosis", "measure": measure, "item_ids": list(item_ids),
                        "comparability": {c: {"score": 2, "reason": "same"}
                                          for c in ("population", "intervention", "comparator", "outcome", "design")},
                        "comparable": comparable, "reason": reason}]}


def _ctx(env, ex, attempt: int = 1):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="synthesize", attempt=attempt)


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


def _claims(ps, attempt: int = 1) -> list[dict]:
    svc = ArgumentService(ps)
    return [svc.get_claim(c) for c in synthesis_claims(ps, attempt)]


def _analysis_deviations(ps) -> list[str]:
    return [d["reason"] for d in store.deviations(ps, kind="analysis")]


# ---- step 1: assessment ----
@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_assessment_caps_sufficient_for_abstract_only(env):
    _, ps, _ = env
    studies = {f"S{i:02d}": {"design": "cohort"} for i in range(11)}
    ids = _setup(env, studies, schema=[DESIGN], plan=SynthesisPlan(method="thematic"), abstract=frozenset({"S03"}))
    ex = _exec()
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    # one task per batch of up to 10 studies
    assert [len(c["inputs"]["studies"]) for c in ex.of("arguer", "AssessmentOut")] == [10, 1]
    got = {a["item_id"]: a for a in synthesis_notes(ps, 1, kind="assessment")}
    assert set(got) == set(ids.values())
    assert got[ids["S03"]]["criteria"]["sufficient"] == {"score": 0, "reason": "abstract only"}
    assert got[ids["S00"]]["criteria"]["sufficient"] == {"score": 2, "reason": "sufficient ok"}
    # the finding's evidence carries the assessed criteria; one abstract note among full-text ones is no cap
    (claim,) = _claims(ps)
    (ev,) = claim["evidence"]
    assert ev["criteria"]["accurate"]["score"] == 2 and ev["criteria"]["sufficient"]["score"] == 2
    assert claim["origin"] == "synthesis" and claim["status"] == "supported"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_evidence_of_abstract_notes_only_is_capped(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "cohort"}, "B": {"design": "rct"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"), abstract=frozenset({"A", "B"}))
    out = SynthesizeWorker().run(_ctx(env, _exec()))
    assert out.status == "done", out.reason
    (claim,) = _claims(ps)
    assert claim["evidence"][0]["criteria"]["sufficient"] == {"score": 0, "reason": "abstract only"}


# ---- step 2: meta-analysis ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_meta_analysis_pools_approved_group_with_kernel_claims(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human" and out.checkpoint_id
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "pooling_approval" and cp["stage"] == "synthesize"
    assert "tuberculosis" in cp["prompt"] and "RR" in cp["prompt"]
    # the pre-specified pool options are shown
    assert "model DL, HKSJ auto, prediction interval df k-2" in cp["prompt"]
    with pytest.raises(ValueError, match="only be answered by a human"):  # a hard human checkpoint
        answer_checkpoint(ps, cp["id"], {"decisions": {"0": "pool"}}, answered_by="vote", actor="kernel")
    assert artifacts(ps, "synthesize", 1) == []  # nothing pooled before your approval

    bad = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decisions": {"0": "maybe"}}))
    assert bad.status == "waiting_human" and bad.checkpoint_id != cp["id"]
    again = get_checkpoint(ps, bad.checkpoint_id)
    assert again["kind"] == "pooling_approval" and "could not be applied" in again["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, again["id"], {"decisions": {"0": "pool"}}))
    assert out.status == "done", out.reason

    stt, fig = artifacts(ps, "synthesize", 1)
    assert (stt["kind"], fig["kind"]) == ("stt", "fig")
    # the protocol's pool options, passed through as requested
    assert stt["values"]["options"] == {"model": "DL", "hksj": "auto", "pi_df": "k-2", "level": 0.95}
    assert stt["values"]["item_ids"] == [ids[t] for t in ("Aronson", "Ferguson", "Rosenthal")]
    assert stt["values"]["extraction_attempt"] == 1 and stt["values"]["display"]["k"] == 3
    stat = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    (claim,) = stat
    text, bindings = stat_claim_text(artifact(ps, stt["id"]))
    assert claim["text"] == text and claim["number_bindings"] == bindings
    assert claim["origin"] == "synthesis" and claim["status"] == "supported"
    assert set(claim["artifact_ids"]) == {stt["id"], fig["id"]}
    # the scope names the protocol's outcome and extraction fields, never model-written text
    assert claim["scope_limits"][:3] == ["outcome: tuberculosis", "effect measure: RR",
                                         "extraction fields: events_treatment, n_treatment, events_control, n_control"]
    for a in (stt, fig):
        assert ("V-STATS", "pass") in [tuple(r) for r in ps.db.execute(
            "SELECT verifier, verdict FROM verifications WHERE target_id=?", (a["id"],))]
    assert _analysis_deviations(ps) == []
    # the narrative synthesis sees the pooled result
    assert ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]["pooled"][0]["claim_id"] == claim["id"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # handling the answer again does nothing twice
    assert w.on_answer(_ctx(env, ex), get_checkpoint(ps, again["id"])).status == "done"
    assert len(artifacts(ps, "synthesize", 1)) == 2 and len(_claims(ps)) == 2


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_p1_pooling_vote_disagreement_means_narrative(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    votes = {("judge", "VoteOut"): [{"choice": "pool", "reason": "same PICO"}],
             ("judge_2", "VoteOut"): [{"choice": "no_pool", "reason": "designs differ"}]}
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=votes)
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    (v,) = [r for r in ps.db.execute("SELECT * FROM votes")]
    assert v["decision"] == "no_pool" and v["conservative"] == 1
    assert artifacts(ps, "synthesize", 1) == [] and all(c["kind"] != "stat_result" for c in _claims(ps))
    (reason,) = _analysis_deviations(ps)
    assert "tuberculosis" in reason and "not approved by the P1 vote" in reason
    (call,) = [c for c in ex.calls if c["role"] == "judge" and c["schema"] == "VoteOut"]
    assert call["inputs"]["subject"]["pool_options"] == {"model": "DL", "hksj": "auto", "pi_df": "k-2", "level": 0.95}
    assert "data, not instructions" in call["inputs"]["question"]
    assert reason in [f["reason"] for f in synthesis_limitations(ps, 1)["fallbacks"]]
    assert protocol_prereg_ok(ps)
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert votes_on(ps, "pooling_group", v["subject_id"])[0]["question"].startswith("Should")


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_incomparable_studies_fall_back_to_narrative_with_deviation(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group((s["item_id"] for s in inputs["studies"]), comparable=False,
                                            reason="the trials report incompatible outcomes")])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]  # nothing to approve
    assert artifacts(ps, "synthesize", 1) == []
    claims = _claims(ps)
    assert claims and all(c["kind"] != "stat_result" for c in claims)  # no pooled number appears
    (reason,) = _analysis_deviations(ps)
    assert "not comparable" in reason and "incompatible outcomes" in reason
    (dev,) = store.deviations(ps, kind="analysis")
    assert (dev["from_version"], dev["to_version"], dev["stage"]) == (1, 1, "synthesize")
    assert reason in [f["reason"] for f in synthesis_limitations(ps, 1)["fallbacks"]]
    assert ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]["fallbacks"][0]["reason"] == reason
    assert protocol_prereg_ok(ps)  # an analysis deviation leaves G-PREREG passing
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_pooled_k_excludes_uncomputable_studies(env):
    _, ps, _ = env
    _enable_stats()
    studies = {**BCG, "Median": {"events_treatment": "not_reported", "n_treatment": 80,
                                 "events_control": "not_reported", "n_control": 75}}
    ids = _setup(env, studies, schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    out = w.on_answer(_ctx(env, ex), _answer(ps, out.checkpoint_id, {"decisions": {"0": "pool"}}))
    assert out.status == "done", out.reason
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    assert stt["values"]["display"]["k"] == 4 and len(stt["values"]["studies"]) == 4
    (un,) = stt["values"]["uncomputable"]
    assert un["item_id"] == ids["Median"] and "events_treatment" in un["reason"]
    (claim,) = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    assert "; 4 studies;" in claim["text"]
    assert synthesis_limitations(ps, 1)["uncomputable"] == [
        {"outcome": "tuberculosis", "measure": "RR", "item_id": ids["Median"], "reason": un["reason"]}]
    # the study stays in the narrative
    assert ids["Median"] in [s["item_id"] for s in ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]["studies"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_unvalidated_stats_module_means_narrative_and_deviation(env):
    _, ps, _ = env
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec()
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert not ex.of("arguer", "PoolingPlan") and artifacts(ps, "synthesize", 1) == []
    assert _analysis_deviations(ps) == [STATS_NOT_VALIDATED]
    assert STATS_NOT_VALIDATED == "meta-analysis pre-specified but the statistics module is not validated"
    assert [f["reason"] for f in synthesis_limitations(ps, 1)["fallbacks"]] == [STATS_NOT_VALIDATED]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # a rerun does not record the same deviation twice
    assert w.run(_ctx(env, _exec(), attempt=2)).status == "done"
    assert _analysis_deviations(ps) == [STATS_NOT_VALIDATED]


# ---- scoping and thematic ----
@pytest.mark.parametrize("env", [{"paper_type": "scoping_review"}], indirect=True)
def test_scoping_review_builds_evidence_map(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct", "n": 10}, "B": {"design": "cohort", "n": 20}, "C": {"design": "rct", "n": 5}},
           schema=[DESIGN, ExtractionField(name="n", description="n", type="number")],
           plan=SynthesisPlan(method="evidence_map"))
    ex = _exec()
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    stt, fig = artifacts(ps, "synthesize", 1)
    assert (stt["code_path"], fig["code_path"]) == ("rk.stats.plots:map_table", "rk.verify.stats:map_markdown")
    assert stt["values"]["fields"] == ["design"]  # the category fields only
    assert stt["values"]["table"]["fields"]["design"] == {"rct": 2, "cohort": 1}
    assert (ps.root / fig["path"]).read_text(encoding="utf-8").startswith("# Evidence map (3 studies)")
    for a in (stt, fig):
        assert [tuple(r) for r in ps.db.execute("SELECT verifier, verdict FROM verifications WHERE target_id=?",
                                                (a["id"],))] == [("V-STATS", "pass")]
    assert ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]["evidence_map"]["fields"]["design"] == \
        {"rct": 2, "cohort": 1}
    assert all(c["kind"] != "stat_result" for c in _claims(ps))
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_critical_review_thematic_synthesis_gate(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin may have been studied in some older people."
    ex = _exec(judges={("judge", "JudgeVerdict"): [judged("partial"), judged()],
                       ("judge_2", "JudgeVerdict"): [judged(), judged()]},
               narrowed=[{"text": narrower, "kind": "fact"}])
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    out = w.run(ctx)
    assert out.status == "done", out.reason
    assert artifacts(ps, "synthesize", 1) == []  # a thematic synthesis has no statistics
    assert not list_checkpoints(ps, "open")  # P1 never asks
    (claim,) = _claims(ps)
    assert claim["status"] == "uncertain"
    g = w.gate(ps, 1)
    assert not g.ok and any(claim["id"] in f and "status uncertain" in f for f in g.failures)
    # P1 at the edge limit: the uncertain finding is narrowed and verified again, never voted up
    assert w.degrade(ctx, g.failures).status == "done"
    old, new = _claims(ps)
    assert old["status"] == "withdrawn" and new["narrowed_from"] == old["id"] and new["status"] == "supported"
    assert new["text"] == narrower and new["evidence"][0]["note_ids"] == old["evidence"][0]["note_ids"]
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]
    lim = synthesis_limitations(ps, 1)["claims"]
    assert [c["claim_id"] for c in lim["withdrawn"]] == [old["id"]]
    assert [c["claim_id"] for c in lim["narrowed"]] == [new["id"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_narrow_or_withdraw_withdraws_both_when_the_rewrite_fails(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(judges=_judges("partial"), narrowed=[{"text": "Aspirin may help some.", "kind": "fact"}])
    ctx = _ctx(env, ex)
    assert SynthesizeWorker().run(ctx).status == "done"
    (claim,) = _claims(ps)
    res = narrow_or_withdraw(ctx, claim["id"])
    assert res.status == "withdrawn" and res.new_claim_id
    old, new = _claims(ps)
    assert old["status"] == new["status"] == "withdrawn"
    assert res.new_claim_id in old["withdrawn_reason"] and "not supported" in new["withdrawn_reason"]
    with pytest.raises(ValueError, match="withdrawn"):
        narrow_or_withdraw(ctx, claim["id"])


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_uncertain_finding_opens_uncertain_support(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(judges=_judges("partial"))
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    (claim,) = _claims(ps)
    assert cp["kind"] == "uncertain_support" and cp["stage"] == "synthesize"
    assert cp["options"]["claim_ids"] == [claim["id"]] and FINDING in cp["prompt"]
    with pytest.raises(ValueError, match="P1"):
        narrow_or_withdraw(_ctx(env, ex), claim["id"])
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {claim["id"]: "supported"}}))
    assert out.status == "done", out.reason
    (claim,) = _claims(ps)
    assert claim["status"] == "supported"
    # your decision is attributed to you in the status event
    (kind,) = ps.db.execute("SELECT actor_kind FROM events WHERE type='claim.status_set' AND entity_id=? "
                            "ORDER BY seq DESC LIMIT 1", (claim["id"],)).fetchone()
    assert kind == "human"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_uncertain_support_answers_withdraw_rerun_and_invalid(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(judges=_judges("partial"))
    w = SynthesizeWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    (claim,) = _claims(ps)
    for bad in ({"claims": {}}, {"claims": {claim["id"]: "maybe"}}, {"decision": "rerun"}):
        out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], bad))
        assert out.status == "waiting_human" and out.checkpoint_id != cp["id"], bad
        cp = get_checkpoint(ps, out.checkpoint_id)
        assert cp["kind"] == "uncertain_support" and "could not be applied" in cp["prompt"]
        assert cp["options"]["claim_ids"] == [claim["id"]]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "rerun", "comment": "Use fewer words."}))
    assert (out.status, out.reason) == ("rerun", "Use fewer words.")
    cp2 = get_checkpoint(ps, w.run(_ctx(env, _exec(judges=_judges("partial")), 2)).checkpoint_id)
    (claim2,) = _claims(ps, 2)
    out = w.on_answer(_ctx(env, ex, 2), _answer(ps, cp2["id"], {"claims": {claim2["id"]: "withdraw"}}))
    assert out.status == "done"
    (claim2,) = _claims(ps, 2)
    assert claim2["status"] == "withdrawn" and "withdrawn by you" in claim2["withdrawn_reason"]
    assert "no supported synthesis finding" in w.gate(ps, 2).failures


# ---- the representativeness statement ----
@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_representativeness_statement_required(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}, "C": {"design": "rct"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    w = SynthesizeWorker()
    for attempt, statement in ((1, ""), (2, "The included studies were selected by the eligibility criteria.")):
        ex = _exec(narratives=[narrative(representativeness=statement)])
        assert w.run(_ctx(env, ex, attempt)).status == "done"
        inputs = ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]
        assert inputs["included_k"] == 3 and "selection_basis" in inputs
        g = w.gate(ps, attempt)
        assert not g.ok and any("representativeness" in f and "3" in f for f in g.failures), g.failures
    ex = _exec(narratives=[narrative()])
    assert w.run(_ctx(env, ex, 3)).status == "done"
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_systematic_narrative_needs_every_swim_item(env):
    _, ps, _ = env
    _setup(env, dict(list(BCG.items())[:2]), schema=BINARY, plan=SynthesisPlan(method="narrative_swim"))
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec(narratives=[narrative(swim=False)]))).status == "done"
    assert any("SWiM" in f for f in w.gate(ps, 1).failures)


# ---- the back-edge ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_missing_outcome_fields_go_back_to_read_extract(env):
    _, ps, _ = env
    _enable_stats()
    studies = {t: {f: x for f, x in v.items() if f != "events_control"} for t, v in list(BCG.items())[:3]}
    _setup(env, studies, schema=BINARY, plan=META)  # no study has an events_control row
    plan = [lambda inputs: _group(s["item_id"] for s in inputs["studies"])]
    ex = _exec(plans=list(plan))
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    out = w.run(ctx)
    assert out.status == "back_edge" and out.to == "read_extract"
    assert "events_control" in out.reason and "tuberculosis" in out.reason
    assert not ex.of("arguer", "NarrativeSynthesis")
    # P1 at the edge limit: the group falls back to narrative with a deviation naming the fields
    assert w.degrade(ctx, [out.reason]).status == "done"
    (reason,) = _analysis_deviations(ps)
    assert "events_control" in reason
    assert artifacts(ps, "synthesize", 1) == [] and ex.of("arguer", "NarrativeSynthesis")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # never back twice for the same fields: the next attempt falls back instead
    out = w.run(_ctx(env, _exec(plans=list(plan)), 2))
    assert out.status == "done", out.reason
    (fb,) = synthesis_notes(ps, 2, kind="fallback")
    assert "events_control" in fb["reason"] and "already asked to go back to read_extract" in fb["reason"]
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_fields_reported_as_not_reported_fall_back_without_going_back(env):
    _, ps, _ = env
    _enable_stats()
    studies = {t: {**v, "events_control": "not_reported"} for t, v in list(BCG.items())[:3]}
    _setup(env, studies, schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason  # the studies verifiably do not report it: no dead-end back-edge
    (reason,) = _analysis_deviations(ps)
    assert "events_control" in reason and "not reported" in reason
    assert artifacts(ps, "synthesize", 1) == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


# ---- the pre-specified analysis never silently changes ----
def _votes_pool(n: int = 1) -> dict:
    return {("judge", "VoteOut"): [{"choice": "pool", "reason": "ok"}] * n,
            ("judge_2", "VoteOut"): [{"choice": "pool", "reason": "ok"}] * n}


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_empty_pooling_plan_falls_back_with_deviation(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec(plans=[{"groups": []}]))).status == "done"
    (reason,) = _analysis_deviations(ps)
    assert reason == "the pooling plan proposed no group for the pre-specified outcome 'tuberculosis' (effect measure RR)"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # a fallback that is not recorded fails the gate
    ps.db.execute("DELETE FROM protocol_deviations")
    assert any("not recorded as a deviation" in f for f in w.gate(ps, 1).failures)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_measure_other_than_pre_specified_falls_back_with_deviation(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group((s["item_id"] for s in inputs["studies"]), measure="OR")],
               votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"] and artifacts(ps, "synthesize", 1) == []
    reasons = _analysis_deviations(ps)
    assert any("pre-specifies the effect measure RR" in r and "OR" in r for r in reasons)
    assert "the pooling plan proposed no group for the pre-specified outcome 'tuberculosis' (effect measure RR)" in reasons
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_meta_analysis_protocol_needs_typed_pool_options():
    from pydantic import ValidationError

    from rk.production.protocol import check_protocol

    bare = a_protocol(synthesis_plan=SynthesisPlan(method="meta_analysis", effect_measure="risk ratio"),
                      prisma_p=SR_ITEMS)
    failures = check_protocol(bare, "systematic_review")
    assert "synthesis_plan.pool_model is required for a meta-analysis (REML, DL or FE)" in failures
    assert "synthesis_plan.outcomes is required for a meta-analysis (the pre-specified outcome names)" in failures
    assert any("synthesis_plan.effect_measure" in f and "RR, OR, RD, MD or SMD" in f for f in failures)
    assert check_protocol(a_protocol(synthesis_plan=META, prisma_p=SR_ITEMS, extraction_schema=BINARY),
                          "systematic_review") == []
    assert check_protocol(a_protocol(synthesis_plan=META2, prisma_p=SR_ITEMS, extraction_schema=BINARY_AE),
                          "systematic_review") == []
    assert (META.pool_model, META.hksj, META.pi_df) == ("DL", "auto", "k-2")
    twice = META.model_copy(update={"outcomes": [TB, OutcomeSpec(name=" Tuberculosis", fields=AE.fields),
                                                 OutcomeSpec(name="", fields={})]})
    failures = check_protocol(a_protocol(synthesis_plan=twice, prisma_p=SR_ITEMS, extraction_schema=BINARY_AE),
                              "systematic_review")
    assert "synthesis_plan.outcomes: outcome 'Tuberculosis' is given twice" in failures
    assert "synthesis_plan.outcomes: an outcome name is empty" in failures
    # each outcome maps every role of the measure to its own number fields of the extraction schema
    schema = [*BINARY, ExtractionField(name="arm", description="Arm", type="text")]
    bad = META.model_copy(update={"outcomes": [
        TB, OutcomeSpec(name="harm", fields={"events_treatment": "ae_events_treatment", "n_treatment": "arm",
                                             "events_control": "events_control", "dose": "n_control"}),
        OutcomeSpec(name="death", fields={r: r for r in BINARY_FIELDS})]})
    failures = check_protocol(a_protocol(synthesis_plan=bad, prisma_p=SR_ITEMS, extraction_schema=schema),
                              "systematic_review")
    for f in ("synthesis_plan.outcomes: outcome 'harm' maps no field to n_control",
              ("synthesis_plan.outcomes: outcome 'harm' maps 'dose', not a role of RR (events_treatment, n_treatment, "
              "events_control, n_control)"),
              ("synthesis_plan.outcomes: outcome 'harm': events_treatment -> 'ae_events_treatment' is not a field of "
              "the extraction schema"),
              "synthesis_plan.outcomes: outcome 'harm': n_treatment -> 'arm' is not a number field",
              "synthesis_plan.outcomes: outcomes 'tuberculosis' and 'death' map to the same fields"):
        assert f in failures, (f, failures)
    # a round-2 protocol's plain outcome names still load (with no field mapping), and fail the check
    legacy = SynthesisPlan.model_validate({**META.model_dump(), "outcomes": ["tuberculosis"]})
    assert legacy.outcomes == [OutcomeSpec(name="tuberculosis", fields={})]
    assert any("maps no field to" in f for f in check_protocol(a_protocol(synthesis_plan=legacy, prisma_p=SR_ITEMS),
                                                               "systematic_review"))
    with pytest.raises(ValidationError):
        SynthesisPlan(method="meta_analysis", hksj="yes")
    with pytest.raises(ValidationError):
        SynthesisPlan(method="meta_analysis", pool_model="ML")


def _two(item_ids, outcomes: list[str]) -> dict:
    groups = [_group(item_ids, )["groups"][0] | {"outcome": o} for o in outcomes]
    return {"groups": groups}


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_plan_for_a_secondary_outcome_only_falls_back_for_the_primary(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, BCG_AE, schema=BINARY_AE, plan=META2)
    ex = _exec(plans=[lambda inputs: _two([s["item_id"] for s in inputs["studies"]], ["adverse events"])],
               votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (claim,) = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    assert claim["scope_limits"][0] == "outcome: adverse events"
    assert _analysis_deviations(ps) == [
        "the pooling plan proposed no group for the pre-specified outcome 'tuberculosis' (effect measure RR)"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_group_of_an_unlisted_outcome_is_not_pooled(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _two([s["item_id"] for s in inputs["studies"]], ["Tuberculosis cases"])],
               votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"] and artifacts(ps, "synthesize", 1) == []
    reasons = _analysis_deviations(ps)
    assert ("pooling of 'Tuberculosis cases' (RR) not done: 'Tuberculosis cases' is not a pre-specified outcome "
            "(pre-specified: 'tuberculosis')") in reasons
    assert "the pooling plan proposed no group for the pre-specified outcome 'tuberculosis' (effect measure RR)" in reasons
    inputs = ex.of("arguer", "PoolingPlan")[0]["inputs"]
    assert inputs["outcomes"] == ["tuberculosis"] and inputs["outcome_fields"] == {"tuberculosis": TB.fields}
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_every_pre_specified_outcome_pooled_from_its_own_fields(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, BCG_AE, schema=BINARY_AE, plan=META2)
    ex = _exec(plans=[lambda inputs: _two([s["item_id"] for s in inputs["studies"]], ["tuberculosis", "adverse events"])],
               votes=_votes_pool(2))
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    stats = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    assert [c["scope_limits"][0] for c in stats] == ["outcome: tuberculosis", "outcome: adverse events"]
    tb, ae = [a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt"]
    # each outcome is pooled from its own fields: distinct numbers, the mapping recorded for V-STATS
    assert (tb["values"]["field_map"], ae["values"]["field_map"]) == (TB.fields, AE.fields)
    assert tb["values"]["display"]["estimate"] != pytest.approx(ae["values"]["display"]["estimate"])
    assert {r["field"] for r in store.extractions(ps, 1) if r["id"] in ae["values"]["row_ids"]} == set(AE_FIELDS)
    assert stats[0]["text"] != stats[1]["text"]
    assert _analysis_deviations(ps) == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures  # V-STATS recomputes each with its mapping
    # the gate checks every pre-specified outcome: one neither pooled nor fallen back is named (here the frozen
    # protocol's projection is edited behind the stage's back)
    death = OutcomeSpec(name="mortality", fields={r: f"m_{r}" for r in BINARY_FIELDS})
    meta3 = META2.model_copy(update={"outcomes": [TB, AE, death]})
    body = store.frozen_protocol(ps)["body"] | {"synthesis_plan": meta3.model_dump()}
    ps.db.execute("UPDATE protocols SET body=? WHERE status='frozen'", (json.dumps(body),))
    assert w.gate(ps, 1).failures == [
        "the pre-specified outcome 'mortality' was neither pooled nor fell back with a deviation"]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_protocol_without_outcome_field_mappings_falls_back(env):
    _, ps, _ = env
    _enable_stats()
    legacy = SynthesisPlan.model_validate({**META.model_dump(), "outcomes": ["tuberculosis"]})  # the round-2 shape
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=legacy)
    ex = _exec()
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not ex.of("arguer", "PoolingPlan") and artifacts(ps, "synthesize", 1) == []
    assert _analysis_deviations(ps) == [
        ("the protocol maps no extraction fields to the measure's roles for outcome 'tuberculosis' "
        "(synthesis_plan.outcomes[].fields)")]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_fewer_than_two_computable_studies_fall_back(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, {"Aronson": BCG["Aronson"], "Median": {**BCG["Ferguson"], "events_treatment": "not_reported"}},
                 schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert artifacts(ps, "synthesize", 1) == []
    (reason,) = _analysis_deviations(ps)
    assert "fewer than two studies with a computable effect (1)" in reason
    assert [u["item_id"] for u in synthesis_limitations(ps, 1)["uncomputable"]] == [ids["Median"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_study_with_two_values_for_a_field_is_left_out_of_the_pool(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    row = next(r for r in store.extractions(ps, 1, item_id=ids["Rosenthal"]) if r["field"] == "events_treatment")
    store.add_extraction(ps, 1, ids["Rosenthal"], "events_treatment", 5, row["note_id"], True, actor="tester")
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    assert stt["values"]["item_ids"] == [ids["Aronson"], ids["Ferguson"]] and stt["values"]["display"]["k"] == 2
    assert synthesis_limitations(ps, 1)["conflicting"] == [
        {"outcome": "tuberculosis", "measure": "RR", "item_id": ids["Rosenthal"],
         "reason": "two values for events_treatment"}]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_stats_invalidated_before_approval_falls_back(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    save_settings(load_settings().model_copy(update={"stats_validated": None}))
    out = w.on_answer(_ctx(env, ex), _answer(ps, out.checkpoint_id, {"decisions": {"0": "pool"}}))
    assert out.status == "done", out.reason
    assert artifacts(ps, "synthesize", 1) == []
    (reason,) = _analysis_deviations(ps)
    assert STATS_NOT_VALIDATED in reason and "tuberculosis" in reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_degrade_narrows_an_overstated_finding(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin was studied in some older people."
    ex = _exec(narratives=[narrative(text="Aspirin was always studied in older people.")],
               narrowed=[{"text": narrower, "kind": "fact"}])
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    (claim,) = _claims(ps)
    assert claim["status"] == "supported"
    g = w.gate(ps, 1)
    assert any("V-HEDGE overstated" in f for f in g.failures)
    out = w.degrade(ctx, g.failures)
    assert out.status == "done" and f"{claim['id']} narrowed" in out.reason
    old, new = _claims(ps)
    assert "V-HEDGE overstated" in old["withdrawn_reason"] and new["text"] == narrower
    assert ex.of("arguer", "NarrowedClaim")[0]["inputs"]["why"].startswith("V-HEDGE overstated")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_stale_stats_or_unrecorded_fallback_fail_the_gate(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    votes = {("judge", "VoteOut"): [{"choice": "pool", "reason": "ok"}],
             ("judge_2", "VoteOut"): [{"choice": "pool", "reason": "ok"}]}
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=votes)
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    row = stt["values"]["row_ids"][0]
    store.mark_extraction(ps, row, "missing: changed", actor="tester")
    failures = w.gate(ps, 1).failures
    assert any(f"V-STATS mismatch {stt['id']}" in f for f in failures)
    assert math.isfinite(stt["values"]["display"]["estimate"])


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_degrade_handles_an_open_priority_1_objection(env):
    from rk.argument import ObjectionIn

    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin was studied in some older people."
    ex = _exec(narrowed=[{"text": narrower, "kind": "fact"}])
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    (claim,) = _claims(ps)
    svc = ArgumentService(ps)
    oid = svc.add_objection(ObjectionIn(target_id=claim["id"], category="counterexample", priority=1,
                                        text="Study B enrolled people under 65."), actor="skeptic")
    g = w.gate(ps, 1)
    assert g.failures == [f"{claim['id']}: open priority-1 objection {oid}"]  # the only problem
    out = w.degrade(ctx, g.failures)
    assert out.status == "done" and f"{claim['id']} narrowed" in out.reason
    why = ex.of("arguer", "NarrowedClaim")[0]["inputs"]["why"]
    assert why == f"open priority-1 objection {oid}: Study B enrolled people under 65."
    old, new = _claims(ps)
    assert old["status"] == "withdrawn" and new["text"] == narrower and new["status"] == "supported"
    (o,) = svc.list_objections(target_id=old["id"])
    assert o["status"] == "conceded" and new["id"] in o["note"]
    assert [c["objection_id"] for c in synthesis_limitations(ps, 1)["claims"]["conceded"]] == [oid]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_judges_of_a_rewrite_exclude_the_original_and_the_rewrite_vendors(env):
    from rk.argument import ClaimIn, EvidenceIn
    from rk.config import DEFAULT_DRIVERS
    from rk.llm.drivers import DriverSpec
    from rk.llm.vendors import vendor_of

    _, ps, _ = env
    ids = _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
                 plan=SynthesisPlan(method="thematic"))
    spec = DriverSpec.parse(DEFAULT_DRIVERS["judge_2"])
    judge_2_vendor = vendor_of(spec.provider, spec.model)
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text=FINDING), generator_vendor=judge_2_vendor, actor="t", origin="synthesis")
    (note,) = [r["note_id"] for r in store.extractions(ps, 1, item_id=ids["A"])]
    svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[note]), actor="t")
    svc.set_claim_status(cid, "uncertain", verification_id=None)
    ex = _exec(narrowed=[{"text": "Aspirin was studied in some older people.", "kind": "fact"}])
    res = narrow_or_withdraw(_ctx(env, ex), cid)
    # judge_2 shares the original claim's vendor and judge_3 the Arguer's: one judge is left, too few to support
    assert [c["role"] for c in ex.calls if c["schema"] == "JudgeVerdict"] == ["judge"]
    assert res.status == "withdrawn" and res.new_claim_id


# ---- no cherry-picked pooling ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_subset_plan_gets_every_computable_study(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, {**dict(list(BCG.items())[:3]),
                       "Median": {**BCG["Hart"], "events_treatment": "not_reported"}}, schema=BINARY, plan=META)
    picked = [ids["Aronson"], ids["Ferguson"]]
    ex = _exec(plans=[_group(picked)], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (g,) = synthesis_notes(ps, 1, kind="plan")[0]["groups"]
    assert g["item_ids"] == [*picked, ids["Rosenthal"]] and g["added_by_kernel"] == [ids["Rosenthal"]]
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    assert stt["values"]["display"]["k"] == 3  # the uncomputable study is not added
    assert _analysis_deviations(ps) == []
    # the voters see the studies the kernel added, and that the planner did not assess them for comparability
    subject = next(c for c in ex.calls if c["schema"] == "VoteOut")["inputs"]["subject"]
    assert subject["added_by_kernel"] == ["Rosenthal 1952"] and subject["left_out"] == []
    assert "not assessed for comparability" in subject["note"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_plan_excluding_a_study_with_a_reason_records_a_deviation(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    plan = _group([ids["Aronson"], ids["Ferguson"]])
    plan["groups"][0]["excluded"] = [{"item_id": ids["Rosenthal"], "reason": "a quasi-randomised trial"}]
    ex = _exec(plans=[plan], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    subject = next(c for c in ex.calls if c["schema"] == "VoteOut")["inputs"]["subject"]
    assert subject["left_out"] == [{"study": "Rosenthal 1952", "reason": "a quasi-randomised trial"}]
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    assert stt["values"]["item_ids"] == [ids["Aronson"], ids["Ferguson"]]
    (reason,) = _analysis_deviations(ps)
    assert reason == ("the pooling plan leaves out of 'tuberculosis' (RR) Rosenthal 1952 (a quasi-randomised trial), "
                      "which has a computable effect")
    assert synthesis_limitations(ps, 1)["left_out"] == [
        {"outcome": "tuberculosis", "measure": "RR", "item_id": ids["Rosenthal"],
         "reason": "a quasi-randomised trial"}]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_blank_exclusion_reason_counts_as_none(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    plan = _group([ids["Aronson"], ids["Ferguson"]])
    plan["groups"][0]["excluded"] = [{"item_id": ids["Rosenthal"], "reason": "  \n "}]
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec(plans=[plan], votes=_votes_pool()))).status == "done"
    (g,) = synthesis_notes(ps, 1, kind="plan")[0]["groups"]
    assert g["added_by_kernel"] == [ids["Rosenthal"]] and g["left_out"] == []
    assert _analysis_deviations(ps) == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_split_outcome_pools_one_group_only(env):
    _, ps, _ = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    first = _group([ids["Aronson"], ids["Ferguson"]])["groups"][0]
    second = _group([ids["Rosenthal"]])["groups"][0] | {"outcome": " Tuberculosis "}
    ex = _exec(plans=[{"groups": [first, second]}], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (stt,) = [a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt"]
    assert stt["values"]["display"]["k"] == 3  # the first group, with the study the plan split off
    (reason,) = _analysis_deviations(ps)
    assert reason == ("pooling of 'tuberculosis' (RR) not done: a second group for the outcome; only one group per "
                      "pre-specified outcome and measure is pooled (group 0)")
    assert len([c for c in ex.calls if c["schema"] == "VoteOut" and c["role"] == "judge"]) == 1
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_outcome_names_match_after_case_and_space_normalisation(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _two([s["item_id"] for s in inputs["studies"]], ["  TUBERCULOSIS\n"])],
               votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (claim,) = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    assert claim["scope_limits"][0] == "outcome: tuberculosis"  # the protocol's spelling
    assert _analysis_deviations(ps) == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_protocol_frozen_in_the_plan_5a_shape_falls_back(env):
    _, ps, _ = env
    _enable_stats()
    old = SynthesisPlan(method="meta_analysis", effect_measure="RR", model="random effects (DerSimonian-Laird)")
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=old)  # frozen without check_protocol's typed fields
    ex = _exec()
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not ex.of("arguer", "PoolingPlan") and artifacts(ps, "synthesize", 1) == []
    assert _analysis_deviations(ps) == ["the protocol pre-specifies no pooling model (synthesis_plan.pool_model)"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_conceded_objections_on_edges_into_the_claim_reach_the_limitations(env):
    from rk.argument import ClaimIn, EdgeIn, ObjectionIn
    from rk.production.claims import claim_limitations

    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(narrowed=[{"text": "Aspirin was studied in some older people.", "kind": "fact"}])
    ctx = _ctx(env, ex)
    assert SynthesizeWorker().run(ctx).status == "done"
    (claim,) = _claims(ps)
    svc = ArgumentService(ps)
    reason = svc.add_claim(ClaimIn(text="Trials enrolled them."), generator_vendor=None, actor="t")
    edge = svc.add_edge(EdgeIn(from_id=reason, to_id=claim["id"], type="reason_of"), actor="t")
    oid = svc.add_objection(ObjectionIn(target_id=edge, category="soundness", priority=1, text="Not a reason."),
                            actor="skeptic")
    assert narrow_or_withdraw(ctx, claim["id"]).status == "narrowed"
    assert [(c["objection_id"], c["target_id"]) for c in claim_limitations(ps, [claim["id"]])["conceded"]] == [
        (oid, edge)]
