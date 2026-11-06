import json
import shutil
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from rk.config import Settings, save_settings
from rk.llm.chat.base import ToolCall, Turn
from rk.llm.chat.omp_gateway import OmpGatewayBackend
from rk.llm.chat_provider import ChatApiProvider
from rk.llm.cli_claude import ClaudeCliProvider
from rk.llm.cli_codex import CodexCliProvider
from rk.llm.types import ChatEvent, ChatSpec, RunContext
from rk.server.chat import ChatManager, _Open

from .test_agent_loop import ScriptedBackend

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "fakecli"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    return str(exe), lambda: [json.loads(line) for line in record.read_text().splitlines()]


SPEC = ChatSpec(role="lead", project="pj", system="SYS", tools=["rk_text_find"])
CTX = RunContext(kernel=None, registry=None, task_token="tok-s")


def test_claude_session_resume_argv(fake, monkeypatch):
    exe, records = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    s = ClaudeCliProvider(Settings(cli_paths={"claude": exe})).open_chat(SPEC, CTX)
    try:
        first = list(s.send("hello"))
        second = list(s.send("again"))
    finally:
        s.close()
    assert [e.kind for e in first] == ["tool_call", "tool_result", "text", "done"]
    assert first[0].data["name"] == "mcp__rk__rk_text_find" and first[2].data["text"] == "turn resumed=False"
    assert second[2].data["text"] == "turn resumed=True" and s.session_id == "sess-1"
    a1, a2 = (r["argv"] for r in records())
    assert "--resume" not in a1 and a2[a2.index("--resume") + 1] == "sess-1"
    assert "stream-json" in a1 and a1[a1.index("--tools") + 1] == "" and "--no-session-persistence" not in a1
    assert records()[0]["cwd"] == records()[1]["cwd"] and not Path(records()[0]["cwd"]).exists()


def test_codex_session_resume_argv(fake, monkeypatch):
    exe, records = fake
    monkeypatch.setenv("FAKE_KIND", "codex")
    s = CodexCliProvider(Settings(cli_paths={"codex": exe})).open_chat(SPEC, CTX)
    try:
        first = list(s.send("hello"))
        second = list(s.send("again"))
    finally:
        s.close()
    assert [e.kind for e in first] == ["tool_result", "text", "done"]  # fake emits completed tool items only
    assert first[1].data["text"] == "resumed=False" and second[1].data["text"] == "resumed=True"
    a1, a2 = (r["argv"] for r in records())
    assert a1[:2] == ["exec", "--json"] and a2[:3] == ["exec", "resume", "thread-1"]
    for a in (a1, a2):
        assert 'sandbox_mode="read-only"' in a and "--ignore-user-config" in a and "--ephemeral" not in a
        assert {a[i + 1] for i, x in enumerate(a) if x == "--disable"} >= {"shell_tool", "unified_exec"}
    assert a1[-1].startswith("SYS") and a2[-1] == "again"  # system prompt only on the first turn


def test_cli_session_reports_exit_error(fake, monkeypatch):
    exe, _ = fake
    monkeypatch.setenv("FAKE_KIND", "codex")
    monkeypatch.setenv("FAKE_MODE", "exit1")
    s = CodexCliProvider(Settings(cli_paths={"codex": exe})).open_chat(SPEC, CTX)
    events = list(s.send("hi"))
    s.close()
    assert events[-1].kind == "error" and "exited 1" in events[-1].data["message"]


