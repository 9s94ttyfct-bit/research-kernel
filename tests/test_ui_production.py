"""Plan 5a Task 8: the production read tools (protocol, PRISMA, screening, extraction), their CLI, and the Workbench's
protocol, screening and extraction views: the PRISMA flow on Progress, the ``protocol_approval`` and
``screening_disagreements`` cards (clicked in a real headless Chromium, their answers applied by the real workers
through ``rk serve``'s RunManager) and the extraction values in the library item view."""

from __future__ import annotations

import dataclasses
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from rk.checkpoints import get_checkpoint, list_checkpoints
from rk.library.models import Ids, ItemIn
from rk.orchestrator.engine import Orchestrator
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import (
    edge_rows,
    record_attempt,
    record_passed,
    record_ran,
    run_row,
    stage_row,
)
from rk.production import store
from rk.production.extraction import ReadExtractWorker, usable_evidence
from rk.production.screening import SearchScreenWorker
from rk.roles.policy import ROLES, TOOL_ROLES, lead_closed_after_discovery, role_tools
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.server.runtime import acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied
from rk.ui.auth import COOKIE, UiAuth
from rk.verify.prisma import (
    check_prisma,
    check_selection_reasons,
    flow,
    prisma_counts,
    prisma_notes,
)
from tests.discovery_fakes import FakeSources, FakeWorker
from tests.production_fakes import (
    Screeners,
    a_protocol,
    make_frozen_project,
    production_ctx,
)
from tests.test_protocol import SR_ITEMS
from tests.test_ui_api import ORIGIN, SERVE_TOKEN, login

W = {"Origin": ORIGIN}
TA, FT = "title_abstract", "full_text"
TIMEOUT = 10.0
READ_TOOLS = ("rk_protocol_get", "rk_prisma_get", "rk_screening_list", "rk_extraction_get")
METHODS = "A cohort of 100 people over 80 took aspirin daily."
PAPER = ("Aspirin in the oldest old\n\nAbstract\nWe followed people over 80 on aspirin.\n\n1. Introduction\n"
         f"INTRO-TEXT Strokes are common.\n\n2. Methods\n{METHODS}\n\n3. Results\n"
         "Fewer strokes on aspirin <i>(p &lt; 0.05)</i>.\n\n4. Discussion\nIt may help.")
ABSTRACT_B = "We followed 80 people over 80 in a cohort; fewer had a stroke."
A, B, C, D = "Aspirin A [yes/yes]", "Aspirin B [yes/no]", "Aspirin C [no/no]", "Aspirin D [yes/yes]"
E, F = "<b>Aspirin</b> E [no/yes]", "Aspirine F [yes/yes]"
DRAFT = {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}],
                         "exclude": [{"id": "E1", "text": "Animal studies <script>"}]},
         "extraction_schema": [{"name": "n", "description": "Participants", "type": "number", "unit": "people"},
                               {"name": "design", "description": "Study design", "type": "category",
                                "categories": ["rct", "cohort", "other"]}],
         "synthesis_plan": {"method": "narrative_swim"}, "prisma_p": dict(SR_ITEMS)}
STRATEGY = {"queries": ["aspirin stroke elderly", "antiplatelet therapy octogenarians", "stroke prevention over 80"],
            "sources": ["openalex", "europepmc"], "date_from": 2000, "date_to": None}
READS = {A: {"values": [{"field": "n", "value": 100, "quote": METHODS},
                        {"field": "design", "value": "cohort", "quote": "A cohort of 100 people over 80"}]},
         B: {"values": [{"field": "n", "value": 80, "quote": "We followed 80 people over 80"},
                        {"field": "design", "value": "cohort", "quote": "a cohort; nothing like this is said"}]}}
READS[D] = READS[A]  # a critical review includes D too (single screening)


def _works() -> list[ItemIn]:
    def work(title: str, i: int, *, abstract: str | None = None, language: str | None = None) -> ItemIn:
        return ItemIn(title=title, year=2010 + i, venue="Age Ageing", ids=Ids(doi=f"10.1000/p{i}"),
                      abstract=abstract or f"Abstract of {title}.", language=language)
    return [work(A, 1), work(B, 2, abstract=ABSTRACT_B), work(C, 3), work(D, 4), work(E, 5),
            work(F, 6, language="fr")]


