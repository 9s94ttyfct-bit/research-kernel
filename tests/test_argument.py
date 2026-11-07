import json
import sqlite3

import pytest
from pydantic import ValidationError

from rk.argument import (
    ArgumentService,
    ClaimIn,
    EdgeIn,
    EvidenceIn,
    ObjectionIn,
    WarrantTests,
)
from rk.config import Settings, save_settings
from rk.discovery.store import add_hypothesis, hypotheses, set_hypothesis_status
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.notes import NoteIn, add_note
from rk.production.artifacts import ArtifactIn, add_artifact
from rk.production.models import DesignIn
from rk.production.store import freeze_design, set_design
from rk.store.db import open_project_db
from rk.store.events import NewEvent
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.workspace import Kernel

from .conftest import DOCS
from .discovery_fakes import add_an_rq, freeze_an_rq


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    ps = k.create_project("arg", "Arg", actor="t")
    _, lib = k.project("arg")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    note = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="about copper catalysts for CO2 reduction",
                                    role_in_source="main"), actor="t")
    yield k, reg, ps, iid, note.note_id
    k.close()


def test_claim_evidence_roundtrip_and_rebuild(env):
    _, _, ps, iid, nid = env
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text="Copper catalysts may reduce CO2."), generator_vendor="anthropic", actor="a")
    eid = svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[nid], criteria={
        "relevant": {"score": 2, "reason": "directly about copper"}}), actor="a")
    oid = svc.add_objection(ObjectionIn(target_id=cid, category="counterexample", text="Nickel too.", source_id=iid),
                            actor="s")
    gid = svc.add_edge(EdgeIn(from_id=eid, to_id=cid, type="evidence_of"), actor="a")
    c = svc.get_claim(cid)
    assert c["status"] == "draft" and c["hedge_level"] == 0 and c["generator_vendor"] == "anthropic"
    assert c["evidence"][0]["notes"][0]["id"] == nid and c["objections"][0]["id"] == oid
    svc.set_claim_status(cid, "supported", verification_id="ver_X")
    before = (svc.get_claim(cid), svc.list_claims("supported"), ps.db.execute("SELECT * FROM edges").fetchall()[0]["id"])
    rebuild(ps.db)
    assert (svc.get_claim(cid), svc.list_claims("supported"), ps.db.execute("SELECT * FROM edges").fetchall()[0]["id"]) \
        == before
    assert before[2] == gid


def test_evidence_rejects_unknown_note_and_criteria(env):
    _, _, ps, _, nid = env
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text="x"), generator_vendor=None, actor="a")
    with pytest.raises(ValueError, match="unknown notes"):
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=["nte_missing"]), actor="a")
    with pytest.raises(ValueError, match="unknown criteria"):
        EvidenceIn(claim_id=cid, note_ids=[nid], criteria={"vibes": {"score": 1, "reason": "?"}})
    with pytest.raises(KeyError):
        svc.link_evidence(EvidenceIn(claim_id="clm_missing", note_ids=[nid]), actor="a")


def test_edge_type_prefix_rules(env):
    _, _, ps, _, _nid = env
    svc = ArgumentService(ps)
    a = svc.add_claim(ClaimIn(text="a"), generator_vendor=None, actor="x")
    b = svc.add_claim(ClaimIn(text="b"), generator_vendor=None, actor="x")
    svc.add_edge(EdgeIn(from_id=a, to_id=b, type="reason_of"), actor="x")
    with pytest.raises(ValueError, match="evidence_of must go from evd"):
        svc.add_edge(EdgeIn(from_id=a, to_id=b, type="evidence_of"), actor="x")
    with pytest.raises(KeyError):
        svc.add_edge(EdgeIn(from_id=a, to_id="clm_nope", type="reason_of"), actor="x")


