"""Plan 5c Task 5: the audit stage. It reruns every hard gate of spec §9.2 over the final manuscript (G-REF with V-REF
and V-RETRACT looked up again, G-QUOTE over every quote note, G-COVER, V-BALANCE, G-SUPPORT, G-HEDGE, G-DISCLOSE,
G-PREREG for systematic and scoping reviews, V-PRISMA with a recomputation of every cited PRISMA event, V-STATS),
writes the versioned report and ``disclosure.md``, takes the owning back-edge on a failure, and asks for the final
review: P2 a hard-human ``final_review``, P1 the judges' vote."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from rk.argument import ArgumentService, ClaimIn, ObjectionIn
from rk.canon import sha256_hex
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.config import load_settings
from rk.discovery.boundary import transient_source_error
from rk.discovery.executor import RateCapped
from rk.library.models import ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.net.http import HttpError
from rk.notes import NoteIn, add_note, list_notes
from rk.orchestrator.engine import default_workers
from rk.orchestrator.graph import BACK_EDGES, EDGE_LIMIT, OPEN_STAGES
from rk.orchestrator.state import (
    current_stage,
    edge_rows,
    record_attempt,
    record_edge_taken,
    record_passed,
    record_ran,
    run_row,
    stage_row,
)
from rk.production.argue import argument_claims, main_claim
from rk.production.artifacts import ArtifactIn, add_artifact
from rk.production.audit import (
    AuditWorker,
    audit_manuscript,
    audit_notes,
    audit_reports,
    cited_items,
    quote_note_problem,
)
from rk.production.disclosure import DISCLOSURE_PATH, write_disclosure
from rk.production.draft import PRISMA_EVENT, DraftWorker
from rk.production.manuscript import (
    draft_text,
    draft_versions,
    latest_draft,
    write_draft,
)
from rk.production.revise import ReviseWorker
from rk.production.store import add_protocol, freeze_protocol, frozen_protocol
from rk.production.synthesis import synthesis_claims
from rk.store.events import NewEvent
from rk.verify.retract import RetractionInfo
from tests.discovery_fakes import FakeSources
from tests.production_fakes import (
    a_protocol,
    make_frozen_project,
    production_ctx,
    stage_run,
)
from tests.test_balance import object_to
from tests.test_design import _votes
from tests.test_draft import _ready, writes
from tests.test_synthesis import CapOnce, Exec


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http,
                                        **{"paper_type": "critical_review", **getattr(request, "param", {})})
    yield k, ps, lib
    k.close()


def _passed(ps, stage: str, attempt: int = 1) -> None:
    record_attempt(ps, stage, attempt, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, stage, attempt, "done", "", actor="tester")
    record_passed(ps, stage, attempt, actor="tester")


def revised(env, writer=None) -> None:
    """argue passed; every required section drafted and verified (draft attempt 1, passed); revise attempt 1 asked
    nothing and passed."""
    k, ps, lib = env
    _ready(env)
    ex = Exec({("writer", "SectionDraft"): [writer or writes()] * 10})
    out = DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft"), guidance=""))
    assert out.status == "done", out.reason
    assert DraftWorker().gate(ps, 1).ok, DraftWorker().gate(ps, 1).failures
    _passed(ps, "draft")
    ex = Exec({("simreader", "ReaderQuestions"): [{"questions": []}]})
    assert ReviseWorker().run(replace(production_ctx(k, ps, lib, ex, stage="revise"), guidance="")).status == "done"
    assert ReviseWorker().gate(ps, 1).ok, ReviseWorker().gate(ps, 1).failures
    _passed(ps, "revise")


def _ctx(env, ex, attempt: int = 1, **sources):
    k, ps, lib = env
    return replace(production_ctx(k, ps, lib, ex, stage="audit", attempt=attempt,
                                  sources=FakeSources({}, lib=lib, **sources)), guidance="")


def _judges(*choices: str) -> Exec:
    return Exec(_votes(list(choices), list(choices)))


def _report(ps) -> dict:
    return audit_reports(ps)[-1]


def _vendor(role: str) -> str | None:
    spec = DriverSpec.parse(load_settings().drivers[role])
    return vendor_of(spec.provider, spec.model)


# ---- tests ----
def test_audit_is_a_default_worker_with_its_edges():
    assert isinstance(default_workers().get("audit"), AuditWorker)
    assert {("audit", "draft"), ("audit", "argue"), ("audit", "read_extract"), ("audit", "revise")} <= BACK_EDGES
    assert "audit" not in OPEN_STAGES


def test_p1_audit_passes_writes_its_report_and_disclosure_and_is_approved_by_the_vote(env):
    _, ps, _ = env
    revised(env)
    ex = _judges("approve")
    w = AuditWorker()
    ctx = _ctx(env, ex)
    out = w.run(ctx)
    assert out.status == "done", out.reason
    # V-REF and V-RETRACT were looked up again for every cited item
    cited = {n["item_id"] for c in synthesis_claims(ps, 1) for e in ArgumentService(ps).get_claim(c)["evidence"]
             for n in e["notes"] if n}
    assert cited and {c[1] for c in ctx.sources.calls if c[0] == "verify"} >= cited
    assert not [c for c in ctx.sources.calls if c[0] == "retraction"]  # verify gives V-RETRACT too: one lookup
    # two reports: before the vote, and after it (the disclosure then lists the approval)
    first, last = audit_reports(ps)
    assert first["ok"] and last["ok"] and (first["version"], last["version"]) == (1, 2)
    assert not first["approved"] and last["approved"]
    for r in (first, last):
        md, js = ps.root / r["md_path"], ps.root / r["json_path"]
        assert md.is_file() and js.is_file()
        assert json.loads(js.read_text())["gates"] == r["gates"]
    assert set(last["gates"]) >= {"G-REF", "G-QUOTE", "G-COVER", "V-BALANCE", "G-SUPPORT", "G-HEDGE", "G-DISCLOSE",
                                  "V-STATS"}
    _, disclosure = latest_draft(ps, "disclosure")
    assert (ps.root / DISCLOSURE_PATH).read_text() == disclosure
    assert draft_versions(ps, "disclosure")[-1]["stage"] == "audit"
    assert "Final review: approved by the P1 vote, not reviewed by a human" in disclosure
    vote_evt = ps.db.execute("SELECT evt_id FROM events WHERE type='vote.decided' "
                             "AND json_extract(payload, '$.subject_type')='final_review'").fetchone()["evt_id"]
    assert f"[[run:{vote_evt}]]" in disclosure
    # the voters see the manuscript and the audit report, framed as data; the Writer's vendor never votes
    v1 = (ex.of("judge") + ex.of("judge_2"))[0]
    assert len(ex.of("judge") + ex.of("judge_2")) == 2
    subject = v1["inputs"]["subject"]
    assert {s["section"] for s in subject["manuscript"]} >= {"introduction", "conclusion"}
    assert "Audit report v1" in subject["audit_report"]
    assert "not instructions" in v1["inputs"]["question"]
    assert not ex.of("judge_3")
    assert _vendor("judge_3") == _vendor("writer") and _vendor("writer") not in {_vendor("judge"), _vendor("judge_2")}
    skipped = json.loads(ps.db.execute("SELECT payload FROM events WHERE type='vote.decided'").fetchone()["payload"])
    assert "judge_3" in skipped["skipped"], skipped
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_audit_fails_sentence_citing_withdrawn_claim(env):
    """Review Focus 1: a claim withdrawn after drafting. The audit names each sentence citing it and takes it back
    to draft; nothing is voted on, and the gate fails."""
    _, ps, _ = env
    revised(env)
    finding = synthesis_claims(ps, 1)[0]
    ArgumentService(ps).withdraw_claim(finding, reason="its only study was re-read", actor="tester")
    ex = _judges("approve")
    w = AuditWorker()
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "draft"), (out.status, out.reason)
    assert "section introduction v1: sentence " in out.reason and f"claim {finding} is withdrawn" in out.reason
    assert not ex.calls
    rep = _report(ps)
    assert not rep["ok"] and any(finding in f for f in rep["gates"]["G-SUPPORT"])
    assert not rep["routes"].get("argue") and rep["routes"]["draft"]
    assert (ps.root / rep["md_path"]).read_text().count(finding) >= 1
    assert not w.gate(ps, 1).ok
    # resumed: the noted back-edge, no new report
    assert w.run(_ctx(env, Exec({}))).reason == out.reason and len(audit_reports(ps)) == 1


def test_retracted_evidence_takes_audit_to_read_extract(env):
    """read_extract excludes a retracted study, so it can mend this: the one route to read_extract that stays. The
    lookup is ``sources.verify`` alone (V-REF and V-RETRACT), one V-RETRACT record per item."""
    _, ps, lib = env
    revised(env)
    (item,) = [i for i in lib.list() if i.title == "A trial"]
    ctx = _ctx(env, _judges("approve"), retractions={"A trial": RetractionInfo(status="retracted", sources=["openalex"])})
    out = AuditWorker().run(ctx)
    assert (out.status, out.to) == ("back_edge", "read_extract"), (out.status, out.reason)
    assert f"item {item.id}" in out.reason and "retracted" in out.reason
    assert lib.get(item.id).retraction == "retracted"
    assert any(item.id in f for f in _report(ps)["gates"]["G-REF"])
    assert [c for c in ctx.sources.calls if c[1:] == (item.id,)] == [("verify", item.id)]
    assert ps.db.execute("SELECT COUNT(*) FROM verifications WHERE target_id=? AND verifier='V-RETRACT'",
                         (item.id,)).fetchone()[0] == 1


def test_failing_quote_note_no_claim_cites_still_fails_g_quote(env):
    _, ps, lib = env
    revised(env)
    (item,) = [i for i in lib.list() if i.title == "A trial"]
    nid = add_note(ps, lib, NoteIn(source_id=item.id, kind="quote", text="Methods section.", role_in_source="main"),
                   actor="tester").note_id
    used = {n for r in ps.db.execute("SELECT data FROM evidence") for n in json.loads(r["data"])["note_ids"]}
    assert nid not in used
    note = next(n for n in list_notes(ps) if n["id"] == nid)
    ps.apply(NewEvent(type="note.added", entity_id=item.id, actor_kind="agent", actor="tester",
                      payload={"note": {**note, "text": "Words the source never says."}}))
    out = AuditWorker().run(_ctx(env, _judges("approve")))
    # notes are append-only and read_extract only adds new ones: no stage can mend it, so the audit fails at once
    assert out.status == "failed", (out.status, out.reason)
    assert "G-QUOTE" in out.reason and nid in out.reason and "integrity" in out.reason
    (failure,) = _report(ps)["gates"]["G-QUOTE"]
    assert nid in failure and "verbatim" in failure
    assert not audit_notes(ps, 1, kind="back_edge")


def test_unsupported_claim_takes_audit_to_argue(env):
    _, ps, _ = env
    revised(env)
    finding = synthesis_claims(ps, 1)[0]
    oid = ArgumentService(ps).add_objection(
        ObjectionIn(target_id=finding, category="counterexample", priority=1, text="a trial found the opposite"),
        actor="tester", actor_kind="kernel")
    out = AuditWorker().run(_ctx(env, _judges("approve")))
    assert (out.status, out.to) == ("back_edge", "argue"), (out.status, out.reason)
    rep = _report(ps)
    assert any(f.startswith(f"claim {finding}: ") and oid in f for f in rep["gates"]["G-SUPPORT"])
    assert rep["routes"]["draft"]  # its sentences fail too; argue, upstream, is taken


def test_unanswered_objection_takes_audit_to_argue_other_balance_problems_to_draft(env):
    _, ps, _ = env
    revised(env)
    main = main_claim(ps, 1)
    (reason,) = [c for c in argument_claims(ps, 1) if c != main]
    f1, f2 = synthesis_claims(ps, 1)
    o1 = object_to(ps, f1, response=reason)
    ArgumentService(ps).withdraw_claim(reason, reason="narrowed away", actor="tester")
    o2 = object_to(ps, f2, response=main)  # main is cited before f2 in every section
    out = AuditWorker().run(_ctx(env, _judges("approve")))
    assert (out.status, out.to) == ("back_edge", "argue"), (out.status, out.reason)
    rep = _report(ps)
    assert any(o1 in f and "unanswered" in f for f in rep["routes"]["argue"]), rep["routes"]
    assert any(o2 in f and "does not cite that response" in f for f in rep["routes"]["draft"]), rep["routes"]
    assert all(f in rep["gates"]["V-BALANCE"] for f in rep["routes"]["argue"])


def _cites_prisma(inputs):
    """A Writer output that cites the PRISMA event in methods."""
    md = writes()(inputs)["markdown"]
    if inputs["section"]["id"] == "methods":
        e = next(e for e in inputs["events"] if e["type"] == PRISMA_EVENT)
        n = e["numbers"]
        md = (md.rstrip("\n") + f" The search identified {n['identified']} records and included {n['included']} "
              f"studies [[run:{e['event_id']}]].\n")
    return {"markdown": md}


def _stale(ps) -> list[str]:
    return [f for f in audit_manuscript(ps, paper_type="systematic_review").gates["V-PRISMA"]
            if f.startswith("stale PRISMA counts")]


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_stale_prisma_counts_take_audit_to_draft(env):
    _, ps, _ = env
    revised(env, writer=_cites_prisma)
    (evt,) = [r["evt_id"] for r in ps.db.execute("SELECT evt_id FROM events WHERE type=?", (PRISMA_EVENT,))]
    assert f"[[run:{evt}]]" in draft_text(ps, "methods", 1)
    w = AuditWorker()
    out = w.run(_ctx(env, _judges("approve")))
    assert out.status == "done", out.reason
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures
    assert _report(ps)["gates"]["V-PRISMA"] == [] and _report(ps)["gates"]["G-PREREG"] == []
    record_passed(ps, "search_screen", 2, actor="tester")  # search_screen passed again since the counts were drafted
    out = w.run(_ctx(env, _judges("approve"), attempt=2))
    assert (out.status, out.to) == ("back_edge", "draft"), (out.status, out.reason)
    assert "stale PRISMA counts" in out.reason and evt in out.reason
    assert any("search_screen attempt 1" in f and evt in f for f in _report(ps)["gates"]["V-PRISMA"])
    assert not w.gate(ps, 2).ok


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_stale_prisma_counts_when_a_count_differs_or_the_protocol_changed(env, monkeypatch):
    import rk.production.audit as audit_mod

    _, ps, _ = env
    revised(env, writer=_cites_prisma)
    (evt,) = [r["evt_id"] for r in ps.db.execute("SELECT evt_id FROM events WHERE type=?", (PRISMA_EVENT,))]
    assert not _stale(ps)
    counts = audit_mod.prisma_counts
    monkeypatch.setattr(audit_mod, "prisma_counts", lambda ps, a: replace(counts(ps, a), identified=99))
    (stale,) = _stale(ps)
    assert evt in stale and "identified" in stale and "is now 99" in stale
    monkeypatch.setattr(audit_mod, "prisma_counts", counts)
    old = frozen_protocol(ps)["id"]
    freeze_protocol(ps, add_protocol(ps, 2, a_protocol(), version=2, actor="tester"), actor="tester")
    (stale,) = _stale(ps)
    assert evt in stale and f"under the protocol {old}" in stale and frozen_protocol(ps)["id"] in stale
    rep = audit_manuscript(ps, paper_type="systematic_review")
    assert stale in rep.routes["draft"]


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


@pytest.mark.parametrize("env", [{"production_mode": "collab"}], indirect=True)
def test_p2_final_review_approve_revise_back(env):
    _, ps, _ = env
    revised(env)
    w = AuditWorker()
    ex = Exec({})
    out = w.run(_ctx(env, ex))
    assert out.status == "waiting_human", out.reason
    cp = get_checkpoint(ps, out.checkpoint_id)
    assert cp["kind"] == "final_review" and cp["options"]["attempt"] == 1
    assert cp["options"]["choices"] == ["approve", "revise", "back"]
    assert cp["options"]["back_to"] == ["draft", "argue", "read_extract"]
    assert "report v1" in cp["prompt"]
    assert w.run(_ctx(env, ex)).checkpoint_id == cp["id"]  # resumed: the same checkpoint, opened once
    # an answer that cannot be applied opens the checkpoint again, saying why
    out = w.on_answer(_ctx(env, ex), _answer(ps, cp["id"], {"decision": "back"}))
    assert out.status == "waiting_human" and out.checkpoint_id != cp["id"]
    again = get_checkpoint(ps, out.checkpoint_id)
    assert "to" in again["prompt"] and again["options"]["replaces"] == cp["id"]
    assert w.on_answer(_ctx(env, ex), get_checkpoint(ps, cp["id"])).checkpoint_id == again["id"]  # opened once
    out = w.on_answer(_ctx(env, ex), _answer(ps, again["id"], {"decision": "revise",
                                                              "comment": "Say who the audience is."}))
    assert (out.status, out.to) == ("back_edge", "revise") and "Say who the audience is." in out.reason
    # attempt 2: back to argue
    cid = w.run(_ctx(env, ex, attempt=2)).checkpoint_id
    out = w.on_answer(_ctx(env, ex, attempt=2), _answer(ps, cid, {"decision": "back", "to": "argue",
                                                                  "comment": "The main claim is too broad."}))
    assert (out.status, out.to) == ("back_edge", "argue") and "The main claim is too broad." in out.reason
    assert not w.gate(ps, 2).ok
    # attempt 3: approved
    cid = w.run(_ctx(env, ex, attempt=3)).checkpoint_id
    out = w.on_answer(_ctx(env, ex, attempt=3), _answer(ps, cid, {"decision": "approve"}))
    assert out.status == "done", out.reason
    assert w.gate(ps, 3).ok, w.gate(ps, 3).failures
    answered = ps.db.execute("SELECT evt_id FROM events WHERE type='checkpoint.answered' AND entity_id=?",
                             (cid,)).fetchone()["evt_id"]
    disclosure = latest_draft(ps, "disclosure")[1]
    assert f"[[run:{answered}]]" in disclosure and "`final_review`" in disclosure
    assert not ex.calls  # P2 never votes
    assert [c["kind"] for c in list_checkpoints(ps)].count("final_review") == 4


def test_p1_final_review_not_approved_goes_to_revise_then_fails_at_limit(env):
    _, ps, _ = env
    revised(env)
    w = AuditWorker()
    ex = _judges("not_approved")
    out = w.run(_ctx(env, ex))
    assert (out.status, out.to) == ("back_edge", "revise"), (out.status, out.reason)
    assert "judge says not_approved" in out.reason and "not reviewed by a human" in out.reason
    calls = len(ex.calls)
    assert w.run(_ctx(env, ex)).reason == out.reason and len(ex.calls) == calls  # resumed: the vote is kept
    assert any("not approved" in f for f in w.gate(ps, 1).failures)
    # at the edge limit P1 never exports an unapproved manuscript: the degrade fails the run with the reasons
    deg = w.degrade(_ctx(env, Exec({})), [out.reason])
    assert deg.status == "failed" and "judge says not_approved" in deg.reason and "never exported" in deg.reason


@pytest.mark.parametrize("env", [{"paper_type": t} for t in ("systematic_review", "critical_review")],
                         indirect=True, ids=["systematic_review", "critical_review"])
def test_prereg_gate_only_for_systematic_and_scoping(env, monkeypatch):
    import rk.production.audit as audit_mod

    _, ps, _ = env
    revised(env)
    paper_type = ps.meta("paper_type")
    assert audit_manuscript(ps, paper_type=paper_type).gates.get("G-PREREG", []) == []
    monkeypatch.setattr(audit_mod, "protocol_prereg_ok", lambda ps: False)
    rep = audit_manuscript(ps, paper_type=paper_type)
    if paper_type == "systematic_review":
        failures = rep.gates.get("G-PREREG")
        assert failures and len(failures) == 1, rep.gates
        failure = failures[0]
        assert "frozen" in failure and not rep.ok
        assert "V-PRISMA" in rep.gates
    else:
        assert "G-PREREG" not in rep.gates and "V-PRISMA" not in rep.gates


def test_audit_resumes_in_place(env):
    _, ps, _ = env
    revised(env)
    ex = CapOnce(_votes(["approve"] * 2, ["approve"] * 2), at=("judge", "VoteOut"))
    w = AuditWorker()
    ctx = _ctx(env, ex)
    with pytest.raises(RateCapped):
        w.run(ctx)
    assert [r["version"] for r in audit_reports(ps)] == [1]
    assert [v["stage"] for v in draft_versions(ps, "disclosure")] == ["draft", "audit"]
    looked_up = len(ctx.sources.calls)
    out = w.run(ctx)  # the same attempt, in place
    assert out.status == "done", out.reason
    assert len(ctx.sources.calls) == looked_up  # V-REF and V-RETRACT are not looked up again
    assert [r["version"] for r in audit_reports(ps)] == [1, 2]
    assert [v["stage"] for v in draft_versions(ps, "disclosure")] == ["draft", "audit", "audit"]
    assert len(audit_notes(ps, 1, kind="refs")) == 1
    # interrupted after it finished: nothing is asked, written or rendered again (the same events, the same bytes)
    votes = len(ex.calls)
    assert w.run(ctx).status == "done"
    assert len(ex.calls) == votes and len(audit_reports(ps)) == 2 and len(draft_versions(ps, "disclosure")) == 3
    assert w.gate(ps, 1).ok, w.gate(ps, 1).failures


def test_write_disclosure_is_idempotent(env):
    _, ps, _ = env
    revised(env)
    v = write_disclosure(ps, attempt=1, paper_type="critical_review", actor="tester")
    assert v == draft_versions(ps, "disclosure")[-1]["version"] and draft_versions(ps, "disclosure")[-1]["stage"] == \
        "audit"
    assert write_disclosure(ps, attempt=1, paper_type="critical_review", actor="tester") == v
    assert draft_versions(ps, "disclosure")[-1]["verdict"]["ok"], draft_versions(ps, "disclosure")[-1]["verdict"]
    assert (ps.root / DISCLOSURE_PATH).is_file()
    assert (ps.root / DISCLOSURE_PATH).read_text() == latest_draft(ps, "disclosure")[1]


# ---- round 1 ----
URL = "https://api.crossref.org/works/10.1000/x"


class _Flaky(FakeSources):
    """``FakeSources`` whose first ``verify`` raises ``error``."""

    def __init__(self, *a, error: Exception, **kw):
        super().__init__(*a, **kw)
        self.error: Exception | None = error

    def verify(self, item_id: str) -> str:
        if self.error is not None:
            e, self.error = self.error, None
            raise e
        return super().verify(item_id)


def test_a_transient_lookup_error_resumes_the_audit_in_place_and_a_persistent_one_reruns_it(env):
    """I1: an HTTP 429/5xx or transport error at V-REF is raised before the ``refs`` note, so the engine waits
    (``waiting_source``) and runs the attempt again in place, spending no edge; a persistent error reruns as before."""
    _, ps, lib = env
    revised(env)
    w = AuditWorker()
    ctx = replace(_ctx(env, _judges("approve")), sources=_Flaky({}, lib=lib, error=HttpError(503, URL)))
    with pytest.raises(HttpError) as e:
        w.run(ctx)
    assert transient_source_error(e.value)
    assert not audit_notes(ps, 1, kind="refs") and not audit_notes(ps, 1, kind="back_edge")
    out = w.run(ctx)  # the same attempt, in place
    assert out.status == "done", out.reason
    (refs,) = audit_notes(ps, 1, kind="refs")
    assert refs["errors"] == []
    ctx = replace(_ctx(env, Exec({}), attempt=2), sources=_Flaky({}, lib=lib, error=HttpError(404, URL)))
    out = w.run(ctx)
    assert out.status == "rerun" and "HTTP 404" in out.reason, (out.status, out.reason)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_a_failure_no_stage_can_mend_fails_the_audit_at_once_using_no_edge(env, monkeypatch):
    """I2: G-PREREG (owned by the protocol) and V-PRISMA's arithmetic (owned by search_screen) cannot change by any
    stage the audit can go back to: the stage fails at once, naming the owner and what fixes it, in P1 and P2 alike."""
    import rk.production.audit as audit_mod

    k, ps, lib = env
    revised(env)
    monkeypatch.setattr(audit_mod, "protocol_prereg_ok", lambda ps: False)
    ex = _judges("approve")
    o, run_id = stage_run(k, ps, lib, "audit", ex)
    assert o.step("demo", run_id, actor="tester") == "failed"
    run = run_row(ps, run_id)
    assert run["status"] == "failed" and "G-PREREG" in run["reason"], run
    assert "owned by the protocol" in run["reason"] and "deviation" in run["reason"]
    assert [e for e, r in edge_rows(ps).items() if e.startswith("audit->") and r["count"]] == []
    assert not ex.calls and not audit_notes(ps, 1, kind="back_edge")
    assert list(_report(ps)["routes"]) == ["fail"]
    monkeypatch.setattr(audit_mod, "protocol_prereg_ok", lambda ps: True)
    monkeypatch.setattr(audit_mod, "check_prisma", lambda p: ["identified does not add up"])
    out = AuditWorker().run(_ctx(env, Exec({}), attempt=2))
    assert out.status == "failed" and "V-PRISMA" in out.reason and "owned by search_screen" in out.reason, out


@pytest.mark.parametrize("case", ["v_ref", "g_disclose", "disclosure_sentence"])
def test_other_failures_no_stage_can_mend_fail_the_audit_at_once(env, monkeypatch, case):
    """I3: V-REF mismatch or not_found (owned by the library item's metadata), G-DISCLOSE and any failure in the
    kernel-written disclosure section (a kernel defect) fail the audit at once; no back-edge, no vote."""
    import rk.production.audit as audit_mod
    import rk.production.disclosure as disclosure_mod

    _, ps, lib = env
    revised(env)
    (item,) = [i for i in lib.list() if i.title == "A trial"]
    sources: dict = {}
    if case == "v_ref":
        sources, owner = {"statuses": {"A trial": "mismatch"}}, "owned by the library item"
    elif case == "g_disclose":
        monkeypatch.setattr(audit_mod, "disclosure_problems", lambda ps, md, cited: ["the part Limitations is missing"])
        owner = "a kernel defect"
    else:
        render = disclosure_mod.render_disclosure
        monkeypatch.setattr(disclosure_mod, "render_disclosure", lambda ps: render(ps) + "\nA sentence without a source.\n")
        owner = "a kernel defect"
    ex = _judges("approve")
    out = AuditWorker().run(_ctx(env, ex, **sources))
    assert out.status == "failed", (out.status, out.reason)
    assert owner in out.reason
    if case == "v_ref":
        assert item.id in out.reason and "V-REF mismatch" in out.reason
    if case == "disclosure_sentence":
        assert "section disclosure" in out.reason
    assert not ex.calls and not audit_notes(ps, 1, kind="back_edge")
    assert list(_report(ps)["routes"]) == ["fail"]


def test_p1_not_approved_takes_audit_to_revise_through_the_engine_and_fails_at_the_limit(env):
    k, ps, lib = env
    revised(env)
    ex = Exec({**_votes(["not_approved"] * 2, ["not_approved"] * 2),
               ("simreader", "ReaderQuestions"): [{"questions": []}]})
    o, run_id = stage_run(k, ps, lib, "audit", ex)
    assert o.step("demo", run_id, actor="tester") == "running"
    assert current_stage(ps) == "revise" and edge_rows(ps)["audit->revise"]["count"] == 1
    assert "judge says not_approved" in stage_row(ps, "revise")["guidance"]
    assert o.step("demo", run_id, actor="tester") == "running"  # revise again: no question, it passes
    assert current_stage(ps) == "audit"
    record_edge_taken(ps, "audit->revise", EDGE_LIMIT, actor="tester")
    assert o.step("demo", run_id, actor="tester") == "failed"  # P1 at the limit never exports it unapproved
    run = run_row(ps, run_id)
    assert "never exported" in run["reason"] and "judge says not_approved" in run["reason"], run
    assert len(ex.of("judge", "VoteOut")) == 2


def test_quote_note_whose_locator_no_longer_holds_its_text_fails_v_quote(env):
    """read_extract's V-QUOTE: a given locator counts only where it holds the quote (no search elsewhere)."""
    _, ps, _ = env
    revised(env)
    note = next(n for n in list_notes(ps) if n["kind"] == "quote" and n.get("locator"))
    assert quote_note_problem(ps, note) is None
    loc = note["locator"]
    moved = {**note, "locator": {**loc, "start": loc["start"] + 1, "end": loc["end"] + 1}}
    assert "V-QUOTE" in (quote_note_problem(ps, moved) or ""), quote_note_problem(ps, moved)


def test_a_removed_report_file_is_written_again_from_its_event_before_the_vote(env):
    _, ps, _ = env
    revised(env)
    ex = CapOnce(_votes(["approve"] * 2, ["approve"] * 2), at=("judge", "VoteOut"))
    w = AuditWorker()
    ctx = _ctx(env, ex)
    with pytest.raises(RateCapped):
        w.run(ctx)
    (rep,) = audit_reports(ps)
    (ps.root / rep["md_path"]).unlink()
    out = w.run(ctx)
    assert out.status == "done", out.reason
    md = (ps.root / rep["md_path"]).read_text(encoding="utf-8")
    assert sha256_hex(md.encode()) == rep["sha256"]["md"]
    assert ex.of("judge", "VoteOut")[-1]["inputs"]["subject"]["audit_report"] == md


def test_g_ref_covers_the_studies_pooled_into_a_cited_stat_result(env):
    """A ``stat_result`` claim's evidence is its artifacts, not notes: the studies they pool are cited items too
    (looked up again, and checked by G-REF), as are those of an artifact cited directly."""
    _, ps, lib = env
    revised(env)
    pooled = lib.add_item(ItemIn(title="A pooled trial", year=2010), added_by="agent", actor="tester").item_id
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version="v", input_hashes=[],
                                      values={"k": 3, "estimate": 0.62, "item_ids": [pooled]}, output_path=None),
                       stage="synthesize", attempt=1, actor="kernel")
    svc = ArgumentService(ps)
    stat = svc.add_claim(ClaimIn(text="Pooled RR 0.62 (3 studies).", kind="stat_result", artifact_ids=[sid],
                                 number_bindings=[{"artifact_id": sid, "key": "estimate", "text": "0.62"},
                                                  {"artifact_id": sid, "key": "k", "text": "3"}]),
                         generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")
    assert cited_items(ps, [stat]) == {pooled: [stat]}
    assert pooled in cited_items(ps, [], [sid])
    _, md = latest_draft(ps, "conclusion")
    write_draft(ps, "conclusion", md.rstrip("\n") + f" Pooled RR 0.62 (3 studies) [[clm:{stat}]].\n", stage="revise",
                attempt=1, round=1, by_task=None, actor="tester")
    ctx = _ctx(env, _judges("approve"),
               retractions={"A pooled trial": RetractionInfo(status="retracted", sources=["openalex"])})
    out = AuditWorker().run(ctx)
    assert ("verify", pooled) in ctx.sources.calls
    assert any(pooled in f and "retracted" in f for f in _report(ps)["gates"]["G-REF"]), _report(ps)["gates"]
    assert (out.status, out.to) == ("back_edge", "read_extract"), (out.status, out.reason)
