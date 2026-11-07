import json

import pytest

from rk.argument import ArgumentService
from rk.checkpoints import list_checkpoints
from rk.config import load_settings, save_settings
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.discovery.gaps import GapsWorker, distortion
from rk.discovery.gaps.contradiction import MAX_PAIRS, ContradictionMiner
from rk.discovery.gaps.distortion import MAX_ITEMS, DistortionMiner
from rk.discovery.models import Cluster, ContradictionsOut
from rk.discovery.passages import Passage, top_passages
from rk.discovery.store import add_cluster, gaps
from rk.library.models import Ids, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.notes import list_notes
from rk.orchestrator.engine import StageContext
from rk.orchestrator.state import record_passed, tasks_used
from rk.roles.prompts import build_system_prompt
from rk.tools.builtin import build_registry
from rk.verify.support import verify_support

from .discovery_fakes import (
    FakeJudge,
    FakeLayer1,
    FakeSources,
    ScriptedExecutor,
    add_text_layer,
    make_project,
)

# The gapminer runs on claude_cli (vendor anthropic), so judges from two other vendors are needed.
OPENAI, DEEPSEEK = DriverSpec("codex_cli"), DriverSpec.parse("ollama:deepseek-v4.1-flash:cloud")
ANTHROPIC = DriverSpec("omp_gateway", "anthropic/claude-haiku-4-5")

FILLER = "Aspirin is widely used for the prevention of cardiovascular events in older adults. " * 3
QUOTE_A = "In our cohort, aspirin lowered the stroke rate in adults over 70."
QUOTE_B = "Aspirin did not lower the stroke rate in adults over 70."
PAGES_A = [FILLER, f"We followed 900 patients. {QUOTE_A} Bleeding was rare."]
PAGES_B = [FILLER, f"We followed 1200 patients. {QUOTE_B} Bleeding was common."]

CITING_QUOTE = "Smith and colleagues showed that aspirin halves the stroke rate in all adults."
PAGES_CITING = [FILLER, f"Earlier work is mixed. {CITING_QUOTE} We revisit this claim."]
PAGES_CITED = [
    "This trial tested aspirin in adults over 70. The stroke rate fell slightly. The difference was not significant.",
    "Bleeding was more common with aspirin. Younger adults were not enrolled. Further trials are needed.",
    "The funding came from a public grant. The authors declare no conflicts. Data are available on request.",
]
MINER_TEXT = "Aspirin halved the stroke rate in every adult we studied."

TRIAL_A = ItemIn(title="Aspirin cohort in older adults", year=2016, ids=Ids(doi="10.1000/a"))
TRIAL_B = ItemIn(title="Aspirin registry in older adults", year=2019, ids=Ids(doi="10.1000/b"))
CITING = ItemIn(title="Aspirin review", year=2022, ids=Ids(doi="10.1000/citing"))
CITED = ItemIn(title="Smith aspirin trial", year=2014, ids=Ids(doi="10.1000/cited"))


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http)
    yield k, ps, lib
    k.close()


@pytest.fixture
def collab(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http, production_mode="collab")
    yield k, ps, lib
    k.close()


def _ctx(k, ps, lib, executor, sources) -> StageContext:
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project="demo", stage="gaps", attempt=1,
                        run_id=None, guidance="Focus on older adults.", executor=executor, sources=sources,
                        actor="tester")


def _landscape(ps, lib, items: list[ItemIn], texts: list[list[str]]) -> list[str]:
    """Landscape attempt 1 passed with one cluster holding ``items`` (all representative, each with its text)."""
    ids = [lib.add_item(it, added_by="agent", actor="tester").item_id for it in items]
    for iid, pages in zip(ids, texts, strict=True):
        add_text_layer(ps, iid, pages)
    add_cluster(ps, 1, Cluster(label="Stroke", description="Aspirin and stroke.", query="aspirin stroke", item_ids=ids,
                               representative_ids=ids), actor="tester")
    record_passed(ps, "landscape", 1, actor="tester")
    return ids


def _judges(*specs_labels: tuple[DriverSpec, str], spans=()) -> dict:
    providers = {str(spec): FakeJudge(label, list(spans)) for spec, label in specs_labels}
    return {"judge_drivers": [spec for spec, _ in specs_labels], "judge_providers": providers}


