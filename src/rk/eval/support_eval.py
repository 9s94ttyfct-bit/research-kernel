"""``rk eval support``: V-SUPPORT's judges on held-out SciFact dev pairs (spec §9.1 as amended 2026-11-07).

Every judge, as its own role (``JUDGE_ROLES``), gives one opinion on every pair of a seeded dev sample. From those
opinions the verdicts are computed as production would for each generator scenario (by default ``anthropic``, the
default writer and the headline, then ``openai`` and ``human``): ``admit`` decides which judges may judge that
generator's claims, a judge that is not admitted counts as no opinion, ``combine`` needs at least ``min_judges`` valid
judges that all give the same label, and V-NUM gates "supported". The report gives, per scenario, the verdict × gold
matrix, coverage and the precision of each verdict, plus per-judge failures and pairwise agreement. Nothing is
calibrated.

Successful opinions are cached under ``$RK_HOME/eval`` keyed by driver and a fingerprint of the judge's prompt, output
schema, claim and evidence, so a rerun (or a resumed run) pays only for missing or failed calls, and a changed prompt
asks again. The judge calls count against the real ``$RK_HOME`` hourly call caps; a capped driver stops the evaluation
with ``RateCapped`` (nothing is recorded as a failure). Runs on a scratch Kernel, so it never touches a project
database that ``rk serve`` owns."""

from __future__ import annotations

import hashlib
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
from rk.discovery.executor import RateCapped
from rk.eval.data import SupportPair, eval_dir, load_scifact
from rk.eval.metrics import VERDICT_LABELS, confusion, md_matrix, support_metrics
from rk.eval.scratch import eval_provider, eval_task, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.roles.prompts import build_system_prompt
from rk.verify import judge
from rk.verify.judge import JudgeOpinion, JudgeVerdict, admit, ask_judge, role_judges
from rk.verify.numbers import check_numbers
from rk.verify.support import combine

GOLD = ["supported", "contradicted", "unsupported"]
PRED = ["supported", "contradicted", "unsupported", "uncertain"]
TARGET_PRECISION, TARGET_COVERAGE = 0.95, 0.70
GENERATORS = ("anthropic", "openai", "human")  # the first is the headline: the default writer's vendor
CACHE_NAME = "support-judge-opinions.jsonl"  # rows keyed by fingerprint; the old unkeyed judges-dev.jsonl is never read


def prompt_fingerprint(role: str) -> str:
    """SHA-256 of what a judge in ``role`` is told: its system prompt, the task instructions and the output schema."""
    schema = json.dumps(JudgeVerdict.model_json_schema(), sort_keys=True)
    text = "\n\x00\n".join([build_system_prompt(role, JudgeVerdict), judge.INSTRUCTIONS, schema])
    return hashlib.sha256(text.encode()).hexdigest()


def _key(driver: DriverSpec, prompt_fp: str, pair: SupportPair) -> str:
    return hashlib.sha256(json.dumps([str(driver), prompt_fp, pair.claim, pair.evidence]).encode()).hexdigest()


def judge_opinions(pairs: list[SupportPair], judges: list[tuple[str, DriverSpec]], *, cache: Path,
                   providers_factory: Callable[[], dict], workers: int = 4) -> dict[str, list[JudgeOpinion]]:
    """Every judge's opinion, as its own role, on every pair (admission is applied later, per scenario). Successful
    opinions are cached by fingerprint; failed calls are retried on the next run. A rate-capped call is neither a
    failure nor cached: once the pool is done, ``RateCapped`` names the capped driver and when a call frees up."""
    fps = {role: prompt_fingerprint(role) for role, _ in judges}
    keys = {(p.id, role): _key(spec, fps[role], p) for p in pairs for role, spec in judges}
    done: dict[str, JudgeOpinion] = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            r = json.loads(line)
            done[r["key"]] = JudgeOpinion.model_validate(r["opinion"])
    todo = [(p, role, spec) for p in pairs for role, spec in judges if keys[(p.id, role)] not in done]
    failed: dict[str, JudgeOpinion] = {}
    capped: dict[str, float] = {}
    if todo:
        k, reg = scratch_kernel("eval-support", "V-SUPPORT evaluation")
        local = threading.local()
        write_lock = threading.Lock()

        def run(**task):
            return eval_task(k, reg, project="eval-support", **task)

        def one(item: tuple[SupportPair, str, DriverSpec]) -> None:
            pair, role, spec = item
            if not hasattr(local, "providers"):
                local.providers = providers_factory()  # one provider (and gateway process) per worker thread
            key = keys[(pair.id, role)]
            try:
                op = ask_judge(run, role, spec, claim_text=pair.claim, evidence_text=pair.evidence,
                               provider=local.providers.get(str(spec)))
            except RateCapped as e:  # the other judges' calls still finish (and are cached) before the stop
                with write_lock:
                    capped[e.driver] = e.wait_s
                return
            with write_lock:
                if op.error is None:
                    done[key] = op
                    with cache.open("a") as f:
                        f.write(json.dumps({"key": key, "id": pair.id, "driver": str(spec), "role": role,
                                            "prompt_fingerprint": fps[role], "opinion": op.model_dump()}) + "\n")
                else:
                    failed[key] = op

        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(one, todo))
        finally:
            k.close()
        if capped:
            driver, wait = max(capped.items(), key=lambda kv: kv[1])
            raise RateCapped(driver, wait)
    return {p.id: [done.get(keys[(p.id, role)]) or failed[keys[(p.id, role)]] for role, _ in judges] for p in pairs}


