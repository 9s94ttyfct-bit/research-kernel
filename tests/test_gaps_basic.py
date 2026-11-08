import json

import pytest

from rk.discovery.gaps import (
    GapsWorker,
    MinerResult,
    contradiction,
    distortion,
    limitation,
    maphole,
    retraction,
)
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


def _jats_source(ps, source: str, *, footnote: str) -> None:
    """The source's latest text layer: a JATS body (the limitation at about 65% of it) followed by a large table whose
    footnote is ``footnote``."""
    from rk.fulltext import _record_snapshot
    from rk.text.markup import jats_to_layer

    rows = b"".join(b"<tr><td>Centre %d</td><td>%d</td></tr>" % (i, i) for i in range(300))
    tail = "Aspirin was well tolerated. " * 11
    xml = (b"<article><body><p>" + (INTRO + METHODS).encode() + b"</p><p>" + DISCUSSION.encode() + b"</p><p>"
           + tail.encode() + b"</p></body><floats-group>"
           b"<table-wrap><label>Table 1</label><table>" + rows + b"</table><table-wrap-foot><fn><p>"
           + footnote.encode() + b"</p></fn></table-wrap-foot></table-wrap></floats-group></article>")
    _record_snapshot(ps, source, xml, jats_to_layer(xml, ""), "jats", None, "tester")


def test_limitation_position_is_measured_on_the_body_not_the_appended_tables(env):
    """Tables appended after a JATS body (they make the layer several times longer) do not push a late limitation
    out of the last 40%: the position is a share of the body."""
    k, ps, lib = env
    source, _, _, sources, script = _limitation_setup(lib, ps, quote=LIMITATION)
    _jats_source(ps, source, footnote="Centres were grouped by region.")
    script[("triage", "LaterWorkOut")] = [{"addressed": [], "basis": {}}]
    _, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["checks"]["quote_note"] == "pass" and gap["checks"]["position"] == "pass"
    assert 0.6 <= gap["details"]["position"] < 0.7


def test_a_table_footnote_is_not_late_text(env):
    """A quote found only in the appended tables (here a table footnote, floats-group) is outside the body, so it is
    not a limitation stated late in the text."""
    k, ps, lib = env
    footnote = "Data from two centres were incomplete and were excluded from the analysis."
    source, _, _, sources, script = _limitation_setup(lib, ps, quote=footnote)
    _jats_source(ps, source, footnote=footnote)
    _, [gap] = _run_limitation(k, ps, lib, sources, script)
    assert gap["checks"]["quote_note"] == "pass" and gap["checks"]["position"] == "fail"
    assert "table" in gap["checks"]["reasons"]["position"] and gap["verified"] is False


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
    assert gap["statement"].endswith('; no later work (published after 2015) addressing it was found within the '
                                     'searched scope (query "aspirin younger adults" on openalex, europepmc)')
    assert gap["details"]["sources"] == ["openalex", "europepmc"]


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

    # every citation lookup is logged like a search; the citing works join the search hits
    logged = {r["query"]: r for r in searches(ps, "gaps", 1)}
    assert set(logged) == {f"cites:{ids[0]}", f"cites:{ids[2]}"}
    hit = logged[f"cites:{ids[0]}"]
    assert (hit["purpose"], hit["source"], hit["item_ids"]) == ("gap_check", "openalex", [after])
    assert logged[f"cites:{ids[2]}"]["item_ids"] == []
    events = {e.payload["query"]: e.payload for e in ps.log.iter() if e.type == "search.logged"}
    assert events[f"cites:{ids[0]}"]["after_year"] == 2016 and events[f"cites:{ids[2]}"]["after_year"] == 2018
    assert lib.collection_by_name("Search hits") in lib.get(after).collection_ids
    assert lib.get(after).discovered_via == "forward"


def test_retraction_miner_reads_the_recorded_v_retract_and_survives_a_lookup_error(env):
    from rk.net.http import HttpError
    from rk.verify.records import record_verification

    k, ps, lib = env
    retracted, broken, quiet = _landscape(ps, lib, {"Stroke": [RETRACTED, NO_DATE, QUIET]})["Stroke"]
    record_verification(ps, "item", retracted, "V-RETRACT", "retracted",
                        RetractionInfo(status="retracted", sources=["crossref:publisher"],
                                       date="2016-05-03").model_dump(), actor="tester")
    sources = FakeSources({}, lib=lib, citing={RETRACTED.title: [
        ItemIn(title="Cited after", year=2019, ids=Ids(doi="10.1000/c3"))]})
    real = sources.retraction

    def lookup(iid):
        if iid == broken:
            raise HttpError(500, "https://api.crossref.org/works/10.1000/nodate")
        return real(iid)

    sources.retraction = lookup
    out = GapsWorker(miners=[retraction.mine]).run(_ctx(k, ps, lib, ScriptedExecutor({}), sources))
    assert out.status == "done"
    assert f"retraction lookup of {broken} failed (HttpError: HTTP 500" in out.reason
    assert ("retraction", retracted) not in sources.calls  # the V-RETRACT result landscape recorded
    assert ("retraction", quiet) in sources.calls
    [gap] = gaps(ps, 1)
    assert gap["verified"] is True and gap["evidence"]["item_ids"][0] == retracted


