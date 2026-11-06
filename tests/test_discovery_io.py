import itertools
import json

import httpx
import pytest
from pydantic import BaseModel

from rk.config import Settings, load_settings, save_settings
from rk.discovery.executor import BudgetExceeded, KernelExecutor, RateCapped
from rk.discovery.passages import top_passages
from rk.discovery.sources import KernelSources, log_and_search
from rk.discovery.store import searches
from rk.fulltext import latest_snapshot
from rk.library.models import Ids, ItemIn
from rk.llm.ratelimit import WINDOW_S, CallCaps
from rk.llm.types import Availability, TaskResult
from rk.llm.vendors import vendor_of
from rk.net.http import HttpClient
from rk.orchestrator.state import tasks_used
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import ToolError
from rk.verify.retract import RetractionInfo
from rk.workspace import Kernel

from .discovery_fakes import FakeSources, ScriptedExecutor, add_text_layer, make_project


class Found(BaseModel):
    found: bool


class OkProvider:
    name = "fake"
    model = "fake-1"
    vendor = "openai"
    kind = "chat_api"

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        return TaskResult(spec.task_id, True, Found(found=True), "{}", "fake", "fake-1", 0.0, None)


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib = make_project(tmp_home, replay_http)
    save_settings(load_settings().model_copy(update={"calls_per_hour": {}}))  # no hourly caps unless a test sets one
    yield k, build_registry(k), ps, lib
    k.close()


def _executor(env, **kw):
    k, reg, _, _ = env
    return KernelExecutor(k, reg, "demo", provider_for=lambda role: OkProvider(), **kw)


def _task(ex, stage, attempt=1):
    return ex.run("gapminer", "do", {}, Found, stage=stage, attempt=attempt)


def test_budget_stops_the_41st_task_in_an_open_stage(env):
    ex = _executor(env, run_id="run_1", session=1)
    for _ in range(40):
        assert _task(ex, "gaps").ok
    with pytest.raises(BudgetExceeded) as e:
        _task(ex, "gaps")
    assert (e.value.stage, e.value.used, e.value.limit) == ("gaps", 40, 40)
    assert _task(ex, "landscape").ok  # the budget is per stage
    assert _task(_executor(env, run_id="run_1", session=2), "gaps").ok  # ... and per run session

    small = _executor(env, run_id="run_1", session=3, max_tasks_per_stage=1)
    assert _task(small, "topics").ok
    with pytest.raises(BudgetExceeded):
        _task(small, "topics")
    for _ in range(3):
        assert _task(small, "question").ok  # question is not an open stage: no budget
    d1 = _executor(env, run_id=None, session=None, max_tasks_per_stage=1)
    for _ in range(3):
        assert _task(d1, "gaps").ok  # D1 calls have no budget


def test_run_task_events_link_task_to_stage(env):
    _, _, ps, _ = env
    r = _task(_executor(env, run_id="run_x", session=1), "landscape")
    rows = [tuple(x) for x in ps.db.execute("SELECT task_id, run_id, session, stage, attempt FROM run_tasks")]
    assert rows == [(r.task_id, "run_x", 1, "landscape", 1)]
    ev = next(e for e in ps.log.iter() if e.type == "run.task")
    assert ev.entity_id == r.task_id and ev.payload == {"task_id": r.task_id, "run_id": "run_x", "session": 1,
                                                        "stage": "landscape", "attempt": 1}
    rebuild(ps.db)
    assert [tuple(x) for x in ps.db.execute("SELECT task_id, run_id, session, stage, attempt FROM run_tasks")] == rows


def test_rate_capped_task_raises_with_wait_and_is_not_budgeted(env):
    _, _, ps, _ = env
    save_settings(load_settings().model_copy(update={"calls_per_hour": {"claude_cli": 1}}))
    ex = _executor(env, run_id="run_r", session=1)
    assert _task(ex, "gaps").ok
    with pytest.raises(RateCapped) as e:
        _task(ex, "gaps")
    assert e.value.driver == "claude_cli"
    assert WINDOW_S - 60 < e.value.wait_s <= WINDOW_S
    assert tasks_used(ps, "run_r", 1, "gaps") == 1  # the capped task made no call


def test_sources_order_without_s2_key(env):
    k, _, _, _ = env
    assert KernelSources(k, "demo").sources() == ["openalex", "europepmc", "pubmed", "arxiv"]
    save_settings(load_settings().model_copy(update={"s2_key": "s2-test"}))
    assert KernelSources(k, "demo").sources()[:3] == ["openalex", "s2", "europepmc"]


SENTENCES = ["Background matters here.", "Many trials were small.", "Aspirin lowered the stroke rate in older adults.",
             "Bleeding was more common.", "The effect faded after five years.", "Costs were not reported.",
             "Further work is needed."]


