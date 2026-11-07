"""The topics stage and V-NOVELTY (Plan 4 Task 7)."""

from __future__ import annotations

import json
import re

import pytest

from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.models import ClosestWork, Gap, GapEvidence, Topic
from rk.discovery.store import (
    add_gap,
    add_topic,
    rounds,
    searches,
    set_topic_status,
    topics,
)
from rk.discovery.topics import TopicsWorker
from rk.library.models import Ids, ItemIn
from rk.notes import NoteIn, add_note
from rk.orchestrator.engine import (
    GateResult,
    Orchestrator,
    StageContext,
    StageOutcome,
    default_workers,
)
from rk.orchestrator.state import (
    current_stage,
    degradations,
    edge_count,
    record_edge_taken,
    run_row,
    stage_row,
    start_run,
)
from rk.verify.novelty import NOTE, novelty_reference

from .discovery_fakes import FakeSources, ScriptedExecutor, add_text_layer, make_project

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}
QUERIES = ["aspirin in the elderly", "aspirin dosing"]
QUOTE = "The cohort excluded patients older than 80 years."


class Stub:
    """A stage that passes at once; ``add(ctx)`` writes the attempt's artifacts."""

    def __init__(self, add=None):
        self.add = add

    def run(self, ctx):
        if self.add:
            self.add(ctx)
        return StageOutcome("done")

    def on_answer(self, ctx, checkpoint):
        return StageOutcome("done")

    def gate(self, ps, attempt):
        return GateResult(True, [])

    def degrade(self, ctx, failures):
        return StageOutcome("failed", reason="stub")


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib = make_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _works(prefix: str, n: int) -> list[ItemIn]:
    return [ItemIn(title=f"{prefix} trial {i}", year=2000 + i, abstract=f"{prefix} trial {i} in adults.",
                   ids=Ids(doi=f"10.1000/{prefix.lower()}-{i}")) for i in range(n)]


def _sources(lib, *, s2: bool = False) -> tuple[FakeSources, list[str], list[str]]:
    """"aspirin" hits 3 works, "elderly" 4 more: QUERIES[0] finds all 7, QUERIES[1] the 3 aspirin works."""
    aspirin, elderly = _works("Aspirin", 3), _works("Elderly", 4)
    ids = [lib.add_item(w, added_by="agent", actor="tester").item_id for w in aspirin + elderly]
    return FakeSources({"aspirin": aspirin, "elderly": elderly}, lib=lib, s2=s2), ids[:3], ids[3:]


def _gaps_writer(ps, lib, out: dict):
    """The gaps stub's artifacts: one verified gap (with a quote note) and one that is not verified."""
    def add(ctx):
        iid = lib.add_item(ItemIn(title="Aspirin cohort", year=2015, ids=Ids(doi="10.1000/cohort")),
                           added_by="agent", actor="tester").item_id
        add_text_layer(ps, iid, ["Introduction to the cohort.", f"Limitations. {QUOTE} More work is needed."])
        note = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=QUOTE, role_in_source="qualification"),
                        actor="tester")
        out["verified"] = add_gap(ps, ctx.attempt, Gap(
            kind="stated_limitation", statement="Aspirin in patients older than 80: not found within the searched "
                                                "scope.", evidence=GapEvidence(note_ids=[note.note_id], item_ids=[iid])),
            verified=True, checks={"quote_note": "pass"}, actor="tester")
        out["unverified"] = add_gap(ps, ctx.attempt, Gap(
            kind="stated_limitation", statement="Unchecked limitation.", evidence=GapEvidence(item_ids=[iid])),
            verified=False, checks={"quote_note": "fail"}, actor="tester")
    return add


def _topic(title: str, gap_ids: list[str]) -> dict:
    return {"title": title, "summary": f"{title}: a summary.", "gap_ids": gap_ids, "feasibility": "Registry data.",
            "data_needed": "A national registry.", "paper_type": "critical_review", "risks": "Few trials."}


def _executor(diffs_for: list[str] | None = None) -> ScriptedExecutor:
    """Scout and judges scripted for many V-NOVELTY calls; the judges describe ``diffs_for``. Tests set the ideator's
    outputs once the gap ids are known."""
    diffs = {"items": [{"item_id": i, "difference": f"The topic studies people over 80; {i} does not."}
                       for i in (diffs_for or [])]}
    return ScriptedExecutor({
        ("scout", "LandscapeQueries"): [{"queries": QUERIES}] * 40,
        ("judge", "DifferencesOut"): [diffs] * 40,
        ("judge_2", "DifferencesOut"): [diffs] * 40,
        ("judge_3", "DifferencesOut"): [diffs] * 40,
    })


