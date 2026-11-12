"""The judges' point ledger (P1 protocol diagnosis 2026-11-12, §7): the protocol vote's judges return numbered points,
the Ideator and the Scout answer every open point routed to them inside the attempt, the next vote sees the points,
the answers, the kernel's rules sheet and a diff, and each judge closes only its own points. An approve with an open
blocking point of the judge's own counts as revise."""

from __future__ import annotations

import pytest

from rk.ids import new_id
from rk.llm.types import TaskResult
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.state import latest_attempt, stage_row, start_run
from rk.production import store
from rk.production.protocol import (
    KERNEL_RULES,
    PRISMA_P_TEXT,
    ProtocolVoteOut,
    ProtocolWorker,
    dropped_prisma_p,
    marked_point,
    points_ledger,
    unmatched_marks,
)
from rk.production.vote import vote
from tests.discovery_fakes import FakeSources, FakeWorker, ScriptedExecutor
from tests.production_fakes import (
    a_protocol,
    make_frozen_project,
    production_ctx,
    votes_on,
)

T0 = 1_800_000_000.0
QUERIES = ["aspirin stroke elderly", "aspirin primary prevention octogenarians", "antiplatelet stroke over 80"]
SR_ITEMS = {k: f"The review's {k.replace('_', ' ')}: as the methods state." for k in PRISMA_P_TEXT}


def _draft(*answers: str, exclude=(("E1", "Animal studies"),)) -> dict:
    """A sound draft (any paper type) answering ``answers`` (point ids) as changed."""
    return {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}],
                            "exclude": [{"id": i, "text": t} for i, t in exclude]},
            "extraction_schema": [{"name": "study_id", "description": "First author and year", "type": "text",
                                   "source": "record"},
                                  {"name": "n", "description": "Participants", "type": "number"}],
            "synthesis_plan": {"method": "narrative_swim", "outcomes": [{"name": "stroke"}]},
            "prisma_p": {"rationale": "Guidelines stop at 80.", "objectives": "Estimate benefit and harm."},
            "answers": [_answer(a) for a in answers]}


def _answer(point_id: str, how: str = "changed") -> dict:
    return {"point_id": point_id, "how": how, "note": f"{point_id}: see eligibility E2"}


def _strategy(*answers: str) -> dict:
    return {"queries": list(QUERIES), "sources": ["openalex", "europepmc"], "date_from": 2000, "date_to": None,
            "answers": [_answer(a) for a in answers]}


def _point(section: str = "eligibility", fault: str = "I1 does not say how age is measured", *,
           blocking: bool = True) -> dict:
    return {"section": section, "fault": fault, "change": f"fix: {fault}", "blocking": blocking}


def _opinion(choice: str, points=(), resolved=(), reason: str = "") -> dict:
    return {"choice": choice, "reason": reason or f"says {choice}", "points": list(points),
            "resolved": [{"point_id": p, "resolved": ok, "why": "answered" if ok else "not yet"} for p, ok in resolved]}


def _executor(drafts, strategies, judge, judge_2) -> ScriptedExecutor:
    return ScriptedExecutor({("ideator", "ProtocolDraftOut"): drafts, ("scout", "SearchStrategyOut"): strategies,
                             ("judge", "ProtocolVoteOut"): judge, ("judge_2", "ProtocolVoteOut"): judge_2})


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _ctx(env, ex, *, attempt: int = 1, sources=None):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="protocol", attempt=attempt, sources=sources or FakeSources({}))


def _ids(ps, status: str | None = None) -> list[str]:
    return [p["id"] for p in points_ledger(ps) if status is None or p["status"] == status]


def _steps(o: Orchestrator, run_id: str) -> str:
    for _ in range(40):
        status = o.step("demo", run_id, actor="tester")
        if status in {"done", "failed", "paused_waiting_human", "paused_budget"}:
            return status
    raise AssertionError("run did not settle")


