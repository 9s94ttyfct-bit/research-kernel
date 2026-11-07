"""Runs in ``rk serve``: the RunManager's threads, resume after a restart, the run and stage tools and the CLI (Plan 4
Task 9)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from rk.checkpoints import answer_checkpoint, create_checkpoint, list_checkpoints
from rk.discovery.executor import KernelExecutor, RateCapped
from rk.discovery.models import Gap, GapEvidence, HypothesisIn, Intake, RQBody, Topic
from rk.discovery.store import (
    add_gap,
    add_rq,
    add_topic,
    freeze_rq,
    record_intake,
    rounds,
)
from rk.llm.drivers import DriverSpec
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    current_stage,
    latest_run,
    run_row,
    stage_rows,
    tasks_used,
)
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.verify.judge import JudgeVerdict
from rk.workspace import Kernel

from .discovery_fakes import (
    DEFAULT_INTAKE,
    FakeJudge,
    FakeSources,
    FakeWorker,
    ScriptedExecutor,
    make_project,
)

TIMEOUT = 10.0


def _wait(pred, timeout: float = TIMEOUT):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def _workers(**overrides) -> dict[str, FakeWorker]:
    return {s: overrides.get(s, FakeWorker()) for s in DISCOVERY if s != "intake"}


def _orch(k, reg, workers: dict, executor_factory=None) -> Orchestrator:
    return Orchestrator(k, reg, workers=workers,
                        executor_factory=executor_factory or (lambda project, run_id, session: ScriptedExecutor({})),
                        sources_factory=lambda project: FakeSources({}))


def _ctx(role: str = "cli") -> CallContext:
    return CallContext(role=role, actor="tester", idempotency_key=uuid.uuid4().hex)


def _review(ctx) -> StageOutcome:
    cid = create_checkpoint(ctx.ps, "topic_review", "Which topic?", {"topics": []}, stage="topics", actor="tester")
    return StageOutcome("waiting_human", checkpoint_id=cid)


def _run_threads(slug: str = "demo") -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == f"rk-run-{slug}"]


@pytest.fixture
def env(tmp_home, replay_http):
    """``make(workers, **kw)`` builds a registry, an Orchestrator with ``workers`` and a RunManager over one kernel
    with the d2 project ``demo``; every manager is closed (its threads joined) before the kernel."""
    k, ps, lib = make_project(tmp_home, replay_http)
    managers: list[RunManager] = []

    def make(workers, *, executor_factory=None, kernel=None, **kw):
        kern = kernel or k
        holder: dict = {}
        reg = build_registry(kern, runs=lambda: holder.get("m"))
        m = RunManager(kern, reg, _orch(kern, reg, workers, executor_factory), poll_s=kw.pop("poll_s", 0.02), **kw)
        holder["m"] = m
        managers.append(m)
        return m, reg

    yield k, ps, lib, make
    for m in managers:
        m.close()
    assert not [t.name for t in threading.enumerate() if t.name.startswith("rk-run-")]
    k.close()


def test_run_resumes_after_restart_without_rerunning_passed_stages(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    w = _workers(topics=FakeWorker(runs=[_review]))
    reg = build_registry(k)
    m = RunManager(k, reg, _orch(k, reg, w), poll_s=0.02)
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    assert current_stage(ps) == "topics"
    calls = {s: len(w[s].calls) for s in ("landscape", "gaps")}
    assert all(calls.values())
    m.close()
    assert not _run_threads()
    k.close()

    k2 = Kernel(tmp_home, http=replay_http)
    try:
        ps2, _ = k2.project("demo")
        assert run_row(ps2, run_id)["status"] == "paused_waiting_human"
        reg2 = build_registry(k2)
        m2 = RunManager(k2, reg2, _orch(k2, reg2, w), poll_s=0.02)
        try:
            m2.resume_all()
            assert len(_run_threads()) == 1
            (cp,) = list_checkpoints(ps2, "open")
            answer_checkpoint(ps2, cp["id"], {"decision": "approve"}, answered_by="human", actor="tester")
            _wait(lambda: run_row(ps2, run_id)["status"] == "done")
        finally:
            m2.close()
        assert [r["status"] for r in stage_rows(ps2)][:6] == ["passed"] * 5 + ["pending"]
        assert {s: len(w[s].calls) for s in ("landscape", "gaps")} == calls
        assert ("on_answer", 1, cp["id"], {"decision": "approve"}) in w["topics"].calls
        assert w["question"].kinds() == ["run", "gate"]
        assert run_row(ps2, run_id)["session"] == 1
    finally:
        k2.close()


def test_one_run_per_project(env):
    _, ps, _, make = env
    m, _ = make(_workers(topics=FakeWorker(runs=[_review])))
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    with pytest.raises(ToolError) as e:
        m.start("demo", "topics", actor="tester")
    assert e.value.type == "run_active" and e.value.details["run_id"] == run_id
    assert [t.daemon for t in _run_threads()] == [True]
    m.cancel("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "cancelled")
    _wait(lambda: not _run_threads())
    assert m.status("demo")["run"]["id"] == run_id


def test_start_needs_a_d2_intake(tmp_home, replay_http):
    k, _, _ = make_project(tmp_home, replay_http, discovery_mode="d1")
    try:
        reg = build_registry(k)
        m = RunManager(k, reg, _orch(k, reg, _workers()))
        with pytest.raises(ToolError) as e:
            m.start("demo", "topics", actor="tester")
        assert e.value.type == "not_d2"
        k.create_project("bare", "No intake", actor="tester")
        with pytest.raises(ToolError) as e:
            m.start("bare", "topics", actor="tester")
        assert e.value.type == "intake_missing"
        with pytest.raises(ToolError) as e:
            m.start("nope", "topics", actor="tester")
        assert e.value.type == "not_found"
        assert not _run_threads("demo") and not _run_threads("bare")
        m.close()
    finally:
        k.close()


@pytest.mark.parametrize("judge_2", ["claude_cli", "codex_cli", "human", "ollama:mystery-model"])
def test_start_fails_fast_without_two_judge_vendors(env, judge_2):
    """A run whose judges cannot give two cross-vendor opinions could never pass topics or question: it fails at
    once, saying which judge is unusable, instead of after hours of stages and reviews."""
    from rk.config import save_settings

    k, ps, _, make = env
    s = k.settings()
    save_settings(s.model_copy(update={"drivers": {**s.drivers, "judge_2": judge_2}}))
    m, _ = make(_workers())
    with pytest.raises(ToolError) as e:
        m.start("demo", "question", actor="tester")
    assert e.value.type == "judges_missing" and "judge_2" in e.value.message and "two judges" in e.value.message
    assert latest_run(ps) is None and not _run_threads()


def test_start_runs_until_topics_or_question(env):
    _, ps, _, make = env
    m, _ = make(_workers())
    for until in ("intake", "landscape", "gaps", "synthesize"):
        with pytest.raises(ToolError) as e:
            m.start("demo", until, actor="tester")
        assert e.value.type == "stage_unavailable", until
    assert latest_run(ps) is None and not _run_threads()


def _d1(ps) -> None:
    """Switch the test project to guided discovery (as the researcher would, with no run active)."""
    record_intake(ps, Intake(**{**DEFAULT_INTAKE, "discovery_mode": "d1"}), actor="tester", actor_kind="human")


def _d1_idle(m, project: str = "demo") -> dict:
    """Wait until no guided stage call of the project runs; returns its ``d1_status``."""
    return _wait(lambda: (s := m.d1_status(project))["running"] is None and s)


def test_d1_stage_call_runs_in_the_background_and_holds_off_a_run(env):
    _, ps, _, make = env
    _d1(ps)
    entered, release = threading.Event(), threading.Event()

    def slow(ctx):
        entered.set()
        assert release.wait(TIMEOUT)
        return StageOutcome("done", reason="intake complete")

    m, reg = make({**_workers(), "intake": FakeWorker(runs=[slow])})
    try:
        t0 = time.monotonic()
        out = reg.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx("lead"))
        assert time.monotonic() - t0 < 2.0  # returned at once while the stage still runs
        assert (out["status"], out["stage"], out["attempt"], out["action"]) == ("running", "intake", 1, "run")
        assert "poll rk_stage_status" in out["message"]
        assert entered.wait(TIMEOUT)
        status = reg.call("rk_stage_status", {"project": "demo"}, _ctx("lead"))
        assert status["d1"]["running"]["stage"] == "intake" and status["d1"]["last"] is None
        for name, args in (("rk_stage_run", {"project": "demo", "stage": "intake"}),
                           ("rk_stage_advance", {"project": "demo"})):
            with pytest.raises(ToolError) as e:
                reg.call(name, args, _ctx("lead"))
            assert e.value.type == "stage_busy" and "rk_stage_status" in e.value.message
        intake = {"project": "demo", **DEFAULT_INTAKE, "discovery_mode": "d1", "audience": "nurses"}
        with pytest.raises(ToolError) as e:  # the intake the stage reads does not change under it
            reg.call("rk_project_intake", intake, _ctx("lead"))
        assert e.value.type == "stage_busy" and "rk_stage_status" in e.value.message
        with pytest.raises(ToolError) as e:  # resume checks the guided call first
            m.resume("demo")
        assert e.value.type == "run_active" and "guided" in e.value.message
        with pytest.raises(ToolError) as e:  # a D1 project never starts a run (and the marker would refuse it too)
            m.start("demo", "question", actor="tester")
        assert e.value.type in ("not_d2", "run_active")
        assert latest_run(ps) is None and not _run_threads()
    finally:
        release.set()
    last = _d1_idle(m)["last"]
    assert last["action"] == "run" and last["error"] is None
    assert (last["outcome"]["status"], last["outcome"]["reason"]) == ("done", "intake complete")
    out = reg.call("rk_stage_advance", {"project": "demo"}, _ctx("lead"))
    assert (out["status"], out["stage"]) == ("running", "intake")
    last = _d1_idle(m)["last"]
    assert last["outcome"]["ok"] is True and last["outcome"]["current"] == "landscape"


def test_d1_outcome_and_errors_survive_a_restart(env):
    _, ps, _, make = env
    _d1(ps)
    m, reg = make({**_workers(), "intake": FakeWorker(runs=[RuntimeError("disk full")])})
    reg.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx("lead"))
    assert _d1_idle(m)["last"]["error"] == "RuntimeError: disk full"
    m.close()
    m2, reg2 = make({**_workers(), "intake": FakeWorker()})  # rk serve restarted
    status = reg2.call("rk_stage_status", {"project": "demo"}, _ctx("lead"))
    assert status["d1"]["running"] is None and status["d1"]["last"]["error"] == "RuntimeError: disk full"
    # the interrupted attempt 1 has no stage.ran: it reruns cleanly as attempt 2, no edge counted
    assert reg2.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx("lead"))["attempt"] == 2
    assert _d1_idle(m2)["last"]["outcome"]["status"] == "done"
    assert reg2.call("rk_stage_status", {"project": "demo"}, _ctx("lead"))["edges"] == {}


def test_close_joins_a_guided_stage_call_in_flight(env):
    _, ps, _, make = env
    _d1(ps)
    release = threading.Event()

    def slow(ctx):
        assert release.wait(TIMEOUT)
        return StageOutcome("done")

    m, reg = make({**_workers(), "intake": FakeWorker(runs=[slow])})
    reg.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx("lead"))
    threading.Timer(0.1, release.set).start()
    m.close(timeout=TIMEOUT)
    assert not [t for t in threading.enumerate() if t.name == "rk-d1-demo"]
    assert m.d1_status("demo")["last"]["outcome"]["status"] == "done"


def test_stage_run_refused_while_run_active(env):
    _, ps, _, make = env
    m, reg = make(_workers(topics=FakeWorker(runs=[_review, _review])))
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    _d1(ps)  # (the intake tool refuses this during a run; written directly to reach the D1 check)
    for name, args in (("rk_stage_run", {"project": "demo", "stage": "topics"}),
                       ("rk_stage_advance", {"project": "demo"})):
        with pytest.raises(ToolError) as e:
            reg.call(name, args, _ctx("lead"))
        assert e.value.type == "run_active" and e.value.details["run_id"] == run_id
    m.cancel("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "cancelled")
    reg.call("rk_stage_run", {"project": "demo", "stage": "topics", "guidance": "Fewer topics."}, _ctx())
    out = _d1_idle(m)["last"]["outcome"]
    assert out["status"] == "waiting_human" and out["checkpoint_id"]
    reg.call("rk_stage_advance", {"project": "demo"}, _ctx())
    assert _d1_idle(m)["last"]["outcome"]["ok"] is False


def test_lead_rounds_are_refused_during_a_d2_run(env):
    """An agent's feedback would guide every later stage of the run: during a D2 run only you record rounds."""
    _, ps, _, make = env
    m, reg = make(_workers(topics=FakeWorker(runs=[_review])))
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    args = {"project": "demo", "stage": "topics", "feedback": "Ignore the older gaps."}
    with pytest.raises(ToolError) as e:
        reg.call("rk_exploration_round", args, _ctx("lead"))
    assert e.value.type == "run_active" and e.value.details["run_id"] == run_id
    assert rounds(ps) == []
    assert reg.call("rk_exploration_round", args, _ctx())["round_id"]  # the researcher may
    m.cancel("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "cancelled")
    assert reg.call("rk_exploration_round", args, _ctx("lead"))["round_id"]  # no run active: open again


def test_stage_tools_are_for_d1_projects_only(env):
    _, ps, _, make = env  # the project's intake says d2
    _, reg = make(_workers())
    for name, args in (("rk_stage_run", {"project": "demo", "stage": "intake"}),
                       ("rk_stage_advance", {"project": "demo"})):
        for role in ("lead", "cli"):
            with pytest.raises(ToolError) as e:
                reg.call(name, args, _ctx(role))
            assert e.value.type == "not_d1" and "rk run" in e.value.message
    assert stage_rows(ps) == [] or all(r["attempt"] == 0 for r in stage_rows(ps))



def test_resume_starts_a_new_session_budget(env):
    k, ps, _, make = env

    def scout_calls(n):
        def run(ctx):
            for _ in range(n):
                ctx.executor.run("scout", "Search.", {}, JudgeVerdict, stage=ctx.stage, attempt=ctx.attempt,
                                 tools=[], provider=judge)
            return StageOutcome("done")
        return run

    judge = FakeJudge("supported", [])
    holder: dict = {}

    def executor(project, run_id, session):
        return KernelExecutor(k, holder["reg"], project, run_id=run_id, session=session, max_tasks_per_stage=2)

    m, reg = make(_workers(landscape=FakeWorker(runs=[scout_calls(3), scout_calls(2)])), executor_factory=executor)
    holder["reg"] = reg
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_budget")
    assert "2 of 2 role tasks" in run_row(ps, run_id)["reason"]
    _wait(lambda: not _run_threads())
    assert tasks_used(ps, run_id, 1, "landscape") == 2
    m.resume("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    row = run_row(ps, run_id)
    assert row["session"] == 2
    assert tasks_used(ps, run_id, 2, "landscape") == 2 and tasks_used(ps, run_id, 1, "landscape") == 2


def test_pause_takes_effect_between_steps(env):
    _, ps, _, make = env
    release = threading.Event()

    def slow(ctx):
        assert release.wait(TIMEOUT)
        return StageOutcome("done")

    w = _workers(landscape=FakeWorker(runs=[slow]))
    m, _ = make(w)
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: w["landscape"].calls)
    assert m.pause("demo")["requested"] == "paused"
    assert run_row(ps, run_id)["status"] == "running"
    release.set()
    _wait(lambda: run_row(ps, run_id)["status"] == "paused")
    _wait(lambda: not _run_threads())
    assert current_stage(ps) == "gaps" and w["gaps"].calls == []
    m.resume("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    assert run_row(ps, run_id)["session"] == 2
    with pytest.raises(ToolError) as e:
        m.resume("demo")
    assert e.value.type == "no_run"


@pytest.mark.parametrize("control,status", [("pause", "paused"), ("cancel", "cancelled")])
def test_pause_or_cancel_survives_shutdown_mid_step(env, control, status):
    _, ps, _, make = env
    release = threading.Event()

    def slow(ctx):
        assert release.wait(TIMEOUT)
        return StageOutcome("done")

    w = _workers(landscape=FakeWorker(runs=[slow]))
    m, _ = make(w, join_timeout_s=0.05)
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: w["landscape"].calls)
    (thread,) = _run_threads()
    assert getattr(m, control)("demo")["requested"] == status
    m.close()
    assert thread.is_alive()
    release.set()
    thread.join(TIMEOUT)
    assert not thread.is_alive()
    assert run_row(ps, run_id)["status"] == status and run_row(ps, run_id)["reason"].endswith("by you")


def test_close_joins_every_thread_within_one_deadline(env):
    k, _, _, make = env
    k.create_project("other", "Other project", actor="tester")
    record_intake(k.project("other")[0], Intake(**DEFAULT_INTAKE), actor="tester", actor_kind="human")
    release = threading.Event()

    def slow(ctx):
        assert release.wait(TIMEOUT)
        return StageOutcome("done")

    w = _workers(landscape=FakeWorker(runs=[slow, slow]))
    m, _ = make(w, join_timeout_s=0.5)
    m.start("demo", "topics", actor="tester")
    m.start("other", "topics", actor="tester")
    _wait(lambda: len(w["landscape"].calls) == 2)
    threads = _run_threads("demo") + _run_threads("other")
    t0 = time.monotonic()
    m.close()
    elapsed = time.monotonic() - t0
    release.set()
    for t in threads:
        t.join(TIMEOUT)
    assert not any(t.is_alive() for t in threads)
    assert 0.4 <= elapsed < 0.9, elapsed


def test_cancel_wakes_a_waiting_run(env):
    _, ps, _, make = env
    m, _ = make(_workers(topics=FakeWorker(runs=[_review])), poll_s=60)
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    m.cancel("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "cancelled", timeout=2)
    _wait(lambda: not _run_threads(), timeout=2)


def test_rate_cap_waits_on_the_capped_driver_then_steps_again(env, monkeypatch):
    k, ps, _, make = env
    judge = FakeJudge("supported", [])
    holder: dict = {}

    def capped(ctx):
        ctx.executor.run("scout", "Search.", {}, JudgeVerdict, stage=ctx.stage, attempt=ctx.attempt,
                         tools=[], driver=DriverSpec("claude_cli"), provider=judge)
        return StageOutcome("done")

    def executor(project, run_id, session):
        return KernelExecutor(k, holder["reg"], project, run_id=run_id, session=session)

    for _ in range(k.settings().calls_per_hour["claude_cli"]):
        assert k.caps.acquire("claude_cli", 1000)
    w = _workers(landscape=FakeWorker(runs=[capped, RateCapped("unknown", 1.0)]))
    m, reg = make(w, executor_factory=executor, max_rate_wait_s=0.05)
    holder["reg"] = reg
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    assert [c[0] for c in w["landscape"].calls].count("run") == 3
    assert 3500 < m.rate_wait_s("demo", run_id) <= 3600
    m2, _ = make(_workers(), unknown_rate_wait_s=42.0)
    assert m2.rate_wait_s("demo", "run_none") == 42.0
    monkeypatch.setattr(k.caps, "wait_s", lambda driver: 0.0)  # the known driver's window emptied: no 60 s wait
    assert m.rate_wait_s("demo", run_id) == m.poll_s


def test_step_error_fails_the_run_with_its_reason(env):
    _, ps, _, make = env
    m, _ = make({**_workers(), "landscape": None})
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "failed")
    assert "stage_unavailable" in run_row(ps, run_id)["reason"]
    _wait(lambda: not _run_threads())

    boom = FakeWorker(runs=[RuntimeError("disk full")])
    m2, _ = make(_workers(landscape=boom))
    run2 = m2.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run2)["status"] == "failed")
    assert run_row(ps, run2)["reason"] == "RuntimeError: disk full"


