"""Plan 5a Task 4: running the frozen search strategy with deduplication, the record cap and record languages."""

from __future__ import annotations

import json
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from rk.checkpoints import answer_checkpoint, get_checkpoint
from rk.config import Settings
from rk.discovery.boundary import transient_source_error
from rk.discovery.sources import log_and_search
from rk.discovery.store import searches
from rk.library.models import Ids, ItemIn
from rk.net.http import BadBody, HttpClient
from rk.production import store
from rk.production.models import Protocol, SearchStrategy
from rk.production.protocol import ProtocolWorker
from rk.production.search import (
    PAGE_LIMIT,
    check_record_cap,
    dropped_entries,
    incomplete_searches,
    language_exclusions,
    language_note,
    run_strategy,
    search_counts,
)
from rk.sources import arxiv, resolver
from rk.sources.identifiers import Identifier
from rk.sources.resolver import SourceContext, check_limits
from rk.store import db as db_mod
from rk.store.db import open_project_db
from tests.discovery_fakes import FakeSources, ScriptedExecutor
from tests.production_fakes import a_protocol, make_frozen_project, production_ctx


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _w(title: str, year: int | None = 2015, *, language: str | None = None, **ids) -> ItemIn:
    return ItemIn(title=title, year=year, ids=Ids(**ids), language=language)


class BySource(FakeSources):
    """``FakeSources`` whose hits depend on the source only: ``pages[source]`` is the source's whole result list, cut
    at ``offset`` and ``limit`` (duplicates kept, as a source may return them; with ``ignore_offset`` always from the
    start). Like the real sources it refuses a call beyond the source's paging limits."""

    def __init__(self, pages: dict[str, list[ItemIn]], *, ignore_offset: bool = False, **kw):
        super().__init__({}, **kw)
        self.pages, self.ignore_offset = pages, ignore_offset

    def search(self, query, source, limit=25, after_year=None, *, offset=0, date_from=None, date_to=None):
        check_limits(source, limit, offset)
        self.search_args.append({"query": query, "source": source, "limit": limit, "after_year": after_year,
                                 "offset": offset, "date_from": date_from, "date_to": date_to})
        start = 0 if self.ignore_offset else offset
        return self.pages.get(source, [])[start:start + limit]


def _ctx(env, sources, *, attempt: int = 1, stage: str = "search_screen", executor=None):
    k, ps, lib = env
    return production_ctx(k, ps, lib, executor or ScriptedExecutor({}), stage=stage, attempt=attempt,
                          sources=sources)


def _strategy(**kw) -> SearchStrategy:
    return SearchStrategy(**{"sources": ["openalex", "europepmc"], "queries": ["aspirin stroke"], "date_from": None,
                             "date_to": None, **kw})


def _logged(ps) -> list[dict]:
    return [e.payload for e in ps.log.iter() if e.type == "search.logged"]


def test_strategy_runs_every_query_on_every_source_and_logs(env):
    _, ps, lib = env
    src = FakeSources({"aspirin": [_w("Aspirin after 80", 2010, doi="10.1000/a"),
                                   _w("Aspirin bleeding", 2016, doi="10.1000/b"),
                                   _w("Aspirin in 1999", 1999, doi="10.1000/old"), _w("Aspirin undated", None)],
                       "antiplatelet": [_w("Antiplatelets in the old", 2012, doi="10.1000/c")]})
    queries, sources = ["aspirin stroke", "antiplatelet elderly"], ["openalex", "europepmc"]
    protocol = a_protocol(search_strategy=_strategy(queries=queries, sources=sources, date_from=2005, date_to=2020))
    res = run_strategy(_ctx(env, src), protocol)
    ids = res.record_ids
    assert (res.incomplete, res.stopped_at_cap) == ([], False)

    pairs = [(q, s) for q in queries for s in sources]
    assert src.search_args == [{"query": q, "source": s, "limit": PAGE_LIMIT, "after_year": None, "offset": 0,
                                "date_from": 2005, "date_to": 2020} for q, s in pairs]
    logged = _logged(ps)
    assert [(p["query"], p["source"], p["purpose"], p["stage"], p["attempt"], p["limit"], p["offset"],
             p["date_from"], p["date_to"]) for p in logged] == [
        (q, s, "protocol_search", "search_screen", 1, PAGE_LIMIT, 0, 2005, 2020) for q, s in pairs]
    assert [(r["offset_n"], r["date_from"], r["date_to"], r["after_year"], r["incomplete"], r["stopped_at_cap"])
            for r in searches(ps, "search_screen", 1, purpose="protocol_search")] == [(0, 2005, 2020, None, None,
                                                                                      False)] * 4

    recs = {r["id"]: r for r in store.records(ps, 1)}
    titles = [lib.get(recs[i]["item_id"]).title for i in ids]
    assert titles == ["Aspirin after 80", "Aspirin bleeding", "Antiplatelets in the old"]  # in range, dated
    assert [p["item_ids"] for p in logged] == [[recs[i]["item_id"] for i in ids[:2]]] * 2 + [
        [recs[ids[2]]["item_id"]]] * 2
    hits = lib.collection_by_name("Search hits")
    assert {it.id for it in lib.list(hits)} == {recs[i]["item_id"] for i in ids}
    firsts = [r for r in recs.values() if r["dedup_of"] is None]
    assert sorted(r["id"] for r in firsts) == sorted(ids)  # one record per library item
    assert len({r["item_id"] for r in firsts}) == len(firsts) == 3