def test_points_are_numbered_and_carried_across_runs(env):
    """Three P1 votes say revise with points; the third hits the edge limit and the run fails, leaving the stage's
    guidance at the attempt-2 vote's reasons (diagnosis §5.3). A new run's attempt 4 gets every open point from the
    ledger, numbered by attempt, judge and order (judge_2's first revise, a reason only, became p1.judge_2.1), and
    none of that stale text."""
    k, ps, _ = env
    a2 = ("p1.judge.1", "p1.judge_2.1")
    a3 = (*a2, "p2.judge.1", "p2.judge_2.1")
    ex = _executor(
        drafts=[_draft(), _draft(*a2), _draft(*a3), _draft(*a3, "p3.judge_2.1"), _draft()],
        strategies=[_strategy()] * 5,
        judge=[_opinion("revise", [_point()], reason="STALE-1"),
               _opinion("revise", [_point("synthesis", "no harm outcome")], reason="STALE-2"),
               _opinion("revise", reason="judge round 3"), _opinion("revise")],
        judge_2=[_opinion("revise", reason="judge_2 round 1"),
                 _opinion("revise", [_point("prisma_p", "rationale cites no source", blocking=False)],
                          reason="STALE-3"),
                 _opinion("revise", [_point("eligibility", "E1 too broad")], reason="judge_2 round 3"),
                 _opinion("revise")])
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", "question")}
    workers["protocol"] = ProtocolWorker()
    o = Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                     sources_factory=lambda project: FakeSources({}), clock=lambda: T0)
    assert _steps(o, start_run(ps, until="protocol", actor="tester", clock=lambda: T0)) == "failed"
    assert latest_attempt(ps, "protocol") == 3
    stale = stage_row(ps, "protocol")["guidance"]
    assert "STALE-2" in stale and "STALE-3" in stale  # the attempt-2 vote's reasons are still the stage's guidance

    _steps(o, start_run(ps, until="protocol", actor="tester", clock=lambda: T0))
    ideator = next(c["inputs"] for c in ex.calls if c["role"] == "ideator" and c["attempt"] == 4)
    assert [p["id"] for p in ideator["open_points"]] == ["p1.judge.1", "p1.judge_2.1", "p2.judge.1", "p2.judge_2.1",
                                                         "p3.judge_2.1"]
    assert [p["owner"] for p in ideator["open_points"]] == ["ideator"] * 5
    assert not any(f"STALE-{n}" in ideator.get("guidance", "") for n in (1, 2, 3))
    # the answers each version gave are in the ledger, from the versions' events
    first = next(p for p in points_ledger(ps) if p["id"] == "p1.judge.1")
    assert [(a["attempt"], a["by"], a["how"]) for a in first["answers"]] == [
        (2, "ideator", "changed"), (3, "ideator", "changed"), (4, "ideator", "changed")]


def test_every_open_point_must_be_answered_in_the_attempt(env):
    """An Ideator draft that leaves its open point unanswered, and a Scout strategy that leaves its search point
    unanswered, fail in the attempt with the point named; the retry answers them and goes to the vote."""
    _, ps, _ = env
    search = _point("search", "no query for octogenarians")
    ex = _executor(drafts=[_draft(), _draft(), _draft("p1.judge.1")],
                   strategies=[_strategy(), _strategy(), _strategy("p1.judge_2.1")],
                   judge=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.1", True)])],
                   judge_2=[_opinion("revise", [search]), _opinion("approve", resolved=[("p1.judge_2.1", True)])])
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "rerun"
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    unanswered, answered = store.protocols(ps, 2)  # one attempt: the retry spent no edge
    tried = {e.entity_id: e.payload["tried"] for e in ps.log.iter() if e.type == "protocol.added"}
    first = tried[unanswered["id"]]["failures"]
    assert any("p1.judge.1" in f and "changed" in f and "rebutted" in f for f in first["draft"])
    assert any("p1.judge_2.1" in f for f in first["strategy"])
    assert not any("p1.judge_2.1" in f for f in first["draft"]) and not any("p1.judge.1" in f for f in first["strategy"])
    assert tried[answered["id"]]["failures"] == {"strategy": [], "draft": []}
    retry = [c["inputs"] for c in ex.calls if c["role"] == "ideator"][2]
    assert "p1.judge.1" in retry["guidance"]
    # answers live in the version's event, never in the protocol body
    assert "answers" not in answered["body"]
    assert [(a["point_id"], a["by"]) for a in tried[answered["id"]]["answers"]] == [
        ("p1.judge.1", "ideator"), ("p1.judge_2.1", "scout")]
    assert store.frozen_protocol(ps)["id"] == answered["id"] and _ids(ps, "open") == []


