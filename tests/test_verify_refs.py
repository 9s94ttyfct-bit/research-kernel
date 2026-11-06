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
from rk.verify.ref import CitedRef, _author_match, verify_reference
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
    assert _author_match(cited, family) is ok


def _resolved(*types: str) -> ResolvedWork:
    return ResolvedWork(item=ItemIn(title="t"), sources_used=["crossref"],
                        retraction_updates=[Update(type=t, source="publisher", doi="10.1/x") for t in types])


def test_partial_retraction_is_flagged_and_ranked():
    assert retraction_status(_resolved("correction", "partial_retraction")).status == "partial_retraction"
    assert retraction_status(_resolved("partial_retraction", "retraction")).status == "retracted"
    assert "partial_retraction" in FLAGGED and "corrected" not in FLAGGED
