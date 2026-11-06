"""Claims, evidence, argument edges and objections (spec §6.3). Event-sourced like everything else.

A claim's ``status`` only changes through ``set_claim_status``, which V-SUPPORT calls; no tool can mark a claim
supported."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from rk.ids import new_id
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.verify.hedge import hedge_level

ClaimKind = Literal["fact", "definition", "cause", "evaluation", "policy"]
ClaimStatus = Literal["draft", "supported", "uncertain", "unsupported", "contradicted", "withdrawn"]
# Which part of the kernel wrote a claim: the argument (the default), or gap mining, whose claims are evidence checks
# of a candidate gap (a quoted finding checked against another source) and stay out of the argument's listings.
ClaimOrigin = Literal["argument", "gaps"]
EdgeType = Literal["reason_of", "evidence_of", "warrant_of", "objection_to", "response_to"]
ObjectionCategory = Literal["problem", "soundness", "alternative", "other_cause", "counterexample", "definition"]
CRITERIA = ("accurate", "precise", "sufficient", "representative", "authoritative", "clear", "relevant")

# Which entity prefixes an edge may connect (from, to).
_EDGE_ENDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "reason_of": (("clm",), ("clm",)),
    "evidence_of": (("evd",), ("clm",)),
    "warrant_of": (("clm",), ("edg",)),
    "objection_to": (("obj",), ("clm", "edg", "evd")),
    "response_to": (("clm",), ("obj",)),
}


class Criterion(BaseModel):
    score: int = Field(ge=0, le=2)
    reason: str


class ClaimIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: ClaimKind = "fact"
    scope_limits: list[str] = []
    contested: bool = False


class EvidenceIn(BaseModel):
    claim_id: str
    note_ids: list[str] = Field(min_length=1)
    criteria: dict[str, Criterion] = {}
    interpretation: str = ""

    @model_validator(mode="after")
    def _known_criteria(self) -> EvidenceIn:
        bad = set(self.criteria) - set(CRITERIA)
        if bad:
            raise ValueError(f"unknown criteria {sorted(bad)}; allowed: {', '.join(CRITERIA)}")
        return self


class EdgeIn(BaseModel):
    from_id: str
    to_id: str
    type: EdgeType
    warrant: dict | None = None


class ObjectionIn(BaseModel):
    target_id: str
    category: ObjectionCategory
    text: str = Field(min_length=1, max_length=4000)
    source_id: str | None = None
    priority: int = Field(1, ge=1, le=3)


class ArgumentService:
    def __init__(self, ps: ProjectStore):
        self.ps = ps

    # ---- reads ----
    def _row(self, table: str, id_: str) -> dict | None:
        r = self.ps.db.execute(f"SELECT data FROM {table} WHERE id=?", (id_,)).fetchone()
        return json.loads(r["data"]) if r else None

    def exists(self, id_: str) -> bool:
        table = {"clm": "claims", "evd": "evidence", "edg": "edges", "obj": "objections"}.get(id_.split("_")[0])
        return table is not None and self._row(table, id_) is not None

    def note(self, note_id: str) -> dict | None:
        r = self.ps.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone()
        return json.loads(r["data"]) if r else None

    def get_claim(self, claim_id: str) -> dict:
        c = self._row("claims", claim_id)
        if c is None:
            raise KeyError(claim_id)
        ev = [json.loads(r["data"]) for r in self.ps.db.execute(
            "SELECT data FROM evidence WHERE claim_id=? ORDER BY id", (claim_id,))]
        for e in ev:
            e["notes"] = [self.note(n) for n in e["note_ids"]]
        c["evidence"] = ev
        c["objections"] = [json.loads(r["data"]) for r in self.ps.db.execute(
            "SELECT data FROM objections WHERE target_id=? ORDER BY id", (claim_id,))]
        return c

    def list_claims(self, status: str | None = None, origin: ClaimOrigin = "argument") -> list[dict]:
        """Claims of one origin (claims written before origins existed are argument claims), optionally by status."""
        q = "SELECT data FROM claims WHERE COALESCE(json_extract(data, '$.origin'), 'argument')=?"
        params: tuple = (origin,)
        if status:
            q, params = q + " AND status=?", (origin, status)
        return [json.loads(r["data"]) for r in self.ps.db.execute(q + " ORDER BY id", params)]

    def generator_vendor(self, task_id: str | None, role: str) -> str | None:
        """Vendor of the model that wrote a claim: the task's driver vendor, ``human`` for the CLI, else unknown."""
        if role == "cli":
            return "human"
        if not task_id:
            return None
        r = self.ps.db.execute("SELECT vendor FROM tasks WHERE id=?", (task_id,)).fetchone()
        return r["vendor"] if r else None

    # ---- writes ----
    def _ev(self, type_: str, entity: str, payload: dict, actor: str, kind: str) -> None:
        self.ps.apply(NewEvent(type=type_, entity_id=entity, actor_kind=kind, actor=actor, payload=payload))

    def add_claim(self, c: ClaimIn, *, generator_vendor: str | None, actor: str, actor_kind: str = "agent",
                  origin: ClaimOrigin = "argument") -> str:
        cid = new_id("clm")
        data = {**c.model_dump(), "id": cid, "hedge_level": hedge_level(c.text), "status": "draft",
                "generator_vendor": generator_vendor, "origin": origin}
        self._ev("claim.added", cid, {"claim": data}, actor, actor_kind)
        return cid

    def link_evidence(self, e: EvidenceIn, *, actor: str, actor_kind: str = "agent") -> str:
        if self._row("claims", e.claim_id) is None:
            raise KeyError(e.claim_id)
        missing = [n for n in e.note_ids if self.note(n) is None]
        if missing:
            raise ValueError(f"unknown notes: {missing}")
        eid = new_id("evd")
        data = {**e.model_dump(), "id": eid}
        self._ev("evidence.linked", eid, {"evidence": data}, actor, actor_kind)
        return eid

    def add_edge(self, e: EdgeIn, *, actor: str, actor_kind: str = "agent") -> str:
        froms, tos = _EDGE_ENDS[e.type]
        if e.from_id.split("_")[0] not in froms or e.to_id.split("_")[0] not in tos:
            raise ValueError(f"{e.type} must go from {'/'.join(froms)} to {'/'.join(tos)}")
        for end in (e.from_id, e.to_id):
            if not self.exists(end):
                raise KeyError(end)
        gid = new_id("edg")
        self._ev("edge.added", gid, {"edge": {**e.model_dump(), "id": gid}}, actor, actor_kind)
        return gid

    def add_objection(self, o: ObjectionIn, *, actor: str, actor_kind: str = "agent") -> str:
        if not self.exists(o.target_id):
            raise KeyError(o.target_id)
        oid = new_id("obj")
        data = {**o.model_dump(), "id": oid, "status": "open", "response_claim_id": None}
        self._ev("objection.added", oid, {"objection": data}, actor, actor_kind)
        return oid

    def set_claim_status(self, claim_id: str, status: ClaimStatus, *, verification_id: str | None,
                         actor: str = "kernel") -> None:
        if self._row("claims", claim_id) is None:
            raise KeyError(claim_id)
        self._ev("claim.status_set", claim_id, {"status": status, "verification_id": verification_id}, actor,
                 "kernel")