class Production(Screeners):
    """One executor for the whole production run: the Ideator's and the Scout's drafts, the screeners and judges
    (``Screeners``) and the reader (``READS`` by title)."""

    def __init__(self, **kw):
        super().__init__(full_text={D: "yes/no"}, **kw)
        self.script[("ideator", "ProtocolDraftOut")] = [DRAFT] * 5
        self.script[("scout", "SearchStrategyOut")] = [STRATEGY] * 5

    def run(self, role, instructions, inputs, output_schema, **kw):
        if output_schema.__name__ == "ExtractOut":
            self.script[(role, "ExtractOut")] = [READS[inputs["study"]["title"]]]
        return super().run(role, instructions, inputs, output_schema, **kw)


def _sources(lib) -> FakeSources:
    return FakeSources({"aspirin": _works()}, lib=lib, fulltexts={A: [PAPER], D: [PAPER]})


def until(pred, timeout: float = TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def _cli(**kw) -> CallContext:
    return CallContext(role="cli", actor="tester", **kw)


# ---- seeding without a run: the workers called directly, the stage events written around them -------------------
def seed_evidence(k, ps, lib, *, votes: dict | None = None) -> None:
    """Freeze ``a_protocol`` and run ``search_screen`` (passed) and ``read_extract`` (ran) on ``_works`` in attempt 1.
    In P1 the disagreements (B, E at title/abstract, D at full text) go to the vote: ``votes`` by title."""
    pid = store.add_protocol(ps, 1, a_protocol(), version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    ex = Production(votes=votes or {B: ("include", "include"), E: ("exclude", "exclude"),
                                    (D, FT): ("include", "exclude")})
    src = _sources(lib)
    for stage, worker in (("search_screen", SearchScreenWorker()), ("read_extract", ReadExtractWorker())):
        record_attempt(ps, stage, 1, run_id=None, guidance="", requested=None, actor="tester")
        out = worker.run(production_ctx(k, ps, lib, ex, stage=stage, sources=src))
        assert out.status == "done", out.reason
        record_ran(ps, stage, 1, out.status, out.reason, actor="tester")
        if stage == "search_screen":
            record_passed(ps, stage, 1, actor="tester")


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
    yield SimpleNamespace(client=client, kernel=k, ps=ps, lib=lib, reg=reg)
    k.close()


def _get(api, path: str, **params) -> dict:
    r = api.client.get(path, params={"project": "demo", **params})
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ---- the read tools ---------------------------------------------------------------------------------------------
def test_read_tools_policy(api):
    full = {"lead", "gapminer", "ideator", "arguer", "skeptic", "simreader", "writer"}
    assert TOOL_ROLES["rk_protocol_get"] == {"cli", *full, "scout", "screener_a", "screener_b", "reader"}
    assert TOOL_ROLES["rk_prisma_get"] == {"cli", *full, "scout"}
    assert TOOL_ROLES["rk_screening_list"] == {"cli", *full}  # no screener sees the other's decisions
    assert TOOL_ROLES["rk_extraction_get"] == {"cli", *full, "reader"}
    for role in ("screener_a", "screener_b"):
        assert {"rk_protocol_get"} == set(READ_TOOLS) & set(role_tools(role, api.reg))
    assert {"rk_protocol_get", "rk_extraction_get"} == set(READ_TOOLS) & set(role_tools("reader", api.reg))
    assert {"rk_protocol_get", "rk_prisma_get"} == set(READ_TOOLS) & set(role_tools("scout", api.reg))
    for role in ("judge", "judge_2", "triage"):
        assert not set(READ_TOOLS) & set(role_tools(role, api.reg))
    for name in READ_TOOLS:
        tool = api.reg.get(name)
        assert not tool.writes and not lead_closed_after_discovery(name, tool.writes)  # the Lead reads after question
        assert tool.roles == TOOL_ROLES[name]
    with pytest.raises(PermissionDenied):
        api.reg.call("rk_screening_list", {"project": "demo"}, CallContext(role="screener_a", actor="t"))
    assert set(ROLES) - {"judge", "judge_2", "triage"} >= {r for n in READ_TOOLS for r in TOOL_ROLES[n]} - {"cli"}


def test_run_descriptions_name_the_production_stages(api):
    summary = api.reg.get("rk_run_start").summary
    assert all(s in summary for s in ("topics", "question", "protocol", "search_screen", "read_extract"))
    assert "read_extract" in api.reg.get("rk_run_status").summary


def test_read_tools_before_anything_ran(api):
    got = api.reg.call("rk_protocol_get", {"project": "demo"}, _cli())
    assert (got["protocol"], got["versions"], got["deviations"]) == (None, [], [])
    got = api.reg.call("rk_prisma_get", {"project": "demo"}, _cli())
    assert (got["attempt"], got["prisma"], got["notes"], got["selection"]) == (None, None, [], [])
    assert api.reg.call("rk_screening_list", {"project": "demo"}, _cli())["rows"] == []
    got = api.reg.call("rk_extraction_get", {"project": "demo"}, _cli())
    assert (got["attempt"], got["studies"]) == (None, [])


def test_prisma_route_matches_v_prisma(api):
    seed_evidence(api.kernel, api.ps, api.lib)
    p = prisma_counts(api.ps, 1)
    got = _get(api, "/ui/api/prisma")
    assert (got["attempt"], got["paper_type"], got["reports_prisma"]) == (1, "systematic_review", True)
    assert got["prisma"] == dataclasses.asdict(p)
    assert got["check"] == check_prisma(p) == []
    assert (got["flow"], got["notes"]) == (flow(p), prisma_notes(p))
    assert (p.identified, p.duplicates_removed, p.screened, p.excluded_ta) == (12, 6, 6, 3)
    assert (p.sought, p.not_retrieved, p.assessed_ft, p.included) == (3, 0, 3, 2)
    assert p.excluded_ft == {"screeners disagreed: excluded by the P1 vote (not reviewed by a human)": 1}
    assert got["notes"] == ["2 excluded by the P1 vote on a screening disagreement (not reviewed by a human)",
                            "1 excluded for language", "1 assessed on the abstract only"]
    assert flow(p).endswith("; notes: " + "; ".join(got["notes"]))
    assert got["selection"] == []  # PRISMA types report the counts, not the selection reasons
    assert _get(api, "/ui/api/prisma", attempt=2)["prisma"] is None


@pytest.mark.parametrize("api", [{"paper_type": "critical_review"}], indirect=True)
def test_prisma_route_gives_selection_reasons_without_prisma_counts(api):
    seed_evidence(api.kernel, api.ps, api.lib)
    got = _get(api, "/ui/api/prisma")
    assert (got["reports_prisma"], got["prisma"], got["check"]) == (False, None, [])
    assert got["selection_check"] == check_selection_reasons(api.ps, 1) == []
    assert got["notes"] == ["1 excluded for language", "1 assessed on the abstract only"]
    rows = {(r["title"], r["phase"]): r for r in got["selection"]}
    assert (rows[(C, TA)]["include"], rows[(C, TA)]["decided_by"]) == ("no", "single")
    assert [s["reason"] for s in rows[(C, TA)]["screeners"]] == ["screener_a read it"]
    assert (rows[(F, TA)]["include"], rows[(F, TA)]["reason"]) == ("no", "language")
    assert {r["phase"] for r in got["selection"]} == {TA, FT}


def test_screening_list_tool(api):
    seed_evidence(api.kernel, api.ps, api.lib)
    got = api.reg.call("rk_screening_list", {"project": "demo", "phase": TA}, _cli())
    assert got["attempt"] == 1 and not got["final"]
    said = {(r["title"], r["screener"]): r["include"] for r in got["rows"]}
    assert said[(B, "screener_a")] == "yes" and said[(B, "screener_b")] == "no"
    assert {r["phase"] for r in got["rows"]} == {TA}
    finals = api.reg.call("rk_screening_list", {"project": "demo", "phase": FT, "final": True}, _cli())["rows"]
    assert {r["title"]: (r["include"], r["decided_by"]) for r in finals} == {
        A: ("yes", "agreement"), B: ("yes", "agreement"), D: ("no", "vote")}


def test_extraction_tool_shows_usable_evidence_apart(api):
    seed_evidence(api.kernel, api.ps, api.lib)
    a_id, b_id = (next(i.id for i in api.lib.list() if i.title == t) for t in (A, B))
    got = api.reg.call("rk_extraction_get", {"project": "demo"}, _cli())
    assert got["attempt"] == 1 and [f["name"] for f in got["fields"]] == ["n", "design"]
    by = {s["item_id"]: s for s in got["studies"]}
    assert set(by) == {a_id, b_id} == {e["item_id"] for e in usable_evidence(api.ps, 1)}
    a, b = by[a_id], by[b_id]
    assert (a["title"], a["read_on"], a["sufficient"], a["in_evidence"]) == (A, "full_text", "full", True)
    assert [(v["field"], v["value"], v["sufficient"]) for v in a["values"]] == [("n", 100, "full"),
                                                                                ("design", "cohort", "full")]
    assert all(v["note"]["kind"] == "quote" and v["note"]["against"] == "text" for v in a["values"])
    assert a["values"][0]["note"]["text"] == METHODS and a["set_aside"] == []
    # read on the abstract: low; the value whose quote is not verbatim is set aside with its problem, never evidence
    assert (b["read_on"], b["sufficient"]) == ("abstract", "low")
    assert [(v["field"], v["value"], v["sufficient"], v["note"]["against"]) for v in b["values"]] == [
        ("n", 80, "low", "abstract")]
    assert [(r["field"], r["value"], r["problem"]) for r in b["set_aside"]] == [("design", "cohort",
                                                                                  "quote not verbatim")]
    only = api.reg.call("rk_extraction_get", {"project": "demo", "item_id": b_id}, _cli())
    assert [s["item_id"] for s in only["studies"]] == [b_id]
    assert got["limitations"]["low_sufficiency"] == [b_id]
    # the library item route of the Workbench shows the same through the tool
    assert _get(api, "/ui/api/extraction", item_id=b_id)["studies"] == only["studies"]


def test_extraction_tool_keeps_degraded_and_excluded_rows_out_of_the_evidence(api):
    seed_evidence(api.kernel, api.ps, api.lib)
    a_id, b_id = (next(i.id for i in api.lib.list() if i.title == t) for t in (A, B))
    ctx = production_ctx(api.kernel, api.ps, api.lib, Production(), stage="read_extract")
    ReadExtractWorker().degrade(ctx, ["edge limit"])  # B's design is marked missing; its old row degraded
    rows = {s["item_id"]: s for s in api.reg.call("rk_extraction_get", {"project": "demo"}, _cli())["studies"]}
    assert [v["field"] for v in rows[b_id]["values"]] == ["n"]
    aside = [(r["field"], r["problem"].split(":")[0]) for r in rows[b_id]["set_aside"]]
    assert aside == [("design", "quote not verbatim; degraded"), ("design", "missing")]
    # a study the degrade takes out of the evidence: every row of it is set aside, none is a value
    store.add_extraction_note(api.ps, 1, "degraded", actor="tester", missing=[],
                              excluded=[{"item_id": a_id, "reason": "retraction not checked"}], dropped_notes=[])
    rows = {s["item_id"]: s for s in api.reg.call("rk_extraction_get", {"project": "demo"}, _cli())["studies"]}
    assert (rows[a_id]["in_evidence"], rows[a_id]["out_reason"], rows[a_id]["values"]) == (
        False, "taken out of the evidence: retraction not checked", [])
    assert [r["field"] for r in rows[a_id]["set_aside"]] == ["n", "design"]


def test_protocol_tool_and_route(api):
    pid = store.add_protocol(api.ps, 1, a_protocol(), version=1, actor="tester")
    got = api.reg.call("rk_protocol_get", {"project": "demo"}, _cli())
    assert (got["protocol"]["id"], got["protocol"]["status"], got["in_force"]) == (pid, "draft", None)
    assert got["preregistration"] is True and got["paper_type"] == "systematic_review"
    assert "Record-cap probe: not run." in got["probe_lines"] and "Research question:" in got["text"]
    store.freeze_protocol(api.ps, pid, actor="tester")
    p2 = store.add_protocol(api.ps, 2, a_protocol(record_cap=5000), version=2, actor="tester")
    got = api.reg.call("rk_protocol_get", {"project": "demo"}, _cli())
    assert (got["protocol"]["id"], got["in_force"]) == (pid, pid)  # the version in force, not the newer draft
    assert [(v["version"], v["status"]) for v in got["versions"]] == [(1, "frozen"), (2, "draft")]
    shown = _get(api, "/ui/api/protocol", protocol_id=p2)
    assert (shown["protocol"]["id"], shown["protocol"]["body"]["record_cap"], shown["in_force"]) == (p2, 5000, pid)
    assert api.client.get("/ui/api/protocol", params={"project": "demo", "protocol_id": "prt_nope"}).status_code == 404


def test_cli_production_commands(serve_rk):
    from typer.testing import CliRunner

    from rk.cli.main import app

    client = serve_rk()
    client.call("rk_project_create", {"slug": "demo", "title": "Demo", "paper_type": "systematic_review"}, write=True)
    ps, lib = client.kernel.project("demo")
    from rk.discovery.models import Intake
    from rk.discovery.store import record_intake
    from tests.discovery_fakes import DEFAULT_INTAKE, freeze_an_rq

    record_intake(ps, Intake(**{**DEFAULT_INTAKE, "paper_type": "systematic_review"}), actor="t", actor_kind="human")
    freeze_an_rq(ps)
    seed_evidence(client.kernel, ps, lib)
    run = CliRunner()
    r = run.invoke(app, ["protocol", "show", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert "Protocol version 1" in r.output and "frozen" in r.output and "I1: People over 80" in r.output
    r = run.invoke(app, ["prisma", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert "identified 12" in r.output and "V-PRISMA: the counts add up" in r.output
    assert "1 excluded for language" in r.output
    r = run.invoke(app, ["screening", "list", "--project", "demo", "--phase", "full_text", "--final"])
    assert r.exit_code == 0, r.output
    assert A in r.output and "vote" in r.output
    r = run.invoke(app, ["extraction", "show", "--project", "demo"])
    assert r.exit_code == 0, r.output
    assert "n = 100" in r.output and "low" in r.output and "quote not verbatim" in r.output
    r = run.invoke(app, ["extraction", "show", "--project", "demo", "--json"])
    assert r.exit_code == 0 and '"studies"' in r.output


# ---- the cards, clicked in a real browser, applied by the workers through a run ----------------------------------
@pytest.fixture
def served(tmp_home, replay_http, request):
    """``rk serve`` (uvicorn in a thread) with an Orchestrator and RunManager over the frozen-RQ systematic review
    ``demo`` in P2: fake discovery workers, the real production workers over ``Production`` and ``_sources``."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **{"paper_type": "systematic_review",
                                                                  "production_mode": "collab",
                                                                  **getattr(request, "param", {})})
    rt = acquire_serve_lock(tmp_home)
    holder: dict = {}
    reg = build_registry(k, runs=lambda: holder.get("m"))
    ex, src = Production(), _sources(lib)
    orch = Orchestrator(k, reg, workers={s: FakeWorker() for s in DISCOVERY if s != "intake"},
                        executor_factory=lambda p, r, s: ex, sources_factory=lambda p: src)
    runs = holder["m"] = RunManager(k, reg, orch, poll_s=0.05)
    ui = UiAuth(f"http://127.0.0.1:{rt.port}")
    app = create_app(reg, rt.token, tokens=k.tokens, chats=ChatManager(k, reg), ui=ui, kernel=k, runs=runs)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
    t.start()
    time.sleep(0.5)
    yield SimpleNamespace(kernel=k, ps=ps, lib=lib, ui=ui, runs=runs, ex=ex)
    server.should_exit = True
    t.join(5)
    runs.close()
    k.close()
    release(rt)


@pytest.fixture
def page(served):
    """A headless Chromium tab signed in to the Workbench (by cookie: the Chat page, which opens a model CLI chat,
    is never loaded)."""
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


def _open(ps, kind: str) -> list[dict]:
    return [c for c in list_checkpoints(ps, "open") if c["kind"] == kind]


def _show(page, ps, kind: str, n: int = 1):
    """Open the Checkpoints page once ``n`` checkpoints of ``kind`` were opened; returns the open one's card."""
    until(lambda: len([c for c in list_checkpoints(ps) if c["kind"] == kind]) >= n and _open(ps, kind))
    (cp,) = _open(ps, kind)
    page.goto_route(f"checkpoints?{cp['id']}")
    card = page.locator(f'.checkpoints li[data-kind="{kind}"]:not(.answered)')
    card.locator(".card-body").wait_for()
    return cp, card


def _answer_of(ps, cid: str) -> dict:
    cp = until(lambda: (c := get_checkpoint(ps, cid))["answer"] and c)
    assert cp["answered_by"] == "human"
    return cp["answer"]


@pytest.mark.browser
def test_protocol_card_answers_reach_the_worker(page, served):
    ps = served.ps
    run_id = served.runs.start("demo", "protocol", actor="tester")
    cp, card = _show(page, ps, "protocol_approval")
    for text in ("aspirin stroke elderly", "People over 80", "Animal studies <script>", "Participants",
                 "narrative swim", "Two screeners, title/abstract then full text.", "Record-cap probe"):
        assert card.get_by_text(text, exact=False).count() >= 1, text
    assert card.locator("script").count() == 0  # the protocol's text is shown as text
    card.get_by_label("What should change in the protocol").fill("Add PubMed and narrow to trials.")
    card.get_by_role("button", name="Ask for a revision").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "revise", "comment": "Add PubMed and narrow to trials."}
    until(lambda: stage_row(ps, "protocol")["attempt"] == 2)
    assert len(served.ex.of("ideator", None)) == 2  # the rerun drafted again

    cp2, card = _show(page, ps, "protocol_approval", n=2)
    assert cp2["options"]["version"] == 2
    card.get_by_label("Record cap (optional)").fill("5000")
    card.get_by_role("button", name="Approve this protocol").click()
    assert _answer_of(ps, cp2["id"]) == {"decision": "approve", "record_cap": 5000}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    frozen = store.frozen_protocol(ps)
    assert (frozen["body"]["record_cap"], frozen["status"]) == (5000, "frozen")
    assert ps.db.execute("SELECT actor_kind FROM events WHERE type='protocol.frozen'").fetchone()[0] == "human"
    assert page.errors == []


@pytest.mark.browser
def test_protocol_card_back_goes_to_question(page, served):
    ps = served.ps
    served.runs.start("demo", "protocol", actor="tester")
    cp, card = _show(page, ps, "protocol_approval")
    card.get_by_label("Why go back (optional)").fill("The question is too broad.")
    card.get_by_role("button", name="Back to the research question").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "back", "comment": "The question is too broad."}
    until(lambda: edge_rows(ps).get("protocol->question", {}).get("count") == 1)
    assert page.errors == []


@pytest.mark.browser
def test_disagreement_card_requires_every_row(page, served):
    ps, lib = served.ps, served.lib
    run_id = served.runs.start("demo", "search_screen", actor="tester")
    cp, card = _show(page, ps, "protocol_approval")
    card.get_by_role("button", name="Approve this protocol").click()
    assert _answer_of(ps, cp["id"]) == {"decision": "approve"}

    cp, card = _show(page, ps, "screening_disagreements")
    assert cp["options"]["phase"] == TA
    rows = card.locator("tbody tr")
    assert rows.count() == 2
    assert card.locator("tbody .record-title").all_text_contents() == [B, E]  # shown as text, not markup
    assert card.locator("b").count() == 0
    body = card.locator(".card-body")  # the prompt's full text, folded away, says the same once more
    assert body.get_by_text("screener_a read it").count() == 2 and body.get_by_text(ABSTRACT_B).count() == 1
    submit = card.get_by_role("button", name="Submit decisions")
    assert submit.is_disabled()
    card.get_by_role("radio", name=f"Include: {B}").check()
    card.get_by_text("1 of 2 records decided").wait_for()
    assert submit.is_disabled()
    card.get_by_role("radio", name=f"Exclude: {E}").check()
    card.get_by_text("2 of 2 records decided").wait_for()
    assert submit.is_enabled()
    submit.click()
    ids = {lib.get(store.record_row(ps, r)["item_id"]).title: r for r in cp["options"]["record_ids"]}
    assert _answer_of(ps, cp["id"]) == {"decisions": {ids[B]: "include", ids[E]: "exclude"}}

    # the full-text phase: the excerpt the backend gives the screeners, as text
    cp, card = _show(page, ps, "screening_disagreements", n=2)
    assert cp["options"]["phase"] == FT
    assert card.locator("tbody .record-title").all_text_contents() == [D]
    excerpt = card.locator("tbody .excerpt").text_content()
    assert "Fewer strokes on aspirin <i>(p &lt; 0.05)</i>." in excerpt and "INTRO-TEXT" not in excerpt
    assert card.locator("tbody i").count() == 0
    card.locator(".card-body").get_by_text("Full text (abstract, methods, results)", exact=True).wait_for()
    assert card.get_by_role("button", name="Submit decisions").is_disabled()
    card.get_by_role("radio", name=f"Exclude: {D}").check()
    card.get_by_role("button", name="Submit decisions").click()
    (rid,) = cp["options"]["record_ids"]
    assert _answer_of(ps, cp["id"]) == {"decisions": {rid: "exclude"}}
    until(lambda: run_row(ps, run_id)["status"] == "done")
    finals = {lib.get(store.record_row(ps, f["record_id"])["item_id"]).title: (f["include"], f["decided_by"])
              for f in store.finals(ps, stage_row(ps, "search_screen")["passed_attempt"], phase=FT)}
    assert finals == {A: ("yes", "agreement"), B: ("yes", "agreement"), D: ("no", "human")}
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"production_mode": "auto"}], indirect=True)
def test_progress_shows_the_prisma_flow(page, served):
    seed_evidence(served.kernel, served.ps, served.lib)
    page.goto_route("progress")
    panel = page.locator(".prisma")
    panel.get_by_text("Identified").wait_for()
    flow_text = panel.text_content()
    for text in ("Identified", "12", "Duplicates removed", "Excluded at full text", "Included",
                 "screeners disagreed: excluded by the P1 vote (not reviewed by a human)",
                 "1 excluded for language", "1 assessed on the abstract only", "The counts add up (V-PRISMA)."):
        assert text in flow_text, text
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"paper_type": "critical_review"}], indirect=True)
def test_progress_shows_selection_reasons_for_a_critical_review(page, served):
    seed_evidence(served.kernel, served.ps, served.lib)
    page.goto_route("progress")
    panel = page.locator(".prisma")
    panel.get_by_text("selection reasons").first.wait_for()
    assert panel.get_by_text("Identified").count() == 0  # no PRISMA arithmetic for this paper type
    assert panel.get_by_text("screener_a read it").count() >= 1 and panel.get_by_text("language").count() >= 1
    assert page.errors == []


