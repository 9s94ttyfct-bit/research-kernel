"""Plan 5b Task 5: the argue stage. The Arguer builds an argument on synthesize's supported findings, the Skeptic
attacks it round after round until the fixed point (or the open-stage budget); the kernel stores and verifies every
claim, edge, warrant test and objection, resolves contradictions (P2 asks, P1 votes) and gates on what it stored."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from rk.argument import ArgumentService, ObjectionIn
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.ids import new_id
from rk.llm.chat.base import ToolCall, Turn
from rk.llm.chat_provider import ChatApiProvider
from rk.llm.types import TaskResult
from rk.net.http import BudgetExhausted
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES, EDGE_LIMIT
from rk.orchestrator.state import (
    current_stage,
    edge_rows,
    latest_attempt,
    record_attempt,
    record_edge_taken,
    record_fixed_stages,
    record_passed,
    record_ran,
    record_run_session,
    record_run_task,
    run_row,
    stage_row,
    start_run,
    tasks_used,
)
from rk.production.argue import (
    RQ_QUESTION,
    SKEPTIC_TOOLS,
    SYNTHESIS_QUESTION,
    ArgueWorker,
    ArgumentDraft,
    Critique,
    RecheckOut,
    argue_limitations,
    argue_notes,
    argument_claims,
    argument_objection_ids,
    main_claim,
    synthesis_requests,
)
from rk.production.argue import _load as argue_inputs
from rk.production.extraction import usable_evidence
from rk.production.models import SynthesisPlan
from rk.production.synthesis import SynthesizeWorker, _revised, synthesis_claims
from rk.roles.policy import tool_roles
from rk.roles.prompts import build_system_prompt
from rk.tools import builtin
from rk.tools.builtin import build_registry
from tests.production_fakes import (
    make_frozen_project,
    production_ctx,
    stage_run,
    votes_on,
)
from tests.test_agent_loop import ScriptedBackend
from tests.test_synthesis import DESIGN, CapOnce, Exec, _setup, assess, first_passage

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
        return {"label": label, "quoted_spans": [first_passage(inputs["evidence"])]}
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
                "heterogeneity_note": "Designs differed.",
                "swim": {i: f"The {i.replace('_', ' ')} is described." for i in inputs["swim_items"]} or None}
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
    assert ex.drivers["skeptic"].startswith("omp_gateway:openai-codex/")
    assert {"rk_search_works", "rk_source_add", "rk_claim_list"} <= set(sk["tools"])
    assert "rk_objection_add" not in sk["tools"]
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_a_reason_restating_a_supported_finding_reuses_it(env):
    """Calibration (critical review rerun, argue attempt 11): the Arguer copied supported synthesis findings word for
    word as new reasons; stored as new claims and judged again, the researcher's verdicts were lost. A new claim with
    a reusable claim's text (whitespace and case folded) citing only its notes reuses that claim by id: not stored,
    not judged again. One citing other notes stays a new claim, its evidence kept."""
    _, ps, _ = env
    synth = _synthesize(env)
    copy = "  " + F2.upper().replace(" ", "  ")

    def first(inputs):
        out = draft()(inputs)
        out["reasons"] += [{"claim": claim(inputs, copy, "cohort"), "supports": "r1", "warrant": WARRANT},
                           {"claim": claim(inputs, F1, "cohort"), "supports": "r1", "warrant": WARRANT},
                           # restated, but refused (it supports nothing stored): not noted as restated
                           {"claim": claim(inputs, F2, "cohort"), "supports": "nowhere", "warrant": WARRANT}]
        return out

    ex = _exec([first], [critique()])
    out = ArgueWorker().run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert [c["text"] for c in _claims(ps)] == [MAIN, REASON, F1]  # no copy of F2; F1 on the cohort note is new
    other = next(c for c in _claims(ps) if c["text"] == F1)
    assert [e["note_ids"] for e in other["evidence"]] != [e["note_ids"] for e in
                                                          ArgumentService(ps).get_claim(synth[0])["evidence"]]
    reason = next(c["id"] for c in _claims(ps) if c["text"] == REASON)
    assert (synth[1], reason) in {(e["from_id"], e["to_id"]) for e in ArgumentService(ps).list_edges(type="reason_of")}
    assert not [c for c in ex.calls if c["role"].startswith("judge") and "cohort" in c["inputs"].get("claim", "")]
    (note,) = argue_notes(ps, 1, kind="draft")
    assert note["restated"] == [{"text": copy, "claim_id": synth[1]}] and synth[1] in note["reused"]


def test_a_restatement_reuses_the_same_text_claim_whose_notes_cover_it(env):
    """Review of 46b3423: two supported findings with the same text on different notes. A restatement citing the
    first one's note reuses that one (every same-text claim is tried, not only the last)."""
    _, ps, _ = env
    first_f, second_f = _synthesize(env, texts=(F2, F2))  # the same text: study A's (rct) note, study B's (cohort)
    svc = ArgumentService(ps)
    assert [svc.get_claim(c)["status"] for c in (first_f, second_f)] == ["supported"] * 2  # both reusable

    def first(inputs):
        out = draft()(inputs)
        out["reasons"].append({"claim": claim(inputs, F2, "rct"), "supports": "r1", "warrant": WARRANT})
        return out

    out = ArgueWorker().run(_ctx(env, _exec([first], [critique()])))
    assert out.status == "done", out.reason
    (note,) = argue_notes(ps, 1, kind="draft")
    assert note["restated"] == [{"text": F2, "claim_id": first_f}]


def test_an_answer_to_an_objection_the_turn_already_conceded_is_refused_not_a_crash(env):
    """Task 9 (melatonin): the Arguer answers objection A again with a new claim, which withdraws A's earlier response
    claim P and concedes the objection B on P; the same turn also answers B. That answer is refused (noted), and the
    stage goes on: it never crashes on the final concession."""
    _, ps, _ = env
    _synthesize(env)
    first = "The trial enrolled people over 80."
    again = "Older people were enrolled in a cohort of patients."
    on_reason = {"category": "problem", "priority": 2, "text": "Which ages?"}

    def a_on_reason(inputs):
        (r,) = role(inputs, "reason")
        return {"target_id": r, **on_reason}

    def b_on_p(inputs):
        (p,) = [c["id"] for c in inputs["argument"]["claims"] if c["text"] == first]
        return {"target_id": p, "category": "problem", "priority": 2, "text": "Over 80 is not what the trial says."}

    def round3(inputs, o):
        if o["text"] == on_reason["text"]:
            return {"objection_id": o["id"], "status": "responded", "claim": claim(inputs, again, "cohort")}
        return {"objection_id": o["id"], "status": "conceded", "note": "It does not."}

    ex = _exec([draft(), responses(first), answer_each(round3), answer_each(lambda i, o: None)],
               [critique(objections=[a_on_reason]), critique(objections=[b_on_p]), critique(), critique()],
               uncertain=(first,))
    w = ArgueWorker()
    w.run(_ctx(env, ex))  # no ValueError from the final concession
    by_text = {o["text"]: o for o in _objections(ps)}
    b = by_text["Over 80 is not what the trial says."]
    assert b["status"] == "conceded" and "replaced and withdrawn" in b["note"]  # the kernel's concession stands
    refused = [r for n in argue_notes(ps, 1, kind="draft") for r in n["refused"]]
    assert any(b["id"] in r and "not an open objection" in r for r in refused), refused
    assert by_text[on_reason["text"]]["status"] == "responded"


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
    # a conceded priority-1 objection holds: its target fails until it is narrowed or withdrawn (I6)
    reason = first["target_id"]
    assert w.gate(ps, 1).failures == [f"{reason}: conceded priority-1 objection {first['id']}"]
    svc.withdraw_claim(reason, reason="the objection holds", actor="tester")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    lim = argue_limitations(ps, 1)
    assert [o["objection_id"] for o in lim["claims"]["conceded"]] == [first["id"]]


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
    assert len(argue_limitations(ps, 1)["claims"]["conceded"]) == 2
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
    w = ArgueWorker(budget=3)
    assert w.run(_ctx(env, ex)).status == "done"
    assert len([c for c in ex.calls if not c["role"].startswith("judge")]) <= 3  # the judges' tasks do not count
    (end,) = argue_notes(ps, 1, kind="end")
    assert end["reason"] == "budget"
    g = w.gate(ps, 1)
    assert not g.ok and any("budget" in f and "priority-1" in f for f in g.failures), g.failures


def test_main_claim_not_answering_rq_goes_back_to_question(env):
    _, ps, _ = env
    _synthesize(env)
    assert ("argue", "question") in BACK_EDGES
    w = ArgueWorker()
    # the P1 judge vote, conservative: does not answer
    ex = _exec([draft()], [critique()], votes=_votes(["does_not_answer"], ["answers"]))
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "question")
    assert "does not answer" in out.reason
    (check,) = argue_notes(ps, 1, kind="rq_check")
    assert check["decision"] == "does_not_answer" and check["by"] == "vote"
    assert any("research question" in f for f in w.gate(ps, 1).failures)


SEED = "the reproduction seed (its question is the published review's)"


