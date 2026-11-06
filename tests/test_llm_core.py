import pytest
from pydantic import BaseModel, ValidationError

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
