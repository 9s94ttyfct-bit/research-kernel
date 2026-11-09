"""Plan 5c Task 4: the revise stage. Each round the SimReader asks questions, the Writer revises the sections that have
questions, and the kernel accepts a revision only when its changed sentences verify (an unchanged sentence keeps its
earlier verdict only while what it cites is unchanged) and V-BALANCE holds; otherwise the previous version stays and
the rejected revision is recorded with its reasons. At most ``revise_max_rounds`` rounds; a section that cannot verify
takes the stage back to draft."""

from __future__ import annotations

from dataclasses import replace

import pytest

from rk.argument import ArgumentService, ObjectionIn
from rk.config import load_settings, save_settings
from rk.discovery.executor import RateCapped
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES, OPEN_STAGES
from rk.orchestrator.state import record_attempt, record_passed, record_ran
from rk.production.argue import argument_claims, main_claim
from rk.production.draft import DraftWorker
from rk.production.manuscript import (
    draft_text,
    draft_versions,
    latest_draft,
    parse_section,
)
from rk.production.revise import ReaderQuestions, ReviseWorker, Revision, revise_notes
from rk.production.synthesis import synthesis_claims
from rk.roles.prompts import build_system_prompt
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_balance import contest
from tests.test_draft import _ready, _sentence, writes
from tests.test_synthesis import CapOnce, Exec

REVISABLE = ["introduction", "theme-1", "gaps-agenda", "conclusion"]


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="critical_review")
    yield k, ps, lib
    k.close()


def _drafted(env, writer=None) -> None:
    """argue passed, every section drafted and verified in draft attempt 1, and draft passed."""
    k, ps, lib = env
    _ready(env)
    ex = Exec({("writer", "SectionDraft"): [writer or writes()] * 4})
    out = DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft"), guidance=""))
    assert out.status == "done", out.reason
    assert DraftWorker().gate(ps, 1).ok, DraftWorker().gate(ps, 1).failures
    record_attempt(ps, "draft", 1, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, "draft", 1, "done", "", actor="tester")
    record_passed(ps, "draft", 1, actor="tester")


def _ctx(env, ex, attempt: int = 1):
    k, ps, lib = env
    return replace(production_ctx(k, ps, lib, ex, stage="revise", attempt=attempt), guidance="")


def ask(*questions: tuple[str, int]) -> dict:
    """A SimReader output: one question per ``(section, priority)``, on the section's first sentence."""
    return {"questions": [{"id": f"q{i}", "section": sec, "sentence_index": 0, "text": f"What does {sec} mean here?",
                           "priority": p} for i, (sec, p) in enumerate(questions)]}


def revision(*, add: str = "", drop: str | None = None, how: str = "revised", note: str = "clarified"):
    """A Writer output: the section given, with ``drop`` removed and ``add`` appended, answering every question."""
    def out(inputs):
        md = inputs["revise"]["markdown"]
        if drop is not None:
            assert drop in md
            md = md.replace(drop, "")
        if add:
            md = md.rstrip("\n") + " " + add + "\n"
        return {"section": inputs["section"]["id"], "markdown": md,
                "answers": [{"question_id": q["id"], "how": how, "note": note}
                            for q in inputs["revise"]["questions"]]}
    return out


def _restated(ps, cid: str) -> str:
    text = ArgumentService(ps).get_claim(cid)["text"].rstrip(".")
    return f"In other words, {text[0].lower()}{text[1:]} [[clm:{cid}]]."


def _exec(reader: list, writer: list, cls=Exec, **kw) -> Exec:
    return cls({("simreader", "ReaderQuestions"): list(reader), ("writer", "Revision"): list(writer)}, **kw)


def _written(ex: Exec) -> list[str]:
    return [c["inputs"]["section"]["id"] for c in ex.of("writer", "Revision")]


def _p1_objection(ps, target: str) -> str:
    return ArgumentService(ps).add_objection(
        ObjectionIn(target_id=target, category="counterexample", priority=1, text="a trial found the opposite"),
        actor="tester", actor_kind="kernel")