def test_in_a_seeded_project_a_main_claim_not_answering_the_rq_goes_back_to_synthesize(env):
    """Calibration (seeded reproduction, 2026-11-13): the question is fixed, so the P1 RQ vote's ``does_not_answer``
    goes back to synthesize, with the judges' reasons as its guidance; a resumed attempt returns the same back-edge."""
    _, ps, _ = env
    _synthesize(env)
    record_fixed_stages(ps, ["gaps", "topics", "question"], SEED, actor="tester")
    w = ArgueWorker()
    ex = _exec([draft()], [critique()], votes=_votes(["does_not_answer"], ["answers"]))
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "synthesize")
    (check,) = argue_notes(ps, 1, kind="rq_check")
    assert check["decision"] == "does_not_answer" and check["reason"]
    assert out.reason == ("the main claim does not answer the fixed research question (P1 vote, not reviewed by a "
                          f"human): {check['reason']}")
    (sent,) = argue_notes(ps, 1, kind="back_edge")
    assert (sent["to"], sent["reason"]) == ("synthesize", out.reason)
    again = w.run(_ctx(env, _exec()))
    assert (again.status, again.to, again.reason) == ("back_edge", "synthesize", out.reason)


def test_in_a_seeded_project_with_argue_to_synthesize_spent_the_stage_fails(env):
    _, ps, _ = env
    _synthesize(env)
    record_fixed_stages(ps, ["question"], SEED, actor="tester")
    record_edge_taken(ps, "argue->synthesize", EDGE_LIMIT, actor="tester")
    out = ArgueWorker().run(_ctx(env, _exec([draft()], [critique()], votes=_votes(["does_not_answer"], ["answers"]))))
    assert out.status == "failed"
    (check,) = argue_notes(ps, 1, kind="rq_check")
    assert out.reason == ("the main claim never answered the fixed research question; argue->synthesize has no use "
                          f"left: {check['reason']}")
    assert not argue_notes(ps, 1, kind="back_edge")



NOT_ANSWERED = {"ok": False, "reason": "It is about trials, not timing."}


def test_failed_self_check_in_p1_is_an_input_of_the_rq_vote(env):
    """I5: the Arguer's failed self-check does not send the project back to question by itself: round 1 is stored, the
    rounds go on, and the judges' RQ vote decides with the self-check among its inputs."""
    _, ps, _ = env
    _synthesize(env)
    w = ArgueWorker()
    ex = _exec([draft(answers=NOT_ANSWERED)], [critique()])
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert main_claim(ps, 1) is not None and not argue_notes(ps, 1, kind="back_edge")
    rq_votes = [c for c in ex.of("judge", "VoteOut") if c["inputs"]["subject"].get("main_claim")]
    assert rq_votes and rq_votes[0]["inputs"]["subject"]["arguer_self_check"] == NOT_ANSWERED
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures  # the vote said it answers
    # the vote's conservative choice still goes back to question
    out = w.run(_ctx(env, _exec([draft(answers=NOT_ANSWERED)], [critique()],
                                votes=_votes(["answers"], ["does_not_answer"])), 2))
    assert (out.status, out.to) == ("back_edge", "question")


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_failed_self_check_in_p2_reaches_your_final_review(env):
    _, ps, _ = env
    _synthesize(env)
    w = ArgueWorker()
    ex = _exec([draft(answers=NOT_ANSWERED)], [critique()])
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "uncertain_support" and cp["options"]["arguer_self_check"] == NOT_ANSWERED
    assert "It is about trials, not timing." in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures  # you decided: it answers


def test_failed_self_check_without_a_main_claim_reruns_with_it_as_guidance(env):
    _, ps, _ = env
    _synthesize(env)
    out = ArgueWorker().run(_ctx(env, _exec([lambda inputs: {"answers_rq": NOT_ANSWERED}] * 2)))  # asked once more
    assert out.status == "rerun" and "It is about trials, not timing." in out.reason
    assert not _claims(ps) and not argue_notes(ps, 1, kind="back_edge")


NEED = "A finding on bleeding risk in people over 80."


def test_needs_synthesis_is_noted_and_the_rounds_go_on(env):
    """Fix wave 4: ``needs_synthesis`` is an input, not a back-edge. The request is noted with its round, the rounds go
    on (critique, fixed point, end check), and at the end the P1 judges decide; they disagree here, so the conservative
    ``proceed`` holds and the unmet request is listed in the limitations."""
    _, ps, _ = env
    _synthesize(env)
    assert ("argue", "synthesize") in BACK_EDGES
    ex = _exec([draft(needs_synthesis=NEED)], [critique()], votes=_votes(["back_to_synthesize"], ["proceed"]))
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert synthesis_requests(ps, 1) == [{"round": 1, "text": NEED}]
    (first_draft,) = argue_notes(ps, 1, kind="draft")
    assert first_draft["needs_synthesis"] == NEED  # the request is part of the stored round
    assert not argue_notes(ps, 1, kind="back_edge")
    assert main_claim(ps, 1) is not None and len(ex.of("skeptic", "Critique")) == 1
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert (d["decision"], d["by"]) == ("proceed", "vote")
    (v,) = votes_on(ps, "argue_synthesis", "argue:1:synthesis")
    assert v["decision"] == "proceed" and v["conservative"]
    assert argue_limitations(ps, 1)["synthesis_requests"] == [{"round": 1, "text": NEED}]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # the Arguer is told to argue with the findings it has and what needs_synthesis is for
    (first,) = ex.of("arguer", "ArgumentDraft")
    assert "narrow the main claim" in first["instructions"] and "from the included studies" in first["instructions"]


def test_needs_synthesis_without_a_main_claim_reruns_with_the_request(env):
    _, ps, _ = env
    _synthesize(env)
    no_main = {"answers_rq": SELF_CHECK, "needs_synthesis": NEED}
    # asked once more, still no main claim: a rerun, with the request even though only the first answer made it
    out = ArgueWorker().run(_ctx(env, _exec([lambda inputs: no_main, lambda inputs: {"answers_rq": SELF_CHECK}])))
    assert out.status == "rerun" and NEED in out.reason and "no main claim (asked twice)" in out.reason
    assert not argue_notes(ps, 1, kind="back_edge")


def test_p1_vote_back_to_synthesize_takes_the_edge_with_the_requests(env):
    """The judges agree that synthesize can still give what the Arguer asked for: argue->synthesize is taken (counted
    against its limit), the requests and the judges' reasons as synthesize's guidance. The judges see the requests, the
    included studies (labels and designs), the extraction schema and the protocol's eligibility and date window."""
    k, ps, lib = env
    _synthesize(env)
    later = "A finding on the cohort's follow-up."

    def second(inputs):
        return {**responses(RESPONSE)(inputs), "needs_synthesis": later}

    ex = _exec([draft(needs_synthesis=NEED), second], [critique(objections=[_o("main", 2, "Too broad.")]), critique()],
               votes=_votes(["back_to_synthesize"], ["back_to_synthesize"]))
    o, run_id = stage_run(k, ps, lib, "argue", ex)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "synthesize" and edge_rows(ps)["argue->synthesize"]["count"] == 1
    assert synthesis_requests(ps, 1) == [{"round": 1, "text": NEED}, {"round": 2, "text": later}]
    guidance = stage_row(ps, "synthesize")["guidance"]
    assert NEED in guidance and later in guidance and "because back_to_synthesize" in guidance
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert (d["decision"], d["by"]) == ("back_to_synthesize", "vote")
    (call,) = ex.of("judge", "VoteOut")
    assert call["inputs"]["question"] == SYNTHESIS_QUESTION
    subject = call["inputs"]["subject"]
    assert subject["requests"] == [{"round": 1, "text": NEED}, {"round": 2, "text": later}]
    assert [(x["label"], x["design"]) for x in subject["included_studies"]] == [("A 1950", "rct"), ("B 1951", "cohort")]
    assert [f["name"] for f in subject["extraction_schema"]] == ["design"]
    assert subject["eligibility"]["include"] == [{"id": "I1", "text": "People over 80"}]
    assert subject["date_window"] == {"date_from": 2000, "date_to": None}
    # the RQ vote is not asked: the argument goes back to synthesize
    assert [c["inputs"]["question"] for c in ex.of("judge", "VoteOut")] == [SYNTHESIS_QUESTION]
    assert argue_limitations(ps, 1)["synthesis_requests"] == []  # acted on: not a limitation


