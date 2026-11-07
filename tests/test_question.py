"""The question stage: RQ structure, the already-done check, rq_approval and the RQ freeze (Plan 4 Task 8)."""

from __future__ import annotations

import json

import pytest

from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.already_done import (
    AlreadyDone,
    JudgedWork,
    Judgement,
    already_done,
    verdict,
)
from rk.discovery.models import HypothesisIn, RQBody, Topic
from rk.discovery.question import QuestionWorker, check_rq
from rk.discovery.store import (
    add_rq,
    add_topic,
    freeze_rq,
    rounds,
    rqs,
    searches,
    set_already_done,
    set_topic_status,
)
from rk.library.models import Ids, ItemIn
from rk.llm.drivers import DriverSpec
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
    run_row,
    stage_row,
    start_run,
)

from .discovery_fakes import FakeSources, ScriptedExecutor, make_project

T0 = 1_800_000_000.0
STOP = {"done", "failed", "cancelled", "paused", "paused_budget", "paused_waiting_human"}
QUERIES = ["aspirin in the elderly", "aspirin dosing", "elderly aspirin trials"]
SENTENCE = ("I am studying aspirin use in people over 80 because I want to find out whether it prevents a first "
            "stroke in order to help my audience understand when to start it.")


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
    return [ItemIn(title=f"{prefix} trial {i}", year=2000 + i, abstract=f"{prefix} trial {i} in adults over 80.",
                   ids=Ids(doi=f"10.1000/{prefix.lower()}-{i}")) for i in range(n)]


def _sources(lib, *, s2: bool = False) -> tuple[FakeSources, list[str]]:
    """"aspirin" hits 3 works, "elderly" 4 more: every query finds some of the 7, all 7 are judged."""
    aspirin, elderly = _works("Aspirin", 3), _works("Elderly", 4)
    ids = [lib.add_item(w, added_by="agent", actor="tester").item_id for w in aspirin + elderly]
    return FakeSources({"aspirin": aspirin, "elderly": elderly}, lib=lib, s2=s2), ids


def _rq(*, x: str = "aspirin use in people over 80", consequences: int = 2, hypotheses: int = 2,
        clinical: bool = False, pico: dict | None = None) -> dict:
    return {
        "body": {
            "three_step": {"x": x, "y": "whether it prevents a first stroke", "z": "when to start it"},
            "problem": {"condition": "Guidelines stop at age 80.",
                        "consequences": [f"Consequence {i}." for i in range(consequences)]},
            "finer": {"feasible": "Registry data exist.", "interesting": "Clinicians ask it.",
                      "novel": "Not found within the searched scope.", "ethical": "Uses existing data.",
                      "relevant": "Ageing populations."},
            "clinical": clinical, "pico": pico,
        },
        "hypotheses": [{"statement": f"Hypothesis {i}: aspirin {'lowers' if i % 2 == 0 else 'does not lower'} "
                                     f"the stroke rate by {i + 1}0%.",
                        "falsification": f"A hazard ratio above {i + 1} would falsify hypothesis {i}."}
                       for i in range(hypotheses)],
    }


def _verdicts(lib, ids: list[str], answering: tuple[str, ...] = (), *, quote=None) -> dict:
    """A judge's ``AnsweredOut``: ``answering`` answer the RQ; each quote is the work's title (verbatim) unless
    ``quote`` gives another."""
    return {"items": [{"item_id": i, "answered": i in answering, "quote": quote or lib.get(i).title} for i in ids]}


def _executor(rq_outputs: list[dict], judge: list[dict], judge_2: list[dict],
              queries: list[str] | None = None, judge_3: list[dict] | None = None) -> ScriptedExecutor:
    """``judge_3`` (anthropic by default) runs only when the RQ author is not anthropic."""
    return ScriptedExecutor({
        ("ideator", "RQOut"): list(rq_outputs),
        ("scout", "LandscapeQueries"): [{"queries": queries or QUERIES}] * 20,
        ("judge", "AnsweredOut"): list(judge),
        ("judge_2", "AnsweredOut"): list(judge_2),
        ("judge_3", "AnsweredOut"): list(judge_3 or []),
    })


