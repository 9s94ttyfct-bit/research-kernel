"""The Workbench's discovery routes (Plan 4 Task 11): the stage graph, run controls, topics and the research question,
and the answers the Checkpoints cards post, applied by the real workers through ``rk serve``'s RunManager."""

# ruff: noqa: F811 - fixtures imported from other test modules are re-bound as test parameters
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rk.checkpoints import create_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.already_done import AlreadyDone, JudgedWork, Judgement
from rk.discovery.models import (
    ClosestWork,
    Gap,
    GapEvidence,
    HypothesisIn,
    RQBody,
    Topic,
)
from rk.discovery.question import QuestionWorker
from rk.discovery.store import (
    add_gap,
    add_rq,
    add_topic,
    gaps,
    rounds,
    rq_row,
    set_already_done,
    set_topic_status,
    topics,
)
from rk.discovery.topics import TopicsWorker
from rk.library.models import Ids, ItemIn
from rk.notes import NoteIn, add_note
from rk.orchestrator.engine import GateResult, Orchestrator, StageOutcome
from rk.orchestrator.graph import DISCOVERY, stages_for
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import edge_rows, latest_run, run_row, stage_row
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.tools.builtin import build_registry
from rk.ui.auth import UiAuth

from .discovery_fakes import (
    FakeSources,
    FakeWorker,
    ScriptedExecutor,
    add_text_layer,
    back_to_question_workers,
    freeze_an_rq,
    make_project,
)
from .test_ui_api import ORIGIN, SERVE_TOKEN, kernel, login, ui  # noqa: F401 - fixtures

W = {"Origin": ORIGIN}
TIMEOUT = 5.0
QUOTE = "The cohort excluded patients older than 80 years."
TITLES = ("Aspirin in people over 80", "<b>Statin</b> withdrawal in frail older adults")
CLOSEST_TITLE = "Low-dose aspirin in older adults: a cohort"
RQ = {
    "three_step": {"x": "aspirin use in people over 80", "y": "whether it prevents a first stroke",
                   "z": "when to start it"},
    "problem": {"condition": "Guidelines stop at age 80.", "consequences": ["Clinicians guess.", "Trials exclude them."]},
    "finer": {"feasible": "Registry data exist.", "interesting": "Clinicians ask it.",
              "novel": "Not found within the searched scope.", "ethical": "Uses existing data.",
              "relevant": "Ageing populations."},
    "clinical": True,
    "pico": {"population": "Adults over 80", "intervention": "Low-dose aspirin", "comparison": "No aspirin",
             "outcome": "First stroke"},
}
HYPOTHESES = [{"statement": "Aspirin lowers the first-stroke rate by 20%.",
               "falsification": "A hazard ratio above 1.0 would falsify it."},
              {"statement": "Aspirin does not lower the first-stroke rate.",
               "falsification": "A hazard ratio below 0.8 would falsify it."}]


# ---- seeding (shared with the e2e tests) ------------------------------------------------------------------------
def seed_gap(ps, lib, attempt: int) -> str:
    """One verified stated-limitation gap of the gaps ``attempt``, with its quote note."""
    iid = lib.add_item(ItemIn(title="Aspirin cohort", year=2015, ids=Ids(doi="10.1000/cohort")), added_by="agent",
                       actor="tester").item_id
    add_text_layer(ps, iid, ["Introduction to the cohort.", f"Limitations. {QUOTE} More work is needed."])
    note = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=QUOTE, role_in_source="qualification"),
                    actor="tester")
    return add_gap(ps, attempt, Gap(kind="stated_limitation",
                                    statement="Aspirin in patients older than 80: not found within the searched scope.",
                                    evidence=GapEvidence(note_ids=[note.note_id], item_ids=[iid])),
                   verified=True, checks={"quote_note": "pass"}, actor="tester")


