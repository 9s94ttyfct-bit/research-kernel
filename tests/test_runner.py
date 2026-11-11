import os
import shutil
import stat
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from rk.config import Settings, load_settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm import runner
from rk.llm.chat.base import ToolCall, Turn
from rk.llm.chat_provider import ChatApiProvider
from rk.llm.drivers import DriverSpec
from rk.llm.limits import MIN_WAIT_S, SESSION_LIMITED
from rk.llm.runner import interrupt_orphaned_tasks, run_task, task_row
from rk.llm.types import Availability, TaskResult
from rk.net.http import BudgetExhausted
from rk.notes import list_notes
from rk.server.app import create_app
from rk.store.projections import rebuild
from rk.tools import builtin
from rk.tools.builtin import build_registry
from rk.tools.registry import Tool
from rk.workspace import Kernel

from .conftest import DOCS
from .test_agent_loop import ScriptedBackend


class Found(BaseModel):
    found: bool


class FakeProvider:
    name = "fake"
    model = "fake-1"
    vendor = "openai"

    def __init__(self, results, kind="chat_api"):
        self.results = list(results)
        self.kind = kind
        self.calls = []

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls.append((spec, ctx))
        ok, error = self.results.pop(0)
        return TaskResult(spec.task_id, ok, Found(found=True) if ok else None, "raw-text", "fake", "fake-1", 0.1,
                          error, tool_calls=2)


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com",
                           calls_per_hour={"claude_cli": 30, "codex_cli": 2}, openai_api_key="sk-test-123"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    ps = k.create_project("rt", "Runner", actor="t")
    yield k, reg, ps
    k.close()


def _run(env, provider, **kw):
    k, reg, _ = env
    return run_task(k, reg, project="rt", role=kw.pop("role", "reader"), instructions="do", output_schema=Found,
                    provider=provider, driver=kw.pop("driver", DriverSpec("claude_cli")), **kw)


def test_task_events_and_projection(env):
    _, _, ps = env
    r = _run(env, FakeProvider([(True, None)]))
    row = task_row(ps, r.task_id)
    assert (row["status"], row["attempts"], row["output"], row["role"], row["driver"]) == \
        ("done", 1, {"found": True}, "reader", "claude_cli")
    types = [e.type for e in ps.log.iter() if e.entity_id == r.task_id]
    assert types == ["task.created", "task.attempt", "task.finished"]
    created = next(e for e in ps.log.iter() if e.type == "task.created")
    tools = set(created.payload["tools"])
    assert {"rk_note_add", "rk_text_find", "rk_source_fetch_fulltext", "rk_flag_injection"} <= tools
    assert not tools & {"rk_get_work", "rk_search_works", "rk_library_attach"}  # not project-scoped / not granted
    rebuild(ps.db)
    assert task_row(ps, r.task_id) == row


def test_retries_up_to_three(env):
    _, _, ps = env
    p = FakeProvider([(False, "invalid_output")] * 3)
    r = _run(env, p)
    assert not r.ok and len(p.calls) == 3 and task_row(ps, r.task_id)["status"] == "failed"
    p2 = FakeProvider([(False, "provider_error: x"), (True, None)])
    assert _run(env, p2).ok and len(p2.calls) == 2


def test_waiting_human_not_retried(env):
    _, _, ps = env
    p = FakeProvider([(False, "waiting_human")])
    r = _run(env, p)
    assert len(p.calls) == 1 and task_row(ps, r.task_id)["status"] == "waiting_human"


CAPACITY = ("provider_error: omp auth-gateway status 502: {'error': {'message': 'Codex error event: Codex is at "
            "capacity, please try again later (code=turn_rate_limit_exceeded) retry-after-ms=21000', "
            "'type': 'upstream_error'}}")


def test_a_gateways_retry_after_is_waited_out_before_the_next_attempt(env, monkeypatch):
    """Calibration (critical review rerun): the omp gateway passes Codex's transient rate limit on as a 502 with a
    retry-after-ms; each judge task tried three times within 0.75 s and failed, so claims fell to uncertain."""
    k, _, _ = env
    slept = []
    monkeypatch.setattr(runner, "_sleep", slept.append)
    p = FakeProvider([(False, CAPACITY), (True, None)])
    assert _run(env, p).ok and len(p.calls) == 2 and slept == [21.0]
    assert k.caps.blocks() == {}  # a transient limit blocks no driver
    # still at capacity on the last attempt: a usage limit, so the run waits instead of the task failing
    p = FakeProvider([(False, CAPACITY)] * 3)
    r = _run(env, p)
    assert (r.error, r.wait_s, len(p.calls), slept) == (SESSION_LIMITED, MIN_WAIT_S, 3, [21.0] * 3)
    assert set(k.caps.blocks()) == {"claude_cli"}
    k.caps.clear("claude_cli")
    # asked to wait longer than a task waits out: a usage limit at once
    p = FakeProvider([(False, "provider_error: omp auth-gateway status 502: retry-after-ms=300000")])
    r = _run(env, p)
    assert (r.error, r.wait_s, len(p.calls), slept) == (SESSION_LIMITED, 300, 1, [21.0] * 3)

