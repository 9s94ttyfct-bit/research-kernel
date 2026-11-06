"""Kernel-chosen passages of a work (Plan 4 deviation 4): the evidence from a cited work is picked by lexical similarity
to the claim, never by the miner."""

from __future__ import annotations

import re

from pydantic import BaseModel
from rapidfuzz import fuzz

from rk.fulltext import latest_snapshot
from rk.store.project import ProjectStore

# A sentence starts at a non-space character and ends at terminal punctuation (with closing quotes or brackets)
# followed by whitespace and not a lower-case letter ("e.g. rates" stays one sentence), before a blank line, or at
# the end of the page.
_SENTENCE = re.compile(r"\S.*?(?:[.!?]+[\"'\u201d\u2019)\]]*(?=\s*\Z|\s+(?![a-z]))|(?=\n[ \t]*\n)|\Z)", re.DOTALL)
WINDOW = 3


class Passage(BaseModel):
    """``text`` is ``page_text[start:end]`` of layer page ``page``: offsets are into the page text, as in
    ``rk_text_read``."""

    text: str
    page: int
    start: int
    end: int


def _windows(page: int, text: str) -> list[Passage]:
    spans = [(m.start(), m.end()) for m in _SENTENCE.finditer(text)]
    out = []
    for i in range(max(1, len(spans) - WINDOW + 1)):
        group = spans[i:i + WINDOW]
        if group:
            s, e = group[0][0], group[-1][1]
            out.append(Passage(text=text[s:e], page=page, start=s, end=e))
    return out


def top_passages(ps: ProjectStore, item_id: str, query: str, k: int = 5) -> list[Passage]:
    """The ``k`` best 3-sentence windows of the item's latest text layer by ``token_set_ratio`` with ``query`` (ties by
    position), never overlapping each other. ``[]`` without a text layer."""
    snap = latest_snapshot(ps, item_id)
    if snap is None:
        return []
    windows = [w for p in snap[1].pages for w in _windows(p.n, p.text)]
    ranked = sorted(windows, key=lambda w: (-fuzz.token_set_ratio(query, w.text), w.page, w.start))
    chosen: list[Passage] = []
    for w in ranked:
        if len(chosen) == k:
            break
        if all(c.page != w.page or w.end <= c.start or c.end <= w.start for c in chosen):
            chosen.append(w)
    return chosen
