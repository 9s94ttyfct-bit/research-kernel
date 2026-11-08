"""Plan 5b Task 7: the synthesis, argument and design read tools (``rk_synthesis_get``, ``rk_argument_get``,
``rk_design_get``), their CLI, and the Workbench: the synthesis panel on Progress (the forest plot an ``<img>`` of the
stored SVG), the Argument page, and the ``pooling_approval``, ``uncertain_support``, ``evidence_conflict`` and
``design_approval`` cards, clicked in a real headless Chromium, their answers applied by the real workers through
``rk serve``'s RunManager."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from rk.argument import ArgumentService
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.orchestrator.engine import Orchestrator
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    attempt_guidance,
    edge_rows,
    record_attempt,
    record_passed,
    record_ran,
    run_row,
    stage_row,
    stages_of,
)
from rk.production import store
from rk.production.argue import (
    ArgueWorker,
    argue_notes,
    argument_claims,
    argument_roles,
    main_claim,
)
from rk.production.artifacts import artifacts
from rk.production.design import DesignWorker, design_notes, feasibility_claims
from rk.production.extraction import reread_request
from rk.production.synthesis import SynthesizeWorker, synthesis_claims, synthesis_notes
from rk.roles.policy import ROLES, TOOL_ROLES, lead_closed_after_discovery, role_tools
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.server.runtime import acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied
from rk.ui.auth import COOKIE, UiAuth
from tests.discovery_fakes import FakeWorker
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_argue import (
    F1,
    F2,
    MAIN,
    REASON,
    RESPONSE,
    WARRANT,
    _judges,
    _o,
    _synth_narrative,
    _synthesize,
    claim,
    critique,
    draft,
    responses,
)
from tests.test_argue import _exec as argue_exec
from tests.test_design import FEASIBLE, NEW_H, _argued, plan
from tests.test_design import _exec as design_exec
from tests.test_synthesis import (
    BCG,
    BCG_AE,
    BINARY,
    BINARY_AE,
    DESIGN,
    FINDING,
    META,
    META2,
    Exec,
    NoNarrative,
    _enable_stats,
    _one_group,
    _setup,
    _two,
    _two_findings,
    assess,
    narrative,
)
from tests.test_synthesis import _exec as synth_exec
from tests.test_ui_api import ORIGIN, SERVE_TOKEN, login
from tests.test_ui_production import _answer_of, _show, until

READ_TOOLS_5B = ("rk_synthesis_get", "rk_argument_get", "rk_design_get")
FULL = {"lead", "gapminer", "ideator", "arguer", "skeptic", "simreader", "writer"}
WHY = "One says <b>randomised</b>, the other cohort."  # a Skeptic's text with markup: shown as text


def _cli() -> CallContext:
    return CallContext(role="cli", actor="tester")


def _pass_before(ps, stage: str) -> None:
    """Record every stage before ``stage`` that has not passed as passed (the stages the seeding did not run)."""
    names = stages_of(ps)
    for s in names[:names.index(stage)]:
        row = stage_row(ps, s)
        if row is None or row["status"] != "passed":
            record_passed(ps, s, 1, actor="tester")


def _ran(ps, stage: str, out) -> None:
    """Record attempt 1 of ``stage`` as run by a worker called directly (``out``: its outcome) and passed."""
    record_attempt(ps, stage, 1, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, stage, 1, out.status, out.reason, actor="tester")
    record_passed(ps, stage, 1, actor="tester")


def _seed_pooled(env) -> dict[str, str]:
    """P1: a passed synthesize attempt 1 that pooled the three BCG trials (the vote said pool)."""
    k, ps, lib = env
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    votes = {("judge", "VoteOut"): [{"choice": "pool", "reason": "same PICO"}],
             ("judge_2", "VoteOut"): [{"choice": "pool", "reason": "same PICO"}]}
    ex = synth_exec(plans=[lambda inputs: _two([s["item_id"] for s in inputs["studies"]], ["tuberculosis"])],
                    votes=votes)
    out = SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize"))
    assert out.status == "done", out.reason
    _ran(ps, "synthesize", out)
    return ids


def _seed_conflict(env) -> tuple[str, str]:
    """P2: a passed argue attempt 1 whose findings conflict: both contested (your answer), each objection answered,
    the main claim answering the RQ."""
    k, ps, lib = env
    a, b = _synthesize(env)
    conflict = {"claim_a": a, "claim_b": b, "why": WHY}
    ex = argue_exec([draft(), responses(RESPONSE, RESPONSE)],
                    [critique(conflicts=[conflict]), critique(conflicts=[conflict])])
    w = ArgueWorker()
    ctx = production_ctx(k, ps, lib, ex, stage="argue")
    out = w.run(ctx)
    for answer in ({"decision": "both_contested"}, {"claims": {}, "answers_rq": "yes"}):
        answer_checkpoint(ps, out.checkpoint_id, answer, answered_by="human", actor="researcher")
        out = w.on_answer(ctx, get_checkpoint(ps, out.checkpoint_id))
    assert out.status == "done", out.reason
    _ran(ps, "argue", out)
    return a, b


# ---- the read tools ---------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_home, replay_http, request):
    """The Workbench TestClient (logged in) of ``rk serve`` over the frozen-RQ project ``demo`` (intake overrides in
    ``request.param``; default a systematic review in P1)."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **{"paper_type": "systematic_review",
                                                                  "production_mode": "auto",
                                                                  **getattr(request, "param", {})})
    reg = build_registry(k)
    auth = UiAuth(ORIGIN)
    client = TestClient(create_app(reg, SERVE_TOKEN, tokens=k.tokens, chats=ChatManager(k, reg), ui=auth, kernel=k))
    client.auth_ui = auth
    login(client)
    yield SimpleNamespace(client=client, kernel=k, ps=ps, lib=lib, reg=reg, env=(k, ps, lib))
    k.close()


def _get(api, path: str, **params):
    return api.client.get(path, params={"project": "demo", **params})


