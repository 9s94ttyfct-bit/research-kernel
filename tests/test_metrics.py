import pytest

from rk.eval.data import SupportPair
from rk.eval.metrics import (
    calibrate_joint,
    calibrate_thresholds,
    confusion,
    layer1_predict,
    support_metrics,
)
from rk.eval.support_eval import run_support_eval
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.verify.judge import JudgeVerdict
from rk.verify.minicheck import Layer1Score


def test_confusion_and_metrics():
    gold = ["supported", "supported", "unsupported", "contradicted", "supported"]
    pred = ["supported", "uncertain", "supported", "contradicted", "supported"]
    m = confusion(gold, pred, ["supported", "contradicted", "unsupported"],
                  ["supported", "contradicted", "unsupported", "uncertain"])
    assert m["supported"] == {"supported": 2, "contradicted": 0, "unsupported": 0, "uncertain": 1}
    assert m["unsupported"]["supported"] == 1
    s = support_metrics(gold, pred)
    assert s == {"n": 5, "predicted_supported": 3, "precision_supported": pytest.approx(2 / 3), "coverage": 0.8}
    assert support_metrics(["supported"], ["uncertain"])["precision_supported"] is None


def test_calibrate_reaches_target_on_separable_data():
    ps = [0.95, 0.9, 0.85, 0.6, 0.4, 0.1, 0.05]
    gold = ["supported", "supported", "supported", "unsupported", "supported", "unsupported", "contradicted"]
    t = calibrate_thresholds(ps, gold, target_precision=0.95)
    assert t["tau_hi"] == 0.85 and t["hi_reached"] and t["tau_lo"] == 0.1 and t["lo_reached"]
    assert [layer1_predict(p, t["tau_hi"], t["tau_lo"]) for p in (0.9, 0.5, 0.05)] == \
        ["supported", "uncertain", "unsupported"]


def test_calibrate_unreachable_returns_one():
    t = calibrate_thresholds([0.9, 0.8, 0.7], ["unsupported", "supported", "unsupported"], target_precision=0.95)
    assert t["tau_hi"] == 1.0 and not t["hi_reached"] and t["tau_lo"] < t["tau_hi"]


class FakeL1:
    """Layer 1 that cannot separate: every true claim and a quarter of the false ones score 0.6."""

    model_id = "fake/minicheck"

    def score_batch(self, pairs):
        return [Layer1Score(p=0.6 if ("true" in c or "decoy" in c) else 0.05, chunk_index=0, n_chunks=1, model="f")
                for _, c in pairs]


class FakeJudge:
    kind, model, vendor = "chat_api", "f", "openai"

    def __init__(self):
        self.calls = 0

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls += 1
        true = "true" in spec.inputs["claim"]
        v = JudgeVerdict(label="supported" if true else "unsupported", quoted_spans=["Evidence text"] if true else [])
        return TaskResult(spec.task_id, True, v, "", "f", "f", 0.0, None)


def _loader(split):
    pairs = []
    for i in range(40):
        kind = "true" if i % 2 else ("decoy" if i % 4 == 0 else "false")
        pairs.append(SupportPair(id=f"{split}{i}", claim=f"claim {chr(65 + i)} is {kind}",
                                 evidence="Evidence text for the claim.",
                                 label="supported" if kind == "true" else "unsupported"))
    return pairs


def test_joint_calibration_report_and_opinion_cache(tmp_home, tmp_path):
    luna = DriverSpec("omp_gateway", "openai-codex/gpt-6-luna")
    sonnet = DriverSpec("omp_gateway", "anthropic/claude-sonnet-5")
    judge = FakeJudge()
    args = {"judges": [luna, sonnet], "min_judges": 2, "limit": 20, "calib_limit": 20, "loader": _loader,
            "layer1": FakeL1(), "providers": {str(luna): judge, str(sonnet): judge}, "report_dir": tmp_path / "eval",
            "cache_dir": tmp_path, "workers": 1}
    r = run_support_eval(**args)
    assert not r["calibration"]["hi_reached"]  # layer 1 alone cannot reach 95% precision here
    j = r["joint_calibration"]
    assert j["tau_hi_judged"] == 0.6 and j["tau_lo_judged"] == 0.6 and j["n_agree_supported"] > 0
    d = r["dev_sample"]
    assert d["old_rule"]["predicted_supported"] == 0 and d["old_rule"]["coverage"] < 0.5
    assert d["amended_rule"]["precision_supported"] == 1.0 and d["amended_rule"]["coverage"] == 1.0
    md = (tmp_path / "eval" / f"support-{r['date']}.md").read_text()
    assert "## Joint calibration" in md and "### Amended rule" in md and "### Old rule" in md
    assert len((tmp_path / "scores-fake_minicheck-dev.jsonl").read_text().splitlines()) == 40
    calls = judge.calls
    assert calls == 2 * (20 + 20)
    run_support_eval(**args)
    assert judge.calls == calls  # every judge opinion came from the cache


def test_calibrate_joint_unreachable_and_empty():
    rows = [(0.9, ["supported", "supported"], "unsupported"), (0.8, ["supported"], "supported"),
            (0.1, ["contradicted", "contradicted"], "supported"), (0.05, ["unsupported", "contradicted"], "unsupported")]
    j = calibrate_joint(rows, target_precision=0.95)
    assert j["tau_hi_judged"] is None and j["tau_lo_judged"] is None  # agreement exists but is wrong
    # single-judge rows and split unsupported/contradicted rows never count as agreement
    assert j["n_agree_supported"] == 1 and j["n_agree_negative"] == 1
    assert calibrate_joint([], target_precision=0.95)["tau_hi_judged"] is None
