"""PubMed E-utilities and PMC BioC adapter."""

from __future__ import annotations

import re

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import BadBody, HttpClient
from rk.sources.hits import Hits

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json"


def _search_params(term: str, retmax: int, date_from: int | None, date_to: int | None) -> dict:
    params: dict = {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax}
    if date_from is not None or date_to is not None:  # E-utilities takes mindate and maxdate only together
        params.update(datetype="pdat", mindate=date_from if date_from is not None else 1000,
                      maxdate=date_to if date_to is not None else 9999)
    return params


def esearch(http: HttpClient, term: str, retmax: int = 50, *, retstart: int = 0, date_from: int | None = None,
            date_to: int | None = None) -> list[str]:
    """PMIDs matching ``term``: ``retmax`` from ``retstart``, published (``pdat``) in the years ``date_from``..
    ``date_to`` (inclusive) when given."""
    params = _search_params(term, retmax, date_from, date_to)
    if retstart:
        params["retstart"] = retstart
    data, _ = http.get_json(f"{EUTILS}/esearch.fcgi", params)
    return ((data or {}).get("esearchresult") or {}).get("idlist", [])


def count(http: HttpClient, term: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
    """How many PubMed records match ``term`` (esearch ``count`` with no ids), published in the years
    ``date_from``..``date_to`` (inclusive, either end open) when given; ``None`` when the response gives none."""
    data, _ = http.get_json(f"{EUTILS}/esearch.fcgi", _search_params(term, 0, date_from, date_to))
    n = ((data or {}).get("esearchresult") or {}).get("count")
    return int(n) if isinstance(n, int | str) and str(n).isdigit() else None


def _name(n: str) -> Creator:
    fam, _, init = n.strip().rpartition(" ")
    return Creator(family=fam, given=init) if fam else Creator(family=n.strip())


def summary_to_item(uid: str, s: dict) -> ItemIn:
    """An esummary record as a library item. A book (``doctype`` ``book``: an NCBI Bookshelf report) has its title in
    ``booktitle`` and an empty ``title``, and its publisher as the venue; a chapter has its own ``title`` and the book
    as its venue."""
    ids = {a.get("idtype"): a.get("value") for a in s.get("articleids", [])}
    year = re.match(r"(\d{4})", s.get("pubdate") or s.get("epubdate") or "")
    title, book = s.get("title") or "", s.get("booktitle") or ""
    if s.get("doctype") == "book":
        item_type = "bookSection" if title and book else "book"
        venue = book if title and book else s.get("publishername")
    else:
        item_type, venue = "journalArticle", s.get("fulljournalname") or s.get("source")
    return ItemIn(
        item_type=item_type,
        title=(title or book or "(untitled)").rstrip("."),
        creators=[_name(a["name"]) for a in s.get("authors", []) if a.get("authtype", "Author") == "Author" and a.get("name")],
        year=int(year.group(1)) if year else None,
        venue=venue or None,
        ids=Ids(doi=ids.get("doi"), pmid=uid, pmcid=ids.get("pmc") or ids.get("pmcid")).normalized(),
        discovered_via="search",
        language=_language(s.get("lang")),
    )


def _language(langs) -> str | None:
    """The record's language: English when it is among them, else the first."""
    langs = [x for x in (langs if isinstance(langs, list) else [langs]) if isinstance(x, str) and x]
    return "eng" if "eng" in langs else (langs[0] if langs else None)


def _esummary_params(pmids: list[str]) -> dict:
    return {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}


def _summaries(http: HttpClient, pmids: list[str], *,
               use_cache: bool = True) -> tuple[list[ItemIn], set[str]] | None:
    """The works of ``pmids``' esummary body with the uids whose entry is an ``error`` (no document summary, e.g. a
    withdrawn record; left out of the works), or ``None`` when it is no usable ``result``: NCBI's error body, a 404, or
    a result that names none of the PMIDs asked for."""
    data, _ = http.get_json(f"{EUTILS}/esummary.fcgi", _esummary_params(pmids), use_cache=use_cache)
    result = (data or {}).get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None
    uids = [uid for uid in result.get("uids") or [] if isinstance(result.get(uid), dict)]
    if not set(uids) & set(pmids):
        return None
    errors = {uid for uid in uids if "error" in result[uid]}
    return [summary_to_item(uid, result[uid]) for uid in uids if uid not in errors], errors


def esummary(http: HttpClient, pmids: list[str], *, use_cache: bool = True) -> list[ItemIn]:
    if not pmids:
        return []
    got = _summaries(http, pmids, use_cache=use_cache)
    return got[0] if got else []


def _short(got: tuple[list[ItemIn], set[str]] | None, pmids: list[str]) -> bool:
    """Whether a body is unusable or short of a PMID with neither a summary nor an explicit ``error`` entry."""
    return got is None or len(got[0]) + len(got[1] & set(pmids)) < len(pmids)


def summaries(http: HttpClient, pmids: list[str]) -> Hits:
    """The works of a page of ``pmids``, as full as its PMID list: a body short of a summary (cut) is dropped from the
    HTTP cache and fetched again at once; a PMID still without one is counted as a dropped entry (``Hits.dropped``, as
    arXiv's unreadable entries), and that short body is not kept either. A uid with an explicit ``error`` entry (a
    withdrawn record) is a dropped entry at once: no refetch can change it, so a body whose every missing PMID is one
    is not fetched again. A body that is no usable ``result`` (an error body, a 404, a result naming none of the
    PMIDs), also when fetched again, is a ``BadBody`` (transient: the run retries after a backoff), never a page of
    dropped records."""
    if not pmids:
        return Hits()
    url, params = f"{EUTILS}/esummary.fcgi", _esummary_params(pmids)
    got = _summaries(http, pmids)
    if _short(got, pmids):
        http.forget(url, params)
        got = _summaries(http, pmids, use_cache=False)
        if _short(got, pmids):
            http.forget(url, params)
        if got is None:
            raise BadBody(url, f"no esummary result for {len(pmids)} PMIDs")
    items = got[0]
    return Hits(items, dropped=len(pmids) - len(items))


def bioc_json(http: HttpClient, pmcid: str) -> dict | None:
    res = http.get(f"{BIOC}/{pmcid}/unicode")
    if res.status != 200 or not res.body.strip().startswith((b"{", b"[")):
        return None
    try:
        data = res.json()
    except ValueError:  # a 200 whose body starts like JSON but is not (an error text): no document
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    return data
