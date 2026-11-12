"""V-HEDGE on stored claims (level and universal-quantifier rules) and the claim gate (Plan 5b Task 3)."""

from __future__ import annotations

import pytest

from rk.argument import ArgumentService, ClaimIn, EdgeIn, EvidenceIn, ObjectionIn
from rk.ids import new_id
from rk.production.artifacts import ArtifactIn, add_artifact
from rk.store.events import NewEvent
from rk.verify.hedge import hedge_level, universal_terms
from rk.verify.numbers import extract_numbers
from rk.verify.support import check_claim_hedge, gate_claims

from .discovery_fakes import make_project


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    yield ps, ArgumentService(ps)
    k.close()


def _note(ps, text: str, *, item: str | None = None, kind: str = "quote") -> str:
    """A verified note of ``text`` (a paraphrase: of the source span ``text``) from ``item`` (a new source by
    default)."""
    nid = new_id("nte")
    item = item or "itm_" + nid.split("_", 1)[1]
    ps.apply(NewEvent(type="note.added", entity_id=item, actor_kind="agent", actor="reader", payload={"note": {
        "id": nid, "item_id": item, "kind": kind, "text": text if kind == "quote" else "A rewording.",
        "locator": None, "role_in_source": "main", "keywords": [], "source_span": None if kind == "quote" else text,
        "hedge_level": hedge_level(text), "snapshot_sha": None, "verified": {"quote": {"ok": True}}}}))
    return nid


def _claim(ps, svc, text: str, *notes: str | tuple[str, str]) -> str:
    """A claim with one evidence link per note: ``text`` or ``(text, item id)``."""
    cid = svc.add_claim(ClaimIn(text=text), generator_vendor="openai", actor="arguer")
    for n in notes:
        t, item = (n, None) if isinstance(n, str) else n
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[_note(ps, t, item=item)]), actor="arguer")
    return cid


def _hedges(ps, cid: str) -> list[str]:
    return [r["verdict"] for r in ps.db.execute(
        "SELECT verdict FROM verifications WHERE target_id=? AND verifier='V-HEDGE' ORDER BY rowid", (cid,))]


def test_universal_terms_skip_negated_hedged_and_idiomatic_uses():
    assert universal_terms("Every catalyst; NONE failed, all of them, always, never.") == \
        ["every", "none", "all", "always", "never"]
    assert universal_terms("Overall, the allowed catalysts were nonetheless tested by everyone.") == []
    for text in ("Not all catalysts worked.", "Almost every trial agreed.", "It nearly always works.",
                 "Virtually none failed.", "It hardly ever fails, which is practically never.",
                 "It doesn't always work.", "It cannot always work.", "It can't always work.",
                 "All-cause mortality fell.", "Never-smokers were excluded.", "In all, 12 trials were found.",
                 "We found 12 trials in all.", "Doses were given every other day.", "No trial reported harm."):
        assert universal_terms(text) == [], text
    assert universal_terms("In all patients the dose was safe.") == ["all"]
    assert universal_terms("In all 12 trials the dose was safe.") == ["all"]


def test_hedge_level_ignores_negated_hedged_and_idiomatic_categorical_words():
    assert hedge_level("All catalysts worked.") == 3 and hedge_level("This proves it.") == 3
    for text in ("Not all catalysts worked.", "All-cause mortality fell.", "This is not proven.",
                 "It cannot always work.", "In all, 12 trials were found.", "Nearly all trials agreed."):
        assert hedge_level(text) == 2, text
    assert hedge_level("Not all trials agreed, but this one proves it.") == 3  # one positive use is enough


def test_negation_reaches_over_two_auxiliaries_or_adverbs():
    """T3: "has not been proven" is negated as "was not proven" is; at most two auxiliaries/adverbs in between."""
    for text in ("Aspirin has not been proven to prevent stroke.", "It is not clearly established that X.",
                 "It has not yet been established.", "This has not been conclusively proven.",
                 "The benefit is not fully established.", "It hasn't been proven.", "It cannot be proven.",
                 # calibration (critical3 conclusion): an article between them kept the negation from counting
                 "It is not an established general causal rule.", "It is not a proven benefit.",
                 "This is not the definitive answer."):
        assert hedge_level(text) == 2, text
    assert universal_terms("It has not been always safe.") == []
    for text in ("Aspirin has been proven to prevent stroke.", "It was clearly established that X.",
                 "Not only was it proven, it was replicated.", "It was not tested, but it is established.",
                 "It has not yet been fully proven."):  # three words between: no longer the negator's scope
        assert hedge_level(text) == 3, text


