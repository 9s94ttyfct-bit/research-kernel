"""The stage engine: attempts, gates, back-edges and their limits, budgets, rate caps and checkpoint waits (Plan 4
Task 3)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from pydantic import BaseModel

from rk.checkpoints import (
    answer_checkpoint,
    create_checkpoint,
    get_checkpoint,
    list_checkpoints,
)
from rk.config import Settings
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.discovery.gaps import GapsWorker, MinerResult
from rk.discovery.models import Gap, GapEvidence
from rk.discovery.store import gaps
from rk.llm.chat.ollama import OllamaBackend
from rk.llm.chat_provider import ChatApiProvider
from rk.llm.cli_base import Completed
from rk.llm.cli_claude import ClaudeCliProvider
from rk.llm.types import TaskResult
from rk.orchestrator.engine import GateResult, IntakeWorker, Orchestrator, StageOutcome
from rk.orchestrator.graph import DISCOVERY, OPEN_STAGES, stages_for
from rk.orchestrator.state import (
    current_stage,
    edge_count,
    edge_rows,
    latest_attempt,
    rate_capped_driver,
    record_degraded,
    run_row,
    stage_rows,
    start_run,
    tasks_used,
)
from rk.production.vote import vote
from rk.store.projections import rebuild
from rk.tools.registry import ToolError

from .discovery_fakes import FakeSources, FakeWorker, ScriptedExecutor, make_project

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}


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
    assert OPEN_STAGES == {"landscape", "gaps", "topics", "protocol", "argue"}
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
    assert [r["status"] for r in stage_rows(ps)] == ["passed"] * 3 + ["pending"] * 11  # every critical_review stage


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


def test_answer_after_the_session_clock_starts_a_new_session(project):
    """Waiting for the researcher does not use up the run: answering topic_review 13 h after the session started
    continues the run in a new session instead of pausing it on the 12 h wall clock."""
    k, ps = project
    clock = Clock()

    def review(ctx):
        cid = create_checkpoint(ctx.ps, "topic_review", "Pick", {"topic_ids": ["tpc_a"]}, stage="topics",
                                actor=ctx.actor)
        return StageOutcome("waiting_human", checkpoint_id=cid)

    topics = FakeWorker(runs=[review])
    o = _orch(k, _workers(topics=topics), clock=clock)
    run_id = start_run(ps, until="topics", actor="tester", clock=clock)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    clock.t = T0 + 13 * 3600
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"  # still open: no new session
    assert run_row(ps, run_id)["session"] == 1
    [cp] = list_checkpoints(ps, "open")
    answer_checkpoint(ps, cp["id"], {"decision": "approve", "topic_id": "tpc_a"}, answered_by="human", actor="tester")
    assert o.step("demo", run_id, actor="tester") == "done"
    row = run_row(ps, run_id)
    assert (row["session"], row["session_started"]) == (2, T0 + 13 * 3600)
    assert topics.kinds() == ["run", "on_answer", "gate"]


def test_wall_clock_restarts_with_each_session(project):
    """The 12 h wall clock counts from the start of the current session: a run paused on it resumes in a new session
    with a fresh 12 h, and pauses again only 12 h after that."""
    from rk.orchestrator.runs import RunManager

    k, ps = project
    clock = Clock()

    def long(ctx):
        clock.t += 12 * 3600 + 1
        return StageOutcome("done")

    landscape, gaps_w, topics = FakeWorker(runs=[long]), FakeWorker(runs=[long]), FakeWorker()
    o = _orch(k, _workers(landscape=landscape, gaps=gaps_w, topics=topics), clock=clock)
    m = RunManager(k, None, o, poll_s=0.01)
    try:
        run_id = m.start("demo", "topics", actor="tester")
        until = time.monotonic() + 10
        for session, stage in ((1, "gaps"), (2, "topics")):
            while run_row(ps, run_id)["status"] != "paused_budget" or m.status("demo")["stepping"]:
                assert time.monotonic() < until
                time.sleep(0.01)
            row = run_row(ps, run_id)
            assert row["session"] == session and f"run session {session} reached its 12 h" in row["reason"]
            assert current_stage(ps) == stage
            m.resume("demo")
            assert run_row(ps, run_id)["session_started"] == clock.t
        while run_row(ps, run_id)["status"] != "done":
            assert time.monotonic() < until
            time.sleep(0.01)
        assert run_row(ps, run_id)["session"] == 3 and topics.kinds() == ["run", "gate"]
    finally:
        m.close()


def test_rate_cap_waits_and_reruns_the_attempt(project):
    k, ps = project
    landscape = FakeWorker(runs=[StageOutcome("rerun", reason="try harder"), RateCapped("claude_cli", 120.0)])
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    assert o.step("demo", run_id, actor="tester") == "running"  # rerun: landscape->landscape
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert run_row(ps, run_id)["status"] == "running"
    assert o.step("demo", run_id, actor="tester") == "done"
    # the interrupted attempt 2 reruns as attempt 3 with its guidance, without another use of the edge
    assert [c[:3] for c in landscape.calls] == [("run", 1, ""), ("run", 2, "try harder"), ("run", 3, "try harder"),
                                                ("gate", 3)]
    assert edge_count(ps, "landscape->landscape") == 1


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


def test_interrupted_attempt_of_a_resuming_worker_runs_again_in_place(project):
    """A worker that resumes in place (``resumes_in_place``) gets its interrupted attempt again, not a new one: the
    same attempt number and guidance, a ``stage.resumed`` event, and the attempt's open checkpoint left open (no
    ``supersede_open``); no edge is used."""
    k, ps = project
    opened = {}

    def open_then_cap(ctx):
        opened["id"] = create_checkpoint(ctx.ps, "topics_review", "Pick.", {"choices": ["a"]}, stage=ctx.stage,
                                         actor="tester")
        raise RateCapped("claude_cli", 60.0)

    landscape = FakeWorker(runs=[StageOutcome("rerun", reason="try harder"), open_then_cap,
                                 RuntimeError("crash"), StageOutcome("done")])
    landscape.resumes_in_place = True
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    assert o.step("demo", run_id, actor="tester") == "running"  # rerun: landscape->landscape
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    with pytest.raises(RuntimeError, match="crash"):
        o.step("demo", run_id, actor="tester")
    assert o.step("demo", run_id, actor="tester") == "done"
    assert [c[:3] for c in landscape.calls] == [("run", 1, ""), ("run", 2, "try harder"), ("run", 2, "try harder"),
                                                ("run", 2, "try harder"), ("gate", 2)]
    assert latest_attempt(ps, "landscape") == 2 and edge_count(ps, "landscape->landscape") == 1
    assert [(e["stage"], e["attempt"]) for e in _events(ps, "stage.resumed")] == [("landscape", 2)] * 2
    assert [e["attempt"] for e in _events(ps, "stage.attempt") if e["stage"] == "landscape"] == [1, 2]
    assert get_checkpoint(ps, opened["id"])["status"] == "open"


def test_resume_that_stores_nothing_three_times_starts_a_new_attempt(project):
    """An attempt that crashes at the same place on every resume is given up after three resumes that stored nothing:
    the reason is recorded (``stage.resume_abandoned``) and a new attempt starts."""
    k, ps = project
    landscape = FakeWorker(runs=[RuntimeError("crash")] * 4 + [StageOutcome("done")])
    landscape.resumes_in_place = True
    o = _orch(k, _workers(landscape=landscape))
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    for _ in range(4):
        with pytest.raises(RuntimeError, match="crash"):
            o.step("demo", run_id, actor="tester")
    assert o.step("demo", run_id, actor="tester") == "done"
    assert [c[1] for c in landscape.calls if c[0] == "run"] == [1, 1, 1, 1, 2]
    (gave_up,) = _events(ps, "stage.resume_abandoned")
    assert gave_up["attempt"] == 1 and "resumed 3 times in a row without storing anything" in gave_up["reason"]
    assert len(_events(ps, "stage.resumed")) == 3


class _Found(BaseModel):
    found: bool


class _ScriptedClaude(ClaudeCliProvider):
    """``claude_cli`` whose process is scripted: each run takes the next ``(exit code, stdout)``; none is started."""

    def __init__(self, replies: list[tuple[int, str]]):
        super().__init__(Settings())
        self.replies, self.runs = list(replies), 0

    def bin_path(self) -> str:
        return "claude"

    def execute(self, argv, cwd, timeout_s) -> Completed:
        self.runs += 1
        code, out = self.replies.pop(0)
        return Completed(code, out, "", 0.1)


class _AskOnce:
    """A stage that asks the arguer once and reruns when it gets no answer, as synthesize, argue and design do."""
    resumes_in_place = True

    def __init__(self):
        self.runs: list[int] = []

    def run(self, ctx):
        self.runs.append(ctx.attempt)
        r = ctx.executor.run("arguer", "Answer.", {}, _Found, stage=ctx.stage, attempt=ctx.attempt, tools=[])
        return StageOutcome("done") if r.ok else StageOutcome("rerun", reason=f"no answer ({r.error})")

    def on_answer(self, ctx, checkpoint):
        return StageOutcome("failed", reason="no checkpoint")

    def gate(self, ps, attempt):
        return GateResult(True, [])

    def degrade(self, ctx, failures):
        return StageOutcome("failed", reason="nothing to degrade")


def test_session_limit_waits_without_spending_a_rerun(project):
    """I2: the Claude CLI's "You've hit your session limit" reply is no failed task. The run waits (``waiting_rate``)
    until the limit resets: no rerun, no back-edge, no new attempt, and no call reaches the CLI before the reset."""
    k, ps = project
    now = [T0]
    k.caps._clock = lambda: now[0]
    limit = json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                        "result": "You've hit your session limit · resets 3pm (UTC)", "session_id": "s1"})
    ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                     "structured_output": {"found": True}, "result": "{}", "session_id": "s2"})
    claude, worker = _ScriptedClaude([(1, limit), (0, ok)]), _AskOnce()
    o = Orchestrator(k, None, workers=_workers(landscape=worker),
                     executor_factory=lambda project, run_id, session: KernelExecutor(
                         k, None, project, run_id=run_id, session=session, provider_for=lambda role: claude),
                     sources_factory=lambda project: FakeSources({}), clock=Clock())
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert claude.runs == 1 and edge_rows(ps) == {} and latest_attempt(ps, "landscape") == 1
    assert rate_capped_driver(ps, run_id) == "claude_cli"  # what the run manager waits on
    wait = k.caps.wait_s("claude_cli")
    assert 60 <= wait <= 24 * 3600
    (task,) = ps.db.execute("SELECT status, error FROM tasks").fetchall()
    assert tuple(task) == ("failed", "session_limited")
    # before the reset no call reaches the CLI: the run keeps waiting
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert claude.runs == 1 and latest_attempt(ps, "landscape") == 1
    now[0] += wait + 1
    assert o.step("demo", run_id, actor="tester") == "done"
    assert claude.runs == 2 and worker.runs == [1, 1, 1] and edge_rows(ps) == {}
    assert latest_attempt(ps, "landscape") == 1 and tasks_used(ps, run_id, 1, "landscape") == 1


class _VoteOut(BaseModel):
    choice: str
    reason: str = ""


class _Voter:
    """A judge provider that always votes ``a``."""
    kind, model, vendor = "chat_api", "fake", "x"

    def available(self):
        return None

    def run(self, spec, ctx):
        out = _VoteOut(choice="a", reason="fine")
        return TaskResult(spec.task_id, True, out, "{}", "codex_cli", "fake", 0.0, None)


class _VoteOnce(_AskOnce):
    """A stage that asks the judges' P1 vote once."""

    def run(self, ctx):
        self.runs.append(ctx.attempt)
        vote(ctx, "record", "rec_1", "Include it?", ["a", "b"], "b", {"title": "x"})
        return StageOutcome("done")


