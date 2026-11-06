"""PubMed E-utilities and PMC BioC adapter."""

from __future__ import annotations

import re

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json"


def esearch(http: HttpClient, term: str, retmax: int = 50) -> list[str]:
    data, _ = http.get_json(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax})
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
    )


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
    data = res.json()
    if isinstance(data, list):
        data = data[0] if data else None
    return data
