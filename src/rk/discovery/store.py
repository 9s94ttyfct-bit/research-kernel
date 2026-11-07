"""Event-sourced discovery artifacts: the intake, search log, clusters, gaps, topics, research questions,
hypotheses and exploration rounds. Every write is one event; the tables below are projections of them."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal, NamedTuple

from rk.canon import canonical_json, sha256_hex
from rk.discovery.models import (
    Cluster,
    Gap,
    HypothesisIn,
    Intake,
    RQBody,
    Topic,
    TopicStatus,
)
from rk.ids import new_id
from rk.store.events import ActorKind, Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.store.query import query_rows, require_row


# ---- intake ----
def record_intake(ps: ProjectStore, intake: Intake, *, actor: str, actor_kind: ActorKind) -> None:
    ps.apply(NewEvent(type="project.intake", entity_id=ps.project_id, actor_kind=actor_kind, actor=actor,
                      payload=intake.model_dump(mode="json")))


def intake_of(ps: ProjectStore) -> Intake | None:
    values = {k: ps.meta(k) for k in Intake.model_fields}
    if values["discovery_mode"] is None:
        return None
    return Intake.model_validate(values)


# ---- writes ----
class SearchNote(NamedTuple):
    """What a logged search page says beyond its hits: ``incomplete``, why results the search needed were not
    searched (e.g. a source's paging limit); ``stopped_at_cap``, the search stopped after this page because it had
    passed the record cap with pages or searches left."""
    incomplete: str | None = None
    stopped_at_cap: bool = False


def log_search(ps: ProjectStore, *, stage: str, attempt: int, purpose: str, source: str, query: str, limit: int,
               item_ids: list[str], actor: str, actor_kind: ActorKind = "kernel", after_year: int | None = None,
               offset: int = 0, date_from: int | None = None, date_to: int | None = None,
               note: SearchNote | None = None) -> str:
    """One search; ``after_year`` is the year the search was limited to works published after (or None),
    ``date_from``..``date_to`` the publication years it was limited to (inclusive; None: open), ``offset`` the rank of
    its first hit (a later page), ``note`` what else it says."""
    sid, note = new_id("sch"), note or SearchNote()
    ps.apply(NewEvent(type="search.logged", entity_id=sid, actor_kind=actor_kind, actor=actor,
                      payload={"stage": stage, "attempt": attempt, "purpose": purpose, "source": source,
                               "query": query, "limit": limit, "after_year": after_year, "offset": offset,
                               "date_from": date_from, "date_to": date_to, "item_ids": list(item_ids),
                               "incomplete": note.incomplete, "stopped_at_cap": note.stopped_at_cap}))
    return sid


def add_cluster(ps: ProjectStore, attempt: int, cluster: Cluster, *, actor: str,
                actor_kind: ActorKind = "agent") -> str:
    cid = new_id("cls")
    ps.apply(NewEvent(type="cluster.added", entity_id=cid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "cluster": cluster.model_dump(mode="json")}))
    return cid


def remove_cluster(ps: ProjectStore, cluster_id: str, *, reason: str, actor: str,
                   actor_kind: ActorKind = "kernel") -> None:
    """Drop a cluster (P1 degrade); the event keeps it dropped when the projections are rebuilt."""
    row = require_row(ps, "clusters", cluster_id)
    ps.apply(NewEvent(type="cluster.removed", entity_id=cluster_id, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": row["attempt"], "reason": reason}))


def add_gap(ps: ProjectStore, attempt: int, gap: Gap, *, verified: bool, checks: dict, actor: str,
            actor_kind: ActorKind = "agent") -> str:
    gid = new_id("gap")
    ps.apply(NewEvent(type="gap.added", entity_id=gid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "gap": gap.model_dump(mode="json"), "verified": bool(verified),
                               "checks": checks}))
    return gid


def add_topic(ps: ProjectStore, attempt: int, topic: Topic, *, actor: str, actor_kind: ActorKind = "agent",
              novelty: dict | None = None) -> str:
    """``novelty`` is the V-NOVELTY reference of the topic (queries, sources, how many works the searches found), kept
    in the event for the gate and the review (``topic_novelty``)."""
    tid = new_id("tpc")
    payload: dict = {"attempt": attempt, "topic": topic.model_dump(mode="json")}
    if novelty is not None:
        payload["novelty"] = novelty
    ps.apply(NewEvent(type="topic.added", entity_id=tid, actor_kind=actor_kind, actor=actor, payload=payload))
    return tid


def set_topic_status(ps: ProjectStore, topic_id: str, status: TopicStatus, *, actor: str,
                     actor_kind: ActorKind) -> None:
    if status not in ("candidate", "approved", "rejected"):
        raise ValueError(f"unknown topic status {status!r}")
    require_row(ps, "topics", topic_id)
    ps.apply(NewEvent(type="topic.status_set", entity_id=topic_id, actor_kind=actor_kind, actor=actor,
                      payload={"status": status}))


def add_rq(ps: ProjectStore, attempt: int, topic_id: str, body: RQBody, hypotheses: list[HypothesisIn], *,
           actor: str, actor_kind: ActorKind = "agent") -> str:
    require_row(ps, "topics", topic_id)
    rid = new_id("rq")
    hyps = [{"id": new_id("hyp"), **h.model_dump(mode="json")} for h in hypotheses]
    ps.apply(NewEvent(type="rq.added", entity_id=rid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "topic_id": topic_id, "body": body.model_dump(mode="json"),
                               "hypotheses": hyps}))
    return rid


def set_already_done(ps: ProjectStore, rq_id: str, result: dict, *, actor: str,
                     actor_kind: ActorKind = "kernel") -> None:
    require_row(ps, "rqs", rq_id)
    ps.apply(NewEvent(type="rq.already_done", entity_id=rq_id, actor_kind=actor_kind, actor=actor,
                      payload={"result": result}))


def rq_text_hash(body: dict, hypotheses: list[dict]) -> str:
    """The frozen RQ's identity: sha256 of the canonical JSON of the body plus its hypotheses (statement and
    falsification, in order)."""
    hyps = [{"statement": h["statement"], "falsification": h["falsification"]} for h in hypotheses]
    return sha256_hex(canonical_json({**body, "hypotheses": hyps}))


def freeze_rq(ps: ProjectStore, rq_id: str, *, adopted_by: Literal["human", "agent"], actor: str) -> str:
    """Freeze the RQ and its hypotheses; returns the text hash. An RQ freezes once."""
    if adopted_by not in ("human", "agent"):
        raise ValueError(f"adopted_by must be 'human' or 'agent', not {adopted_by!r}")
    with ps.lock:
        r = require_row(ps, "rqs", rq_id)
        if r["status"] == "frozen":
            raise ValueError("rq already frozen")
        h = rq_text_hash(json.loads(r["body"]), hypotheses(ps, rq_id))
        ps.apply(NewEvent(type="rq.frozen", entity_id=rq_id, actor_kind=adopted_by, actor=actor,
                          payload={"text_hash": h, "adopted_by": adopted_by}))
    return h


def add_round(ps: ProjectStore, stage: str, feedback: str, *, actor: str, actor_kind: ActorKind = "human",
              checkpoint_id: str | None = None) -> str:
    """One exploration round: feedback on a stage's output that guides its next run. ``checkpoint_id`` names the
    checkpoint answer the feedback came from (``round_for_checkpoint``)."""
    xid = new_id("xpr")
    payload: dict = {"stage": stage, "feedback": feedback}
    if checkpoint_id is not None:
        payload["checkpoint_id"] = checkpoint_id
    with ps.lock:
        n = ps.db.execute("SELECT COALESCE(MAX(n), 0) FROM exploration_rounds").fetchone()[0] + 1
        ps.apply(NewEvent(type="exploration.round", entity_id=xid, actor_kind=actor_kind, actor=actor,
                          payload={"n": n, **payload}))
    return xid


def round_for_checkpoint(ps: ProjectStore, checkpoint_id: str) -> str | None:
    """The id of the exploration round recorded from the checkpoint's answer, if any."""
    r = ps.db.execute("SELECT entity_id FROM events WHERE type='exploration.round' AND "
                      "json_extract(payload, '$.checkpoint_id')=? ORDER BY seq LIMIT 1", (checkpoint_id,)).fetchone()
    return r["entity_id"] if r else None


