"""Plan 5c Task 7: the manuscript and audit read tools (``rk_manuscript_get``: every sentence with its stored verdict
and revise's questions with how each was handled; ``rk_audit_get``: the audit reports with their gates and the export),
the export tool (``rk_export``, the researcher's), and the Workbench: the sentence verdicts and the Revise panel on the
Drafts page, the Audit page with the export downloads (through the artifact route, export files only) and the
``final_review`` card, clicked in a real headless Chromium, its answers applied by the real AuditWorker through
``rk serve``'s RunManager."""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from rk.checkpoints import (
    answer_checkpoint,
    create_checkpoint,
    final_review_problem,
    get_checkpoint,
)
from rk.orchestrator.engine import Orchestrator
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    attempt_guidance,
    edge_rows,
    record_attempt,
    record_ran,
    run_row,
    stage_row,
)
from rk.production import export as export_mod
from rk.production.argue import main_claim
from rk.production.audit import GATES, AuditWorker, latest_audit_report
from rk.production.draft import DraftWorker, load_inputs, section_verdict
from rk.production.export import ExportWorker
from rk.production.manuscript import record_section_verdict, write_draft
from rk.production.revise import ReviseWorker
from rk.production.revise import _note as revise_note
from rk.production.tools import audit_view
from rk.roles.policy import TOOL_ROLES, lead_closed_after_discovery, role_tools
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.server.runtime import acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.ui.auth import COOKIE, UiAuth
from tests.discovery_fakes import FakeSources, FakeWorker
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_audit import _ctx as audit_ctx
from tests.test_audit import _judges, _passed, revised
from tests.test_draft import _ready, writes
from tests.test_export import _fake_render, audited
from tests.test_revise import _exec as revise_exec
from tests.test_revise import _restated, ask, revision
from tests.test_synthesis import Exec
from tests.test_ui_api import ORIGIN, SERVE_TOKEN, login
from tests.test_ui_argument import _pass_before
from tests.test_ui_production import _answer_of, _show, until

READ_TOOLS = ("rk_manuscript_get", "rk_audit_get")
FULL = {"lead", "gapminer", "ideator", "arguer", "skeptic", "simreader", "writer"}
XSS = '<img src=x onerror="window.__rkxss=1">'
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _cli(**kw) -> CallContext:
    return CallContext(role="cli", actor="tester", **kw)


