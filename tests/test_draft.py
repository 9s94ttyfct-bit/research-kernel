"""Plan 5c Task 2: the draft stage. The Writer drafts every required section of the paper type; the kernel stores each
version, verifies every sentence (V-COVER, V-NUM, V-HEDGE, V-QUOTE, G-SUPPORT), drafts a failing section again with
its failures as guidance, writes the disclosure itself and gates on what it stored."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from rk.argument import ArgumentService
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.config import load_settings, save_settings
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES
from rk.orchestrator.state import (
    record_attempt,
    record_passed,
    record_ran,
    record_reopened,
)
from rk.production.argue import argument_claims, main_claim
from rk.production.artifacts import ArtifactIn, add_artifact
from rk.production.design import DesignWorker, approved_design
from rk.production.disclosure import disclosure_problems, render_disclosure
from rk.production.draft import (
    PRISMA_EVENT,
    DraftWorker,
    SectionDraft,
    draft_notes,
    prisma_event,
    unmet_support,
)
from rk.production.draft import load_inputs as draft_inputs
from rk.production.manuscript import (
    draft_text,
    draft_versions,
    parse_section,
    source_markers,
)
from rk.production.sections import SECTIONS, required_sections
from rk.production.synthesis import synthesis_claims
from rk.roles.prompts import build_system_prompt
from rk.verify.cover import EVENT_NUMBERS, verify_sentence
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_balance import contest, object_to
from tests.test_design import _argued, plan
from tests.test_design import _exec as _design_exec
from tests.test_synthesis import CapOnce, Exec

PAPER_TYPES = ("systematic_review", "scoping_review", "critical_review", "research_proposal")


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http,
                                        **{"paper_type": "critical_review", **getattr(request, "param", {})})
    yield k, ps, lib
    k.close()


def _ready(env) -> None:
    """Passed synthesize and argue attempts; a research proposal's design approved by the P1 vote and passed."""
    k, ps, lib = env
    _argued(env)
    if ps.meta("paper_type") == "research_proposal":
        ctx = production_ctx(k, ps, lib, _design_exec([plan()]), stage="design")
        out = DesignWorker().run(ctx)
        assert out.status == "done", out.reason
        record_attempt(ps, "design", 1, run_id=None, guidance="", requested=None, actor="tester")
        record_ran(ps, "design", 1, "done", "", actor="tester")
        record_passed(ps, "design", 1, actor="tester")
        assert approved_design(ps) is not None


def _sentence(c: dict) -> str:
    return f"{c['text'].rstrip('.')} [[clm:{c['claim_id']}]]."


def writes(*, only_main: bool = False, bad: str | None = None, extra: str = ""):
    """A Writer output: one sentence per supported main claim and finding (only the main claim with ``only_main``),
    a proposal's design cited in its methods; ``bad`` a sentence put first, ``extra`` one put last."""
    def out(inputs):
        cs = [c for c in inputs["claims"] if c["status"] == "supported"
              and (c["role"] == "main" or (c["role"] == "finding" and not only_main))]
        parts = [_sentence(c) for c in cs]
        if inputs.get("design") and inputs["section"]["id"] == "methods":
            parts.append(f"The design tests the hypotheses [[plan:{inputs['design']['id']}]].")
        return {"markdown": " ".join([*([bad] if bad else []), *parts, *([extra] if extra else [])]) + "\n"}
    return out


def _writer(outputs) -> Exec:
    return Exec({("writer", "SectionDraft"): list(outputs)})


def _ctx(env, ex, attempt: int = 1, guidance: str = ""):
    k, ps, lib = env
    return replace(production_ctx(k, ps, lib, ex, stage="draft", attempt=attempt), guidance=guidance)


def _ids(ps) -> list[str]:
    return [s.id for s in draft_inputs(ps).sections]


def _writer_sections(ex: Exec) -> list[str]:
    return [c["inputs"]["section"]["id"] for c in ex.of("writer", "SectionDraft")]


# ---- tests ----
def test_draft_is_a_default_worker_with_its_edges_and_prompt():
    assert isinstance(default_workers()["draft"], DraftWorker)
    assert {("draft", "argue"), ("draft", "design")} <= BACK_EDGES
    assert load_settings().draft_section_retries == 2
    prompt = build_system_prompt("writer", SectionDraft)
    for word in ("`SectionDraft`", "[[clm:clm_…]]", "[[run:evt_…]]", "[[plan:hyp_…]]",
                 "Never state a number a marker target does not contain", "missing_support", "missing_design",
                 "only for your own inferences and word them tentatively"):
        assert word in prompt, word


@pytest.mark.parametrize("env", [{"paper_type": t} for t in PAPER_TYPES], indirect=True,
                         ids=list(PAPER_TYPES))
def test_draft_writes_every_required_section_per_paper_type(env):
    _, ps, _ = env
    _ready(env)
    paper_type = ps.meta("paper_type")
    ex = _writer([writes()] * 20)
    w = DraftWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    ids = _ids(ps)
    if paper_type == "critical_review":
        assert ids == ["introduction", "theme-1", "gaps-agenda", "conclusion", "disclosure"]
        assert required_sections(paper_type, ["design"])[1].title == "design"
    else:
        assert ids == [s.id for s in SECTIONS[paper_type]]
    if paper_type == "scoping_review":
        assert SECTIONS[paper_type][3].title == "Evidence map and thematic synthesis"
    # one Writer task per section in order, none for the kernel's disclosure
    assert _writer_sections(ex) == [i for i in ids if i != "disclosure"]
    for sid in ids:
        (v,) = draft_versions(ps, sid)
        assert v["verdict"]["ok"], (sid, v["verdict"])
    first = ex.of("writer", "SectionDraft")[0]["inputs"]
    assert first["section"]["id"] == ids[0] and first["audience"] and "[[clm:" in first["markers"]
    assert {c["role"] for c in first["claims"]} >= {"main", "finding"}
    assert all({"claim_id", "text", "status"} <= set(c) for c in first["claims"])
    assert first["protocol"]["id"].startswith("prt_") and "limitations" in first
    later = ex.of("writer", "SectionDraft")[1]["inputs"]
    assert [x["id"] for x in later["earlier_sections"]] == [ids[0]]
    types = {e["type"] for e in first["events"]}
    assert (PRISMA_EVENT in types) == (paper_type in ("systematic_review", "scoping_review"))
    if paper_type == "research_proposal":
        assert first["design"]["id"] == approved_design(ps)["id"] and first["hypotheses"]
        methods = draft_text(ps, "methods", 1)
        assert any(m.kind == "plan" for m in source_markers(methods))
    res = w.gate(ps, 1)
    assert res.ok, res.failures


