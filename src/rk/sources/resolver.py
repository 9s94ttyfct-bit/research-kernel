"""Identifier resolution and multi-source search."""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from rk.config import Settings
from rk.library.models import Ids, ItemIn
from rk.net.http import HttpClient
from rk.sources import arxiv, biorxiv, crossref, europepmc, openalex, pubmed, s2
from rk.sources.crossref import CrossrefWork, Update
from rk.sources.hits import Hits, as_hits
from rk.sources.identifiers import Identifier
from rk.sources.openalex import OpenAlexWork

SearchSource = Literal["openalex", "europepmc", "pubmed", "arxiv", "s2"]


class SearchLimits(NamedTuple):
    """What one search call of a source may ask for: ``page``, the most hits per call; ``depth``, how far its paging
    reaches (``offset + limit`` at most; ``None``: no limit)."""
    page: int
    depth: int | None


SEARCH_LIMITS: dict[str, SearchLimits] = {
    "openalex": SearchLimits(200, 10_000),  # per_page <= 200; basic paging reaches the first 10,000 results
    "europepmc": SearchLimits(1000, None),  # pageSize <= 1000; cursor paging goes to the end
    "pubmed": SearchLimits(10_000, 10_000),  # ESearch returns the first 10,000 PMIDs only
    "arxiv": SearchLimits(2000, 30_000),  # the API's max_results per call and its result-set limit
    "s2": SearchLimits(100, 1000),  # /paper/search: limit <= 100, offset + limit <= 1000
}


def search_limits(source: str) -> SearchLimits | None:
    """The source's paging limits; ``None`` for a source without known limits."""
    return SEARCH_LIMITS.get(source)


def check_limits(source: str, limit: int, offset: int) -> None:
    """Refuse (``ValueError``) a call beyond the source's ``SEARCH_LIMITS``: the source would answer HTTP 400."""
    lim = SEARCH_LIMITS.get(source)
    if lim is not None and (limit > lim.page or (lim.depth is not None and offset + limit > lim.depth)):
        raise ValueError(f"{source} returns at most {lim.page} results per call"
                         + (f" and only its first {lim.depth}" if lim.depth is not None else "")
                         + f" (asked for {limit} from {offset})")


class SourceContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    http: HttpClient
    settings: Settings


class ResolvedWork(BaseModel):
    item: ItemIn
    retraction_updates: list[Update] = []
    openalex: OpenAlexWork | None = None
    crossref: CrossrefWork | None = None
    sources_used: list[str] = []


def _merge(base: ItemIn, other: ItemIn) -> ItemIn:
    ids = {**{k: v for k, v in other.ids.model_dump().items() if v}, **{k: v for k, v in base.ids.model_dump().items() if v}}
    upd = {
        "ids": Ids(**ids),
        "oa_urls": base.oa_urls + [u for u in other.oa_urls if u not in base.oa_urls],
        "abstract": base.abstract or other.abstract,
        "venue": base.venue or other.venue,
        "year": base.year or other.year,
        "creators": base.creators or other.creators,
    }
    return base.model_copy(update=upd)


