"""Vendor = model developer, not host (spec §8.1). Generators and reviewers must differ."""

from __future__ import annotations

import fnmatch
import tomllib
from functools import cache
from importlib.resources import files


@cache
def _rules() -> tuple[tuple[str, str], ...]:
    data = tomllib.loads(files("rk.llm").joinpath("vendors.toml").read_text(encoding="utf-8"))
    return tuple((r["match"], r["vendor"]) for r in data["rules"])


def vendor_of(provider: str, model: str | None) -> str | None:
    key = f"{provider}:{model or ''}"
    for pattern, vendor in _rules():
        if fnmatch.fnmatchcase(key, pattern):
            return vendor
    return None


def require_cross_vendor(generator: str | None, reviewer: str | None) -> None:
    """Raise unless both vendors are known and different."""
    if generator is None or reviewer is None:
        raise ValueError(f"unknown vendor (generator={generator!r}, reviewer={reviewer!r}) cannot be cross-checked")
    if generator == reviewer:
        raise ValueError(f"generator and reviewer share vendor {generator!r}")