def test_ungrounded_number_fails_sentence_and_redrafts(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    # introduction: every try writes the ungrounded number; theme-1: fixed on its first retry
    outs = [writes(bad=bad)] * 3 + [writes(bad=bad), writes()] + [writes()] * 3
    ex = _writer(outs)
    w = DraftWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert _writer_sections(ex) == ["introduction"] * 3 + ["theme-1"] * 2 + ["gaps-agenda", "conclusion"]
    calls = ex.of("writer", "SectionDraft")
    assert "retry" not in calls[0]["inputs"]
    retry = calls[1]["inputs"]["retry"]
    assert "1234" in retry["previous_draft"]
    (failure,) = retry["failures"]
    assert failure.startswith("sentence 1 “Aspirin was studied in 1234 older people")
    assert "the number 1234 is not in any marker's target" in failure
    assert [v["verdict"]["ok"] for v in draft_versions(ps, "introduction")] == [False] * 3
    assert [v["verdict"]["ok"] for v in draft_versions(ps, "theme-1")] == [False, True]
    res = w.gate(ps, 1)
    assert not res.ok
    (named,) = [f for f in res.failures if f.startswith("section introduction")]
    assert "v3" in named and "Aspirin was studied in 1234 older people" in named and "1234" in named
    assert not any("theme-1" in f for f in res.failures)


def test_a_rerun_after_drafts_own_gate_keeps_the_sections_that_passed(env):
    """Task 9: each draft->draft rerun rewrote every section, so a manuscript whose sections each passed at some attempt
    never passed all at once. A rerun of draft's own gate failure carries a section whose latest draft version passed
    and still verifies (same text, a new version of the attempt recorded with the Writer task that wrote it, no new
    Writer task); the failed ones are drafted again. A
    reopen by another edge (revise, audit, an upstream stage) drafts every section again."""
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    w = DraftWorker()
    w.run(_ctx(env, _writer([writes(bad=bad)] * 3 + [writes(bad=bad), writes()] + [writes()] * 2)))
    assert not w.gate(ps, 1).ok  # introduction failed its three tries; the other sections passed
    record_reopened(ps, "draft", by_edge="draft->draft", guidance="introduction: 1234", actor="t")
    ex = _writer([writes()])
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    assert _writer_sections(ex) == ["introduction"]
    for sec in ("theme-1", "gaps-agenda", "conclusion"):
        before, carried = draft_versions(ps, sec)[-2:]
        assert (carried["attempt"], carried["verdict"]["ok"]) == (2, True)
        assert carried["by_task"] == before["by_task"] is not None  # the audit counts the vendor that wrote it
        assert draft_text(ps, sec, carried["version"]) == draft_text(ps, sec, before["version"])
    assert w.gate(ps, 2).ok
    # reopened by another edge: every section is drafted again
    record_reopened(ps, "draft", by_edge="revise->draft", guidance="say more", actor="t")
    ex = _writer([writes()] * 4)
    assert w.run(_ctx(env, ex, attempt=3)).status == "done"
    assert _writer_sections(ex) == ["introduction", "theme-1", "gaps-agenda", "conclusion"]


def repairs(*fixes: tuple[int, str]):
    """A retry's Writer output: repairs only (sentence number, replacement text; "" deletes), no markdown."""
    return lambda inputs: {"repairs": [{"sentence": n, "text": t} for n, t in fixes]}


def test_a_retry_repairs_only_its_failing_sentences(env):
    """Calibration (critical3, draft attempts 6-10): rewriting the whole section on every retry, the Writer brought in
    new failing sentences each time (one section's failures went 4, 3, 2, 1, 4). A retry's repairs replace or delete
    the failing sentences only and every other sentence stays word for word; a repair of a passing sentence is
    ignored and noted."""
    _, ps, _ = env
    _ready(env)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main_claim(ps, 1)}]]."
    ex = _writer([writes(bad=bad), repairs((1, ""), (2, "Rewritten [[inf]]."))] + [writes()] * 3)
    w = DraftWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    first, second = draft_versions(ps, "introduction")
    before = draft_text(ps, "introduction", first["version"])
    after = draft_text(ps, "introduction", second["version"])
    assert after == before.replace(bad + " ", "", 1) and second["verdict"]["ok"]
    (note,) = draft_notes(ps, 1, kind="repaired")
    assert note["sentences"] == [1, 2] and note["ignored"] == ["sentence 2: not a failing sentence of the previous draft"]


def test_a_section_failing_at_draft_reruns_is_repaired_from_its_last_version(env):
    """A draft->draft rerun gives a section that failed its tries the previous attempt's last version to repair, not a
    blank page, so the failures it had fixed stay fixed."""
    _, ps, _ = env
    w = _failed_introduction(env)
    ex = _writer([repairs((1, ""))])
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    (call,) = ex.of("writer", "SectionDraft")
    assert "1234" in call["inputs"]["retry"]["previous_draft"]
    last = draft_versions(ps, "introduction")[-1]
    assert (last["attempt"], last["verdict"]["ok"]) == (2, True)
    assert "1234" not in draft_text(ps, "introduction", last["version"])
    assert w.gate(ps, 2).ok


