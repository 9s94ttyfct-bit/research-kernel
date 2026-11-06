import json

import pytest

from rk.discovery.gaps import GapsWorker, MinerResult, limitation, maphole, retraction
from rk.discovery.models import Cluster, Gap, GapEvidence
from rk.discovery.store import add_cluster, add_gap, gaps, searches
from rk.library.models import Ids, ItemIn
from rk.notes import list_notes
from rk.orchestrator.engine import Orchestrator, StageContext
from rk.orchestrator.state import record_passed
from rk.verify.retract import RetractionInfo

from .discovery_fakes import FakeSources, ScriptedExecutor, make_project

INTRO = "Aspirin is widely used for the prevention of cardiovascular events in older adults. " * 4
METHODS = "We enrolled patients from twelve hospitals and followed them for a median of three years. " * 4
LIMITATION = "Our trial enrolled only adults over 70, so the results may not apply to younger patients."
DISCUSSION = "Aspirin lowered the stroke rate. " * 4 + LIMITATION + " Future trials should include younger adults."
PAGES = [INTRO, METHODS, DISCUSSION]


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http)
    yield k, ps, lib
    k.close()


def _ctx(k, ps, lib, executor, sources, *, attempt: int = 1, guidance: str = "") -> StageContext:
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="gaps", attempt=attempt,
                        run_id=None, guidance=guidance, executor=executor, sources=sources, actor="tester")


def _add(lib, items: list[ItemIn]) -> list[str]:
    """Add the items up front so scripted outputs can name their ids; searches find the same items (same DOI)."""
    return [lib.add_item(it, added_by="agent", actor="tester").item_id for it in items]


def _landscape(ps, lib, clusters: dict[str, list[ItemIn]], *, reps: int = 1) -> dict[str, list[str]]:
    """Landscape attempt 1 passed with one cluster per label; returns the item ids per label."""
    out = {}
    for label, items in clusters.items():
        ids = _add(lib, items)
        add_cluster(ps, 1, Cluster(label=label, description=f"Works on {label}.", query=label, item_ids=ids,
                                   representative_ids=ids[:reps]), actor="tester")
        out[label] = ids
    record_passed(ps, "landscape", 1, actor="tester")
    return out


SOURCE = ItemIn(title="Aspirin trial in older adults", year=2015, abstract="A trial of aspirin.",
                ids=Ids(doi="10.1000/source"))
LATER = ItemIn(title="Aspirin in younger adults", year=2021, ids=Ids(doi="10.1000/later"),
               abstract="We tested aspirin in adults aged 40 to 60, a group the earlier trials left out.")
EARLIER = ItemIn(title="Aspirin in younger adults, a pilot", year=2012, ids=Ids(doi="10.1000/earlier"),
                 abstract="A pilot of aspirin in adults aged 40 to 60.")


def _limitation_setup(lib, ps, *, quote: str, pages=PAGES):
    [source] = _landscape(ps, lib, {"Stroke": [SOURCE]})["Stroke"]
    later, earlier = _add(lib, [LATER, EARLIER])
    sources = FakeSources({"younger": [LATER, EARLIER]}, lib=lib, fulltexts={SOURCE.title: pages})
    script = {("gapminer", "LimitationsOut"): [{"items": [{"source_id": source, "quote": quote,
                                                           "later_query": "aspirin younger adults"}]}]}
    return source, later, earlier, sources, script


def _run_limitation(k, ps, lib, sources, script):
    ex = ScriptedExecutor(script)
    GapsWorker(miners=[limitation.mine]).run(_ctx(k, ps, lib, ex, sources))
    return ex, gaps(ps, 1)


def test_unverbatim_limitation_quote_is_not_verified(env):
    k, ps, lib = env
    source, _, _, sources, script = _limitation_setup(lib, ps, quote="Our study only included people older than 70.")
    ex, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["verified"] is False and gap["kind"] == "stated_limitation"
    assert gap["checks"]["quote_note"] == "fail"
    assert gap["checks"]["reasons"]["quote_note"] == "quote_not_found"
    assert gap["evidence"]["note_ids"] == [] and gap["evidence"]["item_ids"] == [source]
    assert list_notes(ps, source) == []
    assert searches(ps, "gaps", 1) == [] and [c["role"] for c in ex.calls] == ["gapminer"]