def test_judges_see_points_answers_rules_and_a_diff(env):
    redraft = _draft("p1.judge.1", exclude=(("E1", "Animal studies"), ("E2", "Case reports")))
    ex = _executor(drafts=[_draft(), redraft],
                   strategies=[_strategy(), _strategy()],
                   judge=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.1", True)])],
                   judge_2=[_opinion("approve"), _opinion("approve")])
    w = ProtocolWorker()
    w.run(_ctx(env, ex))
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    first, second = [c for c in ex.calls if c["role"] == "judge"]
    subject = second["inputs"]["subject"]
    (shown,) = subject["open_points"]
    assert shown["id"] == "p1.judge.1" and [a["note"] for a in shown["answers"]] == ["p1.judge.1: see eligibility E2"]
    assert subject["kernel_rules"] == KERNEL_RULES
    assert isinstance(subject["protocol"], str) and "Protocol version 2" in subject["protocol"]
    assert "hksj" not in subject["protocol"] and "pi_df" not in subject["protocol"]  # a narrative plan
    assert "+- E2: Case reports" in subject["changes"] and "version 1" in subject["changes"]
    assert first["inputs"]["subject"]["changes"] is None and first["inputs"]["subject"]["open_points"] == []
    judge_2 = next(c for c in ex.calls if c["role"] == "judge_2")
    assert (first["inputs"]["you"], judge_2["inputs"]["you"]) == ("judge", "judge_2")


def test_a_point_closes_only_by_its_judge(env):
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge.1"), _draft("p1.judge.1")],
                   strategies=[_strategy()] * 3,
                   judge=[_opinion("revise", [_point()]), _opinion("revise", resolved=[("p1.judge.1", False)]),
                          _opinion("approve", resolved=[("p1.judge.1", True)])],
                   judge_2=[_opinion("approve"), _opinion("approve", resolved=[("p1.judge.1", True)]),
                            _opinion("approve")])
    w = ProtocolWorker()
    w.run(_ctx(env, ex))
    assert w.run(_ctx(env, ex, attempt=2)).status == "rerun"
    (point,) = points_ledger(ps)
    assert point["status"] == "open"  # judge_2's mark does not close judge's point
    assert [(m["role"], m["resolved"]) for m in point["marks"]] == [("judge", False), ("judge_2", True)]
    assert w.run(_ctx(env, ex, attempt=3)).status == "done"  # its judge's mark closed it: the approve stands
    (point,) = points_ledger(ps)
    assert (point["role"], point["section"], point["blocking"]) == ("judge", "eligibility", True)
    assert point["marks"][-1] == {"attempt": 3, "role": "judge", "resolved": True, "why": "answered"}
    assert point["status"] == "resolved"  # closed by its judge; the freeze does not change that


def test_approve_with_an_open_blocking_point_counts_as_revise(env):
    """judge answers approve with a blocking point of its own (new, then earlier and not marked): its effective
    choice is revise and nothing freezes. Advisory points alone do not keep an approve from freezing."""
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge.1"), _draft("p1.judge.1")],
                   strategies=[_strategy()] * 3,
                   judge=[_opinion("approve", [_point()]), _opinion("approve"),
                          _opinion("approve", [_point("other", "a typo", blocking=False)],
                                   resolved=[("p1.judge.1", True)])],
                   judge_2=[_opinion("approve"), _opinion("approve"),
                            _opinion("approve", [_point("synthesis", "say why narrative", blocking=False)])])
    w = ProtocolWorker()
    for attempt in (1, 2):
        out = w.run(_ctx(env, ex, attempt=attempt))
        assert out.status == "rerun" and store.frozen_protocol(ps) is None
        (row,) = store.protocols(ps, attempt)
        (v,) = votes_on(ps, "protocol", row["id"])
        judge = v["opinions"][0]
        assert (v["decision"], judge["choice"], judge["effective"]) == ("revise", "approve", "revise")
        assert "judge (openai) approved with a blocking point open (p1.judge.1)" in out.reason
        assert "effective" not in v["opinions"][1]
    assert w.run(_ctx(env, ex, attempt=3)).status == "done"
    (row,) = store.protocols(ps, 3)
    assert store.frozen_protocol(ps)["id"] == row["id"]
    assert _ids(ps, "open") == []  # advisory points stay in the ledger, retired at the freeze
    assert _ids(ps, "retired") == ["p3.judge.1", "p3.judge_2.1"] and _ids(ps, "resolved") == ["p1.judge.1"]


