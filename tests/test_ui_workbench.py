# ruff: noqa: F811 - pytest fixtures imported from test_ui_api / test_sessions are re-bound as test parameters
import json
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


def test_settings_never_returns_secrets(ui, project):
    save_settings(load_settings().model_copy(update={"openalex_key": "secret-oa", "anthropic_api_key": "sk-x"}))
    s = ui.get("/ui/api/settings").json()["result"]
    assert "secret-oa" not in json.dumps(s) and "sk-x" not in json.dumps(s)
    assert s["openalex_key_set"] is True and s["s2_key_set"] is False and s["email"]
    assert post(ui, "/ui/api/settings", {"library_name": "Fixture Library", "s2_key": "new-s2"}).json()["ok"]
    assert (load_settings().library_name, load_settings().s2_key) == ("Fixture Library", "new-s2")
    assert post(ui, "/ui/api/settings", {"support_tau_hi": 0.1}).status_code == 422  # not editable here
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
    drafts = ps.root / "artifacts" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "intro.v1.md").write_text("# Intro\n\nOld.\n")
    (drafts / "intro.v2.md").write_text(f"Encapsulation helps [[clm:{cid}]]. Unknown [[clm:clm_01HXXXXXXXXXXXXXXXXXXXXXXX]]."
                                        " We infer more [[inf]].\n\n<script>alert(1)</script>\n")
    assert ui.get("/ui/api/drafts", params={"project": "demo"}).json()["result"]["drafts"] == [
        {"section": "intro", "versions": [1, 2]}]
    d = ui.get("/ui/api/drafts/intro", params={"project": "demo"}).json()["result"]
    assert d["version"] == 2 and "<script>" not in d["html"] and 'class="mk mk-clm"' in d["html"]
    m = {x["id"]: x for x in d["markers"] if x["kind"] == "clm"}
    assert m[cid]["label"] == "Encapsulation slows degradation." and m[cid]["evidence"][0]["item_id"] == iid
    assert m["clm_01HXXXXXXXXXXXXXXXXXXXXXXX"]["missing"] is True
    assert any(x["kind"] == "inf" for x in d["markers"])
    assert ui.get("/ui/api/drafts/intro", params={"project": "demo", "version": 1}).json()["result"]["version"] == 1


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


def test_ws_chat_with_fake_driver(ui, project, fake, monkeypatch):
    exe, _records = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    save_settings(load_settings().model_copy(update={"cli_paths": {"claude": exe}}))
    with ui.websocket_connect("/ui/ws?project=demo", headers=W) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat.open", "driver": "claude_cli"})
        opened = next(m for m in iter(ws.receive_json, None) if m["type"] in ("chat.opened", "chat.error"))
        assert opened["type"] == "chat.opened"
        ws.send_json({"type": "chat.send", "chat_id": opened["chat_id"], "text": "hello"})
        kinds = []
        while "done" not in kinds:
            m = ws.receive_json()
            if m["type"] == "chat":
                kinds.append(m["kind"])
        assert kinds == ["tool_call", "tool_result", "text", "done"]
