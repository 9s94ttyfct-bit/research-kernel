"""The source facade of the discovery stages: ``DiscoverySources`` and ``KernelSources`` over the source adapters,
item verification and full-text channels, plus ``log_and_search`` (every discovery search is logged)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from rk.discovery.store import SearchNote, log_search
from rk.fulltext import fulltext_with_fallback, latest_snapshot
from rk.library.add import add_source
from rk.library.models import ItemIn
from rk.library.service import Library
from rk.sources import europepmc, openalex, pubmed, resolver
from rk.sources.hits import Hits, as_hits
from rk.sources.identifiers import Identifier, parse_identifier
from rk.store.project import ProjectStore
from rk.tools.registry import CallContext, ToolError
from rk.verify.records import best_identifier, verify_item
from rk.verify.retract import RetractionInfo, retraction_status

if TYPE_CHECKING:
    from rk.browser.service import BrowserService

HITS_COLLECTION = "Search hits"
CITED_BY_LIMIT = 25  # citing works per cited_by lookup (one OpenAlex page)
COUNTED_SOURCES = ("openalex", "europepmc", "pubmed")  # the sources ``count`` can size a query on
ABSTRACT_SOURCES = ("pubmed", "europepmc")  # the sources ``abstracts`` looks abstracts up in, by PMID and by DOI


class DiscoverySources(Protocol):
    def search(self, query: str, source: str, limit: int = 25, after_year: int | None = None, *, offset: int = 0,
               date_from: int | None = None, date_to: int | None = None) -> list[ItemIn]:
        """``limit`` hits of one source from rank ``offset`` (a multiple of ``limit``), published after
        ``after_year`` and in the years ``date_from``..``date_to`` (inclusive; None: open) when given. A
        ``rk.sources.hits.Hits`` also counts the page's entries the source returned but could not read."""
        ...

    def sources(self) -> list[str]: ...

    def year_counts(self, query: str) -> dict[int, int]: ...

    def count(self, query: str, source: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
        """How many works ``query`` matches on ``source`` (one of ``COUNTED_SOURCES``; another raises
        ``ValueError``), published in the years ``date_from``..``date_to`` (inclusive; None: open) when given;
        ``None`` when the source gave no count."""
        ...

    def abstracts(self, source: str, ids: list[str]) -> dict[str, str]:
        """The abstracts ``source`` (one of ``ABSTRACT_SOURCES``; another raises ``ValueError``) has for ``ids``
        (PMIDs for ``pubmed``, DOIs for ``europepmc``), by id as given; an id without one is left out."""
        ...

    def verify(self, item_id: str) -> str: ...

    def cited_by(self, item_id: str, after_year: int | None) -> list[ItemIn]: ...

    def retraction(self, item_id: str) -> RetractionInfo: ...

    def fulltext(self, item_id: str, why: list[str] | None = None) -> bool: ...

    def add(self, identifier: str) -> str | None: ...


class KernelSources:
    """``DiscoverySources`` over the kernel's source adapters. ``browser`` is the built-in browser service, or a
    callable returning it (or ``None``), used as the last full-text channel while it runs."""

    def __init__(self, kernel, project: str,
                 browser: BrowserService | Callable[[], BrowserService | None] | None = None, *,
                 actor: str = "kernel"):
        self.kernel, self.project, self.actor = kernel, project, actor
        self._browser: Callable[[], BrowserService | None] = browser if callable(browser) else (lambda: browser)

    def _lib(self) -> tuple[ProjectStore, Library]:
        return self.kernel.project(self.project)

    def sources(self) -> list[str]:
        """Usable search sources in preference order; Semantic Scholar only with an API key."""
        s2 = ["s2"] if self.kernel.settings().s2_key else []
        return ["openalex", *s2, "europepmc", "pubmed", "arxiv"]

    def search(self, query: str, source: str, limit: int = 25, after_year: int | None = None, *, offset: int = 0,
               date_from: int | None = None, date_to: int | None = None) -> list[ItemIn]:
        """Hits of one source from rank ``offset``; with ``after_year`` only works published in a later year, with
        ``date_from``/``date_to`` only those published in that year range (inclusive)."""
        return resolver.search(self.kernel.ctx(), query, source, limit, after_year=after_year, offset=offset,
                               date_from=date_from, date_to=date_to)

    def year_counts(self, query: str) -> dict[int, int]:
        """OpenAlex works matching ``query`` in their titles, abstracts or keywords, per publication year."""
        ctx = self.kernel.ctx()
        return openalex.year_counts(ctx.http, query, api_key=ctx.settings.openalex_key)

    def count(self, query: str, source: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
        """OpenAlex ``meta.count`` (titles, abstracts and keywords), Europe PMC ``hitCount`` (titles and abstracts) or
        PubMed esearch ``count`` (the query as given) of the query in the years ``date_from``..``date_to`` when given,
        filtered on the source's server (``None`` when the response has none)."""
        ctx = self.kernel.ctx()
        if source == "openalex":
            return openalex.count(ctx.http, query, api_key=ctx.settings.openalex_key, date_from=date_from,
                                  date_to=date_to)
        if source == "europepmc":
            return europepmc.count(ctx.http, query, date_from=date_from, date_to=date_to)
        if source == "pubmed":
            return pubmed.count(ctx.http, query, date_from=date_from, date_to=date_to)
        raise ValueError(f"no record count for source {source!r}; counted: {', '.join(COUNTED_SOURCES)}")

    def abstracts(self, source: str, ids: list[str]) -> dict[str, str]:
        """PubMed ``efetch`` abstracts by PMID, or Europe PMC abstracts by DOI."""
        http = self.kernel.ctx().http
        if source == "pubmed":
            return pubmed.abstracts(http, ids)
        if source == "europepmc":
            return europepmc.abstracts(http, ids)
        raise ValueError(f"no abstract lookup in source {source!r}; looked up in: {', '.join(ABSTRACT_SOURCES)}")

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
        return openalex.cited_by(ctx.http, oa_id, api_key=ctx.settings.openalex_key, per_page=CITED_BY_LIMIT,
                                 after_year=after_year)

    def retraction(self, item_id: str) -> RetractionInfo:
        """V-RETRACT (Crossref update notices and OpenAlex ``is_retracted``) without writing anything; ``date`` is the
        Crossref notice date when there is one."""
        _, lib = self._lib()
        ident = best_identifier(lib.get(item_id).ids)
        resolved = resolver.resolve(self.kernel.ctx(), parse_identifier(ident)) if ident else None
        return retraction_status(resolved)

    def fulltext(self, item_id: str, why: list[str] | None = None) -> bool:
        """Whether the item has a text layer: an existing one, else the open-access API channels, else the built-in
        browser when it runs and the browser fallback is on. Without one, each source tried and why it gave none is
        added to ``why`` (``rk.fulltext.fulltext_with_fallback``)."""
        ps, lib = self._lib()
        if latest_snapshot(ps, item_id) is not None:
            return True
        why = [] if why is None else why
        call = CallContext(role="kernel", actor=self.actor, project=self.project)
        try:
            got = fulltext_with_fallback(self.kernel, ps, lib, item_id, call=call, project=self.project,
                                         browser=self._browser, why=why)
        except ToolError as e:  # the browser got no PDF, or the page needs you (a login_wait checkpoint is open)
            why.append(f"built-in browser: {e}")
            return False
        return got is not None

    def add(self, identifier: str) -> str | None:
        """Add a cited work by its identifier (the ``rk_source_add`` path, ``discovered_via="backward"``, not verified
        here); its item id, or ``None`` when the identifier is malformed or resolves to nothing."""
        _, lib = self._lib()
        try:
            res = add_source(self.kernel.ctx(), lib, identifier=identifier, item=None, discovered_via="backward",
                             added_by="agent", actor=self.actor)
        except ToolError:
            return None
        return res.item_id


def _add_and_log(ps: ProjectStore, hits: list[ItemIn], *, discovered_via: str, stage: str, attempt: int,
                 purpose: str, query: str, source: str, limit: int, actor: str, after_year: int | None,
                 offset: int = 0, date_from: int | None = None, date_to: int | None = None,
                 note: Callable[[list[str], Hits], SearchNote] | None = None) -> list[str]:
    """Add the hits to the library (collection "Search hits"), log the search with their distinct item ids and one
    item id per hit (event ``search.logged``, ``hit_ids``: what a resumed search replays), with ``note`` of the hit ids
    and the page (``Hits``: its dropped entries and the query's total) when given, and the number of entries the
    source dropped, ``SearchNote.dropped``) and return one item id per hit in rank order: a hit the library merged into
    an earlier hit's item repeats that item's id."""
    lib = Library(ps)
    col = lib.ensure_default_collections(actor=actor)[HITS_COLLECTION]
    hit_ids: list[str] = []
    for it in hits:
        iid = lib.add_item(it.model_copy(update={"discovered_via": discovered_via}), added_by="agent", actor=actor,
                           actor_kind="kernel").item_id
        if iid not in hit_ids:
            lib.collect(iid, col, actor=actor, actor_kind="kernel")
        hit_ids.append(iid)
    page = as_hits(hits)
    said = (note(hit_ids, page) if note else SearchNote())._replace(dropped=page.dropped)
    log_search(ps, stage=stage, attempt=attempt, purpose=purpose, source=source, query=query, limit=limit,
               item_ids=list(dict.fromkeys(hit_ids)), actor=actor, after_year=after_year, offset=offset,
               date_from=date_from, date_to=date_to, note=said, hit_ids=hit_ids)
    return hit_ids


def log_and_search_hits(ps: ProjectStore, sources: DiscoverySources, *, stage: str, attempt: int, purpose: str,
                        query: str, source: str, limit: int, actor: str, after_year: int | None = None,
                        offset: int = 0, date_from: int | None = None, date_to: int | None = None,
                        note: Callable[[list[str], Hits], SearchNote] | None = None) -> list[str]:
    """``log_and_search`` keeping every hit: one item id per hit in rank order, a hit the library merged into an
    earlier hit's item repeating that id (so a full page has ``limit`` ids, less the entries the source dropped).
    ``note`` gives, from these ids and the source's page (``Hits``), what the logged search says beyond its hits
    (``SearchNote``)."""
    hits = sources.search(query, source, limit, after_year=after_year, offset=offset, date_from=date_from,
                          date_to=date_to)
    return _add_and_log(ps, hits, discovered_via="search", stage=stage, attempt=attempt, purpose=purpose, query=query,
                        source=source, limit=limit, actor=actor, after_year=after_year, offset=offset,
                        date_from=date_from, date_to=date_to, note=note)


def log_and_search(ps: ProjectStore, sources: DiscoverySources, *, stage: str, attempt: int, purpose: str,
                   query: str, source: str, limit: int, actor: str, after_year: int | None = None, offset: int = 0,
                   date_from: int | None = None, date_to: int | None = None) -> list[str]:
    """Search one source (from rank ``offset``; with ``after_year``, only works published in a later year; with
    ``date_from``/``date_to``, only those published in that year range), add the hits to the library, log the search
    and return the distinct hit item ids in rank order."""
    return list(dict.fromkeys(log_and_search_hits(
        ps, sources, stage=stage, attempt=attempt, purpose=purpose, query=query, source=source, limit=limit,
        actor=actor, after_year=after_year, offset=offset, date_from=date_from, date_to=date_to)))


def log_and_cited_by(ps: ProjectStore, sources: DiscoverySources, item_id: str, *, stage: str, attempt: int,
                     purpose: str, actor: str, after_year: int | None = None) -> list[str]:
    """The works citing ``item_id`` (with ``after_year``, only those published in a later year), added to the library
    and logged like a search (source ``openalex``, query ``cites:<item id>``); returns their item ids."""
    hits = sources.cited_by(item_id, after_year=after_year)
    return list(dict.fromkeys(_add_and_log(ps, hits, discovered_via="forward", stage=stage, attempt=attempt,
                                           purpose=purpose, query=f"cites:{item_id}", source="openalex",
                                           limit=CITED_BY_LIMIT, actor=actor, after_year=after_year)))
