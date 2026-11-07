"""``read_extract``: the reading of every included study and the extraction of the protocol's fields, each value
backed by a verified quote note.

``ReadExtractWorker.run``, per study ``search_screen`` included at full text in its passed attempt
(``included_studies``), each inside the per-item boundary (``rk.discovery.boundary.per_item``: a source, IO or parse
failure is recorded on that study, never fatal):

1. Retraction (V-RETRACT): the result recorded for the item, else ``sources.retraction(item)``, which is then recorded
   as a verification and on the item (a flagged status also puts it in the "Retracted / EoC" collection). A failed
   lookup is the study's problem: its retraction is not checked. A retracted or withdrawn study is flagged and not
   extracted; it stays a discussed object and is listed for the limitations.
2. The ``reader`` reads the study: its full text with ``rk_text_find``, ``rk_text_read`` and ``rk_note_add``, or, with
   no full text (``abstract_only`` at full-text screening), the abstract it is given (no tools). It returns
   ``ExtractOut``: ``values`` (``field``, ``value``, ``quote``, optional ``locator``) and ``notes`` (the ids of the
   notes it saved itself). A task without output is asked once more.
3. For each value the kernel checks the value against its field (a number, one of the categories, a text; or
   ``"not_reported"``, whose quote shows the section searched), then adds its quote with ``add_note(kind="quote")``:
   V-QUOTE, a given locator only where it holds the quote, against the full text or, for an abstract-only study,
   against the abstract (``against="abstract"``). A verified quote note of the item with the same text (and the same
   snapshot and locator) is linked instead of adding it again. A value whose quote fails is stored with
   ``problem="quote not verbatim"`` and no note, as is a value that does not fit its field (with that problem). A
   value of a field outside the schema is not stored; it is the study's problem, as is a listed note that is not the
   study's.
4. Values from the abstract carry ``sufficient`` False (``sufficient="low"``), and the study's note says so.
5. The study's ``study`` note (``rk.production.store.extraction_notes``) is written last: its retraction check,
   whether it is flagged, the text it was read on and its sufficiency, the reader's notes and its problems.

When every included study is retracted or withdrawn the stage goes back to ``search_screen`` (the only back-edge of
``read_extract``; "no included study is left after the retraction check"); else it is done, its reason the counts.

An attempt interrupted by a rate cap, a budget or a crash is resumed by the next one (``rk.production.resume``): a
study an interrupted attempt right before it finished, under the same frozen protocol and read on the same kind of
text, is carried over with its values and notes (``carried_from``) instead of being read again; a study it left
half-read is read again. Within an attempt a study with a ``study`` note is never read again.

The gate (``gate``): every non-flagged included study was read and its retraction checked; every extraction field of
each has a value with a verified quote note, or ``not_reported`` with a verified quote, or (after a P1 degrade) is
marked missing; every paraphrase note the reader linked passes V-PARA; at least one study is left. Each failure names
the study, the field and the reason. A failed gate reruns the stage (``read_extract->read_extract``); at the edge limit
P2 is asked (``stage_blocked``) and P1 degrades (``degrade``): the failing fields are marked missing, a study whose
retraction could not be checked is taken out of the evidence, a failing paraphrase note is dropped, all disclosed
(``stage.degraded``) and listed in the limitations input (``extraction_limitations``)."""

from __future__ import annotations

import json
from collections import Counter

from pydantic import BaseModel

from rk.discovery.boundary import per_item
from rk.discovery.gaps.retraction import recorded_retraction
from rk.fulltext import latest_snapshot
from rk.notes import Locator, NoteIn, NoteRejected, add_note, list_notes
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import record_degraded, stage_row
from rk.production.models import FULL_TEXT, ExtractionField, Protocol
from rk.production.resume import CarriedStudy, carried_extraction, mark_protocol
from rk.production.store import (
    add_extraction,
    add_extraction_note,
    extraction_notes,
    extractions,
    finals,
    frozen_protocol,
    records,
)
from rk.store.project import ProjectStore
from rk.verify.paraphrase import check_paraphrase
from rk.verify.records import FLAG_COLLECTION, record_verification
from rk.verify.retract import FLAGGED

