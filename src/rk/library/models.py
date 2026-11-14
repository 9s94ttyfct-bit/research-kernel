"""Zotero-style library item models."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from rk.sources.identifiers import (
    normalize_arxiv,
    normalize_doi,
    normalize_isbn,
    normalize_nct,
    normalize_openalex,
    normalize_pmcid,
    normalize_pmid,
)

ItemType = Literal[
    "journalArticle", "preprint", "book", "bookSection", "conferencePaper",
    "thesis", "report", "dataset", "webpage",
]
DiscoveredVia = Literal["search", "backward", "forward", "snippet", "browser", "user", "ai_suggested", "import"]


class Creator(BaseModel):
    family: str
    given: str | None = None


class Ids(BaseModel):
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv: str | None = None
    openalex: str | None = None
    s2: str | None = None
    isbn: str | None = None
    nct: str | None = None  # a ClinicalTrials.gov registration number (a registry record's identity)
    url: str | None = None

    def normalized(self) -> Ids:
        return Ids(
            doi=normalize_doi(self.doi),
            pmid=normalize_pmid(self.pmid),
            pmcid=normalize_pmcid(self.pmcid),
            arxiv=normalize_arxiv(self.arxiv),
            openalex=normalize_openalex(self.openalex),
            s2=self.s2 or None,
            isbn=normalize_isbn(self.isbn),
            nct=normalize_nct(self.nct),
            url=self.url or None,
        )

    def pairs(self) -> list[tuple[str, str]]:
        """Identifier (kind, value) pairs used for de-duplication (url excluded)."""
        return [(k, v) for k, v in self.model_dump().items() if v and k != "url"]


# ISO 639-2 (bibliographic and terminology) codes and names the sources give, as ISO 639-1
_LANGUAGES = {
    "eng": "en", "english": "en", "fre": "fr", "fra": "fr", "french": "fr", "ger": "de", "deu": "de", "german": "de",
    "spa": "es", "spanish": "es", "ita": "it", "italian": "it", "por": "pt", "portuguese": "pt", "dut": "nl",
    "nld": "nl", "dutch": "nl", "chi": "zh", "zho": "zh", "chinese": "zh", "jpn": "ja", "japanese": "ja", "rus": "ru",
    "russian": "ru", "pol": "pl", "polish": "pl", "kor": "ko", "korean": "ko", "swe": "sv", "dan": "da", "nor": "no",
    "fin": "fi", "tur": "tr", "turkish": "tr", "ara": "ar", "heb": "he", "gre": "el", "ell": "el", "cze": "cs",
    "ces": "cs", "hun": "hu", "per": "fa", "fas": "fa",
}


def normalize_language(code: str | None) -> str | None:
    """A source's language code as ISO 639-1 where known (``"eng"`` -> ``"en"``), else lower-cased, without a region
    or script subtag (``"pt-BR"`` -> ``"pt"``); ``None`` for no language or an undetermined one (``"und"``)."""
    c = re.split(r"[-_]", (code or "").strip().lower(), maxsplit=1)[0]
    if not c or c == "und":
        return None
    return _LANGUAGES.get(c, c)


class ItemIn(BaseModel):
    item_type: ItemType = "journalArticle"
    title: str
    creators: list[Creator] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    ids: Ids = Field(default_factory=Ids)
    oa_urls: list[str] = Field(default_factory=list)
    discovered_via: DiscoveredVia = "search"
    csl: dict | None = None
    language: str | None = None  # the source's language field (ISO 639-1 where known); None when it gave none

    @field_validator("language")
    @classmethod
    def _language(cls, v: str | None) -> str | None:
        return normalize_language(v)


class Item(ItemIn):
    id: str
    added_by: Literal["human", "agent"]
    src_type: Literal["primary", "secondary", "tertiary", "unknown"] = "unknown"
    stance: Literal["supports", "challenges", "neutral", "unknown"] = "unknown"
    retraction: Literal["none", "retracted", "withdrawn", "partial_retraction", "eoc", "corrected", "unknown"] = \
        "unknown"
    ref_status: Literal["unverified", "pass", "mismatch", "not_found", "page_metadata_only"] = "unverified"
    tags: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    read_status: Literal["unread", "reading", "read"] = "unread"
    rating: int | None = None


UPDATABLE_FIELDS = frozenset({"retraction", "ref_status", "stance", "src_type", "read_status", "rating"})


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", "", t)
