"""``chat_api`` drivers: any ChatBackend driven by the in-process tool loop."""

from __future__ import annotations

from rk.agent.loop import run_tool_loop
from rk.llm.chat.base import Msg, ToolDef, Turn
from rk.llm.types import Availability, ProviderError, RunContext, TaskResult, TaskSpec
from rk.llm.vendors import vendor_of
from rk.roles.prompts import build_system_prompt


class DisabledBackend:
    """Stands in for a backend that is configured off; reports why instead of failing obscurely."""

    def __init__(self, name: str, model: str, reason: str):
        self.name, self.model, self.reason = name, model, reason

    def available(self) -> Availability:
        return Availability(ok=False, reason=self.reason)

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn:
        raise ProviderError(self.reason)


class ChatApiProvider:
    kind = "chat_api"

    def __init__(self, backend, provider_name: str):
        self.backend = backend
        self.name = provider_name
        self.model = backend.model
        self.vendor = vendor_of(provider_name, backend.model)

    def available(self) -> Availability:
        return self.backend.available()

    def actor(self, role: str) -> str:
        return f"{role}@{self.name}:{self.model}"

    def run(self, spec: TaskSpec, ctx: RunContext) -> TaskResult:
        return run_tool_loop(self.backend, spec, ctx.registry, actor=self.actor(spec.role),
                             system=build_system_prompt(spec.role, spec.output_schema), provider=self.name)