def _orch(k, ps, lib, ex, sources, gaps_out: dict) -> Orchestrator:
    return Orchestrator(k, None, workers={"landscape": Stub(), "gaps": Stub(_gaps_writer(ps, lib, gaps_out))},
                        executor_factory=lambda p, r, s: ex, sources_factory=lambda p: sources, clock=lambda: T0)


def _drive(o, run_id: str, limit: int = 40) -> list[str]:
    out = []
    for _ in range(limit):
        out.append(o.step("demo", run_id, actor="tester"))
        if out[-1] in STOP:
            return out
    raise AssertionError(f"run did not settle: {out}")


def _waiting(ps) -> dict:
    return get_checkpoint(ps, stage_row(ps, "topics")["waiting_checkpoint"])


def _answer(ps, cp: dict, answer: dict) -> None:
    answer_checkpoint(ps, cp["id"], answer, answered_by="human", actor="reviewer")


def _ideator_calls(ex) -> list[dict]:
    return [c for c in ex.calls if c["role"] == "ideator"]


# Gap ids are not known before the gaps stub runs; "{verified}" / "{unverified}" stand for its two gaps.
def _resolve(spec: list[list[dict]], out: dict) -> list[list[dict]]:
    return [[{**t, "gap_ids": [out.get(g.strip("{}"), g) if g.startswith("{") else g for g in t["gap_ids"]]}
             for t in ts] for ts in spec]


def _to_topics(env, spec: list[list[dict]]):
    """Start a D2 run until topics and drive it to the topics stage; ``spec`` gives each ideator call's topics."""
    k, ps, lib = env
    sources, _, _ = _sources(lib)
    gaps_out: dict = {}
    ex = _executor()
    o = _orch(k, ps, lib, ex, sources, gaps_out)
    run_id = start_run(ps, until="topics", actor="tester", clock=lambda: T0)
    while current_stage(ps) != "topics":
        assert o.step("demo", run_id, actor="tester") == "running"
    ex.script[("ideator", "TopicsOut")] = [{"topics": ts} for ts in _resolve(spec, gaps_out)]
    return o, ex, run_id, gaps_out


def _run_to_review(env, spec: list[list[dict]]):
    """``_to_topics``, then drive the run to its first ``topic_review``."""
    o, ex, run_id, gaps_out = _to_topics(env, spec)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    return o, ex, run_id, gaps_out


# ---- the worker ----
@pytest.mark.parametrize("env", [{"topics_n": 2}], indirect=True)
def test_topics_drop_unverified_gap_ids(env):
    ps = env[1]
    _, ex, _, g = _run_to_review(env, [[_topic("Aspirin over 80", ["{verified}", "{unverified}"]),
                                        _topic("Unchecked topic", ["{unverified}"]),
                                        _topic("Invented gap", ["gap_invented"]),
                                        _topic("Aspirin dose", ["{verified}", "gap_invented"])]])
    stored = topics(ps, 1)
    assert [t["title"] for t in stored] == ["Aspirin over 80", "Aspirin dose"]
    assert [t["gap_ids"] for t in stored] == [[g["verified"]], [g["verified"]]]
    # The ideator saw only the verified gap, with its statement and evidence quote, and the intake.
    (call,) = _ideator_calls(ex)
    assert [x["gap_id"] for x in call["inputs"]["gaps"]] == [g["verified"]]
    assert call["inputs"]["gaps"][0]["quotes"][0]["quote"] == QUOTE
    assert "not found within the searched scope" in call["inputs"]["gaps"][0]["statement"]
    assert call["inputs"]["intake"]["topics_n"] == 2
    cp = _waiting(ps)
    assert cp["kind"] == "topic_review" and cp["options"]["topic_ids"] == [t["id"] for t in stored]


@pytest.mark.parametrize("env", [{"topics_n": 1}], indirect=True)
@pytest.mark.parametrize("field, text", [("title", "The first study to dose aspirin after 80"),
                                         ("summary", "This has never been studied."),
                                         ("feasibility", "An unexplored registry."),
                                         ("risks", "No prior studies to lean on.")])
