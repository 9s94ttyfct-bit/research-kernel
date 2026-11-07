import json

import pytest

from rk.eval.data import SupportPair
from rk.eval.metrics import confusion, support_metrics
from rk.eval.support_eval import run_support_eval
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.verify.judge import JudgeVerdict


def test_confusion_and_metrics():
    gold = ["supported", "supported", "unsupported", "contradicted", "supported", "supported"]
    pred = ["supported", "uncertain", "supported", "contradicted", "supported", "unsupported"]
    m = confusion(gold, pred, ["supported", "contradicted", "unsupported"],
                  ["supported", "contradicted", "unsupported", "uncertain"])
    assert m["supported"] == {"supported": 2, "contradicted": 0, "unsupported": 1, "uncertain": 1}
    assert m["unsupported"]["supported"] == 1
    s = support_metrics(gold, pred)
    assert s == {"n": 6, "predicted_supported": 3, "precision_supported": pytest.approx(2 / 3),
                 "predicted_negative": 2, "precision_negative": 0.5, "coverage": pytest.approx(5 / 6)}
    none = support_metrics(["supported"], ["uncertain"])
    assert none["precision_supported"] is None and none["precision_negative"] is None


LUNA = DriverSpec("codex_cli", "gpt-6-luna")
DEEPSEEK = DriverSpec("ollama", "deepseek-v4.1-flash:cloud")
HAIKU = DriverSpec("claude_cli", "claude-haiku-5-5")
EVIDENCE = "Copper reduces carbon dioxide at high rates in the tested cells."
# claim → (gold, labels of luna, deepseek, haiku); "failed" makes that judge's task fail
SCRIPT = {
    "Claim one holds.": ("supported", ("supported", "supported", "supported")),
    "Claim two holds.": ("supported", ("supported", "supported", "supported")),
    "Claim three holds.": ("supported", ("supported", "supported", "partial")),
    "Claim four holds.": ("contradicted", ("contradicted", "contradicted", "contradicted")),
    "Claim five holds.": ("contradicted", ("contradicted", "unsupported", "failed")),
    "Claim six holds.": ("unsupported", ("unsupported", "contradicted", "unsupported")),
}


class ScriptedJudge:
    """The ``index``-th judge of ``SCRIPT``; ``calls`` counts its task attempts."""

    kind, model, vendor = "chat_api", "fake", "x"

    def __init__(self, index: int):
        self.index, self.calls = index, 0

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls += 1
        label = SCRIPT[spec.inputs["claim"]][1][self.index]
        if label == "failed":
            return TaskResult(spec.task_id, False, None, "", "fake", "fake", 0.1, "provider_error: quota")
        v = JudgeVerdict(label=label, quoted_spans=["Copper reduces carbon dioxide"])
        return TaskResult(spec.task_id, True, v, "", "fake", "fake", 0.1, None)


def _loader(split):
    assert split == "dev"  # nothing is calibrated, so the train split is never read
    return [SupportPair(id=f"{i}:1", claim=claim, evidence=EVIDENCE, label=gold)
            for i, (claim, (gold, _)) in enumerate(SCRIPT.items())]


def _keys(x) -> set[str]:
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in _keys(v)}
    return {k for v in x for k in _keys(v)} if isinstance(x, list) else set()


def test_support_eval_reports_judges_only_coverage(tmp_home, tmp_path):
    fakes = [ScriptedJudge(i) for i in range(3)]
    args = {"judges": [LUNA, DEEPSEEK, HAIKU], "min_judges": 2, "limit": None, "workers": 1, "loader": _loader,
            "report_dir": tmp_path / "eval",
            "judge_providers": {str(j): f for j, f in zip([LUNA, DEEPSEEK, HAIKU], fakes, strict=True)}}
    r = run_support_eval(**args)
    assert [i["verdict"] for i in r["items"]] == ["supported", "supported", "uncertain", "contradicted", "uncertain",
                                                  "uncertain"]
    d = r["dev"]
    assert d["confusion"] == {
        "supported": {"supported": 2, "contradicted": 0, "unsupported": 0, "uncertain": 1},
        "contradicted": {"supported": 0, "contradicted": 1, "unsupported": 0, "uncertain": 1},
        "unsupported": {"supported": 0, "contradicted": 0, "unsupported": 0, "uncertain": 1}}
    assert d["coverage"] == 0.5 and d["precision_supported"] == 1.0 and d["precision_negative"] == 1.0
    assert d["sample"] == 6 and d["failed_judge_calls"] == 1
    assert [(j["driver"], j["valid"], j["failed"], j["agrees_with_gold"]) for j in r["judges"]] == [
        (str(LUNA), 6, 0, 6), (str(DEEPSEEK), 6, 0, 4), (str(HAIKU), 5, 1, 4)]
    assert [(a["a"], a["b"], a["both_valid"], a["same_label"]) for a in r["pairwise_agreement"]] == [
        (str(LUNA), str(DEEPSEEK), 6, 4), (str(LUNA), str(HAIKU), 5, 4), (str(DEEPSEEK), str(HAIKU), 5, 3)]
    stem = tmp_path / "eval" / f"support-{r['date']}-judges"
    assert r["report"] == str(stem.with_suffix(".md"))
    stored = json.loads(stem.with_suffix(".json").read_text())
    md = stem.with_suffix(".md").read_text()
    assert not {k for k in _keys(stored) if k == "p" or "tau" in k}
    assert "τ" not in md and "layer 1" not in md.lower() and "| supported | 2 | 0 | 0 | 1 |" in md

    calls = [f.calls for f in fakes]
    run_support_eval(**args)  # successful opinions come from the cache; only the failed call is retried
    assert [f.calls for f in fakes][:2] == calls[:2] and fakes[2].calls > calls[2]
