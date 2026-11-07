"""Guided discovery (D1) through the Lead chat: a scripted Lead drives the stages with its tools; the researcher
answers the checkpoints (Plan 4 Task 10)."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from rk.checkpoints import get_checkpoint
from rk.config import Settings, save_settings
from rk.discovery.store import rounds
from rk.llm.chat.base import ToolCall, Turn
from rk.llm.chat_provider import ChatApiProvider
from rk.orchestrator.engine import GateResult, Orchestrator, StageOutcome
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import current_stage, edge_count
from rk.server.chat import ChatManager
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext
from rk.workspace import Kernel

from .discovery_fakes import (
    DEFAULT_INTAKE,
    FakeSources,
    FakeWorker,
    ScriptedExecutor,
    make_project,
)
from .test_agent_loop import ScriptedBackend
from .test_discovery_landscape import QUERIES as LANDSCAPE_QUERIES
from .test_discovery_landscape import _cluster, _stroke_bleeding
from .test_topics import Stub, _executor, _gaps_writer, _sources, _topic

FEEDBACK = "exclude animal studies"


class Lead:
    """A Lead chat in ``rk serve``'s ChatManager whose model is a ``ScriptedBackend``: ``say`` scripts one turn of
    tool calls (run in order) and a closing text, sends the researcher's message and returns the tool results the
    Lead saw, in order."""

    def __init__(self, k: Kernel, orchestrator, monkeypatch):
        self.k, self.backend = k, ScriptedBackend([])
        holder: dict = {}
        self.reg = build_registry(k, runs=lambda: holder.get("m"))
        self.runs = holder["m"] = RunManager(k, self.reg, orchestrator(self.reg))
        monkeypatch.setattr("rk.server.chat.build_provider",
                            lambda spec, settings: ChatApiProvider(self.backend, "scripted"))
        self.chats = ChatManager(k, self.reg)
        self.chat_id = self.chats.open("anthropic_api:scripted-1", "lead", "demo")
        self._n = 0

    def say(self, text: str, *calls: tuple[str, dict]) -> list[dict]:
        tool_calls = []
        for name, args in calls:
            self._n += 1
            tool_calls.append(ToolCall(id=f"c{self._n}", name=name, arguments=args))
        self.backend.turns += [Turn(tool_calls=tool_calls), Turn(text="Here is what the tools returned.")]
        events = list(self.chats.send(self.chat_id, text))
        assert events[-1].kind == "done", events[-1]
        return [e.data["content"] for e in events if e.kind == "tool_result"]

    def stage(self, text: str, name: str, args: dict) -> dict:
        """One guided stage call the way the Lead makes it: start it (it returns at once, ``running``), then, once
        it finished, read its outcome with ``rk_stage_status`` (``d1.last``)."""
        (out,) = self.say(text, (name, args))
        assert _ok(out)["status"] == "running", out
        end = time.monotonic() + 10
        while self.runs.d1_status("demo")["running"] is not None:
            assert time.monotonic() < end, "the stage call did not finish"
            time.sleep(0.01)
        (status,) = self.say("Is it done?", ("rk_stage_status", {}))
        d1 = _ok(status)["d1"]
        assert d1["running"] is None and d1["last"]["error"] is None, d1
        return d1["last"]["outcome"]

    def close(self) -> None:
        self.chats.close_all()
        self.runs.close()


@pytest.fixture
def lead(tmp_home, replay_http, monkeypatch):
    opened: list[Lead] = []
    kernels: list[Kernel] = []

    def make(orchestrator, *, intake: dict | None = None):
        """``orchestrator(kernel, registry)`` builds the Orchestrator; ``intake`` overrides recorded up front (none:
        the Lead records the intake itself)."""
        if intake is None:
            save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
            k = Kernel(tmp_home, http=replay_http)
            k.create_project("demo", "Demo project", actor="tester")
        else:
            k, _, _ = make_project(tmp_home, replay_http, **intake)
        kernels.append(k)
        ld = Lead(k, lambda reg: orchestrator(k, reg), monkeypatch)
        opened.append(ld)
        return ld

    yield make
    for ld in opened:
        ld.close()
    for k in kernels:
        k.close()


def _ok(result: dict) -> dict:
    assert "result" in result, result
    return result["result"]


def test_d1_scripted_chat_reaches_topic_review(lead):
    landscape, g = FakeWorker(), {}
    ex = _executor()
    state: dict = {}

    def orchestrator(k, reg):
        ps, lib = k.project("demo")
        state["sources"] = state.get("sources") or _sources(lib)[0]
        return Orchestrator(k, reg, workers={"landscape": landscape, "gaps": Stub(_gaps_writer(ps, lib, g))},
                            executor_factory=lambda p, r, s: ex, sources_factory=lambda p: state["sources"])

    ld = lead(orchestrator)
    ps, _ = ld.k.project("demo")
    intake = {**DEFAULT_INTAKE, "discovery_mode": "d1", "topics_n": 1}
    (out,) = ld.say("I want to look at aspirin in older people.", ("rk_project_intake", intake))
    assert _ok(out)["discovery_mode"] == "d1"
    assert ld.stage("Run the intake.", "rk_stage_run", {"stage": "intake"})["status"] == "done"
    res = ld.stage("Pass it.", "rk_stage_advance", {})
    assert (res["ok"], res["current"]) == (True, "landscape")

    # a round: run the stage, show it, record the researcher's feedback; the rerun gets it as guidance
    assert ld.stage("Map the field.", "rk_stage_run", {"stage": "landscape"})["status"] == "done"
    out = ld.say(f"Please {FEEDBACK}.", ("rk_exploration_round", {"stage": "landscape", "feedback": FEEDBACK}))
    assert _ok(out[0])["round_id"]
    assert ld.stage("Run it again with that.", "rk_stage_run", {"stage": "landscape"})["status"] == "done"
    assert ld.stage("Move on.", "rk_stage_advance", {})["current"] == "gaps"
    assert landscape.calls[0] == ("run", 1, "") and landscape.calls[-2] == ("run", 2, FEEDBACK)
    assert edge_count(ps, "landscape->landscape") == 1

    ld.stage("Now the gaps.", "rk_stage_run", {"stage": "gaps"})
    assert ld.stage("Pass them.", "rk_stage_advance", {})["current"] == "topics"
    (out,) = ld.say("Show the gaps.", ("rk_gap_list", {"verified": True}))
    assert [x["id"] for x in _ok(out)["gaps"]] == [g["verified"]]
    ex.script[("ideator", "TopicsOut")] = [{"topics": [_topic("Aspirin over 80", [g["verified"]])]}]

    more = "Focus on patients over 80."
    ld.say("Note this.", ("rk_exploration_round", {"stage": "gaps", "feedback": more}))
    run = ld.stage("Suggest topics.", "rk_stage_run", {"stage": "topics"})
    assert run["status"] == "waiting_human" and run["current"] == "topics"
    review = get_checkpoint(ps, run["checkpoint_id"])
    assert (review["kind"], review["status"]) == ("topic_review", "open")
    (out,) = ld.say("What is open?", ("rk_checkpoint_list", {"status": "open"}))
    assert [c["id"] for c in _ok(out)["checkpoints"]] == [review["id"]]
    assert [(r["stage"], r["feedback"]) for r in rounds(ps)] == [("landscape", FEEDBACK), ("gaps", more)]
    (ideator,) = [c for c in ex.calls if c["role"] == "ideator"]
    assert FEEDBACK in ideator["inputs"]["guidance"] and more in ideator["inputs"]["guidance"]

    # the Lead cannot answer the review; advancing tells it the review is open
    (topic_id,) = review["options"]["topic_ids"]
    (out,) = ld.say("Just approve it for me.",
                    ("rk_checkpoint_answer", {"checkpoint_id": review["id"],
                                              "answer": {"decision": "approve", "topic_id": topic_id}}))
    assert out["error"] == "permission_denied"
    res = ld.stage("Advance.", "rk_stage_advance", {})
    assert res["ok"] is False and res["failures"] == [f"checkpoint {review['id']} (topic_review) is open"]
    assert get_checkpoint(ps, review["id"])["status"] == "open"

    # the researcher answers in the Checkpoints panel / CLI (the cli-only tool), then the Lead advances
    ld.reg.call("rk_checkpoint_answer", {"project": "demo", "checkpoint_id": review["id"],
                                         "answer": {"decision": "approve", "topic_id": topic_id}},
                CallContext(role="cli", actor="researcher", idempotency_key=uuid.uuid4().hex))
    res = ld.stage("I approved it.", "rk_stage_advance", {})
    assert (res["ok"], res["stage"], res["current"]) == (True, "topics", "question")
    (out,) = ld.say("Show the topics.", ("rk_topic_list", {}))
    assert [(t["id"], t["status"]) for t in _ok(out)["topics"]] == [(topic_id, "approved")]
    assert current_stage(ps) == "question"


def test_guidance_reaches_the_next_stage_run(lead):
    state: dict = {}

    def orchestrator(k, reg):
        _, lib = k.project("demo")
        stroke, bleeding, sources = _stroke_bleeding(lib)
        plan = {"clusters": [_cluster("Stroke", LANDSCAPE_QUERIES[0], stroke),
                             _cluster("Bleeding", LANDSCAPE_QUERIES[1], bleeding)]}
        state["ex"] = ScriptedExecutor({("scout", "LandscapeQueries"): [{"queries": LANDSCAPE_QUERIES}] * 2,
                                        ("scout", "ClusterPlan"): [plan] * 2})
        return Orchestrator(k, reg, executor_factory=lambda p, r, s: state["ex"], sources_factory=lambda p: sources)

    ld = lead(orchestrator, intake={"discovery_mode": "d1"})
    ld.stage("Start.", "rk_stage_run", {"stage": "intake"})
    ld.stage("Pass it.", "rk_stage_advance", {})
    assert ld.stage("Landscape.", "rk_stage_run", {"stage": "landscape"})["status"] == "done"
    ld.say(f"Please {FEEDBACK}.", ("rk_exploration_round", {"stage": "landscape", "feedback": FEEDBACK}))
    assert ld.stage("Again, please.", "rk_stage_run", {"stage": "landscape"})["status"] == "done"

    calls = state["ex"].calls
    assert [c["attempt"] for c in calls] == [1, 1, 2, 2]
    for c in calls:
        assert (FEEDBACK in json.dumps(c["inputs"])) == (c["attempt"] == 2), c["schema"]


def _fake_stages(k, reg, landscape: FakeWorker | None = None):
    workers = {"landscape": landscape or FakeWorker(), "gaps": FakeWorker(), "topics": FakeWorker(),
               "question": FakeWorker()}
    return Orchestrator(k, reg, workers=workers, executor_factory=lambda p, r, s: ScriptedExecutor({}),
                        sources_factory=lambda p: FakeSources({}))


def test_a_second_stage_call_in_one_reply_is_refused_while_the_first_runs(lead):
    """The Lead may not chain run and advance in one reply any more: the stage runs in the background, so the
    second call gets stage_busy (and says to poll rk_stage_status) unless the first already finished."""
    import threading

    release = threading.Event()

    def slow(ctx):
        assert release.wait(10)
        return StageOutcome("done")

    landscape = FakeWorker(runs=[slow])
    ld = lead(lambda k, reg: _fake_stages(k, reg, landscape), intake={"discovery_mode": "d1"})
    ld.stage("Start.", "rk_stage_run", {"stage": "intake"})
    ld.stage("Pass it.", "rk_stage_advance", {})
    try:
        out = ld.say("Landscape, then advance.", ("rk_stage_run", {"stage": "landscape"}), ("rk_stage_advance", {}))
        assert _ok(out[0])["status"] == "running"
        assert out[1]["error"] == "stage_busy" and "rk_stage_status" in json.dumps(out[1])
    finally:
        release.set()
    end = time.monotonic() + 10
    while ld.runs.d1_status("demo")["running"] is not None:  # the Lead polls rk_stage_status until it finished
        assert time.monotonic() < end
        time.sleep(0.01)
    assert ld.stage("Advance now.", "rk_stage_advance", {})["ok"] is True
    assert landscape.kinds() == ["run", "gate"]
    assert current_stage(ld.k.project("demo")[0]) == "gaps"


def test_one_session_reruns_the_same_stage(lead):
    landscape = FakeWorker(gates=[GateResult(False, ["thin"])])
    ld = lead(lambda k, reg: _fake_stages(k, reg, landscape), intake={"discovery_mode": "d1"})
    ld.stage("Start.", "rk_stage_run", {"stage": "intake"})
    ld.stage("Pass it.", "rk_stage_advance", {})
    ld.stage("Landscape.", "rk_stage_run", {"stage": "landscape"})
    assert ld.stage("Advance.", "rk_stage_advance", {})["failures"] == ["thin"]
    ld.stage("Again.", "rk_stage_run", {"stage": "landscape"})
    assert ld.stage("Advance.", "rk_stage_advance", {})["ok"]
    assert landscape.calls == [("run", 1, ""), ("gate", 1), ("run", 2, "thin"), ("gate", 2)]
    ps = ld.k.project("demo")[0]
    assert edge_count(ps, "landscape->landscape") == 1 and current_stage(ps) == "gaps"


def test_p1_dead_end_is_said_plainly(lead):
    landscape = FakeWorker(gates=[GateResult(False, ["thin"])] * 10,
                           degrades=[StageOutcome("failed", reason="no cluster with 5 verified sources")] * 2)

    def orchestrator(k, reg):
        return Orchestrator(k, reg, workers={"landscape": landscape},
                            executor_factory=lambda p, r, s: ScriptedExecutor({}),
                            sources_factory=lambda p: FakeSources({}))

    ld = lead(orchestrator, intake={"discovery_mode": "d1", "production_mode": "auto"})
    ld.stage("Start.", "rk_stage_run", {"stage": "intake"})
    ld.stage("Pass it.", "rk_stage_advance", {})
    for i in range(3):
        assert ld.stage(f"Landscape, try {i + 1}.", "rk_stage_run", {"stage": "landscape"})["status"] == "done"
        assert ld.stage("Advance.", "rk_stage_advance", {})["failures"] == ["thin"]
    assert edge_count(ld.k.project("demo")[0], "landscape->landscape") == 2

    for _ in range(2):  # running it again says the same thing: no new attempt, no silent loop
        res = ld.stage("Run the landscape again.", "rk_stage_run", {"stage": "landscape"})
        assert res["status"] == "failed" and res["current"] == "landscape"
        assert res["reason"].startswith("dead end: landscape->landscape reached its limit of 2 uses")
        assert "no cluster with 5 verified sources" in res["reason"]
        assert "running 'landscape' again only degrades it again (no new attempt)" in res["reason"]
        assert "unlikely to get it through" in res["reason"]
        assert "collab" in res["reason"]
    assert landscape.kinds().count("run") == 3 and landscape.kinds().count("degrade") == 2
