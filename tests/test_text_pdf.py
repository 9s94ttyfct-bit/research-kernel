from rk.text.layer import TextLayer, build_norm_keys, normalize_query
from rk.text.pdf import extract_pdf

from .conftest import DOCS


def test_norm_keys_hyphen_variants():
    d, j = build_norm_keys("fact-\nchecking and evi-\ndence")
    assert d.key == "factchecking and evidence"
    assert j.key == "fact-checking and evi-dence"


def test_pdfium_hyphen_marker():
    d, j = build_norm_keys("fact\ufffechecking")
    assert d.key == "factchecking" and j.key == "fact-checking"


def test_normalization_rules():
    d, _ = build_norm_keys("“Quoted” — text\u00ad with  \n spaces\u200b and ﬁ ")
    assert d.key == '"Quoted" - text with spaces and fi'


def test_map_points_into_original():
    t = "a  b\nc ﬁx"
    d, _ = build_norm_keys(t)
    assert t[d.map[d.key.index("c")]] == "c"
    i = d.key.index("fix")
    assert t[d.map[i]] == "ﬁ" and t[d.map[i + 1]] == "ﬁ" and t[d.map[i + 2]] == "x"


def test_normalize_query():
    assert normalize_query("  ‘a’ –  b ") == "'a' - b"


def test_pdf_pages_and_ligature():
    layer = extract_pdf(DOCS / "ligature.pdf")
    assert layer.source_kind == "pdf" and len(layer.pages) == 1
    assert "finding was robust across five" in normalize_query(layer.pages[0].text)


def test_pdf_two_pages():
    layer = extract_pdf(DOCS / "two_pages.pdf")
    assert [p.n for p in layer.pages] == [1, 2] and "copper catalysts" in layer.pages[1].text


def test_pdf_hidden_text_removed():
    txt = extract_pdf(DOCS / "hidden.pdf").full_text()
    assert "This visible sentence stays" in txt and "Visible tail line." in txt
    for hidden in ("IGNORE PREVIOUS", "SECRET TINY", "INVISIBLE MODE"):
        assert hidden not in txt


def test_layer_roundtrip(tmp_path):
    layer = extract_pdf(DOCS / "two_pages.pdf")
    layer.save(tmp_path / "l.json")
    assert TextLayer.load(tmp_path / "l.json") == layer