def seed_topics(ps, lib, attempt: int, gap_ids: list[str], titles=TITLES) -> list[str]:
    """Topics of the topics ``attempt``, each with one closest work that two judge vendors described."""
    iid = lib.add_item(ItemIn(title=CLOSEST_TITLE, year=2018, ids=Ids(doi="10.1000/closest")), added_by="agent",
                       actor="tester").item_id
    closest = [ClosestWork(item_id=iid, title=CLOSEST_TITLE, year=2018,
                           differences={"anthropic": "The cohort stops at 79; the topic starts at 80.",
                                        "openai": "It measures bleeding, not stroke."})]
    return [add_topic(ps, attempt, Topic(title=t, summary=f"{t}: what the evidence leaves open.", gap_ids=gap_ids,
                                         closest=closest, feasibility="Registry data exist.",
                                         data_needed="A national registry.", paper_type="critical_review",
                                         risks="Few trials in this group."), actor="tester",
                      novelty={"queries": ["aspirin over 80"], "sources_used": ["openalex"], "found": 1})
            for t in titles]


def open_topic_review(ps, attempt: int, topic_ids: list[str]) -> str:
    return create_checkpoint(ps, "topic_review", "Review the candidate topics.",
                             {"topic_ids": topic_ids, "attempt": attempt, "replaces": None}, stage="topics",
                             actor="tester")


def seed_rq(ps, lib, attempt: int, topic_id: str, result: str = "not_found_in_scope") -> str:
    """An RQ of the question ``attempt`` with its already-done result (one work, judged by two vendors)."""
    rq_id = add_rq(ps, attempt, topic_id, RQBody.model_validate(RQ), [HypothesisIn(**h) for h in HYPOTHESES],
                   actor="tester")
    iid = lib.add_item(ItemIn(title="Aspirin and stroke in the very old", year=2020, ids=Ids(doi="10.1000/old")),
                       added_by="agent", actor="tester").item_id
    work = JudgedWork(item_id=iid, title="Aspirin and stroke in the very old", year=2020, judgements={
        "anthropic": Judgement(answered=False, quote="stroke in the very old", ok_quote=True),
        "openai": Judgement(answered=result == "split", quote="Aspirin and stroke", ok_quote=True)})
    set_already_done(ps, rq_id, AlreadyDone(result=result, queries=["aspirin over 80 stroke"],
                                            sources_used=["openalex", "europepmc"], works=[work], found=1,
                                            author_vendor="google", judged=["anthropic", "openai"],
                                            ).model_dump(mode="json"), actor="tester")
    return rq_id


def open_rq_checkpoint(ps, kind: str, rq_id: str, attempt: int) -> str:
    choices = ["approve", "revise"] if kind == "rq_approval" else ["proceed", "back"]
    return create_checkpoint(ps, kind, "Look at the research question.",
                             {"rq_id": rq_id, "attempt": attempt, "choices": choices, "replaces": None},
                             stage="question", actor="tester")


