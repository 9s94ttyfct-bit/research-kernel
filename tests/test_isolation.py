"""Tests never touch the real RK_HOME: every test runs on a temp home of its own (conftest ``_temp_rk_home``)."""

from __future__ import annotations

from pathlib import Path

from rk.config import Settings, config_path, rk_home, save_settings
from tests.conftest import RK_HOME_AT_START


def _real_homes() -> set[Path]:
    homes = {(Path.home() / "ResearchKernel").resolve()}
    if RK_HOME_AT_START:
        homes.add(Path(RK_HOME_AT_START).expanduser().resolve())
    return homes


def test_rk_home_is_never_a_real_home():
    assert rk_home().resolve() not in _real_homes()


def test_saved_settings_stay_in_the_temp_home(tmp_path_factory):
    p = save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    assert p == config_path() and p.parent.resolve() not in _real_homes()
    assert p.resolve().is_relative_to(tmp_path_factory.getbasetemp().resolve())
