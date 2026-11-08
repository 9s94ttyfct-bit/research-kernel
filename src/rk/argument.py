"""Claims, evidence, argument edges and objections (spec §6.3). Event-sourced like everything else.

A claim's ``status`` only changes through ``set_claim_status``, which V-SUPPORT calls, and ``withdraw_claim``; no tool
can mark a claim supported."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rk.ids import new_id
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.verify.hedge import hedge_level

# What a role may claim; a ``stat_result`` (a number computed by kernel code, reported from its stt_ artifact) only the
# kernel writes.
AgentClaimKind = Literal["fact", "definition", "cause", "evaluation", "policy", "feasibility"]
ClaimKind = Literal[AgentClaimKind, "stat_result"]
ClaimStatus = Literal["draft", "supported", "uncertain", "unsupported", "contradicted", "withdrawn"]
# Which part of the kernel wrote a claim: the argument (the default); gap mining, whose claims are evidence checks of a
# candidate gap (a quoted finding checked against another source) and stay out of the argument's listings; or the
# synthesis, whose findings the argue stage builds on.
ClaimOrigin = Literal["argument", "gaps", "synthesis"]
EdgeType = Literal["reason_of", "evidence_of", "warrant_of", "objection_to", "response_to"]
ObjectionCategory = Literal["problem", "soundness", "alternative", "other_cause", "counterexample", "definition"]
# How an objection was answered: by a response claim, by conceding it (the claim was narrowed or withdrawn), or by
# rejecting it as invalid (the note says why).
ObjectionResponse = Literal["responded", "conceded", "rejected_invalid"]
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


class ClaimDraft(BaseModel):
    """A claim as a role writes it (``rk_claim_add``): no statistical kind and no artifact links, which only the
    kernel writes (``ClaimIn``)."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    kind: AgentClaimKind = "fact"
    scope_limits: list[str] = []
    contested: bool = False
    narrowed_from: str | None = None  # the claim this one rewrites narrower and weaker (P1, §5.3)


class NumberBinding(BaseModel):
    """A number a kernel template printed in a claim (``text``, as written) and the artifact ``display`` value it
    prints (``rk.verify.stats.stat_claim_text``)."""
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    key: str
    text: str


class ClaimIn(ClaimDraft):
    kind: ClaimKind = "fact"
    artifact_ids: list[str] = []  # the stt_/fig_ artifacts the claim reports (a stat_result's numbers)
    number_bindings: list[NumberBinding] = []  # per printed number, in order (a templated stat_result)


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


class Warrant(BaseModel):
    """Why the reason or evidence supports the claim: in this circumstance, this consequence follows, by this kind
    of rule."""
    circumstance: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    type: Literal["causal", "conditional", "generalization", "analogy", "authority", "definition"]


class WarrantTest(BaseModel):
    ok: bool
    reason: str


class WarrantTests(BaseModel):
    """The Skeptic's verdict on a warrant: is it true, limited as it should be, do the circumstance and the consequence
    fit the case, and is it accepted in the field."""
    true: WarrantTest
    limited: WarrantTest
    circumstance_fits: WarrantTest
    consequence_fits: WarrantTest
    accepted: WarrantTest