def test_other_votes_unchanged(env):
    """A vote without ``output`` (screening, final review, audit) asks and stores what it did before; with
    ``output`` each opinion also stores the output's extra fields."""
    k, ps, lib = env
    plain = ScriptedExecutor({(r, "VoteOut"): [{"choice": "exclude", "reason": "animal study"}]
                              for r in ("judge", "judge_2", "judge_3")})
    rid = store.add_record(ps, 1, "src_1", language="en", actor="tester")
    res = vote(production_ctx(k, ps, lib, plain), "record", rid, "Meets the criteria?", ["include", "exclude"],
               "include", {"title": "Aspirin over 80"})
    assert res.decision == "exclude"
    assert all(set(c["inputs"]) == {"question", "options", "subject"} for c in plain.calls)
    (v,) = votes_on(ps, "record", rid)
    assert [set(o) for o in v["opinions"]] == [{"role", "vendor", "choice", "reason"}] * 3
    pointed = ScriptedExecutor({(r, "ProtocolVoteOut"): [_opinion("exclude", [_point()])]
                                for r in ("judge", "judge_2", "judge_3")})
    vote(production_ctx(k, ps, lib, pointed, attempt=2), "record", rid, "Meets the criteria?",
         ["include", "exclude"], "include", {"title": "Aspirin over 80"}, output=ProtocolVoteOut)
    _, with_points = votes_on(ps, "record", rid)
    assert [set(o) for o in with_points["opinions"]] == [{"role", "vendor", "choice", "reason", "points",
                                                          "resolved"}] * 3
    assert with_points["opinions"][0]["points"] == [_point()]


def test_a_redraft_may_drop_an_item_an_open_point_asks_to_remove():
    previous = {"prisma_p": {"rationale": "Guidelines stop at 80.", "dissemination": "A press release."}}
    redraft = a_protocol(prisma_p={"rationale": "Guidelines stop at 80."})
    remove = {**_point("prisma_p", "dissemination is no protocol item"), "change": "Remove the dissemination item"}
    assert dropped_prisma_p(previous, redraft, [remove]) == []
    # a point that names the item without asking to remove it, or a search point (the Scout's), exempts nothing
    for point in ({**remove, "change": "Name the journal in dissemination"}, {**remove, "section": "search"}):
        (failure,) = dropped_prisma_p(previous, redraft, [point])
        assert "'dissemination'" in failure


