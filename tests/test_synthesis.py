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
from rk.discovery.executor import RateCapped
from rk.fulltext import latest_snapshot
from rk.library.models import Creator, Ids, ItemIn
from rk.llm.types import TaskResult
from rk.notes import NoteIn, add_note
from rk.orchestrator.state import (
    current_stage,
    degradations,
    edge_rows,
    latest_attempt,
    record_attempt,
    record_passed,
    record_ran,
    stage_row,
)
from rk.production import store
from rk.production.artifacts import artifact, artifacts
from rk.production.claims import narrow_or_withdraw, review_listing
from rk.production.extraction import reread_request
from rk.production.models import ExtractionField, OutcomeSpec, SynthesisPlan
from rk.production.protocol import protocol_prereg_ok
from rk.production.synthesis import (
    STATS_NOT_VALIDATED,
    SynthesizeWorker,
    options_text,
    pool_options,
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
    stage_run,
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


def _setup(env, studies: dict[str, dict], *, schema, plan, abstract: frozenset = frozenset(),
           text_only: dict[str, dict] | None = None) -> dict[str, str]:
    """Freeze a protocol with ``schema`` and ``plan``, include ``studies`` (title -> {field: value}) in a passed
    search_screen and extract them in a passed read_extract attempt 1, each value with a verified quote note (from the
    full text; from the abstract for the titles in ``abstract``). ``text_only`` (title -> {field: value}): values the
    study's text states but its extraction lacks. Returns title -> item id."""
    _, ps, lib = env
    pid = store.add_protocol(ps, 1, a_protocol(extraction_schema=schema, synthesis_plan=plan, prisma_p=SR_ITEMS),
                             version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    ids = {}
    for i, (title, values) in enumerate(studies.items()):
        stated = {**values, **(text_only or {}).get(title, {})}
        text = "Methods section. " + " ".join(f"The {f} was {v}." for f, v in stated.items() if v != "not_reported")
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


class CapOnce(Exec):
    """``Exec`` whose ``n``-th call of ``at`` (role, schema name) hits the driver's hourly cap (``RateCapped``), once."""

    def __init__(self, script, *, at: tuple[str, str], n: int = 1, **kw):
        super().__init__(script, **kw)
        self.at: tuple[str, str] | None = at
        self.n = n

    def run(self, role, instructions, inputs, output_schema, **kw):
        if self.at == (role, output_schema.__name__) and len(self.of(*self.at)) + 1 == self.n:
            self.at = None
            raise RateCapped("codex_cli", 60.0)
        return super().run(role, instructions, inputs, output_schema, **kw)


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
    assert call["inputs"]["subject"]["pool_options"] == {"model": "DL", "hksj": "auto", "pi_df": "k-2", "level": 0.95,
                                                         "min_studies": 2}
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
    # P1: the uncertain finding is narrowed and verified again before the attempt ends (M8), never voted up
    old, new = _claims(ps)
    assert old["status"] == "withdrawn" and new["narrowed_from"] == old["id"] and new["status"] == "supported"
    assert new["text"] == narrower and new["evidence"][0]["note_ids"] == old["evidence"][0]["note_ids"]
    assert "V-SUPPORT uncertain" in old["withdrawn_reason"]
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
    assert SynthesizeWorker().run(ctx).status == "done"  # P1 narrows the uncertain finding at once
    (n,) = synthesis_notes(ps, 1, kind="narrowed")
    assert n["status"] == "withdrawn" and n["new_claim_id"]
    old, new = _claims(ps)
    assert old["status"] == new["status"] == "withdrawn"
    assert n["new_claim_id"] in old["withdrawn_reason"] and "not supported" in new["withdrawn_reason"]
    with pytest.raises(ValueError, match="withdrawn"):
        narrow_or_withdraw(ctx, old["id"])


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
    """The representativeness statement names the k included studies. Calibration (critical review rerun): checked
    only by the gate, a missing one failed the stage after you had answered on the synthesis claims; it is checked
    when the narrative comes in, asked for once more naming the problem, and still wrong, the attempt stops before
    any claim."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}, "C": {"design": "rct"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    w = SynthesizeWorker()
    ex = _exec(narratives=[narrative(representativeness=""), narrative()])
    assert w.run(_ctx(env, ex, 1)).status == "done"
    first, again = (c["inputs"] for c in ex.of("arguer", "NarrativeSynthesis"))
    assert first["included_k"] == 3 and "selection_basis" in first and "guidance" not in first
    assert "no representativeness statement: it must name the 3 included studies" in again["guidance"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures

    vague = narrative(representativeness="The included studies were selected by the eligibility criteria.")
    out = w.run(_ctx(env, _exec(narratives=[vague, vague]), 2))
    assert out.status != "done" and "does not name the 3 included studies" in out.reason
    assert _claims(ps, 2) == []


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_systematic_narrative_needs_every_swim_item(env):
    _, ps, _ = env
    _setup(env, dict(list(BCG.items())[:2]), schema=BINARY, plan=SynthesisPlan(method="narrative_swim"))
    w = SynthesizeWorker()
    out = w.run(_ctx(env, _exec(narratives=[narrative(swim=False), narrative(swim=False)])))
    assert out.status != "done" and "SWiM items missing" in out.reason and _claims(ps) == []


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
    # an effect's numbers come from the study's text, never from its bibliographic record (a record field)
    rec = [*BINARY, ExtractionField(name="pub_year", description="Year", type="number", source="record")]
    plan = META.model_copy(update={"outcomes": [OutcomeSpec(name=TB.name, fields={**TB.fields, "n_control": "pub_year"})]})
    assert (f"synthesis_plan.outcomes: outcome {TB.name!r}: n_control -> 'pub_year' is a record field; an effect's "
            "numbers come from the study's text") in check_protocol(
        a_protocol(synthesis_plan=plan, prisma_p=SR_ITEMS, extraction_schema=rec), "systematic_review")
    # a round-2 protocol's plain outcome names still load (with no field mapping), and fail the check
    legacy = SynthesisPlan.model_validate({**META.model_dump(), "outcomes": ["tuberculosis"]})
    assert legacy.outcomes == [OutcomeSpec(name="tuberculosis", fields={})]
    assert any("maps no field to" in f for f in check_protocol(a_protocol(synthesis_plan=legacy, prisma_p=SR_ITEMS),
                                                               "systematic_review"))
    with pytest.raises(ValidationError):
        SynthesisPlan(method="meta_analysis", hksj="yes")
    with pytest.raises(ValidationError):
        SynthesisPlan(method="meta_analysis", pool_model="ML")


def _outcome_failures(*outcomes: OutcomeSpec) -> list[str]:
    from rk.production.protocol import check_protocol

    plan = META.model_copy(update={"outcomes": list(outcomes)})
    return check_protocol(a_protocol(synthesis_plan=plan, prisma_p=SR_ITEMS, extraction_schema=BINARY_AE),
                          "systematic_review")


def test_outcome_fields_are_distinct():
    dup = OutcomeSpec(name="tuberculosis", fields={**TB.fields, "n_control": "n_treatment"})
    assert ("synthesis_plan.outcomes: outcome 'tuberculosis' maps 'n_treatment' to more than one role "
            "(n_treatment, n_control)") in _outcome_failures(dup)


def test_outcome_numerators_are_not_shared():
    # an outcome's events may not be another outcome's; the arm sizes may be shared
    shares_events = OutcomeSpec(name="harm", fields={**AE.fields, "events_treatment": "events_treatment"})
    assert ("synthesis_plan.outcomes: outcomes 'tuberculosis' and 'harm' share the numerator field "
            "'events_treatment' (only n_* fields may be shared)") in _outcome_failures(TB, shares_events)
    shares_sizes = OutcomeSpec(name="harm", fields={**AE.fields, "n_treatment": "n_treatment",
                                                    "n_control": "n_control"})
    assert _outcome_failures(TB, shares_sizes) == []


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_gate_checks_the_pooled_field_map_against_the_frozen_protocol(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, BCG_AE, schema=BINARY_AE, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # the frozen protocol's projection now maps tuberculosis to other fields: the pooled group no longer matches
    remapped = META.model_copy(update={"outcomes": [OutcomeSpec(name="tuberculosis", fields=AE.fields)]})
    body = store.frozen_protocol(ps)["body"] | {"synthesis_plan": remapped.model_dump()}
    ps.db.execute("UPDATE protocols SET body=? WHERE status='frozen'", (json.dumps(body),))
    assert ("group 0 'tuberculosis' (RR) was pooled from other fields than the frozen protocol maps to it "
            "(events_treatment = events_treatment, n_treatment = n_treatment, events_control = events_control, "
            "n_control = n_control; the protocol: events_treatment = ae_events_treatment, "
            "n_treatment = ae_n_treatment, events_control = ae_events_control, n_control = ae_n_control)"
            in w.gate(ps, 1).failures), w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_gate_checks_the_figure_field_map_too(env):
    """The forest plot's ``field_map`` is checked against the frozen protocol as the statistic's is."""
    _, ps, _ = env
    _enable_stats()
    _setup(env, BCG_AE, schema=BINARY_AE, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    (fig,) = [a["id"] for a in artifacts(ps, "synthesize", 1) if a["kind"] == "fig"]
    ps.db.execute("""UPDATE artifacts SET "values"=json_set("values", '$.field_map', json(?)) WHERE id=?""",
                  (json.dumps(AE.fields), fig))
    assert ("group 0 'tuberculosis' (RR) was pooled from other fields than the frozen protocol maps to it "
            "(events_treatment = ae_events_treatment, n_treatment = ae_n_treatment, "
            "events_control = ae_events_control, n_control = ae_n_control; the protocol: "
            "events_treatment = events_treatment, n_treatment = n_treatment, events_control = events_control, "
            "n_control = n_control)" in w.gate(ps, 1).failures), w.gate(ps, 1).failures


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
    assert "fewer than 2 studies with a computable effect (1; protocol minimum)" in reason
    assert [u["item_id"] for u in synthesis_limitations(ps, 1)["uncomputable"]] == [ids["Median"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_min_studies_is_typed_and_defaults_to_two():
    from pydantic import ValidationError

    assert SynthesisPlan(method="meta_analysis").min_studies == 2
    assert SynthesisPlan.model_validate({"method": "meta_analysis"}).min_studies == 2  # frozen before the field
    assert SynthesisPlan(method="meta_analysis", min_studies=3).min_studies == 3
    for bad in (1, 0, "3", 2.5, None):
        with pytest.raises(ValidationError):
            SynthesisPlan(method="meta_analysis", min_studies=bad)


def test_min_studies_is_shown_with_the_pool_options_and_in_the_protocol():
    from rk.production.protocol import plan_value

    assert options_text(pool_options(META.model_copy(update={"min_studies": 3}).model_dump()), 3).endswith(
        "95% intervals, at least 3 studies with a computable effect")
    assert plan_value("min_studies", 3, "meta_analysis") == "at least 3 studies with a computable effect"
    assert plan_value("min_studies", 2, "narrative_swim") is None  # a meta-analysis's option only


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_fewer_included_studies_than_the_protocol_minimum_are_not_comparable(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:2]), schema=BINARY, plan=META.model_copy(update={"min_studies": 3}))
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert artifacts(ps, "synthesize", 1) == [] and not ex.of("judge", "VoteOut")
    (plan,) = synthesis_notes(ps, 1, kind="plan")
    assert (plan["groups"][0]["comparable"], plan["groups"][0]["reason"]) == (
        False, "fewer than 3 included studies (2; protocol minimum)")
    (reason,) = _analysis_deviations(ps)
    assert "fewer than 3 included studies (2; protocol minimum)" in reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_fewer_computable_studies_than_the_protocol_minimum_fall_back(env):
    _, ps, _ = env
    _enable_stats()
    studies = {**dict(list(BCG.items())[:2]), "Median": {**BCG["Rosenthal"], "events_treatment": "not_reported"}}
    _setup(env, studies, schema=BINARY, plan=META.model_copy(update={"min_studies": 3}))
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (vote,) = ex.of("judge", "VoteOut")
    assert vote["inputs"]["subject"]["pool_options"]["min_studies"] == 3  # the vote is shown the minimum
    assert artifacts(ps, "synthesize", 1) == []  # two computable studies would pool under the default minimum
    (reason,) = _analysis_deviations(ps)
    assert ("pooling of 'tuberculosis' (RR) not done: fewer than 3 studies with a computable effect (2; protocol "
            "minimum)") in reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_gate_checks_every_pooled_k_against_the_protocol_minimum(env):
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    body = store.frozen_protocol(ps)["body"]
    del body["synthesis_plan"]["min_studies"]  # a protocol frozen before the field: the minimum is 2
    ps.db.execute("UPDATE protocols SET body=? WHERE status='frozen'", (json.dumps(body),))
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    body["synthesis_plan"]["min_studies"] = 4
    ps.db.execute("UPDATE protocols SET body=? WHERE status='frozen'", (json.dumps(body),))
    assert ("group 0 'tuberculosis' (RR) was pooled from 3 studies, fewer than the protocol minimum of 4"
            in w.gate(ps, 1).failures), w.gate(ps, 1).failures


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
def test_p1_narrows_an_overstated_finding_and_degrade_finds_nothing_left(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin was studied in some older people."
    ex = _exec(narratives=[narrative(text="Aspirin was always studied in older people.")],
               narrowed=[{"text": narrower, "kind": "fact"}])
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    old, new = _claims(ps)
    assert "V-HEDGE overstated" in old["withdrawn_reason"] and new["text"] == narrower
    assert ex.of("arguer", "NarrowedClaim")[0]["inputs"]["why"].startswith("V-HEDGE overstated")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    out = w.degrade(ctx, [])
    assert out.status == "done" and "nothing to narrow" in out.reason


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


# ---- fix wave 1: resuming an interrupted attempt in place ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_interrupted_attempt_resumes_in_place(env):
    """A rate cap at the P1 pooling vote (after the assessments and the pooling plan) interrupts attempt 1; the next
    step resumes that attempt: no study is assessed again, the plan is not asked for again, no claim is orphaned."""
    k, ps, lib = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    pool = [{"choice": "pool", "reason": "same PICO"}] * 2
    group = [lambda inputs: _group(s["item_id"] for s in inputs["studies"]) for _ in range(2)]
    ex = CapOnce(_exec(plans=group, narratives=[narrative(), narrative()],
                       votes={("judge", "VoteOut"): pool, ("judge_2", "VoteOut"): list(pool)}).script,
                 at=("judge", "VoteOut"))
    o, run_id = stage_run(k, ps, lib, "synthesize", ex)
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert o.step("demo", run_id, actor="tester") == "done"
    assert latest_attempt(ps, "synthesize") == 1
    assert [len(ex.of("arguer", s)) for s in ("AssessmentOut", "PoolingPlan", "NarrativeSynthesis")] == [1, 1, 1]
    assert len(synthesis_notes(ps, 1, kind="assessment")) == 3 and len(synthesis_notes(ps, 1, kind="plan")) == 1
    every = [c["id"] for c in ArgumentService(ps).list_claims(origin="synthesis")]
    assert sorted(every) == sorted(synthesis_claims(ps, 1))  # nothing orphaned
    assert len(artifacts(ps, "synthesize", 1)) == 2 and len(ps.db.execute("SELECT * FROM votes").fetchall()) == 1


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_pool_cut_short_by_a_crash_is_finished_not_made_again(env, monkeypatch):
    """A crash after the pool's artifacts and stat claim are stored (before its note): applying the answer again
    reuses them instead of pooling again, so no artifact or stat claim is orphaned."""
    import rk.production.synthesis as mod

    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    cp = _answer(ps, w.run(_ctx(env, ex)).checkpoint_id, {"decisions": {"0": "pool"}})
    real, calls = mod.check_stats, []

    def crash_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("crash after the stat claim")
        return real(*a, **kw)

    monkeypatch.setattr(mod, "check_stats", crash_once)
    with pytest.raises(RuntimeError, match="crash after"):
        w.on_answer(_ctx(env, ex), cp)
    assert w.on_answer(_ctx(env, ex), cp).status == "done"
    assert len(artifacts(ps, "synthesize", 1)) == 2
    assert len([c for c in _claims(ps) if c["kind"] == "stat_result"]) == 1
    stat = [c for c in ArgumentService(ps).list_claims(origin="synthesis") if c["kind"] == "stat_result"]
    assert len(stat) == 1 and w.gate(ps, 1).ok, w.gate(ps, 1).failures


# ---- fix wave 2: the P2 review of every claim that fails the gate (I3, c) ----
OTHER = "Aspirin was studied in children."


def _two_findings(second: str):
    """The narrative with FINDING (both studies' notes) and ``second`` (study A's notes)."""
    def out(inputs):
        n = narrative()(inputs)
        n["findings"].append({"text": second, "kind": "fact",
                              "evidence": [{"note_ids": [inputs["studies"][0]["notes"][0]["id"]]}]})
        return n
    return out


def _judges_on(word: str, label: str, n: int = 6) -> dict:
    """Judges labelling a claim containing ``word`` with ``label``, every other claim supported."""
    def judge(inputs):
        return {"label": label if word in inputs["claim"] else "supported",
                "quoted_spans": [inputs["evidence"].split("\n\n")[0]]}
    return {("judge", "JudgeVerdict"): [judge] * n, ("judge_2", "JudgeVerdict"): [judge] * n}


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_unsupported_finding_is_withdrawable_and_the_gate_passes(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(narratives=[_two_findings(OTHER)], judges=_judges_on("children", "unsupported"))
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    bad = next(c["id"] for c in _claims(ps) if c["text"] == OTHER)
    assert cp["kind"] == "uncertain_support" and cp["options"]["claim_ids"] == [bad]
    assert cp["options"]["claim_choices"] == {bad: ["supported", "withdraw"]}  # only its V-SUPPORT status fails
    assert any("V-SUPPORT unsupported" in p for p in cp["options"]["problems"][bad])
    assert "V-SUPPORT unsupported" in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {bad: "withdraw"}}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_overstated_finding_can_only_be_withdrawn(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    overstated = "Aspirin was proven safe in every older person."
    ex = _exec(narratives=[_two_findings(overstated)])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    bad = next(c["id"] for c in _claims(ps) if c["text"] == overstated)
    assert ArgumentService(ps).get_claim(bad)["status"] == "supported"
    assert cp["options"]["claim_choices"] == {bad: ["withdraw"]}
    assert any("V-HEDGE overstated" in p for p in cp["options"]["problems"][bad])
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {bad: "supported"}}))
    assert out.status == "waiting_human" and out.checkpoint_id != cp["id"]
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert "could not be applied" in cp["prompt"] and "withdraw" in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {bad: "withdraw"}}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_review_choices_follow_the_claim_problems():
    from rk.production.claims import review_choices
    from rk.verify.support import ClaimProblem

    status = ClaimProblem("status", "status uncertain (not supported)", "V-SUPPORT uncertain")
    hedge = ClaimProblem("hedge", "V-HEDGE overstated", "V-HEDGE overstated: level 3")
    stats = ClaimProblem("stats", "V-STATS mismatch stt_1", "V-STATS mismatch stt_1", ("stt_1",))
    objection = ClaimProblem("objection", "open priority-1 objection obj_1", "open priority-1 objection obj_1: x")
    assert review_choices([status]) == ["supported", "withdraw"]
    for problems in ([hedge], [stats], [status, stats], [objection], [status, hedge]):
        assert review_choices(problems) == ["withdraw"], problems
    assert review_choices([status], structural=True) == ["withdraw"]


# ---- fix wave 2: your way back to read_extract (a) ----
def _bcg2(env, **kw) -> dict[str, str]:
    _enable_stats()
    return _setup(env, dict(list(BCG.items())[:2]), schema=BINARY, plan=META, **kw)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_pooling_approval_back_goes_to_read_extract_naming_studies(env):
    _, ps, _ = env
    ids = _bcg2(env)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    w = SynthesizeWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    assert cp["kind"] == "pooling_approval" and "back" in cp["options"]["decisions"]
    assert "back to read_extract" in cp["prompt"]
    assert [x["item_id"] for x in cp["options"]["studies"]] == [ids["Aronson"], ids["Ferguson"]]
    for bad in ({"decision": "back", "studies": ["itm_unknown"]}, {"decision": "back", "fields": ["dose"]},
                {"decision": "back", "studies": "Aronson"}):
        out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], bad))
        assert out.status == "waiting_human", bad
        cp = get_checkpoint(ps, out.checkpoint_id)
        assert cp["kind"] == "pooling_approval" and "could not be applied" in cp["prompt"], bad
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {
        "decision": "back", "comment": "Table 3 gives the arm sizes.", "studies": [ids["Ferguson"]],
        "fields": ["n_control"]}))
    assert (out.status, out.to) == ("back_edge", "read_extract")
    assert "Ferguson 1951" in out.reason and "n_control" in out.reason and "Table 3 gives the arm sizes." in out.reason
    (note,) = synthesis_notes(ps, 1, kind="back_edge")
    assert note["by"] == "human" and note["version"] == 1
    assert note["studies"] == [{"item_id": ids["Ferguson"], "label": "Ferguson 1951", "fields": ["n_control"]}]
    assert artifacts(ps, "synthesize", 1) == []  # nothing pooled


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_uncertain_support_back_reads_every_study_again(env):
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = _exec(judges=_judges("partial"))
    w = SynthesizeWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    assert cp["kind"] == "uncertain_support" and "back" in cp["options"]["decisions"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back"}))
    assert (out.status, out.to) == ("back_edge", "read_extract")
    assert "every included study" in out.reason
    (note,) = synthesis_notes(ps, 1, kind="back_edge")
    assert note["studies"] is None and note["by"] == "human"


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_your_back_uses_the_synthesize_read_extract_edge(env):
    k, ps, lib = env
    _bcg2(env)
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])])
    o, run_id = stage_run(k, ps, lib, "synthesize", ex)
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    (cp,) = [c for c in list_checkpoints(ps) if c["kind"] == "pooling_approval"]
    answer_checkpoint(ps, cp["id"], {"decision": "back", "comment": "Re-read the tables."}, answered_by="human",
                      actor="researcher")
    assert o.step("demo", run_id, actor="tester") == "running"
    assert edge_rows(ps)["synthesize->read_extract"]["count"] == 1
    assert current_stage(ps) == "read_extract"
    assert "Re-read the tables." in stage_row(ps, "read_extract")["guidance"]


# ---- fix wave 2: a study missing a value goes back on its own (b, M6) ----
def _reader(values: dict[str, dict]):
    """A reader extracting, per study title, the given values with their verified quotes ("The f was v.")."""
    def out(inputs):
        title = inputs["study"]["title"].removesuffix(" trial")
        return {"values": [{"field": f, "value": v, "quote": f"The {f} was {v}."} for f, v in values[title].items()]}
    return out


def _one_group(inputs) -> dict:
    """A pooling plan of one group with every study given."""
    return _group(s["item_id"] for s in inputs["studies"])


def _no_control(title: str) -> tuple[dict, dict]:
    """BCG's first two trials, ``title`` without its ``n_control`` row (its text still states it)."""
    studies = dict(list(BCG.items())[:2])
    studies[title] = {f: v for f, v in studies[title].items() if f != "n_control"}
    return studies, {title: {"n_control": BCG[title]["n_control"]}}


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_a_study_missing_a_value_goes_back_once_and_only_it_is_read_again(env):
    """(b): one study of a two-study group has no n_control row: synthesize goes back once (naming that study), only
    that study is read again, the other keeps its extraction (carried_from), and the next synthesize pools k=2."""
    k, ps, lib = env
    _enable_stats()
    studies, text_only = _no_control("Ferguson")
    ids = _setup(env, studies, schema=BINARY, plan=META, text_only=text_only)
    ex = _exec(plans=[_one_group, _one_group], votes=_votes_pool())
    ex.script[("reader", "ExtractOut")] = [_reader({"Ferguson": BCG["Ferguson"]})]
    o, run_id = stage_run(k, ps, lib, "synthesize", ex)
    assert o.step("demo", run_id, actor="tester") == "running"
    (note,) = synthesis_notes(ps, 1, kind="back_edge")
    assert note["by"] == "kernel" and note["version"] == 1
    assert [(x["item_id"], x["fields"]) for x in note["studies"]] == [(ids["Ferguson"], ["n_control"])]
    assert "Ferguson 1951" in note["reason"] and "n_control" in note["reason"]
    assert current_stage(ps) == "read_extract"
    assert o.step("demo", run_id, actor="tester") == "running"  # read_extract attempt 2 passes
    assert [c["inputs"]["study"]["title"] for c in ex.of("reader")] == ["Ferguson trial"]  # only it is read again
    assert {r["carried_from"] for r in store.extractions(ps, 2, item_id=ids["Aronson"])} == {1}
    assert {r["carried_from"] for r in store.extractions(ps, 2, item_id=ids["Ferguson"])} == {None}
    assert stage_row(ps, "read_extract")["passed_attempt"] == 2
    assert o.step("demo", run_id, actor="tester") == "done"  # synthesize attempt 2
    (pooled,) = synthesis_notes(ps, 2, kind="pooled")
    assert pooled["item_ids"] == [ids["Aronson"], ids["Ferguson"]] and pooled["uncomputable"] == []
    assert artifact(ps, pooled["stt"])["values"]["display"]["k"] == 2
    assert edge_rows(ps)["synthesize->read_extract"]["count"] == 1


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_a_study_is_asked_for_once_per_protocol_version(env):
    """(b, M6): the same study and fields are not asked for twice under one protocol version; a new version asks
    again."""
    _, ps, _ = env
    _enable_stats()
    studies, _ = _no_control("Ferguson")
    ids = _setup(env, studies, schema=BINARY, plan=META)
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec(plans=[_one_group]))).to == "read_extract"
    out = w.run(_ctx(env, _exec(plans=[_one_group], votes=_votes_pool()), 2))
    assert out.status == "done", out.reason  # asked already: Ferguson stays uncomputable, k < 2 falls back
    assert not synthesis_notes(ps, 2, kind="back_edge")
    assert any("fewer than 2 studies" in f["reason"] for f in synthesis_notes(ps, 2, kind="fallback"))
    pid = store.add_protocol(ps, 1, a_protocol(extraction_schema=BINARY, synthesis_plan=META, prisma_p=SR_ITEMS),
                             version=2, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    out = w.run(_ctx(env, _exec(plans=[_one_group]), 3))
    assert (out.status, out.to) == ("back_edge", "read_extract")
    (note,) = synthesis_notes(ps, 3, kind="back_edge")
    assert note["version"] == 2 and note["studies"][0]["item_id"] == ids["Ferguson"]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_your_back_rereads_a_study_whose_field_was_verified_not_reported(env):
    """The Khaled case: a study's n_control is a verified not_reported (its table was missing from the text layer),
    so the kernel does not go back; your back naming that study reads it again in full, the other keeps its
    extraction."""
    k, ps, lib = env
    _enable_stats()
    studies = dict(list(BCG.items())[:2])
    studies["Ferguson"] = {**studies["Ferguson"], "n_control": "not_reported"}
    ids = _setup(env, studies, schema=BINARY, plan=META, text_only={"Ferguson": {"n_control": 30}})
    ex = _exec(plans=[_one_group, _one_group])
    ex.script[("reader", "ExtractOut")] = [_reader({"Ferguson": {**BCG["Ferguson"], "n_control": 30}})]
    o, run_id = stage_run(k, ps, lib, "synthesize", ex)
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    assert not synthesis_notes(ps, 1, kind="back_edge")  # verified absence: no gap
    (cp,) = [c for c in list_checkpoints(ps) if c["kind"] == "pooling_approval"]
    answer_checkpoint(ps, cp["id"], {"decision": "back", "studies": [ids["Ferguson"]], "fields": ["n_control"],
                                     "comment": "Table 3 gives the arm sizes."}, answered_by="human", actor="researcher")
    assert o.step("demo", run_id, actor="tester") == "running"
    assert o.step("demo", run_id, actor="tester") == "running"  # read_extract attempt 2
    assert [c["inputs"]["study"]["title"] for c in ex.of("reader")] == ["Ferguson trial"]
    assert "Table 3 gives the arm sizes." in ex.of("reader")[0]["inputs"]["guidance"]
    assert {r["carried_from"] for r in store.extractions(ps, 2, item_id=ids["Aronson"])} == {1}
    assert [r["value"] for r in store.extractions(ps, 2, item_id=ids["Ferguson"]) if r["field"] == "n_control"] == [30]


# ---- fix wave 2: P1 narrows at once (M8) ----
@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_p1_narrows_a_failing_finding_at_once(env):
    """M8: in P1 the attempt narrows (or withdraws) a finding that fails the claim gate before it ends, as design does,
    instead of failing its gate and running the whole stage twice more first."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    overstated = "Aspirin was proven safe in every older person."
    narrower = "Aspirin was studied in some older people."
    ex = _exec(narratives=[_two_findings(overstated)], narrowed=[{"text": narrower, "kind": "fact"}])
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    by_text = {c["text"]: c for c in _claims(ps)}
    assert by_text[overstated]["status"] == "withdrawn" and "narrowed to" in by_text[overstated]["withdrawn_reason"]
    assert by_text[narrower]["status"] == "supported" and by_text[narrower]["narrowed_from"] == by_text[overstated]["id"]
    (n,) = synthesis_notes(ps, 1, kind="narrowed")
    assert (n["claim_id"], n["status"]) == (by_text[overstated]["id"], "narrowed")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert w.run(_ctx(env, ex)).status == "done" and len(ex.of("arguer", "NarrowedClaim")) == 1  # once each


# ---- fix wave 2, fix round 1 ----
class NoNarrative(Exec):
    """``Exec`` whose first ``n`` narrative tasks give no output (a failed task)."""

    def __init__(self, script, *, n: int = 1, **kw):
        super().__init__(script, **kw)
        self.n = n

    def run(self, role, instructions, inputs, output_schema, **kw):
        if output_schema.__name__ == "NarrativeSynthesis" and self.n > 0:
            self.n -= 1
            self.calls.append({"role": role, "schema": output_schema.__name__, "inputs": inputs})
            return TaskResult("tsk_x", False, None, "", "claude_cli", "x", 0.0, "invalid_output")
        return super().run(role, instructions, inputs, output_schema, **kw)


def _uncertain_judge(*words: str, n: int = 12) -> dict:
    def judge(inputs):
        label = "partial" if any(w in inputs["claim"] for w in words) else "supported"
        return {"label": label, "quoted_spans": [inputs["evidence"].split("\n\n")[0]]}
    return {("judge", "JudgeVerdict"): [judge] * n, ("judge_2", "JudgeVerdict"): [judge] * n}


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_p1_narrowing_resumes_after_a_rate_cap_in_the_rewrites_verification(env):
    """I-1: a rate cap while the judges verify the rewrite interrupts the attempt; the resumed attempt decides that
    rewrite (both withdrawn: it is not supported either) without a ValueError and asks for no second rewrite."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin was studied in some older people."
    ex = CapOnce({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [narrative()],
                  ("arguer", "NarrowedClaim"): [{"text": narrower, "kind": "fact"}],
                  **_uncertain_judge("older people")}, at=("judge", "JudgeVerdict"), n=2)
    w = SynthesizeWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert w.run(_ctx(env, ex)).status == "done"
    old, new = _claims(ps)
    assert old["status"] == new["status"] == "withdrawn" and new["narrowed_from"] == old["id"]
    assert len(ex.of("arguer", "NarrowedClaim")) == 1
    assert [n["claim_id"] for n in synthesis_notes(ps, 1, kind="narrowed")] == [old["id"]]


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_missing_narrative_asks_you_to_rerun_or_go_back(env):
    """I-2: in P2 a failed narrative task opens uncertain_support with no claim: a claims answer is refused, rerun
    needs a comment, back goes to read_extract."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = NoNarrative(_exec().script)
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["options"]["claim_ids"] == [] and cp["options"]["narrative_missing"]
    assert set(cp["options"]["decisions"]) == {"rerun", "back"} and "no narrative synthesis" in cp["prompt"]
    for bad in ({"claims": {}}, {"decision": "rerun"}):
        out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], bad))
        assert out.status == "waiting_human", bad
        cp = get_checkpoint(ps, out.checkpoint_id)
        assert cp["options"]["narrative_missing"] and "could not be applied" in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "rerun", "comment": "Group by design."}))
    assert (out.status, out.reason) == ("rerun", "Group by design.")


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_p1_missing_narrative_reruns_and_degrade_reports_the_narrowings_of_finish(env):
    """I-2 (P1 keeps the rerun) and minor 7: at the edge limit degrade finishes the synthesis, and the narrowings
    ``_finish`` does are in what degrade reports."""
    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    narrower = "Aspirin was studied in some older people."
    ex = NoNarrative(_exec(narratives=[narrative(text="Aspirin was always studied in older people.")],
                           narrowed=[{"text": narrower, "kind": "fact"}]).script)
    w = SynthesizeWorker()
    ctx = _ctx(env, ex)
    out = w.run(ctx)
    assert out.status == "rerun" and "no synthesis" in out.reason
    out = w.degrade(ctx, [out.reason])
    old, _new = _claims(ps)
    assert out.status == "done" and f"{old['id']} narrowed to" in out.reason, out.reason
    (deg,) = [d for d in degradations(ps) if d["stage"] == "synthesize"]
    assert old["id"] in deg["removed"]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_withdrawing_a_mismatched_stat_result_falls_back_and_passes_the_gate(env):
    """Minor 1: a stat_result is withdraw-only in your review (any problem); withdrawing it falls the group back
    (an analysis deviation) and the gate no longer checks its artifacts. P1: the narrowing does the same."""
    import rk.production.synthesis as mod

    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[_one_group])
    w = SynthesizeWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    assert w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decisions": {"0": "pool"}})).status == "done"
    (stat,) = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    store.mark_extraction(ps, stt["values"]["row_ids"][0], "missing: changed", actor="tester")
    assert any(f"V-STATS mismatch {stt['id']}" in f for f in w.gate(ps, 1).failures)
    listing = review_listing(ps, [stat["id"]])
    assert listing[stat["id"]]["choices"] == ["withdraw"]
    s = mod._load(ps)
    cid = w._open_uncertain(_ctx(env, ex), s, listing, replaces=cp["id"])
    out = w.on_answer(_ctx(env, ex), _answer(ps, cid, {"claims": {stat["id"]: "supported"}}))
    assert out.status == "waiting_human"  # supported is not one of its answers
    out = w.on_answer(_ctx(env, ex), _answer(ps, out.checkpoint_id, {"claims": {stat["id"]: "withdraw"}}))
    assert out.status == "done"
    assert any("pooling of 'tuberculosis' (RR) withdrawn" in r for r in _analysis_deviations(ps))
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"}], indirect=True)
def test_your_back_keeps_the_fields_you_name(env):
    """Minor 6: fields you name per study reach that study's re-read; fields you name without studies are added to
    the kernel's gap studies; a fields map naming a study you did not name is refused."""
    _, ps, _ = env
    _enable_stats()
    studies, _ = _no_control("Ferguson")
    ids = _setup(env, studies, schema=BINARY, plan=META)
    w = SynthesizeWorker()
    assert w.run(_ctx(env, _exec(plans=[_one_group]))).to == "read_extract"  # the kernel asked once already
    ex = _exec(plans=[_one_group])
    cp = get_checkpoint(ps, w.run(_ctx(env, ex, 2)).checkpoint_id)
    assert cp["kind"] == "pooling_approval" and "n_control" in cp["options"]["fields"]
    out = w.on_answer(_ctx(env, ex, 2), _answer(ps, cp["id"], {"decision": "back", "studies": [ids["Aronson"]],
                                                                 "fields": {ids["Ferguson"]: ["n_control"]}}))
    assert out.status == "waiting_human"
    out = w.on_answer(_ctx(env, ex, 2), _answer(ps, out.checkpoint_id, {"decision": "back",
                                                                          "fields": ["events_control"]}))
    assert out.to == "read_extract"
    (note,) = synthesis_notes(ps, 2, kind="back_edge")
    assert note["studies"] == [{"item_id": ids["Ferguson"], "label": "Ferguson 1951",
                                "fields": ["n_control", "events_control"], "outcome": "'tuberculosis' (RR)"}]
    assert "events_control" in out.reason


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_an_interrupted_targeted_reread_is_replaced_with_the_same_request(env):
    """Minor 8: the attempt reading only the named study is interrupted (a rate cap); the attempt that replaces it
    keeps the request and reads only that study."""
    k, ps, lib = env
    _enable_stats()
    studies, text_only = _no_control("Ferguson")
    ids = _setup(env, studies, schema=BINARY, plan=META, text_only=text_only)
    ex = CapOnce({**_exec(plans=[_one_group, _one_group], votes=_votes_pool()).script,
                  ("reader", "ExtractOut"): [_reader({"Ferguson": BCG["Ferguson"]})]}, at=("reader", "ExtractOut"))
    o, run_id = stage_run(k, ps, lib, "synthesize", ex)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"  # read_extract attempt 2 interrupted
    assert o.step("demo", run_id, actor="tester") == "running"  # attempt 3 replaces it
    assert stage_row(ps, "read_extract")["passed_attempt"] == 3
    assert reread_request(ps, 3) == {ids["Ferguson"]}
    assert [c["inputs"]["study"]["title"] for c in ex.of("reader")] == ["Ferguson trial"]
    assert {r["carried_from"] for r in store.extractions(ps, 3, item_id=ids["Aronson"])} == {1}


# ---- fix wave 2, fix round 2: a withdrawn stat_result always takes its pool out ----
def _pooled(env, ex) -> tuple[SynthesizeWorker, dict, dict]:
    """A pooled synthesize attempt 1 (P2: your approval; P1: the vote): the worker, the stat_result claim and the stt."""
    _, ps, _ = env
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    w = SynthesizeWorker()
    out = w.run(_ctx(env, ex))
    if out.status == "waiting_human":
        out = w.on_answer(_ctx(env, ex), _answer(ps, out.checkpoint_id, {"decisions": {"0": "pool"}}))
    assert out.status == "done", out.reason
    (stat,) = [c for c in _claims(ps) if c["kind"] == "stat_result"]
    stt = next(a for a in artifacts(ps, "synthesize", 1) if a["kind"] == "stt")
    return w, stat, stt


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review", "production_mode": "collab"},
                                 {"paper_type": "systematic_review"}], indirect=True)
