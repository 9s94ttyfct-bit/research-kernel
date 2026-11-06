from urllib.parse import parse_qs, urlsplit

import pytest

from rk.browser.adapters import ADAPTERS, valid
from rk.browser.adapters.publisher import find_pdf_url
from rk.config import Settings
from rk.tools.registry import ToolError

from .browser_sites import FIXTURES

PRIMO = Settings(primo_base="https://library.example.edu", primo_vid="INST:VIEW")


def page(name: str) -> str:
    return (FIXTURES / f"{name}_results.html").read_text()


def parse(site: str, base: str):
    a = ADAPTERS[site]
    html = page(site)
    hits = a.parse(html, base)
    return a, html, hits


def test_scholar_parses_fixture():
    a, html, hits = parse("scholar", "https://scholar.google.com/scholar?q=x")
    assert [h.title for h in hits] == ["Fixture study of ion migration in halide films",
                                       "Second fixture result on encapsulation"]  # citation-only record has no link
    h = hits[0]
    assert (h.authors, h.venue_year) == ("A Author, B Writer", "Journal of Fixture Materials, 2024 - publisher.example")
    assert (h.doi, h.pdf_url) == ("10.1234/fixture.001", "https://repo.example.edu/files/paper-one.pdf")
    assert h.snippet.startswith("A synthetic snippet") and h.source_site == "scholar"
    assert valid(a, hits, html)


def test_pubmed_parses_fixture():
    a, html, hits = parse("pubmed", "https://pubmed.ncbi.nlm.nih.gov/?term=x")
    assert [(h.pmid, h.url) for h in hits] == [("39000001", "https://pubmed.ncbi.nlm.nih.gov/39000001/"),
                                              ("38000002", "https://pubmed.ncbi.nlm.nih.gov/38000002/")]
    assert hits[0].title == "Fixture review of perovskite stability." and hits[0].doi == "10.5555/fixture.1001"
    assert hits[0].authors == "Author A, Writer B, Person C." and valid(a, hits, html)


def test_europepmc_parses_fixture():
    a, html, hits = parse("europepmc", "https://europepmc.org/search?query=x")
    assert [(h.title, h.pmid, h.url) for h in hits] == [
        ("Fixture review of perovskite stability.", "39000001", "https://europepmc.org/article/MED/39000001"),
        ("Open-access fixture article.", None, "https://europepmc.org/article/PMC/PMC9000002"),
        ("Preprint fixture.", None, "https://europepmc.org/article/PPR/PPR800003")]
    assert valid(a, hits, html)


def test_primo_parses_fixture():
    a, html, hits = parse("primo", "https://library.example.edu/discovery/search?vid=INST:VIEW")
    assert [h.title for h in hits] == ["Fixture handbook of perovskite devices / editor, A. Editor",
                                       "Moisture ingress in fixture films"]
    assert hits[1].url == "https://library.example.edu/discovery/fulldisplay?docid=cdi_fixture_10_5555_x2&vid=INST:VIEW"
    assert (hits[0].authors, hits[0].venue_year) == ("Editor, Alice, editor", "First edition.; 2023")
    assert (hits[1].authors, hits[1].venue_year) == ("Writer, Bob ; Person, Carol", "Fixture Energy, 2021, Vol.5 (2), p.10-20")
    assert hits[0].snippet == "BOOK" and valid(a, hits, html)


def test_summon_parses_fixture():
    a, html, hits = parse("summon", "https://library.example.summon.example/#!/search?q=x")
    assert [(h.title, h.authors) for h in hits] == [
        ("Fixture effect of oxide layers on two-dimensional perovskites", "Choi, Alice; Lee, Bob"),
        ("Enhanced stability of fixture cells", "Barranco, Carol")]
    assert valid(a, hits, html)