def test_a_topic_that_claims_novelty_reruns_the_stage(env, field, text):
    ps = env[1]
    claiming = {**_topic("Aspirin over 80", ["{verified}"]), field: text}
    o, ex, run_id, _ = _to_topics(env, [[claiming], [_topic("Aspirin over 80", ["{verified}"])]])
    assert o.step("demo", run_id, actor="tester") == "running"  # rerun: topics->topics
    assert edge_count(ps, "topics->topics") == 1
    guidance = stage_row(ps, "topics")["guidance"]
    label = text if field == "title" else "Aspirin over 80"
    assert f"topic {label!r}: {field} claims novelty" in guidance and "searched scope" in guidance
    assert [c["role"] for c in ex.calls if c["attempt"] == 1 and c["stage"] == "topics"] == ["ideator"]
    assert topics(ps, 1) == []  # nothing stored, no V-NOVELTY spent on it
    assert _drive(o, run_id)[-1] == "paused_waiting_human"


def test_topics_open_topic_review_in_auto_mode_too(env):
    ps = env[1]
    assert ps.meta("production_mode") == "auto"
    spec = [[_topic(f"Topic {i}", ["{verified}"]) for i in range(6)]]
    _, ex, run_id, _ = _run_to_review(env, spec)
    cp = _waiting(ps)
    assert (cp["kind"], cp["status"], cp["stage"]) == ("topic_review", "open", "topics")
    assert run_row(ps, run_id)["status"] == "paused_waiting_human"
    assert len(cp["options"]["topic_ids"]) == 6
    # 1 ideator + 6 x (1 scout + 2 judges) = 19 role tasks, within the stage budget of 40.
    assert len(ex.calls) == 19
    with pytest.raises(ValueError):  # a hard kind: no agent vote answers it
        answer_checkpoint(ps, cp["id"], {"decision": "approve", "topic_id": cp["options"]["topic_ids"][0]},
                          answered_by="vote", actor="agent")


@pytest.mark.parametrize("env", [{"topics_n": 1}], indirect=True)
def test_approve_passes_gate_and_revise_reruns(env):
    k, ps, lib = env
    spec = [[_topic("Aspirin over 80", ["{verified}"])], [_topic("Aspirin in women over 80", ["{verified}"])]]
    o, ex, run_id, _ = _run_to_review(env, spec)
    first = _waiting(ps)
    _answer(ps, first, {"decision": "revise", "target": "topics", "comment": "Focus on women."})
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    # The comment is an exploration round of topics and guides the rerun (once, not repeated).
    assert [(r["stage"], r["feedback"]) for r in rounds(ps)] == [("topics", "Focus on women.")]
    second_call = _ideator_calls(ex)[1]
    assert second_call["attempt"] == 2 and second_call["inputs"]["guidance"] == "Focus on women."
    assert edge_count(ps, "topics->topics") == 1

    # on_answer is idempotent: handling the same answer again records no second round.
    worker = TopicsWorker()
    ctx = StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="topics", attempt=1,
                       run_id=None, guidance="", executor=ex, sources=FakeSources({}), actor="tester")
    assert worker.on_answer(ctx, get_checkpoint(ps, first["id"])).status == "rerun"
    assert len(rounds(ps)) == 1

    second = _waiting(ps)
    (tid,) = second["options"]["topic_ids"]
    _answer(ps, second, {"decision": "approve", "topic_id": tid})
    assert _drive(o, run_id)[-1] == "done"
    assert stage_row(ps, "topics")["status"] == "passed"
    assert [(t["title"], t["status"]) for t in topics(ps, 2)] == [("Aspirin in women over 80", "approved")]
    ctx.attempt = 2
    assert worker.on_answer(ctx, get_checkpoint(ps, second["id"])).status == "done"  # approved once
    assert ps.db.execute("SELECT COUNT(*) FROM events WHERE type='topic.status_set'").fetchone()[0] == 1


@pytest.mark.parametrize("env", [{"topics_n": 1}], indirect=True)
def test_revise_gaps_goes_back_to_gaps(env):
    ps = env[1]
    spec = [[_topic("Aspirin over 80", ["{verified}"])]]
    o, _, run_id, _ = _run_to_review(env, spec)
    _answer(ps, _waiting(ps), {"decision": "revise", "target": "gaps", "comment": "Mine renal outcomes too."})
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "gaps" and edge_count(ps, "topics->gaps") == 1
    assert stage_row(ps, "gaps")["guidance"] == "Mine renal outcomes too."
    assert rounds(ps)[-1]["feedback"] == "Mine renal outcomes too."


