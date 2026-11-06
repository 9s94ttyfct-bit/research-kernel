"""Prefixed ULID identifiers."""

from __future__ import annotations

import re

from ulid import ULID

PREFIXES: frozenset[str] = frozenset(
    {
        "prj", "src", "snp", "nte", "clm", "evd", "edg", "obj", "hyp", "rq", "tpc", "gap",
        "prt", "dsg", "fig", "stt", "chk", "ver", "drf", "tsk", "run", "evt", "att", "col",
    }
)

_ID_RE = re.compile(r"^([a-z]{2,3})_([0-9A-HJKMNP-TV-Z]{26})$")


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{ULID()}"


def is_id(value: str, prefix: str | None = None) -> bool:
    m = _ID_RE.match(value or "")
    if not m or m.group(1) not in PREFIXES:
        return False
    return prefix is None or m.group(1) == prefix
