"""The engine over the full stage graph of the paper type: runs continue past question into the production stages,
their back-edges, D1 projects' production runs, the Lead's lockdown once production began and the task budget of the
enumerated stages (Plan 5a Task 1)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from pydantic import BaseModel

from rk.checkpoints import list_checkpoints
from rk.config import load_settings, save_settings
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.graph import (
    BACK_EDGES,
    DISCOVERY,
    OPEN_STAGES,
    PRODUCTION,
    stages_for,
    stages_of,
)
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    current_stage,
    discovery_passed,
    edge_count,
    latest_attempt,
    orchestrator_drives,
    production_began,
    run_row,
    stage_rows,
    start_run,
)
from rk.roles.policy import lead_closed_after_discovery
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, ToolError

from .discovery_fakes import (
    PRODUCTION_FIRST,
    FakeSources,
    FakeWorker,
    ScriptedExecutor,
    back_to_question_workers,
    freeze_an_rq,
    make_project,
)

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}
FIRST = PRODUCTION_FIRST
TIMEOUT = 10.0


def _workers(**overrides) -> dict[str, FakeWorker]:
    return {s: overrides.get(s, FakeWorker()) for s in (*DISCOVERY, *FIRST) if s != "intake"}


def _orch(k, workers: dict, reg=None) -> Orchestrator:
    return Orchestrator(k, reg, workers=workers, executor_factory=lambda project, run_id, session: ScriptedExecutor({}),
                        sources_factory=lambda project: FakeSources({}), clock=lambda: T0)


def _drive(o: Orchestrator, run_id: str, *, limit: int = 60) -> list[str]:
    out: list[str] = []
    for _ in range(limit):
        out.append(o.step("demo", run_id, actor="tester"))
        if out[-1] in STOP:
            return out
    raise AssertionError(f"run did not settle: {out}")


def _wait(pred, timeout: float = TIMEOUT):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if got := pred():
            return got
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def _ctx(role: str = "cli") -> CallContext:
    return CallContext(role=role, actor="tester", idempotency_key=uuid.uuid4().hex)


@pytest.fixture
def project(tmp_home, replay_http, request):
    k, ps, _ = make_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps
    k.close()


def test_graph_of_the_production_stages():
    assert PRODUCTION == ("protocol", "search_screen", "read_extract", "synthesize", "argue", "design", "draft",
                          "revise", "audit", "export")
    for paper_type in ("systematic_review", "scoping_review", "critical_review", "research_proposal"):
        assert set(stages_for(paper_type)[len(DISCOVERY):]) <= set(PRODUCTION)
    assert {("protocol", "question"), ("search_screen", "protocol"), ("read_extract", "search_screen")} <= BACK_EDGES
    # protocol has a task budget; search_screen and read_extract (every record, every study) have none
    assert "protocol" in OPEN_STAGES and not OPEN_STAGES & {"search_screen", "read_extract"}


def test_run_continues_past_question_until_read_extract(project):
    k, ps = project
    w = _workers()
    o = _orch(k, w)
    assert stages_of(ps) == stages_for("critical_review")
    assert o.runnable(ps) == [*DISCOVERY, *FIRST, "synthesize", "argue", "draft", "revise", "audit"]
    run_id = start_run(ps, until="read_extract", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "done"
    assert [r["stage"] for r in stage_rows(ps)] == stages_for("critical_review")
    assert [r["stage"] for r in stage_rows(ps) if r["status"] == "passed"] == [*DISCOVERY, *FIRST]
    assert current_stage(ps) == "synthesize"
    for s in FIRST:
        assert w[s].kinds() == ["run", "gate"], s


def test_run_until_protocol_stops_there(project):
    k, ps = project
    w = _workers()
    o = _orch(k, w)
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "done"
    assert current_stage(ps) == "search_screen" and w["search_screen"].calls == []


def test_one_run_from_intake_through_question_into_production(tmp_home, replay_http):
    """A D2 run that starts before question passed may end past it: the run gets its RQ frozen on the way (here the
    fakes freeze none, and nothing asks for one)."""
    k, ps, _ = make_project(tmp_home, replay_http)
    try:
        reg = build_registry(k)
        w = _workers()
        m = RunManager(k, reg, _orch(k, w, reg), poll_s=0.02)
        try:
            assert m.status("demo")["until_options"] == ["topics", "question", *FIRST, "synthesize", "argue", "draft",
                                                         "revise", "audit"]
            run_id = m.start("demo", "read_extract", actor="tester")
            _wait(lambda: run_row(ps, run_id)["status"] == "done")
            assert current_stage(ps) == "synthesize"
            assert [w[s].kinds() for s in ("question", *FIRST)] == [["run", "gate"]] * 4
        finally:
            m.close()
    finally:
        k.close()


def test_back_edge_search_screen_to_protocol_reopens_downstream(project):
    k, ps = project
    w = _workers(
        read_extract=FakeWorker(runs=[StageOutcome("back_edge", to="search_screen", reason="too few included")]),
        search_screen=FakeWorker(runs=[StageOutcome("done"),
                                       StageOutcome("back_edge", to="protocol", reason="eligibility too narrow")]))
    o = _orch(k, w)
    run_id = start_run(ps, until="read_extract", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "done"
    reopened = [e.payload["stage"] for e in ps.log.iter()
                if e.type == "stage.reopened" and e.payload["by_edge"] == "search_screen->protocol"]
    assert reopened == ["protocol", "search_screen", "read_extract"]  # downstream that ran, never question
    assert [latest_attempt(ps, s) for s in ("question", *FIRST)] == [1, 2, 3, 2]
    assert w["protocol"].calls[-2] == ("run", 2, "eligibility too narrow")
    assert [edge_count(ps, e) for e in ("read_extract->search_screen", "search_screen->protocol")] == [1, 1]


def test_back_edge_protocol_to_question_reopens_question_and_protocol(project):
    k, ps = project
    w = _workers(protocol=FakeWorker(runs=[StageOutcome("back_edge", to="question", reason="RQ too broad")]))
    o = _orch(k, w)
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "done"
    assert [latest_attempt(ps, s) for s in ("topics", "question", "protocol")] == [1, 2, 2]
    assert w["question"].calls[-2] == ("run", 2, "RQ too broad")
    assert edge_count(ps, "protocol->question") == 1


@pytest.mark.parametrize("project", [{"production_mode": "collab"}], indirect=True)
def test_third_use_of_a_production_edge_asks_in_p2(project):
    k, ps = project
    back = StageOutcome("back_edge", to="protocol", reason="eligibility too narrow")
    w = _workers(search_screen=FakeWorker(runs=[back, back, back]))
    o = _orch(k, w)
    run_id = start_run(ps, until="search_screen", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    assert cp["kind"] == "stage_blocked" and cp["options"]["edge"] == "search_screen->protocol"
    assert edge_count(ps, "search_screen->protocol") == 2
    assert [latest_attempt(ps, s) for s in ("protocol", "search_screen")] == [3, 3]


def test_unrunnable_stage_is_unavailable(project):
    k, ps = project
    o = _orch(k, {**_workers(), **{s: FakeWorker() for s in ("synthesize", "argue", "draft", "revise", "audit")}})
    assert "export" not in o.runnable(ps)
    run_id = start_run(ps, until="export", actor="tester", clock=lambda: T0)
    with pytest.raises(ToolError) as e:
        _drive(o, run_id)
    assert e.value.type == "stage_unavailable" and "no worker yet" in e.value.message
    assert current_stage(ps) == "export"
    with pytest.raises(ToolError) as e:  # a stage the paper type does not have
        start_run(ps, until="design", actor="tester", clock=lambda: T0)
    assert e.value.type == "stage_unavailable"

    bare = _orch(k, {s: FakeWorker() for s in DISCOVERY if s != "intake"})  # the default production workers only
    assert bare.runnable(ps) == [*DISCOVERY, "protocol", "search_screen", "read_extract", "synthesize", "argue",
                                 "draft", "revise", "audit"]
    with pytest.raises(ToolError) as e:
        bare.run_stage("demo", "read_extract", actor="tester")
    assert e.value.type == "stage_unavailable"


def test_start_until_a_stage_without_worker_is_refused(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    try:
        reg = build_registry(k)
        m = RunManager(k, reg, _orch(k, {s: FakeWorker() for s in DISCOVERY if s != "intake"}, reg), poll_s=0.02)
        try:
            with pytest.raises(ToolError) as e:  # before question too: export has no worker
                m.start("demo", "export", actor="tester")
            assert e.value.type == "stage_unavailable" and "no worker yet" in e.value.message
            run_id = m.start("demo", "question", actor="tester")
            _wait(lambda: run_row(ps, run_id)["status"] == "done")
            freeze_an_rq(ps)
            with pytest.raises(ToolError) as e:
                m.start("demo", "export", actor="tester")
            assert e.value.type == "stage_unavailable" and "no worker yet" in e.value.message
            assert m.status("demo")["until_options"] == ["protocol", "search_screen", "read_extract", "synthesize",
                                                         "argue", "draft", "revise", "audit"]
        finally:
            m.close()
    finally:
        k.close()


@pytest.mark.parametrize("project", [{"discovery_mode": "d1", "production_mode": "collab"}], indirect=True)
def test_d1_project_can_start_a_production_run_after_question(project):
    k, ps = project
    holder: dict = {}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    w = _workers()
    m = holder["m"] = RunManager(k, reg, _orch(k, w, reg), poll_s=0.02)
    try:
        with pytest.raises(ToolError) as e:  # before question passed, a D1 project's stages are guided
            m.start("demo", "protocol", actor="tester")
        assert e.value.type == "not_d2"
        assert m.status("demo")["until_options"] == []
        for stage in DISCOVERY:  # guided discovery
            assert m.orchestrator.run_stage("demo", stage, actor="tester").status == "done"
            assert m.orchestrator.advance("demo", actor="tester").ok
        assert current_stage(ps) == "protocol"
        with pytest.raises(ToolError) as e:  # the production stages are orchestrator-run only
            m.orchestrator.run_stage("demo", "protocol", actor="tester")
        assert e.value.type == "stage_unavailable"
        with pytest.raises(ToolError) as e:
            m.orchestrator.advance("demo", actor="tester")
        assert e.value.type == "stage_unavailable"
        with pytest.raises(ToolError) as e:  # a run that starts after question passed needs a frozen RQ
            m.start("demo", "read_extract", actor="tester")
        assert e.value.type == "rq_not_frozen"
        assert m.status("demo")["until_options"] == []
        freeze_an_rq(ps)
        assert m.status("demo")["until_options"] == [*FIRST, "synthesize", "argue", "draft", "revise", "audit"]
        for until in ("topics", "question"):  # passed already
            with pytest.raises(ToolError) as e:
                m.start("demo", until, actor="tester")
            assert e.value.type == "stage_unavailable", until
        run_id = m.start("demo", "read_extract", actor="tester")
        _wait(lambda: run_row(ps, run_id)["status"] == "done")
        assert current_stage(ps) == "synthesize"
        assert [w[s].kinds() for s in FIRST] == [["run", "gate"]] * 3
    finally:
        m.close()
    assert not [t for t in threading.enumerate() if t.name.startswith("rk-run-")]


@pytest.mark.parametrize("project", [{"discovery_mode": "d1"}, {"discovery_mode": "d2"}], indirect=True,
                         ids=["d1", "d2"])
def test_lead_stays_read_only_once_production_began(project):
    """A back-edge from protocol to question reopens a discovery stage, but production began: the orchestrator keeps
    the project (spec §8.1). The Lead's writes stay refused, guided stage calls too; reads and status still work."""
    k, ps = project
    reg = build_registry(k)
    o = _orch(k, back_to_question_workers(), reg)
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    assert current_stage(ps) == "question" and not discovery_passed(ps)
    assert production_began(ps) and orchestrator_drives(ps)

    closed = [t for t in reg.list("lead") if lead_closed_after_discovery(t.name, t.writes)
              and "project" in t.input_model.model_fields]
    names = {t.name for t in closed}
    assert {"rk_claim_add", "rk_source_add", "rk_library_tag", "rk_verify", "rk_note_add", "rk_stage_run"} <= names
    for t in closed:
        with pytest.raises(ToolError) as e:
            reg.call(t.name, {"project": "demo"}, _ctx("lead"))
        assert e.value.type == "stage_closed", t.name
    assert reg.call("rk_stage_status", {"project": "demo"}, _ctx("lead"))["current"] == "question"
    assert reg.call("rk_checkpoint_list", {"project": "demo"}, _ctx("lead"))["checkpoints"]
    with pytest.raises(ToolError) as e:  # guided calls are refused to everyone, the researcher included
        o.run_stage("demo", "question", actor="tester")
    assert e.value.type == "stage_unavailable"


