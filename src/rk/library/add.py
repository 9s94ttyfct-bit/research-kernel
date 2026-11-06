"""Adding a work to a project library by identifier or metadata: the path of ``rk_source_add``, also used by the
discovery stages to add a cited work by its DOI."""

from __future__ import annotations

from typing import Literal

from rk.library.models import ItemIn
from rk.library.service import AddResult, Library
from rk.sources.identifiers import parse_identifier
from rk.sources.resolver import SourceContext, resolve
from rk.tools.registry import ToolError


def add_source(sctx: SourceContext, lib: Library, *, identifier: str | None, item: ItemIn | None, discovered_via: str,
               added_by: Literal["human", "agent"], actor: str) -> AddResult:
    """Resolve ``identifier`` in the registries (URLs and ISBNs are not resolved) and add the resolved work, else the
    ``item`` metadata. Raises ``ToolError``: ``invalid`` for a malformed identifier or nothing to add, ``not_found``
    when the identifier resolves to nothing and no metadata is given."""
    if identifier:
        try:
            ident = parse_identifier(identifier)
        except ValueError as e:
            raise ToolError("invalid", str(e)) from e
        r = resolve(sctx, ident) if ident.kind not in ("url", "isbn") else None
        if r is None and item is None:
            raise ToolError("not_found", f"{identifier} not found; supply item metadata to add it anyway", status=404)
        if r is not None:
            item = r.item
    if item is None:
        raise ToolError("invalid", "need identifier or item")
    return lib.add_item(item.model_copy(update={"discovered_via": discovered_via}), added_by=added_by, actor=actor)