def test_generator_vendor_from_task(env):
    _k, reg, ps, _, _ = env
    ps.apply(NewEvent(type="task.created", entity_id="tsk_V1", actor_kind="kernel", actor="k", payload={
        "role": "arguer", "driver": "codex_cli", "vendor": "openai", "instructions": "", "inputs": {}, "tools": [],
        "output_schema": {}}))
    agent = CallContext(role="arguer", actor="arguer@codex_cli", idempotency_key="c1", task_id="tsk_V1",
                        project="arg")
    out = reg.call("rk_claim_add", {"claim": {"text": "Copper works."}}, agent)
    assert out["generator_vendor"] == "openai"
    human = reg.call("rk_claim_add", {"project": "arg", "claim": {"text": "I think so."}},
                     CallContext(role="cli", actor="u", idempotency_key="c2"))
    assert human["generator_vendor"] == "human"
    chat = reg.call("rk_claim_add", {"claim": {"text": "From chat."}},
                    CallContext(role="lead", actor="lead", idempotency_key="c3", project="arg", task_id="chat_x:1"))
    assert chat["generator_vendor"] is None  # unknown vendor: can never pass cross-vendor review


def test_status_set_only_via_service(env):
    _, reg, *_ = env
    names = {t.name for t in reg.list("cli")}
    assert not any("status" in n for n in names if n.startswith("rk_claim"))
    with pytest.raises(PermissionDenied):
        reg.call("rk_claim_add", {"project": "arg", "claim": {"text": "x"}},
                 CallContext(role="reader", actor="r", idempotency_key="k"))
    with pytest.raises(ToolError) as ei:
        reg.call("rk_evidence_link", {"project": "arg", "evidence": {"claim_id": "clm_x", "note_ids": ["nte_y"]}},
                 CallContext(role="arguer", actor="a", idempotency_key="k2"))
    assert ei.value.type == "not_found"


def _snapshot(ps) -> dict:
    """Every argument and hypothesis table (``data`` decoded: its key order is not kept), to compare with a rebuild
    from the events."""
    def row(r) -> dict:
        d = dict(r)
        return {**d, "data": json.loads(d["data"])} if "data" in d else d

    return {t: sorted((row(r) for r in ps.db.execute(f"SELECT * FROM {t}")), key=lambda d: d["id"])
            for t in ("claims", "evidence", "edges", "objections", "hypotheses")}


