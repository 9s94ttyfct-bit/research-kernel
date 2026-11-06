"""Provider protocol and the data passed in and out of a role task (spec §8.2)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

ProviderKind = Literal["agentic_cli", "chat_api", "human"]


class ProviderError(Exception):
    """A driver could not complete a call (transport error, non-2xx, quota, malformed reply)."""


class Availability(BaseModel):
    ok: bool
    version: str | None = None
    reason: str | None = None


@dataclass
class TaskSpec:
    task_id: str
    role: str
    project: str | None
    instructions: str
    inputs: dict
    output_schema: type[BaseModel]
    tools: list[str]
    max_turns: int = 12
    timeout_s: int = 600


@dataclass
class TaskResult:
    task_id: str
    ok: bool
    output: BaseModel | None
    raw: str
    provider: str
    model: str | None
    duration_s: float
    error: str | None
    tool_calls: int = 0
    session_id: str | None = None


@dataclass
class ChatSpec:
    role: str
    project: str | None
    system: str
    tools: list[str]
    model: str | None = None


@dataclass
class ChatEvent:
    kind: Literal["text", "tool_call", "tool_result", "done", "error"]
    data: dict = field(default_factory=dict)


@dataclass
class RunContext:
    """What a provider needs from the Kernel: the in-process registry (chat drivers) or a task token and
    ``RK_HOME`` (agentic CLIs, which reach the Kernel through ``rk mcp``)."""

    kernel: Any
    registry: Any
    task_token: str | None = None
    actor: str = "kernel"


class ChatSession(Protocol):
    session_id: str | None

    def send(self, user_message: str) -> Iterator[ChatEvent]: ...

    def close(self) -> None: ...


class Provider(Protocol):
    name: str
    model: str | None
    vendor: str | None
    kind: ProviderKind

    def available(self) -> Availability: ...

    def run(self, spec: TaskSpec, ctx: RunContext) -> TaskResult: ...

    def open_chat(self, spec: ChatSpec, ctx: RunContext) -> ChatSession: ...
