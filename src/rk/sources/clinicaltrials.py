"""ClinicalTrials.gov adapter (API v2, ``/api/v2/studies``): registry records of clinical studies, searched and counted.

A registry record is a study's registration, not a publication. It becomes a ``report`` item (venue
"ClinicalTrials.gov", its lead sponsor as the institutional creator, as a registry record is cited) with the study's
official title (else its brief title), the year it was first posted, its brief summary as the abstract (plain text: the
API's ``legacy`` markup, whitespace collapsed), its NCT number (``Ids.nct``: the library's identifier of the record, so
the same study found by two queries, pages or searches is one item) and its study page (``Ids.url``). The result
publications a record lists are not added, and no full text is fetched here. ``FIELDS`` keeps a response to what the
item needs. A study without an NCT number or a title is dropped and counted (``Hits.dropped``).

Calibration (registry search, 2026-11-14): a query is sent as written to ``query.term``, ClinicalTrials.gov's basic
search (the website's "Other terms"; Essie syntax: AND, OR, NOT, parentheses and quoted phrases, with the registry's
synonym expansion: "acetylsalicylic acid" finds what aspirin finds). Its BasicSearch area covers a record's titles,
acronym, conditions, interventions, keywords, brief summary, outcomes, arms, design, sponsor, ids, MeSH terms and
locations. A registry record has no title/abstract field: restricting the query to the titles or the brief summary
would need the whole query matched within one field (``AREA[TitleSearch]( … ) OR AREA[BriefSummary]( … )``: 279 studies
for "aspirin AND stroke" against the basic search's 1,032) or the query rewritten concept by concept, which the kernel
does not do. ``*`` and ``?`` are ignored, not truncation (``strok*`` matched 11 studies, ``stroke`` 25,783). Checked
live 2026-11-14.

The publication-year range is applied as the date the study was first posted (``StudyFirstPostDate``, in
``filter.advanced`` as ``AREA[StudyFirstPostDate]RANGE[…]``, ``MIN``/``MAX`` for an open end), and the item's year is
that date's: the date the record became public, the registry's analogue of a publication date, fixed once posted and
known for every record. The study start date was not chosen: it is often an estimate, moves while the study runs and
may lie in the future.

Paging is by ``nextPageToken``, so a page at ``offset`` walks the pages before it (``pageSize`` at most
``MAX_PAGE``), which the HTTP cache keeps from when they were fetched; ``Hits.total`` is the query's ``totalCount``
(the first page's: ``countTotal``). A query the registry cannot parse is an HTTP 400 (``HttpError``), never "no hits"."""

from __future__ import annotations

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient
from rk.sources.hits import Hits
from rk.sources.identifiers import normalize_nct

API = "https://clinicaltrials.gov/api/v2/studies"
VENUE = "ClinicalTrials.gov"
MAX_PAGE = 1000  # the API's largest pageSize
FIELDS = "NCTId,BriefTitle,OfficialTitle,BriefSummary,StudyFirstPostDate,LeadSponsorName"


def study_url(nct: str) -> str:
    return f"https://clinicaltrials.gov/study/{nct}"


def first_posted(date_from: int | None, date_to: int | None) -> str | None:
    """The ``filter.advanced`` expression for studies first posted in the years ``date_from``..``date_to``
    (inclusive, either end open); ``None`` when both ends are open."""
    if date_from is None and date_to is None:
        return None
    lo = f"{date_from:04d}-01-01" if date_from is not None else "MIN"
    hi = f"{date_to:04d}-12-31" if date_to is not None else "MAX"
    return f"AREA[StudyFirstPostDate]RANGE[{lo},{hi}]"


def _filters(query: str, date_from: int | None, date_to: int | None) -> dict:
    """The query in the basic search and the first-posted range, the parameters a search and a count share."""
    params = {"query.term": query, "countTotal": "true"}
    if (posted := first_posted(date_from, date_to)) is not None:
        params["filter.advanced"] = posted
    return params


def _text(value) -> str | None:
    """A text field with its whitespace collapsed; ``None`` when there is none."""
    return " ".join(value.split()) or None if isinstance(value, str) else None


def study_to_item(study: dict) -> ItemIn | None:
    """The registry record of one study (see the module docstring), or ``None`` when it has no NCT number or title."""
    p = study.get("protocolSection") or {}
    ident = p.get("identificationModule") or {}
    nct = normalize_nct(ident.get("nctId"))
    title = _text(ident.get("officialTitle")) or _text(ident.get("briefTitle"))
    if not nct or not title:
        return None
    posted = str(((p.get("statusModule") or {}).get("studyFirstPostDateStruct") or {}).get("date") or "")
    sponsor = _text(((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name"))
    return ItemIn(
        item_type="report",
        title=title.rstrip("."),
        creators=[Creator(family=sponsor)] if sponsor else [],
        year=int(posted[:4]) if posted[:4].isdigit() else None,
        venue=VENUE,
        abstract=_text((p.get("descriptionModule") or {}).get("briefSummary")),
        ids=Ids(nct=nct, url=study_url(nct)).normalized(),
        discovered_via="search",
    )


def _page(http: HttpClient, params: dict, token: str | None) -> tuple[list[dict], str | None, int | None]:
    """One page: its studies, the next page's token and the query's ``totalCount`` (``None`` when not given)."""
    data, _ = http.get_json(API, {**params, "pageToken": token} if token else params)
    data = data or {}
    total = data.get("totalCount")
    return data.get("studies") or [], data.get("nextPageToken"), total if isinstance(total, int) else None


def search(http: HttpClient, query: str, *, page_size: int = 25, offset: int = 0, date_from: int | None = None,
           date_to: int | None = None) -> Hits:
    """One page of studies matching ``query`` in the basic search (first posted in the years
    ``date_from``..``date_to``, inclusive, when given): ``page_size`` from ``offset``, a multiple of ``page_size``.
    ``Hits.dropped`` counts the studies without an NCT number or title, ``Hits.total`` the query's studies."""
    if offset % page_size:
        raise ValueError(f"ClinicalTrials.gov pages by token: offset {offset} is not a multiple of {page_size}")
    params = {**_filters(query, date_from, date_to), "pageSize": page_size, "fields": FIELDS,
              "markupFormat": "legacy"}
    token: str | None = None
    total: int | None = None
    for _ in range(offset // page_size):
        rows, nxt, n = _page(http, params, token)
        total = n if total is None else total
        if not rows or not nxt or nxt == token:  # the results end before the offset
            return Hits(total=total)
        token = nxt
    rows, _, n = _page(http, params, token)
    out = Hits(total=n if total is None else total)
    for study in rows:
        item = study_to_item(study)
        if item is None:
            out.dropped += 1
        else:
            out.append(item)
    return out


def count(http: HttpClient, query: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
    """How many studies match ``query`` in the basic search (``totalCount`` of a one-study page), first posted in the
    years ``date_from``..``date_to`` (inclusive, either end open) when given; ``None`` when the response gives none."""
    data, _ = http.get_json(API, {**_filters(query, date_from, date_to), "pageSize": 1, "fields": "NCTId"})
    n = (data or {}).get("totalCount")
    return n if isinstance(n, int) else None
