"""The Workbench in a real (headless) Chromium against a real ``rk serve`` app: navigation, checkpoint answering,
library search, chat, and the discovery pages (stage graph, run controls, topic, RQ and blocked-stage cards)."""

# ruff: noqa: F811 - fixtures imported from other test modules are re-bound as test parameters
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from rk.checkpoints import create_checkpoint, get_checkpoint, list_checkpoints
from rk.config import Settings, save_settings
from rk.discovery.models import Intake
from rk.discovery.store import record_intake, rq_row, topics
from rk.fulltext import attach_pdf_bytes
from rk.library.models import Creator, Ids, ItemIn
from rk.orchestrator.engine import Orchestrator, StageOutcome
from rk.orchestrator.graph import DISCOVERY, stages_for
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import latest_run, record_run_status
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.server.runtime import acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.ui.auth import UiAuth
from rk.workspace import Kernel

from .browser_sites import tiny_pdf
from .discovery_fakes import (
    DEFAULT_INTAKE,
    FakeSources,
    FakeWorker,
    ScriptedExecutor,
    back_to_question_workers,
    freeze_an_rq,
)
from .test_sessions import fake  # noqa: F401 - fixture
from .test_ui_discovery import (
    HYPOTHESES,
    QUOTE,
    TITLES,
    discovery_workers,
    open_rq_checkpoint,
    open_topic_review,
    seed_gap,
    seed_rq,
    seed_topics,
)

pytestmark = pytest.mark.browser
PAGES = {"chat": "Chat", "library": "Library", "checkpoints": "Checkpoints", "progress": "Progress",
         "drafts": "Drafts", "projects": "Projects", "settings": "Settings"}


@pytest.fixture
def workbench_env(tmp_home, replay_http, fake):
    """The served Workbench with ``rk serve``'s Orchestrator and RunManager (every stage after intake a FakeWorker;
    tests replace ``orch.workers[...]`` before they start a run)."""
    exe, _ = fake  # the chat page opens a chat on load, so every CLI driver must already be the fake
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", cli_paths={"claude": exe, "codex": exe}))
    rt = acquire_serve_lock(tmp_home)
    kernel = Kernel(tmp_home, http=replay_http)
    kernel.create_project("demo", "Demo project", actor="t")
    holder: dict = {}
    registry = build_registry(kernel, runs=lambda: holder.get("m"))
    orch = Orchestrator(kernel, registry, workers={s: FakeWorker() for s in DISCOVERY if s != "intake"},
                        executor_factory=lambda p, r, s: ScriptedExecutor({}),
                        sources_factory=lambda p: FakeSources({}))
    runs = holder["m"] = RunManager(kernel, registry, orch, poll_s=0.05)
    origin = f"http://127.0.0.1:{rt.port}"
    ui = UiAuth(origin)
    chats = ChatManager(kernel, registry)
    app = create_app(registry, rt.token, tokens=kernel.tokens, chats=chats, ui=ui, kernel=kernel, runs=runs)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
    t.start()
    time.sleep(0.5)
    yield SimpleNamespace(kernel=kernel, ui=ui, chats=chats, orch=orch, runs=runs)
    server.should_exit = True
    t.join(5)
    runs.close()
    kernel.close()
    release(rt)


@pytest.fixture
def workbench(workbench_env):
    return workbench_env.kernel, workbench_env.ui, workbench_env.chats


@pytest.fixture
def page(workbench):
    from playwright.sync_api import sync_playwright

    _, ui, _ = workbench
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Playwright Chromium is not available: {e}")
        pg = browser.new_page()
        errors: list[str] = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(ui.login_url())
        pg.wait_for_selector("main h1")
        pg.errors = errors
        yield pg
        browser.close()


def test_login_and_navigate_all_pages(page):
    for route, heading in PAGES.items():
        page.goto(page.url.split("#")[0] + f"#/{route}")
        page.wait_for_function("h => document.querySelector('main h1')?.textContent === h", arg=heading)
        assert page.locator(f'.rail a[data-page="{route}"]').get_attribute("aria-current") == "page"
    assert page.errors == []


