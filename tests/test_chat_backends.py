import json
import shutil
import stat
import sys
from pathlib import Path

import httpx
import pytest

from rk.llm.chat.anthropic import AnthropicBackend
from rk.llm.chat.base import Msg, ToolCall, ToolDef
from rk.llm.chat.ollama import OllamaBackend
from rk.llm.chat.omp_gateway import OmpGatewayBackend
from rk.llm.chat.openai_chat import OpenAIChatBackend
from rk.llm.limits import retry_after
from rk.llm.types import ProviderError

PING = ToolDef(name="rk_ping", description="ping", parameters={"type": "object", "properties": {"x": {"type": "integer"}},
                                                               "required": ["x"]})
CONVO = [
    Msg(role="system", content="sys"),
    Msg(role="user", content="call ping"),
    Msg(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="rk_ping", arguments={"x": 7})]),
    Msg(role="tool", content='{"pong": 7}', tool_call_id="c1", name="rk_ping"),
]


def _client(handler, seen: list):
    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    return httpx.Client(transport=httpx.MockTransport(wrapped))


def test_openai_tool_roundtrip():
    seen: list = []
    resp = {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c2", "type": "function", "function": {"name": "rk_ping", "arguments": '{"x": 8}'}}]}}], "usage": {}}
    b = OpenAIChatBackend("sk", "m", "https://api.example/v1", _client(lambda r: httpx.Response(200, json=resp), seen))
    t = b.complete(CONVO, [PING], None)
    assert t.tool_calls == [ToolCall(id="c2", name="rk_ping", arguments={"x": 8})]
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"x": 7}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"pong": 7}'}
    assert body["tools"][0]["function"]["name"] == "rk_ping" and "response_format" not in body


def test_openai_json_schema_and_error():
    seen: list = []
    b = OpenAIChatBackend("sk", "m", "https://api.example/v1",
                          _client(lambda r: httpx.Response(400, json={"error": {"message": "bad model"}}), seen))
    with pytest.raises(ProviderError, match="bad model"):
        b.complete([Msg(role="user", content="hi")], [], {"type": "object"})
    assert json.loads(seen[0].content)["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_anthropic_tool_roundtrip():
    seen: list = []
    resp = {"model": "claude-x", "content": [{"type": "text", "text": "ok"},
                                             {"type": "tool_use", "id": "t9", "name": "rk_ping", "input": {"x": 9}}],
            "usage": {"input_tokens": 1}}
    b = AnthropicBackend("ak", "claude-x", _client(lambda r: httpx.Response(200, json=resp), seen))
    t = b.complete(CONVO, [PING], None)
    assert t.text == "ok" and t.tool_calls[0].arguments == {"x": 9}
    body = json.loads(seen[0].content)
    assert body["system"] == "sys" and all(m["role"] != "system" for m in body["messages"])
    assert body["messages"][1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "rk_ping", "input": {"x": 7}}
    assert body["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": '{"pong": 7}'}]}
    assert body["tools"][0]["input_schema"] == PING.parameters
    assert seen[0].headers["x-api-key"] == "ak" and seen[0].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize(("model", "has_format"), [("lfm3:latest", True), ("deepseek-v4.1-flash:cloud", False)])
def test_ollama_format_only_for_local(model, has_format):
    seen: list = []
    resp = {"model": model, "message": {"role": "assistant", "content": '{"a": 1}'}, "done": True}
    b = OllamaBackend("http://127.0.0.1:11434", model, _client(lambda r: httpx.Response(200, json=resp), seen))
    assert b.complete([Msg(role="user", content="x")], [], {"type": "object"}).text == '{"a": 1}'
    assert ("format" in json.loads(seen[0].content)) is has_format


