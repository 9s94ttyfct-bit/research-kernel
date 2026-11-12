"""Europe PMC adapter: search and open-access full-text XML."""

from __future__ import annotations

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _kind(r: dict) -> tuple[str, str | None]:
    """The record's item type and venue: a preprint (source PPR); a thesis (source ETH or pubType Dissertation) or a
    guideline (source HIR: a report) with its publisher (the university, the agency) as venue; a record of a book
    (``bookOrReportDetails``) whose book has another title (``comprisingTitle``) is a chapter, with the book as venue
    (Europe PMC tags many chapters ``Book`` too: Endotext's), else the book itself, with its publisher as venue; else a
    journal article in its journal."""
    journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title")
    if r.get("source") == "PPR":
        return "preprint", journal
    book = r.get("bookOrReportDetails") or {}
    if r.get("source") == "ETH" or "Dissertation" in ((r.get("pubTypeList") or {}).get("pubType") or []):
        return "thesis", book.get("publisher")
    if r.get("source") == "HIR":
        return "report", book.get("publisher")
    if book:
        whole = (book.get("comprisingTitle") or "").rstrip(".")
        if whole and whole.casefold() != (r.get("title") or "").rstrip(".").casefold():
            return "bookSection", whole
        return "book", book.get("publisher")
    return "journalArticle", journal


def result_to_item(r: dict) -> ItemIn:
    authors = (r.get("authorList") or {}).get("author", [])
    pdfs = [
        u["url"]
        for u in (r.get("fullTextUrlList") or {}).get("fullTextUrl", [])
        if u.get("documentStyle") == "pdf" and u.get("availabilityCode") in ("OA", "F")
    ]
    year = r.get("pubYear")
    item_type, venue = _kind(r)
    return ItemIn(
        item_type=item_type,
        title=(r.get("title") or "(untitled)").rstrip("."),
        creators=[
            Creator(family=a.get("lastName") or a.get("fullName") or a.get("collectiveName", ""), given=a.get("firstName"))
            for a in authors
            if a.get("lastName") or a.get("fullName") or a.get("collectiveName")
        ],
        year=int(year) if year and str(year).isdigit() else None,
        venue=venue or None,
        abstract=r.get("abstractText"),
        ids=Ids(doi=r.get("doi"), pmid=r.get("pmid"), pmcid=r.get("pmcid")).normalized(),
        oa_urls=pdfs,
        discovered_via="search",
        language=r.get("language"),
    )


def _query(query: str, open_access_only: bool, date_from: int | None, date_to: int | None) -> str:
    if not open_access_only and date_from is None and date_to is None:
        return query
    parts = [f"({query})"] + (["OPEN_ACCESS:y"] if open_access_only else [])
    if date_from is not None or date_to is not None:
        parts.append(f"(PUB_YEAR:[{date_from if date_from is not None else 1000} TO "
                     f"{date_to if date_to is not None else 9999}])")
    return " AND ".join(parts)


def _page(http: HttpClient, q: str, page_size: int, cursor: str | None) -> tuple[list[dict], str | None]:
    params = {"query": q, "format": "json", "resultType": "core", "pageSize": page_size}
    if cursor is not None:
        params["cursorMark"] = cursor
    data, _ = http.get_json(f"{BASE}/search", params)
    return ((data or {}).get("resultList") or {}).get("result", []), (data or {}).get("nextCursorMark")


def search(http: HttpClient, query: str, *, open_access_only: bool = False, page_size: int = 25, offset: int = 0,
           date_from: int | None = None, date_to: int | None = None) -> list[ItemIn]:
    """One page of records matching ``query`` (publication years ``date_from``..``date_to``, inclusive, when given):
    ``page_size`` from ``offset``, a multiple of ``page_size``. Europe PMC pages by cursor, so a later page walks the
    pages before it, which the HTTP cache keeps from when they were fetched."""
    if offset % page_size:
        raise ValueError(f"Europe PMC pages by cursor: offset {offset} is not a multiple of {page_size}")
    q = _query(query, open_access_only, date_from, date_to)
    cursor: str | None = None
    for _ in range(offset // page_size):
        rows, nxt = _page(http, q, page_size, cursor)
        if not rows or not nxt or nxt == cursor:  # the results end before the offset
            return []
        cursor = nxt
    rows, _ = _page(http, q, page_size, cursor)
    return [result_to_item(r) for r in rows]


def count(http: HttpClient, query: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
    """How many records match ``query`` (``hitCount`` of a one-result id list), published in the years
    ``date_from``..``date_to`` (inclusive, either end open) when given; ``None`` when the response gives none."""
    data, _ = http.get_json(f"{BASE}/search", {"query": _query(query, False, date_from, date_to), "format": "json",
                                               "resultType": "idlist", "pageSize": 1})
    n = (data or {}).get("hitCount")
    return int(n) if isinstance(n, int | str) and str(n).isdigit() else None


def fulltext_xml(http: HttpClient, pmcid: str) -> bytes | None:
    res = http.get(f"{BASE}/{pmcid}/fullTextXML")
    return res.body if res.status == 200 and res.body.strip().startswith(b"<") else None