def test_transient_source_error_backs_off_and_steps_again(env):
    from rk.net.http import HttpError

    _, ps, _, make = env
    flaky = FakeWorker(runs=[HttpError(503, "https://api.openalex.org/works"), HttpError(0, "https://x.org"),
                             StageOutcome("done")])
    m, _ = make(_workers(landscape=flaky), source_backoff_s=0.01)
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    assert flaky.kinds().count("run") == 3  # each retry is a new attempt of the interrupted stage
    reasons = [e.payload["reason"] for e in ps.log.iter() if e.type == "run.status" and e.entity_id == run_id]
    assert any("a source failed (HttpError: HTTP 503" in r and "retrying" in r for r in reasons)


def test_source_error_that_persists_fails_the_run_after_retries(env):
    from rk.net.http import HttpError

    _, ps, _, make = env
    down = FakeWorker(runs=[HttpError(502, "https://api.openalex.org/works")] * 4)
    m, _ = make(_workers(landscape=down), source_backoff_s=0.01, max_source_retries=2)
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "failed")
    assert down.kinds().count("run") == 3
    reason = run_row(ps, run_id)["reason"]
    assert "kept failing after 2 retries" in reason and "HTTP 502" in reason


def test_a_non_transient_source_error_fails_at_once(env):
    from rk.net.http import HttpError

    _, ps, _, make = env
    m, _ = make(_workers(landscape=FakeWorker(runs=[HttpError(403, "https://api.openalex.org/works")])),
                source_backoff_s=0.01)
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "failed")
    assert run_row(ps, run_id)["reason"] == "HttpError: HTTP 403 for https://api.openalex.org/works"


