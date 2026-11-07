"""OpenAlex adapter: works, search, citation graph, retraction flag, OA locations."""

from __future__ import annotations

from pydantic import BaseModel

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient
from rk.sources.identifiers import Identifier

BASE = "https://api.openalex.org"
BUDGET = "openalex"

_TYPE = {
    "article": "journalArticle", "review": "journalArticle", "letter": "journalArticle", "editorial": "journalArticle",
    "preprint": "preprint", "book": "book", "book-chapter": "bookSection", "dataset": "dataset",
    "dissertation": "thesis", "report": "report", "proceedings-article": "conferencePaper",
}


class OpenAlexWork(BaseModel):
    item: ItemIn
    is_retracted: bool
    referenced_works: list[str]
    cited_by_count: int
    oa_pdf_urls: list[str]
    raw: dict


def _params(api_key: str | None, **kw) -> dict:
    p = {k: v for k, v in kw.items() if v is not None}
    if api_key:
        p["api_key"] = api_key
    return p


def _abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos: list[tuple[int, str]] = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos)) or None


def _split_name(display: str) -> Creator:
    parts = display.strip().split()
    if len(parts) <= 1:
        return Creator(family=display.strip())
    return Creator(family=parts[-1], given=" ".join(parts[:-1]))


def _short(oa_id: str | None) -> str | None:
    return oa_id.rsplit("/", 1)[-1] if oa_id else None


def work_to_item(w: dict) -> ItemIn:
    ids = w.get("ids") or {}
    pmid = _short(ids.get("pmid"))
    pmcid = _short(ids.get("pmcid"))
    loc = w.get("primary_location") or {}
    if pmcid and pmcid.isdigit():
        pmcid = f"PMC{pmcid}"
    source = loc.get("source") or {}
    return ItemIn(
        item_type=_TYPE.get(w.get("type", ""), "journalArticle"),
        title=w.get("title") or w.get("display_name") or "(untitled)",
        creators=[
            _split_name((a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "")
            for a in w.get("authorships", [])
            if (a.get("author") or {}).get("display_name") or a.get("raw_author_name")
        ],
        year=w.get("publication_year"),
        venue=source.get("display_name"),
        abstract=_abstract(w.get("abstract_inverted_index")),
        ids=Ids(doi=w.get("doi"), pmid=pmid, pmcid=pmcid, openalex=_short(w.get("id"))).normalized(),
        oa_urls=_pdf_urls(w),
        discovered_via="search",
    )


def _pdf_urls(w: dict) -> list[str]:
    urls: list[str] = []
    for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
        u = loc.get("pdf_url")
        if u and u not in urls:
            urls.append(u)
    return urls


def _to_work(w: dict) -> OpenAlexWork:
    return OpenAlexWork(
        item=work_to_item(w),
        is_retracted=bool(w.get("is_retracted")),
        referenced_works=[_short(r) for r in w.get("referenced_works", []) if r],
        cited_by_count=int(w.get("cited_by_count") or 0),
        oa_pdf_urls=_pdf_urls(w),
        raw=w,
    )


def get_work(http: HttpClient, ident: Identifier, api_key: str | None) -> OpenAlexWork | None:
    if ident.kind == "doi":
        path = f"https://doi.org/{ident.value}"
    elif ident.kind == "pmid":
        path = f"pmid:{ident.value}"
    elif ident.kind == "pmcid":
        path = f"pmcid:{ident.value}"
    elif ident.kind == "openalex":
        path = ident.value
    else:
        raise ValueError(f"OpenAlex cannot look up {ident.kind}")
    data, res = http.get_json(f"{BASE}/works/{path}", _params(api_key), budget_key=BUDGET)
    if res.status == 404 or not data:
        return None
    return _to_work(data)


def _list(http: HttpClient, params: dict) -> list[dict]:
    data, _ = http.get_json(f"{BASE}/works", params, budget_key=BUDGET)
    return (data or {}).get("results", [])


def search(http: HttpClient, query: str, *, api_key: str | None, per_page: int = 25,
           filters: dict | None = None) -> list[ItemIn]:
    flt = ",".join(f"{k}:{v}" for k, v in (filters or {}).items()) or None
    return [work_to_item(w) for w in _list(http, _params(api_key, search=query, per_page=per_page, filter=flt))]


def cited_by(http: HttpClient, openalex_id: str, *, api_key: str | None, per_page: int = 25,
             after_year: int | None = None) -> list[ItemIn]:
    """Works citing ``openalex_id``; with ``after_year``, only those published in a later year."""
    flt = f"cites:{openalex_id}" + (f",publication_year:>{after_year}" if after_year is not None else "")
    rows = _list(http, _params(api_key, filter=flt, per_page=per_page))
    return [work_to_item(w).model_copy(update={"discovered_via": "forward"}) for w in rows]


def year_counts(http: HttpClient, query: str, *, api_key: str | None) -> dict[int, int]:
    """Works matching ``query`` per publication year (``group_by=publication_year``); unknown years are left out."""
    data, _ = http.get_json(f"{BASE}/works", _params(api_key, search=query, group_by="publication_year"),
                            budget_key=BUDGET)
    counts = {int(g["key"]): int(g["count"]) for g in (data or {}).get("group_by", []) if str(g["key"]).isdigit()}
    return dict(sorted(counts.items()))


def count(http: HttpClient, query: str, *, api_key: str | None) -> int:
    """How many works match ``query`` (``meta.count`` of a one-result page)."""
    data, _ = http.get_json(f"{BASE}/works", _params(api_key, search=query, per_page=1, select="id"),
                            budget_key=BUDGET)
    return int(((data or {}).get("meta") or {}).get("count") or 0)


def referenced(http: HttpClient, work: OpenAlexWork, *, api_key: str | None, limit: int = 50) -> list[ItemIn]:
    ids = work.referenced_works[:limit]
    out: list[ItemIn] = []
    for i in range(0, len(ids), 50):
        chunk = "|".join(ids[i : i + 50])
        rows = _list(http, _params(api_key, filter=f"openalex:{chunk}", per_page=50))
        out += [work_to_item(w).model_copy(update={"discovered_via": "backward"}) for w in rows]
    return out
