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
    subject = ex.of("judge")[0]["inputs"]["subject"]
    assert subject["outcome"] == "outcome B"
    # the judges get the conclusion's outcome, not the PICO's intervention or comparison
    assert "intervention_or_exposure" not in subject and "comparison" not in subject
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


def test_eval_sr_repro_seeds_runs_continues_and_reports(served, tmp_path, monkeypatch):
    import rk.cli.main as cli_main

    timeouts: dict = {}
    real_call = cli_main._call

    def spy(name, args, **kw):
        timeouts[name] = kw.get("timeout")
        return real_call(name, args, **kw)

    monkeypatch.setattr(cli_main, "_call", spy)
    entries = sr.load_manifest()
    assert entries, "the manifest has no entry"
    entry = entries[0]
    out = tmp_path / "eval"
    r = CliRunner().invoke(app, ["eval", "sr-repro", "--entry", entry.id, "--out", str(out)])
    assert r.exit_code == 0, r.output
    # scoring may wait on two judges: the call is not cut at the client's default 300 s
    assert (timeouts["rk_sr_repro_score"] or 0) >= 3600
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
    assert row["selection_ceiling"] == pytest.approx(entry.search.included_found / len(entry.included))
    assert data["targets"] == {"recall_included": 0.85, "conclusion_direction_agrees": True, "gates_passed": True}
    assert entry.id in md.read_text() and "recall" in md.read_text()
    unknown = CliRunner().invoke(app, ["eval", "sr-repro", "--entry", "nope"])
    assert unknown.exit_code != 0 and "nope" in unknown.output


# ---- round 1 (the Task 8 review) --------------------------------------------------------------------------------
RUN = {"project": "srrepro-x", "action": "scored", "run_id": None, "run_status": None}


def _studies() -> dict[str, sr.Study]:
    return {s.ref: s for e in sr.load_manifest() for s in e.included if s.ref}


def test_title_match_refuses_a_title_whose_words_are_a_subset():
    studies = _studies()
    # token_set_ratio alone scores these 100: the found title's words are a subset of the included study's
    for found, ref in (("Fructose induced hypertension in rats", "ref-63"),
                       ("Trans fatty acids in edible oils", "B17-nutrients-14-01489")):
        pairs, missed = sr.match_included([studies[ref]], [{"title": found, "doi": None, "pmid": None,
                                                             "item_id": "i1"}])
        assert pairs == [] and missed == [studies[ref]], found
    # the same title with case and punctuation changed still matches
    pairs, _ = sr.match_included([studies["ref-63"]], [{"title": studies["ref-63"].title.upper() + ".",
                                                         "doi": None, "pmid": None, "item_id": "i1"}])
    assert [p["by"] for p in pairs] == ["title"]
    # a subset title beside the real one does not hide it: the best candidate is taken among those passing both
    real = {"title": "Effect of Salix tetrasperma Roxb. on fructose-induced hypertension in rats", "doi": None,
            "pmid": None, "item_id": "real"}  # set and sort ratio 91.9
    subset = {"title": "Fructose induced hypertension in rats", "doi": None, "pmid": None,
              "item_id": "subset"}  # set ratio 100, sort ratio 66
    assert [p["item_id"] for p in sr.match_included([studies["ref-63"]], [real])[0]] == ["real"]
    assert [p["item_id"] for p in sr.match_included([studies["ref-63"]], [subset, real])[0]] == ["real"]