def test_strategy_pages_up_to_the_cap_and_stops_early(env):
    _, ps, _ = env
    many = [_w(f"Work {i}", 2015, doi=f"10.1000/w{i}") for i in range(14)]
    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    ids = run_strategy(_ctx(env, src), a_protocol(search_strategy=_strategy(), record_cap=5)).record_ids
    # a page is cap + 1 records (at most PAGE_LIMIT): the first openalex page already has 6 > 5 distinct records
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [("openalex", 0, 6)]
    assert len(ids) == 6 and check_record_cap(ps, 1, a_protocol(record_cap=5)) == \
        "at least 6 records (search stopped at the cap) > cap 5: narrow the search strategy"

    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    run_strategy(_ctx(env, src, attempt=2), a_protocol(search_strategy=_strategy(), record_cap=20))
    # full pages of 20 > what each source has: one page each; 14 distinct works <= 20
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [("openalex", 0, 21),
                                                                                  ("europepmc", 0, 21)]
    assert search_counts(ps, 2) == {"identified": 18, "duplicates_removed": 4, "screened": 14}

    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    run_strategy(_ctx(env, src, attempt=3), a_protocol(search_strategy=_strategy(), record_cap=1000))
    assert [(a["source"], a["offset"]) for a in src.search_args] == [("openalex", 0), ("europepmc", 0)]

    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    res = run_strategy(_ctx(env, src, attempt=4), a_protocol(search_strategy=_strategy(), record_cap=12))
    # pages of 13: openalex has 9 (< 13: its last page); europepmc's last page brings 14 > 12 distinct: the whole
    # search ran, so the count is no lower bound
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [("openalex", 0, 13),
                                                                                  ("europepmc", 0, 13)]
    assert not res.stopped_at_cap
    assert check_record_cap(ps, 4, a_protocol(record_cap=12)) == "14 records > cap 12: narrow the search strategy"

    src = BySource({"openalex": many, "europepmc": []})
    res = run_strategy(_ctx(env, src, attempt=5), a_protocol(search_strategy=_strategy(), record_cap=6))
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [("openalex", 0, 7)]
    assert res.stopped_at_cap

    # a source with duplicates of its own and more than cap + 1 hits: paging goes by distinct works, not hits
    w = many
    src = BySource({"openalex": [w[0], w[1], w[0], w[2], w[1], w[3], w[0], w[4], w[0], w[5], w[6], w[7], w[8],
                                 w[9], w[10]], "europepmc": w[11:13]})
    res = run_strategy(_ctx(env, src, attempt=6), a_protocol(search_strategy=_strategy(), record_cap=6))
    # page 1: 7 hits, 4 works; page 2: 7 hits bring 10 > 6 works: stop
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [("openalex", 0, 7),
                                                                                  ("openalex", 7, 7)]
    assert search_counts(ps, 6) == {"identified": 14, "duplicates_removed": 4, "screened": 10}
    assert check_record_cap(ps, 6, a_protocol(record_cap=6)) == \
        "at least 10 records (search stopped at the cap) > cap 6: narrow the search strategy"

    src = BySource({"openalex": [w[0], w[1], w[0], w[2], w[1], w[3], w[0], w[4], w[0], w[1]], "europepmc": []})
    res = run_strategy(_ctx(env, src, attempt=7), a_protocol(search_strategy=_strategy(), record_cap=6))
    # its 10 hits are 5 works: page 2 is short (its last); 5 <= 6
    assert [(a["source"], a["offset"]) for a in src.search_args] == [("openalex", 0), ("openalex", 7),
                                                                      ("europepmc", 0)]
    assert len(res.record_ids) == 5 and check_record_cap(ps, 7, a_protocol(record_cap=6)) is None


