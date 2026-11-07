"""Vendor = model developer, not host (spec §8.1). Generators and reviewers must differ."""

from __future__ import annotations

import fnmatch
import tomllib
from functools import cache
from importlib.resources import files


@cache
def _table() -> dict:
    return tomllib.loads(files("rk.llm").joinpath("vendors.toml").read_text(encoding="utf-8"))


def vendor_of(provider: str, model: str | None) -> str | None:
    """The explicit ``rules`` against ``provider:model`` first; then, for the hosts of many developers' models
    (``family_providers``), the ``families`` against the model id after its last ``/``."""
    table = _table()
    key = f"{provider}:{model or ''}"
    for r in table["rules"]:
        if fnmatch.fnmatchcase(key, r["match"]):
            return r["vendor"]
    if model and provider in table["family_providers"]:
        model_id = model.rsplit("/", 1)[-1]
        for r in table["families"]:
            if fnmatch.fnmatchcase(model_id, r["match"]):
                return r["vendor"]
    return None


def require_cross_vendor(generator: str | None, reviewer: str | None) -> None:
    """Raise unless both vendors are known and different."""
    if generator is None or reviewer is None:
        raise ValueError(f"unknown vendor (generator={generator!r}, reviewer={reviewer!r}) cannot be cross-checked")
    if generator == reviewer:
        raise ValueError(f"generator and reviewer share vendor {generator!r}")
