from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = FIXTURES / "docs"
HTTP_FIXTURES = FIXTURES / "http"
GOLD = Path(__file__).parent / "gold"


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "rkhome"
    home.mkdir()
    monkeypatch.setenv("RK_HOME", str(home))
    return home


@pytest.fixture
def replay_http(tmp_path):
    from rk.net.http import HttpClient
    from rk.net.recording import ReplayTransport

    transport = ReplayTransport(HTTP_FIXTURES)
    h = HttpClient(tmp_path / "http.sqlite", user_agent="rk-test", sleep=lambda s: None, transport=transport)
    h.transport = transport
    yield h
    h.close()


@pytest.fixture
def source_ctx(replay_http):
    from rk.config import Settings
    from rk.sources.resolver import SourceContext

    return SourceContext(http=replay_http, settings=Settings(email="rk-fixtures@users.noreply.github.com"))


@pytest.fixture
def live_server(tmp_home, replay_http):
    """A real ``rk serve`` (uvicorn in a thread) on a temp RK_HOME; yields a CLI-role RkClient."""
    import threading
    import time

    import uvicorn

    from rk.cli.client import RkClient
    from rk.server.app import create_app
    from rk.server.runtime import acquire_serve_lock, release
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

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
    client.kernel = kernel
    yield client
    server.should_exit = True
    t.join(5)
    kernel.close()
    release(rt)
