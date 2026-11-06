"""Multi-turn sessions for agentic CLIs (spec §8.2): Claude resumes with ``--resume <session_id>`` and streams
``stream-json``; Codex resumes with ``codex exec resume <thread_id>``. Each session keeps one temp working directory
for its whole life (Claude keys stored sessions by cwd) and removes it on ``close``."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from collections.abc import Iterator

from rk.llm.cli_codex import DISABLED_FEATURES, mcp_overrides
from rk.llm.types import ChatEvent, ChatSpec, RunContext

TURN_TIMEOUT_S = 900


def _stream(argv: list[str], cwd) -> Iterator[dict]:
    """Run one CLI turn, yielding its JSON lines; the process group is killed if the turn overruns."""
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    timer = threading.Timer(TURN_TIMEOUT_S, lambda: os.killpg(proc.pid, signal.SIGKILL))
    timer.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        proc.wait()
        if proc.returncode != 0:
            yield {"type": "_exit", "code": proc.returncode, "stderr": proc.stderr.read()[-500:]}
    finally:
        timer.cancel()
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)


class ClaudeCliSession:
    def __init__(self, provider, spec: ChatSpec, ctx: RunContext):
        self.p, self.spec, self.ctx = provider, spec, ctx
        self.cwd = provider.workdir()
        self.session_id: str | None = None

    def argv(self, text: str) -> list[str]:
        a = [self.p.bin_path() or self.p.binary, "-p", text, "--output-format", "stream-json", "--verbose",
             *self.p.tool_flags(self.spec.project, self.spec.role, self.spec.tools, self.ctx.task_token),
             "--append-system-prompt", self.spec.system]
        if self.session_id:
            a += ["--resume", self.session_id]
        if self.p.model:
            a += ["--model", self.p.model]
        return a

    def send(self, user_message: str) -> Iterator[ChatEvent]:
        done = False
        for ev in _stream(self.argv(user_message), self.cwd):
            t = ev.get("type")
            if t == "system" and ev.get("session_id"):
                self.session_id = ev["session_id"]
            elif t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text"):
                        yield ChatEvent("text", {"text": b["text"]})
                    elif b.get("type") == "tool_use":
                        yield ChatEvent("tool_call", {"id": b.get("id"), "name": b.get("name"),
                                                      "arguments": b.get("input") or {}})
            elif t == "user":
                for b in ev.get("message", {}).get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        yield ChatEvent("tool_result", {"id": b.get("tool_use_id"), "content": b.get("content")})
            elif t == "result":
                done = True
                self.session_id = ev.get("session_id") or self.session_id
                if ev.get("subtype") != "success" or ev.get("is_error"):
                    yield ChatEvent("error", {"message": f"claude {ev.get('subtype')}"})
                yield ChatEvent("done", {"session_id": self.session_id})
            elif t == "_exit" and not done:
                yield ChatEvent("error", {"message": f"claude exited {ev['code']}: {ev['stderr']}"})

    def close(self) -> None:
        self.p.cleanup(self.cwd)


class CodexCliSession:
    def __init__(self, provider, spec: ChatSpec, ctx: RunContext):
        self.p, self.spec, self.ctx = provider, spec, ctx
        self.cwd = provider.workdir()
        self.session_id: str | None = None

    def flags(self) -> list[str]:
        # `exec resume` has no --sandbox/-C: the sandbox is set through config and cwd through the process itself.
        f = ["--json", "--strict-config", "--ignore-user-config", "-c", 'sandbox_mode="read-only"',
             "--skip-git-repo-check"]
        for feat in DISABLED_FEATURES:
            f += ["--disable", feat]
        if self.spec.tools and self.ctx.task_token and self.spec.project:
            f += mcp_overrides(self.spec.project, self.spec.role, self.ctx.task_token)
        if self.p.model:
            f += ["-m", self.p.model]
        return f

    def argv(self, text: str) -> list[str]:
        exe = self.p.bin_path() or self.p.binary
        if self.session_id:
            return [exe, "exec", "resume", self.session_id, *self.flags(), text]
        return [exe, "exec", *self.flags(), f"{self.spec.system}\n\n## Conversation\n\n{text}"]

    def send(self, user_message: str) -> Iterator[ChatEvent]:
        done = False
        for ev in _stream(self.argv(user_message), self.cwd):
            t = ev.get("type")
            item = ev.get("item") or {}
            if t == "thread.started":
                self.session_id = ev.get("thread_id")
            elif t == "item.started" and item.get("type") == "mcp_tool_call":
                yield ChatEvent("tool_call", {"id": item.get("id"), "name": item.get("tool"),
                                              "arguments": item.get("arguments") or {}})
            elif t == "item.completed" and item.get("type") == "mcp_tool_call":
                yield ChatEvent("tool_result", {"id": item.get("id"), "status": item.get("status"),
                                                "content": item.get("result"), "error": item.get("error")})
            elif t == "item.completed" and item.get("type") == "agent_message":
                yield ChatEvent("text", {"text": item.get("text", "")})
            elif t == "turn.completed":
                done = True
                yield ChatEvent("done", {"session_id": self.session_id})
            elif t in ("error", "turn.failed"):
                yield ChatEvent("error", {"message": str(ev.get("message") or ev.get("error") or ev)[:500]})
            elif t == "_exit" and not done:
                yield ChatEvent("error", {"message": f"codex exited {ev['code']}: {ev['stderr']}"})

    def close(self) -> None:
        self.p.cleanup(self.cwd)