def test_p1_synthesis_requests_proceed_without_a_vote_once_the_edge_is_spent(env):
    _, ps, _ = env
    _synthesize(env)
    record_edge_taken(ps, "argue->synthesize", EDGE_LIMIT, actor="tester")
    ex = _exec([draft(needs_synthesis=NEED)], [critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert (d["decision"], d["by"]) == ("proceed", "kernel") and "argue->synthesize" in d["reason"]
    assert [c["inputs"]["question"] for c in ex.of("judge", "VoteOut")] == [RQ_QUESTION]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("where", ["first_judge", "rq_vote", "before_the_note"])
def test_p1_synthesis_vote_interrupted_asks_the_judges_nothing_twice(env, monkeypatch, where):
    """A rate cap at the synthesis vote's first judge, or at the RQ vote after it, or a crash after the synthesis vote
    was recorded but before its decision was noted: the resumed attempt asks no judge a question twice (the recorded
    vote is reused) and the Arguer and the Skeptic nothing again."""
    import rk.production.argue as mod

    _, ps, _ = env
    _synthesize(env)
    script = _exec([draft(needs_synthesis=NEED)], [critique()], votes=_votes(["proceed"], ["proceed"])).script
    ex = CapOnce(script, at=("judge", "VoteOut"), n={"first_judge": 1, "rq_vote": 2}.get(where, 0))
    if where == "before_the_note":
        real = mod._note

        def crash(ps_, attempt, kind, **kw):
            if kind == "synthesis_decision":
                monkeypatch.setattr(mod, "_note", real)
                raise RateCapped("codex_cli", 60)
            return real(ps_, attempt, kind, **kw)

        monkeypatch.setattr(mod, "_note", crash)
    w = ArgueWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    for judge in ("judge", "judge_2"):
        assert [c["inputs"]["question"] for c in ex.of(judge, "VoteOut")] == [SYNTHESIS_QUESTION, RQ_QUESTION]
    assert len(ex.of("arguer", "ArgumentDraft")) == len(ex.of("skeptic", "Critique")) == 1
    assert len(votes_on(ps, "argue_synthesis", "argue:1:synthesis")) == 1
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert d["decision"] == "proceed" and len(synthesis_requests(ps, 1)) == 1


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_final_review_lists_the_requests_and_back_takes_the_edge(env):
    k, ps, lib = env
    ids = _synthesize(env)
    ex = _exec([draft(needs_synthesis=NEED)], [critique()])
    o, run_id = stage_run(k, ps, lib, "argue", ex)
    assert o.step("demo", run_id, actor="tester") == "paused_waiting_human"
    cp = get_checkpoint(ps, stage_row(ps, "argue")["waiting_checkpoint"])
    assert cp["kind"] == "uncertain_support"
    assert cp["options"]["synthesis_requests"] == [{"round": 1, "text": NEED}]
    assert cp["options"]["decisions"] == ["rerun", "back"] and cp["options"]["back_to"] == "synthesize"
    assert NEED in cp["prompt"] and '"decision": "back", "to": "synthesize"' in cp["prompt"]
    assert cp["options"]["revisable"] == {ids[0]: F1, ids[1]: F2} and f"- {ids[1]}: {F2}" in cp["prompt"]
    answer_checkpoint(ps, cp["id"], {"decision": "back", "to": "synthesize", "comment": "Use the subgroup table.",
                                     "revise": [ids[1]]}, answered_by="human", actor="researcher")
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "synthesize" and edge_rows(ps)["argue->synthesize"]["count"] == 1
    guidance = stage_row(ps, "synthesize")["guidance"]
    assert NEED in guidance and "Use the subgroup table." in guidance
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert (d["decision"], d["by"], d["checkpoint_id"]) == ("back", "human", cp["id"])
    (b,) = argue_notes(ps, 1, kind="back_edge")
    assert b["revise"] == [ids[1]] and _revised(ps) == {ids[1]}  # the next synthesize attempt writes F2 again
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]  # P2 never votes
    assert not argue_notes(ps, 1, kind="rq_check")  # nothing else of the answer is applied


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_back_is_applied_once_and_other_answers_still_work(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(needs_synthesis=NEED)], [critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    # back elsewhere is refused and the review opens again with the requests
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back", "to": "question"}))
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert "could not be applied" in cp["prompt"] and NEED in cp["prompt"]
    assert cp["options"]["synthesis_requests"] == [{"round": 1, "text": NEED}]
    # "revise" names only a supported finding of the synthesis argued on
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back", "to": "synthesize", "comment": "Why.",
                                                          "revise": ["clm_nope"]}))
    assert out.status == "waiting_human" and "not a supported finding" in out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    # findings to write again need your comment on how
    revisable = list(cp["options"]["revisable"])
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back", "to": "synthesize",
                                                          "revise": revisable[:1]}))
    assert out.status == "waiting_human" and "needs a comment" in out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    # "revise" goes with back only, never silently dropped from your review
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {}, "answers_rq": "yes", "revise": revisable[:1]}))
    assert out.status == "waiting_human" and "goes with back only" in out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    # your usual answer: the request is not acted on; it is an unmet request in the limitations
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    (d,) = argue_notes(ps, 1, kind="synthesis_decision")
    assert (d["decision"], d["by"]) == ("proceed", "human")
    assert argue_limitations(ps, 1)["synthesis_requests"] == [{"round": 1, "text": NEED}]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    # applied again (after a crash), the answer notes nothing twice
    assert w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"])).status == "done"
    assert len(argue_notes(ps, 1, kind="synthesis_decision")) == 1
    # a back answer applied again goes back once, noted once
    w2 = ArgueWorker()
    cp2 = get_checkpoint(ps, w2.run(_ctx(env, _exec([draft(needs_synthesis=NEED)], [critique()]), 2)).checkpoint_id)
    cp2 = _answer(ps, cp2["id"], {"decision": "back", "to": "synthesize"})
    for _ in range(2):
        out = w2.on_answer(_ctx(env, ex, 2), cp2)
        assert (out.status, out.to) == ("back_edge", "synthesize") and NEED in out.reason
    assert w2.run(_ctx(env, ex, 2)).status == "back_edge"  # resumed after the back-edge was noted
    assert len(argue_notes(ps, 2, kind="synthesis_decision")) == len(argue_notes(ps, 2, kind="back_edge")) == 1


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_back_is_not_offered_without_a_synthesis_request(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft()], [critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    assert cp["options"]["decisions"] == ["rerun"] and cp["options"]["synthesis_requests"] == []
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back", "to": "synthesize"}))
    assert out.status == "waiting_human"
    assert "asked synthesize for nothing" in get_checkpoint(ps, out.checkpoint_id)["prompt"]
    assert not argue_notes(ps, 1, kind="back_edge")


# ---- fix wave 4 minors ----
def test_a_round_interrupted_before_it_was_stored_leaves_no_stale_request(env, monkeypatch):
    """Minor 1: the request is part of the stored round (the draft note). A rate cap before round 1 was stored; asked
    again, the Arguer asks for nothing, so no request of the unstored answer is left."""
    _, ps, _ = env
    _synthesize(env)
    real = ArgueWorker._store

    def crash(self, *args, **kw):
        monkeypatch.setattr(ArgueWorker, "_store", real)
        raise RateCapped("codex_cli", 60)

    monkeypatch.setattr(ArgueWorker, "_store", crash)
    ex = _exec([draft(needs_synthesis=NEED), draft()], [critique()])
    w = ArgueWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("arguer", "ArgumentDraft")) == 2
    assert synthesis_requests(ps, 1) == [] and not argue_notes(ps, 1, kind="synthesis_decision")
    assert argue_limitations(ps, 1)["synthesis_requests"] == []
    assert [c["inputs"]["question"] for c in ex.of("judge", "VoteOut")] == [RQ_QUESTION]


class SkepticFails(Exec):
    """``Exec`` whose Skeptic task always fails."""

    def run(self, role, instructions, inputs, output_schema, **kw):
        if role == "skeptic":
            self.calls.append({"role": role, "schema": output_schema.__name__, "inputs": inputs})
            return TaskResult(new_id("tsk"), False, None, "", "openai", "x", 0.0, "the skeptic failed")
        return super().run(role, instructions, inputs, output_schema, **kw)


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_review_skipped_as_hopeless_passes_the_requests_on(env):
    """Minor 2: no review answer can mend the gate (the Skeptic never reviewed the argument), so the review is not
    asked; the requests are in the outcome's reason and in the gate's failures, the next attempt's guidance."""
    _, ps, _ = env
    _synthesize(env)
    ex = SkepticFails(_exec([draft(needs_synthesis=NEED)]).script)
    w = ArgueWorker(budget=2)  # an Arguer and a (failed) Skeptic task: no second round
    out = w.run(_ctx(env, ex))
    assert out.status == "done" and "the Skeptic did not review" in out.reason and NEED in out.reason
    g = w.gate(ps, 1)
    assert not g.ok and any(NEED in f and "asked synthesize" in f for f in g.failures), g.failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_review_says_when_argue_to_synthesize_is_spent(env):
    _, ps, _ = env
    _synthesize(env)
    record_edge_taken(ps, "argue->synthesize", EDGE_LIMIT, actor="tester")
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, _exec([draft(needs_synthesis=NEED)], [critique()]))).checkpoint_id)
    spent = f"argue->synthesize used {EDGE_LIMIT} of {EDGE_LIMIT}: back asks to allow one more use"
    assert cp["options"]["back_spent"] == spent and spent in cp["prompt"]
    assert "back" in cp["options"]["decisions"]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("extra", [{"answers_rq": "no"}, {"claims": {}}])