def test_strategy_pages_through_a_source(env, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr("rk.production.search.PAGE_LIMIT", 4)
    many = [_w(f"Work {i}", 2015, doi=f"10.1000/w{i}") for i in range(14)]
    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    ids = run_strategy(_ctx(env, src), a_protocol(search_strategy=_strategy(), record_cap=20)).record_ids
    # pages of 4 until a short page
    assert [(a["source"], a["offset"], a["limit"]) for a in src.search_args] == [
        ("openalex", 0, 4), ("openalex", 4, 4), ("openalex", 8, 4),
        ("europepmc", 0, 4), ("europepmc", 4, 4), ("europepmc", 8, 4)]
    assert len(ids) == 14 and [p["offset"] for p in _logged(ps)] == [0, 4, 8, 0, 4, 8]

    src = BySource({"openalex": many[:9], "europepmc": many[5:]})
    run_strategy(_ctx(env, src, attempt=2), a_protocol(search_strategy=_strategy(), record_cap=8))
    # 9 distinct > 8 after the third openalex page: the search stops
    assert [(a["source"], a["offset"]) for a in src.search_args] == [("openalex", 0), ("openalex", 4),
                                                                      ("openalex", 8)]
    assert check_record_cap(ps, 2, a_protocol(record_cap=8)) == \
        "at least 9 records (search stopped at the cap) > cap 8: narrow the search strategy"


def test_a_source_ignoring_the_offset_is_an_incomplete_search(env, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr("rk.production.search.PAGE_LIMIT", 3)
    many = [_w(f"Work {i}", 2015, doi=f"10.1000/w{i}") for i in range(5)]
    src = BySource({"openalex": many, "europepmc": many[3:]}, ignore_offset=True)
    res = run_strategy(_ctx(env, src), a_protocol(search_strategy=_strategy(), record_cap=20))
    assert [(a["source"], a["offset"]) for a in src.search_args] == [("openalex", 0), ("openalex", 3),
                                                                      ("europepmc", 0)]
    why = ("openalex returned a full page of results it had already returned for query 'aspirin stroke' (from rank "
           "3): paging stopped there, so later results were not searched")
    assert res.incomplete == [why] == incomplete_searches(ps, 1)
    assert [r["incomplete"] for r in searches(ps, "search_screen", 1)] == [None, why, None]
    assert len(res.record_ids) == 5 and not res.stopped_at_cap


def test_s2_pages_within_its_limits_and_says_what_it_did_not_search(env):
    _, ps, _ = env
    works = [_w(f"Paper {i}", 2015, doi=f"10.1000/p{i}") for i in range(1100)]
    src = BySource({"s2": works})  # refuses limit > 100 and offset + limit > 1000, as Semantic Scholar does
    res = run_strategy(_ctx(env, src), a_protocol(search_strategy=_strategy(sources=["s2"])))  # record cap 3000
    assert [(a["offset"], a["limit"]) for a in src.search_args] == [(o, 100) for o in range(0, 1000, 100)]
    why = ("s2 returned only the first 1000 results for query 'aspirin stroke' (its paging limit): later results "
           "were not searched")
    assert res.incomplete == [why] == incomplete_searches(ps, 1)
    assert [r["incomplete"] for r in searches(ps, "search_screen", 1)] == [None] * 9 + [why]
    assert len(res.record_ids) == 1000 and check_record_cap(ps, 1, a_protocol()) is None


def test_duplicates_are_counted_not_screened(env):
    _, ps, lib = env
    a = _w("Aspirin in the very old", 2010, doi="10.1000/A")
    src = BySource({
        "openalex": [a, _w("Aspirin in the very old (preprint)", 2009, doi="https://doi.org/10.1000/a"),  # same DOI
                     _w("Stroke after eighty", 2012)],
        "europepmc": [_w("Aspirin in the very old.", 2010, pmid="1", doi="10.1000/a"),  # the DOI again
                      _w("Stroke After Eighty.", 2012),  # same title and year, no identifier
                      _w("Dabigatran over 80", 2014, pmid="2")]})
    ids = run_strategy(_ctx(env, src), a_protocol(search_strategy=_strategy())).record_ids
    recs = store.records(ps, 1)
    first = {r["item_id"]: r["id"] for r in recs if r["dedup_of"] is None}
    assert ids == list(first.values()) and len(ids) == 3  # what screening gets: one record per library item
    assert [r["dedup_of"] for r in recs] == [None, first[recs[0]["item_id"]], None, first[recs[0]["item_id"]],
                                             first[recs[2]["item_id"]], None]
    assert all(r["item_id"] == next(x for x in recs if x["id"] == r["dedup_of"])["item_id"]
               for r in recs if r["dedup_of"])
    assert search_counts(ps, 1) == {"identified": 6, "duplicates_removed": 3, "screened": 3}
    assert lib.get(recs[0]["item_id"]).ids.pmid == "1"  # the library merged the duplicate's identifiers
    assert check_record_cap(ps, 1, a_protocol()) is None


def _approve(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


DRAFT = {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}],
                         "exclude": [{"id": "E1", "text": "Animal studies"}]},
         "extraction_schema": [{"name": "n", "description": "Participants", "type": "number"}]}
STRATEGY = {"queries": ["aspirin stroke elderly", "aspirin primary prevention", "aspirin over 80"],
            "sources": ["openalex", "europepmc"], "date_from": 2000, "date_to": None}


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_record_cap_fails_the_gate(env):
    _, ps, _ = env
    works = [_w(f"Aspirin study {i}", 2001 + i, doi=f"10.1000/s{i}") for i in range(8)]
    src = FakeSources({"aspirin": works})
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [DRAFT, DRAFT], ("scout", "SearchStrategyOut"): [
        STRATEGY, STRATEGY]})
    w = ProtocolWorker()

    def protocol_attempt(attempt: int, cap: int) -> Protocol:
        cid = w.run(_ctx(env, src, attempt=attempt, stage="protocol", executor=ex)).checkpoint_id
        out = w.on_answer(_ctx(env, src, attempt=attempt, stage="protocol", executor=ex),
                          _approve(ps, cid, {"decision": "approve", "record_cap": cap}))
        assert out.status == "done"
        return Protocol.model_validate(store.frozen_protocol(ps)["body"])

    small = protocol_attempt(1, 5)
    assert small.record_cap == 5
    run_strategy(_ctx(env, src), small)  # the first page (6 hits) passes the cap with searches left: it stops
    assert check_record_cap(ps, 1, small) == \
        "at least 6 records (search stopped at the cap) > cap 5: narrow the search strategy"

    raised = protocol_attempt(2, 10)  # you raise the cap: a new frozen version, with a deviation
    frozen = store.frozen_protocol(ps)
    assert (raised.record_cap, frozen["status"]) == (10, "frozen")
    assert [d["to_version"] for d in store.deviations(ps)] == [frozen["version"]]
    ids = run_strategy(_ctx(env, src, attempt=2), raised).record_ids
    assert len(ids) == 8 and check_record_cap(ps, 2, raised) is None
    assert check_record_cap(ps, 2, small) == "8 records > cap 5: narrow the search strategy"


