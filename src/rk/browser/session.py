"""The built-in browser window (spec §8.5): one Playwright Chromium persistent context, owned by ``rk serve``.

Playwright runs on its own thread with its own asyncio loop; the synchronous methods below submit coroutines to it, so
tool handlers (which run in FastAPI's thread pool) can use the browser safely.

Tabs belong to an owner: an agent task (``task_id``, or the actor for in-process callers), the human (``None``: tabs
you opened yourself, or that ``rk setup`` opened for you) or the Workbench. Agent tools reach only their own tabs; the
human view lists every tab except the Workbench. Popups inherit their opener's owner.

Agent tabs reach only the public web (spec §8.5 isolation, extended to the local network): no Workbench origin, no
non-http(s) schemes, no loopback / private / link-local / reserved / CGNAT address, whether written as an IP or
resolved from a host name. Hosts in ``allow_hosts`` (``host`` or ``host:port``, e.g. an intranet library proxy) are
exempt; the Workbench never is. Each agent tab gets a CDP ``Fetch`` interceptor that pauses every request and every
redirect hop before it is sent, checks its destination and lets Chromium's own network stack continue it (so
publisher bot checks see a normal browser). A context-wide route stops requests of agent pages that are not armed yet
(a popup's first document is dropped and loaded again once its interceptor is in place) and checks the first request
of cross-site iframes. WebSockets from agent tabs are checked, service workers are blocked. The route disables
Chromium's HTTP cache for the window; the human's own tabs are otherwise passed through unchanged."""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import re
import socket
import threading
import time
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PwError

from rk.tools.registry import ToolError

HUMAN = None
_WORKBENCH = "__workbench__"
_ACTIONS = frozenset({"click", "fill", "press", "scroll", "back"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_DNS_TTL_S = 60.0
_MAX_HOPS = 10


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


def _non_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.strip("[]").split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified
            or (isinstance(a, ipaddress.IPv4Address) and a in _CGNAT))


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]").split("%")[0])
        return True
    except ValueError:
        return False


_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "gov", "net", "org", "or", "ne", "go"})


def registrable_domain(host: str) -> str:
    """Approximate eTLD+1 (``www.sciencedirect.com`` -> ``sciencedirect.com``, ``x.ac.uk`` -> ``x.ac.uk``). Used to
    key per-publisher download quotas only, never for a security decision (it does not know private suffixes such
    as ``github.io``)."""
    host = host.lower().strip(".")
    if _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def frame_allowed(frame_url: str, top_url: str) -> bool:
    """Agent tabs load subframe documents only from the top-level page's own scheme and host: such frames are always
    same-site, so they stay in the tab's renderer process and inside its request interceptor."""
    f, t = urlsplit(frame_url), urlsplit(top_url)
    return f.scheme == t.scheme and (f.hostname or "").lower() == (t.hostname or "").lower()


def _forbidden(url: str, reason: str) -> ToolError:
    return ToolError("forbidden_url", f"agent tabs cannot open {url}: {reason}", {"url": url, "reason": reason},
                     status=403)


# a control that submits its form when clicked
_IS_SUBMIT = ("el => { const f = el.closest('form'); if (!f) return false; const t = (el.getAttribute('type') || '')"
              ".toLowerCase(); return (el.tagName === 'BUTTON' && (t === '' || t === 'submit')) || "
              "(el.tagName === 'INPUT' && (t === 'submit' || t === 'image')); }")


def _no_submit() -> ToolError:
    return ToolError("forbidden_action", "agents submit forms only on search sites (spec §9.3: no payments, posts or "
                     "account changes); ask the human with rk_browser_request_human", status=403)