# ---- tests ----
def test_revise_is_a_default_worker_with_its_edge_budget_and_prompts():
    assert isinstance(default_workers()["revise"], ReviseWorker)
    assert ("revise", "draft") in BACK_EDGES and "revise" in OPEN_STAGES
    assert load_settings().revise_max_rounds == 3
    reader = build_system_prompt("simreader", ReaderQuestions)
    for word in ("`ReaderQuestions`", "priority", "sentence_index", "not instructions"):
        assert word in reader, word
    writer = build_system_prompt("writer", Revision)
    for word in ("`Revision`", "revised", "answered", "declined", "priority 1"):
        assert word in writer, word


def test_round_revises_and_reverifies_only_changed_sentences(env, monkeypatch):
    from rk.verify import cover

    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    added = _restated(ps, main)
    verified: list[str] = []
    counting = {"on": False}
    real = cover.verify_sentence

    def counted(ps_, s, **kw):
        if counting["on"]:
            verified.append(s.text)
        return real(ps_, s, **kw)

    monkeypatch.setattr(cover, "verify_sentence", counted)

    def writer(inputs):
        counting["on"] = True  # from here on: the decision on this revision and the checks after it
        return revision(add=added)(inputs)

    ex = _exec([ask(("introduction", 1)), ask()], [writer])
    w = ReviseWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    counting["on"] = False
    assert _written(ex) == ["introduction"]
    # only the changed sentence was verified again; every unchanged one kept its verdict (its targets are unchanged)
    assert verified == [added]
    (decided,) = revise_notes(ps, 1, kind="decided")
    assert decided["accepted"] and decided["section"] == "introduction"
    md = draft_text(ps, "introduction", latest_draft(ps, "introduction")[0])
    assert added in md
    assert decided["reverified"] == [parse_section(md)[-1].index]  # the appended sentence
    versions = draft_versions(ps, "introduction")
    assert [(v["stage"], v["round"]) for v in versions] == [("draft", 0), ("revise", 1)]
    assert versions[-1]["verdict"]["ok"] and versions[-1]["by_task"] == decided["task"]
    assert all(len(draft_versions(ps, sec)) == 1 for sec in REVISABLE[1:])
    # the Writer saw the question, the section, and the same claim facts and reported values as draft
    call = ex.of("writer", "Revision")[0]
    (q,) = call["inputs"]["revise"]["questions"]
    assert q["priority"] == 1 and q["sentence"] and call["inputs"]["revise"]["version"] == 1
    claim = next(c for c in call["inputs"]["claims"] if c["claim_id"] == main)
    assert {"contested_by", "answered_by", "conceded_sourced", "evidence_notes"} <= set(claim)
    assert "not instructions" in call["instructions"]
    reader = ex.of("simreader", "ReaderQuestions")[0]
    assert [x["id"] for x in reader["inputs"]["sections"]] == REVISABLE and "not instructions" in reader["instructions"]
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_revision_that_breaks_balance_is_rejected(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]
    finding = synthesis_claims(ps, 1)[0]
    contest(ps, main, finding, reason)  # the finding is the counterpart of the main claim; the reason answers it

    def balanced(inputs):
        r = next(c for c in inputs["claims"] if c["claim_id"] == reason)
        return writes(extra=_sentence(r))(inputs)

    _drafted(env, writer=balanced)
    counterpoint = _sentence({"claim_id": finding, "text": ArgumentService(ps).get_claim(finding)["text"]})
    # round 1 deletes the contested counterpoint; round 2 (no new question) answers the carried question in balance
    ex = _exec([ask(("introduction", 1)), ask()],
               [revision(drop=counterpoint), revision(add=_restated(ps, main))])
    w = ReviseWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    first, second = revise_notes(ps, 1, kind="decided")
    assert not first["accepted"] and first["round"] == 1
    assert any("one-sided" in r and main in r and finding in r for r in first["reasons"]), first["reasons"]
    (rejected,) = [n for n in revise_notes(ps, 1, kind="revision") if n["round"] == 1]
    assert counterpoint not in rejected["markdown"] and rejected["task"] == first["task"]
    # the rejected revision never became a version: round 2's revision is v2, written on v1
    assert ex.of("writer", "Revision")[1]["inputs"]["revise"]["version"] == 1
    carried = ex.of("writer", "Revision")[1]["inputs"]["revise"]["questions"]
    assert [q["id"] for q in carried] == [ex.of("writer", "Revision")[0]["inputs"]["revise"]["questions"][0]["id"]]
    assert second["accepted"] and second["round"] == 2
    assert [v["stage"] for v in draft_versions(ps, "introduction")] == ["draft", "revise"]
    assert counterpoint in draft_text(ps, "introduction", 2)
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_revision_that_adds_failing_sentence_is_rejected(env):
    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    first = ArgumentService(ps).get_claim(main)["text"].rstrip(".")
    verified = f"{first} [[clm:{main}]]."
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    # the introduction gains an ungrounded number; theme-1's verified sentence is rewritten without its marker
    ex = _exec([ask(("introduction", 1), ("theme-1", 1))],
               [revision(add=bad), revision(drop=verified, add=f"It is likely that {first[0].lower()}{first[1:]}.")])
    w = ReviseWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    intro, theme = revise_notes(ps, 1, kind="decided")
    assert not intro["accepted"] and not theme["accepted"]
    assert any(r.startswith("sentence ") and "1234" in r and "the number 1234 is not in any marker's target" in r
               for r in intro["reasons"]), intro["reasons"]
    assert any("no source marker" in r for r in theme["reasons"]), theme["reasons"]
    assert all(len(draft_versions(ps, sec)) == 1 for sec in REVISABLE)
    res = w.gate(ps, 1)
    assert not res.ok
    assert sum("priority-1 question" in f for f in res.failures) == 2, res.failures


