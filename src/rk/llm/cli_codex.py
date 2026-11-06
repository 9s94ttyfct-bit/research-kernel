"""``codex_cli``: ``codex exec`` read-only, shell/apps/browser features disabled, Kernel tools via ``rk mcp``."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from pydantic import ValidationError

from rk.llm.cli_base import AgenticCli, mcp_server_spec
from rk.llm.jsonout import extract_json
from rk.llm.types import ChatSpec, RunContext, TaskResult, TaskSpec
from rk.llm.vendors import vendor_of
from rk.roles.prompts import build_system_prompt, render_inputs

# Verified 2026-11-05 (codex-cli 0.166.0-alpha.6): with these disabled, codex cannot read files in its cwd.
DISABLED_FEATURES = ("shell_tool", "unified_exec", "apps", "browser_use", "in_app_browser")


def strict_schema(schema: dict) -> dict:
    """codex --output-schema needs every object closed (additionalProperties: false) with all properties required;
    optional fields stay nullable through their anyOf [T, null]. ``default`` is not allowed there."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


def _toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string literal is a valid TOML basic string


def mcp_overrides(project: str, role: str, token: str) -> list[str]:
    spec = mcp_server_spec(project, role, token)
    env = ",".join(f"{k}={_toml_str(v)}" for k, v in spec["env"].items())
    # exec runs with approval policy "never", which blocks MCP calls that need approval; rk's tools are already
    # restricted by the task token, so they are pre-approved (verified 2026-11-05, codex-cli 0.166.0-alpha.6).
    return ["-c", f"mcp_servers.rk.command={_toml_str(spec['command'])}",
            "-c", f"mcp_servers.rk.args={json.dumps(spec['args'])}",
            "-c", f"mcp_servers.rk.env={{{env}}}",
            "-c", 'mcp_servers.rk.default_tools_approval_mode="approve"']


def parse_events(stdout: str) -> dict:
    """thread id, tool-call count and agent messages from ``codex exec --json`` JSONL."""
    out: dict = {"thread_id": None, "tool_calls": 0, "messages": [], "error": None}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        item = ev.get("item") or {}
        if t == "thread.started":
            out["thread_id"] = ev.get("thread_id")
        elif t == "item.completed" and item.get("type") == "agent_message":
            out["messages"].append(item.get("text", ""))
        elif t == "item.completed" and item.get("type") == "mcp_tool_call":
            out["tool_calls"] += 1
        elif t in ("error", "turn.failed"):
            out["error"] = str(ev.get("message") or ev.get("error") or ev)[:500]
    return out


class CodexCliProvider(AgenticCli):
    name = "codex_cli"
    binary = "codex"
    pinned = "0.166.0-alpha.6"

    @property
    def vendor(self) -> str | None:
        return vendor_of(self.name, self.model)

    @staticmethod
    def parse_version(text: str) -> str:
        return text.split()[-1] if text else ""

    def base_flags(self, cwd: Path, project: str | None, role: str, tools: list[str], token: str | None) -> list[str]:
        # --ignore-user-config: the user's own MCP servers, profiles and settings never reach a role task.
        flags = ["--strict-config", "--ignore-user-config", "--sandbox", "read-only", "--skip-git-repo-check",
                 "-C", str(cwd)]
        for f in DISABLED_FEATURES:
            flags += ["--disable", f]
        if tools and token and project:
            flags += mcp_overrides(project, role, token)
        if self.model:
            flags += ["-m", self.model]
        return flags

    def prompt(self, spec: TaskSpec) -> str:
        return (f"{build_system_prompt(spec.role, None)}\n\n## Task\n\n{spec.instructions}\n\n"
                f"{render_inputs(spec.inputs)}").strip()

    def argv(self, spec: TaskSpec, cwd: Path, token: str | None) -> list[str]:
        schema_file = cwd / "output-schema.json"
        schema_file.write_text(json.dumps(strict_schema(spec.output_schema.model_json_schema())))
        return [self.bin_path() or self.binary, "exec", "--json", "--output-schema", str(schema_file), "--ephemeral",
                "-o", str(cwd / "last-message.txt"),
                *self.base_flags(cwd, spec.project, spec.role, spec.tools, token), self.prompt(spec)]

    def run(self, spec: TaskSpec, ctx: RunContext) -> TaskResult:
        if self.bin_path() is None:
            return TaskResult(spec.task_id, False, None, "", self.name, self.model, 0.0, "codex not found on PATH")
        cwd = self.workdir()
        try:
            done = self.execute(self.argv(spec, cwd, ctx.task_token), cwd, spec.timeout_s)
            last_file = cwd / "last-message.txt"
            last = last_file.read_text() if last_file.exists() else ""
        finally:
            self.cleanup(cwd)
        ev = parse_events(done.stdout)
        raw = last or (ev["messages"][-1] if ev["messages"] else "")

        def result(ok: bool, output=None, error: str | None = None) -> TaskResult:
            return TaskResult(spec.task_id, ok, output, raw, self.name, self.model, done.duration_s, error,
                              ev["tool_calls"], ev["thread_id"])

        if done.code is None:
            return result(False, error="timeout")
        if done.code != 0:
            return result(False, error=f"exit {done.code}: {ev['error'] or done.stderr[-500:]}")
        obj = extract_json(raw)
        if obj is None:
            return result(False, error="invalid_output: no JSON object")
        try:
            return result(True, output=spec.output_schema.model_validate(obj))
        except ValidationError as e:
            return result(False, error=f"invalid_output: {e.errors(include_url=False)[:3]}")

    def open_chat(self, spec: ChatSpec, ctx: RunContext):
        from rk.llm.sessions import CodexCliSession

        return CodexCliSession(self, spec, ctx)
