"""From an article landing page (DOI → publisher, possibly through the library proxy) to its PDF link.

Order: the page's ``citation_pdf_url`` (most publishers set it), then publisher URL patterns, then a ``.pdf`` link
whose text says "PDF". Returns ``None`` when nothing qualifies; the caller then reports ``pdf_not_obtained`` or falls
back to generic navigation."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from rk.browser.adapters import doc_of, text

# (publisher family, href pattern), tried in this order on every link of the page
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("sciencedirect", re.compile(r"/pdfft(?:\?|$)")),
    ("springer", re.compile(r"/content/pdf/10\.\d{4,9}/.+\.pdf$")),
    ("wiley", re.compile(r"/doi/(?:pdfdirect|epdf)/10\.")),
    ("acs_tf_acm_sage", re.compile(r"/doi/pdf/10\.")),
    ("rsc", re.compile(r"/articlepdf/")),
    ("nature", re.compile(r"/articles/[^/?#]+\.pdf$")),
    ("ieee", re.compile(r"/stamp/stamp\.jsp\?")),
)


def find_pdf_url(landing_url: str, html: str) -> str | None:
    doc = doc_of(html)
    meta = doc.xpath('string(//meta[translate(@name, "CITATION_PDF_URL", "citation_pdf_url")="citation_pdf_url"]'
                     "/@content)").strip()
    if meta:
        return urljoin(landing_url, meta)
    links = [(a.get("href"), text(a) or "") for a in doc.xpath("//a[@href]")]
    for _, rx in PATTERNS:
        for href, _label in links:
            if rx.search(href.split("#")[0]):
                return urljoin(landing_url, href).replace("/doi/epdf/", "/doi/pdfdirect/")
    for href, label in links:
        if re.search(r"\.pdf(?:$|[?#])", href, re.IGNORECASE) and re.search(r"\bpdf\b", label, re.IGNORECASE):
            return urljoin(landing_url, href)
    return None