@pytest.fixture
def api(tmp_home, replay_http, request):
    """The Workbench TestClient (logged in) over the frozen-RQ project ``demo`` (intake overrides in
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


def _get(api, path: str, **params) -> dict:
    r = api.client.get(path, params={"project": "demo", **params})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def revise_with_questions(env) -> None:
    """argue passed; every section drafted (draft passed); revise attempt 1 ran one round (``revise_max_rounds`` 1
    would stop it too): a priority-1 question on the introduction, revised and accepted; a priority-2 one on the
    discussion whose revision adds an unmarked sentence and is rejected."""
    k, ps, lib = env
    _ready(env)
    ex = Exec({("writer", "SectionDraft"): [writes()] * 10})
    out = DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft"), guidance=""))
    assert out.status == "done", out.reason
    _passed(ps, "draft")
    main = main_claim(ps, 1)
    ex = revise_exec([ask(("introduction", 1), ("discussion", 2), ("appendix", 2)), ask()],
                     [revision(add=_restated(ps, main)), revision(add="An unmarked sentence here.")])
    ex.script[("simreader", "ReaderQuestions")][0]["questions"][0]["text"] = "Why <b>this</b> age group?"
    record_attempt(ps, "revise", 1, run_id=None, guidance="", requested=None, actor="tester")
    out = ReviseWorker().run(replace(production_ctx(k, ps, lib, ex, stage="revise"), guidance=""))
    assert out.status == "done", out.reason
    record_ran(ps, "revise", 1, out.status, out.reason, actor="tester")


# ---- the read tools ---------------------------------------------------------------------------------------------
def test_read_tools_policy(api):
    """The manuscript and the audit are read by the roles that reason over the whole project and by you; the
    screeners, the Scout and the Reader (who work before them), the judges and triage read neither. Exporting is
    yours alone."""
    for name in READ_TOOLS:
        assert TOOL_ROLES[name] == {"cli", *FULL}
        tool = api.reg.get(name)
        assert not tool.writes and not lead_closed_after_discovery(name, tool.writes)
        assert tool.roles == TOOL_ROLES[name]
    for role in ("screener_a", "screener_b", "scout", "reader", "judge", "judge_2", "judge_3", "triage"):
        assert not set(READ_TOOLS) & set(role_tools(role, api.reg)), role
    for role in FULL:
        assert set(READ_TOOLS) <= set(role_tools(role, api.reg)), role
    assert TOOL_ROLES["rk_export"] == {"cli"} and api.reg.get("rk_export").writes
    for role in ("reader", "scout", "screener_a"):
        with pytest.raises(PermissionDenied):
            api.reg.call("rk_manuscript_get", {"project": "demo"}, CallContext(role=role, actor="t"))
    with pytest.raises(PermissionDenied):
        api.reg.call("rk_export", {"project": "demo"}, CallContext(role="lead", actor="t"))


def test_manuscript_tool_shows_each_sentence_with_its_stored_verdict(api):
    """Every required section's latest version, each sentence with the verdict recorded for it (never verified again
    in a read: a stored verdict is shown as stored), a version not yet verified as such, and a file changed on disk
    as an error."""
    ps = api.ps
    revise_with_questions(api.env)
    got = api.reg.call("rk_manuscript_get", {"project": "demo"}, _cli())
    assert [s["section"] for s in got["sections"]] == [x.id for x in load_inputs(ps).sections]
    intro = next(s for s in got["sections"] if s["section"] == "introduction")
    assert (intro["version"], intro["versions"], intro["stage"], intro["ok"]) == (2, [1, 2], "revise", True)
    assert intro["sentences"] and all(x["ok"] is True and x["problems"] == [] for x in intro["sentences"])
    assert any("[[clm:" in x["text"] for x in intro["sentences"])
    assert {"index", "text", "markers", "ok", "problems"} <= set(intro["sentences"][0])
    # a stored verdict is shown as stored: the unmarked first sentence is "ok" because the record says so
    v = write_draft(ps, "conclusion", "No marker here. A second one [[inf]].\n", stage="draft", attempt=1, round=0,
                    by_task=None, actor="t")
    record_section_verdict(ps, "conclusion", v, {
        "section": "conclusion", "version": v, "ck_share": 0.0, "ok": False, "problems": ["a stored section problem"],
        "sentences": [{"index": 0, "ok": True, "problems": []},
                      {"index": 1, "ok": False, "problems": ["a stored problem"]}]}, actor="t")
    (c,) = _get(api, "/ui/api/manuscript", section="conclusion")["sections"]
    assert (c["version"], c["ok"], c["problems"]) == (v, False, ["a stored section problem"])
    assert [(x["text"], x["ok"], x["problems"]) for x in c["sentences"]] == [
        ("No marker here.", True, []), ("A second one [[inf]].", False, ["a stored problem"])]
    # a version without a verdict yet
    write_draft(ps, "conclusion", "Not verified yet [[inf]].\n", stage="draft", attempt=1, round=0, by_task=None,
                actor="t")
    (c,) = _get(api, "/ui/api/manuscript", section="conclusion")["sections"]
    assert c["ok"] is None and [(x["ok"], x["problems"]) for x in c["sentences"]] == [(None, [])]
    (c,) = _get(api, "/ui/api/manuscript", section="conclusion", version=1)["sections"]
    assert c["version"] == 1 and c["ok"] is True
    # unknown sections and versions; a file changed on disk is shown with its error, not read as the draft
    assert api.client.get("/ui/api/manuscript", params={"project": "demo", "section": "nope"}).status_code == 404
    r = api.client.get("/ui/api/manuscript", params={"project": "demo", "section": "conclusion", "version": 9})
    assert r.status_code == 404
    (ps.root / "artifacts/drafts/conclusion.v1.md").write_text("Tampered [[inf]].\n")
    (c,) = _get(api, "/ui/api/manuscript", section="conclusion", version=1)["sections"]
    assert "sha256" in c["error"] and c["sentences"] == []


def test_revise_view_lists_questions_and_how_each_was_handled(api):
    ps = api.ps
    revise_with_questions(api.env)
    rv = api.reg.call("rk_manuscript_get", {"project": "demo"}, _cli())["revise"]
    assert rv["attempt"] == 1
    q1, q2 = rv["questions"]
    assert (q1["section"], q1["priority"], q1["round"], q1["text"]) == ("introduction", 1, 1, "Why <b>this</b> age group?")
    assert q1["sentence"] and (q1["outcome"], q1["note"]) == ("revised", "clarified")
    assert [(t["accepted"], t["how"]) for t in q1["revisions"]] == [(True, "revised")]
    assert (q2["section"], q2["priority"], q2["outcome"]) == ("discussion", 2, "rejected")
    (t,) = q2["revisions"]
    assert t["accepted"] is False and any("marker" in r for r in t["reasons"])
    assert _get(api, "/ui/api/manuscript")["revise"] == rv
    assert stage_row(ps, "revise")["attempt"] == 1
    (r,) = rv["refused"]  # a question on a section the Writer does not revise is refused, with why
    assert r["section"] == "appendix" and "no section the writer revises" in r["why"]
    assert rv["dropped"] == []


def test_audit_tool_shows_the_gates_with_their_failures_and_the_export(api):
    ps = api.ps
    got = api.reg.call("rk_audit_get", {"project": "demo"}, _cli())
    assert (got["report"], got["reports"]) == (None, [])
    assert "audit has not passed" in got["export"]["ready"] and got["export"]["files"] == []
    audited(api.env)
    got = _get(api, "/ui/api/audit")
    rep = got["report"]
    v = rep["version"]  # the report the approval was audited again in
    assert (rep["ok"], rep["approved"]) == (True, True) and got["reports"][-1]["version"] == v
    assert [g["gate"] for g in rep["gates"]] == list(GATES) and all(g["failures"] == [] for g in rep["gates"])
    assert got["export"]["ready"] is None and got["export"]["files"] == []
    # a section changed since: the export is refused as the worker refuses it; a new audit attempt fails G-COVER
    write_draft(ps, "conclusion", "Unmarked conclusion.\n", stage="revise", attempt=1, round=1, by_task=None,
                actor="t")
    assert "manuscript changed since audit" in _get(api, "/ui/api/audit")["export"]["ready"]
    out = AuditWorker().run(audit_ctx(api.env, _judges("approve"), attempt=2))
    assert (out.status, out.to) == ("back_edge", "draft"), out.reason
    got = _get(api, "/ui/api/audit")
    rep = got["report"]
    assert (rep["version"], rep["attempt"], rep["ok"]) == (v + 1, 2, False)
    cover = next(g for g in rep["gates"] if g["gate"] == "G-COVER")
    assert any("conclusion" in f and "no source marker" in f for f in cover["failures"]), cover
    assert rep["routes"]["draft"]
    assert _get(api, "/ui/api/audit", version=v)["report"]["ok"] is True
    assert api.client.get("/ui/api/audit", params={"project": "demo", "version": 7}).status_code == 404
    # a report picked by version shows the final review of that report's attempt, not of the attempt the audit shows
    # (attempt 1, which passed)
    one = create_checkpoint(ps, "final_review", "Review v1.", {"attempt": 1}, stage="audit", actor="k")
    two = create_checkpoint(ps, "final_review", "Review v2.", {"attempt": 2}, stage="audit", actor="k")
    assert [c["id"] for c in _get(api, "/ui/api/audit")["final_review"]] == [one]
    new = _get(api, "/ui/api/audit", version=v + 1)
    assert (new["attempt"], [c["id"] for c in new["final_review"]], new["approved"]) == (2, [two], None)
    old = _get(api, "/ui/api/audit", version=v)
    assert old["attempt"] == 1 and [c["id"] for c in old["final_review"]] == [one]
    assert old["approved"] is not None  # attempt 1's approval note


def test_export_downloads_go_through_the_artifact_route_export_files_only(api, tmp_path, monkeypatch):
    from rk.production import export as export_mod

    ps = api.ps
    out = ps.root / "artifacts/export"
    out.mkdir(parents=True, exist_ok=True)
    files = {"paper.pdf": (b"%PDF-1.5 fixture\n", "application/pdf"), "paper.docx": (b"PK\x03\x04 docx", DOCX),
             "paper.md": (b"# Paper\n\nText.\n", "text/markdown"),
             "package-0123abcd.zip": (b"PK\x03\x04 zip", "application/zip")}
    for name, (data, _) in files.items():
        (out / name).write_bytes(data)
    # M3 of the final review: files that are not the current export (here: audit has not passed) are not served
    assert api.client.get("/ui/api/artifacts/demo/export/paper.pdf").status_code == 404
    assert _get(api, "/ui/api/audit")["export"]["files"] == []
    monkeypatch.setattr(export_mod, "export_current", lambda ps: None)  # from here on: the file rules alone
    for name, (data, media) in files.items():
        r = api.client.get(f"/ui/api/artifacts/demo/export/{name}")
        assert r.status_code == 200, (name, r.text)
        assert r.content == data and r.headers["content-type"].split(";")[0] == media, name
        assert r.headers["x-content-type-options"] == "nosniff" and "attachment" in r.headers["content-disposition"]
    listed = _get(api, "/ui/api/audit")["export"]["files"]
    assert {f["name"]: f["media_type"] for f in listed} == {n: m for n, (_, m) in files.items()}
    # only the export's downloads: no other export file, nothing outside artifacts/export, no symlink
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF secret")
    (out / "paper.tex").write_text("\\documentclass{article}\n")
    (out / "refs.json").write_text("[]\n")
    (out / "package-deadbeef.zip").symlink_to(secret)
    (ps.root / "artifacts/audit").mkdir(parents=True, exist_ok=True)
    (ps.root / "artifacts/audit/report.v1.md").write_text("# Report\n")
    for name in ("paper.tex", "refs.json", "metadata.yaml", "package-deadbeef.zip", "package-xyz.zip", "missing.pdf",
                 "..", "%2e%2e", "..%2Faudit%2Freport.v1.md", "..%2F..%2F..%2Fconfig.toml", ".paper.pdf.tmp"):
        r = api.client.get(f"/ui/api/artifacts/demo/export/{name}")
        assert r.status_code == 404, (name, r.status_code)
    assert "package-deadbeef.zip" not in {f["name"] for f in _get(api, "/ui/api/audit")["export"]["files"]}
    # the export directory itself a symlink out of the project
    shutil.rmtree(out)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "paper.pdf").write_bytes(b"%PDF elsewhere")
    out.symlink_to(elsewhere)
    assert api.client.get("/ui/api/artifacts/demo/export/paper.pdf").status_code == 404
    assert _get(api, "/ui/api/audit")["export"]["files"] == []
    # an unknown project is not found; without the Workbench cookie nothing is served
    assert api.client.get("/ui/api/artifacts/nope/export/paper.pdf").status_code == 404
    api.client.cookies.clear()
    assert api.client.get("/ui/api/artifacts/demo/export/paper.pdf").status_code == 401


def test_rk_export_refusals(api, monkeypatch):
    """rk_export needs rk serve's RunManager (503 without it), is refused while another run of the project is active,
    and once the export passed says so instead of the RunManager's "already passed"."""
    ps = api.ps
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    audited(api.env)
    _pass_before(ps, "export")
    with pytest.raises(ToolError) as e:
        api.reg.call("rk_export", {"project": "demo"}, _cli(idempotency_key="export-1"))
    assert (e.value.type, e.value.status) == ("unavailable", 503)
    go = threading.Event()

    class Held(ExportWorker):
        def run(self, ctx):
            assert go.wait(20)
            return super().run(ctx)

    holder: dict = {}
    reg = build_registry(api.kernel, runs=lambda: holder.get("m"))
    fakes = {s: FakeWorker() for s in (*DISCOVERY, "protocol", "search_screen", "read_extract", "synthesize", "argue",
                                       "draft", "revise", "audit") if s != "intake"}
    orch = Orchestrator(api.kernel, reg, workers={**fakes, "export": Held()},
                        sources_factory=lambda p: FakeSources({}, lib=api.lib))
    m = holder["m"] = RunManager(api.kernel, reg, orch, poll_s=0.05)
    try:
        first = reg.call("rk_export", {"project": "demo"}, _cli(idempotency_key="export-2"))
        with pytest.raises(ToolError) as e:
            reg.call("rk_export", {"project": "demo"}, _cli(idempotency_key="export-3"))
        assert e.value.type == "run_active" and first["run_id"] in e.value.message, e.value.message
        go.set()
        until(lambda: run_row(ps, first["run_id"])["status"] == "done", timeout=30)
        with pytest.raises(ToolError) as e:
            reg.call("rk_export", {"project": "demo"}, _cli(idempotency_key="export-4"))
        assert (e.value.type, e.value.status) == ("already_exported", 409)
        assert e.value.message == "already exported; reopen export to export again"
    finally:
        go.set()
        m.close()