def test_withdraw_and_narrow_claims(env):
    _, _, ps, _, _ = env
    svc = ArgumentService(ps)
    broad = svc.add_claim(ClaimIn(text="Copper catalysts reduce CO2 in every cell."), generator_vendor="openai",
                          actor="arguer")
    narrow = svc.add_claim(ClaimIn(text="Copper catalysts may reduce CO2 in aqueous cells.", narrowed_from=broad),
                           generator_vendor="openai", actor="arguer")
    svc.set_claim_status(broad, "supported", verification_id="ver_A")
    svc.withdraw_claim(broad, reason="uncertain in P1; narrowed instead", actor="kernel")
    svc.set_claim_status(narrow, "supported", verification_id="ver_B")
    c = svc.get_claim(broad)
    assert c["status"] == "withdrawn" and c["withdrawn_reason"] == "uncertain in P1; narrowed instead"
    assert svc.get_claim(narrow)["narrowed_from"] == broad
    assert [x["id"] for x in svc.list_claims(status="supported")] == [narrow]
    with pytest.raises(ValueError, match="already withdrawn"):
        svc.withdraw_claim(broad, reason="again", actor="kernel")
    with pytest.raises(KeyError):
        svc.withdraw_claim("clm_missing", reason="x", actor="kernel")
    with pytest.raises(KeyError):
        svc.add_claim(ClaimIn(text="x", narrowed_from="clm_missing"), generator_vendor=None, actor="a")
    with pytest.raises(KeyError):  # an artifact a claim cites must exist
        svc.add_claim(ClaimIn(text="x", kind="stat_result", artifact_ids=["stt_missing"]), generator_vendor=None,
                      actor="kernel", actor_kind="kernel", origin="synthesis")
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.meta:pool", code_version="c0de",
                                      input_hashes=[], values={"k": 2}, output_path=None),
                       stage="synthesize", attempt=1, actor="kernel")
    stat = svc.add_claim(ClaimIn(text="Pooled RR 0.62.", kind="stat_result", artifact_ids=[sid]),
                         generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")
    feas = svc.add_claim(ClaimIn(text="The cells can be built in a month.", kind="feasibility"),
                         generator_vendor="openai", actor="arguer")
    assert {x["id"] for x in svc.list_claims()} == {broad, narrow, feas}
    assert {x["id"] for x in svc.list_claims(origin="synthesis")} == {stat}
    assert {x["id"] for x in svc.list_claims(origin=("argument", "synthesis"))} == {broad, narrow, feas, stat}
    assert {x["id"] for x in svc.list_claims(status="draft", origin=("argument", "synthesis"))} == {feas, stat}
    before = _snapshot(ps)
    rebuild(ps.db)
    assert _snapshot(ps) == before


def test_a_withdrawn_claim_stays_withdrawn(env):
    _, _, ps, _, _ = env
    svc = ArgumentService(ps)
    c = svc.add_claim(ClaimIn(text="Copper catalysts reduce CO2."), generator_vendor="openai", actor="arguer")
    svc.withdraw_claim(c, reason="uncertain in P1", actor="kernel")
    for status in ("supported", "uncertain", "draft"):
        with pytest.raises(ValueError, match="withdrawn"):
            svc.set_claim_status(c, status, verification_id="ver_X")
    assert svc.get_claim(c)["status"] == "withdrawn"


def test_stat_claims_and_artifact_links_are_kernel_only(env):
    _, reg, ps, _, _ = env
    svc = ArgumentService(ps)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version="c0de",
                                      input_hashes=[], values={"k": 2}, output_path=None),
                       stage="synthesize", attempt=1, actor="kernel")
    # the agent tool does not offer them
    schema = json.dumps(reg.get("rk_claim_add").input_model.model_json_schema())
    assert "stat_result" not in schema and "artifact_ids" not in schema and "feasibility" in schema
    agent = CallContext(role="arguer", actor="arguer@x", idempotency_key="s1", project="arg")
    with pytest.raises(ValidationError):
        reg.call("rk_claim_add", {"claim": {"text": "Pooled RR 0.62.", "kind": "stat_result"}}, agent)
    with pytest.raises(ValidationError):
        reg.call("rk_claim_add", {"claim": {"text": "Pooled RR 0.62.", "artifact_ids": [sid]}},
                 CallContext(role="arguer", actor="arguer@x", idempotency_key="s2", project="arg"))
    feas = reg.call("rk_claim_add", {"claim": {"text": "Copper works.", "kind": "feasibility"}},
                    CallContext(role="arguer", actor="arguer@x", idempotency_key="s3", project="arg"))
    assert svc.get_claim(feas["claim_id"])["kind"] == "feasibility"
    # and the service refuses them to anyone but the kernel
    for c in (ClaimIn(text="Pooled RR 0.62.", kind="stat_result"), ClaimIn(text="See the table.", artifact_ids=[sid])):
        for kind in ("agent", "human"):
            with pytest.raises(ValueError, match="only the kernel"):
                svc.add_claim(c, generator_vendor=None, actor="a", actor_kind=kind, origin="synthesis")
    k = svc.add_claim(ClaimIn(text="Pooled RR 0.62.", kind="stat_result", artifact_ids=[sid]), generator_vendor=None,
                      actor="kernel", actor_kind="kernel", origin="synthesis")
    assert svc.get_claim(k)["artifact_ids"] == [sid]
    assert [x["id"] for x in svc.list_claims(origin="synthesis")] == [k]