def test_read_tools_policy_5b(api):
    for name in READ_TOOLS_5B:
        assert TOOL_ROLES[name] == {"cli", *FULL}, name
        tool = api.reg.get(name)
        assert not tool.writes and not lead_closed_after_discovery(name, tool.writes)  # the Lead reads after question
        assert tool.roles == TOOL_ROLES[name]
    for role in ("screener_a", "screener_b", "reader", "scout", "judge", "judge_2", "judge_3", "triage"):
        assert not set(READ_TOOLS_5B) & set(role_tools(role, api.reg)), role
    for role in FULL:
        assert set(READ_TOOLS_5B) <= set(role_tools(role, api.reg)), role
    for role in ("screener_a", "reader"):
        with pytest.raises(PermissionDenied):
            api.reg.call("rk_argument_get", {"project": "demo"}, CallContext(role=role, actor="t"))
    assert set(ROLES) >= {r for n in READ_TOOLS_5B for r in TOOL_ROLES[n]} - {"cli"}


def test_read_tools_5b_before_anything_ran(api):
    got = api.reg.call("rk_synthesis_get", {"project": "demo"}, _cli())
    assert (got["attempt"], got["findings"], got["artifacts"]) == (None, [], [])
    got = api.reg.call("rk_argument_get", {"project": "demo"}, _cli())
    assert (got["attempt"], got["claims"], got["edges"], got["objections"]) == (None, [], [], [])
    got = api.reg.call("rk_design_get", {"project": "demo"}, _cli())
    assert (got["attempt"], got["design"], got["frozen_hash"]) == (None, None, None)


def test_synthesis_tool_shows_artifacts_with_their_v_stats_verdict(api):
    ids = _seed_pooled(api.env)
    got = api.reg.call("rk_synthesis_get", {"project": "demo"}, _cli())
    assert got["attempt"] == 1 and got["gate"]["passed"]
    assert {a["item_id"] for a in got["assessments"]} == set(ids.values())
    assert all(set(a["criteria"]) >= {"accurate", "sufficient", "relevant"} for a in got["assessments"])
    (g,) = got["groups"]
    assert (g["outcome"], g["measure"], g["decision"]["decision"], g["decision"]["by"]) == ("tuberculosis", "RR",
                                                                                          "pool", "vote")
    assert g["pooled"]["fig"].startswith("fig_") and g["pooled"]["stt"].startswith("stt_")
    assert got["pool_options"] == {"model": "DL", "hksj": "auto", "pi_df": "k-2", "level": 0.95, "min_studies": 2}
    by_id = {a["id"]: a for a in got["artifacts"]}
    fig = by_id[g["pooled"]["fig"]]
    assert fig["kind"] == "fig" and fig["media_type"] == "image/svg+xml" and fig["v_stats"]["verdict"] == "pass"
    assert by_id[g["pooled"]["stt"]]["values"]["display"]["k"] == 3
    stat = next(f for f in got["findings"] if f["kind"] == "stat_result")
    assert stat["status"] == "supported" and stat["v_stats"] == "pass"
    assert stat["hedge"]["verdict"] == "pass"
    finding = next(f for f in got["findings"] if f["text"] == FINDING)
    assert finding["evidence"] and finding["evidence"][0]["notes"][0]["text"].startswith("The ")
    assert got["narrative"]["representativeness"].startswith("The 3 included studies")
    assert _get(api, "/ui/api/synthesis").json()["result"]["findings"] == got["findings"]


