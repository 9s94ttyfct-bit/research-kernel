"""The protocol stage (Plan 5a Task 3): drafting by the Ideator and the Scout, the structural check per paper type, the
record-cap probe, P2 approval and the P1 vote, the freeze and deviations after screening began (G-PREREG input)."""

from __future__ import annotations

import json

import httpx
import pytest

from rk.canon import canonical_json, sha256_hex
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.config import Settings, save_settings
from rk.discovery.models import HypothesisIn, Intake, RQBody
from rk.discovery.sources import KernelSources
from rk.discovery.store import add_rq, freeze_rq, frozen_rq, intake_of, record_intake
from rk.net.http import BudgetExhausted, HttpClient, HttpError
from rk.orchestrator.engine import Orchestrator, StageOutcome, default_workers
from rk.orchestrator.state import latest_attempt, record_attempt, start_run
from rk.production import store
from rk.production.models import Protocol, SynthesisPlan
from rk.production.protocol import (
    VOTE_QUESTION,
    ProtocolWorker,
    check_protocol,
    counts_as_preregistration,
    probe_of,
    protocol_prereg_ok,
    render_protocol,
)
from rk.store.projections import rebuild
from rk.workspace import Kernel
from tests.discovery_fakes import FakeSources, FakeWorker, ScriptedExecutor
from tests.production_fakes import (
    a_protocol,
    make_frozen_project,
    production_ctx,
    votes_on,
)

T0 = 1_800_000_000.0
SR_ITEMS = {"rationale": "Guidelines stop at 80.", "objectives": "Estimate benefit and harm.",
            "selection_process": "Two screeners, title/abstract then full text.",
            "data_collection_process": "One reader extracts, a second checks.",
            "risk_of_bias": "RoB 2 for trials, ROBINS-I otherwise."}
QUERIES = ["aspirin stroke elderly", "aspirin primary prevention octogenarians", "antiplatelet stroke over 80"]


def _draft(*, prisma: bool = True, **overrides) -> dict:
    d = {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}],
                         "exclude": [{"id": "E1", "text": "Animal studies"}]},
         "extraction_schema": [{"name": "n", "description": "Participants", "type": "number", "unit": "people"},
                               {"name": "design", "description": "Study design", "type": "category",
                                "categories": ["rct", "cohort", "other"]}],
         "synthesis_plan": {"method": "narrative_swim"},
         "prisma_p": dict(SR_ITEMS) if prisma else {}}
    return {**d, **overrides}


def _strategy(**overrides) -> dict:
    return {"queries": list(QUERIES), "sources": ["openalex", "europepmc"], "date_from": 2000, "date_to": None,
            **overrides}


def _votes(*choices: tuple[str, str]) -> dict:
    return {("judge", "VoteOut"): [{"choice": a, "reason": f"judge: {a}"} for a, _ in choices],
            ("judge_2", "VoteOut"): [{"choice": b, "reason": f"judge_2: {b}"} for _, b in choices]}


def _executor(drafts: list[dict], strategies: list[dict] | None = None, votes: dict | None = None) -> ScriptedExecutor:
    return ScriptedExecutor({("ideator", "ProtocolDraftOut"): drafts,
                             ("scout", "SearchStrategyOut"): strategies or [_strategy() for _ in drafts],
                             **(votes or {})})


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _ctx(env, ex, *, attempt: int = 1, sources=None):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="protocol", attempt=attempt, sources=sources or FakeSources({}))


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


def _events(ps, type_: str) -> list:
    return [e for e in ps.log.iter() if e.type == type_]


