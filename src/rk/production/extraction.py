"""``read_extract``: the reading of every included study and the extraction of the protocol's fields, each value
backed by a verified quote note.

``ReadExtractWorker.run``, per study ``search_screen`` included at full text in its passed attempt
(``included_studies``), each inside the per-item boundary (``rk.discovery.boundary.per_item``: a source, IO or parse
failure is recorded on that study, never fatal):

1. Retraction (V-RETRACT): the result recorded for the item, else ``sources.retraction(item)``, which is then recorded
   as a verification and on the item (a flagged status also puts it in the "Retracted / EoC" collection). A failed
   lookup is the study's problem: its retraction is not checked. A retracted or withdrawn study is flagged and not
   extracted; it stays a discussed object and is listed for the limitations.
2. The text it is read on (``_text``): its full text when the item has one (the latest snapshot, whatever the record
   says), else its abstract. A record marked retrieved whose full text is no longer stored, or a study with neither a
   full text nor an abstract, is not read: that is the study's problem (no silent fall-back to the abstract).
3. The ``reader`` reads the study: its full text with ``rk_text_find``, ``rk_text_read`` and ``rk_note_add``, or the
   abstract it is given (no tools). It returns ``ExtractOut``: ``values`` (``field``, ``value``, ``quote``, optional
   ``locator``) and ``notes`` (the ids of the notes it saved itself). A task without output is asked once more.
4. For each value the kernel checks the value against its field (a number, one of the categories, a text; or
   ``"not_reported"``, whose quote shows the section searched) and that it has a quote, then verifies the quote
   (V-QUOTE, ``rk.notes.locate_quote``: a given locator only where it holds the quote) against the text the study is
   read on: the full text, or the abstract (``against="abstract"``; never for an item with a full text). A record
   field (``ExtractionField.source="record"``: where the study was published, its type) is quoted from the study's
   bibliographic record instead (``against="record"``, ``rk.notes.record_text``, given to the reader as
   ``study.record``), never from its text, and a text field never from the record. A verified
   quote note of the item with the same text at the same verified position (same snapshot) is linked; else the quote
   is added with ``add_note(kind="quote")``. A value whose quote fails is stored with ``problem="quote not
   verbatim"`` and no note, as is a value that does not fit its field or has no quote (with that problem). A value of
   a field outside the schema is not stored; it is the study's problem, as is a listed note that is not the study's.
5. Values from the abstract carry ``sufficient`` False (``sufficient="low"``), and the study's note says so.
6. The study's ``study`` note (``rk.production.store.extraction_notes``) is written last: its retraction check,
   whether it is flagged, the text it was read on (and the snapshot), its sufficiency, the reader's notes and its
   problems.

When every included study is retracted or withdrawn the stage fails as a dead end naming them: ``read_extract``'s only
back-edge, to ``search_screen``, would screen the same studies again under the same protocol, and no control reopens
the frozen protocol, so the reason tells you to start a new project with a revised protocol. Else it is done, its
reason the counts.

An attempt interrupted by a rate cap, a budget or a crash is resumed by the next one (``rk.production.resume``): a
study an interrupted attempt right before it finished, under the same frozen protocol and read on the same text (the
same kind and the same snapshot), is carried over with its values and notes (``carried_from``) instead of being read
again; any other is read again. The stage opens no checkpoint, so an attempt never runs twice: every attempt starts
with no extraction of its own.

When ``synthesize->read_extract`` reopened the stage naming studies (``reread_request``: the kernel's per-study gaps
or the studies you named on a synthesize checkpoint), the attempt reads only those studies again; every other study
keeps its values and notes from the passed attempt (``kept_extraction``, ``carried_from``), unless its text changed
or the P1 degrade touched it (then it is read again too). When a failed gate reopened it
(``read_extract->read_extract``, ``rerun_kept``), the attempt reads again only the studies the gate found something
against; every other study keeps its values and notes from the attempt that failed, on the same conditions. Reading
all of them again would make new slips in studies that had passed, and the attempts would not converge.

The gate (``gate``, from ``findings``): every non-flagged included study was read and its retraction checked; every
extraction field of each has a value whose quote note is a verified quote of its source: for a text field the text the
study was read on (the same kind, the same snapshot, which is still the item's latest), for a record field the study's
bibliographic record, with a value that is what the quoted line says (``_record_value_problem``: not "preprint"
quoting "Publication type: journal article"); or ``not_reported`` with such a quote, or (after a P1 degrade) is marked
missing; every note the reader linked is of the text it was read on and every linked paraphrase passes V-PARA; at
least one study is left. Each failure names the study, the field and the reason. A failed gate reruns the stage
(``read_extract->read_extract``); at the edge limit P2 is asked (``stage_blocked``) and P1 degrades (``degrade``): the
failing fields are marked missing (their earlier rows marked ``degraded: …``, so none of them passes as evidence), a
study whose retraction could not be checked (or that was never read) is taken out of the evidence and its rows marked
``excluded: …`` (a mark is added to a row's earlier problem), a failing linked note is dropped, all disclosed
(``stage.degraded``) and listed in the limitations input (``extraction_limitations``). A degrade that would leave no
study in the evidence fails instead, writing nothing.

``usable_evidence`` is what later stages read: the studies left in the evidence, their rows and their notes without
the dropped ones."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from rk.canon import canonical_json
from rk.discovery.boundary import per_item
from rk.discovery.gaps.retraction import recorded_retraction
from rk.fulltext import latest_snapshot
from rk.notes import (
    Locator,
    NoteIn,
    NoteRejected,
    add_note,
    list_notes,
    locate_quote,
    record_text,
)
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import attempt_ran, record_degraded, stage_row
from rk.production.models import FULL_TEXT, ExtractionField, Protocol
from rk.production.resume import (
    CarriedStudy,
    carried_extraction,
    kept_extraction,
    mark_protocol,
    protocol_of,
)
from rk.production.store import (
    add_extraction,
    add_extraction_note,
    extraction_notes,
    extractions,
    finals,
    frozen_protocol,
    mark_extraction,
    records,
)
from rk.store.project import ProjectStore
from rk.verify.paraphrase import check_paraphrase
from rk.verify.records import FLAG_COLLECTION, record_verification
from rk.verify.retract import FLAGGED

STAGE = "read_extract"
ROLE = "reader"
READER_TOOLS = ["rk_text_find", "rk_text_read", "rk_note_add"]
NOT_REPORTED = "not_reported"  # the value of a field the study does not report; its quote shows the section searched
QUOTE_FAILED = "quote not verbatim"
MISSING = "missing"  # the problem of a value the P1 degrade marked missing starts with "missing: "
EXCLUDED = "excluded"  # the problem of a row whose study the P1 degrade took out of the evidence: "excluded: "
DEGRADED = "degraded"  # the problem of a row of a field the P1 degrade marked missing: "degraded: "
RETRACTED = frozenset({"retracted", "withdrawn"})  # flagged: not extracted, kept as a discussed object
READS = 2  # a reader task without output is asked once more
SEARCH_SCREEN = "search_screen"
NO_STUDY = "no included study is left after the retraction check"
NOT_READ = "not read"
FULL, ABSTRACT = "full_text", "abstract"  # the text a study is read on
RECORD = "record"  # the source of a record field (``ExtractionField.source``): the study's bibliographic record
CHANGED = "the study's full text changed since it was read"

EXTRACT_TASK = (
    "Extract the given fields from the given study for the review question. Read the study's text with rk_text_find "
    "and rk_text_read (pages numbered from 1). When its text_kind is abstract_only it has no full text and you have "
    "no tools: read only the given abstract. For every field give at least one value: field (the field's name), value "
    "(a number for a number field, in its unit; one of the categories for a category field; a short text for a text "
    "field) and quote, a passage copied character for character from the text (from the abstract when the study is "
    "abstract_only) that shows the value, with the locator rk_text_find gave you when there is one. A field whose "
    "source is record is about the study's bibliographic record, not its text: quote its value from the study's "
    "record (copied character for character from it, no locator), e.g. the venue or the publication type line. When "
    f'the study does not report a field, give the value "{NOT_REPORTED}" with a quote of the section you searched for '
    "it (e.g. the first sentence of its methods or results; for a record field, the record's line that says not "
    "given). The kernel "
    "verifies every quote and rejects a value whose quote is not verbatim. Do not save the value quotes yourself; in "
    "notes give the ids of the other notes you saved for this study with rk_note_add (a paraphrase needs its original "
    "passage as source_span). Follow the guidance when there is any.")


class ExtractValue(BaseModel):
    field: str
    value: int | float | str | None = None
    quote: str = ""
    locator: Locator | None = None


class ExtractOut(BaseModel):
    """The reader's extraction of one study."""
    values: list[ExtractValue]
    notes: list[str] = []