class SeededTopics(TopicsWorker):
    """The real topics worker (answers, gate) whose run writes two topics instead of asking the Ideator."""

    def __init__(self, lib):
        super().__init__()
        self.lib = lib

    def run(self, ctx):
        gap_ids = [g["id"] for g in gaps(ctx.ps, stage_row(ctx.ps, "gaps")["passed_attempt"], verified=True)]
        ids = seed_topics(ctx.ps, self.lib, ctx.attempt, gap_ids)
        return StageOutcome("waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, ids))


class SeededQuestion(QuestionWorker):
    """The real question worker (answers, gate) whose run writes the RQ and its already-done result."""

    def __init__(self, lib, result: str = "not_found_in_scope"):
        super().__init__()
        self.lib, self.result = lib, result

    def run(self, ctx):
        (topic,) = [t for t in topics(ctx.ps, stage_row(ctx.ps, "topics")["passed_attempt"])
                    if t["status"] == "approved"]
        rq_id = seed_rq(ctx.ps, self.lib, ctx.attempt, topic["id"], self.result)
        kind = "rq_approval" if self.result == "not_found_in_scope" else "significance_novelty"
        return StageOutcome("waiting_human", checkpoint_id=open_rq_checkpoint(ctx.ps, kind, rq_id, ctx.attempt))


def gaps_writer(lib):
    def run(ctx):
        seed_gap(ctx.ps, lib, ctx.attempt)
        return StageOutcome("done")
    return run


def approved_topic_writer(lib):
    def run(ctx):
        (tid, _) = seed_topics(ctx.ps, lib, ctx.attempt, ["gap_x"])
        set_topic_status(ctx.ps, tid, "approved", actor="tester", actor_kind="human")
        return StageOutcome("done")
    return run


def discovery_workers(lib, *, question_result: str = "not_found_in_scope") -> dict:
    """Fake landscape, gaps that write a verified gap, and the real topics and question workers (seeded runs)."""
    return {"landscape": FakeWorker(), "gaps": FakeWorker(runs=[gaps_writer(lib)] * 5), "topics": SeededTopics(lib),
            "question": SeededQuestion(lib, question_result)}


def until(pred, timeout: float = TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


# ---- fixtures -----------------------------------------------------------------------------------------------------
@pytest.fixture
def wb(tmp_home, replay_http):
    """The Workbench (a logged-in TestClient) of ``rk serve`` with an Orchestrator and RunManager, over the d2/collab
    project ``demo`` (2 topics). Every stage after intake has a FakeWorker; tests replace ``wb.orch.workers[...]``
    before they start a run."""
    k, ps, lib = make_project(tmp_home, replay_http, production_mode="collab", topics_n=2)
    holder: dict = {}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    orch = Orchestrator(k, reg, workers={s: FakeWorker() for s in DISCOVERY if s != "intake"},
                        executor_factory=lambda p, r, s: ScriptedExecutor({}), sources_factory=lambda p: FakeSources({}))
    m = holder["m"] = RunManager(k, reg, orch, poll_s=0.02)
    auth = UiAuth(ORIGIN)
    app = create_app(reg, SERVE_TOKEN, tokens=k.tokens, chats=ChatManager(k, reg), ui=auth, kernel=k, runs=m)
    client = TestClient(app)
    client.auth_ui = auth
    login(client)
    yield SimpleNamespace(client=client, kernel=k, ps=ps, lib=lib, orch=orch, runs=m)
    m.close()
    k.close()


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers=W)


def start(wb, until_stage: str = "question") -> str:
    r = post(wb.client, "/ui/api/run", {"project": "demo", "until": until_stage})
    assert r.status_code == 200, r.text
    return r.json()["result"]["run_id"]


def waiting(wb, run_id: str, kind: str) -> dict:
    """Wait until the run waits on an open ``kind`` checkpoint; returns it."""
    until(lambda: run_row(wb.ps, run_id)["status"] == "paused_waiting_human"
          and any(c["kind"] == kind for c in list_checkpoints(wb.ps, "open")))
    (cp,) = [c for c in list_checkpoints(wb.ps, "open") if c["kind"] == kind]
    return cp


def answer(wb, cp: dict, body: dict) -> None:
    r = post(wb.client, f"/ui/api/checkpoints/{cp['id']}/answer", {"project": "demo", "answer": body})
    assert r.status_code == 200, r.text
    assert get_checkpoint(wb.ps, cp["id"])["answered_by"] == "human"


# ---- the stage graph ----------------------------------------------------------------------------------------------
def test_stages_route_marks_plan5_stages(wb):
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert [s["stage"] for s in g["graph"]] == stages_for("critical_review")
    # protocol, search_screen, read_extract, synthesize, argue, draft, revise and audit have workers
    assert [s["status"] for s in g["graph"]] == ["pending"] * 13 + ["plan5"]
    assert (g["paper_type"], g["discovery_mode"], g["run"], g["runs_available"]) == ("critical_review", "d2", None,
                                                                                      True)
    assert {(e["edge"], e["count"], e["limit"]) for e in g["back_edges"]} == {
        ("gaps->landscape", 0, 2), ("topics->gaps", 0, 2), ("question->topics", 0, 2), ("protocol->question", 0, 2),
        ("search_screen->protocol", 0, 2), ("read_extract->search_screen", 0, 2), ("synthesize->read_extract", 0, 2),
        ("argue->synthesize", 0, 2), ("argue->question", 0, 2), ("draft->argue", 0, 2), ("revise->draft", 0, 2),
        ("audit->draft", 0, 2), ("audit->argue", 0, 2), ("audit->read_extract", 0, 2), ("audit->revise", 0, 2)}

    wb.orch.workers["gaps"] = FakeWorker(runs=[StageOutcome("rerun", reason="too few gaps")])
    wb.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
    run_id = start(wb)
    waiting(wb, run_id, "topic_review")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    by = {s["stage"]: s for s in g["graph"]}
    assert [s["status"] for s in g["graph"][:5]] == ["passed", "passed", "passed", "active", "pending"]
    assert (by["topics"]["state"], by["topics"]["attempt"]) == ("waiting", 1)
    assert [(e["edge"], e["count"]) for e in by["gaps"]["back_edges"]] == [("gaps->gaps", 1)]
    assert by["export"] == {"stage": "export", "status": "plan5"}
    assert (g["run"]["id"], g["run"]["status"], g["run_active"]) == (run_id, "paused_waiting_human", True)
    assert g["current"] == "topics"


def test_stages_route_shows_the_last_gate_failures(wb):
    wb.orch.workers["landscape"] = FakeWorker(
        gates=[GateResult(False, ["2 clusters (need 3)"])],
        runs=[StageOutcome("done"), lambda ctx: StageOutcome("waiting_human", checkpoint_id=create_checkpoint(
            ctx.ps, "uncertain_support", "Wait.", actor="t"))])
    run_id = start(wb)
    until(lambda: run_row(wb.ps, run_id)["status"] == "paused_waiting_human")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    (landscape,) = [s for s in g["graph"] if s["stage"] == "landscape"]
    assert landscape["last_gate"] == {"attempt": 1, "ok": False, "failures": ["2 clusters (need 3)"]}
    assert g["last_gate"] == {"stage": "landscape", "attempt": 1, "ok": False, "failures": ["2 clusters (need 3)"]}


def test_stages_route_without_the_orchestrator(ui, kernel):
    login(ui)
    post(ui, "/ui/api/projects", {"slug": "demo", "title": "Demo", "paper_type": "scoping_review"})
    g = ui.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert (g["runs_available"], g["discovery_mode"], g["run"]) == (False, None, None)
    assert [s["status"] for s in g["graph"]] == ["pending"] * 5 + ["plan5"] * 9
    r = post(ui, "/ui/api/run", {"project": "demo", "until": "topics"})
    assert r.status_code == 503


def test_stages_route_marks_the_stages_with_a_worker_and_offers_their_runs(wb):
    later = ("protocol", "search_screen", "read_extract", "synthesize", "argue", "draft", "revise", "audit")
    for s in later:
        wb.orch.workers[s] = FakeWorker()
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert [s["status"] for s in g["graph"]] == ["pending"] * 13 + ["plan5"]
    assert {e["edge"] for e in g["back_edges"]} == {"gaps->landscape", "topics->gaps", "question->topics",
                                                   "protocol->question", "search_screen->protocol",
                                                   "read_extract->search_screen", "synthesize->read_extract",
                                                   "argue->synthesize", "argue->question", "draft->argue",
                                                   "revise->draft", "audit->draft", "audit->argue",
                                                   "audit->read_extract", "audit->revise"}
    assert (g["until_options"], g["discovery_passed"]) == (["topics", "question", *later], False)
    run_id = start(wb, "question")
    until(lambda: run_row(wb.ps, run_id)["status"] == "done")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert (g["current"], g["discovery_passed"], g["until_options"]) == ("protocol", True, [])  # no frozen RQ yet
    r = post(wb.client, "/ui/api/run", {"project": "demo", "until": "read_extract"})
    assert r.status_code == 400 and r.json()["detail"]["type"] == "rq_not_frozen"
    freeze_an_rq(wb.ps)
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert g["until_options"] == list(later)
    run_id = start(wb, "read_extract")
    until(lambda: run_row(wb.ps, run_id)["status"] == "done")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert [s["status"] for s in g["graph"]] == ["passed"] * 8 + ["pending"] * 5 + ["plan5"]
    assert (g["current"], g["until_options"]) == ("synthesize", ["synthesize", "argue", "draft", "revise", "audit"])


def test_stages_route_keeps_the_run_controls_of_a_d1_project_sent_back_to_question(wb):
    """In a D1 project's production run, protocol->question reopens question: the stage graph still reports the
    active run (the page shows its status and controls, not the Lead hint), and the orchestrator keeps the project."""
    from rk.discovery.models import Intake
    from rk.discovery.store import record_intake

    record_intake(wb.ps, Intake(audience="nurses", paper_type="critical_review", discovery_mode="d1",
                                production_mode="collab"), actor="tester", actor_kind="human")
    wb.orch.workers.update(back_to_question_workers())
    for stage in DISCOVERY:  # guided discovery
        assert wb.orch.run_stage("demo", stage, actor="tester").status == "done"
        assert wb.orch.advance("demo", actor="tester").ok
    freeze_an_rq(wb.ps)
    run_id = start(wb, "protocol")
    until(lambda: run_row(wb.ps, run_id)["status"] == "paused_waiting_human")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert (g["discovery_mode"], g["current"], g["discovery_passed"], g["orchestrator_drives"]) == (
        "d1", "question", False, True)
    assert (g["run"]["id"], g["run_active"]) == (run_id, True)
    assert post(wb.client, "/ui/api/run/cancel", {"project": "demo"}).status_code == 200
    until(lambda: run_row(wb.ps, run_id)["status"] == "cancelled")
    g = wb.client.get("/ui/api/stages", params={"project": "demo"}).json()["result"]
    assert g["until_options"] == ["question", "protocol", "search_screen", "read_extract", "synthesize",
                                  "argue", "draft", "revise", "audit"]  # a run goes on



# ---- run controls -------------------------------------------------------------------------------------------------
def test_run_routes_start_pause_resume_cancel(wb):
    wb.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
    run_id = start(wb, "topics")
    waiting(wb, run_id, "topic_review")
    assert post(wb.client, "/ui/api/run/pause", {"project": "demo"}).status_code == 200
    until(lambda: run_row(wb.ps, run_id)["status"] == "paused")
    r = post(wb.client, "/ui/api/run/resume", {"project": "demo"})
    assert r.status_code == 200 and r.json()["result"]["run"]["id"] == run_id
    until(lambda: run_row(wb.ps, run_id)["status"] == "paused_waiting_human")
    assert run_row(wb.ps, run_id)["session"] == 2
    assert post(wb.client, "/ui/api/run/cancel", {"project": "demo"}).status_code == 200
    until(lambda: run_row(wb.ps, run_id)["status"] == "cancelled")
    assert post(wb.client, "/ui/api/run/stop", {"project": "demo"}).status_code in (404, 422)
    r = post(wb.client, "/ui/api/run", {"project": "demo", "until": "gaps"})  # the run manager checks until
    assert r.status_code == 400 and r.json()["detail"]["type"] == "stage_unavailable"
    assert post(wb.client, "/ui/api/run/pause", {"project": "demo"}).json()["detail"]["type"] == "no_run"
    r = post(wb.client, "/ui/api/run/resume", {"project": "demo"}).json()["result"]  # a cancelled run: a new run
    new = r["run"]["id"]
    assert new != run_id and r["replaced"] == run_id and f"started a new run {new} (until topics)" in r["message"]
    waiting(wb, new, "topic_review")
    assert post(wb.client, "/ui/api/run/cancel", {"project": "demo"}).status_code == 200
    until(lambda: run_row(wb.ps, new)["status"] == "cancelled")
    events = wb.ps.db.execute("SELECT actor FROM events WHERE type IN ('run.started') ").fetchall()
    assert [e["actor"] for e in events] == ["ui:user", "ui:user"]


def _run_events(ps) -> list[tuple]:
    return [tuple(r) for r in ps.db.execute("SELECT seq, type FROM events WHERE type LIKE 'run.%' ORDER BY seq")]


@pytest.mark.parametrize("path, body", [("/ui/api/run", {"project": "demo", "until": "topics"}),
                                        ("/ui/api/run/pause", {"project": "demo"}),
                                        ("/ui/api/run/resume", {"project": "demo"}),
                                        ("/ui/api/run/cancel", {"project": "demo"})])
def test_run_routes_refuse_foreign_or_missing_origin_and_no_cookie(wb, path, body):
    """Each request would be applied if it got through: start meets no run, pause and cancel a waiting run, resume a
    paused one."""
    run_id, status = None, None
    if path != "/ui/api/run":
        wb.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
            "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
        run_id = start(wb, "topics")
        waiting(wb, run_id, "topic_review")
        status = "paused_waiting_human"
        if path.endswith("/resume"):
            assert post(wb.client, "/ui/api/run/pause", {"project": "demo"}).status_code == 200
            status = until(lambda: run_row(wb.ps, run_id)["status"] == "paused" and "paused")
    before = _run_events(wb.ps)
    assert wb.client.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert wb.client.post(path, json=body).status_code == 403  # no Origin header
    wb.client.cookies.clear()
    assert wb.client.post(path, json=body, headers=W).status_code == 401
    time.sleep(0.2)  # a request that got through would be applied by the run's thread within a poll
    assert _run_events(wb.ps) == before
    if run_id is None:
        assert latest_run(wb.ps) is None
    else:
        assert run_row(wb.ps, run_id)["status"] == status


def test_run_start_refused_in_d1(wb):
    from rk.discovery.models import Intake
    from rk.discovery.store import record_intake

    record_intake(wb.ps, Intake(audience="nurses", paper_type="critical_review", discovery_mode="d1",
                                production_mode="collab"), actor="tester", actor_kind="human")
    r = post(wb.client, "/ui/api/run", {"project": "demo", "until": "topics"})
    assert r.status_code == 400 and r.json()["detail"]["type"] == "not_d2"
    assert latest_run(wb.ps) is None


def test_ws_sends_run_status_changes(wb):
    wb.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
    with wb.client.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        assert ws.receive_json()["type"] == "hello"
        first = next(m for m in iter(ws.receive_json, None) if m["type"] == "run")
        assert first["status"]["run"] is None
        run_id = start(wb, "topics")
        deadline, seen = time.monotonic() + 10, []
        while time.monotonic() < deadline and "paused_waiting_human" not in seen:
            m = ws.receive_json()
            if m["type"] == "run" and m["status"]["run"]:
                assert m["status"]["run"]["id"] == run_id
                seen.append(m["status"]["run"]["status"])
    assert seen[-1] == "paused_waiting_human"


# ---- topics and the research question -----------------------------------------------------------------------------
def test_topics_route_has_gap_quotes_and_closest_works(wb):
    gid = seed_gap(wb.ps, wb.lib, 1)
    ids = seed_topics(wb.ps, wb.lib, 1, [gid])
    got = wb.client.get("/ui/api/topics", params={"project": "demo", "attempt": 1}).json()["result"]
    assert [t["id"] for t in got["topics"]] == ids and got["attempt"] == 1
    t = got["topics"][1]
    assert t["title"] == TITLES[1]  # as stored; the page inserts it as text
    (g,) = t["gaps"]
    assert (g["id"], g["kind"], g["verified"]) == (gid, "stated_limitation", True)
    assert g["statement"] == "Aspirin in patients older than 80: not found within the searched scope."
    assert [(q["quote"], q["item_title"]) for q in g["quotes"]] == [(QUOTE, "Aspirin cohort")]
    (w,) = t["closest"]
    assert (w["title"], w["year"], sorted(w["differences"])) == (CLOSEST_TITLE, 2018, ["anthropic", "openai"])
    assert t["reference"] == {"queries": ["aspirin over 80"], "sources_used": ["openalex"], "found": 1}
    # no attempt: the attempt the topics stage shows (none yet)
    assert wb.client.get("/ui/api/topics", params={"project": "demo"}).json()["result"] == {
        "project": "demo", "attempt": None, "topics": []}


def test_rq_route_has_the_sentence_hypotheses_and_already_done(wb):
    gid = seed_gap(wb.ps, wb.lib, 1)
    (tid, _) = seed_topics(wb.ps, wb.lib, 1, [gid])
    rq_id = seed_rq(wb.ps, wb.lib, 1, tid)
    got = wb.client.get("/ui/api/rq", params={"project": "demo", "rq_id": rq_id}).json()["result"]
    assert got["rq"]["id"] == rq_id and got["topic"] == {"id": tid, "title": TITLES[0]}
    assert got["sentence"] == ("I am studying aspirin use in people over 80 because I want to find out whether it "
                               "prevents a first stroke in order to help my audience understand when to start it.")
    assert [h["statement"] for h in got["hypotheses"]] == [h["statement"] for h in HYPOTHESES]
    assert got["rq"]["already_done"]["result"] == "not_found_in_scope"
    assert got["rq"]["already_done"]["works"][0]["judgements"]["anthropic"]["quote"] == "stroke in the very old"
    assert wb.client.get("/ui/api/rq", params={"project": "demo", "rq_id": "rq_nope"}).status_code == 404
    assert wb.client.get("/ui/api/rq", params={"project": "demo"}).json()["result"]["rq"] is None


# ---- the answers the cards post, applied by the workers -----------------------------------------------------------
def test_topic_review_answer_through_ui_resumes_run(wb):
    wb.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, ["tpc_a", "tpc_b"]))])
    run_id = start(wb)
    cp = waiting(wb, run_id, "topic_review")
    answer(wb, cp, {"decision": "approve", "topic_id": "tpc_b"})
    call = ("on_answer", 1, cp["id"], {"decision": "approve", "topic_id": "tpc_b"})
    until(lambda: call in wb.orch.workers["topics"].calls)  # the run is set running before on_answer is called


