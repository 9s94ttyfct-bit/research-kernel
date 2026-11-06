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