@pytest.mark.parametrize("env", [{"topics_n": 1}], indirect=True)
def test_invalid_answer_reopens_the_review_saying_why(env):
    k, ps, lib = env
    o, ex, run_id, _ = _run_to_review(env, [[_topic("Aspirin over 80", ["{verified}"])]])
    first = _waiting(ps)
    _answer(ps, first, {"decision": "approve", "topic_id": "tpc_unknown"})
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    second = _waiting(ps)
    assert second["id"] != first["id"] and second["kind"] == "topic_review"
    assert "could not be applied" in second["prompt"] and "tpc_unknown" in second["prompt"]
    assert second["options"]["topic_ids"] == first["options"]["topic_ids"]
    # Handling the same answer again returns the same new review (no second checkpoint).
    ctx = StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="topics", attempt=1,
                       run_id=None, guidance="", executor=ex, sources=FakeSources({}), actor="tester")
    again = TopicsWorker().on_answer(ctx, get_checkpoint(ps, first["id"]))
    assert (again.status, again.checkpoint_id) == ("waiting_human", second["id"])
    for bad in ({"decision": "maybe"}, {"decision": "revise", "target": "topics", "comment": " "},
                {"decision": "revise", "target": "landscape", "comment": "x"},
                {"decision": "reject", "topic_ids": ["tpc_unknown"]}):
        assert TopicsWorker._invalid(ps, bad, first["options"]["topic_ids"])
    assert all(t["status"] == "candidate" for t in topics(ps, 1))


@pytest.mark.parametrize("env", [{"production_mode": "collab", "topics_n": 1}], indirect=True)
def test_third_revise_opens_stage_blocked(env):
    ps = env[1]
    spec = [[_topic(f"Aspirin over 80, take {i}", ["{verified}"])] for i in range(4)]
    o, ex, run_id, _ = _run_to_review(env, spec)
    for n in range(1, 4):
        cp = _waiting(ps)
        assert cp["kind"] == "topic_review"
        _answer(ps, cp, {"decision": "revise", "target": "topics", "comment": f"Revision {n}."})
        assert _drive(o, run_id)[-1] == "paused_waiting_human"
    blocked = _waiting(ps)
    assert (blocked["kind"], blocked["status"]) == ("stage_blocked", "open")
    assert blocked["options"]["edge"] == "topics->topics" and blocked["options"]["failures"] == ["Revision 3."]
    assert len(_ideator_calls(ex)) == 3 and edge_count(ps, "topics->topics") == 2
    assert not degradations(ps)  # P2 asks; it does not degrade
    _answer(ps, blocked, {"decision": "stop"})
    assert _drive(o, run_id)[-1] == "failed"
    assert len(_ideator_calls(ex)) == 3  # it never loops


@pytest.mark.parametrize("env", [{"topics_n": 2}], indirect=True)
def test_p1_degrade_reopens_review_without_rejected_topics(env):
    ps = env[1]
    spec = [[_topic(f"Topic A{i}", ["{verified}"]), _topic(f"Topic B{i}", ["{verified}"])] for i in range(3)]
    o, ex, run_id, _ = _run_to_review(env, spec)
    for n in range(2):
        _answer(ps, _waiting(ps), {"decision": "revise", "target": "topics", "comment": f"Revision {n}."})
        assert _drive(o, run_id)[-1] == "paused_waiting_human"
    third = _waiting(ps)
    a, b = third["options"]["topic_ids"]
    _answer(ps, third, {"decision": "revise", "target": "topics", "comment": "Not A.", "topic_ids": [a]})
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    reopened = _waiting(ps)
    assert reopened["kind"] == "topic_review" and reopened["id"] != third["id"]
    assert reopened["options"]["topic_ids"] == [b] and reopened["options"]["replaces"] == third["id"]
    (deg,) = degradations(ps, "topics")
    assert deg["removed"] == [a] and deg["attempt"] == 3
    assert {t["id"]: t["status"] for t in topics(ps, 3)} == {a: "rejected", b: "candidate"}
    assert len(_ideator_calls(ex)) == 3 and edge_count(ps, "topics->topics") == 2

    _answer(ps, reopened, {"decision": "approve", "topic_id": b})
    assert _drive(o, run_id)[-1] == "done"
    assert stage_row(ps, "topics")["status"] == "passed"