def test_non_english_marked_for_exclusion(env):
    _, ps, lib = env
    src = BySource({
        "pubmed": [_w("Aspirine chez le sujet âgé", 2015, pmid="11"),  # no language here ...
                   _w("Aspirin after 80", 2016, pmid="12", language="eng"),
                   _w("Aspirin without a language", 2017, pmid="13")],
        "openalex": [_w("Aspirine chez le sujet âgé", 2015, pmid="11", language="fr"),  # ... the other source has it
                     _w("ASS bei Hochbetagten", 2014, doi="10.1000/de", language="ger")]})
    protocol = a_protocol(search_strategy=_strategy(sources=["pubmed", "openalex"]))
    ids = run_strategy(_ctx(env, src), protocol).record_ids
    recs = {r["id"]: r for r in store.records(ps, 1)}
    langs = {lib.get(recs[i]["item_id"]).title: recs[i]["language"] for i in ids}
    assert langs == {"Aspirine chez le sujet âgé": "fr", "Aspirin after 80": "en",
                     "Aspirin without a language": "unknown", "ASS bei Hochbetagten": "de"}
    assert all(r["language"] == "fr" for r in recs.values() if r["dedup_of"])  # a duplicate has its item's language
    excluded = language_exclusions(ps, 1, protocol)
    assert sorted(lib.get(recs[i]["item_id"]).title for i in excluded) == ["ASS bei Hochbetagten",
                                                                            "Aspirine chez le sujet âgé"]
    assert language_note("unknown") == "language unknown: treated as English"
    assert language_note("en") is None and language_note("fr") is None
    wider = protocol.model_copy(update={"search_strategy": _strategy(languages=["en", "de"])})
    assert [lib.get(recs[i]["item_id"]).title for i in language_exclusions(ps, 1, wider)] == [
        "Aspirine chez le sujet âgé"]


# ---- the sources: date range, paging and language ----
def _client(tmp_path, respond) -> tuple[HttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return respond(req)

    http = HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None,
                      transport=httpx.MockTransport(handler))
    return http, seen


def _params(req: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}


def test_openalex_filters_the_years_and_pages(tmp_path):
    work = {"id": "https://openalex.org/W1", "title": "T", "publication_year": 2010, "language": "en"}
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, json={"results": [work]}))
    ctx = SourceContext(http=http, settings=Settings())
    hits = resolver.search(ctx, "aspirin", "openalex", 200, offset=400, date_from=2005, date_to=2020)
    assert hits[0].language == "en"
    assert _params(seen[-1]) == {"search": "aspirin", "per_page": "200", "page": "3",
                                 "filter": "publication_year:2005-2020"}
    resolver.search(ctx, "aspirin", "openalex", 50, date_from=2005)
    assert _params(seen[-1])["filter"] == "publication_year:>2004" and "page" not in _params(seen[-1])
    resolver.search(ctx, "aspirin", "openalex", 50, date_to=2020, after_year=2010)
    assert _params(seen[-1])["filter"] == "publication_year:2011-2020"
    resolver.search(ctx, "aspirin", "openalex", 50, after_year=2010)
    assert _params(seen[-1])["filter"] == "publication_year:>2010"
    with pytest.raises(ValueError, match="multiple of"):
        resolver.search(ctx, "aspirin", "openalex", 30, offset=40)
    n = len(seen)
    assert resolver.search(ctx, "aspirin", "openalex", 50, after_year=2020, date_to=2020) == []  # an empty range
    assert resolver.search(ctx, "aspirin", "pubmed", 50, date_from=2021, date_to=2020) == [] and len(seen) == n
    with pytest.raises(ValueError, match="at most 200 results per call and only its first 10000"):
        resolver.search(ctx, "aspirin", "openalex", 200, offset=9900)
    http.close()


def test_europepmc_pages_by_cursor_with_the_year_range(tmp_path):
    def respond(req):
        cursor = _params(req).get("cursorMark", "*")
        nxt = {"*": "c1", "c1": "c2", "c2": "c3"}[cursor]
        row = {"title": f"Page after {cursor}.", "pubYear": "2010", "pmid": cursor, "language": "fre"}
        return httpx.Response(200, json={"hitCount": 9, "nextCursorMark": nxt, "resultList": {"result": [row]}})

    http, seen = _client(tmp_path, respond)
    ctx = SourceContext(http=http, settings=Settings())
    hits = resolver.search(ctx, "aspirin", "europepmc", 2, offset=4, date_from=2005, date_to=2020)
    assert [h.title for h in hits] == ["Page after c2"] and hits[0].language == "fr"
    assert [_params(r).get("cursorMark") for r in seen] == [None, "c1", "c2"]
    assert {_params(r)["query"] for r in seen} == {"(aspirin) AND (PUB_YEAR:[2005 TO 2020])"}
    assert {_params(r)["pageSize"] for r in seen} == {"2"}
    resolver.search(ctx, "aspirin", "europepmc", 2, offset=4, date_from=2005, date_to=2020)
    assert len(seen) == 3  # the pages walked again come from the HTTP cache
    resolver.search(ctx, "aspirin", "europepmc", 2, date_to=2020)
    assert _params(seen[-1])["query"] == "(aspirin) AND (PUB_YEAR:[1000 TO 2020])"
    http.close()