def test_p2_back_with_claims_or_an_rq_answer_is_refused(env, extra):
    """Minor 4: ``back`` takes nothing else of the review; a mixed answer reopens the review saying why."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(needs_synthesis=NEED)], [critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back", "to": "synthesize", **extra}))
    assert out.status == "waiting_human"
    assert "back takes no claims or answers_rq" in get_checkpoint(ps, out.checkpoint_id)["prompt"]
    for kind in ("back_edge", "rq_check", "synthesis_decision"):
        assert not argue_notes(ps, 1, kind=kind), kind


def test_the_judges_see_the_study_design_field_not_a_design_effect(env):
    """Minor 6: the design is the value of a field named ``design``, ``study_design`` or ``*_design``, a category
    field first; never ``design_effect``."""
    from types import SimpleNamespace

    from rk.production.models import ExtractionField

    _, ps, _ = env
    schema = [ExtractionField(name="design_effect", description="Design effect", type="number"),
              ExtractionField(name="trial_design", description="Trial design in words", type="text"),
              ExtractionField(name="study_design", description="Study design", type="category",
                              categories=["rct", "cohort"])]
    _setup(env, {"A": {"design_effect": 1.2, "trial_design": "parallel arms", "study_design": "rct"}},
           schema=schema, plan=SynthesisPlan(method="thematic"))
    subject = ArgueWorker._synthesis_subject(ps, SimpleNamespace(question="q", synthesis=[]), [])
    assert [(x["label"], x["design"]) for x in subject["included_studies"]] == [("A 1950", "rct")]


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
    # attempt 3: the objections attempt 2 answered with its own claims come back to answer (one each), and without
    # answers the earlier answers do not count
    seen3 = {}

    def first3(inputs):
        seen3["open"] = sorted((o["target_id"], o["category"], o["text"]) for o in inputs["open_objections"])
        return draft()(inputs)

    ex3 = _exec([first3, responses()], [critique()])
    assert w.run(_ctx(env, ex3, 3)).status == "done"
    texts = [t for _, _, t in seen3["open"]]
    assert texts.count("The trial was small.") == 1 and len(seen3["open"]) == 3, seen3["open"]
    g = w.gate(ps, 3)
    assert any(f"contested claim {a}" in f for f in g.failures), g.failures
    small3 = {o["id"] for o in _objections(ps) if o["text"] == "The trial was small." and o["status"] == "open"}
    assert any(any(oid in f for oid in small3) for f in g.failures), g.failures


def test_an_unsupported_answer_comes_back_as_earlier_answer_without_its_id(env):
    """Calibration (critical3): the Arguer was shown an open objection's unsupported response claim id, gave it again
    and was refused. The objection comes back ``open`` with the earlier answer's text, status and why, not its id."""
    _, ps, _ = env
    _synthesize(env)
    weak = "Some trials were large."
    seen = {}

    def again(inputs):
        seen["open"] = inputs["open_objections"]
        return responses("conceded")(inputs)

    ex = _exec([draft(), responses(weak), again],
               [critique(objections=[lambda i: {"target_id": role(i, "main")[0], "category": "problem",
                                                "priority": 2, "text": "The trials were small."}]), critique()],
               uncertain=(weak,))
    ArgueWorker().run(_ctx(env, ex))
    (o,) = seen["open"]
    assert o["status"] == "open" and "response_claim_id" not in o
    assert o["earlier_answer"]["text"] == weak and o["earlier_answer"]["why"] == "its claim is not supported"
    assert not any(c["id"] in str(o) for c in ArgumentService(ps).list_claims() if c["text"] == weak)


def test_a_reason_given_again_is_refused_and_the_refusal_reaches_the_next_round(env):
    """Calibration (critical3, argue attempt 6): the Arguer gave its reasons again every round; each became a new
    reason link with a new warrant, which the Skeptic tested and objected to again, so the rounds never reached a fixed
    point (95 links on the main claim). A link the argument already has is refused, and the next round's input says
    what was refused."""
    _, ps, _ = env
    _synthesize(env)
    seen = {}

    def again(inputs):
        (r,) = role(inputs, "reason")
        return {"reasons": [{"ref": "r1", "claim_id": r, "supports": "main", "warrant": WARRANT}], "responses": []}

    def closing(inputs):
        seen["refused"] = inputs.get("refused_last_round")
        return {"responses": [{"objection_id": o["id"], "status": "conceded", "note": "Fair."}
                              for o in inputs["open_objections"]]}

    vague = {"category": "problem", "priority": 2, "text": "Vague."}
    # round 2 gives the reason again and leaves the objection open; the Skeptic finds nothing new, so the closing task
    # (same round) answers it and is told what round 2's answer had refused
    ex = _exec([draft(), again, closing], [critique(objections=[lambda i: {"target_id": role(i, "main")[0], **vague}]),
                                           critique()])
    ArgueWorker().run(_ctx(env, ex))
    assert len(ArgumentService(ps).list_edges(type="reason_of")) == 1
    assert seen["refused"] and "already supports" in seen["refused"][0], seen


def test_imported_conflicts_take_the_latest_both_contested_counterpart(env):
    _, ps, _ = env
    a, b, c = _synthesize(env, texts=(F1, F2, "Aspirin was studied in very old people."))
    conflict_ab = {"claim_a": a, "claim_b": b, "why": "First reason."}
    conflict_bc = {"claim_a": b, "claim_b": c, "why": "Second reason."}
    # attempt 1: a vs b ends both_contested (the votes split); later b vs c is voted a_holds (b holds, c withdrawn),
    # so b's latest conflict is with c but its latest both_contested decision is with a
    ex1 = _exec([draft(), responses(RESPONSE, RESPONSE)],
                [critique(conflicts=[conflict_ab]), critique(conflicts=[conflict_bc])],
                votes=_votes(["a_holds", "a_holds"], ["b_holds", "a_holds"]))
    w = ArgueWorker()
    out = w.run(_ctx(env, ex1))
    assert out.status == "done", out.reason
    assert ArgumentService(ps).get_claim(c)["status"] == "withdrawn"
    ex2 = _exec([draft(), responses()], [critique()])
    assert w.run(_ctx(env, ex2, 2)).status == "done"
    (imp,) = argue_notes(ps, 2, kind="imported")
    svc = ArgumentService(ps)
    by_target = {}
    for oid in imp["raised"]:
        (o,) = [x for x in svc.list_objections() if x["id"] == oid]
        by_target[o["target_id"]] = imp["conflict_objections"][oid]
    assert by_target == {a: b, b: a}