def _failed_introduction(env) -> DraftWorker:
    """Attempt 1: introduction fails its three tries, the other sections pass; then reopened by draft->draft."""
    _, ps, _ = env
    _ready(env)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main_claim(ps, 1)}]]."
    w = DraftWorker()
    w.run(_ctx(env, _writer([writes(bad=bad)] * 3 + [writes(bad=bad), writes()] + [writes()] * 2)))
    record_reopened(ps, "draft", by_edge="draft->draft", guidance="introduction: 1234", actor="t")
    return w


def test_a_passed_section_whose_file_was_altered_is_drafted_again_not_carried(env):
    _, ps, _ = env
    w = _failed_introduction(env)
    (ps.root / f"artifacts/drafts/gaps-agenda.v{draft_versions(ps, 'gaps-agenda')[-1]['version']}.md").write_text(
        "Unmarked text with 999 in it.\n")
    ex = _writer([writes()] * 2)
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    assert _writer_sections(ex) == ["introduction", "gaps-agenda"]
    assert w.gate(ps, 2).ok


def test_a_passed_section_that_no_longer_verifies_is_drafted_again(env):
    """A claim it cites was withdrawn since: its fresh verdict fails, so it is not carried."""
    _, ps, _ = env
    w = _failed_introduction(env)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main_claim(ps, 1)}]]."
    first = _writer([writes(bad=bad)] * 3)  # attempt 2: introduction fails again, the others are carried
    w.run(_ctx(env, first, attempt=2))
    assert _writer_sections(first) == ["introduction"] * 3
    gone = next(c["claim_id"] for c in first.of("writer", "SectionDraft")[0]["inputs"]["claims"]
                if c["role"] == "finding")
    ArgumentService(ps).withdraw_claim(gone, reason="the objection holds", actor="tester")
    record_reopened(ps, "draft", by_edge="draft->draft", guidance="introduction: 1234", actor="t")
    ex = _writer([writes()] * 4)
    w.run(_ctx(env, ex, attempt=3))
    assert "theme-1" in _writer_sections(ex)  # it cited the withdrawn finding: drafted again, not carried


def test_carrying_resumes_in_place_after_a_crash(env, monkeypatch):
    import rk.production.draft as draft_mod

    _, ps, _ = env
    w = _failed_introduction(env)
    real = draft_mod.write_draft

    def crash(*a, **kw):
        v = real(*a, **kw)
        if a[1] == "theme-1":  # the first carried section of attempt 2 (introduction is drafted before it)
            raise RuntimeError("crash after the carried version was stored")
        return v

    monkeypatch.setattr(draft_mod, "write_draft", crash)
    with pytest.raises(RuntimeError):
        w.run(_ctx(env, _writer([writes()]), attempt=2))
    monkeypatch.setattr(draft_mod, "write_draft", real)
    ex = _writer([])
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    assert not ex.calls  # introduction passed before the crash; the rest is carried or verified, no Writer task
    for sec in ("theme-1", "gaps-agenda", "conclusion"):  # each carried once, verified
        (v,) = [x for x in draft_versions(ps, sec) if x["attempt"] == 2]
        assert v["verdict"]["ok"]
    assert w.gate(ps, 2).ok


def test_a_rerun_whose_sections_all_passed_drafts_them_again(env):
    """When every section passed and only the manuscript as a whole failed (the main claim cited nowhere), carrying them
    all would fail the same way: every section is drafted again."""
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    w.run(_ctx(env, _writer([_no_main()] * 4)))
    assert any("cited in no section" in f for f in w.gate(ps, 1).failures)
    record_reopened(ps, "draft", by_edge="draft->draft", guidance="the main claim is cited in no section", actor="t")
    ex = _writer([writes()] * 4)
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    assert _writer_sections(ex) == ["introduction", "theme-1", "gaps-agenda", "conclusion"]


def _no_main():
    """A Writer output that cites only the findings (no main claim), every sentence verifying."""
    def out(inputs):
        cs = [c for c in inputs["claims"] if c["status"] == "supported" and c["role"] == "finding"]
        return {"markdown": " ".join(_sentence(c) for c in cs) + "\n"}
    return out



def test_draft_resumes_in_place_without_rewriting_verified_sections(env):
    from rk.discovery.executor import RateCapped

    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    # introduction passes; theme-1 fails its first try and the cap hits its retry (the third Writer task)
    ex = CapOnce({("writer", "SectionDraft"): [writes(), writes(bad=bad), writes(), writes(), writes()]},
                 at=("writer", "SectionDraft"), n=3)
    w = DraftWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert len(draft_versions(ps, "introduction")) == 1 and len(draft_versions(ps, "theme-1")) == 1
    out = w.run(_ctx(env, ex))  # the same attempt, in place
    assert out.status == "done", out.reason
    assert _writer_sections(ex) == ["introduction", "theme-1", "theme-1", "gaps-agenda", "conclusion"]
    resumed = ex.of("writer", "SectionDraft")[2]["inputs"]
    assert "1234" in resumed["retry"]["previous_draft"]
    assert len(draft_versions(ps, "introduction")) == 1  # never drafted again
    assert [v["verdict"]["ok"] for v in draft_versions(ps, "theme-1")] == [False, True]
    assert len(draft_versions(ps, "disclosure")) == 1
    assert w.gate(ps, 1).ok
    # run once more: nothing is drafted again
    again = _writer([])
    assert w.run(_ctx(env, again)).status == "done" and not again.calls


