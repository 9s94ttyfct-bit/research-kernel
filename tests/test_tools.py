import pytest
from pydantic import ValidationError

from rk.config import Settings, save_settings
from rk.net.http import BudgetExhausted
from rk.tools import builtin
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


def test_a_spent_source_budget_is_recorded_for_a_watched_task_only(reg, monkeypatch):
    """``run_task`` watches its task: a source budget spent in one of the task's tool calls (here wrapped in a
    ToolError by ``rk_search_works``) is recorded for it and the task's later calls are refused. A call outside a
    watched task (the UI, a chat) keeps the ToolError and is never refused."""
    def search(*a, **kw):
        raise BudgetExhausted("openalex", 1_900_000_000.0)
    monkeypatch.setattr(builtin, "search", search)
    reg.call("rk_project_create", {"slug": "bb", "title": "B"}, cli("c"))
    args = {"project": "bb", "query": "aspirin"}

    def scout(task_id, key=None):
        return CallContext(role="scout", actor="t", task_id=task_id, project="bb", idempotency_key=key)

    for ctx in (cli("u1"), scout("tsk_free", "f1")):
        with pytest.raises(ToolError) as ei:
            reg.call("rk_search_works", args, ctx)
        assert ei.value.type == "BudgetExhausted"
    reg.call("rk_library_list", {"project": "bb"}, scout("tsk_free"))  # not refused
    assert reg.budget_exhausted("tsk_free") is None

    reg.watch_budget("tsk_w")
    with pytest.raises(ToolError):
        reg.call("rk_search_works", args, scout("tsk_w", "w1"))
    spent = reg.budget_exhausted("tsk_w")
    assert isinstance(spent, BudgetExhausted) and spent.budget_key == "openalex"
    with pytest.raises(ToolError) as ei:
        reg.call("rk_library_list", {"project": "bb"}, scout("tsk_w"))
    assert ei.value.type == "budget_exhausted" and "suspended" in ei.value.message
    assert reg.unwatch_budget("tsk_w") is spent and reg.budget_exhausted("tsk_w") is None


@pytest.mark.parametrize("name,runner,args", [
    ("rk_task_run", "run_task", {"role": "scout", "instructions": "go"}),
    ("rk_support_check", "support_check", {"text": "Aspirin works.", "source_ids": ["src_x"]}),
])
def test_a_task_the_human_runs_that_is_suspended_on_a_spent_budget_is_a_typed_error(reg, monkeypatch, name, runner,
                                                                                      args):
    """A tool that runs role tasks for the human (no task of its own) answers a task suspended on a spent source
    budget with a ``budget_exhausted`` error (HTTP 429) naming the budget, not an unhandled exception."""
    def spent(*a, **kw):
        raise BudgetExhausted("openalex", 1_900_000_000.0)
    monkeypatch.setattr(builtin, runner, spent)
    reg.call("rk_project_create", {"slug": "tr", "title": "T"}, cli("c"))
    with pytest.raises(ToolError) as ei:
        reg.call(name, {"project": "tr", **args}, cli("t1"))
    assert (ei.value.type, ei.value.status, ei.value.details["budget"]) == ("budget_exhausted", 429, "openalex")


def test_slug_traversal_unknown_collection_and_attach_role(reg, tmp_path):
    for bad in ("../outside", "/abs", "UP"):
        with pytest.raises(ToolError) as ei:
            reg.call("rk_library_list", {"project": bad}, cli())
        assert ei.value.type == "not_found"
    reg.call("rk_project_create", {"slug": "p2", "title": "T"}, cli("c"))
    for col in ("No Such Collection", "col_doesnotexist"):
        with pytest.raises(ToolError) as ei:
            reg.call("rk_library_export", {"project": "p2", "format": "bibtex", "collection": col}, cli())
        assert ei.value.type == "not_found"
    with pytest.raises(PermissionDenied):
        reg.call("rk_library_attach", {"project": "p2", "source_id": "src_x", "path": "/etc/hosts"},
                 CallContext(role="lead", actor="agent", idempotency_key="k"))


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


def test_note_add_offers_no_record_quotes(reg):
    # Only read_extract's record fields quote a bibliographic record (rk.production.extraction); a model calling
    # rk_note_add is not offered that choice, and a call that makes it anyway is refused before it runs.
    against = reg.get("rk_note_add").input_model.model_json_schema()["$defs"]
    against = next(d["properties"]["against"] for d in against.values() if "against" in d.get("properties", {}))
    assert against["enum"] == ["text", "abstract"]
    reg.call("rk_project_create", {"slug": "rec", "title": "T"}, cli("c"))
    with pytest.raises(ValidationError):
        reg.call("rk_note_add", {"project": "rec", "note": {"source_id": "itm_x", "kind": "quote", "text": "x",
                                                            "against": "record"}}, cli("n"))