def test_objection_response_lifecycle(env):
    _, _, ps, iid, nid = env
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text="Copper catalysts may reduce CO2."), generator_vendor="anthropic", actor="a")
    eid = svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[nid]), actor="a")
    svc.add_edge(EdgeIn(from_id=eid, to_id=cid, type="evidence_of"), actor="a")
    o1 = svc.add_objection(ObjectionIn(target_id=cid, category="counterexample", text="Nickel too.", source_id=iid),
                           actor="skeptic")
    o2 = svc.add_objection(ObjectionIn(target_id=cid, category="definition", text="Reduce how?"), actor="skeptic")
    o3 = svc.add_objection(ObjectionIn(target_id=cid, category="problem", text="Too small."), actor="skeptic")
    assert {o["id"] for o in svc.list_objections(target_id=cid, status="open")} == {o1, o2, o3}
    assert all(o["note"] is None and o["response_claim_id"] is None for o in svc.list_objections())
    resp = svc.add_claim(ClaimIn(text="Nickel works only at high overpotential."), generator_vendor="anthropic",
                         actor="a")
    with pytest.raises(ValueError, match="needs the response claim"):
        svc.respond_objection(o1, response_claim_id=None, status="responded", note="", actor="a")
    with pytest.raises(ValueError, match="only a responded objection"):
        svc.respond_objection(o2, response_claim_id=resp, status="conceded", note="", actor="a")
    with pytest.raises(KeyError):
        svc.respond_objection(o1, response_claim_id="clm_missing", status="responded", note="", actor="a")
    with pytest.raises(KeyError):
        svc.respond_objection("obj_missing", response_claim_id=None, status="conceded", note="", actor="a")
    g = svc.respond_objection(o1, response_claim_id=resp, status="responded", note="scope limited", actor="a")
    svc.respond_objection(o2, response_claim_id=None, status="rejected_invalid", note="the claim defines it",
                          actor="kernel")
    svc.respond_objection(o3, response_claim_id=None, status="conceded", note="narrowed the claim", actor="kernel")
    for o in (o2, o3):  # conceded and rejected answers are final
        with pytest.raises(ValueError, match="already answered"):
            svc.respond_objection(o, response_claim_id=resp, status="responded", note="", actor="a")
    svc.set_claim_status(resp, "supported", verification_id="ver_R")
    with pytest.raises(ValueError, match="its response .* is supported"):
        svc.respond_objection(o1, response_claim_id=None, status="conceded", note="", actor="a")
    by_id = {o["id"]: o for o in svc.list_objections(target_id=cid)}
    assert (by_id[o1]["status"], by_id[o1]["response_claim_id"], by_id[o1]["note"]) == ("responded", resp,
                                                                                         "scope limited")
    assert (by_id[o2]["status"], by_id[o2]["response_claim_id"]) == ("rejected_invalid", None)
    assert svc.list_objections(status="open") == []
    assert [o["id"] for o in svc.list_objections(status="responded")] == [o1]
    assert {o["status"] for o in svc.get_claim(cid)["objections"]} == {"responded", "rejected_invalid", "conceded"}
    (edge,) = svc.list_edges(to_id=o1, type="response_to")  # the kernel added the response edge
    assert (edge["id"], edge["from_id"]) == (g, resp)
    assert svc.list_edges(type="response_to") == [edge]
    assert [e["type"] for e in svc.list_edges(to_id=cid)] == ["evidence_of"]
    assert svc.list_edges(from_id=resp, type="evidence_of") == []
    assert [e["id"] for e in svc.list_evidence(cid)] == [eid] and svc.list_evidence(resp) == []
    before = _snapshot(ps)
    rebuild(ps.db)
    assert _snapshot(ps) == before


def test_objection_answered_again_once_its_response_is_narrowed(env):
    _, _, ps, _, _ = env
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text="Copper catalysts may reduce CO2."), generator_vendor="anthropic", actor="a")
    o = svc.add_objection(ObjectionIn(target_id=cid, category="counterexample", text="Nickel too."), actor="skeptic")
    resp = svc.add_claim(ClaimIn(text="Nickel never reduces CO2."), generator_vendor="anthropic", actor="a")
    g1 = svc.respond_objection(o, response_claim_id=resp, status="responded", note="first", actor="a")
    svc.set_claim_status(resp, "uncertain", verification_id="ver_1")
    svc.withdraw_claim(resp, reason="uncertain in P1; narrowed", actor="kernel")
    narrow = svc.add_claim(ClaimIn(text="Nickel reduces CO2 only at high overpotential.", narrowed_from=resp),
                           generator_vendor="anthropic", actor="a")
    g2 = svc.respond_objection(o, response_claim_id=narrow, status="responded", note="narrowed", actor="a")
    (got,) = svc.list_objections(target_id=cid)
    assert (got["status"], got["response_claim_id"], got["note"]) == ("responded", narrow, "narrowed")  # latest wins
    assert {(e["id"], e["from_id"]) for e in svc.list_edges(to_id=o, type="response_to")} == {(g1, resp),
                                                                                             (g2, narrow)}
    answers = [e.payload for e in ps.log.iter() if e.type == "objection.responded"]
    assert [(p["response_claim_id"], p["note"]) for p in answers] == [(resp, "first"), (narrow, "narrowed")]
    svc.set_claim_status(narrow, "supported", verification_id="ver_2")
    with pytest.raises(ValueError, match="is supported"):
        svc.respond_objection(o, response_claim_id=resp, status="responded", note="again", actor="a")
    before = _snapshot(ps)
    rebuild(ps.db)
    assert _snapshot(ps) == before


