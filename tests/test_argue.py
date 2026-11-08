"""Plan 5b Task 5: the argue stage. The Arguer builds an argument on synthesize's supported findings, the Skeptic
attacks it round after round until the fixed point (or the open-stage budget); the kernel stores and verifies every
claim, edge, warrant test and objection, resolves contradictions (P2 asks, P1 votes) and gates on what it stored."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rk.argument import ArgumentService, ObjectionIn
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.executor import RateCapped
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES
from rk.orchestrator.state import record_attempt, record_passed, record_ran
from rk.production.argue import (
    SKEPTIC_TOOLS,
    ArgueWorker,
    ArgumentDraft,
    Critique,
    RecheckOut,
    argue_limitations,
    argue_notes,
    argument_claims,
    main_claim,
)
from rk.production.extraction import usable_evidence
from rk.production.models import SynthesisPlan
from rk.production.synthesis import SynthesizeWorker, synthesis_claims
from rk.roles.policy import tool_roles
from rk.roles.prompts import build_system_prompt
from tests.production_fakes import make_frozen_project, production_ctx, votes_on
from tests.test_synthesis import DESIGN, Exec, _setup, assess

F1 = "Aspirin was studied in a randomised trial of older people."
F2 = "Aspirin was studied in a cohort of older people."
MAIN = "Aspirin was studied in older people."
REASON = "Older people were enrolled in a randomised trial."
RESPONSE = "Older people were enrolled in a cohort study."
WARRANT = {"circumstance": "a study enrolled older people", "consequence": "it studied aspirin in older people",
           "type": "generalization"}
TESTS = ("true", "limited", "circumstance_fits", "consequence_fits", "accepted")
SELF_CHECK = {"ok": True, "reason": "It says in whom aspirin was studied."}


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http,
                                        **{"paper_type": "critical_review", **getattr(request, "param", {})})
    yield k, ps, lib
    k.close()


# ---- scripted roles ----
def _judge(uncertain=()):
    def out(inputs):
        label = "partial" if any(u in inputs["claim"] for u in uncertain) else "supported"
        return {"label": label, "quoted_spans": [inputs["evidence"].split("\n\n")[0]]}
    return out


def _judges(uncertain=(), n: int = 60) -> dict:
    return {("judge", "JudgeVerdict"): [_judge(uncertain)] * n, ("judge_2", "JudgeVerdict"): [_judge(uncertain)] * n}


def _votes(first=(), second=()) -> dict:
    """VoteOut answers of judge and judge_2, in order, then ``answers`` (the RQ check)."""
    def queue(choices):
        return [{"choice": c, "reason": f"because {c}"} for c in (*choices, *["answers"] * 4)]
    return {("judge", "VoteOut"): queue(first), ("judge_2", "VoteOut"): queue(second)}


def _exec(drafts=(), critiques=(), *, uncertain=(), votes=None, narrowed=(), rechecks=()) -> Exec:
    return Exec({("arguer", "ArgumentDraft"): list(drafts), ("skeptic", "Critique"): list(critiques),
                 ("arguer", "NarrowedClaim"): list(narrowed), ("skeptic", "RecheckOut"): list(rechecks),
                 **_judges(uncertain), **(votes if votes is not None else _votes())})


def _note(inputs, word: str) -> str:
    return next(n["id"] for n in inputs["notes"] if word in n["text"])


def claim(inputs, text: str, *words: str) -> dict:
    return {"text": text, "kind": "fact",
            "evidence": [{"note_ids": [_note(inputs, w) for w in words], "interpretation": "The note says so."}]}


def role(inputs, name: str) -> list[str]:
    return [c["id"] for c in inputs["argument"]["claims"] if c["role"] == name]


def draft(*, reasons=None, answers=SELF_CHECK, **extra):
    """Round 1: the main claim (both notes) with one new reason (the rct note) under one warrant."""
    def out(inputs):
        rs = [{"ref": "r1", "claim": claim(inputs, REASON, "rct"), "supports": "main", "warrant": WARRANT}]
        return {"main_claim": claim(inputs, MAIN, "rct", "cohort"),
                "reasons": rs if reasons is None else reasons(inputs), "answers_rq": answers, **extra}
    return out


def responses(*answers):
    """A later round answering the open objections in order: each answer is a status, or (status, note), or a claim
    text for ``responded``."""
    def out(inputs):
        opened = inputs["open_objections"]
        rs = []
        for o, a in zip(opened, answers, strict=False):
            if isinstance(a, tuple):
                rs.append({"objection_id": o["id"], "status": a[0], "note": a[1]})
            elif a in ("conceded", "rejected_invalid"):
                rs.append({"objection_id": o["id"], "status": a})
            else:
                rs.append({"objection_id": o["id"], "status": "responded", "claim": claim(inputs, a, "cohort")})
        return {"responses": rs}
    return out


def verdicts(failed: str | None = None) -> dict:
    return {t: {"ok": t != failed, "reason": f"{t} {'fails' if t == failed else 'holds'}"} for t in TESTS}


def critique(*, objections=(), fail: str | None = None, conflicts=(), test: bool = True, extra_tests=()):
    def out(inputs):
        given = [{"edge_id": w["edge_id"], "tests": verdicts(fail)} for w in inputs["untested_warrants"]]
        return {"objections": [o(inputs) if callable(o) else o for o in objections],
                "warrant_tests": [*(given if test else []), *({"edge_id": e, "tests": verdicts()} for e in extra_tests)],
                "conflicts": [c(inputs) if callable(c) else c for c in conflicts]}
    return out


def answer_each(fn):
    """A round answering each open objection with ``fn(inputs, objection)`` (a response dict or None)."""
    def out(inputs):
        return {"responses": [r for o in inputs["open_objections"] if (r := fn(inputs, o)) is not None]}
    return out


def _synth_narrative(texts):
    def out(inputs):
        studies = inputs["studies"]
        return {"groups": [{"theme_or_outcome": "design", "item_ids": [s["item_id"] for s in studies],
                            "direction_summary": "Two designs.", "certainty_note": "Low."}],
                "findings": [{"text": t, "kind": "fact",
                              "evidence": [{"note_ids": [studies[i % len(studies)]["notes"][0]["id"]]}]}
                             for i, t in enumerate(texts)],
                "representativeness": (f"The {inputs['included_k']} included studies were selected by the protocol's "
                                       f"eligibility criteria."),
                "heterogeneity_note": "Designs differed.", "swim": None}
    return out


def _synthesize(env, texts=(F1, F2)) -> list[str]:
    """A passed synthesize attempt 1 with one supported finding per text (study A: rct, study B: cohort)."""
    k, ps, lib = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_synth_narrative(texts)],
               **_judges()})
    record_attempt(ps, "synthesize", 1, run_id=None, guidance="", requested=None, actor="tester")
    assert SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize")).status == "done"
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    return synthesis_claims(ps, 1)


def _ctx(env, ex, attempt: int = 1):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="argue", attempt=attempt)


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


def _claims(ps, attempt: int = 1) -> list[dict]:
    svc = ArgumentService(ps)
    return [svc.get_claim(c) for c in argument_claims(ps, attempt)]


def _objections(ps) -> list[dict]:
    return ArgumentService(ps).list_objections()


def _rewrite(ps, cid: str) -> str:
    (new,) = [c["id"] for c in ArgumentService(ps).list_claims(origin=("argument", "synthesis"))
              if c.get("narrowed_from") == cid]
    return new


# ---- tests ----
def test_argue_is_a_default_worker():
    assert isinstance(default_workers()["argue"], ArgueWorker)


def test_skeptic_tools_and_role_prompts():
    for name in SKEPTIC_TOOLS:
        assert "skeptic" in tool_roles(name), name
    for name in ("rk_claim_add", "rk_evidence_link", "rk_edge_add"):
        assert "arguer" in tool_roles(name)
    skeptic = build_system_prompt("skeptic", Critique)
    assert "`Critique`" in skeptic and "`RecheckOut`" in skeptic and "warrant_tests" in skeptic
    arguer = build_system_prompt("arguer", ArgumentDraft)
    assert "`ArgumentDraft`" in arguer and "answers_rq" in arguer and "needs_synthesis" in arguer


def test_argument_round_stores_claims_edges_and_verifies(env):
    _, ps, _ = env
    synth = _synthesize(env)

    def first(inputs):
        assert [c["claim_id"] for c in inputs["synthesis"]] == synth  # synthesize's passed attempt only
        out = draft()(inputs)
        out["reasons"].append({"claim_id": synth[1], "supports": "r1", "warrant": WARRANT})  # reused, not restated
        return out

    ex = _exec([first], [critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    svc = ArgumentService(ps)
    main, reason = _claims(ps)
    assert main["id"] == main_claim(ps, 1)
    assert [main["text"], reason["text"]] == [MAIN, REASON]
    for c in (main, reason):
        assert c["origin"] == "argument" and c["status"] == "supported" and c["generator_vendor"] == "anthropic"
        verifiers = {r["verifier"] for r in ps.db.execute("SELECT verifier FROM verifications WHERE target_id=?",
                                                          (c["id"],))}
        assert {"V-SUPPORT", "V-NUM", "V-HEDGE"} <= verifiers
    reasons = svc.list_edges(type="reason_of")
    assert {(e["from_id"], e["to_id"]) for e in reasons} == {(reason["id"], main["id"]), (synth[1], reason["id"])}
    for e in reasons:
        (wr,) = svc.list_edges(to_id=e["id"], type="warrant_of")
        assert wr["warrant"] == WARRANT and all(t["ok"] for t in wr["warrant_tests"].values())
        wc = svc.get_claim(wr["from_id"])
        assert wc["text"].startswith("When a study enrolled older people") and wc["origin"] == "warrant"
    # warrant claims stay out of the argument's listings (rk_claim_list's default)
    assert {c["id"] for c in svc.list_claims()} == {main["id"], reason["id"]}
    assert {e["to_id"] for e in svc.list_edges(type="evidence_of")} == {main["id"], reason["id"]}
    assert all(e["from_id"].startswith("evd_") for e in svc.list_edges(type="evidence_of"))
    # the Skeptic is the codex vendor by default, with search tools; it writes no objection itself
    (sk,) = ex.of("skeptic", "Critique")
    assert ex.drivers["skeptic"].startswith("codex_cli")
    assert {"rk_search_works", "rk_source_add", "rk_claim_list"} <= set(sk["tools"])
    assert "rk_objection_add" not in sk["tools"]
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_failed_warrant_test_becomes_priority1_objection(env):
    _, ps, _ = env
    _synthesize(env)

    def answer(inputs):
        (o,) = inputs["open_objections"]
        assert (o["category"], o["priority"]) == ("soundness", 1) and "true fails" in o["text"]
        return responses(RESPONSE)(inputs)

    ex = _exec([draft(), answer], [critique(fail="true"), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    svc = ArgumentService(ps)
    (wr,) = svc.list_edges(type="warrant_of")
    assert wr["warrant_tests"]["true"] == {"ok": False, "reason": "true fails"}
    (o,) = _objections(ps)
    assert (o["target_id"], o["category"], o["priority"]) == (wr["id"], "soundness", 1)
    assert o["status"] == "responded" and svc.get_claim(o["response_claim_id"])["status"] == "supported"
    assert len(ex.of("skeptic", "Critique")) == 2
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_failed_warrant_test_unanswered_fails_gate(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses(), responses()], [critique(fail="limited"), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (wr,) = ArgumentService(ps).list_edges(type="warrant_of")
    g = w.gate(ps, 1)
    assert not g.ok and any(wr["id"] in f and "warrant" in f for f in g.failures), g.failures


def test_objections_must_be_answered_for_gate(env):
    _, ps, _ = env
    _synthesize(env)
    item = usable_evidence(ps, 1)[0]["item_id"]

    def o1(inputs):
        return {"target_id": role(inputs, "reason")[0], "category": "counterexample",
                "text": "Study A also enrolled younger people.", "priority": 1, "source_id": item}

    def o2(inputs):
        return {"target_id": role(inputs, "main")[0], "category": "alternative", "priority": 2,
                "text": "The cohort may not have used aspirin.", "source_id": "itm_not_in_library"}

    # a priority-1 objection cannot be rejected as invalid, and a rejection needs its reason
    ex = _exec([draft(), responses("rejected_invalid", "rejected_invalid"), responses()],
               [critique(objections=[o1, o2]), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    first, second = _objections(ps)
    assert first["source_id"] == item and second["source_id"] is None  # a source must be in the library
    (crit, _) = argue_notes(ps, 1, kind="critique")
    assert crit["sources_dropped"] == [{"objection_id": second["id"], "source_id": "itm_not_in_library"}]
    assert first["status"] == second["status"] == "open"
    g = w.gate(ps, 1)
    assert not g.ok
    assert any(first["id"] in f for f in g.failures) and any(second["id"] in f for f in g.failures), g.failures
    svc = ArgumentService(ps)
    svc.respond_objection(first["id"], response_claim_id=None, status="conceded", note="Narrower population.",
                          actor="arguer")
    svc.respond_objection(second["id"], response_claim_id=None, status="rejected_invalid",
                          note="The cohort's notes name aspirin.", actor="arguer")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    lim = argue_limitations(ps, 1)
    assert [o["objection_id"] for o in lim["conceded"]] == [first["id"]]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_contradicting_claims_open_evidence_conflict(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    ex = _exec([draft(), responses(RESPONSE, RESPONSE)],
               [critique(conflicts=[conflict]), critique(conflicts=[conflict])])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "evidence_conflict" and cp["stage"] == "argue"
    assert cp["options"]["choices"] == ["a_holds", "b_holds", "both_contested"]
    assert (cp["options"]["claim_a"], cp["options"]["claim_b"]) == (a, b) and F1 in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "both_contested"}))
    # the rounds go on: the Arguer answers both objections, the Skeptic repeats the conflict (one checkpoint only)
    assert out.status == "waiting_human"
    final = get_checkpoint(ps, out.checkpoint_id)
    assert final["kind"] == "uncertain_support" and MAIN in final["prompt"]
    assert len([c for c in list_checkpoints(ps) if c["kind"] == "evidence_conflict"]) == 1
    svc = ArgumentService(ps)
    for cid in (a, b):
        c = svc.get_claim(cid)
        assert c["contested"] and c["status"] == "supported"
        (o,) = svc.list_objections(target_id=cid)
        assert o["priority"] == 2 and o["status"] == "responded"
    out = w.on_answer(_ctx(env, ex), _answer(ps, final["id"], {"claims": {}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]  # P2 never votes
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_p1_conflict_vote_marks_both_contested(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    ex = _exec([draft(), responses(RESPONSE, RESPONSE)], [critique(conflicts=[conflict]), critique()],
               votes=_votes(["a_holds"], ["b_holds"]))
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not list_checkpoints(ps, "open")  # P1 never asks
    (v,) = [x for x in votes_on(ps, "evidence_conflict", f"argue:1:{a}:{b}")]
    assert v["decision"] == "both_contested" and v["conservative"]
    svc = ArgumentService(ps)
    for cid in (a, b):
        c = svc.get_claim(cid)
        assert c["contested"] and c["status"] == "supported"
        (o,) = svc.list_objections(target_id=cid)
        assert o["status"] == "responded"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_p1_uncertain_claim_is_narrowed_or_withdrawn(env):
    _, ps, _ = env
    _synthesize(env)
    sure = "Older people certainly were enrolled in a randomised trial."
    plain = "Cohort studies plainly enrolled older people."
    narrower = "Some older people were enrolled in a randomised trial."

    def reasons(inputs):
        return [{"ref": "r1", "claim": claim(inputs, sure, "rct"), "supports": "main", "warrant": WARRANT},
                {"ref": "r2", "claim": claim(inputs, plain, "cohort"), "supports": "main", "warrant": WARRANT}]

    ex = _exec([draft(reasons=reasons)], [critique()], uncertain=("certainly", "plainly"),
               narrowed=[{"text": narrower, "kind": "fact"}, {"text": "Cohort studies plainly enrolled some older "
                                                                       "people.", "kind": "fact"}],
               rechecks=[lambda inputs: {"warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()}
                                                           for w in inputs["untested_warrants"]]}])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    g = w.gate(ps, 1)
    assert not g.ok and sum("status uncertain" in f for f in g.failures) == 2, g.failures
    assert w.degrade(ctx, g.failures).status == "done"
    svc = ArgumentService(ps)
    by_text = {c["text"]: c for c in _claims(ps)}
    old1, new1 = by_text[sure], by_text[narrower]
    assert old1["status"] == "withdrawn" and new1["narrowed_from"] == old1["id"] and new1["status"] == "supported"
    assert by_text[plain]["status"] == "withdrawn"
    # the rewrite takes over the original's reason link and its warrant, re-tested by the Skeptic
    (link,) = svc.list_edges(from_id=new1["id"], type="reason_of")
    assert link["to_id"] == main_claim(ps, 1)
    (wr,) = svc.list_edges(to_id=link["id"], type="warrant_of")
    assert wr["warrant"] == WARRANT and wr["warrant_tests"]
    # never voted up: the only vote is the RQ check (P1, two judges)
    assert [r[0] for r in ps.db.execute("SELECT subject_type FROM votes")] == ["argue_rq"]
    assert len([c for c in ex.calls if c["schema"] == "VoteOut"]) == 2
    lim = argue_limitations(ps, 1)["claims"]
    assert {c["claim_id"] for c in lim["withdrawn"]} == {old1["id"], by_text[plain]["id"],
                                                         _rewrite(ps, by_text[plain]["id"])}
    assert {c["claim_id"] for c in lim["narrowed"]} == {new1["id"], _rewrite(ps, by_text[plain]["id"])}
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_rewrite_is_rechecked_against_the_objections_it_conceded(env):
    _, ps, _ = env
    _synthesize(env)
    narrower = "Some older people were enrolled in a randomised trial."

    def o1(inputs):
        return {"target_id": role(inputs, "reason")[0], "category": "counterexample", "priority": 1,
                "text": "Study A enrolled people under 65."}

    def holds(inputs):
        (o,) = inputs["conceded_objections"]
        assert inputs["rewrite"]["text"] == narrower
        return {"still_holds": [{"objection_id": o["id"], "reason": "Some is still under 65."}],
                "warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()} for w in inputs["untested_warrants"]]}

    ex = _exec([draft(), responses(), responses()], [critique(objections=[o1]), critique()],
               narrowed=[{"text": narrower, "kind": "fact"}], rechecks=[holds])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    g = w.gate(ps, 1)
    assert not g.ok
    assert w.degrade(ctx, g.failures).status == "done"
    by_text = {c["text"]: c for c in _claims(ps)}
    assert by_text[REASON]["status"] == by_text[narrower]["status"] == "withdrawn"
    assert "conceded" in by_text[narrower]["withdrawn_reason"]
    assert all(o["status"] == "conceded" for o in _objections(ps))
    assert len(argue_limitations(ps, 1)["conceded"]) == 2
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_skeptic_fixed_point_ends_rounds(env):
    _, ps, _ = env
    _synthesize(env)

    def objection(priority: int):
        def o(inputs):
            return {"target_id": role(inputs, "main")[0], "category": "problem", "priority": priority,
                    "text": f"A priority-{priority} problem."}
        return o

    # round 1 raises a priority-2 objection (another round); round 2 only a priority-3 one: the fixed point; the
    # Arguer answers what is left in a closing task without another critique
    ex = _exec([draft(), responses(RESPONSE), responses(("rejected_invalid", "It is about another drug."))],
               [critique(objections=[objection(2)]), critique(objections=[objection(3)])])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("skeptic", "Critique")) == 2 and len(ex.of("arguer", "ArgumentDraft")) == 3
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    assert [o["status"] for o in _objections(ps)] == ["responded", "rejected_invalid"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_budget_reached_with_open_priority1_objection_fails_gate(env):
    _, ps, _ = env
    _synthesize(env)

    def objection(inputs):
        return {"target_id": role(inputs, "main")[0], "category": "problem", "priority": 1,
                "text": f"Problem {len(inputs['argument']['objections'])}."}

    ex = _exec([draft(), responses(), responses()], [critique(objections=[objection])] * 3)
    w = ArgueWorker(budget=8)
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.calls) <= 8
    (end,) = argue_notes(ps, 1, kind="end")
    assert end["reason"] == "budget"
    g = w.gate(ps, 1)
    assert not g.ok and any("budget" in f and "priority-1" in f for f in g.failures), g.failures


def test_main_claim_not_answering_rq_goes_back_to_question(env):
    _, ps, _ = env
    _synthesize(env)
    assert ("argue", "question") in BACK_EDGES
    w = ArgueWorker()
    # the Arguer's own check
    out = w.run(_ctx(env, _exec([draft(answers={"ok": False, "reason": "It is about trials, not timing."})])))
    assert (out.status, out.to) == ("back_edge", "question")
    assert "It is about trials, not timing." in out.reason
    assert not _claims(ps)
    # the P1 judge vote, conservative: does not answer
    ex = _exec([draft()], [critique()], votes=_votes(["does_not_answer"], ["answers"]))
    out = w.run(_ctx(env, ex, 2))
    assert (out.status, out.to) == ("back_edge", "question")
    assert "does not answer" in out.reason
    (check,) = argue_notes(ps, 2, kind="rq_check")
    assert check["decision"] == "does_not_answer" and check["by"] == "vote"
    assert any("research question" in f for f in w.gate(ps, 2).failures)


def test_argue_back_edge_to_synthesize_with_guidance(env):
    _, ps, _ = env
    _synthesize(env)
    assert ("argue", "synthesize") in BACK_EDGES
    need = "A finding on bleeding risk in people over 80."
    out = ArgueWorker().run(_ctx(env, _exec([draft(needs_synthesis=need)])))
    assert (out.status, out.to, out.reason) == ("back_edge", "synthesize", need)
    assert not _claims(ps)
    (n,) = argue_notes(ps, 1, kind="back_edge")
    assert (n["to"], n["reason"]) == ("synthesize", need)


# ---- fix round 1 ----
def _o(role_name: str, priority: int, text: str, category: str = "problem"):
    def o(inputs):
        return {"target_id": role(inputs, role_name)[0], "category": category, "priority": priority, "text": text}
    return o


def test_rewrite_is_rechecked_against_every_unanswered_objection(env):
    _, ps, _ = env
    _synthesize(env)
    sure = "Older people certainly were enrolled in a randomised trial."
    narrower = "Some older people were enrolled in a randomised trial."

    def reasons(inputs):
        return [{"ref": "r1", "claim": claim(inputs, sure, "rct"), "supports": "main", "warrant": WARRANT}]

    def holds(inputs):
        (o,) = inputs["conceded_objections"]  # the open priority-2 objection, conceded before the re-check
        assert (o["priority"], o["text"]) == (2, "The trial was small.")
        return {"still_holds": [{"objection_id": o["id"], "reason": "Still small."}],
                "warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()} for w in inputs["untested_warrants"]]}

    ex = _exec([draft(reasons=reasons), responses(), responses()],
               [critique(objections=[_o("reason", 2, "The trial was small.")]), critique()], uncertain=("certainly",),
               narrowed=[{"text": narrower, "kind": "fact"}], rechecks=[holds])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    assert w.degrade(ctx, w.gate(ps, 1).failures).status == "done"
    by_text = {c["text"]: c for c in _claims(ps)}
    assert by_text[sure]["status"] == by_text[narrower]["status"] == "withdrawn"
    assert all(o["status"] == "conceded" for o in _objections(ps))
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_rewrite_of_a_contested_claim_is_rechecked_against_the_conflict(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    narrower = "Aspirin may have been studied in a randomised trial of some older people."

    def holds(inputs):
        texts = [o["text"] for o in inputs["conceded_objections"]]
        assert any("contradicts" in t for t in texts), texts
        return {"still_holds": [{"objection_id": o["id"], "reason": "Still contradicts."}
                                for o in inputs["conceded_objections"]]}

    # the Arguer answers b's conflict objection only; a stays contested without an answer
    def only_b(inputs, o):
        return ({"objection_id": o["id"], "status": "responded", "claim": claim(inputs, RESPONSE, "cohort")}
                if o["target_id"] == b else None)

    ex = _exec([draft(), answer_each(only_b), answer_each(lambda i, o: None)],
               [critique(conflicts=[conflict]), critique()], votes=_votes(["a_holds"], ["b_holds"]),
               narrowed=[{"text": narrower, "kind": "fact"}], rechecks=[holds])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    # a's open priority-2 conflict objection is no claim problem: narrowing only follows a gate failure of the claim
    ArgumentService(ps).add_objection(ObjectionIn(target_id=a, category="problem", priority=1, text="Too broad."),
                                      actor="tester")
    w.degrade(ctx, w.gate(ps, 1).failures)
    assert len(ex.of("skeptic", "RecheckOut")) == 1
    new = _rewrite(ps, a)
    assert ArgumentService(ps).get_claim(new)["status"] == "withdrawn"


def test_a_claim_cannot_answer_an_objection_against_itself(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}

    def itself(inputs, o):  # a answers its own objection; b's is answered by its counterpart a
        return {"objection_id": o["id"], "status": "responded", "claim_id": a}

    ex = _exec([draft(), answer_each(itself), answer_each(lambda i, o: None)],
               [critique(conflicts=[conflict]), critique()], votes=_votes(["a_holds"], ["b_holds"]))
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (d2,) = [n for n in argue_notes(ps, 1, kind="draft") if n["round"] == 2]
    assert len(d2["refused"]) == 2 and all("its own target" in r or "counterpart" in r for r in d2["refused"])
    assert all(o["status"] == "open" for o in _objections(ps))
    # the gate rejects a self-answer recorded by any other path
    svc = ArgumentService(ps)
    (oa,) = svc.list_objections(target_id=a)
    svc.respond_objection(oa["id"], response_claim_id=a, status="responded", note="self", actor="tester")
    assert any(oa["id"] in f and "own target" in f for f in w.gate(ps, 1).failures), w.gate(ps, 1).failures


def test_untested_warrants_get_another_critique_before_the_fixed_point(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft()], [critique(test=False), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("skeptic", "Critique")) == 2 and len(ex.of("arguer", "ArgumentDraft")) == 1
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_contests_and_objections_are_scoped_to_the_attempt(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}

    def conflicts_only(inputs, o):
        return ({"objection_id": o["id"], "status": "responded", "claim": claim(inputs, RESPONSE, "cohort")}
                if o["category"] == "counterexample" else None)

    ex1 = _exec([draft(), answer_each(conflicts_only), answer_each(lambda i, o: None)],
                [critique(objections=[{"target_id": a, "category": "problem", "priority": 2,
                                       "text": "The trial was small."}], conflicts=[conflict]), critique()],
                votes=_votes(["a_holds"], ["b_holds"]))
    w = ArgueWorker()
    assert w.run(_ctx(env, ex1)).status == "done"
    (small,) = [o for o in _objections(ps) if o["text"] == "The trial was small."]
    assert small["status"] == "open" and not w.gate(ps, 1).ok
    # attempt 2 (a rerun): the earlier unanswered objection and the contests come along as the attempt's own
    seen = {}

    def first(inputs):
        seen["open"] = [(o["target_id"], o["category"]) for o in inputs["open_objections"]]
        out = draft()(inputs)
        out["responses"] = [{"objection_id": o["id"], "status": "responded", "claim": claim(inputs, RESPONSE, "cohort")}
                            for o in inputs["open_objections"]]
        return out

    ex2 = _exec([first], [critique()])
    assert w.run(_ctx(env, ex2, 2)).status == "done"
    assert sorted(seen["open"]) == sorted([(a, "problem"), (a, "counterexample"), (b, "counterexample")])
    (imp,) = argue_notes(ps, 2, kind="imported")
    assert small["id"] in imp["objection_ids"]
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures
    # without answers in attempt 3 the earlier answers do not count
    ex3 = _exec([draft(), responses()], [critique()])
    assert w.run(_ctx(env, ex3, 3)).status == "done"
    g = w.gate(ps, 3)
    assert any(f"contested claim {a}" in f for f in g.failures), g.failures


def test_conceding_a_failed_warrant_drops_its_link(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses("conceded")], [critique(fail="true"), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (wr,) = ArgumentService(ps).list_edges(type="warrant_of")
    (dropped,) = argue_notes(ps, 1, kind="dropped_link")
    assert dropped["edge_id"] == wr["to_id"] and "conceded" in dropped["reason"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_conflict_objections_need_a_supported_response(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    ex = _exec([draft(), responses("conceded", ("rejected_invalid", "Not a conflict.")), responses()],
               [critique(conflicts=[conflict]), critique()], votes=_votes(["a_holds"], ["b_holds"]))
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (d2,) = [n for n in argue_notes(ps, 1, kind="draft") if n["round"] == 2]
    assert len(d2["refused"]) == 2 and all("supported response" in r for r in d2["refused"])
    assert all(o["status"] == "open" for o in _objections(ps))


def test_reason_cycles_are_refused(env):
    _, ps, _ = env
    _synthesize(env)

    def reasons(inputs):
        return [{"ref": "r1", "claim": claim(inputs, REASON, "rct"), "supports": "r2", "warrant": WARRANT},
                {"ref": "r2", "claim": claim(inputs, RESPONSE, "cohort"), "supports": "r1", "warrant": WARRANT}]

    ex = _exec([draft(reasons=reasons)], [critique()])
    assert ArgueWorker().run(_ctx(env, ex)).status == "done"
    assert len(ArgumentService(ps).list_edges(type="reason_of")) == 1
    (d,) = argue_notes(ps, 1, kind="draft")
    assert any("cycle" in r for r in d["refused"])


def test_output_models_forbid_extra_fields_and_dropped_tests_are_noted(env):
    _, ps, _ = env
    _synthesize(env)
    for model, data in ((ArgumentDraft, {"surprise": 1}), (Critique, {"surprise": 1}),
                        (RecheckOut, {"surprise": 1})):
        with pytest.raises(ValidationError):
            model.model_validate(data)
    ex = _exec([draft()], [critique(extra_tests=["edg_unknown"])])
    assert ArgueWorker().run(_ctx(env, ex)).status == "done"
    (crit,) = argue_notes(ps, 1, kind="critique")
    assert crit["tests_dropped"] == [{"edge_id": "edg_unknown", "reason": "not an untested warrant of the argument"}]


def test_budget_estimate_counts_the_pending_verifications(env):
    _, ps, _ = env
    _synthesize(env)
    objections = [_o("main", 2, f"Problem {i}.") for i in range(3)]
    ex = _exec([draft(), responses(RESPONSE, RESPONSE, RESPONSE)], [critique(objections=objections), critique()])
    w = ArgueWorker(budget=15)  # round 1 costs 6; three answers need 2 + 3 x 3 judges more, plus the final vote
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("arguer", "ArgumentDraft")) == 1
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["budget"]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_final_review_is_not_opened_when_the_gate_must_fail(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses(), responses()], [critique(objections=[_o("main", 1, "Wrong.")]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done" and "Wrong" not in out.reason and "gate" in out.reason, out.reason
    assert not [c for c in list_checkpoints(ps) if c["kind"] == "uncertain_support"]
    assert not w.gate(ps, 1).ok


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_rate_cap_mid_verification_resumes_without_orphans(env):
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}

    class Capped(Exec):
        armed = False

        def run(self, role, instructions, inputs, output_schema, **kw):
            if self.armed and role == "judge":
                self.armed = False
                raise RateCapped("codex_cli", 60)
            return super().run(role, instructions, inputs, output_schema, **kw)

    ex = Capped({**_exec([draft(), responses(RESPONSE, RESPONSE)], [critique(conflicts=[conflict]), critique()]).script})
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    _answer(ps, cp["id"], {"decision": "both_contested"})
    ex.armed = True
    with pytest.raises(RateCapped):
        w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"]))
    out = w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"]))
    assert out.status == "waiting_human"
    assert len(ex.of("arguer", "ArgumentDraft")) == 2
    claims = _claims(ps)
    assert len(claims) == 4 and all(c["status"] == "supported" for c in claims)