def test_artifact_route_serves_only_project_artifacts(api):
    _seed_pooled(api.env)
    (fig,) = [a for a in artifacts(api.ps, "synthesize", 1) if a["kind"] == "fig"]
    (stt,) = [a for a in artifacts(api.ps, "synthesize", 1) if a["kind"] == "stt"]
    r = api.client.get(f"/ui/api/artifacts/demo/{fig['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert r.text.lstrip().startswith(("<svg", "<?xml"))
    r = api.client.get(f"/ui/api/artifacts/demo/{stt['id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    for bad in ("fig_nope", "..%2F..%2Fsettings.json", "%2E%2E"):
        assert api.client.get(f"/ui/api/artifacts/demo/{bad}").status_code == 404, bad
    # an artifact row whose stored path leaves the artifacts directory is refused
    outside = api.ps.root / "outside.svg"
    outside.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    api.ps.db.execute("UPDATE artifacts SET path='artifacts/../outside.svg' WHERE id=?", (fig["id"],))
    assert api.client.get(f"/ui/api/artifacts/demo/{fig['id']}").status_code == 404
    assert api.client.get(f"/ui/api/artifacts/nope/{stt['id']}").status_code == 404


@pytest.mark.parametrize("api", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_argument_tool_shows_claims_edges_warrants_and_objections(api):
    a, b = _seed_conflict(api.env)
    got = api.reg.call("rk_argument_get", {"project": "demo"}, _cli())
    ps = api.ps
    assert got["attempt"] == 1 and got["main_claim_id"] == main_claim(ps, 1) and got["gate"]["passed"]
    assert got["synthesis_attempt"] == 1
    roles = {c["id"]: c["role"] for c in got["claims"]}
    assert roles[got["main_claim_id"]] == "main" and roles[a] == roles[b] == "synthesis"
    assert {"reason", "response", "warrant"} <= set(roles.values())
    by_id = {c["id"]: c for c in got["claims"]}
    assert by_id[a]["contested"] and by_id[a]["status"] == "supported" and by_id[a]["hedge"]["verdict"] == "pass"
    assert set(argument_claims(ps, 1)) <= set(by_id)
    (w,) = got["warrants"]
    assert w["warrant"] == WARRANT and by_id[w["claim_id"]]["origin"] == "warrant"
    assert set(w["tests"]) == {"true", "limited", "circumstance_fits", "consequence_fits", "accepted"}
    assert {"reason_of", "warrant_of"} <= {e["type"] for e in got["edges"]}
    contested = [o for o in got["objections"] if o["target_id"] in (a, b)]
    assert len(contested) == 2 and all(o["status"] == "responded" and o["response"]["status"] == "supported"
                                       for o in contested)
    assert all(o["counterpart"] in (a, b) for o in contested)
    (c,) = got["conflicts"]
    assert (c["claim_a"], c["claim_b"], c["why"], c["decision"]["decision"]) == (a, b, WHY, "both_contested")
    assert got["question"] and got["rq_checks"][-1]["decision"] == "answers"
    one = api.reg.call("rk_argument_get", {"project": "demo", "claim_id": a}, _cli())
    assert [c["id"] for c in one["claims"]] == [a] and [o["target_id"] for o in one["objections"]] == [a]
    assert _get(api, "/ui/api/argument", claim_id=a).json()["result"]["claims"] == one["claims"]
    assert _get(api, "/ui/api/argument", claim_id="clm_nope").status_code == 404


def _argue_directly(env, drafts, critiques, *, attempt: int = 1):
    """Run argue attempt ``attempt`` with the scripted rounds (P2: the final review says the main claim answers the
    RQ, no claim to decide) and record it run and passed."""
    k, ps, lib = env
    w = ArgueWorker()
    record_attempt(ps, "argue", attempt, run_id=None, guidance="", requested=None, actor="tester")
    ctx = production_ctx(k, ps, lib, argue_exec(drafts, critiques), stage="argue", attempt=attempt)
    out = w.run(ctx)
    if out.status == "waiting_human":
        answer_checkpoint(ps, out.checkpoint_id, {"claims": {}, "answers_rq": "yes"}, answered_by="human",
                          actor="researcher")
        out = w.on_answer(ctx, get_checkpoint(ps, out.checkpoint_id))
    assert out.status == "done", out.reason
    record_ran(ps, "argue", attempt, out.status, out.reason, actor="tester")
    record_passed(ps, "argue", attempt, actor="tester")


@pytest.mark.parametrize("api", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_argument_view_roles_are_argues_own(api):
    """A finding reused as a reason (round 1) and as a response (round 2) keeps argue's role for it."""
    ps = api.ps
    a, b = _synthesize(api.env)

    def reasons(inputs):
        return [{"ref": "r1", "claim_id": a, "supports": "main", "warrant": WARRANT}]

    def answer_with_b(inputs):
        return {"responses": [{"objection_id": o["id"], "status": "responded", "claim_id": b}
                              for o in inputs["open_objections"]]}

    _argue_directly(api.env, [draft(reasons=reasons), answer_with_b, responses()],
                    [critique(objections=[_o("main", 2, "Too broad.")]), critique()])
    got = api.reg.call("rk_argument_get", {"project": "demo"}, _cli())
    roles = {c["id"]: c["role"] for c in got["claims"]}
    assert roles == argument_roles(ps, 1, synthesis_claims(ps, 1))
    assert roles[a] == roles[b] == "synthesis"
    assert any(e["type"] == "reason_of" and e["from_id"] == a for e in got["edges"])
    (o,) = got["objections"]
    assert o["response"]["id"] == b and o["status"] == "responded"


@pytest.mark.parametrize("api", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_older_argue_attempt_shows_its_own_synthesis_findings(api):
    k, ps, lib = api.env
    a, b = _synthesize(api.env)
    _argue_directly(api.env, [draft()], [critique()])
    # synthesize runs again (attempt 2, passed) with another finding; argue attempt 1 still argued on attempt 1
    later = "Aspirin was studied in very old people."
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_synth_narrative([later])],
               **_judges()})
    record_attempt(ps, "synthesize", 2, run_id=None, guidance="", requested=None, actor="tester")
    out = SynthesizeWorker().run(production_ctx(k, ps, lib, ex, stage="synthesize", attempt=2))
    assert out.status == "done", out.reason
    record_ran(ps, "synthesize", 2, out.status, out.reason, actor="tester")
    record_passed(ps, "synthesize", 2, actor="tester")
    got = api.reg.call("rk_argument_get", {"project": "demo", "attempt": 1}, _cli())
    findings = {c["id"] for c in got["claims"] if c["role"] == "synthesis"}
    assert got["synthesis_attempt"] == 1 and findings == {a, b}
    assert later not in {c["text"] for c in got["claims"]}


@pytest.mark.parametrize("api", [{"paper_type": "critical_review", "production_mode": "collab"}], indirect=True)
def test_withdrawn_findings_are_kept_apart_and_unsupported_ones_left_out(api):
    ps = api.ps
    a, b = _synthesize(api.env)
    _argue_directly(api.env, [draft()], [critique()])
    svc = ArgumentService(ps)
    svc.withdraw_claim(b, reason="contradicted", actor="tester")
    svc.set_claim_status(a, "uncertain", verification_id=None, actor="tester")  # no longer a supported finding
    got = api.reg.call("rk_argument_get", {"project": "demo"}, _cli())
    by_id = {c["id"]: c for c in got["claims"]}
    assert a not in by_id
    assert by_id[b]["role"] == "synthesis" and by_id[b]["status"] == "withdrawn"
    assert by_id[b]["hedge"]["verdict"] is None and by_id[b]["problems"] == []


@pytest.mark.parametrize("api", [{"paper_type": "research_proposal", "production_mode": "collab"}], indirect=True)
def test_design_tool_shows_the_pending_draft_then_the_frozen_design(api):
    k, ps, lib = api.env
    _argued(api.env)
    ex = design_exec([plan()])
    w = DesignWorker()
    ctx = production_ctx(k, ps, lib, ex, stage="design")
    out = w.run(ctx)
    assert out.status == "waiting_human", out.reason
    pending = api.reg.call("rk_design_get", {"project": "demo", "attempt": 1}, _cli())
    assert pending["draft_id"] == "design:1" and pending["status"] == "awaiting approval"
    assert pending["frozen_hash"] is None
    hyps = {h["key"]: h for h in pending["hypotheses"]}
    assert hyps["h_new"]["new"] and hyps["h_new"]["statement"] == NEW_H and hyps["h_new"]["falsified_by"] == ["M2"]
    assert [i["id"] for i in pending["design"]["items"]] == ["D1", "D2"]
    (fc,) = pending["feasibility_claims"]
    assert fc["text"] == FEASIBLE and fc["status"] == "supported"
    answer_checkpoint(ps, out.checkpoint_id, {"decision": "approve"}, answered_by="human", actor="researcher")
    assert w.on_answer(ctx, get_checkpoint(ps, out.checkpoint_id)).status == "done"
    _ran(ps, "design", out)
    got = api.reg.call("rk_design_get", {"project": "demo"}, _cli())
    (approved,) = design_notes(ps, 1, kind="approved")
    assert got["status"] == "frozen" and got["frozen_hash"] == approved["frozen_hash"]
    assert got["design_id"] == approved["design_id"]
    assert set(got["design"]["falsified_by"]) == set(approved["hypothesis_ids"].values())
    assert {h["status"] for h in got["rq_hypotheses"]} == {"tested_by_design"}
    assert _get(api, "/ui/api/design").json()["result"]["frozen_hash"] == got["frozen_hash"]


def test_cli_5b_commands(serve_rk):
    from typer.testing import CliRunner

    from rk.cli.main import app

    client = serve_rk()
    client.call("rk_project_create", {"slug": "demo", "title": "Demo", "paper_type": "research_proposal"}, write=True)
    ps, lib = client.kernel.project("demo")
    from rk.discovery.models import Intake
    from rk.discovery.store import record_intake
    from tests.discovery_fakes import DEFAULT_INTAKE, freeze_an_rq

    record_intake(ps, Intake(**{**DEFAULT_INTAKE, "paper_type": "research_proposal", "production_mode": "collab"}),
                  actor="t", actor_kind="human")
    freeze_an_rq(ps)
    env = (client.kernel, ps, lib)
    _argued(env)
    run = CliRunner()
    r = run.invoke(app, ["synthesis", "show", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert "synthesize attempt 1" in r.output and F1 in r.output and "supported" in r.output
    r = run.invoke(app, ["argument", "show", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert MAIN in r.output and REASON in r.output and "warrant" in r.output
    main = main_claim(ps, 1)
    r = run.invoke(app, ["argument", "show", "--project", "demo", "--claim", main])
    assert r.exit_code == 0 and MAIN in r.output and REASON not in r.output, r.output
    r = run.invoke(app, ["design", "show", "--project", "demo"])
    assert r.exit_code == 0 and "no design yet" in r.output, r.output
    # a design awaiting approval, then frozen
    k = client.kernel
    ex = design_exec([plan()])
    w = DesignWorker()
    record_attempt(ps, "design", 1, run_id=None, guidance="", requested=None, actor="tester")
    out = w.run(production_ctx(k, ps, lib, ex, stage="design"))
    record_ran(ps, "design", 1, out.status, out.reason, actor="tester")
    r = run.invoke(app, ["design", "show", "--project", "demo"])
    assert r.exit_code == 0 and "design:1: awaiting approval" in r.output, r.output
    assert "hypothesis h_new (new): " + NEW_H in r.output and "falsified by M2" in r.output
    assert "item D1 (experiment)" in r.output and "measurement M1: stroke rate M1 by registry" in r.output
    answer_checkpoint(ps, out.checkpoint_id, {"decision": "approve"}, answered_by="human", actor="researcher")
    assert w.on_answer(production_ctx(k, ps, lib, ex, stage="design"), get_checkpoint(ps, out.checkpoint_id)).status \
        == "done"
    r = run.invoke(app, ["design", "show", "--project", "demo"])
    (approved,) = design_notes(ps, 1, kind="approved")
    assert f"design:1: frozen, frozen hash {approved['frozen_hash']}" in r.output, r.output
    # a pooled systematic review
    client.call("rk_project_create", {"slug": "pooled", "title": "Pooled", "paper_type": "systematic_review"},
                write=True)
    ps2, lib2 = k.project("pooled")
    record_intake(ps2, Intake(**{**DEFAULT_INTAKE, "paper_type": "systematic_review", "production_mode": "auto"}),
                  actor="t", actor_kind="human")
    freeze_an_rq(ps2)
    _seed_pooled((k, ps2, lib2))
    r = run.invoke(app, ["synthesis", "show", "--project", "pooled"])
    assert r.exit_code == 0, r.output
    (stt,) = [a for a in artifacts(ps2, "synthesize", 1) if a["kind"] == "stt"]
    assert "pool options: model DL, HKSJ auto, prediction interval df k-2" in r.output
    assert "group 0: tuberculosis (RR), 3 studies; pooled (stt_" in r.output
    assert f"{stt['id']} rk.stats.meta:pool: V-STATS pass" in r.output
    assert "Pooled RR" in r.output and "[supported, hedge pass, V-STATS pass]" in r.output
    r = run.invoke(app, ["argument", "show", "--project", "demo", "--json"])
    assert r.exit_code == 0 and '"warrants"' in r.output
    r = run.invoke(app, ["eval", "stats", "--help"])
    assert r.exit_code == 0 and "--write-config" in r.output


# ---- the Workbench, in a real browser, its answers applied by the workers through a run ---------------------------
@pytest.fixture
def served(tmp_home, replay_http, request):
    """``rk serve`` (uvicorn in a thread) with an Orchestrator and RunManager over the frozen-RQ project ``demo`` in P2
    (intake overrides in ``request.param``): fake discovery and earlier production workers, the real synthesize,
    argue and design workers over the executor the test sets (``served.use(ex)``)."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **{"paper_type": "systematic_review",
                                                                  "production_mode": "collab",
                                                                  **getattr(request, "param", {})})
    rt = acquire_serve_lock(tmp_home)
    holder: dict = {}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    fakes = {s: FakeWorker() for s in (*DISCOVERY, "protocol", "search_screen", "read_extract") if s != "intake"}
    orch = Orchestrator(k, reg, workers={**fakes, "synthesize": SynthesizeWorker(), "argue": ArgueWorker(),
                                         "design": DesignWorker()},
                        executor_factory=lambda p, r, s: holder["ex"])
    runs = holder["m"] = RunManager(k, reg, orch, poll_s=0.05)
    ui = UiAuth(f"http://127.0.0.1:{rt.port}")
    app = create_app(reg, rt.token, tokens=k.tokens, chats=ChatManager(k, reg), ui=ui, kernel=k, runs=runs)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
    t.start()
    time.sleep(0.5)
    yield SimpleNamespace(kernel=k, ps=ps, lib=lib, ui=ui, runs=runs, env=(k, ps, lib),
                          use=lambda ex: holder.__setitem__("ex", ex))
    server.should_exit = True
    t.join(5)
    runs.close()
    k.close()
    release(rt)


@pytest.fixture
def page(served):
    """A headless Chromium tab signed in to the Workbench by cookie (the Chat page is never loaded)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Playwright Chromium is not available: {e}")
        ctx = browser.new_context(viewport={"width": 1280, "height": 2400})
        ctx.add_cookies([{"name": COOKIE, "value": served.ui.redeem(served.ui.new_login_code()),
                          "url": served.ui.origin}])
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        pg.goto_route = lambda route: pg.goto(f"{served.ui.origin}/ui/#/{route}")
        yield pg
        browser.close()


def _start(served, stage: str, ex) -> str:
    served.use(ex)
    _pass_before(served.ps, stage)
    return served.runs.start("demo", stage, actor="tester")


@pytest.mark.browser
def test_pooling_card_answers_reach_the_worker(page, served):
    ps = served.ps
    _enable_stats()
    ids = _setup(served.env, BCG_AE, schema=BINARY_AE, plan=META2)

    def two(inputs):
        out = _two([s["item_id"] for s in inputs["studies"]], ["tuberculosis", "adverse events"])
        for g in out["groups"]:
            g["reason"] = "Same <i>PICO</i>."
        return out

    run_id = _start(served, "synthesize", synth_exec(plans=[two]))
    cp, card = _show(page, ps, "pooling_approval")
    for text in ("tuberculosis", "adverse events", "Aronson 1950", "Ferguson 1951", "Rosenthal 1952",
                 "Same <i>PICO</i>.", "DerSimonian", "k−2", "Population", "2/2",
                 "at least 2 studies with a computable effect"):
        assert card.get_by_text(text, exact=False).count() >= 1, text
    assert card.locator(".card-body i").count() == 0  # the planner's reason is shown as text
    submit = card.get_by_role("button", name="Submit pooling decisions")
    assert submit.is_disabled()
    card.get_by_label("Pool: tuberculosis (RR)", exact=True).check()
    assert submit.is_disabled()  # every group needs your decision
    card.get_by_label("Do not pool: adverse events (RR)").check()
    assert submit.is_enabled()
    submit.click()
    assert _answer_of(ps, cp["id"]) == {"decisions": {"0": "pool", "1": "no_pool"}}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    assert [a["kind"] for a in artifacts(ps, "synthesize", 1)] == ["stt", "fig"]
    assert [n["by"] for n in synthesis_notes(ps, 1, kind="decision")] == ["human", "human"]
    assert any("'adverse events' (RR) not approved by you" in d["reason"]
               for d in store.deviations(ps, kind="analysis"))
    stt = artifacts(ps, "synthesize", 1)[0]
    assert stt["values"]["item_ids"] == [ids[t] for t in ("Aronson", "Ferguson", "Rosenthal")]
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_uncertain_support_card_answers_reach_the_worker(page, served):
    from rk.production.models import SynthesisPlan

    ps = served.ps
    _setup(served.env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [narrative()],
               **_judges(uncertain=(FINDING,))})
    run_id = _start(served, "synthesize", ex)
    cp, card = _show(page, ps, "uncertain_support")
    (cid,) = cp["options"]["claim_ids"]
    assert card.get_by_text(FINDING, exact=False).count() >= 1
    assert card.get_by_text("The design was rct.", exact=False).count() >= 1  # its evidence
    assert card.get_by_text("Does the main claim answer", exact=False).count() == 0  # synthesis asks no RQ check
    submit = card.get_by_role("button", name="Submit your review")
    assert submit.is_disabled()
    card.get_by_label(f"Supported: {FINDING}").check()
    card.get_by_label("Comment (optional)").fill("The trial enrolled older people.")
    submit.click()
    assert _answer_of(ps, cp["id"]) == {"claims": {cid: "supported"}, "comment": "The trial enrolled older people."}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    assert ArgumentService(ps).get_claim(cid)["status"] == "supported"
    assert synthesis_claims(ps, 1) == [cid]
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_final_review_card_answers_reach_the_worker(page, served):
    ps = served.ps
    _synthesize(served.env)
    sure = "Older people certainly were enrolled in a randomised trial."

    def reasons(inputs):
        return [{"ref": "r1", "claim": claim(inputs, sure, "rct"), "supports": "main", "warrant": WARRANT}]

    ex = argue_exec([draft(reasons=reasons), responses(), responses()],
                    [critique(objections=[_o("reason", 1, "Wrong population.")]), critique()], uncertain=("certainly",))
    run_id = _start(served, "argue", ex)
    cp, card = _show(page, ps, "uncertain_support")
    (reason,) = cp["options"]["claim_ids"]
    for text in (MAIN, sure, "Does the main claim answer the research question?"):
        assert card.get_by_text(text, exact=False).count() >= 1, text
    submit = card.get_by_role("button", name="Submit your review")
    card.get_by_label(f"Withdraw: {sure}").check()
    assert submit.is_disabled()  # the research question check is still open
    card.get_by_label("Yes, it answers the research question").check()
    submit.click()
    assert _answer_of(ps, cp["id"]) == {"claims": {reason: "withdraw"}, "answers_rq": "yes"}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    assert ArgumentService(ps).get_claim(reason)["status"] == "withdrawn"
    assert argue_notes(ps, 1, kind="rq_check")[-1]["by"] == "human"
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_final_review_card_shows_the_arguers_failed_self_check(page, served):
    """I5: the Arguer's failed self-check is an input of your final review: the card shows it (as text) next to the
    research question choice, not only in the checkpoint's full text."""
    ps = served.ps
    _synthesize(served.env)
    check = {"ok": False, "reason": "It says <b>who</b> was studied, not whether aspirin helps."}
    _start(served, "argue", argue_exec([draft(answers=check)], [critique()]))
    cp, card = _show(page, ps, "uncertain_support")
    assert cp["options"]["arguer_self_check"] == check
    shown = card.locator(".self-check")
    assert shown.count() == 1 and check["reason"] in shown.inner_text()
    assert shown.locator("b").count() == 0
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_final_review_card_back_to_synthesize_reaches_the_worker(page, served):
    """Fix wave 4: the Arguer's synthesis requests are shown on your final review (as text) with a "Back to
    synthesize" form; its answer takes argue->synthesize, and synthesize's next attempt gets the requests and your
    comment as guidance."""
    ps = served.ps
    _synthesize(served.env)
    need = "A finding on <b>bleeding</b> in people over 80."
    ex = argue_exec([draft(needs_synthesis=need)], [critique()])
    ex.script[("arguer", "AssessmentOut")] = [assess()]
    ex.script[("arguer", "NarrativeSynthesis")] = [_synth_narrative((F1, F2))]
    _start(served, "argue", ex)
    cp, card = _show(page, ps, "uncertain_support")
    shown = card.locator(".synthesis-requests")
    assert shown.count() == 1 and need in shown.inner_text() and "round 1" in shown.inner_text()
    assert shown.locator("b").count() == 0
    form = card.locator("form.back-synthesize")
    form.get_by_label("Why go back to synthesize (optional)").fill("Use the subgroup table.")
    form.get_by_role("button", name="Back to synthesize").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back", "to": "synthesize", "comment": "Use the subgroup table."}
    until(lambda: edge_rows(ps).get("argue->synthesize", {}).get("count") == 1)
    until(lambda: len(ex.of("arguer", "NarrativeSynthesis")) == 1)
    guidance = ex.of("arguer", "NarrativeSynthesis")[0]["inputs"]["guidance"]
    assert need in guidance and "Use the subgroup table." in guidance
    assert stage_row(ps, "synthesize")["attempt"] == 2
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_final_review_card_without_requests_offers_no_back(page, served):
    ps = served.ps
    _synthesize(served.env)
    _start(served, "argue", argue_exec([draft()], [critique()]))
    _, card = _show(page, ps, "uncertain_support")
    card.get_by_role("button", name="Submit your review").wait_for()
    assert card.locator(".synthesis-requests").count() == 0
    assert card.locator("form.back-synthesize").count() == 0
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
@pytest.mark.parametrize(("button", "decision"), [("Claim a holds", "a_holds"), ("Claim b holds", "b_holds"),
                                                  ("Both stand, contested", "both_contested")])
def test_conflict_card_answers_reach_the_worker(page, served, button, decision):
    ps = served.ps
    a, b = _synthesize(served.env)
    conflict = {"claim_a": a, "claim_b": b, "why": WHY}
    ex = argue_exec([draft(), responses(RESPONSE, RESPONSE)],
                    [critique(conflicts=[conflict]), critique(conflicts=[conflict])])
    run_id = _start(served, "argue", ex)
    cp, card = _show(page, ps, "evidence_conflict")
    for text in (F1, F2, "The design was rct.", "The design was cohort.", WHY, "Claim a", "Claim b"):
        assert card.get_by_text(text, exact=False).count() >= 1, text
    assert card.locator(".card-body b").count() == 0  # the Skeptic's text is shown as text
    card.get_by_role("button", name=button).click()
    assert _answer_of(ps, cp["id"]) == {"decision": decision}
    # the rounds go on to the final review: no claim to decide, only whether the main claim answers the RQ
    final, card = _show(page, ps, "uncertain_support")
    assert final["options"]["claim_ids"] == []
    card.get_by_label("Yes, it answers the research question").check()
    card.get_by_role("button", name="Submit your review").click()
    assert _answer_of(ps, final["id"]) == {"claims": {}, "answers_rq": "yes"}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    svc = ArgumentService(ps)
    if decision == "both_contested":
        assert all(svc.get_claim(c)["contested"] for c in (a, b))
    else:
        kept, gone = (a, b) if decision == "a_holds" else (b, a)
        assert svc.get_claim(gone)["status"] == "withdrawn" and svc.get_claim(kept)["status"] == "supported"
        page.goto_route("argument")
        page.locator(".claim-tree").wait_for()
        heading = page.get_by_role("heading", name="Withdrawn findings")
        assert heading.count() == 1
        assert page.locator(f'.argument [data-claim="{gone}"]').count() == 1
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_uncertain_support_rerun_form_reaches_the_worker(page, served):
    from rk.production.models import SynthesisPlan

    ps = served.ps
    _setup(served.env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = Exec({("arguer", "AssessmentOut"): [assess(), assess()],
               ("arguer", "NarrativeSynthesis"): [narrative(), narrative()], **_judges(uncertain=(FINDING,))})
    _start(served, "synthesize", ex)
    cp, card = _show(page, ps, "uncertain_support")
    card.get_by_label("What should change (synthesize runs again with your comment)").fill("Group by design.")
    card.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "rerun", "comment": "Group by design."}
    until(lambda: stage_row(ps, "synthesize")["attempt"] == 2)
    until(lambda: len(ex.of("arguer", "NarrativeSynthesis")) == 2)
    assert ex.of("arguer", "NarrativeSynthesis")[1]["inputs"]["guidance"].endswith("Group by design.")
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_review_card_offers_only_withdraw_past_a_hedge_problem(page, served):
    """I3: an overstated finding (V-HEDGE, supported by the judges) is listed with why; only Withdraw is offered, and
    withdrawing it passes the gate."""
    from rk.production.models import SynthesisPlan

    ps = served.ps
    _setup(served.env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    overstated = "Aspirin was proven safe in every older person."
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [_two_findings(overstated)],
               **_judges()})
    run_id = _start(served, "synthesize", ex)
    cp, card = _show(page, ps, "uncertain_support")
    (cid,) = cp["options"]["claim_ids"]
    row = card.locator(f'[data-claim-review="{cid}"]')
    for text in (overstated, "Why it is listed", "V-HEDGE overstated", "Only withdrawing it mends the gate"):
        assert row.get_by_text(text, exact=False).count() >= 1, text
    assert card.get_by_role("heading", name="Synthesis claims that fail the gate").count() == 1
    assert row.get_by_label(f"Supported: {overstated}").count() == 0
    submit = card.get_by_role("button", name="Submit your review")
    assert submit.is_disabled()
    row.get_by_label(f"Withdraw: {overstated}").check()
    submit.click()
    assert _answer_of(ps, cp["id"]) == {"claims": {cid: "withdraw"}}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    assert ArgumentService(ps).get_claim(cid)["status"] == "withdrawn"
    assert page.errors == []


@pytest.mark.browser
def test_pooling_card_back_names_the_study_to_read_again(page, served):
    """(a): the pooling card's "Back to read_extract" form names a study and says why; read_extract's next attempt is
    asked to read only that study again (the Khaled case: its n_control was a verified not_reported). The read itself
    is the real worker's (tests/test_synthesis.py); here read_extract is a fake."""
    ps = served.ps
    _enable_stats()
    studies = dict(list(BCG.items())[:2])
    studies["Ferguson"] = {**studies["Ferguson"], "n_control": "not_reported"}
    ids = _setup(served.env, studies, schema=BINARY, plan=META, text_only={"Ferguson": {"n_control": 30}})
    _start(served, "synthesize", synth_exec(plans=[_one_group, _one_group]))
    cp, card = _show(page, ps, "pooling_approval")
    form = card.locator("form.back-read")
    assert form.get_by_text("Aronson 1950").count() == 1 and form.get_by_text("Ferguson 1951").count() == 1
    form.get_by_label("Read again: Ferguson 1951").check()
    form.locator(".back-study", has_text="Ferguson 1951").get_by_text("Fields to look for (optional)").click()
    form.get_by_label("Look for n_control in Ferguson 1951").check()
    form.get_by_label("Why go back", exact=False).fill("Table 3 gives the arm sizes.")
    form.get_by_role("button", name="Back to read_extract").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back", "studies": [ids["Ferguson"]],
                                        "fields": {ids["Ferguson"]: ["n_control"]},
                                        "comment": "Table 3 gives the arm sizes."}
    until(lambda: stage_row(ps, "read_extract")["attempt"] == 2)
    assert reread_request(ps, 2) == {ids["Ferguson"]}
    assert "Table 3 gives the arm sizes." in attempt_guidance(ps, "read_extract", 2)
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_missing_narrative_card_offers_rerun_and_back(page, served):
    """Fix round 1 (I-2): no narrative synthesis in P2: the card has no review to submit, only rerun and back."""
    from rk.production.models import SynthesisPlan

    ps = served.ps
    _setup(served.env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = NoNarrative(synth_exec(narratives=[narrative(), narrative()]).script)
    _start(served, "synthesize", ex)
    cp, card = _show(page, ps, "uncertain_support")
    assert cp["options"]["narrative_missing"]
    assert card.get_by_text("gave no narrative synthesis", exact=False).count() >= 1
    assert card.get_by_role("button", name="Submit your review").count() == 0
    assert card.locator("form.back-read").count() == 1
    card.get_by_label("What should change (synthesize runs again with your comment)").fill("Group by design.")
    card.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "rerun", "comment": "Group by design."}
    until(lambda: stage_row(ps, "synthesize")["attempt"] == 2)
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_final_review_card_lists_a_finding_withdraw_only(page, served):
    """Minor 8: argue's review lists a finding left with an unanswered objection; the card offers only Withdraw."""
    ps = served.ps
    a, _ = _synthesize(served.env)

    def on_finding(inputs):
        return {"target_id": a, "category": "problem", "priority": 2, "text": "The trial enrolled people under 65."}

    ex = argue_exec([draft(), responses(), responses()], [critique(objections=[on_finding]), critique()])
    run_id = _start(served, "argue", ex)
    cp, card = _show(page, ps, "uncertain_support")
    assert cp["options"]["claim_ids"] == [a]
    row = card.locator(f'[data-claim-review="{a}"]')
    assert row.get_by_text(F1, exact=False).count() >= 1
    assert row.get_by_text("under 65", exact=False).count() >= 1
    assert row.get_by_label(f"Supported: {F1}").count() == 0
    row.get_by_label(f"Withdraw: {F1}").check()
    card.get_by_label("Yes, it answers the research question").check()
    card.get_by_role("button", name="Submit your review").click()
    assert _answer_of(ps, cp["id"]) == {"claims": {a: "withdraw"}, "answers_rq": "yes"}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_review_card_back_reaches_the_worker(page, served):
    from rk.production.models import SynthesisPlan

    ps = served.ps
    _setup(served.env, {"A": {"design": "rct"}, "B": {"design": "cohort"}}, schema=[DESIGN],
           plan=SynthesisPlan(method="thematic"))
    ex = Exec({("arguer", "AssessmentOut"): [assess()], ("arguer", "NarrativeSynthesis"): [narrative()],
               **_judges(uncertain=(FINDING,))})
    _start(served, "synthesize", ex)
    cp, card = _show(page, ps, "uncertain_support")
    card.locator("form.back-read").get_by_role("button", name="Back to read_extract").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back"}
    until(lambda: stage_row(ps, "read_extract")["attempt"] == 2)
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "research_proposal"}], indirect=True)
def test_design_card_back_reaches_the_worker(page, served):
    ps = served.ps
    _argued(served.env)
    _start(served, "design", design_exec([plan()]))
    cp, card = _show(page, ps, "design_approval")
    card.get_by_label("Why go back (optional)").fill("The argument needs a dose claim.")
    card.get_by_role("button", name="Back to argue").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back", "comment": "The argument needs a dose claim."}
    until(lambda: stage_row(ps, "argue")["attempt"] == 2)
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "research_proposal"}], indirect=True)
def test_design_card_answers_reach_the_worker(page, served):
    ps = served.ps
    _argued(served.env)
    run_id = _start(served, "design", design_exec([plan(), plan()], uncertain=(FEASIBLE,)))
    cp, card = _show(page, ps, "design_approval")
    assert cp["options"]["draft_id"] == "design:1"
    for text in ("D1", "D2", "stroke rate M1", "registry", NEW_H, "New hypothesis", "Falsified by", "M2",
                 FEASIBLE, "uncertain", "Fewer strokes on aspirin."):
        assert card.get_by_text(text, exact=False).count() >= 1, text
    assert card.get_by_role("button", name="Back to argue").count() == 1
    card.get_by_label("What should change in the design").fill("Add a dose arm.")
    card.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "revise", "comment": "Add a dose arm."}

    cp2, card = _show(page, ps, "design_approval", n=2)
    assert cp2["options"]["draft_id"] == "design:2"
    (fc,) = cp2["options"]["claim_ids"]
    approve = card.get_by_role("button", name="Approve this design")
    assert approve.is_disabled()  # the uncertain feasibility claim needs your decision
    card.get_by_label(f"Supported: {FEASIBLE}").check()
    approve.click()
    assert _answer_of(ps, cp2["id"]) == {"decision": "approve", "claims": {fc: "supported"}}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    (approved,) = design_notes(ps, 2, kind="approved")
    assert approved["by"] == "human" and fc in feasibility_claims(ps, 2)

    page.goto_route("argument")
    page.locator(".design-view").wait_for()
    assert page.get_by_text(approved["frozen_hash"][:12], exact=False).count() >= 1
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "research_proposal"}], indirect=True)
def test_design_card_untestable_reaches_the_worker(page, served):
    """I5 (design): the Ideator says the RQ is untestable (no draft); the card shows its reason and offers back to
    the question or a revision, not the approval nor back to argue."""
    ps = served.ps
    _argued(served.env)
    why = "No <b>method</b> can measure the outcome."
    _start(served, "design", design_exec([{"rq_untestable": why}]))
    cp, card = _show(page, ps, "design_approval")
    assert cp["options"]["untestable"] == why
    assert card.get_by_text(why, exact=False).count() >= 1 and card.locator(".card-body b").count() == 0
    assert card.get_by_role("button", name="Approve this design").count() == 0
    assert card.get_by_role("button", name="Back to argue").count() == 0
    card.get_by_label("Why go back to the question (optional)").fill("Measure stroke instead.")
    card.get_by_role("button", name="Back to the research question").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "question", "comment": "Measure stroke instead."}
    until(lambda: (edge_rows(ps).get("design->question") or {}).get("count") == 1)
    assert "Measure stroke instead." in stage_row(ps, "question")["guidance"]
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"production_mode": "auto"}], indirect=True)
def test_forest_plot_served_as_image_not_inline(page, served):
    _seed_pooled(served.env)
    (fig,) = [a for a in artifacts(served.ps, "synthesize", 1) if a["kind"] == "fig"]
    page.goto_route("progress")
    img = page.locator(f'.synthesis img[src="/ui/api/artifacts/demo/{fig["id"]}"]')
    img.wait_for()
    assert "tuberculosis" in img.get_attribute("alt")
    until(lambda: page.evaluate("(i) => i.complete && i.naturalWidth > 0", img.element_handle()))
    assert page.locator("#page svg").count() == 0  # never inline SVG
    r = page.request.get(f"{served.ui.origin}/ui/api/artifacts/demo/{fig['id']}")
    assert r.status == 200 and r.headers["content-type"] == "image/svg+xml"
    synthesis = page.locator(".synthesis")
    for text in ("Pooled RR", "V-STATS pass", "Aronson 1950", "Accurate"):
        assert synthesis.get_by_text(text, exact=False).count() >= 1, text
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_argument_view_shows_the_claim_tree(page, served):
    _seed_conflict(served.env)
    page.goto_route("argument")
    tree = page.locator(".claim-tree")
    tree.wait_for()
    for text in (MAIN, REASON, "When a study enrolled older people", "True", "Contested", RESPONSE, WHY):
        assert page.get_by_text(text, exact=False).count() >= 1, text
    assert tree.locator("b").count() == 0
    # the warrant claim sits under its reason edge, not among the claims
    warrant = page.locator(".warrant")
    assert warrant.count() == 1 and warrant.get_by_text("When a study enrolled older people", exact=False).count() == 1
    assert page.locator(".claim-tree > li").count() == 1  # one root: the main claim
    assert list_checkpoints(served.ps, "open") == []
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_argument_view_lists_each_conceded_objection_once(page, served):
    k, ps, lib = served.env
    _synthesize(served.env)
    ex = argue_exec([draft(), responses("conceded"), responses()],
                    [critique(objections=[_o("main", 2, "The cohort was small.")]), critique()])
    w = ArgueWorker()
    ctx = production_ctx(k, ps, lib, ex, stage="argue")
    out = w.run(ctx)
    answer_checkpoint(ps, out.checkpoint_id, {"claims": {}, "answers_rq": "yes"}, answered_by="human",
                      actor="researcher")
    out = w.on_answer(ctx, get_checkpoint(ps, out.checkpoint_id))
    assert out.status == "done", out.reason
    _ran(ps, "argue", out)
    page.goto_route("argument")
    page.locator(".claim-tree").wait_for()
    page.get_by_role("heading", name="Conceded (in the limitations)").wait_for()
    assert page.locator("h3 + ul > li", has_text="The cohort was small.").count() >= 1
    assert page.errors == []
