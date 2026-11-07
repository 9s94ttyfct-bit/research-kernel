"""Driver specs (``provider[:model]``) and the provider factory."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from rk.config import Settings
from rk.llm.types import Availability, Provider

PROVIDERS = ("claude_cli", "codex_cli", "anthropic_api", "openai_api", "ollama", "omp_gateway", "human")


@dataclass(frozen=True)
class DriverSpec:
    provider: str
    model: str | None = None

    @classmethod
    def parse(cls, text: str) -> DriverSpec:
        provider, _, model = text.strip().partition(":")  # first ':' only: ollama models contain ':'
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}")
        return cls(provider, model or None)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider


def build_provider(spec: DriverSpec, settings: Settings, *, http: httpx.Client | None = None) -> Provider:
    # Imports are local: each driver pulls in only what it needs.
    from rk.config import api_key

    if spec.provider == "claude_cli":
        from rk.llm.cli_claude import ClaudeCliProvider

        return ClaudeCliProvider(settings, spec.model)
    if spec.provider == "codex_cli":
        from rk.llm.cli_codex import CodexCliProvider

        return CodexCliProvider(settings, spec.model)
    if spec.provider == "human":
        from rk.llm.human import HumanProvider

        return HumanProvider()
    if spec.provider in ("anthropic_api", "openai_api") and not spec.model:
        raise ValueError(f"{spec.provider} needs an explicit model, e.g. '{spec.provider}:<model-id>'")
    from rk.llm.chat_provider import ChatApiProvider

    client = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))
    if spec.provider == "anthropic_api":
        from rk.llm.chat.anthropic import AnthropicBackend

        backend = AnthropicBackend(api_key(settings, "anthropic"), spec.model, client)
    elif spec.provider == "openai_api":
        from rk.llm.chat.openai_chat import OpenAIChatBackend

        backend = OpenAIChatBackend(api_key(settings, "openai"), spec.model, settings.openai_base_url, client)
    elif spec.provider == "ollama":
        from rk.llm.chat.ollama import OllamaBackend

        backend = OllamaBackend(settings.ollama_url, spec.model or "deepseek-v4.1-flash:cloud", client,
                                api_key=api_key(settings, "ollama"))
    else:  # omp_gateway: available whenever omp is on PATH (OmpGatewayBackend.available)
        from rk.llm.chat.omp_gateway import OmpGatewayBackend

        backend = OmpGatewayBackend(spec.model or "openai-codex/gpt-6-luna",
                                    omp_bin=settings.cli_paths.get("omp", "omp"))
    return ChatApiProvider(backend, spec.provider)


def available_all(settings: Settings) -> dict[str, tuple[DriverSpec, Availability]]:
    out: dict[str, tuple[DriverSpec, Availability]] = {}
    cache: dict[DriverSpec, Availability] = {}
    for role, text in sorted(settings.drivers.items()):
        spec = DriverSpec.parse(text)
        if spec not in cache:
            try:
                cache[spec] = build_provider(spec, settings).available()
            except Exception as e:  # noqa: BLE001 - reported per driver, never raised
                cache[spec] = Availability(ok=False, reason=f"{type(e).__name__}: {e}")
        out[role] = (spec, cache[spec])
    return out
