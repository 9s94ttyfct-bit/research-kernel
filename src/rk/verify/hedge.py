"""V-HEDGE: a claim may not be stated more strongly than its sources.

Two rules (spec §9.1): the claim's level is at most the highest level of its evidence (``check_hedge``), and a
universal quantifier ("all", "every", "never", "always", "none": ``universal_terms``) needs evidence coverage — at
least two sources among its evidence notes, one note of which itself states the same universal positively, or one of
its declared synonyms (``SYNONYMS``) (``rk.verify.support.check_claim_hedge``).

A quantifier is not a universal statement when a negator or hedge precedes it ("not all", "almost every", "don't
always": ``_QUALIFIERS``), when a hyphen follows it ("all-cause", "never-smokers"), in "every other", or in the idiom
"in all" meaning "in total" ("In all, 12 trials", "12 trials in all."; "in all patients" stays universal)."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from importlib.resources import files

from pydantic import BaseModel

UNIVERSALS = ("all", "every", "never", "always", "none")
# Quantifiers that state the same universal: a note saying "never" or "no …" covers a claim saying "none".
SYNONYMS: dict[str, frozenset[str]] = {u: frozenset({u}) for u in UNIVERSALS} | {
    u: frozenset({"none", "never", "no"}) for u in ("none", "never")}
_QUALIFIERS = re.compile(r"(?:\bnot|n['’]t|\balmost|\bnearly|\bvirtually|\bhardly|\bpractically)\s+$", re.IGNORECASE)
_IN_TOTAL = re.compile(r"\s*(?:[,.;:!?)]|$|\d)")


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


def universal_terms(text: str, *, extra: tuple[str, ...] = ()) -> list[str]:
    """The universal quantifiers (``UNIVERSALS`` plus ``extra``, e.g. ``("no",)``) that ``text`` states positively,
    lower-cased, once each, in order of appearance; negated, hedged, hyphenated and idiomatic uses are left out (see
    the module docstring)."""
    words = (*UNIVERSALS, *extra)
    out = []
    for m in re.finditer(r"\b(" + "|".join(map(re.escape, words)) + r")\b", text, re.IGNORECASE):
        term, before, after = m[1].lower(), text[:m.start()], text[m.end():]
        if (_QUALIFIERS.search(before) or after.startswith(("-", "‐", "‑"))
                or (term == "every" and re.match(r"\s+other\b", after, re.IGNORECASE))
                or (term == "all" and re.search(r"\bin\s+$", before, re.IGNORECASE) and _IN_TOTAL.match(after))):
            continue
        out.append(term)
    return list(dict.fromkeys(out))
