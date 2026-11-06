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


def _boundary_ok(key: str, idx: int, q: str) -> bool:
    """A match must not start or end inside a word ('effective' must not match inside 'ineffective')."""
    before = key[idx - 1] if idx > 0 else ""
    after = key[idx + len(q)] if idx + len(q) < len(key) else ""
    return (not q[0].isalnum() or not before.isalnum()) and (not q[-1].isalnum() or not after.isalnum())


def find_verbatim(q: str, key: str, start: int = 0) -> int:
    """Index of the first word-bounded occurrence of normalized ``q`` in ``key`` at or after ``start``, or -1."""
    idx = key.find(q, start)
    while idx >= 0 and not _boundary_ok(key, idx, q):
        idx = key.find(q, idx + 1)
    return idx


def verify_quote_at(quote: str, layer: TextLayer, page: int, start: int, end: int) -> QuoteResult:
    """V-QUOTE at a known position: ok only when an occurrence ``verify_quote`` would accept (same normalization, line-end
    hyphen keys and word boundaries) spans exactly ``[start:end]`` of the page text."""
    q = normalize_query(quote)
    pg = next((p for p in layer.pages if p.n == page), None)
    if not q or pg is None:
        return QuoteResult(ok=False)
    for key in build_norm_keys(pg.text):
        idx = find_verbatim(q, key.key)
        while idx >= 0:
            if (key.map[idx], key.map[idx + len(q) - 1] + 1) == (start, end):
                return QuoteResult(ok=True, page=page, start=start, end=end, best_score=100.0,
                                   best_snippet=key.key[idx: idx + len(q)])
            idx = find_verbatim(q, key.key, idx + 1)
    return QuoteResult(ok=False)


def verify_quote(quote: str, layer: TextLayer) -> QuoteResult:
    q = normalize_query(quote)
    if not q:
        return QuoteResult(ok=False)
    best = QuoteResult(ok=False)
    for page in layer.pages:
        for key in build_norm_keys(page.text):
            idx = find_verbatim(q, key.key)
            if idx >= 0:
                return QuoteResult(ok=True, page=page.n, start=key.map[idx], end=key.map[idx + len(q) - 1] + 1,
                                   best_score=100.0, best_snippet=key.key[idx : idx + len(q)])
            al = fuzz.partial_ratio_alignment(q, key.key)
            if al is None:
                continue
            s, e = al.dest_start, al.dest_end
            while s > 0 and key.key[s - 1].isalnum():  # suggest whole words, so the snippet is itself quotable
                s -= 1
            while e < len(key.key) and key.key[e].isalnum():
                e += 1
            snippet = key.key[s:e].strip()
            score = round(fuzz.ratio(q, snippet), 1)
            if score > best.best_score:
                best = QuoteResult(ok=False, page=page.n, best_score=score, best_snippet=snippet)
    return best