def test_slow_page_does_not_render_over_a_newer_one(page, monkeypatch):
    """A page still awaiting its API when the user navigates on must not draw itself over the newer page."""
    import rk.ui.api as ui_api

    gate, real_rows = threading.Event(), ui_api._event_rows

    def rows(ps, after, limit=500):
        if limit == 200:  # only the Progress page's request
            gate.wait(10)
        return real_rows(ps, after, limit)

    monkeypatch.setattr(ui_api, "_event_rows", rows)
    base = page.url.split("#")[0]
    page.goto(base + "#/progress")
    page.goto(base + "#/library")
    page.wait_for_function("document.querySelector('main h1')?.textContent === 'Library'")
    with page.expect_response("**/ui/api/progress*"):
        gate.set()
    page.wait_for_timeout(300)  # let the stale render's continuation run
    assert page.locator("main h1").text_content() == "Library"
    assert page.locator('.rail a[data-page="library"]').get_attribute("aria-current") == "page"
    assert page.errors == []



def test_answer_checkpoint_from_ui(page, workbench):
    kernel, _, _ = workbench
    ps, _ = kernel.project("demo")
    chk = create_checkpoint(ps, "captcha_wait", "Google Scholar shows a CAPTCHA.", ["done"], actor="kernel:browser")
    page.goto(page.url.split("#")[0] + "#/checkpoints")
    page.get_by_text("Google Scholar shows a CAPTCHA.").wait_for()
    assert page.locator("#open-count").text_content() == "1"
    page.get_by_role("button", name="Done").click()
    page.get_by_text("Nothing is waiting for you.").wait_for()
    cp = get_checkpoint(ps, chk)
    assert (cp["status"], cp["answered_by"], cp["answer"]) == ("answered", "human", {"choice": "done"})


def test_library_search_and_badges(page, workbench):
    kernel, _, _ = workbench
    ps, lib = kernel.project("demo")
    iid = lib.add_item(ItemIn(title="Encapsulation of fixture cells", year=2024, creators=[Creator(family="Author")],
                              ids=Ids(doi="10.5555/ok")), added_by="human", actor="t").item_id
    attach_pdf_bytes(ps, lib, iid, tiny_pdf("Encapsulation slowed degradation."), origin="user_upload", actor="t",
                     actor_kind="human")
    page.goto(page.url.split("#")[0] + "#/library?q=degradation")
    page.get_by_text("Encapsulation of fixture cells").click()
    page.locator(".detail h2", has_text="Encapsulation of fixture cells").wait_for()
    assert page.locator(".detail .badge", has_text="Full text").count() == 1
    href = page.get_by_role("link", name="PDF").get_attribute("href")
    r = page.request.get(page.url.split("/ui/")[0] + href)  # same cookies as the Workbench tab
    assert r.status == 200 and r.headers["content-type"] == "application/pdf"
    assert page.errors == []


def test_chat_roundtrip_fake_driver(page, monkeypatch):
    monkeypatch.setenv("FAKE_KIND", "claude")
    page.goto(page.url.split("#")[0] + "#/chat")
    page.get_by_text("Talking to claude_cli.").wait_for()
    page.get_by_label("Message").fill("hello")
    page.get_by_role("button", name="Send").click()
    page.locator(".msg:not(.you)").first.wait_for()
    assert page.locator("details.tool").count() >= 1
    assert page.errors == []


def test_chat_ignores_a_superseded_open(page, workbench, monkeypatch):
    """Re-rendering the chat page opens a new chat; the earlier render's ``chat.opened`` arriving late must not bind
    the new page to that chat (whose replies the page would then drop once its own ``chat.opened`` arrives)."""
    monkeypatch.setenv("FAKE_KIND", "claude")
    real_open = ChatManager.open

    def open_slowly(self, *a):
        time.sleep(0.5)
        return real_open(self, *a)

    monkeypatch.setattr(ChatManager, "open", open_slowly)
    base = page.url.split("#")[0]
    page.goto(base + "#/chat")
    page.goto(base + "#/chat?again")  # a second render while the first one's chat is still opening
    page.get_by_text("Talking to claude_cli.").wait_for()
    page.get_by_label("Message").fill("hello")
    page.get_by_role("button", name="Send").click()
    page.locator(".msg:not(.you)").first.wait_for(timeout=10_000)
    assert page.errors == []
    _, _, chats = workbench
    until(lambda: len(chats._sessions) == 1)  # the superseded renders' chats, opened after they were left, are closed


