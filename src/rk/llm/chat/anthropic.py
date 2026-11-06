"""Anthropic Messages API backend."""

from __future__ import annotations

import httpx

from rk.llm.chat.base import Msg, ToolCall, ToolDef, Turn, http_error_message
from rk.llm.types import Availability, ProviderError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_TOKENS = 8192


def anthropic_body(model: str, messages: list[Msg], tools: list[ToolDef]) -> dict:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)  # consecutive tool results share one user turn
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant":
            blocks: list[dict] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": "user", "content": m.content})
    body: dict = {"model": model, "max_tokens": MAX_TOKENS, "messages": out}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
    return body


def anthropic_turn(data: dict) -> Turn:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise ProviderError(f"malformed messages response: {str(data)[:300]}")
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {}) for b in blocks
             if b.get("type") == "tool_use"]
    return Turn(text=text, tool_calls=calls, model=data.get("model"), usage=data.get("usage") or {})


class AnthropicBackend:
    name = "anthropic_api"

    def __init__(self, api_key: str | None, model: str, http: httpx.Client):
        self.api_key, self.model, self._http = api_key, model, http

    def available(self) -> Availability:
        if not self.api_key:
            return Availability(ok=False, reason="no Anthropic API key (config anthropic_api_key or ANTHROPIC_API_KEY)")
        return Availability(ok=True)

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn:
        if not self.api_key:
            raise ProviderError("no Anthropic API key")
        try:
            r = self._http.post(API_URL, json=anthropic_body(self.model, messages, tools),
                                headers={"x-api-key": self.api_key, "anthropic-version": API_VERSION})
        except httpx.HTTPError as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise ProviderError(http_error_message(r))
        return anthropic_turn(r.json())
