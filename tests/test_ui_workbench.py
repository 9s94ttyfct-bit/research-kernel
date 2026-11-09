# ruff: noqa: F811 - pytest fixtures imported from test_ui_api / test_sessions are re-bound as test parameters
import json
import threading
import time

import pytest

from rk.checkpoints import create_checkpoint, get_checkpoint
from rk.config import load_settings, save_settings
from rk.fulltext import attach_html_snapshot, attach_pdf_bytes
from rk.library.models import Creator, Ids, ItemIn
from rk.notes import NoteIn, add_note

from .browser_sites import tiny_pdf
from .test_sessions import fake  # noqa: F401 - fixture
from .test_ui_api import ORIGIN, kernel, login, ui  # noqa: F401 - fixtures

W = {"Origin": ORIGIN}


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers=W)


@pytest.fixture
def project(ui, kernel):
    login(ui)
    assert post(ui, "/ui/api/projects", {"slug": "demo", "title": "Demo", "paper_type": "critical_review"}).json()["ok"]
    return kernel.project("demo")


def add_item_with_pdf(kernel, text="Encapsulation slowed degradation of the fixture cells."):
    ps, lib = kernel.project("demo")
    iid = lib.add_item(ItemIn(title="Fixture article", year=2024, creators=[Creator(family="Author")],
                              ids=Ids(doi="10.5555/ok")), added_by="human", actor="t").item_id
    attach_pdf_bytes(ps, lib, iid, tiny_pdf(text), origin="user_upload", actor="t", actor_kind="human")
    return iid


def test_projects_crud(ui, project, kernel):
    names = [p["slug"] for p in ui.get("/ui/api/projects").json()["result"]["projects"]]
    assert names == ["demo"]
    assert post(ui, "/ui/api/projects/current", {"slug": "demo"}).json()["ok"]
    assert load_settings().current_project == "demo"
    assert post(ui, "/ui/api/projects/current", {"slug": "nope"}).status_code == 404


def test_library_item_badges(ui, project, kernel):
    iid = add_item_with_pdf(kernel)
    ps, lib = kernel.project("demo")
    add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="Encapsulation slowed degradation",
                             role_in_source="main"), actor="t")
    items = ui.get("/ui/api/library", params={"project": "demo"}).json()["result"]["items"]
    assert [(i["id"], i["badges"]["fulltext"], i["badges"]["quotes_verified"]) for i in items] == [(iid, True, 1)]
    item = ui.get(f"/ui/api/library/item/{iid}", params={"project": "demo"}).json()["result"]
    assert item["badges"]["ref_status"] == "unverified" and item["attachments"][0]["kind"] == "pdf"
    hits = ui.get("/ui/api/library", params={"project": "demo", "q": "degradation"}).json()["result"]["items"]
    assert [h["id"] for h in hits] == [iid]


