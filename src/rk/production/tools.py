"""The production read tools: the protocol (``rk_protocol_get``), the PRISMA flow or the selection reasons
(``rk_prisma_get``), the screening decisions (``rk_screening_list``) and the extraction (``rk_extraction_get``).
Registered by ``build_registry``; ``rk.roles.policy`` says which roles read which. Each shows the attempt its stage
shows (``rk.orchestrator.state.shown_attempt``: the one that passed, else the latest that ran)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict

from pydantic import BaseModel

from rk.discovery.store import intake_of
from rk.orchestrator.state import attempt_ran, shown_attempt, stage_row
from rk.production.extraction import (
    extraction_limitations,
    included_studies,
    usable_evidence,
)
from rk.production.models import PHASES, ScreeningPhase
from rk.production.protocol import (
    counts_as_preregistration,
    probe_lines,
    probe_of,
    protocol_prereg_ok,
    render_protocol,
)
from rk.production.screening import reports_prisma
from rk.production.store import (
    decisions,
    deviations,
    extraction_notes,
    extractions,
    finals,
    frozen_protocol,
    protocol_row,
    records,
)
from rk.store.project import ProjectStore
from rk.store.query import query_rows
from rk.tools.registry import CallContext, ToolError
from rk.verify.prisma import (
    check_prisma,
    check_selection_reasons,
    flow,
    prisma_counts,
    prisma_notes,
)

SEARCH_SCREEN, READ_EXTRACT = "search_screen", "read_extract"


class ProtocolGet(BaseModel):
    project: str
    protocol_id: str | None = None  # default: the version in force, else the latest draft


class PrismaGet(BaseModel):
    project: str
    attempt: int | None = None  # default: the attempt search_screen shows


class ScreeningList(BaseModel):
    project: str
    phase: ScreeningPhase | None = None
    final: bool = False  # the final decisions instead of each screener's


class ExtractionGet(BaseModel):
    project: str
    item_id: str | None = None


def paper_type_of(ps: ProjectStore) -> str | None:
    intake = intake_of(ps)
    return intake.paper_type if intake is not None else ps.meta("paper_type")


def _titles(ps: ProjectStore) -> dict[str, str]:
    """library item id -> title."""
    return {r["id"]: r["t"] or "" for r in ps.db.execute("SELECT id, json_extract(data, '$.title') AS t FROM items")}


def _note(ps: ProjectStore, note_id: str | None) -> dict | None:
    """How an extraction value's note shows: ``against`` is what its quote was verified against (``text``: the full
    text; ``abstract``)."""
    r = ps.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone() if note_id else None
    if r is None:
        return None
    n = json.loads(r["data"])
    quote = (n.get("verified") or {}).get("quote") or {}
    return {"id": n["id"], "kind": n["kind"], "text": n["text"], "against": quote.get("against", "text"),
            "verified": bool(quote.get("ok")), "locator": n.get("locator")}


def _sufficiency(row: dict) -> str:
    return "full" if row["sufficient"] else "low"


def protocol_view(ps: ProjectStore, protocol_id: str | None = None) -> dict:
    """The protocol ``protocol_id`` (default: the version in force, else the latest), its probe and rendering, and
    every version and deviation."""
    in_force = frozen_protocol(ps)
    if protocol_id:
        row = protocol_row(ps, protocol_id)
        if row is None:
            raise ToolError("not_found", f"no protocol {protocol_id}", status=404)
    else:
        latest = ps.db.execute("SELECT id FROM protocols ORDER BY rowid DESC LIMIT 1").fetchone()
        row = in_force or (protocol_row(ps, latest["id"]) if latest else None)
    paper_type = paper_type_of(ps)
    probe = probe_of(ps, row["id"]) if row else None
    return {"protocol": row, "in_force": in_force["id"] if in_force else None, "paper_type": paper_type,
            "preregistration": bool(paper_type) and counts_as_preregistration(paper_type),
            "prereg_ok": protocol_prereg_ok(ps), "probe": probe,
            "probe_lines": probe_lines(probe, row["body"]["record_cap"]) if row else [],
            "text": "\n".join(render_protocol(ps, row)) if row else "",
            "versions": query_rows(ps, "SELECT id, version, attempt, status, frozen_hash, frozen_at FROM protocols "
                                       "ORDER BY version, rowid", ()),
            "deviations": deviations(ps)}


def _screeners(ps: ProjectStore, attempt: int) -> dict[tuple[str, str], list[dict]]:
    """(record id, phase) -> what each screener decided on it."""
    out: dict[tuple[str, str], list[dict]] = {}
    for d in decisions(ps, attempt):
        out.setdefault((d["record_id"], d["phase"]), []).append(
            {"screener": d["screener"], "include": d["include"], "criterion": d["criterion"], "reason": d["reason"]})
    return out


def _final_rows(ps: ProjectStore, attempt: int, phase: str | None) -> list[dict]:
    titles = _titles(ps)
    item = {r["id"]: r["item_id"] for r in records(ps, attempt)}
    return [{"record_id": f["record_id"], "item_id": item.get(f["record_id"]),
             "title": titles.get(item.get(f["record_id"]), ""), "phase": f["phase"], "include": f["include"],
             "decided_by": f["decided_by"], "reason": f["reason"], "carried_from": f["carried_from"]}
            for f in finals(ps, attempt, phase=phase)]


def prisma_view(ps: ProjectStore, attempt: int | None = None) -> dict:
    """The PRISMA flow of a ``search_screen`` attempt (default: the one it shows) for the systematic and scoping
    reviews: the counts (``prisma``, ``rk.verify.prisma.prisma_counts``), V-PRISMA (``check``: ``[]`` when the
    arithmetic holds and every exclusion has a reason) and the one-line ``flow``. For the other paper types the
    selection reasons instead (``selection``: every final with each screener's decision, and ``selection_check``).
    For every type the PRISMA ``notes`` (incomplete searches, dropped entries, the P1 vote's exclusions, language
    exclusions, abstract-only assessments, carried decisions). Nothing before the attempt ran."""
    paper_type = paper_type_of(ps)
    reports = bool(paper_type) and reports_prisma(paper_type)
    if attempt is None:
        attempt = shown_attempt(ps, SEARCH_SCREEN)
    out = {"attempt": attempt, "paper_type": paper_type, "reports_prisma": reports, "prisma": None, "check": [],
           "flow": None, "notes": [], "selection": [], "selection_check": []}
    if attempt is None or not attempt_ran(ps, SEARCH_SCREEN, attempt):
        return out
    p = prisma_counts(ps, attempt)
    out["notes"] = prisma_notes(p)
    if reports:
        out.update(prisma=asdict(p), check=check_prisma(p), flow=flow(p))
    else:
        said = _screeners(ps, attempt)
        rows = [r | {"screeners": said.get((r["record_id"], r["phase"]), [])} for r in _final_rows(ps, attempt, None)]
        rows.sort(key=lambda r: PHASES.index(r["phase"]))
        out.update(selection=rows, selection_check=check_selection_reasons(ps, attempt))
    return out


def screening_view(ps: ProjectStore, phase: str | None, final: bool) -> dict:
    """The screening decisions of the attempt ``search_screen`` shows: each screener's (default) or the finals."""
    attempt = shown_attempt(ps, SEARCH_SCREEN)
    if attempt is None:
        return {"attempt": None, "phase": phase, "final": final, "rows": []}
    if final:
        rows = _final_rows(ps, attempt, phase)
    else:
        titles = _titles(ps)
        item = {r["id"]: r["item_id"] for r in records(ps, attempt)}
        rows = [{"id": d["id"], "record_id": d["record_id"], "item_id": item.get(d["record_id"]),
                 "title": titles.get(item.get(d["record_id"]), ""), "phase": d["phase"], "screener": d["screener"],
                 "include": d["include"], "criterion": d["criterion"], "reason": d["reason"],
                 "carried_from": d["carried_from"]} for d in decisions(ps, attempt, phase=phase)]
    return {"attempt": attempt, "phase": phase, "final": final, "rows": rows}


def extraction_view(ps: ProjectStore, item_id: str | None = None) -> dict:
    """The extraction of the attempt ``read_extract`` shows, per included study (or the one ``item_id``): the text it
    was read on and its sufficiency (``low``: read on the abstract alone), whether it is in the evidence (else
    ``out_reason``: flagged as retracted or withdrawn, taken out by the P1 degrade, not read), its ``values`` (only
    usable evidence: ``rk.production.extraction.usable_evidence``'s rows without a problem, with their verified quote
    note) and the rows that are no evidence (``set_aside``, each with its problem: a quote not verbatim, degraded,
    marked missing, or its study out of the evidence); the attempt's limitations input; and its ``gate``:
    ``passed`` (whether this attempt passed read_extract's gate, also once a later back-edge reopened the stage) and
    the ``failures`` of its gate when it failed (``[]`` while it was not gated)."""
    row = frozen_protocol(ps)
    fields = row["body"]["extraction_schema"] if row else []
    attempt = shown_attempt(ps, READ_EXTRACT)
    if attempt is None:
        return {"attempt": None, "fields": fields, "studies": [], "limitations": None, "gate": None}
    stage = stage_row(ps, READ_EXTRACT)
    last = stage["last_gate"] if stage and stage["last_gate"] and stage["last_gate"]["attempt"] == attempt else None
    gate = {"passed": bool(stage and stage["passed_attempt"] == attempt),
            "failures": list(last["failures"]) if last and not last["ok"] else []}
    titles = _titles(ps)
    noted = {n["item_id"]: n for n in extraction_notes(ps, attempt, kind="study")}
    usable = {e["item_id"]: e for e in usable_evidence(ps, attempt)}
    limitations = extraction_limitations(ps, attempt)
    excluded = {e["item_id"]: e["reason"] for e in limitations["excluded"]}
    studies = []
    for rec in included_studies(ps):
        iid = rec["item_id"]
        if item_id is not None and iid != item_id:
            continue
        s, ev = noted.get(iid), usable.get(iid)
        rows = extractions(ps, attempt, item_id=iid)
        if ev is not None:
            out_reason = None
        elif s is None:
            out_reason = "not read"
        elif s["flagged"]:
            out_reason = f"{s['retraction']['status']}: flagged, kept as a discussed object, not extracted"
        else:
            out_reason = f"taken out of the evidence: {excluded.get(iid, 'by the P1 degrade')}"
        values, aside = [], []
        for r in rows:
            if ev is not None and r["problem"] is None and r["note_id"] in ev["notes"]:
                values.append({"id": r["id"], "field": r["field"], "value": r["value"], "sufficient": _sufficiency(r),
                               "note": _note(ps, r["note_id"]), "carried_from": r["carried_from"]})
            else:
                problem = r["problem"] or out_reason or "its note was dropped by the P1 degrade"
                aside.append({"id": r["id"], "field": r["field"], "value": r["value"], "sufficient": _sufficiency(r),
                              "problem": problem, "note_id": r["note_id"]})
        studies.append({"item_id": iid, "record_id": rec["id"], "title": titles.get(iid, ""),
                        "read_on": s["text"] if s else None, "sufficient": s["sufficient"] if s else None,
                        "retraction": s["retraction"]["status"] if s else None,
                        "flagged": bool(s and s["flagged"]), "problems": s["problems"] if s else [],
                        "in_evidence": ev is not None, "out_reason": out_reason,
                        "notes": ev["notes"] if ev else [], "values": values, "set_aside": aside,
                        "carried_from": s["carried_from"] if s else None})
    return {"attempt": attempt, "fields": fields, "studies": studies, "limitations": limitations, "gate": gate}


def register_production_tools(tool: Callable, kernel) -> None:
    """``tool(name, summary, input_model, writes)`` is ``build_registry``'s decorator."""

    def project(slug: str) -> ProjectStore:
        try:
            ps, _ = kernel.project(slug)
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e
        return ps

    @tool("rk_protocol_get", "The review protocol (default: the version in force, else the latest draft): its body "
          "(search strategy, eligibility, extraction schema, synthesis plan, PRISMA-P), the record-cap probe, the "
          "rendered text, every version and the deviations between them, and whether its freeze counts as "
          "preregistration for this paper type.", ProtocolGet, False)
    def _protocol_get(ctx: CallContext, a: ProtocolGet) -> dict:
        return {"project": a.project, **protocol_view(project(a.project), a.protocol_id)}

    @tool("rk_prisma_get", "The PRISMA flow of search_screen (the attempt that passed, else the latest that ran): "
          "each count with the full-text exclusion reasons, the V-PRISMA check and the PRISMA notes (incomplete "
          "searches, dropped entries, exclusions by the P1 vote, language, abstract-only, carried decisions); for a "
          "critical review or research proposal the selection reasons instead of the counts.", PrismaGet, False)
    def _prisma_get(ctx: CallContext, a: PrismaGet) -> dict:
        return {"project": a.project, **prisma_view(project(a.project), a.attempt)}

    @tool("rk_screening_list", "The screening decisions of search_screen (the attempt that passed, else the latest "
          "that ran): each screener's decision with its criterion and reason, or with final the final decision of "
          "each record (decided by agreement, a single screener, you, the P1 vote or the language rule); optionally "
          "one phase (title_abstract, full_text).", ScreeningList, False)
    def _screening_list(ctx: CallContext, a: ScreeningList) -> dict:
        return {"project": a.project, **screening_view(project(a.project), a.phase, a.final)}

    @tool("rk_extraction_get", "The extraction of read_extract (the attempt that passed, else the latest that ran), "
          "per included study or one item_id: the usable values with their verified quote notes and sufficiency "
          "(low: read on the abstract alone), the rows that are no evidence with their problem, the studies out of "
          "the evidence and why, and the limitations input.", ExtractionGet, False)
    def _extraction_get(ctx: CallContext, a: ExtractionGet) -> dict:
        return {"project": a.project, **extraction_view(project(a.project), a.item_id)}
