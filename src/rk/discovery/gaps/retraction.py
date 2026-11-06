"""Retracted but still cited (deterministic, no role task): a cluster work retracted or withdrawn on a known date that
works published after the retraction year still cite. The citing works are added to the library as evidence."""

from __future__ import annotations

from rk.discovery.gaps import Checks, MinerResult
from rk.discovery.models import Gap, GapEvidence
from rk.orchestrator.engine import StageContext

RETRACTED = frozenset({"retracted", "withdrawn"})
CHECKS = ("retraction_date", "cited_after_retraction")


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _check(ctx: StageContext, cluster_id: str, item_id: str) -> tuple[Gap, bool, dict] | None:
    info = ctx.sources.retraction(item_id)
    if info.status not in RETRACTED:
        return None
    item = ctx.lib.get(item_id)
    checks = Checks(CHECKS)
    data = {"retraction_sources": info.sources, "notice_doi": info.notice_doi, "citing_ids": []}
    details = {"retraction_status": info.status, "retraction_date": info.date}
    year = _year(info.date)
    if year is None:
        checks.failed("retraction_date", "retraction date unknown")
        statement = (f'"{item.title}" ({item.year or "n.d."}) was {info.status} on an unknown date; citations after '
                     "the retraction could not be checked.")
        gap = Gap(kind="retracted_still_cited", statement=statement, cluster_id=cluster_id,
                  evidence=GapEvidence(item_ids=[item_id]), details=details)
        return gap, False, checks.dump(**data)

    checks.passed("retraction_date")
    citing: list[str] = []
    for it in ctx.sources.cited_by(item_id, after_year=year):
        iid = ctx.lib.add_item(it, added_by="agent", actor=ctx.actor, actor_kind="kernel").item_id
        if iid != item_id and iid not in citing:
            citing.append(iid)
    if not citing:
        return None  # retracted and no longer cited: not a gap
    checks.passed("cited_after_retraction")
    details["after_year"] = year
    statement = (f'"{item.title}" ({item.year or "n.d."}) was {info.status} in {year}, yet {len(citing)} work(s) '
                 f"published after {year} cite it.")
    gap = Gap(kind="retracted_still_cited", statement=statement, cluster_id=cluster_id,
              evidence=GapEvidence(item_ids=[item_id, *citing]), details=details)
    return gap, checks.ok, checks.dump(**{**data, "citing_ids": citing})


def mine(ctx: StageContext, clusters: list[dict]) -> MinerResult:
    out = MinerResult()
    seen: set[str] = set()
    for c in clusters:
        for iid in c["item_ids"]:
            if iid in seen:
                continue
            seen.add(iid)
            found = _check(ctx, c["id"], iid)
            if found is not None:
                out.gaps.append(found)
    return out
