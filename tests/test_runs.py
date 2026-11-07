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
        assert [r["status"] for r in stage_rows(ps2)] == ["passed"] * 5
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


def test_start_runs_until_topics_or_question(env):
    _, ps, _, make = env
    m, _ = make(_workers())
    for until in ("intake", "landscape", "gaps", "protocol"):
        with pytest.raises(ToolError) as e:
            m.start("demo", until, actor="tester")
        assert e.value.type == "stage_unavailable", until
    assert latest_run(ps) is None and not _run_threads()


def test_d1_stage_call_holds_off_a_run(env):
    _, ps, _, make = env
    entered, release = threading.Event(), threading.Event()

    def slow(ctx):
        entered.set()
        assert release.wait(TIMEOUT)
        return StageOutcome("done")

    m, reg = make({**_workers(), "intake": FakeWorker(runs=[slow])})
    outs: list = []
    d1 = threading.Thread(target=lambda: outs.append(
        reg.call("rk_stage_run", {"project": "demo", "stage": "intake"}, _ctx())), name="d1-call")
    d1.start()
    try:
        assert entered.wait(TIMEOUT)
        for call in (lambda: m.start("demo", "question", actor="tester"), lambda: m.resume("demo")):
            with pytest.raises(ToolError) as e:
                call()
            assert e.value.type == "run_active" and "guided" in e.value.message
        with pytest.raises(ToolError) as e:
            reg.call("rk_stage_advance", {"project": "demo"}, _ctx())
        assert e.value.type == "stage_busy"
        assert latest_run(ps) is None and not _run_threads()
    finally:
        release.set()
        d1.join(TIMEOUT)
    assert not d1.is_alive() and outs[0]["status"] == "done"
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "done")
    assert reg.call("rk_stage_status", {"project": "demo"}, _ctx())["current"] is None


def test_stage_run_refused_while_run_active(env):
    _, ps, _, make = env
    m, reg = make(_workers(topics=FakeWorker(runs=[_review, _review])))
    run_id = m.start("demo", "question", actor="tester")
    _wait(lambda: run_row(ps, run_id)["status"] == "paused_waiting_human")
    for name, args in (("rk_stage_run", {"project": "demo", "stage": "topics"}),
                       ("rk_stage_advance", {"project": "demo"})):
        with pytest.raises(ToolError) as e:
            reg.call(name, args, _ctx("lead"))
        assert e.value.type == "run_active" and e.value.details["run_id"] == run_id
    m.cancel("demo")
    _wait(lambda: run_row(ps, run_id)["status"] == "cancelled")
    out = reg.call("rk_stage_run", {"project": "demo", "stage": "topics", "guidance": "Fewer topics."}, _ctx())
    assert out["status"] == "waiting_human" and out["checkpoint_id"]
    assert reg.call("rk_stage_advance", {"project": "demo"}, _ctx())["ok"] is False


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
    assert st["current"] is None and [s["status"] for s in st["stages"]] == ["passed"] * 5
    with pytest.raises(PermissionDenied):
        reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More."}, _ctx("scout"))
    with pytest.raises(ToolError) as e:  # every discovery stage passed: closed to the Lead (Task 10) ...
        reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More."}, _ctx("lead"))
    assert e.value.type == "stage_closed"
    out = reg.call("rk_exploration_round", {"project": "demo", "stage": "gaps", "feedback": "More trials."},
                   _ctx())  # ... the researcher still records feedback
    assert [(r["id"], r["stage"], r["feedback"]) for r in rounds(ps)] == [(out["round_id"], "gaps", "More trials.")]


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