class EdgeIn(BaseModel):
    from_id: str
    to_id: str
    type: EdgeType
    warrant: Warrant | None = None  # a warrant_of edge's warrant; the other edges have none

    @model_validator(mode="after")
    def _warrant_on_warrant_edges(self) -> EdgeIn:
        if self.type == "warrant_of" and self.warrant is None:
            raise ValueError("a warrant_of edge needs its warrant {circumstance, consequence, type}")
        if self.type != "warrant_of" and self.warrant is not None:
            raise ValueError("only a warrant_of edge has a warrant")
        return self


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

    def list_claims(self, status: str | None = None,
                    origin: ClaimOrigin | tuple[ClaimOrigin, ...] = "argument") -> list[dict]:
        """Claims of one origin or several (claims written before origins existed are argument claims), optionally by
        status."""
        origins = (origin,) if isinstance(origin, str) else tuple(origin)
        q = (f"SELECT data FROM claims WHERE COALESCE(json_extract(data, '$.origin'), 'argument') "
             f"IN ({','.join('?' * len(origins))})")
        params: tuple = origins
        if status:
            q, params = q + " AND status=?", (*origins, status)
        return [json.loads(r["data"]) for r in self.ps.db.execute(q + " ORDER BY id", params)]

    def list_edges(self, from_id: str | None = None, to_id: str | None = None,
                   type: EdgeType | None = None) -> list[dict]:
        """Edges, optionally only those from ``from_id``, to ``to_id`` and of ``type``."""
        q, params = "SELECT data FROM edges WHERE 1=1", ()
        for cond, value in (("from_id=?", from_id), ("to_id=?", to_id), ("json_extract(data, '$.type')=?", type)):
            if value is not None:
                q, params = f"{q} AND {cond}", (*params, value)
        return [json.loads(r["data"]) for r in self.ps.db.execute(q + " ORDER BY id", params)]

    def list_objections(self, target_id: str | None = None, status: str | None = None) -> list[dict]:
        """Objections, optionally only those to ``target_id`` and with ``status`` (``open`` or an answer)."""
        q, params = "SELECT data FROM objections WHERE 1=1", ()
        for cond, value in (("target_id=?", target_id), ("json_extract(data, '$.status')=?", status)):
            if value is not None:
                q, params = f"{q} AND {cond}", (*params, value)
        return [json.loads(r["data"]) for r in self.ps.db.execute(q + " ORDER BY id", params)]

    def list_evidence(self, claim_id: str) -> list[dict]:
        """The evidence linked to the claim."""
        return [json.loads(r["data"]) for r in self.ps.db.execute(
            "SELECT data FROM evidence WHERE claim_id=? ORDER BY id", (claim_id,))]

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
        """A draft claim. The claim it narrows (``narrowed_from``) and the artifacts it reports must exist; only the
        kernel writes a ``stat_result``, links artifacts or binds numbers; a ``stat_result`` reports at least one
        artifact and binds its numbers (``rk.verify.stats.stat_claim_text``); a number binding names one of the
        claim's artifacts."""
        if actor_kind != "kernel" and (c.kind == "stat_result" or c.artifact_ids or c.number_bindings):
            raise ValueError("only the kernel writes a stat_result claim, links a claim to artifacts or binds its "
                             "numbers")
        if c.kind == "stat_result" and not c.artifact_ids:
            raise ValueError("a stat_result claim reports at least one artifact (artifact_ids)")
        if c.kind == "stat_result" and not c.number_bindings:
            raise ValueError("a stat_result claim binds each number it prints to its artifact value "
                             "(number_bindings, from rk.verify.stats.stat_claim_text)")
        unlinked = sorted({b.artifact_id for b in c.number_bindings} - set(c.artifact_ids))
        if unlinked:
            raise ValueError(f"number bindings name artifacts the claim does not report: {unlinked}")
        if c.narrowed_from is not None and self._row("claims", c.narrowed_from) is None:
            raise KeyError(c.narrowed_from)
        for a in c.artifact_ids:
            if self.ps.db.execute("SELECT 1 FROM artifacts WHERE id=?", (a,)).fetchone() is None:
                raise KeyError(a)
        cid = new_id("clm")
        data = {**c.model_dump(), "id": cid, "hedge_level": hedge_level(c.text), "status": "draft",
                "generator_vendor": generator_vendor, "origin": origin}
        self._ev("claim.added", cid, {"claim": data}, actor, actor_kind)
        return cid

    def withdraw_claim(self, claim_id: str, *, reason: str, actor: str, actor_kind: str = "kernel") -> None:
        """Delete the claim from the argument (P1's rule for an uncertain claim, §5.3): its status becomes
        ``withdrawn`` and the reason is kept for the limitations. A claim is withdrawn once."""
        with self.ps.lock:
            c = self._row("claims", claim_id)
            if c is None:
                raise KeyError(claim_id)
            if c["status"] == "withdrawn":
                raise ValueError(f"claim {claim_id} is already withdrawn")
            self._ev("claim.withdrawn", claim_id, {"reason": reason}, actor, actor_kind)

    def set_contested(self, claim_id: str, *, reason: str, actor: str, actor_kind: str = "kernel") -> None:
        """Mark the claim ``contested`` (two supported findings contradict each other and neither is preferred,
        spec §9.1 V-BALANCE): it stays in the argument with its status, carrying an objection and a response. A
        withdrawn claim is refused; marking a contested claim again records nothing."""
        with self.ps.lock:
            c = self._row("claims", claim_id)
            if c is None:
                raise KeyError(claim_id)
            if c["status"] == "withdrawn":
                raise ValueError(f"claim {claim_id} is withdrawn: it is out of the argument")
            if c.get("contested"):
                return
            self._ev("claim.contested", claim_id, {"reason": reason}, actor, actor_kind)

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
        data = {**o.model_dump(), "id": oid, "status": "open", "response_claim_id": None, "note": None}
        self._ev("objection.added", oid, {"objection": data}, actor, actor_kind)
        return oid

    def respond_objection(self, obj_id: str, *, response_claim_id: str | None, status: ObjectionResponse, note: str,
                          actor: str, actor_kind: str = "agent") -> str | None:
        """Answer an objection: ``responded`` with the response claim, ``conceded`` or ``rejected_invalid`` (the note
        says why). A responded answer also records the claim's ``response_to`` edge, in the same event (an edge the
        claim already has to the objection is kept). Returns that edge's id (``None`` without a response claim).

        An open objection is answered; a responded one again while its response claim is not supported (uncertain,
        withdrawn and narrowed, not yet verified): the latest answer wins, the events keep every answer. Conceded and
        rejected answers are final, as is an answer whose response claim is supported."""
        if status not in get_args(ObjectionResponse):
            raise ValueError(f"unknown objection response {status!r}; expected one of {get_args(ObjectionResponse)}")
        if status == "responded" and response_claim_id is None:
            raise ValueError("a responded objection needs the response claim (response_claim_id)")
        if status != "responded" and response_claim_id is not None:
            raise ValueError(f"only a responded objection names a response claim, not a {status} one")
        with self.ps.lock:
            o = self._row("objections", obj_id)
            if o is None:
                raise KeyError(obj_id)
            if o["status"] in ("conceded", "rejected_invalid"):
                raise ValueError(f"objection {obj_id} is already answered ({o['status']}); that answer is final")
            if o["status"] == "responded":
                current = self._row("claims", o["response_claim_id"])
                if current is not None and current["status"] == "supported":
                    raise ValueError(f"objection {obj_id} is answered and its response {current['id']} is supported")
            if response_claim_id is not None:
                r = self._row("claims", response_claim_id)
                if r is None:
                    raise KeyError(response_claim_id)
                if r["status"] == "withdrawn":
                    raise ValueError(f"the response claim {response_claim_id} is withdrawn")
            edge, edge_id = None, None
            if response_claim_id is not None:
                had = self.list_edges(from_id=response_claim_id, to_id=obj_id, type="response_to")
                if had:
                    edge_id = had[0]["id"]
                else:
                    edge_id = new_id("edg")
                    edge = {**EdgeIn(from_id=response_claim_id, to_id=obj_id, type="response_to").model_dump(),
                            "id": edge_id}
            self._ev("objection.responded", obj_id, {"status": status, "response_claim_id": response_claim_id,
                                                     "note": note, "edge": edge}, actor, actor_kind)
        return edge_id

    def record_warrant_tests(self, edge_id: str, tests: WarrantTests, *, actor: str,
                             actor_kind: str = "agent") -> None:
        """Store the Skeptic's tests of a warrant_of edge's warrant (a later verdict replaces an earlier one)."""
        e = self._row("edges", edge_id)
        if e is None:
            raise KeyError(edge_id)
        if e["type"] != "warrant_of":
            raise ValueError(f"{edge_id} is not a warrant_of edge (it is {e['type']})")
        self._ev("warrant.tested", edge_id, {"tests": tests.model_dump(mode="json")}, actor, actor_kind)

    def set_claim_status(self, claim_id: str, status: ClaimStatus, *, verification_id: str | None,
                         actor: str = "kernel", actor_kind: Literal["kernel", "human"] = "kernel") -> None:
        """V-SUPPORT's verdict (``actor_kind="kernel"``), or the researcher's on an ``uncertain_support`` checkpoint
        (``"human"``, attributed in the status event). A withdrawn claim stays withdrawn; withdrawing goes through
        ``withdraw_claim``."""
        c = self._row("claims", claim_id)
        if c is None:
            raise KeyError(claim_id)
        if c["status"] == "withdrawn":
            raise ValueError(f"claim {claim_id} is withdrawn: its status is final")
        if status == "withdrawn":
            raise ValueError("withdraw a claim with withdraw_claim (it keeps the reason)")
        self._ev("claim.status_set", claim_id, {"status": status, "verification_id": verification_id}, actor,
                 actor_kind)