def test_pubmed_arxiv_and_s2_take_the_range_and_offset(tmp_path):
    def respond(req):
        host = req.url.host
        if "eutils" in host and req.url.path.endswith("esearch.fcgi"):
            ids = ["7", "8"] if _params(req)["term"] == "two" else ["7"]
            return httpx.Response(200, json={"esearchresult": {"idlist": ids}})
        if "eutils" in host:
            return httpx.Response(200, json={"result": {"uids": ["7"], "7": {"title": "P", "pubdate": "2011",
                                                                               "lang": ["ger"]}}})
        if "arxiv" in host:
            return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        return httpx.Response(200, json={"data": []})

    http, seen = _client(tmp_path, respond)
    ctx = SourceContext(http=http, settings=Settings(s2_key="k"))
    hits = resolver.search(ctx, "aspirin", "pubmed", 100, offset=200, date_from=2005, date_to=2020)
    assert hits[0].language == "de"
    assert _params(seen[0]) == {"db": "pubmed", "term": "aspirin", "retmode": "json", "retmax": "100",
                                "retstart": "200", "datetype": "pdat", "mindate": "2005", "maxdate": "2020"}
    resolver.search(ctx, "aspirin", "arxiv", 100, offset=200, date_from=2005)
    assert _params(seen[-1]) == {"max_results": "100", "start": "200",
                                 "search_query": "all:aspirin AND submittedDate:[200501010000 TO 999912312359]"}
    resolver.search(ctx, "aspirin", "s2", 100, offset=200, date_to=2020)
    assert _params(seen[-1]) == {"query": "aspirin", "limit": "100", "offset": "200",
                                 "fields": "title,authors,year,venue,abstract,externalIds", "year": "-2020"}
    resolver.search(ctx, "aspirin", "pubmed", 100)
    last = [r for r in seen if r.url.path.endswith("esearch.fcgi")][-1]
    assert set(_params(last)) == {"db", "term", "retmode", "retmax"}  # no range, no offset: as before
    # a page is as full as its PMID list: a PMID that still has no summary when fetched again is a dropped entry
    # (counted, so the page is no short last page), never a silent miss
    n = len(seen)
    two = resolver.search(ctx, "two", "pubmed", 100)
    assert ([h.ids.pmid for h in two], two.dropped, two.raw) == (["7"], 1, 2)
    assert len([r for r in seen[n:] if r.url.path.endswith("esummary.fcgi")]) == 2  # fetched again once
    with pytest.raises(ValueError, match="s2 returns at most 100 results per call and only its first 1000"):
        resolver.search(ctx, "aspirin", "s2", 200)
    with pytest.raises(ValueError, match=r"asked for 100 from 950"):
        resolver.search(ctx, "aspirin", "s2", 100, offset=950)
    http.close()


def test_pubmed_short_summary_is_fetched_again_not_cached(tmp_path):
    """An esummary body short of its PMIDs (a transient cut) is dropped from the HTTP cache and fetched again at once;
    the full body found then is kept."""
    summaries = iter([["7"], ["7", "8"]])

    def respond(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["7", "8"]}})
        uids = next(summaries)
        return httpx.Response(200, json={"result": {"uids": uids, **{u: {"title": f"P{u}", "pubdate": "2011"}
                                                                      for u in uids}}})

    http, seen = _client(tmp_path, respond)
    ctx = SourceContext(http=http, settings=Settings())
    for _ in range(3):  # the later searches read the full body from the cache
        hits = resolver.search(ctx, "aspirin", "pubmed", 2)
        assert ([h.title for h in hits], hits.dropped) == (["P7", "P8"], 0)
    assert len([r for r in seen if r.url.path.endswith("esummary.fcgi")]) == 2
    http.close()


def test_pubmed_summary_missing_for_good_is_dropped_and_not_cached(tmp_path):
    def respond(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["7", "8"]}})
        return httpx.Response(200, json={"result": {"uids": ["7"], "7": {"title": "P7", "pubdate": "2011"}}})

    http, seen = _client(tmp_path, respond)
    ctx = SourceContext(http=http, settings=Settings())
    for _ in range(2):  # a withdrawn record: every search counts it dropped, and its short body is not kept
        hits = resolver.search(ctx, "aspirin", "pubmed", 2)
        assert ([h.title for h in hits], hits.dropped) == (["P7"], 1)
    assert len([r for r in seen if r.url.path.endswith("esummary.fcgi")]) == 4
    http.close()


@pytest.mark.parametrize("error", [lambda: httpx.Response(200, json={"esummaryresult": ["Unable to obtain query #1"]}),
                                   lambda: httpx.Response(404),
                                   lambda: httpx.Response(200, json={"result": {"uids": []}})],
                         ids=["error_body", "404", "empty_result"])
