from rk.sources.identifiers import parse_identifier
from rk.sources.resolver import resolve, search


def test_resolve_doi_merges_sources(source_ctx):
    r = resolve(source_ctx, parse_identifier("10.7717/peerj.1364"))
    assert r.sources_used == ["crossref", "openalex"]
    assert r.item.ids.pmid == "26528420" and r.item.ids.openalex and r.item.abstract


def test_resolve_wakefield_updates(source_ctx):
    r = resolve(source_ctx, parse_identifier("10.1016/S0140-6736(97)11096-0"))
    assert any(u.type == "retraction" for u in r.retraction_updates) and r.openalex.is_retracted


def test_resolve_missing(source_ctx):
    assert resolve(source_ctx, parse_identifier("10.9999/rk-does-not-exist-2026")) is None


def test_resolve_pmid(source_ctx):
    r = resolve(source_ctx, parse_identifier("26528420"))
    assert r.item.ids.doi == "10.7717/peerj.1364" and "openalex" in r.sources_used


def test_resolve_arxiv(source_ctx):
    r = resolve(source_ctx, parse_identifier("arXiv:2404.10774"))
    assert r.item.item_type == "preprint" and r.item.ids.arxiv == "2404.10774"
    assert r.sources_used[0] == "arxiv"


def test_search_sources(source_ctx):
    assert len(search(source_ctx, "quotation accuracy", "openalex", limit=5)) == 5