ROLE = "reader"
READER_TOOLS = ["rk_text_find", "rk_text_read", "rk_note_add"]
NOT_REPORTED = "not_reported"  # the value of a field the study does not report; its quote shows the section searched
QUOTE_FAILED = "quote not verbatim"
MISSING = "missing"  # the problem of a value the P1 degrade marked missing starts with this
RETRACTED = frozenset({"retracted", "withdrawn"})  # flagged: not extracted, kept as a discussed object
READS = 2  # a reader task without output is asked once more
SEARCH_SCREEN = "search_screen"
NO_STUDY = "no included study is left after the retraction check"
FULL, ABSTRACT = "full_text", "abstract"  # the text a study is read on

EXTRACT_TASK = (
    "Extract the given fields from the given study for the review question. Read the study's text with rk_text_find "
    "and rk_text_read (pages numbered from 1). When its text_kind is abstract_only it has no full text and you have "
    "no tools: read only the given abstract. For every field give at least one value: field (the field's name), value "
    "(a number for a number field, in its unit; one of the categories for a category field; a short text for a text "
    "field) and quote, a passage copied character for character from the text (from the abstract when the study is "
    "abstract_only) that shows the value, with the locator rk_text_find gave you when there is one. When the study "
    f'does not report a field, give the value "{NOT_REPORTED}" with a quote of the section you searched for it (e.g. '
    "the first sentence of its methods or results). The kernel verifies every quote and rejects a value whose quote "
    "is not verbatim. Do not save the value quotes yourself; in notes give the ids of the other notes you saved for "
    "this study with rk_note_add (a paraphrase needs its original passage as source_span). Follow the guidance when "
    "there is any.")


class ExtractValue(BaseModel):
    field: str
    value: int | float | str | None = None
    quote: str = ""
    locator: Locator | None = None


class ExtractOut(BaseModel):
    """The reader's extraction of one study."""
    values: list[ExtractValue]
    notes: list[str] = []


# ---- queries ----
def included_studies(ps: ProjectStore) -> list[dict]:
    """The records ``search_screen`` included at full text in the attempt that passed its gate, in search order
    (``[]`` before it passed)."""
    row = stage_row(ps, SEARCH_SCREEN)
    attempt = row["passed_attempt"] if row else None
    if not attempt:
        return []
    yes = {f["record_id"] for f in finals(ps, attempt, phase=FULL_TEXT) if f["include"] == "yes"}
    return [r for r in records(ps, attempt) if r["id"] in yes]


def is_missing(row: dict) -> bool:
    """Whether an extraction row is a value the P1 degrade marked missing."""
    return (row["problem"] or "").startswith(f"{MISSING}:")


def _note_data(ps: ProjectStore, note_id: str) -> dict | None:
    r = ps.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def _title(ps: ProjectStore, item_id: str) -> str:
    r = ps.db.execute("SELECT json_extract(data, '$.title') AS t FROM items WHERE id=?", (item_id,)).fetchone()
    return r["t"] if r and r["t"] else ""


def _study_label(ps: ProjectStore, item_id: str) -> str:
    title = _title(ps, item_id)
    return f'study {item_id}' + (f' ("{title}")' if title else "")


def _verified_quote(ps: ProjectStore, item_id: str, note_id: str | None) -> bool:
    n = _note_data(ps, note_id) if note_id else None
    return (n is not None and n["item_id"] == item_id and n["kind"] == "quote"
            and bool(n["verified"].get("quote", {}).get("ok")))


def _studies(ps: ProjectStore, attempt: int) -> dict[str, dict]:
    """item id -> the attempt's ``study`` note."""
    return {n["item_id"]: n for n in extraction_notes(ps, attempt, kind="study")}


def _degraded(ps: ProjectStore, attempt: int) -> tuple[dict[str, str], set[str]]:
    """What the attempt's P1 degrades took out: study item id -> why it is out of the evidence, and the dropped note
    ids."""
    out: dict[str, str] = {}
    dropped: set[str] = set()
    for n in extraction_notes(ps, attempt, kind="degraded"):
        out |= {e["item_id"]: e["reason"] for e in n["excluded"]}
        dropped |= set(n["dropped_notes"])
    return out, dropped


# ---- the gate's findings ----
def _paraphrase_ok(n: dict) -> bool:
    """V-PARA: the note's recorded check passed and its text still is no near-copy of its source span."""
    return (bool(n["verified"].get("paraphrase", {}).get("ok"))
            and check_paraphrase(n["text"], n.get("source_span") or "").ok)