@pytest.mark.parametrize("site,renamed", [("scholar", "gs_r gs_or gs_scl"), ("pubmed", "full-docsum"),
                                          ("europepmc", "/article/"), ("primo", "prm-brief-result-container"),
                                          ("summon", "documentSummary")])
def test_invalid_markup_falls_back(site, renamed):
    a = ADAPTERS[site]
    html = page(site).replace(renamed, "renamed-by-site-redesign")
    hits = a.parse(html, "https://site.example/")
    assert not valid(a, hits, html)


def test_no_results_page_is_valid():
    a = ADAPTERS["pubmed"]
    html = "<html><body><div class='results-amount'><h3>No results were found.</h3></div></body></html>"
    assert a.parse(html, "https://pubmed.ncbi.nlm.nih.gov/") == [] and valid(a, [], html)


def test_search_urls():
    s = Settings()
    q = "perovskite solar cell"
    assert parse_qs(urlsplit(ADAPTERS["scholar"].search_url(q, s, 1)).query) == {"hl": ["en"], "q": [q],
                                                                                 "start": ["10"]}
    assert ADAPTERS["pubmed"].search_url(q, s).startswith("https://pubmed.ncbi.nlm.nih.gov/?term=perovskite")
    u = ADAPTERS["primo"].search_url(q, PRIMO)
    assert u.startswith("https://library.example.edu/discovery/search?query=any,contains,perovskite%20solar%20cell")
    assert "vid=INST:VIEW" in u and "search_scope=MyInst_and_CI" in u and "tab=Everything" in u


def test_primo_requires_config():
    with pytest.raises(ToolError) as e:
        ADAPTERS["primo"].search_url("x", Settings())
    assert e.value.type == "not_configured"
    with pytest.raises(ToolError):
        ADAPTERS["summon"].search_url("x", Settings())


LANDING = "https://www.publisher.example/doi/10.5555/abc"


@pytest.mark.parametrize("href,expected", [
    ("/science/article/pii/S0000/pdfft?md5=1&pid=1-s2.0-S0000-main.pdf",
     "https://www.publisher.example/science/article/pii/S0000/pdfft?md5=1&pid=1-s2.0-S0000-main.pdf"),
    ("/content/pdf/10.5555/abc.pdf", "https://www.publisher.example/content/pdf/10.5555/abc.pdf"),
    ("/doi/pdfdirect/10.5555/abc", "https://www.publisher.example/doi/pdfdirect/10.5555/abc"),
    ("/doi/pdf/10.5555/abc", "https://www.publisher.example/doi/pdf/10.5555/abc"),
    ("/en/content/articlepdf/2024/ee/d4ee00001a", "https://www.publisher.example/en/content/articlepdf/2024/ee/d4ee00001a"),
    ("https://www.publisher.example/articles/s41467-024-0001.pdf", "https://www.publisher.example/articles/s41467-024-0001.pdf"),
    ("/stamp/stamp.jsp?tp=&arnumber=123", "https://www.publisher.example/stamp/stamp.jsp?tp=&arnumber=123"),
])
def test_find_pdf_url_patterns(href, expected):
    html = f'<html><body><a href="/doi/full/10.5555/abc">Full text</a><a href="{href}">Download</a></body></html>'
    assert find_pdf_url(LANDING, html) == expected


def test_find_pdf_url_prefers_citation_pdf_url():
    html = ('<html><head><meta name="citation_pdf_url" content="https://cdn.publisher.example/x.pdf"></head>'
            '<body><a href="/doi/pdf/10.5555/abc">PDF</a></body></html>')
    assert find_pdf_url(LANDING, html) == "https://cdn.publisher.example/x.pdf"


def test_find_pdf_url_generic_pdf_link_and_none():
    assert find_pdf_url(LANDING, '<a href="/files/report.pdf">Download PDF</a>') == \
        "https://www.publisher.example/files/report.pdf"
    assert find_pdf_url(LANDING, '<a href="/files/data.pdf">Supplementary data</a><a href="/about">About</a>') is None
