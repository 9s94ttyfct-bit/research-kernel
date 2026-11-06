import os
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from rk.cli.client import RkClient
from rk.cli.setup import run_setup
from rk.config import load_settings
from rk.net.http import HttpClient
from rk.server.app import create_app
from rk.server.runtime import AlreadyRunning, acquire_serve_lock, release
from rk.tools.builtin import build_registry
from rk.workspace import Kernel


def test_server_auth(tmp_home, replay_http):
    app = create_app(build_registry(Kernel(tmp_home, http=replay_http)), "secret-token")
    c = TestClient(app)
    assert c.get("/healthz").json()["ok"]
    assert c.post("/api/tools/rk_project_list", json={"args": {}}).status_code == 401
    assert c.post("/api/tools/rk_project_list", json={"args": {}}, headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = c.post("/api/tools/rk_project_list", json={"args": {}}, headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "result": {"projects": []}}
    bad = c.post("/api/tools/rk_project_create", json={"args": {"slug": "x"}, "idempotency_key": "k"},
                 headers={"Authorization": "Bearer secret-token"})
    assert bad.status_code == 422 and bad.json()["error"]["type"] == "validation_error"
    assert c.post("/api/tools/nope", json={"args": {}}, headers={"Authorization": "Bearer secret-token"}).status_code == 404


def test_serve_lock(tmp_home):
    rt = acquire_serve_lock(tmp_home)
    try:
        assert oct((tmp_home / "serve.token").stat().st_mode & 0o777) == "0o600"
        with pytest.raises(AlreadyRunning) as ei:  # lock is held -> a second server must refuse
            acquire_serve_lock(tmp_home)
        assert ei.value.pid == os.getpid()
    finally:
        release(rt)
    assert not (tmp_home / "serve.port").exists()
    (tmp_home / "serve.lock").write_text("1")  # leftover pid of a dead/other process does not matter
    rt2 = acquire_serve_lock(tmp_home)
    (tmp_home / "serve.port").write_text("1")  # a newer server took over the port file
    release(rt2)
    assert (tmp_home / "serve.port").read_text() == "1"


@pytest.fixture
def live_server(tmp_home, replay_http):
    rt = acquire_serve_lock(tmp_home)
    kernel = Kernel(tmp_home, http=replay_http)
    server = uvicorn.Server(uvicorn.Config(create_app(build_registry(kernel), rt.token, tokens=kernel.tokens),
                                           log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
    t.start()
    client = RkClient.from_home(tmp_home)
    for _ in range(100):
        if client.healthy():
            break
        time.sleep(0.05)
    yield client
    server.should_exit = True
    t.join(5)
    kernel.close()
    release(rt)


def test_concurrent_cli_writes(live_server):
    c = live_server
    c.call("rk_project_create", {"slug": "cc", "title": "Concurrency"}, write=True)
    iid = c.call("rk_library_import", {"project": "cc", "format": "csl",
                                       "text": '[{"type":"article-journal","title":"A paper","issued":{"date-parts":[[2020]]}}]'},
                 write=True)["item_ids"][0]
    errors: list[Exception] = []

    def worker(n: int) -> None:
        cl = RkClient(c.base_url, c._http.headers["Authorization"].split()[1])
        try:
            for i in range(10):
                cl.call("rk_library_tag", {"project": "cc", "item_id": iid, "add": [f"t{n}-{i}"]}, write=True)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            cl.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    st = c.call("rk_project_status", {"project": "cc"})
    assert st["chain"]["ok"]
    tags = c.call("rk_library_list", {"project": "cc"})["items"][0]["tags"]
    assert len(tags) == 20


def test_setup_wizard_with_mocks(tmp_home, tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if "crossref" in req.url.host:
            return httpx.Response(200, json={"message": {}})
        if "openalex" in req.url.host:
            return httpx.Response(200, json={"id": "W1"}, headers={"x-ratelimit-remaining": "0.99", "x-ratelimit-limit": "1"})
        return httpx.Response(403)

    http = HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None, transport=httpx.MockTransport(handler))
    answers = iter(["me@example.org", "OAKEY", ""])
    said: list[str] = []
    s, caps = run_setup(http, lambda q, d: next(answers), said.append)
    assert s.email == "me@example.org" and s.openalex_key == "OAKEY" and s.s2_key is None
    assert caps["semantic_scholar"].startswith("pending") and caps["openalex"].startswith("available")
    assert any("openalex.org/settings/api" in m for m in said)
    p = tmp_home / "config.toml"
    assert oct(p.stat().st_mode & 0o777) == "0o600" and load_settings().openalex_key == "OAKEY"