def test_unknown_limitation_source_is_not_verified(env):
    k, ps, lib = env
    _, _, _, sources, script = _limitation_setup(lib, ps, quote=LIMITATION)
    script[("gapminer", "LimitationsOut")][0]["items"][0]["source_id"] = "itm_invented"
    _, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["verified"] is False and gap["checks"]["quote_note"] == "fail"
    assert gap["checks"]["reasons"]["quote_note"] == "not a cluster item"
    assert gap["evidence"]["item_ids"] == [] and gap["details"]["source_id"] == "itm_invented"


def test_limitation_from_the_first_page_is_not_verified(env):
    k, ps, lib = env
    first = "Aspirin is widely used for the prevention of cardiovascular events in older adults."
    source, _, _, sources, script = _limitation_setup(lib, ps, quote=first)
    _, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["checks"]["quote_note"] == "pass" and gap["checks"]["position"] == "fail"
    assert gap["verified"] is False
    [note] = list_notes(ps, source)
    assert note["kind"] == "quote" and gap["evidence"]["note_ids"] == [note["id"]]


def test_limitation_already_addressed_later_is_not_verified(env):
    k, ps, lib = env
    _, later, earlier, sources, script = _limitation_setup(lib, ps, quote=LIMITATION)
    script[("triage", "LaterWorkOut")] = [{"addressed": [later],
                                           "basis": {later: "We tested aspirin in adults aged 40 to 60"}}]
    ex, [gap] = _run_limitation(k, ps, lib, sources, script)
    checks = gap["checks"]
    assert (checks["quote_note"], checks["position"], checks["later_search"]) == ("pass", "pass", "pass")
    assert checks["later_judgement"] == "fail" and gap["verified"] is False
    assert gap["details"]["addressed"] == [later]

    # the later search runs only over works published after the source (2015)
    searched = [c for c in sources.calls if c[0] == "search"]
    assert searched and all(c[1] == "aspirin younger adults" and c[4] == 2015 for c in searched)
    logged = searches(ps, "gaps", 1)
    assert logged and {r["purpose"] for r in logged} == {"gap_check"}
    assert all(r["item_ids"] == [later] for r in logged)
    events = [e.payload for e in ps.log.iter() if e.type == "search.logged" and e.payload["stage"] == "gaps"]
    assert events and all(p["after_year"] == 2015 for p in events)
    triage = next(c for c in ex.calls if c["role"] == "triage")
    assert [w["item_id"] for w in triage["inputs"]["works"]] == [later] and triage["tools"] == []
    assert earlier not in json.dumps(triage["inputs"])


def test_limitation_with_unverbatim_addressed_basis_is_verified(env):
    k, ps, lib = env
    _, later, _, sources, script = _limitation_setup(lib, ps, quote=LIMITATION)
    script[("triage", "LaterWorkOut")] = [{"addressed": [later, "itm_invented"],
                                           "basis": {later: "a large trial of aspirin in middle-aged adults",
                                                     "itm_invented": "anything"}}]
    _, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["checks"]["later_judgement"] == "pass" and gap["verified"] is True
    assert gap["details"]["addressed"] == [] and gap["details"]["rejected"] == [later, "itm_invented"]
    assert gap["cluster_id"] is not None and LIMITATION in gap["statement"]


RETRACTED = ItemIn(title="Retracted trial", year=2010, ids=Ids(doi="10.1000/retracted"))
NO_DATE = ItemIn(title="Retracted without date", year=2011, ids=Ids(doi="10.1000/nodate"))
QUIET = ItemIn(title="Retracted and forgotten", year=2009, ids=Ids(doi="10.1000/quiet"))


def _by_doi(lib, doi: str) -> str | None:
    r = lib.db.execute("SELECT item_id FROM item_ids WHERE kind='doi' AND value=?", (doi,)).fetchone()
    return r["item_id"] if r else None


