"""PDF text extraction with per-character hidden-text filtering (pypdfium2)."""

from __future__ import annotations

import ctypes
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from rk.canon import sha256_hex
from rk.text.layer import Page, TextLayer

MIN_FONT_PT = 1.0
INVISIBLE_RENDER_MODE = 3
WHITE_MIN = 250


def _visible(tp_raw, i: int) -> bool:
    if raw.FPDFText_IsGenerated(tp_raw, i):
        return True
    size = raw.FPDFText_GetFontSize(tp_raw, i)
    if 0 < size < MIN_FONT_PT:
        return False
    obj = raw.FPDFText_GetTextObject(tp_raw, i)
    if obj and raw.FPDFTextObj_GetTextRenderMode(obj) == INVISIBLE_RENDER_MODE:
        return False
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if raw.FPDFText_GetFillColor(tp_raw, i, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)):
        if min(r.value, g.value, b.value) >= WHITE_MIN or a.value == 0:
            return False
    return True


def _page_text(page) -> str:
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        out: list[str] = []
        for i in range(n):
            cp = raw.FPDFText_GetUnicode(tp.raw, i)
            ch = chr(cp) if cp else ""
            if not ch:
                continue
            if ch.isspace() or _visible(tp.raw, i):
                out.append(ch)
        return "".join(out).replace("\r\n", "\n")
    finally:
        tp.close()


def extract_pdf(path: Path | str, data: bytes | None = None) -> TextLayer:
    blob = data if data is not None else Path(path).read_bytes()
    doc = pdfium.PdfDocument(blob)
    try:
        pages = [Page(n=i + 1, text=_page_text(doc[i])) for i in range(len(doc))]
    finally:
        doc.close()
    return TextLayer(source_kind="pdf", pages=pages, sha256=sha256_hex(blob))