# ---- structure per paper type ----
def test_sr_protocol_needs_prisma_p_items():
    body = a_protocol()  # PRISMA-P: only the rationale
    failures = check_protocol(body, "systematic_review")
    assert failures == [f"PRISMA-P item {i!r} is missing" for i in
                        ("objectives", "selection_process", "data_collection_process", "risk_of_bias")]
    assert check_protocol(body.model_copy(update={"prisma_p": SR_ITEMS}), "systematic_review") == []
    no_plan = body.model_copy(update={"prisma_p": SR_ITEMS, "synthesis_plan": None, "extraction_schema": []})
    assert check_protocol(no_plan, "systematic_review") == [  # each structural PRISMA-P item is reported once
        "extraction_schema has no field", "synthesis_plan is missing"]
    assert check_protocol(no_plan, "scoping_review") == ["extraction_schema has no field", "synthesis_plan is missing"]
    assert counts_as_preregistration("systematic_review") and counts_as_preregistration("scoping_review")


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_sr_draft_without_prisma_p_items_is_rerun(env):
    _, ps, _ = env
    ex = _executor([_draft(prisma=False)])
    out = ProtocolWorker().run(_ctx(env, ex))
    assert out.status == "rerun" and "PRISMA-P item 'rationale' is missing" in out.reason
    assert [c["role"] for c in ex.calls] == ["ideator", "scout"]  # no probe, no vote
    assert store.frozen_protocol(ps) is None


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_meta_analysis_draft_without_typed_fields_fails_the_gate_and_is_redrafted(env):
    """A Plan-5a-shaped meta-analysis plan (free-text model only) cannot be frozen: the stage reruns with the missing
    typed fields as its reason, and the redraft that fills them is frozen."""
    _, ps, _ = env
    old = _draft(synthesis_plan={"method": "meta_analysis", "effect_measure": "RR", "model": "random effects (DL)"})
    roles = ("events_treatment", "n_treatment", "events_control", "n_control")
    schema = [{"name": f"stroke_{r}", "description": r, "type": "number"} for r in roles]
    stroke = {"name": "stroke", "fields": {r: f"stroke_{r}" for r in roles}}
    typed = _draft(extraction_schema=schema,
                   synthesis_plan={"method": "meta_analysis", "effect_measure": "RR", "model": "random effects",
                                   "pool_model": "DL", "hksj": False, "outcomes": [stroke]})
    ex = _executor([old, typed], votes=_votes(("approve", "approve")))
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "rerun"
    assert "synthesis_plan.pool_model is required for a meta-analysis (REML, DL or FE)" in out.reason
    assert "synthesis_plan.outcomes is required for a meta-analysis" in out.reason
    g = w.gate(ps, 1)
    assert not g.ok and any("pool_model" in f for f in g.failures)
    assert w.run(_ctx(env, ex, attempt=2)).status == "done" and w.gate(ps, 2).ok
    plan = store.frozen_protocol(ps)["body"]["synthesis_plan"]
    assert (plan["pool_model"], plan["hksj"], plan["pi_df"], plan["outcomes"]) == ("DL", False, "ci", [stroke])
    text = "\n".join(render_protocol(ps, store.frozen_protocol(ps)))
    assert "- hksj: never (Wald intervals)" in text
    assert ("- outcomes: stroke (events_treatment = stroke_events_treatment, n_treatment = stroke_n_treatment, "
            "events_control = stroke_events_control, n_control = stroke_n_control)") in text
    # the pool options are shown only for a meta-analysis
    narrative = store.frozen_protocol(ps) | {"body": a_protocol(synthesis_plan=SynthesisPlan(
        method="narrative_swim", hksj=False, outcomes=[stroke])).model_dump(mode="json")}
    text = "\n".join(render_protocol(ps, narrative))
    assert "- method: narrative_swim" in text
    assert not any(f"- {k}:" in text for k in ("hksj", "pi_df", "pool_model", "outcomes"))


def test_light_protocol_for_critical_review(env):
    light = a_protocol(extraction_schema=[], synthesis_plan=None, prisma_p=None)
    for t in ("critical_review", "research_proposal"):
        assert check_protocol(light, t) == []
        assert not counts_as_preregistration(t)
    assert "PRISMA-P item 'rationale' is missing" in check_protocol(light, "systematic_review")
    no_selection = light.model_copy(update={"eligibility": light.eligibility.model_copy(update={"include": []})})
    assert check_protocol(no_selection, "critical_review") == ["eligibility has no include criterion"]
    no_search = light.model_copy(update={"search_strategy": light.search_strategy.model_copy(
        update={"queries": [" "], "sources": []})})
    assert check_protocol(no_search, "critical_review") == ["search_strategy has no query",
                                                             "search_strategy has no source"]
    unnamed = a_protocol()
    unnamed.extraction_schema[0].name = " "
    assert check_protocol(unnamed, "critical_review") == ["extraction field 1 has no name"]
    with pytest.raises(ValueError):
        check_protocol(light, "essay")

    # the worker: a light draft (no schema, plan or PRISMA-P) of a critical review is asked for approval (P1: voted)
    _, ps, _ = env
    ex = _executor([_draft(prisma=False, extraction_schema=[], synthesis_plan=None)],
                   votes=_votes(("approve", "approve")))
    out = ProtocolWorker().run(_ctx(env, ex))
    assert out.status == "done"
    frozen = store.frozen_protocol(ps)
    assert frozen["body"]["prisma_p"] is None and frozen["body"]["extraction_schema"] == []
    assert frozen["body"]["question"].startswith("I am studying aspirin over 80")