def _cluster_id(ps) -> str:
    return ps.db.execute("SELECT id FROM clusters").fetchone()["id"]


def _note_texts(ps, ids: list[str]) -> list[str]:
    by_id = {n["id"]: n["text"] for n in list_notes(ps)}
    return [by_id[i] for i in ids]


# ---- contradiction ----
def _contradiction(env, label: str, p: float):
    k, ps, lib = env
    a, b = _landscape(ps, lib, [TRIAL_A, TRIAL_B], [PAGES_A, PAGES_B])
    pair = {"cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": QUOTE_A},
            "b": {"source_id": b, "quote": QUOTE_B}}
    ex = ScriptedExecutor({("gapminer", "ContradictionsOut"): [{"pairs": [pair]}]})
    miner = ContradictionMiner(layer1=FakeLayer1(p), **_judges((OPENAI, label), (DEEPSEEK, label),
                                                                 spans=["did not lower the stroke rate"]))
    GapsWorker(miners=[miner]).run(_ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    (gap,) = gaps(ps, 1)
    return gap, ex, (a, b)


def test_contradiction_needs_contradicted_verdict(env):
    _k, ps, _lib = env
    gap, ex, (a, b) = _contradiction(env, "contradicted", 0.03)
    assert gap["kind"] == "contradiction" and gap["verified"] is True
    assert gap["checks"]["same_cluster"] == gap["checks"]["quote_a"] == gap["checks"]["quote_b"] == "pass"
    assert gap["checks"]["support"] == "pass" and gap["checks"]["verdict"] == "contradicted"
    assert gap["checks"]["p"] == 0.03 and [o["label"] for o in gap["checks"]["opinions"]] == ["contradicted"] * 2
    assert _note_texts(ps, gap["evidence"]["note_ids"]) == [QUOTE_A, QUOTE_B]
    assert gap["evidence"]["item_ids"] == [a, b]
    # claim A is quote A, written by the gapminer's vendor, with note B as its evidence
    (cid,) = gap["evidence"]["claim_ids"]
    claim = ArgumentService(ps).get_claim(cid)
    assert claim["text"] == QUOTE_A and claim["generator_vendor"] == "anthropic" and claim["origin"] == "gaps"
    assert claim["status"] == "contradicted"
    assert [ev["note_ids"] for ev in claim["evidence"]] == [gap["evidence"]["note_ids"][1:]]
    assert gap["details"]["claim_id"] == cid
    assert TRIAL_A.title in gap["statement"] and TRIAL_B.title in gap["statement"]
    assert QUOTE_A in gap["statement"] and QUOTE_B in gap["statement"]
    call, *judged = ex.calls
    assert call["role"] == "gapminer" and call["inputs"]["guidance"] == "Focus on older adults."
    assert [w["item_id"] for w in call["inputs"]["clusters"][0]["works"]] == [a, b]
    # the judges ran through the stage's task executor, on their own drivers
    assert [(c["role"], c["stage"], c["attempt"], c["driver"]) for c in judged] == [
        ("judge", "gaps", 1, str(OPENAI)), ("judge_2", "gaps", 1, str(DEEPSEEK))]


def test_contradiction_uncertain_verdict_is_stored_unverified(env):
    gap, _, _ = _contradiction(env, "partial", 0.5)  # judges who see only partial support: the verdict is uncertain
    assert gap["verified"] is False and gap["checks"]["support"] == "fail"
    assert gap["checks"]["verdict"] == "uncertain" and "uncertain" in gap["checks"]["reasons"]["support"]
    assert gap["statement"].startswith("Possible contradiction")


def test_contradiction_across_clusters_is_not_verified(env):
    k, ps, lib = env
    a, _b = _landscape(ps, lib, [TRIAL_A, TRIAL_B], [PAGES_A, PAGES_B])
    other = lib.add_item(ItemIn(title="Unclustered work", year=2020), added_by="agent", actor="tester").item_id
    add_text_layer(ps, other, PAGES_B)
    pair = {"cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": QUOTE_A},
            "b": {"source_id": other, "quote": QUOTE_B}}
    ex = ScriptedExecutor({("gapminer", "ContradictionsOut"): [{"pairs": [pair]}]})
    judge = _judges((OPENAI, "contradicted"), (DEEPSEEK, "contradicted"), spans=["did not lower"])
    GapsWorker(miners=[ContradictionMiner(layer1=FakeLayer1(0.01), **judge)]).run(
        _ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    (gap,) = gaps(ps, 1)
    assert gap["verified"] is False and gap["checks"]["same_cluster"] == "fail"
    assert gap["checks"]["quote_a"] == gap["checks"]["support"] == "skipped"
    assert list_notes(ps) == [] and ArgumentService(ps).list_claims(origin="gaps") == []


def test_contradiction_pairs_are_capped(env):
    k, ps, lib = env
    a, b = _landscape(ps, lib, [TRIAL_A, TRIAL_B], [PAGES_A, PAGES_B])
    pair = {"cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": QUOTE_A},
            "b": {"source_id": b, "quote": "not verbatim"}}
    ex = ScriptedExecutor({("gapminer", "ContradictionsOut"): [{"pairs": [pair] * (MAX_PAIRS + 2)}]})
    GapsWorker(miners=[ContradictionMiner(layer1=FakeLayer1(0.01))]).run(_ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    assert MAX_PAIRS == 4 and len(gaps(ps, 1)) == MAX_PAIRS
    assert all(g["checks"]["quote_b"] == "fail" and g["checks"]["support"] == "skipped" for g in gaps(ps, 1))


def test_contradiction_quote_too_long_for_a_claim_is_not_verified(env):
    k, ps, lib = env
    long_a = "In our cohort, aspirin lowered the stroke rate " + "and the bleeding rate " * 100 + "in adults over 70."
    a, b = _landscape(ps, lib, [TRIAL_A, TRIAL_B], [[FILLER, long_a], PAGES_B])
    pair = {"cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": long_a},
            "b": {"source_id": b, "quote": QUOTE_B}}
    ex = ScriptedExecutor({("gapminer", "ContradictionsOut"): [{"pairs": [pair]}]})
    GapsWorker(miners=[ContradictionMiner(layer1=FakeLayer1(0.01))]).run(_ctx(k, ps, lib, ex, FakeSources({}, lib=lib)))
    (gap,) = gaps(ps, 1)
    assert gap["checks"]["quote_a"] == gap["checks"]["quote_b"] == "pass" and gap["checks"]["support"] == "fail"
    assert "cannot be checked as a claim" in gap["checks"]["reasons"]["support"] and gap["verified"] is False