@pytest.mark.parametrize(("headers", "body", "http_calls", "wait"), [
    ({"retry-after": "120"}, {"error": "slow down"}, 1, 120),  # says how long: a usage limit at once
    ({}, {"error": "you have reached your monthly usage limit"}, 1, 1800),
    ({}, {"error": "too many requests"}, 3, 1800),  # a plain 429: retried, then a usage limit
])
def test_chat_api_429_in_a_judge_vote_waits_without_a_conservative_vote(project, headers, body, http_calls, wait):
    """I-3: an HTTP 429 of a chat API judge (Ollama) is a usage limit, not a failed judge: no conservative vote is
    recorded and the run waits (``waiting_rate``) until the driver may be called again."""
    k, ps = project
    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(429, json=body, headers=headers)

    ollama = ChatApiProvider(OllamaBackend("http://ollama:1", "deepseek-v4.1-flash:cloud",
                                           httpx.Client(transport=httpx.MockTransport(answer))), "ollama")
    voter = _Voter()
    o = Orchestrator(k, None, workers=_workers(landscape=_VoteOnce()),
                     executor_factory=lambda project, run_id, session: KernelExecutor(
                         k, None, project, run_id=run_id, session=session,
                         provider_for=lambda role: ollama if role == "judge_2" else voter),
                     sources_factory=lambda project: FakeSources({}), clock=Clock())
    run_id = start_run(ps, until="landscape", actor="tester", clock=Clock())
    assert o.step("demo", run_id, actor="tester") == "running"  # intake
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert len(calls) == http_calls and ps.db.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0
    assert edge_rows(ps) == {} and latest_attempt(ps, "landscape") == 1
    assert rate_capped_driver(ps, run_id).startswith("ollama")
    assert k.caps.wait_s("ollama") == pytest.approx(wait, abs=5)
    errors = [r["error"] for r in ps.db.execute("SELECT error FROM tasks ORDER BY created")]
    assert errors[-1] == "session_limited"


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


