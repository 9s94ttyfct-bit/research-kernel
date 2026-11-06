"""Site adapters (spec §8.5): one per search site. Each one builds the search URL and parses the rendered results page
into ``SearchHit`` records. ``valid`` is the self-check: when it fails (the site changed its markup), the browser
service falls back to generic navigation instead of returning a wrong or empty list."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from lxml import html as lhtml
from pydantic import BaseModel

from rk.text.markup import sanitize_html

DOI_IN = re.compile(r"10\.\d{4,9}/[^\s\"'<>?#]+")


class SearchHit(BaseModel):
    title: str
    url: str
    authors: str | None = None
    venue_year: str | None = None
    snippet: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pdf_url: str | None = None
    source_site: str


@dataclass(frozen=True)
class SiteAdapter:
    name: str
    ready_selector: str  # what to wait for after navigating (results, "no results" or a block page)
    search_url: Callable[..., str]  # (query, settings, page=0) -> url
    parse: Callable[[str, str], list[SearchHit]]  # (html, base_url) -> hits
    no_results: re.Pattern


def text(el) -> str | None:
    if el is None:
        return None
    t = " ".join(el.text_content().split())
    return t or None


def doc_of(html: str):
    """Parse the page with hidden text removed (aria-hidden, display:none, hidden, zero-width): what a reader sees,
    and no hidden instructions in result titles or snippets."""
    try:
        return lhtml.fromstring(sanitize_html(html or "<html></html>"))
    except (ValueError, lhtml.etree.ParserError):
        return lhtml.fromstring("<html></html>")


def doi_in(*texts: str | None) -> str | None:
    for t in texts:
        m = DOI_IN.search(t or "")
        if m:
            return m.group(0).rstrip(".,;)").lower()
    return None


def has_class(cls: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {cls} ")'


def valid(adapter: SiteAdapter, hits: list[SearchHit], html: str) -> bool:
    """Hits are well formed, or the page explicitly says there are no results."""
    if hits:
        return all(h.title and h.url.startswith(("http://", "https://")) for h in hits)
    body = doc_of(html).text_content()
    return bool(adapter.no_results.search(body))


def _registry() -> dict[str, SiteAdapter]:
    # imported here: the site modules import the helpers above from this package
    from rk.browser.adapters import europepmc, primo, pubmed, scholar, summon

    return {m.ADAPTER.name: m.ADAPTER for m in (scholar, pubmed, europepmc, primo, summon)}


ADAPTERS: dict[str, SiteAdapter] = _registry()
