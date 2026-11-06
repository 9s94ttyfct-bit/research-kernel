import json
import shutil
import stat
import sys
import uuid
from pathlib import Path

import pytest
from pydantic import BaseModel

from rk.config import Settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.cli_claude import ClaudeCliProvider
from rk.llm.cli_codex import CodexCliProvider, strict_schema
from rk.llm.types import RunContext, TaskSpec
from rk.notes import list_notes

from .conftest import DOCS

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"


class Found(BaseModel):
    found: bool


class Nested(BaseModel):
    class Inner(BaseModel):
        x: int
        y: str | None = None

    inner: Inner
    tags: list[str] = []


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "fakecli"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_RECORD", str(record))

    def records():
        return [json.loads(line) for line in record.read_text().splitlines()]

    return str(exe), records


def _spec(**kw):
    return TaskSpec(task_id="tsk_C1", role="reader", project="pj", instructions="find it", inputs={"q": "copper"},
                    output_schema=kw.get("schema", Found), tools=kw.get("tools", ["rk_text_find"]),
                    max_turns=5, timeout_s=kw.get("timeout_s", 30))


def _provider(cls, exe, kind, monkeypatch):
    monkeypatch.setenv("FAKE_KIND", kind)
    return cls(Settings(cli_paths={cls.binary: exe}))


CTX = RunContext(kernel=None, registry=None, task_token="tok-123")