def test_objection_from_review_compromises_the_direction(tmp_home, replay_http):
    from rk.argument import ArgumentService, ObjectionIn
    from rk.production.argue import _note

    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review",
                                        production_mode="auto")
    _ready((k, ps, lib))
    attempt = stage_row(ps, "argue")["passed_attempt"]
    main = main_claim(ps, attempt)
    entry = _entry("objected", doi="10.5555/the-review", title="A pooled analysis of drug A and outcome B in adults")
    # the Skeptic found the review (outside the search window) and put it in the library: information only
    review = lib.add_item(ItemIn(title=entry.title, ids=Ids(doi=entry.doi)), added_by="agent", actor="tester").item_id
    got = sr.score(ps, entry)
    assert got["review_in_library"] is True and got["objection_from_review"] is False
    assert got["direction_compromised"] is None
    # then objected to the main claim with the review's pooled result
    oid = ArgumentService(ps).add_objection(ObjectionIn(target_id=main, category="counterexample", priority=1,
                                                        text="The pooled analysis finds the opposite.",
                                                        source_id=review), actor="tester")
    _note(ps, attempt, "critique", actor="tester", round=99, objection_ids=[oid])
    got = sr.score(ps, entry)
    assert got["objection_from_review"] is True and got["claim_from_review"] is False
    assert "objection" in got["direction_compromised"]
    row = sr.report_row(entry, RUN, {**got, "conclusion_direction_agrees": True})
    assert row["meets"]["conclusion_direction_agrees"] is False and row["objection_from_review"] is True
    k.close()


def test_review_included_compromises_the_direction(kernel):
    entry = _entry("leaky")
    ps = sr.seed_project(kernel, entry)
    _, lib = kernel.project(sr.slug_of(entry))
    # the review only retrieved (in the library, not included): information, not a compromise
    lib.add_item(ItemIn(title=entry.title, ids=Ids(doi=entry.doi)), added_by="agent", actor="tester")
    got = sr.score(ps, entry)
    assert got["review_in_library"] is True and got["review_included"] is False
    assert got["claim_from_review"] is False and got["direction_compromised"] is None
    row = sr.report_row(entry, RUN, {**got, "conclusion_direction_agrees": True})
    assert row["meets"]["conclusion_direction_agrees"] is True and row["review_in_library"] is True
    # the review included as one of the project's studies
    _include(ps, lib, [ItemIn(title=entry.title, ids=Ids(doi=entry.doi))])
    got = sr.score(ps, entry)
    assert got["review_included"] is True and "included" in got["direction_compromised"]
    row = sr.report_row(entry, RUN, {**got, "conclusion_direction_agrees": True})
    assert row["meets"]["conclusion_direction_agrees"] is False and row["direction_compromised"]
    assert "included" in sr.write_report([row], kernel.home / "eval-out")[0].read_text()


def test_claim_from_review_compromises_the_direction(tmp_home, replay_http):
    from rk.argument import ArgumentService

    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review",
                                        production_mode="auto")
    _ready((k, ps, lib))
    svc = ArgumentService(ps)
    main = main_claim(ps, stage_row(ps, "argue")["passed_attempt"])

    def items(cid: str) -> set[str]:
        return {n["item_id"] for e in svc.get_claim(cid)["evidence"] for n in e["notes"] if n}

    reasons = [e["from_id"] for e in svc.list_edges(to_id=main, type="reason_of")]
    main_items = items(main)
    reason_items = set().union(*(items(r) for r in reasons))
    assert reasons and main_items - reason_items and reason_items, "the fixture's argument has a reason"

    def review_of(eid: str, iid: str) -> sr.Entry:
        data = json.loads(ps.db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone()["data"])
        return _entry(eid, doi=(data.get("ids") or {}).get("doi") or "10.9999/no-doi", title=data["title"])

    def judged(entry: sr.Entry) -> dict:
        ctx = replace(production_ctx(k, ps, lib, _votes("benefit", "benefit"), stage=sr.STAGE), guidance="")
        return sr.score(ps, entry, ctx=ctx)

    def compromised(eid: str, iid: str) -> None:
        got = judged(review_of(eid, iid))
        assert got["claim_from_review"] is True and got["conclusion_direction_agrees"] is True, eid
        row = sr.report_row(review_of(eid, iid), RUN, got)
        assert row["meets"]["conclusion_direction_agrees"] is False and "main claim" in row["direction_compromised"]

    only_main = min(main_items - reason_items)
    compromised("from-main", only_main)  # an evidence note of the main claim itself
    # a study only a reason rests on: the main claim's own evidence taken out of the projection, so the review is
    # reached through the reason_of edge alone
    ps.db.execute("DELETE FROM evidence WHERE claim_id=?", (main,))
    ps.db.commit()
    compromised("from-reason", min(reason_items))
    assert judged(review_of("main-gone", only_main))["claim_from_review"] is False
    clean = judged(_entry("clean", doi="10.9999/elsewhere", title="An unrelated review of sleep in shift workers"))
    assert clean["claim_from_review"] is False and clean["direction_compromised"] is None
    k.close()


