"""PubMed E-utilities and PMC BioC adapter."""

from __future__ import annotations

import re

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json"


def esearch(http: HttpClient, term: str, retmax: int = 50, *, retstart: int = 0, date_from: int | None = None,
            date_to: int | None = None) -> list[str]:
    """PMIDs matching ``term``: ``retmax`` from ``retstart``, published (``pdat``) in the years ``date_from``..
    ``date_to`` (inclusive) when given."""
    params: dict = {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax}
    if retstart:
        params["retstart"] = retstart
    if date_from is not None or date_to is not None:  # E-utilities takes mindate and maxdate only together
        params.update(datetype="pdat", mindate=date_from if date_from is not None else 1000,
                      maxdate=date_to if date_to is not None else 9999)
    data, _ = http.get_json(f"{EUTILS}/esearch.fcgi", params)
    return ((data or {}).get("esearchresult") or {}).get("idlist", [])


def _name(n: str) -> Creator:
    fam, _, init = n.strip().rpartition(" ")
    return Creator(family=fam, given=init) if fam else Creator(family=n.strip())


def summary_to_item(uid: str, s: dict) -> ItemIn:
    ids = {a.get("idtype"): a.get("value") for a in s.get("articleids", [])}
    year = re.match(r"(\d{4})", s.get("pubdate") or s.get("epubdate") or "")
    return ItemIn(
        item_type="journalArticle",
        title=(s.get("title") or "(untitled)").rstrip("."),
        creators=[_name(a["name"]) for a in s.get("authors", []) if a.get("authtype", "Author") == "Author" and a.get("name")],
        year=int(year.group(1)) if year else None,
        venue=s.get("fulljournalname") or s.get("source"),
        ids=Ids(doi=ids.get("doi"), pmid=uid, pmcid=ids.get("pmc") or ids.get("pmcid")).normalized(),
        discovered_via="search",
        language=_language(s.get("lang")),
    )


def _language(langs) -> str | None:
    """The record's language: English when it is among them, else the first."""
    langs = [x for x in (langs if isinstance(langs, list) else [langs]) if isinstance(x, str) and x]
    return "eng" if "eng" in langs else (langs[0] if langs else None)


def esummary(http: HttpClient, pmids: list[str]) -> list[ItemIn]:
    if not pmids:
        return []
    data, _ = http.get_json(f"{EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"})
    result = (data or {}).get("result") or {}
    return [summary_to_item(uid, result[uid]) for uid in result.get("uids", []) if uid in result]


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
