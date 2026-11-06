"""V-PARA: lexical closeness flag for paraphrases (the book's "finger test").

This only detects near-copying. Semantic fidelity of a paraphrase is judged by V-SUPPORT (Plan 2).
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from rapidfuzz import fuzz

MAX_COMMON_RUN = 8
MAX_NGRAM4_CONTAINMENT = 0.35
MAX_TOKEN_SET_RATIO = 85.0


class ParaResult(BaseModel):
    ok: bool
    longest_common_run: int
    ngram4_containment: float
    token_set_ratio: float


def _tokens(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def _ngrams(t: list[str], n: int = 4) -> set[tuple[str, ...]]:
    return {tuple(t[i : i + n]) for i in range(len(t) - n + 1)}


def check_paraphrase(paraphrase: str, source_span: str) -> ParaResult:
    p, s = _tokens(paraphrase), _tokens(source_span)
    run = _longest_common_run(p, s)
    pg = _ngrams(p)
    containment = len(pg & _ngrams(s)) / len(pg) if pg else 0.0
    tsr = float(fuzz.token_set_ratio(" ".join(p), " ".join(s)))
    ok = run <= MAX_COMMON_RUN and containment <= MAX_NGRAM4_CONTAINMENT and tsr <= MAX_TOKEN_SET_RATIO
    return ParaResult(ok=ok, longest_common_run=run, ngram4_containment=round(containment, 3), token_set_ratio=tsr)