def scenario_verdicts(pairs: list[SupportPair], judges: list[tuple[str, DriverSpec]],
                      ops: dict[str, list[JudgeOpinion]], *, generator: str, min_judges: int) -> tuple[list[str], dict]:
    """Verdicts as production gives them for claims written by ``generator``: a judge ``admit`` skips counts as no
    opinion. Also returns the admitted drivers and the skipped ones with the reason."""
    _, skipped = admit(judges, generator)
    roles = [role for role, _ in judges]
    pred = []
    for pair in pairs:
        opinions = [o.model_copy(update={"label": "uncertain", "error": skipped[role]}) if role in skipped else o
                    for role, o in zip(roles, ops[pair.id], strict=True)]
        v = combine(opinions, min_judges=min_judges)
        pred.append("uncertain" if v == "supported" and not check_numbers(pair.claim, [pair.evidence]).ok else v)
    return pred, {"admitted": [str(s) for r, s in judges if r not in skipped],
                  "not_admitted": {str(s): skipped[r] for r, s in judges if r in skipped}}


def judge_agreement(pairs: list[SupportPair], judges: list[tuple[str, DriverSpec]],
                    ops: dict[str, list[JudgeOpinion]]) -> tuple[list[dict], list[dict]]:
    """Per judge: valid opinions, failed calls, and valid labels equal to gold. Per pair of judges, over the pairs
    where both gave a valid opinion: how many share a verdict label (supported/unsupported/contradicted), and how many
    share a non-verdict label (partial, or uncertain after unquoted spans)."""
    per_judge = []
    for i, (role, spec) in enumerate(judges):
        valid = [(p, ops[p.id][i]) for p in pairs if ops[p.id][i].error is None]
        per_judge.append({"role": role, "driver": str(spec), "prompt_fingerprint": prompt_fingerprint(role),
                          "valid": len(valid), "failed": len(pairs) - len(valid),
                          "agrees_with_gold": sum(o.label == p.label for p, o in valid)})
    pairwise = []
    for (a, (_, ja)), (b, (_, jb)) in combinations(enumerate(judges), 2):
        both = [(ops[p.id][a].label, ops[p.id][b].label) for p in pairs
                if ops[p.id][a].error is None and ops[p.id][b].error is None]
        pairwise.append({"a": str(ja), "b": str(jb), "both_valid": len(both),
                         "same_verdict_label": sum(x == y and x in VERDICT_LABELS for x, y in both),
                         "same_other": sum(x == y and x not in VERDICT_LABELS for x, y in both)})
    return per_judge, pairwise