# ---- fix round 1 ----
def test_a_revise_with_no_blocking_point_of_its_own_becomes_a_point(env):
    """A judge whose effective choice is revise but who leaves no blocking point of its own open (only a reason, or
    only advisory points) gets one blocking point built from its reason, so the next attempt has it to answer; an
    approve gets none."""
    _, ps, _ = env
    advisory = _point("synthesis", "say why narrative", blocking=False)
    ex = _executor(drafts=[_draft()], strategies=[_strategy()],
                   judge=[_opinion("revise", reason="Add a harm outcome.")],
                   judge_2=[_opinion("revise", [advisory], reason="Name the RoB tool.")])
    assert ProtocolWorker().run(_ctx(env, ex)).status == "rerun"
    built = {p["id"]: p for p in points_ledger(ps)}
    assert list(built) == ["p1.judge.1", "p1.judge_2.1", "p1.judge_2.2"]
    for pid, reason in (("p1.judge.1", "Add a harm outcome."), ("p1.judge_2.2", "Name the RoB tool.")):
        p = built[pid]
        assert (p["section"], p["fault"], p["change"], p["blocking"], p["status"]) == (
            "other", reason, reason, True, "open")
    # the disagreeing side: only the revising judge gets a point built from its reason
    ex2 = _executor(drafts=[_draft("p1.judge.1", "p1.judge_2.1", "p1.judge_2.2")], strategies=[_strategy()],
                    judge=[_opinion("approve", resolved=[("p1.judge.1", True)])],
                    judge_2=[_opinion("revise", resolved=[("p1.judge_2.2", True)], reason="Still vague.")])
    assert ProtocolWorker().run(_ctx(env, ex2, attempt=2)).status == "rerun"
    assert _ids(ps, "open") == ["p1.judge_2.1", "p2.judge_2.1"]
    assert next(p for p in points_ledger(ps) if p["id"] == "p2.judge_2.1")["fault"] == "Still vague."


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_points_retire_at_a_freeze_and_are_not_served_after_a_reopening(env):
    k, ps, _ = env
    sr = {**_draft(), "prisma_p": dict(SR_ITEMS)}
    ex = _executor(drafts=[sr, sr], strategies=[_strategy(), _strategy()],
                   judge=[_opinion("approve", [_point(blocking=False)]), _opinion("approve")],
                   judge_2=[_opinion("approve"), _opinion("approve")])
    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", "question", "read_extract")}
    workers["search_screen"] = FakeWorker(runs=[StageOutcome("back_edge", to="protocol", reason="too broad"),
                                                StageOutcome("done")])
    workers["protocol"] = ProtocolWorker()
    o = Orchestrator(k, None, workers=workers, executor_factory=lambda project, run_id, session: ex,
                     sources_factory=lambda project: FakeSources({}), clock=lambda: T0)
    assert _steps(o, start_run(ps, until="search_screen", actor="tester", clock=lambda: T0)) == "done"
    assert latest_attempt(ps, "protocol") == 2
    second = next(c["inputs"] for c in ex.calls if c["role"] == "ideator" and c["attempt"] == 2)
    assert second["open_points"] == []  # the advisory point open at the freeze is not served again
    judged = [c for c in ex.calls if c["role"] == "judge"][1]
    assert judged["inputs"]["subject"]["open_points"] == []
    (point,) = points_ledger(ps)
    assert (point["id"], point["status"]) == ("p1.judge.1", "retired")


def test_an_own_point_marked_unresolved_again_keeps_an_approve_from_freezing(env):
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge.1"), _draft("p2.judge_2.1")],
                   strategies=[_strategy()] * 3,
                   judge=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.1", True)]),
                          _opinion("approve", resolved=[("p1.judge.1", False)])],
                   judge_2=[_opinion("approve"), _opinion("revise", [_point("other", "typo")]),
                            _opinion("approve", resolved=[("p2.judge_2.1", True)])])
    w = ProtocolWorker()
    for attempt in (1, 2):
        assert w.run(_ctx(env, ex, attempt=attempt)).status == "rerun"
    out = w.run(_ctx(env, ex, attempt=3))
    assert out.status == "rerun" and store.frozen_protocol(ps) is None
    (row,) = store.protocols(ps, 3)
    (v,) = votes_on(ps, "protocol", row["id"])
    assert v["opinions"][0]["effective"] == "revise" and "(p1.judge.1)" in out.reason


def test_a_narrowing_keeps_the_scout_answers_it_does_not_repeat(env):
    _, ps, _ = env
    narrow = ["aspirin stroke elderly trial", *QUERIES[1:]]
    counts = {(QUERIES[0], "openalex"): 4000}  # above the cap 3000; the narrower strategy counts 0
    narrowed = {**_strategy(), "queries": narrow,
                "answers": [{"point_id": "p1.judge_2.1", "how": "changed", "note": "narrowed: dropped query 1"}]}
    ex = _executor(drafts=[_draft(), _draft()],
                   strategies=[{**_strategy(), "queries": narrow}, _strategy("p1.judge_2.1", "p1.judge_2.2"),
                               narrowed],
                   judge=[_opinion("approve"), _opinion("approve")],
                   judge_2=[_opinion("revise", [_point("search", "no octogenarian query"),
                                                _point("search", "no 2024 cut-off")]),
                            _opinion("approve", resolved=[("p1.judge_2.1", True), ("p1.judge_2.2", True)])])
    w = ProtocolWorker()
    sources = FakeSources({}, record_counts=counts)
    assert w.run(_ctx(env, ex, sources=sources)).status == "rerun"
    assert w.run(_ctx(env, ex, attempt=2, sources=sources)).status == "done"
    voted = store.frozen_protocol(ps)
    assert voted["body"]["search_strategy"]["queries"] == narrow
    tried = next(e.payload["tried"] for e in ps.log.iter() if e.type == "protocol.added" and e.entity_id == voted["id"])
    assert {a["point_id"]: a["note"] for a in tried["answers"]} == {
        "p1.judge_2.1": "narrowed: dropped query 1", "p1.judge_2.2": "p1.judge_2.2: see eligibility E2"}


