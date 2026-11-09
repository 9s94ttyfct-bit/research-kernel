"""Plan 5c Task 4: the revise stage. Each round the SimReader asks questions, the Writer revises the sections that have
questions, and the kernel accepts a revision only when its changed sentences verify (an unchanged sentence keeps its
earlier verdict only while what it cites is unchanged) and V-BALANCE holds; otherwise the previous version stays and
the rejected revision is recorded with its reasons. At most ``revise_max_rounds`` rounds; a section that cannot verify
takes the stage back to draft."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from rk.argument import ArgumentService, ObjectionIn
from rk.config import load_settings, save_settings
from rk.discovery.executor import RateCapped
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES, OPEN_STAGES
from rk.orchestrator.state import record_attempt, record_passed, record_ran
from rk.production.argue import argument_claims, main_claim
from rk.production.artifacts import ArtifactIn, add_artifact
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


def ask(*questions: tuple) -> dict:
    """A SimReader output: one question per ``(section, priority)`` on the section's first sentence, or per
    ``(section, priority, sentence_index)`` (``None``: on the section as a whole)."""
    return {"questions": [{"id": f"q{i}", "section": q[0], "sentence_index": q[2] if len(q) > 2 else 0,
                           "text": f"What does {q[0]} mean here?", "priority": q[1]} for i, q in enumerate(questions)]}


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
    # an answer needs a note: the next attempt carries the declined question (no new question asked)
    ex2 = _exec([ask()], [revision(how="answered", note="  ")])
    assert w.run(_ctx(env, ex2, attempt=2)).status == "done"
    (bare,) = w.gate(ps, 2).failures
    assert "introduction" in bare and "without a note" in bare
    ex3 = _exec([ask()], [revision(how="answered", note="the age group is defined in Methods")])
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


# ---- round 1 ----
def _main_sentence(ps, cid: str) -> str:
    return _sentence({"claim_id": cid, "text": ArgumentService(ps).get_claim(cid)["text"]})


def _degraded(ps) -> list[dict]:
    return [json.loads(r["payload"]) for r in
            ps.db.execute("SELECT payload FROM events WHERE type='stage.degraded' ORDER BY seq")]


def _done(out) -> bool:
    assert out.status == "done", out.reason
    return True


def _placed(inputs):
    """A draft Writer output with the main claim only in the introduction and the findings everywhere."""
    cs = [c for c in inputs["claims"] if c["status"] == "supported" and (
        c["role"] == "finding" or (c["role"] == "main" and inputs["section"]["id"] == "introduction"))]
    return {"markdown": " ".join(_sentence(c) for c in cs) + "\n"}


def test_resume_stores_an_accepted_revision_made_for_a_carried_question(env, monkeypatch):
    import rk.production.revise as revise_mod

    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    bad = f"Aspirin was studied in 1234 older people [[clm:{main}]]."
    added = _restated(ps, main)
    # round 1 rejects the introduction's revision; round 2 asks only about theme-1 (priority 2), and the introduction
    # is revised for its carried priority-1 question alone; the store fails right after that revision is accepted
    ex = _exec([ask(("introduction", 1)), ask(("theme-1", 2))],
               [revision(add=bad), revision(add=added), revision(how="answered", note="stated in Methods")])
    real = revise_mod.write_draft

    def boom(*a, **kw):
        raise RuntimeError("crash before the accepted version was stored")

    monkeypatch.setattr(revise_mod, "write_draft", boom)
    w = ReviseWorker()
    with pytest.raises(RuntimeError):
        w.run(_ctx(env, ex))
    monkeypatch.setattr(revise_mod, "write_draft", real)
    assert [(n["round"], n["accepted"]) for n in revise_notes(ps, 1, kind="decided")] == [(1, False), (2, True)]
    assert _done(w.run(_ctx(env, ex)))
    assert _written(ex) == ["introduction", "introduction", "theme-1"] and len(ex.of("simreader")) == 2
    versions = draft_versions(ps, "introduction")
    assert [(v["stage"], v["round"]) for v in versions] == [("draft", 0), ("revise", 2)]
    assert versions[-1]["verdict"] is not None and versions[-1]["verdict"]["ok"]
    assert added in draft_text(ps, "introduction", 2)
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_kept_verdict_needs_the_same_rendered_sentence(env):
    _, ps, _ = env
    definition = "[42]: https://example.org/trials\n"

    def with_reference(inputs):
        md = writes()(inputs)["markdown"]
        if inputs["section"]["id"] != "introduction":
            return {"markdown": md}
        c = next(c for c in inputs["claims"] if c["role"] == "main")
        linked = f"{c['text'].rstrip('.')} in [the trials][42] [[clm:{c['claim_id']}]]."
        return {"markdown": md.rstrip("\n") + " " + linked + "\n\n" + definition}

    _drafted(env, writer=with_reference)
    main = main_claim(ps, 1)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    # the Writer deletes only the reference definition: the sentence's source is unchanged, but it now renders
    # "[the trials][42]", whose 42 no marker target contains
    ex = _exec([ask(("introduction", 1))], [revision(drop=definition)])
    w = ReviseWorker()
    assert _done(w.run(_ctx(env, ex)))
    (decided,) = revise_notes(ps, 1, kind="decided")
    linked = parse_section(draft_text(ps, "introduction", 1))[-1]
    assert main in linked.text and "the trials" in linked.text
    assert not decided["accepted"] and linked.index in decided["reverified"]
    assert any("the number 42 is not in any marker's target" in r for r in decided["reasons"]), decided["reasons"]
    assert len(draft_versions(ps, "introduction")) == 1


def test_revision_dropping_the_main_claim_or_citing_another_attempts_artifact_is_rejected(env):
    _, ps, _ = env
    _drafted(env, writer=_placed)
    main = main_claim(ps, 1)
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.test:x", code_version="v", input_hashes=[],
                                      values={"display": {"estimate": 0.5}}, output_path=None),
                       stage="synthesize", attempt=2, actor="k")
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    ex = _exec([ask(("introduction", 1), ("theme-1", 1))],
               [revision(drop=_main_sentence(ps, main)),
                revision(add=f"Another pooled analysis was made [[stat:{sid}]].")])
    w = ReviseWorker()
    assert _done(w.run(_ctx(env, ex)))
    intro, theme = revise_notes(ps, 1, kind="decided")
    assert not intro["accepted"] and any(f"the main claim {main} is cited in no section" in r
                                         for r in intro["reasons"]), intro["reasons"]
    assert not theme["accepted"] and any(f"[[stat:{sid}]] is not an artifact of synthesize's passed attempt" in r
                                         for r in theme["reasons"]), theme["reasons"]
    assert all(len(draft_versions(ps, sec)) == 1 for sec in REVISABLE)


def test_p1_degrade_deletes_questioned_and_failing_sentences(env):
    _, ps, _ = env
    _ready(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]

    def with_reason(inputs):
        if inputs["section"]["id"] != "conclusion":
            return writes()(inputs)
        r = next(c for c in inputs["claims"] if c["claim_id"] == reason)
        return writes(extra=_sentence(r))(inputs)

    _drafted(env, writer=with_reason)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    questioned = parse_section(draft_text(ps, "introduction", 1))[1].text
    w = ReviseWorker()
    ex = _exec([ask(("introduction", 1, 1))], [revision(how="declined", note="out of scope")])
    assert _done(w.run(_ctx(env, ex)))
    _p1_objection(ps, reason)  # the conclusion's sentence citing the reason cannot verify any more
    failures = w.gate(ps, 1).failures
    assert any("declined" in f for f in failures) and any(f.startswith("section conclusion v1") for f in failures)
    assert _done(w.degrade(_ctx(env, _exec([], [])), failures))
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    (q,) = revise_notes(ps, 1, kind="questions")[0]["questions"]
    for sec, gone in (("introduction", questioned), ("conclusion", _main_sentence(ps, reason))):
        v = draft_versions(ps, sec)[-1]
        assert (v["stage"], v["round"], v["by_task"]) == ("revise", 0, None) and v["verdict"]["ok"]
        assert gone not in draft_text(ps, sec, v["version"])
    (deg,) = _degraded(ps)
    assert deg["stage"] == "revise" and deg["questions"] == [q["id"]]
    assert any(questioned in r for r in deg["removed"]) and any(reason in r for r in deg["removed"])
    assert all(len(draft_versions(ps, sec)) == 1 for sec in ("theme-1", "gaps-agenda"))


def test_p1_degrade_fails_on_a_section_level_question_of_a_required_section(env):
    _, ps, _ = env
    _drafted(env)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    ex = _exec([ask(("theme-1", 1, None))], [revision(how="declined", note="out of scope")])
    assert _done(w.run(_ctx(env, ex)))
    (q,) = revise_notes(ps, 1, kind="questions")[0]["questions"]
    out = w.degrade(_ctx(env, _exec([], [])), w.gate(ps, 1).failures)
    assert out.status == "failed" and q["id"] in out.reason and "theme-1" in out.reason and "required" in out.reason
    assert all(len(draft_versions(ps, sec)) == 1 for sec in REVISABLE) and not _degraded(ps)
    assert not w.gate(ps, 1).ok


def test_p1_degrade_fails_when_a_deletion_would_break_coverage(env):
    _, ps, _ = env
    _drafted(env, writer=_placed)
    main = main_claim(ps, 1)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    ex = _exec([ask(("introduction", 1, 0))], [revision(how="declined", note="out of scope")])
    assert _done(w.run(_ctx(env, ex)))
    out = w.degrade(_ctx(env, _exec([], [])), w.gate(ps, 1).failures)
    assert out.status == "failed" and f"the main claim {main} is cited in no section" in out.reason
    assert len(draft_versions(ps, "introduction")) == 1 and not _degraded(ps)


def test_p1_degrade_fails_when_a_deletion_would_empty_a_section(env):
    _, ps, _ = env
    _drafted(env)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    n = len(parse_section(draft_text(ps, "gaps-agenda", 1)))
    ex = _exec([ask(*[("gaps-agenda", 1, i) for i in range(n)])], [revision(how="declined", note="no")])
    assert _done(w.run(_ctx(env, ex)))
    out = w.degrade(_ctx(env, _exec([], [])), w.gate(ps, 1).failures)
    assert out.status == "failed" and "gaps-agenda" in out.reason and "empty" in out.reason
    assert len(draft_versions(ps, "gaps-agenda")) == 1 and not _degraded(ps)


def test_new_attempt_carries_unresolved_priority1_questions(env):
    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    assert _done(w.run(_ctx(env, _exec([ask(("introduction", 1), ("theme-1", 2))],
                                       [revision(how="declined", note="out of scope")] * 2))))
    (asked,) = [q for q in revise_notes(ps, 1, kind="questions")[0]["questions"] if q["priority"] == 1]
    assert not w.gate(ps, 1).ok
    ex = _exec([ask()], [revision(add=_restated(ps, main))])
    assert _done(w.run(_ctx(env, ex, attempt=2)))
    carried = revise_notes(ps, 2, kind="questions")[0]
    assert carried["round"] == 0 and [q["id"] for q in carried["questions"]] == [asked["id"]]  # not the priority 2
    (q,) = ex.of("writer", "Revision")[0]["inputs"]["revise"]["questions"]
    assert q["id"] == asked["id"] and q["earlier_answer"]["how"] == "declined"
    assert [e["id"] for e in ex.of("simreader")[0]["inputs"]["earlier_questions"]] == [asked["id"]]
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


def test_rounds_stop_when_the_budget_cannot_cover_a_round(env):
    _, ps, _ = env
    _drafted(env)
    main = main_claim(ps, 1)
    w = ReviseWorker()
    w.budget = 6  # a round needs 1 + 4 revisable sections; after round 1 (2 tasks) 4 are left
    ex = _exec([ask(("introduction", 1))] * 3, [revision(add=_restated(ps, main))] * 3)
    assert _done(w.run(_ctx(env, ex)))
    assert len(ex.of("simreader")) == 1 and _written(ex) == ["introduction"]
    (note,) = revise_notes(ps, 1, kind="budget")
    assert (note["round"], note["used"], note["needed"], note["budget"]) == (2, 2, 5, 6)
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


# ---- Task 4 re-review ----
def test_carried_question_whose_sentence_is_gone_is_dropped_and_noted(env):
    """(b) After revise->draft, draft writes the introduction afresh without the sentence an unresolved priority-1
    question was about: the next attempt drops that question from the carry and notes why (``carry_dropped``) instead
    of carrying it into a failure nothing can answer or delete."""
    k, ps, lib = env
    _drafted(env)
    main = main_claim(ps, 1)
    save_settings(load_settings().model_copy(update={"revise_max_rounds": 1}))
    w = ReviseWorker()
    assert _done(w.run(_ctx(env, _exec([ask(("introduction", 1, 0))], [revision(how="declined", note="no")]))))
    (q,) = revise_notes(ps, 1, kind="questions")[0]["questions"]
    assert q["sentence"] == parse_section(draft_text(ps, "introduction", 1))[0].text

    def restated(inputs):
        md = writes()(inputs)["markdown"]
        if inputs["section"]["id"] == "introduction":
            md = md.replace(_main_sentence(ps, main), _restated(ps, main))
        return {"markdown": md}

    ex = Exec({("writer", "SectionDraft"): [restated] * 4})
    assert _done(DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft", attempt=2), guidance="")))
    assert DraftWorker().gate(ps, 2).ok, DraftWorker().gate(ps, 2).failures
    record_attempt(ps, "draft", 2, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, "draft", 2, "done", "", actor="tester")
    record_passed(ps, "draft", 2, actor="tester")
    ex2 = _exec([ask()], [revision(how="answered", note="stated")])
    assert _done(w.run(_ctx(env, ex2, attempt=2)))
    dropped = revise_notes(ps, 2, kind="carry_dropped")
    assert len(dropped) == 1, revise_notes(ps, 2, kind="questions")
    (d,) = dropped[0]["questions"]
    assert d["id"] == q["id"] and "no longer in section introduction" in d["reason"] and q["sentence"] in d["reason"]
    assert not _written(ex2)  # nothing to revise for it
    assert not [x for n in revise_notes(ps, 2, kind="questions") for x in n["questions"]]
    assert w.gate(ps, 2).ok, w.gate(ps, 2).failures


def test_in_a_run_the_budget_counts_the_run_sessions_tasks_of_the_stage(env):
    """(c) With a run, the role tasks used are the run session's tasks of the stage (``tasks_used``), not the
    attempt's: 36 of 40 used leave too little for a round (1 + 4 revisable sections)."""
    from rk.ids import new_id
    from rk.orchestrator.state import record_run_task, run_row, start_run

    _, ps, _ = env
    _drafted(env)
    run_id = start_run(ps, until="revise", actor="tester", clock=lambda: 1.8e9)
    session = run_row(ps, run_id)["session"]
    for stage, n in (("revise", 36), ("draft", 9)):  # another stage's tasks do not count
        for _ in range(n):
            record_run_task(ps, task_id=new_id("tsk"), run_id=run_id, session=session, stage=stage, attempt=1)
    ex = _exec([ask(("introduction", 1))], [revision(add=_restated(ps, main_claim(ps, 1)))])
    assert _done(ReviseWorker().run(replace(_ctx(env, ex), run_id=run_id)))
    assert not ex.calls
    (note,) = revise_notes(ps, 1, kind="budget")
    assert (note["round"], note["used"], note["needed"], note["budget"]) == (1, 36, 5, 40)


def test_resumed_round_whose_checked_section_no_longer_reads_goes_back_to_draft(env):
    """(d) A round's check is recorded, then the run stops before the SimReader; the introduction's file is altered
    before the resume. The resumed round does not read the error text as the section: the check runs again, fails,
    and the stage goes back to draft naming it, with no SimReader task."""
    _, ps, _ = env
    _drafted(env)
    ex = _exec([ask()], [], cls=CapOnce, at=("simreader", "ReaderQuestions"), n=1)
    w = ReviseWorker()
    with pytest.raises(RateCapped):
        w.run(_ctx(env, ex))
    assert [n["ok"] for n in revise_notes(ps, 1, kind="checked")] == [True] * 5
    (ps.root / "artifacts/drafts/introduction.v1.md").write_text("Unmarked text.\n")
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "draft"), (out.status, out.reason)
    assert "section introduction v1" in out.reason and "sha256" in out.reason
    assert not ex.of("simreader")
    rechecked = [n for n in revise_notes(ps, 1, kind="checked") if n["section"] == "introduction"]
    assert [(n["round"], n["ok"]) for n in rechecked] == [(1, True), (1, False)]