FindingKind = Literal["protocol", "no_study", "not_read", "retraction", "field", "note"]


@dataclass(frozen=True)
class Finding:
    """What keeps an attempt from its gate: a field of a study (``field``), a study (``not_read``, ``retraction``, a
    linked ``note``: ``note_id``) or the attempt (``protocol``, ``no_study``)."""
    kind: FindingKind
    reason: str
    item_id: str | None = None
    field: str | None = None
    note_id: str | None = None


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


def _marked(row: dict, mark: str) -> bool:
    return (row["problem"] or "").startswith(f"{mark}: ")


def is_missing(row: dict) -> bool:
    """Whether an extraction row is a value the P1 degrade marked missing."""
    return _marked(row, MISSING)


def _note_data(ps: ProjectStore, note_id: str) -> dict | None:
    r = ps.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def _title(ps: ProjectStore, item_id: str) -> str:
    r = ps.db.execute("SELECT json_extract(data, '$.title') AS t FROM items WHERE id=?", (item_id,)).fetchone()
    return r["t"] if r and r["t"] else ""


def _study_label(ps: ProjectStore, item_id: str) -> str:
    title = _title(ps, item_id)
    return f"study {item_id}" + (f' ("{title}")' if title else "")


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


def _snapshot_sha(ps: ProjectStore, item_id: str) -> str | None:
    snap = latest_snapshot(ps, item_id)
    return snap[0] if snap else None


