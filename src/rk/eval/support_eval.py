"""``rk eval support``: calibrate V-SUPPORT on SciFact train, report on held-out SciFact dev.

1. Layer 1 (MiniCheck) on every train and dev pair → layer-1-only thresholds τ_hi/τ_lo (train).
2. With judges: their opinions on a seeded train sample → joint thresholds τ_hi_judged/τ_lo_judged (spec §9.1 as
   amended 2026-11-06: used when ≥2 cross-vendor judges agree).
3. On a seeded dev sample (held out): the old rule (layer-1-only thresholds) and the amended rule, from the same judge
   opinions, plus judges alone as a diagnostic. V-NUM gates "supported" everywhere, as in ``verify_support``.

Judge opinions and layer-1 scores are cached under ``$RK_HOME/eval`` so reruns cost no model calls. Runs on a scratch
Kernel, so it never touches a project database that ``rk serve`` owns."""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from rk import __version__
from rk.config import load_settings, rk_home, save_settings
from rk.eval.data import SupportPair, eval_dir, load_scifact
from rk.eval.metrics import (
    calibrate_joint,
    calibrate_thresholds,
    confusion,
    layer1_predict,
    md_matrix,
    support_metrics,
)
from rk.eval.scratch import eval_provider, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.verify.judge import JudgeOpinion, run_judges
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


def judge_opinions(pairs: list[SupportPair], judges: list[DriverSpec], *, cache: Path,
                   providers_factory: Callable[[], dict], workers: int = 4) -> dict[str, list[JudgeOpinion]]:
    """Opinions of every judge on every pair (claims authored by "human", so all judges are eligible). Successful
    opinions are cached per (pair, judge); failed calls are retried on the next run instead of being frozen."""
    done: dict[tuple[str, str], JudgeOpinion] = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            r = json.loads(line)
            done[(r["id"], r["driver"])] = JudgeOpinion.model_validate(r["opinion"])
    todo = [(p, j) for p in pairs for j in judges if (p.id, str(j)) not in done]
    failed: dict[tuple[str, str], JudgeOpinion] = {}
    if todo:
        k, reg = scratch_kernel("eval-support", "V-SUPPORT evaluation")
        local = threading.local()
        write_lock = threading.Lock()

        def one(item: tuple[SupportPair, DriverSpec]) -> None:
            pair, judge = item
            if not hasattr(local, "providers"):
                local.providers = providers_factory()  # one provider (and gateway process) per worker thread
            (op,) = run_judges(k, reg, project="eval-support", claim_text=pair.claim, evidence_text=pair.evidence,
                               generator_vendor="human", drivers=[judge], providers=local.providers)
            with write_lock:
                if op.error is None:
                    done[(pair.id, str(judge))] = op
                    with cache.open("a") as f:
                        f.write(json.dumps({"id": pair.id, "driver": str(judge), "opinion": op.model_dump()}) + "\n")
                else:
                    failed[(pair.id, str(judge))] = op

        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(one, todo))
        finally:
            k.close()
    return {p.id: [done.get((p.id, str(j))) or failed[(p.id, str(j))] for j in judges] for p in pairs}


def _verdicts(pairs: list[SupportPair], p: dict[str, float], ops: dict[str, list[JudgeOpinion]], *,
              min_judges: int, tau_hi: float, tau_lo: float, tau_hi_judged: float | None = None,
              tau_lo_judged: float | None = None) -> list[str]:
    out = []
    for pair in pairs:
        v = combine(p[pair.id], ops[pair.id], tau_hi=tau_hi, tau_lo=tau_lo, min_judges=min_judges,
                    tau_hi_judged=tau_hi_judged, tau_lo_judged=tau_lo_judged)
        if v == "supported" and not check_numbers(pair.claim, [pair.evidence]).ok:
            v = "uncertain"
        out.append(v)
    return out


def _block(pairs: list[SupportPair], pred: list[str], **extra) -> dict:
    gold = [p.label for p in pairs]
    return {**extra, **support_metrics(gold, pred), "confusion": confusion(gold, pred, GOLD, PRED)}


