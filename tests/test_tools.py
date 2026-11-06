import pytest

from rk.config import Settings, save_settings
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.workspace import Kernel


@pytest.fixture
def kernel(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))  # recorded fixtures carry this mailto
    k = Kernel(tmp_home, http=replay_http)
    yield k
    k.close()


@pytest.fixture
def reg(kernel):
    return build_registry(kernel)


def cli(key=None):
    return CallContext(role="cli", actor="cli:test", idempotency_key=key)


def test_registry_permission_and_idempotency(reg):
    with pytest.raises(PermissionDenied):
        reg.call("rk_project_create", {"slug": "p1", "title": "T"}, CallContext(role="skeptic", actor="x", idempotency_key="k"))
    with pytest.raises(ToolError) as ei:
        reg.call("rk_project_create", {"slug": "p1", "title": "T"}, cli(None))
    assert ei.value.type == "idempotency_key_required"
    a = reg.call("rk_project_create", {"slug": "p1", "title": "T"}, cli("k1"))
    b = reg.call("rk_project_create", {"slug": "p1", "title": "T"}, cli("k1"))
    assert a == b
    assert [p["slug"] for p in reg.call("rk_project_list", {}, cli())["projects"]] == ["p1"]
    with pytest.raises(ToolError):
        reg.call("rk_project_create", {"slug": "p1", "title": "T"}, cli("k2"))  # exists


def test_end_to_end_tools_offline(reg):
    reg.call("rk_project_create", {"slug": "smoke", "title": "Smoke"}, cli("c"))
    wake = reg.call("rk_source_add", {"project": "smoke", "identifier": "10.1016/S0140-6736(97)11096-0"}, cli("a1"))
    assert (wake["ref_status"], wake["retraction"]) == ("pass", "retracted")
    peer = reg.call("rk_source_add", {"project": "smoke", "identifier": "10.7717/peerj.1364"}, cli("a2"))
    assert (peer["ref_status"], peer["retraction"]) == ("pass", "none")
    ft = reg.call("rk_source_fetch_fulltext", {"project": "smoke", "source_id": peer["item_id"]}, cli("f1"))
    assert ft["kind"] == "jats"
    hit = reg.call("rk_text_find", {"project": "smoke", "source_id": peer["item_id"], "query": "quotation errors"},
                   cli())["matches"][0]
    sl = reg.call("rk_text_read", {"project": "smoke", "source_id": peer["item_id"], "page": hit["page"],
                                   "start": hit["start"] - 20, "end": hit["end"] + 60}, cli())
    quote = " ".join(sl["text"].split()[1:-1])  # drop possibly cut-off edge words: quotes must be whole words
    n = reg.call("rk_note_add", {"project": "smoke", "note": {"source_id": peer["item_id"], "kind": "quote",
                                                              "text": quote, "role_in_source": "main"}}, cli("n1"))
    assert n["locator"]["page"] == 1
    with pytest.raises(ToolError) as ei:
        reg.call("rk_note_add", {"project": "smoke", "note": {"source_id": peer["item_id"], "kind": "quote",
                                 "text": "Quotation errors are rare in medicine.", "role_in_source": "main"}}, cli("n2"))
    assert ei.value.type == "note_rejected" and ei.value.details["best_snippet"]
    st = reg.call("rk_project_status", {"project": "smoke"}, cli())
    assert st["chain"]["ok"] and st["flagged_items"] == [wake["item_id"]] and st["notes"] == 1
    flagged = reg.call("rk_library_list", {"project": "smoke", "collection": "Retracted / EoC"}, cli())["items"]
    assert [i["id"] for i in flagged] == [wake["item_id"]]
    bib = reg.call("rk_library_export", {"project": "smoke", "format": "bibtex"}, cli())["text"]
    assert "10.7717/peerj.1364" in bib
    reg.call("rk_project_create", {"slug": "copy", "title": "Copy"}, cli("c2"))
    imp = reg.call("rk_library_import", {"project": "copy", "format": "bibtex", "text": bib}, cli("i1"))
    assert imp["created"] == 2


def test_missing_identifier_and_project(reg):
    reg.call("rk_project_create", {"slug": "pp", "title": "P"}, cli("c"))
    with pytest.raises(ToolError) as ei:
        reg.call("rk_source_add", {"project": "pp", "identifier": "10.9999/rk-does-not-exist-2026"}, cli("x"))
    assert ei.value.type == "not_found"
    with pytest.raises(ToolError) as ei:
        reg.call("rk_library_list", {"project": "nope"}, cli())
    assert ei.value.status == 404