# ---- projections ----
@projection("claim.added")
def _claim_added(conn: sqlite3.Connection, ev: Event) -> None:
    c = ev.payload["claim"]
    conn.execute("INSERT OR REPLACE INTO claims(id, status, data) VALUES (?,?,?)", (c["id"], c["status"], json.dumps(c)))


@projection("claim.status_set")
def _claim_status(conn: sqlite3.Connection, ev: Event) -> None:
    r = conn.execute("SELECT data FROM claims WHERE id=?", (ev.entity_id,)).fetchone()
    c = json.loads(r[0])
    c["status"] = ev.payload["status"]
    c["verification_id"] = ev.payload["verification_id"]
    conn.execute("UPDATE claims SET status=?, data=? WHERE id=?", (c["status"], json.dumps(c), ev.entity_id))


@projection("evidence.linked")
def _evidence(conn: sqlite3.Connection, ev: Event) -> None:
    e = ev.payload["evidence"]
    conn.execute("INSERT OR REPLACE INTO evidence(id, claim_id, data) VALUES (?,?,?)",
                 (e["id"], e["claim_id"], json.dumps(e)))


@projection("edge.added")
def _edge(conn: sqlite3.Connection, ev: Event) -> None:
    e = ev.payload["edge"]
    conn.execute("INSERT OR REPLACE INTO edges(id, from_id, to_id, data) VALUES (?,?,?,?)",
                 (e["id"], e["from_id"], e["to_id"], json.dumps(e)))


@projection("objection.added")
def _objection(conn: sqlite3.Connection, ev: Event) -> None:
    o = ev.payload["objection"]
    conn.execute("INSERT OR REPLACE INTO objections(id, target_id, data) VALUES (?,?,?)",
                 (o["id"], o["target_id"], json.dumps(o)))
