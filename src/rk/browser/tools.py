"""The ``rk_browser_*`` tools (spec §8.4). Registered always, so the tool list does not depend on whether this
``rk serve`` started the browser; without it every call fails with ``browser_unavailable``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from rk.browser import BrowserUnavailable
from rk.browser.service import BrowserService
from rk.tools.registry import CallContext

SITES = Literal["scholar", "pubmed", "europepmc", "primo", "summon"]


class Out(BaseModel):
    model_config = {"extra": "allow"}


class SearchArgs(BaseModel):
    project: str
    site: SITES
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(10, ge=1, le=20)


class OpenArgs(BaseModel):
    project: str
    url: str = Field(min_length=1, max_length=4000)
    tab: str | None = None


class TabArgs(BaseModel):
    project: str
    tab: str


class Action(BaseModel):
    kind: Literal["click", "fill", "press", "scroll", "back"]
    selector: str | None = Field(None, max_length=500)
    text: str | None = Field(None, max_length=2000)
    key: str | None = Field(None, max_length=40)


class ActArgs(BaseModel):
    project: str
    tab: str
    action: Action


class ProjectOnly(BaseModel):
    project: str


class SourceArgs(BaseModel):
    project: str
    source_id: str


class HumanArgs(BaseModel):
    project: str
    reason: str = Field(min_length=1, max_length=500)
    url: str | None = Field(None, max_length=4000)


class SaveArgs(BaseModel):
    project: str
    tab: str | None = None


def register_browser_tools(tool: Callable, browser: Callable[[], BrowserService | None]) -> None:
    """``tool(name, summary, input_model, writes)`` is ``build_registry``'s decorator; ``browser()`` returns the
    running service or None."""

    def svc() -> BrowserService:
        s = browser()
        if s is None or not s.running:
            raise BrowserUnavailable("the built-in browser is not running (start rk serve without --no-browser)")
        return s

    @tool("rk_browser_search", "Search Google Scholar, PubMed, Europe PMC or the library catalogue in the built-in "
          "browser at human pace; returns structured hits.", SearchArgs, True)
    def _search(ctx: CallContext, a: SearchArgs) -> Out:
        return Out(**svc().search(ctx, a.project, a.site, a.query, a.max_results))

    @tool("rk_browser_open", "Open a public web page in a tab of this task; returns its page metadata.", OpenArgs, False)
    def _open(ctx: CallContext, a: OpenArgs) -> Out:
        return Out(**svc().open(ctx, a.project, a.url, a.tab))

    @tool("rk_browser_snapshot", "Screenshot and accessibility tree of a tab of this task (for generic navigation).",
          TabArgs, False)
    def _snapshot(ctx: CallContext, a: TabArgs) -> Out:
        return Out(**svc().snapshot(ctx, a.project, a.tab))

    @tool("rk_browser_act", "Click, fill, press a key, scroll or go back in a tab of this task (generic navigation; "
          "step-limited; never fills passwords).", ActArgs, False)
    def _act(ctx: CallContext, a: ActArgs) -> Out:
        return Out(**svc().act(ctx, a.project, a.tab, a.action.model_dump(exclude_none=True)))

    @tool("rk_browser_tabs", "List the browser tabs of this task.", ProjectOnly, False)
    def _tabs(ctx: CallContext, a: ProjectOnly) -> Out:
        return Out(tabs=svc().tabs(ctx))

    @tool("rk_browser_fetch_fulltext", "Get an item's PDF through the library proxy or the publisher page in the "
          "built-in browser and build its text layer.", SourceArgs, True)
    def _fetch(ctx: CallContext, a: SourceArgs) -> Out:
        return Out(**svc().fetch_fulltext(ctx, a.project, a.source_id).model_dump())

    @tool("rk_browser_request_human", "Ask the human to sign in or handle a page in the browser window (opens a "
          "login_wait checkpoint).", HumanArgs, True)
    def _human(ctx: CallContext, a: HumanArgs) -> Out:
        return Out(**svc().request_human(ctx, a.project, a.reason, a.url))

    @tool("rk_browser_status", "Browser state: running, paused sites, today's quotas, open tabs.", ProjectOnly, False)
    def _status(ctx: CallContext, a: ProjectOnly) -> Out:
        return Out(**svc().status(ctx))

    @tool("rk_browser_save_page", "Save the page you are looking at in the built-in browser to the library (metadata, "
          "PDF or page snapshot).", SaveArgs, True)
    def _save(ctx: CallContext, a: SaveArgs) -> Out:
        return Out(**svc().save_page(ctx, a.project, a.tab))