def test_a_contest_without_a_recorded_conflict_fails_the_gate(env):
    _, ps, _ = env
    a, _ = _synthesize(env)
    ArgumentService(ps).set_contested(a, reason="marked by hand", actor="tester")
    ex = _exec([draft(), responses()], [critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert not ArgumentService(ps).list_objections(target_id=a)  # no objection nobody could answer
    g = w.gate(ps, 1)
    assert any(a in f and "no recorded conflict" in f for f in g.failures), g.failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_final_review_opens_when_withdrawing_a_listed_claim_mends_the_gate(env):
    _, ps, _ = env
    _synthesize(env)
    sure = "Older people certainly were enrolled in a randomised trial."

    def reasons(inputs):
        return [{"ref": "r1", "claim": claim(inputs, sure, "rct"), "supports": "main", "warrant": WARRANT}]

    ex = _exec([draft(reasons=reasons), responses(), responses()],
               [critique(objections=[_o("reason", 1, "Wrong population.")]), critique()], uncertain=("certainly",))
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    (reason,) = cp["options"]["claim_ids"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {reason: "withdraw"}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


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


def test_judge_tasks_do_not_count_against_the_budget(env):
    """Your decision (2026-11-11): the budget caps argue's exploration (the Arguer and the Skeptic), not the judges
    who check it. Before, three answers needing 3 x 2 V-SUPPORT judges did not fit in 6 tasks after a round of 2: now
    the second round (an Arguer and a Skeptic task) fits, judges and all."""
    _, ps, _ = env
    _synthesize(env)
    objections = [_o("main", 2, f"Problem {i}.") for i in range(3)]
    ex = _exec([draft(), responses(RESPONSE, RESPONSE, RESPONSE)], [critique(objections=objections), critique()])
    w = ArgueWorker(budget=4)
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("arguer", "ArgumentDraft")) == 2 and len(ex.of("skeptic", "Critique")) == 2
    assert len([c for c in ex.calls if c["role"].startswith("judge")]) > 4  # more judges than the whole budget
    assert [n["reason"] for n in argue_notes(ps, 1, kind="end")] == ["fixed_point"]
    assert sum(n.get("tasks") or 0 for n in argue_notes(ps, 1)) == 4  # the notes count the budgeted tasks only


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_final_review_shows_the_main_claims_own_problems_for_you_to_steer(env):
    """Fix round 1 (minor 3): an unanswered priority-1 objection on the main claim is no answer's to mend, but the
    review opens anyway: the main claim is not listed (no supported/withdraw), its problems are shown, and you say
    whether it answers the RQ or argue again with your comment."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses(), responses()], [critique(objections=[_o("main", 1, "Wrong.")]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    main = main_claim(ps, 1)
    assert main not in cp["options"]["claim_ids"]
    assert any("Wrong." in p for p in cp["options"]["main_problems"]) and "no answer here mends" in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "rerun", "comment": "Answer the objection."}))
    assert (out.status, out.reason) == ("rerun", "Answer the objection.")


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_review_of_an_overstated_main_claim_and_finding(env):
    """Minor 3 and 8: an overstated (V-HEDGE) main claim opens the review with its problem shown; a finding that is
    overstated is listed, withdraw-only."""
    _, ps, _ = env
    overstated = "Aspirin was proven safe in every older person studied."
    k, _, lib = env
    _setup(env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    sx = Exec({("arguer", "AssessmentOut"): [assess()],
               ("arguer", "NarrativeSynthesis"): [_synth_narrative((F1, overstated))], **_judges()})
    record_attempt(ps, "synthesize", 1, run_id=None, guidance="", requested=None, actor="tester")
    # P2 asks you about the overstated finding; a test passes the attempt as it is (the helpers' gate bypass)
    SynthesizeWorker().run(production_ctx(k, ps, lib, sx, stage="synthesize"))
    record_ran(ps, "synthesize", 1, "done", "", actor="tester")
    record_passed(ps, "synthesize", 1, actor="tester")
    _, finding = synthesis_claims(ps, 1)

    def bold(inputs):
        out = draft()(inputs)
        out["main_claim"] = claim(inputs, "Aspirin was always studied in older people.", "rct", "cohort")
        return out

    ex = _exec([bold], [critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    main = main_claim(ps, 1)
    assert main not in cp["options"]["claim_ids"]
    assert any("V-HEDGE overstated" in p for p in cp["options"]["main_problems"])
    assert cp["options"]["claim_choices"][finding] == ["withdraw"]
    assert any("V-HEDGE overstated" in p for p in cp["options"]["problems"][finding])


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


# ---- fix wave 1: resuming an interrupted attempt in place ----
def test_interrupted_attempt_resumes_in_place(env):
    """A rate cap at the Skeptic's first critique (after round 1 is stored and verified) interrupts attempt 1; the next
    step resumes that attempt from its notes: the Arguer is not asked for round 1 again and no claim is orphaned."""
    k, ps, lib = env
    _synthesize(env)
    ex = CapOnce(_exec([draft(), draft()], [critique(), critique()]).script, at=("skeptic", "Critique"))
    o, run_id = stage_run(k, ps, lib, "argue", ex)
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert o.step("demo", run_id, actor="tester") == "done"
    assert latest_attempt(ps, "argue") == 1
    assert len(ex.of("arguer", "ArgumentDraft")) == 1 and len(ex.of("skeptic", "Critique")) == 1
    every = [c["id"] for c in ArgumentService(ps).list_claims(origin="argument")]
    assert sorted(every) == sorted(argument_claims(ps, 1))  # nothing orphaned


def test_a_source_budget_spent_in_a_skeptic_search_pauses_the_run(env, monkeypatch):
    """Spec §11.3: the Skeptic's critique task (a real ``run_task`` over a scripted chat model) searches a source whose
    API budget is spent: the task is suspended, not skipped. The step pauses the run (``paused_budget``, the budget
    named), the Skeptic never sees the error, and the attempt is not recorded as a crash."""
    k, ps, lib = env
    _synthesize(env)

    def search(*a, **kw):
        raise BudgetExhausted("openalex", 1_900_000_000.0)
    monkeypatch.setattr(builtin, "search", search)
    backend = ScriptedBackend([Turn(tool_calls=[ToolCall(id="c1", name="rk_search_works",
                                                         arguments={"query": "aspirin older people"})]),
                               Turn(text='{"objections": [], "warrant_tests": [], "conflicts": []}')])
    real = KernelExecutor(k, build_registry(k), "demo", run_id=None, session=None)

    class SkepticSearches(Exec):
        def run(self, role, instructions, inputs, output_schema, **kw):
            if role == "skeptic":
                return real.run(role, instructions, inputs, output_schema,
                                **{**kw, "provider": ChatApiProvider(backend, "scripted")})
            return super().run(role, instructions, inputs, output_schema, **kw)

    o, run_id = stage_run(k, ps, lib, "argue", SkepticSearches(_exec([draft()]).script))
    assert o.step("demo", run_id, actor="tester") == "paused_budget"
    assert "API budget exhausted for openalex" in run_row(ps, run_id)["reason"]
    assert len(backend.seen) == 1  # the Skeptic was never shown the error
    (task,) = ps.db.execute("SELECT status, attempts, error FROM tasks WHERE role='skeptic'").fetchall()
    assert tuple(task) == ("failed", 1, "budget_exhausted: openalex")
    assert not any(e.type == "stage.crashed" for e in ps.log.iter())


def test_p1_conflict_vote_interrupted_resumes_without_asking_the_skeptic_again(env):
    """A rate cap at the P1 conflict vote (after the Skeptic's critique raised the conflict) resumes with the vote:
    the critique is not asked for again and the conflict is decided once."""
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    votes = _votes(["both_contested"], ["both_contested"])
    ex = CapOnce(_exec([draft(), responses(RESPONSE, RESPONSE), responses()],
                       [critique(conflicts=[conflict]), critique(), critique()], votes=votes).script,
                 at=("judge", "VoteOut"))
    w = ArgueWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert len(ex.of("skeptic", "Critique")) == 1
    w.run(_ctx(env, ex))
    assert len(ex.of("skeptic", "Critique")) == 2  # round 1's critique once, then round 2's
    (decision,) = argue_notes(ps, 1, kind="conflict_decision")
    assert decision["decision"] == "both_contested" and decision["by"] == "vote"


class SessionBudget(Exec):
    """``Exec`` that counts each task against the stage budget of run session 1 as ``KernelExecutor`` does: linked to
    the run (``run.task``) and refused with ``BudgetExceeded`` once the session's tasks reach ``limit``; a judge's task
    is neither refused nor counted (the fake has no ``tasks`` row with its role, so it is not linked)."""

    def __init__(self, script, *, ps, run_id: str, limit: int):
        super().__init__(script)
        self.ps, self.run_id, self.limit = ps, run_id, limit

    def run(self, role, instructions, inputs, output_schema, **kw):
        judge = role.startswith("judge")
        used = tasks_used(self.ps, self.run_id, 1, kw["stage"])
        if not judge and used >= self.limit:
            raise BudgetExceeded(kw["stage"], used, self.limit)
        r = super().run(role, instructions, inputs, output_schema, **kw)
        if not judge:
            record_run_task(self.ps, task_id=r.task_id, run_id=self.run_id, session=1, stage=kw["stage"],
                            attempt=kw["attempt"])
        return r


def test_rerun_in_the_same_session_plans_with_the_remaining_session_budget(env):
    """argue's budget is the open stage's per run session (§7.2), as the executor counts it: a rerun in the same
    session plans its rounds with what earlier attempts left, so it ends at ``budget`` instead of running into the
    executor's ``BudgetExceeded`` (``paused_budget``) in the middle of a round."""
    _, ps, _ = env
    _synthesize(env)
    run_id = start_run(ps, until="argue", actor="tester", clock=lambda: 1.8e9)
    for i in range(10):  # attempt 1's tasks in this session
        record_run_task(ps, task_id=f"tsk_dead{i}", run_id=run_id, session=1, stage="argue", attempt=1)
    ex = SessionBudget(_exec([draft(), responses(), responses(), responses()],
                             [critique(objections=[_o("main", 1, f"Problem {i}.")]) for i in range(4)]).script,
                       ps=ps, run_id=run_id, limit=16)  # 10 used + three rounds of an Arguer and a Skeptic task
    out = ArgueWorker(budget=16).run(replace(_ctx(env, ex, 2), run_id=run_id))
    assert out.status in ("done", "back_edge"), out.reason
    assert [n["reason"] for n in argue_notes(ps, 2, kind="end")] == ["budget"]
    assert tasks_used(ps, run_id, 1, "argue") == 16


def test_attempt_resumed_in_a_new_session_gets_the_fresh_session_budget(env):
    _, ps, _ = env
    _synthesize(env)
    ArgueWorker().run(_ctx(env, _exec([draft()], [critique()])))
    assert sum(n.get("tasks") or 0 for n in argue_notes(ps, 1)) > 0
    run_id = start_run(ps, until="argue", actor="tester", clock=lambda: 1.8e9)
    for i in range(30):
        record_run_task(ps, task_id=f"tsk_old{i}", run_id=run_id, session=1, stage="argue", attempt=1)
    w = ArgueWorker()
    assert w._used(replace(_ctx(env, _exec()), run_id=run_id)) == 30
    record_run_session(ps, run_id, 2, actor="tester", clock=lambda: 1.8e9)
    assert w._used(replace(_ctx(env, _exec()), run_id=run_id)) == 0


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_conflict_checkpoint_is_opened_once_after_its_note(env, monkeypatch):
    """A crash right after P2's evidence_conflict checkpoint is opened: the resumed attempt finds that checkpoint
    (opened after the conflict's note) instead of opening a second one or asking the Skeptic again."""
    import rk.production.argue as mod

    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    ex = _exec([draft(), draft()], [critique(conflicts=[conflict]), critique(conflicts=[conflict])])
    real, calls = mod.create_checkpoint, []

    def crash_once(*args, **kw):
        cid = real(*args, **kw)
        calls.append(cid)
        if len(calls) == 1:
            raise RuntimeError("crash after opening the checkpoint")
        return cid

    monkeypatch.setattr(mod, "create_checkpoint", crash_once)
    w = ArgueWorker()
    with pytest.raises(RuntimeError, match="crash after"):
        w.run(_ctx(env, ex))
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human" and out.checkpoint_id == calls[0]
    assert [c["id"] for c in list_checkpoints(ps) if c["kind"] == "evidence_conflict"] == [calls[0]]
    assert len(ex.of("skeptic", "Critique")) == 1


# ---- fix wave 2 ----
def test_limitations_of_an_older_attempt_use_the_synthesis_it_argued_on(env):
    """M7: argue_limitations of argue attempt 1 lists the limitations of synthesize attempt 1 (the one it argued on),
    not of a later passed synthesize attempt."""
    k, ps, lib = env
    first = _synthesize(env)
    ArgueWorker().run(_ctx(env, _exec([draft()], [critique()])))
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_synth_narrative((F1,))],
               **_judges()})
    record_attempt(ps, "synthesize", 2, run_id=None, guidance="", requested=None, actor="tester")
    assert SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize", attempt=2)).status == "done"
    record_ran(ps, "synthesize", 2, "done", "", actor="tester")
    record_passed(ps, "synthesize", 2, actor="tester")
    later = synthesis_claims(ps, 2)  # F1 and the findings of attempt 1 it keeps, each a new claim
    svc = ArgumentService(ps)
    for c in later:
        svc.withdraw_claim(c, reason="a later synthesis", actor="tester")
    svc.withdraw_claim(first[1], reason="the argued synthesis", actor="tester")
    withdrawn = {c["claim_id"] for c in argue_limitations(ps, 1)["claims"]["withdrawn"]}
    assert first[1] in withdrawn and not withdrawn & set(later)


