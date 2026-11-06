"""Europe PMC web results. Europe PMC answers headless Chromium with a Cloudflare interstitial (probed 2026-11-06), so
this adapter does not depend on CSS classes: every result title links to ``/article/<SOURCE>/<ID>`` (MED = PubMed id,
PMC, PPR = preprint, …), which is the site's stable permalink scheme."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin, urlsplit

from rk.browser.adapters import SearchHit, SiteAdapter, doc_of, text

BASE = "https://europepmc.org/"
_ARTICLE = re.compile(r"^/article/(MED|PMC|PPR|AGR|CBA|CTX|ETH|HIR|PAT|NBK)/([A-Za-z0-9]+)/?$")


def search_url(query: str, settings, page: int = 0) -> str:
    params = {"query": query}
    if page:
        params["page"] = str(page + 1)
    return BASE + "search?" + urlencode(params)


def parse(html: str, base_url: str) -> list[SearchHit]:
    hits, seen = [], set()
    for a in doc_of(html).xpath("//a[@href]"):
        path = urlsplit(a.get("href")).path
        m = _ARTICLE.match(path)
        title = text(a)
        if not m or not title or path in seen or urlsplit(a.get("href")).fragment:
            continue
        seen.add(path)
        hits.append(SearchHit(title=title, url=urljoin(BASE, path), pmid=m.group(2) if m.group(1) == "MED" else None,
                              source_site="europepmc"))
    return hits


ADAPTER = SiteAdapter("europepmc", 'a[href*="/article/"], .search-no-results, #challenge-error-text', search_url,
                      parse, re.compile(r"No results (?:were )?found|returned 0 results", re.IGNORECASE))
