import pytest

from rk.sources.identifiers import normalize_arxiv, normalize_doi, parse_identifier


@pytest.mark.parametrize(
    "raw,kind,value",
    [
        ("10.1016/S0140-6736(97)11096-0", "doi", "10.1016/s0140-6736(97)11096-0"),
        ("https://doi.org/10.7717/PEERJ.1364", "doi", "10.7717/peerj.1364"),
        ("doi: 10.7717/peerj.1364", "doi", "10.7717/peerj.1364"),
        ("arXiv:2401.00001v2", "arxiv", "2401.00001"),
        ("https://arxiv.org/abs/hep-th/9901001v1", "arxiv", "hep-th/9901001"),
        ("2404.10774", "arxiv", "2404.10774"),
        ("PMC4627914", "pmcid", "PMC4627914"),
        ("26528420", "pmid", "26528420"),
        ("978-0-226-82667-7", "isbn", "9780226826677"),
        ("https://example.org/paper", "url", "https://example.org/paper"),
    ],
)
def test_parse_identifier(raw, kind, value):
    i = parse_identifier(raw)
    assert (i.kind, i.value) == (kind, value)


def test_bad_identifiers():
    assert normalize_doi("11.1/x") is None
    assert normalize_arxiv("not-an-id") is None
    with pytest.raises(ValueError):
        parse_identifier("hello world")