# ---- citation distortion ----
def _distortion_setup(env, *, cited: dict, fulltexts: dict | None = None):
    _k, ps, lib = env
    (citing,) = _landscape(ps, lib, [CITING], [PAGES_CITING])
    sources = FakeSources({}, lib=lib, resolvable={"10.1000/cited": CITED},
                          fulltexts={CITED.title: PAGES_CITED} if fulltexts is None else fulltexts)
    item = {"citing_source_id": citing, "quote": CITING_QUOTE, "cited": cited}
    ex = ScriptedExecutor({("gapminer", "DistortionsOut"): [{"items": [item]}]})
    return citing, sources, ex


def _run_distortion(env, sources, ex, judges: dict, p: float = 0.04):
    k, ps, lib = env
    miner = DistortionMiner(layer1=FakeLayer1(p), **judges)
    GapsWorker(miners=[miner]).run(_ctx(k, ps, lib, ex, sources))
    (gap,) = gaps(ps, 1)
    return gap, miner


def test_distortion_uses_kernel_passages_not_miner_text(env):
    _k, ps, lib = env
    citing, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited", "quote": MINER_TEXT,
                                                         "text": MINER_TEXT})
    judges = _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported"))
    gap, miner = _run_distortion(env, sources, ex, judges)
    cited = gap["details"]["cited_id"]
    assert lib.get(cited).title == CITED.title and lib.get(cited).discovered_via == "backward"
    assert ("add", "10.1000/cited") in sources.calls and ("verify", cited) in sources.calls
    assert gap["kind"] == "citation_distortion" and gap["verified"] is True
    assert gap["checks"]["fulltext"] == "pass" and gap["checks"]["verdict"] == "unsupported"
    passages = [p.text for p in top_passages(ps, cited, CITING_QUOTE, k=5)]
    note_ids = gap["checks"]["passage_note_ids"]
    assert _note_texts(ps, note_ids) == passages and gap["evidence"]["note_ids"][1:] == note_ids
    assert _note_texts(ps, gap["evidence"]["note_ids"][:1]) == [CITING_QUOTE]
    assert all(MINER_TEXT not in n["text"] for n in list_notes(ps))
    judged = [i["evidence"] for j in judges["judge_providers"].values() for i in j.inputs]
    assert len(judged) == 2 and all(MINER_TEXT not in e and passages[0] in e for e in judged)
    assert miner.layer1.calls[0][1] == CITING_QUOTE
    (cid,) = gap["evidence"]["claim_ids"]
    claim = ArgumentService(ps).get_claim(cid)
    assert claim["text"] == CITING_QUOTE and claim["generator_vendor"] == "anthropic" and claim["origin"] == "gaps"
    assert gap["checks"]["p"] == 0.04 and len(gap["checks"]["opinions"]) == 2
    assert gap["evidence"]["item_ids"] == [citing, cited] and gap["cluster_id"] == _cluster_id(ps)
    assert CITING.title in gap["statement"] and CITED.title in gap["statement"]
    assert "within the searched scope" in gap["statement"]


