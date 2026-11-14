"""P1 earns another protocol round while it converges (P1 protocol calibration 1, Task 1; the rerun of 2026-11-12):
at the ``protocol->protocol`` limit the kernel grants one more use when the last vote closed a blocking point and left
no more open than before, at most ``KERNEL_EXTRA_ROUNDS`` times; P2 still asks the researcher."""

from __future__ import annotations

import json

import pytest

from rk.checkpoints import list_checkpoints
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.state import edge_row, latest_attempt, run_row, start_run
from rk.production.protocol import KERNEL_EXTRA_ROUNDS, ProtocolWorker
from rk.store.events import NewEvent
from tests.discovery_fakes import FakeSources, FakeWorker, ScriptedExecutor
from tests.production_fakes import make_frozen_project
from tests.test_protocol_ledger import T0, _draft, _opinion, _point, _steps, _strategy

EDGE = "protocol->protocol"


class _Authors(ScriptedExecutor):
    """The Ideator and the Scout give a sound draft and strategy answering every open point routed to them; the
    judges answer as scripted."""

    def run(self, role, instructions, inputs, output_schema, **kw):
        if role in ("ideator", "scout"):
            ids = [p["id"] for p in inputs.get("open_points") or [] if p["owner"] == role]
            self.script[(role, output_schema.__name__)] = [_draft(*ids) if role == "ideator" else _strategy(*ids)]
        return super().run(role, instructions, inputs, output_schema, **kw)


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _orch(k, ex, protocol=None) -> Orchestrator:
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", "question")}
    workers["protocol"] = protocol or ProtocolWorker()
    return Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                        sources_factory=lambda project: FakeSources({}), clock=lambda: T0)


def _judged(judge: list[dict]) -> _Authors:
    """``judge``'s votes in order; judge_2 approves every time. Asked to mark the points a revise left unmarked, judge
    marks none (the shapes' marks are all in its votes)."""
    return _Authors({("judge", "ProtocolVoteOut"): judge, ("judge_2", "ProtocolVoteOut"): [_opinion("approve")] * 9,
                     ("judge", "PointMarksOut"): [{"resolved": []}] * 9})


