import pytest

from rk.library.formats import FORMATS, export_bibtex, import_bibtex
from rk.library.models import Creator, Ids, ItemIn

from .conftest import DOCS

ITEMS = [
    ItemIn(item_type="journalArticle", title="Deep learning for proteins", year=2020, venue="Nature",
           creators=[Creator(family="Smith", given="Ann"), Creator(family="Lee")], ids=Ids(doi="10.1000/abc", pmid="42")),
    ItemIn(item_type="preprint", title="Deep thoughts", year=2020, creators=[Creator(family="Smith", given="Bo")],
           ids=Ids(arxiv="2401.00001")),
    ItemIn(item_type="book", title="The Craft of Research", year=2024, creators=[Creator(family="Booth")],
           venue="University of Chicago Press", ids=Ids(isbn="9780226826677")),
]


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_roundtrip(fmt):
    export, imp = FORMATS[fmt]
    back = imp(export(ITEMS))
    assert len(back) == len(ITEMS)
    for a, b in zip(ITEMS, back, strict=True):
        assert b.title == a.title
        assert [c.family for c in b.creators] == [c.family for c in a.creators]
        assert b.year == a.year and b.item_type == a.item_type
        assert b.ids.doi == a.ids.doi and b.ids.arxiv == a.ids.arxiv


def test_bibtex_citation_key_collision():
    text = export_bibtex([ITEMS[0], ITEMS[0]])
    assert "smith2020deep," in text and "smith2020deepa," in text


def test_import_sample_bib():
    items = import_bibtex((DOCS / "sample.bib").read_text())
    by_title = {i.title: i for i in items}
    assert [i.item_type for i in items] == ["journalArticle", "book", "conferencePaper", "preprint", "journalArticle"]
    assert by_title["A Preprint on Things"].ids.arxiv == "2404.10774"
    acc = items[4]
    assert acc.title == "Größe Effekte"
    assert [(c.family, c.given) for c in acc.creators] == [("Müller", "Jürgen"), ("van der Berg", "Anna")]
    assert items[0].ids.doi == "10.7717/peerj.1364" and items[1].ids.isbn == "9780226826677"