def test_task_ends_the_gateway_it_started(env, tmp_path, monkeypatch):
    """A task's own omp_gateway driver ends its `omp auth-gateway` child when the task ends (rk serve runs thousands
    of tasks: a child left behind per task piles up)."""
    k, reg, _ = env
    exe = tmp_path / "omp"
    exe.write_text(f"#!{sys.executable}\n" + (Path(__file__).parent / "fakes" / "fake_omp.py").read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    pids = tmp_path / "pids"
    monkeypatch.setenv("FAKE_OMP_PIDS", str(pids))
    save_settings(load_settings().model_copy(update={"cli_paths": {"omp": str(exe)}}))
    run_task(k, reg, project="rt", role="judge", instructions="do", tools=[],
             driver=DriverSpec.parse("omp_gateway:openai-codex/gpt-6-luna"))
    started = [int(p) for p in pids.read_text().split()]
    assert started
    for pid in started:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_rate_cap_blocks_and_records(env):
    _, _, ps = env
    for _ in range(2):
        assert _run(env, FakeProvider([(True, None)]), driver=DriverSpec("codex_cli")).ok
    p = FakeProvider([(True, None)])
    r = _run(env, p, driver=DriverSpec("codex_cli"))
    assert r.error == "rate_capped" and p.calls == [] and task_row(ps, r.task_id)["status"] == "failed"
    assert _run(env, FakeProvider([(True, None)]), driver=DriverSpec("claude_cli")).ok  # other drivers unaffected


def test_token_scoped_and_revoked_after_task(env):
    k, _, _ = env
    p = FakeProvider([(True, None)], kind="agentic_cli")
    r = _run(env, p)
    token = p.calls[0][1].task_token
    assert token and k.tokens.lookup(token) is None
    spec = p.calls[0][0]
    assert spec.project == "rt" and "rk_project_list" not in spec.tools  # only project-scoped tools
    assert r.ok


def test_event_payload_has_no_api_key(env, tmp_home):
    _run(env, FakeProvider([(True, None)]))
    raw = b"".join(p.read_bytes() for p in (tmp_home / "projects" / "rt").glob("rk.sqlite*"))
    assert b"sk-test-123" not in raw


def test_driver_exception_is_recorded_not_raised(env):
    _, _, ps = env

    class Boom(FakeProvider):
        def run(self, spec, ctx):
            raise RuntimeError("driver bug")

    r = _run(env, Boom([]))
    assert not r.ok and "driver bug" in r.error and task_row(ps, r.task_id)["attempts"] == 3


def test_a_task_cut_off_by_a_restart_is_marked_interrupted(env, tmp_home, replay_http):
    """Spec §11.6: a process killed during a provider call writes no ``task.finished``; on restart the sweep marks the
    task left ``running`` as ``interrupted`` (an event, so a rebuild keeps it) and leaves finished tasks alone."""
    k, _, ps = env
    done = _run(env, FakeProvider([(True, None)]))

    class Killed(FakeProvider):
        def run(self, spec, ctx):
            self.calls.append((spec, ctx))
            raise SystemExit(1)  # like a kill: gets past ``except Exception``

    p = Killed([])
    with pytest.raises(SystemExit):
        _run(env, p)
    orphan = p.calls[0][0].task_id
    assert task_row(ps, orphan)["status"] == "running"
    k.close()

    k2 = Kernel(tmp_home, http=replay_http)
    try:
        ps2, _ = k2.project("rt")
        assert interrupt_orphaned_tasks(ps2) == [orphan]
        row = task_row(ps2, orphan)
        assert (row["status"], row["output"], row["attempts"]) == ("interrupted", None, 0)
        assert row["finished"] and row["error"] == "interrupted: rk serve stopped before the task returned"
        assert [e.type for e in ps2.log.iter() if e.entity_id == orphan] == ["task.created", "task.finished"]
        assert task_row(ps2, done.task_id)["status"] == "done"
        assert interrupt_orphaned_tasks(ps2) == []
        rebuild(ps2.db)
        assert task_row(ps2, orphan) == row
    finally:
        k2.close()


class Spend(BaseModel):
    project: str


def _spend_budget(reg, monkeypatch, how: str) -> str:
    """A tool whose source budget runs out: ``rk_search_works`` (it wraps the error in a ToolError) or a tool that lets
    ``BudgetExhausted`` escape raw. Returns the tool's name."""
    if how == "wrapped":
        def search(*a, **kw):
            raise BudgetExhausted("openalex", 1_900_000_000.0)
        monkeypatch.setattr(builtin, "search", search)
        return "rk_search_works"

    def spend(ctx, a):
        raise BudgetExhausted("openalex", 1_900_000_000.0)
    reg.register(Tool("rk_test_spend", "spends the budget", Spend, BaseModel, frozenset({"scout"}), False, spend))
    return "rk_test_spend"


@pytest.mark.parametrize("how", ["wrapped", "raw"])
def test_a_tool_call_that_spends_a_source_budget_suspends_the_task(env, monkeypatch, how):
    """Spec §11.3: a source budget running out inside a role task's tool call suspends the task, never skips it: the
    model does not get the error as data, the attempt and the task fail naming the budget, nothing is retried and
    ``BudgetExhausted`` reaches the caller (the engine pauses the run on it)."""
    _, reg, ps = env
    name = _spend_budget(reg, monkeypatch, how)
    args = {"query": "aspirin"} if name == "rk_search_works" else {}
    turns = [Turn(tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)]) if i % 2 == 0
             else Turn(text='{"found": true}') for i in range(6)]
    backend = ScriptedBackend(turns)
    with pytest.raises(BudgetExhausted) as e:
        _run(env, ChatApiProvider(backend, "scripted"), role="scout", tools=[name])
    assert e.value.budget_key == "openalex"
    assert len(backend.seen) == 1  # the model was never shown the tool result, and no attempt was retried
    (task_id,) = [r["id"] for r in ps.db.execute("SELECT id FROM tasks")]
    row = task_row(ps, task_id)
    assert (row["status"], row["attempts"], row["error"]) == ("failed", 1, "budget_exhausted: openalex")
    (attempt,) = [ev.payload for ev in ps.log.iter() if ev.type == "task.attempt"]
    assert attempt["ok"] is False and attempt["error"] == "budget_exhausted: openalex"