def test_hedge_overstated_and_universal_rule(env):
    ps, svc = env
    plain = "Copper catalysts reduced CO2 in one cell."
    strong = _claim(ps, svc, "Copper catalysts prove CO2 reduction.", plain)
    r = check_claim_hedge(ps, strong, actor="kernel")
    assert not r.ok and (r.claim_level, r.source_level) == (3, 2) and _hedges(ps, strong) == ["overstated"]
    weak = _claim(ps, svc, "Copper catalysts may reduce CO2.", plain)
    assert check_claim_hedge(ps, weak, actor="kernel").ok and _hedges(ps, weak) == ["pass"]
    assert check_claim_hedge(ps, weak, record=False).ok and _hedges(ps, weak) == ["pass"]  # not recorded

    every = "Every copper catalyst reduced CO2."
    states = "Every copper catalyst tested reduced CO2."
    one = _claim(ps, svc, every, states)
    r = check_claim_hedge(ps, one, actor="kernel")
    assert not r.ok and r.universal == ["every"] and any("at least two sources" in p for p in r.problems)
    # two notes of one source are one source
    same = _claim(ps, svc, every, (states, "itm_S"), ("Copper catalyst B reduced CO2.", "itm_S"))
    r = check_claim_hedge(ps, same, actor="kernel")
    assert not r.ok and any("at least two sources (it has 1)" in p for p in r.problems)
    silent = _claim(ps, svc, every, "Copper catalyst A reduced CO2.", "Copper catalyst B reduced CO2.")
    r = check_claim_hedge(ps, silent, actor="kernel")
    assert not r.ok and any("not stated" in p for p in r.problems) and _hedges(ps, silent) == ["overstated"]
    covered = _claim(ps, svc, every, "Copper catalyst A reduced CO2.", states)
    assert check_claim_hedge(ps, covered, actor="kernel").ok
    # a note covers only the same universal, stated positively
    other = _claim(ps, svc, every, "Copper catalyst A reduced CO2.", "All copper catalysts tested reduced CO2.")
    assert not check_claim_hedge(ps, other, actor="kernel").ok
    negated = _claim(ps, svc, "All copper catalysts reduced CO2.", "Copper catalyst A reduced CO2.",
                     "Not all copper catalysts reduced CO2.")
    assert not check_claim_hedge(ps, negated, actor="kernel").ok
    # none and never are one declared synonym group; a paraphrase counts by its source span
    none = _claim(ps, svc, "None of the catalysts failed.", "Catalyst B did not fail.")
    svc.link_evidence(EvidenceIn(claim_id=none, note_ids=[_note(ps, "Catalysts never failed in the trials.",
                                                                 kind="paraphrase")]), actor="arguer")
    assert check_claim_hedge(ps, none, actor="kernel").ok
    # "no" is not in the group
    assert not check_claim_hedge(ps, _claim(ps, svc, "None of the catalysts failed.", "Catalyst B did not fail.",
                                            "No catalyst failed in trial C."), actor="k").ok
    # a claim whose quantifier is negated or idiomatic states no universal, nor a categorical level
    assert check_claim_hedge(ps, _claim(ps, svc, "Not all copper catalysts reduced CO2.", plain), actor="k").ok
    assert check_claim_hedge(ps, _claim(ps, svc, "All-cause mortality was reported.", plain), actor="k").ok

    bare = _claim(ps, svc, "Copper catalysts reduce CO2.")
    r = check_claim_hedge(ps, bare, actor="kernel")
    assert not r.ok and r.source_level is None and r.reason == "no_evidence"
    svc.withdraw_claim(bare, reason="no evidence", actor="kernel")
    with pytest.raises(ValueError, match="withdrawn"):
        check_claim_hedge(ps, bare, actor="kernel")

    # a stat_result reports its artifact: a plain statement of the computed value, never a universal
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.meta:pool", code_version="v", input_hashes=[],
                                      values={"k": 3}, output_path=None), stage="synthesize", attempt=1, actor="kernel")

    def stat(text: str) -> str:
        bindings = [{"artifact_id": sid, "key": "k", "text": n.text} for n in extract_numbers(text)]
        return svc.add_claim(ClaimIn(text=text, kind="stat_result", artifact_ids=[sid], number_bindings=bindings),
                             generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")

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
    unhedged = ready(hedge=False)  # the gate reruns V-HEDGE itself
    overstated_unrecorded = ready("Copper catalysts prove CO2 reduction.", hedge=False)
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
    # a supported stat_result whose artifact no longer recomputes (here: made by no known code)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version="v", input_hashes=[],
                                      values={"k": 3}, output_path=None), stage="synthesize", attempt=1, actor="kernel")
    stat = svc.add_claim(ClaimIn(text="Pooled RR 0.62 (3 studies).", kind="stat_result", artifact_ids=[sid],
                                 number_bindings=[{"artifact_id": sid, "key": "estimate", "text": "0.62"},
                                                  {"artifact_id": sid, "key": "k", "text": "3"}]),
                         generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")
    svc.set_claim_status(stat, "supported", verification_id="ver_X")

    claims = [good, uncertain, overstated, objected, minor, withdrawn, unhedged, overstated_unrecorded, on_evidence,
              on_edge, everything, stat]
    before = ps.db.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
    lines = gate_claims(ps, claims)
    assert ps.db.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] == before  # the gate records nothing
    assert [x.split(":")[0] for x in lines] == [uncertain, overstated, objected, overstated_unrecorded, on_evidence,
                                                on_edge, everything, stat]
    by = {x.split(":")[0]: x for x in lines}
    assert "status uncertain" in by[uncertain] and "V-HEDGE" not in by[uncertain]
    assert "V-HEDGE overstated" in by[overstated] and "status" not in by[overstated]
    assert "V-HEDGE overstated" in by[overstated_unrecorded]
    assert o_open in by[objected] and "priority-1" in by[objected]
    assert o_evd in by[on_evidence] and o_edge in by[on_edge]
    assert all(p in by[everything] for p in ("status draft", "V-HEDGE overstated", o_all))
    assert f"V-STATS mismatch {sid}" in by[stat] and "V-HEDGE" not in by[stat]
    assert gate_claims(ps, [good, minor, withdrawn, unhedged]) == []
    with pytest.raises(KeyError):
        gate_claims(ps, ["clm_missing"])


