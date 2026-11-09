"""``chat_api`` drivers: any ChatBackend driven by the in-process tool loop."""

from __future__ import annotations

import uuid

from rk.agent.loop import ChatApiSession, run_tool_loop
from rk.llm.types import Availability, ChatSpec, RunContext, TaskResult, TaskSpec
from rk.llm.vendors import vendor_of
from rk.roles.prompts import build_system_prompt


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

    def open_chat(self, spec: ChatSpec, ctx: RunContext) -> ChatApiSession:
        return ChatApiSession(self.backend, spec, ctx.registry, actor=self.actor(spec.role),
                              session_id=f"chat-{uuid.uuid4().hex[:12]}")

    def close(self) -> None:
        """End what the backend started (the omp auth-gateway child); it restarts on its next call."""
        close = getattr(self.backend, "close", None)
        if close is not None:
            close()
