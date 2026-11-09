"""Plan 5c Task 5: ``disclosure.md`` (spec §9.2 G-DISCLOSE): the models used, the human decisions, what no human
reviewed, the limitations and the preregistration deviations, every line citing its event; kernel-written,
deterministic, and itself verified as a manuscript section. G-DISCLOSE fails a disclosure that misses one of them."""

from __future__ import annotations

import json

import pytest

from rk.argument import ArgumentService
from rk.checkpoints import answer_checkpoint, create_checkpoint
from rk.discovery.models import Gap, GapEvidence, HypothesisIn, RQBody, Topic
from rk.discovery.store import add_gap, add_rq, add_topic, freeze_rq, frozen_rq
from rk.ids import new_id
from rk.orchestrator.state import record_degraded, record_run_task
from rk.production.audit import audit_manuscript
from rk.production.disclosure import (
    DISCLOSURE_PATH,
    PARTS,
    render_disclosure,
    write_disclosure,
)
from rk.production.draft import section_verdict
from rk.production.manuscript import parse_section, source_markers, write_draft
from rk.production.store import add_analysis_deviation, add_vote
from rk.production.synthesis import synthesis_claims
from rk.store.events import NewEvent
from tests.discovery_fakes import make_project
from tests.production_fakes import make_frozen_project
from tests.test_audit import revised
from tests.test_balance import object_to


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="critical_review")
    yield k, ps, lib
    k.close()


def _evt(ps, type_: str, entity_id: str | None = None) -> str:
    sql = "SELECT evt_id FROM events WHERE type=?" + (" AND entity_id=?" if entity_id else "") + " ORDER BY seq DESC"
    return ps.db.execute(sql, (type_, entity_id) if entity_id else (type_,)).fetchone()["evt_id"]


def _task(ps, role: str, driver: str, stage: str, model: str) -> str:
    tid = new_id("tsk")
    ps.apply(NewEvent(type="task.created", entity_id=tid, actor_kind="kernel", actor="tester", payload={
        "role": role, "driver": driver, "vendor": "anthropic", "instructions": "", "inputs": {}, "tools": [],
        "output_schema": {}}))
    ps.apply(NewEvent(type="task.attempt", entity_id=tid, actor_kind="agent", actor="tester", payload={
        "n": 1, "ok": True, "error": None, "raw": "", "model": model, "duration_s": 1.0, "tool_calls": 0,
        "session_id": None, "wait_s": None}))
    record_run_task(ps, task_id=tid, run_id=None, session=None, stage=stage, attempt=1)
    return tid


def _cited(md: str) -> set[str]:
    return {m.target for m in source_markers(md) if m.kind == "run"}


def _g_disclose(ps) -> list[str]:
    gates = audit_manuscript(ps, paper_type="critical_review").gates
    assert "G-DISCLOSE" in gates, gates
    return gates["G-DISCLOSE"]


def test_disclosure_lists_models_humans_votes_limitations_deviations(env):
    _, ps, _ = env
    revised(env)
    first = _task(ps, "writer", "claude_cli:claude-haiku-5-5", "draft", "claude-haiku-5-5")
    _task(ps, "writer", "claude_cli:claude-haiku-5-5", "revise", "claude-haiku-5-5")
    _task(ps, "judge", "codex_cli:gpt-6-luna", "audit", "gpt-6-luna")
    human = create_checkpoint(ps, "stage_blocked", "Allow one more?", {"choices": ["allow_one_more", "stop"]},
                              stage="draft", actor="tester")
    answer_checkpoint(ps, human, {"decision": "allow_one_more"}, answered_by="human", actor="researcher")
    voted = create_checkpoint(ps, "example_review", "Keep it?", {"choices": ["yes", "no"]}, stage="draft",
                              actor="tester")
    answer_checkpoint(ps, voted, {"decision": "no"}, answered_by="vote", actor="kernel")
    add_vote(ps, subject_type="record", subject_id="rec_1", question="Include?", options=["yes", "no"],
             conservative="no", decision="no", conservative_used=True, reason="the judges disagree",
             opinions=[], skipped={}, stage="search_screen", attempt=1, actor="tester")
    record_degraded(ps, stage="draft", attempt=1, removed=["introduction: “A sentence [[clm:clm_x]].”"],
                    reason="failing sentences deleted", actor="tester")
    rq = frozen_rq(ps)
    ps.apply(NewEvent(type="rq.frozen", entity_id=rq["id"], actor_kind="agent", actor="tester",
                      payload={"text_hash": rq["text_hash"], "adopted_by": "agent"}))
    finding = synthesis_claims(ps, 1)[1]
    ArgumentService(ps).withdraw_claim(finding, reason="its study was flagged", actor="tester")
    dev = add_analysis_deviation(ps, 1, "fell back to a narrative synthesis", "synthesize", actor="tester")
    md = render_disclosure(ps)
    assert render_disclosure(ps) == md  # the same events, the same bytes
    assert [ln[4:] for ln in md.splitlines() if ln.startswith("### ")] == list(PARTS)
    cited = _cited(md)
    expected = {
        "the writer's first task": _evt(ps, "task.created", first),
        "the human answer": _evt(ps, "checkpoint.answered", human),
        "the vote's answer": _evt(ps, "checkpoint.answered", voted),
        "the P1 vote": _evt(ps, "vote.decided"),
        "the degrade": _evt(ps, "stage.degraded"),
        "the agent's research question": _evt(ps, "rq.frozen", rq["id"]),
        "the withdrawn claim": _evt(ps, "claim.withdrawn", finding),
        "the deviation": dev,
    }
    assert {k: v for k, v in expected.items() if v not in cited} == {}
    writer = next(s for s in parse_section(md) if "`writer`" in s.text)
    assert "`2` tasks" in writer.text and "`claude_cli:claude-haiku-5-5`" in writer.text
    assert "drafting" in writer.text and "revision" in writer.text
    # the human decision: its kind, decision and time, citing the answer
    answered = ps.db.execute("SELECT payload FROM events WHERE type='checkpoint.answered' AND entity_id=?",
                             (human,)).fetchone()["payload"]
    (line,) = [s for s in parse_section(md) if expected["the human answer"] in {m.target for m in s.markers}]
    assert line.text.startswith("The researcher answered the `stage_blocked` checkpoint"), line.text
    assert f"`{human}`" in line.text and "with `allow_one_more`" in line.text
    assert f"at `{json.loads(answered)['answered_at']}`" in line.text
    for s in parse_section(md):
        if any(m.target in (expected["the vote's answer"], expected["the P1 vote"], expected["the degrade"],
                            expected["the agent's research question"]) for m in s.markers):
            assert "not reviewed by a human" in s.text, s.text
    # kernel-written, it verifies as a section (every sentence cites its event; data in code spans)
    verdict = section_verdict(ps, "disclosure", md, paper_type="critical_review")
    assert verdict.ok, [p for v in verdict.sentences for p in v.problems] + verdict.problems


