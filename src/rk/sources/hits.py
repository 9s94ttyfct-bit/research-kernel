"""``Hits``: one page of a source's search hits, with the entries the source returned but could not be read and the
size of the whole result set when the source says it."""

from __future__ import annotations

from collections.abc import Iterable

from rk.library.models import ItemIn


class Hits(list[ItemIn]):
    """A page of search hits; ``dropped`` counts the entries of the page the source returned that could not be read as
    a work (malformed), so ``raw``, hits plus dropped, is how full the page came back: paging judges a page by it, and
    the drop is counted with the logged search, never silent. ``total`` is how many results the whole query has
    (arXiv's ``opensearch:totalResults``; ``None``: the source did not say), so a short page inside the result set is
    no last page."""

    def __init__(self, items: Iterable[ItemIn] = (), dropped: int = 0, total: int | None = None):
        super().__init__(items)
        self.dropped, self.total = dropped, total

    @property
    def raw(self) -> int:
        return len(self) + self.dropped


def as_hits(hits: list[ItemIn]) -> Hits:
    """``hits`` as a ``Hits`` (a plain list: nothing dropped, no total)."""
    return hits if isinstance(hits, Hits) else Hits(hits)
