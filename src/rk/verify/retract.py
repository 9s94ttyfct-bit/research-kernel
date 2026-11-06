"""V-RETRACT: union of Crossref update notices and OpenAlex ``is_retracted``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rk.sources.resolver import ResolvedWork

RetractionStatus = Literal["none", "retracted", "withdrawn", "partial_retraction", "eoc", "corrected", "unknown"]

_UPDATE_MAP: dict[str, RetractionStatus] = {
    "retraction": "retracted",
    "withdrawal": "withdrawn",
    "removal": "withdrawn",
    "partial_retraction": "partial_retraction",
    "expression_of_concern": "eoc",
    "correction": "corrected",
    "erratum": "corrected",
    "corrigendum": "corrected",
}
_PRIORITY: list[RetractionStatus] = ["retracted", "withdrawn", "partial_retraction", "eoc", "corrected"]
# statuses that put an item into the flag collection and project-status warnings
FLAGGED: frozenset[str] = frozenset({"retracted", "withdrawn", "partial_retraction", "eoc"})


class RetractionInfo(BaseModel):
    status: RetractionStatus
    sources: list[str] = []
    notice_doi: str | None = None
    date: str | None = None  # the earliest Crossref notice date of this status ("YYYY[-MM[-DD]]"); OpenAlex has none


def retraction_status(resolved: ResolvedWork | None) -> RetractionInfo:
    if resolved is None or not ({"crossref", "openalex"} & set(resolved.sources_used)):
        return RetractionInfo(status="unknown")
    found: dict[RetractionStatus, tuple[list[str], str | None, list[str]]] = {}
    for u in resolved.retraction_updates:
        st = _UPDATE_MAP.get(u.type)
        if st:
            srcs, doi, dates = found.get(st, ([], None, []))
            srcs.append(f"crossref:{u.source}")
            dates += [u.date] if u.date else []
            found[st] = (srcs, doi or u.doi, dates)
    if resolved.openalex is not None and resolved.openalex.is_retracted:
        srcs, doi, dates = found.get("retracted", ([], None, []))
        srcs.append("openalex")
        found["retracted"] = (srcs, doi, dates)
    for st in _PRIORITY:
        if st in found:
            srcs, doi, dates = found[st]
            return RetractionInfo(status=st, sources=sorted(set(srcs)), notice_doi=doi,
                                  date=min(dates) if dates else None)
    return RetractionInfo(status="none", sources=[s for s in resolved.sources_used if s in ("crossref", "openalex")])
