"""Global settings stored in ``$RK_HOME/config.toml`` (mode 0600)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field, field_validator

DEFAULT_DRIVERS: dict[str, str] = {
    **{r: "claude_cli" for r in ("lead", "scout", "screener_a", "reader", "gapminer", "ideator", "arguer", "writer")},
    **{r: "codex_cli" for r in ("screener_b", "skeptic", "simreader")},
    # the V-SUPPORT and P1-vote judges: three vendors, so any one generator vendor still leaves two (spec §8.1)
    "judge": "codex_cli:gpt-6-luna",
    "judge_2": "ollama:deepseek-v4.1-flash:cloud",
    "judge_3": "claude_cli:claude-haiku-5-5",
    "triage": "ollama:deepseek-v4.1-flash:cloud",
}

# provider -> environment variable consulted when the config holds no key
_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "ollama": "OLLAMA_API_KEY"}


class Settings(BaseModel):
    email: str | None = None
    openalex_key: str | None = Field(None, repr=False)
    s2_key: str | None = Field(None, repr=False)
    unpaywall_email: str | None = None
    current_project: str | None = None
    # ---- model providers (spec §8.1-8.2) ----
    anthropic_api_key: str | None = Field(None, repr=False)
    openai_api_key: str | None = Field(None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_api_key: str | None = Field(None, repr=False)
    omp_gateway: bool = False  # optional adapter; the product never needs omp
    drivers: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_DRIVERS))
    calls_per_hour: dict[str, int] = Field(default_factory=lambda: {"claude_cli": 30, "codex_cli": 20})
    cli_paths: dict[str, str] = Field(default_factory=dict)
    # ---- V-SUPPORT (spec §9.1): cloud judges only ----
    support_min_judges: int = 2  # spec §8.1: one cross-vendor vote is not enough to pass
    # ---- built-in browser (spec §8.5) and library access ----
    browser_enabled: bool = True
    browser_headless: bool = False  # RK_BROWSER_HEADLESS=1 overrides (tests, agent smoke runs)
    # {"scholar"|"pdf_domain"|"pdf_total"|"default"|<site>: {"gap": [min_s, max_s], "per_day": int | None}}
    browser_limits: dict[str, dict] = Field(default_factory=dict)
    browser_max_wait_s: float = 120.0
    browser_max_steps: int = 30  # generic-navigation actions per task
    # hosts agent tabs may reach although they are on a local/private network ("host" or "host:port"), e.g. an
    # intranet library proxy; otherwise agent tabs reach only the public web
    browser_allow_hosts: list[str] = Field(default_factory=list)
    library_name: str | None = None
    library_portal_url: str | None = None
    library_proxy: str | None = None  # template with {url}, e.g. https://login.ezproxy.example.edu/login?url={url}
    library_discovery: Literal["primo", "summon"] | None = None
    primo_base: str | None = None
    primo_vid: str | None = None
    primo_tab: str = "Everything"
    primo_scope: str = "MyInst_and_CI"
    summon_base: str | None = None
    translation_server_url: str | None = None  # optional Zotero translation-server, e.g. http://127.0.0.1:1969
    fulltext_browser_fallback: bool = True

    @field_validator("drivers")
    @classmethod
    def _fill_driver_roles(cls, v: dict[str, str]) -> dict[str, str]:
        """A stored ``[drivers]`` table keeps its roles; roles it lacks (e.g. one added later) get the default."""
        return {**DEFAULT_DRIVERS, **v}

    def headless(self) -> bool:
        return self.browser_headless or os.environ.get("RK_BROWSER_HEADLESS") == "1"


def check_library_proxy(value: str | None) -> str | None:
    """A library proxy template must be https and contain ``{url}`` (raw, EZproxy) or ``{url_enc}`` (encoded,
    OpenAthens redirector)."""
    if not value:
        return None
    v = value.strip()
    if not v.startswith("https://") or ("{url}" not in v and "{url_enc}" not in v):
        raise ValueError("the library proxy must be an https URL containing {url} or {url_enc}, e.g. "
                         "https://login.ezproxy.example.edu/login?url={url}")
    return v


def api_key(s: Settings, provider: Literal["anthropic", "openai", "ollama"]) -> str | None:
    """Key from config.toml, else from the environment. Resolved at use time so an environment key is never
    written back into config.toml by a load/save cycle."""
    return getattr(s, f"{provider}_api_key") or os.environ.get(_KEY_ENV[provider]) or None


def rk_home() -> Path:
    env = os.environ.get("RK_HOME")
    return Path(env).expanduser() if env else Path.home() / "ResearchKernel"


def config_path() -> Path:
    return rk_home() / "config.toml"


def load_settings() -> Settings:
    p = config_path()
    if not p.exists():
        return Settings()
    with p.open("rb") as f:
        return Settings.model_validate(tomllib.load(f))


def save_settings(s: Settings) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in s.model_dump().items() if v is not None}
    tmp = p.with_suffix(".toml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        tomli_w.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p