def _approved_topic(ps):
    def add(ctx):
        tid = add_topic(ps, ctx.attempt, Topic(title="Aspirin over 80", summary="Aspirin in people over 80.",
                                               gap_ids=["gap_x"], feasibility="Registry data.",
                                               data_needed="A registry.", paper_type="critical_review",
                                               risks="Few trials."), actor="tester")
        set_topic_status(ps, tid, "approved", actor="tester", actor_kind="human")
    return add


def _orch(k, ps, ex, sources, *, judges: list[DriverSpec] | None = None) -> Orchestrator:
    return Orchestrator(k, None, workers={"landscape": Stub(), "gaps": Stub(), "topics": Stub(_approved_topic(ps)),
                                          "question": QuestionWorker(judges=judges)},
                        executor_factory=lambda p, r, s: ex, sources_factory=lambda p: sources, clock=lambda: T0)


def _drive(o, run_id: str, limit: int = 40) -> list[str]:
    out = []
    for _ in range(limit):
        out.append(o.step("demo", run_id, actor="tester"))
        if out[-1] in STOP:
            return out
    raise AssertionError(f"run did not settle: {out}")


def _to_question(o, ps) -> str:
    """Start a D2 run until question and step it to the question stage."""
    run_id = start_run(ps, until="question", actor="tester", clock=lambda: T0)
    while current_stage(ps) != "question":
        assert o.step("demo", run_id, actor="tester") == "running"
    return run_id


def _waiting(ps) -> dict:
    return get_checkpoint(ps, stage_row(ps, "question")["waiting_checkpoint"])


def _answer(ps, cp: dict, answer: dict) -> None:
    answer_checkpoint(ps, cp["id"], answer, answered_by="human", actor="reviewer")


def _calls(ex, role: str) -> list[dict]:
    return [c for c in ex.calls if c["role"] == role]


def _frozen(ps, attempt: int) -> list[dict]:
    return [r for r in rqs(ps, attempt) if r["status"] == "frozen"]


# ---- structure ----
def test_structural_failures_are_named(env):
    k, ps, lib = env
    sources, _ = _sources(lib)
    ex = _executor([_rq(consequences=1, hypotheses=1)], [], [])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "question" and edge_count(ps, "question->question") == 1
    guidance = stage_row(ps, "question")["guidance"]
    assert "problem.consequences has 1 (need at least 2)" in guidance
    assert "1 distinct hypotheses (need at least 2)" in guidance
    assert [c["role"] for c in ex.calls] == ["ideator"]  # no already-done task spent on a malformed RQ
    (stored,) = rqs(ps, 1)  # the RQ is stored, never checked for already-done
    assert stored["already_done"] is None


def test_check_rq_names_every_failure():
    good = _rq(clinical=True, pico={"population": "Adults over 80", "intervention": "Aspirin",
                                    "comparison": "No aspirin", "outcome": "First stroke"})
    body, hyps = RQBody(**good["body"]), [HypothesisIn(**h) for h in good["hypotheses"]]
    assert check_rq(body, hyps) == []
    bad = RQBody(**{**good["body"], "three_step": {"x": " ", "y": "y", "z": ""}, "pico": None,
                    "finer": {**good["body"]["finer"], "ethical": "", "novel": "A novel question: nobody asked it."}})
    same = [HypothesisIn(statement="Aspirin lowers  the rate.", falsification="HR > 1."),
            HypothesisIn(statement="aspirin lowers the rate", falsification="")]
    assert check_rq(bad, same) == ["three_step.x is empty", "three_step.z is empty", "finer.ethical is empty",
                                   "finer.novel claims novelty; say what was not found within the searched scope",
                                   "a clinical RQ needs a PICO", "1 distinct hypotheses (need at least 2)",
                                   "hypothesis 2 has no falsification condition"]
    half_pico = RQBody(**{**good["body"], "pico": {**good["body"]["pico"], "comparison": ""}})
    assert check_rq(half_pico, hyps) == ["pico.comparison is empty"]