def test_novelty_claims_and_a_thin_strategy_are_rerun_reasons(env):
    draft = _draft(eligibility={"include": [{"id": "I1", "text": "The first study to test aspirin over 80"}],
                                "exclude": []})
    ex = _executor([draft], [_strategy(queries=["aspirin", "aspirin"], sources=["openalex", "scopus"])])
    out = ProtocolWorker().run(_ctx(env, ex))
    assert out.status == "rerun"
    assert "eligibility.include.I1 claims novelty" in out.reason
    assert "1 distinct queries (need at least 3)" in out.reason
    assert "source 'scopus' is not available" in out.reason and "1 usable sources (need at least 2)" in out.reason


def test_a_novelty_refusal_names_the_matched_words(env):
    # live run 2026-11-08: the rerun reason did not say which words were refused, so the Ideator added "it is not a
    # claim that the review is novel" to its rationale on every rerun and the stage looped on the same failure
    rationale = ("Within the searched scope no review pairs non-ICU settings with these agents. This is a statement "
                 "about what was not found; it is not a claim that the review is novel or the first study to ask.")
    ex = _executor([_draft(prisma_p={**SR_ITEMS, "rationale": rationale})], [_strategy()])
    out = ProtocolWorker().run(_ctx(env, ex))
    assert out.status == "rerun"
    assert out.reason == ('prisma_p.rationale claims novelty ("novel", "first study"): do not use these words, not '
                          "even negated or in a disclaimer; say what was not found within the searched scope")


def test_agents_cannot_set_the_record_cap(env):
    _, ps, _ = env
    ex = _executor([{**_draft(), "record_cap": 100_000}], [{**_strategy(), "record_cap": 100_000}],
                   votes=_votes(("approve", "approve")))
    assert ProtocolWorker().run(_ctx(env, ex)).status == "done"
    assert store.frozen_protocol(ps)["body"]["record_cap"] == 3000


# ---- P2 approval, P1 vote ----
@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_opens_protocol_approval_p1_votes(env):
    _, ps, _ = env
    ex = _executor([_draft()])
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    (row,) = store.protocols(ps, 1)
    assert cp["kind"] == "protocol_approval"
    assert cp["options"] == {"protocol_id": row["id"], "version": 1, "attempt": 1,
                             "choices": ["approve", "revise", "back"], "replaces": None}
    prompt = cp["prompt"]
    for text in ("Protocol version 1", "traceability only", "I am studying aspirin over 80", "Search strategy",
                 *QUERIES, "openalex, europepmc", "2000", "Record cap: 3000", "Record-cap probe (advisory): 0 records",
                 "Eligibility", "I1: People over 80", "E1: Animal studies", "Extraction schema",
                 "n (number, unit people): Participants", "design (category: rct | cohort | other): Study design",
                 "Synthesis plan", "narrative_swim", "PRISMA-P", "risk_of_bias: RoB 2 for trials",
                 '{"decision": "approve"}', '{"decision": "back"}'):
        assert text in prompt, text
    assert [c["role"] for c in ex.calls] == ["ideator", "scout"]  # P2 asks you: no vote

    # an answer that cannot be applied opens the same kind again, once
    bad = _answer(ps, cp["id"], {"decision": "maybe"})
    again = w.on_answer(_ctx(env, ex), bad)
    assert again.status == "waiting_human" and again.checkpoint_id != cp["id"]
    assert w.on_answer(_ctx(env, ex), bad).checkpoint_id == again.checkpoint_id
    cp2 = get_checkpoint(ps, again.checkpoint_id)
    assert cp2["options"]["replaces"] == cp["id"] and "'maybe' is not one of approve, revise, back" in cp2["prompt"]
    assert "comment" in w.on_answer(_ctx(env, ex), _answer(ps, cp2["id"], {"decision": "revise"})).reason

    # approve freezes, once
    cp3 = get_checkpoint(ps, list_checkpoints(ps, "open")[0]["id"])
    done = _answer(ps, cp3["id"], {"decision": "approve"})
    assert w.on_answer(_ctx(env, ex), done).status == "done"
    assert w.on_answer(_ctx(env, ex), done).status == "done"
    assert len(_events(ps, "protocol.frozen")) == 1
    frozen = _events(ps, "protocol.frozen")[0]
    assert (frozen.actor_kind, frozen.actor) == ("human", "researcher")
    assert w.gate(ps, 1).ok
    assert votes_on(ps, "protocol", row["id"]) == []

    # P1: the same stage votes instead of asking (here over the frozen version 1: a deviation)
    record_intake(ps, Intake(**{**intake_of(ps).model_dump(), "production_mode": "auto"}), actor="tester",
                  actor_kind="human")
    ex1 = _executor([_draft()], votes=_votes(("approve", "approve")))
    n_checkpoints = len(list_checkpoints(ps))
    assert w.run(_ctx(env, ex1, attempt=2)).status == "done"
    (p1,) = store.protocols(ps, 2)
    assert len(list_checkpoints(ps)) == n_checkpoints  # no checkpoint in P1
    assert [c["role"] for c in ex1.calls] == ["ideator", "scout", "judge", "judge_2"]
    assert votes_on(ps, "protocol", p1["id"])[0]["decision"] == "approve"
    assert store.frozen_protocol(ps)["id"] == p1["id"]
    assert [(d["from_version"], d["to_version"]) for d in store.deviations(ps)] == [(1, 2)]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_revise_reruns_and_back_goes_to_question(env):
    _, ps, _ = env
    ex = _executor([_draft(), _draft()])
    w = ProtocolWorker()
    cid = w.run(_ctx(env, ex)).checkpoint_id
    out = w.on_answer(_ctx(env, ex), _answer(ps, cid, {"decision": "revise", "comment": "Add a harm outcome."}))
    assert (out.status, out.reason) == ("rerun", "Add a harm outcome.")
    (draft,) = store.protocols(ps, 1)
    cid = w.run(_ctx(env, ex, attempt=2)).checkpoint_id
    assert "previous_draft" not in ex.calls[0]["inputs"]
    for call in ex.calls[2:4]:  # the attempt-2 Ideator and Scout revise the draft they were asked to revise
        assert call["role"] in ("ideator", "scout")
        assert call["inputs"]["previous_draft"] == {"version": 1, "body": draft["body"],
                                                    "record_probe": probe_of(ps, draft["id"])}
    out = w.on_answer(_ctx(env, ex, attempt=2), _answer(ps, cid, {"decision": "back", "comment": "RQ too broad"}))
    assert (out.status, out.to) == ("back_edge", "question") and "RQ too broad" in out.reason
    assert store.frozen_protocol(ps) is None and not w.gate(ps, 2).ok