@pytest.mark.browser
@pytest.mark.parametrize("served", [{"production_mode": "auto"}], indirect=True)
def test_library_item_shows_extraction_values_with_note_links(page, served):
    ps, lib = served.ps, served.lib
    seed_evidence(served.kernel, ps, lib)
    b_id = next(i.id for i in lib.list() if i.title == B)
    (row,) = [e for e in usable_evidence(ps, 1) if e["item_id"] == b_id]
    note_id = next(v["note_id"] for v in row["values"] if v["field"] == "n")
    page.goto_route(f"library?item={b_id}")
    box = page.locator(".detail")
    table = box.locator("table.extraction")
    table.wait_for()
    assert table.locator("tbody tr").count() == 1  # the value whose quote is not verbatim is no evidence
    assert table.locator("tbody tr td").all_text_contents()[:3] == ["n", "80", "low"]
    aside = box.locator(".set-aside")
    assert "quote not verbatim" in aside.text_content() and "design" in aside.text_content()
    link = table.get_by_role("link", name="Quote note (abstract)")
    assert link.get_attribute("href").endswith(f"note={note_id}")
    link.click()
    page.wait_for_function("id => document.activeElement?.dataset.noteId === id", arg=note_id)
    assert "abstract" in box.locator(f'blockquote[data-note-id="{note_id}"]').text_content()
    assert page.errors == []
