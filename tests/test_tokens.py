import pytest
from fastapi.testclient import TestClient

from rk.config import Settings, save_settings
from rk.server.app import create_app
from rk.server.tokens import TokenStore
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied
from rk.workspace import Kernel


@pytest.fixture
def setup(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    for slug in ("pa", "pb"):
        reg.call("rk_project_create", {"slug": slug, "title": slug.upper()},
                 CallContext(role="cli", actor="t", idempotency_key=f"c-{slug}"))
    client = TestClient(create_app(reg, "serve-tok", tokens=k.tokens))
    yield k, reg, client
    k.close()


def _post(client, token, name, args, key=None):
    return client.post(f"/api/tools/{name}", json={"args": args, "idempotency_key": key},
                       headers={"Authorization": f"Bearer {token}"})


def test_task_token_scope_and_expiry(setup):
    k, _, c = setup
    tok = k.tokens.issue_task("tsk_1", "lead", "pa", ["rk_library_list"], ttl_s=60)
    assert _post(c, tok, "rk_library_list", {"project": "pa"}).status_code == 200
    assert _post(c, tok, "rk_library_list", {}).json()["ok"]  # project injected from the grant
    assert _post(c, tok, "rk_library_list", {"project": "pb"}).status_code == 403
    assert _post(c, tok, "rk_library_search", {"project": "pa", "query": "x"}).status_code == 403  # not granted
    assert _post(c, tok, "rk_project_list", {}).status_code == 403  # not project-scoped
    assert k.tokens.revoke("tsk_1") == 1
    assert _post(c, tok, "rk_library_list", {"project": "pa"}).status_code == 401
    expired = k.tokens.issue_task("tsk_2", "lead", "pa", ["rk_library_list"], ttl_s=0)
    assert _post(c, expired, "rk_library_list", {"project": "pa"}).status_code == 401
    assert _post(c, "made-up", "rk_library_list", {"project": "pa"}).status_code == 401


def test_lead_token_cannot_use_cli_only_tools(setup):
    k, _, c = setup
    tok = k.tokens.issue_lead("claude-desktop")
    assert _post(c, tok, "rk_project_list", {}).status_code == 200  # lead tokens are not project-bound
    r = _post(c, tok, "rk_library_attach", {"project": "pa", "source_id": "src_x", "path": "/etc/hosts"}, key="k1")
    assert r.status_code == 403
    assert _post(c, tok, "rk_token_create", {"name": "escalate"}).status_code == 403
    with pytest.raises(ValueError):
        k.tokens.issue_lead("claude-desktop")  # names are unique while active


def test_tools_listing_filtered(setup):
    k, _, c = setup
    tok = k.tokens.issue_task("tsk_3", "lead", "pa", ["rk_text_find", "rk_project_list"], ttl_s=60)
    names = {t["name"] for t in c.get("/api/tools", headers={"Authorization": f"Bearer {tok}"}).json()["tools"]}
    assert names == {"rk_text_find"}  # rk_project_list is hidden from a project-bound caller
    cli_names = {t["name"] for t in c.get("/api/tools", headers={"Authorization": "Bearer serve-tok"}).json()["tools"]}
    assert {"rk_library_attach", "rk_token_create"} <= cli_names


def test_tokens_stored_hashed(tmp_path):
    store = TokenStore(tmp_path / "k.sqlite")
    tok = store.issue_task("tsk_9", "reader", "pa", ["rk_text_find"], ttl_s=60)
    lead = store.issue_lead("x")
    store.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert tok.encode() not in raw and lead.encode() not in raw


def test_registry_project_binding_direct(setup):
    _, reg, _ = setup
    ctx = CallContext(role="lead", actor="t", project="pa", allowed_tools=frozenset({"rk_library_list"}))
    assert reg.call("rk_library_list", {}, ctx)["items"] == []
    with pytest.raises(PermissionDenied):
        reg.call("rk_library_list", {"project": "pb"}, ctx)


def test_token_cli_roundtrip_via_tools(setup):
    _, reg, _ = setup
    cli = CallContext(role="cli", actor="u")
    created = reg.call("rk_token_create", {"name": "ext"}, cli)
    assert created["token"] and reg.call("rk_token_list", {}, cli)["tokens"][0]["name"] == "ext"
    assert reg.call("rk_token_revoke", {"name": "ext"}, cli)["revoked"] == 1