def usable_evidence(ps: ProjectStore, attempt: int) -> list[dict]:
    """The attempt's evidence for later stages, one entry per included study left in it (not flagged as retracted or
    withdrawn, not taken out by the P1 degrade), in search order: ``item_id``, ``sufficient`` (``"full"`` or
    ``"low"``), ``values`` (its rows: a row with a problem, e.g. one marked missing, is no evidence but stays visible)
    and ``notes`` (the notes it links: its values' quote notes and the reader's notes, without the dropped ones)."""
    studies = _studies(ps, attempt)
    excluded, dropped = _degraded(ps, attempt)
    out = []
    for rec in included_studies(ps):
        s = studies.get(rec["item_id"])
        if s is None or s["flagged"] or s["item_id"] in excluded:
            continue
        rows = extractions(ps, attempt, item_id=s["item_id"])
        notes = [r["note_id"] for r in rows if r["note_id"] and r["problem"] is None] + s["notes"]
        out.append({"item_id": s["item_id"], "sufficient": s["sufficient"], "values": rows,
                    "notes": [n for n in dict.fromkeys(notes) if n not in dropped]})
    return out


# ---- the gate's findings ----
def _paraphrase_ok(n: dict) -> bool:
    """V-PARA: the note's recorded check passed and its text still is no near-copy of its source span."""
    return (bool(n["verified"].get("paraphrase", {}).get("ok"))
            and check_paraphrase(n["text"], n.get("source_span") or "").ok)


def _source_problem(n: dict, study: dict, current: str | None) -> str | None:
    """Why a verified note is not of the text the study was read on (``None``: it is)."""
    against = n["verified"].get("quote", {}).get("against", "text")
    if against == RECORD:
        return "its quote was verified against the study's record, not the text the study was read on"
    if study["text"] == ABSTRACT:
        return None if against == "abstract" else "its quote was not verified against the abstract the study was read on"
    if against != "text":
        return "its quote was verified against the abstract, not the full text the study was read on"
    if n.get("snapshot_sha") != study.get("snapshot"):
        return "its quote note is of another full text than the one the study was read on"
    if study.get("snapshot") != current:
        return CHANGED
    return None


