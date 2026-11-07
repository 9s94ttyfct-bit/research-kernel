"""``Hits``: one page of a source's search hits, with the entries the source returned but could not be read."""

from __future__ import annotations

from collections.abc import Iterable

from rk.library.models import ItemIn


class Hits(list[ItemIn]):
    """A page of search hits; ``dropped`` counts the entries of the page the source returned that could not be read as
    a work (malformed), so ``raw``, hits plus dropped, is how full the page came back: paging judges a page by it, and
    the drop is recorded with the logged search, never silent."""

    def __init__(self, items: Iterable[ItemIn] = (), dropped: int = 0):
        super().__init__(items)
        self.dropped = dropped

    @property
    def raw(self) -> int:
        return len(self) + self.dropped


def dropped_of(hits: list[ItemIn]) -> int:
    """The entries a page of hits dropped (0 for a plain list)."""
    return hits.dropped if isinstance(hits, Hits) else 0