def test_retracted_cited_after_retraction_is_a_gap(env):
    k, ps, lib = env
    ids = _landscape(ps, lib, {"Stroke": [RETRACTED, NO_DATE, QUIET, SOURCE]})["Stroke"]
    notice = RetractionInfo(status="retracted", sources=["crossref:publisher"], notice_doi="10.1000/notice",
                            date="2016-05-03")
    sources = FakeSources({}, lib=lib, retractions={
        RETRACTED.title: notice,
        NO_DATE.title: RetractionInfo(status="withdrawn", sources=["openalex"]),
        QUIET.title: RetractionInfo(status="retracted", sources=["crossref:publisher"], date="2018-01"),
    }, citing={RETRACTED.title: [ItemIn(title="Cited before", year=2014, ids=Ids(doi="10.1000/c1")),
                                 ItemIn(title="Cited in the retraction year", year=2016, ids=Ids(doi="10.1000/c2")),
                                 ItemIn(title="Cited after", year=2019, ids=Ids(doi="10.1000/c3"))],
               QUIET.title: [ItemIn(title="Cited long before", year=2012, ids=Ids(doi="10.1000/c4"))]})
    ex = ScriptedExecutor({})
    GapsWorker(miners=[retraction.mine]).run(_ctx(k, ps, lib, ex, sources))

    assert ex.calls == []  # deterministic, no role task
    assert {c[1] for c in sources.calls if c[0] == "retraction"} == set(ids)
    assert ("cited_by", ids[0], 2016) in sources.calls and ("cited_by", ids[2], 2018) in sources.calls
    stored = gaps(ps, 1)
    assert [g["kind"] for g in stored] == ["retracted_still_cited"] * 2
    gap = next(g for g in stored if g["evidence"]["item_ids"][0] == ids[0])
    after = _by_doi(lib, "10.1000/c3")
    assert gap["verified"] is True
    assert gap["checks"]["citing_ids"] == [after] and gap["evidence"]["item_ids"] == [ids[0], after]
    assert gap["checks"]["retraction_sources"] == ["crossref:publisher"]
    assert gap["checks"]["notice_doi"] == "10.1000/notice"
    assert (gap["checks"]["retraction_date"], gap["checks"]["cited_after_retraction"]) == ("pass", "pass")
    assert gap["details"]["retraction_date"] == "2016-05-03" and gap["details"]["after_year"] == 2016
    assert "2016" in gap["statement"]
    # cited before or in the retraction year: not counted, not even added
    assert _by_doi(lib, "10.1000/c1") is None and _by_doi(lib, "10.1000/c2") is None

    undated = next(g for g in stored if g["evidence"]["item_ids"][0] == ids[1])
    assert undated["verified"] is False and undated["checks"]["retraction_date"] == "fail"
    assert undated["checks"]["reasons"]["retraction_date"] == "retraction date unknown"
    assert not any(c[0] == "cited_by" and c[1] == ids[1] for c in sources.calls)


CELL_ITEMS = [ItemIn(title=f"Aspirin study {i}", year=2015 + i, abstract=f"Abstract {i}.",
                     ids=Ids(doi=f"10.1000/cell-{i}")) for i in range(4)]
MATCH = ItemIn(title="Aspirin and mortality in children", year=2020, ids=Ids(doi="10.1000/match"),
               abstract="Mortality in children given aspirin was recorded.")


def test_empty_cell_needs_a_targeted_search_with_no_match(env):
    k, ps, lib = env
    a, b, c, d = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    [match] = _add(lib, [MATCH])
    sources = FakeSources({"mortality children": [MATCH]}, lib=lib)
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]},
                     {"name": "outcome", "values": ["stroke", "bleeding", "mortality"]}],
            "cells": {a: ["adults", "stroke"], b: ["adults", "bleeding"], c: ["children", "stroke"],
                      d: ["adults", "mortality"], "itm_invented": ["children", "bleeding"]},
            "empty_cell_queries": {"children|bleeding": "aspirin bleeding children",
                                   "children|mortality": "aspirin mortality children"}}
    ex = ScriptedExecutor({("gapminer", "MapPlan"): [plan], ("triage", "CellMatchOut"): [{"matches": [match]}]})
    GapsWorker(miners=[maphole.mine]).run(_ctx(k, ps, lib, ex, sources, guidance="children only"))

    hole, filled = gaps(ps, 1)
    assert hole["kind"] == filled["kind"] == "evidence_map_hole"
    assert hole["details"]["cell"] == ["children", "bleeding"] and hole["verified"] is True
    assert (hole["checks"]["targeted_search"], hole["checks"]["cell_match"]) == ("pass", "pass")
    assert filled["details"]["cell"] == ["children", "mortality"] and filled["verified"] is False
    assert filled["checks"]["cell_match"] == "fail" and filled["details"]["matches"] == [match]
    assert hole["details"]["counts"]["adults|stroke"] == 1 and hole["details"]["counts"]["children|bleeding"] == 0
    assert hole["details"]["ignored_items"] == ["itm_invented"]

    logged = searches(ps, "gaps", 1)
    assert {r["query"] for r in logged} == {"aspirin bleeding children", "aspirin mortality children"}
    assert {r["purpose"] for r in logged} == {"gap_check"}
    miner, triage = ex.calls
    assert miner["role"] == "gapminer" and miner["inputs"]["guidance"] == "children only"
    assert {w["item_id"] for w in miner["inputs"]["works"]} == {a, b, c, d}
    assert triage["role"] == "triage" and [w["item_id"] for w in triage["inputs"]["works"]] == [match]


