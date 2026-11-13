"""The search of ``search_screen``: the frozen protocol's search strategy, run with deduplication.

``run_strategy`` runs every query on every source of the strategy, in the strategy's publication-year range, through
``log_and_search_hits`` (each page a logged search, purpose ``protocol_search``; the hits go to the library's
"Search hits" collection). A page asks for ``PAGE_LIMIT`` hits, or fewer: the source's own page limit
(``rk.sources.resolver.SEARCH_LIMITS``) or ``record_cap + 1``. A source and query is paged until

- a page shorter than asked for (its last page);
- more than ``record_cap`` distinct works were found over the whole search: the search stops (the cap is exceeded, so
  the rest does not matter), and the page is logged ``stopped_at_cap`` when searches or pages were left;
- the source's paging limit (e.g. Semantic Scholar's first 1,000 results) with the cap not exceeded: the search is
  incomplete, which the page's log and ``SearchResult.incomplete`` say ("s2 returned only the first 1000 results
  ...");
- a full page that brings no work this source and query had not already returned (a source ignoring the offset):
  incomplete too, said the same way;
- a short page that ends before the source's total (``rk.sources.hits.Hits.total``; arXiv's ``totalResults``, its
  page already fetched again once): incomplete ("arxiv returned a short page (2 of 3 results) at start=0 of
  totalResults=10 ..."). Paging never ends silently.

A page is full when the source returned ``limit`` entries, counting the ones it returned but could not be read
(``rk.sources.hits.Hits.dropped``, e.g. an arXiv entry without an id or title, or a PubMed PMID still without a
summary when fetched again): paging goes on past them. A dropped entry is one unreadable record, not a truncated
search: the page's log counts it (``dropped``, not ``incomplete``) and ``dropped_entries`` sums the counts for the
PRISMA notes.

Run again in the same attempt (an attempt interrupted after or during its search resumes in place), the search asks no
source again for a page the attempt already logged under the protocol it works under and the same year range
(``_Paging.replay``: the logged hits and what the page said), and a hit already stored as a record keeps that record:
no search request and no record is made twice. A page is replayed hit by hit (its logged ``hit_ids``, a hit the
library merged into an item already on the page repeating that id), so hit i of the replayed search is hit i of the
first pass and keeps its record. A page logged before ``hit_ids`` were (its distinct ``item_ids`` only) is replayed
from the attempt's stored records when they line up with the logged pages (``_legacy_hits``), else asked for again:
its distinct ids alone never make records.

A failed search is never "nothing found": its error propagates (a transient one steps the run again, spec §11.3).

Every hit is a record. The library merges a hit into the item of the same work (identifier, then title and year), so
the first record of a library item is the one screened, and every later hit of that item, from the same page, another
page, another source or another query, is a duplicate record (``dedup_of`` = the first record). So PRISMA's
"identified" counts the hits of every source and query (a work found by two queries on one source is identified
twice) and "duplicates removed" includes the cross-query duplicates: screened = identified - duplicates removed.
Calibration (search and screening, 2026-11-12): the merge fills in what the item lacks from the later hit, its
identifiers, open-access URLs, language and abstract (``Library.add_item``), so a work OpenAlex returned first without
an abstract takes Europe PMC's; screening then looks up the abstracts still missing (``rk.production.screening``).

A record's language is its item's language field (from OpenAlex, Europe PMC or PubMed; any source that has it fills
it in for the item), else ``"unknown"``, which screening treats as English (``language_note``). A record in a known
language outside the protocol's ``languages`` is excluded at screening with the reason "language" (§11.8).

``check_record_cap`` is the authoritative record cap: more deduplicated records than the protocol's ``record_cap``
send the stage back to ``protocol`` (the protocol's record-count probe is advisory only)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

from rk.discovery.sources import log_and_search_hits
from rk.discovery.store import SearchNote
from rk.library.models import normalize_language
from rk.orchestrator.engine import StageContext
from rk.production.models import Protocol, SearchStrategy
from rk.production.store import add_record, records
from rk.sources.hits import Hits
from rk.sources.resolver import search_limits
from rk.store.project import ProjectStore
from rk.store.query import query_rows

PURPOSE = "protocol_search"
PAGE_LIMIT = 200  # hits per search call at most
UNKNOWN_LANGUAGE = "unknown"
ENGLISH = "en"


@dataclass(frozen=True)
class SearchResult:
    """What ``run_strategy`` found: ``record_ids``, the deduplicated records (one per library item, in the order first
    found), the records to screen; ``incomplete``, why results the strategy needed were not searched (for the stage's
    reason and the PRISMA notes); ``stopped_at_cap``, the search stopped once it passed the record cap."""
    record_ids: list[str]
    incomplete: list[str]
    stopped_at_cap: bool


class _Paging:
    """One source and query's paging over the search's ``found`` works; ``note`` is passed to
    ``log_and_search_hits`` and judges each page (its hit ids and the source's ``Hits``: the entries it dropped and
    the query's total) before it is logged."""

    def __init__(self, source: str, query: str, page: int, depth: int | None, cap: int, found: set[str],
                 more_searches: bool):
        self.source, self.query, self.page, self.depth, self.cap = source, query, page, depth, cap
        self.found, self.more_searches = found, more_searches
        self.offset, self.raw = 0, 0
        self.seen: set[str] = set()
        self.ended, self.last = False, SearchNote()

    def note(self, ids: list[str], hits: Hits) -> SearchNote:
        self.raw = len(ids) + hits.dropped
        full, new = self.raw == self.page, set(ids) - self.seen
        at_depth = self.depth is not None and self.offset + 2 * self.page > self.depth
        self.ended, note = True, SearchNote()
        if len(self.found | new) > self.cap:
            note = SearchNote(stopped_at_cap=self.more_searches or (full and not at_depth))
        elif full and ids and not new:
            note = SearchNote(incomplete=f"{self.source} returned a full page of results it had already returned for "
                                         f"query {self.query!r} (from rank {self.offset}): paging stopped there, so "
                                         "later results were not searched")
        elif full and at_depth:
            note = SearchNote(incomplete=f"{self.source} returned only the first {self.offset + self.page} results "
                                         f"for query {self.query!r} (its paging limit): later results were not "
                                         "searched")
        elif not full and hits.total is not None and self.offset + self.raw < hits.total:
            note = SearchNote(incomplete=f"{self.source} returned a short page ({self.raw} of {self.page} results) at "
                                         f"start={self.offset} of totalResults={hits.total} for query {self.query!r}: "
                                         "paging stopped there, so later results were not searched")
        else:
            self.ended = not full
        self.last = note
        return note

    def replay(self, ids: list[str], logged: dict) -> None:
        """Take a page the attempt already logged as ``note`` judged it then: its hits and what it said."""
        self.raw = len(ids) + (logged.get("dropped") or 0)
        new = set(ids) - self.seen
        self.last = SearchNote(incomplete=logged.get("incomplete"), stopped_at_cap=bool(logged.get("stopped_at_cap")),
                               dropped=logged.get("dropped") or 0)
        self.ended = bool(logged.get("incomplete")) or len(self.found | new) > self.cap or self.raw != self.page

    def took(self, ids: list[str]) -> None:
        self.seen.update(ids)
        self.found.update(ids)
        self.offset += self.raw


def _key(p: dict, offset: int | None = None) -> tuple:
    """A logged page's key: (source, query, offset, limit, date_from, date_to); ``offset`` replaces the page's."""
    at = (p.get("offset") or 0) if offset is None else offset
    return (p["source"], p["query"], at, p["limit"], p.get("date_from"), p.get("date_to"))


def _logged_pages(ps: ProjectStore, stage: str, attempt: int) -> dict[tuple, dict]:
    """The attempt's logged search pages under the protocol it works under now (logged after its latest
    ``attempt.protocol`` mark: a page of an earlier protocol is not replayed), by ``_key``, in the order they were
    logged: the first of each, or the first with ``hit_ids`` when an earlier one lacks them (asked for again)."""
    mark = ps.db.execute("SELECT MAX(seq) FROM events WHERE type='attempt.protocol' AND json_extract(payload, "
                         "'$.stage')=? AND json_extract(payload, '$.attempt')=?", (stage, attempt)).fetchone()[0]
    rows = ps.db.execute("SELECT seq, payload FROM events WHERE type='search.logged' AND seq>? AND "
                         "json_extract(payload, '$.stage')=? AND json_extract(payload, '$.attempt')=? AND "
                         "json_extract(payload, '$.purpose')=? ORDER BY seq", (mark or 0, stage, attempt, PURPOSE))
    out: dict[tuple, tuple[int, dict]] = {}
    for r in rows:
        p = json.loads(r["payload"])
        key = _key(p)
        if key not in out or (out[key][1].get("hit_ids") is None and p.get("hit_ids") is not None):
            out[key] = (r["seq"], p)
    return {k: p for k, (_, p) in sorted(out.items(), key=lambda kv: kv[1][0])}


def _slice_lengths(distinct: list[str], items: list[str], pos: int, n: int | None) -> list[int]:
    """The lengths, longest first, of the runs of ``items`` from ``pos`` that can be the hits of a page whose distinct
    hit item ids are ``distinct``: those ids in their order, each later hit of an item already on the page repeating
    it; only ``n`` when the page's number of hits is known."""
    seen: set[str] = set()
    j, k, out = 0, pos, [0] if not distinct else []
    while k < len(items) and (n is None or k - pos < n):
        if j < len(distinct) and items[k] == distinct[j]:
            seen.add(items[k])
            j += 1
        elif items[k] not in seen:
            break
        k += 1
        if j == len(distinct):
            out.append(k - pos)
    return [m for m in reversed(out) if n is None or m == n]


def _legacy_hits(logged: dict[tuple, dict], stored: list[str]) -> dict[tuple, list[str]]:
    """The hits of each logged page without ``hit_ids`` (logged before they were), read from the attempt's stored
    records (``stored``: their item ids in record order, the hits of the first pass's whole search, since records
    are made after it): the logged pages, in the order logged, must cover the records in order, a page with
    ``hit_ids`` exactly, a page without them as its distinct ids with later hits of an item on the page repeating it
    (``_slice_lengths``), each record used once; a page followed by the next page of its query and source was full
    (``limit`` hits less the dropped ones). ``{}`` when there is no such page or the records do not line up (those
    pages are asked for again). The search is iterative over (page, record position) and never retries a position
    that could not reach the last page, so a long log neither overflows the stack nor backtracks without end."""
    pages = list(logged.values())
    if all(p.get("hit_ids") is not None for p in pages):
        return {}

    def known(p: dict) -> int | None:
        if _key(p, (p.get("offset") or 0) + p["limit"]) in logged:
            return p["limit"] - (p.get("dropped") or 0)
        return None

    def lengths(i: int, pos: int) -> list[int]:
        p = pages[i]
        if (hits := p.get("hit_ids")) is not None:
            return [len(hits)] if stored[pos:pos + len(hits)] == hits else []
        return _slice_lengths(p["item_ids"], stored, pos, known(p))

    dead: set[tuple[int, int]] = set()  # (page, position) from which no alignment reaches the last page
    stack: list[tuple[int, int, Iterator[int]]] = [(0, 0, iter(lengths(0, 0)))]
    end = None
    while stack:
        i, pos, todo = stack[-1]
        if (m := next(todo, None)) is None:
            dead.add((i, pos))
            stack.pop()
        elif i + 1 == len(pages):
            end = pos + m
            break
        elif (i + 1, pos + m) not in dead:
            stack.append((i + 1, pos + m, iter(lengths(i + 1, pos + m))))
    if end is None:
        return {}
    starts = [pos for _, pos, _ in stack]
    found = [stored[a:b] for a, b in zip(starts, [*starts[1:], end], strict=True)]
    return {_key(p): hits for p, hits in zip(pages, found, strict=True) if p.get("hit_ids") is None}


def _search(ctx: StageContext, strategy: SearchStrategy, cap: int) -> tuple[list[str], list[str], bool]:
    """One item id per hit in search order, the incomplete reasons, and whether the search stopped at the cap. A page
    the attempt already logged, under the same protocol and year range, is replayed hit by hit, not asked for again
    (one logged without ``hit_ids`` only when the stored records give its hits, ``_legacy_hits``)."""
    hits: list[str] = []
    incomplete: list[str] = []
    found: set[str] = set()
    logged = _logged_pages(ctx.ps, ctx.stage, ctx.attempt)
    legacy = _legacy_hits(logged, [r["item_id"] for r in records(ctx.ps, ctx.attempt)])
    pairs = [(q, s) for q in strategy.queries for s in strategy.sources]
    for i, (query, source) in enumerate(pairs):
        lim = search_limits(source)
        p = _Paging(source, query, min(PAGE_LIMIT, cap + 1, *([lim.page] if lim else [])),
                    lim.depth if lim else None, cap, found, more_searches=i < len(pairs) - 1)
        while not p.ended:
            key = (source, query, p.offset, p.page, strategy.date_from, strategy.date_to)
            page = logged.get(key)
            if page is not None and (ids := page.get("hit_ids", legacy.get(key))) is not None:
                p.replay(ids, page)
            else:
                ids = log_and_search_hits(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose=PURPOSE,
                                          query=query, source=source, limit=p.page, actor=ctx.actor,
                                          offset=p.offset, date_from=strategy.date_from, date_to=strategy.date_to,
                                          note=p.note)
            hits += ids
            p.took(ids)
            if p.last.incomplete:
                incomplete.append(p.last.incomplete)
            if len(found) > cap:
                return hits, incomplete, p.last.stopped_at_cap
    return hits, incomplete, False


def run_strategy(ctx: StageContext, protocol: Protocol) -> SearchResult:
    """Run the protocol's search strategy for ``ctx.attempt`` and add a record per hit (a hit the attempt already
    stored keeps its record)."""
    hits, incomplete, stopped = _search(ctx, protocol.search_strategy, protocol.record_cap)
    stored = records(ctx.ps, ctx.attempt)
    first: dict[str, str] = {}  # item id -> its first record
    languages: dict[str, str] = {}
    for i, item_id in enumerate(hits):  # after the whole search, so every source's language field reached the item
        if item_id not in languages:
            languages[item_id] = ctx.lib.get(item_id).language or UNKNOWN_LANGUAGE
        dup = first.get(item_id)
        if i < len(stored) and stored[i]["item_id"] == item_id:
            rid = stored[i]["id"]
        else:
            stored = []  # the stored records end here (or differ): every later hit gets a new record
            rid = add_record(ctx.ps, ctx.attempt, item_id, dedup_of=dup, language=languages[item_id], actor=ctx.actor)
        if dup is None:
            first[item_id] = rid
    return SearchResult(list(first.values()), incomplete, stopped)


def _searches(ps: ProjectStore, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM search_log WHERE purpose=? AND attempt=? ORDER BY rowid", (PURPOSE, attempt),
                      ("item_ids",))


def incomplete_searches(ps: ProjectStore, attempt: int) -> list[str]:
    """Why the attempt's search did not search every result it needed (the PRISMA notes show them), in search
    order."""
    return [r["incomplete"] for r in _searches(ps, attempt) if r["incomplete"]]


def dropped_entries(ps: ProjectStore, attempt: int) -> dict[str, int]:
    """How many entries each source returned in the attempt's search that could not be read as works (dropped, so
    neither recorded nor screened; for the PRISMA notes), for the sources that dropped any, in search order."""
    out: dict[str, int] = {}
    for r in _searches(ps, attempt):
        if r["dropped"]:
            out[r["source"]] = out.get(r["source"], 0) + r["dropped"]
    return out


def deduplicated(ps: ProjectStore, attempt: int) -> list[dict]:
    """The attempt's records that are no duplicate: one per library item, the ones screened."""
    return [r for r in records(ps, attempt) if r["dedup_of"] is None]


def search_counts(ps: ProjectStore, attempt: int) -> dict[str, int]:
    """PRISMA's search counts of the attempt: records identified (every hit of every source and query, so a work
    two queries found on one source counts twice), duplicates removed (every later hit of a work, across pages,
    sources and queries) and records screened (identified - duplicates removed: one per work)."""
    recs = records(ps, attempt)
    dups = sum(1 for r in recs if r["dedup_of"] is not None)
    return {"identified": len(recs), "duplicates_removed": dups, "screened": len(recs) - dups}


def check_record_cap(ps: ProjectStore, attempt: int, protocol: Protocol) -> str | None:
    """Why the attempt's deduplicated records exceed the protocol's ``record_cap`` (the reason of the back-edge to
    ``protocol``), else ``None``. When the search stopped at the cap, the count is what it had found by then."""
    n, cap = len(deduplicated(ps, attempt)), protocol.record_cap
    if n <= cap:
        return None
    if any(r["stopped_at_cap"] for r in _searches(ps, attempt)):
        return f"at least {n} records (search stopped at the cap) > cap {cap}: narrow the search strategy"
    return f"{n} records > cap {cap}: narrow the search strategy"


def language_note(language: str) -> str | None:
    """The note screening shows for a record's language: an unknown language is treated as English."""
    return "language unknown: treated as English" if language == UNKNOWN_LANGUAGE else None


def language_in_scope(language: str, languages: list[str]) -> bool:
    """Whether a record in ``language`` is in the protocol's ``languages``; ``"unknown"`` counts as English."""
    wanted = {normalize_language(x) for x in languages}
    return (ENGLISH if language == UNKNOWN_LANGUAGE else normalize_language(language)) in wanted


def language_exclusions(ps: ProjectStore, attempt: int, protocol: Protocol) -> list[str]:
    """The deduplicated records screening excludes with the reason "language": a known language outside the
    protocol's ``languages``."""
    langs = protocol.search_strategy.languages
    return [r["id"] for r in deduplicated(ps, attempt) if not language_in_scope(r["language"], langs)]
