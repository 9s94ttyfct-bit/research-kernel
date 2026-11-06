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