def test_missing_support_takes_draft_to_argue(env):
    _, ps, _ = env
    _ready(env)
    need = {"markdown": "", "missing_support": ["a claim on the bleeding risk of aspirin in older people"]}
    ex = _writer([writes(), need])
    w = DraftWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "back_edge" and out.to == "argue"
    assert "theme-1" in out.reason and "bleeding risk" in out.reason
    assert ("draft", "argue") in BACK_EDGES
    (n,) = draft_notes(ps, 1, kind="back_edge")
    assert n["section"] == "theme-1" and not draft_versions(ps, "theme-1")
    again = _writer([])
    out2 = w.run(_ctx(env, again))  # resumed: the noted back-edge, no Writer task
    assert (out2.status, out2.to, out2.reason) == ("back_edge", "argue", out.reason) and not again.calls


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


NEED = {"markdown": "", "missing_support": ["a claim on the bleeding risk of aspirin in older people"]}


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_missing_support_asks_you_before_going_back_to_argue(env):
    """Calibration (critical review rerun): in P2 the Writer's missing_support took draft back to argue with no one
    asked, for a claim the argument had ruled out as a limitation. You decide now (argue_request): back takes the edge
    with your comment; write has the section written from the claims given, the gap a limitation."""
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    ex = _writer([writes(), NEED])
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human" and not draft_notes(ps, 1, kind="back_edge")
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert (cp["kind"], cp["options"]["section"], cp["options"]["needs"]) == (
        "argue_request", "theme-1", NEED["missing_support"])
    assert w.run(_ctx(env, _writer([]))).checkpoint_id == cp["id"]  # resumed before the wait was linked: no new task
    with pytest.raises(ValueError, match="only be answered by a human"):  # your decision: a P1 vote may never give it
        answer_checkpoint(ps, cp["id"], {"decision": "write"}, answered_by="vote", actor="kernel")
    # an answer that cannot be applied asks again
    first = cp["id"]
    out = w.on_answer(_ctx(env, ex), _answer(ps, first, {"decision": "proceed"}))
    assert out.status == "waiting_human" and "not one of back, write" in out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["prompt"].startswith("Your answer could not be applied")
    # back: the edge with your comment. You answered the replacement before the engine linked it (a crash): the first
    # checkpoint handled again finds its replacement, and a resumed run waits on it; neither opens a third one
    _answer(ps, cp["id"], {"decision": "back", "comment": "Look at the cohorts."})
    assert w.on_answer(_ctx(env, ex), get_checkpoint(ps, first)).checkpoint_id == cp["id"]
    assert w.run(_ctx(env, _writer([]))).checkpoint_id == cp["id"]
    out = w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"]))
    assert (out.status, out.to) == ("back_edge", "argue") and "Look at the cohorts." in out.reason
    assert w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"])).reason == out.reason  # applied again (a crash)
    assert len(draft_notes(ps, 1, kind="back_edge")) == 1


@pytest.mark.parametrize("env", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_p2_write_without_argue_states_the_gap_and_asks_once_per_section(env):
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    ex = _writer([writes(), NEED, NEED, writes(), writes(), writes()])
    cp = get_checkpoint(ps, w.run(_ctx(env, ex)).checkpoint_id)
    # you answer before the engine linked the wait (a crash): the resumed run waits on the answered checkpoint
    _answer(ps, cp["id"], {"decision": "write", "comment": "A limitation."})
    assert w.run(_ctx(env, _writer([]))).checkpoint_id == cp["id"]
    out = w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"]))
    assert out.status == "done", out.reason  # drafting went on in place
    assert _writer_sections(ex) == ["introduction", "theme-1", "theme-1", "theme-1", "gaps-agenda", "conclusion"]
    told, retried = ex.of("writer", "SectionDraft")[2:4]
    # told your decision, it asks again: refused (a failed try), not asked of you a second time
    assert told["inputs"]["argue_declined"] == [{"needs": NEED["missing_support"], "comment": "A limitation."}]
    assert any("decided this section is written without asking argue" in f
               for f in retried["inputs"]["retry"]["failures"])
    assert [c["id"] for c in list_checkpoints(ps, "open")] == [] and not draft_notes(ps, 1, kind="back_edge")
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    declined, *_ = unmet_support(ps)
    assert declined["declined"] == NEED["missing_support"]
    assert "researcher decided it is written without asking the argument stage again" in render_disclosure(ps)


def test_a_section_that_already_went_back_to_argue_writes_without_what_it_lacks(env):
    """Task 9: argue cannot always give what a section asks for (the included evidence has none). A section that
    already sent the stage back to argue does not send it back again: its new missing_support is refused (noted, a
    failed try), and the Writer is told what argue was already asked for and to state the gap as a limitation."""
    _, ps, _ = env
    _ready(env)
    need = {"markdown": "", "missing_support": ["a claim on the bleeding risk of aspirin in older people"]}
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes(), need]))).to == "argue"  # attempt 1: theme-1 asks argue once
    ex = _writer([writes(), need, writes(), writes(), writes()])
    out = w.run(_ctx(env, ex, attempt=2))  # argue ran again (it could not give the claim); draft attempt 2
    assert out.status == "done", out.reason
    assert _writer_sections(ex)[:3] == ["introduction", "theme-1", "theme-1"]
    asked, retried = ex.of("writer", "SectionDraft")[1:3]
    assert "bleeding risk" in asked["inputs"]["asked_of_argue"][0]
    assert any("already asked argue" in f for f in retried["inputs"]["retry"]["failures"])
    assert not draft_notes(ps, 2, kind="back_edge")
    assert [v["verdict"]["ok"] for v in draft_versions(ps, "theme-1")] == [False, True]
    assert w.gate(ps, 2).ok
    # the kernel discloses the gap whatever the Writer wrote (the passing draft here states no limitation)
    asked_u, still_u = unmet_support(ps)
    assert (asked_u["section"], still_u["section"]) == ("theme-1", "theme-1") and "asked" in asked_u
    assert still_u["needs"] == need["missing_support"]
    md = render_disclosure(ps)
    limitations = md.split("### Limitations")[1].split("###")[0]
    assert f"[[run:{asked_u['evt_id']}]]" in limitations and f"[[run:{still_u['evt_id']}]]" in limitations
    assert "still lacked support for `a claim on the bleeding risk of aspirin in older people`" in limitations
    missing = [p for p in disclosure_problems(ps, md.replace(f"[[run:{still_u['evt_id']}]]", ""), [])
               if "lacked from the argument" in p]
    assert missing == [(f"what the theme-1 section lacked from the argument is not listed (event {still_u['evt_id']} "
                        "is not cited)")]