def _open_review(ctx):
    return StageOutcome("waiting_human", checkpoint_id=create_checkpoint(
        ctx.ps, "topic_review", "Pick", {"attempt": ctx.attempt}, stage="topics", actor=ctx.actor))


def _to_topics(o, ps):
    for stage in ("intake", "landscape", "gaps"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    assert current_stage(ps) == "topics"


def test_d1_rerun_does_not_supersede_an_open_review(project):
    """Running the stage again while its review is open would leave the researcher answering a review that is never
    applied: it returns the open review instead, with no new attempt and no edge."""
    k, ps = project
    topics = FakeWorker(runs=[_open_review, _open_review])
    o = _orch(k, _workers(topics=topics))
    _to_topics(o, ps)
    first = o.run_stage("demo", "topics", actor="tester")
    again = o.run_stage("demo", "topics", guidance="other topics", actor="tester")
    assert again.status == "waiting_human" and again.checkpoint_id == first.checkpoint_id
    assert "is open" in again.reason
    assert topics.kinds() == ["run"] and edge_count(ps, "topics->topics") == 0
    assert [c["id"] for c in list_checkpoints(ps, "open")] == [first.checkpoint_id]


def test_orphaned_review_of_an_interrupted_attempt_is_superseded(project):
    """A crash between opening a review and recording that the attempt ran leaves an open review no attempt waits
    on; the next attempt supersedes it: hidden from the open list, and an answer to it is refused."""
    k, ps = project
    orphan: dict = {}

    def crash(ctx):
        orphan["id"] = _open_review(ctx).checkpoint_id
        raise RuntimeError("the kernel crashed")

    topics = FakeWorker(runs=[crash, _open_review])
    o = _orch(k, _workers(topics=topics))
    _to_topics(o, ps)
    with pytest.raises(RuntimeError):
        o.run_stage("demo", "topics", actor="tester")
    out = o.run_stage("demo", "topics", actor="tester")
    old = get_checkpoint(ps, orphan["id"])
    assert old["status"] == "superseded"
    assert [c["id"] for c in list_checkpoints(ps, "open")] == [out.checkpoint_id]
    assert [c["id"] for c in list_checkpoints(ps, "superseded")] == [orphan["id"]]
    with pytest.raises(ValueError) as e:
        answer_checkpoint(ps, orphan["id"], {"decision": "approve"}, answered_by="human", actor="tester")
    assert str(e.value) == (f"checkpoint {orphan['id']} was superseded: attempt 2 of 'topics' replaced the attempt it "
                            f"belonged to; answer the open checkpoint of 'topics' instead")
    rebuild(ps.db)
    assert get_checkpoint(ps, orphan["id"])["status"] == "superseded"


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
    assert out.status == "failed" and out.reason.startswith("dead end: landscape->landscape reached its limit")
    assert "no cluster with 5 verified sources" in out.reason
    assert current_stage(ps) == "landscape" and landscape.kinds().count("run") == 3


@pytest.mark.parametrize("interruption", [RateCapped("claude_cli", 60.0), RuntimeError("the kernel crashed")])
def test_d1_interrupted_attempt_reruns_as_a_new_attempt_without_an_edge(project, interruption):
    k, ps = project
    landscape = FakeWorker(runs=[interruption])
    o = _orch(k, _workers(landscape=landscape))
    o.run_stage("demo", "intake", actor="tester")
    assert o.advance("demo", actor="tester").ok
    with pytest.raises(type(interruption)):
        o.run_stage("demo", "landscape", guidance="exclude animal studies", actor="tester")
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"
    assert landscape.calls == [("run", 1, "exclude animal studies"), ("run", 2, "exclude animal studies")]
    assert edge_count(ps, "landscape->landscape") == 0
    # an attempt that finished is still rerun as a use of X->X
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"
    assert edge_count(ps, "landscape->landscape") == 1 and latest_attempt(ps, "landscape") == 3


@pytest.mark.parametrize("interruption", [RateCapped("claude_cli", 60.0), RuntimeError("the kernel crashed")])
def test_d1_advance_does_not_gate_an_interrupted_attempt(project, interruption):
    k, ps = project
    landscape = FakeWorker(runs=[interruption])
    o = _orch(k, _workers(landscape=landscape))
    o.run_stage("demo", "intake", actor="tester")
    assert o.advance("demo", actor="tester").ok
    with pytest.raises(type(interruption)):
        o.run_stage("demo", "landscape", actor="tester")
    res = o.advance("demo", actor="tester")
    assert res == GateResult(False, ["'landscape' attempt 1 was interrupted before it finished; run the stage again"])
    assert landscape.kinds() == ["run"] and _events(ps, "stage.gate")[-1]["stage"] == "intake"
    assert current_stage(ps) == "landscape"
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"
    assert o.advance("demo", actor="tester").ok and current_stage(ps) == "gaps"


def _one_gap(ctx, clusters):
    return MinerResult(gaps=[(Gap(kind="evidence_map_hole", statement=f"hole of attempt {ctx.attempt}",
                                  evidence=GapEvidence()), True, {"targeted_search": "pass"})])


@pytest.mark.parametrize("mode", ["d1", "d2"])
def test_interrupted_gaps_attempt_keeps_its_gaps_out_of_the_rerun(project, mode):
    k, ps = project
    interrupted: list[int] = []

    def flaky(ctx, clusters):
        if not interrupted:
            interrupted.append(ctx.attempt)
            raise RateCapped("claude_cli", 60.0)
        return MinerResult()

    o = _orch(k, {**_workers(), "gaps": GapsWorker(miners=[_one_gap, flaky])})
    if mode == "d2":
        run_id = start_run(ps, until="gaps", actor="tester", clock=Clock())
        assert "waiting_rate" in _drive(o, run_id) and run_row(ps, run_id)["status"] == "done"
    else:
        for stage in ("intake", "landscape"):
            o.run_stage("demo", stage, actor="tester")
            assert o.advance("demo", actor="tester").ok
        with pytest.raises(RateCapped):
            o.run_stage("demo", "gaps", actor="tester")
        assert o.run_stage("demo", "gaps", actor="tester").status == "done"
        assert o.advance("demo", actor="tester").ok
    assert interrupted == [1] and latest_attempt(ps, "gaps") == 2
    assert [g["statement"] for g in gaps(ps, 1)] == ["hole of attempt 1"]
    assert [g["statement"] for g in gaps(ps, 2)] == ["hole of attempt 2"]
    assert edge_count(ps, "gaps->gaps") == 0


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
    with pytest.raises(ToolError) as e:  # design is a research_proposal stage, not one of this critical_review
        start_run(ps, until="design", actor="tester", clock=Clock())
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


def test_stage_blocked_is_answered_by_a_human_only(project):
    """Allowing one more use of an edge is the researcher's decision: a P1 vote may never answer stage_blocked."""
    from rk.checkpoints import HARD_HUMAN_KINDS

    _k, ps = project
    assert "stage_blocked" in HARD_HUMAN_KINDS
    cid = create_checkpoint(ps, "stage_blocked", "limit", None, stage="gaps", actor="tester")
    with pytest.raises(ValueError, match="stage_blocked checkpoints can only be answered by a human"):
        answer_checkpoint(ps, cid, {"decision": "stop"}, answered_by="vote", actor="tester")
    assert get_checkpoint(ps, cid)["status"] == "open"
    answer_checkpoint(ps, cid, {"decision": "stop"}, answered_by="human", actor="tester")
    assert get_checkpoint(ps, cid)["answered_by"] == "human"