@pytest.mark.parametrize("env", [{"topics_n": 1}], indirect=True)
def test_p1_degrade_fails_when_no_topic_is_left(env):
    ps = env[1]
    spec = [[_topic(f"Topic {i}", ["{verified}"])] for i in range(3)]
    o, _, run_id, _ = _run_to_review(env, spec)
    for _ in range(3):
        _answer(ps, _waiting(ps), {"decision": "reject"})
        status = _drive(o, run_id)[-1]
    assert status == "failed" and run_row(ps, run_id)["reason"] == "no topics left"
    assert degradations(ps, "topics")[0]["removed"] == [t["id"] for t in topics(ps, 3)]


# ---- V-NOVELTY ----
def _ctx(k, ps, lib, ex, sources) -> StageContext:
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="topics", attempt=1,
                        run_id=None, guidance="", executor=ex, sources=sources, actor="tester")


def test_novelty_without_s2_uses_two_other_sources(env):
    k, ps, lib = env
    sources, aspirin, elderly = _sources(lib)
    ex = ScriptedExecutor({
        ("scout", "LandscapeQueries"): [{"queries": QUERIES + ["Aspirin dosing", "aspirin trials"]}],
        ("judge", "DifferencesOut"): [{"items": [
            {"item_id": aspirin[0], "difference": "The topic is limited to people over 80."},
            {"item_id": aspirin[1], "difference": "This topic is novel."},  # a novelty claim: discarded
            {"item_id": "src_invented", "difference": "Not one of the works."}]}],
        ("judge_2", "DifferencesOut"): [{"items": [
            {"item_id": aspirin[0], "difference": "It uses registry data, not a trial."}]}],
    })
    report = novelty_reference(_ctx(k, ps, lib, ex, sources), "Aspirin in the elderly", generator_vendor="anthropic")

    assert report.sources_used == ["openalex", "europepmc"]
    assert {s["source"] for s in searches(ps, "topics", 1)} == {"openalex", "europepmc"}
    assert {s["purpose"] for s in searches(ps, "topics", 1)} == {"novelty"}
    assert report.queries == QUERIES + ["aspirin trials"]  # 2-3 queries, repeated ones dropped
    # RRF: the aspirin works are in every result list, so they rank first; 7 distinct works found, top 5 kept.
    assert report.found == 7
    assert [c.item_id for c in report.closest] == aspirin + elderly[:2]
    assert report.closest[0].differences == {"openai": "The topic is limited to people over 80.",
                                             "deepseek": "It uses registry data, not a trial."}
    assert report.closest[1].differences == {}
    assert report.note == NOTE == "reference only; never a novelty verdict"
    dumped = json.dumps(report.model_dump(exclude={"note"}))
    assert not re.search("novel", dumped, re.IGNORECASE)
    assert not any("novel" in name for name in report.model_dump())


def test_novelty_with_s2_uses_openalex_and_s2(env):
    k, ps, lib = env
    sources, _, _ = _sources(lib, s2=True)
    ex = _executor()
    assert novelty_reference(_ctx(k, ps, lib, ex, sources), "Aspirin").sources_used == ["openalex", "s2"]


def test_novelty_skips_a_judge_of_the_generator_vendor(env):
    k, ps, lib = env
    sources, aspirin, _ = _sources(lib)
    ex = _executor(diffs_for=aspirin)
    report = novelty_reference(_ctx(k, ps, lib, ex, sources), "Aspirin in the elderly", generator_vendor="openai")
    assert report.skipped == {"judge": "same_or_unknown_vendor"} and report.judged == ["deepseek", "anthropic"]
    assert [c["role"] for c in ex.calls] == ["scout", "judge_2", "judge_3"]
    assert set(report.closest[0].differences) == {"deepseek", "anthropic"}


