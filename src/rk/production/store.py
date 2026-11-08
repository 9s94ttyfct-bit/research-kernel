"""Event-sourced production artifacts: protocols and their deviations, records, screening decisions and final
decisions, extractions, votes and method designs. Every write is one event; the tables below are projections of them.
The queries of attempt-keyed artifacts (records, screening, extractions) read one attempt, as the discovery store
does."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, get_args

from rk.canon import canonical_json, sha256_hex
from rk.discovery.store import frozen_rq, hypotheses
from rk.ids import new_id
from rk.production.models import DecidedBy, DesignIn, Include, Protocol, ScreeningPhase
from rk.store.events import ActorKind, Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.store.query import query_rows, require_row

RECORD_FIELDS = frozenset({"retrieved", "abstract_only", "problem"})


def _check(name: str, value: str, allowed: Any) -> None:
    if value not in get_args(allowed):
        raise ValueError(f"unknown {name} {value!r}; expected one of {get_args(allowed)}")


# ---- protocols ----
def add_protocol(ps: ProjectStore, attempt: int, body: Protocol, *, version: int, actor: str,
                 actor_kind: ActorKind = "agent") -> str:
    pid = new_id("prt")
    ps.apply(NewEvent(type="protocol.added", entity_id=pid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "version": version, "body": body.model_dump(mode="json")}))
    return pid


def protocol_hash(body: dict) -> str:
    """The frozen protocol's identity: sha256 of the canonical JSON of its body."""
    return sha256_hex(canonical_json(body))


def freeze_protocol(ps: ProjectStore, protocol_id: str, *, actor: str, actor_kind: ActorKind = "agent") -> str:
    """Freeze the protocol; returns the hash of its body. A protocol freezes once."""
    with ps.lock:
        r = require_row(ps, "protocols", protocol_id)
        if r["status"] == "frozen":
            raise ValueError("protocol already frozen")
        h = protocol_hash(json.loads(r["body"]))
        ps.apply(NewEvent(type="protocol.frozen", entity_id=protocol_id, actor_kind=actor_kind, actor=actor,
                          payload={"frozen_hash": h}))
    return h


DeviationKind = Literal["version", "analysis"]


def add_deviation(ps: ProjectStore, from_v: int, to_v: int, reason: str, stage: str, *, actor: str,
                  actor_kind: ActorKind = "agent") -> str:
    """A ``version`` deviation from protocol version ``from_v`` (a stored version) to a later ``to_v``; returns its id
    (the event's ``evt_id``). Only these count for G-PREREG and the protocol gate."""
    if to_v <= from_v:
        raise ValueError(f"a deviation goes to a later version: {from_v} -> {to_v}")
    return _deviation_event(ps, "version", from_v, to_v, reason, stage, actor=actor, actor_kind=actor_kind)


def add_analysis_deviation(ps: ProjectStore, version: int, reason: str, stage: str, *, actor: str,
                           actor_kind: ActorKind = "kernel") -> str:
    """An ``analysis`` deviation: a later stage did not follow the stored protocol ``version`` (e.g. synthesize fell
    back from a pre-specified meta-analysis to a narrative synthesis) without a new protocol version; recorded from
    and to ``version``. It is shown with the protocol and in the limitations input, but never counts as the version
    deviation G-PREREG needs. Returns its id."""
    return _deviation_event(ps, "analysis", version, version, reason, stage, actor=actor, actor_kind=actor_kind)


def _deviation_event(ps: ProjectStore, kind: DeviationKind, from_v: int, to_v: int, reason: str, stage: str, *,
                     actor: str, actor_kind: ActorKind) -> str:
    with ps.lock:
        if ps.db.execute("SELECT 1 FROM protocols WHERE version=?", (from_v,)).fetchone() is None:
            raise ValueError(f"no protocol version {from_v} to deviate from")
        ev = ps.apply(NewEvent(type="protocol.deviation", entity_id=None, actor_kind=actor_kind, actor=actor,
                               payload={"kind": kind, "from_version": from_v, "to_version": to_v, "reason": reason,
                                        "stage": stage}))
    return ev.evt_id


