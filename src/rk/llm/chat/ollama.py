"""Ollama ``/api/chat`` backend (local daemon, or ollama.com with an API key)."""

from __future__ import annotations

import httpx

from rk.llm.chat.base import Msg, ToolCall, ToolDef, Turn, http_error_message
from rk.llm.types import Availability, ProviderError


def ollama_body(model: str, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> dict:
    msgs: list[dict] = []
    for m in messages:
        if m.role == "tool":
            msgs.append({"role": "tool", "content": m.content, "tool_name": m.name or ""})
        elif m.role == "assistant" and m.tool_calls:
            msgs.append({"role": "assistant", "content": m.content,
                         "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]})
        else:
            msgs.append({"role": m.role, "content": m.content})
    body: dict = {"model": model, "messages": msgs, "stream": False}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                           "parameters": t.parameters}} for t in tools]
    # Ollama's cloud does not enforce structured outputs; local models do. Output is validated client-side anyway.
    if json_schema is not None and not model.endswith(":cloud"):
        body["format"] = json_schema
    return body


def ollama_turn(data: dict) -> Turn:
    msg = data.get("message")
    if not isinstance(msg, dict):
        raise ProviderError(f"malformed /api/chat response: {str(data)[:300]}")
    calls = [ToolCall(id=c.get("id") or f"call_{i}", name=c["function"]["name"],
                      arguments=c["function"].get("arguments") or {})
             for i, c in enumerate(msg.get("tool_calls") or [])]
    usage = {k: data[k] for k in ("prompt_eval_count", "eval_count") if k in data}
    return Turn(text=msg.get("content") or "", tool_calls=calls, model=data.get("model"), usage=usage)


class OllamaBackend:
    name = "ollama"

    def __init__(self, url: str, model: str, http: httpx.Client, *, api_key: str | None = None):
        self.url, self.model, self._http, self.api_key = url.rstrip("/"), model, http, api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def available(self) -> Availability:
        try:
            r = self._http.get(f"{self.url}/api/tags", headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as e:
            return Availability(ok=False, reason=f"ollama not reachable at {self.url}: {type(e).__name__}")
        if r.status_code >= 400:
            return Availability(ok=False, reason=http_error_message(r))
        names = {m.get("name") for m in r.json().get("models", [])} | {m.get("model") for m in r.json().get("models", [])}
        if self.model not in names:
            return Availability(ok=False, reason=f"model {self.model} not pulled (ollama pull {self.model})")
        return Availability(ok=True)

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn:
        try:
            r = self._http.post(f"{self.url}/api/chat", json=ollama_body(self.model, messages, tools, json_schema),
                                headers=self._headers())
        except httpx.HTTPError as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise ProviderError(http_error_message(r))
        data = r.json()
        if "error" in data:  # some errors arrive with 200
            raise ProviderError(str(data["error"]))
        return ollama_turn(data)
