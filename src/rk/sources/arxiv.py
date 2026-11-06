"""arXiv API adapter (Atom)."""

from __future__ import annotations

from lxml import etree

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient, HttpError
from rk.sources.identifiers import normalize_arxiv

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


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


def parse_feed(xml: bytes) -> list[ItemIn]:
    root = etree.fromstring(xml)
    out: list[ItemIn] = []
    for e in root.findall("a:entry", NS):
        aid = normalize_arxiv((_text(e, "a:id") or "").rsplit("/abs/", 1)[-1])
        title = _text(e, "a:title")
        if not aid or not title or title == "Error":
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


def query(http: HttpClient, *, search_query: str | None = None, id_list: list[str] | None = None,
          max_results: int = 25) -> list[ItemIn]:
    params: dict = {"max_results": max_results}
    if search_query:
        params["search_query"] = search_query
    if id_list:
        params["id_list"] = ",".join(id_list)
    res = http.get(API, params)
    if res.status != 200:
        raise HttpError(res.status, res.url)
    return parse_feed(res.body)