# ---- method designs (research proposals) ----
def set_design(ps: ProjectStore, d: DesignIn, *, stage: str, attempt: int, actor: str,
               actor_kind: ActorKind = "agent") -> str:
    """A draft design; every hypothesis it tests (or names in ``falsified_by`` or ``hypotheses``) must be one of the
    frozen research question's and not dropped, so that freezing it can pre-register every one of them, and the text
    it records for a hypothesis must be that hypothesis's statement and falsification."""
    wanted = {h for item in d.items for h in item.hypothesis_ids} | set(d.falsified_by) | set(d.hypotheses)
    rq = frozen_rq(ps)
    rows = {h["id"]: h for h in hypotheses(ps, rq["id"])} if rq else {}
    if missing := sorted(wanted - set(rows)):
        raise ValueError(f"the design tests what are not hypotheses of the frozen research question: {missing}")
    if dropped := sorted(h for h in wanted if rows[h]["status"] == "dropped"):
        raise ValueError(f"the design tests dropped hypotheses: {dropped}")
    if wrong := sorted(h for h, t in d.hypotheses.items()
                       if (t.statement, t.falsification) != (rows[h]["statement"], rows[h]["falsification"])):
        raise ValueError(f"the design records another text than the hypothesis's for: {wrong}")
    did = new_id("dsg")
    ps.apply(NewEvent(type="design.set", entity_id=did, actor_kind=actor_kind, actor=actor,
                      payload={"stage": stage, "attempt": attempt, "body": d.model_dump(mode="json")}))
    return did


def freeze_design(ps: ProjectStore, design_id: str, *, actor: str, actor_kind: ActorKind = "kernel") -> str:
    """Freeze the design; returns its hash, sha256 of the canonical JSON of its body. A design freezes once."""
    with ps.lock:
        r = require_row(ps, "designs", design_id)
        if r["status"] == "frozen":
            raise ValueError("design already frozen")
        h = sha256_hex(canonical_json(json.loads(r["body"])))
        ps.apply(NewEvent(type="design.frozen", entity_id=design_id, actor_kind=actor_kind, actor=actor,
                          payload={"frozen_hash": h}))
    return h


# ---- records ----
def add_record(ps: ProjectStore, attempt: int, item_id: str, *, dedup_of: str | None = None, language: str,
               actor: str, actor_kind: ActorKind = "kernel") -> str:
    rid = new_id("rec")
    ps.apply(NewEvent(type="record.added", entity_id=rid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "item_id": item_id, "dedup_of": dedup_of, "language": language}))
    return rid


def set_record(ps: ProjectStore, record_id: str, *, actor: str, actor_kind: ActorKind = "kernel", **fields) -> None:
    """Set ``retrieved``, ``abstract_only`` (bools) and/or ``problem`` (text or None) of a record."""
    if not fields:
        raise ValueError(f"no record fields given; expected some of {sorted(RECORD_FIELDS)}")
    unknown = set(fields) - RECORD_FIELDS
    if unknown:
        raise ValueError(f"unknown record field(s) {sorted(unknown)}; expected some of {sorted(RECORD_FIELDS)}")
    require_row(ps, "records", record_id)
    payload = {k: bool(v) if k != "problem" else v for k, v in fields.items()}
    ps.apply(NewEvent(type="record.set", entity_id=record_id, actor_kind=actor_kind, actor=actor, payload=payload))


# ---- screening ----
def add_decision(ps: ProjectStore, attempt: int, record_id: str, phase: ScreeningPhase, screener: str,
                 include: Include, reason: str, criterion: str | None, *, actor: str,
                 actor_kind: ActorKind = "agent", carried_from: int | None = None) -> str:
    """One screener's decision on a record in a phase; ``criterion`` is the eligibility criterion id it rests on;
    ``carried_from``, the interrupted attempt it was made in when it is carried over (``rk.production.resume``)."""
    _check("phase", phase, ScreeningPhase)
    _check("include", include, Include)
    require_row(ps, "records", record_id)
    sid = new_id("scr")
    ps.apply(NewEvent(type="screening.decided", entity_id=sid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "record_id": record_id, "phase": phase, "screener": screener,
                               "include": include, "reason": reason, "criterion": criterion,
                               "carried_from": carried_from}))
    return sid


def set_final(ps: ProjectStore, record_id: str, attempt: int, phase: ScreeningPhase, include: Include,
              decided_by: DecidedBy, reason: str, *, actor: str, actor_kind: ActorKind = "kernel",
              carried_from: int | None = None) -> None:
    """The final decision on a record in a phase of an attempt; a later one replaces it. ``carried_from``: the
    interrupted attempt it was made in when it is carried over (``rk.production.resume``)."""
    _check("phase", phase, ScreeningPhase)
    _check("include", include, Include)
    _check("decided_by", decided_by, DecidedBy)
    require_row(ps, "records", record_id)
    ps.apply(NewEvent(type="screening.final", entity_id=record_id, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "phase": phase, "include": include, "decided_by": decided_by,
                               "reason": reason, "carried_from": carried_from}))