def _quote_problem(ps: ProjectStore, study: dict, current: str | None, note_id: str,
                   source: str = "text") -> str | None:
    """Why a value's quote note is not usable evidence (``None``: it is). A value of a record field (``source``)
    is quoted from the study's record, any other from the text the study was read on."""
    n = _note_data(ps, note_id)
    if (n is None or n["item_id"] != study["item_id"] or n["kind"] != "quote"
            or not n["verified"].get("quote", {}).get("ok")):
        return "its quote note is not a verified quote of the study"
    if source == RECORD:
        ok = n["verified"]["quote"].get("against") == RECORD
        return None if ok else "its quote was not verified against the study's record (the field's source)"
    return _source_problem(n, study, current)


def _words(text) -> str:
    """``text`` lower-cased, its non-alphanumerics (``_`` included) one space each, padded: a phrase of it is
    ``f" {phrase} " in``."""
    return " " + " ".join(re.sub(r"[\W_]+", " ", str(text).casefold()).split()) + " "


def _record_value_problem(field: ExtractionField, value, quote: str) -> str | None:
    """Why a record field's value is not what its quoted line of the record says (``None``: it is, or the record does
    not word it): a category fails when the line names another category of the field and not the value; any other
    value must be in the line."""
    if value == NOT_REPORTED:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    said = _words(quote.split(":", 1)[-1])
    if _words(value) in said:
        return None
    if field.type == "category" and not any(_words(c) in said for c in field.categories or [] if c != value):
        return None
    return f'its value "{value}" is not what its quote of the record says ("{quote.strip()}")'


def _field_problem(ps: ProjectStore, study: dict, current: str | None, rows: list[dict],
                   field: ExtractionField) -> str | None:
    """Why a field of a study has no value the gate accepts (``None``: it has one)."""
    why = []
    for r in rows:
        if r["problem"] is None and r["note_id"]:
            p = _quote_problem(ps, study, current, r["note_id"], field.source)
            if p is None and field.source == RECORD:
                p = _record_value_problem(field, r["value"], _note_data(ps, r["note_id"])["text"])
            if p is None:
                return None
            why.append(p)
        elif is_missing(r):
            return None
        else:
            why.append(r["problem"] or "no quote note")
    return "; ".join(dict.fromkeys(why)) if why else "no value"


def findings(ps: ProjectStore, attempt: int) -> list[Finding]:
    """What keeps the attempt from its gate, in study and field order."""
    row = frozen_protocol(ps)
    if row is None:
        return [Finding("protocol", "no frozen protocol")]
    fields = {f.name: f for f in Protocol.model_validate(row["body"]).extraction_schema}
    studies = _studies(ps, attempt)
    excluded, dropped = _degraded(ps, attempt)
    out: list[Finding] = []
    left = 0
    for rec in included_studies(ps):
        iid = rec["item_id"]
        if iid in excluded:
            continue
        s = studies.get(iid)
        if s is None:
            out.append(Finding("not_read", NOT_READ, iid))
            left += 1
            continue
        if s["flagged"]:
            continue
        left += 1
        if err := s["retraction"].get("error"):
            out.append(Finding("retraction", f"retraction not checked: {err}", iid))
        current = _snapshot_sha(ps, iid) if s["text"] == FULL else None
        values = extractions(ps, attempt, item_id=iid)
        for f, field in fields.items():
            why = _field_problem(ps, s, current, [v for v in values if v["field"] == f], field)
            if why is not None:
                out.append(Finding("field", why, iid, f))
        for nid in s["notes"]:
            n = _note_data(ps, nid)
            if nid in dropped or n is None:
                continue
            why = None
            if n["kind"] in ("quote", "paraphrase"):
                why = _source_problem(n, s, current)
            if why is None and n["kind"] == "paraphrase" and not _paraphrase_ok(n):
                why = "fails V-PARA"
            if why is not None:
                out.append(Finding("note", f"{n['kind']} note {nid}: {why}", iid, note_id=nid))
    if not left:
        out.append(Finding("no_study", NO_STUDY))
    return out


def _say(ps: ProjectStore, f: Finding) -> str:
    if f.item_id is None:
        return f.reason
    return _study_label(ps, f.item_id) + (f', field "{f.field}"' if f.field else "") + f": {f.reason}"


