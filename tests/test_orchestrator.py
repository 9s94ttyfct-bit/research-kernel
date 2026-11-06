"""The stage engine: attempts, gates, back-edges and their limits, budgets, rate caps and checkpoint waits (Plan 4
Task 3)."""

from __future__ import annotations

import json

import pytest

from rk.checkpoints import (
    answer_checkpoint,
    create_checkpoint,
    get_checkpoint,
    list_checkpoints,
)
from rk.discovery.executor import BudgetExceeded, RateCapped
from rk.orchestrator.engine import GateResult, IntakeWorker, Orchestrator, StageOutcome
from rk.orchestrator.graph import DISCOVERY, OPEN_STAGES, stages_for
from rk.orchestrator.state import (
    current_stage,
    edge_count,
    latest_attempt,
    record_degraded,
    run_row,
    stage_rows,
    start_run,
)
from rk.store.projections import rebuild
from rk.tools.registry import ToolError

from .discovery_fakes import FakeSources, ScriptedExecutor, make_project

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}


class FakeWorker:
    """A scripted ``Worker``. Each queue item is a ``StageOutcome`` (or ``GateResult`` for ``gates``), an exception
    to raise, or a callable taking the context; an empty queue gives ``done`` (or a passing gate)."""

    def __init__(self, *, runs=(), answers=(), gates=(), degrades=()):
        self.runs, self.answers, self.gates, self.degrades = list(runs), list(answers), list(gates), list(degrades)
        self.calls: list[tuple] = []

    @staticmethod
    def _next(queue, arg, default):
        item = queue.pop(0) if queue else default
        if isinstance(item, BaseException):
            raise item
        return item(arg) if callable(item) else item

    def run(self, ctx):
        self.calls.append(("run", ctx.attempt, ctx.guidance))
        return self._next(self.runs, ctx, StageOutcome("done"))

    def on_answer(self, ctx, checkpoint):
        self.calls.append(("on_answer", ctx.attempt, checkpoint["id"], checkpoint["answer"]))
        return self._next(self.answers, ctx, StageOutcome("done"))

    def gate(self, ps, attempt):
        self.calls.append(("gate", attempt))
        return self._next(self.gates, ps, GateResult(True, []))

    def degrade(self, ctx, failures):
        self.calls.append(("degrade", ctx.attempt, list(failures)))
        return self._next(self.degrades, ctx, StageOutcome("failed", reason="nothing to degrade"))

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _orch(k, workers: dict, clock=None) -> Orchestrator:
    return Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ScriptedExecutor({}),
                        sources_factory=lambda project: FakeSources({}), clock=clock or Clock())


def _workers(**overrides) -> dict[str, FakeWorker]:
    return {s: overrides.get(s, FakeWorker()) for s in DISCOVERY if s != "intake"}


def _drive(o: Orchestrator, run_id: str, *, limit: int = 40) -> list[str]:
    out: list[str] = []
    for _ in range(limit):
        out.append(o.step("demo", run_id, actor="tester"))
        if out[-1] in STOP:
            return out
    raise AssertionError(f"run did not settle: {out}")


def _events(ps, type_: str) -> list[dict]:
    return [json.loads(r["payload"]) for r in
            ps.db.execute("SELECT payload FROM events WHERE type=? ORDER BY seq", (type_,))]


def _passed(ps) -> list[str]:
    return [r["stage"] for r in stage_rows(ps) if r["status"] == "passed"]


@pytest.fixture
def project(tmp_home, replay_http, request):
    overrides = getattr(request, "param", {})
    k, ps, _ = make_project(tmp_home, replay_http, **overrides)
    yield k, ps
    k.close()


def test_graph_sequences():
    assert stages_for("critical_review")[:5] == list(DISCOVERY)
    assert "design" in stages_for("research_proposal") and "design" not in stages_for("systematic_review")
    assert stages_for("research_proposal").index("design") == stages_for("research_proposal").index("argue") + 1
    assert OPEN_STAGES == {"landscape", "gaps", "topics"}
    with pytest.raises(ValueError):
        stages_for("essay")


