import pytest

from rk.sources import arxiv, biorxiv, europepmc, pubmed, s2
from rk.sources.s2 import NotConfigured


def test_europepmc_search_and_fulltext(replay_http):
    hits = europepmc.search(replay_http, "quotation accuracy", open_access_only=True, page_size=3)
    assert len(hits) == 3 and all(h.ids.pmcid or h.ids.doi for h in hits)
    xml = europepmc.fulltext_xml(replay_http, "PMC4627914")
    assert xml and b"<article" in xml


def test_pubmed_esummary(replay_http):
    items = pubmed.esummary(replay_http, ["26528420"])
    assert len(items) == 1
    it = items[0]
    assert it.ids.pmid == "26528420" and it.ids.doi == "10.7717/peerj.1364" and it.year == 2015
    assert it.creators[0].family == "Jergas"


def test_pubmed_search_pipeline(replay_http):
    pmids = pubmed.esearch(replay_http, "quotation accuracy medical journal meta-analysis", retmax=3)
    assert "26528420" in pmids
    assert len(pubmed.esummary(replay_http, pmids)) == len(pmids)


def test_bioc(replay_http):
    doc = pubmed.bioc_json(replay_http, "PMC4627914")
    assert doc and doc.get("documents")


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