def test_a_crash_after_withdrawing_a_stat_result_is_mended_on_resume(env):
    """Round 2: the stat_result is withdrawn and the attempt crashes before its pool is taken out: the gate refuses
    that state; the resumed attempt records the fallback and its deviation (once), and the gate passes."""
    _, ps, _ = env
    w, stat, _ = _pooled(env, _exec(plans=[_one_group], votes=_votes_pool()))
    ArgumentService(ps).withdraw_claim(stat["id"], reason="withdrawn before a crash", actor="tester")
    assert any("has no fallback" in f for f in w.gate(ps, 1).failures), w.gate(ps, 1).failures
    for _ in range(2):  # resumed twice: recorded once
        assert w.run(_ctx(env, _exec())).status == "done"
    fell = [n for n in synthesis_notes(ps, 1, kind="fallback") if "withdrawn" in n["reason"]]
    assert len(fell) == 1 and fell[0]["deviation_id"]
    assert sum("withdrawn" in r for r in _analysis_deviations(ps)) == 1
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_p1_withdraws_a_mismatched_stat_result_and_falls_back(env):
    """Round 2 minor 4: the P1 case of the stat_result withdrawal (V-STATS mismatch at the edge limit)."""
    _, ps, _ = env
    ex = _exec(plans=[_one_group], votes=_votes_pool())
    w, stat, stt = _pooled(env, ex)
    store.mark_extraction(ps, stt["values"]["row_ids"][0], "missing: changed", actor="tester")
    g = w.gate(ps, 1)
    assert not g.ok
    out = w.degrade(_ctx(env, ex), g.failures)
    assert f"{stat['id']} withdrawn" in out.reason, out.reason
    assert any("pooling of 'tuberculosis' (RR) withdrawn" in r for r in _analysis_deviations(ps))
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_argue_withdrawing_a_stat_result_takes_its_pool_out(env):
    """Round 2: argue (your review or its P1 degrade) withdraws a stat_result of synthesize's passed attempt and calls
    the reconcile on that attempt: the group falls back with its deviation."""
    from rk.production.synthesis import reconcile_withdrawn_pools

    k, ps, lib = env
    w, stat, _ = _pooled(env, _exec(plans=[_one_group], votes=_votes_pool()))
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    ArgumentService(ps).withdraw_claim(stat["id"], reason="withdrawn in argue", actor="tester")
    reconcile_withdrawn_pools(production_ctx(k, ps, lib, _exec(), stage="argue", attempt=3), 1)
    (fb,) = [n for n in synthesis_notes(ps, 1, kind="fallback") if "withdrawn in argue" in n["reason"]]
    assert fb["group"] == 0 and fb["deviation_id"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_argue_conflict_decision_dropping_a_stat_result_takes_its_pool_out(env):
    """Round 3: a conflict decided against the stat_result (P1 vote ``a_holds``) withdraws it in argue; the pool is
    taken out of the synthesize attempt argue argued on at once (fallback with its deviation), so both gates agree,
    and synthesize gets no ``narrowed`` note for a withdrawal it did not make."""
    from rk.production.argue import ArgueWorker

    k, ps, lib = env
    w, stat, _ = _pooled(env, _exec(plans=[_one_group], votes=_votes_pool()))
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    other = next(c for c in synthesis_claims(ps, 1) if c != stat["id"])
    conflict = {"claim_a": other, "claim_b": stat["id"], "why": "They disagree.", "round": 1, "index": 0}
    ctx = production_ctx(k, ps, lib, _exec(), stage="argue", attempt=1)
    ArgueWorker()._apply_conflict(ctx, conflict, "a_holds", by="vote", actor_kind="kernel", via={})
    assert ArgumentService(ps).get_claim(stat["id"])["status"] == "withdrawn"
    (fb,) = [n for n in synthesis_notes(ps, 1, kind="fallback") if "withdrawn" in n["reason"]]
    assert fb["group"] == 0 and fb["deviation_id"]
    assert not [n for n in synthesis_notes(ps, 1, kind="narrowed") if n["claim_id"] == stat["id"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_argue_going_back_with_no_finding_left_still_takes_withdrawn_pools_out(env):
    """Round 3: every finding, the stat_result among them, was withdrawn in argue and the reconcile never ran (a
    crash): argue goes back to synthesize (I4) and still takes the pool out of the attempt it argued on first."""
    from rk.production.argue import ArgueWorker

    k, ps, lib = env
    w, _, _ = _pooled(env, _exec(plans=[_one_group], votes=_votes_pool()))
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    svc = ArgumentService(ps)
    for cid in synthesis_claims(ps, 1):
        if svc.get_claim(cid)["status"] != "withdrawn":
            svc.withdraw_claim(cid, reason="withdrawn in argue", actor="tester")
    out = ArgueWorker().run(production_ctx(k, ps, lib, _exec(), stage="argue", attempt=1))
    assert out.status == "back_edge" and out.to == "synthesize", out.reason
    (fb,) = [n for n in synthesis_notes(ps, 1, kind="fallback") if "withdrawn" in n["reason"]]
    assert fb["deviation_id"]
    assert not any("has no fallback" in f for f in w.gate(ps, 1).failures), w.gate(ps, 1).failures


def test_a_request_covers_the_same_study_read_for_its_fields_or_in_full():
    """Round 2 minor 2: your back naming a study (without fields: a full re-read) or the kernel's request for more
    fields covers a later gap of that study; another field does not."""
    asked = SynthesizeWorker._asked
    assert asked({("itm_f", ())}, "itm_f", ["n_control"])
    assert asked({("itm_f", ("n_control", "events_control"))}, "itm_f", ["n_control"])
    assert not asked({("itm_f", ("events_control",))}, "itm_f", ["n_control"])
    assert not asked({("itm_a", ())}, "itm_f", ["n_control"])


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_a_review_of_another_shape_supersedes_the_open_one(env):
    """Round 2: the attempt's open review is reused only when its shape matches (no-narrative flag, listed claims)."""
    import rk.production.synthesis as mod

    _, ps, _ = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    w = SynthesizeWorker()
    first = w.run(_ctx(env, NoNarrative(_exec().script))).checkpoint_id
    s = mod._load(ps)
    assert w._open_uncertain(_ctx(env, _exec()), s, {}, missing="again") == first  # same shape: reused
    second = w._open_uncertain(_ctx(env, _exec()), s, {})  # a review (no narrative flag): another shape
    assert second != first and get_checkpoint(ps, first)["status"] == "superseded"


def _assess_dropping(*titles_left_out: str, mangle: bool = False):
    """An Arguer that leaves out the studies whose title starts with one of ``titles_left_out`` (``mangle``: answers
    them under a wrong id instead, a letter dropped)."""
    def out(inputs):
        got = assess()(inputs)["assessments"]
        for a, s in zip(got, inputs["studies"], strict=True):
            if s["title"].startswith(titles_left_out):
                a["item_id"] = a["item_id"][:-2] + a["item_id"][-1] if mangle else None
        return {"assessments": [a for a in got if a["item_id"]]}
    return out


@pytest.mark.parametrize("env", [{"paper_type": "critical_review"}], indirect=True)
def test_a_study_left_out_of_an_assessment_is_asked_again_before_any_claim(env):
    """Calibration (critical review rerun): the Arguer answered one study under an id with a letter dropped and, in
    the next attempt, left one out; the attempt went on to its claims and their uncertain_support checkpoint, and only
    the gate said "was not assessed", so the researcher's answers on the claims were thrown away. A study left out
    is asked for once more; still left out, the attempt stops before any claim."""
    _, ps, _ = env
    ids = _setup(env, {t: {"design": "cohort"} for t in ("Ames", "Brook", "Cole")}, schema=[DESIGN],
                 plan=SynthesisPlan(method="thematic"))
    ex = _exec()
    ex.script[("arguer", "AssessmentOut")] = [_assess_dropping("Cole", mangle=True), assess()]
    w = SynthesizeWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert [[s["item_id"] for s in c["inputs"]["studies"]] for c in ex.of("arguer", "AssessmentOut")] == [
        [ids["Ames"], ids["Brook"], ids["Cole"]], [ids["Cole"]]]
    assert {a["item_id"] for a in synthesis_notes(ps, 1, kind="assessment")} == set(ids.values())

    ex = _exec()
    ex.script[("arguer", "AssessmentOut")] = [_assess_dropping("Brook"), _assess_dropping("Brook")]
    out = w.run(_ctx(env, ex, attempt=2))
    assert out.status == "rerun" and f'study {ids["Brook"]}' in out.reason and "asked twice" in out.reason
    assert ex.of("arguer", "NarrativeSynthesis") == []  # no claim, so no checkpoint on them