def test_a_failed_citation_lookup_is_no_gap_and_is_reported(env):
    from rk.net.http import HttpError

    k, ps, lib = env
    [_] = _landscape(ps, lib, {"Stroke": [RETRACTED]})["Stroke"]
    sources = FakeSources({}, lib=lib, retractions={RETRACTED.title: RetractionInfo(
        status="retracted", sources=["crossref:publisher"], date="2016-05-03")})

    def cited_by(iid, after_year):
        raise HttpError(429, "https://api.openalex.org/works")

    sources.cited_by = cited_by
    out = GapsWorker(miners=[retraction.mine]).run(_ctx(k, ps, lib, ScriptedExecutor({}), sources))
    [gap] = gaps(ps, 1)
    assert gap["verified"] is False and gap["checks"]["cited_after_retraction"] == "fail"
    assert "citation lookup of" in gap["checks"]["reasons"]["cited_after_retraction"]
    assert "HTTP 429" in gap["checks"]["reasons"]["cited_after_retraction"] and out.status == "done"


def test_one_full_text_error_does_not_fail_the_gaps_attempt(env):
    from rk.net.http import HttpError

    k, ps, lib = env
    items = [ItemIn(title=f"Rep {i}", year=2015, ids=Ids(doi=f"10.1000/rep-{i}")) for i in range(3)]
    ids = _landscape(ps, lib, {"Stroke": items}, reps=3)["Stroke"]
    sources = FakeSources({}, lib=lib)
    seen = []

    def fulltext(iid):
        seen.append(iid)
        if iid == ids[0]:
            raise HttpError(500, "https://www.ebi.ac.uk/europepmc/x")
        if iid == ids[1]:
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        return False

    sources.fulltext = fulltext
    out = GapsWorker(miners=[_gap_miner(True)]).run(_ctx(k, ps, lib, ScriptedExecutor({}), sources))
    assert seen == ids and out.status == "done"
    assert f"full text of {ids[0]} failed (HttpError: HTTP 500" in out.reason
    assert f"full text of {ids[1]} failed (UnicodeEncodeError" in out.reason


def _failing_search(sources):
    from rk.net.http import HttpError

    def search(query, source, limit=25, after_year=None, *, offset=0, date_from=None, date_to=None):
        raise HttpError(503, "https://api.openalex.org/works")

    sources.search = search
    return sources


def test_a_failed_targeted_search_is_no_hole(env):
    k, ps, lib = env
    a, b, c, d, e, f = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]},
                     {"name": "outcome", "values": ["stroke", "bleeding"]}],
            "cells": {a: ["adults", "stroke"], b: ["adults", "bleeding"], c: ["children", "stroke"],
                      d: ["adults", "stroke"], e: ["adults", "bleeding"], f: ["children", "stroke"]},
            "empty_cell_queries": {"children|bleeding": "aspirin bleeding children"}}
    ex = ScriptedExecutor({("gapminer", "MapPlan"): [plan]})
    GapsWorker(miners=[maphole.mine]).run(_ctx(k, ps, lib, ex, _failing_search(FakeSources({}, lib=lib))))
    [cell] = gaps(ps, 1)
    assert cell["verified"] is False and cell["checks"]["targeted_search"] == "fail"
    assert "targeted search failed (HttpError: HTTP 503" in cell["checks"]["reasons"]["targeted_search"]
    assert cell["details"]["searched_ids"] == [] and cell["checks"]["cell_match"] == "skipped"


def test_a_failed_later_search_does_not_verify_a_limitation(env):
    k, ps, lib = env
    _, _, _, sources, script = _limitation_setup(lib, ps, quote=LIMITATION)
    _, [gap] = _run_limitation(k, ps, lib, _failing_search(sources), script)
    assert gap["verified"] is False and gap["checks"]["later_search"] == "fail"
    assert "later-work search failed (HttpError: HTTP 503" in gap["checks"]["reasons"]["later_search"]


CELL_ITEMS = [ItemIn(title=f"Aspirin study {i}", year=2015 + i, abstract=f"Abstract {i}.",
                     ids=Ids(doi=f"10.1000/cell-{i}")) for i in range(6)]
MATCH = ItemIn(title="Aspirin and mortality in children", year=2020, ids=Ids(doi="10.1000/match"),
               abstract="Mortality in children given aspirin was recorded.")