@pytest.mark.parametrize("env", [{"discovery_mode": "d1"}], indirect=True)
def test_d1_advance_names_the_review_a_p1_degrade_opened(env):
    k, ps, lib = env
    sources, _, _ = _sources(lib)
    g: dict = {}
    ex = _executor()
    o = _orch(k, ps, lib, ex, sources, g)
    for stage in ("intake", "landscape", "gaps"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    ex.script[("ideator", "TopicsOut")] = [{"topics": [_topic("Topic A", [g["verified"]]),
                                                       _topic("Topic B", [g["verified"]])]}]
    out = o.run_stage("demo", "topics", actor="tester")
    review = get_checkpoint(ps, out.checkpoint_id)
    a, b = review["options"]["topic_ids"]
    for n in (1, 2):  # the edge topics->gaps is used up
        record_edge_taken(ps, "topics->gaps", n, actor="tester")
    _answer(ps, review, {"decision": "revise", "target": "gaps", "comment": "Not A.", "topic_ids": [a]})
    res = o.advance("demo", actor="tester")
    reopened = _waiting(ps)
    assert reopened["kind"] == "topic_review" and reopened["options"]["topic_ids"] == [b]
    assert res.failures == [f"checkpoint {reopened['id']} (topic_review) is open"]
    assert current_stage(ps) == "topics" and edge_count(ps, "topics->gaps") == 2


# ---- gate ----
def _stored_topics(ps, gap_id: str, n: int, *, closest: int = 5, found: int = 7) -> list[str]:
    works = [ClosestWork(item_id=f"src_{i}", title=f"Work {i}") for i in range(closest)]
    return [add_topic(ps, 1, Topic(**{**_topic(f"Topic {i}", [gap_id]), "closest": works}), actor="tester",
                      novelty={"queries": QUERIES, "sources_used": ["openalex", "europepmc"], "found": found,
                               "note": NOTE})
            for i in range(n)]


def _passed_gaps(k, ps, lib) -> str:
    out: dict = {}
    o = Orchestrator(k, None, workers={"landscape": Stub(), "gaps": Stub(_gaps_writer(ps, lib, out))},
                     executor_factory=lambda p, r, s: ScriptedExecutor({}), sources_factory=lambda p: FakeSources({}))
    for stage in ("intake", "landscape", "gaps"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    return out["verified"]


def test_d2_gate_needs_topics_n_candidates(env):
    k, ps, lib = env
    gap = _passed_gaps(k, ps, lib)
    ids = _stored_topics(ps, gap, 5)
    set_topic_status(ps, ids[0], "approved", actor="tester", actor_kind="human")
    res = TopicsWorker().gate(ps, 1)
    assert not res.ok and res.failures == ["5 of 6 topics"]
    _stored_topics(ps, gap, 1)
    assert TopicsWorker().gate(ps, 1) == GateResult(True, [])


def test_d2_gate_checks_each_topic(env):
    k, ps, lib = env
    gap = _passed_gaps(k, ps, lib)
    ids = _stored_topics(ps, gap, 4) + _stored_topics(ps, gap, 1, closest=3, found=3)  # fewer found: recorded
    ids += _stored_topics(ps, gap, 1, closest=4, found=9)
    set_topic_status(ps, ids[0], "approved", actor="tester", actor_kind="human")
    assert TopicsWorker().gate(ps, 1).failures == ["topic 'Topic 0': 4 closest works (need 5)"]
    assert TopicsWorker().gate(ps, 2).failures == ["no approved topic", "0 of 6 topics"]


@pytest.mark.parametrize("env", [{"discovery_mode": "d1"}], indirect=True)
def test_d1_gate_needs_one_topic_and_its_approval(env):
    k, ps, lib = env
    gap = _passed_gaps(k, ps, lib)
    (tid,) = _stored_topics(ps, gap, 1)
    assert TopicsWorker().gate(ps, 1).failures == ["no approved topic"]
    set_topic_status(ps, tid, "approved", actor="tester", actor_kind="human")
    assert TopicsWorker().gate(ps, 1).ok


@pytest.mark.parametrize("env", [{"topics_n": 3}], indirect=True)
def test_d2_too_few_topics_reruns_without_asking(env):
    ps = env[1]
    o, ex, run_id, g = _to_topics(env, [])
    ex.script[("ideator", "TopicsOut")] = [{"topics": [_topic("Only one", [g["verified"]])]}]
    assert o.step("demo", run_id, actor="tester") == "running"
    assert not [c for c in list_checkpoints(ps) if c["kind"] == "topic_review"]
    assert stage_row(ps, "topics")["guidance"] == "1 of 3 topics with a verified gap and their closest works"
    assert [c["role"] for c in ex.calls] == ["ideator"]  # no V-NOVELTY task spent on a batch that cannot pass


def test_topics_is_a_default_worker():
    assert isinstance(default_workers()["topics"], TopicsWorker)
