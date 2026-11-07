import json

import pytest

from rk.config import Settings, save_settings
from rk.discovery.executor import RateCapped
from rk.eval.data import SupportPair, eval_dir
from rk.eval.metrics import confusion, support_metrics
from rk.eval.support_eval import CACHE_NAME, run_support_eval
from rk.llm.drivers import DriverSpec
from rk.llm.ratelimit import CallCaps
from rk.llm.types import Availability, TaskResult
from rk.verify import judge
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
                 "predicted_negative": 2, "precision_negative": 0.5, "coverage": pytest.approx(5 / 6),
                 "precision_by_label": {"supported": pytest.approx(2 / 3), "unsupported": 0.0, "contradicted": 1.0}}
    none = support_metrics(["supported"], ["uncertain"])
    assert none["precision_supported"] is None and none["precision_negative"] is None
    assert none["precision_by_label"] == {"supported": None, "unsupported": None, "contradicted": None}


LUNA = DriverSpec("codex_cli", "gpt-6-luna")  # openai
DEEPSEEK = DriverSpec("ollama", "deepseek-v4.1-flash:cloud")  # deepseek
HAIKU = DriverSpec("claude_cli", "claude-haiku-5-5")  # anthropic
JUDGES = [LUNA, DEEPSEEK, HAIKU]
EVIDENCE = "Copper reduces carbon dioxide at high rates in the tested cells."
# claim → (gold, labels of luna, deepseek, haiku); "failed" makes that judge's task fail
SCRIPT = {
    "Claim one holds.": ("supported", ("supported", "supported", "supported")),
    "Claim two holds.": ("supported", ("supported", "supported", "supported")),
    "Claim three holds.": ("supported", ("supported", "partial", "partial")),
    "Claim four holds.": ("contradicted", ("contradicted", "contradicted", "contradicted")),
    "Claim five holds.": ("contradicted", ("contradicted", "unsupported", "failed")),
    "Claim six holds.": ("unsupported", ("unsupported", "unsupported", "contradicted")),
}


class ScriptedJudge:
    """The ``index``-th judge of ``SCRIPT``; ``calls`` counts its task attempts and ``roles`` the roles it ran as."""

    kind, model, vendor = "chat_api", "fake", "x"

    def __init__(self, index: int, script=SCRIPT):
        self.index, self.script, self.calls, self.roles = index, script, 0, set()

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls += 1
        self.roles.add(spec.role)
        label = self.script[spec.inputs["claim"]][1][self.index]
        if label == "failed":
            return TaskResult(spec.task_id, False, None, "", "fake", "fake", 0.1, "provider_error: quota")
        v = JudgeVerdict(label=label, quoted_spans=["Copper reduces carbon dioxide"])
        return TaskResult(spec.task_id, True, v, "", "fake", "fake", 0.1, None)


def _loader(script=SCRIPT):
    return lambda: [SupportPair(id=f"{i}:1", claim=claim, evidence=EVIDENCE, label=gold)
                    for i, (claim, (gold, _)) in enumerate(script.items())]


def _args(tmp_path, fakes, script=SCRIPT):
    return {"judges": JUDGES, "min_judges": 2, "limit": None, "workers": 1, "loader": _loader(script),
            "report_dir": tmp_path / "eval", "judge_providers": {str(j): f for j, f in zip(JUDGES, fakes, strict=True)}}


def _keys(x) -> set[str]:
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in _keys(v)}
    return {k for v in x for k in _keys(v)} if isinstance(x, list) else set()


def _matrix(sup, con, uns):
    labels = ["supported", "contradicted", "unsupported", "uncertain"]
    return {g: dict(zip(labels, row, strict=True)) for g, row in
            (("supported", sup), ("contradicted", con), ("unsupported", uns))}


