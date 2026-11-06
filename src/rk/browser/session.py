"""The built-in browser window (spec §8.5): one Playwright Chromium persistent context, owned by ``rk serve``.

Playwright runs on its own thread with its own asyncio loop; the synchronous methods below submit coroutines to it, so
tool handlers (which run in FastAPI's thread pool) can use the browser safely.

Tabs belong to an owner: an agent task (``task_id``, or the actor for in-process callers), the human (``None``: tabs
you opened yourself, or that ``rk setup`` opened for you) or the Workbench. Agent tools reach only their own tabs; the
human view lists every tab except the Workbench. Requests from agent tabs to the Workbench origin are aborted."""

from __future__ import annotations

import asyncio
import itertools
import re
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PwError

from rk.tools.registry import ToolError

HUMAN = None
_WORKBENCH = "__workbench__"
_ACTIONS = frozenset({"click", "fill", "press", "scroll", "back"})


@dataclass
class Tab:
    id: str
    owner: str | None
    url: str


@dataclass
class PageState:
    url: str
    title: str
    html: str
    status: int | None = None


def _loopback_variants(origin: str) -> set[str]:
    p = urlsplit(origin)
    port = f":{p.port}" if p.port else ""
    return {f"{p.scheme}://{h}{port}" for h in ("127.0.0.1", "localhost", "[::1]")}


