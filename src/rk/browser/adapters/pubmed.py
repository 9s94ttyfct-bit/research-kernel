"""PubMed web results. Selectors probed live 2026-11-06: ``article.full-docsum``, ``a.docsum-title``,
``.docsum-authors.full-authors``, ``.docsum-journal-citation.full-journal-citation``, ``.docsum-pmid``. A query with a
single match redirects to the abstract page; that page is parsed through its Highwire metadata."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin

from rk.browser.adapters import SearchHit, SiteAdapter, doc_of, doi_in, has_class, text
from rk.browser.metadata import parse_page_metadata

BASE = "https://pubmed.ncbi.nlm.nih.gov/"


def search_url(query: str, settings, page: int = 0) -> str:
    params = {"term": query}
    if page:
        params["page"] = str(page + 1)
    return BASE + "?" + urlencode(params)


def parse(html: str, base_url: str) -> list[SearchHit]:
    hits = []
    for a in doc_of(html).xpath(f"//article[{has_class('full-docsum')}]"):
        link = next(iter(a.xpath(f".//a[{has_class('docsum-title')}]")), None)
        if link is None:
            continue
        cite = text(next(iter(a.xpath(f".//span[{has_class('full-journal-citation')}]")), None))
        hits.append(SearchHit(
            title=text(link), url=urljoin(BASE, link.get("href")),
            authors=text(next(iter(a.xpath(f".//span[{has_class('full-authors')}]")), None)), venue_year=cite,
            snippet=text(next(iter(a.xpath(f".//div[{has_class('full-view-snippet')}]")), None)),
            pmid=text(next(iter(a.xpath(f".//span[{has_class('docsum-pmid')}]")), None)), doi=doi_in(cite),
            source_site="pubmed"))
    if not hits:  # single-result redirect to the abstract page
        meta = parse_page_metadata(html, base_url)
        if meta.pmid and meta.title:
            hits.append(SearchHit(title=meta.title, url=urljoin(BASE, f"/{meta.pmid}/"), pmid=meta.pmid, doi=meta.doi,
                                  authors="; ".join(c.family for c in meta.creators) or None,
                                  venue_year=", ".join(str(x) for x in (meta.venue, meta.year) if x) or None,
                                  source_site="pubmed"))
    return hits


ADAPTER = SiteAdapter("pubmed", "article.full-docsum, .results-amount, h1.heading-title", search_url, parse,
                      re.compile(r"No results were found", re.IGNORECASE))