@pytest.mark.parametrize("env", [{"paper_type": "research_proposal"}], indirect=True)
def test_proposal_missing_design_takes_draft_to_design(env):
    _ready(env)
    ex = _writer([writes(), writes(), writes(),
                  {"markdown": "", "missing_design": "no measurement of bleeding events"}])
    out = DraftWorker().run(_ctx(env, ex))
    assert _writer_sections(ex) == ["background", "gap", "questions-hypotheses", "methods"]
    assert out.status == "back_edge" and out.to == "design"
    assert "methods" in out.reason and "bleeding events" in out.reason
    assert ("draft", "design") in BACK_EDGES


def test_missing_design_outside_a_proposal_is_a_failed_try(env):
    _, ps, _ = env
    _ready(env)

    def verifying_with_missing_design(inputs):  # markdown that verifies, and a missing_design it may not give
        return {**writes()(inputs), "missing_design": "a design"}

    ex = _writer([{"markdown": "", "missing_design": "a design"}, verifying_with_missing_design, writes(), writes(),
                  writes(), writes()])
    out = DraftWorker().run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert _writer_sections(ex)[:3] == ["introduction"] * 3
    for call in ex.of("writer", "SectionDraft")[1:3]:
        assert "missing_design is only for a research proposal" in call["inputs"]["retry"]["failures"]
    # the version that verifies is not accepted with the refusal: the section was drafted again
    assert [v["verdict"]["ok"] for v in draft_versions(ps, "introduction")] == [False, False, True]
    assert DraftWorker().gate(ps, 1).ok


def test_a_bare_id_marker_is_named_in_the_writers_retry(env):
    """Task 9: a Writer that writes [[clm_…]] is told, in its retry failures, the marker form to write."""
    _ready(env)
    ex = _writer([writes(bad="Copper catalysts may matter [[clm_01ABC]]."), *[writes()] * 4])
    assert DraftWorker().run(_ctx(env, ex)).status == "done"
    failures = ex.of("writer", "SectionDraft")[1]["inputs"]["retry"]["failures"]
    assert any("[[clm_01ABC]] is not a source marker" in f and "write [[clm:clm_01ABC]]" in f for f in failures), \
        failures


def test_the_writer_is_not_offered_a_withdrawn_claim(env):
    """Task 9: the Writer (haiku) cited withdrawn claims it was given in its claims input; a withdrawn claim cannot be
    cited, so only the limitations list it, by text and reason. Calibration (critical3, draft attempts 2 and 10):
    given its id there, the Writer cited it anyway, so the id is left out."""
    _, ps, _ = env
    _ready(env)
    first = _writer([writes()])
    with pytest.raises(AssertionError):  # only the first task's inputs are needed
        DraftWorker().run(_ctx(env, first))
    claims = first.of("writer", "SectionDraft")[0]["inputs"]["claims"]
    gone = next(c for c in claims if c["role"] == "finding")
    ArgumentService(ps).withdraw_claim(gone["claim_id"], reason="the objection holds", actor="tester")
    ex = _writer([writes()])
    with pytest.raises(AssertionError):
        DraftWorker().run(_ctx(env, ex, attempt=2))
    inputs = ex.of("writer", "SectionDraft")[0]["inputs"]
    assert gone["claim_id"] not in json.dumps(inputs) and inputs["claims"]
    assert gone["text"] in json.dumps(inputs["limitations"]) and "the objection holds" in json.dumps(inputs["limitations"])


def test_disclosure_section_is_kernel_written(env):
    _, ps, _ = env
    _ready(env)
    ex = _writer([writes()] * 4)
    w = DraftWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert "disclosure" not in _writer_sections(ex)
    (v,) = draft_versions(ps, "disclosure")
    assert v["by_task"] is None and v["verdict"]["ok"]
    md = draft_text(ps, "disclosure", 1)
    sentences = parse_section(md)
    assert sentences and all([m.kind for m in s.markers] == ["run"] for s in sentences)
    assert "The synthesis stage passed its gate" in md and "The argument stage passed its gate" in md
    passed = {r["evt_id"] for r in ps.db.execute("SELECT evt_id FROM events WHERE type='stage.passed'")}
    assert {m.target for m in source_markers(md)} <= passed
    assert "TODO" not in md and "placeholder" not in md.lower()