def test_an_agentic_cli_task_whose_tool_call_spent_a_source_budget_is_suspended(env, monkeypatch):
    """An agentic CLI calls tools through ``rk serve`` with its task token and only sees an error; ``run_task`` still
    learns of the spent budget when the CLI returns: the task fails naming it, is not retried, and raises. Once the
    budget is spent the task's later tool calls are refused, so the suspended task stores nothing more."""
    k, reg, ps = env
    _spend_budget(reg, monkeypatch, "wrapped")
    client = TestClient(create_app(reg, "serve-tok", tokens=k.tokens))
    replies = []

    class Cli(FakeProvider):
        def run(self, spec, ctx):
            self.calls.append((spec, ctx))
            auth = {"Authorization": f"Bearer {ctx.task_token}"}
            for i, (tool, args) in enumerate([("rk_search_works", {"query": "aspirin"}), ("rk_library_list", {})]):
                replies.append(client.post(f"/api/tools/{tool}", headers=auth,
                                           json={"args": {**args, "project": "rt"}, "idempotency_key": f"k{i}"}))
            return TaskResult(spec.task_id, True, Found(found=True), "carried on", "fake", "fake-1", 0.1, None)

    p = Cli([], kind="agentic_cli")
    with pytest.raises(BudgetExhausted) as e:
        _run(env, p, role="scout")
    assert e.value.budget_key == "openalex" and len(p.calls) == 1
    search, later = (r.json() for r in replies)
    assert search["error"]["type"] == "BudgetExhausted"  # what the CLI saw: an error, no hits
    assert later["error"]["type"] == "budget_exhausted" and "suspended" in later["error"]["message"]
    row = task_row(ps, p.calls[0][0].task_id)
    assert (row["status"], row["attempts"], row["error"]) == ("failed", 1, "budget_exhausted: openalex")
    assert k.tokens.lookup(p.calls[0][1].task_token) is None


# ---------------- live demo: the same reader task on three drivers ----------------

class NoteOut(BaseModel):
    note_id: str


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.parametrize("driver", ["claude_cli", "codex_cli", "omp_gateway:openai-codex/gpt-6-luna"])
def test_same_reader_task_on_three_drivers(live_server, driver):
    binary = {"claude_cli": "claude", "codex_cli": "codex"}.get(driver, "omp")
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} not installed")
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    slug = f"rd-{uuid.uuid4().hex[:6]}"
    live_server.call("rk_project_create", {"slug": slug, "title": "Drivers"}, write=True)
    k = live_server.kernel
    ps, lib = k.project(slug)
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    r = live_server.call("rk_task_run", {
        "project": slug, "role": "reader", "driver": driver, "timeout_s": 400,
        "instructions": (f"Source {iid} contains a sentence about copper catalysts. Locate it with rk_text_find, read "
                         "it with rk_text_read, and save the clause about copper catalysts as a verbatim quote note "
                         "(kind 'quote', role_in_source 'main') with rk_note_add. Reply with the note id as text.")},
        write=True, timeout=1500)
    assert r["ok"], r
    notes = list_notes(ps)
    assert notes and all(n["verified"]["quote"]["ok"] for n in notes) and r["tool_calls"] >= 1
    assert task_row(ps, r["task_id"])["status"] == "done"
