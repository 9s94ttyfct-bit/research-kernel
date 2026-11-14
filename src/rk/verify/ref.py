"""V-REF: a cited reference must exist and its metadata must match the registry record."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from rk.library.models import ItemIn
from rk.sources.identifiers import parse_identifier
from rk.sources.resolver import ResolvedWork, SourceContext, resolve

TITLE_MIN = 90.0
YEAR_TOLERANCE = 1

RefVerdict = Literal["pass", "mismatch", "not_found", "page_metadata_only"]


class CitedRef(BaseModel):
    identifier: str | None = None
    title: str
    first_author: str | None = None
    year: int | None = None
    authors: list[str] = []  # every author's family name, in the cited order, when known (a library item's)


class RefResult(BaseModel):
    verdict: RefVerdict
    resolved: ItemIn | None = None
    details: dict = {}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).casefold()
    return "".join(c for c in s if not unicodedata.combining(c))


def _title_score(a: str, b: str) -> float:
    clean = lambda s: re.sub(r"[^\w\s]", " ", _fold(s))
    return float(fuzz.token_set_ratio(clean(a), clean(b)))


def _author_match(cited: str, family: str) -> bool:
    """True when the record's family name appears in the cited author string: as a contiguous run of word tokens
    ('van der Berg' in 'J. van der Berg'), joined ('vanderberg'), or — for scripts written without spaces (CJK) —
    as a substring."""
    fam = re.findall(r"\w+", _fold(family))
    if not fam:
        return False
    toks = re.findall(r"\w+", _fold(cited))
    n = len(fam)
    if any(toks[i : i + n] == fam for i in range(len(toks) - n + 1)):
        return True
    joined = "".join(fam)
    if joined in toks:
        return True
    return not joined.isascii() and joined in "".join(toks)


def _same_authors(cited: list[str], record: list[str]) -> bool:
    """Whether two lists of family names hold the same authors, in any order (folded, punctuation ignored)."""
    key = lambda names: sorted(" ".join(re.findall(r"\w+", _fold(n))) for n in names)
    return bool(cited) and len(cited) == len(record) and key(cited) == key(record)


def compare(cited: CitedRef, resolved: ResolvedWork | None) -> RefResult:
    if resolved is None:
        return RefResult(verdict="not_found", details={"identifier": cited.identifier})
    item = resolved.item
    details: dict = {"sources": resolved.sources_used}
    ok = True
    if cited.title.strip():
        score = _title_score(cited.title, item.title)
        details["title"] = {"cited": cited.title, "record": item.title, "score": score, "ok": score >= TITLE_MIN}
        ok &= score >= TITLE_MIN
    if cited.first_author and item.creators:
        m = _author_match(cited.first_author, item.creators[0].family)
        details["first_author"] = {"cited": cited.first_author, "record": item.creators[0].family, "ok": m}
        # calibration (srrepro10, 2026-11-13): PubMed and a DOI's Crossref deposit may order the same authors
        # differently (the corresponding author first); the same author set is no wrong reference
        if not m and _same_authors(cited.authors, [c.family for c in item.creators if c.family]):
            m = True
            details["first_author"].update(ok=True, order="the same authors in another order")
        ok &= m
    if cited.year is not None and item.year is not None:
        y = abs(cited.year - item.year) <= YEAR_TOLERANCE
        details["year"] = {"cited": cited.year, "record": item.year, "ok": y}
        ok &= y
    return RefResult(verdict="pass" if ok else "mismatch", resolved=item, details=details)


def verify_reference(ctx: SourceContext, cited: CitedRef) -> tuple[RefResult, ResolvedWork | None]:
    if not cited.identifier:
        return RefResult(verdict="page_metadata_only", details={"reason": "no identifier"}), None
    try:
        ident = parse_identifier(cited.identifier)
    except ValueError:
        return RefResult(verdict="not_found", details={"reason": "unparseable identifier"}), None
    if ident.kind in ("url", "isbn"):
        return RefResult(verdict="page_metadata_only", details={"reason": f"{ident.kind} cannot be resolved"}), None
    resolved = resolve(ctx, ident)
    return compare(cited, resolved), resolved