def test_file_route_serves_pdf_and_sandboxes_html(ui, project, kernel):
    iid = add_item_with_pdf(kernel)
    ps, lib = kernel.project("demo")
    att = ui.get(f"/ui/api/library/item/{iid}", params={"project": "demo"}).json()["result"]["attachments"][0]
    r = ui.get(f"/ui/api/files/demo/{att['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"
    html_att, _ = attach_html_snapshot(ps, lib, iid, "<script>fetch('/ui/api/session')</script><p>page</p>",
                                       origin="publisher_browser", actor="t", actor_kind="human", url="https://x.example")
    r = ui.get(f"/ui/api/files/demo/{html_att}")
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    post(ui, "/ui/api/projects", {"slug": "other", "title": "Other"})
    assert ui.get(f"/ui/api/files/other/{att['id']}").status_code == 404


def test_checkpoint_answer_is_human(ui, project):
    ps, _ = project
    chk = create_checkpoint(ps, "captcha_wait", "Solve it", ["done"], actor="kernel:browser")
    listed = ui.get("/ui/api/checkpoints", params={"project": "demo", "status": "open"}).json()["result"]
    assert [c["id"] for c in listed["checkpoints"]] == [chk]
    assert post(ui, f"/ui/api/checkpoints/{chk}/answer", {"project": "demo", "answer": {"choice": "done"}}).json()["ok"]
    cp = get_checkpoint(ps, chk)
    assert (cp["status"], cp["answered_by"], cp["answer"]) == ("answered", "human", {"choice": "done"})


def test_progress_lists_events_and_tasks(ui, project, kernel):
    add_item_with_pdf(kernel)
    p = ui.get("/ui/api/progress", params={"project": "demo"}).json()["result"]
    assert p["tasks"] == [] and p["open_checkpoints"] == []
    assert {"project.created", "item.added", "attachment.added"} <= {e["type"] for e in p["events"]}


def test_progress_shows_an_interrupted_task(ui, project):
    """Spec §11.6: a task cut off by a restart is listed with status ``interrupted`` (text in the task table)."""
    from rk.llm.runner import INTERRUPTED, interrupt_orphaned_tasks
    from rk.store.events import NewEvent

    ps, _ = project
    ps.apply(NewEvent(type="task.created", entity_id="tsk_cut", actor_kind="kernel", actor="t", payload={
        "role": "judge", "driver": "codex_cli", "vendor": "openai", "instructions": "x", "inputs": {}, "tools": [],
        "output_schema": {}}))
    assert interrupt_orphaned_tasks(ps) == ["tsk_cut"]
    (t,) = ui.get("/ui/api/progress", params={"project": "demo"}).json()["result"]["tasks"]
    assert (t["id"], t["status"], t["attempts"], t["error"]) == ("tsk_cut", "interrupted", 0, INTERRUPTED)
    assert t["finished"]


def test_settings_never_returns_secrets(ui, project):
    save_settings(load_settings().model_copy(update={"openalex_key": "secret-oa", "anthropic_api_key": "sk-x"}))
    s = ui.get("/ui/api/settings").json()["result"]
    assert "secret-oa" not in json.dumps(s) and "sk-x" not in json.dumps(s)
    assert s["openalex_key_set"] is True and s["s2_key_set"] is False and s["email"]
    assert post(ui, "/ui/api/settings", {"library_name": "Fixture Library", "s2_key": "new-s2"}).json()["ok"]
    assert (load_settings().library_name, load_settings().s2_key) == ("Fixture Library", "new-s2")
    assert post(ui, "/ui/api/settings", {"support_min_judges": 1}).status_code == 422  # not editable here
    assert post(ui, "/ui/api/settings", {"library_proxy": "http://proxy.example/login"}).status_code == 422


def test_render_draft_markers(ui, project, kernel):
    ps, lib = project
    iid = add_item_with_pdf(kernel)
    note = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="Encapsulation slowed degradation",
                                    role_in_source="main"), actor="t")
    from rk.argument import ArgumentService, ClaimIn, EvidenceIn

    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text="Encapsulation slows degradation."), generator_vendor="human", actor="t")
    svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[note.note_id]), actor="t")
    from rk.production.manuscript import write_draft

    write_draft(ps, "intro", "# Intro\n\nOld.\n", stage="draft", attempt=1, round=0, by_task=None, actor="t")
    write_draft(ps, "intro", f"Encapsulation helps [[clm:{cid}]]. Unknown [[clm:clm_01HXXXXXXXXXXXXXXXXXXXXXXX]]."
                " We infer more [[inf]].\n\n<script>alert(1)</script>\n", stage="draft", attempt=1, round=0,
                by_task=None, actor="t")
    drafts = ps.root / "artifacts" / "drafts"
    (drafts / "orphan.v1.md").write_text("Not a draft [[inf]].\n")  # a file without a drafts row is no draft
    (drafts / "intro.v3.md").write_text("Not recorded either [[inf]].\n")
    assert ui.get("/ui/api/drafts", params={"project": "demo"}).json()["result"]["drafts"] == [
        {"section": "intro", "versions": [1, 2]}]
    d = ui.get("/ui/api/drafts/intro", params={"project": "demo"}).json()["result"]
    assert d["version"] == 2 and "<script>" not in d["html"] and 'class="mk mk-clm"' in d["html"]
    m = {x["id"]: x for x in d["markers"] if x["kind"] == "clm"}
    assert m[cid]["label"] == "Encapsulation slows degradation." and m[cid]["evidence"][0]["item_id"] == iid
    assert m["clm_01HXXXXXXXXXXXXXXXXXXXXXXX"]["missing"] is True
    assert any(x["kind"] == "inf" for x in d["markers"])
    assert ui.get("/ui/api/drafts/intro", params={"project": "demo", "version": 1}).json()["result"]["version"] == 1
    assert ui.get("/ui/api/drafts/orphan", params={"project": "demo"}).status_code == 404
    assert ui.get("/ui/api/drafts/intro", params={"project": "demo", "version": 3}).status_code == 404
    (drafts / "intro.v1.md").write_text("Tampered [[inf]].\n")
    r = ui.get("/ui/api/drafts/intro", params={"project": "demo", "version": 1})
    assert r.status_code == 409 and "sha256" in r.json()["detail"]


