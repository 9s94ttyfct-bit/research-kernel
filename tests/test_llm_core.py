import pytest
from pydantic import BaseModel, ValidationError

from rk.config import Settings, api_key, load_settings, save_settings
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.jsonout import extract_json, parse_output
from rk.llm.vendors import require_cross_vendor, vendor_of


@pytest.mark.parametrize(("prov", "model", "vendor"), [
    ("claude_cli", None, "anthropic"),
    ("codex_cli", "gpt-6-luna", "openai"),
    ("omp_gateway", "openai-codex/gpt-6-luna", "openai"),
    ("omp_gateway", "anthropic/claude-haiku-4-5", "anthropic"),
    ("ollama", "deepseek-v4.1-flash:cloud", "deepseek"),
    ("ollama", "gemma4:31b-cloud", "google"),
    ("ollama", "lfm3:latest", None),
    ("omp_gateway", "somebody/new-model", None),
    ("human", None, "human"),
])
def test_vendor_of(prov, model, vendor):
    assert vendor_of(prov, model) == vendor


def test_require_cross_vendor():
    require_cross_vendor("anthropic", "openai")
    for g, r in [("openai", "openai"), ("anthropic", None), (None, "openai")]:
        with pytest.raises(ValueError):
            require_cross_vendor(g, r)


@pytest.mark.parametrize("text", [
    '{"a": 1}',
    'Sure:\n```json\n{"a": 1}\n```',
    'x {"a": 1} y {"b": 2}',
    '```\n{"a": {"n": "}"}}\n```',
    'broken {"a": } then {"a": 1}',
])
def test_extract_json_variants(text):
    assert extract_json(text) is not None


def test_extract_json_takes_first_valid_object():
    assert extract_json('x {"a": 1} y {"b": 2}') == {"a": 1}
    assert extract_json('broken {"a": } then {"a": 1}') == {"a": 1}
    assert extract_json('```\n{"a": {"n": "}"}}\n```') == {"a": {"n": "}"}}


def test_extract_json_none():
    assert extract_json("no json here") is None
    assert extract_json("[1, 2]") is None  # outputs are objects


def test_parse_output_validates():
    class O(BaseModel):
        a: int

    assert parse_output('```json\n{"a": 1}\n```', O).a == 1
    with pytest.raises(ValidationError):
        parse_output('{"a": "x"}', O)
    with pytest.raises(ValueError, match="no JSON object"):
        parse_output("nothing", O)



@pytest.mark.parametrize("text", ["claude_cli", "omp_gateway:openai-codex/gpt-6-luna",
                                  "ollama:deepseek-v4.1-flash:cloud", "anthropic_api:claude-x"])
def test_driver_spec_roundtrip(text):
    assert str(DriverSpec.parse(text)) == text


def test_driver_spec_splits_on_first_colon_and_rejects_unknown():
    assert DriverSpec.parse("ollama:deepseek-v4.1-flash:cloud") == DriverSpec("ollama", "deepseek-v4.1-flash:cloud")
    with pytest.raises(ValueError):
        DriverSpec.parse("gpt:4")


def test_api_drivers_require_model():
    with pytest.raises(ValueError, match="explicit model"):
        build_provider(DriverSpec.parse("openai_api"), Settings())


def test_env_keys_resolved_at_use_not_saved(tmp_home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    s = load_settings()
    assert api_key(s, "openai") == "sk-env" and s.openai_api_key is None
    save_settings(s)
    assert "sk-env" not in (tmp_home / "config.toml").read_text()
    assert api_key(Settings(openai_api_key="sk-file"), "openai") == "sk-file"  # file wins over env


def test_secrets_not_in_settings_repr():
    s = Settings(openai_api_key="sk-x", anthropic_api_key="sk-a", ollama_api_key="ok-o", openalex_key="oa")
    assert not any(k in repr(s) for k in ("sk-x", "sk-a", "ok-o", "oa'"))


def test_default_drivers_cover_roles_and_settings_roundtrip(tmp_home):
    s = Settings()
    assert s.drivers["judge"] == "codex_cli" and s.drivers["reader"] == "claude_cli" and not s.omp_gateway
    s.drivers["reader"] = "omp_gateway:openai-codex/gpt-6-luna"
    save_settings(s)
    assert load_settings().drivers["reader"] == "omp_gateway:openai-codex/gpt-6-luna"