def test_p1_vote_approves_or_reruns_with_the_judges_reasons(env):
    _, ps, _ = env
    ex = _executor([_draft(), _draft()], votes=_votes(("revise", "approve"), ("approve", "approve")))
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "rerun"
    assert "judges disagree" in out.reason and "judge: revise" in out.reason and "judge_2: approve" in out.reason
    (first,) = store.protocols(ps, 1)
    (v1,) = votes_on(ps, "protocol", first["id"])
    assert (v1["decision"], v1["conservative"], v1["question"]) == ("revise", 1, VOTE_QUESTION)
    assert store.frozen_protocol(ps) is None and not w.gate(ps, 1).ok

    rerun = _ctx(env, ex, attempt=2)
    rerun.guidance = out.reason  # what the engine gives the rerun (the edge's failures)
    assert w.run(rerun).status == "done"
    for call in ex.calls[4:6]:  # attempt 2: the Ideator and the Scout get the draft the judges asked to revise
        assert call["inputs"]["previous_draft"]["version"] == 1
        assert call["inputs"]["previous_draft"]["body"] == first["body"]
        assert call["inputs"]["previous_draft"]["record_probe"]["total"] == 0
        assert "judge: revise" in call["inputs"]["guidance"]
    (second,) = store.protocols(ps, 2)
    assert store.frozen_protocol(ps)["id"] == second["id"] and w.gate(ps, 2).ok
    judged = [c for c in ex.calls if c["schema"] == "VoteOut"]
    assert [c["role"] for c in judged] == ["judge", "judge_2"] * 2
    assert judged[0]["inputs"]["subject"]["protocol"] == first["body"]
    # the generator vendors are the Ideator's and the Scout's (anthropic): both judges (openai, deepseek) were admitted
    assert [o["vendor"] for o in votes_on(ps, "protocol", second["id"])[0]["opinions"]] == ["openai", "deepseek"]


def test_p1_vote_with_a_judge_of_the_ideator_vendor_is_conservative(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http)
    try:
        cfg = k.settings()
        cfg.drivers["judge"] = "claude_cli"
        save_settings(cfg)
        ex = _executor([_draft()], votes=_votes(("approve", "approve")))
        out = ProtocolWorker().run(_ctx((k, ps, lib), ex))
        assert out.status == "rerun" and "fewer than two admitted judges" in out.reason
        assert store.frozen_protocol(ps) is None
    finally:
        k.close()


