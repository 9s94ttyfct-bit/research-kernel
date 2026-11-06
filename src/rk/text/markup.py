"""JATS XML, BioC JSON and HTML to text layers (with hidden-content removal for HTML)."""

from __future__ import annotations

import re

import trafilatura
from lxml import etree
from lxml import html as lhtml

from rk.text.layer import Page, TextLayer

_ZW = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_HIDDEN_DECL = re.compile(
    r"display\s*:\s*none\b"
    r"|visibility\s*:\s*(?:hidden|collapse)\b"
    r"|opacity\s*:\s*0*(?:\.0+)?\s*(?:!important)?\s*(?:;|$)"
    r"|font-size\s*:\s*0*(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:!important)?\s*(?:;|$)"
    r"|color\s*:\s*transparent\b"
    r"|text-indent\s*:\s*-\d{3,}"
    r"|(?:left|top)\s*:\s*-\d{4,}",
    re.IGNORECASE,
)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR = re.compile(r"^([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)$")


def _norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def _hidden_selectors(doc) -> list[tuple[str | None, set[str], str | None]]:
    """Simple selectors (tag, .class, #id and combinations) whose <style> rules hide content.

    Complex selectors (descendant, attribute, pseudo-classes) are not evaluated.
    """
    out: list[tuple[str | None, set[str], str | None]] = []
    for st in doc.iter("style"):
        css = re.sub(r"/\*.*?\*/", "", st.text or "", flags=re.DOTALL)
        for sels, decls in _RULE.findall(css):
            if not _HIDDEN_DECL.search(decls):
                continue
            for sel in sels.split(","):
                m = _SIMPLE_SELECTOR.match(sel.strip())
                if not m or not (m.group(1) or m.group(2)):
                    continue
                parts = re.findall(r"[.#][\w-]+", m.group(2) or "")
                classes = {p[1:] for p in parts if p[0] == "."}
                ids = [p[1:] for p in parts if p[0] == "#"]
                out.append(((m.group(1) or "").lower() or None, classes, ids[0] if ids else None))
    return out


def _matches(el, sel: tuple[str | None, set[str], str | None]) -> bool:
    tag, classes, el_id = sel
    if tag and el.tag.lower() != tag:
        return False
    if el_id and el.get("id") != el_id:
        return False
    return classes <= set((el.get("class") or "").split())


def sanitize_html(html: str) -> str:
    doc = lhtml.fromstring(html)
    hidden_sels = _hidden_selectors(doc)
    for el in list(doc.iter(etree.Comment)):
        el.getparent().remove(el)
    for el in list(doc.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        tag = el.tag.lower()
        if (
            tag in ("script", "style", "noscript", "template")
            or el.get("hidden") is not None
            or el.get("aria-hidden") == "true"
            or _HIDDEN_DECL.search(el.get("style") or "")
            or any(_matches(el, s) for s in hidden_sels)
        ):
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
