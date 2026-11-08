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
from rk.discovery.executor import RateCapped
from rk.discovery.store import (
    add_hypothesis,
    frozen_rq,
    hypotheses,
    set_hypothesis_status,
)
from rk.orchestrator.engine import Orchestrator, default_workers
from rk.orchestrator.state import (
    latest_attempt,
    record_attempt,
    record_edge_taken,
    record_passed,
    record_ran,
)
from rk.production.argue import ArgueWorker
from rk.production.design import (
    DesignDraft,
    DesignWorker,
    approved_design,
    design_limitations,
    design_notes,
    feasibility_claims,
)
from rk.production.models import DesignBody, DesignIn
from rk.production.store import design, set_design
from rk.roles.prompts import build_system_prompt
from tests.production_fakes import (
    make_frozen_project,
    production_ctx,
    stage_run,
    votes_on,
)
from tests.test_argue import _exec as _argue_exec
from tests.test_argue import _judges, _synthesize, critique, draft
from tests.test_synthesis import CapOnce, Exec

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


def _count(ps) -> int:
    """How many hypotheses the frozen RQ has."""
    return len(hypotheses(ps, frozen_rq(ps)["id"]))


def _approved(ps, attempt: int) -> dict:
    (n,) = design_notes(ps, attempt, kind="approved")
    return n


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


class Capped(Exec):
    """Raises ``RateCapped`` at the first judge task once ``armed``."""
    armed = False

    def run(self, role, instructions, inputs, output_schema, **kw):
        if self.armed and role == "judge":
            self.armed = False
            raise RateCapped("codex_cli", 60)
        return super().run(role, instructions, inputs, output_schema, **kw)


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
    res = w.gate(ps, 1)
    assert not res.ok
    text = "\n".join(res.failures)
    assert "design item D2 tests no hypothesis" in text
    assert "hypothesis h_new is tested by no design item" in text
    assert "hypothesis h_new: its tested_by names D1, which does not list it" in text
    assert "not approved" in text
    for line in ("design item D2 tests no hypothesis", "hypothesis h_new is tested by no design item"):
        assert line in out.reason
    assert _count(ps) == 1  # the new hypothesis of an attempt that was not approved never reaches the RQ

    # an item listing a hypothesis whose tested_by leaves it out fails too
    one_way = plan(hypotheses=lambda i, h0: [{"id": h0, "falsified_by": ["M1"], "tested_by": []}],
                   design=lambda i, h0: {"items": [item("D1", ["M1"], [h0])], "expected_results": "E.",
                                         "alternative_explanations": []})
    out = w.run(_ctx(env, _exec([one_way]), attempt=2))
    assert out.status == "rerun"
    h0 = _h0(ps)
    assert f"hypothesis {h0}: design item D1 lists it, but its tested_by does not name D1" in out.reason

    # attempt 3 links both ways
    out = w.run(_ctx(env, _exec([plan()]), attempt=3))
    assert out.status == "done", out.reason
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures
    assert _count(ps) == 2


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
    assert ps.db.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 0 and _count(ps) == 1
    # at design->argue's limit P1 degrades: it never freezes a design that was not approved, and asks no role
    ex = _exec([])
    deg = w.degrade(_ctx(env, ex, attempt=2), [out.reason])
    assert deg.status == "failed" and "never freezes a design the vote did not approve" in deg.reason
    assert f"hypothesis {h0} has no measurement that can falsify it" in deg.reason and not ex.calls


