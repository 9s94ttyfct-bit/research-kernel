"""ClinicalTrials.gov (API v2) as a search source: registry records mapped to library items, token paging, the
first-posted year range and counts, on responses recorded live (scripts/record_fixtures.py, group ``registry``)."""

import httpx
import pytest

from rk.discovery.sources import COUNTED_SOURCES, KernelSources, log_and_search_hits
from rk.library.models import Ids
from rk.net.http import HttpClient
from rk.sources import clinicaltrials
from rk.sources.identifiers import normalize_nct
from rk.sources.resolver import SEARCH_LIMITS, search

from .discovery_fakes import make_project

QUERY = "aspirin AND stroke"  # scripts/record_fixtures.REGISTRY_QUERY


def test_search_maps_registry_records(replay_http):
    """A study is a ``report`` record of ClinicalTrials.gov: its official title, the year first posted, its lead sponsor,
    its brief summary as plain text and its NCT number with its study page."""
    hits = clinicaltrials.search(replay_http, QUERY, page_size=3)
    assert (len(hits), hits.dropped, hits.total) == (3, 0, 1032)
    first = hits[0]
    assert first.item_type == "report" and first.venue == "ClinicalTrials.gov"
    assert first.title == "Antiplatelet Therapy for Patients Undergoing Transcatheter Aortic Valve Implantation"
    assert first.year == 2014 and [c.family for c in first.creators] == ["St. Antonius Hospital"]
    assert first.ids.nct == "NCT02247128" and first.ids.url == "https://clinicaltrials.gov/study/NCT02247128"
    assert first.abstract.startswith("At present, a variety of antithrombotic regimens are prescribed in the early "
                                     "postprocedure period")
    assert all("\r" not in h.abstract and "\n" not in h.abstract and "  " not in h.abstract for h in hits)
    assert all(h.ids.nct and h.ids.url.endswith(h.ids.nct) for h in hits)
    assert ("nct", "NCT02247128") in first.ids.pairs()


def test_search_pages_by_token(replay_http):
    """A later page walks the pages before it by their ``nextPageToken`` and keeps the query's total; an offset off
    the page grid is refused."""
    page1 = clinicaltrials.search(replay_http, QUERY, page_size=3)
    page2 = clinicaltrials.search(replay_http, QUERY, page_size=3, offset=3)
    assert [h.ids.nct for h in page2] == ["NCT00822536", "NCT06521463", "NCT03578822"]
    assert not {h.ids.nct for h in page1} & {h.ids.nct for h in page2}
    assert page2.total == 1032
    assert page2[0].title == "Optimal Duration of Dual Antiplatelet Therapy After Drug Eluting Stent (DES) Implantation"
    with pytest.raises(ValueError, match="multiple of 3"):
        clinicaltrials.search(replay_http, QUERY, page_size=3, offset=2)


def test_search_and_count_in_first_posted_years(replay_http):
    """The publication years filter on the date a study was first posted (``filter.advanced``), either end open."""
    assert clinicaltrials.first_posted(None, None) is None
    assert clinicaltrials.first_posted(2010, 2012) == "AREA[StudyFirstPostDate]RANGE[2010-01-01,2012-12-31]"
    assert clinicaltrials.first_posted(2010, None) == "AREA[StudyFirstPostDate]RANGE[2010-01-01,MAX]"
    assert clinicaltrials.first_posted(None, 2001) == "AREA[StudyFirstPostDate]RANGE[MIN,2001-12-31]"
    hits = clinicaltrials.search(replay_http, QUERY, page_size=3, date_from=2010, date_to=2012)
    assert len(hits) == 3 and hits.total == 102 and all(2010 <= h.year <= 2012 for h in hits)
    assert clinicaltrials.count(replay_http, QUERY) == 1032
    assert clinicaltrials.count(replay_http, QUERY, date_from=2010, date_to=2012) == 102
    assert clinicaltrials.count(replay_http, QUERY, date_to=2001) == 7


def test_unreadable_studies_are_dropped_and_counted(tmp_path):
    """A study without an NCT number or a title is dropped and counted; a response without ``totalCount`` gives no
    count."""
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["query.term"] == "q"
        return httpx.Response(200, json={"studies": [
            {"protocolSection": {"identificationModule": {"nctId": "NCT01234567", "briefTitle": "A trial."}}},
            {"protocolSection": {"identificationModule": {"briefTitle": "No id"}}},
            {"protocolSection": {"identificationModule": {"nctId": "NCT07654321"}}}]})

    http = HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None,
                      transport=httpx.MockTransport(handler))
    hits = clinicaltrials.search(http, "q", page_size=3)
    assert [h.title for h in hits] == ["A trial"] and hits.dropped == 1 + 1 and hits.raw == 3
    assert (hits[0].year, hits[0].creators, hits[0].abstract) == (None, [], None)
    assert clinicaltrials.count(http, "q") is None
    http.close()


def test_nct_numbers_normalise():
    assert normalize_nct(" nct01234567 ") == "NCT01234567"
    assert normalize_nct("https://clinicaltrials.gov/study/NCT01234567") == "NCT01234567"
    assert normalize_nct("https://clinicaltrials.gov/ct2/show/NCT01234567") == "NCT01234567"
    assert all(normalize_nct(s) is None for s in (None, "", "NCT0123456", "ISRCTN12345678", "NCT012345678"))
    assert Ids(nct="nct01234567").normalized().nct == "NCT01234567"


def test_kernel_sources_search_count_and_deduplicate(tmp_home, replay_http, source_ctx):
    """ClinicalTrials.gov is a search source (last, so the discovery stages' first sources are unchanged), counted for
    the record-cap probe, and a study found again joins its library item by NCT number."""
    assert SEARCH_LIMITS["clinicaltrials"].page == 1000 and "clinicaltrials" in COUNTED_SOURCES
    later = search(source_ctx, QUERY, "clinicaltrials", 3, after_year=2014)
    assert later.total == 1032 and all(h.year > 2014 for h in later)
    k, ps, lib = make_project(tmp_home, replay_http)
    try:
        src = KernelSources(k, "demo")
        assert src.sources()[-1] == "clinicaltrials" and src.sources()[:2] == ["openalex", "europepmc"]
        assert src.count(QUERY, "clinicaltrials", date_from=2010, date_to=2012) == 102
        kw = {"stage": "search_screen", "attempt": 1, "purpose": "test", "query": QUERY, "source": "clinicaltrials",
              "limit": 3, "actor": "t"}
        first = log_and_search_hits(ps, src, **kw)
        again = log_and_search_hits(ps, src, **kw)
        assert again == first and len(set(first)) == 3
        item = lib.get(first[0])
        assert item.ids.nct == "NCT02247128" and lib.find_by_ids(Ids(nct="nct02247128")) == first[0]
    finally:
        k.close()
