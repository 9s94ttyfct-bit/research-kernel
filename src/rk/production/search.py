"""The search of ``search_screen``: the frozen protocol's search strategy, run with deduplication.

``run_strategy`` runs every query on every source of the strategy, in the strategy's publication-year range, through
``log_and_search_hits`` (each page a logged search, purpose ``protocol_search``; the hits go to the library's
"Search hits" collection). Pages hold ``PAGE_LIMIT`` hits, or ``record_cap + 1`` when that is fewer; a source and
query is paged up to ``record_cap + 1`` hits (whole pages), and the search stops once more than ``record_cap``
distinct works were found, which is enough to know the cap is exceeded. A failed search is never "nothing found": its
error propagates (a transient one steps the run again, spec §11.3).

Every hit is a record (PRISMA "identified"). The library merges a hit into the item of the same work (identifier,
then title and year), so the first record of a library item is the one screened and every later hit of that item is a
duplicate record (``dedup_of`` = the first record): screened = identified - duplicates removed.

A record's language is its item's language field (from OpenAlex, Europe PMC or PubMed; any source that has it fills
it in for the item), else ``"unknown"``, which screening treats as English (``language_note``). A record in a known
language outside the protocol's ``languages`` is excluded at screening with the reason "language" (§11.8).

``check_record_cap`` is the authoritative record cap: more deduplicated records than the protocol's ``record_cap``
send the stage back to ``protocol`` (the protocol's record-count probe is advisory only)."""

from __future__ import annotations

from rk.discovery.sources import log_and_search_hits
from rk.library.models import normalize_language
from rk.orchestrator.engine import StageContext
from rk.production.models import Protocol, SearchStrategy
from rk.production.store import add_record, records
from rk.store.project import ProjectStore

PURPOSE = "protocol_search"
PAGE_LIMIT = 200  # hits per search call
UNKNOWN_LANGUAGE = "unknown"
ENGLISH = "en"


def _search(ctx: StageContext, strategy: SearchStrategy, cap: int) -> list[str]:
    """One item id per hit, in search order; stops once more than ``cap`` distinct items were found."""
    page = min(PAGE_LIMIT, cap + 1)
    hits: list[str] = []
    found: set[str] = set()
    for query in strategy.queries:
        for source in strategy.sources:
            got = 0
            while got <= cap:  # cap + 1 hits per source and query at most (whole pages)
                ids = log_and_search_hits(ctx.ps, ctx.sources, stage=ctx.stage, attempt=ctx.attempt, purpose=PURPOSE,
                                          query=query, source=source, limit=page, actor=ctx.actor, offset=got,
                                          date_from=strategy.date_from, date_to=strategy.date_to)
                hits += ids
                found.update(ids)
                if len(found) > cap:
                    return hits
                if len(ids) < page:  # the source's last page
                    break
                got += len(ids)
    return hits


def run_strategy(ctx: StageContext, protocol: Protocol) -> list[str]:
    """Run the protocol's search strategy for ``ctx.attempt`` and add a record per hit; returns the ids of the
    deduplicated records (one per library item, in the order first found), the records to screen."""
    hits = _search(ctx, protocol.search_strategy, protocol.record_cap)
    first: dict[str, str] = {}  # item id -> its first record
    languages: dict[str, str] = {}
    for item_id in hits:  # after the whole search, so every source's language field reached the item
        if item_id not in languages:
            languages[item_id] = ctx.lib.get(item_id).language or UNKNOWN_LANGUAGE
        dup = first.get(item_id)
        rid = add_record(ctx.ps, ctx.attempt, item_id, dedup_of=dup, language=languages[item_id], actor=ctx.actor)
        if dup is None:
            first[item_id] = rid
    return list(first.values())


def deduplicated(ps: ProjectStore, attempt: int) -> list[dict]:
    """The attempt's records that are no duplicate: one per library item, the ones screened."""
    return [r for r in records(ps, attempt) if r["dedup_of"] is None]


def search_counts(ps: ProjectStore, attempt: int) -> dict[str, int]:
    """PRISMA's search counts of the attempt: records identified, duplicates removed, records screened."""
    recs = records(ps, attempt)
    dups = sum(1 for r in recs if r["dedup_of"] is not None)
    return {"identified": len(recs), "duplicates_removed": dups, "screened": len(recs) - dups}


def check_record_cap(ps: ProjectStore, attempt: int, protocol: Protocol) -> str | None:
    """Why the attempt's deduplicated records exceed the protocol's ``record_cap`` (the reason of the back-edge to
    ``protocol``), else ``None``. A search that stopped at the cap counts the records it found."""
    n, cap = len(deduplicated(ps, attempt)), protocol.record_cap
    return f"{n} records > cap {cap}: narrow the search strategy" if n > cap else None


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