def test_pubmed_error_body_raises_for_a_retry_and_drops_nothing(tmp_path, error):
    """An esummary body with no ``result`` (NCBI's error body, a 404), also when fetched again at once, is a source
    error the run retries after a backoff, never a page of dropped records; the retry then finds them."""
    bodies = iter([error, error, lambda: httpx.Response(200, json={"result": {
        "uids": ["7", "8"], "7": {"title": "P7", "pubdate": "2011"}, "8": {"title": "P8", "pubdate": "2011"}}})])

    def respond(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["7", "8"]}})
        return next(bodies)()

    http, seen = _client(tmp_path, respond)
    ctx = SourceContext(http=http, settings=Settings())
    with pytest.raises(BadBody, match="no esummary result for 2 PMIDs"):
        resolver.search(ctx, "aspirin", "pubmed", 2)
    assert transient_source_error(BadBody("u", "x"))
    hits = resolver.search(ctx, "aspirin", "pubmed", 2)  # the run's retry: the error body was not kept
    assert ([h.title for h in hits], hits.dropped) == (["P7", "P8"], 0)
    assert len([r for r in seen if r.url.path.endswith("esummary.fcgi")]) == 3
    http.close()


def test_pubmed_uid_with_an_error_entry_is_dropped(tmp_path):
    def respond(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["7", "8"]}})
        return httpx.Response(200, json={"result": {"uids": ["7", "8"], "7": {"title": "P7", "pubdate": "2011"},
                                                    "8": {"uid": "8", "error": "cannot get document summary"}}})

    http, _ = _client(tmp_path, respond)
    hits = resolver.search(SourceContext(http=http, settings=Settings()), "aspirin", "pubmed", 2)
    assert ([h.title for h in hits], hits.dropped) == (["P7"], 1)  # no "(untitled)" record
    http.close()


def test_pubmed_page_of_only_withdrawn_uids_is_dropped_not_retried(tmp_path):
    """A well-formed result whose every uid is an error entry (withdrawn records) is a page of dropped entries, not a
    source error: retrying could never change it."""
    def respond(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["8"]}})
        return httpx.Response(200, json={"result": {"uids": ["8"],
                                                    "8": {"uid": "8", "error": "cannot get document summary"}}})

    http, _ = _client(tmp_path, respond)
    hits = resolver.search(SourceContext(http=http, settings=Settings()), "aspirin", "pubmed", 1)
    assert (list(hits), hits.dropped) == ([], 1)
    http.close()


def _arxiv_feed(*entries: tuple[str | None, str | None], total: int | None = None) -> bytes:
    """An arXiv Atom feed of ``(arxiv id, title)`` entries; ``None`` leaves the element out (a malformed entry).
    ``total`` is its ``opensearch:totalResults``."""
    body = "".join("<entry>" + (f"<id>http://arxiv.org/abs/{i}v1</id>" if i else "")
                   + (f"<title>{t}</title>" if t else "") + "<published>2015-01-01T00:00:00Z</published></entry>"
                   for i, t in entries)
    if total is not None:
        body = f"<os:totalResults>{total}</os:totalResults>{body}"
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:os="http://a9.com/-/spec/opensearch/1.1/">{body}'
            "</feed>").encode()


ARXIV_ERROR = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
               b"<id>http://arxiv.org/api/errors#incorrect_id_format_for_1234</id><title>Error</title>"
               b"<summary>incorrect id format for 1234</summary></entry></feed>")


def test_an_arxiv_error_feed_is_a_source_error_not_no_hits(tmp_path):
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=ARXIV_ERROR))
    ctx = SourceContext(http=http, settings=Settings())
    with pytest.raises(BadBody, match="incorrect id format for 1234") as e:
        resolver.search(ctx, "aspirin", "arxiv", 10)
    assert transient_source_error(e.value)  # stepped again in a run, recorded by the per-item boundary
    with pytest.raises(BadBody):
        arxiv.query(http, id_list=["1234"])
    with pytest.raises(BadBody):
        resolver.search(ctx, "aspirin", "arxiv", 10)
    assert len(seen) == 3  # the error body was not cached
    http.close()


def test_an_arxiv_id_arxiv_rejects_resolves_to_nothing(tmp_path):
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=ARXIV_ERROR))
    ctx = SourceContext(http=http, settings=Settings())
    # an id lookup arXiv answers with its error feed is a final answer (not found), not an outage
    assert resolver.resolve(ctx, Identifier(kind="arxiv", value="1234")) is None
    assert [r.url.host for r in seen] == ["export.arxiv.org"]
    # a search query arXiv answers with its error feed stays a transient source error
    with pytest.raises(BadBody) as e:
        resolver.search(ctx, "aspirin", "arxiv", 10)
    assert transient_source_error(e.value)
    http.close()


@pytest.mark.parametrize("body", [b"<html><body>Service unavailable", b"", b"<html><body>busy</body></html>"])
def test_an_arxiv_body_that_is_no_atom_feed_is_a_bad_body(tmp_path, body):
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=body))
    ctx = SourceContext(http=http, settings=Settings())
    with pytest.raises(BadBody, match="not XML|not an Atom feed") as e:
        resolver.search(ctx, "aspirin", "arxiv", 10)
    assert transient_source_error(e.value)
    with pytest.raises(BadBody):
        resolver.search(ctx, "aspirin", "arxiv", 10)
    assert len(seen) == 2  # the body was not cached
    http.close()


