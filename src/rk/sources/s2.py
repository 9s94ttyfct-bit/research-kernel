"""Semantic Scholar Graph API adapter (requires a key)."""

from __future__ import annotations

from pydantic import BaseModel

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

BASE = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,authors,year,venue,abstract,externalIds"


class NotConfigured(Exception):
    def __init__(self, what: str):
        super().__init__(f"{what} is not configured; run `rk setup`")
        self.what = what


class Snippet(BaseModel):
    paper_ids: Ids
    title: str | None
    text: str
    section: str | None = None


def _ids(ext: dict | None) -> Ids:
    ext = ext or {}
    return Ids(doi=ext.get("DOI"), pmid=ext.get("PubMed"), pmcid=ext.get("PubMedCentral"), arxiv=ext.get("ArXiv"),
               s2=str(ext.get("CorpusId")) if ext.get("CorpusId") else None).normalized()


def _name(n: str) -> Creator:
    parts = n.split()
    return Creator(family=parts[-1], given=" ".join(parts[:-1]) or None) if parts else Creator(family=n)


def search(http: HttpClient, query: str, *, api_key: str | None, limit: int = 20, offset: int = 0,
           date_from: int | None = None, date_to: int | None = None) -> list[ItemIn]:
    """Papers matching ``query``: ``limit`` from ``offset``, published in the years ``date_from``..``date_to``
    (inclusive; either end open) when given."""
    if not api_key:
        raise NotConfigured("s2_key")
    params: dict = {"query": query, "limit": limit, "fields": FIELDS}
    if offset:
        params["offset"] = offset
    if date_from is not None or date_to is not None:
        params["year"] = f"{date_from if date_from is not None else ''}-{date_to if date_to is not None else ''}"
    data, _ = http.get_json(f"{BASE}/paper/search", params, headers={"x-api-key": api_key})
    return [
        ItemIn(title=p.get("title") or "(untitled)", creators=[_name(a["name"]) for a in p.get("authors", []) if a.get("name")],
               year=p.get("year"), venue=p.get("venue") or None, abstract=p.get("abstract"),
               ids=_ids(p.get("externalIds")), discovered_via="search")
        for p in (data or {}).get("data", [])
    ]


def snippet_search(http: HttpClient, query: str, *, api_key: str | None, limit: int = 10) -> list[Snippet]:
    if not api_key:
        raise NotConfigured("s2_key")
    data, _ = http.get_json(f"{BASE}/snippet/search", {"query": query, "limit": limit}, headers={"x-api-key": api_key})
    out = []
    for d in (data or {}).get("data", []):
        paper = d.get("paper") or {}
        snip = d.get("snippet") or {}
        out.append(Snippet(paper_ids=_ids(paper.get("externalIds") or {"CorpusId": paper.get("corpusId")}),
                           title=paper.get("title"), text=snip.get("text", ""), section=snip.get("section")))
    return out