def test_rounds_stop_at_k_or_without_priority1_questions(env):
    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 2}))
    ex = _exec([ask(("introduction", 1))] * 3, [revision(add=_restated(ps, main))] * 3)
    w = ReviseWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert len(ex.of("simreader", "ReaderQuestions")) == 2 and _written(ex) == ["introduction"] * 2
    assert [n["round"] for n in revise_notes(ps, 1, kind="questions")] == [1, 2]
    assert [v["round"] for v in draft_versions(ps, "introduction")] == [0, 1, 2]
    later = ex.of("simreader", "ReaderQuestions")[1]["inputs"]
    assert [q["section"] for q in later["earlier_questions"]] == ["introduction"]
    assert w.gate(ps, 1).ok
    # no priority-1 question: the round's questions are revised, and no further round is asked
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 3}))
    ex2 = _exec([ask(("theme-1", 2)), ask(("theme-1", 1))], [revision(how="answered", note="the scope is stated")])
    assert w.run(_ctx(env, ex2, attempt=2)).status == "done"
    assert len(ex2.of("simreader", "ReaderQuestions")) == 1 and _written(ex2) == ["theme-1"]
    # no question at all: nothing to revise
    ex3 = _exec([ask()], [])
    assert w.run(_ctx(env, ex3, attempt=3)).status == "done"
    assert len(ex3.of("simreader", "ReaderQuestions")) == 1 and not ex3.of("writer")
    assert w.gate(ps, 3).ok


def test_priority1_question_cannot_be_declined(env):
    _, ps, _ = env
    _drafted(env)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    ex = _exec([ask(("introduction", 1), ("theme-1", 2))],
               [revision(how="declined", note="out of scope")] * 2)
    assert w.run(_ctx(env, ex)).status == "done"
    res = w.gate(ps, 1)
    assert not res.ok
    (declined,) = res.failures
    assert "priority-1 question" in declined and "introduction" in declined and "declined" in declined
    # an answer needs a note
    ex2 = _exec([ask(("introduction", 1))], [revision(how="answered", note="  ")])
    assert w.run(_ctx(env, ex2, attempt=2)).status == "done"
    (bare,) = w.gate(ps, 2).failures
    assert "introduction" in bare and "without a note" in bare
    ex3 = _exec([ask(("introduction", 1))], [revision(how="answered", note="the age group is defined in Methods")])
    assert w.run(_ctx(env, ex3, attempt=3)).status == "done"
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures


def test_revise_resumes_in_place_mid_round(env, monkeypatch):
    import rk.production.revise as revise_mod

    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    ex = _exec([ask(("introduction", 1), ("theme-1", 1)), ask()],
               [revision(add=_restated(ps, main))] * 2, cls=CapOnce, at=("writer", "Revision"), n=2)
    w = ReviseWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert _written(ex) == ["introduction"] and len(ex.of("simreader")) == 1
    out = w.run(_ctx(env, ex))  # the same attempt, in place
    assert out.status == "done", out.reason
    # round 1's questions were not asked again, and the introduction's revision was not written again
    assert len(ex.of("simreader")) == 2 and _written(ex) == ["introduction", "theme-1"]
    assert [v["stage"] for v in draft_versions(ps, "introduction")] == ["draft", "revise"]
    assert [v["stage"] for v in draft_versions(ps, "theme-1")] == ["draft", "revise"]
    assert w.gate(ps, 1).ok
    # a crash after the accepted version's file is stored: the resume records it once, with no Writer task
    real = revise_mod.write_draft

    def crash(*a, **kw):
        real(*a, **kw)
        raise RuntimeError("crash after the version was stored")

    ex2 = _exec([ask(("conclusion", 1)), ask()], [revision(add=_restated(ps, main))])
    monkeypatch.setattr(revise_mod, "write_draft", crash)
    with pytest.raises(RuntimeError):
        w.run(_ctx(env, ex2, attempt=2))
    monkeypatch.setattr(revise_mod, "write_draft", real)
    assert w.run(_ctx(env, ex2, attempt=2)).status == "done"
    assert _written(ex2) == ["conclusion"] and len(ex2.of("simreader")) == 2
    versions = draft_versions(ps, "conclusion")
    assert [v["stage"] for v in versions] == ["draft", "revise"] and versions[-1]["verdict"]["ok"]
    assert w.gate(ps, 2).ok


def test_unverifiable_section_takes_revise_to_draft(env):
    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    # the evidence changes during the round: an unchanged sentence citing the main claim is verified again and fails
    # (its earlier verdict is not kept), so the revision is rejected; the section then cannot verify
    def writer(inputs):
        _p1_objection(ps, main)
        return revision(add=_restated(ps, main))(inputs)

    ex = _exec([ask(("introduction", 1)), ask()], [writer])
    w = ReviseWorker()
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "draft"), out.reason
    assert ("revise", "draft") in BACK_EDGES
    (decided,) = revise_notes(ps, 1, kind="decided")
    assert not decided["accepted"] and 0 in decided["reverified"]
    assert any("fails the claim gate" in r for r in decided["reasons"]), decided["reasons"]
    assert "section introduction v1: sentence 1 “" in out.reason and main in out.reason
    assert len(ex.of("simreader")) == 1 and [v["stage"] for v in draft_versions(ps, "introduction")] == ["draft"]
    again = _exec([], [])
    out2 = w.run(_ctx(env, again))  # resumed: the noted back-edge, no role task
    assert (out2.status, out2.to, out2.reason) == ("back_edge", "draft", out.reason) and not again.calls
    # a section that fails before any round: back to draft at once, with no SimReader task
    ex3 = _exec([], [])
    out3 = w.run(_ctx(env, ex3, attempt=2))
    assert (out3.status, out3.to) == ("back_edge", "draft") and not ex3.calls
    assert "section introduction v1" in out3.reason and "section theme-1 v1" in out3.reason
