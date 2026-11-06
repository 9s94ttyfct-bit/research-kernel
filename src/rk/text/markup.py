"""JATS XML, BioC JSON and HTML to text layers (with hidden-content removal for HTML)."""

from __future__ import annotations

import re

import trafilatura
from lxml import etree, html as lhtml

from rk.text.layer import Page, TextLayer

_ZW = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em)?\s*(?:;|$)", re.I)


def _norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def sanitize_html(html: str) -> str:
    doc = lhtml.fromstring(html)
    for el in list(doc.iter(etree.Comment)):
        el.getparent().remove(el)
    for el in list(doc.iter()):
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        style = el.get("style") or ""
        if (
            tag in ("script", "style", "noscript", "template")
            or el.get("hidden") is not None
            or el.get("aria-hidden") == "true"
            or _HIDDEN_STYLE.search(style)
        ):
            parent = el.getparent()
            if parent is not None:
                el.drop_tree()
    return _ZW.sub("", lhtml.tostring(doc, encoding="unicode"))


def html_to_layer(html: str, sha: str) -> TextLayer:
    clean = sanitize_html(html)
    text = trafilatura.extract(clean, include_comments=False, include_tables=True, favor_recall=True) or ""
    return TextLayer(source_kind="html", pages=[Page(n=1, text=_ZW.sub("", text))], sha256=sha)


def _itertext(el) -> str:
    return _norm_ws("".join(el.itertext()))


def jats_to_layer(xml: bytes, sha: str) -> TextLayer:
    root = etree.fromstring(xml)
    lines: list[str] = []
    title = root.find(".//article-meta//article-title")
    if title is not None:
        lines.append(_itertext(title))
    for ab in root.findall(".//article-meta/abstract"):
        lines.append("Abstract")
        for p in ab.iter("p", "title"):
            t = _itertext(p)
            if t:
                lines.append(t)
    body = root.find(".//body")
    if body is not None:
        for el in body.iter("title", "p"):
            t = _itertext(el)
            if t:
                lines.append(t)
    return TextLayer(source_kind="jats", pages=[Page(n=1, text="\n\n".join(lines))], sha256=sha)


def bioc_to_layer(doc: dict, sha: str) -> TextLayer:
    docs = doc.get("documents") or []
    passages = docs[0].get("passages", []) if docs else []
    texts = [_norm_ws(p.get("text", "")) for p in passages if p.get("text")]
    return TextLayer(source_kind="bioc", pages=[Page(n=1, text="\n\n".join(texts))], sha256=sha)