def test_topic_review_card_approve_reaches_the_worker(wb):
    wb.orch.workers.update(discovery_workers(wb.lib))
    run_id = start(wb, "topics")
    cp = waiting(wb, run_id, "topic_review")
    first, second = cp["options"]["topic_ids"]
    answer(wb, cp, {"decision": "approve", "topic_id": second})
    until(lambda: run_row(wb.ps, run_id)["status"] == "done")
    assert {t["id"]: t["status"] for t in topics(wb.ps, 1)} == {first: "candidate", second: "approved"}
    assert stage_row(wb.ps, "topics")["status"] == "passed"


def test_topic_review_card_revise_and_reject_reach_the_worker(wb):
    wb.orch.workers.update(discovery_workers(wb.lib))
    run_id = start(wb, "topics")
    cp = waiting(wb, run_id, "topic_review")
    answer(wb, cp, {"decision": "revise", "target": "gaps", "comment": "Look at trials in care homes."})
    cp2 = until(lambda: next((c for c in list_checkpoints(wb.ps, "open") if c["kind"] == "topic_review"), None))
    assert [(r["stage"], r["feedback"]) for r in rounds(wb.ps)] == [("topics", "Look at trials in care homes.")]
    assert edge_rows(wb.ps)["topics->gaps"]["count"] == 1
    assert stage_row(wb.ps, "gaps")["attempt"] == 2
    answer(wb, cp2, {"decision": "reject"})
    until(lambda: any(c["kind"] == "topic_review" and c["id"] != cp2["id"] for c in list_checkpoints(wb.ps, "open")))
    assert {t["status"] for t in topics(wb.ps, cp2["options"]["attempt"])} == {"rejected"}
    assert edge_rows(wb.ps)["topics->topics"]["count"] == 1


