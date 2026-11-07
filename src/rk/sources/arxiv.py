"""arXiv API adapter (Atom).

A failed query is a 200 whose feed has one entry with an ``/api/errors`` id (title "Error", the message in its
summary): ``ArxivError``, a ``BadBody`` (a source error, never "no hits"). A 200 whose body is no Atom feed (not XML,
e.g. an HTML error page) is a ``BadBody`` too; either body is dropped from the HTTP cache. An entry without an id or
title is dropped and counted (``Hits.dropped``), so a full page never looks short. ``Hits.total`` is the feed's
``opensearch:totalResults``: a page shorter than asked for that ends before it (arXiv under load) is fetched again
once; still short, it is returned so (the caller's paging says the search is incomplete) and not cached."""

from __future__ import annotations

from lxml import etree

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import BadBody, HttpClient, HttpError
from rk.sources.hits import Hits
from rk.sources.identifiers import normalize_arxiv

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/"}


class ArxivError(BadBody):
    """arXiv answered the query with its error feed; ``message`` is arXiv's."""

    def __init__(self, url: str, message: str):
        super().__init__(url, f"arXiv error: {message}")
        self.message = message


def html_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/html/{arxiv_id}"


def pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}"


def _text(el, path: str) -> str | None:
    found = el.find(path, NS)
    return " ".join(found.text.split()) if found is not None and found.text else None


def _name(full: str) -> Creator:
    parts = full.split()
    return Creator(family=parts[-1], given=" ".join(parts[:-1]) or None) if parts else Creator(family=full)


def parse_feed(xml: bytes, url: str = API) -> Hits:
    """The feed's works, counting the entries without an id or title as dropped. A body (fetched from ``url``) that is
    no Atom feed raises ``BadBody``, arXiv's error feed ``ArxivError``."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        raise BadBody(url, f"the body is not XML ({type(e).__name__})") from e
    if root.tag != f"{{{NS['a']}}}feed":
        raise BadBody(url, f"the body is not an Atom feed (root {root.tag!r})")
    total = _text(root, "os:totalResults")
    out = Hits(total=int(total) if total and total.isdigit() else None)
    for e in root.findall("a:entry", NS):
        raw_id = _text(e, "a:id") or ""
        if "/api/errors" in raw_id:
            raise ArxivError(url, _text(e, "a:summary") or raw_id)
        aid = normalize_arxiv(raw_id.rsplit("/abs/", 1)[-1])
        title = _text(e, "a:title")
        if not aid or not title:
            out.dropped += 1
            continue
        published = _text(e, "a:published") or ""
        out.append(ItemIn(
            item_type="preprint",
            title=title,
            creators=[_name(_text(a, "a:name") or "") for a in e.findall("a:author", NS)],
            year=int(published[:4]) if published[:4].isdigit() else None,
            venue="arXiv",
            abstract=_text(e, "a:summary"),
            ids=Ids(arxiv=aid, doi=_text(e, "arxiv:doi")).normalized(),
            oa_urls=[pdf_url(aid)],
            discovered_via="search",
        ))
    return out


def _short(hits: Hits, start: int, max_results: int) -> bool:
    """Whether the page is shorter than asked for while the result set goes on past it."""
    return hits.raw < max_results and hits.total is not None and start + hits.raw < hits.total


def _fetch(http: HttpClient, params: dict, *, use_cache: bool = True) -> Hits:
    res = http.get(API, params, use_cache=use_cache)
    if res.status != 200:
        raise HttpError(res.status, res.url)
    try:
        return parse_feed(res.body, res.url)
    except BadBody:  # ArxivError too
        http.forget(API, params)
        raise


def query(http: HttpClient, *, search_query: str | None = None, id_list: list[str] | None = None,
          max_results: int = 25, start: int = 0) -> Hits:
    params: dict = {"max_results": max_results}
    if start:
        params["start"] = start
    if search_query:
        params["search_query"] = search_query
    if id_list:
        params["id_list"] = ",".join(id_list)
    hits = _fetch(http, params)
    if _short(hits, start, max_results):  # a short page inside the result set: fetched again once
        hits = _fetch(http, params, use_cache=False)
        if _short(hits, start, max_results):  # still short: returned so, but not kept for a later search
            http.forget(API, params)
    return hits