def test_distortion_without_fulltext_is_unverified(env):
    _k, _ps, lib = env
    cited = lib.add_item(CITED, added_by="agent", actor="tester").item_id
    _, sources, ex = _distortion_setup(env, cited={"item_id": cited}, fulltexts={})
    judges = _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported"))
    gap, miner = _run_distortion(env, sources, ex, judges)
    assert gap["verified"] is False and gap["checks"]["fulltext"] == "missing"
    assert gap["checks"]["citing_quote"] == gap["checks"]["cited"] == gap["checks"]["cited_ref"] == "pass"
    assert gap["checks"]["passages"] == gap["checks"]["support"] == "skipped"
    assert gap["checks"]["passage_note_ids"] == [] and miner.layer1.calls == []
    assert ("add", "10.1000/cited") not in sources.calls
    assert all(j.inputs == [] for j in judges["judge_providers"].values())


def test_distortion_needs_two_vendor_agreement(env):
    _k, _ps, _lib = env
    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited"})
    gap, _ = _run_distortion(env, sources, ex, _judges((OPENAI, "unsupported"), (ANTHROPIC, "unsupported")))
    assert gap["verified"] is False and gap["checks"]["verdict"] == "uncertain" and gap["checks"]["support"] == "fail"
    assert [o["error"] for o in gap["checks"]["opinions"]] == [None, "same_or_unknown_vendor"]
    assert gap["statement"].startswith("Possible citation distortion")