def test_feasibility_claim_must_be_supported(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    # P1: an uncertain feasibility claim is narrowed (or withdrawn), never voted up
    ex = _exec([plan()], uncertain=("can be",), narrowed=[{"text": NARROWER, "kind": "fact"}])
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    svc = ArgumentService(ps)
    original, rewrite = feasibility_claims(ps, 1)
    o, r = svc.get_claim(original), svc.get_claim(rewrite)
    assert (o["kind"], o["status"], o["generator_vendor"]) == ("feasibility", "withdrawn", "anthropic")
    assert (r["status"], r["narrowed_from"], r["text"], r["kind"]) == ("supported", original, NARROWER, "feasibility")
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
    # uncertain (the judges disagree): your design_approval decides it
    out = w.run(_ctx(env, _exec([plan()], uncertain=("can be",)), attempt=2))
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
    assert _hyp(ps, extra)["status"] == "proposed"  # nothing is added, dropped or pre-registered before approval
    assert _count(ps) == 2
    answered = _answer(ps, cp["id"], {"decision": "approve"})
    out = w.on_answer(_ctx(env, ex), answered)
    assert out.status == "done", out.reason
    assert w.on_answer(_ctx(env, ex), answered).status == "done"  # applying the answer again changes nothing
    assert _count(ps) == 3
    new = next(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"]) if h["statement"] == NEW_H)
    assert _hyp(ps, new)["rival_of"] == h0
    d = design(ps, _approved(ps, 1)["design_id"])
    assert d["status"] == "frozen" and d["frozen_hash"]
    assert d["body"]["falsified_by"] == {h0: ["M1"], new: ["M2"]}
    assert d["body"]["hypotheses"] == {h0: {"statement": "H.", "falsification": "X."},
                                       new: {"statement": NEW_H, "falsification": "No lower stroke rate."}}
    assert d["body"]["items"][1]["hypothesis_ids"] == [new]
    for hid in (h0, new):
        h = _hyp(ps, hid)
        assert (h["status"], h["prereg_hash"]) == ("tested_by_design", d["frozen_hash"])
    assert (_hyp(ps, extra)["status"], _hyp(ps, extra)["prereg_hash"]) == ("dropped", None)
    assert not [c for c in ex.calls if c["schema"] == "VoteOut"]  # P2 never votes
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert design_limitations(ps, 1)["dropped"] == [{"id": extra, "reason": "No available method measures it."}]
    assert len([c for c in list_checkpoints(ps) if c["kind"] == "design_approval"]) == 1

    # a new hypothesis that restates one of the RQ's (whitespace and case aside) is that hypothesis
    restated = plan(hypotheses=lambda i, h: [
        {"id": h, "falsified_by": ["M1"], "tested_by": ["D1"]},
        {"new": {"statement": f"  {NEW_H.upper()} ", "falsification": "no lower  stroke rate."}, "ref": "h_new",
         "falsified_by": ["M2"], "tested_by": ["D2"]}])
    out = w.run(_ctx(env, _exec([restated]), attempt=2))
    assert out.status == "waiting_human", out.reason
    assert [e["id"] for e in design_notes(ps, 2, kind="draft")[0]["hypotheses"]] == [h0, new]
    cp = get_checkpoint(ps, out.checkpoint_id)
    # revise reruns with your comment
    out = w.on_answer(_ctx(env, ex, attempt=2), _answer(ps, cp["id"], {"decision": "revise", "comment": "Add M3."}))
    assert (out.status, out.reason) == ("rerun", "Add M3.")
    assert _count(ps) == 3


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
    (v,) = votes_on(ps, "design", n["draft_id"])
    assert (v["decision"], v["conservative"]) == ("not_approved", 1)
    assert {o["vendor"] for o in v["opinions"]}.isdisjoint({"anthropic"})
    assert ps.db.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 0
    assert [h["status"] for h in hypotheses(ps, frozen_rq(ps)["id"])] == ["proposed"]
    assert not w.gate(ps, 1).ok

    def revised(inputs):
        assert "judge says not_approved" in inputs["guidance"]
        assert inputs["previous_design"]["draft_id"] == n["draft_id"]
        return plan()(inputs)

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
    assert ps.db.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 0
    # your design_approval may send it back to argue
    out = w.run(_ctx(env, _exec([plan()]), attempt=2))
    cp = get_checkpoint(ps, out.checkpoint_id)
    out = w.on_answer(_ctx(env, _exec([]), attempt=2),
                      _answer(ps, cp["id"], {"decision": "back", "comment": "The argument is too weak."}))
    assert (out.status, out.to) == ("back_edge", "argue") and "too weak" in out.reason


UNTESTABLE = "No method can measure the outcome."


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_ideator_untestable_is_yours_to_decide(env):
    """I5 (design): the Ideator's rq_untestable is an input only. P2 opens your design_approval with its reason,
    offering back to the question, a revision and, when its draft passes its structure, the approval."""
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    # no draft: back to the question or a revision
    out = w.run(_ctx(env, _exec([{"rq_untestable": UNTESTABLE}])))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "design_approval" and cp["options"]["untestable"] == UNTESTABLE and UNTESTABLE in cp["prompt"]
    assert cp["options"]["choices"] == ["revise", "question"] and cp["options"]["draft_id"] is None
    out = w.on_answer(_ctx(env, _exec([])), _answer(ps, cp["id"], {"decision": "approve"}))
    assert out.status == "waiting_human" and "approve" in out.reason  # not one of its choices: asked again
    again = get_checkpoint(ps, out.checkpoint_id)
    assert again["options"]["replaces"] == cp["id"] and again["options"]["choices"] == ["revise", "question"]
    assert again["options"]["untestable"] == UNTESTABLE
    out = w.on_answer(_ctx(env, _exec([])), _answer(ps, again["id"], {"decision": "question",
                                                                      "comment": "Narrow the outcome."}))
    assert (out.status, out.to) == ("back_edge", "question")
    assert UNTESTABLE in out.reason and "Narrow the outcome." in out.reason
    # a draft that passes its structure: the approval too; revise reruns with your comment
    out = w.run(_ctx(env, _exec([plan(rq_untestable=UNTESTABLE)]), attempt=2))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["options"]["choices"] == ["approve", "revise", "question"] and cp["options"]["draft_id"] == "design:2"
    assert UNTESTABLE in cp["prompt"] and NEW_H in cp["prompt"]
    out = w.on_answer(_ctx(env, _exec([]), attempt=2),
                      _answer(ps, cp["id"], {"decision": "revise", "comment": "Use a registry."}))
    assert (out.status, out.reason) == ("rerun", "Use a registry.")
    out = w.run(_ctx(env, _exec([plan(rq_untestable=UNTESTABLE)]), attempt=3))
    out = w.on_answer(_ctx(env, _exec([]), attempt=3), _answer(ps, out.checkpoint_id, {"decision": "approve"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures


def test_p1_ideator_untestable_is_decided_by_a_vote(env):
    """I5 (design), P1: the judges vote whether the RQ is testable (conservative: untestable, back to the question);
    testable, a draft goes on to its approval vote and no draft reruns with their reasons."""
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    out = w.run(_ctx(env, _exec([plan(rq_untestable=UNTESTABLE)], votes=_votes(["testable"], ["untestable"]))))
    assert (out.status, out.to) == ("back_edge", "question"), out.reason
    assert UNTESTABLE in out.reason and "not reviewed by a human" in out.reason
    (v,) = votes_on(ps, "design_rq", "design:1")
    assert (v["decision"], v["conservative"]) == ("untestable", 1)
    assert w.run(_ctx(env, _exec([]))).status == "back_edge"  # resumed: the noted back-edge, no vote again
    out = w.run(_ctx(env, _exec([{"rq_untestable": UNTESTABLE}], votes=_votes(["testable"], ["testable"])), attempt=2))
    assert out.status == "rerun" and "judge says testable" in out.reason and UNTESTABLE in out.reason
    out = w.run(_ctx(env, _exec([plan(rq_untestable=UNTESTABLE)],
                                votes=_votes(["testable", "approve"], ["testable", "approve"])), attempt=3))
    assert out.status == "done", out.reason
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures


def test_interrupted_attempt_neither_duplicates_nor_orphans(env):
    """A rate cap after the draft is stored (its feasibility claim being verified) interrupts the attempt; its new
    hypothesis was never added, so the next attempt neither duplicates it nor fails on an orphan."""
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    ex = Capped({**_exec([plan()]).script})
    ex.armed = True
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert design_notes(ps, 1, kind="draft") and _count(ps) == 1
    out = w.run(_ctx(env, _exec([plan()]), attempt=2))
    assert out.status == "done", out.reason
    assert [h["statement"] for h in hypotheses(ps, frozen_rq(ps)["id"])] == ["H.", NEW_H]
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


def test_measurement_ids_unique_across_design(env):
    body = {"items": [item("D1", ["M1"], ["h"]), item("D2", ["M1"], ["h"])], "expected_results": "E.",
            "alternative_explanations": []}
    with pytest.raises(ValueError, match=r"measurement ids used by more than one design item: \['M1'\]"):
        DesignBody.model_validate(body)


def _once(real, error: str):
    """``real``, except that its first call raises (a crash at that write)."""
    calls = {"n": 0}

    def wrapped(*args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(error)
        return real(*args, **kw)
    return wrapped


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
@pytest.mark.parametrize("crash_at", ["set_design", "set_hypothesis_status"])
def test_crashed_approval_finishes_when_applied_again(env, monkeypatch, crash_at):
    """A crash in the middle of applying your approval (after the new hypothesis is added; or after the design is
    frozen) leaves the answer to be applied again: that finishes the approval, with no rerun and no duplicate."""
    import rk.production.design as mod

    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    out = w.run(_ctx(env, _exec([plan()])))
    answered = _answer(ps, out.checkpoint_id, {"decision": "approve"})
    monkeypatch.setattr(mod, crash_at, _once(getattr(mod, crash_at), f"crash at {crash_at}"))
    with pytest.raises(RuntimeError, match="crash at"):
        w.on_answer(_ctx(env, _exec([])), answered)
    assert _count(ps) == 2 and not design_notes(ps, 1, kind="approved")
    (approving,) = design_notes(ps, 1, kind="approving")
    assert approving["to_add"] == ["h_new"] and list(approving["ids"].values()) == [_h0(ps)]
    # the hypothesis the crash left added is the draft's own: the structure still holds, only the approval is missing
    assert w.gate(ps, 1).failures == [f"the design {approving['draft_id']} was not approved: no design is frozen"]
    out = w.on_answer(_ctx(env, _exec([])), answered)
    assert out.status == "done", out.reason
    assert _count(ps) == 2
    assert ps.db.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 1
    assert len(design_notes(ps, 1, kind="approving")) == 1
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_new_hypotheses_are_the_ideators_and_never_twice(env):
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    twice = plan(hypotheses=lambda i, h: [
        {"id": h, "falsified_by": ["M1"], "tested_by": ["D1"]},
        {"new": {"statement": NEW_H, "falsification": "No lower rate."}, "ref": "a", "falsified_by": ["M2"],
         "tested_by": ["D2"]},
        {"new": {"statement": f" {NEW_H.lower()}", "falsification": "no lower  rate."}, "ref": "b",
         "falsified_by": ["M2"], "tested_by": ["D2"]}],
        design=lambda i, h: {"items": [item("D1", ["M1"], [h]), item("D2", ["M2"], ["a", "b"])],
                             "expected_results": "E.", "alternative_explanations": []})
    out = w.run(_ctx(env, _exec([twice])))
    assert out.status == "rerun" and "the new hypotheses 'a' and 'b' state the same hypothesis" in out.reason
    assert not design_notes(ps, 1, kind="draft")
    cp = w.run(_ctx(env, _exec([plan()]), attempt=2)).checkpoint_id
    out = w.on_answer(_ctx(env, _exec([]), attempt=2), _answer(ps, cp, {"decision": "approve"}))
    assert out.status == "done", out.reason
    new = next(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"]) if h["statement"] == NEW_H)
    rows = {r["type"]: (r["actor"], r["actor_kind"]) for r in ps.db.execute(
        "SELECT type, actor, actor_kind FROM events WHERE entity_id=? ORDER BY seq", (new,))}
    assert rows == {"hypothesis.added": ("ideator", "agent"), "hypothesis.status_set": ("researcher", "human")}
    assert (_approved(ps, 2)["by"], _approved(ps, 2)["checkpoint_id"]) == ("human", cp)


# ---- fix wave 1: resuming an interrupted attempt in place ----
def _after(real, error: str):
    """``real``, then a crash (the write is done, nothing after it is)."""
    def wrapped(*args, **kw):
        real(*args, **kw)
        raise RuntimeError(error)
    return wrapped


def _feasibility_ids(ps) -> list[str]:
    return sorted(c["id"] for c in ArgumentService(ps).list_claims() if c["kind"] == "feasibility")


def test_interrupted_attempt_resumes_in_place(env):
    """A rate cap at the P1 design vote (after the draft is noted and its feasibility claim verified) interrupts
    attempt 1; the next step resumes that attempt: the Ideator is not asked again and no claim is orphaned."""
    k, ps, lib = env
    _argued(env)
    ex = CapOnce(_exec([plan(), plan()]).script, at=("judge", "VoteOut"))
    o, run_id = stage_run(k, ps, lib, "design", ex)
    assert o.step("demo", run_id, actor="tester") == "waiting_rate"
    assert o.step("demo", run_id, actor="tester") == "done"
    assert latest_attempt(ps, "design") == 1 and len(ex.of("ideator", "DesignDraft")) == 1
    assert _feasibility_ids(ps) == sorted(feasibility_claims(ps, 1))
    assert _count(ps) == 2 and len(design_notes(ps, 1, kind="vote")) == 1


def test_p1_crash_after_freezing_finishes_with_one_design(env, monkeypatch):
    """T6: a crash right after the P1 approval froze the design; the next step finishes the approval in the same
    attempt: one frozen design, the same hypothesis ids, the Ideator and the vote not asked again."""
    import rk.production.design as mod

    k, ps, lib = env
    _argued(env)
    ex = _exec([plan(), plan()])
    o, run_id = stage_run(k, ps, lib, "design", ex)
    monkeypatch.setattr(mod, "freeze_design", _after(mod.freeze_design, "crash after freeze_design"))
    with pytest.raises(RuntimeError, match="crash after freeze_design"):
        o.step("demo", run_id, actor="tester")
    ids = sorted(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"]))
    votes = len(ex.of("judge", "VoteOut"))
    monkeypatch.undo()
    assert o.step("demo", run_id, actor="tester") == "done"
    assert latest_attempt(ps, "design") == 1 and len(ex.of("ideator", "DesignDraft")) == 1
    assert len(ex.of("judge", "VoteOut")) == votes
    assert sorted(h["id"] for h in hypotheses(ps, frozen_rq(ps)["id"])) == ids and len(ids) == 2
    assert [tuple(r) for r in ps.db.execute("SELECT status, attempt FROM designs")] == [("frozen", 1)]
    assert DesignWorker().gate(ps, 1).ok


def test_interrupted_narrowing_resumes_with_its_rewrite(env):
    """A rate cap while the judges verify the P1 rewrite of an uncertain feasibility claim: resumed, the rewrite is
    decided as it is (the Arguer is not asked for another), so no second rewrite is left orphaned."""
    _, ps, _ = env
    _argued(env)
    w = DesignWorker()
    narrowed = [{"text": NARROWER, "kind": "fact"}] * 2
    ex = CapOnce(_exec([plan()], uncertain=("can be",), narrowed=narrowed).script, at=("judge", "JudgeVerdict"), n=2)
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("arguer", "NarrowedClaim")) == 1
    original, rewrite = feasibility_claims(ps, 1)
    svc = ArgumentService(ps)
    assert (svc.get_claim(original)["status"], svc.get_claim(rewrite)["status"]) == ("withdrawn", "supported")
    assert _feasibility_ids(ps) == sorted([original, rewrite])


def test_approved_design_is_the_one_the_passed_attempt_approved(env):
    """``approved_design``: the design the passed attempt approved (its ``approved`` note), not merely the latest
    design row (e.g. one an interrupted approval left)."""
    k, ps, lib = env
    _argued(env)
    o, run_id = stage_run(k, ps, lib, "design", _exec([plan()]))
    assert approved_design(ps) is None
    assert o.step("demo", run_id, actor="tester") == "done"
    approved = approved_design(ps)
    assert approved["id"] == _approved(ps, 1)["design_id"] and approved["status"] == "frozen"
    later = set_design(ps, DesignIn.model_validate(approved["body"]), stage="design", attempt=2, actor="ideator")
    assert later != approved["id"] and approved_design(ps)["id"] == approved["id"]


def test_vote_recorded_before_a_crash_is_reused(env, monkeypatch):
    """A crash between the P1 vote's record and its note: resumed, the attempt takes the recorded vote
    (``reuse=True``) instead of asking the judges again."""
    import rk.production.vote as vote_mod

    _, ps, _ = env
    _argued(env)
    real, calls = vote_mod.add_vote, []

    def crash_once(*args, **kw):
        vid = real(*args, **kw)
        calls.append(vid)
        if len(calls) == 1:
            raise RuntimeError("crash after the vote")
        return vid

    monkeypatch.setattr(vote_mod, "add_vote", crash_once)
    ex = _exec([plan()])
    w = DesignWorker()
    with pytest.raises(RuntimeError, match="crash after"):
        w.run(_ctx(env, ex))
    asked = len(ex.of("judge", "VoteOut"))
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("judge", "VoteOut")) == asked and len(calls) == 1
    assert ps.db.execute("SELECT COUNT(*) FROM votes WHERE subject_type='design'").fetchone()[0] == 1
    assert design_notes(ps, 1, kind="vote")[0]["vote_id"] == calls[0]
