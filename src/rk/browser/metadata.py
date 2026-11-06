"""Bibliographic metadata from an article page (spec §8.5 key-less workflow, step 2).

Precedence inside one page: Highwire ``citation_*`` > JSON-LD > Dublin Core > COinS > ``<title>``. Against the APIs,
``merge_with_api`` applies spec §8.5: API metadata wins and the differences are returned for the event log; with no API
record the page metadata is used and the caller marks the item ``page_metadata_only``."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin

from lxml import html as lhtml
from pydantic import BaseModel, Field

from rk.library.models import Creator, Ids, ItemIn, ItemType, normalize_title
from rk.sources.identifiers import (
    normalize_arxiv,
    normalize_doi,
    normalize_isbn,
    normalize_pmid,
)
from rk.sources.resolver import ResolvedWork

_ZW = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
_DOI_IN = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
_SCHOLARLY = {"scholarlyarticle", "article", "medicalscholarlyarticle", "book", "chapter", "report", "thesis",
              "techarticle", "newsarticle"}


class PageMetadata(BaseModel):
    url: str
    title: str | None = None
    creators: list[Creator] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    pmid: str | None = None
    arxiv: str | None = None
    isbn: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    item_type: ItemType = "webpage"
    sources: list[str] = Field(default_factory=list)  # which metadata vocabularies contributed


def _clean(s: Any) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", _ZW.sub("", str(s))).strip()
    return t or None


def _year(s: str | None) -> int | None:
    m = _YEAR.search(s or "")
    return int(m.group(1)) if m else None


def _doi(s: str | None) -> str | None:
    m = _DOI_IN.search(s or "")
    return normalize_doi(m.group(0).rstrip(".,;)")) if m else None


def _person(name: str) -> Creator | None:
    name = _clean(name) or ""
    if not name:
        return None
    if "," in name:
        family, given = (p.strip() for p in name.split(",", 1))
        return Creator(family=family, given=given or None)
    parts = name.split()
    return Creator(family=parts[-1], given=" ".join(parts[:-1]) or None)


def _metas(doc) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in doc.iter("meta"):
        key = (m.get("name") or m.get("property") or "").strip().lower()
        val = _clean(m.get("content"))
        if key and val:
            out.setdefault(key, []).append(val)
    return out


def _first(metas: dict[str, list[str]], *keys: str) -> str | None:
    for k in keys:
        if metas.get(k):
            return metas[k][0]
    return None


def _highwire(metas: dict[str, list[str]], url: str) -> dict:
    if "citation_title" not in metas:
        return {}
    d: dict[str, Any] = {"title": _first(metas, "citation_title"),
                         "creators": [c for a in metas.get("citation_author", []) if (c := _person(a))],
                         "year": _year(_first(metas, "citation_publication_date", "citation_date", "citation_year",
                                              "citation_online_date", "citation_cover_date")),
                         "venue": _first(metas, "citation_journal_title", "citation_conference_title",
                                         "citation_book_title", "citation_publisher"),
                         "doi": _doi(_first(metas, "citation_doi")), "pmid": normalize_pmid(_first(metas, "citation_pmid")),
                         "arxiv": normalize_arxiv(_first(metas, "citation_arxiv_id")),
                         "isbn": normalize_isbn(_first(metas, "citation_isbn")),
                         "abstract": _first(metas, "citation_abstract")}
    pdf = _first(metas, "citation_pdf_url")
    d["pdf_url"] = urljoin(url, pdf) if pdf else None
    if "citation_conference_title" in metas:
        d["item_type"] = "conferencePaper"
    elif "citation_dissertation_institution" in metas:
        d["item_type"] = "thesis"
    elif "citation_technical_report_institution" in metas:
        d["item_type"] = "report"
    elif "citation_book_title" in metas or ("citation_isbn" in metas and "citation_journal_title" not in metas):
        d["item_type"] = "bookSection" if "citation_book_title" in metas else "book"
    elif d["arxiv"] or "citation_arxiv_id" in metas:
        d["item_type"] = "preprint"
    else:
        d["item_type"] = "journalArticle"
    return d


def _jsonld_nodes(doc) -> list[dict]:
    nodes: list[dict] = []

    def walk(x: Any) -> None:
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            nodes.append(x)
            walk(x.get("@graph"))

    for s in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            walk(json.loads(s.text or ""))
        except json.JSONDecodeError:
            continue
    return nodes


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {str(x).lower() for x in (t if isinstance(t, list) else [t]) if x}


def _jsonld(doc) -> dict:
    node = next((n for n in _jsonld_nodes(doc) if _types(n) & _SCHOLARLY), None)
    if not node:
        return {}
    creators: list[Creator] = []
    authors = node.get("author") or []
    for a in authors if isinstance(authors, list) else [authors]:
        if isinstance(a, dict) and a.get("familyName"):
            creators.append(Creator(family=_clean(a["familyName"]), given=_clean(a.get("givenName"))))
        elif isinstance(a, dict) and a.get("name"):
            if c := _person(a["name"]):
                creators.append(c)
        elif isinstance(a, str) and (c := _person(a)):
            creators.append(c)
    part = node.get("isPartOf")
    venue = part.get("name") if isinstance(part, dict) else part if isinstance(part, str) else None
    ids = [node.get("@id"), node.get("url")]
    for key in ("sameAs", "identifier"):
        v = node.get(key)
        for x in v if isinstance(v, list) else [v]:
            ids.append(x.get("value") if isinstance(x, dict) else x)
    types = _types(node)
    return {"title": _clean(node.get("headline") or node.get("name")), "creators": creators,
            "year": _year(str(node.get("datePublished") or "")), "venue": _clean(venue),
            "doi": next((d for x in ids if isinstance(x, str) and (d := _doi(x))), None),
            "abstract": _clean(node.get("description")),
            "item_type": "book" if "book" in types else "bookSection" if "chapter" in types else
                         "report" if "report" in types else "thesis" if "thesis" in types else "journalArticle"}


def _dublin_core(metas: dict[str, list[str]]) -> dict:
    def get(field: str) -> list[str]:
        return metas.get(f"dc.{field}", []) + metas.get(f"dcterms.{field}", [])

    if not get("title"):
        return {}
    return {"title": get("title")[0], "creators": [c for a in get("creator") if (c := _person(a))],
            "year": _year((get("date") + get("issued") + [""])[0]),
            "doi": next((d for x in get("identifier") if (d := _doi(x))), None),
            "abstract": (get("description") + [None])[0], "item_type": "journalArticle"}


def _coins(doc) -> dict:
    span = next(iter(doc.xpath('//span[contains(concat(" ", normalize-space(@class), " "), " Z3988 ")]')), None)
    if span is None or not span.get("title"):
        return {}
    q = {k: v[0] for k, v in parse_qs(span.get("title")).items()}
    title = q.get("rft.atitle") or q.get("rft.btitle") or q.get("rft.title")
    if not title:
        return {}
    creators = []
    if q.get("rft.aulast"):
        creators.append(Creator(family=q["rft.aulast"], given=q.get("rft.aufirst")))
    for au in parse_qs(span.get("title")).get("rft.au", []):
        if (c := _person(au)) and all(c.family != x.family for x in creators):
            creators.append(c)
    rft_ids = parse_qs(span.get("title")).get("rft_id", [])
    pmid = next((normalize_pmid(x.removeprefix("info:pmid/")) for x in rft_ids if x.startswith("info:pmid/")), None)
    return {"title": _clean(title), "creators": creators, "year": _year(q.get("rft.date")),
            "venue": _clean(q.get("rft.jtitle") or q.get("rft.btitle")),
            "doi": next((d for x in rft_ids if (d := _doi(x))), None), "pmid": pmid,
            "isbn": normalize_isbn(q.get("rft.isbn")),
            "item_type": "book" if q.get("rft.genre") == "book" else "journalArticle"}


def parse_page_metadata(html: str, url: str) -> PageMetadata:
    try:
        doc = lhtml.fromstring(html or "<html></html>")
    except (ValueError, lhtml.etree.ParserError):
        return PageMetadata(url=url)
    metas = _metas(doc)
    layers = [("highwire", _highwire(metas, url)), ("jsonld", _jsonld(doc)), ("dublin_core", _dublin_core(metas)),
              ("coins", _coins(doc))]
    meta = PageMetadata(url=url)
    for name, layer in layers:
        if not layer:
            continue
        if not meta.sources:  # the highest-precedence vocabulary present decides the item type
            meta.item_type = layer.get("item_type", "journalArticle")
        meta.sources.append(name)
        for field, value in layer.items():
            if field != "item_type" and value and not getattr(meta, field):
                setattr(meta, field, value)
    if not meta.title:
        t = doc.find(".//title")
        meta.title = _clean(t.text_content()) if t is not None else None
        if meta.title:
            meta.sources.append("title")
    return meta


def to_item(meta: PageMetadata, *, discovered_via: str = "browser") -> ItemIn:
    return ItemIn(item_type=meta.item_type, title=meta.title or meta.url, creators=meta.creators, year=meta.year,
                  venue=meta.venue, abstract=meta.abstract, discovered_via=discovered_via,
                  ids=Ids(doi=meta.doi, pmid=meta.pmid, arxiv=meta.arxiv, isbn=meta.isbn, url=meta.url).normalized())


def merge_with_api(meta: PageMetadata, resolved: ResolvedWork | None) -> tuple[ItemIn, dict]:
    """The item to add and ``{field: {"page": …, "api": …}}`` for fields where page and API disagree."""
    page = to_item(meta)
    if resolved is None:
        return page, {}
    api = resolved.item
    diff: dict[str, dict] = {}
    if page.title and api.title and normalize_title(page.title) != normalize_title(api.title):
        diff["title"] = {"page": page.title, "api": api.title}
    if page.year and api.year and page.year != api.year:
        diff["year"] = {"page": page.year, "api": api.year}
    if page.creators and api.creators and page.creators[0].family.casefold() != api.creators[0].family.casefold():
        diff["first_author"] = {"page": page.creators[0].family, "api": api.creators[0].family}
    ids = api.ids.model_copy(update={k: v for k, v in page.ids.model_dump().items()
                                     if v and not getattr(api.ids, k)})
    return api.model_copy(update={"ids": ids, "discovered_via": "browser"}), diff
