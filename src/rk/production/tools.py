"""The production read tools: the protocol (``rk_protocol_get``), the PRISMA flow or the selection reasons
(``rk_prisma_get``), the screening decisions (``rk_screening_list``), the extraction (``rk_extraction_get``), the
synthesis (``rk_synthesis_get``), the argument (``rk_argument_get``), the design (``rk_design_get``), the manuscript
with each sentence's stored verdict and revise's questions (``rk_manuscript_get``) and the audit with the export
(``rk_audit_get``); and the researcher's ``rk_export``, which starts the export stage. Registered by
``build_registry``; ``rk.roles.policy`` says which roles read which. Each shows the attempt its stage shows
(``rk.orchestrator.state.shown_attempt``: the one that passed, else the latest that ran) unless an attempt is given."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.checkpoints import get_checkpoint, list_checkpoints, opened_in_place_of
from rk.discovery.models import RQBody
from rk.discovery.store import frozen_rq, hypotheses, intake_of
from rk.orchestrator.state import attempt_ran, shown_attempt, stage_row
from rk.production.argue import (
    argue_limitations,
    argue_notes,
    argued_synthesis_attempt,
    argument_edge_ids,
    argument_objection_ids,
    argument_roles,
    conflict_checkpoint,
    conflict_counterparts,
    main_claim,
    soundness_ids,
)
from rk.production.artifacts import ARTIFACT_MEDIA_TYPES, artifacts
from rk.production.audit import GATES, audit_notes, audit_reports
from rk.production.audit import KIND as FINAL_REVIEW
from rk.production.audit import STAGE as AUDIT
from rk.production.common import by_group, entity_row
from rk.production.design import design_notes, design_problems, feasibility_claims
from rk.production.draft import load_inputs
from rk.production.export import EXPORT_DIR, audited_manuscript, built, downloads
from rk.production.export import STAGE as EXPORT
from rk.production.extraction import (
    extraction_limitations,
    included_studies,
    usable_evidence,
)
from rk.production.manuscript import (
    draft_text,
    draft_versions,
    drafted_sections,
    parse_section,
)
from rk.production.models import PHASES, ScreeningPhase
from rk.production.protocol import (
    counts_as_preregistration,
    probe_lines,
    probe_of,
    protocol_prereg_ok,
    render_protocol,
)
from rk.production.revise import STAGE as REVISE
from rk.production.revise import revise_notes
from rk.production.screening import reports_prisma
from rk.production.store import (
    decisions,
    design,
    deviations,
    extraction_notes,
    extractions,
    finals,
    frozen_protocol,
    protocol_row,
    records,
)
from rk.production.synthesis import (
    min_studies,
    pool_options,
    study_label,
    synthesis_claims,
    synthesis_limitations,
    synthesis_notes,
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
from rk.verify.stats import StatsResult, check_stats
from rk.verify.support import check_claim_hedge, claim_problems, link_gone

if TYPE_CHECKING:
    from rk.orchestrator.runs import RunManager

SEARCH_SCREEN, READ_EXTRACT = "search_screen", "read_extract"
SYNTHESIZE, ARGUE, DESIGN = "synthesize", "argue", "design"


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


class SynthesisGet(BaseModel):
    project: str
    attempt: int | None = None  # default: the attempt synthesize shows


class ArgumentGet(BaseModel):
    project: str
    attempt: int | None = None  # default: the attempt argue shows
    claim_id: str | None = None  # only this claim, with its edges, warrants and objections


class DesignGet(BaseModel):
    project: str
    attempt: int | None = None  # default: the attempt design shows


class ManuscriptGet(BaseModel):
    project: str
    section: str | None = None  # default: every required section (then any other drafted one)
    version: int | None = None  # with section: that version (default: the latest)


class AuditGet(BaseModel):
    project: str
    version: int | None = None  # default: the latest audit report


class ExportRun(BaseModel):
    project: str


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


def gate_of(ps: ProjectStore, stage: str, attempt: int) -> dict:
    """How the attempt stands at its stage's gate: ``passed`` (also once a later back-edge reopened the stage) and the
    ``failures`` of its gate when it failed (``[]`` while it was not gated)."""
    row = stage_row(ps, stage)
    last = row["last_gate"] if row and row["last_gate"] and row["last_gate"]["attempt"] == attempt else None
    return {"passed": bool(row and row["passed_attempt"] == attempt),
            "failures": list(last["failures"]) if last and not last["ok"] else []}


def _latest(ps: ProjectStore, target_id: str, verifier: str) -> dict | None:
    """The latest recorded verdict of ``verifier`` on ``target_id`` with its details, else ``None``."""
    r = ps.db.execute("SELECT verdict, details FROM verifications WHERE target_id=? AND verifier=? "
                      "ORDER BY rowid DESC LIMIT 1", (target_id, verifier)).fetchone()
    return {"verdict": r["verdict"], "details": json.loads(r["details"])} if r else None


def _evidence_note(ps: ProjectStore, note_id: str, titles: dict[str, str]) -> dict:
    """An evidence note (``_note``) with the study it quotes."""
    n = _note(ps, note_id)
    if n is None:
        return {"id": note_id, "missing": True}
    item = json.loads(ps.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone()["data"])["item_id"]
    return {**n, "item_id": item, "label": study_label(ps, item), "title": titles.get(item, "")}


def claim_view(ps: ProjectStore, claim_id: str, *, role: str | None = None, titles: dict[str, str] | None = None,
               stats: dict[str, StatsResult] | None = None) -> dict:
    """A claim as the views show it: its text, kind, origin and status (``support_reason``: V-SUPPORT's recorded
    reason), why it was withdrawn or is contested, V-HEDGE rerun now (the wording's level, the verdict and its
    problems; ``None`` for a withdrawn claim, which is out of the argument), for a ``stat_result`` its artifacts and
    V-STATS rerun now (``pass`` / ``mismatch``; ``stats``: the artifacts' results already rerun for this read), its
    evidence with the criteria and the quoted notes, and ``problems``: why it fails the claim gate now
    (``claim_problems``, with the same V-HEDGE and V-STATS results)."""
    titles = _titles(ps) if titles is None else titles
    c = ArgumentService(ps).get_claim(claim_id)
    live = c["status"] != "withdrawn"
    hedge = check_claim_hedge(ps, claim_id, record=False) if live else None
    mine = {}
    if c["kind"] == "stat_result":
        given = stats or {}
        mine = {a: given[a] if a in given else check_stats(ps, a, record=False) for a in c.get("artifact_ids") or []}
    problems = claim_problems(ps, claim_id, hedge=hedge, stats=mine) if live else []
    support = _latest(ps, claim_id, "V-SUPPORT")
    out = {"id": claim_id, "text": c["text"], "kind": c["kind"], "origin": c.get("origin") or "argument",
           "role": role, "status": c["status"], "support_reason": (support or {}).get("details", {}).get("reason"),
           "scope_limits": c.get("scope_limits") or [], "narrowed_from": c.get("narrowed_from"),
           "withdrawn_reason": c.get("withdrawn_reason"), "contested": bool(c.get("contested")),
           "contested_reason": c.get("contested_reason"),
           "hedge": {"level": hedge.claim_level if hedge else c.get("hedge_level"),
                     "verdict": None if hedge is None else "pass" if hedge.ok else "overstated",
                     "problems": list(hedge.problems) if hedge else []},
           "evidence": [{"id": e["id"], "criteria": e.get("criteria") or {},
                         "interpretation": e.get("interpretation") or "",
                         "notes": [_evidence_note(ps, n, titles) for n in e["note_ids"]]} for e in c["evidence"]],
           "problems": [p.line for p in problems]}
    if c["kind"] == "stat_result":
        out["artifact_ids"] = list(mine)
        out["v_stats"] = "pass" if all(r.ok for r in mine.values()) else "mismatch"
    return out


def _artifact_view(ps: ProjectStore, a: dict, now: StatsResult) -> dict:
    """An artifact with its file's media type and V-STATS: ``verdict`` rerun now (``now``: ``pass`` / ``mismatch``,
    with the ``mismatches``; nothing is recorded) and the ``recorded`` verdict."""
    recorded = _latest(ps, a["id"], "V-STATS")
    return {k: a[k] for k in ("id", "kind", "code_path", "code_version", "values", "path", "sha256")} | {
        "media_type": ARTIFACT_MEDIA_TYPES.get(Path(a["path"]).suffix, "application/octet-stream"),
        "v_stats": {"verdict": "pass" if now.ok else "mismatch", "mismatches": now.mismatches,
                    "recorded": recorded["verdict"] if recorded else None}}


def synthesis_view(ps: ProjectStore, attempt: int | None = None) -> dict:
    """The synthesis of a ``synthesize`` attempt (default: the one it shows): the protocol's method and pre-specified
    pool options (with ``min_studies``, the fewest studies with a computable effect a pool needs); each study's
    assessment (the seven criteria; ``capped``: sufficiency capped for abstract-only
    evidence); the pooling plan's groups with their comparability, the studies the kernel added or the plan left out,
    the decision (by you, the vote or the kernel), the fallback or the pooled artifacts; the artifacts with V-STATS;
    the findings (``claim_view``: the narrative findings and the kernel's ``stat_result`` claims, then their narrower
    rewrites); the Arguer's narrative synthesis; the evidence map; the fallbacks (each a recorded analysis deviation);
    the limitations input and the gate."""
    row = frozen_protocol(ps)
    plan = row["body"].get("synthesis_plan") if row else None
    options = pool_options(plan)
    if attempt is None:
        attempt = shown_attempt(ps, SYNTHESIZE)
    out = {"attempt": attempt, "paper_type": paper_type_of(ps), "method": (plan or {}).get("method"),
           "pool_options": options.model_dump() | {"min_studies": min_studies(plan)} if options else None,
           "assessments": [], "groups": [],
           "artifacts": [], "findings": [], "narrative": None, "map": None, "fallbacks": [], "limitations": None,
           "gate": None}
    if attempt is None:
        return out
    titles = _titles(ps)
    notes = synthesis_notes(ps, attempt)

    def study(item_id: str) -> dict:
        return {"item_id": item_id, "label": study_label(ps, item_id), "title": titles.get(item_id, "")}

    plans = [n for n in notes if n["kind"] == "plan"]
    decided, fell, pooled = (by_group(notes, k) for k in ("decision", "fallback", "pooled"))
    groups = []
    for g in plans[-1]["groups"] if plans else []:
        i = g["group"]
        d, f, p = decided.get(i), fell.get(i), pooled.get(i)
        groups.append({
            "group": i, "outcome": g["outcome"], "planned_outcome": g.get("planned_outcome"), "measure": g["measure"],
            "studies": [study(x) for x in g["item_ids"]], "added_by_kernel": [study(x) for x in g["added_by_kernel"]],
            "left_out": [study(x["item_id"]) | {"reason": x["reason"]} for x in g.get("left_out") or []],
            "duplicate_of": g.get("duplicate_of"), "comparability": g["comparability"],
            "comparable": g["comparable"], "reason": g["reason"],
            "decision": {k: d.get(k) for k in ("decision", "by", "reason")} if d else None,
            "fallback": f["reason"] if f else None,
            "pooled": {"stt": p["stt"], "fig": p["fig"], "claim_id": p["claim_id"],
                       "studies": [study(x) for x in p["item_ids"]],
                       "uncomputable": [study(u["item_id"]) | {"reason": u["reason"]} for u in p["uncomputable"]],
                       "excluded": [study(e["item_id"]) | {"reason": e["reason"]} for e in p["excluded"]]}
            if p else None})
    narrative = [n for n in notes if n["kind"] == "narrative"]
    maps = [n for n in notes if n["kind"] == "map"]
    made = {a["id"]: a for a in artifacts(ps, SYNTHESIZE, attempt)}
    stats = {aid: check_stats(ps, aid, record=False) for aid in made}  # V-STATS once per read
    out.update(
        assessments=[study(a["item_id"]) | {"criteria": a["criteria"], "capped": a["capped"],
                                            "note_ids": a["note_ids"]}
                     for a in notes if a["kind"] == "assessment"],
        groups=groups, artifacts=[_artifact_view(ps, a, stats[aid]) for aid, a in made.items()],
        findings=[claim_view(ps, c, titles=titles, stats=stats) for c in synthesis_claims(ps, attempt)],
        narrative={**narrative[-1]["output"], "included_k": narrative[-1]["included_k"],
                   "selection_basis": narrative[-1]["selection_basis"]} if narrative else None,
        map={"stt": maps[-1]["stt"], "fig": maps[-1]["fig"], "fields": maps[-1]["fields"],
             "dropped": maps[-1]["dropped"], "table": made[maps[-1]["stt"]]["values"]["table"]} if maps else None,
        fallbacks=[{"reason": n["reason"], "group": n.get("group"), "deviation_id": n["deviation_id"]}
                   for n in notes if n["kind"] == "fallback"],
        limitations=synthesis_limitations(ps, attempt), gate=gate_of(ps, SYNTHESIZE, attempt))
    return out


def _question(ps: ProjectStore) -> str | None:
    rq = frozen_rq(ps)
    return RQBody.model_validate(rq["body"]).three_step.sentence() if rq else None


def argument_view(ps: ProjectStore, attempt: int | None = None, claim_id: str | None = None) -> dict:
    """The argument of an ``argue`` attempt (default: the one it shows), in argue's own terms: the frozen RQ's
    question, the main claim and the Arguer's self-check; the synthesize attempt it argued on
    (``argued_synthesis_attempt``); every claim (``claim_view``) with its role (``argument_roles``: ``main``,
    ``reason``, ``response``, ``warrant`` or ``synthesis``), where the findings are that synthesize attempt's supported
    findings and, out of the argument, its withdrawn ones; the edges (``argument_edge_ids``; ``out``: ``link_gone``;
    ``dropped`` and ``dropped_reason``: the attempt's ``dropped_link`` notes); the warrants with the Skeptic's tests;
    the objections (``argument_objection_ids``) with their answer and response claim (``soundness``: a failed warrant
    test; ``counterpart``: the claim a conflict objection says contradicts its target); the conflicts with their
    decision; the RQ checks; how the rounds ended; the limitations input and the gate. With ``claim_id`` only that
    claim, the edges into or out of it or its evidence, their warrants, and the objections on it, its evidence or
    those edges."""
    if attempt is None:
        attempt = shown_attempt(ps, ARGUE)
    out = {"attempt": attempt, "question": _question(ps), "synthesis_attempt": None, "main_claim_id": None,
           "self_check": None, "claims": [], "edges": [], "warrants": [], "objections": [], "conflicts": [],
           "rq_checks": [], "end": None, "limitations": None, "gate": None}
    if attempt is None:
        if claim_id is not None:
            raise ToolError("not_found", f"no claim {claim_id} in the argument: argue has not run", status=404)
        return out
    svc = ArgumentService(ps)
    notes = argue_notes(ps, attempt)
    titles = _titles(ps)
    synth = argued_synthesis_attempt(ps, attempt)
    findings = [c for c in (synthesis_claims(ps, synth) if synth else [])
                if svc.get_claim(c)["status"] in ("supported", "withdrawn")]
    roles = argument_roles(ps, attempt, findings)
    dropped = {n["edge_id"]: n["reason"] for n in notes if n["kind"] == "dropped_link"}
    counterparts = conflict_counterparts(ps, attempt)
    soundness = soundness_ids(ps, attempt)
    edges = [entity_row(ps, "edges", e) for e in argument_edge_ids(ps, attempt)]
    objections = [entity_row(ps, "objections", o) for o in argument_objection_ids(ps, attempt)]
    if claim_id is not None:
        if claim_id not in roles:
            raise ToolError("not_found", f"no claim {claim_id} in argue attempt {attempt}", status=404)
        ends = {claim_id, *(e["id"] for e in svc.list_evidence(claim_id))}
        touching = {e["id"] for e in edges if e["from_id"] in ends or e["to_id"] in ends}
        edges = [e for e in edges if e["id"] in touching or (e["type"] == "warrant_of" and e["to_id"] in touching)]
        targets = ends | {e["id"] for e in edges}
        objections = [o for o in objections if o["target_id"] in targets]
        roles = {claim_id: roles[claim_id]}
    order = {"main": 0, "reason": 1, "response": 2, "warrant": 3, "synthesis": 4}
    decisions = {n["index"]: n for n in notes if n["kind"] == "conflict_decision"}
    drafts = [n for n in notes if n["kind"] == "draft"]
    ends_notes = [n for n in notes if n["kind"] == "end"]

    def response(o: dict) -> dict | None:
        rc = o.get("response_claim_id")
        if not rc:
            return None
        c = entity_row(ps, "claims", rc)
        return {"id": rc, "text": c["text"], "status": c["status"]}

    out.update(
        synthesis_attempt=synth, main_claim_id=main_claim(ps, attempt),
        self_check=drafts[0].get("answers_rq") if drafts else None,
        claims=[claim_view(ps, c, role=r, titles=titles) for c, r in sorted(roles.items(), key=lambda x: order[x[1]])],
        edges=[{k: e.get(k) for k in ("id", "type", "from_id", "to_id", "warrant", "warrant_tests")}
               | {"out": link_gone(ps, e["id"]), "dropped": e["id"] in dropped, "dropped_reason": dropped.get(e["id"])}
               for e in edges],
        warrants=[{"edge_id": e["id"], "claim_id": e["from_id"], "text": entity_row(ps, "claims", e["from_id"])["text"],
                   "licenses": e["to_id"], "warrant": e["warrant"], "tests": e.get("warrant_tests"),
                   "dropped": e["to_id"] in dropped} for e in edges if e["type"] == "warrant_of"],
        objections=[{k: o.get(k) for k in ("id", "target_id", "category", "text", "priority", "source_id", "status",
                                           "note", "response_claim_id")}
                    | {"response": response(o), "soundness": o["id"] in soundness,
                       "counterpart": counterparts.get(o["id"])} for o in objections],
        conflicts=[{k: n.get(k) for k in ("round", "index", "claim_a", "claim_b", "why")}
                   | {"checkpoint_id": conflict_checkpoint(ps, attempt, n),
                      "decision": {k: decisions[n["index"]].get(k) for k in ("decision", "by", "reason", "comment")}
                      if n["index"] in decisions else None}
                   for n in notes if n["kind"] == "conflict"],
        rq_checks=[{k: n.get(k) for k in ("main_claim_id", "decision", "by", "reason")}
                   for n in notes if n["kind"] == "rq_check"],
        end=ends_notes[-1] if ends_notes else None, limitations=argue_limitations(ps, attempt),
        gate=gate_of(ps, ARGUE, attempt))
    return out


def _approval_state(ps: ProjectStore, attempt: int) -> str:
    """Where the attempt's design stands: ``frozen`` (approved), ``not approved`` (the P1 vote), ``untestable`` (the
    P1 vote on the Ideator's rq_untestable sent it back to the question), ``awaiting approval`` (its design_approval
    is open), ``answered: <decision>``, or ``draft`` (not asked yet)."""
    if design_notes(ps, attempt, kind="approved"):
        return "frozen"
    if (rq := design_notes(ps, attempt, kind="rq_vote")) and rq[-1]["decision"] == "untestable":
        return "untestable"
    votes = design_notes(ps, attempt, kind="vote")
    if votes and votes[-1]["decision"] != "approve":
        return "not approved"
    asked = design_notes(ps, attempt, kind="approval")
    if not asked:
        return "draft"
    cid = asked[-1]["checkpoint_id"]
    while (nxt := opened_in_place_of(ps, "design_approval", cid)) is not None:
        cid = nxt
    cp = get_checkpoint(ps, cid)
    if cp["status"] == "open":
        return "awaiting approval"
    return f"answered: {(cp.get('answer') or {}).get('decision', cp['status'])}"


def design_view(ps: ProjectStore, attempt: int | None = None) -> dict:
    """The design of a ``design`` attempt (default: the one it shows). Before its approval it is the attempt's draft
    (``draft_id`` ``design:<attempt>``; nothing is stored or added to the RQ yet): the body as the Ideator wrote it
    (a new hypothesis named by its ref); once approved the stored, frozen design (real ids, ``falsified_by`` and the
    frozen ``hypotheses`` map) with its ``design_id`` and ``frozen_hash``. Also the design's hypotheses (``new``:
    added on approval; ``id``: the RQ hypothesis it is, once known) with their falsification and links, the dropped
    ones with their reasons, the feasibility claims (``claim_view``), every hypothesis of the frozen RQ with its
    status and pre-registration hash, the structural ``problems`` now and the gate. ``untestable``: the reason the
    Ideator gave when it said the research question is untestable (``None`` when it did not), with ``status`` even when
    it gave no draft."""
    if attempt is None:
        attempt = shown_attempt(ps, DESIGN)
    rq = frozen_rq(ps)
    rows = {h["id"]: h for h in (hypotheses(ps, rq["id"]) if rq else [])}
    out = {"attempt": attempt, "draft_id": None, "status": None, "design_id": None, "design": None,
           "hypotheses": [], "dropped": [], "feasibility_claims": [], "frozen_hash": None,
           "rq_hypotheses": [{k: h.get(k) for k in ("id", "statement", "falsification", "rival_of", "status",
                                                    "prereg_hash")} for h in rows.values()],
           "problems": [], "gate": None, "untestable": None}
    untestable = design_notes(ps, attempt, kind="untestable") if attempt is not None else []
    if untestable:
        out.update(untestable=untestable[-1]["reason"], status=_approval_state(ps, attempt))
    drafts = design_notes(ps, attempt, kind="draft") if attempt is not None else []
    if not drafts:
        return out
    draft = drafts[-1]
    approved = design_notes(ps, attempt, kind="approved")
    ids = approved[-1]["hypothesis_ids"] if approved else {}
    stored = design(ps, approved[-1]["design_id"]) if approved else None
    titles = _titles(ps)
    hyps = []
    for e in draft["hypotheses"]:
        text = e["new"] or {k: rows.get(e["id"], {}).get(k) for k in ("statement", "falsification", "rival_of")}
        hid = ids.get(e["key"]) or e["id"]
        hyps.append({"key": e["key"], "id": hid, "new": e["new"] is not None, "statement": text["statement"],
                     "falsification": text["falsification"], "rival_of": text.get("rival_of"),
                     "tested_by": e["tested_by"], "falsified_by": e["falsified_by"],
                     "status": rows[hid]["status"] if hid in rows else None})
    out.update(draft_id=draft["draft_id"], status=_approval_state(ps, attempt),
               design_id=stored["id"] if stored else None, design=stored["body"] if stored else draft["body"],
               hypotheses=hyps,
               dropped=[{**x, "statement": rows.get(x["id"], {}).get("statement")} for x in draft["dropped"]],
               feasibility_claims=[claim_view(ps, c, titles=titles) for c in feasibility_claims(ps, attempt)],
               frozen_hash=stored["frozen_hash"] if stored else None, problems=design_problems(ps, attempt),
               gate=gate_of(ps, DESIGN, attempt))
    return out


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
    gate = gate_of(ps, READ_EXTRACT, attempt)
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


# ---- the manuscript, revise and the audit (Plan 5c) ----
def _sentences(markdown: str, verdict: dict | None) -> list[dict]:
    """Each sentence of the markdown with the verdict recorded for it (``ok`` ``None``: not verified)."""
    by_index = {x["index"]: x for x in verdict["sentences"]} if verdict else {}
    return [{"index": x.index, "text": x.text, "quoted": x.quoted,
             "markers": [{"kind": m.kind, "target": m.target} for m in x.markers],
             "ok": by_index[x.index]["ok"] if x.index in by_index else None,
             "problems": list(by_index[x.index]["problems"]) if x.index in by_index else []}
            for x in parse_section(markdown)]


def _section_view(ps: ProjectStore, section: str, title: str | None, version: int | None) -> dict:
    """A stored version of ``section`` (default: the latest) as stored: each sentence with its recorded verdict, never
    verified again here; ``error`` when the version's file cannot be read (missing, or changed since it was stored).
    ``LookupError`` for a version that was never stored."""
    rows = draft_versions(ps, section)
    base = {"section": section, "title": title, "versions": [r["version"] for r in rows]}
    if not rows:
        return {**base, "version": None, "stage": None, "attempt": None, "round": None, "by_task": None, "ok": None,
                "problems": [], "sentences": [], "error": f"section {section} has no draft"}
    row = rows[-1] if version is None else next((r for r in rows if r["version"] == version), None)
    if row is None:
        raise LookupError(f"section {section} has no version {version}")
    base |= {k: row[k] for k in ("version", "stage", "attempt", "round", "by_task")}
    verdict = row["verdict"]
    try:
        md = draft_text(ps, section, row["version"])
    except (KeyError, ValueError) as e:
        return {**base, "ok": None, "problems": [], "sentences": [], "error": f"section {section} v{row['version']}: {e}"}
    return {**base, "ok": verdict["ok"] if verdict else None, "problems": list(verdict.get("problems") or [])
            if verdict else [], "sentences": _sentences(md, verdict), "error": None}


def revise_view(ps: ProjectStore, attempt: int | None = None) -> dict:
    """revise's questions (of the attempt it shows, or ``attempt``), each with how it was handled: ``outcome``
    ``revised`` / ``answered`` / ``declined`` (its answer in an accepted revision, with the ``note``), ``rejected`` (only
    rejected revisions answered it), ``deleted`` (P1's degrade deleted its sentence) or ``open``; and every revision
    asked for it (accepted or not, with the reasons); the refused questions, those not carried into the attempt and
    the gate."""
    attempt = attempt if attempt is not None else shown_attempt(ps, REVISE)
    if attempt is None:
        return {"attempt": None, "questions": [], "refused": [], "dropped": [], "back_edge": None, "gate": None}
    notes = revise_notes(ps, attempt)
    decided = {n["task"]: n for n in notes if n["kind"] == "decided"}
    tries = [{**n, "accepted": decided[n["task"]]["accepted"] if n["task"] in decided else None,
              "reasons": decided[n["task"]]["reasons"] if n["task"] in decided else []}
             for n in notes if n["kind"] == "revision"]
    deleted = {q for r in ps.db.execute("SELECT payload FROM events WHERE type='stage.degraded' AND "
                                        "json_extract(payload, '$.stage')=? AND json_extract(payload, '$.attempt')=?",
                                        (REVISE, attempt))
               for q in json.loads(r["payload"]).get("questions") or []}
    questions = []
    for qn in (n for n in notes if n["kind"] == "questions"):
        for q in qn["questions"]:
            revisions = []
            for t in (t for t in tries if q["id"] in t["asked"]):
                a = next((x for x in t["answers"] if x["question_id"] == q["id"]), None)
                revisions.append({"round": t["round"], "task": t["task"], "accepted": t["accepted"],
                                  "reasons": t["reasons"], "how": a["how"] if a else None,
                                  "note": a["note"] if a else None})
            answered = [r for r in revisions if r["accepted"] and r["how"]]
            if q["id"] in deleted:
                outcome, note = "deleted", "P1's degrade deleted the sentence it was about"
            elif answered:
                outcome, note = answered[-1]["how"], answered[-1]["note"]
            else:
                outcome, note = ("rejected" if any(r["accepted"] is False for r in revisions) else "open"), None
            questions.append({"id": q["id"], "round": qn["round"], "section": q["section"],
                              "sentence_index": q.get("sentence_index"), "sentence": q.get("sentence"),
                              "text": q["text"], "priority": q["priority"], "carried_from": q.get("carried_from"),
                              "outcome": outcome, "note": note, "revisions": revisions})
    back = [n for n in notes if n["kind"] == "back_edge"]
    return {"attempt": attempt, "questions": questions,
            "refused": [q for n in notes if n["kind"] == "questions" for q in n.get("refused") or []],
            "dropped": [q for n in notes if n["kind"] == "carry_dropped" for q in n["questions"]],
            "back_edge": {"to": back[-1]["to"], "reason": back[-1]["reason"]} if back else None,
            "gate": gate_of(ps, REVISE, attempt)}


def manuscript_view(ps: ProjectStore, section: str | None = None, version: int | None = None) -> dict:
    """The manuscript: every required section's latest version (then any other drafted section), or the one
    ``section`` (at ``version``), each sentence with its stored verdict (``_section_view``); and revise's questions
    with how each was handled (``revise_view``). ``LookupError`` for a section without a draft or an unknown
    version."""
    s = load_inputs(ps)
    specs = [] if isinstance(s, str) else s.sections
    titles = {x.id: x.title for x in specs}
    drafted = [d["section"] for d in drafted_sections(ps)]
    if section is not None:
        if section not in drafted:
            raise LookupError(f"section {section} has no draft")
        ids = [section]
    else:
        ids = [*titles, *(d for d in drafted if d not in titles)]
    return {"paper_type": paper_type_of(ps), "inputs_error": s if isinstance(s, str) else None,
            "sections": [_section_view(ps, sid, titles.get(sid), version) for sid in ids],
            "revise": revise_view(ps)}


def export_view(ps: ProjectStore) -> dict:
    """Whether the manuscript can be exported now (``ready``: ``None``, else the reason the export worker refuses
    it, ``audited_manuscript``), the export attempt it shows with its package, and the files to download."""
    got = audited_manuscript(ps)
    attempt = shown_attempt(ps, EXPORT)
    b = built(ps, attempt) if attempt is not None else None
    return {"ready": got if isinstance(got, str) else None, "attempt": attempt,
            "built": {k: b[k] for k in ("package", "sha256", "warnings")} if b else None, "files": downloads(ps)}


def audit_view(ps: ProjectStore, version: int | None = None) -> dict:
    """The audit: its report ``version`` (default: the latest) with every hard gate in order and its failures, the
    owners of the failures (``routes``) and the warnings; every report; the final review of the attempt the audit
    shows (its checkpoints and the approval); and the export (``export_view``). ``LookupError`` for an unknown
    version."""
    reports = audit_reports(ps)
    rep = reports[-1] if reports and version is None else next((r for r in reports if r["version"] == version), None)
    if rep is None and version is not None:
        raise LookupError(f"there is no audit report v{version}")
    attempt = shown_attempt(ps, AUDIT)
    report = None
    if rep is not None:
        report = {k: rep[k] for k in ("version", "attempt", "ok", "approved", "sections", "routes", "warnings",
                                      "md_path", "json_path")}
        report["gates"] = [{"gate": g, "failures": list(rep["gates"].get(g) or [])}
                           for g in [*GATES, *(g for g in rep["gates"] if g not in GATES)]]
    reviews = [{k: c[k] for k in ("id", "status", "answer", "answered_by", "answered_at")}
               for c in list_checkpoints(ps) if c["kind"] == FINAL_REVIEW
               and (c["options"] or {}).get("attempt") == attempt] if attempt is not None else []
    approved = audit_notes(ps, attempt, kind="approved") if attempt is not None else []
    return {"attempt": attempt, "gate": gate_of(ps, AUDIT, attempt) if attempt is not None else None,
            "report": report, "reports": [{k: r[k] for k in ("version", "attempt", "ok", "approved")} for r in reports],
            "final_review": reviews, "approved": approved[-1] if approved else None, "export": export_view(ps)}


def register_production_tools(tool: Callable, kernel, runs: Callable[[], RunManager | None] = lambda: None) -> None:
    """``tool(name, summary, input_model, writes)`` is ``build_registry``'s decorator; ``runs()`` returns ``rk serve``'s
    RunManager (``rk_export`` starts the export stage in a run), or None."""

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

    @tool("rk_synthesis_get", "The synthesis of synthesize (the attempt that passed, else the latest that ran, or "
          "attempt): each study's assessment on the seven criteria, the pooling plan's groups with their "
          "comparability and decision, the kernel's statistics and figures with their values and V-STATS verdict, the "
          "findings with their status, hedge and evidence, the narrative synthesis, the evidence map, the fallbacks "
          "(recorded deviations), the limitations input and the gate.", SynthesisGet, False)
    def _synthesis_get(ctx: CallContext, a: SynthesisGet) -> dict:
        return {"project": a.project, **synthesis_view(project(a.project), a.attempt)}

    @tool("rk_argument_get", "The argument of argue (the attempt that passed, else the latest that ran, or attempt): "
          "every claim (main, reasons, responses, warrants, the findings it rests on) with its status, hedge and "
          "contested flag, the edges, the warrants with the Skeptic's tests, the objections with their answers, the "
          "conflicts, the research-question checks, the limitations input and the gate; with claim_id only that "
          "claim, its edges, warrants and objections.", ArgumentGet, False)
    def _argument_get(ctx: CallContext, a: ArgumentGet) -> dict:
        return {"project": a.project, **argument_view(project(a.project), a.attempt, a.claim_id)}

    @tool("rk_design_get", "The method design of design (research proposals; the attempt that passed, else the latest "
          "that ran, or attempt): before approval the attempt's draft, once approved the frozen design with its hash; "
          "the hypotheses it tests with their falsification links, the dropped hypotheses with their reasons, the "
          "feasibility claims with their status, every hypothesis of the research question with its status, the "
          "structural problems and the gate.", DesignGet, False)
    def _design_get(ctx: CallContext, a: DesignGet) -> dict:
        return {"project": a.project, **design_view(project(a.project), a.attempt)}

    @tool("rk_manuscript_get", "The manuscript: every required section's latest version (or one section, at a "
          "version), each sentence with its markers and the verdict recorded for it (ok, problems; null: not verified "
          "yet) and the section's own problems; and revise's questions (the attempt it shows), each with how it was "
          "handled (revised, answered or declined with the note, a rejected revision with its reasons) and the gate.",
          ManuscriptGet, False)
    def _manuscript_get(ctx: CallContext, a: ManuscriptGet) -> dict:
        if a.version is not None and a.section is None:
            raise ToolError("invalid", "a version needs its section", status=422)
        try:
            return {"project": a.project, **manuscript_view(project(a.project), a.section, a.version)}
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e

    @tool("rk_audit_get", "The audit: its latest report (or version) with every hard gate and its failures, the stage "
          "owning each failure and the warnings; every report; the final review of the attempt it shows; and the "
          "export: whether the manuscript can be exported now (else why not), the package built and the files.",
          AuditGet, False)
    def _audit_get(ctx: CallContext, a: AuditGet) -> dict:
        try:
            return {"project": a.project, **audit_view(project(a.project), a.version)}
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e

    # the researcher's alone (rk.roles.policy): it starts a run
    @tool("rk_export", "Export the manuscript (human only): starts a run of the export stage in rk serve (follow it "
          "with rk_run_status). Refused, with the reason the export would give, unless the audit passed and the final "
          "review approved the manuscript as it stands.", ExportRun, True)
    def _export(ctx: CallContext, a: ExportRun) -> dict:
        ps = project(a.project)
        if isinstance(why := audited_manuscript(ps), str):
            raise ToolError("not_exportable", why, status=409)
        m = runs()
        if m is None:
            raise ToolError("unavailable", "runs are driven by rk serve's orchestrator, which is not available here",
                            status=503)
        run_id = m.start(a.project, EXPORT, actor=ctx.actor)
        return {"project": a.project, "run_id": run_id, "pdf": str((ps.root / EXPORT_DIR / "paper.pdf").resolve())}