def test_never_negates_a_categorical_word_and_stays_a_universal_elsewhere():
    """Fix round 1 (minor 4): "has never been proven" is negated like "has not been proven"; "never" on its own is
    still a universal and categorical."""
    for text in ("Aspirin has never been proven to prevent stroke.", "It was never clearly established.",
                 "It has never been conclusively proven."):
        assert hedge_level(text) == 2, text
        assert universal_terms(text) == [], text
    assert hedge_level("Aspirin never caused bleeding.") == 3
    assert universal_terms("Aspirin never caused bleeding.") == ["never"]
    assert hedge_level("It never failed, which proves it.") == 3


def test_too_few_to_establish_is_a_negation():
    """Calibration (melatonin draft): "too few events to establish an effect" says the effect is not established;
    "too … to", "insufficient to" and "not … enough to" negate the categorical word they lead to."""
    for text in ("There were too few events to establish an effect.", "The trials were too small to prove a benefit.",
                 "The data are insufficient to establish efficacy.", "It was not large enough to establish it.",
                 "Two trials are not enough to firmly establish an effect.", "Too few trials to always agree."):
        assert hedge_level(text) == 2, text
    assert universal_terms("Too few trials to always agree.") == []
    for text in ("The effect was too large to ignore, which proves it.", "Enough trials were done to establish it.",
                 "It was established in too few trials to matter.",
                 "There were not only enough trials to establish it but to replicate it.",
                 "It is not just large enough to prove it."):
        assert hedge_level(text) == 3, text
