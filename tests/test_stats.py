"""rk.stats: effect sizes, pooling and plots, validated against published meta-analyses (rk/stats/reference)."""

from __future__ import annotations

import json
import math
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from scipy import stats as sps

from rk.config import Settings, load_settings
from rk.stats import (
    REFERENCE_DIR,
    STATS_VERSION,
    meta,
    plots,
    stats_enabled,
    version_files,
    version_of,
)
from rk.stats import effects as fx
from rk.stats.effects import Effect, Uncomputable

REFS = REFERENCE_DIR
HARD = [Effect(1.432, 1.34), Effect(0.459, 0.1198), Effect(-2.208, 2.3243), Effect(-2.525, 10.5635)]


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
    assert {("REML", "tau2"), ("REML", "i2"), ("REML", "estimate"), ("REML", "se"), ("REML", "exp_pi_low"),
            ("REML", "pi_high"), ("DL", "tau2"), ("DL", "i2"), ("DL", "estimate")} <= stats_seen

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


def test_prediction_interval_uses_the_ci_family_or_higgins_k_minus_2():
    es = _bcg_effects("RR")
    k = len(es)
    hk = meta.pool(es, model="REML", hksj=True)
    wald = meta.pool(es, model="REML", hksj=False)
    sw = math.fsum(1 / (e.vi + hk.tau2) for e in es)
    # RevMan rule (Cochrane Handbook 6.5.1 §10.10.4.4): the CI's SE and multiplier family
    half_hk = sps.t.ppf(0.975, k - 1) * math.sqrt(hk.tau2 + hk.se**2)
    half_wald = sps.norm.ppf(0.975) * math.sqrt(wald.tau2 + 1 / sw)
    for p, half in ((hk, half_hk), (wald, half_wald)):
        assert p.pi_df == "ci"
        assert math.isclose(p.pi_high - p.estimate, half, rel_tol=1e-12)
        assert math.isclose(p.estimate - p.pi_low, half, rel_tol=1e-12)
    # Higgins, Thompson & Spiegelhalter (2009): t(k-2) on the Wald variance, whatever the CI
    half_k2 = sps.t.ppf(0.975, k - 2) * math.sqrt(hk.tau2 + 1 / sw)
    for hksj in (True, False):
        p = meta.pool(es, model="REML", hksj=hksj, pi_df="k-2")
        assert p.pi_df == "k-2" and p.hksj == hksj
        assert math.isclose(p.pi_high - p.estimate, half_k2, rel_tol=1e-12)
    # no PI below 3 studies or for a fixed-effect model; tau2 = 0 under Wald gives PI == CI
    assert meta.pool(es[:2], model="REML").pi_low is None
    assert meta.pool(es, model="FE").pi_low is None
    same = meta.pool([Effect(0.3, 0.04)] * 5, model="REML")
    assert (same.pi_low, same.pi_high) == pytest.approx((same.ci_low, same.ci_high), abs=1e-15)
    with pytest.raises(ValueError):
        meta.pool(es, pi_df="k-1")


def test_hksj_auto_follows_the_handbook():
    es = _bcg_effects("RR")
    default = meta.pool(es)  # REML, hksj="auto": tau2 > 0 and k > 2
    assert default.model == "REML" and default.tau2 > 0 and default.hksj
    assert default == meta.pool(es, model="REML", hksj=True)
    pair = [es[3], es[7]]  # Hart & Sutherland vs TPT Madras: clearly heterogeneous
    two = meta.pool(pair)  # k = 2: Wald under auto, HKSJ only when forced
    assert two.tau2 > 0 and not two.hksj
    assert meta.pool(pair, hksj=True).hksj
    homogeneous = [Effect(0.10, 0.04), Effect(0.11, 0.04), Effect(0.09, 0.04), Effect(0.10, 0.04)]
    for model in ("DL", "REML"):
        p = meta.pool(homogeneous, model=model)  # tau2 floored at 0: Wald under auto
        assert p.tau2 == 0.0 and p.i2 == 0.0 and not p.hksj
        assert math.isclose(p.se, math.sqrt(0.04 / 4), rel_tol=1e-12)
    for bad in ("yes", 1, 0, 1.0, np.True_, np.False_, None):  # only bool instances or "auto"
        with pytest.raises(ValueError):
            meta.pool(es, hksj=bad)