def test_ollama_tool_calls_and_messages():
    seen: list = []
    resp = {"model": "m", "message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "rk_ping", "arguments": {"x": 1}}}]}}
    b = OllamaBackend("http://h:1", "m", _client(lambda r: httpx.Response(200, json=resp), seen))
    t = b.complete(CONVO, [PING], None)
    assert t.tool_calls[0].name == "rk_ping" and t.tool_calls[0].arguments == {"x": 1} and t.tool_calls[0].id
    msgs = json.loads(seen[0].content)["messages"]
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == {"x": 7}
    assert msgs[3] == {"role": "tool", "content": '{"pong": 7}', "tool_name": "rk_ping"}


def test_ollama_quota_error_surfaces():
    err = {"error": "you (someone) have reached your monthly usage limit, upgrade for higher limits"}
    b = OllamaBackend("http://h:1", "deepseek-v4.1-flash:cloud", _client(lambda r: httpx.Response(429, json=err), []))
    with pytest.raises(ProviderError, match="monthly usage limit"):
        b.complete([Msg(role="user", content="x")], [], None)


def test_ollama_availability_reports_missing_model():
    tags = {"models": [{"name": "lfm3:latest", "model": "lfm3:latest"}]}
    b = OllamaBackend("http://h:1", "gemma4:12b-it-qat", _client(lambda r: httpx.Response(200, json=tags), []))
    a = b.available()
    assert not a.ok and "not pulled" in a.reason


@pytest.fixture
def fake_omp(tmp_path) -> str:
    exe = tmp_path / "omp"
    exe.write_text(f"#!{sys.executable}\n" + (Path(__file__).parent / "fakes" / "fake_omp.py").read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def test_omp_gateway_stdio_protocol(fake_omp, tmp_path, monkeypatch):
    user_overlay = tmp_path / "user.yml"
    user_overlay.write_text("retry:\n  modelFallback: true\n")
    monkeypatch.setenv("PI_CONFIG_FILES", str(user_overlay))  # the user's own overlay cannot turn fallback back on
    b = OmpGatewayBackend("openai-codex/gpt-6-luna", omp_bin=fake_omp)
    try:
        assert b.complete([Msg(role="user", content="hello")], [], None).text == "echo:hello"
        assert b.complete([Msg(role="user", content="OUT_OF_ORDER")], [], None).text == "echo:OUT_OF_ORDER"
        assert 1002 in b._pending  # the stray reply was kept, not consumed as ours
        assert b.complete([Msg(role="user", content="again")], [], None).model == "openai-codex/gpt-6-luna"
        with pytest.raises(ProviderError, match="401"):
            b.complete([Msg(role="user", content="FAIL")], [], None)
        # a rate limit with a long message: the error is clipped, but its retry-after-ms is kept whole for run_task's
        # wait, also when the cut would fall inside it (a cut "=21" would read as 21 ms)
        for content in ("CAPACITY", "CAPACITY_EDGE"):
            with pytest.raises(ProviderError, match="status 502") as e:
                b.complete([Msg(role="user", content=content)], [], None)
            assert retry_after(str(e.value)) == 21.0 and len(str(e.value)) < 600, content
        # the requested model failed: omp's model fallback is off in the gateway, so the call fails, not Sonnet answers
        with pytest.raises(ProviderError, match="401"):
            b.complete([Msg(role="user", content="FALLBACK")], [], None)
        # a reply from another vendor's model anyway: a failed call, never this driver's answer
        with pytest.raises(ProviderError, match="claude-sonnet-5-5 instead of openai-codex/gpt-6-luna"):
            b.complete([Msg(role="user", content="WRONGMODEL")], [], None)
        with pytest.raises(ProviderError, match="no model instead of openai-codex/gpt-6-luna"):
            b.complete([Msg(role="user", content="NOMODEL")], [], None)  # unidentifiable: fail closed
    finally:
        b.close()


def test_omp_gateway_missing_binary():
    assert not OmpGatewayBackend("m", omp_bin="/nonexistent/omp").available().ok


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.skipif(shutil.which("omp") is None, reason="omp not installed")
def test_omp_gateway_luna_tool_call():
    b = OmpGatewayBackend("openai-codex/gpt-6-luna")
    try:
        t = b.complete([Msg(role="user", content="Call rk_ping with x=7.")], [PING], None)
    finally:
        b.close()
    assert [(c.name, c.arguments) for c in t.tool_calls] == [("rk_ping", {"x": 7})]