def _grants(ps, by: str | None = None) -> list[dict]:
    """The ``edge.allowed`` payloads of the protocol rerun edge (``by`` kernel: the kernel's only)."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='edge.allowed' ORDER BY seq").fetchall()
    out = [json.loads(r["payload"]) for r in rows]
    return [p for p in out if p["edge"] == EDGE and (by is None or p.get("by") == by)]


def _run(k, ps, ex, protocol=None) -> tuple[str, str]:
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    return _steps(_orch(k, ex, protocol), run_id), run_id


# the shapes of the rerun: the judge's open blocking points after each vote
PROSTATE = [  # 2 -> 2 -> 1, the last vote closing two: one more round, then approved
    _opinion("revise", [_point(), _point("synthesis", "no harm outcome")]),
    _opinion("revise", [_point("extraction", "no unit for n")], resolved=[("p1.judge.1", True)]),
    _opinion("revise", [_point("extraction", "lesion- versus patient-level counts")],
             resolved=[("p1.judge.2", True), ("p2.judge.1", True)]),
    _opinion("approve", resolved=[("p3.judge.1", True)])]


def test_a_converging_protocol_gets_another_round_at_the_limit(env):
    k, ps, _ = env
    ex = _judged(list(PROSTATE))
    status, run_id = _run(k, ps, ex)
    assert status == "done", run_row(ps, run_id)["reason"]
    assert latest_attempt(ps, "protocol") == 4
    assert edge_row(ps, EDGE) == {"count": 3, "allowed": 1}
    (grant,) = _grants(ps)
    assert grant["by"] == "kernel"
    assert "closed 2 blocking points and left 1 open (2 before)" in grant["reason"]


@pytest.mark.parametrize("judge", [
    # fructose: 1 -> 1, nothing closed
    [_opinion("revise", [_point()]), _opinion("revise", resolved=[("p1.judge.1", False)]),
     _opinion("revise", resolved=[("p1.judge.1", False)], reason="the measurement method is still not named")],
    # ozone: 1 -> 3, one closed but more open than before
    [_opinion("revise", [_point()]), _opinion("revise", resolved=[("p1.judge.1", False)]),
     _opinion("revise", [_point("extraction", "split-mouth variance"), _point("synthesis", "several ozone arms"),
                         _point("synthesis", "no pooling rule")], resolved=[("p1.judge.1", True)],
              reason="the measurement method is still not named")],
], ids=["fructose", "ozone"])
def test_a_protocol_that_does_not_converge_degrades_at_the_limit(env, judge):
    k, ps, _ = env
    status, run_id = _run(k, ps, _judged(judge))
    assert status == "failed"
    reason = run_row(ps, run_id)["reason"]
    assert "not approved at the edge limit" in reason and "the measurement method is still not named" in reason
    assert latest_attempt(ps, "protocol") == 3 and _grants(ps) == []


@pytest.mark.parametrize("human_first", [False, True], ids=["kernel-only", "after-a-human-grant"])
def test_the_kernel_grants_at_most_two_rounds(env, human_first):
    """Every vote closes one of six points and leaves fewer open: the kernel grants two rounds, then the third
    converging limit degrades. A human grant (an ``edge.allowed`` from before, without ``by``) raises the limit and
    does not count against the kernel's two."""
    k, ps, _ = env
    if human_first:
        ps.apply(NewEvent(type="edge.allowed", entity_id=EDGE, actor_kind="kernel", actor="tester",
                          payload={"edge": EDGE}))
    six = [_point("other", f"fault {n}") for n in range(1, 7)]
    judge = [_opinion("revise", six)] + [_opinion("revise", resolved=[(f"p1.judge.{n}", True)]) for n in range(1, 7)]
    status, run_id = _run(k, ps, _judged(judge))
    assert status == "failed" and "not approved at the edge limit" in run_row(ps, run_id)["reason"]
    assert KERNEL_EXTRA_ROUNDS == 2 and len(_grants(ps, "kernel")) == 2
    assert latest_attempt(ps, "protocol") == 5 + human_first
    assert edge_row(ps, EDGE) == {"count": 4 + human_first, "allowed": 2 + human_first}


class _Converging(FakeWorker):
    """A protocol worker that always asks for another round at the limit."""

    def another_use(self, ctx, edge):
        return "converging"


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_at_the_limit_asks_the_researcher(env):
    k, ps, _ = env
    worker = _Converging(runs=[StageOutcome("rerun", reason=f"revise {n}") for n in range(1, 4)])
    status, _ = _run(k, ps, ScriptedExecutor({}), worker)
    assert status == "paused_waiting_human"
    [blocked] = list_checkpoints(ps, "open")
    assert blocked["kind"] == "stage_blocked" and blocked["options"]["edge"] == EDGE
    assert _grants(ps) == [] and edge_row(ps, EDGE) == {"count": 2, "allowed": 0}


def test_a_step_interrupted_after_the_grant_does_not_grant_twice(env):
    """The step crashes after the kernel's grant is recorded and before the edge is taken: the grant raised the limit,
    so the next use of the edge is an ordinary one and the kernel does not grant again."""
    k, ps, _ = env
    judge = [*PROSTATE[:3],
             _opinion("revise", [_point("other", "one more")], resolved=[("p3.judge.1", True)]),
             _opinion("approve", resolved=[("p4.judge.1", True)])]
    ex = _judged(judge)
    o = _orch(k, ex)
    take = o._take_edge

    def crash_once(*a, **kw):
        if _grants(ps, "kernel") and edge_row(ps, EDGE)["count"] == 2:
            o._take_edge = take
            raise RuntimeError("killed between the grant and the edge")
        return take(*a, **kw)

    o._take_edge = crash_once
    run_id = start_run(ps, until="protocol", actor="tester", clock=lambda: T0)
    with pytest.raises(RuntimeError, match="killed"):
        _steps(o, run_id)
    assert edge_row(ps, EDGE) == {"count": 2, "allowed": 1}
    assert _steps(o, run_id) == "done", run_row(ps, run_id)["reason"]
    assert len(_grants(ps, "kernel")) == 1
    assert edge_row(ps, EDGE) == {"count": 3, "allowed": 1}