# ---- extractions ----
def add_extraction(ps: ProjectStore, attempt: int, item_id: str, field: str, value: Any, note_id: str | None,
                   sufficient: bool, *, actor: str, actor_kind: ActorKind = "agent", problem: str | None = None,
                   carried_from: int | None = None) -> str:
    """One extracted value (any JSON) of a protocol extraction field, with the note that supports it. ``sufficient``
    False is ``sufficient="low"``: the value comes from the abstract alone. ``carried_from``: the interrupted attempt
    it was made in, when carried over (``rk.production.resume``)."""
    eid = new_id("ext")
    ps.apply(NewEvent(type="extraction.added", entity_id=eid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "item_id": item_id, "field": field, "value": value,
                               "note_id": note_id, "sufficient": bool(sufficient), "problem": problem,
                               "carried_from": carried_from}))
    return eid


def add_extraction_note(ps: ProjectStore, attempt: int, kind: str, *, actor: str, **fields) -> None:
    """Something extraction notes in the attempt (``extraction_notes``)."""
    ps.apply(NewEvent(type="extraction.noted", entity_id=None, actor_kind="kernel", actor=actor,
                      payload={"attempt": attempt, "kind": kind, **fields}))


def extraction_notes(ps: ProjectStore, attempt: int, *, kind: str | None = None) -> list[dict]:
    """What extraction noted in the attempt, in order: ``study`` (one per included study once it is read, carried
    over or flagged: its retraction check, whether it is flagged, the text it was read on and its sufficiency, the
    reader's notes and its problems) and ``degraded`` (what the P1 degrade marked missing or took out of the
    evidence)."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='extraction.noted' "
                         "AND json_extract(payload, '$.attempt')=? ORDER BY seq", (attempt,))
    notes = [json.loads(r["payload"]) for r in rows]
    return [n for n in notes if kind is None or n["kind"] == kind]


def mark_extraction(ps: ProjectStore, extraction_id: str, problem: str, *, actor: str) -> None:
    """Add ``problem`` to a stored extraction row's ``problem`` ("; "-joined, keeping what it says and leaving out what
    it already says), e.g. ``"excluded: …"`` when the P1 degrade takes its study out of the evidence."""
    r = ps.db.execute("SELECT problem FROM extractions WHERE id=?", (extraction_id,)).fetchone()
    before = (r["problem"] if r else None) or ""
    if f"; {problem}; " in f"; {before}; ":
        return
    ps.apply(NewEvent(type="extraction.marked", entity_id=extraction_id, actor_kind="kernel", actor=actor,
                      payload={"problem": f"{before}; {problem}" if before else problem}))


# ---- votes ----
def add_vote(ps: ProjectStore, *, subject_type: str, subject_id: str, question: str, options: list[str],
             conservative: str, decision: str, conservative_used: bool, reason: str, opinions: list[dict],
             skipped: dict[str, str], stage: str, attempt: int, actor: str) -> str:
    """The P1 two-vendor vote's decision (``rk.production.vote``). ``opinions``: each judge that ran, as ``{role,
    vendor, choice, reason}``; ``skipped``: the judges not admitted, role -> why. Never human-reviewed."""
    vid = new_id("vot")
    ps.apply(NewEvent(type="vote.decided", entity_id=vid, actor_kind="kernel", actor=actor,
                      payload={"subject_type": subject_type, "subject_id": subject_id, "question": question,
                               "options": list(options), "conservative": conservative, "decision": decision,
                               "conservative_used": bool(conservative_used), "reason": reason,
                               "opinions": opinions, "skipped": skipped, "human_reviewed": False,
                               "stage": stage, "attempt": attempt}))
    return vid


def recorded_vote(ps: ProjectStore, subject_type: str, subject_id: str, *, stage: str, attempt: int) -> dict | None:
    """The latest vote recorded on the subject in the stage's attempt (its ``vote.decided`` payload with its ``id``),
    else ``None``: an attempt resumed after an interruption takes it instead of asking the judges again."""
    r = ps.db.execute("SELECT entity_id, payload FROM events WHERE type='vote.decided' "
                      "AND json_extract(payload, '$.subject_type')=? AND json_extract(payload, '$.subject_id')=? "
                      "AND json_extract(payload, '$.stage')=? AND json_extract(payload, '$.attempt')=? "
                      "ORDER BY seq DESC LIMIT 1", (subject_type, subject_id, stage, attempt)).fetchone()
    return {**json.loads(r["payload"]), "id": r["entity_id"]} if r else None


# ---- queries ----
def protocols(ps: ProjectStore, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM protocols WHERE attempt=? ORDER BY rowid", (attempt,), ("body",))


def protocol_row(ps: ProjectStore, protocol_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM protocols WHERE id=?", (protocol_id,), ("body",))
    return rows[0] if rows else None


def frozen_protocol(ps: ProjectStore) -> dict | None:
    """The frozen protocol in force, else ``None``. Project-level, not attempt-scoped: the protocol frozen in one
    attempt binds every later stage and attempt; should there be several, the highest version (then the latest)."""
    rows = query_rows(ps, "SELECT * FROM protocols WHERE status='frozen' ORDER BY version DESC, rowid DESC LIMIT 1",
                      (), ("body",))
    return rows[0] if rows else None


def deviations(ps: ProjectStore, *, kind: DeviationKind | None = None) -> list[dict]:
    """Every deviation (or those of one ``kind``), project-level: a deviation links protocol versions, which span
    attempts and stages. ``version``: a new protocol version over an earlier one; ``analysis``: a later stage did not
    follow the version in force (``add_analysis_deviation``)."""
    if kind is None:
        return query_rows(ps, "SELECT * FROM protocol_deviations ORDER BY rowid", ())
    return query_rows(ps, "SELECT * FROM protocol_deviations WHERE kind=? ORDER BY rowid", (kind,))


def design(ps: ProjectStore, design_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM designs WHERE id=?", (design_id,), ("body",))
    return rows[0] if rows else None


def records(ps: ProjectStore, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM records WHERE attempt=? ORDER BY rowid", (attempt,))


def record_row(ps: ProjectStore, record_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM records WHERE id=?", (record_id,))
    return rows[0] if rows else None


def decisions(ps: ProjectStore, attempt: int, *, phase: ScreeningPhase | None = None) -> list[dict]:
    sql, params = "SELECT * FROM screening WHERE attempt=?", (attempt,)
    if phase is not None:
        sql, params = sql + " AND phase=?", (attempt, phase)
    return query_rows(ps, sql + " ORDER BY rowid", params)


def finals(ps: ProjectStore, attempt: int, *, phase: ScreeningPhase | None = None) -> list[dict]:
    sql, params = "SELECT * FROM screening_final WHERE attempt=?", (attempt,)
    if phase is not None:
        sql, params = sql + " AND phase=?", (attempt, phase)
    return query_rows(ps, sql + " ORDER BY rowid", params)


def voted_out(final: dict) -> bool:
    """Whether a final is a P1 vote's exclusion of a screening disagreement (PRISMA lists them apart, and the
    limitations name the recall loss)."""
    return final["decided_by"] == "vote" and final["include"] == "no"


def add_screening_note(ps: ProjectStore, attempt: int, phase: str | None, kind: str, *, actor: str,
                       **fields) -> None:
    """Something screening notes in the attempt (``screening_notes``)."""
    ps.apply(NewEvent(type="screening.noted", entity_id=None, actor_kind="kernel", actor=actor,
                      payload={"attempt": attempt, "phase": phase, "kind": kind, **fields}))


def screening_notes(ps: ProjectStore, attempt: int, *, kind: str | None = None,
                    phase: str | None = None) -> list[dict]:
    """What screening noted in the attempt, in order (for the PRISMA notes and the limitations): ``stray_decision``
    (a decision on a record not in the batch, or a second one, ignored), ``unreasoned_decision`` (a decision without
    the reason its selection needs, ignored and asked again), ``failed_batch`` (a screener task gave no output),
    ``missing_decision`` (a record a screener left undecided, also when asked again), ``scoping_sample`` (the scoping
    review's dual sample, its differences on included-or-not and whether it switched to dual screening) and
    ``full_text_retrieval`` (the record ids sought for their full text, and those ``retrieved``, ``abstract_only`` and
    ``not_retrieved``; it also marks that the attempt reached the full-text phase)."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='screening.noted' "
                         "AND json_extract(payload, '$.attempt')=? ORDER BY seq", (attempt,))
    notes = [json.loads(r["payload"]) for r in rows]
    return [n for n in notes if (kind is None or n["kind"] == kind) and (phase is None or n["phase"] == phase)]