def test_top_passages_are_verbatim(env):
    _, _, ps, lib = env
    iid = lib.add_item(ItemIn(title="A trial"), added_by="agent", actor="t").item_id
    page2 = "  ".join(SENTENCES) + "\n\nLimitations\nThe sample was\nsmall!  Follow-up was short?"
    add_text_layer(ps, iid, ["Title page. Nothing about the topic.", page2])
    query = "aspirin lowered the stroke rate in older adults"
    got = top_passages(ps, iid, query, k=5)
    assert 1 <= len(got) <= 5
    pages = {1: "Title page. Nothing about the topic.", 2: page2}
    for p in got:
        assert pages[p.page][p.start:p.end] == p.text
    assert SENTENCES[2] in got[0].text and got[0].page == 2
    spans = sorted((p.page, p.start, p.end) for p in got)
    assert all(a[0] != b[0] or a[2] <= b[1] for a, b in itertools.pairwise(spans))  # no overlap
    assert top_passages(ps, lib.add_item(ItemIn(title="No text"), added_by="agent", actor="t").item_id, query) == []


def test_top_passages_ignore_case_and_punctuation(env):
    _, _, ps, lib = env
    iid = lib.add_item(ItemIn(title="Lipids"), added_by="agent", actor="t").item_id
    # Without case/punctuation folding the partial match on page 1 scores 63.6 and the verbatim one on page 2 only 59.2.
    add_text_layer(ps, iid, ["Statins reduce mortality. Costs were not reported, overall.",
                             "statins reduce ldl cholesterol, as shown."])
    best = top_passages(ps, iid, "Statins reduce LDL cholesterol", k=1)
    assert [(p.page, p.text) for p in best] == [(2, "statins reduce ldl cholesterol, as shown.")]


def test_log_and_search_logs_and_adds(env):
    _, _, ps, lib = env
    hits = [ItemIn(title="Aspirin and stroke", year=2020, ids=Ids(doi="10.1/a")),
            ItemIn(title="Aspirin in the elderly", year=2021, ids=Ids(doi="10.1/b"))]
    fake = FakeSources({"aspirin": hits, "statin": [ItemIn(title="Statins", year=2019)]}, lib=lib)
    ids = log_and_search(ps, fake, stage="landscape", attempt=1, purpose="landscape", query="Aspirin stroke",
                         source="openalex", limit=25, actor="kernel")
    assert [lib.get(i).title for i in ids] == ["Aspirin and stroke", "Aspirin in the elderly"]
    col = lib.collection_by_name("Search hits")
    assert all(col in lib.get(i).collection_ids for i in ids)
    rows = searches(ps, "landscape", 1)
    assert [(r["query"], r["source"], r["limit_n"], r["purpose"], r["item_ids"]) for r in rows] == \
        [("Aspirin stroke", "openalex", 25, "landscape", ids)]
    assert fake.calls == [("search", "Aspirin stroke", "openalex", 25)]


def _mock_kernel(tmp_home, tmp_path, handler):
    http = HttpClient(tmp_path / "mock-http.sqlite", user_agent="rk-test", sleep=lambda s: None,
                      transport=httpx.MockTransport(handler))
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=http)
    k.create_project("demo", "Demo", actor="t")
    return k