def test_arxiv_counts_the_entries_it_drops(tmp_path):
    feed = _arxiv_feed(("1501.00001", "A"), (None, "No id"), ("1501.00003", None), ("1501.00004", "D"),
                       ("1501.00005", "Error"))
    http, _ = _client(tmp_path, lambda req: httpx.Response(200, content=feed))
    hits = resolver.search(SourceContext(http=http, settings=Settings()), "aspirin", "arxiv", 5)
    # a real paper titled "Error" (an /abs/ id) is kept: only an /api/errors entry is arXiv's error feed
    assert [h.title for h in hits] == ["A", "D", "Error"] and hits.dropped == 2 and hits.raw == 5
    assert arxiv.parse_feed(feed).dropped == 2
    http.close()


class ArxivSources(FakeSources):
    """``FakeSources`` whose search is the real arXiv adapter over ``http``."""

    def __init__(self, http: HttpClient):
        super().__init__({})
        self.ctx = SourceContext(http=http, settings=Settings())

    def search(self, query, source, limit=25, after_year=None, *, offset=0, date_from=None, date_to=None):
        return resolver.search(self.ctx, query, source, limit, after_year=after_year, offset=offset,
                               date_from=date_from, date_to=date_to)


def test_a_full_arxiv_page_with_a_malformed_entry_keeps_paging_and_counts_it(env, tmp_path, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr("rk.production.search.PAGE_LIMIT", 3)
    pages = {0: _arxiv_feed(("1501.00001", "A"), (None, "Broken"), ("1501.00003", "C")),
             3: _arxiv_feed(("1501.00004", "D"))}
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=pages[int(_params(req).get("start", 0))]))
    res = run_strategy(_ctx(env, ArxivSources(http)), a_protocol(search_strategy=_strategy(sources=["arxiv"])))
    # the first page came back full (3 entries, one unreadable): paging goes on from rank 3, and ends on a short page
    assert [_params(r).get("start") for r in seen] == [None, "3"]
    # one unreadable record, not a truncated search: counted on its page, not incomplete
    assert res.incomplete == [] == incomplete_searches(ps, 1)
    assert [(r["incomplete"], r["dropped"]) for r in searches(ps, "search_screen", 1)] == [(None, 1), (None, 0)]
    assert [e["dropped"] for e in _logged(ps)] == [1, 0]
    assert dropped_entries(ps, 1) == {"arxiv": 1} and dropped_entries(ps, 2) == {}
    assert len(res.record_ids) == 3 and not res.stopped_at_cap
    # a discovery search counts the drop too
    log_and_search(ps, ArxivSources(http), stage="gaps", attempt=1, purpose="gap_check", query="aspirin stroke",
                   source="arxiv", limit=3, actor="t")
    assert [(r["incomplete"], r["dropped"]) for r in searches(ps, "gaps", 1)] == [(None, 1)]
    assert dropped_entries(ps, 1) == {"arxiv": 1}  # the protocol search's only
    http.close()


def test_an_arxiv_page_of_only_malformed_entries_keeps_paging(env, tmp_path, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr("rk.production.search.PAGE_LIMIT", 3)
    pages = {0: _arxiv_feed((None, "X"), ("1501.00002", None), (None, None)), 3: _arxiv_feed(("1501.00004", "D"))}
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=pages[int(_params(req).get("start", 0))]))
    res = run_strategy(_ctx(env, ArxivSources(http)), a_protocol(search_strategy=_strategy(sources=["arxiv"])))
    # a full page of nothing readable is no "full page of results already returned": the second page is fetched
    assert [_params(r).get("start") for r in seen] == [None, "3"]
    assert res.incomplete == [] and dropped_entries(ps, 1) == {"arxiv": 3} and len(res.record_ids) == 1
    http.close()


def test_a_short_arxiv_page_inside_the_result_set_is_fetched_again(tmp_path):
    short = _arxiv_feed(("1501.00001", "A"), total=10)
    full = _arxiv_feed(("1501.00001", "A"), ("1501.00002", "B"), ("1501.00003", "C"), total=10)
    answers = [short, full]
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=answers.pop(0)))
    hits = arxiv.query(http, search_query="all:aspirin", max_results=3)
    assert [h.title for h in hits] == ["A", "B", "C"] and hits.total == 10 and len(seen) == 2
    # the last page of the result set is short and that is no fault: not fetched again
    http2, seen2 = _client(tmp_path / "2", lambda req: httpx.Response(200, content=_arxiv_feed(("1501.00009", "Z"),
                                                                                               total=10)))
    assert len(arxiv.query(http2, search_query="all:aspirin", max_results=3, start=9)) == 1 and len(seen2) == 1
    http.close()
    http2.close()