def test_p1_vote_admits_no_judge_of_the_scout_vendor(env):
    """The voted protocol holds the Scout's search strategy too: a judge of the Scout's vendor is not admitted."""
    _, ps, _ = env
    ex = _executor([_draft()], votes=_votes(("approve", "approve")))
    ex.drivers["scout"] = "codex_cli"  # an openai Scout: the openai judge is out, as is the anthropic (Ideator) one
    out = ProtocolWorker().run(_ctx(env, ex))
    assert out.status == "rerun" and "fewer than two admitted judges" in out.reason
    assert "'judge': 'same_or_unknown_vendor'" in out.reason and "'judge_3': 'same_or_unknown_vendor'" in out.reason
    assert [c["role"] for c in ex.calls] == ["ideator", "scout"] and store.frozen_protocol(ps) is None


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_frozen_protocol_of_another_question_is_not_given_to_the_redraft(env):
    """After protocol->question and a new RQ, the version in force answers the previous question: the redraft for
    the new RQ is not asked to change only what the guidance asks of it."""
    _, ps, _ = env
    old = a_protocol(question="I am studying aspirin over 70 because of stroke in order to understand dosing.")
    store.freeze_protocol(ps, store.add_protocol(ps, 1, old, version=1, actor="tester"), actor="tester")
    ex = _executor([_draft()])
    assert ProtocolWorker().run(_ctx(env, ex, attempt=2)).status == "waiting_human"
    assert "frozen_protocol" not in ex.calls[0]["inputs"] and "frozen_protocol" not in ex.calls[1]["inputs"]


# ---- freeze, hash, deviations ----
def test_freeze_hash_is_stable(env):
    _, ps, _ = env
    ex = _executor([_draft()], votes=_votes(("approve", "approve")))
    assert ProtocolWorker().run(_ctx(env, ex)).status == "done"
    frozen = store.frozen_protocol(ps)
    body = frozen["body"]
    assert frozen["frozen_hash"] == sha256_hex(canonical_json(body)) == store.protocol_hash(body)
    shuffled = json.loads(json.dumps(dict(reversed(list(body.items())))))
    assert store.protocol_hash(shuffled) == frozen["frozen_hash"]  # key order does not matter
    assert store.protocol_hash(Protocol.model_validate(body).model_dump(mode="json")) == frozen["frozen_hash"]
    rebuild(ps.db)
    assert store.frozen_protocol(ps)["frozen_hash"] == frozen["frozen_hash"]
    assert probe_of(ps, frozen["id"])["total"] == 0


def _run_to(k, ps, workers: dict, ex, until: str, sources=None) -> list[str]:
    o = Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                     sources_factory=lambda project: sources or FakeSources({}), clock=lambda: T0)
    run_id = start_run(ps, until=until, actor="tester", clock=lambda: T0)
    out: list[str] = []
    for _ in range(40):
        out.append(o.step("demo", run_id, actor="tester"))
        if out[-1] in {"done", "failed", "paused_waiting_human", "paused_budget"}:
            return out
    raise AssertionError(out)


