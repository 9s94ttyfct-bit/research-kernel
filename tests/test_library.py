import pytest
from pydantic import ValidationError

from rk.library.models import Creator, Ids, ItemIn
from rk.library.service import DEFAULT_COLLECTIONS, Library
from rk.store.project import ProjectStore
from rk.store.projections import rebuild


def item(title="A study of things", year=2020, **ids):
    return ItemIn(
        title=title, year=year, creators=[Creator(family="Smith", given="Ann")], abstract="Abstract text here.",
        ids=Ids(**ids),
    )


@pytest.fixture
def lib(tmp_path):
    return Library(ProjectStore(tmp_path / "p", "prj_T"))


def test_dedupe_identifier_variants(lib):
    a = lib.add_item(item(doi="10.1000/ABC.1"), added_by="agent", actor="t")
    b = lib.add_item(item(title="Other title", doi="https://doi.org/10.1000/abc.1", pmid="123"), added_by="agent", actor="t")
    c = lib.add_item(item(title="Arxiv paper", year=2024, arxiv="2401.00001v2"), added_by="agent", actor="t")
    d = lib.add_item(item(title="Arxiv paper v1", year=2024, arxiv="arXiv:2401.00001"), added_by="agent", actor="t")
    assert a.created and a.item_id == b.item_id and not b.created
    assert lib.get(a.item_id).ids.pmid == "123"
    assert c.item_id == d.item_id and not d.created
    assert len(lib.list()) == 2


def test_title_year_dedupe(lib):
    x = lib.add_item(item(title="Deep  Learning: a Review", year=2020), added_by="agent", actor="t")
    y = lib.add_item(item(title="deep learning — A review", year=2020), added_by="agent", actor="t")
    z = lib.add_item(item(title="deep learning — A review", year=2021), added_by="agent", actor="t")
    assert x.item_id == y.item_id and z.item_id != x.item_id


def test_tags_collections_search(lib):
    i = lib.add_item(item(title="Perovskite stability under humidity"), added_by="human", actor="u").item_id
    lib.tag(i, add=["perovskite", "stability"], actor="u")
    lib.tag(i, remove=["stability"], actor="u")
    col = lib.create_collection("Included", actor="u")
    lib.collect(i, col, actor="u")
    got = lib.get(i)
    assert got.tags == ["perovskite"] and col in got.collection_ids
    hits = lib.search("humidity")
    assert hits and hits[0].item_id == i and "[humidity]" in hits[0].snippet
    assert lib.search("") == [] and lib.search("nonexistentword") == []
    smart = lib.create_collection("Humidity papers", query="humidity", actor="u")
    assert [x.id for x in lib.list(smart)] == [i]
    with pytest.raises(ValueError):
        lib.collect(i, smart, actor="u")


def test_update_and_validation(lib):
    i = lib.add_item(item(), added_by="agent", actor="t").item_id
    lib.update(i, retraction="retracted", ref_status="pass", actor="k")
    assert lib.get(i).retraction == "retracted"
    with pytest.raises(ValueError):
        lib.update(i, title="nope", actor="k")
    with pytest.raises(ValidationError):
        lib.update(i, retraction="bogus", actor="k")


def test_default_collections_idempotent(lib):
    a = lib.ensure_default_collections(actor="k")
    b = lib.ensure_default_collections(actor="k")
    assert a == b and set(a) == set(DEFAULT_COLLECTIONS)


def test_library_rebuild_equal(lib):
    i = lib.add_item(item(doi="10.1000/x"), added_by="agent", actor="t").item_id
    lib.add_item(item(doi="10.1000/x", pmid="9"), added_by="agent", actor="t")
    lib.tag(i, add=["x"], actor="u")
    c = lib.create_collection("C", actor="u")
    lib.collect(i, c, actor="u")
    lib.update(i, stance="supports", actor="u")
    before = lib.get(i).model_dump(), lib.search("study")
    rebuild(lib.db)
    assert (lib.get(i).model_dump(), lib.search("study")) == before


def test_merge_fills_a_missing_abstract(lib):
    bare = item(doi="10.1000/m").model_copy(update={"abstract": None})
    i = lib.add_item(bare, added_by="agent", actor="t").item_id
    assert lib.get(i).abstract is None
    lib.add_item(item(doi="10.1000/m").model_copy(update={"abstract": "Later abstract on humidity."}),
                 added_by="agent", actor="t")
    assert lib.get(i).abstract == "Later abstract on humidity."
    assert [h.item_id for h in lib.search("humidity")] == [i]  # the FTS row has it too
    before = lib.get(i).model_dump(), lib.search("humidity")
    rebuild(lib.db)
    assert (lib.get(i).model_dump(), lib.search("humidity")) == before


def test_merge_keeps_the_first_abstract(lib):
    i = lib.add_item(item(doi="10.1000/k").model_copy(update={"abstract": "First abstract."}),
                     added_by="agent", actor="t").item_id
    lib.add_item(item(doi="10.1000/k").model_copy(update={"abstract": "Second abstract."}), added_by="agent", actor="t")
    lib.add_item(item(doi="10.1000/k").model_copy(update={"abstract": ""}), added_by="agent", actor="t")
    assert lib.get(i).abstract == "First abstract."
    assert not [e for e in lib.ps.log.iter() if e.type == "item.merged"]


def test_fill_abstract_records_its_source(lib):
    i = lib.add_item(item(doi="10.1000/f").model_copy(update={"abstract": ""}), added_by="agent", actor="t").item_id
    assert lib.fill_abstract(i, "Fetched abstract.", source="pubmed", actor="k")
    assert lib.get(i).abstract == "Fetched abstract."
    (ev,) = [e for e in lib.ps.log.iter() if e.type == "item.merged"]
    assert ev.payload["abstract"] == "Fetched abstract." and ev.payload["source"] == "pubmed"
    assert ev.actor_kind == "kernel"
    assert not lib.fill_abstract(i, "Another.", source="europepmc", actor="k")  # never replaced
    assert lib.get(i).abstract == "Fetched abstract."
