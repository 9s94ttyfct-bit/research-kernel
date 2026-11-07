"""rk.stats: effect sizes, pooling and plots, validated against published meta-analyses (tests/fixtures/stats)."""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scipy import stats as sps

from rk.config import Settings, load_settings
from rk.stats import STATS_VERSION, meta, plots, stats_enabled
from rk.stats import effects as fx
from rk.stats.effects import Effect, Uncomputable

REFS = Path(__file__).parent / "fixtures" / "stats"


def _ref(name: str) -> dict:
    return json.loads((REFS / f"{name}.json").read_text())


def _bcg_effects(measure: str) -> list[Effect]:
    from rk.eval.stats_eval import study_data

    out = [fx.compute(measure, study_data(s)) for s in _ref("bcg")["studies"]]
    assert all(isinstance(e, Effect) for e in out)
    return out


def _close(a: float, b: float, tol: float = 1e-12) -> bool:
    return abs(a - b) <= tol


def test_effect_sizes_match_formulae():
    bin_ = {"events_treatment": 10, "n_treatment": 100, "events_control": 20, "n_control": 100}
    rr = fx.compute("RR", bin_)
    assert _close(rr.yi, math.log(0.1 / 0.2)) and _close(rr.vi, 1 / 10 - 1 / 100 + 1 / 20 - 1 / 100)
    orr = fx.compute("OR", bin_)
    assert _close(orr.yi, math.log(10 * 80 / (90 * 20))) and _close(orr.vi, 1 / 10 + 1 / 90 + 1 / 20 + 1 / 80)
    rd = fx.compute("RD", bin_)
    assert _close(rd.yi, -0.1) and _close(rd.vi, 0.1 * 0.9 / 100 + 0.2 * 0.8 / 100)

    # a zero cell: RR and OR add 0.5 to every cell of that study; RD needs no correction
    zero = {"events_treatment": 0, "n_treatment": 50, "events_control": 5, "n_control": 50}
    rr0 = fx.compute("RR", zero)
    assert _close(rr0.yi, math.log((0.5 / 51) / (5.5 / 51))) and _close(rr0.vi, 1 / 0.5 - 1 / 51 + 1 / 5.5 - 1 / 51)
    or0 = fx.compute("OR", zero)
    assert _close(or0.yi, math.log(0.5 * 45.5 / (50.5 * 5.5)))
    assert _close(or0.vi, 1 / 0.5 + 1 / 50.5 + 1 / 5.5 + 1 / 45.5)
    rd0 = fx.compute("RD", zero)
    assert _close(rd0.yi, -0.1) and _close(rd0.vi, 0.1 * 0.9 / 50)

    cont = {"mean_treatment": 103, "sd_treatment": 5.5, "n_treatment": 50,
            "mean_control": 100, "sd_control": 4.5, "n_control": 50}
    md = fx.compute("MD", cont)
    assert _close(md.yi, 3.0) and _close(md.vi, 5.5**2 / 50 + 4.5**2 / 50)

    # Hedges' g: d = 1 with a pooled SD of 2, exact small-sample correction J for df = 18
    smd = fx.compute("SMD", {"mean_treatment": 10, "sd_treatment": 2, "n_treatment": 10,
                             "mean_control": 8, "sd_control": 2, "n_control": 10})
    j = math.exp(math.lgamma(9) - math.log(3) - math.lgamma(8.5))
    assert _close(smd.yi, j) and _close(smd.vi, j * j * (20 / 100 + 1 / 40))
    assert 0.957 < j < 0.958  # ≈ 1 - 3/(4·18 - 1)

    # numbers extracted as text are read, not rejected
    assert fx.compute("RR", {k: str(v) for k, v in bin_.items()}) == rr