def run_support_eval(*, judges: list[DriverSpec], min_judges: int, limit: int = 100, calib_limit: int = 200,
                     seed: int = 13, layer1_only: bool = False, write_config: bool = False,
                     report_dir: Path = Path("docs/eval"), loader: Callable = load_scifact, layer1=None,
                     providers: dict | None = None, cache_dir: Path | None = None, workers: int = 4) -> dict:
    settings = load_settings()
    layer1 = layer1 or get_minicheck(rk_home() / "models", settings.support_model)
    cache_dir = cache_dir or eval_dir()
    model_tag = layer1.model_id.replace("/", "_")
    train, dev = loader("train"), loader("dev")
    p_train = layer1_scores(train, "train", layer1, cache=cache_dir / f"scores-{model_tag}-train.jsonl")
    p_dev = layer1_scores(dev, "dev", layer1, cache=cache_dir / f"scores-{model_tag}-dev.jsonl")
    tau = calibrate_thresholds([p_train[p.id] for p in train], [p.label for p in train],
                               target_precision=TARGET_PRECISION)
    l1_pred = [layer1_predict(p_dev[p.id], tau["tau_hi"], tau["tau_lo"]) for p in dev]
    result: dict = {
        "date": datetime.now(UTC).date().isoformat(), "rk_version": __version__, "model": layer1.model_id,
        "calibration": {"split": "train", **tau},
        "layer1": _block(dev, l1_pred, split="dev"),
    }
    joint = None
    if not layer1_only:
        def factory() -> dict:
            return providers if providers is not None else {str(j): eval_provider(j, settings) for j in judges}

        calib_sample = random.Random(seed).sample(train, min(calib_limit, len(train)))
        dev_sample = random.Random(seed).sample(dev, min(limit, len(dev)))
        ops_train = judge_opinions(calib_sample, judges, cache=cache_dir / "judges-train.jsonl",
                                   providers_factory=factory, workers=workers)
        ops_dev = judge_opinions(dev_sample, judges, cache=cache_dir / "judges-dev.jsonl", providers_factory=factory,
                                 workers=workers)
        rows = [(p_train[p.id], [o.label for o in ops_train[p.id] if o.error is None], p.label) for p in calib_sample]
        joint = calibrate_joint(rows, target_precision=TARGET_PRECISION)
        judged = {"tau_hi_judged": joint["tau_hi_judged"], "tau_lo_judged": joint["tau_lo_judged"]}
        common = {"min_judges": min_judges, "tau_hi": tau["tau_hi"], "tau_lo": tau["tau_lo"]}
        failures = sum(o.error is not None for ops in (*ops_train.values(), *ops_dev.values()) for o in ops)
        result["joint_calibration"] = {"split": "train", "sample": len(calib_sample), "seed": seed, **joint,
                                       "train_amended": _block(calib_sample, _verdicts(
                                           calib_sample, p_train, ops_train, **common, **judged))}
        result["dev_sample"] = {
            "split": "dev", "sample": len(dev_sample), "seed": seed, "judges": [str(j) for j in judges],
            "min_judges": min_judges, "failed_judge_calls": failures,
            "old_rule": _block(dev_sample, _verdicts(dev_sample, p_dev, ops_dev, **common)),
            "amended_rule": _block(dev_sample, _verdicts(dev_sample, p_dev, ops_dev, **common, **judged)),
            # diagnostic only, not a product rule: judges alone (layer 1 ignored)
            "judges_only": _block(dev_sample, _verdicts(dev_sample, p_dev, ops_dev, min_judges=min_judges,
                                                        tau_hi=0.0, tau_lo=1.0)),
        }
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"support-{result['date']}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=1))
    stem.with_suffix(".md").write_text(render_report(result))
    result["report"] = str(stem.with_suffix(".md"))
    if write_config:
        s = load_settings()
        s.support_tau_hi, s.support_tau_lo = tau["tau_hi"], tau["tau_lo"]
        record = {**tau, "dataset": "SciFact", "split": "train", "model": layer1.model_id, "rk_version": __version__,
                  "date": result["date"], "dev_precision_supported": result["layer1"]["precision_supported"],
                  "dev_coverage": result["layer1"]["coverage"]}
        if joint is not None:
            s.support_tau_hi_judged, s.support_tau_lo_judged = joint["tau_hi_judged"], joint["tau_lo_judged"]
            amended = result["dev_sample"]["amended_rule"]
            record["joint"] = {k: joint[k] for k in ("tau_hi_judged", "tau_lo_judged", "n", "n_agree_supported",
                                                       "n_agree_negative")}
            record["joint"].update(judges=[str(j) for j in judges], min_judges=min_judges,
                                   dev_precision_supported=amended["precision_supported"],
                                   dev_coverage=amended["coverage"], dev_sample=result["dev_sample"]["sample"])
        s.support_calibration = record
        save_settings(s)
    return result