# ---- queries ----
def searches(ps: ProjectStore, stage: str, attempt: int, *, purpose: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM search_log WHERE stage=? AND attempt=?", (stage, attempt)
    if purpose is not None:
        sql, params = sql + " AND purpose=?", (stage, attempt, purpose)
    rows = query_rows(ps, sql + " ORDER BY rowid", params, ("item_ids",))
    for r in rows:
        r["stopped_at_cap"] = bool(r["stopped_at_cap"])
    return rows


def clusters(ps: ProjectStore, attempt: int) -> list[dict]:
    rows = query_rows(ps, "SELECT * FROM clusters WHERE attempt=? ORDER BY rowid", (attempt,),
                      ("item_ids", "representative_ids", "year_counts"))
    for d in rows:
        d["year_counts"] = {int(y): n for y, n in d["year_counts"].items()}
    return rows


def gaps(ps: ProjectStore, attempt: int, verified: bool | None = None) -> list[dict]:
    sql, params = "SELECT * FROM gaps WHERE attempt=?", (attempt,)
    if verified is not None:
        sql, params = sql + " AND verified=?", (attempt, int(verified))
    rows = query_rows(ps, sql + " ORDER BY rowid", params, ("evidence", "details", "checks"))
    for d in rows:
        d["verified"] = bool(d["verified"])
    return rows


def topics(ps: ProjectStore, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM topics WHERE attempt=? ORDER BY rowid", (attempt,), ("gap_ids", "closest"))


def topic_novelty(ps: ProjectStore, topic_id: str) -> dict | None:
    """The V-NOVELTY reference stored with the topic (``add_topic(novelty=...)``), or ``None``."""
    r = ps.db.execute("SELECT json_extract(payload, '$.novelty') AS n FROM events WHERE type='topic.added' AND "
                      "entity_id=? ORDER BY seq DESC LIMIT 1", (topic_id,)).fetchone()
    return json.loads(r["n"]) if r and r["n"] else None


def rqs(ps: ProjectStore, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM rqs WHERE attempt=? ORDER BY rowid", (attempt,), ("body", "already_done"))


def rq_row(ps: ProjectStore, rq_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM rqs WHERE id=?", (rq_id,), ("body", "already_done"))
    return rows[0] if rows else None


def frozen_rq(ps: ProjectStore) -> dict | None:
    """The frozen research question (the latest frozen, should there be several), else ``None``."""
    rows = query_rows(ps, "SELECT * FROM rqs WHERE status='frozen' ORDER BY rowid DESC LIMIT 1", (),
                      ("body", "already_done"))
    return rows[0] if rows else None


def hypotheses(ps: ProjectStore, rq_id: str) -> list[dict]:
    return query_rows(ps, "SELECT * FROM hypotheses WHERE rq_id=? ORDER BY rowid", (rq_id,))


def rounds(ps: ProjectStore) -> list[dict]:
    return query_rows(ps, "SELECT * FROM exploration_rounds ORDER BY n", ())


def guidance(ps: ProjectStore, stage: str) -> str:
    """The feedback of every exploration round, whatever stage it was given on, newest first, one per line.
    ``stage`` is the stage about to run; feedback on one stage guides later runs of every stage."""
    return "\n".join(r["feedback"] for r in reversed(rounds(ps)))


# ---- projections ----
@projection("project.intake")
def _intake(conn: sqlite3.Connection, ev: Event) -> None:
    for k, v in ev.payload.items():
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (k, str(v)))


@projection("search.logged")
def _search(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO search_log(id, stage, attempt, purpose, source, query, limit_n, item_ids, "
                 "ts, after_year, offset_n, date_from, date_to, incomplete, stopped_at_cap) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["stage"], p["attempt"], p["purpose"], p["source"], p["query"], p["limit"],
                  json.dumps(p["item_ids"]), ev.ts, p.get("after_year"), p.get("offset", 0), p.get("date_from"),
                  p.get("date_to"), p.get("incomplete"), int(bool(p.get("stopped_at_cap")))))


@projection("cluster.added")
def _cluster(conn: sqlite3.Connection, ev: Event) -> None:
    c = ev.payload["cluster"]
    conn.execute("INSERT OR REPLACE INTO clusters(id, attempt, label, description, query, item_ids, "
                 "representative_ids, year_counts) VALUES (?,?,?,?,?,?,?,?)",
                 (ev.entity_id, ev.payload["attempt"], c["label"], c["description"], c["query"],
                  json.dumps(c["item_ids"]), json.dumps(c["representative_ids"]), json.dumps(c["year_counts"])))


@projection("cluster.removed")
def _cluster_removed(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("DELETE FROM clusters WHERE id=?", (ev.entity_id,))


@projection("gap.added")
def _gap(conn: sqlite3.Connection, ev: Event) -> None:
    p, g = ev.payload, ev.payload["gap"]
    conn.execute("INSERT OR REPLACE INTO gaps(id, attempt, kind, statement, cluster_id, evidence, details, verified, "
                 "checks, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["attempt"], g["kind"], g["statement"], g["cluster_id"], json.dumps(g["evidence"]),
                  json.dumps(g["details"]), int(p["verified"]), json.dumps(p["checks"]), ev.ts))


@projection("topic.added")
def _topic(conn: sqlite3.Connection, ev: Event) -> None:
    t = ev.payload["topic"]
    conn.execute("INSERT OR REPLACE INTO topics(id, attempt, title, summary, gap_ids, closest, feasibility, "
                 "data_needed, paper_type, risks, status, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, ev.payload["attempt"], t["title"], t["summary"], json.dumps(t["gap_ids"]),
                  json.dumps(t["closest"]), t["feasibility"], t["data_needed"], t["paper_type"], t["risks"],
                  "candidate", ev.ts))


@projection("topic.status_set")
def _topic_status(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE topics SET status=? WHERE id=?", (ev.payload["status"], ev.entity_id))


@projection("rq.added")
def _rq(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO rqs(id, attempt, topic_id, body, already_done, status, text_hash, "
                 "adopted_by, created) VALUES (?,?,?,?,NULL,'draft',NULL,NULL,?)",
                 (ev.entity_id, p["attempt"], p["topic_id"], json.dumps(p["body"]), ev.ts))
    for h in p["hypotheses"]:
        conn.execute("INSERT OR REPLACE INTO hypotheses(id, rq_id, statement, falsification) VALUES (?,?,?,?)",
                     (h["id"], ev.entity_id, h["statement"], h["falsification"]))


@projection("rq.already_done")
def _rq_already_done(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE rqs SET already_done=? WHERE id=?", (json.dumps(ev.payload["result"]), ev.entity_id))


@projection("rq.frozen")
def _rq_frozen(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("UPDATE rqs SET status='frozen', text_hash=?, adopted_by=? WHERE id=?",
                 (ev.payload["text_hash"], ev.payload["adopted_by"], ev.entity_id))


@projection("exploration.round")
def _round(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO exploration_rounds(id, n, stage, feedback, created) VALUES (?,?,?,?,?)",
                 (ev.entity_id, p["n"], p["stage"], p["feedback"], ev.ts))