@pytest.mark.parametrize("novel", [
    "This is the first study to measure X.", "X has never been studied.", "The first time anyone asks it.",
    "It was never investigated in adults over 80.", "No prior studies exist.", "No previous research addressed it.",
    "An unprecedented question.", "An unexplored area.", "It has not yet been studied.", "Not yet explored.",
    "A NOVEL question.", "Its novelty: nobody asked it."])
def test_check_rq_rejects_common_novelty_claims(novel):
    rq = _rq()
    body = RQBody(**{**rq["body"], "finer": {**rq["body"]["finer"], "novel": novel}})
    assert check_rq(body, [HypothesisIn(**h) for h in rq["hypotheses"]]) == [
        "finer.novel claims novelty; say what was not found within the searched scope"]


@pytest.mark.parametrize("novel", [
    "Not found within the searched scope.", "No trial in PubMed or OpenAlex reports a first stroke in adults over 80.",
    "The searched sources hold no registry study of first strokes after 80; earlier work stops at 75.",
    "Studied only in adults under 80 within the searched scope."])
def test_check_rq_accepts_scoped_not_found_wording(novel):
    rq = _rq()
    body = RQBody(**{**rq["body"], "finer": {**rq["body"]["finer"], "novel": novel}})
    assert check_rq(body, [HypothesisIn(**h) for h in rq["hypotheses"]]) == []


@pytest.mark.parametrize("path, text", [
    (("three_step", "x"), "a novel aspirin dose"), (("three_step", "y"), "what was never studied"),
    (("three_step", "z"), "the first time aspirin is dosed so"), (("problem", "condition"), "It is unexplored."),
    (("problem", "consequences"), ["No previous research exists.", "Consequence 2."]),
    (("finer", "interesting"), "It is unprecedented.")])
def test_check_rq_rejects_novelty_claims_in_every_field(path, text):
    rq = _rq()
    body = json.loads(json.dumps(rq["body"]))
    body[path[0]][path[1]] = text
    out = check_rq(RQBody(**body), [HypothesisIn(**h) for h in rq["hypotheses"]])
    name = ".".join(path) + (".1" if isinstance(text, list) else "")
    assert out == [f"{name} claims novelty; say what was not found within the searched scope"]


def test_check_rq_rejects_novelty_claims_in_hypotheses():
    rq = _rq()
    hyps = [HypothesisIn(statement="A novel dose lowers the rate.", falsification="HR > 1."),
            HypothesisIn(statement="Aspirin does not lower it.", falsification="Never been studied, so none.")]
    assert check_rq(RQBody(**rq["body"]), hyps) == [
        "hypothesis 1.statement claims novelty; say what was not found within the searched scope",
        "hypothesis 2.falsification claims novelty; say what was not found within the searched scope"]