def test_an_exhausted_source_budget_pauses_the_run_and_resume_continues(env):
    """Spec §11.3: budget exhaustion suspends the work and shows the reason, never skips it: the run is
    ``paused_budget`` with the exhausted source budget named, and resuming it later runs the stage again."""
    from rk.net.http import BudgetExhausted

    _, ps, _, make = env
    landscape = FakeWorker(runs=[BudgetExhausted("openalex", 1_900_000_000.0), StageOutcome("done")])
    m, _ = make(_workers(landscape=landscape))
    run_id = m.start("demo", "topics", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_budget")
    reason = run_row(ps, run_id)["reason"]
    assert "API budget exhausted for openalex" in reason and "resets at 1900000000" in reason
    _wait(lambda: not _run_threads())
    m.resume("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    assert landscape.kinds().count("run") == 2


@pytest.mark.parametrize("end", ["failed", "cancelled"])
def test_resume_of_a_failed_or_cancelled_run_starts_a_new_run(env, end):
    _, ps, _, make = env
    first = [RuntimeError("disk full")] if end == "failed" else [_review]
    m, reg = make(_workers(landscape=FakeWorker(runs=first)))
    with pytest.raises(ToolError) as e:
        m.resume("demo")
    assert e.value.type == "no_run"
    old = m.start("demo", "question", actor="tester")
    if end == "cancelled":
        _wait(lambda: run_row(ps, old)["status"] == "paused_waiting_human")
        m.cancel("demo")
    _wait(lambda: run_row(ps, old)["status"] == end)
    _wait(lambda: not _run_threads())
    out = reg.call("rk_run_resume", {"project": "demo"}, _ctx())
    new = out["run"]["id"]
    assert new != old and out["replaced"] == old
    assert out["message"] == (f"run {old} was {end}, so it cannot be resumed; started a new run {new} (until "
                              "question): it goes on from the stages that already passed")
    assert run_row(ps, new)["until"] == "question"
    if end == "cancelled":
        m.cancel("demo")
    _wait(lambda: run_row(ps, new)["status"] in ("done", "cancelled"))


class _LockTimeout(Exception):
    """A gated thread waited too long for a lock: the locks deadlocked."""


class _Gate:
    """Widens the window between a thread's first and second lock. Each gated thread (by name), once it holds its
    first hooked lock, waits (at most ``pause_s``) until every gated thread holds one; under a lock-order inversion
    each then waits for the lock the other holds, every time. A gated thread gives up on a lock after ``give_up_s``
    (``_LockTimeout``), so a deadlock fails the test instead of hanging it or leaking its threads."""

    def __init__(self, names: tuple[str, ...], *, pause_s: float = 1.0, give_up_s: float = 10.0):
        self.names, self.give_up_s = set(names), give_up_s
        self.barrier = threading.Barrier(len(names), timeout=pause_s)
        self.fired: set[str] = set()

    def hook(self, lock) -> _HookedLock:
        return _HookedLock(lock, self)


class _HookedLock:
    def __init__(self, inner, gate: _Gate):
        self.inner, self.gate = inner, gate

    def __enter__(self):
        me = threading.current_thread().name
        if me not in self.gate.names:
            self.inner.acquire()
            return self
        if not self.inner.acquire(timeout=self.gate.give_up_s):
            raise _LockTimeout(f"{me} waited {self.gate.give_up_s} s for a lock")
        if me not in self.gate.fired:
            self.gate.fired.add(me)
            try:
                self.gate.barrier.wait()
            except threading.BrokenBarrierError:  # the other thread waits on this lock: the lock order holds
                pass
        return self

    def __exit__(self, *exc):
        self.inner.release()


def _race(*calls) -> list:
    """Run each ``(thread name, fn)`` in its own thread and join each within 15 s (more than a gated thread waits for
    a lock); the result or exception of each."""
    out: dict = {}

    def go(name, fn):
        try:
            out[name] = fn()
        except BaseException as e:  # noqa: BLE001 - returned to the test
            out[name] = e

    threads = [threading.Thread(target=go, args=c, name=c[0], daemon=True) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15.0)
    assert not [t.name for t in threads if t.is_alive()], "the threads hang"
    return [out[name] for name, _ in calls]


@pytest.mark.parametrize("path", ["start", "resume"])
def test_intake_and_a_run_start_never_deadlock(env, path):
    """The intake guard takes the RunManager's lock before the project's lock, like start and resume → new run. In the
    other order an intake racing a start deadlocked, and with the manager's lock held every run of ``rk serve`` hung.
    Either the intake comes first (and the run starts after it) or the run does (and the intake is refused)."""
    from rk.discovery.store import intake_of

    k, ps, _, make = env
    m, reg = make(_workers(landscape=FakeWorker(runs=[RuntimeError("disk full"), _review] if path == "resume"
                                                else [_review])))
    if path == "resume":
        old = m.start("demo", "question", actor="tester")
        _wait(lambda: run_row(ps, old)["status"] == "failed")
        _wait(lambda: not _run_threads())
    assert k.project("demo")[0] is ps  # the tools and the manager share this ProjectStore and its lock
    gate = _Gate(("rk-test-intake", "rk-test-run"))
    m._lock, ps.lock = gate.hook(m._lock), gate.hook(ps.lock)
    args = {"project": "demo", **intake_of(ps).model_dump(), "audience": "nurses"}
    begin = (lambda: m.start("demo", "question", actor="tester")) if path == "start" else (lambda: m.resume("demo"))
    intake, run = _race(("rk-test-intake", lambda: reg.call("rk_project_intake", args, _ctx())),
                        ("rk-test-run", begin))
    assert not isinstance(intake, _LockTimeout) and not isinstance(run, _LockTimeout), "lock-order deadlock"
    assert not isinstance(run, BaseException), run
    new = run if path == "start" else run["run"]["id"]
    seq = lambda kind: ps.db.execute("SELECT MAX(seq) FROM events WHERE type=?", (kind,)).fetchone()[0]
    if isinstance(intake, ToolError):
        assert intake.type == "run_active" and new in intake.message
        assert intake_of(ps).audience != "nurses"
    else:
        assert not isinstance(intake, BaseException), intake
        assert intake_of(ps).audience == "nurses" and seq("project.intake") < seq("run.started")
    _wait(lambda: run_row(ps, new)["status"] == "paused_waiting_human")


def _hold_after_first_read(monkeypatch, module: str, thread: str, until: threading.Event) -> threading.Event:
    """Patch ``module.intake_of``: the first read of the intake in the thread named ``thread`` then waits (at most
    2 s) for ``until``, widening the window between a precondition check and the start it guards. Returns an event
    set once that read happened."""
    import importlib

    mod = importlib.import_module(module)
    real, read = mod.intake_of, threading.Event()

    def intake_of(ps):
        got = real(ps)
        if threading.current_thread().name == thread and not read.is_set():
            read.set()
            until.wait(2.0)
        return got

    monkeypatch.setattr(mod, "intake_of", intake_of)
    return read


def _intake_once_read(reg, read: threading.Event, done: threading.Event, mode: str):
    """Record the intake with discovery mode ``mode`` once the other thread checked its precondition."""

    def call():
        assert read.wait(TIMEOUT)
        try:
            return reg.call("rk_project_intake", {"project": "demo", **DEFAULT_INTAKE, "discovery_mode": mode},
                            _ctx())
        finally:
            done.set()

    return call


def test_an_intake_switching_to_d1_never_lands_inside_a_run_start(env, monkeypatch):
    """start checks the discovery mode and starts the run in one locked step: an intake switching to d1 after the
    check either comes first (``not_d2``) or is refused (``run_active``); no run starts in d1."""
    import json

    from rk.discovery.store import intake_of

    _, ps, _, make = env
    m, reg = make(_workers(landscape=FakeWorker(runs=[_review])))
    done = threading.Event()
    read = _hold_after_first_read(monkeypatch, "rk.orchestrator.runs", "rk-test-run", done)
    run, intake = _race(("rk-test-run", lambda: m.start("demo", "question", actor="tester")),
                        ("rk-test-intake", _intake_once_read(reg, read, done, "d1")))
    modes = [json.loads(r[0])["discovery_mode"] for r in
             ps.db.execute("SELECT payload FROM events WHERE type='run.started'")]
    assert "d1" not in modes
    if isinstance(run, ToolError):
        assert run.type in ("not_d2", "run_active") and modes == [] and intake_of(ps).discovery_mode == "d1"
    else:
        assert not isinstance(run, BaseException), run
        assert isinstance(intake, ToolError) and intake.type == "run_active" and modes == ["d2"]
        assert intake_of(ps).discovery_mode == "d2"
        _wait(lambda: run_row(ps, run)["status"] == "paused_waiting_human")


def test_a_switch_to_d2_never_lands_inside_a_guided_stage_start(env, monkeypatch):
    """d1_start checks the D1 precondition again under its lock: an intake switching to d2 after the tool's check
    either comes first (``not_d1``, no stage call starts) or is refused (``stage_busy``)."""
    from rk.discovery.store import intake_of

    _, ps, _, make = env
    _d1(ps)
    m, reg = make(_workers())
    done = threading.Event()
    read = _hold_after_first_read(monkeypatch, "rk.discovery.tools", "rk-test-run", done)
    stage, intake = _race(("rk-test-run", lambda: reg.call("rk_stage_run", {"project": "demo", "stage": "intake"},
                                                           _ctx("lead"))),
                          ("rk-test-intake", _intake_once_read(reg, read, done, "d2")))
    status = _d1_idle(m)
    if isinstance(stage, ToolError):
        assert stage.type == "not_d1" and status["last"] is None
        assert not isinstance(intake, BaseException), intake
        assert intake_of(ps).discovery_mode == "d2"
    else:
        assert not isinstance(stage, BaseException), stage
        assert isinstance(intake, ToolError) and intake.type == "stage_busy"
        assert intake_of(ps).discovery_mode == "d1"


def test_run_tools_are_cli_only(env):
    _, ps, _, make = env
    _, reg = make(_workers())
    for name in ("rk_run_start", "rk_run_pause", "rk_run_resume", "rk_run_cancel"):
        for role in ("lead", "scout", "ideator"):
            with pytest.raises(PermissionDenied):
                reg.call(name, {"project": "demo", "until": "topics"}, _ctx(role))
    with pytest.raises(PermissionDenied):
        reg.call("rk_run_status", {"project": "demo"}, _ctx("scout"))
    assert reg.call("rk_run_status", {"project": "demo"}, _ctx("lead"))["run"] is None
    out = reg.call("rk_run_start", {"project": "demo", "until": "topics"}, _ctx())
    _wait(lambda: run_row(ps, out["run_id"])["status"] == "done")
    status = reg.call("rk_run_status", {"project": "demo"}, _ctx("lead"))
    assert status["run"]["id"] == out["run_id"] and [s["status"] for s in status["stages"]][:4] == ["passed"] * 4


def test_run_and_stage_tools_need_the_orchestrator(tmp_home, replay_http):
    k, _, _ = make_project(tmp_home, replay_http)
    try:
        reg = build_registry(k)
        for name, args in (("rk_run_start", {"project": "demo", "until": "topics"}),
                           ("rk_stage_run", {"project": "demo", "stage": "intake"})):
            with pytest.raises(ToolError) as e:
                reg.call(name, args, _ctx())
            assert e.value.type == "unavailable"
    finally:
        k.close()


def test_discovery_read_tools_show_the_latest_attempt(env):
    _, ps, _, make = env
    ids: dict = {}

    def gaps_run(ctx):
        ids["gap"] = add_gap(ps, ctx.attempt, Gap(kind="stated_limitation", statement="Not found within the searched "
                                                  "scope.", evidence=GapEvidence(item_ids=["itm_a"])),
                             verified=True, checks={}, actor="tester")
        add_gap(ps, ctx.attempt, Gap(kind="stated_limitation", statement="Unchecked.", evidence=GapEvidence()),
                verified=False, checks={}, actor="tester")
        return StageOutcome("done")

    def topics_run(ctx):
        ids["topic"] = add_topic(ps, ctx.attempt, Topic(title="Aspirin over 80", summary="S.", gap_ids=[ids["gap"]],
                                                        feasibility="F.", data_needed="D.",
                                                        paper_type="critical_review", risks="R."), actor="tester")
        return StageOutcome("done")

    def question_run(ctx):
        body = RQBody.model_validate({
            "three_step": {"x": "aspirin over 80", "y": "stroke", "z": "when to start"},
            "problem": {"condition": "Guidelines stop at 80.", "consequences": ["A.", "B."]},
            "finer": {"feasible": "F.", "interesting": "I.", "novel": "N.", "ethical": "E.", "relevant": "R."},
            "clinical": False})
        ids["rq"] = add_rq(ps, ctx.attempt, ids["topic"], body, [HypothesisIn(statement="H.", falsification="X.")],
                           actor="tester")
        freeze_rq(ps, ids["rq"], adopted_by="agent", actor="tester")
        return StageOutcome("done")

    m, reg = make(_workers(gaps=FakeWorker(runs=[gaps_run]), topics=FakeWorker(runs=[topics_run]),
                           question=FakeWorker(runs=[question_run])))
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    for role in ("lead", "scout", "reader", "gapminer", "ideator"):
        assert [g["id"] for g in reg.call("rk_gap_list", {"project": "demo", "verified": True}, _ctx(role))["gaps"]] \
            == [ids["gap"]]
        assert [t["id"] for t in reg.call("rk_topic_list", {"project": "demo"}, _ctx(role))["topics"]] == [ids["topic"]]
        rq = reg.call("rk_rq_get", {"project": "demo"}, _ctx(role))
        assert rq["rq"]["id"] == ids["rq"] and rq["rq"]["status"] == "frozen" and len(rq["hypotheses"]) == 1
    assert len(reg.call("rk_gap_list", {"project": "demo"}, _ctx())["gaps"]) == 2
    with pytest.raises(PermissionDenied):
        reg.call("rk_gap_list", {"project": "demo"}, _ctx("screener_a"))
    st = reg.call("rk_stage_status", {"project": "demo"}, _ctx("screener_a"))
    assert st["current"] == "protocol" and [s["status"] for s in st["stages"]][:6] == ["passed"] * 5 + ["pending"]
    with pytest.raises(PermissionDenied):
        reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More."}, _ctx("scout"))
    with pytest.raises(ToolError) as e:  # every discovery stage passed: closed to the Lead (Task 10) ...
        reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More."}, _ctx("lead"))
    assert e.value.type == "stage_closed"
    out = reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More trials."},
                   _ctx())  # ... the researcher still records feedback
    assert [(r["id"], r["stage"], r["feedback"]) for r in rounds(ps)] == [(out["round_id"], "gaps", "More trials.")]


def test_read_tools_show_the_latest_attempt_that_ran(env):
    """An interrupted attempt (no stage.ran) may be partial: the read tools (and the Workbench routes built on them)
    show the latest attempt that ran instead."""
    from rk.orchestrator.state import record_attempt, record_ran

    _, ps, _, make = env
    _, reg = make(_workers())
    gap = Gap(kind="stated_limitation", statement="Not found within the searched scope.", evidence=GapEvidence())
    record_attempt(ps, "gaps", 1, run_id=None, guidance="", requested=None, actor="tester")
    ran = add_gap(ps, 1, gap, verified=True, checks={}, actor="tester")
    record_ran(ps, "gaps", 1, "done", "", actor="tester")
    record_attempt(ps, "gaps", 2, run_id=None, guidance="", requested=None, actor="tester")
    add_gap(ps, 2, gap, verified=False, checks={}, actor="tester")  # attempt 2 was interrupted here
    out = reg.call("rk_gap_list", {"project": "demo"}, _ctx("lead"))
    assert out["attempt"] == 1 and [g["id"] for g in out["gaps"]] == [ran]
    record_attempt(ps, "topics", 1, run_id=None, guidance="", requested=None, actor="tester")
    assert reg.call("rk_topic_list", {"project": "demo"}, _ctx("lead")) == {"project": "demo", "attempt": None,
                                                                            "topics": []}


def test_rk_run_cli_round_trip(serve_rk):
    from typer.testing import CliRunner

    from rk.cli.main import app

    w = _workers()
    client = serve_rk(orchestrator=lambda kernel, registry: _orch(kernel, registry, w))
    client.call("rk_project_create", {"slug": "demo", "title": "Demo"}, write=True)
    client.call("rk_project_intake", {"project": "demo", "audience": "nurses", "paper_type": "scoping_review",
                                      "discovery_mode": "d2", "production_mode": "auto"}, write=True)
    ps, _ = client.kernel.project("demo")
    runner = CliRunner()
    r = runner.invoke(app, ["run", "demo", "--until", "topics"])
    assert r.exit_code == 0, r.output
    (run_id,) = [tok for tok in r.output.split() if tok.startswith("run_")]
    assert run_row(ps, run_id) is not None
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    r = runner.invoke(app, ["run", "status", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert f"{run_id}: done" in r.output
    for stage in DISCOVERY:
        assert stage in r.output
    assert "topics     passed" in r.output and "question   pending" in r.output
    r = runner.invoke(app, ["stage", "status", "--project", "demo"])
    assert r.exit_code == 0 and "question   pending" in r.output, r.output
    r = runner.invoke(app, ["run", "pause", "--project", "demo"])
    assert r.exit_code == 1 and "no_run" in r.output
    assert "between steps" in runner.invoke(app, ["run", "pause", "--help"]).output
    r = runner.invoke(app, ["topic", "list", "--project", "demo"])
    assert r.exit_code == 0 and "no topics" in r.output, r.output
