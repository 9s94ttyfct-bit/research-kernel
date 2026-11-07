"""Identifier resolution and multi-source search."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from rk.config import Settings
from rk.library.models import Ids, ItemIn
from rk.net.http import HttpClient
from rk.sources import arxiv, biorxiv, crossref, europepmc, openalex, pubmed, s2
from rk.sources.crossref import CrossrefWork, Update
from rk.sources.identifiers import Identifier
from rk.sources.openalex import OpenAlexWork

SearchSource = Literal["openalex", "europepmc", "pubmed", "arxiv", "s2"]


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
        items = arxiv.query(http, id_list=[ident.value], max_results=1)
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
           date_to: int | None = None) -> list[ItemIn]:
    """Search one source: ``limit`` hits from ``offset`` (OpenAlex and Europe PMC page, so there ``offset`` must be a
    multiple of ``limit``). ``date_from``..``date_to`` (publication years, inclusive, either end open) is filtered on
    every source's server. With ``after_year`` only works published in a later year are returned: OpenAlex filters on
    the server; the other sources' hits are filtered here, so fewer than ``limit`` may be left (undated hits drop)."""
    hits = _search(ctx, query, source, limit, after_year, offset, date_from, date_to)
    if after_year is None:
        return hits
    return [h for h in hits if h.year is not None and h.year > after_year]


def _openalex_years(after_year: int | None, date_from: int | None, date_to: int | None) -> str | None:
    """The OpenAlex ``publication_year`` filter of a year range and ``after_year``."""
    if date_from is None and date_to is None:
        return f">{after_year}" if after_year is not None else None
    lows = [y for y in (date_from, after_year + 1 if after_year is not None else None) if y is not None]
    if not lows:
        return f"<{date_to + 1}"
    return f"{max(lows)}-{date_to}" if date_to is not None else f">{max(lows) - 1}"


def _search(ctx: SourceContext, query: str, source: SearchSource, limit: int, after_year: int | None, offset: int,
            date_from: int | None, date_to: int | None) -> list[ItemIn]:
    http, st = ctx.http, ctx.settings
    if source == "openalex":
        years = _openalex_years(after_year, date_from, date_to)
        return openalex.search(http, query, api_key=st.openalex_key, per_page=limit, offset=offset,
                               filters={"publication_year": years} if years else None)
    if source == "europepmc":
        return europepmc.search(http, query, page_size=limit, offset=offset, date_from=date_from, date_to=date_to)
    if source == "pubmed":
        return pubmed.esummary(http, pubmed.esearch(http, query, retmax=limit, retstart=offset, date_from=date_from,
                                                    date_to=date_to))
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