def test_all_findings_withdrawn_in_p1_goes_back_to_synthesize(env):
    """I4: a P1 degrade that withdraws every finding of synthesize's passed attempt; the next run's argue attempt
    takes argue->synthesize (counted against its limit) with a reason naming the withdrawals, instead of failing."""
    k, ps, lib = env
    a, b = _synthesize(env)

    def on(i: int):
        def o(inputs):
            return {"target_id": role(inputs, "synthesis")[i], "category": "problem", "priority": 1,
                    "text": f"Finding {i} misreads its study."}
        return o

    ex = _exec([draft(), responses(), responses()], [critique(objections=[on(0), on(1)]), critique()],
               narrowed=[{"text": F1, "kind": "fact"}, {"text": F2, "kind": "fact"}])  # the same text: withdrawn
    w = ArgueWorker()
    record_attempt(ps, "argue", 1, run_id=None, guidance="", requested=None, actor="tester")
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    record_ran(ps, "argue", 1, "done", "", actor="tester")
    g = w.gate(ps, 1)
    assert not g.ok
    w.degrade(ctx, g.failures)
    svc = ArgumentService(ps)
    assert all(svc.get_claim(c)["status"] == "withdrawn" for c in (a, b))
    o, run_id = stage_run(k, ps, lib, "argue", _exec())
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "synthesize" and edge_rows(ps)["argue->synthesize"]["count"] == 1
    guidance = stage_row(ps, "synthesize")["guidance"]
    assert "every finding of synthesize attempt 1 was withdrawn in argue" in guidance
    assert a in guidance and b in guidance


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("priority", [1, 2])
def test_unanswered_objection_on_a_finding_reaches_your_review(env, priority):
    """I3: an objection on a synthesis finding the Arguer never answers is not a forced rerun: the final review lists
    the finding (withdraw only), and withdrawing it mends the gate."""
    _, ps, _ = env
    a, b = _synthesize(env)

    def on_finding(inputs):
        return {"target_id": role(inputs, "synthesis")[0], "category": "problem", "priority": priority,
                "text": "The trial enrolled people under 65."}

    ex = _exec([draft(), responses(), responses()], [critique(objections=[on_finding]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["options"]["claim_ids"] == [a]
    assert cp["options"]["claim_choices"] == {a: ["withdraw"]}
    assert any("under 65" in p for p in cp["options"]["problems"][a])
    assert f"Withdrawing a finding of the synthesis ({a}) withdraws it for the project" in cp["prompt"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {a: "supported"}, "answers_rq": "yes"}))
    assert out.status == "waiting_human"  # supported is not one of its answers
    cp = get_checkpoint(ps, out.checkpoint_id)
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {a: "withdraw"}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert ArgumentService(ps).get_claim(b)["status"] == "supported"


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("asked", [False, True])
def test_withdrawing_every_finding_in_your_review_goes_back_to_synthesize(env, asked):
    _, ps, _ = env
    a, b = _synthesize(env)

    def on(i: int):
        def o(inputs):
            return {"target_id": role(inputs, "synthesis")[i], "category": "problem", "priority": 1,
                    "text": f"Finding {i} misreads its study."}
        return o

    first = draft(needs_synthesis=NEED) if asked else draft()
    ex = _exec([first, responses(), responses()], [critique(objections=[on(0), on(1)]), critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    assert cp["options"]["claim_ids"] == [a, b]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {a: "withdraw", b: "withdraw"},
                                                              "answers_rq": "yes"}))
    assert (out.status, out.to) == ("back_edge", "synthesize")
    assert "every finding of synthesize attempt 1 was withdrawn in argue" in out.reason
    assert (NEED in out.reason) == asked  # the I4 back-edge carries the Arguer's requests too


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_your_withdrawals_concede_their_objections_when_applied_again_after_a_crash(env, monkeypatch):
    """Fix round (M4 minor): a crash in the first concession after your withdrawals were applied; applied again, the
    answer concedes the objections of every claim you withdrew (already withdrawn ones included), so none is left
    open."""
    _, ps, _ = env
    a, _ = _synthesize(env)
    finding = {"target_id": a, "category": "problem", "priority": 1, "text": "Finding a misreads its study."}
    ex = _exec([draft(), responses(), responses()],
               [critique(objections=[finding, _o("reason", 1, "Study A enrolled people under 65.")]), critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    listed = cp["options"]["claim_ids"]
    assert a in listed and len(listed) == 2
    cp = _answer(ps, cp["id"], {"claims": dict.fromkeys(listed, "withdraw"), "answers_rq": "yes"})
    real = ArgumentService.respond_objection

    def crash(*args, **kw):
        raise RuntimeError("crash in the first concession")

    monkeypatch.setattr(ArgumentService, "respond_objection", crash)
    with pytest.raises(RuntimeError, match="first concession"):
        w.on_answer(_ctx(env, ex), cp)
    monkeypatch.setattr(ArgumentService, "respond_objection", real)
    assert [ArgumentService(ps).get_claim(c)["status"] for c in listed] == ["withdrawn", "withdrawn"]
    assert [o["status"] for o in _objections(ps)] == ["open", "open"]
    out = w.on_answer(_ctx(env, ex), cp)
    assert out.status == "done", out.reason
    assert [o["status"] for o in _objections(ps)] == ["conceded", "conceded"]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("rq", ["yes", "no"])
def test_your_review_withdrawing_every_finding_applied_again_after_a_crash_goes_back(env, monkeypatch, rq):
    """W3 re-apply gap: you withdraw every finding, the first concession crashes; the answer applied again (the engine
    hands the answered checkpoint back) concedes every objection, notes your RQ check once and goes back — to
    question if you said the main claim does not answer it, else to synthesize, as ``run`` does — instead of failing."""
    _, ps, _ = env
    a, b = _synthesize(env)
    objections = [{"target_id": x, "category": "problem", "priority": 1, "text": f"Finding {x} misreads its study."}
                  for x in (a, b)]
    ex = _exec([draft(), responses(), responses()], [critique(objections=objections), critique()])
    w = ArgueWorker()
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    listed = cp["options"]["claim_ids"]
    assert {a, b} <= set(listed)
    cp = _answer(ps, cp["id"], {"claims": dict.fromkeys(listed, "withdraw"), "answers_rq": rq})
    real = ArgumentService.respond_objection

    def crash(*args, **kw):
        raise RuntimeError("crash in the first concession")

    monkeypatch.setattr(ArgumentService, "respond_objection", crash)
    with pytest.raises(RuntimeError, match="first concession"):
        w.on_answer(_ctx(env, ex), cp)
    monkeypatch.setattr(ArgumentService, "respond_objection", real)
    out = w.on_answer(_ctx(env, ex), cp)
    assert out.status == "back_edge" and out.to == ("synthesize" if rq == "yes" else "question"), out.reason
    assert {o["status"] for o in _objections(ps)} == {"conceded"}
    assert [n["checkpoint_id"] for n in argue_notes(ps, 1, kind="rq_check")] == [cp["id"]]
    assert w.on_answer(_ctx(env, ex), cp).status == "back_edge"  # applied once more: nothing noted twice
    assert len(argue_notes(ps, 1, kind="rq_check")) == 1


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_your_conflict_decision_applied_again_with_no_finding_left_is_finished(env, monkeypatch):
    """Your evidence_conflict answer (b holds) withdraws finding a; a crash in its concession; by then no finding is
    left (b withdrawn elsewhere). The answer applied again goes back to synthesize and still finishes the decision:
    a's objection conceded, the decision noted once."""
    _, ps, _ = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    on_a = {"target_id": a, "category": "problem", "priority": 2, "text": "Finding a misreads its study."}
    ex = _exec([draft(), responses()], [critique(objections=[on_a], conflicts=[conflict])])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "evidence_conflict", cp["kind"]
    cp = _answer(ps, cp["id"], {"decision": "b_holds"})
    real = ArgumentService.respond_objection

    def crash(*args, **kw):
        raise RuntimeError("crash in the concession")

    monkeypatch.setattr(ArgumentService, "respond_objection", crash)
    with pytest.raises(RuntimeError, match="concession"):
        w.on_answer(_ctx(env, ex), cp)
    monkeypatch.setattr(ArgumentService, "respond_objection", real)
    ArgumentService(ps).withdraw_claim(b, reason="withdrawn elsewhere", actor="tester")
    out = w.on_answer(_ctx(env, ex), cp)
    assert out.status == "back_edge" and out.to == "synthesize", out.reason
    assert [o["status"] for o in ArgumentService(ps).list_objections(target_id=a)] == ["conceded"]
    assert [n["decision"] for n in argue_notes(ps, 1, kind="conflict_decision")] == ["b_holds"]


# ---- fix wave 3 ----
def test_conceded_priority1_objection_fails_its_target_in_p1_until_narrowed(env):
    """I6: the Arguer may concede a priority-1 objection, but its target then fails the claim gate (it is not withdrawn
    for it); the P1 degrade narrows it, and the rewrite is re-checked against the conceded objection."""
    _, ps, _ = env
    _synthesize(env)
    narrower = "Some older people were enrolled in a randomised trial."
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[_o("reason", 1, "Study A enrolled people under 65.")]), critique()],
               narrowed=[{"text": narrower, "kind": "fact"}],
               rechecks=[lambda inputs: {"warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()}
                                                           for w in inputs["untested_warrants"]]}])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    (o,) = _objections(ps)
    by_text = {c["text"]: c for c in _claims(ps)}
    assert o["status"] == "conceded" and by_text[REASON]["status"] == "supported"  # not withdrawn by the kernel
    g = w.gate(ps, 1)
    assert f"{by_text[REASON]['id']}: conceded priority-1 objection {o['id']}" in g.failures, g.failures
    assert w.degrade(ctx, g.failures).status == "done"
    by_text = {c["text"]: c for c in _claims(ps)}
    assert by_text[REASON]["status"] == "withdrawn" and by_text[narrower]["status"] == "supported"
    (recheck,) = ex.of("skeptic", "RecheckOut")
    assert [x["id"] for x in recheck["inputs"]["conceded_objections"]] == [o["id"]]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_conceded_priority1_objection_reaches_your_review_in_p2(env):
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[_o("reason", 1, "Study A enrolled people under 65.")]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    (o,) = _objections(ps)
    (reason,) = cp["options"]["claim_ids"]
    assert _claim_text(ps, reason) == REASON and cp["options"]["claim_choices"][reason] == ["withdraw"]
    assert any(f"conceded priority-1 objection {o['id']}" in p for p in cp["options"]["problems"][reason])
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {reason: "withdraw"}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def _on_link(priority: int, text: str):
    """An objection on the reason link into the main claim."""
    def o(inputs):
        (main,) = role(inputs, "main")
        (e,) = [e for e in inputs["argument"]["edges"] if e["type"] == "reason_of" and e["to_id"] == main]
        return {"target_id": e["id"], "category": "problem", "priority": priority, "text": text}
    return o