def _field_problem(ps: ProjectStore, item_id: str, rows: list[dict]) -> str | None:
    """Why a field of a study has no value the gate accepts (``None``: it has one)."""
    if any(r["note_id"] and r["problem"] is None and _verified_quote(ps, item_id, r["note_id"]) for r in rows):
        return None
    if any(is_missing(r) for r in rows):
        return None
    problems = list(dict.fromkeys(r["problem"] or "its quote note is not a verified quote of the study" for r in rows))
    return "; ".join(problems) if problems else "no value"


def findings(ps: ProjectStore, attempt: int) -> list[tuple[str | None, str | None, str]]:
    """What keeps the attempt from its gate, as ``(study item id, field, reason)``: a study-level finding has no
    field, the attempt-level one (no study left) neither a study."""
    row = frozen_protocol(ps)
    if row is None:
        return [(None, None, "no frozen protocol")]
    fields = [f.name for f in Protocol.model_validate(row["body"]).extraction_schema]
    studies = _studies(ps, attempt)
    excluded, dropped = _degraded(ps, attempt)
    out: list[tuple[str | None, str | None, str]] = []
    left = 0
    for rec in included_studies(ps):
        iid = rec["item_id"]
        s = studies.get(iid)
        if s is None:
            out.append((iid, None, "not read"))
            left += 1
            continue
        if s["flagged"] or iid in excluded:
            continue
        left += 1
        if err := s["retraction"].get("error"):
            out.append((iid, None, f"retraction not checked: {err}"))
        values = extractions(ps, attempt, item_id=iid)
        for f in fields:
            why = _field_problem(ps, iid, [v for v in values if v["field"] == f])
            if why is not None:
                out.append((iid, f, why))
        for nid in s["notes"]:
            n = _note_data(ps, nid)
            if nid not in dropped and n is not None and n["kind"] == "paraphrase" and not _paraphrase_ok(n):
                out.append((iid, None, f"paraphrase note {nid} fails V-PARA"))
    if not left:
        out.append((None, None, NO_STUDY))
    return out


def _say(ps: ProjectStore, finding: tuple[str | None, str | None, str]) -> str:
    iid, field, why = finding
    if iid is None:
        return why
    return _study_label(ps, iid) + (f', field "{field}"' if field else "") + f": {why}"


def extraction_limitations(ps: ProjectStore, attempt: int) -> dict:
    """The limitations input of the attempt: the studies flagged as retracted or withdrawn (not extracted), those
    extracted despite another retraction flag (partial retraction, expression of concern), those whose retraction
    status is unknown, those the P1 degrade took out of the evidence, those read on the abstract alone
    (``sufficient="low"``), the values marked missing, and the studies' problems."""
    studies = list(_studies(ps, attempt).values())
    excluded, _ = _degraded(ps, attempt)
    missing = [{"item_id": e["item_id"], "field": e["field"], "reason": e["problem"][len(MISSING) + 2:]}
               for e in extractions(ps, attempt) if is_missing(e)]
    return {
        "flagged": [{"item_id": s["item_id"], "status": s["retraction"]["status"]} for s in studies if s["flagged"]],
        "retraction_concerns": [{"item_id": s["item_id"], "status": s["retraction"]["status"]} for s in studies
                                if not s["flagged"] and s["retraction"]["status"] in FLAGGED],
        "retraction_unknown": [s["item_id"] for s in studies
                               if s["retraction"]["status"] == "unknown" and not s["retraction"].get("error")],
        "excluded": [{"item_id": i, "reason": why} for i, why in excluded.items()],
        "low_sufficiency": [s["item_id"] for s in studies if s["sufficient"] == "low"],
        "missing": missing,
        "problems": [{"item_id": s["item_id"], "problems": s["problems"]} for s in studies if s["problems"]],
    }


# ---- reading ----
def _text_kind(ctx: StageContext, rec: dict) -> str | None:
    """The text a study is read on: its full text, its abstract, or neither."""
    if rec["retrieved"] and latest_snapshot(ctx.ps, rec["item_id"]) is not None:
        return FULL
    return ABSTRACT if (ctx.lib.get(rec["item_id"]).abstract or "").strip() else None


