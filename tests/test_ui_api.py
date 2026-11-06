import pytest
from fastapi.testclient import TestClient

from rk.config import Settings, save_settings
from rk.server.app import create_app
from rk.server.chat import ChatManager
from rk.tools.builtin import build_registry
from rk.ui.auth import UiAuth
from rk.workspace import Kernel

ORIGIN = "http://testserver"
SERVE_TOKEN = "serve-token-for-tests"


@pytest.fixture
def kernel(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    yield k
    k.close()


@pytest.fixture
def ui(kernel):
    auth = UiAuth(ORIGIN)
    reg = build_registry(kernel)
    app = create_app(reg, SERVE_TOKEN, tokens=kernel.tokens, chats=ChatManager(kernel, reg), ui=auth, kernel=kernel)
    client = TestClient(app)
    client.auth_ui = auth
    return client


def login(client) -> None:
    r = client.get("/ui/login", params={"code": client.auth_ui.new_login_code()}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/"
    cookie = r.headers["set-cookie"]
    assert "rk_ui=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")


def test_login_code_single_use_and_expires(ui):
    code = ui.auth_ui.new_login_code()
    assert ui.get("/ui/login", params={"code": code}, follow_redirects=False).status_code == 303
    assert ui.get("/ui/login", params={"code": code}, follow_redirects=False).status_code == 403  # used
    old = ui.auth_ui.new_login_code()
    ui.auth_ui._codes[old] -= 121  # 120 s lifetime
    assert ui.get("/ui/login", params={"code": old}, follow_redirects=False).status_code == 403


def test_ui_api_requires_cookie(ui):
    assert ui.get("/ui/api/session").status_code == 401
    login(ui)
    assert ui.get("/ui/api/session").json() == {"ok": True, "result": {"actor": "ui:user"}}


def test_serve_token_rejected_on_ui(ui):
    r = ui.get("/ui/api/session", headers={"Authorization": f"Bearer {SERVE_TOKEN}"})
    assert r.status_code == 401


def test_ui_cookie_rejected_on_api(ui):
    login(ui)
    assert ui.get("/api/tools").status_code == 401  # the cookie is not a bearer token


def test_cross_origin_post_rejected(ui):
    login(ui)
    assert ui.delete("/ui/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert ui.delete("/ui/api/session").status_code == 403  # no Origin header either
    assert ui.delete("/ui/api/session", headers={"Origin": ORIGIN}).json()["ok"] is True
    assert ui.get("/ui/api/session").status_code == 401  # logged out


def test_ui_link_is_cli_only(ui, kernel):
    assert ui.post("/api/ui/link").status_code == 401
    lead = kernel.tokens.issue_lead("ext")
    assert ui.post("/api/ui/link", headers={"Authorization": f"Bearer {lead}"}).status_code == 403
    r = ui.post("/api/ui/link", headers={"Authorization": f"Bearer {SERVE_TOKEN}"})
    url = r.json()["result"]["url"]
    assert url.startswith(ORIGIN + "/ui/login?code=")
    assert ui.get(url.removeprefix(ORIGIN), follow_redirects=False).status_code == 303


def test_open_human_without_browser_is_503(ui):
    r = ui.post("/api/browser/open-human", json={"url": "https://example.org"},
                headers={"Authorization": f"Bearer {SERVE_TOKEN}"})
    assert r.status_code == 503


def test_static_index_served(ui):
    r = ui.get("/ui/")
    assert r.status_code == 200 and "<title>Research Kernel" in r.text