def _steps(o: Orchestrator, run_id: str) -> str:
    for _ in range(40):
        status = o.step("demo", run_id, actor="tester")
        if status in {"done", "failed", "paused_waiting_human", "paused_budget"}:
            return status
    raise AssertionError("run did not settle")


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("new_rq", [True, False])
def test_back_to_question_leaves_no_draft_to_revise(env, new_rq):
    """P2 back -> question reopened (and the RQ frozen again, changed or not) -> the next protocol attempt drafts
    afresh: the draft of the earlier attempt is no previous_draft."""
    k, ps, _ = env

    def requestion(ctx):
        if new_rq:
            old = frozen_rq(ps)
            body = RQBody.model_validate({**old["body"], "three_step": {**old["body"]["three_step"],
                                                                        "x": "aspirin over 85"}})
            rid = add_rq(ps, ctx.attempt, old["topic_id"], body,
                         [HypothesisIn(statement="H.", falsification="X.")], actor="tester")
            freeze_rq(ps, rid, adopted_by="human", actor="tester")
        return StageOutcome("done")

    ex = _executor([_draft(), _draft()])
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics")}
    workers["question"] = FakeWorker(runs=[StageOutcome("done"), requestion])
    workers["protocol"] = ProtocolWorker()
    o = Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                     sources_factory=lambda project: FakeSources({}), clock=lambda: T0)
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    assert _steps(o, run_id) == "paused_waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    answer_checkpoint(ps, cp["id"], {"decision": "back", "comment": "RQ too broad"}, answered_by="human",
                      actor="researcher")
    assert _steps(o, run_id) == "paused_waiting_human"
    assert [latest_attempt(ps, s) for s in ("question", "protocol")] == [2, 2]
    ideator = [c for c in ex.calls if c["role"] == "ideator"]
    scout = [c for c in ex.calls if c["role"] == "scout"]
    assert [c["attempt"] for c in ideator] == [1, 2]
    assert "previous_draft" not in ideator[1]["inputs"] and "previous_draft" not in scout[1]["inputs"]
    assert ideator[1]["inputs"]["research_question"]["sentence"].startswith(
        "I am studying aspirin over 85" if new_rq else "I am studying aspirin over 80")


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_revise_through_a_run_keeps_the_draft_to_revise(env):
    """A revise reruns protocol (protocol->protocol): the rerun revises the draft of the same RQ."""
    k, ps, _ = env
    ex = _executor([_draft(), _draft()])
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", "question")}
    workers["protocol"] = ProtocolWorker()
    o = Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                     sources_factory=lambda project: FakeSources({}), clock=lambda: T0)
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    assert _steps(o, run_id) == "paused_waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    answer_checkpoint(ps, cp["id"], {"decision": "revise", "comment": "Add a harm outcome."}, answered_by="human",
                      actor="researcher")
    assert _steps(o, run_id) == "paused_waiting_human"
    (draft,) = store.protocols(ps, 1)
    ideator = [c for c in ex.calls if c["role"] == "ideator"]
    assert ideator[1]["inputs"]["previous_draft"]["body"] == draft["body"]
    assert "Add a harm outcome." in ideator[1]["inputs"]["guidance"]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_change_after_screening_is_a_recorded_deviation(env):
    k, ps, _ = env
    narrower = _draft(eligibility={"include": [{"id": "I1", "text": "People over 80"},
                                               {"id": "I2", "text": "Trials or cohorts"}],
                                   "exclude": [{"id": "E1", "text": "Animal studies"}]})
    ex = _executor([_draft(), narrower], votes=_votes(("approve", "approve"), ("approve", "approve")))
    back = StageOutcome("back_edge", to="protocol", reason="eligibility too narrow")
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", "question", "read_extract")}
    workers["search_screen"] = FakeWorker(runs=[back, StageOutcome("done")])
    workers["protocol"] = ProtocolWorker()
    assert _run_to(k, ps, workers, ex, "search_screen")[-1] == "done"

    assert [latest_attempt(ps, s) for s in ("protocol", "search_screen")] == [2, 2]
    v1, v2 = store.protocols(ps, 1)[0], store.protocols(ps, 2)[0]
    assert (v1["version"], v2["version"]) == (1, 2)
    assert store.protocol_row(ps, v1["id"])["status"] == "frozen"  # the earlier frozen version stays readable
    assert store.protocol_row(ps, v1["id"])["body"]["eligibility"]["include"] == [{"id": "I1",
                                                                                   "text": "People over 80"}]
    assert store.frozen_protocol(ps)["id"] == v2["id"]
    assert [(d["from_version"], d["to_version"], d["stage"], d["reason"]) for d in store.deviations(ps)] == [
        (1, 2, "search_screen", "eligibility too narrow")]
    assert protocol_prereg_ok(ps)
    assert ex.calls[0]["inputs"].get("frozen_protocol") is None
    assert ex.calls[4]["inputs"]["frozen_protocol"]["version"] == 1  # the rerun sees the version in force

    # a later frozen version without a deviation breaks G-PREREG
    v3 = store.add_protocol(ps, 2, a_protocol(prisma_p=SR_ITEMS), version=3, actor="tester")
    store.freeze_protocol(ps, v3, actor="tester")
    assert not protocol_prereg_ok(ps)

    # an analysis deviation (a synthesis fallback: same version, protocol text unchanged) is no version deviation
    store.add_analysis_deviation(ps, 3, "meta-analysis pre-specified but the statistics module is not validated",
                                 "synthesize", actor="kernel")
    assert [(d["kind"], d["from_version"], d["to_version"]) for d in store.deviations(ps)] == [
        ("version", 1, 2), ("analysis", 3, 3)]
    assert not protocol_prereg_ok(ps)
    assert "frozen version 3 has no deviation from version 2" in ProtocolWorker().gate(ps, 2).failures


