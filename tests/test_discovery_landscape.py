import json

import pytest

from rk.discovery.landscape import LandscapeWorker
from rk.discovery.models import Cluster
from rk.discovery.store import add_cluster, clusters, log_search, searches
from rk.library.models import Ids, ItemIn
from rk.orchestrator.engine import Orchestrator, StageContext
from rk.orchestrator.state import current_stage, degradations
from rk.store.projections import rebuild

from .discovery_fakes import FakeSources, ScriptedExecutor, make_project

QUERIES = ["aspirin stroke prevention", "aspirin bleeding risk", "aspirin in the elderly"]


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http)
    yield k, ps, lib
    k.close()


def _hits(topic: str, n: int) -> list[ItemIn]:
    return [ItemIn(title=f"{topic.capitalize()} study {i}", year=2010 + i,
                   abstract=f"Abstract of the {topic} study {i}. " + "x" * 700,
                   ids=Ids(doi=f"10.1000/{topic}-{i}")) for i in range(n)]


def _ids(lib, items: list[ItemIn]) -> list[str]:
    """Add the items up front so the scripted cluster plan can name their ids; the searches find the same items."""
    return [lib.add_item(it, added_by="agent", actor="tester").item_id for it in items]


def _ctx(k, ps, lib, executor, sources, *, attempt: int = 1, guidance: str = "") -> StageContext:
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="landscape", attempt=attempt,
                        run_id=None, guidance=guidance, executor=executor, sources=sources, actor="tester")


def _cluster(label: str, query: str, item_ids: list[str], reps: list[str] | None = None) -> dict:
    return {"label": label, "description": f"Works on {label.lower()}.", "query": query, "item_ids": item_ids,
            "representative_ids": reps if reps is not None else item_ids[:2]}


def _stroke_bleeding(lib):
    stroke, bleeding = _hits("stroke", 6), _hits("bleeding", 6)
    sources = FakeSources({"stroke": stroke, "bleeding": bleeding}, lib=lib,
                          year_counts={"aspirin stroke prevention": {2019: 4, 2020: 7},
                                       "aspirin bleeding risk": {2020: 2}})
    return _ids(lib, stroke), _ids(lib, bleeding), sources


def test_landscape_builds_clusters_from_logged_searches(env):
    k, ps, lib = env
    stroke, bleeding, sources = _stroke_bleeding(lib)
    ex = ScriptedExecutor({
        ("scout", "LandscapeQueries"): [{"queries": QUERIES}],
        ("scout", "ClusterPlan"): [{"clusters": [_cluster("Stroke", QUERIES[0], stroke),
                                                 _cluster("Bleeding", QUERIES[1], bleeding)]}],
    })
    o = Orchestrator(k, None, executor_factory=lambda p, r, s: ex, sources_factory=lambda p: sources)
    o.run_stage("demo", "intake", actor="tester")
    assert o.advance("demo", actor="tester").ok
    assert o.run_stage("demo", "landscape", actor="tester").status == "done"

    stored = clusters(ps, 1)
    assert [c["label"] for c in stored] == ["Stroke", "Bleeding"]
    assert stored[0]["item_ids"] == stroke and stored[1]["item_ids"] == bleeding
    assert stored[0]["representative_ids"] == stroke[:2]
    assert stored[0]["year_counts"] == {2019: 4, 2020: 7} and stored[1]["year_counts"] == {2020: 2}

    first_two = sources.sources()[:2]
    searched = [(c[1], c[2], c[3]) for c in sources.calls if c[0] == "search"]
    assert searched == [(q, s, 25) for q in QUERIES for s in first_two]
    logged = searches(ps, "landscape", 1)
    assert [(r["query"], r["source"], r["purpose"], r["limit_n"]) for r in logged] == \
        [(q, s, "landscape", 25) for q in QUERIES for s in first_two]
    assert {c[1] for c in sources.calls if c[0] == "verify"} == set(stroke + bleeding)

    queries_call, plan_call = ex.calls
    assert (queries_call["role"], queries_call["tools"], plan_call["tools"]) == ("scout", [], [])
    assert queries_call["inputs"]["intake"]["audience"] == "clinicians and health-service researchers"
    assert queries_call["inputs"]["intake"]["paper_type"] == "critical_review"
    works = {w["item_id"]: w for w in plan_call["inputs"]["works"]}
    assert set(works) == set(stroke + bleeding)
    assert len(works[stroke[0]]["abstract"]) == 600 and works[stroke[0]]["year"] == 2010

    assert o.advance("demo", actor="tester").ok
    assert current_stage(ps) == "gaps"


def test_guidance_reaches_the_scout(env):
    k, ps, lib = env
    stroke, _, sources = _stroke_bleeding(lib)
    ex = ScriptedExecutor({("scout", "LandscapeQueries"): [{"queries": QUERIES}],
                           ("scout", "ClusterPlan"): [{"clusters": [_cluster("Stroke", QUERIES[0], stroke)]}]})
    LandscapeWorker().run(_ctx(k, ps, lib, ex, sources, guidance="exclude animal studies"))
    for call in ex.calls:
        assert "exclude animal studies" in json.dumps(call["inputs"])


