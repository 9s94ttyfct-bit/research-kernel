import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from rk.checkpoints import (
    HARD_HUMAN_KINDS,
    answer_checkpoint,
    create_checkpoint,
    list_checkpoints,
)
from rk.cli.main import app
from rk.config import Settings, save_settings
from rk.llm.human import HumanProvider
from rk.llm.types import RunContext, TaskSpec
from rk.roles.policy import ROLES
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, PermissionDenied
from rk.workspace import Kernel


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    ps = k.create_project("cp", "CP", actor="t")
    yield k, reg, ps
    k.close()


def test_answer_tool_is_cli_only(env):
    _, reg, ps = env
    chk = create_checkpoint(ps, "final_review", "Approve?", ["yes", "no"], actor="kernel")
    for role in ROLES:
        with pytest.raises(PermissionDenied):
            reg.call("rk_checkpoint_answer", {"project": "cp", "checkpoint_id": chk, "answer": {"choice": "yes"}},
                     CallContext(role=role, actor="agent", idempotency_key=f"a-{role}"))
    assert list_checkpoints(ps, "open")[0]["id"] == chk
    out = reg.call("rk_checkpoint_answer", {"project": "cp", "checkpoint_id": chk, "answer": {"choice": "yes"}},
                   CallContext(role="cli", actor="user", idempotency_key="h1"))
    assert out["checkpoint"]["status"] == "answered" and out["checkpoint"]["answered_by"] == "human"
    lead = reg.call("rk_checkpoint_list", {"project": "cp"}, CallContext(role="lead", actor="lead"))
    assert lead["checkpoints"][0]["answer"] == {"choice": "yes"}


def test_vote_cannot_answer_hard_checkpoint(env):
    _, _, ps = env
    for kind in sorted(HARD_HUMAN_KINDS):
        chk = create_checkpoint(ps, kind, "q", actor="kernel")
        with pytest.raises(ValueError, match="only be answered by a human"):
            answer_checkpoint(ps, chk, {"x": 1}, answered_by="vote", actor="kernel")
    soft = create_checkpoint(ps, "task_input", "q", actor="kernel")
    answer_checkpoint(ps, soft, {"x": 1}, answered_by="vote", actor="kernel")
    with pytest.raises(ValueError, match="not open"):
        answer_checkpoint(ps, soft, {"x": 2}, answered_by="human", actor="u")


def test_human_driver_creates_checkpoint(env):
    k, reg, ps = env

    class O(BaseModel):
        decision: str

    r = HumanProvider().run(TaskSpec(task_id="tsk_H", role="screener_a", project="cp", instructions="Include?",
                                     inputs={"title": "A study"}, output_schema=O, tools=[]),
                            RunContext(kernel=k, registry=reg))
    assert not r.ok and r.error == "waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    assert cp["id"] == r.session_id and cp["task_id"] == "tsk_H" and "A study" in cp["prompt"]
    assert cp["options"]["output_schema"]["properties"]["decision"]


def test_checkpoints_rebuild(env):
    _, _, ps = env
    a = create_checkpoint(ps, "final_review", "q1", actor="kernel")
    create_checkpoint(ps, "topic_review", "q2", actor="kernel")
    answer_checkpoint(ps, a, {"ok": True}, answered_by="human", actor="u")
    before = list_checkpoints(ps)
    rebuild(ps.db)
    assert list_checkpoints(ps) == before


def test_cli_answer_requires_confirmation(live_server, monkeypatch):
    live_server.call("rk_project_create", {"slug": "cpc", "title": "C"}, write=True)
    ps, _ = live_server.kernel.project("cpc")
    chk = create_checkpoint(ps, "final_review", "Ship it?", actor="kernel")
    runner = CliRunner()
    monkeypatch.setenv("RK_ALLOW_NONINTERACTIVE_ANSWER", "1")  # CliRunner stdin is not a TTY
    r = runner.invoke(app, ["checkpoint", "answer", chk, "--project", "cpc", "--json", '{"ship": true}'], input="n\n")
    assert r.exit_code == 1 and "not answered" in r.output
    assert list_checkpoints(ps, "open")
    r = runner.invoke(app, ["checkpoint", "answer", chk, "--project", "cpc", "--json", '{"ship": true}'], input="y\n")
    assert r.exit_code == 0, r.output
    assert list_checkpoints(ps)[0]["answer"] == {"ship": True}


def test_cli_answer_refuses_non_tty_without_override(live_server, monkeypatch):
    live_server.call("rk_project_create", {"slug": "cpn", "title": "N"}, write=True)
    ps, _ = live_server.kernel.project("cpn")
    chk = create_checkpoint(ps, "final_review", "Ship it?", actor="kernel")
    monkeypatch.delenv("RK_ALLOW_NONINTERACTIVE_ANSWER", raising=False)
    r = CliRunner().invoke(app, ["checkpoint", "answer", chk, "--project", "cpn", "--json", "{}", "--yes"])
    assert r.exit_code == 2 and list_checkpoints(ps, "open")
