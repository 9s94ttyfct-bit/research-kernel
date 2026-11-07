"""The model catalog (``rk models``, ``/ui/api/models``), the vendor family rules and the driver validation shared by
the Settings page, ``rk driver set`` and ``rk providers``."""

import json
import stat
import subprocess
import threading
import time
import tomllib

import pytest
from typer.testing import CliRunner

from rk.cli.main import app
from rk.config import Settings, config_path, load_settings, save_settings
from rk.llm import catalog
from rk.llm.catalog import driver_warnings, list_models, model_catalog, validate_drivers
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.vendors import vendor_of

from .conftest import FIXTURES

OMP_JSON = (FIXTURES / "omp_models.json").read_text()
GATEWAY = {"omp_gateway:anthropic/claude-haiku-5-5": "anthropic", "omp_gateway:openai-codex/gpt-6-luna": "openai",
           "omp_gateway:ollama-cloud/deepseek-v4.1-flash": "deepseek"}


def omp_ok(argv, timeout):
    assert argv[1:] == ["models", "--json"] and timeout == catalog.OMP_TIMEOUT_S == 10
    return OMP_JSON


def no_ollama(settings):
    return []


def test_omp_catalog_lists_gateway_models():
    models = list_models(Settings(), omp_runner=omp_ok, ollama_runner=no_ollama)
    gateway = {m.driver: m for m in models if m.driver.startswith("omp_gateway")}
    assert {d: m.vendor for d, m in gateway.items()} == GATEWAY  # the embedding model is not a chat model
    assert all(m.available and m.reason is None for m in gateway.values())
    assert gateway["omp_gateway:openai-codex/gpt-6-luna"].label == "GPT-6 Luna"
    drivers = {m.driver: m.vendor for m in models}
    # the same catalog names the subscription CLIs' and Ollama Cloud's models
    assert drivers["claude_cli:claude-haiku-5-5"] == "anthropic" and drivers["codex_cli:gpt-6-luna"] == "openai"
    assert drivers["ollama:deepseek-v4.1-flash:cloud"] == "deepseek"
    assert len(drivers) == len(models)  # one choice per driver


def test_catalog_marks_ollama_models_not_in_ollama_list():
    models = {m.driver: m for m in list_models(Settings(), omp_runner=omp_ok,
                                                ollama_runner=lambda s: ["gemma4:12b-it-qat"])}
    assert models["ollama:gemma4:12b-it-qat"].available and models["ollama:gemma4:12b-it-qat"].vendor == "google"
    cloud = models["ollama:deepseek-v4.1-flash:cloud"]
    assert not cloud.available and "ollama pull deepseek-v4.1-flash:cloud" in cloud.reason


@pytest.mark.parametrize(("error", "why"), [
    (subprocess.TimeoutExpired(["omp", "models", "--json"], 10), "timed out after 10 s"),
    (FileNotFoundError("omp"), "omp is not installed"),
    (subprocess.CalledProcessError(2, ["omp"], stderr="not logged in"), "not logged in"),
])
def test_omp_missing_or_failing_is_reported_not_raised(error, why):
    def runner(argv, timeout):
        raise error

    def unreachable(settings):
        raise OSError("connection refused")

    models = list_models(Settings(), omp_runner=runner, ollama_runner=unreachable)
    gateway = [m for m in models if m.provider == "omp_gateway"]
    assert len(gateway) == 1 and gateway[0].driver == "omp_gateway" and not gateway[0].available
    assert why in gateway[0].reason
    # the other drivers are still listed: the configured ones and the subscription CLIs
    drivers = {m.driver for m in models}
    assert {"claude_cli", "codex_cli", "claude_cli:claude-haiku-5-5", "ollama:deepseek-v4.1-flash:cloud"} <= drivers
    assert "connection refused" in next(m for m in models if m.provider == "ollama").reason