def extraction_limitations(ps: ProjectStore, attempt: int) -> dict:
    """The limitations input of the attempt: the studies flagged as retracted or withdrawn (not extracted), those
    extracted despite another retraction flag (partial retraction, expression of concern), those whose retraction
    status is unknown, those the P1 degrade took out of the evidence, those read on the abstract alone
    (``sufficient="low"``), the values marked missing, the fields with conflicting verified values (``not_reported``
    included), and the studies' problems."""
    studies = list(_studies(ps, attempt).values())
    excluded, _ = _degraded(ps, attempt)
    rows = extractions(ps, attempt)
    missing = [{"item_id": e["item_id"], "field": e["field"], "reason": e["problem"][len(MISSING) + 2:]}
               for e in rows if is_missing(e)]
    conflicts = []
    usable = {s["item_id"] for s in studies if not s["flagged"] and s["item_id"] not in excluded}
    seen: dict[tuple[str, str], list] = {}
    for e in rows:
        if e["item_id"] in usable and e["problem"] is None and e["note_id"]:
            values = seen.setdefault((e["item_id"], e["field"]), [])
            if canonical_json(e["value"]) not in {canonical_json(v) for v in values}:
                values.append(e["value"])
    for (iid, field), values in seen.items():
        if len(values) > 1:
            conflicts.append({"item_id": iid, "field": field, "values": values})
    return {
        "flagged": [{"item_id": s["item_id"], "status": s["retraction"]["status"]} for s in studies if s["flagged"]],
        "retraction_concerns": [{"item_id": s["item_id"], "status": s["retraction"]["status"]} for s in studies
                                if not s["flagged"] and s["retraction"]["status"] in FLAGGED],
        "retraction_unknown": [s["item_id"] for s in studies
                               if s["retraction"]["status"] == "unknown" and not s["retraction"].get("error")],
        "excluded": [{"item_id": i, "reason": why} for i, why in excluded.items()],
        "low_sufficiency": [s["item_id"] for s in studies if s["sufficient"] == "low"],
        "missing": missing,
        "conflicts": conflicts,
        "problems": [{"item_id": s["item_id"], "problems": s["problems"]} for s in studies if s["problems"]],
    }


# ---- reading ----
def _text(ctx: StageContext, rec: dict) -> tuple[str | None, str | None, str | None]:
    """The text a study is read on: ``(kind, snapshot sha, problem)``. Its full text when the item has one, else its
    abstract; a record marked retrieved without a stored full text, or a study with no text at all, is not read."""
    iid = rec["item_id"]
    if (sha := _snapshot_sha(ctx.ps, iid)) is not None:
        return FULL, sha, None
    if rec["retrieved"]:
        return None, None, "recorded as retrieved, but no full text is stored: not read"
    if (ctx.lib.get(iid).abstract or "").strip():
        return ABSTRACT, None, None
    return None, None, "no full text and no abstract to read"


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


def _same_quote(ps: ProjectStore, item_id: str, text: str, against: str, sha: str | None,
                locator: Locator) -> str | None:
    """A verified quote note of the item with ``text`` at the verified ``locator`` of the same text (snapshot)."""
    for n in list_notes(ps, item_id):
        q = n["verified"].get("quote") or {}
        if (n["kind"] == "quote" and n["text"] == text and q.get("ok") and q.get("against", "text") == against
                and n.get("snapshot_sha") == sha and n["locator"] == locator.model_dump()):
            return n["id"]
    return None


def _quote(ctx: StageContext, item_id: str, field: str, v: ExtractValue, against: str) -> tuple[str | None, str | None]:
    """The value's quote as a verified quote note (V-QUOTE): ``(note id, None)``, or ``(None, problem)``. The quote is
    verified first; a note of it at the verified position is linked instead of adding another."""
    if not v.quote.strip():
        return None, "no quote"
    note = NoteIn(source_id=item_id, kind="quote", text=v.quote, locator=v.locator, role_in_source="main",
                  keywords=["extraction", field], against=against)
    try:
        sha, locator = locate_quote(ctx.ps, ctx.lib, note)
        if (nid := _same_quote(ctx.ps, item_id, v.quote, against, sha, locator)) is not None:
            return nid, None
        return add_note(ctx.ps, ctx.lib, note, actor=ctx.actor, actor_kind="agent").note_id, None
    except NoteRejected as e:
        return None, QUOTE_FAILED if e.reason == "quote_not_found" else f"quote not verified ({e.reason})"