# ---- the already-done check and what follows ----
def test_already_done_by_both_vendors_backs_to_topics_in_d2(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[0],))], [_verdicts(lib, ids, (ids[0], ids[1]))])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "topics" and edge_count(ps, "question->topics") == 1
    assert stage_row(ps, "topics")["guidance"].startswith("already done: Aspirin trial 0")
    (stored,) = rqs(ps, 1)
    result = stored["already_done"]
    assert result["result"] == "already_done" and stored["status"] == "draft"
    first = next(w for w in result["works"] if w["item_id"] == ids[0])
    assert first["judgements"] == {
        "openai": {"answered": True, "quote": "Aspirin trial 0", "ok_quote": True},
        "deepseek": {"answered": True, "quote": "Aspirin trial 0", "ok_quote": True}}
    # 1 ideator + 1 scout + 2 judges: the question attempt's role tasks.
    assert [c["role"] for c in ex.calls] == ["ideator", "scout", "judge", "judge_2"]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_split_in_collab_opens_significance_novelty(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[0],))], [_verdicts(lib, ids)])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    cp = _waiting(ps)
    assert (cp["kind"], cp["status"]) == ("significance_novelty", "open")
    (stored,) = rqs(ps, 1)
    assert cp["options"]["rq_id"] == stored["id"] and cp["options"]["choices"] == ["proceed", "back"]
    assert stored["already_done"]["result"] == "split"
    assert "Aspirin trial 0" in cp["prompt"] and "openai" in cp["prompt"]
    with pytest.raises(ValueError):  # a hard kind: no agent vote answers it
        answer_checkpoint(ps, cp["id"], {"decision": "proceed"}, answered_by="vote", actor="agent")

    _answer(ps, cp, {"decision": "proceed"})  # treated as not found: you still approve the RQ
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    approval = _waiting(ps)
    assert approval["kind"] == "rq_approval" and approval["options"]["rq_id"] == stored["id"]
    _answer(ps, approval, {"decision": "approve"})
    assert _drive(o, run_id)[-1] == "done"
    (frozen,) = _frozen(ps, 1)
    assert frozen["adopted_by"] == "human" and stage_row(ps, "question")["status"] == "passed"


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_significance_novelty_back_goes_to_topics(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[0],))], [_verdicts(lib, ids)])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    _answer(ps, _waiting(ps), {"decision": "back"})
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "topics" and edge_count(ps, "question->topics") == 1
    assert not _frozen(ps, 1)


def test_split_in_auto_is_treated_as_done(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[0],))], [_verdicts(lib, ids)])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "topics" and edge_count(ps, "question->topics") == 1
    assert rqs(ps, 1)[0]["already_done"]["result"] == "split"
    assert not [c for c in list_checkpoints(ps) if c["kind"] == "significance_novelty"]


@pytest.mark.parametrize("judges", [["codex_cli", "codex_cli"], ["claude_cli", "codex_cli"]],
                         ids=["duplicate_vendor", "author_vendor"])
def test_single_judge_vendor_never_clears_already_done(env, judges):
    k, ps, lib = env
    sources, ids = _sources(lib)
    nobody = _verdicts(lib, ids)  # every admitted judge says: not answered, with a verbatim quote
    ex = _executor([_rq()], [nobody], [nobody])
    o = _orch(k, ps, ex, sources, judges=[DriverSpec.parse(j) for j in judges])
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    (stored,) = rqs(ps, 1)
    result = stored["already_done"]
    assert result["result"] == "split"
    assert result["judged"] == ["openai"] and result["author_vendor"] == "anthropic"
    skipped = {"judge": "same_or_unknown_vendor"} if judges[0] == "claude_cli" else {"judge_2": "duplicate_vendor"}
    assert result["skipped"] == skipped
    assert len(_calls(ex, "judge") + _calls(ex, "judge_2")) == 1
    # P1: the split counts as already done; the RQ is neither frozen nor called "not found".
    assert current_stage(ps) == "topics" and stored["status"] == "draft"
    assert "not_found_in_scope" not in json.dumps([e["payload"] for e in ps.db.execute(
        "SELECT payload FROM events WHERE type LIKE 'rq.%'")])


def test_unverbatim_judge_quote_is_invalid(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids)], [_verdicts(lib, ids, quote="A trial that never said this.")])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    result = rqs(ps, 1)[0]["already_done"]
    assert result["result"] == "split"  # the deepseek judgements count as missing
    for w in result["works"]:
        assert w["judgements"]["deepseek"]["ok_quote"] is False
        assert w["judgements"]["openai"]["ok_quote"] is True


def _direct_ctx(k, ps, lib, ex, sources) -> StageContext:
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="question", attempt=1,
                        run_id=None, guidance="", executor=ex, sources=sources, actor="tester")