def test_every_main_claim_and_supported_finding_is_cited(env):
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes(only_main=True)] * 4))).status == "done"
    res = w.gate(ps, 1)
    assert not res.ok
    findings = synthesis_claims(ps, 1)
    assert findings and all(f"the supported finding {c} is cited in no section" in res.failures for c in findings)
    assert not any("main claim" in f for f in res.failures)
    # a statistic of another attempt is no artifact of synthesize's passed attempt
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values={"display": {"estimate": 0.5}}, output_path=None),
                       stage="synthesize", attempt=2, actor="k")
    ex = _writer([writes(extra=f"Another pooled analysis was made [[stat:{sid}]].")] * 4)
    assert w.run(_ctx(env, ex, attempt=2)).status == "done"
    res2 = w.gate(ps, 2)
    assert any(f"[[stat:{sid}]] is not an artifact of synthesize's passed attempt" in f for f in res2.failures)
    assert not any("cited in no section" in f for f in res2.failures)
    # a section without a draft of the attempt fails the gate
    assert any("has no draft from this attempt" in f for f in w.gate(ps, 3).failures)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_prisma_event_pools_its_counts_never_its_attempt(env):
    _, ps, _ = env
    _ready(env)
    fields = EVENT_NUMBERS[PRISMA_EVENT]
    assert len(fields) == 11 and "excluded_ft_total" in fields
    assert not {"attempt", "search_attempt", "protocol_id"} & set(fields)
    pid = ps.db.execute("SELECT id FROM protocols WHERE status='frozen'").fetchone()["id"]
    evt = prisma_event(ps, 9, 1, pid, actor="tester")
    assert prisma_event(ps, 9, 1, pid, actor="tester") == evt  # once per attempt
    (row,) = ps.db.execute("SELECT payload FROM events WHERE evt_id=?", (evt,)).fetchall()
    p = json.loads(row["payload"])
    assert (p["attempt"], p["search_attempt"], p["protocol_id"], p["identified"], p["included"]) == (9, 1, pid, 2, 2)
    assert p["excluded_ft_total"] == 0
    (ok,) = parse_section(f"The search identified 2 records and included 2 studies [[run:{evt}]].")
    assert verify_sentence(ps, ok, paper_type="systematic_review").ok
    for n in ("9", "1"):  # the draft attempt and the search_screen attempt it records are never pooled
        (s,) = parse_section(f"The counts come from attempt {n} [[run:{evt}]].")
        v = verify_sentence(ps, s, paper_type="systematic_review")
        assert f"the number {n} is not in any marker's target" in v.problems


def test_draft_section_retries_setting_bounds_the_writer(env):
    _, ps, _ = env
    _ready(env)
    save_settings(load_settings().model_copy(update={"draft_section_retries": 0}))
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    ex = _writer([writes(bad=bad)] + [writes()] * 3)
    assert DraftWorker().run(_ctx(env, ex)).status == "done"
    assert _writer_sections(ex) == ["introduction", "theme-1", "gaps-agenda", "conclusion"]


def test_p1_degrade_deletes_failing_sentences(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes(bad=bad)] * 3 + [writes()] * 3))).status == "done"
    failures = w.gate(ps, 1).failures
    assert failures
    ex = _writer([])
    out = w.degrade(_ctx(env, ex), failures)
    assert out.status == "done" and not ex.calls
    md = draft_text(ps, "introduction", draft_versions(ps, "introduction")[-1]["version"])
    assert "1234" not in md and f"[[clm:{main}]]" in md
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    (deg,) = ps.db.execute("SELECT payload FROM events WHERE type='stage.degraded'").fetchall()
    assert "1234" in deg["payload"]


def test_p1_degrade_refuses_to_empty_a_section(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    only_bad = {"markdown": bad + "\n"}
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes()] * 3 + [only_bad] * 3))).status == "done"
    versions = len(draft_versions(ps, "conclusion"))
    out = w.degrade(_ctx(env, _writer([])), w.gate(ps, 1).failures)
    assert out.status == "failed" and "conclusion" in out.reason and "empty" in out.reason
    assert len(draft_versions(ps, "conclusion")) == versions  # nothing written
    assert not ps.db.execute("SELECT 1 FROM events WHERE type='stage.degraded'").fetchall()


def test_p1_degrade_deletes_sentences_citing_another_attempts_artifact(env):
    _, ps, _ = env
    _ready(env)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values={"display": {"estimate": 0.5}}, output_path=None),
                       stage="synthesize", attempt=2, actor="k")
    w = DraftWorker()
    extra = f"Another pooled analysis was made [[stat:{sid}]]."
    assert w.run(_ctx(env, _writer([writes(extra=extra)] * 4))).status == "done"
    failures = w.gate(ps, 1).failures
    assert any(f"[[stat:{sid}]]" in f for f in failures)
    out = w.degrade(_ctx(env, _writer([])), failures)
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    md = draft_text(ps, "introduction", draft_versions(ps, "introduction")[-1]["version"])
    assert sid not in md


def test_writer_artifacts_carry_only_reported_values(env):
    from rk.verify.cover import (
        reported_values,  # Draft round 1: one public definition of reported values
    )

    _, ps, _ = env
    _ready(env)
    stored = {"display": {"estimate": 0.62, "label": "RR"}, "k": 4, "measure": "RR", "row_ids": ["ext_1", "ext_2"],
              "extraction_attempt": 1, "studies": [{"item_id": "it_1", "yi": -1.23}],
              "pooled": {"k": 4, "mu": -0.478}, "options": {"level": 95}}
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values=stored, output_path=None), stage="synthesize", attempt=1, actor="k")
    table = {"fields": ["design"], "item_ids": ["it_1"], "row_ids": ["ext_9"], "extraction_attempt": 1,
             "table": {"n": 2, "fields": {"design": {"rct": 1, "cohort": 1}},
                       "cross": {"rows": "design", "cols": "setting", "counts": {"rct": {"ward": 1}}}}}
    tid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:y", code_version="v", input_hashes=[],
                                      values=table, output_path=None), stage="synthesize", attempt=1, actor="k")
    assert reported_values(stored) == {"display": {"estimate": 0.62}, "k": 4, "pooled": {"k": 4}}
    assert reported_values(table) == {"table": {"n": 2, "fields": {"design": {"rct": 1, "cohort": 1}},
                                                "cross": {"rows": "design", "cols": "setting",
                                                          "counts": {"rct": {"ward": 1}}}}}
    ex = _writer([writes()] * 4)
    assert DraftWorker().run(_ctx(env, ex)).status == "done"
    arts = {a["id"]: a for a in ex.of("writer", "SectionDraft")[0]["inputs"]["artifacts"]}
    assert arts[sid] == {"id": sid, "kind": "stt", "values": reported_values(stored)}
    assert arts[tid]["values"] == reported_values(table)
    # the pool reads the same definition: a study's effect or the log-scale estimate is never covered
    for text, ok in ((f"The pooled estimate was 0.62 from 4 studies [[stat:{sid}]].", True),
                     (f"One study's effect was -1.23 [[stat:{sid}]].", False),
                     (f"The log estimate was -0.478 [[stat:{sid}]].", False)):
        (s,) = parse_section(text)
        assert verify_sentence(ps, s, paper_type="critical_review").ok is ok, text