def test_an_old_cycle_point_marked_unresolved_does_not_tighten_a_current_vote(env):
    """judge's point, resolved in the cycle a freeze ended, is marked unresolved in a later cycle's vote: it is not
    live, so the approve stands, and the point stays resolved."""
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge.1"), _draft()], strategies=[_strategy()] * 3,
                   judge=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.1", True)]),
                          _opinion("approve", resolved=[("p1.judge.1", False)])],
                   judge_2=[_opinion("approve"), _opinion("approve"), _opinion("approve")])
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "rerun"
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    out = w.run(_ctx(env, ex, attempt=3))  # a later cycle (after the freeze)
    assert out.status == "done", out.reason
    (row,) = store.protocols(ps, 3)
    assert store.frozen_protocol(ps)["id"] == row["id"]
    (v,) = votes_on(ps, "protocol", row["id"])
    assert "effective" not in v["opinions"][0]
    (point,) = points_ledger(ps)
    assert (point["id"], point["status"], point["live"]) == ("p1.judge.1", "resolved", False)


# ---- final-review fix round ----
@pytest.mark.parametrize("env", [{"paper_type": "scoping_review", "production_mode": "collab"}], indirect=True)
def test_p2_a_comment_asking_to_remove_an_item_lets_the_redraft_drop_it(env):
    """P2 has no open points: your revise comment is the guidance, and an item it asks to remove may go."""
    _, ps, _ = env
    with_rob = {**_draft(), "synthesis_plan": {"method": "evidence_map"},
                "prisma_p": {"rationale": "Guidelines stop at 80.", "risk_of_bias": "RoB 2 for trials."}}
    without = {**with_rob, "prisma_p": {"rationale": "Guidelines stop at 80."}}
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [with_rob, without, without],
                           ("scout", "SearchStrategyOut"): [_strategy(), _strategy()]})
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "waiting_human"
    rerun = _ctx(env, ex, attempt=2)
    rerun.guidance = "A scoping review does not assess bias. Remove the risk of bias item."
    assert w.run(rerun).status == "waiting_human"
    (redraft, *_) = store.protocols(ps, 2)
    assert "risk_of_bias" not in redraft["body"]["prisma_p"]
    tried = next(e.payload["tried"] for e in ps.log.iter() if e.type == "protocol.added" and e.entity_id == redraft["id"])
    assert tried["failures"]["draft"] == []
    # guidance that names the item without asking to remove it exempts nothing
    previous = {"prisma_p": with_rob["prisma_p"]}
    body = a_protocol(prisma_p=without["prisma_p"])
    assert dropped_prisma_p(previous, body, guidance="Remove the funnel plot. Keep risk of bias.")
    assert dropped_prisma_p(previous, body, guidance="Please drop the risk-of-bias item.") == []


class _Crashes(ScriptedExecutor):
    """``crash`` gives no output (a failed task); the other roles answer as scripted."""

    def __init__(self, script, crash: str):
        super().__init__(script)
        self.crash = crash

    def run(self, role, instructions, inputs, output_schema, **kw):
        if role != self.crash:
            return super().run(role, instructions, inputs, output_schema, **kw)
        self.calls.append({"role": role, "schema": output_schema.__name__, "inputs": inputs})
        return TaskResult(new_id("tsk"), False, None, "", "ollama", "deepseek-v4.1-flash:cloud", 0.0, "timeout")


def test_a_judge_answering_outside_the_options_still_reaches_the_authors(env):
    """A judge that answered (its points and marks are stored) with a choice outside the options gets a point built
    from its reason; a judge whose task failed gives none."""
    _, ps, _ = env
    script = {("ideator", "ProtocolDraftOut"): [_draft()], ("scout", "SearchStrategyOut"): [_strategy()],
              ("judge", "ProtocolVoteOut"): [_opinion("maybe", reason="E1 is vague.")]}
    ex = _Crashes(script, "judge_2")
    assert ProtocolWorker().run(_ctx(env, ex)).status == "rerun"
    (point,) = points_ledger(ps)
    assert (point["id"], point["section"], point["blocking"], point["status"]) == ("p1.judge.1", "other", True, "open")
    assert point["fault"] == "answered 'maybe', not one of the options: E1 is vague."  # the reason as stored


