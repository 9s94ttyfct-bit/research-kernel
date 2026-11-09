"""Plan 5c Task 7: the production CLI. ``rk draft show`` (each sentence with its stored verdict), ``rk revise show``
(the reader's questions and how each was handled), ``rk audit show`` (the gates with their failures) and ``rk export``
(the export stage through a run in ``rk serve``, refused as the worker refuses it; ``--open`` opens the PDF only on a
terminal, through an opener the tests replace)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from typer.testing import CliRunner

from rk.cli import main as cli_main
from rk.cli.main import app
from rk.orchestrator.engine import Orchestrator
from rk.orchestrator.state import record_reopened, stages_of
from rk.production import export as export_mod
from rk.production.argue import main_claim
from rk.production.audit import AuditWorker
from rk.production.draft import DraftWorker
from rk.production.export import ExportWorker
from rk.production.revise import ReviseWorker
from tests.discovery_fakes import FakeWorker
from tests.production_fakes import make_frozen_project, production_ctx
from tests.test_audit import _ctx as audit_ctx
from tests.test_audit import _judges, _passed
from tests.test_draft import _ready, writes
from tests.test_export import _fake_render, _pool
from tests.test_revise import _exec as revise_exec
from tests.test_revise import _restated, ask, revision
from tests.test_synthesis import Exec
from tests.test_ui_argument import _pass_before


def _audited_with_a_question(env) -> None:
    """P1: every section drafted (draft passed, results citing the pooled RR and its forest plot); revise answered a
    priority-1 question on the introduction (round 1) and asked nothing more (round 2); the audit passed, approved by
    the vote; every stage before export passed."""
    k, ps, lib = env
    _ready(env)
    stt, fig = _pool(env)
    extra = f"The pooled risk ratio and its forest plot are shown [[stat:{stt}]] [[fig:{fig}]]."
    ex = Exec({("writer", "SectionDraft"): [writes(extra=extra)] * 10})
    out = DraftWorker().run(replace(production_ctx(k, ps, lib, ex, stage="draft"), guidance=""))
    assert out.status == "done", out.reason
    _passed(ps, "draft")
    ex = revise_exec([ask(("introduction", 1)), ask()], [revision(add=_restated(ps, main_claim(ps, 1)))])
    out = ReviseWorker().run(replace(production_ctx(k, ps, lib, ex, stage="revise"), guidance=""))
    assert out.status == "done", out.reason
    _passed(ps, "revise")
    out = AuditWorker().run(audit_ctx(env, _judges("approve")))
    assert out.status == "done", out.reason
    _passed(ps, "audit")
    _pass_before(ps, "export")


@pytest.fixture
def cli(serve_rk, tmp_home, replay_http, monkeypatch):
    """``rk serve`` over the project ``demo`` audited and approved (``_audited_with_a_question``), whose runs drive the
    real export worker (pandoc stood in for by ``_fake_render``), and the project ``other`` with nothing drafted."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review", production_mode="auto")
    _audited_with_a_question((k, ps, lib))
    k.close()
    monkeypatch.setattr(export_mod, "_render", _fake_render)
    opened: list = []
    monkeypatch.setattr(cli_main, "_open_file", opened.append, raising=False)  # tests never open anything

    def orchestrator(kernel, registry):
        ps, _ = kernel.project("demo")
        fakes = {s: FakeWorker() for s in stages_of(ps) if s not in ("intake", "export")}
        return Orchestrator(kernel, registry, workers={**fakes, "export": ExportWorker()})

    client = serve_rk(orchestrator=orchestrator)
    client.call("rk_project_create", {"slug": "other", "title": "Other"}, write=True)
    client.opened = opened
    return client


def _run(*args: str):
    return CliRunner().invoke(app, list(args))


def test_draft_show_prints_each_sentence_with_its_verdict(cli):
    r = _run("draft", "show", "--project", "demo")
    assert r.exit_code == 0, r.output
    for text in ("introduction v2 (revise, round 1): verified", "results v1 (draft): verified",
                 "disclosure v", "[ok]", "In other words,"):
        assert text in r.output, text
    r = _run("draft", "show", "--project", "demo", "--section", "introduction", "--version", "1")
    assert r.exit_code == 0, r.output
    assert "introduction v1 (draft): verified" in r.output and "results v" not in r.output
    assert "In other words," not in r.output
    r = _run("draft", "show", "--project", "demo", "--section", "nope")
    assert r.exit_code == 1 and "nope" in r.output
    r = _run("draft", "show", "--project", "demo", "--json")
    assert r.exit_code == 0 and '"sentences"' in r.output and '"verdict"' not in r.output


def test_revise_show_prints_the_questions_and_how_each_was_handled(cli):
    r = _run("revise", "show", "--project", "demo")
    assert r.exit_code == 0, r.output
    for text in ("revise attempt 1", "a1r1q1", "priority 1", "introduction", "What does introduction mean here?",
                 "revised: clarified"):
        assert text in r.output, text
    r = _run("revise", "show", "--project", "other")
    assert r.exit_code == 0 and "revise has not run" in r.output


def test_audit_show_prints_the_gates_with_their_failures(cli):
    r = _run("audit", "show", "--project", "demo")
    assert r.exit_code == 0, r.output
    for text in ("Audit report v", "passed every hard gate", "final review approved", "G-COVER: pass",
                 "G-PREREG: pass", "export: ready"):
        assert text in r.output, text
    r = _run("audit", "show", "--project", "other")
    assert r.exit_code == 0 and "no audit report yet" in r.output and "audit has not passed" in r.output


def test_export_runs_the_export_stage_and_opens_the_pdf_only_on_a_terminal(cli, monkeypatch):
    r = _run("export", "--project", "other")
    assert r.exit_code == 1 and "audit has not passed" in r.output, r.output
    r = _run("export", "--project", "demo", "--open")
    assert r.exit_code == 0, r.output
    for name in ("paper.pdf", "paper.docx", "paper.md", "package-"):
        assert name in r.output, name
    assert "not a terminal" in r.output and cli.opened == []  # CliRunner's stdout is no TTY: nothing is opened
    ps, _ = cli.kernel.project("demo")
    assert (ps.root / "artifacts/export/paper.pdf").is_file()
    # exported again, on a terminal: the PDF is opened with the OS default app
    record_reopened(ps, "export", by_edge="test", guidance=None, actor="tester")
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: True)
    r = _run("export", "--project", "demo", "--open")
    assert r.exit_code == 0, r.output
    (path,) = cli.opened
    assert str(path).endswith("artifacts/export/paper.pdf") and (ps.root / "artifacts/export/paper.pdf").samefile(path)