def _retraction(ctx: StageContext, item_id: str) -> dict:
    """The study's V-RETRACT: the recorded result, else a lookup, recorded as a verification and on the item. A
    failed lookup is ``{"status": "unknown", "error": ...}``."""
    info = recorded_retraction(ctx.ps, item_id)
    if info is not None:
        return {"status": info.status, "sources": info.sources, "checked": "recorded"}
    info, err = per_item(f"retraction lookup of {item_id}", lambda: ctx.sources.retraction(item_id))
    if err:
        return {"status": "unknown", "error": err}
    record_verification(ctx.ps, "item", item_id, "V-RETRACT", info.status, info.model_dump(), actor=ctx.actor)
    ctx.lib.update(item_id, retraction=info.status, actor=ctx.actor)
    if info.status in FLAGGED:
        cols = ctx.lib.ensure_default_collections(actor=ctx.actor)
        ctx.lib.collect(item_id, cols[FLAG_COLLECTION], actor=ctx.actor, actor_kind="kernel")
    return {"status": info.status, "sources": info.sources, "checked": "looked_up"}


def _value_problem(field: ExtractionField, value) -> str | None:
    """Why ``value`` does not fit ``field`` (``None``: it fits)."""
    if value == NOT_REPORTED:
        return None
    if value is None:
        return f'no value (give "{NOT_REPORTED}" with a quote of the section searched when it is not reported)'
    if field.type == "number" and (isinstance(value, bool) or not isinstance(value, int | float)):
        return "not a number"
    if field.type == "category" and value not in (field.categories or []):
        return f"not one of the categories ({', '.join(field.categories or [])})"
    if field.type == "text" and not (isinstance(value, str) and value.strip()):
        return "not a text"
    return None


def _same_quote(ps: ProjectStore, item_id: str, v: ExtractValue, against: str, sha: str | None) -> str | None:
    """A verified quote note of the item with the value's quote (same text it was verified against, same locator when
    one is given), so the same quote is not added twice."""
    for n in list_notes(ps, item_id):
        q = n["verified"].get("quote") or {}
        if (n["kind"] == "quote" and n["text"] == v.quote and q.get("ok") and q.get("against", "text") == against
                and n.get("snapshot_sha") == sha
                and (v.locator is None or n["locator"] == v.locator.model_dump())):
            return n["id"]
    return None


def _quote(ctx: StageContext, item_id: str, field: str, v: ExtractValue, against: str) -> tuple[str | None, str | None]:
    """The value's quote as a verified quote note (V-QUOTE): ``(note id, None)``, or ``(None, problem)``."""
    snap = latest_snapshot(ctx.ps, item_id) if against == "text" else None
    if (nid := _same_quote(ctx.ps, item_id, v, against, snap[0] if snap else None)) is not None:
        return nid, None
    try:
        note = add_note(ctx.ps, ctx.lib, NoteIn(source_id=item_id, kind="quote", text=v.quote, locator=v.locator,
                                                role_in_source="main", keywords=["extraction", field],
                                                against=against),
                        actor=ctx.actor, actor_kind="agent")
    except NoteRejected as e:
        return None, QUOTE_FAILED if e.reason == "quote_not_found" else f"quote not verified ({e.reason})"
    except ValueError:  # an empty quote
        return None, "no quote"
    return note.note_id, None


def _inputs(ctx: StageContext, protocol: Protocol, rec: dict, kind: str) -> dict:
    item = ctx.lib.get(rec["item_id"])
    study = {"item_id": rec["item_id"], "title": item.title, "year": item.year, "venue": item.venue,
             "text_kind": "full_text" if kind == FULL else "abstract_only", "abstract": item.abstract or ""}
    if kind == FULL:
        study["pages"] = len(latest_snapshot(ctx.ps, rec["item_id"])[1].pages)
    out = {"question": protocol.question, "study": study,
           "fields": [f.model_dump(mode="json", exclude_none=True) for f in protocol.extraction_schema]}
    if ctx.guidance:
        out["guidance"] = ctx.guidance
    return out