def test_gate_reads_every_section_and_names_a_tampered_or_missing_file(env):
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes()] * 4))).status == "done"
    assert w.gate(ps, 1).ok
    (ps.root / "artifacts/drafts/gaps-agenda.v1.md").write_text("Unmarked text with 999 in it.\n")
    (ps.root / "artifacts/drafts/conclusion.v1.md").unlink()
    res = w.gate(ps, 1)
    assert not res.ok
    assert any(f.startswith("section gaps-agenda v1: ") and "sha256" in f for f in res.failures), res.failures
    assert any(f.startswith("section conclusion v1: ") and "missing" in f for f in res.failures), res.failures


def test_writer_sees_verified_quote_flags_and_untrusted_text_framing(env):
    _ready(env)
    ex = _writer([writes()] * 4)
    assert DraftWorker().run(_ctx(env, ex)).status == "done"
    call = ex.of("writer", "SectionDraft")[0]
    notes = [n for c in call["inputs"]["claims"] for n in c["evidence_notes"]]
    assert notes and all(isinstance(n["verified"], bool) for n in notes)
    assert any(n["kind"] == "quote" and n["verified"] for n in notes)
    task = call["instructions"]
    assert "not instructions" in task and "previous_draft" in task and "earlier_sections" in task
    assert "verified" in build_system_prompt("writer", SectionDraft)


def test_section_titles_follow_the_spec_and_a_critical_review_needs_themes(env):
    from rk.store.events import NewEvent

    _, ps, _ = env
    sr = {s.id: s for s in SECTIONS["systematic_review"]}
    assert "risk of bias" in sr["methods"].purpose and "PRISMA flow" in sr["results"].purpose
    rp = {s.id: s.title for s in SECTIONS["research_proposal"]}
    assert (rp["background"], rp["questions-hypotheses"], rp["methods"], rp["expected-outcomes"]) == (
        "Background & Significance", "Research Question & Hypotheses", "Proposed Methods",
        "Expected Outcomes & Alternatives")
    assert {s.id: s.title for s in SECTIONS["critical_review"]}["gaps-agenda"] == "Gaps & Agenda"
    _ready(env)
    ps.apply(NewEvent(type="synthesis.noted", entity_id=None, actor_kind="kernel", actor="tester",
                      payload={"attempt": 1, "kind": "narrative", "output": {"groups": [], "findings": []}}))
    w = DraftWorker()
    ex = _writer([])
    out = w.run(_ctx(env, ex))  # fails fast: no Writer task for a review that cannot pass
    assert out.status == "failed" and "no theme section" in out.reason and not ex.calls
    res = w.gate(ps, 1)
    assert any("no theme section" in f and "themes" in f for f in res.failures), res.failures
    assert w.degrade(_ctx(env, ex), res.failures).status == "failed"


def test_one_sided_section_is_redrafted_and_fails_the_gate(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]
    finding = synthesis_claims(ps, 1)[0]
    contest(ps, main, finding, reason)  # the main claim and the finding contest each other; the reason answers it

    def balanced(inputs):
        r = next(c for c in inputs["claims"] if c["claim_id"] == reason)
        return writes(extra=_sentence(r))(inputs)

    # introduction: one-sided (no response cited) on every try; theme-1: balanced on its retry
    ex = _writer([writes()] * 3 + [writes(), balanced, balanced, balanced])
    w = DraftWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert _writer_sections(ex) == ["introduction"] * 3 + ["theme-1"] * 2 + ["gaps-agenda", "conclusion"]
    failures = ex.of("writer", "SectionDraft")[1]["inputs"]["retry"]["failures"]
    assert any("one-sided" in f and main in f and reason in f for f in failures), failures
    res = w.gate(ps, 1)
    (named,) = [f for f in res.failures if f.startswith("section introduction")]
    assert "v3" in named and "one-sided" in named and main in named and reason in named
    assert not any("theme-1" in f for f in res.failures), res.failures
    # a verified section the gate finds one-sided now: an objection to the finding answered by the main claim, which
    # every section cites before the finding
    object_to(ps, finding, response=main)
    res2 = w.gate(ps, 1)
    assert any(f.startswith("section theme-1 v2: ") and "one-sided" in f and finding in f and main in f
               for f in res2.failures), res2.failures


def test_degrade_that_deletes_nothing_fails_naming_the_failures(env):
    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes(only_main=True)] * 4))).status == "done"
    failures = w.gate(ps, 1).failures
    assert failures and all("cited in no section" in f for f in failures)
    versions = {x: len(draft_versions(ps, x)) for x in _ids(ps)}
    out = w.degrade(_ctx(env, _writer([])), failures)
    assert out.status == "failed" and "nothing" in out.reason
    assert all(f in out.reason for f in failures)
    assert {x: len(draft_versions(ps, x)) for x in _ids(ps)} == versions
    assert not ps.db.execute("SELECT 1 FROM events WHERE type='stage.degraded'").fetchall()


