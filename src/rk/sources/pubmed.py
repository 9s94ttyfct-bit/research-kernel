"""PubMed E-utilities and PMC BioC adapter.

Search builds its items from ``esummary``, which carries no abstract. Calibration (search and screening, 2026-11-12):
``abstracts`` fetches the abstracts of given PMIDs with ``efetch`` (XML, at most ``EFETCH_BATCH`` PMIDs a request);
the kernel asks for them only before title/abstract screening, for the records still without one."""

from __future__ import annotations

import re

from lxml import etree

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import BadBody, HttpClient, HttpError
from rk.sources.hits import Hits

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json"
EFETCH_BATCH = 200


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
    """An esummary record as a library item. An NCBI Bookshelf book (``doctype`` ``book``) has its title in
    ``booktitle`` and an empty ``title``, and its publisher as the venue; a chapter (``doctype`` ``chapter``) has its
    own ``title`` and the book as its venue. Both are dated by ``docdate`` (a chapter's ``pubdate`` is its series'),
    else ``pubdate``."""
    ids = {a.get("idtype"): a.get("value") for a in s.get("articleids", [])}
    doctype, title, book = s.get("doctype"), s.get("title") or "", (s.get("booktitle") or "").rstrip(".")
    dated = (s.get("docdate") or "") if doctype in ("book", "chapter") else ""
    year = re.match(r"(\d{4})", dated or s.get("pubdate") or s.get("epubdate") or "")
    if doctype == "chapter":
        item_type, venue = "bookSection", book
    elif doctype == "book":
        item_type, venue = "book", s.get("publishername")
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


def _abstract_text(abstract) -> str | None:
    """An ``Abstract`` element's ``AbstractText`` sections, each after its ``Label`` ("BACKGROUND: …"), one a line."""
    parts = []
    for sec in abstract.findall("AbstractText"):
        text = " ".join("".join(sec.itertext()).split())
        if text:
            label = (sec.get("Label") or "").strip()
            parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts) or None


def _parse_abstracts(xml: bytes, url: str) -> dict[str, str]:
    try:
        root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except etree.XMLSyntaxError as e:
        raise BadBody(url, f"the body is not XML ({type(e).__name__})") from e
    if root.tag != "PubmedArticleSet":
        raise BadBody(url, f"the body is no PubmedArticleSet (root {root.tag!r})")
    out: dict[str, str] = {}
    for art in root:
        doc = art.find("MedlineCitation") if art.tag == "PubmedArticle" else art.find("BookDocument")
        if doc is None:
            continue
        pmid, abstract = doc.findtext("PMID"), doc.find("Article/Abstract")
        if abstract is None:
            abstract = doc.find("Abstract")  # a Bookshelf document's abstract
        text = _abstract_text(abstract) if abstract is not None else None
        if pmid and text:
            out[pmid.strip()] = text
    return out


def abstracts(http: HttpClient, pmids: list[str]) -> dict[str, str]:
    """The abstracts of ``pmids`` by PMID (``efetch``, ``EFETCH_BATCH`` PMIDs a request); a PMID whose record has no
    abstract is left out. A status other than 200 is an ``HttpError``; a body that is no PubMed article set is a
    ``BadBody`` and is dropped from the HTTP cache."""
    out: dict[str, str] = {}
    url = f"{EUTILS}/efetch.fcgi"
    for i in range(0, len(pmids), EFETCH_BATCH):
        params = {"db": "pubmed", "id": ",".join(pmids[i:i + EFETCH_BATCH]), "retmode": "xml", "rettype": "abstract"}
        res = http.get(url, params)
        if res.status != 200:
            raise HttpError(res.status, res.url)
        try:
            out.update(_parse_abstracts(res.body, res.url))
        except BadBody:
            http.forget(url, params)
            raise
    return out


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
