"""Event-sourced discovery artifacts: the intake, search log, clusters, gaps, topics, research questions,
hypotheses and exploration rounds. Every write is one event; the tables below are projections of them."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

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
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection

ActorKind = Literal["human", "agent", "kernel"]


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
def log_search(ps: ProjectStore, *, stage: str, attempt: int, purpose: str, source: str, query: str, limit: int,
               item_ids: list[str], actor: str, actor_kind: ActorKind = "kernel") -> str:
    sid = new_id("sch")
    ps.apply(NewEvent(type="search.logged", entity_id=sid, actor_kind=actor_kind, actor=actor,
                      payload={"stage": stage, "attempt": attempt, "purpose": purpose, "source": source,
                               "query": query, "limit": limit, "item_ids": list(item_ids)}))
    return sid


def add_cluster(ps: ProjectStore, attempt: int, cluster: Cluster, *, actor: str,
                actor_kind: ActorKind = "agent") -> str:
    cid = new_id("cls")
    ps.apply(NewEvent(type="cluster.added", entity_id=cid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "cluster": cluster.model_dump(mode="json")}))
    return cid


def add_gap(ps: ProjectStore, attempt: int, gap: Gap, *, verified: bool, checks: dict, actor: str,
            actor_kind: ActorKind = "agent") -> str:
    gid = new_id("gap")
    ps.apply(NewEvent(type="gap.added", entity_id=gid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "gap": gap.model_dump(mode="json"), "verified": bool(verified),
                               "checks": checks}))
    return gid


def add_topic(ps: ProjectStore, attempt: int, topic: Topic, *, actor: str, actor_kind: ActorKind = "agent") -> str:
    tid = new_id("tpc")
    ps.apply(NewEvent(type="topic.added", entity_id=tid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "topic": topic.model_dump(mode="json")}))
    return tid


def set_topic_status(ps: ProjectStore, topic_id: str, status: TopicStatus, *, actor: str,
                     actor_kind: ActorKind) -> None:
    if status not in ("candidate", "approved", "rejected"):
        raise ValueError(f"unknown topic status {status!r}")
    _require(ps, "topics", topic_id)
    ps.apply(NewEvent(type="topic.status_set", entity_id=topic_id, actor_kind=actor_kind, actor=actor,
                      payload={"status": status}))


def add_rq(ps: ProjectStore, attempt: int, topic_id: str, body: RQBody, hypotheses: list[HypothesisIn], *,
           actor: str, actor_kind: ActorKind = "agent") -> str:
    _require(ps, "topics", topic_id)
    rid = new_id("rq")
    hyps = [{"id": new_id("hyp"), **h.model_dump(mode="json")} for h in hypotheses]
    ps.apply(NewEvent(type="rq.added", entity_id=rid, actor_kind=actor_kind, actor=actor,
                      payload={"attempt": attempt, "topic_id": topic_id, "body": body.model_dump(mode="json"),
                               "hypotheses": hyps}))
    return rid


def set_already_done(ps: ProjectStore, rq_id: str, result: dict, *, actor: str,
                     actor_kind: ActorKind = "kernel") -> None:
    _require(ps, "rqs", rq_id)
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
        r = _require(ps, "rqs", rq_id)
        if r["status"] == "frozen":
            raise ValueError("rq already frozen")
        h = rq_text_hash(json.loads(r["body"]), hypotheses(ps, rq_id))
        ps.apply(NewEvent(type="rq.frozen", entity_id=rq_id, actor_kind=adopted_by, actor=actor,
                          payload={"text_hash": h, "adopted_by": adopted_by}))
    return h


def add_round(ps: ProjectStore, stage: str, feedback: str, *, actor: str, actor_kind: ActorKind = "human") -> str:
    """One exploration round: feedback on a stage's output that guides its next run."""
    xid = new_id("xpr")
    with ps.lock:
        n = ps.db.execute("SELECT COALESCE(MAX(n), 0) FROM exploration_rounds").fetchone()[0] + 1
        ps.apply(NewEvent(type="exploration.round", entity_id=xid, actor_kind=actor_kind, actor=actor,
                          payload={"n": n, "stage": stage, "feedback": feedback}))
    return xid