def test_first_freeze_after_screening_began_is_not_preregistered(env):
    _, ps, _ = env
    assert not protocol_prereg_ok(ps)  # nothing frozen
    record_attempt(ps, "search_screen", 1, run_id=None, guidance="", requested=None, actor="tester")
    ex = _executor([_draft()], votes=_votes(("approve", "approve")))
    assert ProtocolWorker().run(_ctx(env, ex)).status == "done"
    assert not protocol_prereg_ok(ps)


# ---- the record-cap probe (advisory) ----
def _big(n_openalex: int, n_epmc: int) -> FakeSources:
    return FakeSources({}, record_counts={(QUERIES[0], "openalex"): n_openalex, (QUERIES[0], "europepmc"): n_epmc,
                                          (QUERIES[1], "openalex"): None})


def test_record_cap_probe_is_advisory(env):
    _, ps, _ = env
    ex = _executor([_draft()], [_strategy(sources=["openalex", "europepmc", "pubmed"])],
                   votes=_votes(("approve", "approve")))
    sources = _big(2500, 1500)
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex, sources=sources))
    assert out.status == "done"  # 4000 > 3000 fails nothing: search_screen enforces the cap after deduplication
    assert [c for c in sources.calls if c[0] == "count"] == [("count", q, s) for q in QUERIES
                                                             for s in ("openalex", "europepmc")]
    (row,) = store.protocols(ps, 1)
    probe = probe_of(ps, row["id"])
    assert (probe["total"], probe["uncounted"]) == (4000, ["pubmed"])
    assert {"query": QUERIES[1], "source": "openalex", "count": None} in probe["counts"]  # no count: recorded, not 0
    assert w.gate(ps, 1).ok
    (judged, _) = [c for c in ex.calls if c["schema"] == "VoteOut"]
    assert judged["inputs"]["subject"]["record_probe"]["total"] == 4000  # the judges see it


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_probe_without_any_count_says_not_counted(env):
    _, ps, _ = env
    sources = FakeSources({}, record_counts={(q, "openalex"): None for q in QUERIES})
    ex = _executor([_draft()], [_strategy(sources=["openalex", "pubmed"])])
    out = ProtocolWorker().run(_ctx(env, ex, sources=sources))
    (row,) = store.protocols(ps, 1)
    assert probe_of(ps, row["id"])["total"] is None
    prompt = get_checkpoint(ps, out.checkpoint_id)["prompt"]
    assert "Record-cap probe (advisory): not counted" in prompt and "probe (advisory): 0" not in prompt
    assert "Not counted (no record count): pubmed." in prompt


