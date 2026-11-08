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
# RK_HOME as it was when pytest started (the researcher's own home, or a scratch one): no test may write there
RK_HOME_AT_START = os.environ.get("RK_HOME")


@pytest.fixture(autouse=True)
def _temp_rk_home(tmp_path_factory, monkeypatch):
    """Every test runs with RK_HOME on a temp directory of its own, so nothing a test saves (config.toml, the
    kernel database, eval caches) reaches the real ``$RK_HOME`` or ``~/ResearchKernel``. ``tmp_home`` and tests
    that set RK_HOME themselves override it. The directory sits outside ``tmp_path``, which stays empty.
    Fixtures of a broader scope run before this one, so a session- or module-scoped fixture, or a subprocess started
    with an explicit ``env``, must set RK_HOME itself."""
    monkeypatch.setenv("RK_HOME", str(tmp_path_factory.mktemp("rk-home")))


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


def _catalog_blocked(*args):
    raise OSError("the model catalog's omp and Ollama listings are blocked in tests")


@pytest.fixture(autouse=True)
def _no_real_model_catalog(request, monkeypatch):
    """The model catalog (Settings page, ``rk models``) never runs ``omp models`` or asks the local Ollama server in
    tests: both default listings fail unless a test sets its own (``llm`` tests see the real ones)."""
    from rk.llm import catalog

    catalog.clear_cache()
    if not request.node.get_closest_marker("llm"):
        monkeypatch.setattr(catalog, "default_omp_runner", _catalog_blocked)
        monkeypatch.setattr(catalog, "default_ollama_runner", _catalog_blocked)
    yield
    catalog.clear_cache()


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
def serve_rk(tmp_home, replay_http):
    """Starts a real ``rk serve`` (uvicorn in a thread) on a temp RK_HOME and returns a CLI-role RkClient with
    ``.kernel`` and ``.runs``. ``serve_rk(orchestrator=f)`` wires the runs and stages: ``f(kernel, registry)`` returns
    the ``Orchestrator`` (tests give it fake workers, never real drivers); without it the run and stage tools are
    unavailable."""
    import threading
    import time

    import uvicorn

    from rk.cli.client import RkClient
    from rk.orchestrator.runs import RunManager
    from rk.server.app import create_app
    from rk.server.chat import ChatManager
    from rk.server.runtime import acquire_serve_lock, release
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    started: list[tuple] = []

    def start(*, orchestrator=None):
        rt = acquire_serve_lock(tmp_home)
        kernel = Kernel(tmp_home, http=replay_http)
        runs = None
        registry = build_registry(kernel, runs=lambda: runs)
        if orchestrator is not None:
            runs = RunManager(kernel, registry, orchestrator(kernel, registry), poll_s=0.05)
        chats = ChatManager(kernel, registry)
        server = uvicorn.Server(uvicorn.Config(create_app(registry, rt.token, tokens=kernel.tokens, chats=chats,
                                                          runs=runs), log_level="warning"))
        t = threading.Thread(target=server.run, kwargs={"sockets": [rt.sock]}, daemon=True)
        t.start()
        started.append((server, t, kernel, runs, rt))
        client = RkClient.from_home(tmp_home)
        for _ in range(100):
            if client.healthy():
                break
            time.sleep(0.05)
        client.kernel, client.runs = kernel, runs
        return client

    yield start
    for server, t, kernel, runs, rt in reversed(started):
        server.should_exit = True
        t.join(5)
        if runs is not None:
            runs.close()
        kernel.close()
        release(rt)


@pytest.fixture
def live_server(serve_rk):
    """A real ``rk serve`` (uvicorn in a thread) on a temp RK_HOME; yields a CLI-role RkClient."""
    return serve_rk()