# ---- Plan 5b: the stage graph after read_extract and the run texts --------------------------------------------------
def test_new_back_edges_and_argue_is_open():
    assert {("synthesize", "read_extract"), ("argue", "synthesize"), ("argue", "question"), ("design", "argue"),
            ("design", "question")} <= BACK_EDGES
    # argue has a task budget; synthesize and design (bounded by the studies and the design) have none
    assert "argue" in OPEN_STAGES and not OPEN_STAGES & {"synthesize", "design"}


@pytest.mark.parametrize("project", [{"paper_type": "research_proposal"}, {"paper_type": "critical_review"}],
                         indirect=True)
def test_run_texts_list_runnable_stages(project):
    k, ps = project
    holder: dict = {}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    o = _orch(k, {**_workers(), **{s: FakeWorker() for s in ("synthesize", "argue", "design", "draft", "revise",
                                                              "audit")}},
              reg)
    m = holder["m"] = RunManager(k, reg, o, poll_s=0.02)
    try:
        proposal = ps.meta("paper_type") == "research_proposal"
        later = [*FIRST, "synthesize", "argue", *(["design"] if proposal else []), "draft", "revise", "audit"]
        assert o.stages_after_question(ps) == later
        with pytest.raises(ToolError) as e:  # a D2 project: guided calls are refused, naming where a run can end
            reg.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx())
        assert e.value.type == "not_d1" and f"then {', '.join(later)};" in e.value.message
        assert ("design" in e.value.message) == proposal
        # the default workers: the texts follow what runs, no hard-coded stage list
        assert _orch(k, _workers()).stages_after_question(ps) == later
        for name in ("rk_run_start", "rk_run_status"):
            assert "read_extract" not in reg.get(name).summary
    finally:
        m.close()