def test_leaving_the_chat_page_closes_its_chat(page, workbench):
    _, _, chats = workbench
    base = page.url.split("#")[0]
    page.get_by_text("Talking to claude_cli.").wait_for()  # login lands on Chat
    until(lambda: len(chats._sessions) == 1)
    page.goto(base + "#/library")
    page.wait_for_function("document.querySelector('main h1')?.textContent === 'Library'")
    until(lambda: not chats._sessions)
    page.goto(base + "#/chat")
    page.get_by_text("Talking to claude_cli.").wait_for()
    (second,) = until(lambda: list(chats._sessions))
    page.goto(base + "#/chat?again")  # supersedes that render's chat
    until(lambda: list(chats._sessions) not in ([], [second]) and second not in chats._sessions)
    assert len(chats._sessions) == 1
    assert page.errors == []


def until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(0.05)
    raise AssertionError("condition not reached")


# ---- discovery: the stage graph, run controls and the review cards ------------------------------------------------
def _intake(ps, **overrides) -> None:
    record_intake(ps, Intake(**{**DEFAULT_INTAKE, "production_mode": "collab", "topics_n": 2, **overrides}),
                  actor="t", actor_kind="human")


def _goto(page, route: str) -> None:
    page.goto(page.url.split("#")[0] + f"#/{route}")


def _answer_of(page, ps, cid: str) -> dict:
    """The answer the page posted, once the page has redrawn after it (so the next checkpoint meets a settled page)."""
    answer = until(lambda: get_checkpoint(ps, cid)["answer"])
    page.get_by_text("Nothing is waiting for you.").wait_for()
    return answer


def test_topic_review_card_approve(page, workbench):
    kernel, _, _ = workbench
    ps, lib = kernel.project("demo")
    ids = seed_topics(ps, lib, 1, [seed_gap(ps, lib, 1)])
    cid = open_topic_review(ps, 1, ids)
    _goto(page, "checkpoints")
    card = page.locator('.checkpoints li[data-kind="topic_review"]')
    card.locator("blockquote", has_text=QUOTE).first.wait_for()
    assert card.locator(".topic h3").all_text_contents() == list(TITLES)  # model text is shown as text, not markup
    assert card.locator("b").count() == 0
    assert card.get_by_text("Closest prior work (reference only)").count() == 2
    assert card.get_by_text("The cohort stops at 79; the topic starts at 80.").count() == 2
    page.get_by_role("button", name=f"Approve this topic: {TITLES[1]}", exact=True).click()
    assert _answer_of(page, ps, cid) == {"decision": "approve", "topic_id": ids[1]}
    assert page.errors == []


def test_topic_review_card_revise_and_reject(page, workbench):
    kernel, _, _ = workbench
    ps, lib = kernel.project("demo")
    ids = seed_topics(ps, lib, 1, [seed_gap(ps, lib, 1)])
    cid = open_topic_review(ps, 1, ids)
    _goto(page, "checkpoints")
    page.get_by_label("What should change").fill("Look at trials in care homes.")
    page.get_by_label("What to redo").select_option("gaps")
    page.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(page, ps, cid) == {"decision": "revise", "target": "gaps",
                                         "comment": "Look at trials in care homes."}
    cid2 = open_topic_review(ps, 1, ids)
    _goto(page, "checkpoints?again")
    page.get_by_role("button", name="Reject all topics").click()
    assert _answer_of(page, ps, cid2) == {"decision": "reject"}
    assert page.errors == []