def test_warrant_shape_validated_and_tests_recorded(env):
    _, _, ps, _, _ = env
    svc = ArgumentService(ps)
    a = svc.add_claim(ClaimIn(text="Copper binds CO intermediates."), generator_vendor=None, actor="x")
    b = svc.add_claim(ClaimIn(text="Copper may reduce CO2 to ethylene."), generator_vendor=None, actor="x")
    w = svc.add_claim(ClaimIn(text="Catalysts that bind CO make C2 products."), generator_vendor=None, actor="x")
    e = svc.add_edge(EdgeIn(from_id=a, to_id=b, type="reason_of"), actor="x")
    warrant = {"circumstance": "a catalyst binds CO", "consequence": "it makes C2 products", "type": "causal"}
    with pytest.raises(ValidationError, match="warrant_of edge needs its warrant"):
        EdgeIn(from_id=w, to_id=e, type="warrant_of")
    with pytest.raises(ValidationError):
        EdgeIn(from_id=w, to_id=e, type="warrant_of", warrant={**warrant, "type": "vibes"})
    with pytest.raises(ValidationError):
        EdgeIn(from_id=w, to_id=e, type="warrant_of", warrant={"circumstance": "c"})
    with pytest.raises(ValidationError, match="only a warrant_of edge"):
        EdgeIn(from_id=a, to_id=b, type="reason_of", warrant=warrant)
    g = svc.add_edge(EdgeIn(from_id=w, to_id=e, type="warrant_of", warrant=warrant), actor="x")
    ok = {"ok": True, "reason": "fits"}
    tests = WarrantTests(true=ok, limited={"ok": True, "reason": "C2 only on Cu(100)"}, circumstance_fits=ok,
                         consequence_fits=ok, accepted={"ok": False, "reason": "contested in the field"})
    with pytest.raises(ValidationError):
        WarrantTests(true=ok, limited=ok, circumstance_fits=ok, consequence_fits=ok)
    with pytest.raises(ValueError, match="not a warrant_of edge"):
        svc.record_warrant_tests(e, tests, actor="skeptic")
    with pytest.raises(KeyError):
        svc.record_warrant_tests("edg_missing", tests, actor="skeptic")
    svc.record_warrant_tests(g, tests, actor="skeptic")
    (edge,) = svc.list_edges(type="warrant_of")
    assert edge["warrant"] == warrant and edge["warrant_tests"] == tests.model_dump(mode="json")
    assert edge["warrant_tests"]["accepted"] == {"ok": False, "reason": "contested in the field"}
    before = _snapshot(ps)
    rebuild(ps.db)
    assert _snapshot(ps) == before


def _a_design(hyp_ids: list[str]) -> DesignIn:
    return DesignIn.model_validate({
        "items": [{"id": "D1", "kind": "experiment", "sample": "40 cells", "controls": ["bare carbon"],
                   "measurements": [{"id": "M1", "what": "C2 efficiency", "instrument": "GC", "unit": "%"}],
                   "analysis": "t test", "data_source": "new measurements", "hypothesis_ids": hyp_ids}],
        "expected_results": "Copper beats nickel.", "alternative_explanations": ["surface area"]})