def test_cluster_items_outside_the_search_are_dropped(env):
    k, ps, lib = env
    stroke, bleeding, sources = _stroke_bleeding(lib)
    elsewhere = _ids(lib, [ItemIn(title="Unrelated cohort", ids=Ids(doi="10.1000/unrelated"))])[0]
    ex = ScriptedExecutor({
        ("scout", "LandscapeQueries"): [{"queries": QUERIES}],
        ("scout", "ClusterPlan"): [{"clusters": [
            _cluster("Stroke", QUERIES[0], [*stroke, "itm_invented", elsewhere, stroke[0]],
                     reps=[stroke[1], bleeding[0], "itm_invented"]),
            _cluster("Ghost", "ghost query", ["itm_invented"]),
        ]}],
    })
    assert LandscapeWorker().run(_ctx(k, ps, lib, ex, sources)).status == "done"
    [stored] = clusters(ps, 1)
    assert stored["item_ids"] == stroke
    assert stored["representative_ids"] == [stroke[1]]
    assert ("verify", elsewhere) not in sources.calls


def test_too_few_queries_reruns(env):
    k, ps, lib = env
    _, _, sources = _stroke_bleeding(lib)
    ex = ScriptedExecutor({("scout", "LandscapeQueries"): [{"queries": ["aspirin stroke", " Aspirin stroke ", ""]}]})
    out = LandscapeWorker().run(_ctx(k, ps, lib, ex, sources))
    assert out.status == "rerun" and "1 queries (need 3-8)" in out.reason
    assert searches(ps, "landscape", 1) == []


def test_on_answer_is_done(env):
    k, ps, lib = env
    ctx = _ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({}))
    assert LandscapeWorker().on_answer(ctx, {"id": "chk_x"}).status == "done"


def _stored_cluster(ps, lib, label: str, items: list[ItemIn], statuses: list[str], *, attempt: int = 1,
                    logged: bool = True) -> list[str]:
    ids = _ids(lib, items)
    for iid, status in zip(ids, statuses, strict=True):
        lib.update(iid, ref_status=status, actor="tester")
    if logged:
        log_search(ps, stage="landscape", attempt=attempt, purpose="landscape", source="openalex", query=label,
                   limit=25, item_ids=ids, actor="tester")
    add_cluster(ps, attempt, Cluster(label=label, description="d", query=label, item_ids=ids), actor="tester")
    return ids


def test_gate_needs_five_verified_per_cluster(env):
    _, ps, lib = env
    w = LandscapeWorker()
    res = w.gate(ps, 1)
    assert not res.ok and res.failures == ["no cluster"]

    _stored_cluster(ps, lib, "Stroke", _hits("stroke", 5), ["pass"] * 4 + ["mismatch"])
    res = w.gate(ps, 1)
    assert not res.ok and res.failures == ["cluster 'Stroke': 4 verified sources (need 5)"]

    _stored_cluster(ps, lib, "Bleeding", _hits("bleeding", 5), ["pass"] * 5, attempt=2)
    assert w.gate(ps, 2).ok


def test_gate_needs_every_item_in_the_search_log(env):
    _, ps, lib = env
    _stored_cluster(ps, lib, "Stroke", _hits("stroke", 5), ["pass"] * 5, logged=False)
    res = LandscapeWorker().gate(ps, 1)
    assert not res.ok and res.failures == ["cluster 'Stroke': 5 items not in this attempt's search log"]


def test_p1_degrade_drops_weak_clusters(env):
    k, ps, lib = env
    w = LandscapeWorker()
    _stored_cluster(ps, lib, "Stroke", _hits("stroke", 5), ["pass"] * 5)
    _stored_cluster(ps, lib, "Bleeding", _hits("bleeding", 5), ["pass"] * 3 + ["not_found"] * 2)
    failures = w.gate(ps, 1).failures
    assert failures == ["cluster 'Bleeding': 3 verified sources (need 5)"]

    ctx = _ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({}))
    assert w.degrade(ctx, failures).status == "done"
    assert [c["label"] for c in clusters(ps, 1)] == ["Stroke"]
    [d] = degradations(ps, "landscape")
    assert d["attempt"] == 1 and d["removed"] == ["Bleeding"] and "3 verified sources" in d["reason"]
    assert w.gate(ps, 1).ok
    rebuild(ps.db)
    assert [c["label"] for c in clusters(ps, 1)] == ["Stroke"]

    _stored_cluster(ps, lib, "Elderly", _hits("elderly", 5), ["pass"] * 2 + ["mismatch"] * 3, attempt=2)
    out = w.degrade(_ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({}), attempt=2), w.gate(ps, 2).failures)
    assert out.status == "failed" and out.reason == "no cluster with 5 verified sources"
    assert clusters(ps, 2) == []
    assert degradations(ps, "landscape")[-1]["removed"] == ["Elderly"]


def test_landscape_is_a_default_worker(env):
    k, _, _ = env
    assert isinstance(Orchestrator(k, None).workers["landscape"], LandscapeWorker)
