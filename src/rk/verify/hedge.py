"""V-HEDGE: a claim may not be stated more strongly than its sources.

Two rules (spec §9.1): the claim's level is at most the highest level of its evidence (``check_hedge``), and a
universal quantifier ("all", "every", "never", "always", "none": ``universal_terms``) needs evidence coverage — at
least two evidence notes, one of which itself states a universal (``rk.verify.support.check_claim_hedge``)."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from importlib.resources import files

from pydantic import BaseModel

UNIVERSALS = ("all", "every", "never", "always", "none")
_UNIVERSAL = re.compile(r"\b(" + "|".join(UNIVERSALS) + r")\b", re.IGNORECASE)


class HedgeResult(BaseModel):
    ok: bool
    claim_level: int
    source_level: int | None  # None: no evidence text to compare with
    universal: list[str] = []  # the claim's universal quantifiers
    problems: list[str] = []
    reason: str | None = None


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


def universal_terms(text: str) -> list[str]:
    """The universal quantifiers in ``text``, lower-cased, once each, in order of appearance."""
    return list(dict.fromkeys(m.lower() for m in _UNIVERSAL.findall(text)))
