"""The Workbench in a real (headless) Chromium against a real ``rk serve`` app: navigation, checkpoint answering,
library search and chat."""

# ruff: noqa: F811 - fixtures imported from other test modules are re-bound as test parameters
import threading
import time

import pytest
import uvicorn

from rk.checkpoints import create_checkpoint, get_checkpoint
from rk.config import Settings, load_settings, save_settings
from rk.fulltext import attach_pdf_bytes
from rk.library.models import Creator, Ids, ItemIn
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.server.runtime import acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.ui.auth import UiAuth
from rk.workspace import Kernel

from .browser_sites import tiny_pdf
from .test_sessions import fake  # noqa: F401 - fixture

pytestmark = pytest.mark.browser
PAGES = {"chat": "Chat", "library": "Library", "checkpoints": "Checkpoints", "progress": "Progress",
         "drafts": "Drafts", "projects": "Projects", "settings": "Settings"}


@pytest.fixture
def workbench(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    rt = acquire_serve_lock(tmp_home)
    kernel = Kernel(tmp_home, http=replay_http)
    kernel.create_project("demo", "Demo project", actor="t")
    registry = build_registry(kernel)
    origin = f"http://127.0.0.1:{rt.port}"
    ui = UiAuth(origin)
    app = create_app(registry, rt.token, tokens=kernel.tokens, chats=ChatManager(kernel, registry), ui=ui,
                     kernel=kernel)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
    t.start()
    time.sleep(0.5)
    yield kernel, ui
    server.should_exit = True
    t.join(5)
    kernel.close()
    release(rt)


@pytest.fixture
def page(workbench):
    from playwright.sync_api import sync_playwright

    _, ui = workbench
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
    kernel, _ = workbench
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
    kernel, _ = workbench
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


def test_chat_roundtrip_fake_driver(page, fake, monkeypatch):
    exe, _ = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    save_settings(load_settings().model_copy(update={"cli_paths": {"claude": exe}}))
    page.goto(page.url.split("#")[0] + "#/chat")
    page.get_by_text("Talking to claude_cli.").wait_for()
    page.get_by_label("Message").fill("hello")
    page.get_by_role("button", name="Send").click()
    page.locator(".msg:not(.you)").first.wait_for()
    assert page.locator("details.tool").count() >= 1
    assert page.errors == []


def test_chat_ignores_a_superseded_open(page, fake, monkeypatch):
    """Re-rendering the chat page opens a new chat; the earlier render's ``chat.opened`` arriving late must not bind
    the new page to that chat (whose replies the page would then drop once its own ``chat.opened`` arrives)."""
    exe, _ = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    save_settings(load_settings().model_copy(update={"cli_paths": {"claude": exe}}))
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