def test_d2_runs_stages_in_order_until(project):
    k, ps = project
    w = _workers()
    o = _orch(k, w)
    run_id = start_run(ps, until="gaps", actor="tester", clock=Clock())
    statuses = _drive(o, run_id)
    assert statuses[-1] == "done"
    assert [p["stage"] for p in _events(ps, "stage.passed")] == ["intake", "landscape", "gaps"]
    assert w["topics"].calls == [] and w["question"].calls == []
    assert current_stage(ps) == "topics"
    row = run_row(ps, run_id)
    assert row["status"] == "done" and row["ended"] is not None
    assert [r["status"] for r in stage_rows(ps)] == ["passed", "passed", "passed", "pending", "pending"]


def test_gate_failure_reruns_with_failures_as_guidance(project):
    k, ps = project
    landscape = FakeWorker(gates=[GateResult(False, ["cluster 'sleep' has 3 of 5 verified sources"])])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "done"
    runs = [c for c in landscape.calls if c[0] == "run"]
    assert [c[1] for c in runs] == [1, 2]
    assert "cluster 'sleep' has 3 of 5 verified sources" in runs[1][2]
    assert "3 of 5" not in (runs[0][2] or "")
    assert edge_count(ps, "landscape->landscape") == 1
    gates = _events(ps, "stage.gate")
    assert [(g["stage"], g["attempt"], g["ok"]) for g in gates if g["stage"] == "landscape"] == \
        [("landscape", 1, False), ("landscape", 2, True)]
    assert latest_attempt(ps, "landscape") == 2
    attempts = [a for a in _events(ps, "stage.attempt") if a["stage"] == "landscape"]
    assert attempts[1]["run_id"] == run_id and "3 of 5" in attempts[1]["guidance"]


def test_back_edge_reopens_downstream(project):
    k, ps = project
    topics = FakeWorker(runs=[StageOutcome("back_edge", to="gaps", reason="gaps too thin for 6 topics")])
    w = _workers(topics=topics)
    o = _orch(k, w)
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    for _ in range(4):  # intake, landscape, gaps, topics (back-edge)
        assert o.step("demo", run_id, actor="tester") == "running"
    assert _passed(ps) == ["intake", "landscape"]
    assert current_stage(ps) == "gaps"
    assert edge_count(ps, "topics->gaps") == 1
    assert [(r["stage"], r["by_edge"]) for r in _events(ps, "stage.reopened")] == \
        [("gaps", "topics->gaps"), ("topics", "topics->gaps")]
    assert _drive(o, run_id)[-1] == "done"
    gaps_runs = [c for c in w["gaps"].calls if c[0] == "run"]
    assert [c[1] for c in gaps_runs] == [1, 2] and "gaps too thin" in gaps_runs[1][2]
    assert [c[1] for c in topics.calls if c[0] == "run"] == [1, 2]
    assert _passed(ps) == ["intake", "landscape", "gaps", "topics"]


@pytest.mark.parametrize("project", [{"production_mode": "collab"}], indirect=True)
def test_third_use_of_an_edge_opens_stage_blocked(project):
    k, ps = project
    landscape = FakeWorker(gates=[GateResult(False, [f"failure {i}"]) for i in range(1, 10)])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    assert edge_count(ps, "landscape->landscape") == 2
    assert [c[1] for c in landscape.calls if c[0] == "run"] == [1, 2, 3]
    [blocked] = list_checkpoints(ps, "open")
    assert blocked["kind"] == "stage_blocked" and blocked["stage"] == "landscape"
    assert blocked["options"]["choices"] == ["allow_one_more", "stop"]
    assert blocked["options"]["failures"] == ["failure 3"]
    assert "degrade" not in landscape.kinds()
    # Still open: nothing runs.
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    assert len([c for c in landscape.calls if c[0] == "run"]) == 3

    answer_checkpoint(ps, blocked["id"], {"decision": "allow_one_more"}, answered_by="human", actor="tester")
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    assert [c[1] for c in landscape.calls if c[0] == "run"] == [1, 2, 3, 4]
    assert edge_count(ps, "landscape->landscape") == 3
    assert [e["edge"] for e in _events(ps, "edge.allowed")] == ["landscape->landscape"]
    [second] = list_checkpoints(ps, "open")
    assert second["kind"] == "stage_blocked" and second["id"] != blocked["id"]

    answer_checkpoint(ps, second["id"], {"decision": "stop"}, answered_by="human", actor="tester")
    assert o.step("demo", run_id, actor="tester") == "failed"
    row = run_row(ps, run_id)
    assert row["status"] == "failed" and "landscape->landscape" in row["reason"]
    assert len([c for c in landscape.calls if c[0] == "run"]) == 4