def test_both_answered_with_one_unverbatim_quote_is_split(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([], [_verdicts(lib, ids, (ids[0],))],
                   [_verdicts(lib, ids, (ids[0],), quote="Aspirin settles it for everyone.")])
    report = already_done(_direct_ctx(k, ps, lib, ex, sources), _stored_rq(ps), author_vendor="anthropic")
    first = next(w for w in report.works if w.item_id == ids[0])
    assert [j.answered for j in first.judgements.values()] == [True, True]
    assert report.result == "split" and report.answered_by_both() == []


def test_judge_quote_counts_only_from_what_the_judge_saw(env):
    k, ps, lib = env
    visible = "Aspirin in adults over 80 did not change the stroke rate."
    hidden = "A later subgroup answered the question."
    work = ItemIn(title="Aspirin over 80", year=2020, abstract=f"{visible} {'Background. ' * 200}{hidden}",
                  ids=Ids(doi="10.1000/long"))
    (iid,) = [lib.add_item(work, added_by="agent", actor="tester").item_id]
    sources = FakeSources({"aspirin": [work]}, lib=lib)
    ex = _executor([], [{"items": [{"item_id": iid, "answered": False, "quote": visible}]}],
                   [{"items": [{"item_id": iid, "answered": True, "quote": hidden}]}])
    report = already_done(_direct_ctx(k, ps, lib, ex, sources), _stored_rq(ps), author_vendor="anthropic")
    (w,) = report.works
    assert w.judgements["openai"].ok_quote is True  # from the abstract, within what the judge saw
    assert w.judgements["deepseek"].ok_quote is False  # in the abstract, but past the slice the judge saw
    assert report.result == "split"


@pytest.mark.parametrize(("answered", "expected"), [
    ((True, False, False), "split"),               # one valid "answered" is never outvoted
    ((False, False, False), "not_found_in_scope"),
    ((True, True, False), "already_done"),
    ((False, False), "not_found_in_scope"),
    ((False,), "split"),                            # one valid "not answered" is not enough
])
def test_three_judge_verdicts(answered, expected):
    vendors = ("openai", "deepseek", "anthropic")
    work = JudgedWork(item_id="src_a", title="A", judgements={
        v: Judgement(answered=a, quote="A", ok_quote=True) for v, a in zip(vendors, answered, strict=False)})
    assert verdict(AlreadyDone(works=[work], judged=list(vendors[:len(answered)]))) == expected


def test_unknown_author_vendor_is_split(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids)], [_verdicts(lib, ids)], judge_3=[_verdicts(lib, ids)])
    ex.drivers["ideator"] = "ollama:mystery-model"  # a driver whose vendor is unknown
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"
    result = rqs(ps, 1)[0]["already_done"]
    assert result["author_vendor"] is None and result["result"] == "split"
    assert result["split_reason"] == "the RQ author's vendor is unknown, so no judge can be shown to differ from it"
    assert current_stage(ps) == "topics"  # P1: a split counts as already done