def test_agents_never_get_the_main_conclusion():
    entries = {e.id: e for e in sr.load_manifest()}
    for e in entries.values():
        seen = json.dumps([sr.rq_body(e).model_dump(), sr._topic(e).model_dump(),
                           [h.model_dump() for h in sr._hypotheses(e)]], ensure_ascii=False)
        assert e.main_conclusion.outcome not in seen, e.id
        # a PICO states the question, not the review's finding (tfa: the 200 °C threshold; fructose: its subgroups)
        assert "200 °C" not in seen and "10–30%" not in seen and "60–75%" not in seen, e.id
    # a clinical PICO makes the RQ clinical, whatever the domain
    assert sr.rq_body(entries["ai-pulmonary-hypertension"]).clinical is True


def test_gates_passed_reads_the_latest_report_of_the_passed_audit(tmp_home, replay_http, monkeypatch):
    from rk.production.audit import AuditWorker
    from tests.test_audit import _ctx, _judges, revised

    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="critical_review")
    env = (k, ps, lib)
    revised(env)
    assert sr.gates_passed(ps) is False  # no audit yet
    assert AuditWorker().run(_ctx(env, _judges("approve"))).status == "done"
    assert sr.gates_passed(ps) is False  # its report is ok, but the audit has not passed
    record_attempt(ps, "audit", 1, run_id=None, guidance="", requested=None, actor="tester")
    record_ran(ps, "audit", 1, "done", "", actor="tester")
    record_passed(ps, "audit", 1, actor="tester")
    assert sr.gates_passed(ps) is True
    reports = sr.audit_reports(ps)
    monkeypatch.setattr(sr, "audit_reports", lambda _ps: [*reports, {**reports[-1], "version": 99, "ok": False}])
    assert sr.gates_passed(ps) is False  # the latest report of the passed attempt fails a gate
    k.close()


def test_seeding_resumes_after_each_interruption(kernel, monkeypatch):
    def crash_once(name: str) -> None:
        real = getattr(sr, name)

        def crash(*a, **kw):
            monkeypatch.setattr(sr, name, real)
            raise RuntimeError(f"interrupted in {name}")
        monkeypatch.setattr(sr, name, crash)

    def count(ps, sql: str, *args) -> int:
        return ps.db.execute(sql, args).fetchone()[0]

    # the project was created, its seed record never written: it is yours, so seeding goes on
    a = _entry("crash-created")
    kernel.create_project(sr.slug_of(a), f"Reproduction set: {a.id}", field=a.domain, paper_type=sr.PAPER_TYPE,
                          actor="tester")
    ps = sr.seed_project(kernel, a)
    assert sr.seeded(ps)["entry"] == a.id and current_stage(ps) == "protocol"
    assert count(ps, "SELECT COUNT(*) FROM events WHERE type=?", sr.SEEDED) == 1
    # a discovery stage ran, its pass not recorded: it passes without running twice
    b = _entry("crash-ran")
    crash_once("record_passed")
    with pytest.raises(RuntimeError):
        sr.seed_project(kernel, b)
    ps = sr.seed_project(kernel, b)
    for s in DISCOVERY:
        assert count(ps, "SELECT COUNT(*) FROM events WHERE type='stage.ran' AND json_extract(payload, '$.stage')=?",
                     s) == 1, s
    assert current_stage(ps) == "protocol"
    # the topic was added, its RQ not: the topic is used, not added again
    c = _entry("crash-topic")
    crash_once("add_rq")
    with pytest.raises(RuntimeError):
        sr.seed_project(kernel, c)
    ps = sr.seed_project(kernel, c)
    assert count(ps, "SELECT COUNT(*) FROM topics") == 1 and frozen_rq(ps) is not None
    assert current_stage(ps) == "protocol"
