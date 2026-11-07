"""Europe PMC adapter: search and open-access full-text XML."""

from __future__ import annotations

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def result_to_item(r: dict) -> ItemIn:
    authors = (r.get("authorList") or {}).get("author", [])
    pdfs = [
        u["url"]
        for u in (r.get("fullTextUrlList") or {}).get("fullTextUrl", [])
        if u.get("documentStyle") == "pdf" and u.get("availabilityCode") in ("OA", "F")
    ]
    year = r.get("pubYear")
    return ItemIn(
        item_type="preprint" if r.get("source") == "PPR" else "journalArticle",
        title=(r.get("title") or "(untitled)").rstrip("."),
        creators=[
            Creator(family=a.get("lastName") or a.get("fullName") or a.get("collectiveName", ""), given=a.get("firstName"))
            for a in authors
            if a.get("lastName") or a.get("fullName") or a.get("collectiveName")
        ],
        year=int(year) if year and str(year).isdigit() else None,
        venue=((r.get("journalInfo") or {}).get("journal") or {}).get("title"),
        abstract=r.get("abstractText"),
        ids=Ids(doi=r.get("doi"), pmid=r.get("pmid"), pmcid=r.get("pmcid")).normalized(),
        oa_urls=pdfs,
        discovered_via="search",
    )


def search(http: HttpClient, query: str, *, open_access_only: bool = False, page_size: int = 25) -> list[ItemIn]:
    q = f"({query}) AND OPEN_ACCESS:y" if open_access_only else query
    data, _ = http.get_json(f"{BASE}/search", {"query": q, "format": "json", "resultType": "core", "pageSize": page_size})
    return [result_to_item(r) for r in ((data or {}).get("resultList") or {}).get("result", [])]


def count(http: HttpClient, query: str) -> int | None:
    """How many records match ``query`` (``hitCount`` of a one-result id list); ``None`` when the response gives
    none."""
    data, _ = http.get_json(f"{BASE}/search", {"query": query, "format": "json", "resultType": "idlist",
                                               "pageSize": 1})
    n = (data or {}).get("hitCount")
    return int(n) if isinstance(n, int | str) and str(n).isdigit() else None


def fulltext_xml(http: HttpClient, pmcid: str) -> bytes | None:
    res = http.get(f"{BASE}/{pmcid}/fullTextXML")
    return res.body if res.status == 200 and res.body.strip().startswith(b"<") else None
