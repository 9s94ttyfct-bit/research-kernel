"""Retracted but still cited (deterministic, no role task): a cluster work retracted or withdrawn on a known date that
works published after the retraction year still cite. The retraction status is the V-RETRACT result landscape
recorded for the work (looked up again only when there is none, or it was unknown); the citation lookup is logged like
a search and the citing works are added to the library as evidence. One work's failed lookup is reported and the
miner goes on."""

from __future__ import annotations

import json

from rk.discovery.boundary import per_item
from rk.discovery.gaps import Checks, MinerResult
from rk.discovery.models import Gap, GapEvidence
from rk.discovery.sources import log_and_cited_by
from rk.orchestrator.engine import StageContext
from rk.store.project import ProjectStore
from rk.verify.retract import RetractionInfo

RETRACTED = frozenset({"retracted", "withdrawn"})
CHECKS = ("retraction_date", "cited_after_retraction")


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def recorded_retraction(ps: ProjectStore, item_id: str) -> RetractionInfo | None:
    """The latest V-RETRACT result recorded for the item, unless there is none or it was ``unknown``."""
    r = ps.db.execute("SELECT details FROM verifications WHERE target_type='item' AND target_id=? AND "
                      "verifier='V-RETRACT' ORDER BY ts DESC, rowid DESC LIMIT 1", (item_id,)).fetchone()
    if r is None:
        return None
    info = RetractionInfo.model_validate(json.loads(r["details"]))
    return None if info.status == "unknown" else info


def _check(ctx: StageContext, cluster_id: str, item_id: str,
           problems: list[str]) -> tuple[Gap, bool, dict] | None:
    info = recorded_retraction(ctx.ps, item_id)
    if info is None:
        info, err = per_item(f"retraction lookup of {item_id}", lambda: ctx.sources.retraction(item_id))
        if err:
            problems.append(err)
            return None
    if info.status not in RETRACTED:
        return None
    item = ctx.lib.get(item_id)
    checks = Checks(CHECKS)
    data = {"retraction_sources": info.sources, "notice_doi": info.notice_doi, "citing_ids": []}
    details = {"retraction_status": info.status, "retraction_date": info.date}
    year = _year(info.date)
    unchecked = (f'"{item.title}" ({item.year or "n.d."}) was {info.status} {{when}}; citations after the retraction '
                 "could not be checked.")
    if year is None:
        checks.failed("retraction_date", "retraction date unknown")
        gap = Gap(kind="retracted_still_cited", statement=unchecked.format(when="on an unknown date"),
                  cluster_id=cluster_id, evidence=GapEvidence(item_ids=[item_id]), details=details)
        return gap, False, checks.dump(**data)

    checks.passed("retraction_date")
    found, err = per_item(f"citation lookup of {item_id}",
                          lambda: log_and_cited_by(ctx.ps, ctx.sources, item_id, stage=ctx.stage, attempt=ctx.attempt,
                                                   purpose="gap_check", actor=ctx.actor, after_year=year))
    if err:
        checks.failed("cited_after_retraction", err)
        details["after_year"] = year
        gap = Gap(kind="retracted_still_cited", statement=unchecked.format(when=f"in {year}"),
                  cluster_id=cluster_id, evidence=GapEvidence(item_ids=[item_id]), details=details)
        return gap, False, checks.dump(**data)
    citing = [i for i in found if i != item_id]
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
            found = _check(ctx, c["id"], iid, out.problems)
            if found is not None:
                out.gaps.append(found)
    return out