# ---- projections ----
@projection("claim.added")
def _claim_added(conn: sqlite3.Connection, ev: Event) -> None:
    c = ev.payload["claim"]
    conn.execute("INSERT OR REPLACE INTO claims(id, status, data) VALUES (?,?,?)", (c["id"], c["status"], json.dumps(c)))


def _update(conn: sqlite3.Connection, table: str, entity_id: str, **fields) -> dict:
    """Merge ``fields`` into the row's JSON ``data`` and store it; returns the merged data."""
    r = conn.execute(f"SELECT data FROM {table} WHERE id=?", (entity_id,)).fetchone()
    d = {**json.loads(r[0]), **fields}
    conn.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(d), entity_id))
    return d


@projection("claim.status_set")
def _claim_status(conn: sqlite3.Connection, ev: Event) -> None:
    c = _update(conn, "claims", ev.entity_id, status=ev.payload["status"],
                verification_id=ev.payload["verification_id"])
    conn.execute("UPDATE claims SET status=? WHERE id=?", (c["status"], ev.entity_id))


@projection("claim.withdrawn")
def _claim_withdrawn(conn: sqlite3.Connection, ev: Event) -> None:
    _update(conn, "claims", ev.entity_id, status="withdrawn", withdrawn_reason=ev.payload["reason"])
    conn.execute("UPDATE claims SET status='withdrawn' WHERE id=?", (ev.entity_id,))


@projection("claim.contested")
def _claim_contested(conn: sqlite3.Connection, ev: Event) -> None:
    _update(conn, "claims", ev.entity_id, contested=True, contested_reason=ev.payload["reason"])


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


@projection("objection.responded")
def _objection_responded(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    _update(conn, "objections", ev.entity_id, status=p["status"], response_claim_id=p["response_claim_id"],
            note=p["note"])
    if (e := p.get("edge")) is not None:  # the response claim's response_to edge, recorded with the answer
        conn.execute("INSERT OR REPLACE INTO edges(id, from_id, to_id, data) VALUES (?,?,?,?)",
                     (e["id"], e["from_id"], e["to_id"], json.dumps(e)))


@projection("warrant.tested")
def _warrant_tested(conn: sqlite3.Connection, ev: Event) -> None:
    _update(conn, "edges", ev.entity_id, warrant_tests=ev.payload["tests"])