def test_any_open_ideator_point_may_ask_to_remove_an_item():
    """A point built from a judge's reason (section other) asks to remove an item in hyphenated words."""
    previous = {"prisma_p": {"rationale": "Guidelines stop at 80.", "certainty_of_evidence": "GRADE per outcome."}}
    redraft = a_protocol(prisma_p={"rationale": "Guidelines stop at 80."})
    why = "Drop the certainty-of-evidence item: the review rates no certainty."
    reason_point = {"section": "other", "fault": why, "change": why, "blocking": True}
    assert dropped_prisma_p(previous, redraft, [reason_point]) == []
    (failure,) = dropped_prisma_p(previous, redraft, [{**reason_point, "section": "search"}])  # the Scout's
    assert "'certainty_of_evidence'" in failure



# ---- calibration 1 (P1 protocol rerun 2026-11-12) ----
def test_a_mark_id_is_matched_ignoring_separators(env):
    """judge_2 marks its point p1.judge_2.1 as p1.judge.2.1: the mark closes it and the approve stands; the vote keeps
    the mark as given."""
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge_2.1")], strategies=[_strategy()] * 2,
                   judge=[_opinion("approve"), _opinion("approve")],
                   judge_2=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.2.1", True)])])
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "rerun"
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    (point,) = points_ledger(ps)
    assert (point["id"], point["status"]) == ("p1.judge_2.1", "resolved")
    (row,) = store.protocols(ps, 2)
    (v,) = votes_on(ps, "protocol", row["id"])
    judge_2 = v["opinions"][1]
    assert "effective" not in judge_2 and judge_2["resolved"][0]["point_id"] == "p1.judge.2.1"
    assert unmatched_marks(ps) == []


def test_an_unmatched_mark_is_ignored_and_listed(env):
    """A mark matching no own point (another judge's point without separators, an id that does not exist) closes
    nothing, keeps judge_2's approve from standing while its own point is open, and is listed."""
    _, ps, _ = env
    ex = _executor(drafts=[_draft(), _draft("p1.judge.1", "p1.judge_2.1")], strategies=[_strategy()] * 2,
                   judge=[_opinion("revise", [_point()]), _opinion("approve", resolved=[("p1.judge.1", False)])],
                   judge_2=[_opinion("revise", [_point("synthesis", "no harm outcome")]),
                            _opinion("approve", resolved=[("p1judge1", True), ("p9.judge_2.1", True)])])
    w = ProtocolWorker()
    assert w.run(_ctx(env, ex)).status == "rerun"
    assert w.run(_ctx(env, ex, attempt=2)).status == "rerun"
    assert _ids(ps, "open") == ["p1.judge.1", "p1.judge_2.1"]
    assert [m["role"] for m in next(p for p in points_ledger(ps) if p["id"] == "p1.judge.1")["marks"]] == ["judge"]
    (row,) = store.protocols(ps, 2)
    (v,) = votes_on(ps, "protocol", row["id"])
    assert v["opinions"][1]["effective"] == "revise"
    assert [(m["attempt"], m["role"], m["point_id"]) for m in unmatched_marks(ps)] == [
        (2, "judge_2", "p1judge1"), (2, "judge_2", "p9.judge_2.1")]


def test_a_mark_matching_two_own_points_matches_none():
    points = {"p1.judge_2.1": {"role": "judge_2"}, "p1-judge-2-1": {"role": "judge_2"},
              "p2.judge.1": {"role": "judge"}}
    assert marked_point(points, "judge_2", "p1.judge.2.1") is None
    del points["p1-judge-2-1"]
    assert marked_point(points, "judge_2", "p1.judge.2.1") == "p1.judge_2.1"
    assert marked_point(points, "judge_2", "p2judge1") is None  # judge's point is never judge_2's
    assert marked_point(points, "judge_2", "p2.judge.1") == "p2.judge.1"  # an id of the ledger is taken as given