def _require(ps: ProjectStore, table: str, entity_id: str) -> sqlite3.Row:
    r = ps.db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    if r is None:
        raise KeyError(f"no {table[:-1]} {entity_id}")
    return r


# ---- queries ----
def _rows(ps: ProjectStore, sql: str, params: tuple, json_cols: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for r in ps.db.execute(sql, params):
        d = dict(r)
        for c in json_cols:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        out.append(d)
    return out


def searches(ps: ProjectStore, stage: str, attempt: int) -> list[dict]:
    return _rows(ps, "SELECT * FROM search_log WHERE stage=? AND attempt=? ORDER BY rowid", (stage, attempt),
                 ("item_ids",))


def clusters(ps: ProjectStore, attempt: int) -> list[dict]:
    rows = _rows(ps, "SELECT * FROM clusters WHERE attempt=? ORDER BY rowid", (attempt,),
                 ("item_ids", "representative_ids", "year_counts"))
    for d in rows:
        d["year_counts"] = {int(y): n for y, n in d["year_counts"].items()}
    return rows


def gaps(ps: ProjectStore, attempt: int, verified: bool | None = None) -> list[dict]:
    sql, params = "SELECT * FROM gaps WHERE attempt=?", (attempt,)
    if verified is not None:
        sql, params = sql + " AND verified=?", (attempt, int(verified))
    rows = _rows(ps, sql + " ORDER BY rowid", params, ("evidence", "details", "checks"))
    for d in rows:
        d["verified"] = bool(d["verified"])
    return rows


def topics(ps: ProjectStore, attempt: int) -> list[dict]:
    return _rows(ps, "SELECT * FROM topics WHERE attempt=? ORDER BY rowid", (attempt,), ("gap_ids", "closest"))


def rqs(ps: ProjectStore, attempt: int) -> list[dict]:
    return _rows(ps, "SELECT * FROM rqs WHERE attempt=? ORDER BY rowid", (attempt,), ("body", "already_done"))


def hypotheses(ps: ProjectStore, rq_id: str) -> list[dict]:
    return _rows(ps, "SELECT * FROM hypotheses WHERE rq_id=? ORDER BY rowid", (rq_id,))


def rounds(ps: ProjectStore) -> list[dict]:
    return _rows(ps, "SELECT * FROM exploration_rounds ORDER BY n", ())


def guidance(ps: ProjectStore, stage: str) -> str:
    """The feedback of every exploration round on this stage, newest first, one per line."""
    return "\n".join(r["feedback"] for r in reversed(rounds(ps)) if r["stage"] == stage)


# ---- projections ----
@projection("project.intake")
def _intake(conn: sqlite3.Connection, ev: Event) -> None:
    for k, v in ev.payload.items():
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (k, str(v)))


@projection("search.logged")
def _search(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO search_log(id, stage, attempt, purpose, source, query, limit_n, item_ids, "
                 "ts) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["stage"], p["attempt"], p["purpose"], p["source"], p["query"], p["limit"],
                  json.dumps(p["item_ids"]), ev.ts))


@projection("cluster.added")
def _cluster(conn: sqlite3.Connection, ev: Event) -> None:
    c = ev.payload["cluster"]
    conn.execute("INSERT OR REPLACE INTO clusters(id, attempt, label, description, query, item_ids, "
                 "representative_ids, year_counts) VALUES (?,?,?,?,?,?,?,?)",
                 (ev.entity_id, ev.payload["attempt"], c["label"], c["description"], c["query"],
                  json.dumps(c["item_ids"]), json.dumps(c["representative_ids"]), json.dumps(c["year_counts"])))


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
