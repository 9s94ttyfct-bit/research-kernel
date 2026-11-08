import httpx
import pytest

from rk.config import Settings
from rk.fulltext import attach_pdf, fetch_fulltext, latest_snapshot
from rk.library.models import Creator, Ids, ItemIn
from rk.library.service import Library
from rk.net.http import HttpClient
from rk.sources.resolver import SourceContext
from rk.store.project import ProjectStore
from rk.store.projections import rebuild
from rk.text.markup import html_to_layer, sanitize_html

from .conftest import DOCS


@pytest.fixture
def ps(tmp_path):
    return ProjectStore(tmp_path / "proj", "prj_T")


@pytest.fixture
def lib(ps):
    return Library(ps)


def _add(lib, **ids):
    return lib.add_item(
        ItemIn(title="Quotation accuracy in medical journal articles", year=2015, creators=[Creator(family="Jergas")],
               ids=Ids(**ids)),
        added_by="agent", actor="t",
    ).item_id


def test_jats_first(ps, lib, source_ctx):
    iid = _add(lib, pmcid="PMC4627914", doi="10.7717/peerj.1364")
    r = fetch_fulltext(ps, lib, source_ctx, iid, actor="t")
    assert r and r.kind == "jats" and r.page_count == 1
    sha, layer = latest_snapshot(ps, iid)
    assert sha == r.snapshot_sha and "quotation errors" in layer.pages[0].text.lower()
    assert ps.blobs.has(sha)
    assert lib.search("meta-regression")[0].item_id == iid


def test_doi_only_gets_pmcid_via_openalex(ps, lib, source_ctx):
    iid = _add(lib, doi="10.7717/peerj.1364")
    r = fetch_fulltext(ps, lib, source_ctx, iid, actor="t")
    assert r.kind == "jats" and lib.get(iid).ids.pmcid == "PMC4627914"


def test_arxiv_html(ps, lib, source_ctx):
    iid = lib.add_item(ItemIn(title="Arxiv paper", year=2024, ids=Ids(arxiv="2404.10774")), added_by="agent", actor="t").item_id
    r = fetch_fulltext(ps, lib, source_ctx, iid, actor="t")
    assert r and r.kind == "html" and len(latest_snapshot(ps, iid)[1].pages[0].text) > 2000


def test_pdf_fallback_creates_attachment(ps, lib, tmp_path):
    pdf = (DOCS / "two_pages.pdf").read_bytes()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"}))
    ctx = SourceContext(http=HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None, transport=transport),
                        settings=Settings())
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")],
                              oa_urls=["https://example.org/paper.pdf"]), added_by="agent", actor="t").item_id
    r = fetch_fulltext(ps, lib, ctx, iid, actor="t")
    assert r.kind == "pdf" and r.page_count == 2
    files = list((ps.files_dir / iid).iterdir())
    assert [f.name for f in files] == [f"Li 2021 - Copper catalysts [{r.snapshot_sha[:8]}].pdf"]
    att = ps.db.execute("SELECT origin, sha256 FROM attachments WHERE item_id=?", (iid,)).fetchone()
    assert att["origin"] == "oa_api" and att["sha256"] == r.snapshot_sha


def test_no_source_returns_none(ps, lib, tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    ctx = SourceContext(http=HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None, transport=transport),
                        settings=Settings())
    iid = lib.add_item(ItemIn(title="Nothing", year=2020, oa_urls=["https://example.org/x.pdf"]), added_by="agent",
                       actor="t").item_id
    assert fetch_fulltext(ps, lib, ctx, iid, actor="t") is None


def test_http_error_on_one_channel_falls_through_to_the_next(ps, lib, tmp_path):
    pdf = (DOCS / "two_pages.pdf").read_bytes()

    def handler(req):
        if req.url.host == "example.org":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        return httpx.Response(500)  # Europe PMC and BioC both fail

    ctx = SourceContext(http=HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None,
                                        transport=httpx.MockTransport(handler)), settings=Settings())
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, ids=Ids(pmcid="PMC1234567", doi="10.1/x"),
                              oa_urls=["https://example.org/paper.pdf"]), added_by="agent", actor="t").item_id
    r = fetch_fulltext(ps, lib, ctx, iid, actor="t")
    assert r is not None and r.kind == "pdf"


