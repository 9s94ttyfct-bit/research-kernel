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
    assert [f.name for f in files] == ["Li 2021 - Copper catalysts.pdf"]
    att = ps.db.execute("SELECT origin, sha256 FROM attachments WHERE item_id=?", (iid,)).fetchone()
    assert att["origin"] == "oa_api" and att["sha256"] == r.snapshot_sha


def test_no_source_returns_none(ps, lib, tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    ctx = SourceContext(http=HttpClient(tmp_path / "h.sqlite", user_agent="t", sleep=lambda s: None, transport=transport),
                        settings=Settings())
    iid = lib.add_item(ItemIn(title="Nothing", year=2020, oa_urls=["https://example.org/x.pdf"]), added_by="agent",
                       actor="t").item_id
    assert fetch_fulltext(ps, lib, ctx, iid, actor="t") is None


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