def resolve(ctx: SourceContext, ident: Identifier) -> ResolvedWork | None:
    http, st = ctx.http, ctx.settings
    key = st.openalex_key
    if ident.kind == "doi":
        cr = crossref.get_work(http, ident.value, st.email)
        oa = openalex.get_work(http, ident, key)
        used = [n for n, v in (("crossref", cr), ("openalex", oa)) if v]
        if cr is None and oa is None:
            if ident.value.startswith("10.1101/") and (bx := biorxiv.details(http, ident.value)):
                return ResolvedWork(item=bx, sources_used=["biorxiv"])
            return None
        item = cr.item if cr else oa.item
        if cr and oa:
            item = _merge(cr.item, oa.item)
        return ResolvedWork(item=item, retraction_updates=cr.updates if cr else [], openalex=oa, crossref=cr,
                            sources_used=used)
    if ident.kind in ("pmid", "pmcid", "openalex"):
        oa = openalex.get_work(http, ident, key)
        if oa:
            cr = crossref.get_work(http, oa.item.ids.doi, st.email) if oa.item.ids.doi else None
            item = _merge(cr.item, oa.item) if cr else oa.item
            return ResolvedWork(item=item, retraction_updates=cr.updates if cr else [], openalex=oa, crossref=cr,
                                sources_used=["openalex"] + (["crossref"] if cr else []))
        if ident.kind == "pmid":
            items = pubmed.esummary(http, [ident.value])
            return ResolvedWork(item=items[0], sources_used=["pubmed"]) if items else None
        return None
    if ident.kind == "arxiv":
        try:
            items = arxiv.query(http, id_list=[ident.value], max_results=1)
        except arxiv.ArxivError:  # arXiv rejects the id (e.g. its format): a final answer, not an outage
            return None
        if not items:
            return None
        item = items[0]
        oa = openalex.get_work(http, Identifier(kind="doi", value=f"10.48550/arxiv.{ident.value}"), key)
        if oa:
            item = _merge(item, oa.item)
        return ResolvedWork(item=item, openalex=oa, sources_used=["arxiv"] + (["openalex"] if oa else []))
    return None


def search(ctx: SourceContext, query: str, source: SearchSource, limit: int = 25, *,
           after_year: int | None = None, offset: int = 0, date_from: int | None = None,
           date_to: int | None = None) -> Hits:
    """Search one source: ``limit`` hits from ``offset``, within the source's ``SEARCH_LIMITS`` (else
    ``ValueError``; OpenAlex and Europe PMC page, so there ``offset`` must be a multiple of ``limit``).
    ``date_from``..``date_to`` (publication years, inclusive, either end open) is filtered on every source's server.
    With ``after_year`` only works published in a later year are returned: OpenAlex filters on the server; the other
    sources' hits are filtered here, so fewer than ``limit`` may be left (undated hits drop). An empty year range
    (``date_from`` after ``date_to``, or ``after_year`` at or after ``date_to``) has no hits. ``Hits.dropped`` counts
    the page's entries the source returned but that could not be read, ``Hits.total`` the query's results (arXiv's)."""
    check_limits(source, limit, offset)
    lo = max((y for y in (date_from, after_year + 1 if after_year is not None else None) if y is not None),
             default=None)
    if lo is not None and date_to is not None and lo > date_to:
        return Hits()
    hits = as_hits(_search(ctx, query, source, limit, after_year, offset, date_from, date_to))
    kept = hits if after_year is None else [h for h in hits if h.year is not None and h.year > after_year]
    return Hits(kept, hits.dropped, hits.total)


def _search(ctx: SourceContext, query: str, source: SearchSource, limit: int, after_year: int | None, offset: int,
            date_from: int | None, date_to: int | None) -> list[ItemIn]:
    http, st = ctx.http, ctx.settings
    if source == "openalex":
        years = openalex.publication_years(after_year, date_from, date_to)
        return openalex.search(http, query, api_key=st.openalex_key, per_page=limit, offset=offset,
                               filters={"publication_year": years} if years else None)
    if source == "europepmc":
        return europepmc.search(http, query, page_size=limit, offset=offset, date_from=date_from, date_to=date_to)
    if source == "pubmed":
        pmids = pubmed.esearch(http, query, retmax=limit, retstart=offset, date_from=date_from, date_to=date_to)
        return pubmed.summaries(http, pmids)  # a page is as full as its PMID list: a missing summary is dropped
    if source == "arxiv":
        q = f"all:{query}"
        if date_from is not None or date_to is not None:
            q += (f" AND submittedDate:[{date_from if date_from is not None else 1000}01010000 TO "
                  f"{date_to if date_to is not None else 9999}12312359]")
        return arxiv.query(http, search_query=q, max_results=limit, start=offset)
    if source == "s2":
        return s2.search(http, query, api_key=st.s2_key, limit=limit, offset=offset, date_from=date_from,
                         date_to=date_to)
    raise ValueError(f"unknown source {source}")