def _read(ctx: StageContext, protocol: Protocol, rec: dict, kind: str) -> tuple[list[str], list[str]]:
    """The reader's extraction of one study, stored value by value. Returns the notes it linked and the problems."""
    iid = rec["item_id"]
    inputs = _inputs(ctx, protocol, rec, kind)
    out, error = None, "no output"
    for _ in range(READS):
        r = ctx.executor.run(ROLE, EXTRACT_TASK, inputs, ExtractOut, stage=ctx.stage, attempt=ctx.attempt,
                             tools=READER_TOOLS if kind == FULL else [])
        if r.ok and r.output is not None:
            out = r.output
            break
        error = r.error or "no output"
    if out is None:
        return [], [f"the reader gave no extraction ({error}), also when asked again"]
    fields = {f.name: f for f in protocol.extraction_schema}
    against, sufficient = ("text", True) if kind == FULL else ("abstract", False)
    problems: list[str] = []
    for v in out.values:
        f = fields.get(v.field)
        if f is None:
            problems.append(f'a value for "{v.field}", not a field of the extraction schema, was ignored')
            continue
        note_id, problem = None, _value_problem(f, v.value)
        if problem is None:
            note_id, problem = _quote(ctx, iid, f.name, v, against)
        add_extraction(ctx.ps, ctx.attempt, iid, f.name, v.value, note_id, sufficient, actor=ctx.actor,
                       problem=problem)
    linked: list[str] = []
    for nid in dict.fromkeys(out.notes):
        n = _note_data(ctx.ps, nid)
        if n is None or n["item_id"] != iid:
            problems.append(f"note {nid} is not a note of this study")
        else:
            linked.append(nid)
    return linked, problems