def test_map_plan_without_two_dimensions_stores_nothing(env):
    k, ps, lib = env
    _landscape(ps, lib, {"Aspirin": CELL_ITEMS})
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]}], "cells": {},
            "empty_cell_queries": {}}
    ex = ScriptedExecutor({("gapminer", "MapPlan"): [plan]})
    out = GapsWorker(miners=[maphole.mine]).run(_ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    assert gaps(ps, 1) == []
    assert out.status == "rerun" and "exactly 2 dimensions (got 1)" in out.reason


def test_worker_fetches_full_text_of_three_representatives_per_cluster(env):
    k, ps, lib = env
    items = [ItemIn(title=f"Rep {i}", year=2015, ids=Ids(doi=f"10.1000/rep-{i}")) for i in range(5)]
    ids = _landscape(ps, lib, {"Stroke": items}, reps=5)["Stroke"]
    sources = FakeSources({}, lib=lib)
    seen = []
    sources.fulltext = lambda iid: seen.append(iid) or False

    def miner(ctx, clusters):
        assert [c["item_ids"] for c in clusters] == [ids]
        return MinerResult(gaps=[(Gap(kind="evidence_map_hole", statement="s", evidence=GapEvidence()), True,
                                  {"targeted_search": "pass"})])

    out = GapsWorker(miners=[miner]).run(_ctx(k, ps, lib, ScriptedExecutor({}), sources))
    assert out.status == "done" and seen == ids[:3]
    [gap] = gaps(ps, 1)
    assert gap["verified"] is True and gap["checks"] == {"targeted_search": "pass"}


def test_gaps_without_a_passed_landscape_fail(env):
    k, ps, lib = env
    out = GapsWorker(miners=[]).run(_ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({}, lib=lib)))
    assert out.status == "failed" and out.reason == "landscape has not passed"


def _gap(kind="stated_limitation") -> Gap:
    return Gap(kind=kind, statement="s", evidence=GapEvidence(item_ids=["itm_1"]))


def test_gaps_gate_needs_one_verified(env):
    _, ps, _ = env
    w = GapsWorker()
    assert w.gate(ps, 1).failures == ["no verified gap"]
    add_gap(ps, 1, _gap(), verified=False, checks={"quote_note": "fail"}, actor="tester")
    add_gap(ps, 2, _gap(), verified=True, checks={"quote_note": "pass"}, actor="tester")
    res = w.gate(ps, 1)
    assert not res.ok and res.failures == ["no verified gap"]
    assert w.gate(ps, 2).ok


def test_gaps_degrade_fails_with_reason(env):
    k, ps, lib = env
    add_gap(ps, 1, _gap(), verified=False, checks={"quote_note": "fail"}, actor="tester")
    w = GapsWorker()
    out = w.degrade(_ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({})), w.gate(ps, 1).failures)
    assert out.status == "failed" and out.reason == "no verified gap within the searched scope"
    assert w.on_answer(_ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({})), {"id": "chk_x"}).status == "done"


def test_gaps_is_a_default_worker_with_three_miners(env):
    k, _, _ = env
    w = Orchestrator(k, None).workers["gaps"]
    assert isinstance(w, GapsWorker)
    assert w.miners == [limitation.mine, retraction.mine, maphole.mine]
