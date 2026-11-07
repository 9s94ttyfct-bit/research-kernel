import json

import pytest

from rk.canon import canonical_json, sha256_hex
from rk.discovery import store
from rk.discovery.models import (
    ClosestWork,
    Cluster,
    Finer,
    Gap,
    GapEvidence,
    HypothesisIn,
    Intake,
    Pico,
    Problem,
    RQBody,
    ThreeStep,
    Topic,
)
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied
from tests.discovery_fakes import make_project


@pytest.fixture
def proj(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http)
    yield k, ps, lib
    k.close()


def _gap(statement: str = "Trials disagree on the effect in older adults.") -> Gap:
    return Gap(kind="contradiction", statement=statement, cluster_id=None,
               evidence=GapEvidence(note_ids=["nte_1"], item_ids=["src_1", "src_2"], claim_ids=[]),
               details={"pairs": [["src_1", "src_2"]]})


def _body() -> RQBody:
    return RQBody(three_step=ThreeStep(x="how exercise dose changes falls", y="the dose-response is unclear",
                                       z="clinicians can prescribe a minimum dose"),
                  problem=Problem(condition="no dose-response estimate exists",
                                  consequences=["over-prescription", "low adherence"]),
                  finer=Finer(feasible="public trial data", interesting="clinical relevance", novel="not found in scope",
                              ethical="secondary data only", relevant="ageing populations"),
                  clinical=True,
                  pico=Pico(population="adults over 65", intervention="exercise", comparison="usual care",
                            outcome="falls"))


def _hyps() -> list[HypothesisIn]:
    return [HypothesisIn(statement="Higher dose reduces falls.", falsification="No trend across dose strata."),
            HypothesisIn(statement="The effect plateaus above 3 h/week.", falsification="Linear trend beyond 3 h.")]


def test_intake_sets_meta_and_round_trips(proj):
    _, ps, _ = proj
    intake = Intake(audience="nurses", paper_type="scoping_review", discovery_mode="d1", production_mode="collab",
                    language="de", topics_n=4)
    store.record_intake(ps, intake, actor="tester", actor_kind="human")
    assert ps.meta("production_mode") == "collab"
    assert ps.meta("discovery_mode") == "d1" and ps.meta("topics_n") == "4"
    assert store.intake_of(ps) == intake


def test_intake_of_is_none_before_intake(tmp_home, replay_http):
    from rk.config import Settings, save_settings
    from rk.workspace import Kernel

    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    try:
        ps = k.create_project("bare", "Bare", actor="tester")
        assert store.intake_of(ps) is None
    finally:
        k.close()


def _queries(ps, rq_id: str) -> dict:
    return {"clusters": store.clusters(ps, 1), "gaps": store.gaps(ps, 1), "topics": store.topics(ps, 1),
            "rqs": store.rqs(ps, 1), "hypotheses": store.hypotheses(ps, rq_id), "rounds": store.rounds(ps),
            "searches": store.searches(ps, "landscape", 1), "guidance": store.guidance(ps, "landscape"),
            "intake": store.intake_of(ps)}


def test_rebuild_restores_discovery_tables(proj):
    _, ps, _ = proj
    store.log_search(ps, stage="landscape", attempt=1, purpose="landscape", source="openalex", query="exercise falls",
                     limit=25, item_ids=["src_1", "src_2"], actor="scout")
    cid = store.add_cluster(ps, 1, Cluster(label="Exercise", description="Exercise trials", query="exercise falls",
                                           item_ids=["src_1", "src_2"], representative_ids=["src_1"],
                                           year_counts={2020: 3, 2021: 5}), actor="scout")
    gid = store.add_gap(ps, 1, _gap().model_copy(update={"cluster_id": cid}), verified=True,
                        checks={"quotes": "ok"}, actor="gapminer")
    tid = store.add_topic(ps, 1, Topic(title="Dose-response of exercise", summary="s", gap_ids=[gid],
                                       closest=[ClosestWork(item_id="src_1", title="T", year=2020,
                                                            differences={"openai": "older adults only"})],
                                       feasibility="high", data_needed="trial data", paper_type="critical_review",
                                       risks="few trials"), actor="ideator")
    store.set_topic_status(ps, tid, "approved", actor="tester", actor_kind="human")
    rq_id = store.add_rq(ps, 1, tid, _body(), _hyps(), actor="ideator")
    store.set_already_done(ps, rq_id, {"result": "not_found_in_scope", "works": []}, actor="kernel")
    store.freeze_rq(ps, rq_id, adopted_by="human", actor="tester")
    store.add_round(ps, "landscape", "exclude animal studies", actor="tester")
    store.add_round(ps, "landscape", "focus on RCTs", actor="tester")

    before = _queries(ps, rq_id)
    assert all(v for v in before.values())
    assert before["topics"][0]["status"] == "approved" and before["rqs"][0]["status"] == "frozen"
    assert before["clusters"][0]["year_counts"] == {2020: 3, 2021: 5}
    assert before["guidance"].index("focus on RCTs") < before["guidance"].index("exclude animal studies")
    rebuild(ps.db)
    assert _queries(ps, rq_id) == before