def test_objections_on_a_link_out_of_the_argument_fail_no_claim(env):
    """I6 on links: an open priority-1 objection on a reason link fails the link's destination while the link is in
    the argument; a conceded one fails neither claim (conceding it drops the link); a link argue dropped is out of
    the argument for every claim problem (``link_gone``, the one helper argue and the claim gate share)."""
    from rk.production.argue import _note
    from rk.verify.support import claim_problems, link_gone

    _, ps, _ = env
    _synthesize(env)
    w = ArgueWorker()
    assert w.run(_ctx(env, _exec([draft()], [critique()]))).status == "done"
    svc = ArgumentService(ps)
    main = main_claim(ps, 1)
    (link,) = svc.list_edges(to_id=main, type="reason_of")
    oid = svc.add_objection(ObjectionIn(target_id=link["id"], category="problem", priority=1,
                                        text="The trial does not support the claim."), actor="skeptic")
    assert [p.kind for p in claim_problems(ps, main)] == ["objection"]
    svc.respond_objection(oid, response_claim_id=None, status="conceded", note="It holds.", actor="arguer")
    assert claim_problems(ps, main) == [] and claim_problems(ps, link["from_id"]) == []
    svc.add_objection(ObjectionIn(target_id=link["id"], category="problem", priority=1,
                                  text="The trial enrolled no older people."), actor="skeptic")
    assert [p.kind for p in claim_problems(ps, main)] == ["objection"] and not link_gone(ps, link["id"])
    _note(ps, 1, "dropped_link", actor="tester", edge_id=link["id"], warrant_edge_id=None, objection_ids=[],
          reason="dropped by the test")
    assert link_gone(ps, link["id"]) and claim_problems(ps, main) == []
    (wr,) = svc.list_edges(to_id=link["id"], type="warrant_of")
    assert link_gone(ps, wr["id"])  # a warrant leaves with the link it licenses


def test_conceded_priority1_objection_on_a_reason_link_drops_it_in_p1(env):
    """I6 on links (P1): the Arguer concedes a priority-1 objection on the reason link into the main claim. The link
    is dropped (noted like a failed warrant's, with the other objections on it and its warrant conceded), neither
    claim fails, the attempt passes without a degrade and the main claim is not narrowed."""
    from rk.verify.support import claim_problems

    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim.")]), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (o,) = _objections(ps)
    main = main_claim(ps, 1)
    (dropped,) = argue_notes(ps, 1, kind="dropped_link")
    assert dropped["edge_id"] == o["target_id"] and o["id"] in dropped["objection_ids"]
    (reason,) = [e["from_id"] for e in ArgumentService(ps).list_edges(type="reason_of") if e["id"] == o["target_id"]]
    assert claim_problems(ps, main) == [] and claim_problems(ps, reason) == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert main == next(n["main_claim_id"] for n in argue_notes(ps, 1, kind="draft"))  # not narrowed
    assert ex.of("arguer", "NarrowedClaim") == []
    assert dropped["edge_id"] in {d["edge_id"] for d in argue_limitations(ps, 1)["dropped_links"]}