def test_uncomputable_study_is_reported_not_imputed():
    none = {"events_treatment": 0, "n_treatment": 30, "events_control": 0, "n_control": 32}
    for m in ("RR", "OR", "RD"):
        assert fx.compute(m, none) == Uncomputable("no events in either arm")

    partial = {"events_treatment": 3, "n_treatment": 10, "p_value": 0.03}
    r = fx.compute("OR", partial)
    assert isinstance(r, Uncomputable) and "events_control" in r.reason and "n_control" in r.reason

    # a median and an IQR are never turned into a mean and an SD
    medians = {"median_treatment": 5, "iqr_treatment": 2, "n_treatment": 20,
               "mean_control": 4, "sd_control": 1.5, "n_control": 20}
    r = fx.compute("SMD", medians)
    assert isinstance(r, Uncomputable)
    assert "mean_treatment" in r.reason and "sd_treatment" in r.reason and "mean_control" not in r.reason

    assert isinstance(fx.compute("RR", {"events_treatment": 12, "n_treatment": 10,
                                        "events_control": 1, "n_control": 10}), Uncomputable)
    assert isinstance(fx.compute("SMD", {"mean_treatment": 1, "sd_treatment": 0, "n_treatment": 10,
                                         "mean_control": 1, "sd_control": 0, "n_control": 10}), Uncomputable)
    assert isinstance(fx.compute("MD", {"mean_treatment": "n/a", "sd_treatment": 1, "n_treatment": 10,
                                        "mean_control": 1, "sd_control": 1, "n_control": 10}), Uncomputable)


def test_bcg_reproduces_published_dl_and_reml():
    from rk.eval.stats_eval import run_reference

    rows = run_reference(_ref("bcg"))
    failed = [r for r in rows if not r["pass"]]
    assert not failed, failed
    stats_seen = {(r["model"], r["statistic"]) for r in rows if r["measure"] == "RR"}
    assert {("REML", "tau2"), ("REML", "i2"), ("REML", "estimate"), ("REML", "se"),
            ("DL", "tau2"), ("DL", "i2"), ("DL", "estimate")} <= stats_seen

    # the published values themselves (Viechtbauer 2010, p. 14; Stata metan via metafor's tests)
    reml = meta.pool(_bcg_effects("RR"), model="REML", hksj=False)
    assert abs(reml.tau2 - 0.3132) < 1e-3 and abs(reml.estimate - -0.7145) < 1e-3 and abs(reml.se - 0.1798) < 1e-3
    dl = meta.pool(_bcg_effects("RR"), model="DL", hksj=False)
    assert abs(dl.tau2 - 0.3088) < 1e-3 and abs(dl.i2 - 0.921173) < 1e-3


def test_smd_dataset_reproduces_published_pooling():
    from rk.eval.stats_eval import run_reference

    for name in ("raudenbush2009", "borenstein2009"):
        rows = run_reference(_ref(name))
        assert rows and not [r for r in rows if not r["pass"]], name
    smd = {(r["model"], r["hksj"], r["statistic"]) for r in run_reference(_ref("borenstein2009"))
           if r["measure"] == "SMD"}
    assert ("DL", False, "pi_low") in smd and ("-", False, "yi") in smd


def test_hksj_widens_interval_and_uses_t_k_minus_1():
    es = _bcg_effects("RR")
    k = len(es)
    wald = meta.pool(es, model="REML", hksj=False)
    hk = meta.pool(es, model="REML", hksj=True)
    assert hk.hksj and not wald.hksj
    assert hk.estimate == wald.estimate and hk.tau2 == wald.tau2
    assert hk.ci_high - hk.ci_low > wald.ci_high - wald.ci_low

    w = [1 / (e.vi + hk.tau2) for e in es]
    sw = math.fsum(w)
    q = math.fsum(wi * (e.yi - hk.estimate) ** 2 for wi, e in zip(w, es, strict=True)) / (k - 1)
    assert math.isclose(hk.se, math.sqrt(q / sw), rel_tol=1e-12)
    assert math.isclose(wald.se, math.sqrt(1 / sw), rel_tol=1e-12)
    assert math.isclose(hk.ci_high - hk.estimate, sps.t.ppf(0.975, k - 1) * hk.se, rel_tol=1e-12)
    assert math.isclose(wald.ci_high - wald.estimate, sps.norm.ppf(0.975) * wald.se, rel_tol=1e-12)
    # the prediction interval: t with k-2 df on tau2 + the (Wald) variance of the mean, either way
    half = sps.t.ppf(0.975, k - 2) * math.sqrt(hk.tau2 + 1 / sw)
    for p in (hk, wald):
        assert math.isclose(p.pi_high - p.estimate, half, rel_tol=1e-12)
        assert math.isclose(p.estimate - p.pi_low, half, rel_tol=1e-12)
    two = meta.pool(es[:2], model="REML")
    assert two.pi_low is None and two.pi_high is None


