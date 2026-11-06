"""V-HEDGE: a claim may not be stated more strongly than its sources."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from importlib.resources import files

from pydantic import BaseModel


class HedgeResult(BaseModel):
    ok: bool
    claim_level: int
    source_level: int


@cache
def _patterns() -> dict[str, re.Pattern]:
    lex = tomllib.loads(files("rk.verify").joinpath("hedge_lexicon.toml").read_text(encoding="utf-8"))
    return {
        level: re.compile(r"\b(?:" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")\b", re.IGNORECASE)
        for level, terms in lex.items()
    }


def hedge_level(text: str) -> int:
    p = _patterns()
    if p["speculative"].search(text):
        return 0
    if p["tentative"].search(text):
        return 1
    if p["categorical"].search(text):
        return 3
    return 2


def check_hedge(claim: str, sources: list[str]) -> HedgeResult:
    if not sources:
        raise ValueError("check_hedge needs at least one source text")
    c = hedge_level(claim)
    s = max(hedge_level(t) for t in sources)
    return HedgeResult(ok=c <= s, claim_level=c, source_level=s)
