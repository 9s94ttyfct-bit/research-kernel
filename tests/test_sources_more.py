import pytest

from rk.sources import arxiv, biorxiv, europepmc, pubmed, s2
from rk.sources.s2 import NotConfigured


def test_europepmc_search_and_fulltext(replay_http):
    hits = europepmc.search(replay_http, "quotation accuracy", open_access_only=True, page_size=3)
    assert len(hits) == 3 and all(h.ids.pmcid or h.ids.doi for h in hits)
    xml = europepmc.fulltext_xml(replay_http, "PMC4627914")
    assert xml and b"<article" in xml


def test_europepmc_bookshelf_records_are_books_and_chapters():
    """Calibration (critical3 export, 2026-11-12): the AHRQ report (PMID 42981454) was added from Europe PMC as a journal
    article with no venue and exported so. A book (pubType Book) has its publisher as venue; a chapter (a book whose
    title is not its own: GeneReviews' Loeys-Dietz Syndrome) is a book section with the book as venue."""
    book = {"source": "MED", "pmid": "42981454", "title": "AI and ML Tools: A Rapid Review, 2026 Review Update",
            "pubYear": "2026", "pubTypeList": {"pubType": ["Book", "Review"]},
            "bookOrReportDetails": {"publisher": "Agency for Healthcare Research and Quality (US), Rockville (MD)",
                                    "comprisingTitle": "AI and ML Tools: A Rapid Review, 2026 Review Update",
                                    "yearOfPublication": 2026}}
    it = europepmc.result_to_item(book)
    assert (it.item_type, it.venue, it.year) == ("book", "Agency for Healthcare Research and Quality (US), Rockville (MD)",
                                                 2026)
    chapter = {"source": "MED", "pmid": "20301312", "title": "Loeys-Dietz Syndrome", "pubYear": "1993",
               "pubTypeList": {"pubType": ["Review"]},
               "bookOrReportDetails": {"publisher": "University of Washington, Seattle, Seattle (WA)",
                                       "comprisingTitle": "GeneReviews®", "yearOfPublication": 1993}}
    it = europepmc.result_to_item(chapter)
    assert (it.item_type, it.title, it.venue) == ("bookSection", "Loeys-Dietz Syndrome", "GeneReviews®")
    article = {"source": "MED", "pmid": "1", "title": "A trial.", "pubYear": "2020",
               "journalInfo": {"journal": {"title": "The Lancet"}}}
    assert (europepmc.result_to_item(article).item_type, europepmc.result_to_item(article).venue) == (
        "journalArticle", "The Lancet")


def test_pubmed_esummary(replay_http):
    items = pubmed.esummary(replay_http, ["26528420"])
    assert len(items) == 1
    it = items[0]
    assert it.ids.pmid == "26528420" and it.ids.doi == "10.7717/peerj.1364" and it.year == 2015
    assert it.creators[0].family == "Jergas"


def test_pubmed_bookshelf_records_are_books_and_chapters():
    """Calibration (critical3 audit, 2026-11-12): a PubMed book (an AHRQ report, PMID 42981454) has an empty ``title``
    and its title in ``booktitle``; read as "(untitled)", V-REF failed the audit on a correct item. A chapter
    (``doctype`` ``chapter``, as GeneReviews' PMID 20301312) keeps its own title and its own date (``docdate``: its
    ``pubdate`` is the series'), with the book as its venue."""
    book = {"uid": "42981454", "title": "", "booktitle": "AI and ML Tools To (Semi-)Automate Evidence Synthesis",
            "doctype": "book", "source": "", "fulljournalname": "",
            "publishername": "Agency for Healthcare Research and Quality (US)", "pubdate": "2026 Aug", "docdate": "",
            "authors": [{"name": "Adam GP", "authtype": "Author"}], "articleids": []}
    it = pubmed.summary_to_item("42981454", book)
    assert (it.item_type, it.title, it.venue, it.year) == (
        "book", "AI and ML Tools To (Semi-)Automate Evidence Synthesis",
        "Agency for Healthcare Research and Quality (US)", 2026)
    chapter = {"uid": "20301312", "doctype": "chapter", "title": "Loeys-Dietz Syndrome.", "booktitle": "GeneReviews(®)",
               "source": "", "fulljournalname": "", "publishername": "University of Washington, Seattle",
               "pubdate": "1993", "docdate": "2008 Feb 28 [updated 2024 Sep 12]",
               "authors": [{"name": "Loeys BL", "authtype": "Author"}], "articleids": []}
    it = pubmed.summary_to_item("20301312", chapter)
    assert (it.item_type, it.title, it.venue, it.year) == ("bookSection", "Loeys-Dietz Syndrome", "GeneReviews(®)", 2008)
    it = pubmed.summary_to_item("20301312", {**chapter, "booktitle": "GeneReviews."})
    assert it.venue == "GeneReviews"


def test_pubmed_search_pipeline(replay_http):
    pmids = pubmed.esearch(replay_http, "quotation accuracy medical journal meta-analysis", retmax=3)
    assert "26528420" in pmids
    assert len(pubmed.esummary(replay_http, pmids)) == len(pmids)


def test_bioc(replay_http):
    doc = pubmed.bioc_json(replay_http, "PMC4627914")
    assert doc and doc.get("documents")


def test_bioc_non_json_body_is_no_document(tmp_path):
    import httpx

    from rk.net.http import HttpClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"[Error] : No result can be found."))
    http = HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None, transport=transport)
    assert pubmed.bioc_json(http, "PMC1234567") is None


def test_arxiv_query(replay_http):
    items = arxiv.query(replay_http, id_list=["2404.10774"], max_results=1)
    assert len(items) == 1 and items[0].ids.arxiv == "2404.10774" and items[0].item_type == "preprint"
    assert items[0].oa_urls == ["https://arxiv.org/pdf/2404.10774"]


def test_biorxiv(replay_http):
    it = biorxiv.details(replay_http, "10.1101/2024.12.31.630767")
    assert it and it.item_type == "preprint" and "astrocytes" in it.title.lower()


def test_s2_requires_key(replay_http):
    with pytest.raises(NotConfigured):
        s2.search(replay_http, "x", api_key=None)
    with pytest.raises(NotConfigured):
        s2.snippet_search(replay_http, "x", api_key=None)