def test_freeze_hash_is_stable_and_logged(proj):
    _, ps, _ = proj
    tid = store.add_topic(ps, 1, Topic(title="t", summary="s", gap_ids=[], feasibility="f", data_needed="d",
                                       paper_type="critical_review", risks="r"), actor="ideator")
    body, hyps = _body(), _hyps()
    rq_id = store.add_rq(ps, 1, tid, body, hyps, actor="ideator")
    expected = sha256_hex(canonical_json({**body.model_dump(mode="json"),
                                          "hypotheses": [h.model_dump(mode="json") for h in hyps]}))
    h = store.freeze_rq(ps, rq_id, adopted_by="agent", actor="kernel")
    assert h == expected
    payload = json.loads(ps.db.execute("SELECT payload FROM events WHERE type='rq.frozen'").fetchone()["payload"])
    assert payload["text_hash"] == expected and payload["adopted_by"] == "agent"
    row = store.rqs(ps, 1)[0]
    assert row["text_hash"] == expected and row["adopted_by"] == "agent"
    with pytest.raises(ValueError, match="rq already frozen"):
        store.freeze_rq(ps, rq_id, adopted_by="human", actor="tester")


def test_queries_read_one_attempt(proj):
    _, ps, _ = proj
    store.add_gap(ps, 1, _gap("first"), verified=True, checks={}, actor="gapminer")
    g2 = store.add_gap(ps, 2, _gap("second"), verified=False, checks={"why": "quote missing"}, actor="gapminer")
    g3 = store.add_gap(ps, 2, _gap("third"), verified=True, checks={}, actor="gapminer")
    assert [g["id"] for g in store.gaps(ps, 2)] == [g2, g3]
    assert [g["statement"] for g in store.gaps(ps, 1)] == ["first"]
    assert [g["id"] for g in store.gaps(ps, 2, verified=True)] == [g3]
    assert [g["id"] for g in store.gaps(ps, 2, verified=False)] == [g2]


def test_guidance_includes_rounds_of_every_stage_newest_first(proj):
    _, ps, _ = proj
    assert store.guidance(ps, "topics") == ""
    store.add_round(ps, "landscape", "exclude animal studies", actor="tester")
    store.add_round(ps, "gaps", "prefer contradictions", actor="tester")
    assert store.guidance(ps, "topics") == "prefer contradictions\nexclude animal studies"
    assert store.guidance(ps, "landscape") == store.guidance(ps, "topics")


def test_intake_tool_policy(proj):
    k, ps, _ = proj
    reg = build_registry(k)
    args = {"project": "demo", "audience": "policy makers", "paper_type": "systematic_review",
            "discovery_mode": "d1", "production_mode": "collab"}
    out = reg.call("rk_project_intake", args, CallContext(role="lead", actor="lead", idempotency_key="a"))
    assert out["production_mode"] == "collab" and out["topics_n"] == 6
    assert ps.meta("audience") == "policy makers"
    reg.call("rk_project_intake", {**args, "topics_n": 3}, CallContext(role="cli", actor="me", idempotency_key="b"))
    assert store.intake_of(ps).topics_n == 3
    with pytest.raises(PermissionDenied):
        reg.call("rk_project_intake", args, CallContext(role="scout", actor="scout", idempotency_key="c"))


def test_intake_refused_while_a_run_is_active(tmp_home, replay_http):
    """A Lead (e.g. after an injection) cannot switch a P2 run to P1 mid-run; nobody can change the intake then."""
    import time

    from rk.orchestrator.state import record_run_status, start_run
    from rk.tools.registry import ToolError

    k, ps, _ = make_project(tmp_home, replay_http, production_mode="collab")
    try:
        reg = build_registry(k)
        run_id = start_run(ps, until="question", actor="me", clock=time.time)
        args = {"project": "demo", **store.intake_of(ps).model_dump(), "production_mode": "auto"}
        for i, role in enumerate(("lead", "cli")):
            with pytest.raises(ToolError) as e:
                reg.call("rk_project_intake", args, CallContext(role=role, actor=role, idempotency_key=f"r{i}"))
            assert e.value.type == "run_active" and run_id in e.value.message
        assert store.intake_of(ps).production_mode == "collab"
        record_run_status(ps, run_id, "cancelled", actor="me", clock=time.time)
        reg.call("rk_project_intake", args, CallContext(role="cli", actor="me", idempotency_key="after"))
        assert store.intake_of(ps).production_mode == "auto"
    finally:
        k.close()