def test_degrade_that_verified_a_version_and_deleted_nothing_gates_again(env):
    from rk.production.manuscript import write_draft

    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes()] * 4))).status == "done"
    md = draft_text(ps, "conclusion", 1)
    write_draft(ps, "conclusion", md, stage="draft", attempt=1, round=0, by_task=None, actor="tester")  # unverified
    failures = w.gate(ps, 1).failures
    assert failures == ["section conclusion v2 was not verified"], failures
    out = w.degrade(_ctx(env, _writer([])), failures)
    assert out.status == "done", out.reason
    assert draft_versions(ps, "conclusion")[-1]["verdict"]["ok"]
    assert w.gate(ps, 1).ok
    assert not ps.db.execute("SELECT 1 FROM events WHERE type='stage.degraded'").fetchall()


def test_writer_claims_carry_their_balance_requirements(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]
    finding = synthesis_claims(ps, 1)[0]
    conflict = contest(ps, main, finding, reason)
    answered = object_to(ps, finding, response=main)
    conceded = object_to(ps, finding, status="conceded", source_id="itm_source")
    ex = _writer([writes()] * 12)
    DraftWorker().run(_ctx(env, ex))
    claims = {c["claim_id"]: c for c in ex.of("writer", "SectionDraft")[0]["inputs"]["claims"]}
    assert claims[main]["contested_by"] == [finding]
    assert claims[main]["answered_by"] == [{"objection_id": conflict, "response_claim_id": reason}]
    assert claims[main]["conceded_sourced"] == []
    assert claims[finding]["contested_by"] == []
    assert claims[finding]["answered_by"] == [{"objection_id": answered, "response_claim_id": main}]
    assert claims[finding]["conceded_sourced"] == [{"objection_id": conceded, "text": "another trial found otherwise",
                                                   "source_id": "itm_source"}]
    prompt = build_system_prompt("writer", SectionDraft)
    for word in ("contested_by", "answered_by", "after the claim, in the same section", "conceded_sourced",
                 "qualify the claim"):
        assert word in prompt, word


def test_missing_design_refusal_survives_a_crash_after_its_version(env, monkeypatch):
    import rk.production.draft as draft_mod

    _, ps, _ = env
    _ready(env)
    real = draft_mod.write_draft

    def crash(*a, **kw):
        real(*a, **kw)
        raise RuntimeError("crash after the version was stored")

    def verifying_with_missing_design(inputs):
        return {**writes()(inputs), "missing_design": "a design"}

    ex = _writer([verifying_with_missing_design] + [writes()] * 4)
    monkeypatch.setattr(draft_mod, "write_draft", crash)
    with pytest.raises(RuntimeError):
        DraftWorker().run(_ctx(env, ex))
    monkeypatch.setattr(draft_mod, "write_draft", real)
    (v,) = draft_versions(ps, "introduction")
    assert v["verdict"] is None
    assert DraftWorker().run(_ctx(env, ex)).status == "done"
    first, second = draft_versions(ps, "introduction")
    assert (first["verdict"]["ok"], second["verdict"]["ok"]) == (False, True)
    assert "missing_design is only for a research proposal" in first["verdict"]["problems"]
    assert "missing_design is only for a research proposal" in ex.of("writer", "SectionDraft")[1]["inputs"]["retry"][
        "failures"]


def test_gate_and_degrade_name_an_unreadable_draft_file(env, monkeypatch):
    from pathlib import Path

    _, ps, _ = env
    _ready(env)
    w = DraftWorker()
    assert w.run(_ctx(env, _writer([writes()] * 4))).status == "done"
    real = Path.read_bytes

    def read_bytes(self):
        if self.name == "conclusion.v1.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    res = w.gate(ps, 1)
    assert any(f.startswith("section conclusion v1: ") and "cannot be read" in f for f in res.failures), res.failures
    out = w.degrade(_ctx(env, _writer([])), res.failures)
    assert out.status == "failed" and "conclusion v1" in out.reason and "cannot be read" in out.reason


def test_p1_degrade_deletes_a_sentence_that_fails_now_though_it_verified_when_drafted(env):
    """Task 4 re-review (a): the degrade deletes from a fresh section verdict (as revise does), not only from the
    recorded one: a sentence that verified when it was drafted and fails now (its claim has an open priority-1
    objection since) is deleted, and the gate passes."""
    from rk.argument import ArgumentService, ObjectionIn

    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]

    def with_reason(inputs):
        if inputs["section"]["id"] != "conclusion":
            return writes()(inputs)
        r = next(c for c in inputs["claims"] if c["claim_id"] == reason)
        return writes(extra=_sentence(r))(inputs)

    w = DraftWorker()
    assert w.run(_ctx(env, _writer([with_reason] * 4))).status == "done"
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    ArgumentService(ps).add_objection(
        ObjectionIn(target_id=reason, category="counterexample", priority=1, text="a trial found the opposite"),
        actor="tester", actor_kind="kernel")
    assert draft_versions(ps, "conclusion")[-1]["verdict"]["ok"]  # recorded before the objection
    failures = w.gate(ps, 1).failures
    assert any(f.startswith("section conclusion v1") and reason in f for f in failures), failures
    out = w.degrade(_ctx(env, _writer([])), failures)
    assert out.status == "done", out.reason
    md = draft_text(ps, "conclusion", draft_versions(ps, "conclusion")[-1]["version"])
    assert f"[[clm:{reason}]]" not in md and f"[[clm:{main}]]" in md
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
