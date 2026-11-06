"""Record live HTTP fixtures used by the offline test-suite.

Usage: RK_FIXTURE_MAILTO=you@example.org uv run python scripts/record_fixtures.py [group ...]
Groups: core (Crossref/OpenAlex), more (Europe PMC, PubMed, BioC, arXiv, bioRxiv), traps, fulltext.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from rk.net.http import HttpClient
from rk.net.recording import RecordingTransport
from rk.sources import arxiv, biorxiv, crossref, europepmc, openalex, pubmed
from rk.sources.identifiers import Identifier

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "http"
MAILTO = os.environ.get("RK_FIXTURE_MAILTO", "rk-fixtures@users.noreply.github.com")

WAKEFIELD = "10.1016/s0140-6736(97)11096-0"
SURGISPHERE = "10.1016/s0140-6736(20)31180-6"
PEERJ = "10.7717/peerj.1364"
MISSING = "10.9999/rk-does-not-exist-2026"


def client() -> HttpClient:
    cache = Path(tempfile.mkdtemp()) / "c.sqlite"
    return HttpClient(cache, user_agent=f"rk-fixture-recorder (mailto:{MAILTO})", transport=RecordingTransport(OUT))


def core(h: HttpClient) -> None:
    for doi in (WAKEFIELD, PEERJ, MISSING, SURGISPHERE):
        crossref.get_work(h, doi, MAILTO)
        openalex.get_work(h, Identifier(kind="doi", value=doi), None)
    openalex.search(h, "quotation accuracy", api_key=None, per_page=5)
    openalex.get_work(h, Identifier(kind="pmid", value="26528420"), None)


def more(h: HttpClient) -> None:
    europepmc.search(h, "quotation accuracy", open_access_only=True, page_size=3)
    europepmc.fulltext_xml(h, "PMC4627914")
    pubmed.esummary(h, pubmed.esearch(h, "quotation accuracy medical journal meta-analysis", retmax=3))
    pubmed.esummary(h, ["26528420"])
    pubmed.bioc_json(h, "PMC4627914")
    arxiv.query(h, id_list=["2404.10774"], max_results=1)
    openalex.get_work(h, Identifier(kind="doi", value="10.48550/arxiv.2404.10774"), None)
    biorxiv.details(h, "10.1101/2024.12.31.630767")


def traps(h: HttpClient) -> None:
    for kind in ("expression_of_concern", "correction"):
        data, _ = h.get_json(
            "https://api.crossref.org/works",
            {"filter": f"update-type:{kind}", "rows": 1, "mailto": MAILTO, "select": "DOI,update-to"},
        )
        notice = data["message"]["items"][0]
        target = notice["update-to"][0]["DOI"]
        print(kind, "notice", notice["DOI"], "target", target)
        crossref.get_work(h, target, MAILTO)
        openalex.get_work(h, Identifier(kind="doi", value=target.lower()), None)


def fulltext(h: HttpClient) -> None:
    h.get(arxiv.html_url("2404.10774"))


# Gold-set sources: one chemistry and one biology OA article (spec §10 domain coverage).
GOLD_PMCIDS = {"chemistry": "PMC13614178", "biology": "PMC13434336"}


def gold(h: HttpClient) -> None:
    for pmcid in GOLD_PMCIDS.values():
        assert europepmc.fulltext_xml(h, pmcid), pmcid


GROUPS = {"core": core, "more": more, "traps": traps, "fulltext": fulltext, "gold": gold}

if __name__ == "__main__":
    h = client()
    for g in sys.argv[1:] or list(GROUPS):
        print("recording", g)
        GROUPS[g](h)
    h.close()
    print("fixtures:", len(list(OUT.glob("*.json"))))