# ---- the task budget of the enumerated stages ----------------------------------------------------------------------
class Found(BaseModel):
    found: bool


class OkProvider:
    name = "fake"
    model = "fake-1"
    vendor = "openai"
    kind = "chat_api"

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        return TaskResult(spec.task_id, True, Found(found=True), "{}", "fake", "fake-1", 0.0, None)


def test_enumerated_stage_has_no_task_cap(project):
    k, _ = project
    save_settings(load_settings().model_copy(update={"calls_per_hour": {}}))
    reg = build_registry(k)
    driver = DriverSpec.parse("claude_cli")  # the hourly cap below is this driver's

    def executor():
        return KernelExecutor(k, reg, "demo", run_id="run_1", session=1, provider_for=lambda role: OkProvider())

    def task(ex, stage):
        return ex.run("gapminer", "do", {}, Found, stage=stage, attempt=1, driver=driver)

    ex = executor()
    for _ in range(41):
        assert task(ex, "search_screen").ok
        assert task(ex, "read_extract").ok
    for _ in range(40):
        assert task(ex, "protocol").ok
    with pytest.raises(BudgetExceeded):  # protocol is an open stage
        task(ex, "protocol")

    save_settings(load_settings().model_copy(update={"calls_per_hour": {"claude_cli": 1}}))
    capped = executor()
    with pytest.raises(RateCapped):  # the hourly cap of the driver still applies
        for _ in range(2):
            task(capped, "search_screen")
