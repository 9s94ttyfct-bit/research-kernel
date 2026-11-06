"""The source facade of the discovery stages: ``DiscoverySources`` and ``KernelSources`` over the source adapters,
item verification and full-text channels, plus ``log_and_search`` (every discovery search is logged)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from rk.discovery.store import log_search
from rk.fulltext import fetch_fulltext, latest_snapshot
from rk.library.models import ItemIn
from rk.library.service import Library
from rk.sources import openalex, resolver
from rk.sources.identifiers import Identifier, parse_identifier
from rk.store.project import ProjectStore
from rk.tools.registry import CallContext, ToolError
from rk.verify.records import best_identifier, verify_item
from rk.verify.retract import RetractionInfo, retraction_status

if TYPE_CHECKING:
    from rk.browser.service import BrowserService

HITS_COLLECTION = "Search hits"


class DiscoverySources(Protocol):
    def search(self, query: str, source: str, limit: int = 25) -> list[ItemIn]: ...

    def sources(self) -> list[str]: ...

    def year_counts(self, query: str) -> dict[int, int]: ...

    def verify(self, item_id: str) -> str: ...

    def cited_by(self, item_id: str, after_year: int | None) -> list[ItemIn]: ...

    def retraction(self, item_id: str) -> RetractionInfo: ...

    def fulltext(self, item_id: str) -> bool: ...


class KernelSources:
    """``DiscoverySources`` over the kernel's source adapters. ``browser`` is the built-in browser service, or a
    callable returning it (or ``None``), used as the last full-text channel while it runs."""

    def __init__(self, kernel, project: str,
                 browser: BrowserService | Callable[[], BrowserService | None] | None = None, *,
                 actor: str = "kernel"):
        self.kernel, self.project, self.actor = kernel, project, actor
        self._browser = browser

    def _lib(self) -> tuple[ProjectStore, Library]:
        return self.kernel.project(self.project)

    def sources(self) -> list[str]:
        """Usable search sources in preference order; Semantic Scholar only with an API key."""
        s2 = ["s2"] if self.kernel.settings().s2_key else []
        return ["openalex", *s2, "europepmc", "pubmed", "arxiv"]

    def search(self, query: str, source: str, limit: int = 25) -> list[ItemIn]:
        return resolver.search(self.kernel.ctx(), query, source, limit)

    def year_counts(self, query: str) -> dict[int, int]:
        ctx = self.kernel.ctx()
        return openalex.year_counts(ctx.http, query, api_key=ctx.settings.openalex_key)

    def verify(self, item_id: str) -> str:
        """V-REF and V-RETRACT for the item (recorded, and written to the item); returns its ``ref_status``."""
        ps, lib = self._lib()
        ref, _ = verify_item(ps, lib, self.kernel.ctx(), item_id, actor=self.actor)
        return ref.verdict

    def cited_by(self, item_id: str, after_year: int | None) -> list[ItemIn]:
        """OpenAlex works citing the item, published after ``after_year`` when given; ``[]`` when the item has no
        OpenAlex record."""
        _, lib = self._lib()
        item = lib.get(item_id)
        ctx = self.kernel.ctx()
        oa_id = item.ids.openalex
        if not oa_id:
            ident = next((Identifier(kind=k, value=v) for k in ("doi", "pmid", "pmcid") if (v := getattr(item.ids, k))),
                         None)
            work = openalex.get_work(ctx.http, ident, ctx.settings.openalex_key) if ident else None
            oa_id = work.item.ids.openalex if work else None
        if not oa_id:
            return []
        return openalex.cited_by(ctx.http, oa_id, api_key=ctx.settings.openalex_key, after_year=after_year)

    def retraction(self, item_id: str) -> RetractionInfo:
        """V-RETRACT (Crossref update notices and OpenAlex ``is_retracted``) without writing anything."""
        _, lib = self._lib()
        ident = best_identifier(lib.get(item_id).ids)
        resolved = resolver.resolve(self.kernel.ctx(), parse_identifier(ident)) if ident else None
        return retraction_status(resolved)

    def fulltext(self, item_id: str) -> bool:
        """Whether the item has a text layer: an existing one, else the open-access API channels, else the built-in
        browser when it runs and the browser fallback is on."""
        ps, lib = self._lib()
        if latest_snapshot(ps, item_id) is not None:
            return True
        if fetch_fulltext(ps, lib, self.kernel.ctx(), item_id, actor=self.actor) is not None:
            return True
        svc = self._browser() if callable(self._browser) else self._browser
        if svc is None or not svc.running or not self.kernel.settings().fulltext_browser_fallback:
            return False
        ctx = CallContext(role="kernel", actor=self.actor, project=self.project)
        try:
            svc.fetch_fulltext(ctx, self.project, item_id)
        except ToolError:  # no PDF obtained, or the page needs you (a login_wait checkpoint is open)
            return False
        return True


def log_and_search(ps: ProjectStore, sources: DiscoverySources, *, stage: str, attempt: int, purpose: str,
                   query: str, source: str, limit: int, actor: str) -> list[str]:
    """Search one source, add the hits to the library (collection "Search hits"), log the search with the hit item
    ids (event ``search.logged``) and return those ids in rank order."""
    lib = Library(ps)
    hits = sources.search(query, source, limit)
    col = lib.ensure_default_collections(actor=actor)[HITS_COLLECTION]
    ids: list[str] = []
    for it in hits:
        iid = lib.add_item(it.model_copy(update={"discovered_via": "search"}), added_by="agent", actor=actor,
                           actor_kind="kernel").item_id
        if iid not in ids:
            lib.collect(iid, col, actor=actor, actor_kind="kernel")
            ids.append(iid)
    log_search(ps, stage=stage, attempt=attempt, purpose=purpose, source=source, query=query, limit=limit,
               item_ids=ids, actor=actor)
    return ids
