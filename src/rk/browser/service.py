"""Browser work for agents and for you (spec §8.4 browser tools, §8.5, §8.6 "save to library").

Every call goes through the human-pace ``Pacer`` and the block detector. A CAPTCHA pauses the site and opens a
``captcha_wait`` checkpoint; a sign-in page opens ``login_wait``; the call then fails with ``human_required`` and the
agent's tab becomes yours. A paused site fails fast for agents (``site_paused``) until its checkpoint is answered."""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote, urlsplit

from rk.browser import HumanRequired, Paced, SitePaused
from rk.browser.adapters import ADAPTERS, SiteAdapter, valid
from rk.browser.adapters.publisher import find_pdf_url
from rk.browser.detect import Block, detect_block
from rk.browser.log import record_browse
from rk.browser.metadata import PageMetadata, merge_with_api, parse_page_metadata
from rk.browser.pacing import Pacer
from rk.browser.session import HUMAN, BrowserSession, registrable_domain
from rk.browser.translation import TranslationServer
from rk.checkpoints import create_checkpoint, get_checkpoint
from rk.fulltext import (
    FulltextResult,
    attach_html_snapshot,
    attach_pdf_bytes,
    latest_snapshot,
)
from rk.library.formats import from_csl
from rk.net.http import HttpError
from rk.sources.identifiers import Identifier
from rk.sources.resolver import ResolvedWork, resolve
from rk.tools.registry import CallContext, ToolError

SITE_LABELS = {"scholar": "Google Scholar", "pubmed": "PubMed", "europepmc": "Europe PMC",
               "primo": "the library catalogue (Primo)", "summon": "the library catalogue (Summon)",
               "publisher": "the publisher site", "library": "the library proxy"}
_AX_LIMIT = 20000
_MAX_AGENT_TABS = 5
_NAVIGATING = {"click", "press", "back"}


def _owner(ctx: CallContext) -> str | None:
    """Tabs of the human (CLI and Workbench calls) or of one agent task."""
    return HUMAN if ctx.role == "cli" else (ctx.task_id or ctx.actor)


def _actor_kind(ctx: CallContext) -> str:
    return "human" if ctx.role == "cli" else "agent"