def test_distortion_with_unknown_cited_work_is_unverified(env):
    _k, _ps, _lib = env
    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/unknown"})
    gap, _ = _run_distortion(env, sources, ex, _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported")))
    assert gap["verified"] is False and gap["checks"]["cited"] == "fail"
    assert gap["checks"]["fulltext"] == "skipped" and gap["evidence"]["claim_ids"] == []


def test_distortion_needs_the_cited_work_to_pass_v_ref(env):
    _k, _ps, _lib = env
    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited"})
    sources.statuses = {CITED.title: "mismatch"}
    judges = _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported"))
    gap, miner = _run_distortion(env, sources, ex, judges)
    assert gap["verified"] is False and gap["checks"]["cited"] == "pass" and gap["checks"]["cited_ref"] == "fail"
    assert gap["checks"]["ref_status"] == "mismatch" and "mismatch" in gap["checks"]["reasons"]["cited_ref"]
    assert gap["checks"]["fulltext"] == gap["checks"]["support"] == "skipped" and miner.layer1.calls == []
    assert all(j.inputs == [] for j in judges["judge_providers"].values())


@pytest.mark.parametrize("call, check", [("add", "cited"), ("verify", "cited_ref"), ("fulltext", "fulltext")])
def test_distortion_source_error_fails_its_check_not_the_run(env, call, check):
    from rk.net.http import HttpError

    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited"})

    def broken(*args):
        raise HttpError(502, f"https://example.org/{call}")

    setattr(sources, call, broken)
    gap, miner = _run_distortion(env, sources, ex, _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported")))
    assert gap["verified"] is False and gap["checks"][check] == "fail"
    assert "failed (HttpError: HTTP 502" in gap["checks"]["reasons"][check]
    assert miner.layer1.calls == []


def test_distortion_with_a_rejected_kernel_passage_is_unverified(env, monkeypatch):
    _k, _ps, _lib = env
    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited"})
    real = distortion.top_passages
    monkeypatch.setattr(distortion, "top_passages", lambda *a, **kw: [
        *real(*a, **kw), Passage(text="A sentence the cited work never contains.", page=1, start=0, end=41)])
    judges = _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported"))
    gap, miner = _run_distortion(env, sources, ex, judges)
    assert gap["verified"] is False and gap["checks"]["passages"] == "fail"
    assert gap["checks"]["support"] == "skipped" and miner.layer1.calls == []
    assert [r["reason"] for r in gap["checks"]["rejected_passages"]] == ["quote_not_found"]
    assert "1 of 4 passages" in gap["checks"]["reasons"]["passages"]


def test_distortion_passage_notes_keep_their_kernel_positions(env):
    _k, ps, _lib = env
    # the cited work repeats its first page: V-QUOTE alone would locate both copies on page 1
    _, sources, ex = _distortion_setup(env, cited={"doi": "10.1000/cited"},
                                       fulltexts={CITED.title: [PAGES_CITED[0], PAGES_CITED[0]]})
    gap, _ = _run_distortion(env, sources, ex, _judges((OPENAI, "unsupported"), (DEEPSEEK, "unsupported")))
    cited = gap["details"]["cited_id"]
    passages = top_passages(ps, cited, CITING_QUOTE, k=5)
    assert sorted(p.page for p in passages) == [1, 2]
    by_id = {n["id"]: n["locator"] for n in list_notes(ps)}
    assert [by_id[i] for i in gap["checks"]["passage_note_ids"]] == [
        {"page": p.page, "start": p.start, "end": p.end} for p in passages]
    assert gap["checks"]["passages"] == "pass" and gap["verified"] is True


def test_distortion_items_are_capped(env):
    k, ps, lib = env
    citing, sources, _ = _distortion_setup(env, cited={"doi": "10.1000/cited"})
    item = {"citing_source_id": citing, "quote": "not in the text", "cited": {"doi": "10.1000/cited"}}
    ex = ScriptedExecutor({("gapminer", "DistortionsOut"): [{"items": [item] * (MAX_ITEMS + 3)}]})
    GapsWorker(miners=[DistortionMiner(layer1=FakeLayer1(0.01))]).run(_ctx(k, ps, lib, ex, sources))
    assert MAX_ITEMS == 4 and len(gaps(ps, 1)) == MAX_ITEMS
    assert all(g["checks"]["citing_quote"] == "fail" for g in gaps(ps, 1)) and sources.calls == []


# ---- both ----
def test_gap_mining_opens_no_uncertain_support_checkpoint(collab):
    k, ps, lib = collab
    a, b, citing = _landscape(ps, lib, [TRIAL_A, TRIAL_B, CITING], [PAGES_A, PAGES_B, PAGES_CITING])
    pair = {"cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": QUOTE_A},
            "b": {"source_id": b, "quote": QUOTE_B}}
    item = {"citing_source_id": citing, "quote": CITING_QUOTE, "cited": {"doi": "10.1000/cited"}}
    ex = ScriptedExecutor({("gapminer", "ContradictionsOut"): [{"pairs": [pair]}],
                           ("gapminer", "DistortionsOut"): [{"items": [item]}]})
    sources = FakeSources({}, lib=lib, resolvable={"10.1000/cited": CITED}, fulltexts={CITED.title: PAGES_CITED})
    judges = _judges((OPENAI, "partial"), (DEEPSEEK, "partial"), spans=["did not lower"])
    miners = [ContradictionMiner(layer1=FakeLayer1(0.5), **judges), DistortionMiner(layer1=FakeLayer1(0.5), **judges)]
    GapsWorker(miners=miners).run(_ctx(k, ps, lib, ex, sources))
    stored = gaps(ps, 1)
    assert [g["checks"]["verdict"] for g in stored] == ["uncertain", "uncertain"]
    assert list_checkpoints(ps) == []
    # the project is in collab mode: the same claim checked outside gap mining opens the checkpoint
    cid = stored[0]["evidence"]["claim_ids"][0]
    verify_support(k, None, ps, cid, actor="tester", layer1=FakeLayer1(0.5), min_judges=2, **judges)
    assert [c["kind"] for c in list_checkpoints(ps)] == ["uncertain_support"]


class GapminerProvider:
    """The gapminer on claude_cli, returning one scripted output."""
    name, model, vendor, kind = "claude_cli", None, "anthropic", "chat_api"

    def __init__(self, output):
        self.output = output

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        return TaskResult(spec.task_id, True, self.output, "", "claude_cli", None, 0.0, None)


def _kernel_executor_run(env, *, max_tasks: int = 40, calls_per_hour: dict | None = None):
    """Contradiction mining in a D2 run session through the real ``KernelExecutor`` (fake providers)."""
    k, ps, lib = env
    save_settings(load_settings().model_copy(update={"calls_per_hour": calls_per_hour or {}}))
    reg = build_registry(k)
    a, b = _landscape(ps, lib, [TRIAL_A, TRIAL_B], [PAGES_A, PAGES_B])
    out = ContradictionsOut.model_validate({"pairs": [{
        "cluster_id": _cluster_id(ps), "a": {"source_id": a, "quote": QUOTE_A}, "b": {"source_id": b, "quote": QUOTE_B}}]})
    ex = KernelExecutor(k, reg, "demo", run_id="run_g", session=1, max_tasks_per_stage=max_tasks,
                        provider_for=lambda role: GapminerProvider(out))
    judges = _judges((OPENAI, "contradicted"), (DEEPSEEK, "contradicted"), spans=["did not lower the stroke rate"])
    ctx = StageContext(kernel=k, registry=reg, ps=ps, lib=lib, project="demo", stage="gaps", attempt=1, run_id="run_g",
                       guidance="", executor=ex, sources=FakeSources({}, lib=lib), actor="tester")
    return GapsWorker(miners=[ContradictionMiner(layer1=FakeLayer1(0.02), **judges)]), ctx


def test_gap_judges_count_toward_the_stage_budget(env):
    _k, ps, _lib = env
    worker, ctx = _kernel_executor_run(env)
    worker.run(ctx)
    (gap,) = gaps(ps, 1)
    assert gap["verified"] is True
    rows = [tuple(r) for r in ps.db.execute(
        "SELECT t.role, r.run_id, r.session, r.stage, r.attempt FROM run_tasks r JOIN tasks t ON t.id = r.task_id "
        "ORDER BY r.rowid")]
    assert rows == [("gapminer", "run_g", 1, "gaps", 1), ("judge", "run_g", 1, "gaps", 1),
                    ("judge_2", "run_g", 1, "gaps", 1)]
    assert tasks_used(ps, "run_g", 1, "gaps") == 3


def test_gap_judge_beyond_the_stage_budget_stops_the_attempt(env):
    _k, ps, _lib = env
    worker, ctx = _kernel_executor_run(env, max_tasks=2)  # the gapminer and the first judge
    with pytest.raises(BudgetExceeded) as e:
        worker.run(ctx)
    assert (e.value.stage, e.value.used, e.value.limit) == ("gaps", 2, 2)
    assert gaps(ps, 1) == [] and ps.db.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] == 0


def test_rate_capped_gap_judge_pauses_instead_of_an_uncertain_verdict(env):
    _k, ps, _lib = env
    worker, ctx = _kernel_executor_run(env, calls_per_hour={"codex_cli": 0})  # the first judge's driver is capped
    with pytest.raises(RateCapped) as e:
        worker.run(ctx)
    assert e.value.driver == "codex_cli"
    assert gaps(ps, 1) == [] and ps.db.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] == 0


def test_gap_claims_stay_out_of_the_argument_listing(env):
    _k, ps, _lib = env
    _contradiction(env, "contradicted", 0.03)
    svc = ArgumentService(ps)
    assert svc.list_claims() == [] and svc.list_claims("contradicted") == []
    assert [c["text"] for c in svc.list_claims(origin="gaps")] == [QUOTE_A]
    assert json.loads(ps.db.execute("SELECT data FROM claims").fetchone()["data"])["origin"] == "gaps"


def test_gaps_prompts_cover_contradictions_and_distortions():
    text = build_system_prompt("gapminer", None)
    assert "ContradictionsOut" in text and "DistortionsOut" in text
