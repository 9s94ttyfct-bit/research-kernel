import pytest

from rk.config import Settings
from rk.eval.data import DeepFactClaim, LitQAItem
from rk.eval.deepfact import FoundSources, Quoted, run_deepfact
from rk.eval.litqa2 import INSUFFICIENT, Answer, options_for, run_litqa2, score
from rk.eval.scratch import eval_provider
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.notes import NoteIn, add_note
from rk.verify.judge import JudgeVerdict

from .conftest import DOCS

LUNA = DriverSpec("omp_gateway", "openai-codex/gpt-6-luna")
QUOTE = "about copper catalysts for CO2 reduction"


class Scripted:
    """Plays scout and reader: the scout 'finds' a local copper paper, the reader quotes it."""

    kind, model, vendor = "chat_api", "s", "openai"

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        ps, lib = ctx.kernel.project(spec.project)
        if spec.role == "scout":
            if "copper" not in spec.inputs["claim"].lower():
                return TaskResult(spec.task_id, True, FoundSources(source_ids=[]), "", "s", "s", 0.0, None)
            iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]),
                               added_by="agent", actor="scout").item_id
            attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="scout")
            return TaskResult(spec.task_id, True, FoundSources(source_ids=[iid]), "", "s", "s", 0.0, None)
        (iid,) = spec.inputs["source_ids"]
        nid = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=QUOTE, role_in_source="main"),
                       actor="reader").note_id
        return TaskResult(spec.task_id, True, Quoted(note_ids=[nid]), "", "s", "s", 0.0, None)


class Judge:
    kind, model, vendor = "chat_api", "j", "openai"

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        v = JudgeVerdict(label="supported", quoted_spans=["copper catalysts for CO2 reduction"])
        return TaskResult(spec.task_id, True, v, "", "j", "j", 0.0, None)


def test_deepfact_mapping_and_report(tmp_home, tmp_path, replay_http):
    claims = [DeepFactClaim(id="r:1", sentence="Copper catalysts are studied for CO2 reduction.", report_id="r",
                            domain="chemistry", gold="supported"),
              DeepFactClaim(id="r:2", sentence="Zinc fixes everything.", report_id="r", domain="chemistry",
                            gold="contradictory")]
    r = run_deepfact(driver=LUNA, judges=[LUNA], min_judges=1, limit=2, claims=claims, provider=Scripted(),
                     judge_providers={str(LUNA): Judge()}, report_dir=tmp_path, http=replay_http)
    rows = {row["id"]: row for row in r["rows"]}
    assert rows["r:1"]["pred"] == "supported" and rows["r:1"]["notes"] == 1 and rows["r:1"]["sources"] == 1
    assert rows["r:2"]["pred"] == "inconclusive" and rows["r:2"]["sources"] == 0  # no evidence found → inconclusive
    assert r["confusion"]["contradictory"]["inconclusive"] == 1 and r["accuracy"] == 0.5
    assert "evaluation only" in (tmp_path / f"deepfact-{r['date']}.md").read_text()


def test_litqa2_scoring_counts_insufficient_as_unanswered():
    rows = [{"answered": True, "correct": True}, {"answered": True, "correct": False},
            {"answered": False, "correct": False}, {"answered": True, "correct": True}]
    assert score(rows) == {"n": 4, "answered": 3, "correct": 2, "accuracy": 0.5, "precision": 2 / 3,
                           "coverage": 0.75}


def test_option_shuffle_seeded():
    item = LitQAItem(id="q1", question="?", ideal="right", distractors=["w1", "w2", "w3"], sources=[])
    a, b = options_for(item, 13), options_for(item, 13)
    assert a == b and a[0][a[1]] == "right" and a[0][a[2]] == INSUFFICIENT and len(a[0]) == 5
    assert {tuple(options_for(LitQAItem(id=f"q{i}", question="?", ideal="r", distractors=["x", "y"], sources=[]),
                              13)[0]) for i in range(20)} != {("r", "x", "y", INSUFFICIENT)}  # actually shuffled


def test_litqa2_run_with_scripted_answers(tmp_home, tmp_path, replay_http):
    items = [LitQAItem(id=f"q{i}", question=f"Question {i}?", ideal="right", distractors=["wrong"], sources=[])
             for i in range(3)]

    class Answerer:
        kind, model, vendor = "chat_api", "a", "openai"

        def available(self):
            return Availability(ok=True)

        def run(self, spec, ctx):
            opts = list(spec.inputs["options"].values())
            pick = {"Question 0?": "right", "Question 1?": "wrong"}.get(spec.inputs["question"], INSUFFICIENT)
            return TaskResult(spec.task_id, True, Answer(choice_index=opts.index(pick)), "", "a", "a", 0.0, None)

    r = run_litqa2(driver=LUNA, limit=3, items=items, provider=Answerer(), report_dir=tmp_path, http=replay_http)
    assert (r["correct"], r["answered"], r["accuracy"]) == (1, 2, pytest.approx(1 / 3))
    assert "PaperQA2 + Ollama baseline | not run" in (tmp_path / f"litqa2-{r['date']}.md").read_text()


def test_eval_rejects_agentic_cli_drivers_for_tool_tasks_only():
    with pytest.raises(ValueError, match="chat-API driver"):
        eval_provider(DriverSpec("codex_cli"), Settings())
    # judges call no tools, so the default judges (codex_cli, claude_cli) run in a scratch kernel
    for spec in (DriverSpec("codex_cli", "gpt-6-luna"), DriverSpec("claude_cli", "claude-haiku-5-5")):
        assert eval_provider(spec, Settings(), tools=False).kind == "agentic_cli"