def _to_question(wb, result: str = "not_found_in_scope"):
    wb.orch.workers.update(discovery_workers(wb.lib, question_result=result))
    wb.orch.workers["topics"] = FakeWorker(runs=[approved_topic_writer(wb.lib)] * 3)
    return start(wb, "question")


def test_rq_approval_card_approve_reaches_the_worker(wb):
    run_id = _to_question(wb)
    cp = waiting(wb, run_id, "rq_approval")
    answer(wb, cp, {"decision": "approve"})
    until(lambda: run_row(wb.ps, run_id)["status"] == "done")
    rq = rq_row(wb.ps, cp["options"]["rq_id"])
    assert (rq["status"], rq["adopted_by"]) == ("frozen", "human")


def test_rq_approval_card_revise_reaches_the_worker(wb):
    run_id = _to_question(wb)
    cp = waiting(wb, run_id, "rq_approval")
    answer(wb, cp, {"decision": "revise", "comment": "Narrow it to secondary prevention."})
    until(lambda: stage_row(wb.ps, "question")["attempt"] == 2)
    assert [(r["stage"], r["feedback"]) for r in rounds(wb.ps)] == [("question",
                                                                    "Narrow it to secondary prevention.")]
    assert rq_row(wb.ps, cp["options"]["rq_id"])["status"] == "draft"


