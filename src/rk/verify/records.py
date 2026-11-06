"""Verification records (event-sourced) and item-level reference/retraction verification."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from rk.canon import canonical_json
from rk.ids import new_id
from rk.library.service import Library
from rk.sources.identifiers import parse_identifier
from rk.sources.resolver import SourceContext, resolve
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.verify.ref import CitedRef, RefResult, compare
from rk.verify.retract import FLAGGED, RetractionInfo, retraction_status

FLAG_COLLECTION = "Retracted / EoC"


def record_verification(ps: ProjectStore, target_type: str, target_id: str, verifier: str, verdict: str,
                        details: dict, *, actor: str) -> str:
    vid = new_id("ver")
    ps.apply(NewEvent(
        type="verification.recorded", entity_id=target_id, actor_kind="kernel", actor=actor,
        payload={"id": vid, "target_type": target_type, "verifier": verifier, "verdict": verdict, "details": details,
                 "ts": datetime.now(UTC).isoformat(timespec="seconds")},
    ))
    return vid


def _best_identifier(ids) -> str | None:
    for k in ("doi", "pmid", "arxiv", "pmcid", "openalex"):
        v = getattr(ids, k)
        if v:
            return f"arXiv:{v}" if k == "arxiv" else v
    return None


def verify_item(ps: ProjectStore, lib: Library, ctx: SourceContext, item_id: str, *,
                actor: str) -> tuple[RefResult, RetractionInfo]:
    item = lib.get(item_id)
    ident = _best_identifier(item.ids)
    cited = CitedRef(identifier=ident, title=item.title, first_author=item.creators[0].family if item.creators else None,
                     year=item.year)
    if ident is None:
        ref, resolved = RefResult(verdict="page_metadata_only", details={"reason": "no identifier"}), None
    else:
        resolved = resolve(ctx, parse_identifier(ident))
        ref = compare(cited, resolved)
    ret = retraction_status(resolved)
    lib.update(item_id, ref_status=ref.verdict, retraction=ret.status, actor=actor)
    record_verification(ps, "item", item_id, "V-REF", ref.verdict, ref.details, actor=actor)
    record_verification(ps, "item", item_id, "V-RETRACT", ret.status, ret.model_dump(), actor=actor)
    if ret.status in FLAGGED:
        cols = lib.ensure_default_collections(actor=actor)
        lib.collect(item_id, cols[FLAG_COLLECTION], actor=actor, actor_kind="kernel")
    return ref, ret


@projection("verification.recorded")
def _verification_recorded(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT OR REPLACE INTO verifications(id, target_type, target_id, verifier, verdict, details, ts)"
        " VALUES (?,?,?,?,?,?,?)",
        (p["id"], p["target_type"], ev.entity_id, p["verifier"], p["verdict"], canonical_json(p["details"]), p["ts"]),
    )