def _fake_omp(tmp_path, body: str):
    exe = tmp_path / "omp"
    exe.write_text(f"#!/bin/sh\necho run >> \"{tmp_path / 'runs.log'}\"\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def test_slow_omp_times_out_and_success_is_cached(tmp_path, monkeypatch):
    """The default runner: a slow ``omp`` is cut off at the timeout, so the Settings page never hangs on it; a good
    listing is cached in-process, so a second catalog does not run ``omp`` again."""
    monkeypatch.setattr(catalog, "default_omp_runner", catalog.run_omp)
    monkeypatch.setattr(catalog, "OMP_TIMEOUT_S", 0.5)
    slow = Settings(cli_paths={"omp": _fake_omp(tmp_path, "sleep 5")})
    t0 = time.monotonic()
    gateway = [m for m in list_models(slow, ollama_runner=no_ollama) if m.provider == "omp_gateway"]
    assert time.monotonic() - t0 < 3 and "timed out after 0.5 s" in gateway[0].reason

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    (good_dir / "models.json").write_text(OMP_JSON)
    good = Settings(cli_paths={"omp": _fake_omp(good_dir, f"cat \"{good_dir / 'models.json'}\"")})
    for _ in range(2):
        assert {m.driver for m in list_models(good, ollama_runner=no_ollama)} >= set(GATEWAY)
    assert (good_dir / "runs.log").read_text().count("run") == 1


MALFORMED = json.dumps({"models": [
    *json.loads(OMP_JSON)["models"],
    42,  # not an object
    {"kind": "chat", "provider": "openai-codex", "id": "gpt-x"},  # no selector
    {"kind": "chat", "selector": ["ollama-cloud/glm-5"]},  # selector not a string
    {"kind": "chat", "selector": "ollama-cloud/bad model"},  # not a valid driver
    {"kind": "chat", "selector": "ollama-cloud/glm-5", "id": 7, "provider": ["x"], "name": None},  # usable
]})


def test_malformed_omp_entries_are_skipped_not_raised():
    cat = model_catalog(Settings(), omp_runner=lambda argv, t: MALFORMED, ollama_runner=no_ollama)
    drivers = {m.driver: m for m in cat.models}
    assert set(GATEWAY) <= set(drivers) and drivers["omp_gateway:ollama-cloud/glm-5"].vendor == "zhipu"
    assert drivers["omp_gateway:ollama-cloud/glm-5"].label == "glm-5"  # id and name from the selector
    assert "ollama:glm-5:cloud" not in drivers  # its provider is not a string: no Ollama Cloud entry derived
    assert "4 malformed" in cat.omp_status
    assert cat.omp_status == model_catalog(Settings(), omp_runner=lambda a, t: MALFORMED,
                                           ollama_runner=no_ollama).omp_status


@pytest.mark.parametrize("out", ["not json", '{"models": "x"}', "[1, 2]", '{"models": null}', "[" * 100_000])
def test_unreadable_omp_listing_is_reported_and_not_cached_as_good(out, monkeypatch):
    monkeypatch.setattr(catalog, "default_omp_runner", lambda argv, t: out)
    cat = model_catalog(Settings(), ollama_runner=no_ollama)
    gateway = [m for m in cat.models if m.provider == "omp_gateway"]
    assert len(gateway) == 1 and not gateway[0].available and "failed" in gateway[0].reason
    assert "claude_cli:claude-haiku-5-5" in {m.driver for m in cat.models}
    assert cat.omp_status.startswith("omp gateway models are not listed")
    _, (models, error, _) = catalog._omp_cache["omp"]
    assert models == [] and error  # cached as a failure (1 min), never as a good listing


def test_omp_status_shown_with_a_configured_gateway_driver():
    def missing(argv, timeout):
        raise FileNotFoundError("omp")

    cat = model_catalog(Settings(drivers={"reader": "omp_gateway:openai-codex/gpt-6-luna"}), omp_runner=missing,
                        ollama_runner=no_ollama)
    gateway = [m for m in cat.models if m.provider == "omp_gateway"]
    assert [m.driver for m in gateway] == ["omp_gateway:openai-codex/gpt-6-luna"] and not gateway[0].available
    assert "omp is not installed" in cat.omp_status


def test_cold_catalog_runs_omp_once_for_concurrent_loads(monkeypatch):
    calls = []

    def slow(argv, timeout):
        calls.append(1)
        time.sleep(0.3)
        return OMP_JSON

    monkeypatch.setattr(catalog, "default_omp_runner", slow)
    threads = [threading.Thread(target=list_models, args=(Settings(),), kwargs={"ollama_runner": no_ollama})
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads)  # every load finished, none still waiting on the lock
    assert len(calls) == 1


@pytest.mark.parametrize(("driver", "vendor"), [
    ("omp_gateway:ollama-cloud/deepseek-v4.1-flash", "deepseek"),
    ("omp_gateway:ollama-cloud/kimi-k3", "moonshot"),
    ("omp_gateway:foo/unknown-model", None),
    ("omp_gateway:ollama-cloud/glm-5.3-flash", "zhipu"),
    ("omp_gateway:ollama-cloud/minimax-m3", "minimax"),
    ("omp_gateway:ollama-cloud/qwen3-coder:480b", "alibaba"),
    ("omp_gateway:ollama-cloud/mistral-large-4", "mistral"),
    ("omp_gateway:ollama-cloud/gemini-3-flash-preview", "google"),
    ("omp_gateway:ollama-cloud/llama4", "meta"),
    ("omp_gateway:ollama-cloud/gpt-oss:20b", "openai"),
    ("omp_gateway:ollama/gemma4:31b-cloud", "google"),
    ("omp_gateway:openai-codex/o4-mini", "openai"),  # host rule: the openai hosts serve OpenAI's models
    ("omp_gateway:openai/o1-mini", "openai"),
    # fail closed: unknown beats wrong
    ("omp_gateway:some-host/llama4", None),  # a host that is not known to serve first-party or library models
    ("omp_gateway:some-host/gpt-6-luna", None),
    ("omp_gateway:some-host/claude-haiku-5-5", None),
    ("omp_gateway:ollama-cloud/o1-mini-distill", None),  # no generic o-series family
    ("omp_gateway:ollama-cloud/o4-mini", None),
    ("omp_gateway:ollama-cloud/gpt-j", None),
    ("omp_gateway:ollama-cloud/gpt-neox", None),
    ("omp_gateway:apple/on-device", None),
    ("omp_gateway:gpt-6-luna", None),  # no host
    # host/org/model: the org must be the family's developer
    ("omp_gateway:openrouter/anthropic/claude-sonnet-5", "anthropic"),
    ("omp_gateway:openrouter/deepseek-ai/deepseek-v3.2", "deepseek"),
    ("omp_gateway:openrouter/meta-llama/llama-4-scout", "meta"),
    ("omp_gateway:openrouter/qwen/qwen3-coder", "alibaba"),
    ("omp_gateway:openrouter/mistralai/mistral-large", "mistral"),
    ("omp_gateway:openrouter/moonshotai/kimi-k2", "moonshot"),
    ("omp_gateway:openrouter/thudm/glm-4", "zhipu"),
    ("omp_gateway:ollama/someuser/claude-3-distill", None),
    ("omp_gateway:openrouter/someuser/deepseek-r1-merge", None),
    ("omp_gateway:openrouter/anthropic/gpt-6", None),  # the family's developer is not the org
    ("ollama:deepseek-v4.1-flash:cloud", "deepseek"),
    ("ollama:kimi-k3:cloud", "moonshot"),
    ("ollama:glm-5:cloud", "zhipu"),
    ("ollama:gpt-oss:120b-cloud", "openai"),
    ("ollama:gpt-oss:20b", "openai"),
    ("ollama:gpt-j", None),
    ("ollama:gpt4all", None),  # Nomic's, not OpenAI's: OpenAI ids are gpt-N or gpt-oss
    ("omp_gateway:ollama-cloud/gpt4all:13b", None),
    ("ollama:gpt-neox:20b", None),
    ("ollama:o1-mini-distill", None),
    ("ollama:someuser/claude-3-distill", None),  # namespaced uploads are unknown
    ("ollama:someuser/kimi-k3", None),
    ("ollama:hf.co/org/qwen3-gguf", None),
    ("ollama:lfm3:latest", None),
    ("claude_cli:gpt-6-luna", "anthropic"),  # host rules come first: claude_cli runs Anthropic's models only
])
def test_family_vendor_rules(driver, vendor):
    spec = DriverSpec.parse(driver)
    assert vendor_of(spec.provider, spec.model) == vendor


def test_unknown_vendor_cannot_judge():
    unknown = "omp_gateway:foo/unknown-model"
    problems = validate_drivers({"judge": unknown})
    assert len(problems) == 1 and "judge" in problems[0] and "unknown vendor" in problems[0] and unknown in problems[0]
    assert validate_drivers({"writer": unknown, "reader": unknown}) == []  # it can still generate
    model = next(m for m in list_models(Settings(drivers={"writer": unknown}), omp_runner=omp_ok,
                                        ollama_runner=no_ollama) if m.driver == unknown)
    assert model.vendor is None


def test_validate_drivers_rejects_duplicate_judge_vendors():
    openai = {"judge": "codex_cli:gpt-6-luna", "judge_2": "omp_gateway:openai-codex/gpt-6-luna",
              "judge_3": "openai_api:gpt-6-sol"}
    problems = validate_drivers(openai)
    assert len(problems) == 1 and "two judges of different known vendors" in problems[0]
    # two openai judges and an anthropic one: two distinct vendors, so valid; but the default Anthropic writer leaves
    # only one judge to check its claims
    two = {**openai, "judge_3": "claude_cli:claude-haiku-5-5"}
    assert validate_drivers(two) == []
    warnings = driver_warnings(two)
    writer = [w for w in warnings if "writer" in w]
    assert len(writer) == 1 and "1 judge" in writer[0]
    assert writer[0].startswith("lead, arguer and writer (claude_cli) are anthropic")  # one line for a shared driver
    assert driver_warnings({}) == []  # the defaults: any one generator vendor leaves two judges


def test_validate_drivers_other_problems():
    problems = validate_drivers({"reader": "nonsense:model", "editor": "claude_cli"})
    assert len(problems) == 2  # the vendor rules run once every driver parses
    assert any(p.startswith("reader:") and "unknown provider" in p for p in problems)
    assert any("unknown role 'editor'" in p for p in problems)
    problems = validate_drivers({"screener_b": "claude_cli:claude-sonnet-5-5"})
    assert len(problems) == 1 and "screener_a and screener_b of two different known vendors" in problems[0]


@pytest.mark.parametrize("text", ["codex_cli:-c", "codex_cli:--config=x", "claude_cli:-", "ollama:a b",
                                  "ollama:a\tb", "ollama:a\nb", "claude_cli:x\x00", "omp_gateway:a/\x1b[31m"])
def test_driver_spec_rejects_unsafe_model_ids(text):
    with pytest.raises(ValueError, match="model"):
        DriverSpec.parse(text)
    assert validate_drivers({"writer": text})  # refused as a choice too


def test_omp_gateway_needs_no_flag(tmp_home):
    config_path().write_text('email = "rk-fixtures@users.noreply.github.com"\nomp_gateway = false\n'
                             '[drivers]\nreader = "omp_gateway:openai-codex/gpt-6-luna"\n')
    s = load_settings()
    assert s.drivers["reader"] == "omp_gateway:openai-codex/gpt-6-luna" and not hasattr(s, "omp_gateway")
    # available whenever omp is on PATH (here the test stand-in; availability never runs it)
    assert build_provider(DriverSpec.parse(s.drivers["reader"]), s).available().ok
    save_settings(s)
    assert "omp_gateway" not in tomllib.loads(config_path().read_text())


def test_cli_models_json_with_a_failing_listing_and_unknown_vendor(tmp_home, monkeypatch):
    def missing(argv, timeout):
        raise FileNotFoundError("omp")

    monkeypatch.setattr(catalog, "default_omp_runner", missing)
    monkeypatch.setattr(catalog, "default_ollama_runner", no_ollama)
    runner = CliRunner()
    r = runner.invoke(app, ["models", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert set(out) == {"models", "omp_status"} and "omp is not installed" in out["omp_status"]
    assert "claude_cli:claude-haiku-5-5" in {m["driver"] for m in out["models"]}  # the other drivers still listed
    assert any(x.startswith("omp: ") and "omp is not installed" in x
               for x in runner.invoke(app, ["models"]).output.splitlines())

    catalog.clear_cache()
    listing = json.dumps({"models": [{"kind": "chat", "selector": "foo/unknown-model", "id": "unknown-model"}]})
    monkeypatch.setattr(catalog, "default_omp_runner", lambda argv, t: listing)
    line = next(x for x in runner.invoke(app, ["models"]).output.splitlines()
                if x.startswith("omp_gateway:foo/unknown-model"))
    assert "vendor=?" in line and "cannot judge" in line  # as the Settings page says
    assert "cannot judge" not in next(x for x in runner.invoke(app, ["models"]).output.splitlines()
                                      if x.startswith("claude_cli:claude-haiku-5-5"))


def test_cli_models_and_driver_set(tmp_home, monkeypatch):
    monkeypatch.setattr(catalog, "default_omp_runner", omp_ok)
    monkeypatch.setattr(catalog, "default_ollama_runner", no_ollama)
    runner = CliRunner()
    r = runner.invoke(app, ["models", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert {m["driver"]: m["vendor"] for m in out["models"] if m["provider"] == "omp_gateway"} == GATEWAY
    assert "omp_status" in out and out["omp_status"] is None
    assert "omp_gateway:openai-codex/gpt-6-luna" in runner.invoke(app, ["models"]).output

    r = runner.invoke(app, ["driver", "set", "judge", "omp_gateway:openai-codex/gpt-6-luna"])
    assert r.exit_code == 0, r.output
    assert load_settings().drivers["judge"] == "omp_gateway:openai-codex/gpt-6-luna"
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600

    r = runner.invoke(app, ["driver", "set", "judge_2", "omp_gateway:foo/unknown-model"])
    assert r.exit_code == 1 and "unknown vendor" in r.output
    assert load_settings().drivers["judge_2"] == "ollama:deepseek-v4.1-flash:cloud"  # nothing saved

    r = runner.invoke(app, ["driver", "set", "judge_3", "omp_gateway:anthropic/claude-sonnet-5-5"])
    assert r.exit_code == 0 and "warning" not in r.output  # still anthropic: two judges remain for any writer
    r = runner.invoke(app, ["driver", "set", "judge_2", "omp_gateway:openai-codex/gpt-6-sol"])
    assert r.exit_code == 0 and "warning: lead, arguer and writer (claude_cli)" in r.output  # saved, with the warning


def test_rk_providers_reports_problems_and_warnings(tmp_home, replay_http, monkeypatch):
    from rk.tools import builtin
    from rk.tools.registry import CallContext
    from rk.workspace import Kernel

    monkeypatch.setattr(builtin, "available_all", lambda s: {})  # availability runs the CLIs; not this test's subject
    k = Kernel(tmp_home, http=replay_http)
    reg = builtin.build_registry(k)
    try:
        save_settings(Settings(drivers={"judge_2": "codex_cli:gpt-6-sol"}))
        out = reg.call("rk_providers", {}, CallContext(role="cli", actor="u"))
        assert out["problems"] == [] and any("writer (claude_cli) are anthropic" in w for w in out["warnings"])
        save_settings(Settings(drivers={"judge": "omp_gateway:foo/unknown-model"}))
        out = reg.call("rk_providers", {}, CallContext(role="cli", actor="u"))
        assert any("unknown vendor" in p for p in out["problems"]) and out["warnings"] == []
    finally:
        k.close()
