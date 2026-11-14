"""Scholarly identifier parsing and normalization."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

IdKind = Literal["doi", "pmid", "pmcid", "arxiv", "openalex", "isbn", "url"]

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
_ARXIV_PREFIX = re.compile(r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/)", re.IGNORECASE)
_PMCID_RE = re.compile(r"^(?:pmcid:\s*)?(PMC\d+)$", re.IGNORECASE)
_OPENALEX_RE = re.compile(r"^(?:https?://openalex\.org/)?(W\d+)$", re.IGNORECASE)
_PMID_RE = re.compile(r"^(?:pmid:\s*)?(\d{1,9})$", re.IGNORECASE)


class Identifier(BaseModel):
    kind: IdKind
    value: str


def normalize_doi(s: str | None) -> str | None:
    if not s:
        return None
    d = _DOI_PREFIX_RE.sub("", s.strip()).strip().lower()
    return d if _DOI_RE.match(d) else None


def normalize_arxiv(s: str | None) -> str | None:
    if not s:
        return None
    a = _ARXIV_PREFIX.sub("", s.strip()).strip()
    a = re.sub(r"\.pdf$", "", a)
    for rx in (_ARXIV_NEW, _ARXIV_OLD):
        m = rx.match(a)
        if m:
            return m.group(1)
    return None


def normalize_pmid(s: str | None) -> str | None:
    if not s:
        return None
    m = _PMID_RE.match(str(s).strip())
    return m.group(1).lstrip("0") or None if m else None


def normalize_pmcid(s: str | None) -> str | None:
    if not s:
        return None
    m = _PMCID_RE.match(str(s).strip())
    return m.group(1).upper() if m else None


def normalize_openalex(s: str | None) -> str | None:
    if not s:
        return None
    m = _OPENALEX_RE.match(str(s).strip())
    return m.group(1).upper() if m else None


_NCT_RE = re.compile(r"^(?:https?://(?:www\.)?clinicaltrials\.gov/(?:study|ct2/show)/)?(NCT\d{8})/?$", re.IGNORECASE)


def normalize_nct(s: str | None) -> str | None:
    """A ClinicalTrials.gov registration number (``NCT`` and eight digits, or its study-page address) as
    ``NCT01234567``; ``None`` for anything else."""
    if not s:
        return None
    m = _NCT_RE.match(str(s).strip())
    return m.group(1).upper() if m else None


def _isbn_ok(digits: str) -> bool:
    if len(digits) == 10:
        total = sum((10 - i) * (10 if c in "Xx" else int(c)) for i, c in enumerate(digits))
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits))
        return total % 10 == 0
    return False


def normalize_isbn(s: str | None) -> str | None:
    if not s:
        return None
    d = re.sub(r"^isbn:?\s*", "", s.strip(), flags=re.IGNORECASE).replace("-", "").replace(" ", "")
    if re.fullmatch(r"\d{9}[\dXx]|\d{13}", d) and _isbn_ok(d):
        return d.upper()
    return None


def parse_identifier(s: str) -> Identifier:
    s = (s or "").strip()
    if (d := normalize_doi(s)) is not None:
        return Identifier(kind="doi", value=d)
    if _ARXIV_PREFIX.match(s) and (a := normalize_arxiv(s)):
        return Identifier(kind="arxiv", value=a)
    if (p := normalize_pmcid(s)) is not None:
        return Identifier(kind="pmcid", value=p)
    if (o := normalize_openalex(s)) is not None:
        return Identifier(kind="openalex", value=o)
    if (i := normalize_isbn(s)) is not None:
        return Identifier(kind="isbn", value=i)
    if (pm := normalize_pmid(s)) is not None:
        return Identifier(kind="pmid", value=pm)
    if (a := normalize_arxiv(s)) is not None:
        return Identifier(kind="arxiv", value=a)
    if re.match(r"^https?://\S+$", s):
        return Identifier(kind="url", value=s)
    raise ValueError(f"unrecognized identifier: {s!r}")
