"""V-HEDGE: a claim may not be stated more strongly than its sources.

Two rules (spec §9.1): the claim's level is at most the highest level of its evidence (``check_hedge``), and a
universal quantifier ("all", "every", "never", "always", "none": ``universal_terms``) needs evidence coverage — at
least two sources among its evidence notes, one note of which itself states the same universal positively, or its
declared synonym (``SYNONYMS``: none ↔ never) (``rk.verify.support.check_claim_hedge``).

A categorical word or universal quantifier counts only where it is stated positively (``_stated``): not when a
negator or hedge precedes it ("not all", "not proven", "almost every", "cannot always", "don't always") or a
"too … to", "insufficient to" or "not … enough to" leads to it ("too few events to establish", "not large enough to
prove": ``_QUALIFIERS``), also across up to two auxiliaries, adverbs or articles ("has not been proven", "is not clearly
established", "is not an established rule": ``_BETWEEN``), when a hyphen follows it ("all-cause", "never-smokers"), in "every other", or in the idiom "in all"
meaning "in total", before punctuation or the end of the text ("In all, 12 trials", "12 trials in all."; "in all
patients" and "in all 12 trials" stay universal). ``hedge_level`` applies the same filter to the categorical
(level-3) words."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from importlib.resources import files

from pydantic import BaseModel

UNIVERSALS = ("all", "every", "never", "always", "none")
# Quantifiers that state the same universal: a note saying "never" covers a claim saying "none", and back.
SYNONYMS: dict[str, frozenset[str]] = {u: frozenset({u}) for u in UNIVERSALS} | {
    u: frozenset({"none", "never"}) for u in ("none", "never")}
# Auxiliaries, adverbs and articles that may stand between a negator and the word it negates (at most two of them).
# Calibration (critical3 conclusion): "not an established general causal rule" was read as categorical.
_BETWEEN = (r"(?:\s+(?:been|be|being|yet|ever|always|fully|clearly|conclusively|definitively|firmly|entirely|completely"
            r"|a|an|the))")
# Words that keep what follows from being stated: a negator or hedge right before it, or a "too … to", "insufficient
# to" or "not … enough to" leading to it ("too few events to establish", "not large enough to prove").
_WORD = r"[\w'’-]+\s+"
_QUALIFIERS = re.compile(r"(?:\bnot|\bnever|\bcannot|n['’]t|\balmost|\bnearly|\bvirtually|\bhardly|\bpractically"
                         r"|\btoo\s+(?:" + _WORD + r"){1,3}to|\binsufficient\s+to"
                         r"|(?:\bnot|n['’]t)\s+(?!(?:only|just|merely|simply)\b)(?:" + _WORD + r"){0,3}enough\s+(?:"
                         + _WORD + r"){0,3}to)"
                         + _BETWEEN + r"{0,2}\s+$", re.IGNORECASE)
# "whether or not" negates no categorical word ("whether or not the proven therapy was given"); a universal after it
# stays unstated ("whether or not all patients benefit" claims nothing of all).
_WHETHER = re.compile(r"\bwhether\s+or\s+not" + _BETWEEN + r"{0,2}\s+$", re.IGNORECASE)
_AFTER = re.compile(_BETWEEN + r"{0,2}\s+", re.IGNORECASE)
_IN_TOTAL = re.compile(r"\s*(?:[,.;:!?)]|$)")
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


def _negator(text: str, m: re.Match) -> bool:
    """Whether ``m`` is a "never" that negates the categorical word after it ("has never been proven"): a negator
    there, not a universal or a categorical claim of its own ("it never failed" stays both)."""
    if m[0].lower() != "never" or (gap := _AFTER.match(text, m.end())) is None:
        return False
    return _patterns()["categorical"].match(text, gap.end()) is not None


def _stated(text: str, m: re.Match) -> bool:
    """Whether the word matched by ``m`` in ``text`` is stated positively (see the module docstring)."""
    word, before, after = m[0].lower(), text[:m.start()], text[m.end():]
    qualified = _QUALIFIERS.search(before) and not (word not in UNIVERSALS and _WHETHER.search(before))
    return not (qualified or after.startswith(("-", "‐", "‑")) or _negator(text, m)
                or (word == "every" and re.match(r"\s+other\b", after, re.IGNORECASE))
                or (word == "all" and re.search(r"\bin\s+$", before, re.IGNORECASE) and _IN_TOTAL.match(after)))


def hedge_level(text: str) -> int:
    p = _patterns()
    if p["speculative"].search(text):
        return 0
    if p["tentative"].search(text):
        return 1
    if any(_stated(text, m) for m in p["categorical"].finditer(text)):
        return 3
    return 2


def check_hedge(claim: str, sources: list[str]) -> HedgeResult:
    if not sources:
        raise ValueError("check_hedge needs at least one source text")
    c = hedge_level(claim)
    s = max(hedge_level(t) for t in sources)
    return HedgeResult(ok=c <= s, claim_level=c, source_level=s)


def universal_terms(text: str) -> list[str]:
    """The universal quantifiers (``UNIVERSALS``) that ``text`` states positively (``_stated``), lower-cased, once
    each, in order of appearance."""
    return list(dict.fromkeys(m[0].lower() for m in _UNIVERSAL.finditer(text) if _stated(text, m)))