def test_chat_api_session_keeps_history(tmp_home, replay_http):
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    save_settings(Settings())
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    k.create_project("pj", "P", actor="t")
    b = ScriptedBackend([
        Turn(text="first answer"),
        Turn(tool_calls=[ToolCall(id="c1", name="rk_library_list", arguments={})]),
        Turn(text="second answer"),
    ])
    spec = ChatSpec(role="lead", project="pj", system="SYS", tools=["rk_library_list"])
    s = ChatApiProvider(b, "scripted").open_chat(spec, RunContext(kernel=k, registry=reg))
    e1 = list(s.send("one"))
    e2 = list(s.send("two"))
    k.close()
    assert [e.kind for e in e1] == ["text", "done"]
    assert [e.kind for e in e2] == ["tool_call", "tool_result", "text", "done"]
    assert e2[1].data["content"]["result"]["items"] == []
    assert [m.content for m in b.seen[1] if m.role == "user"] == ["one", "two"]  # history carried into turn 2
    assert any(m.role == "assistant" and m.content == "first answer" for m in b.seen[1])


def test_server_chat_endpoints_with_fake_claude(fake, monkeypatch, tmp_home, live_server):
    exe, records = fake
    monkeypatch.setenv("FAKE_KIND", "claude")
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", cli_paths={"claude": exe}))
    live_server.call("rk_project_create", {"slug": "chatp", "title": "Chat"}, write=True)
    chat_id = live_server.chat_open("claude_cli", "lead", "chatp")
    events = list(live_server.chat_send(chat_id, "hello"))
    assert [e["kind"] for e in events] == ["tool_call", "tool_result", "text", "done"]
    a = records()[0]["argv"]
    mcp = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["rk"]
    token = mcp["env"]["RK_TASK_TOKEN"]
    assert live_server.kernel.tokens.lookup(token).project == "chatp"
    live_server.chat_close(chat_id)
    assert live_server.kernel.tokens.lookup(token) is None  # session token dies with the session
    bad = list(live_server.chat_send(chat_id, "after close"))
    assert bad[0]["kind"] == "error"


def test_chat_close_waits_for_the_running_turn():
    """Closing a chat mid-turn takes it out of use at once, but its session (working directory) and token go only
    after the turn ends, so a closed or abandoned chat never pulls them out from under a running CLI."""
    revoked = []
    mgr = ChatManager(SimpleNamespace(tokens=SimpleNamespace(revoke=revoked.append)), None)
    started, finish, closed = threading.Event(), threading.Event(), threading.Event()

    class Session:
        def send(self, text):
            started.set()
            finish.wait(5)
            yield ChatEvent("done", {})

        def close(self):
            closed.set()

    mgr._sessions["c1"] = _Open(Session(), threading.Lock(), "fake")
    events = []
    turn = threading.Thread(target=lambda: events.extend(mgr.send("c1", "hi")))
    turn.start()
    assert started.wait(5)
    assert mgr.close("c1")
    assert not closed.is_set() and revoked == []  # the turn is still running
    with pytest.raises(KeyError):
        list(mgr.send("c1", "again"))
    finish.set()
    turn.join(5)
    assert closed.wait(5) and revoked == ["c1"] and [e.kind for e in events] == ["done"]
    assert not mgr.close("c1")


# ---------------- live: memory across turns on each driver family ----------------

def _driver(name: str):
    if name == "omp_gateway":
        if shutil.which("omp") is None:
            pytest.skip("omp not installed")
        return ChatApiProvider(OmpGatewayBackend("openai-codex/gpt-6-luna"), "omp_gateway")
    binary = {"claude_cli": "claude", "codex_cli": "codex"}[name]
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} not installed")
    return (ClaudeCliProvider if name == "claude_cli" else CodexCliProvider)(Settings())


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.parametrize("name", ["claude_cli", "codex_cli", "omp_gateway"])
def test_session_memory(name):
    p = _driver(name)
    s = p.open_chat(ChatSpec(role="lead", project=None, system="You are a concise assistant.", tools=[]),
                    RunContext(kernel=None, registry=None))
    try:
        e1 = list(s.send("Remember the code word PLUM-417. Reply only with OK."))
        e2 = list(s.send("What was the code word? Reply with just the code word."))
    finally:
        s.close()
    assert e1[-1].kind == "done", e1
    text = " ".join(e.data.get("text", "") for e in e2 if e.kind == "text")
    assert "PLUM-417" in text, (e2, s.session_id)
