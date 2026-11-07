"""Page-anchored text layers and normalization keys with offset maps."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_QUOTES = {c: "'" for c in "\u2018\u2019\u201a\u201b\u2032"} | {c: '"' for c in "\u201c\u201d\u201e\u201f\u2033"}
_DASHES = {c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
_DROP = {"\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff", "\r"}
_PDF_HYPHEN = "\ufffe"  # pdfium marks line-end hyphens with U+FFFE and joins the lines


class Page(BaseModel):
    n: int
    text: str


class TextLayer(BaseModel):
    source_kind: Literal["pdf", "jats", "bioc", "html", "abstract"]  # abstract: in memory only (rk.notes.abstract_layer)
    pages: list[Page]
    sha256: str

    def full_text(self) -> str:
        return "\f".join(p.text for p in self.pages)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> TextLayer:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


class NormKey(BaseModel):
    key: str
    map: list[int]


def _expand(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for i, ch in enumerate(text):
        if ch in _DROP:
            continue
        if ch == _PDF_HYPHEN:
            out.append((_PDF_HYPHEN, i))
            continue
        for c in unicodedata.normalize("NFKC", ch):
            c = _QUOTES.get(c, _DASHES.get(c, c))
            out.append((c, i))
    return out


def _is_word(c: str) -> bool:
    return c.isalnum()


def _build(chars: list[tuple[str, int]], dehyphenate: bool) -> NormKey:
    key: list[str] = []
    mp: list[int] = []
    i, n = 0, len(chars)
    while i < n:
        c, src = chars[i]
        # line-end hyphenation: "word-\n word" or pdfium's U+FFFE marker
        if c in ("-", _PDF_HYPHEN) and key and _is_word(key[-1]):
            j = i + 1
            saw_newline = c == _PDF_HYPHEN
            while j < n and chars[j][0] in " \t\n\f":
                saw_newline = saw_newline or chars[j][0] == "\n"
                j += 1
            if saw_newline and j < n and _is_word(chars[j][0]):
                if not dehyphenate:
                    key.append("-")
                    mp.append(src)
                i = j
                continue
            if c == _PDF_HYPHEN:
                key.append("-")
                mp.append(src)
                i += 1
                continue
        if c.isspace():
            if key and key[-1] != " ":
                key.append(" ")
                mp.append(src)
            i += 1
            continue
        key.append(c)
        mp.append(src)
        i += 1
    while key and key[-1] == " ":
        key.pop()
        mp.pop()
    return NormKey(key="".join(key), map=mp)


def build_norm_keys(text: str) -> tuple[NormKey, NormKey]:
    """Return (dehyphenated, hyphen_joined) normalization keys for ``text``."""
    chars = _expand(text)
    return _build(chars, dehyphenate=True), _build(chars, dehyphenate=False)


def normalize_query(s: str) -> str:
    return _build(_expand(s), dehyphenate=False).key.strip()
