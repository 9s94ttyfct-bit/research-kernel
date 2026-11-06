from rk.sources import crossref, openalex
from rk.sources.identifiers import Identifier

MAILTO = "rk-fixtures@users.noreply.github.com"
WAKEFIELD = "10.1016/s0140-6736(97)11096-0"


def test_wakefield_has_retraction_update(replay_http):
    w = crossref.get_work(replay_http, WAKEFIELD, MAILTO)
    assert w and w.item.year == 1998 and w.item.creators[0].family == "Wakefield"
    assert any(u.type == "retraction" and u.source == "retraction-watch" for u in w.updates)


def test_missing_doi_none(replay_http):
    assert crossref.get_work(replay_http, "10.9999/rk-does-not-exist-2026", MAILTO) is None
    assert openalex.get_work(replay_http, Identifier(kind="doi", value="10.9999/rk-does-not-exist-2026"), None) is None


def test_crossref_peerj_metadata(replay_http):
    w = crossref.get_work(replay_http, "10.7717/peerj.1364", MAILTO)
    assert w.item.item_type == "journalArticle" and w.item.year == 2015
    assert [c.family for c in w.item.creators] == ["Jergas", "Baethge"]
    assert w.item.ids.doi == "10.7717/peerj.1364" and w.updates == []


def test_openalex_peerj(replay_http):
    w = openalex.get_work(replay_http, Identifier(kind="doi", value="10.7717/peerj.1364"), None)
    assert w.item.year == 2015 and w.item.ids.pmid == "26528420" and w.item.ids.pmcid == "PMC4627914"
    assert w.item.abstract and "quotation" in w.item.abstract.lower()
    assert w.referenced_works and not w.is_retracted
    same = openalex.get_work(replay_http, Identifier(kind="pmid", value="26528420"), None)
    assert same.item.ids.openalex == w.item.ids.openalex


def test_openalex_wakefield_retracted(replay_http):
    assert openalex.get_work(replay_http, Identifier(kind="doi", value=WAKEFIELD), None).is_retracted


def test_openalex_search(replay_http):
    hits = openalex.search(replay_http, "quotation accuracy", api_key=None, per_page=5)
    assert len(hits) == 5 and all(h.title for h in hits)