def test_render_draft_skips_markers_in_code(ui, project):
    """One grammar with the verifier: a marker inside a code span or a code block is text, not a hover card."""
    from rk.ui.drafts import render_draft

    ps, lib = project
    d = render_draft(ps, lib, "Use `[[clm:clm_A]]` here [[inf]].\n\n```\n[[clm:clm_B]]\n```\n\n    [[clm:clm_C]]\n")
    assert [x["kind"] for x in d["markers"]] == ["inf"]
    assert "<code>[[clm:clm_A]]</code>" in d["html"] and "[[clm:clm_B]]" in d["html"] and "[[clm:clm_C]]" in d["html"]


def test_render_draft_resolves_statistics_figures_and_missing_plans(ui, project, tmp_path):
    """The preview names a statistic's or figure's code; an unknown ``plan`` target is marked missing."""
    from rk.production.artifacts import ArtifactIn, add_artifact
    from rk.ui.drafts import render_draft

    ps, lib = project
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.meta:pool", code_version="v", input_hashes=[],
                                      values={"estimate": 0.62}, output_path=None),
                       stage="synthesize", attempt=1, actor="k")
    d = render_draft(ps, lib, f"Pooled 0.62 [[stat:{sid}]]. Planned [[plan:hyp_01HXXXXXXXXXXXXXXXXXXXXXXX]].")
    m = {x["kind"]: x for x in d["markers"]}
    assert m["stat"]["label"] == "statistic from rk.stats.meta:pool" and not m["stat"].get("missing")
    assert m["plan"]["missing"] is True
    svg = tmp_path / "forest.svg"
    svg.write_text("<svg/>")
    fid = add_artifact(ps, ArtifactIn(kind="fig", code_path="rk.stats.plots:forest_svg", code_version="v",
                                      input_hashes=[], values={"estimate": 0.62}, output_path=str(svg)),
                       stage="synthesize", attempt=1, actor="k")
    wrong = render_draft(ps, lib, f"See [[fig:{sid}]] and [[stat:{fid}]] [[inf]].")  # fix round 2: kind mismatch
    assert [(x["kind"], x.get("missing")) for x in wrong["markers"]] == [("fig", True), ("stat", True), ("inf", None)]
    assert "not a figure" in wrong["markers"][0]["label"] and "not a statistic" in wrong["markers"][1]["label"]


@pytest.mark.parametrize("md", [
    'See [text](http://x "[[clm:clm_A]]") here [[inf]].', "See [text](http://x/[[clm:clm_A]]) here [[inf]].",
    "See <http://x/[[clm:clm_A]]> here [[inf]].", "# Results [[clm:clm_A]]\n\nSee text here [[inf]].",
    'See [text][r] here [[inf]].\n\n[r]: http://x "[[clm:clm_A]]"\n', "See ![[[clm:clm_A]]](img.png) here [[inf]]."],
    ids=["link-title", "link-destination", "autolink", "heading", "reference-definition", "image-alt"])
def test_render_draft_renders_no_marker_the_verifier_does_not_count(ui, project, md):
    """Fix round 2: the preview substitutes the verifier's markers only, so no hover card lands in an attribute."""
    from html.parser import HTMLParser

    from rk.ui.drafts import render_draft

    ps, lib = project
    d = render_draft(ps, lib, md)
    assert [x["kind"] for x in d["markers"]] == ["inf"]
    attrs: list[str] = []

    class Attrs(HTMLParser):
        def handle_starttag(self, tag, a):
            attrs.extend(v or "" for _, v in a)

    Attrs().feed(d["html"])
    assert not any("<" in v or "RKMARKER" in v for v in attrs) and "RKMARKER" not in d["html"]


def test_browser_status_without_browser(ui, project):
    assert ui.get("/ui/api/browser/status").json()["result"] == {"running": False}


def test_ws_requires_cookie_and_origin(ui, project):
    from starlette.websockets import WebSocketDisconnect

    ui.cookies.clear()
    with pytest.raises(WebSocketDisconnect), ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        ws.receive_json()
    login(ui)
    with pytest.raises(WebSocketDisconnect), ui.websocket_connect(
            "/ui/ws?project=demo", headers={"Origin": "https://evil.example"}) as ws:
        ws.receive_json()


def test_ws_pushes_new_events(ui, project, kernel):
    with ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        assert ws.receive_json()["type"] == "hello"
        add_item_with_pdf(kernel)
        deadline, seen = time.time() + 10, set()
        while "attachment.added" not in seen and time.time() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "event":
                seen.add(msg["event"]["type"])
    assert {"item.added", "attachment.added"} <= seen


