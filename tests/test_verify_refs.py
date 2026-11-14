import json
import tempfile
from pathlib import Path

import pytest

from rk.config import Settings
from rk.library.models import Creator, Ids, ItemIn
from rk.library.service import Library
from rk.net.http import HttpClient
from rk.sources.crossref import Update
from rk.sources.resolver import ResolvedWork, SourceContext
from rk.store.project import ProjectStore
from rk.verify.records import FLAG_COLLECTION, verify_item
from rk.verify.ref import CitedRef, author_match, compare, verify_reference
from rk.verify.retract import FLAGGED, retraction_status

from .conftest import GOLD

TRAPS = json.loads((GOLD / "trap_refs.json").read_text())


def _check(ctx, case):
    ref, resolved = verify_reference(ctx, CitedRef(**case["cited"]))
    return ref.verdict, retraction_status(resolved).status


@pytest.mark.parametrize("case", TRAPS, ids=[c["id"] for c in TRAPS])
def test_trap_set_offline(source_ctx, case):
    assert _check(source_ctx, case) == (case["ref"], case["retraction"])


@pytest.mark.live
@pytest.mark.parametrize("case", TRAPS, ids=[c["id"] for c in TRAPS])
def test_trap_set_live(case):
    http = HttpClient(Path(tempfile.mkdtemp()) / "h.sqlite", user_agent="rk-live-test")
    ctx = SourceContext(http=http, settings=Settings(email="rk-fixtures@users.noreply.github.com"))
    assert _check(ctx, case) == (case["ref"], case["retraction"])


def test_mismatch_details(source_ctx):
    ref, _ = verify_reference(source_ctx, CitedRef(**TRAPS[1]["cited"]))
    assert ref.details["title"]["ok"] is False and ref.details["first_author"]["ok"] is True


def test_verify_item_flags_wakefield(tmp_path, source_ctx):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    iid = lib.add_item(ItemIn(title="Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive "
                                    "developmental disorder in children", year=1998,
                              creators=[Creator(family="Wakefield")], ids=Ids(doi="10.1016/S0140-6736(97)11096-0")),
                       added_by="agent", actor="t").item_id
    ref, ret = verify_item(ps, lib, source_ctx, iid, actor="t")
    item = lib.get(iid)
    assert (ref.verdict, ret.status) == ("pass", "retracted")
    assert item.ref_status == "pass" and item.retraction == "retracted"
    assert lib.collection_by_name(FLAG_COLLECTION) in item.collection_ids
    rows = ps.db.execute("SELECT verifier, verdict FROM verifications WHERE target_id=? ORDER BY verifier", (iid,)).fetchall()
    assert [tuple(r) for r in rows] == [("V-REF", "pass"), ("V-RETRACT", "retracted")]


@pytest.mark.parametrize(("cited", "family", "ok"), [
    ("J. van der Berg", "van der Berg", True),
    ("Vanderberg, J.", "van der Berg", True),
    ("Müller, K.", "Muller", True),
    ("王小明", "王", True),
    ("Berg, J.", "van der Berg", False),
    ("Li, X.", "Lin", False),
    ("Smithson", "Smith", False),
])
def test_author_match(cited, family, ok):
    assert author_match(cited, family) is ok


def _record(*families: str) -> ResolvedWork:
    return ResolvedWork(item=ItemIn(title="Study on detection rate of polyps", year=2020,
                                    creators=[Creator(family=f) for f in families]), sources_used=["crossref"])


def test_the_same_authors_in_another_order_pass_v_ref():
    """Calibration (srrepro10, 2026-11-13): a cade run failed its audit on G-REF for a PubMed record (Liu first, Huang
    last) whose DOI's Crossref deposit lists the same seven authors with Huang first: registries ordering the same
    authors differently is no wrong reference. A first author absent from the record's authors still fails, and so
    does a different author set with the cited first author in it."""
    cited = CitedRef(identifier="10.1/x", title="Study on detection rate of polyps", first_author="Liu", year=2020,
                     authors=["Liu", "Zhang", "Huang"])
    ref = compare(cited, _record("Huang", "Liu", "Zhang"))
    assert ref.verdict == "pass" and ref.details["first_author"] == {
        "cited": "Liu", "record": "Huang", "ok": True, "order": "the same authors in another order"}
    assert compare(cited, _record("Huang", "Zhang", "Wang")).verdict == "mismatch"
    assert compare(cited, _record("Huang", "Liu", "Zhang", "Wang")).verdict == "mismatch"
    assert compare(cited.model_copy(update={"authors": []}), _record("Huang", "Liu", "Zhang")).verdict == "mismatch"


def _resolved(*types: str) -> ResolvedWork:
    return ResolvedWork(item=ItemIn(title="t"), sources_used=["crossref"],
                        retraction_updates=[Update(type=t, source="publisher", doi="10.1/x") for t in types])


def test_partial_retraction_is_flagged_and_ranked():
    assert retraction_status(_resolved("correction", "partial_retraction")).status == "partial_retraction"
    assert retraction_status(_resolved("partial_retraction", "retraction")).status == "retracted"
    assert "partial_retraction" in FLAGGED and "corrected" not in FLAGGED


def test_retraction_date_comes_from_the_crossref_notice():
    resolved = ResolvedWork(item=ItemIn(title="t"), sources_used=["crossref", "openalex"], retraction_updates=[
        Update(type="correction", source="publisher", doi="10.1/c", date="2012-01-02"),
        Update(type="retraction", source="publisher", doi="10.1/r", date="2016-05-03"),
        Update(type="retraction", source="retraction-watch", doi="10.1/r", date="2016-04-30")])
    info = retraction_status(resolved)
    assert (info.status, info.notice_doi, info.date) == ("retracted", "10.1/r", "2016-04-30")
    assert retraction_status(_resolved("retraction")).date is None  # the notice carries no date


def test_retraction_date_falls_back_to_a_dated_withdrawal():
    from rk.sources.openalex import OpenAlexWork

    oa = OpenAlexWork(item=ItemIn(title="t"), is_retracted=True, referenced_works=[], cited_by_count=0,
                      oa_pdf_urls=[], raw={})
    resolved = ResolvedWork(item=ItemIn(title="t"), sources_used=["crossref", "openalex"], openalex=oa,
                            retraction_updates=[
                                Update(type="expression_of_concern", source="publisher", date="2013-01-01"),
                                Update(type="withdrawal", source="publisher", doi="10.1/w", date="2015-07-01")])
    info = retraction_status(resolved)
    assert (info.status, info.sources, info.date) == ("retracted", ["openalex"], "2015-07-01")
