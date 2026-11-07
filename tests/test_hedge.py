"""V-HEDGE on stored claims (level and universal-quantifier rules) and the claim gate (Plan 5b Task 3)."""

from __future__ import annotations

import pytest

from rk.argument import ArgumentService, ClaimIn, EdgeIn, EvidenceIn, ObjectionIn
from rk.ids import new_id
from rk.production.artifacts import ArtifactIn, add_artifact
from rk.store.events import NewEvent
from rk.verify.hedge import hedge_level, universal_terms
from rk.verify.support import check_claim_hedge, gate_claims

from .discovery_fakes import make_project


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    yield ps, ArgumentService(ps)
    k.close()


def _note(ps, text: str, *, kind: str = "quote") -> str:
    """A verified note of ``text`` (a paraphrase: of the source span ``text``)."""
    nid = new_id("nte")
    ps.apply(NewEvent(type="note.added", entity_id="itm_A", actor_kind="agent", actor="reader", payload={"note": {
        "id": nid, "item_id": "itm_A", "kind": kind, "text": text if kind == "quote" else "A rewording.",
        "locator": None, "role_in_source": "main", "keywords": [], "source_span": None if kind == "quote" else text,
        "hedge_level": hedge_level(text), "snapshot_sha": None, "verified": {"quote": {"ok": True}}}}))
    return nid


def _claim(ps, svc, text: str, *notes: str) -> str:
    cid = svc.add_claim(ClaimIn(text=text), generator_vendor="openai", actor="arguer")
    for t in notes:
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[_note(ps, t)]), actor="arguer")
    return cid


def _hedges(ps, cid: str) -> list[str]:
    return [r["verdict"] for r in ps.db.execute(
        "SELECT verdict FROM verifications WHERE target_id=? AND verifier='V-HEDGE' ORDER BY rowid", (cid,))]


def test_hedge_overstated_and_universal_rule(env):
    ps, svc = env
    plain = "Copper catalysts reduced CO2 in one cell."
    strong = _claim(ps, svc, "Copper catalysts prove CO2 reduction.", plain)
    r = check_claim_hedge(ps, strong, actor="kernel")
    assert not r.ok and (r.claim_level, r.source_level) == (3, 2) and _hedges(ps, strong) == ["overstated"]
    weak = _claim(ps, svc, "Copper catalysts may reduce CO2.", plain)
    assert check_claim_hedge(ps, weak, actor="kernel").ok and _hedges(ps, weak) == ["pass"]

    assert universal_terms("Every catalyst; NONE failed, all of them, always, never.") == \
        ["every", "none", "all", "always", "never"]
    assert universal_terms("Overall, the allowed catalysts were nonetheless tested by everyone.") == []

    every = "Every copper catalyst reduced CO2."
    states = "All copper catalysts tested reduced CO2."
    one = _claim(ps, svc, every, states)
    r = check_claim_hedge(ps, one, actor="kernel")
    assert not r.ok and r.universal == ["every"] and any("at least two" in p for p in r.problems)
    silent = _claim(ps, svc, every, "Copper catalyst A reduced CO2.", "Copper catalyst B reduced CO2.")
    r = check_claim_hedge(ps, silent, actor="kernel")
    assert not r.ok and any("not stated" in p for p in r.problems) and _hedges(ps, silent) == ["overstated"]
    covered = _claim(ps, svc, every, "Copper catalyst A reduced CO2.", states)
    assert check_claim_hedge(ps, covered, actor="kernel").ok
    # any universal in a note covers it; a paraphrase counts by its source span
    none = _claim(ps, svc, "None of the catalysts failed.", "Catalyst B did not fail.")
    svc.link_evidence(EvidenceIn(claim_id=none, note_ids=[_note(ps, "Catalysts never failed in the trials.",
                                                                 kind="paraphrase")]), actor="arguer")
    assert check_claim_hedge(ps, none, actor="kernel").ok

    bare = _claim(ps, svc, "Copper catalysts reduce CO2.")
    r = check_claim_hedge(ps, bare, actor="kernel")
    assert not r.ok and r.source_level is None and r.reason == "no_evidence"

    # a stat_result reports its artifact: a plain statement of the computed value, never a universal
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.meta:pool", code_version="v", input_hashes=[],
                                      values={"k": 3}, output_path=None), stage="synthesize", attempt=1, actor="kernel")

    def stat(text: str) -> str:
        return svc.add_claim(ClaimIn(text=text, kind="stat_result", artifact_ids=[sid]), generator_vendor=None,
                             actor="kernel", actor_kind="kernel", origin="synthesis")

    assert check_claim_hedge(ps, stat("Pooled RR 0.62 (3 studies)."), actor="kernel").ok
    assert not check_claim_hedge(ps, stat("All 3 studies favoured treatment."), actor="kernel").ok