class BrowserSession:
    def __init__(self, profile_dir: Path, *, headless: bool, workbench_origin: str | None = None,
                 allow_hosts: Iterable[str] = ()):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self._workbench = _loopback_variants(workbench_origin) if workbench_origin else set()
        self._allow = {h.strip().lower() for h in allow_hosts if h.strip()}
        self._dns: dict[str, tuple[float, bool]] = {}
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._ctx = None
        self._pages: dict[str, Any] = {}  # tab id -> Page
        self._owners: dict[str, str | None] = {}
        self._refused: dict[str, tuple[str, str]] = {}  # tab id -> (url, reason) of a refused main-frame hop
        self._armed: set[str] = set()  # agent tabs whose CDP interceptor is active
        self._arming: set[str] = set()
        self._seen: dict[str, float] = {}  # tab id -> when it was last visible (for "save the page you look at")
        self.submit_hosts: set[str] = set()  # host[:port] where agents may send POST and similar (search sites)
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
                str(self.profile_dir), headless=self.headless, accept_downloads=True, no_viewport=not self.headless,
                service_workers="block", args=["--site-per-process"])  # isolation as in desktop Chrome, everywhere
        except BaseException:
            await self._pw.stop()
            self._pw = None
            raise
        # which of your tabs you used last: "save the page you are looking at" saves that one
        await self._ctx.expose_binding("__rkVisible", self._on_visible)
        await self._ctx.add_init_script(
            "for (const e of ['pointerdown', 'keydown']) addEventListener(e, () => window.__rkVisible(), true);"
            "addEventListener('focus', () => window.__rkVisible());"
            "document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') "
            "window.__rkVisible(); });")
        await self._ctx.route("**/*", self._route)
        self._ctx.on("page", self._on_page)
        for p in self._ctx.pages:  # the window's initial blank tab: the human's until the Workbench claims it
            self._ensure_registered(p, HUMAN)

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
            async def drain():
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            try:
                asyncio.run_coroutine_threadsafe(drain(), self._loop).result(10)
            except (TimeoutError, RuntimeError):
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(10)

    def call(self, coro: Coroutine, timeout_s: float) -> Any:
        """Run a coroutine on the browser thread and return its result."""
        if not self._thread or not self._thread.is_alive():
            coro.close()
            raise ToolError("browser_unavailable", "the browser is not running", status=503)
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout_s)

    # ---- destinations -------------------------------------------------------------------------------------------
    async def _resolves_non_public(self, host: str) -> bool:
        now = time.monotonic()
        hit = self._dns.get(host)
        if hit and hit[0] > now:
            return hit[1]
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
            bad = any(_non_public(i[4][0]) for i in infos)
        except OSError:  # unresolvable: the browser cannot reach it either
            bad = False
        self._dns[host] = (now + _DNS_TTL_S, bad)
        return bad

    async def _blocked_reason(self, url: str) -> str | None:
        """Why an agent tab may not load ``url`` (None: allowed)."""
        p = urlsplit(url)
        scheme = {"ws": "http", "wss": "https"}.get(p.scheme, p.scheme)
        if scheme not in ("http", "https"):
            return f"scheme {p.scheme or '(none)'} is not allowed"
        if f"{scheme}://{p.netloc}".lower() in self._workbench:
            return "the Workbench is not reachable from agent tabs"
        host = (p.hostname or "").lower()
        if not host:
            return "no host"
        port = p.port or (443 if scheme == "https" else 80)
        if host in self._allow or f"{host}:{port}" in self._allow:
            return None
        if host == "localhost" or host.endswith(".localhost") or _non_public(host):
            return "local or private network address"
        if not _is_ip(host) and await self._resolves_non_public(host):
            return "host resolves to a local or private network address"
        return None

    # ---- request gating ------------------------------------------------------------------------------------------
    async def _route(self, route, request) -> None:
        """Context-wide gate (Playwright route). An armed agent tab is checked hop by hop by its CDP interceptor
        before a request reaches this route; here we only stop what an unarmed agent page (a popup in its first
        moments) would send, and check first hops of frames the page interceptor cannot see (cross-site iframes)."""
        try:
            page = request.frame.page
        except PwError:  # no frame: be strict
            page = None
        tid = self._tid_of(page) if page is not None else None
        was_armed = tid in self._armed
        if page is not None and tid is None:
            tid = await self._owner_tid(page)  # registers (and arms) a popup on its first request
        if tid is not None and not self._is_agent(self._owners.get(tid)):
            await route.fallback()
            return
        url = request.url
        if urlsplit(url).scheme in ("data", "blob"):
            await route.fallback()
            return
        if await self._blocked_reason(url) is not None:
            await route.abort("accessdenied")
            return
        if (page is not None and request.resource_type == "document" and request.frame != page.main_frame
                and not frame_allowed(url, page.main_frame.url)):
            await route.abort("accessdenied")  # frame from another host: it could run outside the tab's interceptor
            return
        if not was_armed and page is not None and request.is_navigation_request() and request.frame == page.main_frame:
            # sent before the tab's interceptor existed: drop it and load it again once every hop is checked
            await route.abort("aborted")
            asyncio.ensure_future(self._replay(tid, page, url))
            return
        await route.fallback()

    async def _replay(self, tid: str, page, url: str) -> None:
        while tid not in self._armed and tid in self._pages:
            await asyncio.sleep(0.02)
        try:
            await page.goto(url)
        except PwError:
            pass

    async def _arm(self, page, tid: str) -> None:
        """Give an agent tab its CDP request interceptor: every request and every redirect hop is paused, checked and
        continued on Chromium's own network stack (or refused)."""
        cdp = await self._ctx.new_cdp_session(page)
        tree = await cdp.send("Page.getFrameTree")
        main_id = tree["frameTree"]["frame"]["id"]

        async def paused(ev: dict) -> None:
            url = ev["request"]["url"]
            try:
                reason = None if urlsplit(url).scheme in ("data", "blob") else await self._blocked_reason(url)
                doc = ev.get("resourceType") == "Document"
                if not reason and doc and ev.get("frameId") != main_id and not frame_allowed(url, page.main_frame.url):
                    reason = "frames from other hosts are not loaded in agent tabs"
                method = ev["request"].get("method", "GET").upper()
                # §9.3: while an agent owns the tab, nothing it triggers may post (forms, scripts behind a button:
                # account changes, payments) except on search sites; a tab handed to you can sign in
                if not reason and method not in ("GET", "HEAD", "OPTIONS") and self._is_agent(self._owners.get(tid)) \
                        and urlsplit(url).netloc.lower() not in self.submit_hosts:
                    reason = "agents submit forms only on search sites"
                if reason:
                    if doc and ev.get("frameId") == main_id:
                        self._refused[tid] = (url, reason)
                    await cdp.send("Fetch.failRequest", {"requestId": ev["requestId"], "errorReason": "AccessDenied"})
                else:
                    await cdp.send("Fetch.continueRequest", {"requestId": ev["requestId"]})
            except PwError:  # the tab closed meanwhile
                pass

        cdp.on("Fetch.requestPaused", lambda ev: asyncio.ensure_future(paused(ev)))
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        await page.route_web_socket(re.compile(r".*"), self._guard_ws)
        self._armed.add(tid)

    async def _guard_ws(self, ws) -> None:
        if await self._blocked_reason(ws.url):
            await ws.close(code=1008, reason="blocked by rk")
            return
        ws.connect_to_server()

    async def _blank(self, page) -> None:
        """Leave a refused page: wait out Chromium's own error navigation, then load about:blank."""
        for _ in range(3):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.goto("about:blank", timeout=10000)
                return
            except PwError:
                await asyncio.sleep(0.1)

    async def _settle(self, tid: str, page) -> None:
        """After an agent action: let the document load, then fail if a main-frame hop was refused."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PwError:
            pass
        if refused := self._refused.pop(tid, None):
            await self._blank(page)
            raise _forbidden(*refused)

    # ---- ownership ----------------------------------------------------------------------------------------------
    def _ensure_registered(self, page, owner: str | None) -> str:
        """Register a page once (runs on the browser thread, no await inside: atomic there)."""
        with self._lock:
            tid = next((t for t, p in self._pages.items() if p is page), None)
            if tid is not None:
                return tid
            tid = f"tab_{next(self._ids)}"
            self._pages[tid] = page
            self._owners[tid] = owner
        page.on("close", lambda _p, tid=tid: self._forget(tid))
        return tid

    async def _owner_tid(self, page) -> str:
        tid = self._tid_of(page)
        if tid is not None:
            return tid
        opener = await page.opener()  # a popup: it belongs to its opener's owner
        owner = self._owners.get(self._tid_of(opener)) if opener else HUMAN
        return await self._adopt(page, HUMAN if owner == _WORKBENCH else owner)

    async def _adopt(self, page, owner: str | None) -> str:
        tid = self._ensure_registered(page, owner)
        self._owners[tid] = owner
        if self._is_agent(owner) and tid not in self._armed and tid not in self._arming:
            self._arming.add(tid)
            try:
                await self._arm(page, tid)
            finally:
                self._arming.discard(tid)
        return tid

    async def _on_visible(self, source) -> None:
        tid = self._tid_of(source["page"])
        if tid and self._owners.get(tid, _WORKBENCH) is HUMAN:  # your own tabs; agent clicks do not count
            self._seen[tid] = time.monotonic()

    def _forget(self, tid: str) -> None:
        with self._lock:
            self._pages.pop(tid, None)
            self._owners.pop(tid, None)
        self._refused.pop(tid, None)
        self._armed.discard(tid)

    def _tid_of(self, page) -> str | None:
        return next((t for t, p in self._pages.items() if p is page), None)

    def _is_agent(self, owner: str | None) -> bool:
        return owner is not HUMAN and owner != _WORKBENCH

    async def _on_page(self, page) -> None:
        await self._owner_tid(page)

    def _check_owner(self, tab_id: str, owner: str | None):
        page = self._pages.get(tab_id)
        tab_owner = self._owners.get(tab_id, _WORKBENCH)
        if page is None or tab_owner == _WORKBENCH or (owner is not HUMAN and tab_owner != owner):
            raise ToolError("tab_not_found", f"no tab {tab_id} for this task", {"tab": tab_id}, status=404)
        return page

    def _page(self, tab_id: str):
        return self._pages[tab_id]

    # ---- tabs ---------------------------------------------------------------------------------------------------
    def open_workbench(self, url: str) -> tuple[str, str]:
        """Load the Workbench in the window's first tab; returns the loaded (url, title)."""

        async def go():
            human_blank = [p for t, p in self._pages.items() if self._owners[t] is HUMAN and p.url == "about:blank"]
            page = human_blank[0] if human_blank else await self._ctx.new_page()
            tid = self._ensure_registered(page, _WORKBENCH)
            self._owners[tid] = _WORKBENCH
            await page.goto(url)
            return page.url, await page.title()

        return self.call(go(), 60)

    def new_tab(self, owner: str | None, url: str | None = None) -> Tab:
        async def go():
            if url and self._is_agent(owner) and (reason := await self._blocked_reason(url)):
                raise _forbidden(url, reason)
            page = await self._ctx.new_page()
            tid = self._ensure_registered(page, owner)
            self._owners[tid] = owner  # the 'page' event may have registered it as the human's first
            await self._adopt(page, owner)
            if url:
                try:
                    await self._goto(page, url, "domcontentloaded", 45)
                    if self._is_agent(owner):
                        await self._settle(tid, page)
                except ToolError:
                    await page.close()  # a refused or failed first navigation leaves no empty tab behind
                    raise
            return Tab(tid, owner, page.url)

        return self.call(go(), 120)

    def tabs(self, owner: str | None) -> list[Tab]:
        with self._lock:
            items = [(t, p, self._owners.get(t, _WORKBENCH)) for t, p in self._pages.items()]
        return [Tab(t, o, p.url) for t, p, o in items if o != _WORKBENCH and (owner is HUMAN or o == owner)]

    def get_tab(self, tab_id: str, owner: str | None) -> Tab:
        page = self._check_owner(tab_id, owner)
        return Tab(tab_id, self._owners[tab_id], page.url)

    def active_human_tab(self) -> Tab | None:
        """The tab you looked at last (other than the Workbench), else the most recently opened human tab."""

        async def find():
            candidates = [t for t in self._pages if self._owners.get(t, _WORKBENCH) != _WORKBENCH]
            seen = [t for t in candidates if t in self._seen]
            if seen:
                return max(seen, key=lambda t: self._seen[t])
            human = [t for t in candidates if self._owners.get(t) is HUMAN]
            return human[-1] if human else None

        tid = self.call(find(), 30)
        return Tab(tid, self._owners.get(tid), self._pages[tid].url) if tid else None

    def hand_over(self, tab_id: str, owner: str | None) -> None:
        """Give an agent's tab to the human. Its request interceptor stays, so what an agent opened stays on the
        public web even after the hand-over."""
        self._check_owner(tab_id, owner)
        self._owners[tab_id] = HUMAN

    def close_tab(self, tab_id: str, owner: str | None) -> None:
        page = self._check_owner(tab_id, owner)
        self.call(page.close(), 30)

    # ---- page operations ----------------------------------------------------------------------------------------
    async def _goto(self, page, url: str, wait: str, timeout_s: float):
        try:
            return await page.goto(url, wait_until=wait, timeout=timeout_s * 1000)
        except PwError as e:
            tid = self._tid_of(page)
            if tid and (refused := self._refused.pop(tid, None)):
                await self._blank(page)
                raise _forbidden(*refused) from None
            raise ToolError("navigation_failed", f"could not load {url}: {str(e).splitlines()[0]}",
                            {"url": url}, status=502) from None

    async def _state(self, page, status: int | None = None) -> PageState:
        """The page's URL, title and HTML. A page still navigating gets 15 s more; one that still cannot be read fails
        with ``navigation_failed`` (a ``ToolError``, so a run's full-text fetch records it per record)."""
        try:
            try:
                content = await page.content()
            except PwError:  # navigation still in progress
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                content = await page.content()
            return PageState(page.url, await page.title(), content, status)
        except PwError as e:
            raise ToolError("navigation_failed", f"could not read {page.url}: {str(e).splitlines()[0]}",
                            {"url": page.url}, status=502) from None

    async def _checked_state(self, tab_id: str, owner: str | None, page, status: int | None = None) -> PageState:
        if self._is_agent(owner):
            await self._settle(tab_id, page)
        return await self._state(page, status)

    def goto(self, tab_id: str, owner: str | None, url: str, *, wait: str = "domcontentloaded",
             timeout_s: float = 45) -> PageState:
        page = self._check_owner(tab_id, owner)

        async def go():
            if self._is_agent(owner) and (reason := await self._blocked_reason(url)):
                raise _forbidden(url, reason)
            resp = await self._goto(page, url, wait, timeout_s)
            return await self._checked_state(tab_id, owner, page, resp.status if resp else None)

        return self.call(go(), timeout_s + 60)

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
        return self.call(self._checked_state(tab_id, owner, page), 60)

    def snapshot(self, tab_id: str, owner: str | None, *, screenshot_to: Path | None = None) -> dict:
        page = self._check_owner(tab_id, owner)

        async def go():
            if self._is_agent(owner):
                await self._settle(tab_id, page)
            ax = await page.locator("body").aria_snapshot(timeout=15000)
            if screenshot_to:
                Path(screenshot_to).parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(screenshot_to))
            return {"tab": tab_id, "url": page.url, "title": await page.title(), "ax": ax,
                    "screenshot_path": str(screenshot_to) if screenshot_to else None}

        return self.call(go(), 90)

    def act(self, tab_id: str, owner: str | None, action: dict, *, allow_submit: bool = False) -> PageState:
        """``allow_submit``: agents may submit forms only on search sites (spec §9.3); every other form submission is
        refused here (submit controls, Enter in a form field) and, for POST and similar, again at the request."""
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
                    loc = page.locator(sel).first
                    if self._is_agent(owner) and not allow_submit and await loc.evaluate(_IS_SUBMIT, timeout=10000):
                        raise _no_submit()
                    await loc.click(timeout=10000)
                elif kind == "fill":
                    loc = page.locator(sel).first
                    if (await loc.get_attribute("type", timeout=10000) or "").lower() == "password":
                        raise ToolError("forbidden_action", "the Kernel never fills in passwords; ask the human "
                                        "with rk_browser_request_human", {"selector": sel}, status=403)
                    await loc.fill(action.get("text") or "", timeout=10000)
                elif kind == "press":
                    key = action.get("key") or "Enter"
                    if self._is_agent(owner) and not allow_submit and key in ("Enter", "NumpadEnter"):
                        handle = page.locator(sel).first if sel else None
                        js = "el => !!(el && el.closest('form') && el.tagName === 'INPUT')"
                        in_form = await (handle.evaluate(js) if handle else page.evaluate(
                            "(() => { const el = document.activeElement; "
                            "return !!(el && el.closest('form') && el.tagName === 'INPUT'); })()"))
                        if in_form:
                            raise _no_submit()
                    target = page.locator(sel).first if sel else page.keyboard
                    await target.press(key)
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
            return await self._checked_state(tab_id, owner, page)

        return self.call(go(), 120)

    def fetch_bytes(self, tab_id: str, owner: str | None, url: str, *, referer: str | None = None,
                    timeout_s: float = 60) -> tuple[int, str, bytes, str]:
        """GET with the browser's cookies (library proxy and publisher sessions): (status, content type, body,
        final URL). Redirects are followed here, hop by hop, so each hop is checked for agent owners."""
        self._check_owner(tab_id, owner)

        async def go():
            current = url
            for _ in range(_MAX_HOPS + 1):
                if self._is_agent(owner) and (reason := await self._blocked_reason(current)):
                    raise _forbidden(current, reason)
                try:
                    r = await self._ctx.request.get(current, headers={"Referer": referer} if referer else None,
                                                    timeout=timeout_s * 1000, max_redirects=0)
                except PwError as e:
                    raise ToolError("download_failed", str(e).splitlines()[0], {"url": current}, status=502) from None
                location = r.headers.get("location")
                if 300 <= r.status < 400 and location:
                    current = urljoin(current, location)
                    continue
                return (r.status, (r.headers.get("content-type") or "").split(";")[0].strip(), await r.body(),
                        current)
            raise ToolError("download_failed", "too many redirects", {"url": url}, status=502)

        return self.call(go(), timeout_s + 30)

    def click_download(self, tab_id: str, owner: str | None, selector: str, *, timeout_s: float = 60) -> bytes | None:
        """Click a link that starts a download (the request goes through the route, so it is checked)."""
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
