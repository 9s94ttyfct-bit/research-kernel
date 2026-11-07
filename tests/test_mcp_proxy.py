import json
import os
import sys

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from rk.discovery.store import rounds
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.notes import list_notes

from .conftest import DOCS

QUOTE = "about copper catalysts for CO2 reduction"


@pytest.fixture
def proj(live_server, tmp_home):
    live_server.call("rk_project_create", {"slug": "mcpp", "title": "MCP"}, write=True)
    k = live_server.kernel
    ps, lib = k.project("mcpp")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    return k, ps, iid, tmp_home


def _session(home, token, fn):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "rk.cli.main", "mcp", "--project", "mcpp", "--role", "reader"],
        env={**os.environ, "RK_HOME": str(home), "RK_TASK_TOKEN": token})

    async def main():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)

    return anyio.run(main)


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_mcp_lists_only_granted_tools(proj):
    k, _, _, home = proj
    tok = k.tokens.issue_task("tsk_m1", "reader", "mcpp", ["rk_text_find", "rk_note_add"], ttl_s=60)

    async def fn(s):
        return await s.list_tools()

    tools = _session(home, tok, fn).tools
    assert {t.name for t in tools} == {"rk_text_find", "rk_note_add"}
    assert all("project" not in t.input_schema.get("properties", {}) for t in tools)


def test_mcp_call_roundtrip(proj):
    k, _, iid, home = proj
    tok = k.tokens.issue_task("tsk_m2", "reader", "mcpp", ["rk_text_find"], ttl_s=60)

    async def fn(s):
        return await s.call_tool("rk_text_find", {"source_id": iid, "query": "copper catalysts"})

    r = _session(home, tok, fn)
    assert not r.is_error and _payload(r)["matches"][0]["page"] == 2


def test_mcp_repeated_write_is_idempotent(proj):
    k, ps, iid, home = proj
    tok = k.tokens.issue_task("tsk_m3", "reader", "mcpp", ["rk_note_add"], ttl_s=60)
    note = {"note": {"source_id": iid, "kind": "quote", "text": QUOTE, "role_in_source": "main"}}

    async def fn(s):
        return [await s.call_tool("rk_note_add", note), await s.call_tool("rk_note_add", note)]

    a, b = _session(home, tok, fn)
    assert _payload(a)["note_id"] == _payload(b)["note_id"]
    assert len(list_notes(ps)) == 1


@pytest.mark.parametrize("grant", ["chat", "lead_token"])
def test_mcp_lead_grant_repeated_write_runs_each_time(proj, grant):
    k, ps, _, home = proj
    # a claude_cli Lead chat holds a task-kind grant with role lead; an external MCP client holds a lead token. The
    # proxy is started with --role reader: the server decides by the grant, not by that label.
    tok = (k.tokens.issue_task("chat_m6", "lead", "mcpp", ["rk_exploration_round"], ttl_s=60) if grant == "chat"
           else k.tokens.issue_lead("mcp-lead"))
    feedback = {"stage": "landscape", "feedback": "Adults only."}

    async def fn(s):
        return [await s.call_tool("rk_exploration_round", feedback),
                await s.call_tool("rk_exploration_round", feedback)]

    a, b = _session(home, tok, fn)
    assert not a.is_error and not b.is_error
    assert _payload(a)["round_id"] != _payload(b)["round_id"]
    assert [r["feedback"] for r in rounds(ps)] == ["Adults only.", "Adults only."]


def test_mcp_forbidden_and_rejected_calls_are_errors(proj):
    k, _, iid, home = proj
    tok = k.tokens.issue_task("tsk_m4", "reader", "mcpp", ["rk_note_add"], ttl_s=60)

    async def fn(s):
        forbidden = await s.call_tool("rk_library_attach", {"source_id": iid, "path": "/etc/hosts"})
        misquote = await s.call_tool("rk_note_add", {"note": {"source_id": iid, "kind": "quote",
                                                              "text": "about nickel catalysts", "role_in_source": "main"}})
        return forbidden, misquote

    forbidden, misquote = _session(home, tok, fn)
    assert forbidden.is_error and _payload(forbidden)["error"] == "permission_denied"
    assert misquote.is_error and _payload(misquote)["details"]["reason"] == "quote_not_found"


def test_mcp_expired_token_errors_cleanly(proj):
    k, _, iid, home = proj
    tok = k.tokens.issue_task("tsk_m5", "reader", "mcpp", ["rk_text_find"], ttl_s=60)
    k.tokens.revoke("tsk_m5")

    async def fn(s):
        return await s.call_tool("rk_text_find", {"source_id": iid, "query": "copper"})

    r = _session(home, tok, fn)
    assert r.is_error and _payload(r)["error"] == "http_401"