def _fmt(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _target(m: dict) -> str:
    prec, cov = m["precision_supported"], m["coverage"]
    p_ok = prec is not None and prec >= TARGET_PRECISION
    return (f"precision(supported) {_fmt(prec)} ({m['predicted_supported']} predicted supported) → "
            f"{'MET' if p_ok else 'MISSED'} (target ≥ {TARGET_PRECISION}); coverage {cov:.3f} → "
            f"{'MET' if cov >= TARGET_COVERAGE else 'MISSED'} (target ≥ {TARGET_COVERAGE})")


def render_report(r: dict) -> str:
    c = r["calibration"]
    out = [f"# V-SUPPORT evaluation — {r['date']}", "",
           (f"rk {r['rk_version']}; layer 1 `{r['model']}`. Data: SciFact (claims CC BY 4.0, abstracts ODC-By); "
            "thresholds calibrated on train, metrics on held-out dev."), "",
           "## Layer-1-only calibration (SciFact train, all pairs)", "",
           (f"- τ_hi = {c['tau_hi']:.4f} (target precision {c['target_precision']} "
            f"{'reached' if c['hi_reached'] else 'NOT reachable → nothing is ever supported by layer 1 alone'})"),
           f"- τ_lo = {c['tau_lo']:.4f} ({'reached' if c['lo_reached'] else 'not reached'}); n = {c['n']}", "",
           "## Layer 1 only (SciFact dev, all pairs)", "", _target(r["layer1"]), "", md_matrix(r["layer1"]["confusion"])]
    if "joint_calibration" in r:
        j, d = r["joint_calibration"], r["dev_sample"]
        out += ["", f"## Joint calibration (SciFact train, seeded sample of {j['sample']}, seed {j['seed']})", "",
                f"Judges: {', '.join(f'`{x}`' for x in d['judges'])}; min_judges = {d['min_judges']}.", "",
                (f"- Pairs where ≥2 judges agree on \"supported\": {j['n_agree_supported']} "
                f"(gold-supported share {_fmt(j['agree_supported_precision'])})"),
                (f"- Pairs where ≥2 judges agree on unsupported/contradicted: {j['n_agree_negative']} "
                f"(not-gold-supported share {_fmt(j['agree_negative_precision'])})"),
                (f"- τ_hi_judged = {_fmt(j['tau_hi_judged'], 4)}; τ_lo_judged = {_fmt(j['tau_lo_judged'], 4)} "
                "(a value of n/a would mean the target was not reachable; the layer-1-only threshold then applies)"), "",
                "In-sample (train) result of the amended rule — optimistic by construction:", "",
                _target(j["train_amended"]), "",
                f"## Held-out comparison (SciFact dev, seeded sample of {d['sample']}, seed {d['seed']})", "",
                f"Failed judge calls: {d['failed_judge_calls']} (counted as no opinion).", "",
                "### Old rule (layer-1-only thresholds + judges)", "", _target(d["old_rule"]), "",
                md_matrix(d["old_rule"]["confusion"]), "",
                "### Amended rule (§9.1, 2026-11-06)", "", _target(d["amended_rule"]), "",
                md_matrix(d["amended_rule"]["confusion"]), "",
                "### Diagnostic: judges alone (layer 1 ignored; not a product rule)", "", _target(d["judges_only"]),
                "", md_matrix(d["judges_only"]["confusion"])]
    return "\n".join(out) + "\n"
