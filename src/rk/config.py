"""Global settings stored in ``$RK_HOME/config.toml`` (mode 0600)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field

DEFAULT_DRIVERS: dict[str, str] = {
    **{r: "claude_cli" for r in ("lead", "scout", "screener_a", "reader", "gapminer", "ideator", "arguer", "writer")},
    **{r: "codex_cli" for r in ("screener_b", "skeptic", "simreader", "judge")},
    "judge_2": "ollama:deepseek-v4.1-flash:cloud",
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
    # ---- V-SUPPORT (spec §9.1); calibrated values and their record replace the initial thresholds ----
    support_tau_hi: float = 0.85
    support_tau_lo: float = 0.15
    # Lower layer-1 thresholds used when ≥2 cross-vendor judges agree (joint calibration); None = not calibrated, so
    # the layer-1-only thresholds above apply in every case.
    support_tau_hi_judged: float | None = None
    support_tau_lo_judged: float | None = None
    support_min_judges: int = 2  # spec §8.1: one cross-vendor vote is not enough to pass
    # Layer 1: Bespoke-MiniCheck-7B in local Ollama (CC BY-NC 4.0; rk is non-commercial). Alternative, MIT-licensed:
    # "lytang/MiniCheck-Flan-T5-Large" (in-process, needs the 'support' extra).
    support_model: str = "ollama:bespoke-minicheck:7b-q8_0"
    support_calibration: dict | None = None
    # ---- built-in browser (spec §8.5) and library access ----
    browser_enabled: bool = True
    browser_headless: bool = False  # RK_BROWSER_HEADLESS=1 overrides (tests, agent smoke runs)
    # {"scholar"|"pdf_domain"|"pdf_total"|"default"|<site>: {"gap": [min_s, max_s], "per_day": int | None}}
    browser_limits: dict[str, dict] = Field(default_factory=dict)
    browser_max_wait_s: float = 120.0
    browser_max_steps: int = 30  # generic-navigation actions per task
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

    def headless(self) -> bool:
        return self.browser_headless or os.environ.get("RK_BROWSER_HEADLESS") == "1"


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