def test_identical_studies_and_too_few_studies():
    same = [Effect(0.3, 0.04)] * 5
    for model in ("FE", "DL", "REML"):
        p = meta.pool(same, model=model)
        assert p.estimate == pytest.approx(0.3, abs=1e-15) and p.q == pytest.approx(0, abs=1e-25)
        assert p.tau2 == 0.0 and p.i2 == 0.0 and not p.hksj and p.q_df == 4
    for few in ([], [Effect(0.3, 0.04)]):
        with pytest.raises(ValueError):
            meta.pool(few)


def test_non_finite_inputs_and_sums_raise():
    with pytest.raises(ValueError):
        meta.pool([Effect(0.1, 1e-320), Effect(0.2, 0.1)])  # the weight overflows to inf
    with pytest.raises(ValueError):
        meta.pool([Effect(1e200, 0.1), Effect(-1e200, 0.1)])  # Q overflows
    with pytest.raises(ValueError):
        meta.pool([Effect(float("nan"), 0.1), Effect(0.2, 0.1)])


@pytest.mark.parametrize("model", ["FE", "DL", "REML"])
@pytest.mark.parametrize("variances", [[1e-200, 1e-200, 1e-200], [1e-200, 0.1, 0.2],  # weights overflow
                                       [1e200, 1e200, 1e200], [1e200, 0.1]])  # weights or denominators underflow
def test_out_of_range_variances_raise_value_error(model, variances):
    """Weights that overflow (tiny variances) or random-effects denominators that underflow to 0 (huge variances)
    give a ValueError naming the variances, never a ZeroDivisionError."""
    es = [Effect(y, v) for y, v in zip((0.1, 0.5, -0.3), variances, strict=False)]
    if model == "FE" and variances[0] == 1e200:
        p = meta.pool(es, model="FE")  # no random-effects denominator: plain inverse-variance arithmetic
        assert math.isfinite(p.estimate) and math.isfinite(p.se)
        return
    with pytest.raises(ValueError, match="variances"):
        meta.pool(es, model=model)


def test_fe_model_has_zero_tau2():
    es = _bcg_effects("RR")
    fe = meta.pool(es, model="FE")  # hksj does not apply to a fixed-effect model
    assert fe.tau2 == 0.0 and fe.model == "FE" and not fe.hksj
    assert meta.pool(es, model="FE", hksj=True) == fe
    assert fe.pi_low is None and fe.pi_high is None
    w = [1 / e.vi for e in es]
    assert math.isclose(fe.estimate, math.fsum(wi * e.yi for wi, e in zip(w, es, strict=True)) / math.fsum(w))
    assert math.isclose(fe.se, math.sqrt(1 / math.fsum(w)))
    assert fe.q_df == len(es) - 1 and math.isclose(fe.i2, (fe.q - fe.q_df) / fe.q)


def _reml_loglik(y: list[float], v: list[float], t: float) -> float:
    """The restricted log-likelihood up to a constant, written out independently of rk.stats."""
    w = [1 / (vi + t) for vi in v]
    sw = sum(w)
    mu = sum(wi * yi for wi, yi in zip(w, y, strict=True)) / sw
    return -0.5 * (sum(math.log(vi + t) for vi in v) + math.log(sw)
                   + sum(wi * (yi - mu) ** 2 for wi, yi in zip(w, y, strict=True)))


def _is_reml_root(y: list[float], v: list[float], t: float) -> bool:
    """``t`` is a stationary point of the REML likelihood (the score changes sign within 1e-7·(1 + t)), or ``t`` is 0
    and the score there is not positive."""
    if t == 0.0:
        return meta.reml_score(y, v, 0.0) <= 0
    d = 1e-7 * (1 + t)
    return meta.reml_score(y, v, max(0.0, t - d)) > 0 > meta.reml_score(y, v, t + d)