def test_empty_cell_needs_a_targeted_search_with_no_match(env):
    k, ps, lib = env
    a, b, c, d, e, f = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    [match] = _add(lib, [MATCH])
    sources = FakeSources({"mortality children": [MATCH]}, lib=lib)
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]},
                     {"name": "outcome", "values": ["stroke", "bleeding", "mortality"]}],
            "cells": {a: ["adults", "stroke"], b: ["adults", "bleeding"], c: ["children", "stroke"],
                      d: ["adults", "mortality"], e: ["adults", "stroke"], f: ["children", "stroke"],
                      "itm_invented": ["children", "bleeding"]},
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
    assert hole["statement"] == (
        "Evidence-map hole: no work with population = 'children' and outcome = 'bleeding' was found within the "
        'searched scope (the landscape and the query "aspirin bleeding children" on openalex, europepmc).')
    assert "searched scope" not in filled["statement"]
    assert hole["details"]["counts"]["adults|stroke"] == 2 and hole["details"]["counts"]["children|bleeding"] == 0
    assert hole["details"]["ignored_items"] == ["itm_invented"]
    assert hole["checks"]["map_coverage"] == "pass" and hole["checks"]["placed_neighbours"] == "pass"
    assert set(hole["evidence"]["item_ids"]) == {b, c, f}  # the placed works that share a value with the cell

    logged = searches(ps, "gaps", 1)
    assert {r["query"] for r in logged} == {"aspirin bleeding children", "aspirin mortality children"}
    assert {r["purpose"] for r in logged} == {"gap_check"}
    miner, triage = ex.calls
    assert miner["role"] == "gapminer" and miner["inputs"]["guidance"] == "children only"
    assert {w["item_id"] for w in miner["inputs"]["works"]} == {a, b, c, d, e, f}
    assert triage["role"] == "triage" and [w["item_id"] for w in triage["inputs"]["works"]] == [match]


def _run_map(k, ps, lib, plan, sources=None, triage=()):
    ex = ScriptedExecutor({("gapminer", "MapPlan"): [plan], ("triage", "CellMatchOut"): list(triage)})
    out = GapsWorker(miners=[maphole.mine]).run(_ctx(k, ps, lib, ex, sources or FakeSources({}, lib=lib)))
    return out, gaps(ps, 1), ex


def test_cell_keyed_map_of_the_live_demo_yields_no_verified_hole(env):
    """The live demo's GapMiner keyed ``cells`` by cell ("a|b" -> item ids): nothing is placed, so no cell is a hole,
    and the miner says why."""
    k, ps, lib = env
    a, b, c, d, e, f = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]},
                     {"name": "outcome", "values": ["stroke", "bleeding"]}],
            "cells": {"adults|stroke": [a, b], "adults|bleeding": [c, d], "children|stroke": [e, f]},
            "empty_cell_queries": {"children|bleeding": "aspirin bleeding children"}}
    out, stored, ex = _run_map(k, ps, lib, plan)
    assert out.status == "rerun"
    assert "keyed by something other than a given item id" in out.reason
    assert "placed 0 of 6 works" in out.reason
    assert stored and not any(g["verified"] for g in stored)
    assert {g["checks"]["map_coverage"] for g in stored} == {"fail"}
    assert {g["checks"]["targeted_search"] for g in stored} == {"skipped"}
    assert [c["role"] for c in ex.calls] == ["gapminer"]  # no search, no triage for a vacuous map
    assert searches(ps, "gaps", 1) == []


def test_map_plan_cells_need_exactly_two_values():
    from pydantic import ValidationError

    from rk.discovery.models import MapPlan
    with pytest.raises(ValidationError):
        MapPlan.model_validate({"dims": [], "cells": {"itm_1": ["adults", "stroke", "extra"]}})
    with pytest.raises(ValidationError):
        MapPlan.model_validate({"dims": [], "cells": {"itm_1": ["adults"]}})
    schema = MapPlan.model_json_schema()
    cell = schema["properties"]["cells"]["additionalProperties"]
    assert (cell["minItems"], cell["maxItems"]) == (2, 2)


def test_map_values_match_case_folded_and_trimmed(env):
    k, ps, lib = env
    a, b, c, d, e, _ = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    plan = {"dims": [{"name": "population", "values": ["Adults", "Children"]},
                     {"name": "outcome", "values": ["Stroke", "Bleeding"]}],
            "cells": {a: [" adults ", "STROKE"], b: ["adults", "bleeding"], c: ["children ", "Stroke"],
                      d: ["Adults", "Bleeding"], f" {e} ": ["adults", "stroke"]},
            "empty_cell_queries": {" children | BLEEDING ": "aspirin bleeding children"}}
    _, [hole], _ = _run_map(k, ps, lib, plan)
    assert hole["details"]["cell"] == ["Children", "Bleeding"]
    assert hole["details"]["query"] == "aspirin bleeding children"
    assert hole["details"]["counts"] == {"Adults|Stroke": 2, "Adults|Bleeding": 2, "Children|Stroke": 1,
                                         "Children|Bleeding": 0}
    assert hole["verified"] is True and hole["details"]["ignored_items"] == []


