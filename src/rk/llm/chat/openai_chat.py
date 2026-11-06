"""OpenAI Chat Completions wire format (also spoken by ``omp auth-gateway``)."""

from __future__ import annotations

import json

import httpx

from rk.llm.chat.base import Msg, ToolCall, ToolDef, Turn, http_error_message
from rk.llm.types import Availability, ProviderError


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparsed_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_unparsed_arguments": raw}


def openai_messages(messages: list[Msg]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append({"role": "assistant", "content": m.content or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in m.tool_calls]})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def openai_body(model: str, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> dict:
    body: dict = {"model": model, "messages": openai_messages(messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                           "parameters": t.parameters}} for t in tools]
    if json_schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": json_schema}}
    return body


def openai_turn(data: dict) -> Turn:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"malformed chat completion: {str(data)[:300]}") from e
    calls = [ToolCall(id=c.get("id") or f"call_{i}", name=c["function"]["name"],
                      arguments=_args(c["function"].get("arguments")))
             for i, c in enumerate(msg.get("tool_calls") or [])]
    return Turn(text=msg.get("content") or "", tool_calls=calls, model=data.get("model"), usage=data.get("usage") or {})


class OpenAIChatBackend:
    name = "openai_api"

    def __init__(self, api_key: str | None, model: str, base_url: str, http: httpx.Client):
        self.api_key, self.model, self.base_url, self._http = api_key, model, base_url.rstrip("/"), http

    def available(self) -> Availability:
        if not self.api_key:
            return Availability(ok=False, reason="no OpenAI API key (config openai_api_key or OPENAI_API_KEY)")
        return Availability(ok=True)

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn:
        if not self.api_key:
            raise ProviderError("no OpenAI API key")
        try:
            r = self._http.post(f"{self.base_url}/chat/completions",
                                json=openai_body(self.model, messages, tools, json_schema),
                                headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise ProviderError(http_error_message(r))
        return openai_turn(r.json())
