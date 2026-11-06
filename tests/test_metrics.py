import json

import pytest

from rk.eval.data import SupportPair
from rk.eval.metrics import (
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
    model_id = "fake/minicheck"

    def score_batch(self, pairs):
        return [Layer1Score(p=0.97 if "true" in claim else 0.03, chunk_index=0, n_chunks=1, model="f")
                for _, claim in pairs]


class FakeJudge:
    kind, model, vendor = "chat_api", "f", "openai"

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        true = "true" in spec.inputs["claim"]
        v = JudgeVerdict(label="supported" if true else "unsupported", quoted_spans=["Evidence text"] if true else [])
        return TaskResult(spec.task_id, True, v, "", "f", "f", 0.0, None)


def _loader(split):
    pairs = []
    for i in range(12):
        true = i % 3 != 0
        pairs.append(SupportPair(id=f"{split}{i}", claim=f"claim {chr(97 + i)} is {'true' if true else 'false'}",
                                 evidence="Evidence text for the claim.", label="supported" if true else "unsupported"))
    return pairs


def test_eval_support_report_written(tmp_home, tmp_path):
    luna = DriverSpec("omp_gateway", "openai-codex/gpt-6-luna")
    r = run_support_eval(judges=[luna], min_judges=1, limit=6, loader=_loader, layer1=FakeL1(),
                         providers={str(luna): FakeJudge()}, report_dir=tmp_path / "eval",
                         score_cache_dir=tmp_path)
    assert r["calibration"]["hi_reached"] and r["layer1"]["precision_supported"] == 1.0
    assert r["two_layer"]["sample"] == 6 and r["two_layer"]["precision_supported"] == 1.0
    md = (tmp_path / "eval" / f"support-{r['date']}.md").read_text()
    assert "## Layer 1 only" in md and "## Two layers" in md and "MET" in md and "test configuration" in md
    assert json.loads((tmp_path / "eval" / f"support-{r['date']}.json").read_text())["two_layer"]["min_judges"] == 1
    cached = (tmp_path / "scores-dev.jsonl").read_text().splitlines()
    assert len(cached) == 12  # layer-1 scores are cached for reruns
