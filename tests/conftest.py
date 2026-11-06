from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = FIXTURES / "docs"
HTTP_FIXTURES = FIXTURES / "http"
GOLD = Path(__file__).parent / "gold"
MODEL_CLIS = ("claude", "codex", "omp")


@pytest.fixture(scope="session")
def _blocked_clis(tmp_path_factory):
    """A directory of stand-ins for the model CLIs that record each call and fail."""
    d = tmp_path_factory.mktemp("blocked-clis")
    log = d / "calls.log"
    log.touch()
    for name in MODEL_CLIS:
        exe = d / name
        exe.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\necho "{name} is blocked in tests" >&2\nexit 97\n')
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return d, log


@pytest.fixture(autouse=True)
def _no_real_model_cli(request, _blocked_clis, monkeypatch):
    """Tests never reach a real model CLI: ``claude``/``codex``/``omp`` on PATH resolve to recording stand-ins, and a
    test that ran one fails. Only ``llm`` tests (deselected by default) see the real PATH."""
    if request.node.get_closest_marker("llm"):
        yield
        return
    d, log = _blocked_clis
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ.get('PATH', '')}")
    before = log.read_text()
    yield
    calls = log.read_text()[len(before):]
    if calls:
        pytest.fail(f"test ran a model CLI from PATH instead of a fake: {calls.strip()}")


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
    from rk.server.chat import ChatManager
    from rk.server.runtime import acquire_serve_lock, release
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    rt = acquire_serve_lock(tmp_home)
    kernel = Kernel(tmp_home, http=replay_http)
    registry = build_registry(kernel)
    chats = ChatManager(kernel, registry)
    server = uvicorn.Server(uvicorn.Config(create_app(registry, rt.token, tokens=kernel.tokens, chats=chats),
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