def test_html_hidden_text_removed():
    html = (DOCS / "hidden.html").read_text()
    assert "IGNORE" not in sanitize_html(html).replace("<title>", "")
    t = html_to_layer(html, "x").full_text()
    assert "IGNORE PREVIOUS" not in t and "alloying with silver" in t and "Zerowidth" in t
    # look-alike styles that are visible must survive
    assert "Half opacity text stays visible." in t and "Red text stays visible too." in t


def test_user_attach_pdf_and_rebuild(ps, lib):
    iid = lib.add_item(ItemIn(title="Perovskite page", year=2020, creators=[Creator(family="Wu")]), added_by="human",
                       actor="u").item_id
    r = attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="u")
    assert r.origin == "user_upload" and r.kind == "pdf"
    assert lib.search("catalysts")[0].item_id == iid
    before = lib.search("catalysts")
    rebuild(ps.db)
    assert lib.search("catalysts") == before
    with pytest.raises(ValueError):
        attach_pdf(ps, lib, iid, DOCS / "hidden.html", origin="user_upload", actor="u")


def test_two_pdfs_of_one_item_keep_separate_files(ps, lib):
    iid = lib.add_item(ItemIn(title="Same item", year=2020, creators=[Creator(family="Wu")]), added_by="human",
                       actor="u").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="u")
    attach_pdf(ps, lib, iid, DOCS / "hyphen.pdf", origin="user_upload", actor="u")
    rows = ps.db.execute("SELECT filename, sha256 FROM attachments WHERE item_id=?", (iid,)).fetchall()
    assert len({r["filename"] for r in rows}) == 2
    for r in rows:
        assert (ps.files_dir / iid / r["filename"]).read_bytes() == ps.blobs.path(r["sha256"]).read_bytes()


def test_rebuild_fails_loudly_when_text_layer_missing(ps, lib):
    iid = lib.add_item(ItemIn(title="Gone", year=2020), added_by="human", actor="u").item_id
    r = attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="u")
    (ps.text_dir / f"{r.snapshot_sha}.json").unlink()
    with pytest.raises(FileNotFoundError):
        rebuild(ps.db)
    assert lib.search("catalysts")[0].item_id == iid  # failed rebuild rolled back; old projections intact


def test_bioc_lone_surrogates_become_replacement(tmp_path):
    """A BioC JSON escape like \\ud800 decodes to a lone surrogate, which UTF-8 cannot encode (it broke saving the
    layer in the live demo); it becomes U+FFFD, a pair stays one character."""
    import json

    from rk.text.layer import TextLayer
    from rk.text.markup import bioc_to_layer

    doc = json.loads('{"documents": [{"passages": [{"text": "a \\ud800 b"}, {"text": "x \\ud835\\udc65 y"}]}]}')
    layer = bioc_to_layer(doc, "x")
    assert layer.pages[0].text == "a \ufffd b\n\nx \U0001d465 y"
    layer.save(tmp_path / "l.json")
    assert TextLayer.load(tmp_path / "l.json") == layer


def test_jats_lone_surrogates_become_replacement(tmp_path):
    from rk.text.layer import TextLayer
    from rk.text.markup import jats_to_layer

    xml = (b'<?xml version="1.0" encoding="UTF-8"?><article><body><p>char ref &#55296; and &#xDC00; here</p>'
           b"<p>raw \xed\xa0\x80 bytes</p><p>pair &#x1D465;</p></body></article>")
    layer = jats_to_layer(xml, "x")
    assert layer.pages[0].text == "char ref \ufffd and \ufffd here\n\nraw \ufffd\ufffd\ufffd bytes\n\npair \U0001d465"
    layer.save(tmp_path / "l.json")
    assert TextLayer.load(tmp_path / "l.json") == layer