def test_d2_auto_freezes_with_agent_adoption(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids)], [_verdicts(lib, ids)])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "done"
    assert not [c for c in list_checkpoints(ps) if c["kind"] == "rq_approval"]
    (frozen,) = _frozen(ps, 1)
    assert frozen["adopted_by"] == "agent" and frozen["already_done"]["result"] == "not_found_in_scope"
    (ev,) = ps.db.execute("SELECT actor_kind, payload FROM events WHERE type='rq.frozen'").fetchall()
    payload = json.loads(ev["payload"])
    assert payload == {"text_hash": frozen["text_hash"], "adopted_by": "agent"} and ev["actor_kind"] == "agent"
    (ran,) = [json.loads(r["payload"]) for r in ps.db.execute(
        "SELECT payload FROM events WHERE type='stage.ran' AND json_extract(payload, '$.stage')='question'")]
    assert ran["status"] == "done" and "not found within the searched scope" in ran["reason"]
    assert "novel" not in ran["reason"].lower()
    assert stage_row(ps, "question")["status"] == "passed" and run_row(ps, run_id)["status"] == "done"


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_d2_collab_not_found_asks_rq_approval(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq(), _rq(x="aspirin use in women over 80")], [_verdicts(lib, ids)] * 2,
                   [_verdicts(lib, ids)] * 2)
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    first = _waiting(ps)
    assert first["kind"] == "rq_approval"
    _answer(ps, first, {"decision": "revise", "comment": "Only women."})
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    assert [(r["stage"], r["feedback"]) for r in rounds(ps)] == [("question", "Only women.")]
    assert _calls(ex, "ideator")[1]["inputs"]["guidance"] == "Only women."
    assert edge_count(ps, "question->question") == 1

    # on_answer is idempotent: the revise records its round once.
    ctx = StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="question", attempt=1,
                       run_id=None, guidance="", executor=ex, sources=sources, actor="tester")
    assert QuestionWorker().on_answer(ctx, get_checkpoint(ps, first["id"])).status == "rerun"
    assert len(rounds(ps)) == 1

    second = _waiting(ps)
    _answer(ps, second, {"decision": "approve"})
    assert _drive(o, run_id)[-1] == "done"
    (frozen,) = _frozen(ps, 2)
    assert frozen["adopted_by"] == "human"
    ctx.attempt = 2  # approving again freezes nothing twice
    assert QuestionWorker().on_answer(ctx, get_checkpoint(ps, second["id"])).status == "done"
    assert ps.db.execute("SELECT COUNT(*) FROM events WHERE type='rq.frozen'").fetchone()[0] == 1


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_invalid_answer_reopens_the_checkpoint_saying_why(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids)], [_verdicts(lib, ids)])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "paused_waiting_human"
    first = _waiting(ps)
    _answer(ps, first, {"decision": "revise"})  # no comment
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    second = _waiting(ps)
    assert second["id"] != first["id"] and second["kind"] == "rq_approval"
    assert "could not be applied" in second["prompt"] and "comment" in second["prompt"]
    assert second["options"]["rq_id"] == first["options"]["rq_id"]
    assert second["options"]["replaces"] == first["id"]
    ctx = StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="question", attempt=1,
                       run_id=None, guidance="", executor=ex, sources=sources, actor="tester")
    again = QuestionWorker().on_answer(ctx, get_checkpoint(ps, first["id"]))
    assert (again.status, again.checkpoint_id) == ("waiting_human", second["id"])  # opened once
    _answer(ps, second, {"decision": "maybe"})
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    assert "'maybe'" in _waiting(ps)["prompt"]
    assert not _frozen(ps, 1)