def test_significance_novelty_card_answers_reach_the_worker(wb):
    run_id = _to_question(wb, "split")
    cp = waiting(wb, run_id, "significance_novelty")
    answer(wb, cp, {"decision": "proceed"})
    cp2 = waiting(wb, run_id, "rq_approval")
    assert cp2["options"]["rq_id"] == cp["options"]["rq_id"]
    answer(wb, cp2, {"decision": "approve"})
    until(lambda: run_row(wb.ps, run_id)["status"] == "done")


def test_significance_novelty_back_goes_to_topics(wb):
    run_id = _to_question(wb, "split")
    cp = waiting(wb, run_id, "significance_novelty")
    answer(wb, cp, {"decision": "back"})
    until(lambda: edge_rows(wb.ps).get("question->topics", {}).get("count") == 1)


@pytest.mark.parametrize("decision", ["allow_one_more", "stop"])
def test_stage_blocked_card_answers_reach_the_engine(wb, decision):
    rerun = StageOutcome("rerun", reason="2 clusters (need 3)")
    wb.orch.workers["landscape"] = FakeWorker(runs=[rerun, rerun, rerun])
    run_id = start(wb, "topics")
    cp = waiting(wb, run_id, "stage_blocked")
    assert cp["options"]["edge"] == "landscape->landscape"
    answer(wb, cp, {"decision": decision})
    if decision == "allow_one_more":
        until(lambda: run_row(wb.ps, run_id)["status"] == "done")
        assert edge_rows(wb.ps)["landscape->landscape"] == {"count": 3, "allowed": 1}
    else:
        until(lambda: run_row(wb.ps, run_id)["status"] == "failed")
        assert "stopped at landscape->landscape" in run_row(wb.ps, run_id)["reason"]
