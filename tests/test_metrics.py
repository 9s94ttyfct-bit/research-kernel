import pytest

from rk.eval.metrics import (
    calibrate_joint,
    calibrate_thresholds,
    confusion,
    layer1_predict,
    support_metrics,
)


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


def test_calibrate_joint_unreachable_and_empty():
    rows = [(0.9, ["supported", "supported"], "unsupported"), (0.8, ["supported"], "supported"),
            (0.1, ["contradicted", "contradicted"], "supported"), (0.05, ["unsupported", "contradicted"], "unsupported")]
    j = calibrate_joint(rows, target_precision=0.95)
    assert j["tau_hi_judged"] is None and j["tau_lo_judged"] is None  # agreement exists but is wrong
    # single-judge rows and split unsupported/contradicted rows never count as agreement
    assert j["n_agree_supported"] == 1 and j["n_agree_negative"] == 1
    assert calibrate_joint([], target_precision=0.95)["tau_hi_judged"] is None
