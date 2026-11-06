"""``claude_cli``: Claude Code in print mode, built-in tools disabled, Kernel tools via ``rk mcp`` (spec §8.2)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from rk.llm.cli_base import AgenticCli, mcp_server_spec
from rk.llm.types import ChatSpec, RunContext, TaskResult, TaskSpec
from rk.llm.vendors import vendor_of
from rk.roles.prompts import build_system_prompt, render_inputs

NO_MCP = json.dumps({"mcpServers": {}})


class ClaudeCliProvider(AgenticCli):
    name = "claude_cli"
    binary = "claude"
    pinned = "2.1.309"

    @property
    def vendor(self) -> str | None:
        return vendor_of(self.name, self.model)

    @staticmethod
    def parse_version(text: str) -> str:
        return text.split()[0] if text else ""

    def tool_flags(self, spec_project: str | None, role: str, tools: list[str], token: str | None) -> list[str]:
        # --tools "" removes every built-in tool (Bash, Read, Write, WebFetch, ...); only rk's MCP tools remain.
        # --setting-sources "": the user's hooks, plugins and permission rules never apply to a role task.
        flags = ["--tools", "", "--setting-sources", "", "--strict-mcp-config"]
        if tools and token and spec_project:
            flags += ["--mcp-config", json.dumps({"mcpServers": {"rk": mcp_server_spec(spec_project, role, token)}}),
                      "--allowedTools", "mcp__rk__*"]
        else:
            flags += ["--mcp-config", NO_MCP]
        return flags

    def argv(self, spec: TaskSpec, token: str | None) -> list[str]:
        prompt = f"{spec.instructions}\n\n{render_inputs(spec.inputs)}".strip()
        argv = [self.bin_path() or self.binary, "-p", prompt, "--output-format", "json",
                "--json-schema", json.dumps(spec.output_schema.model_json_schema()),
                "--max-turns", str(spec.max_turns), "--no-session-persistence",
                *self.tool_flags(spec.project, spec.role, spec.tools, token),
                "--append-system-prompt", build_system_prompt(spec.role, None)]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def run(self, spec: TaskSpec, ctx: RunContext) -> TaskResult:
        if self.bin_path() is None:
            return TaskResult(spec.task_id, False, None, "", self.name, self.model, 0.0, "claude not found on PATH")
        cwd = self.workdir()
        try:
            done = self.execute(self.argv(spec, ctx.task_token), cwd, spec.timeout_s)
        finally:
            self.cleanup(cwd)

        def fail(error: str, raw: str = done.stdout, **kw) -> TaskResult:
            return TaskResult(spec.task_id, False, None, raw, self.name, kw.get("model", self.model), done.duration_s,
                              error, kw.get("tool_calls", 0), kw.get("session_id"))

        if done.code is None:
            return fail("timeout")
        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError:
            return fail(f"exit {done.code}: {(done.stderr or done.stdout)[-500:]}")
        model = next(iter(data.get("modelUsage") or {}), None) or self.model
        calls = max(int(data.get("num_turns") or 1) - 1, 0)
        meta = {"model": model, "tool_calls": calls, "session_id": data.get("session_id")}
        if done.code != 0 or data.get("is_error") or data.get("subtype") != "success":
            return fail(f"claude {data.get('subtype') or 'error'} (exit {done.code})", raw=str(data.get("result", "")),
                        **meta)
        structured = data.get("structured_output")
        if structured is None:
            return fail("no structured_output", raw=str(data.get("result", "")), **meta)
        try:
            output = spec.output_schema.model_validate(structured)
        except ValidationError as e:
            return fail(f"invalid_output: {e.errors(include_url=False)[:3]}", raw=json.dumps(structured), **meta)
        return TaskResult(spec.task_id, True, output, json.dumps(structured), self.name, model, done.duration_s, None,
                          calls, data.get("session_id"))

    def open_chat(self, spec: ChatSpec, ctx: RunContext):
        from rk.llm.sessions import ClaudeCliSession

        return ClaudeCliSession(self, spec, ctx)
