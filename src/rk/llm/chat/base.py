"""Provider-neutral chat messages, tool definitions and turns."""

from __future__ import annotations

import time
from datetime import UTC
from email.utils import parsedate_to_datetime
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


def _retry_after_s(value: str) -> float | None:
    """A ``Retry-After`` header in seconds: delay seconds, or an HTTP date from now (``None`` when neither)."""
    value = value.strip()
    if value.replace(".", "", 1).isdigit():
        return float(value)
    try:
        at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return max(0.0, at.timestamp() - time.time())


def http_error_message(r) -> str:
    """Server-provided error text for a non-2xx response (JSON ``error`` field when present); a 429 names its
    ``Retry-After`` (seconds, or an HTTP date as seconds from now; ``rk.llm.limits.http_limit`` reads them)."""
    status = f"HTTP {r.status_code}"
    if r.status_code == 429 and (after := _retry_after_s(r.headers.get("retry-after") or "")) is not None:
        status += f" (retry-after {after:.0f})"
    try:
        body = r.json()
    except ValueError:
        return f"{status}: {r.text[:500]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        err = err.get("message") or err
    return f"{status}: {err or body}"
