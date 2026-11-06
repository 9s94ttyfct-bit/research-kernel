"""Ex Libris Summon. Search URL ``<base>/#!/search?q=<q>&pn=<n>``. Rendered results probed live 2026-11-06:
``div.documentSummary``, title link ``div.title a``, ``div.authors`` ("by A; B ; More..."), ``.snippet``."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from rk.browser.adapters import SearchHit, SiteAdapter, doc_of, has_class, text
from rk.tools.registry import ToolError


def search_url(query: str, settings, page: int = 0) -> str:
    if not settings.summon_base:
        raise ToolError("not_configured", "set summon_base (rk setup, library step) to search Summon",
                        {"site": "summon"})
    return f"{settings.summon_base.rstrip('/')}/#!/search?q={quote(query)}&pn={page + 1}"


def _authors(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"^by\s+", "", s)
    s = re.sub(r"\s*;?\s*More\.\.\.$", "", s)
    return re.sub(r"\s+;", ";", s).strip() or None


def parse(html: str, base_url: str) -> list[SearchHit]:
    hits = []
    for d in doc_of(html).xpath(f"//div[{has_class('documentSummary')}]"):
        link = next(iter(d.xpath(f".//div[{has_class('title')}]//a[@href]")), None)
        if link is None:
            continue
        hits.append(SearchHit(title=text(link), url=urljoin(base_url, link.get("href")),
                              authors=_authors(text(next(iter(d.xpath(f".//div[{has_class('authors')}]")), None))),
                              snippet=text(next(iter(d.xpath(f".//*[{has_class('snippet')}]")), None)),
                              source_site="summon"))
    return hits


ADAPTER = SiteAdapter("summon", "div.documentSummary, .noResults, .no-results", search_url, parse,
                      re.compile(r"returned no results|No results found", re.IGNORECASE))
