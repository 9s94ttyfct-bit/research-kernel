import pytest

from rk.fulltext import _record_snapshot, attach_pdf
from rk.library.models import Creator, ItemIn
from rk.library.service import Library
from rk.notes import NoteIn, NoteRejected, add_note, list_notes, text_find, text_read
from rk.store.project import ProjectStore
from rk.store.projections import rebuild
from rk.text.layer import Page, TextLayer, normalize_query

from .conftest import DOCS


@pytest.fixture
def env(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="u").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="u")
    return ps, lib, iid


def test_verbatim_quote_stored_with_locator(env):
    ps, lib, iid = env
    r = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="about copper catalysts for CO2 reduction",
                                 role_in_source="main", keywords=["catalysis"]), actor="a")
    assert r.locator.page == 2 and r.verified["quote"]["ok"]
    sl = text_read(ps, iid, page=2, start=r.locator.start, end=r.locator.end)
    assert normalize_query(sl.text) == "about copper catalysts for CO2 reduction"
    notes = list_notes(ps, iid)
    assert len(notes) == 1 and notes[0]["hedge_level"] == 2 and notes[0]["snapshot_sha"]
    assert lib.search("catalysis")[0].item_id == iid  # note keywords are indexed
    assert lib.search("reduction")[0].item_id == iid


def test_misquote_rejected_and_not_stored(env):
    ps, lib, iid = env
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="about nickel catalysts for CO2 reduction",
                                 role_in_source="main"), actor="a")
    assert ei.value.reason == "quote_not_found" and "copper" in ei.value.details["best_snippet"]
    assert list_notes(ps) == []


def test_paraphrase_rules(env):
    ps, lib, iid = env
    span = "Moisture ingress is a known failure mode."
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="paraphrase", text="Moisture ingress is a known failure mode here.",
                                 source_span=span, role_in_source="sub"), actor="a")
    assert ei.value.reason == "too_close"
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="paraphrase", text="Water getting in breaks devices.",
                                 source_span="Moisture never matters.", role_in_source="sub"), actor="a")
    assert ei.value.reason == "span_not_found"
    ok = add_note(ps, lib, NoteIn(source_id=iid, kind="paraphrase", text="Water getting into the device commonly "
                                  "causes it to fail.", source_span=span, role_in_source="sub"), actor="a")
    assert ok.locator.page == 1 and ok.verified["paraphrase"]["ok"]


def test_paraphrase_cannot_copy_text_verbatim_or_from_neighbouring_sentence(env):
    ps, lib, iid = env
    span = "Moisture ingress is a known failure mode."
    # the whole "paraphrase" is a verbatim sentence from the source (different from the cited span)
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="paraphrase", text="Page one discusses perovskite stability.",
                                 source_span=span, role_in_source="sub"), actor="a")
    assert ei.value.reason == "too_close"
    # mostly copied from the sentence right before the cited span
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="paraphrase", text="Indeed, page one discusses perovskite "
                                 "stability.", source_span=span, role_in_source="sub"), actor="a")
    assert ei.value.reason == "too_close" and ei.value.details["ngram4_containment"] > 0.35
    assert list_notes(ps) == []


def test_quote_must_match_whole_words(env):
    ps, lib, iid = env
    with pytest.raises(NoteRejected):
        add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="opper catalysts for CO2 reductio",
                                 role_in_source="main"), actor="a")
    r = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="copper catalysts for CO2 reduction.",
                                 role_in_source="main"), actor="a")
    assert r.locator.page == 2


def test_text_find_case_insensitive_and_hyphen_joined(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    iid = lib.add_item(ItemIn(title="Fact checking", year=2022), added_by="human", actor="u").item_id
    attach_pdf(ps, lib, iid, DOCS / "hyphen.pdf", origin="user_upload", actor="u")
    assert text_find(ps, iid, "FACT-CHECKING")  # only in the hyphen-joined key
    assert text_find(ps, iid, "factchecking")  # only in the dehyphenated key
    hit = text_find(ps, iid, "EVIDENCE BASE")[0]
    assert "evi" in text_read(ps, iid, page=hit.page, start=hit.start, end=hit.end).text


def test_role_required_and_own_notes(env):
    ps, lib, iid = env
    with pytest.raises(ValueError):
        add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="copper catalysts"), actor="a")
    r = add_note(ps, lib, NoteIn(source_id=iid, kind="own", text="Compare with silver alloys."), actor="a")
    assert r.locator is None and list_notes(ps)[0]["hedge_level"] is None


def test_quote_needs_snapshot(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    iid = lib.add_item(ItemIn(title="No text", year=2020), added_by="human", actor="u").item_id
    with pytest.raises(NoteRejected) as ei:
        add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="x y z", role_in_source="main"), actor="a")
    assert ei.value.reason == "no_snapshot"


def test_text_find_locators_reproduce(env):
    ps, _, iid = env
    hits = text_find(ps, iid, "COPPER catalysts")
    assert len(hits) == 1 and hits[0].page == 2
    sl = text_read(ps, iid, page=hits[0].page, start=hits[0].start, end=hits[0].end)
    assert sl.text.lower() == "copper catalysts"
    assert text_find(ps, iid, "zzz") == []


def test_notes_rebuild(env):
    ps, lib, iid = env
    add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text="Moisture ingress", role_in_source="main"), actor="a")
    before = list_notes(ps), lib.search("ingress")
    rebuild(ps.db)
    assert (list_notes(ps), lib.search("ingress")) == before


def test_text_find_unicode_casefold(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    iid = lib.add_item(ItemIn(title="Straße", year=2020), added_by="human", actor="u").item_id
    text = "Die Große Straße führt zum ΣΟΦΟΣ-Institut."
    _record_snapshot(ps, iid, text.encode(), TextLayer(source_kind="html", pages=[Page(n=1, text=text)], sha256=""),
                     "html", None, "u")
    hit = text_find(ps, iid, "GROSSE STRASSE")[0]  # 'ß' casefolds to 'ss'; lower() would not match
    assert text_read(ps, iid, page=1, start=hit.start, end=hit.end).text == "Große Straße"
    hit = text_find(ps, iid, "σοφος")[0]  # final sigma 'ς' and 'Σ' fold to the same letter
    assert text_read(ps, iid, page=1, start=hit.start, end=hit.end).text == "ΣΟΦΟΣ"