_JATS_TABLE = (
    b'<?xml version="1.0" encoding="UTF-8"?><article><front><article-meta><article-title>Pilot</article-title>'
    b"<abstract><p>Short abstract.</p></abstract></article-meta></front><body>"
    b"<sec><title>Results</title><p>POD did not differ (Table 3).</p>"
    b'<table-wrap id="t3"><label>Table 3</label><caption><p>Incidence of postoperative delirium.</p></caption>'
    b"<table><thead><tr><th/><th>Melatonin Group (n = 43)</th><th>Placebo Group (n = 42)</th></tr></thead>"
    b"<tbody><tr><td>Incidence of POD, n (%)</td><td>7 (16.3)</td><td>2 (4.9)</td></tr></tbody></table>"
    b"<table-wrap-foot><fn><p>POD = postoperative delirium.</p></fn></table-wrap-foot></table-wrap>"
    b"</sec></body><floats-group><table-wrap><label>Table 4</label><caption><title>Secondary outcomes</title>"
    b"</caption><table><tr><td>Sedation</td><td>0</td><td>1</td></tr></table></table-wrap></floats-group></article>"
)


def test_jats_tables_are_in_the_text_layer():
    """Table cells (here the per-arm denominators of a trial, found only in its Table 3 in the live demo) are in the
    layer, after the body so the body keeps the offsets of layers built before tables were read: each table with its
    label and caption, one line per row with the cells kept apart, and its footnotes; floats-group tables too."""
    from rk.text.markup import jats_to_layer

    text = jats_to_layer(_JATS_TABLE, "x").pages[0].text
    before = ("Pilot\n\nAbstract\n\nShort abstract.\n\nResults\n\nPOD did not differ (Table 3).\n\n"
              "Incidence of postoperative delirium.\n\nPOD = postoperative delirium.")
    assert text.startswith(before + "\n\n")  # the body as it was read before
    assert text[len(before):] == (
        "\n\nTable 3. Incidence of postoperative delirium."
        "\n\n | Melatonin Group (n = 43) | Placebo Group (n = 42)"
        "\n\nIncidence of POD, n (%) | 7 (16.3) | 2 (4.9)"
        "\n\nPOD = postoperative delirium."
        "\n\nTable 4. Secondary outcomes"
        "\n\nSedation | 0 | 1")


def test_a_quote_from_a_jats_table_cell_passes_v_quote():
    from rk.text.markup import jats_to_layer
    from rk.verify.quote import verify_quote, verify_quote_at

    layer = jats_to_layer(_JATS_TABLE, "x")
    for quote in ("Melatonin Group (n = 43)", "Placebo Group (n = 42)", "Incidence of POD, n (%) | 7 (16.3) | 2 (4.9)"):
        r = verify_quote(quote, layer)
        assert r.ok, quote
        assert verify_quote_at(quote, layer, r.page, r.start, r.end).ok
    assert not verify_quote("7 (16.3) 2 (4.9)", layer).ok  # cells stay apart: no number runs into the next cell


def test_reading_tables_keeps_the_locators_of_an_earlier_layer():
    """A quote located in a layer built before tables were read is still verified at the same place in the layer
    rebuilt from the same JATS (the refetch overwrites text/<sha>.json)."""
    from rk.text.layer import Page, TextLayer
    from rk.text.markup import jats_to_layer
    from rk.verify.quote import verify_quote, verify_quote_at

    new = jats_to_layer(_JATS_TABLE, "x")
    old_text = new.pages[0].text.split("\n\nTable 3. ")[0]
    old = TextLayer(source_kind="jats", pages=[Page(n=1, text=old_text)], sha256="x")
    for quote in ("POD did not differ", "Incidence of postoperative delirium.", "Short abstract."):
        r = verify_quote(quote, old)
        assert r.ok and verify_quote_at(quote, new, r.page, r.start, r.end).ok, quote
