import shutil
import uuid

import pytest
from pydantic import BaseModel

from rk.config import Settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.runner import run_task, task_row
from rk.llm.types import Availability, TaskResult
from rk.notes import list_notes
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.workspace import Kernel

from .conftest import DOCS


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
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", omp_gateway=True))
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