def test_a_short_arxiv_page_that_stays_short_makes_the_search_incomplete(env, tmp_path, monkeypatch):
    _, ps, _ = env
    monkeypatch.setattr("rk.production.search.PAGE_LIMIT", 3)
    short = _arxiv_feed(("1501.00001", "A"), ("1501.00002", "B"), total=10)
    http, seen = _client(tmp_path, lambda req: httpx.Response(200, content=short))
    res = run_strategy(_ctx(env, ArxivSources(http)), a_protocol(search_strategy=_strategy(sources=["arxiv"])))
    assert [_params(r).get("start") for r in seen] == [None, None]  # fetched again once, short again
    why = ("arxiv returned a short page (2 of 3 results) at start=0 of totalResults=10 for query 'aspirin stroke': "
           "paging stopped there, so later results were not searched")
    assert res.incomplete == [why] == incomplete_searches(ps, 1)
    resolver.search(ArxivSources(http).ctx, "aspirin stroke", "arxiv", 3)
    assert len(seen) == 4  # the short page was not cached: a later search fetches it again
    http.close()


def test_language_codes_are_normalized():
    assert ItemIn(title="x", language="ENG").language == "en"
    assert ItemIn(title="x", language="und").language is None
    assert ItemIn(title="x", language=" pt-BR ").language == "pt"
    assert ItemIn(title="x", language="zh_Hant").language == "zh"
    assert json.loads(ItemIn(title="x").model_dump_json())["language"] is None


def _old_db(tmp_path):
    """A project database whose search_log predates the added columns, with one row and its event."""
    db = tmp_path / "old.sqlite"
    conn = open_project_db(db)
    conn.execute("DROP TABLE search_log")
    conn.execute("CREATE TABLE search_log (id TEXT PRIMARY KEY, stage TEXT NOT NULL, attempt INTEGER NOT NULL, "
                 "purpose TEXT NOT NULL, source TEXT NOT NULL, query TEXT NOT NULL, limit_n INTEGER NOT NULL, "
                 "item_ids TEXT NOT NULL, ts TEXT NOT NULL)")
    conn.execute("INSERT INTO search_log VALUES ('sch_1', 'gaps', 1, 'gap_check', 'openalex', 'q', 25, '[]', 't')")
    conn.execute("INSERT INTO events(evt_id, ts, project_id, actor_kind, actor, type, entity_id, payload, hash) "
                 "VALUES ('evt_1', 't', 'p', 'kernel', 'k', 'search.logged', 'sch_1', ?, 'h')",
                 (json.dumps({"after_year": 2015}),))
    conn.close()
    return db


def test_an_older_search_log_gets_the_new_columns(tmp_path):
    conn = open_project_db(_old_db(tmp_path))
    row = dict(conn.execute("SELECT * FROM search_log").fetchone())
    assert {k: row[k] for k in ("after_year", "offset_n", "date_from", "date_to", "incomplete", "stopped_at_cap",
                                "dropped")} == {"after_year": 2015, "offset_n": 0, "date_from": None, "date_to": None,
                                                "incomplete": None, "stopped_at_cap": 0, "dropped": 0}
    conn.close()


class _Conn:
    """A connection to the old database that runs ``hook(sql)`` before each statement."""

    def __init__(self, db, hook):
        self._conn = sqlite3.connect(db, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self.hook = hook

    def execute(self, sql, *args):
        self.hook(sql)
        return self._conn.execute(sql, *args)


def test_two_opens_of_an_older_project_migrate_it_once(tmp_path):
    """Another process opens the old project between this one's look at the columns and its ALTER: no "duplicate
    column name", and the columns are added and backfilled once."""
    db = _old_db(tmp_path)
    seen, other = [], []

    def open_other_first(sql):
        if seen and not other:  # right after this process read search_log's columns
            other.append(open_project_db(db))  # the other process migrates the project meanwhile
        if sql.startswith("PRAGMA table_info(search_log)"):
            seen.append(sql)

    conn = _Conn(db, open_other_first)
    db_mod._add_columns(conn)
    assert conn.execute("SELECT after_year FROM search_log").fetchone()[0] == 2015
    other[0].close()


def test_a_crash_inside_the_migration_leaves_the_project_to_migrate_again(tmp_path):
    db = _old_db(tmp_path)

    def crash_at_backfill(sql):
        if sql.startswith("UPDATE search_log SET after_year"):
            raise sqlite3.OperationalError("simulated crash between ALTER and backfill")

    with pytest.raises(sqlite3.OperationalError, match="simulated crash"):
        db_mod._add_columns(_Conn(db, crash_at_backfill))
    conn = open_project_db(db)  # opened again: the columns are added and backfilled
    assert conn.execute("SELECT after_year FROM search_log").fetchone()[0] == 2015
    conn.close()


def test_a_new_project_waits_for_another_process_creating_it(tmp_path):
    """Another process holds the brand-new database (still in rollback-journal mode) while this one opens it: the
    open waits its busy timeout for the switch to WAL instead of failing with "database is locked"."""
    db = tmp_path / "new.sqlite"
    other = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    other.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")  # the other's first write
    other.execute("BEGIN EXCLUSIVE")
    # held past the 5 s busy handler Python's sqlite3.connect sets by default: only the busy timeout set before the
    # switch to WAL (10 s) outlasts it
    release = threading.Timer(5.5, lambda: other.execute("COMMIT"))
    release.start()
    try:
        conn = open_project_db(db)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.close()
    finally:
        release.join()
        other.close()
