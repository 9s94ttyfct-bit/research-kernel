"""Ex Libris Primo VE (the default library discovery system, spec §13 item 3). Search URL
``<base>/discovery/search?query=any,contains,<q>&tab=<tab>&search_scope=<scope>&vid=<vid>&offset=<n>``. Rendered brief
results probed live 2026-11-06: ``prm-brief-result-container``, title ``h3.item-title`` (link ``.item-title a``),
``.item-detail`` lines (creators first, then edition/publication), ``.media-content-type``."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from rk.browser.adapters import SearchHit, SiteAdapter, doc_of, doi_in, has_class, text
from rk.tools.registry import ToolError


def search_url(query: str, settings, page: int = 0) -> str:
    if not (settings.primo_base and settings.primo_vid):
        raise ToolError("not_configured", "set primo_base and primo_vid (rk setup, library step) to search Primo",
                        {"site": "primo"})
    base = settings.primo_base.rstrip("/")
    return (f"{base}/discovery/search?query=any,contains,{quote(query)}&tab={quote(settings.primo_tab)}"
            f"&search_scope={quote(settings.primo_scope)}&vid={quote(settings.primo_vid, safe=':')}&offset={page * 10}")


def parse(html: str, base_url: str) -> list[SearchHit]:
    hits = []
    for r in doc_of(html).xpath("//prm-brief-result-container"):
        title_el = next(iter(r.xpath(f".//h3[{has_class('item-title')}]")), None)
        link = next(iter(r.xpath(f".//*[{has_class('item-title')}]//a[@href]")), None)
        if title_el is None or link is None:
            continue
        details = [t for d in r.xpath(f".//*[{has_class('item-detail')}]") if (t := text(d))]
        hits.append(SearchHit(title=text(title_el), url=urljoin(base_url, link.get("href")),
                              authors=details[0] if len(details) > 1 else None,
                              venue_year=details[-1] if details else None,
                              snippet=text(next(iter(r.xpath(f".//*[{has_class('media-content-type')}]")), None)),
                              doi=doi_in(r.xpath("string(.//@data-recordid)")), source_site="primo"))
    return hits


ADAPTER = SiteAdapter("primo", "prm-brief-result-container, prm-no-search-result", search_url, parse,
                      re.compile(r"No records found", re.IGNORECASE))