def test_intake_modes_are_human_only_once_landscape_ran(proj):
    from rk.orchestrator.state import record_attempt
    from rk.tools.registry import ToolError

    k, ps, _ = proj
    reg = build_registry(k)
    base = {"project": "demo", **store.intake_of(ps).model_dump()}
    record_attempt(ps, "landscape", 1, run_id=None, guidance="", requested=None, actor="lead")
    for i, change in enumerate(({"discovery_mode": "d1"}, {"production_mode": "collab"},
                                {"paper_type": "scoping_review"})):
        with pytest.raises(ToolError) as e:
            reg.call("rk_project_intake", {**base, **change}, CallContext(role="lead", actor="lead",
                                                                          idempotency_key=f"m{i}"))
        assert e.value.type == "intake_frozen" and next(iter(change)) in e.value.message
    assert store.intake_of(ps).production_mode == "auto"
    reg.call("rk_project_intake", {**base, "audience": "nurses"}, CallContext(role="lead", actor="lead",
                                                                             idempotency_key="aud"))
    assert store.intake_of(ps).audience == "nurses"
    reg.call("rk_project_intake", {**base, "production_mode": "collab"}, CallContext(role="cli", actor="me",
                                                                                    idempotency_key="human"))
    assert store.intake_of(ps).production_mode == "collab"


def test_cli_intake_maps_production_modes(live_server):
    from typer.testing import CliRunner

    from rk.cli.main import app

    live_server.call("rk_project_create", {"slug": "cli-intake", "title": "C"}, write=True)
    ps, _ = live_server.kernel.project("cli-intake")
    r = CliRunner().invoke(app, ["project", "intake", "--project", "cli-intake", "--audience", "nurses",
                                 "--paper-type", "scoping_review", "--mode", "d1", "--production", "p2",
                                 "--topics-n", "4"])
    assert r.exit_code == 0, r.output
    assert store.intake_of(ps) == Intake(audience="nurses", paper_type="scoping_review", discovery_mode="d1",
                                         production_mode="collab", topics_n=4)
    r = CliRunner().invoke(app, ["project", "intake", "--project", "cli-intake", "--audience", "nurses",
                                 "--paper-type", "scoping_review", "--mode", "d1", "--production", "p3"])
    assert r.exit_code == 2 and ps.meta("production_mode") == "collab"


def test_intake_refuses_topics_n_beyond_the_stage_budget(proj):
    from pydantic import ValidationError

    from rk.discovery.models import MAX_TOPICS_N, TASKS_PER_TOPIC
    from rk.orchestrator.graph import STAGE_TASK_BUDGET

    _, ps, _ = proj
    assert MAX_TOPICS_N == 12 and 1 + TASKS_PER_TOPIC * MAX_TOPICS_N < STAGE_TASK_BUDGET
    base = {"audience": "nurses", "paper_type": "scoping_review", "discovery_mode": "d1", "production_mode": "auto"}
    assert Intake(**base, topics_n=12).topics_n == 12
    for bad in (0, 13):
        with pytest.raises(ValidationError, match="topics_n must be 1 to 12"):
            Intake(**base, topics_n=bad)
    reg = build_registry(proj[0])
    with pytest.raises(ValidationError, match="topics_n must be 1 to 12"):
        reg.call("rk_project_intake", {"project": "demo", **base, "topics_n": 13},
                 CallContext(role="cli", actor="me", idempotency_key="t13"))
    assert ps.meta("topics_n") == "6"  # unchanged
    schema = reg.get("rk_project_intake").input_model.model_json_schema()["properties"]["topics_n"]
    assert (schema["minimum"], schema["maximum"]) == (1, 12)


def test_cli_intake_refuses_topics_n_13(live_server):
    from typer.testing import CliRunner

    from rk.cli.main import app

    live_server.call("rk_project_create", {"slug": "cli-topics", "title": "C"}, write=True)
    ps, _ = live_server.kernel.project("cli-topics")
    r = CliRunner().invoke(app, ["project", "intake", "--project", "cli-topics", "--audience", "nurses",
                                 "--paper-type", "scoping_review", "--mode", "d2", "--production", "p1",
                                 "--topics-n", "13"])
    assert r.exit_code != 0 and "topics_n must be 1 to 12" in r.output
    assert ps.meta("topics_n") is None
