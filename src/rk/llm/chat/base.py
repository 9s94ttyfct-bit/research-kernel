"""Provider-neutral chat messages, tool definitions and turns."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from rk.llm.types import Availability


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class Msg(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name, for tool-result messages


class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict


class Turn(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str | None = None
    usage: dict = Field(default_factory=dict)


class ChatBackend(Protocol):
    name: str
    model: str

    def available(self) -> Availability: ...

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn: ...


def http_error_message(r) -> str:
    """Server-provided error text for a non-2xx response (JSON ``error`` field when present); a 429 names its
    ``Retry-After`` seconds (``rk.llm.limits.http_limit`` reads them)."""
    status = f"HTTP {r.status_code}"
    after = (r.headers.get("retry-after") or "").strip()
    if r.status_code == 429 and after.replace(".", "", 1).isdigit():
        status += f" (retry-after {after})"
    try:
        body = r.json()
    except ValueError:
        return f"{status}: {r.text[:500]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        err = err.get("message") or err
    return f"{status}: {err or body}"