def _degraded_gate(ps):
    return GateResult(True, []) if _events(ps, "stage.degraded") else GateResult(False, ["2 clusters under 5"])


def test_p1_limit_calls_degrade(project):
    k, ps = project

    def degrade(ctx):
        record_degraded(ctx.ps, stage=ctx.stage, attempt=ctx.attempt, removed=["cls_weak"],
                        reason="fewer than 5 verified sources", actor=ctx.actor)
        return StageOutcome("done")

    landscape = FakeWorker(gates=[_degraded_gate] * 10, degrades=[degrade])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "done"
    assert landscape.kinds() == ["run", "gate"] * 3 + ["degrade", "gate"]
    assert landscape.calls[6] == ("degrade", 3, ["2 clusters under 5"])
    assert _events(ps, "stage.degraded") == [{"stage": "landscape", "attempt": 3, "removed": ["cls_weak"],
                                              "reason": "fewer than 5 verified sources"}]
    assert _passed(ps) == ["intake", "landscape"]
    assert list_checkpoints(ps) == []


def test_p1_degrade_failure_fails_the_run(project):
    k, ps = project
    gaps = FakeWorker(runs=[StageOutcome("rerun", reason=f"try {i}") for i in range(5)],
                      degrades=[StageOutcome("failed", reason="no verified gap within the searched scope")])
    o = _orch(k, _workers(gaps=gaps))
    run_id = start_run(ps, until="gaps", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "failed"
    assert run_row(ps, run_id)["reason"] == "no verified gap within the searched scope"
    assert gaps.kinds() == ["run", "run", "run", "degrade"]
    assert gaps.calls[-1] == ("degrade", 3, ["try 2"])
    assert list_checkpoints(ps) == []


def test_p1_gate_failure_after_degrade_fails_instead_of_looping(project):
    k, ps = project
    landscape = FakeWorker(gates=[GateResult(False, ["still thin"])] * 10, degrades=[StageOutcome("done")])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "failed"
    assert "still thin" in run_row(ps, run_id)["reason"]
    assert landscape.kinds().count("degrade") == 1


def test_open_checkpoint_pauses_and_answer_resumes(project):
    k, ps = project

    def review(ctx):
        cid = create_checkpoint(ctx.ps, "topic_review", "Pick a topic", {"topic_ids": ["tpc_a"]}, stage="topics",
                                actor=ctx.actor)
        return StageOutcome("waiting_human", checkpoint_id=cid)

    topics = FakeWorker(runs=[review])
    o = _orch(k, _workers(topics=topics))
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    assert run_row(ps, run_id)["status"] == "paused_waiting_human"
    [cp] = list_checkpoints(ps, "open")
    assert {r["stage"]: r["waiting_checkpoint"] for r in stage_rows(ps)}["topics"] == cp["id"]
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    assert topics.kinds() == ["run"]

    answer_checkpoint(ps, cp["id"], {"decision": "approve", "topic_id": "tpc_a"}, answered_by="human", actor="tester")
    assert o.step("demo", run_id, actor="tester") == "done"
    assert topics.calls[1] == ("on_answer", 1, cp["id"], {"decision": "approve", "topic_id": "tpc_a"})
    assert topics.kinds() == ["run", "on_answer", "gate"]
    assert {r["stage"]: r["waiting_checkpoint"] for r in stage_rows(ps)}["topics"] is None


def test_answer_of_an_older_checkpoint_is_ignored(project):
    k, ps = project
    stray = create_checkpoint(ps, "topic_review", "old review", None, stage="topics", actor="tester")
    answer_checkpoint(ps, stray, {"decision": "approve"}, answered_by="human", actor="tester")
    topics = FakeWorker()
    o = _orch(k, _workers(topics=topics))
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "done"
    assert "on_answer" not in topics.kinds()


def test_budget_and_wall_clock_pause(project):
    k, ps = project
    clock = Clock()
    landscape = FakeWorker(runs=[BudgetExceeded("landscape", 40, 40)])
    o = _orch(k, _workers(landscape=landscape), clock=clock)
    run_id = start_run(ps, until="gaps", actor="tester", clock=clock)
    assert _drive(o, run_id)[-1] == "paused_budget"
    row = run_row(ps, run_id)
    assert row["status"] == "paused_budget" and "40 of 40" in row["reason"]
    assert o.step("demo", run_id, actor="tester") == "paused_budget"
    assert landscape.kinds() == ["run"]

    k2_landscape = FakeWorker()
    o2 = _orch(k, _workers(landscape=k2_landscape, gaps=FakeWorker()), clock=clock)
    run2 = _restart(ps, run_id, clock)
    clock.t = run_row(ps, run2)["session_started"] + 12 * 3600 + 1
    assert o2.step("demo", run2, actor="tester") == "paused_budget"
    assert k2_landscape.calls == []
    assert "12 h" in run_row(ps, run2)["reason"]


def _restart(ps, run_id, clock):
    from rk.orchestrator.state import record_run_session, record_run_status

    record_run_session(ps, run_id, run_row(ps, run_id)["session"] + 1, actor="tester", clock=clock)
    record_run_status(ps, run_id, "running", actor="tester", clock=clock)
    assert run_row(ps, run_id)["session"] == 2
    return run_id


def test_rate_cap_waits_and_reruns_the_attempt(project):
    k, ps = project
    landscape = FakeWorker(runs=[RateCapped("claude_cli", 120.0)])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert run_row(ps, run_id)["status"] == "running"
    assert o.step("demo", run_id, actor="tester") == "done"
    assert [c[:2] for c in landscape.calls] == [("run", 1), ("run", 2), ("gate", 2)]
    assert edge_count(ps, "landscape->landscape") == 0


def test_on_answer_runs_again_after_a_rate_cap_and_settles_once(project):
    k, ps = project

    def review(ctx):
        cid = create_checkpoint(ctx.ps, "topic_review", "Pick a topic", None, stage="topics", actor=ctx.actor)
        return StageOutcome("waiting_human", checkpoint_id=cid)

    topics = FakeWorker(runs=[review], answers=[RateCapped("claude_cli", 60.0)])
    o = _orch(k, _workers(topics=topics))
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    [cp] = list_checkpoints(ps, "open")
    answer_checkpoint(ps, cp["id"], {"decision": "approve"}, answered_by="human", actor="tester")
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert run_row(ps, run_id)["status"] == "running"
    assert {r["stage"]: r["waiting_checkpoint"] for r in stage_rows(ps)}["topics"] == cp["id"]
    assert o.step("demo", run_id, actor="tester") == "done"
    assert [c[:3] for c in topics.calls if c[0] == "on_answer"] == [("on_answer", 1, cp["id"])] * 2
    assert topics.kinds() == ["run", "on_answer", "on_answer", "gate"]
    assert [p["stage"] for p in _events(ps, "stage.passed")].count("topics") == 1


def test_run_stage_refuses_later_and_plan5_stages(project):
    k, ps = project
    o = _orch(k, _workers())
    with pytest.raises(ToolError) as e:
        o.run_stage("demo", "protocol", actor="tester")
    assert e.value.type == "stage_unavailable"
    with pytest.raises(ToolError) as e:
        o.run_stage("demo", "topics", actor="tester")
    assert e.value.type == "stage_unavailable"
    assert o.run_stage("demo", "intake", actor="tester").status == "done"
    assert o.advance("demo", actor="tester").ok
    with pytest.raises(ToolError) as e:
        o.run_stage("demo", "intake", actor="tester")  # already passed
    assert e.value.type == "stage_unavailable"
    assert latest_attempt(ps, "topics") == 0


def test_stage_without_worker_is_unavailable(project):
    k, _ps = project
    o = Orchestrator(k, None, executor_factory=lambda p, r, s: ScriptedExecutor({}),
                     sources_factory=lambda p: FakeSources({}))
    del o.workers["landscape"]
    o.run_stage("demo", "intake", actor="tester")
    o.advance("demo", actor="tester")
    with pytest.raises(ToolError) as e:
        o.run_stage("demo", "landscape", actor="tester")
    assert e.value.type == "stage_unavailable"


def test_d1_advance_applies_the_answer_then_gates(project):
    k, ps = project

    def review(ctx):
        return StageOutcome("waiting_human", checkpoint_id=create_checkpoint(
            ctx.ps, "topic_review", "Pick", None, stage="landscape", actor=ctx.actor))

    landscape = FakeWorker(runs=[review], gates=[GateResult(False, ["not yet"])])
    o = _orch(k, _workers(landscape=landscape))
    o.run_stage("demo", "intake", actor="tester")
    assert o.advance("demo", actor="tester").ok
    out = o.run_stage("demo", "landscape", guidance="exclude animal studies", actor="tester")
    assert out.status == "waiting_human"
    assert landscape.calls[0] == ("run", 1, "exclude animal studies")
    res = o.advance("demo", actor="tester")
    assert not res.ok and "open" in res.failures[0]
    answer_checkpoint(ps, out.checkpoint_id, {"decision": "approve"}, answered_by="human", actor="tester")
    res = o.advance("demo", actor="tester")
    assert res == GateResult(False, ["not yet"])
    assert landscape.kinds() == ["run", "on_answer", "gate"]
    assert edge_count(ps, "landscape->landscape") == 0  # a gate failure takes no edge in D1 ...
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"
    assert edge_count(ps, "landscape->landscape") == 1  # ... running the stage again does
    assert landscape.calls[-1] == ("run", 2, "not yet")  # with the gate failures as guidance
    assert o.advance("demo", actor="tester").ok
    assert current_stage(ps) == "gaps"


def test_d1_back_edge_reopens_its_target(project):
    k, ps = project
    gaps = FakeWorker()
    o = _orch(k, _workers(gaps=gaps, topics=FakeWorker(runs=[StageOutcome("back_edge", to="gaps", reason="thin")])))
    for stage in ("intake", "landscape", "gaps"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    assert o.run_stage("demo", "topics", actor="tester").status == "back_edge"
    assert current_stage(ps) == "gaps" and edge_count(ps, "topics->gaps") == 1
    o.run_stage("demo", "gaps", actor="tester")
    assert gaps.calls[-1] == ("run", 2, "thin")
    assert edge_count(ps, "topics->gaps") == 1 and edge_count(ps, "gaps->gaps") == 0  # a reopened stage is no rerun


@pytest.mark.parametrize("project", [{"production_mode": "collab"}], indirect=True)
def test_d1_third_back_edge_opens_stage_blocked_in_p2(project):
    k, ps = project
    topics = FakeWorker(runs=[StageOutcome("back_edge", to="gaps", reason="thin")] * 4)
    o = _orch(k, _workers(topics=topics))
    for stage in ("intake", "landscape", "gaps"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    for _ in range(2):
        assert o.run_stage("demo", "topics", actor="tester").status == "back_edge"
        o.run_stage("demo", "gaps", actor="tester")
        assert o.advance("demo", actor="tester").ok
    out = o.run_stage("demo", "topics", actor="tester")
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert (cp["kind"], cp["status"], cp["options"]["edge"]) == ("stage_blocked", "open", "topics->gaps")
    assert cp["options"]["failures"] == ["thin"]
    assert edge_count(ps, "topics->gaps") == 2 and current_stage(ps) == "topics"
    assert {r["stage"]: r["waiting_checkpoint"] for r in stage_rows(ps)}["topics"] == cp["id"]
    assert "open" in o.advance("demo", actor="tester").failures[0]

    answer_checkpoint(ps, cp["id"], {"decision": "allow_one_more"}, answered_by="human", actor="tester")
    assert not o.advance("demo", actor="tester").ok
    assert edge_count(ps, "topics->gaps") == 3 and current_stage(ps) == "gaps"
    assert [e["edge"] for e in _events(ps, "edge.allowed")] == ["topics->gaps"]
    o.run_stage("demo", "gaps", actor="tester")
    assert o.advance("demo", actor="tester").ok
    out = o.run_stage("demo", "topics", actor="tester")  # the granted use is spent: ask again
    assert out.status == "waiting_human" and get_checkpoint(ps, out.checkpoint_id)["kind"] == "stage_blocked"
    assert edge_count(ps, "topics->gaps") == 3


def test_d1_third_rerun_calls_degrade_in_p1(project):
    k, ps = project

    def degrade(ctx):
        record_degraded(ctx.ps, stage=ctx.stage, attempt=ctx.attempt, removed=["cls_weak"],
                        reason="fewer than 5 verified sources", actor=ctx.actor)
        return StageOutcome("done")

    landscape = FakeWorker(gates=[_degraded_gate] * 10, degrades=[degrade])
    o = _orch(k, _workers(landscape=landscape))
    o.run_stage("demo", "intake", actor="tester")
    assert o.advance("demo", actor="tester").ok
    for _ in range(3):
        assert o.run_stage("demo", "landscape", actor="tester").status == "done"
        assert not o.advance("demo", actor="tester").ok
    assert edge_count(ps, "landscape->landscape") == 2
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"
    assert landscape.kinds() == ["run", "gate"] * 3 + ["degrade", "gate"]
    assert landscape.calls[6] == ("degrade", 3, ["2 clusters under 5"])
    assert current_stage(ps) == "gaps" and list_checkpoints(ps) == []
    assert edge_count(ps, "landscape->landscape") == 2


def test_d1_degrade_failure_returns_failed(project):
    k, ps = project
    landscape = FakeWorker(gates=[GateResult(False, ["thin"])] * 10,
                           degrades=[StageOutcome("failed", reason="no cluster with 5 verified sources")])
    o = _orch(k, _workers(landscape=landscape))
    o.run_stage("demo", "intake", actor="tester")
    o.advance("demo", actor="tester")
    for _ in range(3):
        o.run_stage("demo", "landscape", actor="tester")
        o.advance("demo", actor="tester")
    out = o.run_stage("demo", "landscape", actor="tester")
    assert (out.status, out.reason) == ("failed", "no cluster with 5 verified sources")
    assert current_stage(ps) == "landscape" and landscape.kinds().count("run") == 3


def test_intake_worker(project):
    _k, ps = project
    w = IntakeWorker()
    assert w.gate(ps, 1) == GateResult(True, [])
    ps.db.execute("DELETE FROM meta WHERE key='audience'")
    ps.db.commit()
    res = w.gate(ps, 1)
    assert not res.ok and any("audience" in f for f in res.failures)


def test_intake_missing_fails_the_run(tmp_home, replay_http):
    from rk.config import Settings, save_settings
    from rk.workspace import Kernel

    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    try:
        k.create_project("demo", "Demo", actor="tester")
        ps, _ = k.project("demo")
        o = _orch(k, _workers())
        run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
        assert o.step("demo", run_id, actor="tester") == "failed"
        assert run_row(ps, run_id)["reason"] == "intake missing"
    finally:
        k.close()


def test_start_run_refuses_unknown_until_and_a_second_run(project):
    _k, ps = project
    with pytest.raises(ToolError) as e:
        start_run(ps, until="protocol", actor="tester", clock=Clock())
    assert e.value.type == "stage_unavailable"
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    with pytest.raises(ToolError) as e:
        start_run(ps, until="topics", actor="tester", clock=Clock())
    assert e.value.type == "run_active"
    row = run_row(ps, run_id)
    assert (row["status"], row["until"], row["session"], row["session_started"]) == ("running", "topics", 1, T0)


def test_state_rebuilds_from_events(project):
    k, ps = project
    topics = FakeWorker(runs=[StageOutcome("back_edge", to="gaps", reason="thin")])
    o = _orch(k, _workers(topics=topics))
    run_id = start_run(ps, until="topics", actor="tester", clock=Clock())
    _drive(o, run_id)
    before = (stage_rows(ps), run_row(ps, run_id), edge_count(ps, "topics->gaps"))
    rebuild(ps.db)
    assert (stage_rows(ps), run_row(ps, run_id), edge_count(ps, "topics->gaps")) == before


def test_stage_blocked_is_a_soft_checkpoint_kind(project):
    _k, ps = project
    cid = create_checkpoint(ps, "stage_blocked", "limit", None, stage="gaps", actor="tester")
    answer_checkpoint(ps, cid, {"decision": "stop"}, answered_by="vote", actor="tester")  # not refused by kind
    cp = get_checkpoint(ps, cid)
    assert (cp["status"], cp["answered_by"]) == ("answered", "vote")