def test_support_eval_reports_judges_only_coverage(tmp_home, tmp_path):
    fakes = [ScriptedJudge(i) for i in range(3)]
    r = run_support_eval(**_args(tmp_path, fakes))
    assert [f.roles for f in fakes] == [{"judge"}, {"judge_2"}, {"judge_3"}]  # every judge runs as its own role
    sc = {s["generator"]: s for s in r["scenarios"]}
    assert list(sc) == ["anthropic", "openai", "human"]  # the default writer's scenario is the headline
    human = sc["human"]
    assert human["not_admitted"] == {} and human["admitted"] == [str(j) for j in JUDGES]
    assert [i["verdicts"]["human"] for i in r["items"]] == ["supported", "supported", "uncertain", "contradicted",
                                                            "uncertain", "uncertain"]
    assert human["confusion"] == _matrix([2, 0, 0, 1], [0, 1, 0, 1], [0, 0, 0, 1])
    assert human["coverage"] == 0.5 and human["precision_supported"] == 1.0
    # Haiku writes: haiku is not admitted, luna and deepseek decide (claim six: both say unsupported)
    anthropic = sc["anthropic"]
    assert anthropic["not_admitted"] == {str(HAIKU): "same_or_unknown_vendor"}
    assert [i["verdicts"]["anthropic"] for i in r["items"]] == ["supported", "supported", "uncertain", "contradicted",
                                                                "uncertain", "unsupported"]
    assert anthropic["confusion"] == _matrix([2, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 0])
    assert anthropic["coverage"] == pytest.approx(4 / 6) and anthropic["precision_negative"] == 1.0
    assert anthropic["precision_by_label"] == {"supported": 1.0, "unsupported": 1.0, "contradicted": 1.0}
    assert sc["openai"]["not_admitted"] == {str(LUNA): "same_or_unknown_vendor"}
    assert sc["openai"]["coverage"] == 0.5  # claim five: deepseek alone (haiku's call failed) is not enough
    d = r["dev"]
    assert d["sample"] == 6 and d["failed_judge_calls"] == 1
    assert [(j["role"], j["driver"], j["valid"], j["failed"], j["agrees_with_gold"]) for j in r["judges"]] == [
        ("judge", str(LUNA), 6, 0, 6), ("judge_2", str(DEEPSEEK), 6, 0, 4), ("judge_3", str(HAIKU), 5, 1, 3)]
    assert all(len(j["prompt_fingerprint"]) == 64 for j in r["judges"])
    # agreement on a verdict label; matching partial/uncertain opinions are counted apart
    assert [(a["a"], a["b"], a["both_valid"], a["same_verdict_label"], a["same_other"])
            for a in r["pairwise_agreement"]] == [(str(LUNA), str(DEEPSEEK), 6, 4, 0), (str(LUNA), str(HAIKU), 5, 3, 0),
                                                 (str(DEEPSEEK), str(HAIKU), 5, 3, 1)]
    stem = tmp_path / "eval" / f"support-{r['date']}-judges"
    assert r["report"] == str(stem.with_suffix(".md"))
    stored = json.loads(stem.with_suffix(".json").read_text())
    md = stem.with_suffix(".md").read_text()
    assert not {k for k in _keys(stored) if k == "p" or "tau" in k}
    assert "τ" not in md and "layer 1" not in md.lower()
    assert "## Generator `anthropic` (headline)" in md and "not admitted: `claude_cli:claude-haiku-5-5`" in md
    assert "| supported | 2 | 0 | 0 | 1 |" in md

    calls = [f.calls for f in fakes]
    run_support_eval(**_args(tmp_path, fakes))  # successful opinions come from the cache; only the failed call reruns
    assert [f.calls for f in fakes][:2] == calls[:2] and fakes[2].calls > calls[2]


def test_cache_is_fingerprinted_and_ignores_the_old_file(tmp_home, tmp_path, monkeypatch):
    # an opinion cached by the old evaluation (no fingerprint) is never used
    (eval_dir() / "judges-dev.jsonl").write_text(json.dumps({"id": "0:1", "driver": str(LUNA), "opinion": {
        "driver": str(LUNA), "vendor": "openai", "label": "contradicted"}}) + "\n")
    fakes = [ScriptedJudge(i) for i in range(3)]
    r = run_support_eval(**_args(tmp_path, fakes))
    assert fakes[0].calls == 6 and r["items"][0]["labels"][0] == "supported"
    assert all("key" in json.loads(line) for line in (eval_dir() / CACHE_NAME).read_text().splitlines())
    run_support_eval(**_args(tmp_path, fakes))
    assert fakes[0].calls == 6  # same prompt: cached
    monkeypatch.setattr(judge, "INSTRUCTIONS", judge.INSTRUCTIONS + " Be brief.")
    r2 = run_support_eval(**_args(tmp_path, fakes))
    assert fakes[0].calls == 12  # changed prompt: every opinion is asked again
    assert r2["judges"][0]["prompt_fingerprint"] != r["judges"][0]["prompt_fingerprint"]


def test_rate_capped_judge_stops_the_eval_without_a_failure(tmp_home, tmp_path):
    save_settings(Settings(calls_per_hour={"ollama": 1, "codex_cli": 20, "claude_cli": 30}))
    caps = CallCaps(tmp_home / "kernel.sqlite")  # the real call log, shared with rk serve
    assert caps.acquire("ollama", 1)  # the hour's only ollama call is already used
    caps.close()
    fakes = [ScriptedJudge(i) for i in range(3)]
    with pytest.raises(RateCapped, match="ollama"):
        run_support_eval(**_args(tmp_path, fakes))
    assert fakes[1].calls == 0 and fakes[0].calls == 6
    cached = [json.loads(line) for line in (eval_dir() / CACHE_NAME).read_text().splitlines()]
    assert {c["driver"] for c in cached} == {str(LUNA), str(HAIKU)}  # nothing cached for the capped judge
    assert not (tmp_path / "eval").exists()  # no report with capped calls counted as failures


def test_all_failed_judges_render_as_not_available(tmp_home, tmp_path):
    script = {"Claim one holds.": ("supported", ("failed",) * 3), "Claim two holds.": ("unsupported", ("failed",) * 3)}
    fakes = [ScriptedJudge(i, script) for i in range(3)]
    r = run_support_eval(**_args(tmp_path, fakes, script))
    head = r["scenarios"][0]
    assert head["coverage"] == 0.0 and head["predicted_supported"] == 0 and head["precision_supported"] is None
    assert r["dev"]["failed_judge_calls"] == 6
    md = (tmp_path / "eval" / f"support-{r['date']}-judges.md").read_text()
    assert "precision(supported) n/a (0 predicted supported)" in md and "coverage 0.000" in md
    assert "precision by verdict: supported n/a, unsupported n/a, contradicted n/a" in md