# ---- P1 degrade ----
def test_p1_degrade_narrows_the_rq_once_then_fails(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    answering = _verdicts(lib, ids, (ids[0],))
    ex = _executor([_rq(), _rq(), _rq(), _rq(x="aspirin use in women over 90")], [answering] * 4, [answering] * 4)
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "failed"
    assert run_row(ps, run_id)["reason"].startswith("RQ could not be narrowed past prior work")
    assert edge_count(ps, "question->topics") == 2
    ideator = _calls(ex, "ideator")
    assert len(ideator) == 4  # 3 attempts, then one narrowing: never a loop
    narrow = ideator[-1]["inputs"]
    assert narrow["rq"]["three_step"]["x"] == "aspirin use in people over 80"
    assert [(w["item_id"], w["quotes"]) for w in narrow["answering_works"]] == \
        [(ids[0], {"openai": "Aspirin trial 0", "deepseek": "Aspirin trial 0"})]
    (deg,) = degradations(ps, "question")
    old, new = rqs(ps, 3)
    assert deg["attempt"] == 3 and deg["removed"] == [old["id"]]
    assert new["already_done"]["result"] == "already_done" and not _frozen(ps, 3)


def test_p1_degrade_freezes_a_narrowed_rq_that_clears(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    answering, nobody = _verdicts(lib, ids, (ids[0],)), _verdicts(lib, ids)
    ex = _executor([_rq(), _rq(), _rq(), _rq(x="aspirin use in women over 90")], [answering] * 3 + [nobody],
                   [answering] * 3 + [nobody])
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert _drive(o, run_id)[-1] == "done"
    (frozen,) = _frozen(ps, 3)
    assert frozen["adopted_by"] == "agent" and frozen["body"]["three_step"]["x"] == "aspirin use in women over 90"
    assert len(degradations(ps, "question")) == 1


# ---- D1 ----
def _d1_to_question(k, ps, ex, sources) -> Orchestrator:
    o = _orch(k, ps, ex, sources)
    for stage in ("intake", "landscape", "gaps", "topics"):
        o.run_stage("demo", stage, actor="tester")
        assert o.advance("demo", actor="tester").ok
    return o


@pytest.mark.parametrize("env", [{"discovery_mode": "d1", "production_mode": "auto"}], indirect=True)
def test_d1_always_asks_rq_approval(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    pico = {"population": "Adults over 80", "intervention": "Aspirin", "comparison": "No aspirin",
            "outcome": "First stroke"}
    ex = _executor([_rq(clinical=True, pico=pico)], [_verdicts(lib, ids)], [_verdicts(lib, ids)])
    o = _d1_to_question(k, ps, ex, sources)
    out = o.run_stage("demo", "question", actor="tester")
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "rq_approval" and cp["options"]["choices"] == ["approve", "revise"]
    prompt = cp["prompt"]
    for text in (SENTENCE, "Guidelines stop at age 80.", "Consequence 1.", "Registry data exist.",
                 "Population: Adults over 80", "Hypothesis 1: aspirin does not lower the stroke rate by 20%.",
                 "A hazard ratio above 2 would falsify hypothesis 1.", "not found within the searched scope",
                 "Aspirin trial 0", "openai", "deepseek"):
        assert text in prompt, text
    assert not _frozen(ps, 1)
    _answer(ps, cp, {"decision": "approve"})
    assert o.advance("demo", actor="tester").ok
    (frozen,) = _frozen(ps, 1)
    assert frozen["adopted_by"] == "human"


@pytest.mark.parametrize("env", [{"discovery_mode": "d1"}], indirect=True)
def test_d1_already_done_goes_back_to_topics(env):
    """D1 has a way back from an RQ that is already done: the stage sends the project back to topics (counted on
    question->topics), instead of failing with nothing but a rerun of the same topic left."""
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[1],))], [_verdicts(lib, ids, (ids[1],))])
    o = _d1_to_question(k, ps, ex, sources)
    out = o.run_stage("demo", "question", actor="tester")
    assert (out.status, out.to) == ("back_edge", "topics") and out.reason.startswith("already done: Aspirin trial 1")
    assert current_stage(ps) == "topics" and edge_count(ps, "question->topics") == 1


@pytest.mark.parametrize("env", [{"discovery_mode": "d1", "production_mode": "collab"}], indirect=True)
def test_d1_already_done_at_the_edge_limit_asks_in_p2(env):
    from rk.orchestrator.state import record_edge_taken

    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq()], [_verdicts(lib, ids, (ids[1],))], [_verdicts(lib, ids, (ids[1],))])
    o = _d1_to_question(k, ps, ex, sources)
    record_edge_taken(ps, "question->topics", 2, actor="tester")
    out = o.run_stage("demo", "question", actor="tester")
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "stage_blocked" and cp["options"]["to"] == "topics"
    assert current_stage(ps) == "question" and edge_count(ps, "question->topics") == 2