def test_map_with_low_coverage_fails_every_cell(env):
    k, ps, lib = env
    items = [ItemIn(title=f"Coverage study {i}", year=2015, ids=Ids(doi=f"10.1000/cov-{i}")) for i in range(12)]
    ids = _landscape(ps, lib, {"Aspirin": items})["Aspirin"]
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]},
                     {"name": "outcome", "values": ["stroke", "bleeding"]}],
            "cells": {ids[0]: ["adults", "stroke"], ids[1]: ["adults", "bleeding"], ids[2]: ["children", "stroke"],
                      ids[3]: ["adults", "stroke"], ids[4]: ["adults", "stroke"]},  # 5 of 12 < 50 %
            "empty_cell_queries": {"children|bleeding": "aspirin bleeding children"}}
    out, [cell], _ = _run_map(k, ps, lib, plan)
    assert cell["verified"] is False and cell["checks"]["map_coverage"] == "fail"
    assert cell["checks"]["reasons"]["map_coverage"] == "the map placed 5 of 12 works (needs at least 6)"
    assert out.status == "rerun" and "placed 5 of 12 works" in out.reason


def test_hole_needs_a_placed_neighbour(env):
    k, ps, lib = env
    a, b, c, d, e, f = _landscape(ps, lib, {"Aspirin": CELL_ITEMS})["Aspirin"]
    plan = {"dims": [{"name": "population", "values": ["adults", "children", "infants"]},
                     {"name": "outcome", "values": ["stroke", "bleeding", "rash"]}],
            "cells": {a: ["adults", "stroke"], b: ["adults", "bleeding"], c: ["children", "stroke"],
                      d: ["children", "bleeding"], e: ["adults", "stroke"], f: ["children", "stroke"]},
            "empty_cell_queries": {"infants|rash": "aspirin rash infants"}}
    _, stored, _ = _run_map(k, ps, lib, plan)
    lonely = next(g for g in stored if g["details"]["cell"] == ["infants", "rash"])
    assert lonely["verified"] is False and lonely["checks"]["placed_neighbours"] == "fail"
    assert lonely["checks"]["targeted_search"] == "skipped"


def test_map_plan_without_two_dimensions_stores_nothing(env):
    k, ps, lib = env
    _landscape(ps, lib, {"Aspirin": CELL_ITEMS})
    plan = {"dims": [{"name": "population", "values": ["adults", "children"]}], "cells": {},
            "empty_cell_queries": {}}
    ex = ScriptedExecutor({("gapminer", "MapPlan"): [plan]})
    out = GapsWorker(miners=[maphole.mine]).run(_ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    assert gaps(ps, 1) == []
    assert out.status == "rerun" and "exactly 2 dimensions (got 1)" in out.reason


def _gap_miner(verified: bool):
    def miner(ctx, clusters):
        return MinerResult(gaps=[(Gap(kind="evidence_map_hole", statement="s", evidence=GapEvidence()), verified,
                                  {"targeted_search": "pass" if verified else "fail"})])
    return miner


def _failing_miner(ctx, clusters):
    return MinerResult(problems=["the gapminer gave no limitations (timeout)"])


def test_miner_problem_reruns_when_no_gap_is_verified(env):
    k, ps, lib = env
    _landscape(ps, lib, {"Aspirin": CELL_ITEMS})
    ctx = _ctx(k, ps, lib, ScriptedExecutor({}), FakeSources({}, lib=lib))
    out = GapsWorker(miners=[_failing_miner, _gap_miner(False)]).run(ctx)
    assert out.status == "rerun" and out.reason == "the gapminer gave no limitations (timeout)"
    assert [g["verified"] for g in gaps(ps, 1)] == [False]

    out = GapsWorker(miners=[_failing_miner, _gap_miner(True)]).run(_ctx(k, ps, lib, ScriptedExecutor({}),
                                                                          FakeSources({}, lib=lib), attempt=2))
    assert out.status == "done" and out.reason == "the gapminer gave no limitations (timeout)"


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


def test_gaps_is_a_default_worker_with_five_miners(env):
    k, _, _ = env
    w = Orchestrator(k, None).workers["gaps"]
    assert isinstance(w, GapsWorker)
    assert w.miners[:3] == [limitation.mine, retraction.mine, maphole.mine]
    assert [type(m) for m in w.miners[3:]] == [contradiction.ContradictionMiner, distortion.DistortionMiner]