def _inputs(ctx: StageContext, protocol: Protocol, rec: dict, kind: str) -> dict:
    item = ctx.lib.get(rec["item_id"])
    study = {"item_id": rec["item_id"], "title": item.title, "year": item.year, "venue": item.venue,
             "text_kind": "full_text" if kind == FULL else "abstract_only", "abstract": item.abstract or ""}
    if kind == FULL:
        study["pages"] = len(latest_snapshot(ctx.ps, rec["item_id"])[1].pages)
    out = {"question": protocol.question, "study": study,
           "fields": [f.model_dump(mode="json", exclude_none=True) for f in protocol.extraction_schema]}
    if any(f.source == RECORD for f in protocol.extraction_schema):
        study["record"] = record_text(item)  # what a record field's quote is verified against
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
    against, sufficient = ("text", True) if kind == FULL else ("abstract", False)  # a record field: its record
    problems: list[str] = []
    for v in out.values:
        f = fields.get(v.field)
        if f is None:
            problems.append(f'a value for "{v.field}", not a field of the extraction schema, was ignored')
            continue
        note_id, problem = None, _value_problem(f, v.value)
        if problem is None:
            note_id, problem = _quote(ctx, iid, f.name, v, RECORD if f.source == RECORD else against)
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


# ---- the ways back into the stage ----
SYNTHESIZE_BACK = "synthesize->read_extract"
RERUN = f"{STAGE}->{STAGE}"


def _reopen(ps: ProjectStore, attempt: int):
    """The ``stage.reopened`` event the attempt started after (its ``seq`` and ``payload``), or ``None``."""
    start = ps.db.execute("SELECT seq FROM events WHERE type='stage.attempt' AND json_extract(payload, '$.stage')=? "
                          "AND json_extract(payload, '$.attempt')=? ORDER BY seq LIMIT 1", (STAGE, attempt)).fetchone()
    if start is None:
        return None
    return ps.db.execute("SELECT seq, payload FROM events WHERE type='stage.reopened' AND "
                         "json_extract(payload, '$.stage')=? AND seq<? ORDER BY seq DESC LIMIT 1",
                         (STAGE, start["seq"])).fetchone()


def reread_request(ps: ProjectStore, attempt: int) -> set[str] | None:
    """The studies the attempt reads again when it starts after ``synthesize->read_extract`` reopened the stage and
    synthesize's ``back_edge`` note (the kernel's per-study gaps, or the studies you named) names studies: their item
    ids. ``None``: no such request (any other attempt, or a back-edge that names no study). An attempt that replaces
    an interrupted one keeps the request (no reopen between them); a rerun after a failed gate is ``rerun_kept``'s."""
    reopen = _reopen(ps, attempt)
    if reopen is None or json.loads(reopen["payload"])["by_edge"] != SYNTHESIZE_BACK:
        return None
    note = ps.db.execute("SELECT payload FROM events WHERE type='synthesis.noted' AND "
                         "json_extract(payload, '$.kind')='back_edge' AND seq<? ORDER BY seq DESC LIMIT 1",
                         (reopen["seq"],)).fetchone()
    studies = json.loads(note["payload"]).get("studies") if note else None
    return {x["item_id"] for x in studies} if studies else None


def _undegraded(ps: ProjectStore, attempt: int, studies: dict[str, CarriedStudy]) -> dict[str, CarriedStudy]:
    """``studies`` of ``attempt`` without those its P1 degrade touched (taken out of the evidence, a value marked
    missing, a linked note dropped): those are read again."""
    excluded, dropped = _degraded(ps, attempt)
    return {iid: c for iid, c in studies.items()
            if iid not in excluded and not set(c.note["notes"]) & dropped
            and not any(_marked(e, m) for e in c.values for m in (MISSING, EXCLUDED, DEGRADED))}


