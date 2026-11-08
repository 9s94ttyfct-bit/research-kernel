"""Plan 5b Task 6: the design stage of a research proposal. The Ideator ties falsifiable hypotheses to a method design
and states its feasibility; the kernel stores and verifies it, checks its structure, then P2 asks you (design_approval)
and P1 votes; an approved design is frozen and pre-registers the hypotheses it tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from rk.argument import ArgumentService
from rk.checkpoints import (
    HARD_HUMAN_KINDS,
    answer_checkpoint,
    get_checkpoint,
    list_checkpoints,
)
from rk.discovery.store import (
    add_hypothesis,
    frozen_rq,
    hypotheses,
    set_hypothesis_status,
)
from rk.orchestrator.engine import Orchestrator, default_workers
from rk.orchestrator.state import (
    record_attempt,
    record_edge_taken,
    record_passed,
    record_ran,
)
from rk.production.argue import ArgueWorker
from rk.production.design import (
    DesignDraft,
    DesignWorker,
    design_limitations,
    design_notes,
    feasibility_claims,
)
from rk.production.models import DesignIn
from rk.production.store import design, set_design
from rk.roles.prompts import build_system_prompt
from tests.production_fakes import make_frozen_project, production_ctx, votes_on
from tests.test_argue import _exec as _argue_exec
from tests.test_argue import _judges, _synthesize, critique, draft
from tests.test_synthesis import Exec

FEASIBLE = "Older people can be enrolled in a randomised trial."
NARROWER = "Older people were enrolled in a randomised trial."
NEW_H = "Aspirin started after 80 lowers the stroke rate."


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http,
                                        **{"paper_type": "research_proposal", **getattr(request, "param", {})})
    yield k, ps, lib
    k.close()


def _argued(env) -> None:
    """Passed synthesize and argue attempts (argue: one round, the Skeptic finds nothing; P2's final review says the
    main claim answers the RQ)."""
    k, ps, lib = env
    _synthesize(env)
    ex = _argue_exec([draft()], [critique()])
    w = ArgueWorker()
    out = w.run(production_ctx(k, ps, lib, ex, stage="argue"))
    if out.status == "waiting_human":
        answer_checkpoint(ps, out.checkpoint_id, {"claims": {}, "answers_rq": "yes"}, answered_by="human",
                          actor="researcher")
        out = w.on_answer(production_ctx(k, ps, lib, ex, stage="argue"), get_checkpoint(ps, out.checkpoint_id))
    assert out.status == "done", out.reason
    record_attempt(ps, "argue", 1, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, "argue", 1, "done", "", actor="tester")
    record_passed(ps, "argue", 1, actor="tester")


def _h0(ps) -> str:
    return hypotheses(ps, frozen_rq(ps)["id"])[0]["id"]


def _note(inputs, word: str) -> str:
    return next(n["id"] for n in inputs["notes"] if word in n["text"])


def feasibility(inputs, text: str = FEASIBLE) -> dict:
    return {"text": text, "kind": "feasibility",
            "evidence": [{"note_ids": [_note(inputs, "rct")], "interpretation": "A trial enrolled older people."}]}


def item(id_: str, measurements: list[str], hyps: list[str]) -> dict:
    return {"id": id_, "kind": "experiment", "sample": "200 people over 80", "controls": ["placebo"],
            "measurements": [{"id": m, "what": f"stroke rate {m}", "instrument": "registry", "unit": "%"}
                             for m in measurements],
            "analysis": "Cox model, two-sided alpha 0.05", "data_source": "a new trial", "hypothesis_ids": hyps}


def plan(**over):
    """A draft: the RQ's hypothesis tested by D1 (falsified by M1), a new rival tested by D2 (falsified by M2), one
    feasibility claim. ``over`` maps a field to a value or a function of (inputs, h0)."""
    def out(inputs):
        h0 = inputs["hypotheses"][0]["id"]
        d = {"hypotheses": [{"id": h0, "falsified_by": ["M1"], "tested_by": ["D1"]},
                            {"new": {"statement": NEW_H, "falsification": "No lower stroke rate.", "rival_of": h0},
                             "ref": "h_new", "falsified_by": ["M2"], "tested_by": ["D2"]}],
             "design": {"items": [item("D1", ["M1"], [h0]), item("D2", ["M2"], ["h_new"])],
                        "expected_results": "Fewer strokes on aspirin.",
                        "alternative_explanations": ["healthier people take aspirin"]},
             "feasibility_claims": [feasibility(inputs)]}
        for key, v in over.items():
            d[key] = v(inputs, h0) if callable(v) else v
        return d
    return out


def retest():
    """A later attempt's draft: the RQ's hypothesis and the new one an earlier attempt added (by their ids now)."""
    def out(inputs):
        new = next(h["id"] for h in inputs["hypotheses"] if h["statement"] == NEW_H)
        return plan(hypotheses=lambda i, h0: [{"id": h0, "falsified_by": ["M1"], "tested_by": ["D1"]},
                                              {"id": new, "falsified_by": ["M2"], "tested_by": ["D2"]}],
                    design=lambda i, h0: {"items": [item("D1", ["M1"], [h0]), item("D2", ["M2"], [new])],
                                          "expected_results": "E.", "alternative_explanations": []})(inputs)
    return out


def _votes(first=(), second=()) -> dict:
    def queue(choices):
        return [{"choice": c, "reason": f"judge says {c}"} for c in choices]
    return {("judge", "VoteOut"): queue(first), ("judge_2", "VoteOut"): queue(second)}


def _exec(drafts, *, uncertain=(), votes=None, narrowed=()) -> Exec:
    return Exec({("ideator", "DesignDraft"): list(drafts), ("arguer", "NarrowedClaim"): list(narrowed),
                 **_judges(uncertain), **(votes if votes is not None else _votes(["approve"] * 3, ["approve"] * 3))})


def _ctx(env, ex, attempt: int = 1, guidance: str = ""):
    k, ps, lib = env
    return replace(production_ctx(k, ps, lib, ex, stage="design", attempt=attempt), guidance=guidance)


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


def _hyp(ps, hid: str) -> dict:
    return next(h for h in hypotheses(ps, frozen_rq(ps)["id"]) if h["id"] == hid)


# ---- tests ----
def test_design_only_in_research_proposal(tmp_home, replay_http, env):
    k, ps, _ = env
    assert isinstance(default_workers()["design"], DesignWorker)
    assert "design_approval" in HARD_HUMAN_KINDS
    prompt = build_system_prompt("ideator", DesignDraft)
    for word in ("`DesignDraft`", "falsified_by", "tested_by", "needs_argument", "rq_untestable", "feasibility"):
        assert word in prompt, word
    assert "design" in Orchestrator(k, None).runnable(ps)

    k2, ps2, lib2, _ = make_frozen_project(tmp_home, replay_http, slug="review", paper_type="critical_review")
    try:
        assert "design" not in Orchestrator(k2, None).runnable(ps2)
        ex = _exec([plan()])
        out = DesignWorker().run(production_ctx(k2, ps2, lib2, ex, stage="design", project="review"))
        assert out.status == "failed" and "research proposal" in out.reason
        assert not ex.calls
        res = DesignWorker().gate(ps2, 1)
        assert not res.ok and any("research proposal" in f for f in res.failures)
    finally:
        k2.close()


def test_design_gate_requires_links_both_ways(env):
    _, ps, _ = env
    _argued(env)
    bad = plan(design=lambda i, h0: {"items": [item("D1", ["M1"], [h0]), item("D2", ["M2"], [])],
                                     "expected_results": "Fewer strokes.", "alternative_explanations": []},
               hypotheses=lambda i, h0: [
                   {"id": h0, "falsified_by": ["M1"], "tested_by": ["D1"]},
                   {"new": {"statement": NEW_H, "falsification": "No lower rate."}, "ref": "h_new",
                    "falsified_by": ["M1"], "tested_by": ["D1"]}])
    w = DesignWorker()
    ex = _exec([bad, plan()])
    out = w.run(_ctx(env, ex))
    assert out.status == "rerun"
    new = next(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"]) if h["statement"] == NEW_H)
    res = w.gate(ps, 1)
    assert not res.ok
    text = "\n".join(res.failures)
    assert "design item D2 tests no hypothesis" in text
    assert f"hypothesis {new} is tested by no design item" in text
    assert f"hypothesis {new}: its tested_by names D1, which does not list it" in text
    assert "not frozen" in text
    for line in ("design item D2 tests no hypothesis", f"hypothesis {new} is tested by no design item"):
        assert line in out.reason
    assert _hyp(ps, new)["status"] == "proposed"

    # an item listing a hypothesis whose tested_by leaves it out fails too
    one_way = plan(hypotheses=lambda i, h0: [{"id": h0, "falsified_by": ["M1"], "tested_by": []}],
                   design=lambda i, h0: {"items": [item("D1", ["M1"], [h0])], "expected_results": "E.",
                                         "alternative_explanations": []})
    out = w.run(_ctx(env, _exec([one_way]), attempt=2))
    assert out.status == "rerun"
    h0 = _h0(ps)
    assert f"hypothesis {h0}: design item D1 lists it, but its tested_by does not name D1" in out.reason

    # attempt 3 links both ways (the earlier attempt's new hypothesis is the RQ's now: it is tested too)
    out = w.run(_ctx(env, _exec([retest()]), attempt=3))
    assert out.status == "done", out.reason
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures


def test_unfalsifiable_hypothesis_fails_design_gate(env):
    _, ps, _ = env
    _argued(env)
    h0 = _h0(ps)
    no_measure = plan(hypotheses=lambda i, h: [
        {"id": h, "falsified_by": [], "tested_by": ["D1"]},
        {"new": {"statement": NEW_H, "falsification": "No lower rate."}, "ref": "h_new", "falsified_by": ["M1"],
         "tested_by": ["D2"]}])
    w = DesignWorker()
    out = w.run(_ctx(env, _exec([no_measure])))
    assert out.status == "rerun"
    assert f"hypothesis {h0} has no measurement that can falsify it" in out.reason
    assert "names M1, which is no measurement of a design item that tests it" in out.reason
    assert not w.gate(ps, 1).ok

    # the next attempt gets that guidance; the edge limit of reruns sends the stage back to argue
    record_edge_taken(ps, "design->design", 2, actor="tester")

    def again(inputs):
        assert f"hypothesis {h0} has no measurement that can falsify it" in inputs["guidance"]
        assert inputs["previous_design"]["body"]["items"][0]["id"] == "D1"
        return no_measure(inputs)

    out = w.run(_ctx(env, _exec([again]), attempt=2, guidance=out.reason))
    assert out.status == "back_edge" and out.to == "argue"
    assert f"hypothesis {h0} has no measurement that can falsify it" in out.reason
    assert design(ps, design_notes(ps, 2, kind="draft")[0]["design_id"])["status"] == "draft"
    # at design->argue's limit P1 degrades, and it never freezes a design that was not approved
    deg = w.degrade(_ctx(env, _exec([]), attempt=2), [out.reason])
    assert deg.status == "failed" and "never freezes a design the vote did not approve" in deg.reason


def test_feasibility_claim_must_be_supported(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    # P1: an uncertain feasibility claim is narrowed (or withdrawn), never voted up
    ex = _exec([plan()], uncertain=("can be",), narrowed=[{"text": NARROWER, "kind": "feasibility"}])
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    svc = ArgumentService(ps)
    original, rewrite = feasibility_claims(ps, 1)
    o, r = svc.get_claim(original), svc.get_claim(rewrite)
    assert (o["kind"], o["status"], o["generator_vendor"]) == ("feasibility", "withdrawn", "anthropic")
    assert (r["status"], r["narrowed_from"], r["text"]) == ("supported", original, NARROWER)
    verifiers = {x["verifier"] for x in ps.db.execute("SELECT verifier FROM verifications WHERE target_id=?",
                                                      (original,))}
    assert {"V-SUPPORT", "V-NUM", "V-HEDGE"} <= verifiers
    judged = [c for c in ex.calls if c["schema"] == "JudgeVerdict"]
    assert judged and all(c["role"] != "judge_3" for c in judged)  # the Ideator's vendor never judges its claim
    (vote_call, *_) = [c for c in ex.calls if c["schema"] == "VoteOut"]
    assert [f["text"] for f in vote_call["inputs"]["subject"]["feasibility_claims"]] == [NARROWER]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert [(c["claim_id"], c["narrowed_from"]) for c in design_limitations(ps, 1)["claims"]["narrowed"]] == [
        (rewrite, original)]
    assert [c["claim_id"] for c in design_limitations(ps, 1)["claims"]["withdrawn"]] == [original]


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_feasibility_claim_is_decided_by_you(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    # judges agreeing it is unsupported: the structure fails, the Ideator revises
    unsupported = Exec({("ideator", "DesignDraft"): [plan()],
                        ("judge", "JudgeVerdict"): [{"label": "unsupported", "quoted_spans": []}],
                        ("judge_2", "JudgeVerdict"): [{"label": "unsupported", "quoted_spans": []}]})
    out = w.run(_ctx(env, unsupported))
    assert out.status == "rerun"
    (cid,) = feasibility_claims(ps, 1)
    assert f"{cid}: status unsupported" in out.reason
    # uncertain (the judges disagree): your design_approval decides it (the earlier new hypothesis is tested by id)
    out = w.run(_ctx(env, _exec([retest()], uncertain=("can be",)), attempt=2))
    assert out.status == "waiting_human"
    cp = get_checkpoint(ps, out.checkpoint_id)
    (cid,) = feasibility_claims(ps, 2)
    assert cp["kind"] == "design_approval" and cp["options"]["claim_ids"] == [cid] and FEASIBLE in cp["prompt"]
    out = w.on_answer(_ctx(env, _exec([]), attempt=2), _answer(ps, cp["id"], {"decision": "approve"}))
    assert out.status == "waiting_human" and "every listed claim" in out.reason
    again = get_checkpoint(ps, out.checkpoint_id)
    assert again["options"]["replaces"] == cp["id"]
    out = w.on_answer(_ctx(env, _exec([]), attempt=2),
                      _answer(ps, again["id"], {"decision": "approve", "claims": {cid: "withdraw"}}))
    assert out.status == "done", out.reason
    assert ArgumentService(ps).get_claim(cid)["status"] == "withdrawn"
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_design_approval_freezes_and_preregisters_hypotheses(env):
    _, ps, _ = env
    _argued(env)
    h0 = _h0(ps)
    extra = add_hypothesis(ps, frozen_rq(ps)["id"], "Aspirin raises bleeding.", "No more bleeding.", actor="tester")
    w = DesignWorker()
    ex = _exec([plan(dropped=lambda i, h: [{"id": extra, "reason": "No available method measures it."}])])
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "design_approval" and cp["stage"] == "design"
    assert cp["options"]["choices"] == ["approve", "revise", "back"] and cp["options"]["claim_ids"] == []
    for text in ("D1", "M1", "stroke rate M1", NEW_H, "No available method measures it.", FEASIBLE):
        assert text in cp["prompt"], text
    assert _hyp(ps, extra)["status"] == "proposed"  # nothing is dropped or pre-registered before approval
    new = next(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"]) if h["statement"] == NEW_H)
    assert _hyp(ps, new)["rival_of"] == h0
    answered = _answer(ps, cp["id"], {"decision": "approve"})
    out = w.on_answer(_ctx(env, ex), answered)
    assert out.status == "done", out.reason
    assert w.on_answer(_ctx(env, ex), answered).status == "done"  # applying the answer again changes nothing
    (n,) = design_notes(ps, 1, kind="draft")
    d = design(ps, n["design_id"])
    assert d["status"] == "frozen" and d["frozen_hash"]
    assert d["body"]["falsified_by"] == {h0: ["M1"], new: ["M2"]}
    for hid in (h0, new):
        h = _hyp(ps, hid)
        assert (h["status"], h["prereg_hash"]) == ("tested_by_design", d["frozen_hash"])
    assert (_hyp(ps, extra)["status"], _hyp(ps, extra)["prereg_hash"]) == ("dropped", None)
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]  # P2 never votes
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert design_limitations(ps, 1)["dropped"] == [{"id": extra, "reason": "No available method measures it."}]
    assert len([c for c in list_checkpoints(ps) if c["kind"] == "design_approval"]) == 1

    # revise reruns with your comment
    out = w.run(_ctx(env, _exec([retest()]), attempt=2))
    cp = get_checkpoint(ps, out.checkpoint_id)
    out = w.on_answer(_ctx(env, ex, attempt=2), _answer(ps, cp["id"], {"decision": "revise", "comment": "Add M3."}))
    assert (out.status, out.reason) == ("rerun", "Add M3.")


def test_dropped_hypothesis_cannot_be_designed(env):
    _, ps, _ = env
    h0 = _h0(ps)
    set_hypothesis_status(ps, h0, "dropped", actor="tester")
    body = DesignIn.model_validate({"items": [item("D1", ["M1"], [h0])], "expected_results": "E.",
                                    "alternative_explanations": []})
    with pytest.raises(ValueError, match="dropped"):
        set_design(ps, body, stage="design", attempt=1, actor="ideator")


def test_p1_design_vote_disagreement_reruns_with_reasons(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    ex = _exec([plan()], votes=_votes(["approve"], ["not_approved"]))
    out = w.run(_ctx(env, ex))
    assert out.status == "rerun"
    assert "judge says approve" in out.reason and "judge says not_approved" in out.reason
    assert "not reviewed by a human" in out.reason
    (n,) = design_notes(ps, 1, kind="draft")
    (v,) = votes_on(ps, "design", n["design_id"])
    assert (v["decision"], v["conservative"]) == ("not_approved", 1)
    assert {o["vendor"] for o in v["opinions"]}.isdisjoint({"anthropic"})
    assert design(ps, n["design_id"])["status"] == "draft"
    assert {h["status"] for h in hypotheses(ps, frozen_rq(ps)["id"])} == {"proposed"}
    assert not w.gate(ps, 1).ok

    def revised(inputs):
        assert "judge says not_approved" in inputs["guidance"]
        assert inputs["previous_design"]["design_id"] == n["design_id"]
        return retest()(inputs)

    out = w.run(_ctx(env, _exec([revised]), attempt=2, guidance=out.reason))
    assert out.status == "done", out.reason
    assert "not reviewed by a human" in out.reason
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_design_back_edges(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    out = w.run(_ctx(env, _exec([{"needs_argument": "No supported claim motivates a testable effect size."}])))
    assert (out.status, out.to) == ("back_edge", "argue") and "effect size" in out.reason
    out = w.run(_ctx(env, _exec([{"rq_untestable": "No method can measure the outcome."}]), attempt=2))
    assert (out.status, out.to) == ("back_edge", "question") and "measure the outcome" in out.reason
    assert ps.db.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 0
    # your design_approval may send it back to argue
    out = w.run(_ctx(env, _exec([plan()]), attempt=3))
    cp = get_checkpoint(ps, out.checkpoint_id)
    out = w.on_answer(_ctx(env, _exec([]), attempt=3),
                      _answer(ps, cp["id"], {"decision": "back", "comment": "The argument is too weak."}))
    assert (out.status, out.to) == ("back_edge", "argue") and "too weak" in out.reason
