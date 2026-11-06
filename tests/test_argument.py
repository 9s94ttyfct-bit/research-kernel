import pytest

from rk.argument import ArgumentService, ClaimIn, EdgeIn, EvidenceIn, ObjectionIn
from rk.config import Settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.notes import NoteIn, add_note
from rk.store.events import NewEvent
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.workspace import Kernel

from .conftest import DOCS


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