class BrowserService:
    def __init__(self, kernel, session: BrowserSession, pacer: Pacer, *, adapters: dict[str, SiteAdapter] | None = None,
                 doi_base: str = "https://doi.org/"):
        self.kernel = kernel
        self.session = session
        self.pacer = pacer
        self.adapters = adapters or ADAPTERS
        self.doi_base = doi_base
        self._steps: dict[tuple[str | None, str | None], int] = {}
        self._tabs: dict[tuple[str | None, str], str] = {}
        self._op = threading.RLock()
        self.session.submit_hosts = set(self._site_hosts())

    @property
    def running(self) -> bool:
        return self.session.running

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _project(self, slug: str):
        try:
            return self.kernel.project(slug)
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e

    def _proxy_host(self) -> str | None:
        proxy = self.kernel.settings().library_proxy
        return urlsplit(proxy.replace("{url}", "").replace("{url_enc}", "")).hostname if proxy else None

    def _proxied(self, url: str) -> tuple[str, bool]:
        """The URL through the library proxy (``{url}`` raw as EZproxy expects, ``{url_enc}`` percent-encoded as
        OpenAthens redirectors expect), and whether the proxy is used."""
        proxy = self.kernel.settings().library_proxy
        if not proxy:
            return url, False
        return proxy.replace("{url_enc}", quote(url, safe="")).replace("{url}", url), True

    def _check_pause(self, site: str) -> None:
        p = self.pacer.paused(site)
        if not p:
            return
        if p.get("checkpoint_id") and p.get("project"):
            try:
                ps, _ = self.kernel.project(p["project"])
                if get_checkpoint(ps, p["checkpoint_id"])["status"] != "open":
                    self.pacer.unpause(site)
                    return
            except (LookupError, KeyError):
                self.pacer.unpause(site)  # the checkpoint is gone: nothing left to wait for
                return
        raise SitePaused(site, p["reason"], p.get("checkpoint_id"))

    def _site_hosts(self) -> dict[str, str]:
        """host[:port] -> site for the search sites and the library proxy: pauses and pacing are per site."""
        s = self.kernel.settings()
        out: dict[str, str] = {}
        for name, a in self.adapters.items():
            try:
                out.setdefault(urlsplit(a.search_url("x", s)).netloc.lower(), name)
            except ToolError:  # a library catalogue that is not configured
                continue
        if s.library_proxy:
            out.setdefault(urlsplit(s.library_proxy.replace("{url}", "").replace("{url_enc}", "")).netloc.lower(),
                           "library")
        return out

    def _site_of(self, url: str) -> str:
        parts = urlsplit(url)
        return self._site_hosts().get(parts.netloc.lower()) or parts.hostname or "?"

    @contextmanager
    def _busy(self):
        """One browser operation at a time (agents and you share one window); waiting is bounded like pacing."""
        if not self._op.acquire(timeout=self.pacer.max_wait_s):
            raise Paced("browser", self.pacer.max_wait_s)
        try:
            yield
        finally:
            self._op.release()

    def _block(self, ctx: CallContext, project: str, ps, site: str, block: Block, url: str, tab: str):
        kind = "captcha_wait" if block.kind == "captcha" else "login_wait"
        label = SITE_LABELS.get(site, site)
        if kind == "captcha_wait":
            prompt = (f"{label} is showing a CAPTCHA or an 'unusual traffic' page ({url}). Solve it in the built-in "
                      "browser window, then answer 'done'. The Kernel never solves CAPTCHAs itself.")
        else:
            prompt = (f"{label} sent the browser to a sign-in page ({url}): your library or publisher session has "
                      "expired. Sign in in the built-in browser window (the Kernel never sees your password), then "
                      "answer 'done'.")
        chk = create_checkpoint(ps, kind, prompt, ["done"], task_id=ctx.task_id, actor="kernel:browser")
        self.pacer.pause(site, block.reason, project=project, checkpoint_id=chk)
        record_browse(ps, task_id=ctx.task_id, site=site, action=kind, url=url, actor=ctx.actor)
        owner = _owner(ctx)
        if owner is not HUMAN:  # the page is yours now (an agent tab cannot post: your sign-in would be refused)
            self.session.hand_over(tab, owner)
            self._tabs = {k: v for k, v in self._tabs.items() if v != tab}
        raise HumanRequired(kind, chk, url, block.reason, tab)

    def _tab_for(self, owner: str | None, key: str) -> str:
        tid = self._tabs.get((owner, key))
        if tid and any(t.id == tid for t in self.session.tabs(owner)):
            return tid
        tid = self._new_tab(owner)
        self._tabs[(owner, key)] = tid
        return tid

    def _new_tab(self, owner: str | None, url: str | None = None) -> str:
        """A new tab; an agent keeps at most ``_MAX_AGENT_TABS`` (its oldest closes first)."""
        if owner is not HUMAN:
            mine = sorted(self.session.tabs(owner), key=lambda t: int(t.id.rsplit("_", 1)[-1]))
            for t in mine[:max(0, len(mine) - _MAX_AGENT_TABS + 1)]:
                self.session.close_tab(t.id, owner)
                self._tabs = {k: v for k, v in self._tabs.items() if v != t.id}
        return self.session.new_tab(owner, url).id

    def _resolve(self, meta: PageMetadata) -> ResolvedWork | None:
        for kind, value in (("doi", meta.doi), ("pmid", meta.pmid), ("arxiv", meta.arxiv)):
            if value:
                try:
                    return resolve(self.kernel.ctx(), Identifier(kind=kind, value=value))
                except (HttpError, OSError, AssertionError):  # AssertionError: no recorded fixture in replay tests
                    return None
        return None

    # ---- search, open, snapshot, act ----------------------------------------------------------------------------
    def search(self, ctx: CallContext, project: str, site: str, query: str, max_results: int = 10) -> dict:
        adapter = self.adapters.get(site)
        if adapter is None:
            raise ToolError("unknown_site", f"no adapter for {site}", {"sites": sorted(self.adapters)}, status=404)
        url = adapter.search_url(query, self.kernel.settings())
        with self._busy():
            ps, _ = self._project(project)
            self._check_pause(site)
            self.pacer.acquire([f"site:{site}"])
            owner = _owner(ctx)
            tab = self._tab_for(owner, f"search:{site}")
            self.session.goto(tab, owner, url)
            self.session.wait_for(tab, owner, adapter.ready_selector, 20)
            state = self.session.content(tab, owner)
            if block := detect_block(state.url, state.html, proxy_host=self._proxy_host()):
                self._block(ctx, project, ps, site, block, state.url, tab)
            hits = adapter.parse(state.html, state.url)
            if not valid(adapter, hits, state.html):
                record_browse(ps, task_id=ctx.task_id, site=site, action="search", url=state.url, query=query,
                              actor=ctx.actor)
                return {"site": site, "query": query, "tab": tab, "url": state.url, "hits": [], "fallback": "generic",
                        "reason": f"the {site} results page did not match the adapter; use rk_browser_snapshot and "
                                  "rk_browser_act on this tab", "snapshot": self.snapshot(ctx, project, tab)}
            hits = hits[:max_results]
            record_browse(ps, task_id=ctx.task_id, site=site, action="search", url=state.url, query=query,
                          result_count=len(hits), actor=ctx.actor)
            return {"site": site, "query": query, "tab": tab, "url": state.url,
                    "hits": [h.model_dump(exclude_none=True) for h in hits]}

    def open(self, ctx: CallContext, project: str, url: str, tab: str | None = None) -> dict:
        with self._busy():
            ps, _ = self._project(project)
            owner = _owner(ctx)
            site = self._site_of(url)  # a Scholar page is paused and paced as Scholar, whichever tool opens it
            if owner is not HUMAN:
                self._check_pause(site)
                self.pacer.acquire([f"site:{site}"])
            if tab:
                state = self.session.goto(tab, owner, url)
            else:
                tab = self._new_tab(owner, url)
                state = self.session.content(tab, owner)
            if owner is not HUMAN and (block := detect_block(state.url, state.html, proxy_host=self._proxy_host())):
                self._block(ctx, project, ps, site, block, state.url, tab)
            meta = parse_page_metadata(state.html, state.url)
            record_browse(ps, task_id=ctx.task_id, site=site, action="open", url=state.url, actor=ctx.actor)
            return {"tab": tab, "url": state.url, "title": state.title, "status": state.status,
                    "metadata": meta.model_dump(exclude_none=True) if meta.sources else None}

    def snapshot(self, ctx: CallContext, project: str, tab: str) -> dict:
        with self._busy():
            ps, _ = self._project(project)
            shot = ps.root / "browser" / f"{tab}-{int(time.time() * 1000)}.png"
            snap = self.session.snapshot(tab, _owner(ctx), screenshot_to=shot)
            if len(snap["ax"]) > _AX_LIMIT:
                snap["ax"] = snap["ax"][:_AX_LIMIT] + "\n… (accessibility tree truncated)"
            return snap

    def act(self, ctx: CallContext, project: str, tab: str, action: dict) -> dict:
        with self._busy():
            ps, _ = self._project(project)
            owner = _owner(ctx)
            if owner is not HUMAN:
                limit = self.kernel.settings().browser_max_steps
                key = (owner, None) if ctx.task_id else (owner, tab)  # no task: each tab is its own budget
                used = self._steps.get(key, 0)
                if used >= limit:
                    raise ToolError("step_limit", f"this task used its {limit} browser actions", {"limit": limit},
                                    status=429)
                self._steps[key] = used + 1
            before = self.session.get_tab(tab, owner).url
            site = self._site_of(before)
            if owner is not HUMAN:  # you may act on a paused site: that is how a pause gets resolved
                self._check_pause(site)
                if action.get("kind") in _NAVIGATING:
                    self.pacer.acquire([f"site:{site}"])
            hosts = self._site_hosts()
            self.session.submit_hosts = set(hosts)
            state = self.session.act(tab, owner, action, allow_submit=urlsplit(before).netloc.lower() in hosts)
            if owner is not HUMAN and (block := detect_block(state.url, state.html, proxy_host=self._proxy_host())):
                self._block(ctx, project, ps, site, block, state.url, tab)
            return {"tab": tab, "url": state.url, "title": state.title}

    def tabs(self, ctx: CallContext) -> list[dict]:
        return [{"tab": t.id, "url": t.url, "owner": "human" if t.owner is HUMAN else t.owner}
                for t in self.session.tabs(_owner(ctx))]

    # ---- full text ----------------------------------------------------------------------------------------------
    def _landing(self, item) -> str:
        if item.ids.doi:
            return self.doi_base + item.ids.doi
        if item.ids.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{item.ids.pmid}/"
        if item.ids.arxiv:
            return f"https://arxiv.org/abs/{item.ids.arxiv}"
        if item.ids.url:
            return item.ids.url
        raise ToolError("no_identifier", "the item has no DOI, PMID, arXiv id or URL to open", status=422)

    def _download(self, ctx, project, ps, tab, owner, page_url: str, html: str, site: str, *,
                  click: bool = True) -> tuple[bytes, str] | None:
        """The PDF behind an article page, or None. Raises ``human_required`` on a sign-in or CAPTCHA response."""
        pdf_url = find_pdf_url(page_url, html)
        if not pdf_url:
            return None
        self.pacer.acquire([f"pdf:{registrable_domain(urlsplit(pdf_url).hostname or '')}", "pdf:*"])
        _status, _ctype, body, final = self.session.fetch_bytes(tab, owner, pdf_url, referer=page_url)
        if body[:5] == b"%PDF-":
            return body, pdf_url
        if block := detect_block(final, body[:200_000].decode("utf-8", "replace"), proxy_host=self._proxy_host()):
            self._block(ctx, project, ps, site, block, final, tab)
        path = urlsplit(pdf_url).path
        if click and path and not re.search(r"[\"'\\\]]", path):  # some sites serve the PDF only as a download
            dl = self.session.click_download(tab, owner, f'a[href*="{path}"]', timeout_s=15)
            if dl and dl[:5] == b"%PDF-":
                return dl, pdf_url
        raise ToolError("pdf_not_obtained", "the PDF link did not return a PDF (paywall or interstitial page?)",
                        {"final_url": final, "landing_url": page_url, "pdf_url": pdf_url}, status=404)

    def fetch_fulltext(self, ctx: CallContext, project: str, item_id: str) -> FulltextResult:
        with self._busy():
            return self._fetch_fulltext(ctx, project, item_id)

    def _fetch_fulltext(self, ctx: CallContext, project: str, item_id: str) -> FulltextResult:
        ps, lib = self._project(project)
        try:
            item = lib.get(item_id)
        except KeyError as e:
            raise ToolError("not_found", f"no item {item_id}", status=404) from e
        landing, via_library = self._proxied(self._landing(item))
        site = "library" if via_library else "publisher"
        self._check_pause(site)
        self.pacer.acquire([f"site:{site}"])
        owner = _owner(ctx)
        tab = self._new_tab(owner)
        try:
            state = self.session.goto(tab, owner, landing)
            if block := detect_block(state.url, state.html, proxy_host=self._proxy_host()):
                self._block(ctx, project, ps, site, block, state.url, tab)
            record_browse(ps, task_id=ctx.task_id, site=site, action="open", url=state.url, actor=ctx.actor)
            got = self._download(ctx, project, ps, tab, owner, state.url, state.html, site)
            if got is None:
                raise ToolError("pdf_not_obtained", "no PDF link on the article page",
                                {"final_url": state.url, "landing_url": landing}, status=404)
            body, pdf_url = got
            s = self.kernel.settings()
            note = (f"Obtained via {s.library_name or 'the library'} in the built-in browser; personal research use "
                    "only" if via_library else "Obtained from the publisher site in the built-in browser; personal "
                    "research use only")
            res = attach_pdf_bytes(ps, lib, item_id, body, origin="library_browser" if via_library else
                                   "publisher_browser", actor=ctx.actor, actor_kind=_actor_kind(ctx),
                                   access_note=note, url=pdf_url)
            record_browse(ps, task_id=ctx.task_id, site=site, action="download", url=pdf_url, actor=ctx.actor)
        except HumanRequired:
            raise  # the tab is now yours, open at the page that needs you
        except BaseException:
            self.session.close_tab(tab, owner)
            raise
        self.session.close_tab(tab, owner)
        return res

    # ---- save the page you are looking at (§8.6) --------------------------------------------------------------
    def save_page(self, ctx: CallContext, project: str, tab: str | None = None, collection: str | None = None) -> dict:
        with self._busy():
            return self._save_page(ctx, project, tab, collection)

    def _save_page(self, ctx: CallContext, project: str, tab: str | None, collection: str | None) -> dict:
        ps, lib = self._project(project)
        if collection and not any(c["id"] == collection for c in lib.collections()):
            raise ToolError("not_found", f"no collection {collection}", status=404)
        t = self.session.get_tab(tab, HUMAN) if tab else self.session.active_human_tab()
        if t is None:
            raise ToolError("no_tab", "open the article in the built-in browser first", status=404)
        state = self.session.content(t.id, HUMAN)
        meta = parse_page_metadata(state.html, state.url)
        s = self.kernel.settings()
        if s.translation_server_url and (csl := TranslationServer(s.translation_server_url).csl_for(state.url)):
            z = from_csl(csl)
            meta = meta.model_copy(update={"title": z.title, "creators": z.creators or meta.creators,
                                           "year": z.year or meta.year, "venue": z.venue or meta.venue,
                                           "doi": z.ids.doi or meta.doi, "item_type": z.item_type,
                                           "sources": [*meta.sources, "translation_server"]})
        resolved = self._resolve(meta)
        item, diff = merge_with_api(meta, resolved)
        added = lib.add_item(item, added_by="human" if ctx.role == "cli" else "agent", actor=ctx.actor,
                             provenance={"page_metadata_diff": diff} if diff else None)
        if resolved is None and added.created:  # an item already in the library keeps its reference check
            lib.update(added.item_id, ref_status="page_metadata_only", actor=ctx.actor, actor_kind=_actor_kind(ctx))
        via_library = bool(self._proxy_host()) and urlsplit(state.url).hostname == self._proxy_host()
        origin = "library_browser" if via_library else "publisher_browser"
        try:  # never clicks in your tab: only a PDF the page links to is fetched
            got = self._download(ctx, project, ps, t.id, HUMAN, state.url, state.html, "publisher", click=False)
        except ToolError as e:
            if e.type == "human_required":
                raise
            got = None
        if got:
            res = attach_pdf_bytes(ps, lib, added.item_id, got[0], origin=origin, actor=ctx.actor,
                                   actor_kind=_actor_kind(ctx), url=got[1],
                                   access_note="Saved from the built-in browser; personal research use only")
            attachment = {"kind": "pdf", "snapshot_sha": res.snapshot_sha}
        else:  # a page snapshot becomes the text layer only when the item has none (it never replaces a PDF's)
            att, res = attach_html_snapshot(ps, lib, added.item_id, state.html, origin=origin, actor=ctx.actor,
                                            actor_kind=_actor_kind(ctx), url=state.url,
                                            access_note="Web page snapshot saved from the built-in browser",
                                            text_layer=latest_snapshot(ps, added.item_id) is None)
            attachment = {"id": att, "kind": "html_snapshot", "snapshot_sha": res.snapshot_sha if res else None}
        col = collection or lib.collection_by_name("Search hits")
        if col:
            lib.collect(added.item_id, col, actor=ctx.actor, actor_kind=_actor_kind(ctx))
        record_browse(ps, task_id=ctx.task_id, site=urlsplit(state.url).hostname or "?", action="download",
                      url=state.url, actor=ctx.actor)
        return {"item_id": added.item_id, "created": added.created, "ref_status": lib.get(added.item_id).ref_status,
                "attachment": attachment, "page_metadata_diff": diff}

    # ---- hand-off and status ------------------------------------------------------------------------------------
    def request_human(self, ctx: CallContext, project: str, reason: str, url: str | None = None) -> dict:
        with self._busy():
            ps, _ = self._project(project)
            owner = _owner(ctx)
            tab = None
            if url:
                # an agent's URL opens in a guarded tab first (refused before anything loads, redirects included),
                # which then becomes yours
                tab = self._new_tab(owner, url)
                if owner is not HUMAN:
                    self.session.hand_over(tab, owner)
            prompt = f"{reason}{f' ({url})' if url else ''}. Handle it in the built-in browser window, then answer 'done'."
            chk = create_checkpoint(ps, "login_wait", prompt, ["done"], task_id=ctx.task_id, actor="kernel:browser")
            record_browse(ps, task_id=ctx.task_id, site=(urlsplit(url).hostname if url else None) or "browser",
                          action="login_wait", url=url or "about:blank", actor=ctx.actor)
            return {"checkpoint_id": chk, "tab": tab}

    def status(self, ctx: CallContext) -> dict:
        """Running state, paused sites and today's quotas, plus the caller's own tabs (every tab for the human)."""
        return {"running": self.running, **self.pacer.status(), "tabs": self.tabs(ctx)}