# ---- the worker ----
class ReadExtractWorker:
    def run(self, ctx: StageContext) -> StageOutcome:
        ps = ctx.ps
        row = frozen_protocol(ps)
        if row is None:
            return StageOutcome("failed", reason="no frozen protocol")
        studies = included_studies(ps)
        if not studies:
            return StageOutcome("back_edge", to=SEARCH_SCREEN, reason="no included study to read")
        mark_protocol(ps, ctx.stage, ctx.attempt, row["frozen_hash"], actor=ctx.actor)
        protocol = Protocol.model_validate(row["body"])
        done = set(_studies(ps, ctx.attempt))
        carried = carried_extraction(ps, ctx.stage, ctx.attempt, row["frozen_hash"])
        for rec in studies:
            iid = rec["item_id"]
            if iid in done:
                continue
            c = carried.get(iid)
            if c is not None and (c.note["flagged"] or c.note["text"] == _text_kind(ctx, rec)):
                self._carry(ctx, c)
            else:
                self._study(ctx, protocol, rec)
        noted = _studies(ps, ctx.attempt)
        reason = _summary(ps, ctx.attempt, len(studies))
        if all(noted[r["item_id"]]["flagged"] for r in studies):
            return StageOutcome("back_edge", to=SEARCH_SCREEN, reason=f"{NO_STUDY}: {reason}")
        return StageOutcome("done", reason=reason)

    def _carry(self, ctx: StageContext, c: CarriedStudy) -> None:
        """Copy a study an interrupted attempt finished, its values and then its note, into this attempt."""
        for e in c.values:
            add_extraction(ctx.ps, ctx.attempt, e["item_id"], e["field"], e["value"], e["note_id"], bool(e["sufficient"]),
                           actor=ctx.actor, actor_kind="kernel", problem=e["problem"], carried_from=e["carried_from"])
        fields = {k: v for k, v in c.note.items() if k not in ("attempt", "kind")}
        add_extraction_note(ctx.ps, ctx.attempt, "study", actor=ctx.actor, **fields)

    def _study(self, ctx: StageContext, protocol: Protocol, rec: dict) -> None:
        """Check one study's retraction and, unless it is retracted or withdrawn, read it; its ``study`` note last."""
        iid = rec["item_id"]
        problems: list[str] = []
        retraction = _retraction(ctx, iid)
        if err := retraction.get("error"):
            problems.append(err)
        flagged = retraction["status"] in RETRACTED
        kind: str | None = None
        linked: list[str] = []
        if not flagged:
            kind = _text_kind(ctx, rec)
            if kind is None:
                problems.append("no full text and no abstract to read")
            else:
                got, err = per_item(f"reading of {iid}", lambda: _read(ctx, protocol, rec, kind))
                if err:
                    problems.append(err)
                else:
                    linked, more = got
                    problems += more
        add_extraction_note(ctx.ps, ctx.attempt, "study", actor=ctx.actor, item_id=iid, record_id=rec["id"],
                            retraction=retraction, flagged=flagged, text=kind,
                            sufficient=None if kind is None else ("full" if kind == FULL else "low"),
                            notes=linked, problems=problems, carried_from=None)

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        """``read_extract`` opens no checkpoint of its own (a gate failure reruns it; at the edge limit the engine asks
        with ``stage_blocked``)."""
        return StageOutcome("failed", reason=f"read_extract cannot apply checkpoint {checkpoint['id']} "
                                             f"({checkpoint['kind']})")

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        """Every non-flagged included study was read and its retraction checked, every field has a value with a
        verified quote note (or ``not_reported`` with one, or was marked missing by the P1 degrade), every linked
        paraphrase note passes V-PARA, and a study is left. Each failure names the study, the field and the reason."""
        failures = [_say(ps, f) for f in findings(ps, attempt)]
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at the edge limit: mark every failing field missing (a study never read: all its fields), take a study
        whose retraction could not be checked out of the evidence, drop a paraphrase note failing V-PARA; disclosed
        and listed in the limitations input. Fails when no study is left."""
        ps = ctx.ps
        found = findings(ps, ctx.attempt)
        if any(iid is None for iid, _, _ in found):
            return StageOutcome("failed", reason="read_extract did not pass at the edge limit: "
                                                 + "; ".join(_say(ps, f) for f in found))
        row = frozen_protocol(ps)
        fields = [f.name for f in Protocol.model_validate(row["body"]).extraction_schema]
        studies = _studies(ps, ctx.attempt)
        missing: list[dict] = []
        excluded: list[dict] = []
        dropped: list[str] = []
        for iid, field, why in found:
            if field is not None:
                missing.append({"item_id": iid, "field": field, "reason": why})
            elif why == "not read":
                missing += [{"item_id": iid, "field": f, "reason": why} for f in fields]
            elif why.startswith("retraction not checked"):
                excluded.append({"item_id": iid, "reason": why})
            else:  # a paraphrase note failing V-PARA
                dropped.append(why.split()[2])
        out = {e["item_id"] for e in excluded}
        missing = [m for m in missing if m["item_id"] not in out]
        for m in missing:
            s = studies.get(m["item_id"])
            add_extraction(ps, ctx.attempt, m["item_id"], m["field"], None, None,
                           s is None or s["sufficient"] != "low", actor=ctx.actor, actor_kind="kernel",
                           problem=f"{MISSING}: {m['reason']}")
        add_extraction_note(ps, ctx.attempt, "degraded", actor=ctx.actor, missing=missing, excluded=excluded,
                            dropped_notes=dropped)
        removed = ([f"{m['item_id']}:{m['field']}" for m in missing] + [e["item_id"] for e in excluded] + dropped)
        record_degraded(ps, stage=ctx.stage, attempt=ctx.attempt, removed=removed,
                        reason=f"read_extract at the edge limit: {len(missing)} values marked missing, "
                               f"{len(excluded)} studies taken out of the evidence (retraction not checked), "
                               f"{len(dropped)} paraphrase notes dropped", actor=ctx.actor)
        return StageOutcome("done", reason="degraded: " + "; ".join(failures))


def _summary(ps: ProjectStore, attempt: int, n: int) -> str:
    studies = list(_studies(ps, attempt).values())
    flagged = sum(1 for s in studies if s["flagged"])
    low = sum(1 for s in studies if s["sufficient"] == "low")
    carried = sum(1 for s in studies if s["carried_from"])
    rows = extractions(ps, attempt)
    kinds = Counter("not_reported" if e["value"] == NOT_REPORTED and e["problem"] is None
                    else "value" if e["problem"] is None else "problem" for e in rows)
    parts = [f"{n} included studies: {n - flagged} read ({low} on the abstract alone, sufficient=low)",
             f"{flagged} retracted or withdrawn (flagged, not extracted)",
             (f"{kinds['value']} values with a verified quote, {kinds['not_reported']} not reported, "
              f"{kinds['problem']} values with a problem")]
    if carried:
        parts.append(f"{carried} studies carried over from an interrupted attempt")
    return "; ".join(parts)
