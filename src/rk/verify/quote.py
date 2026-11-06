"""V-QUOTE: a quotation must appear verbatim (after normalization) in the source text layer."""

from __future__ import annotations

from pydantic import BaseModel
from rapidfuzz import fuzz

from rk.text.layer import TextLayer, build_norm_keys, normalize_query


class QuoteResult(BaseModel):
    ok: bool
    page: int | None = None
    start: int | None = None
    end: int | None = None
    best_score: float = 0.0
    best_snippet: str = ""


def verify_quote(quote: str, layer: TextLayer) -> QuoteResult:
    q = normalize_query(quote)
    if not q:
        return QuoteResult(ok=False)
    best = QuoteResult(ok=False)
    for page in layer.pages:
        for key in build_norm_keys(page.text):
            idx = key.key.find(q)
            if idx >= 0:
                return QuoteResult(ok=True, page=page.n, start=key.map[idx], end=key.map[idx + len(q) - 1] + 1,
                                   best_score=100.0, best_snippet=key.key[idx : idx + len(q)])
            al = fuzz.partial_ratio_alignment(q, key.key)
            if al is not None and al.score > best.best_score:
                best = QuoteResult(ok=False, page=page.n, best_score=round(al.score, 1),
                                   best_snippet=key.key[al.dest_start : al.dest_end])
    return best
