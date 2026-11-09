"""Plan 5c Task 8: the systematic-review reproduction set (spec §10). The manifest (six PMC OA systematic reviews, at
least one per domain, each with its included studies and a search that dedupes to at most 3,000 records), a seeded
project per review (its question frozen as the RQ, Deviation 4, so a run starts at protocol), and the metrics: recall
of the included studies (DOI/PMID, then title token_set_ratio >= 90), the main conclusion's direction (the vote of
two judges of different vendors, none the main claim's author's; otherwise None) and the hard gates. Offline: no
network, no model; the judges are scripted."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from rk.cli.main import app
from rk.config import DEFAULT_DRIVERS, Settings, load_settings, save_settings
from rk.discovery.models import Intake
from rk.discovery.store import frozen_rq, intake_of, record_intake
from rk.eval import sr_repro as sr
from rk.library.models import Ids, ItemIn
from rk.orchestrator.engine import Orchestrator
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.state import (
    current_stage,
    discovery_passed,
    record_attempt,
    record_passed,
    record_ran,
    stage_row,
)
from rk.production import store
from rk.production.argue import main_claim
from rk.production.models import RECORD_CAP
from rk.workspace import Kernel
from tests.discovery_fakes import FakeWorker
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_draft import _ready
from tests.test_synthesis import Exec


def _entry(eid: str = "test-review", *, included=None, direction: str = "benefit", **over) -> sr.Entry:
    body = {
        "id": eid, "pmcid": "PMC0000001", "doi": "10.1000/review", "title": "A test review",
        "domain": "medicine",
        "question": "whether drug A changes outcome B compared with placebo in adults",
        "pico": {"population": "adults", "intervention": "drug A", "comparison": "placebo", "outcome": "outcome B"},
        "date_from": 2010, "date_to": 2022,
        "included": included or [{"doi": "10.1000/one", "title": "Trial one of drug A"}],
        "main_conclusion": {"outcome": "outcome B", "direction": direction},
        "note": "test entry",
        "search": {"searches": [{"source": "europepmc", "query": "drug A AND outcome B"}], "date_from": 2010,
                   "date_to": 2022, "records": 12, "deduplicated": 10, "included_found": 1,
                   "retrieved": "2026-11-09"},
        "provenance": {"review_url": "https://europepmc.org/article/PMC/PMC0000001",
                       "included_from": "Table 1 (characteristics of included studies)", "retrieved": "2026-11-09"},
    }
    return sr.Entry.model_validate({**body, **over})


@pytest.fixture
def kernel(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    yield k
    k.close()


# ---- the manifest ----------------------------------------------------------------------------------------------
def test_manifest_has_six_entries_covering_four_domains(tmp_path):
    entries = sr.load_manifest()
    assert len(entries) == 6
    assert {e.domain for e in entries} == {"chemistry", "biology", "medicine", "cs"}
    assert len({e.id for e in entries}) == 6 and len({e.pmcid for e in entries}) == 6
    for e in entries:
        assert e.pmcid.startswith("PMC") and e.doi.startswith("10.") and e.question.startswith(("whether", "how"))
        assert e.included and all(s.title for s in e.included), e.id
        assert e.search.deduplicated <= RECORD_CAP and e.search.included_found <= len(e.included), e.id
        assert e.note and e.provenance.review_url.endswith(e.pmcid), e.id
        assert sr.slug_of(e).startswith(sr.SLUG_PREFIX)
    text = sr.MANIFEST_PATH.read_text()
    for needle in ("/Users/", "/home/", "/var/folders", "file://"):
        assert needle not in text
    # the schema refuses a set that is not six reviews over the four domains, or a search over the record budget
    data = json.loads(text)
    bad = tmp_path / "bad.json"
    for broken in ({**data, "entries": data["entries"][:5]},
                   {**data, "entries": [{**x, "domain": "medicine"} for x in data["entries"]]},
                   {**data, "entries": [{**data["entries"][0], "search": {**data["entries"][0]["search"],
                                                                           "deduplicated": RECORD_CAP + 1}},
                                        *data["entries"][1:]]}):
        bad.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            sr.load_manifest(bad)


# ---- recall -----------------------------------------------------------------------------------------------------
def _include(ps, lib, works: list[ItemIn]) -> None:
    """search_screen attempt 1 passed with ``works`` included at full text."""
    record_attempt(ps, "search_screen", 1, run_id=None, guidance="", requested=None, actor="tester")
    for w in works:
        iid = lib.add_item(w, added_by="agent", actor="tester").item_id
        rid = store.add_record(ps, 1, iid, language="en", actor="tester")
        for phase in ("title_abstract", "full_text"):
            store.set_final(ps, rid, 1, phase, "yes", "agreement", "eligible", actor="tester")
    record_ran(ps, "search_screen", 1, "done", "", actor="tester")
    record_passed(ps, "search_screen", 1, actor="tester")


def test_recall_matches_by_doi_pmid_then_title(kernel):
    included = [
        {"doi": "10.1000/ABC.1", "title": "Matched by its DOI"},
        {"pmid": "12345678", "title": "Matched by its PMID"},
        {"title": "Effect of drug A on outcome B in adults: a randomised trial"},
        {"doi": "10.1000/gone", "title": "Melatonin and delirium in older inpatients"},
        {"title": "Drug A versus placebo for outcome B"},
    ]
    entry = _entry(included=included)
    ps = sr.seed_project(kernel, entry)
    _, lib = kernel.project(sr.slug_of(entry))
    _include(ps, lib, [
        # the DOI matches (case and resolver prefix aside) though the title differs
        ItemIn(title="Another title entirely", ids=Ids(doi="https://doi.org/10.1000/abc.1")),
        ItemIn(title="Something else", ids=Ids(pmid="12345678")),
        # the same title as found, with case, punctuation and a subtitle word changed
        ItemIn(title="Effect of Drug A on Outcome B in Adults - a Randomised Trial", ids=Ids(doi="10.1000/x")),
        # a similar but different study (ratio < 90) and an item already matched by its DOI do not count
        ItemIn(title="Melatonin for sleep in children with autism", ids=Ids(doi="10.1000/other")),
    ])
    got = sr.score(ps, entry)
    assert got["recall_included"] == pytest.approx(3 / 5)
    assert sorted(m["title"] for m in got["missed"]) == ["Drug A versus placebo for outcome B",
                                                         "Melatonin and delirium in older inpatients"]
    assert [m["by"] for m in got["matched"]] == ["doi", "pmid", "title"]
    assert got["gates_passed"] is False and got["conclusion_direction_agrees"] is None
    # nothing included yet (search_screen has not passed): recall 0, every study missed
    other = _entry("other-review", included=included)
    ps2 = sr.seed_project(kernel, other)
    got = sr.score(ps2, other)
    assert got["recall_included"] == 0.0 and len(got["missed"]) == 5
    # a title match is one to one: a found study matches one included study at most
    pairs, missed = sr.match_included([sr.Study(title="Trial of A"), sr.Study(title="Trial of A")],
                                      [{"title": "Trial of A", "doi": None, "pmid": None, "item_id": "i1"}])
    assert len(pairs) == 1 and len(missed) == 1


# ---- the direction of the main conclusion -----------------------------------------------------------------------
def _votes(first: str | None, second: str | None) -> Exec:
    script = {}
    for role, choice in (("judge", first), ("judge_2", second)):
        if choice is not None:
            script[(role, "VoteOut")] = [{"choice": choice, "reason": f"the claim says {choice}"}]
    return Exec(script)


def test_direction_needs_two_vendor_agreement(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review",
                                        production_mode="auto")
    env = (k, ps, lib)
    _ready(env)  # argue passed: the main claim, written by the Arguer (anthropic, as judge_3)
    claim = main_claim(ps, stage_row(ps, "argue")["passed_attempt"])
    assert claim is not None

    def judged(eid: str, ex: Exec, direction: str = "benefit") -> dict:
        ctx = replace(production_ctx(k, ps, lib, ex, stage=sr.STAGE), guidance="")
        return sr.score(ps, _entry(eid, direction=direction), ctx=ctx)

    ex = _votes("benefit", "benefit")
    got = judged("agree", ex)
    assert got["conclusion_direction_agrees"] is True and got["direction"] == "benefit"
    assert got["main_claim_id"] == claim
    assert len(ex.of("judge")) == len(ex.of("judge_2")) == 1 and ex.of("judge_3") == []  # the author's vendor
    assert ex.of("judge")[0]["inputs"]["subject"]["outcome"] == "outcome B"
    again = _votes("harm", "harm")  # scored again: the recorded vote is reused, no judge is asked
    assert judged("agree", again)["conclusion_direction_agrees"] is True and again.calls == []
    assert judged("opposite", _votes("harm", "harm"))["conclusion_direction_agrees"] is False
    split = judged("split", _votes("benefit", "harm"))
    assert split["conclusion_direction_agrees"] is None and split["direction"] is None
    assert judged("unclear", _votes("unclear", "unclear"))["conclusion_direction_agrees"] is None
    # one judge of another vendor than the author's is no agreement of two vendors
    s = load_settings()
    save_settings(s.model_copy(update={"drivers": {**s.drivers, "judge_2": DEFAULT_DRIVERS["judge_3"]}}))
    lone = _votes("benefit", "benefit")
    got = judged("lone", lone)
    assert got["conclusion_direction_agrees"] is None and lone.of("judge_2") == []
    # without the judges' context (or before argue passed) nothing is judged
    assert sr.score(ps, _entry("plain"))["conclusion_direction_agrees"] is None
    k.close()


# ---- seeding ----------------------------------------------------------------------------------------------------
def test_seeded_project_starts_at_protocol_with_frozen_rq(kernel):
    entry = _entry()
    ps = sr.seed_project(kernel, entry)
    assert ps.meta("slug") == "srrepro-test-review" == sr.slug_of(entry)
    intake = intake_of(ps)
    assert intake is not None
    assert (intake.paper_type, intake.discovery_mode, intake.production_mode) == ("systematic_review", "d2", "auto")
    assert discovery_passed(ps) and current_stage(ps) == "protocol"
    assert all(stage_row(ps, s)["status"] == "passed" for s in DISCOVERY)
    rq = frozen_rq(ps)
    assert rq is not None and rq["adopted_by"] == "human"
    assert rq["body"]["three_step"]["y"] == entry.question
    assert rq["body"]["pico"] == entry.pico.model_dump()
    # the date window is copied: in the seed record and in the topic the protocol's Ideator and Scout read
    seeded = sr.seeded(ps)
    assert (seeded["entry"], seeded["date_from"], seeded["date_to"]) == ("test-review", 2010, 2022)
    row = ps.db.execute("SELECT * FROM topics WHERE id=?", (rq["topic_id"],)).fetchone()
    topic = json.dumps(dict(row))
    assert "2010" in topic and "2022" in topic
    # the reproduced review is never named to the agents (its included studies would leak through it)
    rq_text = json.dumps(rq["body"]) + topic
    assert entry.doi not in rq_text and entry.title not in rq_text and entry.pmcid not in rq_text
    # seeding again continues the seeded project: nothing is recorded twice
    events = ps.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert sr.seed_project(kernel, entry).root == ps.root
    assert ps.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    # a project with that slug that was not seeded from the entry is refused, not adopted
    other = kernel.create_project("srrepro-not-seeded", "Other", actor="tester")
    record_intake(other, Intake(audience="a", paper_type="systematic_review", discovery_mode="d2",
                                production_mode="auto"), actor="tester", actor_kind="human")
    with pytest.raises(ValueError):
        sr.seed_project(kernel, _entry("not-seeded"))


# ---- the eval command -------------------------------------------------------------------------------------------
@pytest.fixture
def served(serve_rk, tmp_home):
    """``rk serve`` whose runs drive fake workers for every stage (no model), so a seeded run reaches export."""
    def orchestrator(kernel, registry):
        stages = (*DISCOVERY, "protocol", "search_screen", "read_extract", "synthesize", "argue", "draft", "revise",
                  "audit", "export")
        return Orchestrator(kernel, registry, workers={s: FakeWorker() for s in stages})
    return serve_rk(orchestrator=orchestrator)


def test_eval_sr_repro_seeds_runs_continues_and_reports(served, tmp_path):
    entries = sr.load_manifest()
    assert entries, "the manifest has no entry"
    entry = entries[0]
    out = tmp_path / "eval"
    r = CliRunner().invoke(app, ["eval", "sr-repro", "--entry", entry.id, "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert f"srrepro-{entry.id}" in r.output and "seeded" in r.output
    ps, _ = served.kernel.project(sr.slug_of(entry))
    assert current_stage(ps) is not None or stage_row(ps, "export")["status"] == "passed"
    # run again: the seeded project is continued (its run is followed or done), never seeded twice
    import time
    deadline = time.monotonic() + 30
    while stage_row(ps, "export") is None or stage_row(ps, "export")["status"] != "passed":
        assert time.monotonic() < deadline, "the run did not reach export"
        time.sleep(0.05)
    r = CliRunner().invoke(app, ["eval", "sr-repro", "--entry", entry.id, "--write-report", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "exported" in r.output and "seeded" not in r.output
    assert ps.db.execute("SELECT COUNT(*) FROM events WHERE type=?", (sr.SEEDED,)).fetchone()[0] == 1
    (md,) = out.glob("sr-repro-*.md")
    (js,) = out.glob("sr-repro-*.json")
    data = json.loads(js.read_text())
    (row,) = data["entries"]
    assert row["id"] == entry.id and row["recall_included"] == 0.0 and row["exported"] is True
    assert data["targets"] == {"recall_included": 0.85, "conclusion_direction_agrees": True, "gates_passed": True}
    assert entry.id in md.read_text() and "recall" in md.read_text()
    unknown = CliRunner().invoke(app, ["eval", "sr-repro", "--entry", "nope"])
    assert unknown.exit_code != 0 and "nope" in unknown.output