def test_gate_claims_lists_each_problem(env):
    ps, svc = env
    plain = "Copper catalysts reduced CO2 in one cell."

    def ready(text: str = "Copper catalysts reduce CO2.", status: str = "supported", hedge: bool = True) -> str:
        cid = _claim(ps, svc, text, plain)
        if hedge:
            check_claim_hedge(ps, cid, actor="kernel")
        if status != "draft":
            svc.set_claim_status(cid, status, verification_id="ver_X")
        return cid

    def objection(target: str, priority: int = 1) -> str:
        return svc.add_objection(ObjectionIn(target_id=target, category="counterexample", text="Nickel too.",
                                             priority=priority), actor="skeptic")

    good = ready()
    uncertain = ready(status="uncertain")
    overstated = ready("Copper catalysts prove CO2 reduction.")
    objected = ready()
    o_open = objection(objected)
    minor = ready()
    objection(minor, priority=2)
    answered = objection(minor)
    svc.respond_objection(answered, response_claim_id=None, status="rejected_invalid", note="off topic",
                          actor="arguer")
    withdrawn = ready(status="uncertain")
    objection(withdrawn)
    svc.withdraw_claim(withdrawn, reason="uncertain in P1", actor="kernel")
    unhedged = ready(hedge=False)
    on_evidence = ready()
    o_evd = objection(svc.list_evidence(on_evidence)[0]["id"])
    on_edge = ready()
    # legacy edges: a reason edge carrying an untyped warrant, a warrant_of edge without one, and a response_to
    # edge added by hand (its objection stays open)
    legacy = new_id("edg")
    ps.apply(NewEvent(type="edge.added", entity_id=legacy, actor_kind="agent", actor="a", payload={"edge": {
        "id": legacy, "from_id": good, "to_id": on_edge, "type": "reason_of", "warrant": {"text": "because"}}}))
    bare = new_id("edg")
    ps.apply(NewEvent(type="edge.added", entity_id=bare, actor_kind="agent", actor="a", payload={"edge": {
        "id": bare, "from_id": good, "to_id": legacy, "type": "warrant_of"}}))
    o_edge = objection(legacy)
    svc.add_edge(EdgeIn(from_id=good, to_id=o_open, type="response_to"), actor="a")
    everything = ready("Copper catalysts prove CO2 reduction.", status="draft")
    o_all = objection(everything)

    claims = [good, uncertain, overstated, objected, minor, withdrawn, unhedged, on_evidence, on_edge, everything]
    lines = gate_claims(ps, claims)
    assert [x.split(":")[0] for x in lines] == [uncertain, overstated, objected, unhedged, on_evidence, on_edge,
                                                everything]
    by = {x.split(":")[0]: x for x in lines}
    assert "status uncertain" in by[uncertain] and "V-HEDGE" not in by[uncertain]
    assert "V-HEDGE overstated" in by[overstated] and "status" not in by[overstated]
    assert o_open in by[objected] and "priority-1" in by[objected]
    assert "V-HEDGE not run" in by[unhedged]
    assert o_evd in by[on_evidence] and o_edge in by[on_edge]
    assert all(p in by[everything] for p in ("status draft", "V-HEDGE overstated", o_all))
    assert gate_claims(ps, [good, minor, withdrawn]) == []
    with pytest.raises(KeyError):
        gate_claims(ps, ["clm_missing"])
