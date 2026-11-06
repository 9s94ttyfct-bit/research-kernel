from rk.browser.metadata import merge_with_api, parse_page_metadata, to_item
from rk.library.models import Creator, Ids, ItemIn
from rk.sources.resolver import ResolvedWork

URL = "https://publisher.example/article/123"

HIGHWIRE = """<html><head><title>Site title | Publisher</title>
<meta name="citation_title" content="Stability of perovskite solar cells">
<meta name="citation_author" content="Smith, Jane">
<meta name="citation_author" content="Wei Zhang">
<meta name="citation_author" content="O'Neil, Pat">
<meta name="citation_publication_date" content="2024/03/01">
<meta name="citation_journal_title" content="Energy Letters">
<meta name="citation_doi" content="doi:10.1000/XYZ123">
<meta name="citation_pdf_url" content="/content/pdf/123.pdf">
<script type="application/ld+json">{"@type": "ScholarlyArticle", "headline": "A different JSON-LD title"}</script>
</head><body>x</body></html>"""

JSONLD = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "WebPage", "name": "page"},
 {"@type": "ScholarlyArticle", "headline": "Graph neural networks for catalysts",
  "author": [{"@type": "Person", "givenName": "Ada", "familyName": "Lovelace"}, {"@type": "Person", "name": "Alan Turing"}],
  "datePublished": "2023-07-15", "isPartOf": {"@type": "Periodical", "name": "J. Chem. Inf."},
  "sameAs": "https://doi.org/10.2000/abc.1", "description": "We study GNNs."}]}
</script></head><body></body></html>"""

DC = """<html><head><meta name="DC.title" content="Dublin core paper">
<meta name="DC.creator" content="Curie, Marie"><meta name="DC.date" content="2019-01-02">
<meta name="DC.identifier" content="https://doi.org/10.3000/dc.9"></head><body></body></html>"""

COINS = """<html><body><span class="Z3988" title="ctx_ver=Z39.88-2004&amp;rft.atitle=COinS%20article&amp;rft.jtitle=Old%20Journal&amp;rft.date=2001&amp;rft.aulast=Bohr&amp;rft.aufirst=Niels&amp;rft_id=info%3Adoi%2F10.4000%2Fcoins.2"></span></body></html>"""


def test_highwire_full_record():
    m = parse_page_metadata(HIGHWIRE, URL)
    assert m.title == "Stability of perovskite solar cells"
    assert [(c.family, c.given) for c in m.creators] == [("Smith", "Jane"), ("Zhang", "Wei"), ("O'Neil", "Pat")]
    assert (m.year, m.venue, m.doi) == (2024, "Energy Letters", "10.1000/xyz123")
    assert m.pdf_url == "https://publisher.example/content/pdf/123.pdf"
    assert m.item_type == "journalArticle" and "highwire" in m.sources


def test_jsonld_when_no_highwire():
    m = parse_page_metadata(JSONLD, URL)
    assert m.title == "Graph neural networks for catalysts"
    assert [(c.family, c.given) for c in m.creators] == [("Lovelace", "Ada"), ("Turing", "Alan")]
    assert (m.year, m.venue, m.doi, m.abstract) == (2023, "J. Chem. Inf.", "10.2000/abc.1", "We study GNNs.")


def test_dublin_core_and_coins():
    d = parse_page_metadata(DC, URL)
    assert (d.title, d.creators[0].family, d.year, d.doi) == ("Dublin core paper", "Curie", 2019, "10.3000/dc.9")
    c = parse_page_metadata(COINS, URL)
    assert (c.title, c.venue, c.year, c.doi) == ("COinS article", "Old Journal", 2001, "10.4000/coins.2")
    assert (c.creators[0].family, c.creators[0].given) == ("Bohr", "Niels")


def test_precedence_highwire_over_jsonld():
    assert parse_page_metadata(HIGHWIRE, URL).title == "Stability of perovskite solar cells"


def test_plain_page_falls_back_to_title_as_webpage():
    m = parse_page_metadata("<html><head><title> A blog post </title></head></html>", URL)
    assert (m.title, m.item_type, m.doi) == ("A blog post", "webpage", None)


def test_no_identifier_gives_page_metadata_only():
    meta = parse_page_metadata(DC.replace("https://doi.org/10.3000/dc.9", "local-42"), URL)
    item, diff = merge_with_api(meta, None)
    assert item.ids.pairs() == [] and item.ids.url == URL and diff == {}
    assert item.discovered_via == "browser" and item.title == "Dublin core paper"


def test_api_wins_and_diff_recorded():
    meta = parse_page_metadata(HIGHWIRE, URL)
    api = ItemIn(title="Stability of perovskite solar cells: a review", year=2024, venue="Energy Letters",
                 creators=[Creator(family="Smith", given="Jane")], ids=Ids(doi="10.1000/xyz123"))
    item, diff = merge_with_api(meta, ResolvedWork(item=api))
    assert item.title == "Stability of perovskite solar cells: a review"
    assert diff["title"] == {"page": "Stability of perovskite solar cells", "api": item.title}
    assert "year" not in diff and item.ids.url == URL and item.discovered_via == "browser"


def test_hidden_meta_text_ignored():
    m = parse_page_metadata(HIGHWIRE.replace("Stability of", "Stab\u200bility of"), URL)
    assert m.title == "Stability of perovskite solar cells"


def test_to_item_maps_fields():
    item = to_item(parse_page_metadata(HIGHWIRE, URL))
    assert (item.ids.doi, item.year, item.discovered_via) == ("10.1000/xyz123", 2024, "browser")
