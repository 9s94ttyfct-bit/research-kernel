"""Stated limitations: the GapMiner quotes limitations the authors state in the representative works; the kernel keeps
the quote as a verified quote note (V-QUOTE), checks it starts in the last 40% of the text layer (Plan 4 deviation 3),
searches for later work and has Triage judge whether any of it addressed the limitation, on verbatim evidence."""

from __future__ import annotations

from rk.discovery.gaps import (
    FAIL,
    READ_TOOLS,
    Checks,
    MinerResult,
    cluster_of,
    fulltext_clusters,
    gap_check_search,
    gap_check_sources,
    in_record,
    task_context,
    work,
)
from rk.discovery.models import (
    Gap,
    GapEvidence,
    LaterWorkOut,
    LimitationOut,
    LimitationsOut,
)
from rk.fulltext import latest_snapshot
from rk.notes import Locator, NoteIn, NoteRejected, add_note
from rk.orchestrator.engine import StageContext
from rk.store.project import ProjectStore

MAX_LIMITATIONS = 6
POSITION_MIN = 0.6  # the quote starts at >= 60% of the text layer
CHECKS = ("quote_note", "position", "later_search", "later_judgement")

MINE_TASK = (
    "Find limitations that the authors of the given works state themselves. Read each work's text with rk_text_read "
    "(pages numbered from 1) and rk_text_find, in the discussion, conclusion, limitations and future-work sections, "
    "usually on the last pages. For each limitation give the work's item id as source_id, a quote of one or two "
    "sentences copied character for character from the text (the kernel rejects a quote that is not verbatim or "
    "starts in the first 60% of the text), and an English later_query that finds later work which may have addressed "
    "the limitation (the kernel searches only works published after the source). At most 6 limitations, the most "
    "consequential first. Follow the guidance when there is any.")
LATER_TASK = (
    "Decide which of the given works address the stated limitation, i.e. study what the source says was missing. Use "
    "only the given works and their item ids. For each id in addressed, give in basis that id with a quote copied "
    "character for character from the work's title or abstract that shows it; an id without such a quote does not "
    "count. When none does, return empty lists.")


def _position(ps: ProjectStore, item_id: str, loc: Locator) -> float:
    """Where the located quote starts, as a share of the whole text layer."""
    _, layer = latest_snapshot(ps, item_id)
    before = sum(len(p.text) for p in layer.pages if p.n < loc.page)
    total = sum(len(p.text) for p in layer.pages)
    return (before + loc.start) / total if total else 0.0


def _judge_later(ctx: StageContext, it: LimitationOut, later: list[str], checks: Checks, details: dict) -> None:
    """Triage over the later works; a work counts as addressing the limitation only with a verbatim basis quote."""
    details["addressed"], details["rejected"] = [], []
    if not later:
        checks.passed("later_judgement")
        return
    source = ctx.lib.get(it.source_id)
    res = ctx.executor.run("triage", LATER_TASK,
                           {"limitation": {"source": {"title": source.title, "year": source.year}, "quote": it.quote},
                            "works": [work(ctx.lib, i) for i in later]},
                           LaterWorkOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
    if not res.ok or res.output is None:
        checks.failed("later_judgement", f"triage gave no judgement ({res.error})")
        return
    claimed = list(dict.fromkeys(res.output.addressed))
    given = set(later)
    addressed = [i for i in claimed if i in given and in_record(ctx.lib, i, res.output.basis.get(i, ""))]
    details["addressed"] = addressed
    details["rejected"] = [i for i in claimed if i not in addressed]  # unknown ids or no verbatim basis
    if addressed:
        checks.failed("later_judgement", f"{len(addressed)} later work(s) address the limitation")
    else:
        checks.passed("later_judgement")


def _check(ctx: StageContext, it: LimitationOut, cluster_of: dict[str, str]) -> tuple[Gap, bool, dict]:
    checks = Checks(CHECKS)
    details: dict = {"source_id": it.source_id, "quote": it.quote, "later_query": it.later_query}
    evidence = GapEvidence()
    cluster_id = cluster_of.get(it.source_id)
    statement = f'Stated limitation: "{it.quote}"'
    if cluster_id is None:
        checks.failed("quote_note", "not a cluster item")
        return Gap(kind="stated_limitation", statement=statement, evidence=evidence, details=details), False, \
            checks.dump()

    source = ctx.lib.get(it.source_id)
    statement = f'{source.title} ({source.year or "n.d."}) states this limitation: "{it.quote}"'
    evidence.item_ids = [it.source_id]
    try:
        note = add_note(ctx.ps, ctx.lib, NoteIn(source_id=it.source_id, kind="quote", text=it.quote,
                                                role_in_source="qualification", keywords=["limitation"]),
                        actor=ctx.actor, actor_kind="agent")
    except NoteRejected as e:
        checks.failed("quote_note", e.reason)
    except ValueError as e:  # an empty quote
        checks.failed("quote_note", str(e))
    else:
        checks.passed("quote_note")
        evidence.note_ids = [note.note_id]
        share = _position(ctx.ps, it.source_id, note.locator)
        details["position"] = round(share, 3)
        if share >= POSITION_MIN:
            checks.passed("position")
        else:
            checks.failed("position", f"the quote starts at {share:.0%} of the text (need >= {POSITION_MIN:.0%})")

    if FAIL not in checks.values.values():
        if not it.later_query.strip():
            checks.failed("later_search", "no later query")
        elif source.year is None:
            checks.failed("later_search", "source year unknown")
        else:
            sources = gap_check_sources(ctx)
            later = [i for i in gap_check_search(ctx, it.later_query, after_year=source.year) if i != it.source_id]
            details["after_year"], details["later_ids"], details["sources"] = source.year, later, sources
            checks.passed("later_search")
            _judge_later(ctx, it, later, checks, details)
            if checks.ok:
                statement += (f"; no later work (published after {source.year}) addressing it was found within the "
                              f'searched scope (query "{it.later_query}" on {", ".join(sources)})')

    gap = Gap(kind="stated_limitation", statement=statement, cluster_id=cluster_id, evidence=evidence,
              details=details)
    return gap, checks.ok, checks.dump()


def mine(ctx: StageContext, clusters: list[dict]) -> MinerResult:
    sources = fulltext_clusters(ctx, clusters)
    if not sources:
        return MinerResult(problems=["no representative work has a full text for the limitation miner"])

    res = ctx.executor.run("gapminer", MINE_TASK, {**task_context(ctx), "clusters": sources}, LimitationsOut,
                           stage=ctx.stage, attempt=ctx.attempt, tools=READ_TOOLS)
    if not res.ok or res.output is None:
        return MinerResult(problems=[f"the gapminer gave no limitations ({res.error})"])
    of = cluster_of(clusters)
    return MinerResult(gaps=[_check(ctx, it, of) for it in res.output.items[:MAX_LIMITATIONS]])
