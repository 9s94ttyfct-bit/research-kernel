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
    """The explicit ``rules`` against ``provider:model`` first; then the ``families`` against the model id after its
    last ``/``, failing closed (see vendors.toml): Ollama library ids only; omp_gateway ``host/id`` on the
    ``family_hosts`` only, and ``host/org/id`` only when the org is one of the family vendor's ``vendor_orgs``."""
    table = _table()
    key = f"{provider}:{model or ''}"
    for r in table["rules"]:
        if fnmatch.fnmatchcase(key, r["match"]):
            return r["vendor"]
    if not model:
        return None
    parts = model.split("/")
    if provider == "ollama":
        return _family(table, model) if len(parts) == 1 else None
    if provider == "omp_gateway":
        if len(parts) == 2:
            return _family(table, parts[1]) if parts[0] in table["family_hosts"] else None
        if len(parts) >= 3:
            vendor = _family(table, parts[-1])
            return vendor if vendor and parts[1].lower() in table["vendor_orgs"].get(vendor, ()) else None
    return None


def _family(table: dict, model_id: str) -> str | None:
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