def test_library_search_within_collection(ui, project, kernel):
    _ps, lib = project
    a = lib.add_item(ItemIn(title="Humidity aging of films A", ids=Ids(doi="10.5555/a")), added_by="human", actor="t").item_id
    lib.add_item(ItemIn(title="Humidity aging of films B", ids=Ids(doi="10.5555/b")), added_by="human", actor="t")
    included = lib.collection_by_name("Included")
    lib.collect(a, included, actor="t")
    hits = ui.get("/ui/api/library", params={"project": "demo", "q": "humidity"}).json()["result"]["items"]
    assert len(hits) == 2
    inside = ui.get("/ui/api/library", params={"project": "demo", "q": "humidity", "collection": included}).json()
    assert [i["id"] for i in inside["result"]["items"]] == [a]


def test_setup_check_routes(ui, project, monkeypatch):
    monkeypatch.setattr("rk.ui.api.check_providers", lambda s: {
        "claude_cli": {"roles": ["lead"], "ok": False, "reason": "not logged in", "hint": "run `claude`"}})
    prov = post(ui, "/ui/api/setup/check/providers").json()["result"]
    assert prov["claude_cli"]["hint"] == "run `claude`"
    caps = post(ui, "/ui/api/setup/check/capabilities").json()["result"]["capabilities"]
    assert {"area": "full text", "name": "built-in browser",
            "status": "not running (start rk serve without --no-browser)"} in caps
    lib = post(ui, "/ui/api/setup/check/library", {"doi": "10.5555/x"}).json()["result"]
    assert lib["status"] == "failed" and "browser_unavailable" in lib["detail"]
    assert post(ui, "/ui/api/setup/check/nope").status_code == 404


def test_open_portal_needs_browser_and_portal(ui, project):
    assert post(ui, "/ui/api/browser/open-portal").status_code == 503  # no browser in this server


def test_ws_pushes_checkpoint_updates(ui, project):
    ps, _ = project
    with ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        assert ws.receive_json()["type"] == "hello"
        chk = create_checkpoint(ps, "login_wait", "Sign in to the library", ["done"], actor="kernel:browser")
        deadline, msg = time.time() + 10, None
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "checkpoint":
                break
    assert msg["type"] == "checkpoint" and msg["checkpoint"]["id"] == chk and msg["checkpoint"]["kind"] == "login_wait"
    assert msg["open_count"] == 1


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_ws_chat_with_fake_driver(ui, project, fake, monkeypatch):
    exe, _records = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    save_settings(load_settings().model_copy(update={"cli_paths": {"claude": exe}}))
    with ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat.open", "driver": "claude_cli", "ref": 7})
        opened = next(m for m in iter(ws.receive_json, None) if m["type"] in ("chat.opened", "chat.error"))
        assert opened["type"] == "chat.opened" and opened["ref"] == 7
        ws.send_json({"type": "chat.send", "chat_id": opened["chat_id"], "text": "hello"})
        kinds = []
        while "done" not in kinds:
            m = ws.receive_json()
            if m["type"] == "chat":
                kinds.append(m["kind"])
        assert kinds == ["tool_call", "tool_result", "text", "done"]
    # The socket (and its event loop) is gone while the chat thread still finishes the turn; it must end quietly.
    for t in threading.enumerate():
        if t.name == "rk-ui-chat":
            t.join(10)


def test_ws_disconnect_closes_its_chats(ui, project, fake):
    exe, _records = fake
    save_settings(load_settings().model_copy(update={"cli_paths": {"claude": exe}}))

    def open_chat(ws, ref):
        ws.send_json({"type": "chat.open", "driver": "claude_cli", "ref": ref})
        return next(m for m in iter(ws.receive_json, None) if m["type"] == "chat.opened")["chat_id"]

    with ui.websocket_connect("/ui/ws?project=demo", headers=W) as other:
        assert other.receive_json()["type"] == "hello"
        kept = open_chat(other, 1)
        with ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
            assert ws.receive_json()["type"] == "hello"
            first, second = open_chat(ws, 1), open_chat(ws, 2)
            ws.send_json({"type": "chat.close", "chat_id": first})
            assert open_chat(ws, 3) not in (first, second)  # chat.close is handled in order, before this open
            assert first not in ui.chats._sessions and second in ui.chats._sessions
        deadline = time.time() + 5
        while time.time() < deadline and len(ui.chats._sessions) > 1:
            time.sleep(0.05)
        assert list(ui.chats._sessions) == [kept]  # the closed socket's chats are gone; the other socket's stay