def test_hypothesis_add_with_rival_and_status(env):
    _, _, ps, _, _ = env
    draft_rq = add_an_rq(ps)
    (other,) = hypotheses(ps, draft_rq)
    with pytest.raises(ValueError, match="not frozen"):
        add_hypothesis(ps, draft_rq, "S.", "F.", actor="ideator")
    rq_id = freeze_an_rq(ps)
    (h0,) = hypotheses(ps, rq_id)  # added with the RQ: the defaults
    assert (h0["rival_of"], h0["status"], h0["prereg_hash"]) == (None, "proposed", None)
    h1 = add_hypothesis(ps, rq_id, "Nickel works as well.", "Nickel's yield is under half of copper's.",
                        rival_of=h0["id"], actor="ideator")
    assert h1.startswith("hyp_")
    with pytest.raises(KeyError):
        add_hypothesis(ps, "rq_missing", "S.", "F.", actor="ideator")
    with pytest.raises(KeyError):
        add_hypothesis(ps, rq_id, "S.", "F.", rival_of="hyp_missing", actor="ideator")
    with pytest.raises(ValueError, match="another research question"):
        add_hypothesis(ps, rq_id, "S.", "F.", rival_of=other["id"], actor="ideator")
    with pytest.raises(ValueError, match="needs the prereg_hash"):
        set_hypothesis_status(ps, h1, "tested_by_design", actor="kernel")
    with pytest.raises(ValueError, match="unknown hypothesis status"):
        set_hypothesis_status(ps, h1, "maybe", actor="kernel")  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        set_hypothesis_status(ps, "hyp_missing", "dropped", actor="kernel")
    draft = set_design(ps, _a_design([h1]), stage="design", attempt=1, actor="ideator")
    with pytest.raises(ValueError, match="no frozen design"):
        set_hypothesis_status(ps, h1, "tested_by_design", prereg_hash="ab" * 32, actor="kernel")
    prereg = freeze_design(ps, draft, actor="kernel")
    with pytest.raises(ValueError, match="does not test"):
        set_hypothesis_status(ps, h0["id"], "tested_by_design", prereg_hash=prereg, actor="kernel")
    set_hypothesis_status(ps, h1, "tested_by_design", prereg_hash=prereg, actor="kernel")
    with pytest.raises(ValueError, match="tested_by_design -> proposed"):
        set_hypothesis_status(ps, h1, "proposed", actor="kernel")
    set_hypothesis_status(ps, h0["id"], "dropped", actor="kernel")
    for status, prereg_hash in (("proposed", None), ("dropped", None), ("tested_by_design", prereg)):
        with pytest.raises(ValueError, match="dropped is final"):
            set_hypothesis_status(ps, h0["id"], status, prereg_hash=prereg_hash, actor="kernel")
    rows = {h["id"]: h for h in hypotheses(ps, rq_id)}
    assert [*rows] == [h0["id"], h1]
    assert (rows[h1]["rival_of"], rows[h1]["status"], rows[h1]["prereg_hash"]) == (h0["id"], "tested_by_design",
                                                                                   prereg)
    assert (rows[h0["id"]]["status"], rows[h0["id"]]["prereg_hash"]) == ("dropped", None)
    before = _snapshot(ps)
    rebuild(ps.db)
    assert _snapshot(ps) == before


def test_an_older_hypotheses_table_gets_the_new_columns(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = open_project_db(db)
    conn.execute("DROP TABLE hypotheses")
    conn.execute("CREATE TABLE hypotheses (id TEXT PRIMARY KEY, rq_id TEXT NOT NULL, statement TEXT NOT NULL, "
                 "falsification TEXT NOT NULL)")
    conn.execute("INSERT INTO hypotheses VALUES ('hyp_1', 'rq_1', 'H.', 'X.')")
    conn.close()
    conn = open_project_db(db)
    row = dict(conn.execute("SELECT * FROM hypotheses").fetchone())
    assert row == {"id": "hyp_1", "rq_id": "rq_1", "statement": "H.", "falsification": "X.", "rival_of": None,
                   "status": "proposed", "prereg_hash": None}
    conn.close()
    assert sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE name IN ('artifacts', 'designs') "
                                       "ORDER BY name").fetchall() == [("artifacts",), ("designs",)]