def _kept(ps: ProjectStore, protocol_hash: str) -> dict[str, CarriedStudy]:
    """The studies of the passed attempt an attempt reading only some studies again keeps (``kept_extraction``), when
    that attempt worked under the same frozen protocol (``_undegraded``)."""
    row = stage_row(ps, STAGE)
    passed = row["passed_attempt"] if row else None
    if not passed or protocol_of(ps, STAGE, passed) not in (None, protocol_hash):
        return {}
    return _undegraded(ps, passed, kept_extraction(ps, passed))


def rerun_kept(ps: ProjectStore, attempt: int, protocol_hash: str) -> dict[str, CarriedStudy]:
    """After a failed gate reopened the stage (``read_extract->read_extract``): the studies of the attempt it failed
    on (the last one that ran to completion, under the same frozen protocol) that the gate found nothing against,
    which the rerun keeps (``kept_extraction``, ``_undegraded``); it reads the others again. Reading every study
    again would make new slips in the studies that had passed, so the attempts would not converge. Nothing is kept
    after any other reopen, or when the gate found something against the attempt as a whole."""
    reopen = _reopen(ps, attempt)
    if reopen is None or json.loads(reopen["payload"])["by_edge"] != RERUN:
        return {}
    failed = next((a for a in range(attempt - 1, 0, -1) if attempt_ran(ps, STAGE, a)), None)
    if failed is None or protocol_of(ps, STAGE, failed) not in (None, protocol_hash):
        return {}
    found = findings(ps, failed)
    if any(f.item_id is None for f in found):
        return {}
    failing = {f.item_id for f in found}
    return _undegraded(ps, failed, {iid: c for iid, c in kept_extraction(ps, failed).items() if iid not in failing})