def run_support_eval(*, judges: list[DriverSpec] | None, min_judges: int, limit: int | None = 100, workers: int = 4,
                     report_dir: Path = Path("docs/eval"), loader: Callable = load_scifact,
                     judge_providers: dict | None = None, seed: int = 13,
                     generators: tuple[str, ...] = GENERATORS) -> dict:
    """``judges``: explicit drivers (the i-th as ``JUDGE_ROLES[i]``), else the configured judges. ``limit``: size of the
    seeded dev sample (``None``: every dev pair). ``judge_providers`` (driver string → Provider) replaces the drivers
    (tests). Raises ``RateCapped`` when a judge's driver is at its hourly call cap."""
    settings = load_settings()
    roles = role_judges(settings, judges)

    def factory() -> dict:
        if judge_providers is not None:
            return judge_providers
        return {str(s): eval_provider(s, settings, tools=False) for _, s in roles}

    dev = loader()
    sample = dev if limit is None else random.Random(seed).sample(dev, min(limit, len(dev)))
    ops = judge_opinions(sample, roles, cache=eval_dir() / CACHE_NAME, providers_factory=factory, workers=workers)
    gold = [p.label for p in sample]
    scenarios, verdicts = [], {}
    for g in generators:
        pred, admission = scenario_verdicts(sample, roles, ops, generator=g, min_judges=min_judges)
        verdicts[g] = pred
        scenarios.append({"generator": g, **admission, **support_metrics(gold, pred),
                          "confusion": confusion(gold, pred, GOLD, PRED)})
    per_judge, pairwise = judge_agreement(sample, roles, ops)
    result: dict = {
        "date": datetime.now(UTC).date().isoformat(), "rk_version": __version__,
        "dev": {"split": "dev", "pairs": len(dev), "sample": len(sample), "seed": None if limit is None else seed,
                "judges": [str(s) for _, s in roles], "min_judges": min_judges,
                "failed_judge_calls": sum(j["failed"] for j in per_judge)},
        "scenarios": scenarios, "judges": per_judge, "pairwise_agreement": pairwise,
        "items": [{"id": p.id, "gold": p.label,
                   "labels": [o.label if o.error is None else f"failed: {o.error}" for o in ops[p.id]],
                   "verdicts": {g: verdicts[g][i] for g in generators}} for i, p in enumerate(sample)],
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


def _scenario(s: dict, headline: bool) -> list[str]:
    skipped = ", ".join(f"`{d}` ({why})" for d, why in s["not_admitted"].items()) or "none"
    return [f"## Generator `{s['generator']}`{' (headline)' if headline else ''}", "",
            f"- Admitted: {', '.join(f'`{d}`' for d in s['admitted']) or 'none'}; not admitted: {skipped}.",
            f"- {_target(s)}",
            (f"- precision by verdict: "
             f"{', '.join(f'{lab} {_fmt(v)}' for lab, v in s['precision_by_label'].items())}; negative verdicts "
             f"(unsupported/contradicted): {s['predicted_negative']}, not gold-supported share "
             f"{_fmt(s['precision_negative'])}."), "",
            md_matrix(s["confusion"]), ""]


def render_report(r: dict) -> str:
    d = r["dev"]
    sample = (f"all {d['pairs']} pairs" if d["seed"] is None
              else f"a seeded sample of {d['sample']} of {d['pairs']} pairs (seed {d['seed']})")
    out = [f"# V-SUPPORT evaluation, judges only — {r['date']}", "",
           (f"rk {r['rk_version']}. Data: SciFact dev (claims CC BY 4.0, abstracts ODC-By), {sample}. Judges: "
            f"{', '.join(f'`{x}`' for x in d['judges'])}, each asked once as its own role. Per generator scenario, "
            f"only the judges admitted for that generator's vendor count, and a verdict needs at least "
            f"{d['min_judges']} valid admitted judges that all give the same label; V-NUM gates \"supported\". "
            "Nothing is calibrated."), "",
           f"Failed judge calls: {d['failed_judge_calls']} (counted as no opinion; not admitted is listed apart).", ""]
    for i, s in enumerate(r["scenarios"]):
        out += _scenario(s, headline=i == 0)
    out += ["## Judges", "",
            "| role | judge | valid opinions | failed calls | label equals gold | prompt fingerprint |",
            "|---|---|---|---|---|---|"]
    out += [f"| {j['role']} | `{j['driver']}` | {j['valid']} | {j['failed']} | {j['agrees_with_gold']} | "
            f"`{j['prompt_fingerprint'][:12]}` |" for j in r["judges"]]
    out += ["", "Pairwise agreement (pairs where both judges gave a valid opinion):", "",
            "| judges | both valid | same verdict label | same partial/uncertain |", "|---|---|---|---|"]
    out += [f"| `{a['a']}` · `{a['b']}` | {a['both_valid']} | {a['same_verdict_label']} | {a['same_other']} |"
            for a in r["pairwise_agreement"]]
    return "\n".join(out) + "\n"
