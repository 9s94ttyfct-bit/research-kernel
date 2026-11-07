"""The engine over the full stage graph of the paper type: runs continue past question into the production stages,
their back-edges, D1 projects' production runs and the task budget of the enumerated stages (Plan 5a Task 1)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from pydantic import BaseModel

from rk.config import load_settings, save_settings
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.discovery.models import HypothesisIn, RQBody, Topic
from rk.discovery.store import add_rq, add_topic, freeze_rq
from rk.llm.types import Availability, TaskResult
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.graph import (
    BACK_EDGES,
    DISCOVERY,
    ENUMERATED_STAGES,
    OPEN_STAGES,
    PRODUCTION,
    stages_for,
    stages_of,
)
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    current_stage,
    edge_count,
    latest_attempt,
    run_row,
    stage_rows,
    start_run,
)
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, ToolError

from .discovery_fakes import FakeSources, FakeWorker, ScriptedExecutor, make_project

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}
FIRST = ("protocol", "search_screen", "read_extract")  # the production stages Plan 5a runs
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


def _freeze_an_rq(ps) -> str:
    tid = add_topic(ps, 1, Topic(title="Aspirin over 80", summary="S.", gap_ids=[], feasibility="F.",
                                 data_needed="D.", paper_type="critical_review", risks="R."), actor="tester")
    body = RQBody.model_validate({
        "three_step": {"x": "aspirin over 80", "y": "stroke", "z": "when to start"},
        "problem": {"condition": "Guidelines stop at 80.", "consequences": ["A.", "B."]},
        "finer": {"feasible": "F.", "interesting": "I.", "novel": "N.", "ethical": "E.", "relevant": "R."},
        "clinical": False})
    rq = add_rq(ps, 1, tid, body, [HypothesisIn(statement="H.", falsification="X.")], actor="tester")
    freeze_rq(ps, rq, adopted_by="human", actor="tester")
    return rq


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
    assert "protocol" in OPEN_STAGES and ENUMERATED_STAGES == {"search_screen", "read_extract"}
    assert not OPEN_STAGES & ENUMERATED_STAGES


def test_run_continues_past_question_until_read_extract(project):
    k, ps = project
    w = _workers()
    o = _orch(k, w)
    assert stages_of(ps) == stages_for("critical_review")
    assert o.runnable(ps) == [*DISCOVERY, *FIRST]
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


def test_unrunnable_stage_is_unavailable(project):
    k, ps = project
    o = _orch(k, _workers())
    assert "synthesize" not in o.runnable(ps)
    run_id = start_run(ps, until="synthesize", actor="tester", clock=lambda: T0)
    with pytest.raises(ToolError) as e:
        _drive(o, run_id)
    assert e.value.type == "stage_unavailable" and "Plan 5b/5c" in e.value.message
    assert current_stage(ps) == "synthesize"
    with pytest.raises(ToolError) as e:  # a stage the paper type does not have
        start_run(ps, until="design", actor="tester", clock=lambda: T0)
    assert e.value.type == "stage_unavailable"

    bare = _orch(k, {s: FakeWorker() for s in DISCOVERY if s != "intake"})  # no production worker yet
    assert bare.runnable(ps) == list(DISCOVERY)
    with pytest.raises(ToolError) as e:
        bare.run_stage("demo", "protocol", actor="tester")
    assert e.value.type == "stage_unavailable"


def test_start_until_a_stage_without_worker_is_refused(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    try:
        reg = build_registry(k)
        m = RunManager(k, reg, _orch(k, {s: FakeWorker() for s in DISCOVERY if s != "intake"}, reg), poll_s=0.02)
        try:
            run_id = m.start("demo", "question", actor="tester")
            _wait(lambda: run_row(ps, run_id)["status"] == "done")
            _freeze_an_rq(ps)
            with pytest.raises(ToolError) as e:
                m.start("demo", "protocol", actor="tester")
            assert e.value.type == "stage_unavailable" and "Plan 5b/5c" in e.value.message
            assert m.status("demo")["until_options"] == []
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
        with pytest.raises(ToolError) as e:  # a run past question needs a frozen RQ (the fakes froze none)
            m.start("demo", "read_extract", actor="tester")
        assert e.value.type == "rq_not_frozen"
        _freeze_an_rq(ps)
        assert m.status("demo")["until_options"] == list(FIRST)
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

    def executor():
        return KernelExecutor(k, reg, "demo", run_id="run_1", session=1, provider_for=lambda role: OkProvider())

    def task(ex, stage):
        return ex.run("gapminer", "do", {}, Found, stage=stage, attempt=1)

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