def test_rq_cards_answer(page, workbench):
    kernel, _, _ = workbench
    ps, lib = kernel.project("demo")
    (tid, _) = seed_topics(ps, lib, 1, [seed_gap(ps, lib, 1)])
    rq_id = seed_rq(ps, lib, 1, tid)
    cid = open_rq_checkpoint(ps, "rq_approval", rq_id, 1)
    _goto(page, "checkpoints")
    card = page.locator('.checkpoints li[data-kind="rq_approval"]')
    card.get_by_text("I am studying aspirin use in people over 80").wait_for()
    for h in HYPOTHESES:
        assert card.get_by_text(h["falsification"]).count() == 1
    assert card.locator("blockquote", has_text="stroke in the very old").count() == 1
    assert card.get_by_text("not found within the searched scope").count() >= 1
    page.get_by_role("button", name="Approve this research question").click()
    assert _answer_of(page, ps, cid) == {"decision": "approve"}

    cid = open_rq_checkpoint(ps, "rq_approval", rq_id, 1)
    _goto(page, "checkpoints?revise")
    page.get_by_label("What should change in the question").fill("Narrow it to secondary prevention.")
    page.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(page, ps, cid) == {"decision": "revise", "comment": "Narrow it to secondary prevention."}

    for button, decision in (("Proceed with this question", "proceed"), ("Go back to topics", "back")):
        cid = open_rq_checkpoint(ps, "significance_novelty", rq_id, 1)
        _goto(page, f"checkpoints?{decision}")
        page.get_by_role("button", name=button).click()
        assert _answer_of(page, ps, cid) == {"decision": decision}
    assert page.errors == []


def test_stage_blocked_card(page, workbench):
    kernel, _, _ = workbench
    ps, _ = kernel.project("demo")
    for button, decision in (("Allow one more use", "allow_one_more"), ("Stop", "stop")):
        cid = create_checkpoint(ps, "stage_blocked", "'gaps' wants gaps->landscape again, but it was used 2 times.",
                                {"choices": ["allow_one_more", "stop"], "edge": "gaps->landscape", "to": "landscape",
                                 "failures": ["no verified gap to build topics on"]}, stage="gaps",
                                actor="orchestrator")
        _goto(page, f"checkpoints?{decision}")
        page.locator('.checkpoints li[data-kind="stage_blocked"]').get_by_text(
            "no verified gap to build topics on").wait_for()
        page.get_by_role("button", name=button, exact=True).click()
        assert _answer_of(page, ps, cid) == {"decision": decision}
    assert page.errors == []


def test_run_controls_follow_state(page, workbench_env):
    ps, _ = workbench_env.kernel.project("demo")
    _intake(ps)
    workbench_env.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
    _goto(page, "progress")
    chips = page.locator("ol.stages > li")
    chips.first.wait_for()
    assert chips.count() == len(stages_for("critical_review"))
    assert page.locator('ol.stages > li[data-status="plan5"]').count() == 8  # every stage after protocol
    assert page.get_by_role("button", name="Pause run").count() == 0
    page.get_by_role("button", name="Start D2 run until topics").click()
    page.get_by_role("button", name="Pause run").wait_for()
    page.locator('ol.stages > li[data-stage="topics"]', has_text="waiting for you").wait_for()
    assert page.get_by_role("button", name="Start D2 run until topics").count() == 0
    page.get_by_role("button", name="Pause run").click()
    until(lambda: latest_run(ps)["status"] == "paused")
    page.get_by_text("paused by you").wait_for()
    assert page.get_by_role("button", name="Pause run").count() == 0
    page.get_by_role("button", name="Resume run").click()
    page.get_by_role("button", name="Pause run").wait_for()
    page.get_by_role("button", name="Cancel run").click()
    page.get_by_role("button", name="Start D2 run until question").wait_for()
    assert latest_run(ps)["status"] == "cancelled"
    assert page.errors == []