class FailingCounts(FakeSources):
    """A source whose count of the first query fails: an HTTP 400 on openalex, an exhausted budget on europepmc."""

    def count(self, query: str, source: str) -> int | None:
        if query == QUERIES[0] and source == "openalex":
            raise HttpError(400, "https://api.openalex.org/works")
        if query == QUERIES[0] and source == "europepmc":
            raise BudgetExhausted("europepmc", T0)
        return super().count(query, source)


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_failed_count_fails_nothing_and_is_recorded_in_the_probe(env):
    _, ps, _ = env
    sources = FailingCounts({}, record_counts={(q, s): 10 for q in QUERIES for s in ("openalex", "europepmc")})
    ex = _executor([_draft()])
    out = ProtocolWorker().run(_ctx(env, ex, sources=sources))
    assert out.status == "waiting_human"  # the approval opens: the advisory probe failed nothing
    (row,) = store.protocols(ps, 1)
    probe = probe_of(ps, row["id"])
    failed = {c["source"]: c for c in probe["counts"] if c["query"] == QUERIES[0]}
    assert failed["openalex"]["count"] is None and "HttpError" in failed["openalex"]["error"]
    assert failed["europepmc"]["count"] is None and "BudgetExhausted" in failed["europepmc"]["error"]
    assert probe["total"] == 40 and all("error" not in c for c in probe["counts"] if c["query"] != QUERIES[0])
    prompt = get_checkpoint(ps, out.checkpoint_id)["prompt"]
    assert f"{QUERIES[0]!r} on openalex: no count (record count of" in prompt


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_raises_the_record_cap_on_approval(env):
    _, ps, _ = env
    ex = _executor([_draft(), _draft()])
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex, sources=_big(2500, 1500)))
    cp = get_checkpoint(ps, out.checkpoint_id)
    for text in ("Record-cap probe (advisory): 4000 records", f"{QUERIES[1]!r} on openalex: no count",
                 "4000 records, above the record cap 3000", "search_screen stops above the cap"):
        assert text in cp["prompt"], text
    for bad in (True, "5000", 0):
        cid = list_checkpoints(ps, "open")[0]["id"]
        assert "whole number" in w.on_answer(_ctx(env, ex), _answer(ps, cid, {"decision": "approve",
                                                                              "record_cap": bad})).reason
    cid = list_checkpoints(ps, "open")[0]["id"]
    raised = _answer(ps, cid, {"decision": "approve", "record_cap": 5000})
    assert w.on_answer(_ctx(env, ex), raised).status == "done"
    assert w.on_answer(_ctx(env, ex), raised).status == "done"  # handled again: nothing twice
    draft, by_you = store.protocols(ps, 1)
    assert (draft["status"], by_you["status"], by_you["version"]) == ("draft", "frozen", 2)
    assert by_you["body"] == {**draft["body"], "record_cap": 5000}
    assert [e.actor_kind for e in _events(ps, "protocol.added")] == ["agent", "human"]
    assert probe_of(ps, by_you["id"]) == probe_of(ps, draft["id"]) and w.gate(ps, 1).ok
    assert store.deviations(ps) == []  # one frozen version: nothing to deviate from

    # the raised cap stays the project's: the next draft carries it
    w.run(_ctx(env, ex, attempt=2, sources=_big(2500, 1500)))
    assert store.protocols(ps, 2)[0]["body"]["record_cap"] == 5000


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_raised_record_cap_over_a_frozen_version_records_the_deviation(env):
    _, ps, _ = env
    ex = _executor([_draft(), _draft()])
    w = ProtocolWorker()
    cid = w.run(_ctx(env, ex)).checkpoint_id
    assert w.on_answer(_ctx(env, ex), _answer(ps, cid, {"decision": "approve"})).status == "done"
    again = _ctx(env, ex, attempt=2)
    again.guidance = "search too broad"
    cid = w.run(again).checkpoint_id
    assert ex.calls[2]["inputs"]["frozen_protocol"]["version"] == 1
    assert "previous_draft" not in ex.calls[2]["inputs"]  # the latest protocol is frozen
    assert "Version 1 is frozen: approving records a deviation from it." in get_checkpoint(ps, cid)["prompt"]
    assert w.on_answer(again, _answer(ps, cid, {"decision": "approve", "record_cap": 5000})).status == "done"
    draft, by_you = store.protocols(ps, 2)
    assert (draft["version"], draft["status"], by_you["version"], by_you["status"]) == (2, "draft", 3, "frozen")
    assert [(d["from_version"], d["to_version"], d["stage"], d["reason"]) for d in store.deviations(ps)] == [
        (1, 3, "protocol", "search too broad")]
    assert w.gate(ps, 2).ok and protocol_prereg_ok(ps)


def test_p1_degrade_fails(env):
    ex = _executor([_draft()], votes=_votes(("revise", "revise")))
    w = ProtocolWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "rerun"
    failed = w.degrade(_ctx(env, ex), [out.reason])
    assert failed.status == "failed" and "not approved" in failed.reason and "judge: revise" in failed.reason


# ---- sources.count ----
def test_kernel_sources_count_reads_openalex_and_europepmc(tmp_home, tmp_path):
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, dict(request.url.params)))
        if request.url.params.get("search") == "nothing" or request.url.params.get("query") == "nothing":
            return httpx.Response(200, json={"results": []})  # no count field
        if "openalex" in request.url.host:
            return httpx.Response(200, json={"meta": {"count": 1234}, "results": [{"id": "https://openalex.org/W1"}]})
        return httpx.Response(200, json={"version": "6.9", "hitCount": 567, "resultList": {"result": []}})

    http = HttpClient(tmp_path / "mock-http.sqlite", user_agent="rk-test", sleep=lambda s: None,
                      transport=httpx.MockTransport(handler))
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=http)
    k.create_project("demo", "Demo", actor="t")
    try:
        src = KernelSources(k, "demo")
        assert src.count("aspirin stroke", "openalex") == 1234
        assert seen[-1][1]["search"] == "aspirin stroke" and seen[-1][1]["per_page"] == "1"
        assert src.count("aspirin stroke", "europepmc") == 567
        assert seen[-1][1]["query"] == "aspirin stroke" and seen[-1][1]["pageSize"] == "1"
        assert src.count("nothing", "openalex") is None and src.count("nothing", "europepmc") is None
        with pytest.raises(ValueError, match="pubmed"):
            src.count("aspirin stroke", "pubmed")
    finally:
        k.close()


def test_protocol_is_a_default_worker():
    assert isinstance(default_workers()["protocol"], ProtocolWorker)