def retrieval_note(ps: ProjectStore, attempt: int) -> dict | None:
    """The attempt's ``full_text_retrieval`` note, or ``None`` when it never reached the full-text phase."""
    noted = screening_notes(ps, attempt, kind="full_text_retrieval")
    return noted[-1] if noted else None


def extractions(ps: ProjectStore, attempt: int, *, item_id: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM extractions WHERE attempt=?", (attempt,)
    if item_id is not None:
        sql, params = sql + " AND item_id=?", (attempt, item_id)
    return query_rows(ps, sql + " ORDER BY rowid", params, ("value",))


# ---- projections ----
@projection("protocol.added")
def _protocol(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO protocols(id, version, attempt, body, frozen_hash, frozen_at, status) "
                 "VALUES (?,?,?,?,NULL,NULL,'draft')", (ev.entity_id, p["version"], p["attempt"], json.dumps(p["body"])))


@projection("protocol.frozen")
def _protocol_frozen(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE protocols SET status='frozen', frozen_hash=?, frozen_at=? WHERE id=?",
                 (ev.payload["frozen_hash"], ev.ts, ev.entity_id))


@projection("protocol.deviation")
def _deviation(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO protocol_deviations(id, kind, from_version, to_version, reason, stage, ts) "
                 "VALUES (?,?,?,?,?,?,?)", (ev.evt_id, p.get("kind", "version"), p["from_version"], p["to_version"],
                                            p["reason"], p["stage"], ev.ts))


@projection("record.added")
def _record(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO records(id, attempt, item_id, dedup_of, language) VALUES (?,?,?,?,?)",
                 (ev.entity_id, p["attempt"], p["item_id"], p["dedup_of"], p["language"]))


@projection("record.set")
def _record_set(conn: sqlite3.Connection, ev: Event) -> None:
    for k, v in ev.payload.items():
        if k in RECORD_FIELDS:  # column names come from this fixed set only
            conn.execute(f"UPDATE records SET {k}=? WHERE id=?", (int(v) if k != "problem" else v, ev.entity_id))


@projection("screening.decided")
def _decision(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO screening(id, attempt, record_id, phase, screener, include, reason, criterion, "
                 "carried_from) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["attempt"], p["record_id"], p["phase"], p["screener"], p["include"], p["reason"],
                  p["criterion"], p.get("carried_from")))


@projection("screening.final")
def _final(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("DELETE FROM screening_final WHERE record_id=? AND attempt=? AND phase=?",
                 (ev.entity_id, p["attempt"], p["phase"]))
    conn.execute("INSERT INTO screening_final(record_id, attempt, phase, include, decided_by, reason, carried_from) "
                 "VALUES (?,?,?,?,?,?,?)", (ev.entity_id, p["attempt"], p["phase"], p["include"], p["decided_by"],
                                            p["reason"], p.get("carried_from")))


@projection("extraction.added")
def _extraction(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO extractions(id, attempt, item_id, field, value, note_id, sufficient, problem, "
                 "carried_from) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["attempt"], p["item_id"], p["field"], json.dumps(p["value"]), p["note_id"],
                  int(p["sufficient"]), p["problem"], p.get("carried_from")))


@projection("extraction.marked")
def _extraction_marked(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE extractions SET problem=? WHERE id=?", (ev.payload["problem"], ev.entity_id))


@projection("vote.decided")
def _vote(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO votes(id, subject_type, subject_id, question, decision, conservative, "
                 "opinions, ts) VALUES (?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["subject_type"], p["subject_id"], p["question"], p["decision"],
                  int(p["conservative_used"]), json.dumps(p["opinions"]), ev.ts))


@projection("design.set")
def _design(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO designs(id, stage, attempt, body, frozen_hash, frozen_at, status, created) "
                 "VALUES (?,?,?,?,NULL,NULL,'draft',?)", (ev.entity_id, p["stage"], p["attempt"],
                                                          canonical_json(p["body"]), ev.ts))


@projection("design.frozen")
def _design_frozen(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE designs SET status='frozen', frozen_hash=?, frozen_at=? WHERE id=?",
                 (ev.payload["frozen_hash"], ev.ts, ev.entity_id))