class ReadExtractWorker:
    def run(self, ctx: StageContext) -> StageOutcome:
        ps = ctx.ps
        row = frozen_protocol(ps)
        if row is None:
            return StageOutcome("failed", reason="no frozen protocol")
        studies = included_studies(ps)
        if not studies:
            return StageOutcome("failed", reason="no included study to read: search_screen has not passed")
        mark_protocol(ps, ctx.stage, ctx.attempt, row["frozen_hash"], actor=ctx.actor)
        protocol = Protocol.model_validate(row["body"])
        carried = carried_extraction(ps, ctx.stage, ctx.attempt, row["frozen_hash"])
        reread = reread_request(ps, ctx.attempt)
        kept = _kept(ps, row["frozen_hash"]) if reread is not None else rerun_kept(ps, ctx.attempt, row["frozen_hash"])
        read = 0
        for rec in studies:
            iid = rec["item_id"]
            kind, sha, _ = _text(ctx, rec)
            c = carried.get(iid)
            if c is None and (reread is None or iid not in reread):
                c = kept.get(iid)
            if c is not None and (c.note["flagged"] or (kind is not None and c.note["text"] == kind
                                                        and c.note.get("snapshot") == sha)):
                self._carry(ctx, c)
            else:
                self._study(ctx, protocol, rec)
                read += 1
        noted = _studies(ps, ctx.attempt)
        reason = _summary(ps, ctx.attempt, len(studies))
        if reread is not None:
            reason += (f"; synthesize sent read_extract back to read {len(reread)} "
                       f"{'study' if len(reread) == 1 else 'studies'} again, every other study kept its extraction")
        elif kept:
            reason += (f"; after the failed gate {read} {'study was' if read == 1 else 'studies were'} read again, "
                       "every study the gate found nothing against kept its extraction")
        if all(noted[r["item_id"]]["flagged"] for r in studies):
            which = "; ".join(f"{_study_label(ps, r['item_id'])}: {noted[r['item_id']]['retraction']['status']}"
                              for r in studies)
            return StageOutcome("failed", reason=(
                f"dead end: every included study is retracted or withdrawn ({which}). Going back to search_screen "
                "would screen the same studies again under the same protocol, and the frozen protocol cannot be "
                "reopened: start a new project with a revised protocol (its eligibility or search strategy)."))
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
        sha: str | None = None
        linked: list[str] = []
        if not flagged:
            kind, sha, why = _text(ctx, rec)
            if why:
                problems.append(why)
            else:
                got, err = per_item(f"reading of {iid}", lambda: _read(ctx, protocol, rec, kind))
                if err:
                    problems.append(err)
                else:
                    linked, more = got
                    problems += more
        add_extraction_note(ctx.ps, ctx.attempt, "study", actor=ctx.actor, item_id=iid, record_id=rec["id"],
                            retraction=retraction, flagged=flagged, text=kind, snapshot=sha,
                            sufficient=None if kind is None else ("full" if kind == FULL else "low"),
                            notes=linked, problems=problems, carried_from=None)

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        """``read_extract`` opens no checkpoint of its own (a gate failure reruns it; at the edge limit the engine asks
        with ``stage_blocked``)."""
        return StageOutcome("failed", reason=f"read_extract cannot apply checkpoint {checkpoint['id']} "
                                             f"({checkpoint['kind']})")

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        """Every non-flagged included study was read and its retraction checked, every field has a value whose quote
        note is a verified quote of the text the study was read on (or ``not_reported`` with one, or was marked missing
        by the P1 degrade), every linked note is of that text and every linked paraphrase passes V-PARA, and a study is
        left. Each failure names the study, the field and the reason."""
        failures = [_say(ps, f) for f in findings(ps, attempt)]
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at the edge limit: mark every failing field missing (its earlier rows marked ``degraded: …``), take a
        study whose retraction could not be checked or that was never read out of the evidence (its rows marked
        ``excluded: …``), drop a failing linked note; disclosed and listed in the limitations input. Fails, writing
        nothing, when no study would be left."""
        ps = ctx.ps
        found = findings(ps, ctx.attempt)
        if dead := [f for f in found if f.kind in ("protocol", "no_study")]:
            return StageOutcome("failed", reason="read_extract did not pass at the edge limit: "
                                                 + "; ".join(_say(ps, f) for f in dead))
        excluded = {f.item_id: f.reason for f in found if f.kind in ("not_read", "retraction")}
        studies = _studies(ps, ctx.attempt)
        already, _ = _degraded(ps, ctx.attempt)
        left = [r["item_id"] for r in included_studies(ps) if r["item_id"] not in excluded
                and r["item_id"] not in already and not studies.get(r["item_id"], {}).get("flagged")]
        if not left:
            why = "; ".join(f"{_study_label(ps, i)}: {r}" for i, r in excluded.items())
            return StageOutcome("failed", reason=f"read_extract did not pass at the edge limit, and degrading it would "
                                                 f"take every study out: no study would be left in the evidence ({why}). "
                                                 "This project's run cannot get past it (the frozen protocol cannot be "
                                                 "reopened): start a new project with a revised protocol.")
        missing = [{"item_id": f.item_id, "field": f.field, "reason": f.reason} for f in found
                   if f.kind == "field" and f.item_id not in excluded]
        dropped = [f.note_id for f in found if f.kind == "note" and f.item_id not in excluded]
        for iid, why in excluded.items():
            for e in extractions(ps, ctx.attempt, item_id=iid):
                mark_extraction(ps, e["id"], f"{EXCLUDED}: {why}", actor=ctx.actor)
        for m in missing:
            for e in extractions(ps, ctx.attempt, item_id=m["item_id"]):
                if e["field"] == m["field"]:
                    mark_extraction(ps, e["id"], f"{DEGRADED}: {m['reason']}", actor=ctx.actor)
            add_extraction(ps, ctx.attempt, m["item_id"], m["field"], None, None,
                           studies[m["item_id"]]["sufficient"] != "low", actor=ctx.actor, actor_kind="kernel",
                           problem=f"{MISSING}: {m['reason']}")
        add_extraction_note(ps, ctx.attempt, "degraded", actor=ctx.actor, missing=missing,
                            excluded=[{"item_id": i, "reason": r} for i, r in excluded.items()],
                            dropped_notes=dropped)
        removed = [f"{m['item_id']}:{m['field']}" for m in missing] + list(excluded) + dropped
        record_degraded(ps, stage=ctx.stage, attempt=ctx.attempt, removed=removed,
                        reason=f"read_extract at the edge limit: {len(missing)} values marked missing, "
                               f"{len(excluded)} studies taken out of the evidence (retraction not checked or not "
                               f"read), {len(dropped)} linked notes dropped", actor=ctx.actor)
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
        parts.append(f"{carried} studies carried over from an earlier attempt")
    return "; ".join(parts)