def test_kernel_sources_year_counts_and_cited_by_after_year(tmp_home, tmp_path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if params.get("group_by") == "publication_year":
            return httpx.Response(200, json={"group_by": [{"key": "2021", "count": 7}, {"key": "2020", "count": 3},
                                                          {"key": "unknown", "count": 1}]})
        return httpx.Response(200, json={"results": [{"id": "https://openalex.org/W9", "title": "Citing work",
                                                      "publication_year": 2022}]})

    k = _mock_kernel(tmp_home, tmp_path, handler)
    try:
        _, lib = k.project("demo")
        src = KernelSources(k, "demo")
        assert src.year_counts("aspirin stroke") == {2020: 3, 2021: 7}
        assert seen[-1]["search"] == "aspirin stroke"
        iid = lib.add_item(ItemIn(title="Retracted", ids=Ids(openalex="W1")), added_by="agent", actor="t").item_id
        got = src.cited_by(iid, after_year=2019)
        assert [i.title for i in got] == ["Citing work"]
        assert seen[-1]["filter"] == "cites:W1,publication_year:>2019"
        src.cited_by(iid, after_year=None)
        assert seen[-1]["filter"] == "cites:W1"
        no_ids = lib.add_item(ItemIn(title="Unknown work"), added_by="agent", actor="t").item_id
        n = len(seen)
        assert src.cited_by(no_ids, after_year=None) == [] and len(seen) == n  # nothing to look up
    finally:
        k.close()


class FakeBrowser:
    def __init__(self, ps, *, fail=False, running=True):
        self.ps, self.fail, self.running, self.calls = ps, fail, running, []

    def fetch_fulltext(self, ctx, project, item_id):
        self.calls.append((ctx.role, project, item_id))
        if self.fail:
            raise ToolError("pdf_not_obtained", "no PDF link on the article page", status=404)
        return add_text_layer(self.ps, item_id, ["Browser text."])


def test_kernel_sources_fulltext_falls_back_to_the_browser(env):
    k, _, ps, lib = env
    iid = lib.add_item(ItemIn(title="Paywalled"), added_by="agent", actor="t").item_id  # no API channel applies
    assert KernelSources(k, "demo").fulltext(iid) is False
    off = FakeBrowser(ps, running=False)
    assert KernelSources(k, "demo", browser=off).fulltext(iid) is False and off.calls == []
    failing = FakeBrowser(ps, fail=True)
    assert KernelSources(k, "demo", browser=lambda: failing).fulltext(iid) is False and len(failing.calls) == 1
    ok = FakeBrowser(ps)
    assert KernelSources(k, "demo", browser=ok).fulltext(iid) is True and ok.calls == [("kernel", "demo", iid)]
    assert latest_snapshot(ps, iid) is not None
    again = FakeBrowser(ps)
    assert KernelSources(k, "demo", browser=again).fulltext(iid) is True and again.calls == []  # already has text


def test_scripted_executor_pops_outputs_and_records_calls():
    ex = ScriptedExecutor({("judge", "Found"): [{"found": True}, {"found": False}],
                           ("judge_2", "Found"): [{"found": True}]},
                          drivers={"judge_2": "ollama:deepseek-v4.1-flash:cloud"})
    r1 = ex.run("judge", "do", {"x": 1}, Found, stage="question", attempt=2)
    r2 = ex.run("judge", "do", {"x": 2}, Found, stage="question", attempt=2)
    assert (r1.ok, r1.output, r1.provider, r1.model) == (True, Found(found=True), "codex_cli", None)
    assert r2.output == Found(found=False) and json.loads(r2.raw) == {"found": False}
    r3 = ex.run("judge_2", "do", {}, Found, stage="question", attempt=2)
    assert (r3.provider, r3.model) == ("ollama", "deepseek-v4.1-flash:cloud")
    v1, v3 = vendor_of(r1.provider, r1.model), vendor_of(r3.provider, r3.model)
    assert v1 is not None and v3 is not None and v1 != v3
    assert [(c["role"], c["schema"], c["inputs"], c["stage"], c["attempt"]) for c in ex.calls[:2]] == \
        [("judge", "Found", {"x": 1}, "question", 2), ("judge", "Found", {"x": 2}, "question", 2)]
    with pytest.raises(AssertionError, match="judge"):
        ex.run("judge", "do", {}, Found, stage="question", attempt=2)


def test_call_caps_wait_is_the_time_until_the_oldest_call_leaves_the_window(tmp_path):
    now = [1000.0]
    caps = CallCaps(tmp_path / "caps.sqlite", clock=lambda: now[0])
    assert caps.wait_s("codex_cli") == 0.0
    caps.acquire("codex_cli", 2)
    now[0] = 1100.0
    caps.acquire("codex_cli", 2)
    now[0] = 1500.0
    assert caps.wait_s("codex_cli") == 1000.0 + WINDOW_S - 1500.0
    now[0] = 1000.0 + WINDOW_S + 50.0  # the first call has left the window
    assert caps.wait_s("codex_cli") == 1100.0 + WINDOW_S - now[0]
    caps.close()


def test_fake_sources_presets_by_title(env):
    _, _, ps, lib = env
    a = lib.add_item(ItemIn(title="Retracted trial", year=2015), added_by="agent", actor="t").item_id
    b = lib.add_item(ItemIn(title="Other"), added_by="agent", actor="t").item_id
    fake = FakeSources({}, lib=lib, statuses={"Retracted trial": "mismatch"}, s2=True,
                       retractions={"Retracted trial": RetractionInfo(status="retracted", sources=["openalex"])},
                       citing={"Retracted trial": [ItemIn(title="Old", year=2016), ItemIn(title="New", year=2021)]},
                       fulltexts={"Retracted trial": ["Page one.", "Page two."]}, year_counts={"q": {2020: 2}})
    assert fake.sources()[:2] == ["openalex", "s2"] and fake.year_counts("q") == {2020: 2}
    assert (fake.verify(a), fake.verify(b)) == ("mismatch", "pass")
    assert (lib.get(a).ref_status, lib.get(b).ref_status) == ("mismatch", "pass")
    assert fake.retraction(a).status == "retracted" and fake.retraction(b).status == "none"
    assert [i.title for i in fake.cited_by(a, after_year=2018)] == ["New"] and fake.cited_by(b, None) == []
    assert fake.fulltext(a) is True and fake.fulltext(b) is False
    assert top_passages(ps, a, "page two")[0].page == 2