class BrowserSession:
    def __init__(self, profile_dir: Path, *, headless: bool, workbench_origin: str | None = None):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self._blocked = _loopback_variants(workbench_origin) if workbench_origin else set()
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._ctx = None
        self._pages: dict[str, Any] = {}  # tab id -> Page
        self._owners: dict[str, str | None] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ---- lifecycle ----------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._ctx is not None

    def start(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop.run_forever, name="rk-browser", daemon=True)
        self._thread.start()
        try:
            self.call(self._start(), 120)
        except BaseException:
            self._stop_loop()
            raise

    async def _start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless, accept_downloads=True, no_viewport=not self.headless)
        except BaseException:
            await self._pw.stop()
            self._pw = None
            raise
        if self._blocked:
            await self._ctx.route(re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/"),
                                  self._guard_workbench)
        self._ctx.on("page", self._on_page)
        for p in self._ctx.pages:  # the window's initial blank tab: the human's until the Workbench claims it
            self._register(p, HUMAN)

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self.call(self._close(), 60)
            finally:
                self._ctx = None
        self._stop_loop()

    async def _close(self) -> None:
        await self._ctx.close()
        await self._pw.stop()

    def _stop_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(10)

    def call(self, coro: Coroutine, timeout_s: float) -> Any:
        """Run a coroutine on the browser thread and return its result."""
        if not self._thread or not self._thread.is_alive():
            coro.close()
            raise ToolError("browser_unavailable", "the browser is not running", status=503)
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout_s)

    # ---- ownership and isolation --------------------------------------------------------------------------------
    def _register(self, page, owner: str | None) -> str:
        with self._lock:
            tid = f"tab_{next(self._ids)}"
            self._pages[tid] = page
            self._owners[tid] = owner
        page.on("close", lambda _p, tid=tid: self._forget(tid))
        return tid

    def _forget(self, tid: str) -> None:
        with self._lock:
            self._pages.pop(tid, None)
            self._owners.pop(tid, None)

    def _tid_of(self, page) -> str | None:
        return next((t for t, p in self._pages.items() if p is page), None)

    async def _on_page(self, page) -> None:
        if self._tid_of(page) is not None:
            return
        opener = await page.opener()
        owner = self._owners.get(self._tid_of(opener)) if opener else HUMAN
        self._register(page, HUMAN if owner == _WORKBENCH else owner)  # popups inherit their opener's owner

    def _origin_blocked(self, url: str) -> bool:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}" in self._blocked

    async def _guard_workbench(self, route, request) -> None:
        if self._origin_blocked(request.url):
            try:
                owner = self._owners.get(self._tid_of(request.frame.page), "?")
            except PwError:  # service worker or detached frame: no page to vouch for it
                owner = "?"
            if owner not in (_WORKBENCH, HUMAN):
                await route.abort("accessdenied")
                return
        await route.fallback()

    def _check_owner(self, tab_id: str, owner: str | None):
        page = self._pages.get(tab_id)
        tab_owner = self._owners.get(tab_id, _WORKBENCH)
        if page is None or tab_owner == _WORKBENCH or (owner is not HUMAN and tab_owner != owner):
            raise ToolError("tab_not_found", f"no tab {tab_id} for this task", {"tab": tab_id}, status=404)
        return page

    def _page(self, tab_id: str):
        return self._pages[tab_id]

    # ---- tabs ---------------------------------------------------------------------------------------------------
    def open_workbench(self, url: str) -> None:
        async def go():
            human_blank = [p for t, p in self._pages.items() if self._owners[t] is HUMAN and p.url == "about:blank"]
            page = human_blank[0] if human_blank else await self._ctx.new_page()
            tid = self._tid_of(page) or self._register(page, _WORKBENCH)
            self._owners[tid] = _WORKBENCH
            await page.goto(url)

        self.call(go(), 60)

    def new_tab(self, owner: str | None, url: str | None = None) -> Tab:
        if url and owner is not HUMAN and self._origin_blocked(url):
            raise ToolError("forbidden_url", "agent tabs cannot open the Workbench", {"url": url}, status=403)

        async def go():
            page = await self._ctx.new_page()
            tid = self._tid_of(page) or self._register(page, owner)
            self._owners[tid] = owner
            if url:
                await self._goto(page, url, "domcontentloaded", 45)
            return Tab(tid, owner, page.url)

        return self.call(go(), 90)

    def tabs(self, owner: str | None) -> list[Tab]:
        with self._lock:
            items = list(self._pages.items())
        return [Tab(t, self._owners[t], p.url) for t, p in items
                if self._owners.get(t) != _WORKBENCH and (owner is HUMAN or self._owners.get(t) == owner)]

    def get_tab(self, tab_id: str, owner: str | None) -> Tab:
        page = self._check_owner(tab_id, owner)
        return Tab(tab_id, self._owners[tab_id], page.url)

    def active_human_tab(self) -> Tab | None:
        """The tab you are looking at: the visible non-Workbench tab, else the most recently opened human tab."""

        async def find():
            candidates = [(t, p) for t, p in self._pages.items() if self._owners.get(t) != _WORKBENCH]
            for t, p in reversed(candidates):
                try:
                    if await p.evaluate("document.visibilityState === 'visible' && document.hasFocus()"):
                        return t
                except PwError:  # page closed or navigating: not the active tab
                    continue
            human = [t for t, _ in candidates if self._owners.get(t) is HUMAN]
            return human[-1] if human else None

        tid = self.call(find(), 30)
        return Tab(tid, self._owners[tid], self._pages[tid].url) if tid else None

    # ---- page operations ----------------------------------------------------------------------------------------
    async def _goto(self, page, url: str, wait: str, timeout_s: float):
        try:
            return await page.goto(url, wait_until=wait, timeout=timeout_s * 1000)
        except PwError as e:
            raise ToolError("navigation_failed", f"could not load {url}: {str(e).splitlines()[0]}",
                            {"url": url}, status=502) from None

    async def _state(self, page, response=None) -> PageState:
        try:
            content = await page.content()
        except PwError:  # navigation still in progress
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            content = await page.content()
        return PageState(page.url, await page.title(), content, response.status if response else None)

    def goto(self, tab_id: str, owner: str | None, url: str, *, wait: str = "domcontentloaded",
             timeout_s: float = 45) -> PageState:
        page = self._check_owner(tab_id, owner)
        if owner is not HUMAN and self._origin_blocked(url):
            raise ToolError("forbidden_url", "agent tabs cannot open the Workbench", {"url": url}, status=403)

        async def go():
            resp = await self._goto(page, url, wait, timeout_s)
            return await self._state(page, resp)

        return self.call(go(), timeout_s + 30)

    def wait_for(self, tab_id: str, owner: str | None, selector: str, timeout_s: float = 20) -> bool:
        page = self._check_owner(tab_id, owner)

        async def go():
            try:
                await page.wait_for_selector(selector, timeout=timeout_s * 1000)
                return True
            except PwError:
                return False

        return self.call(go(), timeout_s + 10)

    def content(self, tab_id: str, owner: str | None) -> PageState:
        page = self._check_owner(tab_id, owner)
        return self.call(self._state(page), 30)

    def snapshot(self, tab_id: str, owner: str | None, *, screenshot_to: Path | None = None) -> dict:
        page = self._check_owner(tab_id, owner)

        async def go():
            ax = await page.locator("body").aria_snapshot(timeout=15000)
            if screenshot_to:
                Path(screenshot_to).parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(screenshot_to))
            return {"tab": tab_id, "url": page.url, "title": await page.title(), "ax": ax,
                    "screenshot_path": str(screenshot_to) if screenshot_to else None}

        return self.call(go(), 60)

    def act(self, tab_id: str, owner: str | None, action: dict) -> PageState:
        kind = action.get("kind")
        if kind not in _ACTIONS:
            raise ToolError("bad_action", f"action kind must be one of {sorted(_ACTIONS)}", {"action": action})
        page = self._check_owner(tab_id, owner)
        sel = action.get("selector")
        if kind in ("click", "fill") and not sel:
            raise ToolError("bad_action", f"{kind} needs a selector", {"action": action})

        async def go():
            try:
                if kind == "click":
                    await page.locator(sel).first.click(timeout=10000)
                elif kind == "fill":
                    loc = page.locator(sel).first
                    if (await loc.get_attribute("type", timeout=10000) or "").lower() == "password":
                        raise ToolError("forbidden_action", "the Kernel never fills in passwords; ask the human "
                                        "with rk_browser_request_human", {"selector": sel}, status=403)
                    await loc.fill(action.get("text") or "", timeout=10000)
                elif kind == "press":
                    target = page.locator(sel).first if sel else page.keyboard
                    await target.press(action.get("key") or "Enter")
                elif kind == "scroll":
                    await page.mouse.wheel(0, 800)
                else:
                    await page.go_back(wait_until="domcontentloaded", timeout=30000)
            except PwError as e:
                raise ToolError("action_failed", str(e).splitlines()[0], {"action": action}, status=422) from None
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PwError:
                pass
            if self._origin_blocked(page.url) and owner is not HUMAN:
                await page.goto("about:blank")
                raise ToolError("forbidden_url", "agent tabs cannot open the Workbench", status=403)
            return await self._state(page)

        return self.call(go(), 90)

    def fetch_bytes(self, tab_id: str, owner: str | None, url: str, *, referer: str | None = None,
                    timeout_s: float = 60) -> tuple[int, str, bytes, str]:
        """GET with the browser's cookies (library proxy and publisher sessions): (status, content type, body,
        final URL)."""
        self._check_owner(tab_id, owner)
        if owner is not HUMAN and self._origin_blocked(url):
            raise ToolError("forbidden_url", "agent tabs cannot open the Workbench", {"url": url}, status=403)

        async def go():
            try:
                r = await self._ctx.request.get(url, headers={"Referer": referer} if referer else None,
                                                timeout=timeout_s * 1000, max_redirects=10)
                return (r.status, (r.headers.get("content-type") or "").split(";")[0].strip(), await r.body(), r.url)
            except PwError as e:
                raise ToolError("download_failed", str(e).splitlines()[0], {"url": url}, status=502) from None

        return self.call(go(), timeout_s + 30)

    def click_download(self, tab_id: str, owner: str | None, selector: str, *, timeout_s: float = 60) -> bytes | None:
        page = self._check_owner(tab_id, owner)

        async def go():
            try:
                async with page.expect_download(timeout=timeout_s * 1000) as info:
                    await page.locator(selector).first.click(timeout=10000)
                dl = await info.value
                path = await dl.path()
                return Path(path).read_bytes() if path else None
            except PwError:
                return None

        return self.call(go(), timeout_s + 30)

    def close_tab(self, tab_id: str, owner: str | None) -> None:
        page = self._check_owner(tab_id, owner)
        self.call(page.close(), 30)
