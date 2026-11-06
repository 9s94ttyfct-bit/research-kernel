"""Text layers used to build and evaluate the V-QUOTE/V-PARA/V-HEDGE gold set (all offline fixtures)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from rk.net.recording import request_key
from rk.text.layer import TextLayer
from rk.text.markup import bioc_to_layer, html_to_layer, jats_to_layer
from rk.text.pdf import extract_pdf

ROOT = Path(__file__).parent
DOCS = ROOT / "fixtures" / "docs"
HTTP = ROOT / "fixtures" / "http"


def _fixture_body(url: str) -> str:
    key, _ = request_key("GET", url)
    return json.loads((HTTP / f"{key}.json").read_text(encoding="utf-8"))["body"]


@cache
def layer(source_id: str) -> TextLayer:
    if source_id.startswith("pdf:"):
        return extract_pdf(DOCS / source_id[4:])
    if source_id.startswith("jats:"):
        pmcid = source_id[5:]
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
        return jats_to_layer(_fixture_body(url).encode(), "x")
    if source_id == "bioc:PMC4627914":
        doc = json.loads(_fixture_body(
            "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/PMC4627914/unicode"))
        return bioc_to_layer(doc[0] if isinstance(doc, list) else doc, "x")
    if source_id == "html:arxiv2404.10774":
        return html_to_layer(_fixture_body("https://arxiv.org/html/2404.10774"), "x")
    raise KeyError(source_id)


# Domains: synthetic PDFs (layout edge cases); medicine (PMC4627914 as JATS and BioC); computer science
# (arXiv 2404.10774 HTML); chemistry (PMC13614178, JACS); biology (PMC13434336, Nucleic Acids Res).
SOURCES = ("pdf:hyphen.pdf", "pdf:ligature.pdf", "pdf:two_pages.pdf", "jats:PMC4627914", "bioc:PMC4627914",
           "html:arxiv2404.10774", "jats:PMC13614178", "jats:PMC13434336")