def test_already_done_needs_three_queries_two_sources(env):
    k, ps, lib = env
    sources, ids = _sources(lib)
    ex = _executor([_rq(), _rq()], [_verdicts(lib, ids)], [_verdicts(lib, ids)])
    ex.script[("scout", "LandscapeQueries")] = [{"queries": QUERIES[:2]}, {"queries": QUERIES}]
    o = _orch(k, ps, ex, sources)
    run_id = _to_question(o, ps)
    assert o.step("demo", run_id, actor="tester") == "running"  # 2 queries: rerun without searching
    assert edge_count(ps, "question->question") == 1
    assert "need 3-5" in stage_row(ps, "question")["guidance"]
    assert not searches(ps, "question", 1)
    assert _drive(o, run_id)[-1] == "done"
    logged = searches(ps, "question", 2)
    assert {s["source"] for s in logged} == {"openalex", "europepmc"} and {s["purpose"] for s in logged} == \
        {"already_done"}
    assert sorted({s["query"] for s in logged}) == sorted(QUERIES)
    result = _frozen(ps, 2)[0]["already_done"]
    assert result["sources_used"] == ["openalex", "europepmc"] and result["queries"] == QUERIES


def test_already_done_keeps_the_top_ten_by_rrf(env):
    k, ps, lib = env
    aspirin, falls = _works("Aspirin", 8), _works("Falls", 6)
    ids = [lib.add_item(w, added_by="agent", actor="tester").item_id for w in aspirin + falls]
    sources = FakeSources({"aspirin": aspirin, "falls": falls}, lib=lib, s2=True)
    rq_id = _stored_rq(ps)
    top = ids[:10]  # the aspirin works are in two queries' results, so they rank above the falls works
    ex = _executor([], [_verdicts(lib, top)], [_verdicts(lib, top)],
                   queries=["aspirin dosing", "falls in the elderly", "aspirin bleeding"])
    ctx = StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="question", attempt=1,
                       run_id=None, guidance="", executor=ex, sources=sources, actor="tester")
    report = already_done(ctx, rq_id, author_vendor="anthropic")
    assert isinstance(report, AlreadyDone) and report.sources_used == ["openalex", "s2"]
    assert [w.item_id for w in report.works] == top and report.found == 14
    assert report.result == "not_found_in_scope"
    judged = [c["inputs"]["works"] for c in ex.calls if c["role"].startswith("judge")]
    assert len(judged) == 2 and all([w["item_id"] for w in ws] == top for ws in judged)
    assert judged[0][0]["abstract"] == "Aspirin trial 0 in adults over 80."


# ---- gate ----
def _stored_rq(ps, *, attempt: int = 1, hypotheses: int = 2) -> str:
    tid = add_topic(ps, attempt, Topic(title="T", summary="S", gap_ids=["gap_x"], feasibility="F", data_needed="D",
                                       paper_type="critical_review", risks="R"), actor="tester")
    rq = _rq(hypotheses=hypotheses)
    return add_rq(ps, attempt, tid, RQBody(**rq["body"]), [HypothesisIn(**h) for h in rq["hypotheses"]],
                  actor="tester")


def test_question_gate(env):
    ps = env[1]
    gate = QuestionWorker().gate
    assert gate(ps, 1).failures == ["no frozen RQ"]
    rq_id = _stored_rq(ps)
    set_already_done(ps, rq_id, {"result": "split"}, actor="tester")
    freeze_rq(ps, rq_id, adopted_by="human", actor="tester")
    assert gate(ps, 1).failures == ["the already-done check found 'split' and nobody sent the RQ on"]
    one = _stored_rq(ps, attempt=2, hypotheses=1)
    set_already_done(ps, one, {"result": "not_found_in_scope"}, actor="tester")
    freeze_rq(ps, one, adopted_by="agent", actor="tester")
    assert gate(ps, 2).failures == ["1 hypotheses (need at least 2)"]
    ok = _stored_rq(ps, attempt=3)
    set_already_done(ps, ok, {"result": "not_found_in_scope"}, actor="tester")
    freeze_rq(ps, ok, adopted_by="agent", actor="tester")
    assert gate(ps, 3) == GateResult(True, [])


def test_question_is_a_default_worker():
    assert isinstance(default_workers()["question"], QuestionWorker)
