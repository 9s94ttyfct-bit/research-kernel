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
(``rk.sources.hits.Hits.dropped``, e.g. an arXiv entry without an id or title): paging goes on past them. A dropped
entry is one unreadable record, not a truncated search: the page's log counts it (``dropped``, not ``incomplete``)
and ``dropped_entries`` sums the counts for the PRISMA notes.

A failed search is never "nothing found": its error propagates (a transient one steps the run again, spec §11.3).

Every hit is a record. The library merges a hit into the item of the same work (identifier, then title and year), so
the first record of a library item is the one screened, and every later hit of that item, from the same page, another
page, another source or another query, is a duplicate record (``dedup_of`` = the first record). So PRISMA's
"identified" counts the hits of every source and query (a work found by two queries on one source is identified
twice) and "duplicates removed" includes the cross-query duplicates: screened = identified - duplicates removed.

A record's language is its item's language field (from OpenAlex, Europe PMC or PubMed; any source that has it fills
it in for the item), else ``"unknown"``, which screening treats as English (``language_note``). A record in a known
language outside the protocol's ``languages`` is excluded at screening with the reason "language" (§11.8).

``check_record_cap`` is the authoritative record cap: more deduplicated records than the protocol's ``record_cap``
send the stage back to ``protocol`` (the protocol's record-count probe is advisory only)."""

from __future__ import annotations

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

    def took(self, ids: list[str]) -> None:
        self.seen.update(ids)
        self.found.update(ids)
        self.offset += self.raw


def _search(ctx: StageContext, strategy: SearchStrategy, cap: int) -> tuple[list[str], list[str], bool]:
    """One item id per hit in search order, the incomplete reasons, and whether the search stopped at the cap."""
    hits: list[str] = []
    incomplete: list[str] = []
    found: set[str] = set()
    pairs = [(q, s) for q in strategy.queries for s in strategy.sources]
    for i, (query, source) in enumerate(pairs):
        lim = search_limits(source)
        p = _Paging(source, query, min(PAGE_LIMIT, cap + 1, *([lim.page] if lim else [])),
                    lim.depth if lim else None, cap, found, more_searches=i < len(pairs) - 1)
        while not p.ended:
            ids = log_and_search_hits(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose=PURPOSE,
                                      query=query, source=source, limit=p.page, actor=ctx.actor, offset=p.offset,
                                      date_from=strategy.date_from, date_to=strategy.date_to, note=p.note)
            hits += ids
            p.took(ids)
            if p.last.incomplete:
                incomplete.append(p.last.incomplete)
            if len(found) > cap:
                return hits, incomplete, p.last.stopped_at_cap
    return hits, incomplete, False


def run_strategy(ctx: StageContext, protocol: Protocol) -> SearchResult:
    """Run the protocol's search strategy for ``ctx.attempt`` and add a record per hit."""
    hits, incomplete, stopped = _search(ctx, protocol.search_strategy, protocol.record_cap)
    first: dict[str, str] = {}  # item id -> its first record
    languages: dict[str, str] = {}
    for item_id in hits:  # after the whole search, so every source's language field reached the item
        if item_id not in languages:
            languages[item_id] = ctx.lib.get(item_id).language or UNKNOWN_LANGUAGE
        dup = first.get(item_id)
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
