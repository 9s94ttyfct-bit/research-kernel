"""``rk eval support``: calibrate V-SUPPORT thresholds on SciFact train, report on held-out SciFact dev.

Layer 1 (MiniCheck) is evaluated on every dev pair; the full two-layer verdict (same ``combine`` and V-NUM gate as
``verify_support``) on a seeded sample, because judges cost model calls. Runs on a scratch Kernel in a temporary
directory, so it never touches a project database that ``rk serve`` owns."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rk import __version__
from rk.config import load_settings, rk_home, save_settings
from rk.eval.data import SupportPair, eval_dir, load_scifact
from rk.eval.metrics import (
    calibrate_thresholds,
    confusion,
    layer1_predict,
    md_matrix,
    support_metrics,
)
from rk.eval.scratch import eval_provider, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.verify.minicheck import get_minicheck
from rk.verify.numbers import check_numbers
from rk.verify.support import combine

GOLD = ["supported", "contradicted", "unsupported"]
PRED = ["supported", "contradicted", "unsupported", "uncertain"]
TARGET_PRECISION, TARGET_COVERAGE = 0.95, 0.70


def layer1_scores(pairs: list[SupportPair], split: str, layer1, *, cache: Path | None = None) -> dict[str, float]:
    """MiniCheck p per pair id, cached on disk (scoring all of SciFact takes minutes)."""
    cache = cache or eval_dir() / f"scores-{layer1.model_id.replace('/', '_')}-{split}.jsonl"
    done: dict[str, float] = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r["p"]
    todo = [p for p in pairs if p.id not in done]
    for i in range(0, len(todo), 32):
        batch = todo[i:i + 32]
        scores = layer1.score_batch([(p.evidence, p.claim) for p in batch])
        with cache.open("a") as f:
            for p, s in zip(batch, scores, strict=True):
                done[p.id] = s.p
                f.write(json.dumps({"id": p.id, "p": s.p}) + "\n")
    return {p.id: done[p.id] for p in pairs}



def run_support_eval(*, judges: list[DriverSpec], min_judges: int, limit: int = 100, seed: int = 13,
                     layer1_only: bool = False, write_config: bool = False, report_dir: Path = Path("docs/eval"),
                     loader: Callable = load_scifact, layer1=None, providers: dict | None = None,
                     score_cache_dir: Path | None = None) -> dict:
    from rk.verify.judge import run_judges

    settings = load_settings()
    layer1 = layer1 or get_minicheck(rk_home() / "models", settings.support_model)
    calib = loader("train")
    dev = loader("dev")
    cache = (lambda s: score_cache_dir / f"scores-{s}.jsonl") if score_cache_dir else (lambda s: None)
    p_cal = layer1_scores(calib, "train", layer1, cache=cache("train"))
    tau = calibrate_thresholds([p_cal[p.id] for p in calib], [p.label for p in calib],
                               target_precision=TARGET_PRECISION)
    p_dev = layer1_scores(dev, "dev", layer1, cache=cache("dev"))
    l1_pred = [layer1_predict(p_dev[p.id], tau["tau_hi"], tau["tau_lo"]) for p in dev]
    gold = [p.label for p in dev]
    result: dict = {
        "date": datetime.now(UTC).date().isoformat(), "rk_version": __version__, "model": layer1.model_id,
        "calibration": {"split": "train", **tau},
        "layer1": {"split": "dev", **support_metrics(gold, l1_pred), "confusion": confusion(gold, l1_pred, GOLD, PRED)},
    }
    if not layer1_only:
        sample = random.Random(seed).sample(dev, min(limit, len(dev)))
        providers = providers or {str(j): eval_provider(j, settings) for j in judges}
        k, reg = scratch_kernel("eval-support", "V-SUPPORT evaluation")
        preds = []
        try:
            for pair in sample:
                ops = run_judges(k, reg, project="eval-support", claim_text=pair.claim, evidence_text=pair.evidence,
                                 generator_vendor="human", drivers=judges, providers=providers)
                v = combine(p_dev[pair.id], ops, tau_hi=tau["tau_hi"], tau_lo=tau["tau_lo"], min_judges=min_judges)
                if v == "supported" and not check_numbers(pair.claim, [pair.evidence]).ok:
                    v = "uncertain"
                preds.append(v)
        finally:
            k.close()
        sgold = [p.label for p in sample]
        result["two_layer"] = {"split": "dev", "sample": len(sample), "seed": seed, "judges": [str(j) for j in judges],
                               "min_judges": min_judges, **support_metrics(sgold, preds),
                               "confusion": confusion(sgold, preds, GOLD, PRED)}
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"support-{result['date']}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=1))
    stem.with_suffix(".md").write_text(render_report(result))
    result["report"] = str(stem.with_suffix(".md"))
    if write_config:
        s = load_settings()
        s.support_tau_hi, s.support_tau_lo = tau["tau_hi"], tau["tau_lo"]
        s.support_calibration = {**tau, "dataset": "SciFact", "split": "train", "model": layer1.model_id,
                                 "rk_version": __version__, "date": result["date"],
                                 "dev_precision_supported": result["layer1"]["precision_supported"],
                                 "dev_coverage": result["layer1"]["coverage"]}
        save_settings(s)
    return result


def _target(m: dict) -> str:
    prec, cov = m["precision_supported"], m["coverage"]
    p_ok = prec is not None and prec >= TARGET_PRECISION
    return (f"precision(supported) {'n/a' if prec is None else f'{prec:.3f}'} → {'MET' if p_ok else 'MISSED'} "
            f"(target ≥ {TARGET_PRECISION}); coverage {cov:.3f} → {'MET' if cov >= TARGET_COVERAGE else 'MISSED'} "
            f"(target ≥ {TARGET_COVERAGE})")


def render_report(r: dict) -> str:
    c = r["calibration"]
    out = [f"# V-SUPPORT evaluation — {r['date']}", "",
           (f"rk {r['rk_version']}; layer 1 `{r['model']}`. Data: SciFact (claims CC BY 4.0, abstracts ODC-By); "
           "τ calibrated on train, metrics on held-out dev. Aggregate numbers only."), "",
           "## Calibration (SciFact train)", "",
           (f"- τ_hi = {c['tau_hi']:.4f} (target precision {c['target_precision']} "
           f"{'reached' if c['hi_reached'] else 'NOT reachable → nothing is ever supported by layer 1 alone'})"),
           f"- τ_lo = {c['tau_lo']:.4f} ({'reached' if c['lo_reached'] else 'not reached'}); n = {c['n']}", "",
           "## Layer 1 only (SciFact dev, all pairs)", "", _target(r["layer1"]), "", md_matrix(r["layer1"]["confusion"])]
    if "two_layer" in r:
        t = r["two_layer"]
        out += ["", f"## Two layers (SciFact dev, seeded sample of {t['sample']}, seed {t['seed']})", "",
                f"Judges: {', '.join(f'`{j}`' for j in t['judges'])}; min_judges = {t['min_judges']}"
                + (" (test configuration: the product default is 2)" if t["min_judges"] < 2 else ""), "",
                _target(t), "", md_matrix(t["confusion"])]
    return "\n".join(out) + "\n"