def test_disclosure_misses_a_human_checkpoint_fails_g_disclose(env):
    _, ps, _ = env
    revised(env)
    write_disclosure(ps, attempt=1, paper_type="critical_review", actor="tester")
    assert _g_disclose(ps) == []
    cid = create_checkpoint(ps, "stage_blocked", "Allow one more?", {"choices": ["allow_one_more", "stop"]},
                            stage="revise", actor="tester")
    answer_checkpoint(ps, cid, {"decision": "stop"}, answered_by="human", actor="researcher")
    (failure,) = _g_disclose(ps)
    assert cid in failure and "not listed" in failure and "human" in failure
    write_disclosure(ps, attempt=1, paper_type="critical_review", actor="tester")
    assert _g_disclose(ps) == []
    # a part removed, and disclosure.md no longer the section's text
    md = render_disclosure(ps)
    cut = md.replace("### Limitations\n", "")
    write_draft(ps, "disclosure", cut, stage="audit", attempt=1, round=0, by_task=None, actor="tester")
    failures = _g_disclose(ps)
    assert any("Limitations" in f and "part" in f for f in failures), failures
    assert any(DISCLOSURE_PATH.rsplit("/", 1)[-1] in f and "differs" in f for f in failures), failures


def test_disclosure_names_conceded_sourced_objections(env):
    _, ps, lib = env
    revised(env)
    finding = synthesis_claims(ps, 1)[0]
    source = lib.list()[0].id
    oid = object_to(ps, finding, source_id=source, status="conceded")
    md = render_disclosure(ps)
    lines = [s for s in parse_section(md) if oid in s.text]
    assert len(lines) == 1, md
    line = lines[0]
    assert finding in line.text and source in line.text
    assert _evt(ps, "objection.responded", oid) in {m.target for m in line.markers}
    write_disclosure(ps, attempt=1, paper_type="critical_review", actor="tester")
    assert _g_disclose(ps) == []
    # a disclosure without that line (disclosure.md alike) fails, naming the claim, the objection and the source
    cut = md[:md.index(line.text)] + md[md.index(line.text) + len(line.text):].lstrip("\n")
    write_draft(ps, "disclosure", cut, stage="audit", attempt=1, round=0, by_task=None, actor="tester")
    (ps.root / DISCLOSURE_PATH).write_text(cut)
    (failure,) = _g_disclose(ps)
    assert finding in failure and oid in failure and source in failure


def test_disclosure_lists_the_gaps_behind_the_chosen_topic(tmp_home, replay_http):
    """Each gap behind the frozen research question's topic is a line of its own (not reviewed by a human: verified
    only within the search scope), citing its ``gap.added``; a gap behind another topic is not listed."""
    k, ps, _ = make_project(tmp_home, replay_http, paper_type="critical_review")
    try:
        gaps = [add_gap(ps, 1, Gap(kind="contradiction", statement=f"Trials disagree ({n}).",
                                   evidence=GapEvidence(item_ids=["src_1", "src_2"])), verified=True, checks={},
                        actor="gapminer") for n in ("one", "two", "three")]
        chosen = add_topic(ps, 1, Topic(title="Aspirin over 80", summary="S.", gap_ids=gaps[:2], feasibility="F.",
                                        data_needed="D.", paper_type="critical_review", risks="R."), actor="tester")
        body = RQBody.model_validate({
            "three_step": {"x": "aspirin over 80", "y": "stroke", "z": "when to start"},
            "problem": {"condition": "Guidelines stop at 80.", "consequences": ["A.", "B."]},
            "finer": {"feasible": "F.", "interesting": "I.", "novel": "N.", "ethical": "E.", "relevant": "R."},
            "clinical": False})
        rq = add_rq(ps, 1, chosen, body, [HypothesisIn(statement="H.", falsification="X.")], actor="tester")
        freeze_rq(ps, rq, adopted_by="human", actor="tester")
        md = render_disclosure(ps)
        for gid in gaps[:2]:
            (line,) = [s for s in parse_section(md) if f"`{gid}`" in s.text]
            assert [m.target for m in line.markers] == [_evt(ps, "gap.added", gid)]
            assert "within the search scope" in line.text and "not reviewed by a human" in line.text
        assert f"`{gaps[2]}`" not in md
        part = md[md.index("### Not reviewed by a human"):md.index("### Limitations")]
        assert all(f"`{gid}`" in part for gid in gaps[:2])
    finally:
        k.close()
