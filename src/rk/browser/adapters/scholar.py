"""Google Scholar results. Selectors: result ``div.gs_r.gs_or.gs_scl``, title ``h3.gs_rt`` (link ``h3.gs_rt a``), byline
``div.gs_a`` ("authors - venue, year - host"), snippet ``div.gs_rs``, full-text link ``div.gs_or_ggsm a``. A live probe
on 2026-11-06 from headless Chromium got the "automated queries" page instead of results (handled by detect.py), so
these selectors follow Scholar's documented result markup. Citation-only records have no link and are skipped."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin

from rk.browser.adapters import SearchHit, SiteAdapter, doc_of, doi_in, has_class, text


def search_url(query: str, settings, page: int = 0) -> str:
    params = {"hl": "en", "q": query}
    if page:
        params["start"] = str(page * 10)
    return "https://scholar.google.com/scholar?" + urlencode(params)


def parse(html: str, base_url: str) -> list[SearchHit]:
    hits = []
    xp = f"//div[{has_class('gs_r')} and {has_class('gs_or')} and {has_class('gs_scl')}]"
    for r in doc_of(html).xpath(xp):
        link = next(iter(r.xpath(f".//h3[{has_class('gs_rt')}]//a[@href]")), None)
        if link is None:
            continue
        byline = text(next(iter(r.xpath(f".//div[{has_class('gs_a')}]")), None)) or ""
        authors, _, rest = byline.partition(" - ")
        pdf = next(iter(r.xpath(f".//div[{has_class('gs_or_ggsm')}]//a/@href")), None)
        url = urljoin(base_url, link.get("href"))
        hits.append(SearchHit(title=text(link), url=url, authors=authors or None, venue_year=rest or None,
                              snippet=text(next(iter(r.xpath(f".//div[{has_class('gs_rs')}]")), None)),
                              doi=doi_in(url), pdf_url=urljoin(base_url, pdf) if pdf else None, source_site="scholar"))
    return hits


ADAPTER = SiteAdapter("scholar", "#gs_res_ccl_mid, #gs_captcha_ccl, #captcha-form, #gs_top", search_url, parse,
                      re.compile(r"did not match any articles", re.IGNORECASE))