def test_reml_converges_where_plain_fisher_scoring_cycles():
    y, v = [e.yi for e in HARD], [e.vi for e in HARD]
    p = meta.pool(HARD, model="REML")
    assert p.tau2 > 0 and _is_reml_root(y, v, p.tau2)
    grid = [i * 1e-4 for i in range(50_001)]  # 0 .. 5
    best = max(grid, key=lambda t: _reml_loglik(y, v, t))
    assert abs(p.tau2 - best) <= 1e-4
    assert _reml_loglik(y, v, p.tau2) >= _reml_loglik(y, v, best) - 1e-12


def test_reml_seeded_stress_has_no_failures():
    rng = random.Random(20261107)
    failures = []
    for n in range(2000):
        k = rng.randint(2, 12)
        scale = 10 ** rng.uniform(-3, 2)
        v = [scale * 10 ** rng.uniform(-1.5, 1.5) for _ in range(k)]
        tau = math.sqrt(scale) * rng.choice([0.0, 0.3, 1.0, 3.0, 10.0])
        y = [rng.gauss(0, tau) + rng.gauss(0, math.sqrt(vi)) for vi in v]
        try:
            p = meta.pool([Effect(a, b) for a, b in zip(y, v, strict=True)], model="REML")
        except Exception as e:  # noqa: BLE001 - every failure is collected and reported
            failures.append((n, repr(e)))
            continue
        if not _is_reml_root(y, v, p.tau2):
            failures.append((n, p.tau2))
    assert failures == []


# Two bimodal REML likelihoods, found by a seeded search: both have a local maximum at τ² = 0 (score(0) < 0) and
# one in the interior. In BOUNDARY_GLOBAL the boundary is higher, in INTERIOR_GLOBAL the interior maximum is.
BOUNDARY_GLOBAL = ([-1.123, 0.016, -0.871, -0.002, -0.006, -0.016], [0.3219, 0.003, 0.0744, 0.0217, 0.0298, 0.0038])
INTERIOR_GLOBAL = ([2.126, 6.149, -0.005, 1.838, -1.426, -0.014, 0.665],
                   [1.5448, 2.7721, 0.0038, 7.4657, 1.9275, 0.0056, 3.8707])


@pytest.mark.parametrize(("data", "boundary_wins"), [(BOUNDARY_GLOBAL, True), (INTERIOR_GLOBAL, False)])
def test_reml_returns_the_global_maximum(data, boundary_wins):
    y, v = data
    grid = [0.0] + [10 ** (e / 400) for e in range(-2400, 1201)]  # 0 and 1e-6 .. 1e3, log-spaced
    lls = [_reml_loglik(y, v, t) for t in grid]
    peaks = [i for i in range(len(grid)) if (i == 0 or lls[i] > lls[i - 1]) and (i == len(grid) - 1 or
                                                                                   lls[i] > lls[i + 1])]
    assert len(peaks) == 2 and peaks[0] == 0 and meta.reml_score(y, v, 0.0) < 0  # really bimodal
    best = max(peaks, key=lambda i: lls[i])
    assert (best == 0) == boundary_wins
    p = meta.pool([Effect(a, b) for a, b in zip(y, v, strict=True)], model="REML")
    if boundary_wins:
        assert p.tau2 == 0.0
    else:
        assert _is_reml_root(y, v, p.tau2) and abs(p.tau2 - grid[best]) <= 0.03 * grid[best]
    assert _reml_loglik(y, v, p.tau2) >= lls[best] - 1e-12


def test_reml_nonconvergence_is_an_error(monkeypatch):
    assert meta.pool(HARD, model="REML").tau2 > 0
    monkeypatch.setattr(meta, "REML_MAX_ITER", 1)
    assert meta.pool(HARD, model="REML").tau2 > 0  # the safeguarded solve finishes it
    monkeypatch.setattr(meta, "REML_BRENT_MAXITER", 1)
    with pytest.raises(meta.ConvergenceError):
        meta.pool(HARD, model="REML")