def test_a_final_review_answer_is_checked_where_it_is_given(api):
    """The Workbench and the CLI answer through rk_checkpoint_answer, which refuses a final_review answer the worker
    could not apply with ``final_review_problem``'s reason (the one check the worker makes too)."""
    ps = api.ps
    cid = create_checkpoint(ps, "final_review", "Review the manuscript.", {"attempt": 1}, stage="audit", actor="k")
    for bad in ({"decision": "back"}, {"decision": "revise"}, {"decision": "back", "to": "protocol"},
                {"decision": "ship"}, {"decision": "revise", "comment": 3}):
        r = api.client.post(f"/ui/api/checkpoints/{cid}/answer", json={"project": "demo", "answer": bad},
                            headers={"Origin": ORIGIN})
        assert r.status_code == 422, (bad, r.text)
        assert final_review_problem(bad) in r.json()["detail"]["message"], (bad, r.text)
        assert get_checkpoint(ps, cid)["status"] == "open"
    with pytest.raises(ToolError) as e:
        api.reg.call("rk_checkpoint_answer", {"project": "demo", "checkpoint_id": cid,
                                              "answer": {"decision": "back"}},
                     CallContext(role="cli", actor="tester", idempotency_key="final-review-back"))
    assert e.value.type == "invalid"
    r = api.client.post(f"/ui/api/checkpoints/{cid}/answer", json={"project": "demo", "answer": {
        "decision": "back", "to": "draft", "comment": "Tighten it."}}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    assert get_checkpoint(ps, cid)["answer"] == {"decision": "back", "to": "draft", "comment": "Tighten it."}


# ---- the Workbench, in a real browser -------------------------------------------------------------------------------
@pytest.fixture
def served(tmp_home, replay_http, monkeypatch, request):
    """``rk serve`` (uvicorn in a thread) with an Orchestrator and RunManager over the frozen-RQ systematic review
    ``demo`` in P2: fake discovery and earlier production workers, the real draft, revise, audit and export workers
    (pandoc stood in for by ``_fake_render``) over the executor the test sets (``served.use(ex)``)."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **{"paper_type": "systematic_review",
                                                                  "production_mode": "collab",
                                                                  **getattr(request, "param", {})})
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    rt = acquire_serve_lock(tmp_home)
    holder: dict = {"ex": Exec({})}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    fakes = {s: FakeWorker() for s in (*DISCOVERY, "protocol", "search_screen", "read_extract", "synthesize", "argue")
             if s != "intake"}
    orch = Orchestrator(k, reg, workers={**fakes, "draft": DraftWorker(), "revise": ReviseWorker(),
                                         "audit": AuditWorker(), "export": ExportWorker()},
                        executor_factory=lambda p, r, s: holder["ex"],
                        sources_factory=lambda p: FakeSources({}, lib=lib))
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


def _review(served, until_stage: str) -> str:
    """Every section drafted and revise passed; a run up to ``until_stage`` from the audit, whose P2 final review
    waits for you. The executor drafts and revises again after a revise or back answer."""
    revised(served.env)
    _pass_before(served.ps, "audit")
    served.use(Exec({("writer", "SectionDraft"): [writes()] * 30, ("simreader", "ReaderQuestions"): [{"questions": []}] * 5}))
    return served.runs.start("demo", until_stage, actor="tester")


@pytest.mark.browser
def test_final_review_card_approve_reaches_the_worker_and_the_export_downloads(page, served):
    ps = served.ps
    run_id = _review(served, "export")
    cp, card = _show(page, ps, "final_review")
    for text in ("Final review", "G-COVER", "introduction v1"):
        assert page.get_by_text(text, exact=False).count() >= 1, text
    card.get_by_role("button", name="Approve for export").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "approve"}
    until(lambda: run_row(ps, run_id)["status"] == "done", timeout=30)
    page.goto_route("audit")
    page.locator(".audit .gates").wait_for()
    assert page.get_by_text("passed every hard gate", exact=False).count() >= 1
    links = page.locator(".downloads a")
    names = sorted(links.all_text_contents())
    assert len(names) == 4 and names[0].startswith("package-") and names[1:] == ["paper.docx", "paper.md", "paper.pdf"]
    for link in links.all():
        href = link.get_attribute("href")
        assert href.startswith("/ui/api/artifacts/demo/export/"), href
        r = page.request.get(served.ui.origin + href)
        assert r.status == 200 and r.headers["content-type"].split(";")[0] in (
            "application/pdf", DOCX, "text/markdown", "application/zip"), (href, r.headers["content-type"])
    # the downloads are labelled with the export attempt and the audit report they were built from
    built = audit_view(ps)["export"]["built"]
    label = f"Export attempt 1, built from the audit report v{built['audit_report']}"
    assert page.locator(".export").get_by_text(label, exact=False).count() == 1, label
    page.set_viewport_size({"width": 760, "height": 1600})
    page.goto_route("audit")
    page.locator(".audit .gates").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    # once the manuscript changes, the files are no longer the manuscript's export: no links
    write_draft(ps, "conclusion", "Changed since [[inf]].\n", stage="revise", attempt=1, round=1, by_task=None,
                actor="t")
    page.goto("about:blank")
    page.goto_route("audit")
    page.get_by_text("Not exportable now", exact=False).wait_for()
    assert page.locator(".downloads a").count() == 0
    assert page.errors == []


@pytest.mark.browser
def test_final_review_card_on_a_failed_or_missing_report(page, served):
    """Approve is disabled on a report that failed; a report that cannot be found still leaves revise and back."""
    ps = served.ps
    revised(served.env)
    _pass_before(ps, "audit")
    write_draft(ps, "conclusion", "Unmarked conclusion.\n", stage="revise", attempt=1, round=1, by_task=None,
                actor="t")
    out = AuditWorker().run(audit_ctx(served.env, Exec({})))
    assert out.status == "back_edge", out.reason
    rep = latest_audit_report(ps)
    assert rep["ok"] is False
    opts = {"attempt": 1, "back_to": ["draft", "argue", "read_extract"]}
    create_checkpoint(ps, "final_review", "Review.", {**opts, "report_version": rep["version"]}, stage="audit",
                      actor="k")
    cp, card = _show(page, ps, "final_review")
    card.get_by_text("G-COVER", exact=False).first.wait_for()
    assert card.get_by_role("button", name="Approve for export").is_disabled()
    assert card.get_by_role("button", name="Ask for a revision").count() == 1
    answer_checkpoint(ps, cp["id"], {"decision": "revise", "comment": "x"}, answered_by="human", actor="tester")
    create_checkpoint(ps, "final_review", "Review.", {**opts, "report_version": 99}, stage="audit", actor="k")
    _, card = _show(page, ps, "final_review", n=2)
    assert card.get_by_text("The audit report v99 cannot be shown", exact=False).count() == 1
    assert card.get_by_role("button", name="Approve for export").is_disabled()
    assert card.get_by_role("button", name="Ask for a revision").count() == 1
    assert card.get_by_role("button", name="Send it back").count() == 1
    assert page.errors == []


@pytest.mark.browser
def test_final_review_card_revise_reaches_the_worker(page, served):
    ps = served.ps
    _review(served, "audit")
    cp, card = _show(page, ps, "final_review")
    card.get_by_label("What should change in the manuscript").fill("Say who the audience is.")
    card.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "revise", "comment": "Say who the audience is."}
    until(lambda: edge_rows(ps).get("audit->revise", {}).get("count") == 1)
    until(lambda: stage_row(ps, "revise")["attempt"] == 2)
    assert "Say who the audience is." in (attempt_guidance(ps, "revise", 2) or "")
    assert page.errors == []


@pytest.mark.browser
def test_final_review_card_back_reaches_the_worker(page, served):
    ps = served.ps
    _review(served, "audit")
    cp, card = _show(page, ps, "final_review")
    send = card.get_by_role("button", name="Send it back")
    assert send.is_disabled()  # gated: choose where it goes back to first
    card.get_by_label("Back to draft").check()
    assert send.is_enabled()
    card.get_by_label("Why send it back (optional)").fill("The discussion overreaches.")
    send.click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back", "to": "draft", "comment": "The discussion overreaches."}
    until(lambda: edge_rows(ps).get("audit->draft", {}).get("count") == 1)
    until(lambda: stage_row(ps, "draft")["attempt"] == 2)
    assert "The discussion overreaches." in (attempt_guidance(ps, "draft", 2) or "")
    assert page.errors == []


@pytest.mark.browser
def test_drafts_page_shows_sentence_verdicts_and_the_revise_panel_as_text(page, served):
    ps = served.ps
    revise_with_questions(served.env)
    text = f"Shown as text {XSS} here [[inf]]. Unmarked sentence here.\n"
    v = write_draft(ps, "discussion", text, stage="revise", attempt=1, round=1, by_task=None, actor="t")
    record_section_verdict(ps, "discussion", v, section_verdict(ps, "discussion", text,
                                                                paper_type="systematic_review", version=v), actor="t")
    page.goto_route("drafts?section=discussion")
    verdicts = page.locator(".verdicts")
    verdicts.wait_for()
    assert verdicts.get_by_text(XSS, exact=False).count() == 1
    assert verdicts.get_by_text("the sentence has no source marker", exact=False).count() == 1
    assert verdicts.locator(".badge.bad").count() >= 1
    assert page.locator("main img").count() == 0 and page.evaluate("window.__rkxss") is None
    panel = page.locator(".revise-panel")
    for t in ("Why <b>this</b> age group?", "clarified", "Rejected", "priority 1"):
        assert panel.get_by_text(t, exact=False).count() >= 1, t
    assert panel.locator("b").count() == 0
    # the refused questions (with why) and those not carried into the attempt (with the reason), as text
    refused = panel.locator(".refused li")
    assert refused.count() == 1 and "appendix" in refused.inner_text()
    assert "no section the writer revises" in refused.inner_text()
    assert panel.locator(".dropped").count() == 0
    revise_note(ps, 1, "carry_dropped", actor="t", questions=[{
        "id": "a0r1q2", "section": "methods", "sentence_index": 0, "sentence": "Gone.", "priority": 1,
        "text": "Which <i>databases</i>?", "reason": "its sentence is gone from methods v2"}])
    page.goto("about:blank")
    page.goto_route("drafts?section=discussion")
    dropped = page.locator(".revise-panel .dropped li")
    dropped.first.wait_for()
    assert "Which <i>databases</i>?" in dropped.inner_text() and "its sentence is gone" in dropped.inner_text()
    assert page.locator(".revise-panel i").count() == 0
    page.goto_route("drafts?section=introduction")
    page.locator(".verdicts").get_by_text("Verified", exact=False).first.wait_for()
    assert page.errors == []


@pytest.mark.browser
def test_argue_request_card_write_reaches_the_worker(page, served):
    """P2: a section that needs claims the argument lacks asks you first (argue_request); "write" has it written from
    the claims given, your comment going to its Writer, and drafting goes on in the same run."""
    ps = served.ps
    _ready(served.env)
    _pass_before(ps, "draft")
    need = ["a claim on the bleeding risk of aspirin in older people"]

    def writer(inputs):
        if inputs["section"]["id"] == "introduction" and "argue_declined" not in inputs:
            return {"markdown": "", "missing_support": need}
        return writes()(inputs)

    ex = Exec({("writer", "SectionDraft"): [writer] * 30})
    served.use(ex)
    run_id = served.runs.start("demo", "draft", actor="tester")
    cp, card = _show(page, ps, "argue_request")
    for text in ("A section needs claims the argument lacks", need[0], "Argue is asked once per section"):
        assert page.get_by_text(text, exact=False).count() >= 1, text
    assert card.get_by_role("button", name="Back to argue").count() == 1
    card.get_by_label("What the Writer should keep in mind (optional)").fill("State it as a limitation.")
    card.get_by_role("button", name="Write it, the gap as a limitation").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "write", "comment": "State it as a limitation."}
    until(lambda: run_row(ps, run_id)["status"] == "done", timeout=30)
    told = [c["inputs"]["argue_declined"] for c in ex.of("writer", "SectionDraft") if "argue_declined" in c["inputs"]]
    assert told[0] == [{"needs": need, "comment": "State it as a limitation."}]
    assert page.errors == []