def test_run_controls_keep_keyboard_focus(page, workbench_env):
    """A refresh with nothing changed leaves the run controls alone; a change redraws them and focus returns to the
    button of the same name."""
    ps, _ = workbench_env.kernel.project("demo")
    _intake(ps)
    workbench_env.orch.workers["topics"] = FakeWorker(runs=[lambda ctx: StageOutcome(
        "waiting_human", checkpoint_id=open_topic_review(ctx.ps, ctx.attempt, []))])
    _goto(page, "progress")
    page.get_by_role("button", name="Start D2 run until topics").click()
    page.get_by_text("Waiting for your answer").wait_for()
    run_id = latest_run(ps)["id"]
    focused = "document.activeElement && document.activeElement.textContent"
    page.get_by_role("button", name="Pause run").focus()
    with page.expect_response("**/ui/api/stages*"):  # the same status again: a run.status event, nothing changed
        record_run_status(ps, run_id, "paused_waiting_human", actor="t", clock=time.time)
    page.wait_for_timeout(200)
    assert page.evaluate(focused) == "Pause run"
    page.get_by_role("button", name="Cancel run").focus()
    workbench_env.runs.pause("demo", actor="t")  # a change: Pause gives way to Resume, Cancel stays
    page.get_by_role("button", name="Resume run").wait_for()
    assert page.evaluate(focused) == "Cancel run"
    assert page.errors == []


def test_d1_project_shows_the_graph_and_the_lead_hint(page, workbench):
    kernel, _, _ = workbench
    ps, _ = kernel.project("demo")
    _intake(ps, discovery_mode="d1")
    _goto(page, "progress")
    page.get_by_text("the Lead drives the stages").wait_for()
    assert page.locator("ol.stages > li").count() == len(stages_for("critical_review"))
    assert page.get_by_role("button", name="Start D2 run until topics").count() == 0
    assert page.errors == []


def test_d1_project_sent_back_to_question_keeps_its_run_controls(page, workbench_env):
    """protocol->question in a D1 project's run reopens question: the page shows the live run's status and controls,
    not the Lead hint (the orchestrator keeps a project once production began)."""
    ps, _ = workbench_env.kernel.project("demo")
    _intake(ps, discovery_mode="d1")
    orch = workbench_env.orch
    orch.workers.update(back_to_question_workers())
    for stage in DISCOVERY:  # guided discovery
        assert orch.run_stage("demo", stage, actor="t").status == "done"
        assert orch.advance("demo", actor="t").ok
    freeze_an_rq(ps)
    run_id = workbench_env.runs.start("demo", "protocol", actor="t")
    until(lambda: latest_run(ps)["status"] == "paused_waiting_human")
    _goto(page, "progress")
    page.get_by_role("button", name="Cancel run").wait_for()
    assert page.get_by_role("button", name="Pause run").count() == 1
    assert page.get_by_text(f"Run {run_id} (until protocol)").count() == 1
    assert page.get_by_text("the Lead drives the stages").count() == 0
    page.get_by_role("button", name="Cancel run").click()
    page.get_by_role("button", name="Start run until question").wait_for()  # a run goes on; the Lead does not
    assert page.get_by_text("the Lead drives the stages").count() == 0
    assert page.errors == []


def test_discovery_cards_through_a_run_reach_the_workers(page, workbench_env):
    ps, lib = workbench_env.kernel.project("demo")
    _intake(ps)
    workbench_env.orch.workers.update(discovery_workers(lib))
    _goto(page, "progress")
    page.get_by_role("button", name="Start D2 run until question").click()
    until(lambda: get_open(ps, "topic_review"))
    _goto(page, "checkpoints")
    page.get_by_role("button", name=f"Approve this topic: {TITLES[0]}", exact=True).click()
    page.get_by_role("button", name="Approve this research question").click(timeout=10_000)  # the page follows
    until(lambda: latest_run(ps)["status"] == "done")
    assert [t["status"] for t in topics(ps, 1)] == ["approved", "candidate"]
    (rq_cp,) = [c for c in list_checkpoints(ps, "answered") if c["kind"] == "rq_approval"]
    rq = rq_row(ps, rq_cp["options"]["rq_id"])
    assert (rq["status"], rq["adopted_by"]) == ("frozen", "human")
    assert page.errors == []


def get_open(ps, kind: str) -> list[dict]:
    return [c for c in list_checkpoints(ps, "open") if c["kind"] == kind]
