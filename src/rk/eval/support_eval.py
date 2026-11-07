"""``rk eval support``: V-SUPPORT's judges on held-out SciFact dev pairs (spec §9.1 as amended 2026-11-07).

Every judge gives an opinion on every pair of a seeded dev sample (claims authored by "human", so all judges are
eligible). The verdict is ``combine`` (at least ``min_judges`` valid judges, all with the same label), and V-NUM gates
"supported", as in ``verify_support``. The report gives the verdict × gold matrix, coverage, the precision of
"supported" and of the negative verdicts, and per-judge failure and agreement counts. Nothing is calibrated.

Successful judge opinions are cached under ``$RK_HOME/eval``, so a rerun (or a resumed, interrupted run) pays only for
missing or failed calls. Runs on a scratch Kernel, so it never touches a project database that ``rk serve`` owns."""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from rk import __version__
from rk.config import load_settings
from rk.eval.data import SupportPair, eval_dir, load_scifact
from rk.eval.metrics import confusion, md_matrix, support_metrics
from rk.eval.scratch import eval_provider, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.verify.judge import JudgeOpinion, run_judges
from rk.verify.numbers import check_numbers
from rk.verify.support import combine

GOLD = ["supported", "contradicted", "unsupported"]
PRED = ["supported", "contradicted", "unsupported", "uncertain"]
TARGET_PRECISION, TARGET_COVERAGE = 0.95, 0.70


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


def verdict(pair: SupportPair, opinions: list[JudgeOpinion], *, min_judges: int) -> str:
    v = combine(opinions, min_judges=min_judges)
    return "uncertain" if v == "supported" and not check_numbers(pair.claim, [pair.evidence]).ok else v


def judge_agreement(pairs: list[SupportPair], judges: list[DriverSpec], ops: dict[str, list[JudgeOpinion]]
                    ) -> tuple[list[dict], list[dict]]:
    """Per judge: valid opinions, failed calls, and valid labels equal to gold. Per pair of judges: pairs where both
    gave a valid opinion, and how many of those carry the same label."""
    per_judge = []
    for i, j in enumerate(judges):
        valid = [(p, ops[p.id][i]) for p in pairs if ops[p.id][i].error is None]
        per_judge.append({"driver": str(j), "valid": len(valid), "failed": len(pairs) - len(valid),
                          "agrees_with_gold": sum(o.label == p.label for p, o in valid)})
    pairwise = []
    for (a, ja), (b, jb) in combinations(enumerate(judges), 2):
        both = [(ops[p.id][a], ops[p.id][b]) for p in pairs if ops[p.id][a].error is None and ops[p.id][b].error is None]
        pairwise.append({"a": str(ja), "b": str(jb), "both_valid": len(both),
                         "same_label": sum(x.label == y.label for x, y in both)})
    return per_judge, pairwise


def run_support_eval(*, judges: list[DriverSpec], min_judges: int, limit: int | None = 100, workers: int = 4,
                     report_dir: Path = Path("docs/eval"), loader: Callable = load_scifact,
                     judge_providers: dict | None = None, seed: int = 13) -> dict:
    """``limit``: size of the seeded dev sample (``None``: every dev pair). ``judge_providers`` (driver string →
    Provider) replaces the configured drivers (tests)."""
    settings = load_settings()

    def factory() -> dict:
        if judge_providers is not None:
            return judge_providers
        return {str(j): eval_provider(j, settings, tools=False) for j in judges}

    dev = loader("dev")
    sample = dev if limit is None else random.Random(seed).sample(dev, min(limit, len(dev)))
    ops = judge_opinions(sample, judges, cache=eval_dir() / "judges-dev.jsonl", providers_factory=factory,
                         workers=workers)
    pred = [verdict(p, ops[p.id], min_judges=min_judges) for p in sample]
    gold = [p.label for p in sample]
    per_judge, pairwise = judge_agreement(sample, judges, ops)
    result: dict = {
        "date": datetime.now(UTC).date().isoformat(), "rk_version": __version__,
        "dev": {"split": "dev", "pairs": len(dev), "sample": len(sample), "seed": None if limit is None else seed,
                "judges": [str(j) for j in judges], "min_judges": min_judges,
                "failed_judge_calls": sum(j["failed"] for j in per_judge),
                **support_metrics(gold, pred), "confusion": confusion(gold, pred, GOLD, PRED)},
        "judges": per_judge, "pairwise_agreement": pairwise,
        "items": [{"id": p.id, "gold": p.label, "verdict": v,
                   "labels": [o.label if o.error is None else f"failed: {o.error}" for o in ops[p.id]]}
                  for p, v in zip(sample, pred, strict=True)],
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"support-{result['date']}-judges"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=1))
    stem.with_suffix(".md").write_text(render_report(result))
    result["report"] = str(stem.with_suffix(".md"))
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
    d = r["dev"]
    sample = (f"all {d['pairs']} pairs" if d["seed"] is None
              else f"a seeded sample of {d['sample']} of {d['pairs']} pairs (seed {d['seed']})")
    out = [f"# V-SUPPORT evaluation, judges only — {r['date']}", "",
           (f"rk {r['rk_version']}. Data: SciFact dev (claims CC BY 4.0, abstracts ODC-By), {sample}. Judges: "
            f"{', '.join(f'`{x}`' for x in d['judges'])}. A verdict needs at least {d['min_judges']} valid judges "
            "that all give the same label; V-NUM gates \"supported\". Nothing is calibrated."), "",
           "## Verdicts", "", _target(d), "",
           (f"- Negative verdicts (unsupported/contradicted): {d['predicted_negative']}, of which a share of "
            f"{_fmt(d['precision_negative'])} is not gold-supported."),
           f"- Failed judge calls: {d['failed_judge_calls']} (counted as no opinion).", "",
           md_matrix(d["confusion"]), "",
           "## Judges", "",
           "| judge | valid opinions | failed calls | label equals gold |", "|---|---|---|---|"]
    out += [f"| `{j['driver']}` | {j['valid']} | {j['failed']} | {j['agrees_with_gold']} |" for j in r["judges"]]
    out += ["", "Pairwise agreement (pairs where both judges gave a valid opinion):", "",
            "| judges | both valid | same label |", "|---|---|---|"]
    out += [f"| `{a['a']}` · `{a['b']}` | {a['both_valid']} | {a['same_label']} |" for a in r["pairwise_agreement"]]
    return "\n".join(out) + "\n"