def test_unanswered_priority1_objection_on_a_reason_link_drops_it_at_the_edge_limit(env):
    """I6 on links (P1 degrade): a priority-1 objection on a reason link left unanswered is conceded at the edge limit
    with the link, which is dropped; the link's destination, the main claim, is not narrowed for it."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses(), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim.")]), critique()])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    (o,) = _objections(ps)
    main = main_claim(ps, 1)
    g = w.gate(ps, 1)
    assert not g.ok
    assert w.degrade(ctx, g.failures).status == "done"
    (dropped,) = argue_notes(ps, 1, kind="dropped_link")
    assert dropped["edge_id"] == o["target_id"] and o["id"] in dropped["objection_ids"]
    assert _objections(ps)[0]["status"] == "conceded"
    assert main_claim(ps, 1) == main and ex.of("arguer", "NarrowedClaim") == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_degrade_drops_a_conceded_link_whose_drop_was_never_noted(env, monkeypatch):
    """Fix round (degrade): the Arguer conceded a priority-1 objection on the reason link, but a crash came before the
    ``dropped_link`` note; the attempt then ran on. The gate says the link was not dropped; at the edge limit the
    degrade drops it (whatever the objection's status) instead of narrowing anything, and the gate passes."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim.")]), critique()])
    w = ArgueWorker()
    ctx = _ctx(env, ex)

    def crash(self, *args, **kw):
        raise RuntimeError("crash before the dropped_link note")

    monkeypatch.setattr(ArgueWorker, "_drop_link", crash)
    with pytest.raises(RuntimeError, match="dropped_link note"):
        w.run(ctx)
    monkeypatch.undo()
    (o,) = _objections(ps)
    assert o["status"] == "conceded" and not argue_notes(ps, 1, kind="dropped_link")
    assert w.run(ctx).status == "done"  # resumed in place: the rounds end
    main = main_claim(ps, 1)
    g = w.gate(ps, 1)
    assert any("its reason link was not dropped" in f for f in g.failures), g.failures
    assert w.degrade(ctx, g.failures).status == "done"
    (dropped,) = argue_notes(ps, 1, kind="dropped_link")
    assert dropped["edge_id"] == o["target_id"] and o["id"] in dropped["objection_ids"]
    assert main_claim(ps, 1) == main and ex.of("arguer", "NarrowedClaim") == []
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("skeptic_says", ["clear", "link_holds"])
def test_degrade_keeps_a_dropped_link_out_of_the_narrowed_main_claim(env, skeptic_says):
    """Fix round (I6 edges, P1 degrade): an unanswered priority-1 objection on the reason link into the main claim and
    one on the main claim itself. The degrade drops the link, then narrows the main claim: the rewrite does not take
    the dropped link over (``_take_over``), and the Skeptic re-checks it against the main claim's objection only, not
    the link's (``_recheck``). Whatever the Skeptic says of the link objection, the rewrite is not withdrawn for it,
    the link stays out and the gate passes."""
    from rk.verify.support import link_gone

    _, ps, _ = env
    _synthesize(env)
    narrower = "Aspirin was studied in some older people."
    seen: list[list[str]] = []

    def recheck(inputs):
        seen.append([o["id"] for o in inputs["conceded_objections"]])
        (link_o,) = [o for o in _objections(ps) if o["target_id"].startswith("edg_")]
        held = [{"objection_id": link_o["id"], "reason": "The trial still does not bear on it."}]
        return {"still_holds": held if skeptic_says == "link_holds" else [],
                "warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()} for w in inputs["untested_warrants"]]}

    ex = _exec([draft(), responses(), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim."),
                                     _o("main", 1, "It says nothing of people over 90.")]), critique()],
               narrowed=[{"text": narrower, "kind": "fact"}], rechecks=[recheck])
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    link_o, main_o = sorted(_objections(ps), key=lambda o: not o["target_id"].startswith("edg_"))
    link, main = link_o["target_id"], main_claim(ps, 1)
    g = w.gate(ps, 1)
    assert not g.ok
    assert w.degrade(ctx, g.failures).status == "done"
    new = _rewrite(ps, main)
    assert main_claim(ps, 1) == new and ArgumentService(ps).get_claim(new)["status"] == "supported"
    assert seen == [[main_o["id"]]]  # the dropped link's objection is not re-checked against the rewrite
    (dropped,) = argue_notes(ps, 1, kind="dropped_link")
    assert dropped["edge_id"] == link and link_gone(ps, link)
    (reason,) = [e["from_id"] for e in ArgumentService(ps).list_edges(type="reason_of") if e["id"] == link]
    assert not [e for e in ArgumentService(ps).list_edges(type="reason_of")
                if (e["from_id"], e["to_id"]) == (reason, new)]  # the dropped link was not taken over
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_degrade_in_a_seeded_project_notes_no_back_edge_for_a_narrowed_main_claim(env):
    """Fix round 1 (seeded-fixed): the degrade checks a narrowed main claim against the RQ only. In a project whose
    question is fixed, a ``does_not_answer`` vote notes the check and the degrade's reason, but no ``back_edge``: the
    degrade takes no edge, and a resumed attempt would otherwise return one the engine never took."""
    _, ps, _ = env
    _synthesize(env)
    record_fixed_stages(ps, ["question"], SEED, actor="tester")

    def recheck(inputs):
        return {"still_holds": [],
                "warrant_tests": [{"edge_id": w["edge_id"], "tests": verdicts()} for w in inputs["untested_warrants"]]}

    ex = _exec([draft(), responses(), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim."),
                                     _o("main", 1, "It says nothing of people over 90.")]), critique()],
               narrowed=[{"text": "Aspirin was studied in some older people.", "kind": "fact"}], rechecks=[recheck],
               votes=_votes(["answers", "does_not_answer"], ["answers", "answers"]))
    w = ArgueWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    main = main_claim(ps, 1)
    out = w.degrade(ctx, w.gate(ps, 1).failures)
    assert out.status == "done", out.reason
    new = main_claim(ps, 1)
    assert new != main and "does not answer the research question" in out.reason
    assert [(n["main_claim_id"], n["decision"]) for n in argue_notes(ps, 1, kind="rq_check")] == [
        (main, "answers"), (new, "does_not_answer")]
    assert not argue_notes(ps, 1, kind="back_edge")



@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_conceded_priority1_objection_on_a_reason_link_drops_it_in_p2(env):
    """I6 on links (P2): once the conceded link objection dropped the link, your final review lists nothing and the
    main claim has no problem of its own: no forced rerun; the review and the gate pass."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[_on_link(1, "The trial does not bear on the claim.")]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["options"]["claim_ids"] == [] and cp["options"]["main_problems"] == []
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"claims": {}, "answers_rq": "yes"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_an_earlier_attempts_concession_failing_a_finding_is_carried_into_the_rerun(env):
    """A priority-1 objection conceded on an in-scope finding in attempt 1 still fails it in attempt 2 (you asked to
    argue again): attempt 2 carries it (``conceded_in_scope``), so it is among its objections and its limitations,
    and your review lists the finding (withdraw only)."""
    _, ps, _ = env
    a, _ = _synthesize(env)
    ex = _exec([draft(), responses("conceded"), responses()],
               [critique(objections=[{"target_id": a, "category": "problem", "priority": 1,
                                      "text": "The trial was small."}]), critique()])
    w = ArgueWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["options"]["claim_choices"][a] == ["withdraw"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "rerun", "comment": "Argue it again."}))
    assert out.status == "rerun", out.reason
    (o,) = _objections(ps)
    assert o["status"] == "conceded"
    out = w.run(_ctx(env, _exec([draft()], [critique()]), 2))
    assert out.status == "waiting_human", out.reason
    (imp,) = argue_notes(ps, 2, kind="imported")
    assert imp["conceded_in_scope"] == [o["id"]] and o["id"] in imp["objection_ids"]
    assert o["id"] in argument_objection_ids(ps, 2)
    assert [x["objection_id"] for x in argue_limitations(ps, 2)["claims"]["conceded"]] == [o["id"]]
    cp2 = get_checkpoint(ps, out.checkpoint_id)
    assert cp2["options"]["claim_choices"][a] == ["withdraw"]
    assert any(o["id"] in p for p in cp2["options"]["problems"][a])


def test_limitations_list_the_attempts_conceded_objections_once(env):
    """T5: the conceded objections of an attempt's limitations are its own (a soundness objection included), listed
    once (``claims.conceded``), never an earlier attempt's on the same finding."""
    _, ps, _ = env
    a, _ = _synthesize(env)
    ex = _exec([draft(), responses("conceded", "conceded")],
               [critique(objections=[{"target_id": a, "category": "problem", "priority": 2,
                                      "text": "The trial was small."}], fail="true"), critique()])
    w = ArgueWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    conceded = {o["id"] for o in _objections(ps) if o["status"] == "conceded"}
    assert len(conceded) == 2
    lim = argue_limitations(ps, 1)
    assert "conceded" not in lim
    assert sorted(o["objection_id"] for o in lim["claims"]["conceded"]) == sorted(conceded)
    assert w.run(_ctx(env, _exec([draft()], [critique()]), 2)).status == "done"
    assert argue_limitations(ps, 2)["claims"]["conceded"] == []  # attempt 1's concessions are not attempt 2's


def test_objections_answered_by_an_in_scope_finding_are_carried_into_the_new_attempt(env):
    """T5: an objection on a finding that another in-scope finding answers is carried into a new attempt as answered
    (``answered_in_scope``: in its objections and the V-BALANCE input), a conflict objection with its counterpart, so
    the contested findings get no second conflict objection to answer again."""
    _, ps, _ = env
    a, b, c = _synthesize(env, texts=(F1, F2, "Aspirin was studied in very old people."))
    conflict = {"claim_a": a, "claim_b": b, "why": "One says randomised, the other cohort."}
    by_c = answer_each(lambda inputs, o: {"objection_id": o["id"], "status": "responded", "claim_id": c})
    ex1 = _exec([draft(), by_c],
                [critique(objections=[{"target_id": a, "category": "problem", "priority": 2,
                                       "text": "The trial was small."}], conflicts=[conflict]), critique()],
                votes=_votes(["both_contested"], ["both_contested"]))
    w = ArgueWorker()
    assert w.run(_ctx(env, ex1)).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    answered = {o["id"] for o in _objections(ps) if o["status"] == "responded" and o["response_claim_id"] == c}
    assert len(answered) == 3
    conflicts1 = {oid: cp for n in argue_notes(ps, 1, kind="conflict_decision")
                  for oid, cp in (n.get("conflict_objections") or {}).items()}
    assert w.run(_ctx(env, _exec([draft()], [critique()]), 2)).status == "done"
    (imp,) = argue_notes(ps, 2, kind="imported")
    assert set(imp["answered_in_scope"]) == answered and imp["raised"] == [] and imp["copied"] == {}
    assert imp["conflict_objections"] == conflicts1
    assert answered <= set(argument_objection_ids(ps, 2))
    view = ArgueWorker()._view(ps, 2, argue_inputs(ps))
    assert answered <= {o["id"] for o in view["objections"]}
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


def _claim_text(ps, cid: str) -> str:
    return ArgumentService(ps).get_claim(cid)["text"]


def test_a_round_1_without_its_self_check_is_asked_again_not_rerun(env):
    """Calibration (critical review rerun, argue attempt 8): a sound argument whose output lacked answers_rq cost a
    whole argue->argue rerun. Round 1 is asked once more naming what it lacked; only a second slip reruns."""
    _, ps, _ = env
    _synthesize(env)
    ex = _exec([draft(answers=None), draft()], [critique()])
    assert ArgueWorker().run(_ctx(env, ex)).status == "done"
    first, again = ex.of("arguer", "ArgumentDraft")[:2]
    assert "guidance" not in first["inputs"] and "no answers_rq self-check" in again["inputs"]["guidance"]
    (d,) = argue_notes(ps, 1, kind="draft")
    assert d["answers_rq"] == SELF_CHECK
    ex2 = _exec([draft(answers=None), draft(answers=None)], [critique()])
    out = ArgueWorker().run(_ctx(env, ex2, 2))
    assert (out.status, out.reason) == ("rerun", "the arguer gave no answers_rq self-check (asked twice)")
