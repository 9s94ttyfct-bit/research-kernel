"""Global settings stored in ``$RK_HOME/config.toml`` (mode 0600)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel


class Settings(BaseModel):
    email: str | None = None
    openalex_key: str | None = None
    s2_key: str | None = None
    unpaywall_email: str | None = None
    current_project: str | None = None


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