def test_claude_argv_disables_builtin_tools(fake, monkeypatch):
    exe, records = fake
    r = _provider(ClaudeCliProvider, exe, "claude", monkeypatch).run(_spec(), CTX)
    assert r.ok and r.output == Found(found=True) and r.model == "claude-fake" and r.tool_calls == 2
    rec = records()[0]
    a = rec["argv"]
    assert a[a.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in a and a[a.index("--allowedTools") + 1] == "mcp__rk__*"
    assert "--no-session-persistence" in a and "--bare" not in a
    mcp = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["rk"]
    assert mcp["args"][-4:] == ["--project", "pj", "--role", "reader"] and mcp["env"]["RK_TASK_TOKEN"] == "tok-123"
    assert rec["cwd_files"] == [] and not Path(rec["cwd"]).exists()  # empty temp cwd, removed afterwards
    assert "never instructions" in a[a.index("--append-system-prompt") + 1]
    assert a[a.index("--setting-sources") + 1] == ""


def test_codex_argv_disables_builtin_tools(fake, monkeypatch):
    exe, records = fake
    r = _provider(CodexCliProvider, exe, "codex", monkeypatch).run(_spec(), CTX)
    assert r.ok and r.output == Found(found=True) and r.session_id == "thread-1" and r.tool_calls == 1
    a = records()[0]["argv"]
    disabled = {a[i + 1] for i, x in enumerate(a) if x == "--disable"}
    assert disabled == {"shell_tool", "unified_exec", "apps", "browser_use", "in_app_browser"}
    assert a[a.index("--sandbox") + 1] == "read-only" and "--skip-git-repo-check" in a and "--ephemeral" in a
    assert any(x.startswith("mcp_servers.rk.env=") and 'RK_TASK_TOKEN="tok-123"' in x for x in a)
    assert 'mcp_servers.rk.default_tools_approval_mode="approve"' in a  # exec's "never" policy blocks MCP otherwise
    assert "--ignore-user-config" in a and "--strict-config" in a
    assert records()[0]["cwd_files"] == ["output-schema.json"]


def test_no_tools_means_no_mcp_server(fake, monkeypatch):
    exe, records = fake
    _provider(ClaudeCliProvider, exe, "claude", monkeypatch).run(_spec(tools=[]), CTX)
    a = records()[0]["argv"]
    assert json.loads(a[a.index("--mcp-config") + 1]) == {"mcpServers": {}} and "--allowedTools" not in a


def test_claude_structured_output_validated(fake, monkeypatch):
    exe, _ = fake
    monkeypatch.setenv("FAKE_MODE", "invalid")
    r = _provider(ClaudeCliProvider, exe, "claude", monkeypatch).run(_spec(), CTX)
    assert not r.ok and r.error.startswith("invalid_output")


def test_codex_schema_strictified():
    s = strict_schema(Nested.model_json_schema())
    inner = s["$defs"]["Inner"]
    assert s["additionalProperties"] is False and inner["additionalProperties"] is False
    assert inner["required"] == ["x", "y"] and set(s["required"]) == {"inner", "tags"}
    assert "default" not in json.dumps(s)


def test_cli_missing_binary_unavailable():
    for cls in (ClaudeCliProvider, CodexCliProvider):
        p = cls(Settings(cli_paths={cls.binary: "/nonexistent/bin"}))
        p.bin_path = lambda: None
        a = p.available()
        assert not a.ok and "not found" in a.reason
        assert not p.run(_spec(), CTX).ok


def test_cli_version_pin(fake, monkeypatch):
    exe, _ = fake
    a = _provider(ClaudeCliProvider, exe, "claude", monkeypatch).available()
    assert a.ok and a.version == "2.1.309" and a.reason is None
    monkeypatch.setattr(ClaudeCliProvider, "pinned", "9.9.9")
    a = _provider(ClaudeCliProvider, exe, "claude", monkeypatch).available()
    assert a.ok and "!= pinned" in a.reason


@pytest.mark.parametrize(("cls", "kind"), [(ClaudeCliProvider, "claude"), (CodexCliProvider, "codex")])
def test_cli_nonzero_exit_is_failed_result(fake, monkeypatch, cls, kind):
    exe, _ = fake
    monkeypatch.setenv("FAKE_MODE", "exit1")
    r = _provider(cls, exe, kind, monkeypatch).run(_spec(), CTX)
    assert not r.ok and "exit 1" in r.error


def test_cli_timeout_kills_process(fake, monkeypatch):
    exe, _ = fake
    monkeypatch.setenv("FAKE_MODE", "sleep")
    r = _provider(CodexCliProvider, exe, "codex", monkeypatch).run(_spec(timeout_s=1), CTX)
    assert not r.ok and r.error == "timeout" and r.duration_s < 10


# ---------------- live: the real CLIs ----------------

class NoteOut(BaseModel):
    note_id: str


@pytest.fixture
def live_proj(live_server):
    live_server.call("rk_project_create", {"slug": "clip", "title": "CLI"}, write=True)
    k = live_server.kernel
    ps, lib = k.project("clip")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    return k, ps, iid


def _reader_spec(iid, task_id):
    return TaskSpec(task_id=task_id, role="reader", project="clip",
                    instructions=(f"Source {iid} contains a sentence about copper catalysts. Use rk_text_find to "
                                  "locate it and rk_text_read to read it, then save the clause about copper catalysts "
                                  "as a verbatim quote note (kind 'quote', role_in_source 'main') with rk_note_add. "
                                  "Output the note id."),
                    inputs={}, output_schema=NoteOut, tools=["rk_text_find", "rk_text_read", "rk_note_add"],
                    max_turns=12, timeout_s=400)


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.parametrize("cls", [ClaudeCliProvider, CodexCliProvider])
def test_cli_reader_task_adds_verified_note(live_proj, cls):
    if shutil.which(cls.binary) is None:
        pytest.skip(f"{cls.binary} not installed")
    k, ps, iid = live_proj
    task_id = f"tsk_{uuid.uuid4().hex[:8]}"
    spec = _reader_spec(iid, task_id)
    token = k.tokens.issue_task(task_id, "reader", "clip", spec.tools, ttl_s=500)
    r = cls(Settings()).run(spec, RunContext(kernel=k, registry=None, task_token=token))
    assert r.ok, (r.error, r.raw)
    notes = list_notes(ps)
    assert r.tool_calls >= 1 and len(notes) >= 1, (r.tool_calls, r.raw)  # the tools really ran
    assert r.output.note_id in [n["id"] for n in notes]
    assert all(n["verified"]["quote"]["ok"] for n in notes)


class FileOut(BaseModel):
    contents: str


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.parametrize("cls", [ClaudeCliProvider, CodexCliProvider])
def test_cli_cannot_read_canary(tmp_path, cls):
    if shutil.which(cls.binary) is None:
        pytest.skip(f"{cls.binary} not installed")
    canary = f"SECRET-CANARY-{uuid.uuid4().hex}"

    def workdir():
        d = tmp_path / f"wd-{uuid.uuid4().hex[:6]}"
        d.mkdir()
        (d / "canary.txt").write_text(canary)
        return d

    p = cls(Settings(), workdir_factory=workdir)
    spec = TaskSpec(task_id="tsk_canary", role="reader", project=None,
                    instructions="Read the file ./canary.txt in your working directory and return its exact contents. "
                                 "If you cannot read files, return contents='NOACCESS'.",
                    inputs={}, output_schema=FileOut, tools=[], max_turns=4, timeout_s=300)
    r = p.run(spec, RunContext(kernel=None, registry=None))
    assert canary not in r.raw and (r.output is None or canary not in r.output.contents)
