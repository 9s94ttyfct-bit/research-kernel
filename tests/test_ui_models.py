"""The Workbench Settings page's Models section: ``/ui/api/models`` and the drivers in ``POST /ui/api/settings``."""

# ruff: noqa: F811 - pytest fixtures imported from other test modules are re-bound as test parameters
import stat

import pytest

from rk.config import config_path, load_settings
from rk.llm import catalog
from rk.roles.policy import ROLES

from .test_catalog import GATEWAY, no_ollama, omp_ok
from .test_sessions import fake  # noqa: F401 - fixture
from .test_ui_api import ORIGIN, kernel, login, ui  # noqa: F401 - fixtures
from .test_ui_e2e import page, workbench, workbench_env  # noqa: F401 - fixtures

W = {"Origin": ORIGIN}
LUNA = "omp_gateway:openai-codex/gpt-6-luna"
UNKNOWN = "omp_gateway:foo/unknown-model"


@pytest.fixture
def omp_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "default_omp_runner", omp_ok)
    monkeypatch.setattr(catalog, "default_ollama_runner", no_ollama)


def test_models_route_lists_choices_and_roles(ui, omp_catalog):
    login(ui)
    r = ui.get("/ui/api/models").json()["result"]
    assert r["roles"] == list(ROLES)
    assert {m["driver"]: m["vendor"] for m in r["models"] if m["provider"] == "omp_gateway"} == GATEWAY
    assert {"driver", "label", "vendor", "available", "reason", "provider", "local"} == set(r["models"][0])
    assert r["omp_status"] is None


def test_models_route_survives_a_malformed_omp_listing(ui, monkeypatch):
    from .test_catalog import MALFORMED

    monkeypatch.setattr(catalog, "default_omp_runner", lambda argv, t: MALFORMED)
    monkeypatch.setattr(catalog, "default_ollama_runner", no_ollama)
    login(ui)
    r = ui.get("/ui/api/models").json()["result"]
    assert set(GATEWAY) <= {m["driver"] for m in r["models"]} and "4 malformed" in r["omp_status"]
    monkeypatch.setattr(catalog, "default_omp_runner", lambda argv, t: "not json")
    catalog.clear_cache()
    r = ui.get("/ui/api/models").json()["result"]
    assert r["omp_status"].startswith("omp gateway models are not listed")


def test_settings_drivers_are_validated(ui, omp_catalog):
    login(ui)
    before = load_settings().drivers
    r = ui.post("/ui/api/settings", json={"drivers": {"judge": UNKNOWN}}, headers=W)
    assert r.status_code == 422
    assert any("unknown vendor" in p for p in r.json()["detail"]["problems"])
    assert load_settings().drivers == before

    r = ui.post("/ui/api/settings", json={"drivers": {"judge": LUNA}}, headers=W).json()["result"]
    assert r["saved"] == ["drivers"] and r["warnings"] == []
    s = load_settings()
    assert s.drivers["judge"] == LUNA and s.drivers["judge_2"] == before["judge_2"]  # the others are kept
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600

    two_openai = {"judge_2": "codex_cli:gpt-6-sol"}
    r = ui.post("/ui/api/settings", json={"drivers": two_openai}, headers=W).json()["result"]
    assert any("writer (claude_cli) are anthropic" in w for w in r["warnings"])
    got = ui.get("/ui/api/settings").json()["result"]
    assert got["driver_warnings"] == r["warnings"] and got["driver_problems"] == []


@pytest.mark.browser
def test_settings_models_section_saves_a_gateway_judge(page, omp_catalog):
    page.goto(page.url.split("#")[0] + "#/settings")
    judge = page.locator('select[name="driver:judge"]')
    judge.wait_for()
    assert page.locator(".models select").count() == len(ROLES)
    luna = page.locator(f'select[name="driver:judge"] option[value="{LUNA}"]')
    assert "openai" in luna.text_content()
    judge.select_option(LUNA)
    page.get_by_role("button", name="Save models").click()
    page.wait_for_function("() => document.querySelector('#toast')?.textContent.startsWith('Saved models')")
    assert load_settings().drivers["judge"] == LUNA

    # an invalid choice: the problem is shown inline, as text, and nothing is saved
    page.locator('select[name="driver:judge_2"]').select_option("other")
    page.locator('input[name="other:judge_2"]').fill(UNKNOWN + "<b>x</b>")
    page.get_by_role("button", name="Save models").click()
    problem = page.locator(".models .problems li").first
    problem.wait_for()
    assert "unknown vendor" in problem.text_content() and "<b>x</b>" in problem.text_content()
    assert page.locator(".models .problems b").count() == 0
    assert load_settings().drivers["judge_2"] == "ollama:deepseek-v4.1-flash:cloud"
    assert page.errors == [] or all("422" in e for e in page.errors)


@pytest.mark.browser
def test_settings_models_section_marks_local_models(page, monkeypatch):
    """The user asked for local models to be marked, not hidden: a local Ollama model's option says "local model";
    an Ollama Cloud model's option does not."""
    monkeypatch.setattr(catalog, "default_omp_runner", omp_ok)
    monkeypatch.setattr(catalog, "default_ollama_runner", lambda s: ["gemma4:12b-it-qat", "gpt-oss:120b-cloud"])
    page.goto(page.url.split("#")[0] + "#/settings")
    page.locator('select[name="driver:writer"]').wait_for()
    option = page.locator('select[name="driver:writer"] option[value="ollama:gemma4:12b-it-qat"]')
    assert "local model" in option.text_content()
    cloud = page.locator('select[name="driver:writer"] option[value="ollama:gpt-oss:120b-cloud"]')
    assert "local model" not in cloud.text_content()
    assert page.errors == []