def test_stats_version_covers_sources_reference_data_and_the_evaluation(tmp_path):
    files = version_files()
    names = {p.relative_to(REFS.parents[1]).as_posix() for p in files}
    assert {"stats/meta.py", "stats/effects.py", "stats/reference/bcg.json", "eval/stats_eval.py"} <= names
    root = tmp_path / "rk"
    copies = []
    for p in files:
        c = root / p.relative_to(REFS.parents[1])
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_bytes(p.read_bytes())
        copies.append(c)
    assert version_of(copies, root) == STATS_VERSION
    meta_copy = root / "stats" / "meta.py"
    meta_copy.write_bytes(meta_copy.read_bytes().replace(b"\n", b"\r\n"))  # a CRLF checkout: same version
    assert version_of(copies, root) == STATS_VERSION
    data = bytearray(meta_copy.read_bytes())
    data[-3] ^= 1  # one changed source byte: another version
    meta_copy.write_bytes(bytes(data))
    assert version_of(copies, root) != STATS_VERSION


def test_reference_tolerances_follow_the_printed_digits():
    from rk.eval.stats_eval import check_tolerance, plan_tolerance

    for path in sorted(REFS.glob("*.json")):
        ref = json.loads(path.read_text())
        for block in ref.get("study_checks", []) + ref.get("analyses", []):
            for exp in block["expected"]:
                assert isinstance(exp["value"], str), (path.name, exp)  # as printed, digits intact
                tol = check_tolerance(exp)
                assert tol <= plan_tolerance(exp["statistic"])
                decimals = len(exp["value"].partition(".")[2])
                if "tolerance" in exp:
                    assert exp.get("tolerance_note"), (path.name, exp)
                else:
                    assert tol == min(plan_tolerance(exp["statistic"]), 0.55 * 10.0**-decimals)


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

    monkeypatch.chdir(tmp_path)
    r = run_stats_eval(Path("out"), write_config=True)  # a relative report directory
    assert r["all_pass"] and r["config_written"] and r["n_checks"] > 100
    md, js = Path(r["report"]), Path(r["json"])
    assert md.name == f"stats-{r['date']}.md" and js.name == f"stats-{r['date']}.json"
    assert md.is_absolute() and md == (tmp_path / "out" / md.name).resolve()
    assert json.loads(js.read_text())["stats_version"] == STATS_VERSION
    text = md.read_text()
    assert "10.10.4.4" in text and "10.10.4.3" in text
    assert load_settings().stats_validated == {"version": STATS_VERSION, "report": str(md)}

    home2 = tmp_path / "home2"
    home2.mkdir()
    monkeypatch.setenv("RK_HOME", str(home2))
    bad = tmp_path / "bad-refs"
    shutil.copytree(REFS, bad)
    with pytest.raises(ValueError):  # only the packaged references can validate, even an identical copy cannot
        run_stats_eval(tmp_path / "out2", refs_dir=bad, write_config=True)
    ref = json.loads((bad / "bcg.json").read_text())
    exp = ref["analyses"][0]["expected"][0]
    exp["value"] = f"{float(exp['value']) + 0.01:.4f}"  # a published estimate no longer reproduced
    (bad / "bcg.json").write_text(json.dumps(ref))
    r2 = run_stats_eval(tmp_path / "out2", refs_dir=bad)  # other references: a report, never a validation
    assert not r2["all_pass"] and not r2["config_written"]
    assert "FAIL" in Path(r2["report"]).read_text()
    assert load_settings().stats_validated is None

    import rk.eval.stats_eval as se

    monkeypatch.setattr(se, "check_tolerance", lambda exp: 0.0)  # the packaged references, now failing
    r3 = run_stats_eval(tmp_path / "out3", write_config=True)
    assert not r3["all_pass"] and not r3["config_written"]
    assert load_settings().stats_validated is None
