"""V-RETRACT: union of Crossref update notices and OpenAlex ``is_retracted``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rk.sources.resolver import ResolvedWork

RetractionStatus = Literal["none", "retracted", "withdrawn", "eoc", "corrected", "unknown"]

_UPDATE_MAP: dict[str, RetractionStatus] = {
    "retraction": "retracted",
    "withdrawal": "withdrawn",
    "removal": "withdrawn",
    "expression_of_concern": "eoc",
    "correction": "corrected",
    "erratum": "corrected",
    "corrigendum": "corrected",
}
_PRIORITY: list[RetractionStatus] = ["retracted", "withdrawn", "eoc", "corrected"]


class RetractionInfo(BaseModel):
    status: RetractionStatus
    sources: list[str] = []
    notice_doi: str | None = None


def retraction_status(resolved: ResolvedWork | None) -> RetractionInfo:
    if resolved is None or not ({"crossref", "openalex"} & set(resolved.sources_used)):
        return RetractionInfo(status="unknown")
    found: dict[RetractionStatus, tuple[list[str], str | None]] = {}
    for u in resolved.retraction_updates:
        st = _UPDATE_MAP.get(u.type)
        if st:
            srcs, doi = found.get(st, ([], None))
            srcs.append(f"crossref:{u.source}")
            found[st] = (srcs, doi or u.doi)
    if resolved.openalex is not None and resolved.openalex.is_retracted:
        srcs, doi = found.get("retracted", ([], None))
        srcs.append("openalex")
        found["retracted"] = (srcs, doi)
    for st in _PRIORITY:
        if st in found:
            srcs, doi = found[st]
            return RetractionInfo(status=st, sources=sorted(set(srcs)), notice_doi=doi)
    return RetractionInfo(status="none", sources=[s for s in resolved.sources_used if s in ("crossref", "openalex")])