def test_fe_model_has_zero_tau2():
    es = _bcg_effects("RR")
    fe = meta.pool(es, model="FE")  # the default hksj=True does not apply to a fixed-effect model
    assert fe.tau2 == 0.0 and fe.model == "FE" and not fe.hksj
    assert fe.pi_low is None and fe.pi_high is None
    w = [1 / e.vi for e in es]
    assert math.isclose(fe.estimate, math.fsum(wi * e.yi for wi, e in zip(w, es, strict=True)) / math.fsum(w))
    assert math.isclose(fe.se, math.sqrt(1 / math.fsum(w)))
    assert fe.q_df == len(es) - 1 and math.isclose(fe.i2, (fe.q - fe.q_df) / fe.q)
    with pytest.raises(ValueError):
        meta.pool(es[:1])


def test_reml_nonconvergence_is_an_error(monkeypatch):
    es = _bcg_effects("RR")
    assert meta.pool(es, model="REML").tau2 > 0
    monkeypatch.setattr(meta, "REML_MAX_ITER", 1)
    with pytest.raises(meta.ConvergenceError):
        meta.pool(es, model="REML")


def test_forest_svg_escapes_labels_and_is_deterministic():
    es = _bcg_effects("RR")[:3]
    labels = ['A & B <script>alert(1)</script>', 'C "quoted" \x01', "Ok"]
    pooled = meta.pool(es, model="REML")
    svg = plots.forest_svg(labels, es, pooled, measure="RR")
    assert svg == plots.forest_svg(labels, es, pooled, measure="RR")
    assert "<script" not in svg.lower() and "A &amp; B &lt;script&gt;alert(1)&lt;/script&gt;" in svg
    root = ET.fromstring(svg)  # well-formed XML (the control character is dropped)
    assert root.tag.endswith("svg")
    texts = ["".join(t.itertext()) for t in root.iter() if t.tag.endswith("text")]
    assert labels[0] in texts and 'C "quoted" ' in texts
    assert plots.forest_svg(labels, es, None, measure="RR") != svg
    with pytest.raises(ValueError):
        plots.forest_svg(labels[:2], es, None, measure="RR")


def test_map_table_counts_categories():
    rows = [{"design": "RCT", "outcome": ["pain", "function"]}, {"design": "cohort", "outcome": "pain"},
            {"design": "RCT", "outcome": None}]
    t = plots.map_table(rows, ["design", "outcome"])
    assert t["n"] == 3
    assert t["fields"]["design"] == {"RCT": 2, "cohort": 1}
    assert t["fields"]["outcome"] == {"pain": 2, "function": 1, "not reported": 1}
    assert t["cross"]["counts"]["RCT"] == {"pain": 1, "function": 1, "not reported": 1}


def test_stats_enabled_requires_current_version(monkeypatch):
    import rk.stats

    assert not stats_enabled(Settings())
    s = Settings(stats_validated={"version": STATS_VERSION, "report": "docs/eval/stats-x.md"})
    assert stats_enabled(s)
    monkeypatch.setattr(rk.stats, "STATS_VERSION", "0" * 64)  # the module's source changed since validation
    assert not stats_enabled(s)
    assert len(STATS_VERSION) == 64


def test_eval_write_config_only_when_all_pass(tmp_home, tmp_path, monkeypatch):
    from rk.eval.stats_eval import run_stats_eval

    r = run_stats_eval(tmp_path / "out", write_config=True)
    assert r["all_pass"] and r["config_written"] and r["n_checks"] > 100
    md, js = Path(r["report"]), Path(r["json"])
    assert md.name == f"stats-{r['date']}.md" and js.name == f"stats-{r['date']}.json"
    assert json.loads(js.read_text())["stats_version"] == STATS_VERSION
    assert "10.10.4.4" in md.read_text()
    assert load_settings().stats_validated == {"version": STATS_VERSION, "report": str(md)}

    bad = tmp_path / "bad-refs"
    shutil.copytree(REFS, bad)
    ref = json.loads((bad / "bcg.json").read_text())
    ref["analyses"][0]["expected"][0]["value"] += 0.01  # a published estimate no longer reproduced
    (bad / "bcg.json").write_text(json.dumps(ref))
    home2 = tmp_path / "home2"
    home2.mkdir()
    monkeypatch.setenv("RK_HOME", str(home2))
    r2 = run_stats_eval(tmp_path / "out2", refs_dir=bad, write_config=True)
    assert not r2["all_pass"] and not r2["config_written"]
    assert load_settings().stats_validated is None
    assert "FAIL" in Path(r2["report"]).read_text()
