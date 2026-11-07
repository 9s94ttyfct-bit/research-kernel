"""``rk eval stats``: reproduce published meta-analyses with ``rk.stats`` (spec §9.1 V-STATS gate).

Each reference dataset in ``tests/fixtures/stats/*.json`` holds the study data and the published results copied from
the cited source (table or page per analysis), each with its tolerance; nothing in them was computed by this code. The
evaluation recomputes every statistic, compares it with the published value and writes
``docs/eval/stats-<date>.{md,json}``. With ``write_config`` and only when every check passes, it records
``{"version": STATS_VERSION, "report": <md path>}`` as ``Settings.stats_validated``, which ``rk.stats.stats_enabled``
requires before any pooling. No network, no model."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from rk.config import load_settings, save_settings
from rk.stats import STATS_VERSION
from rk.stats.effects import Effect, Uncomputable, compute
from rk.stats.meta import pool

REFS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stats"
# the plan's statistical tolerance: 1e-3 absolute on estimates, SEs, tau², I² (as a proportion) and Q; 1e-2 on
# interval bounds as reported (ratio-scale exp_* values included). A fixture may be stricter, never looser.
BOUND_STATS = frozenset({"ci_low", "ci_high", "pi_low", "pi_high", "exp_estimate", "exp_ci_low", "exp_ci_high",
                         "exp_pi_low", "exp_pi_high", "exp_yi"})
_Z95 = 1.959963984540054

HANDBOOK_NOTE = """## Default estimator against the Cochrane Handbook

Checked on 2026-11-07 against the *Cochrane Handbook for Systematic Reviews of Interventions*, Version 6.5.1 (2026),
Chapter 10 "Analysing data and undertaking meta-analyses" (Deeks, Higgins, Altman, McKenzie, Veroniki, Simmonds, eds.;
chapter last updated November 2026), https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-10.
The plan (Deviation 5) cites "v6.5"; the current version is 6.5.1.

- **τ² estimator — agrees.** §10.10.4.4: REML is RevMan's default between-study variance estimator, DerSimonian-Laird
  remains an option; a comprehensive simulation study recommended REML (Langan et al. 2019). `rk.stats` defaults to
  REML.
- **HKSJ — the plan goes further than the Handbook.** §10.10.4.4: RevMan's *default* interval is the Wald-type one;
  users are *prompted* to use HKSJ when the estimated τ² > 0 and there are more than two studies, because HKSJ can be
  overly narrow when τ² is estimated as 0 and overly wide with few studies (§10.10.4.5). Deviation 5 makes HKSJ the
  unconditional default; that default is kept here as decided and flagged.
- **Prediction interval — differs.** §10.10.4.3 gives M ± t(k-1) × √(τ² + SE(M)²), and §10.10.4.4 says RevMan uses the
  confidence interval's multiplier (t(k-1) with HKSJ, z with Wald). `rk.stats` uses t(k-2) (Higgins, Thompson &
  Spiegelhalter 2009, eq. 12; Riley et al. 2011), as the plan specifies; the Borenstein et al. (2009) intervals
  below validate that formula.
- **I² — agrees.** §10.10.4.4: RevMan computes I² = τ²/(τ² + s²) with the typical within-study variance s², which
  `rk.stats` uses for random-effects models (equal to (Q − df)/Q under DerSimonian-Laird); a fixed-effect model
  reports (Q − df)/Q.
"""


def plan_tolerance(statistic: str) -> float:
    return 1e-2 if statistic in BOUND_STATS else 1e-3


def study_data(study: dict) -> dict:
    """A reference study as ``effects.compute`` input: arm totals are given, or are events + nonevents."""
    d = dict(study)
    for arm in ("treatment", "control"):
        if f"n_{arm}" not in d and f"nonevents_{arm}" in d:
            d[f"n_{arm}"] = d[f"events_{arm}"] + d[f"nonevents_{arm}"]
    return d


def _effect(study: dict, measure: str) -> Effect | Uncomputable:
    if "yi" in study:
        return Effect(float(study["yi"]), float(study["vi"]))
    return compute(measure, study_data(study))


def _study_values(e: Effect) -> dict[str, float]:
    half = _Z95 * math.sqrt(e.vi)
    return {"yi": e.yi, "vi": e.vi, "exp_yi": math.exp(e.yi), "exp_ci_low": math.exp(e.yi - half),
            "exp_ci_high": math.exp(e.yi + half)}


def _pooled_values(p) -> dict[str, float | None]:
    v = {s: getattr(p, s) for s in ("estimate", "se", "ci_low", "ci_high", "tau2", "i2", "q", "pi_low", "pi_high")}
    for s in ("estimate", "ci_low", "ci_high", "pi_low", "pi_high"):
        v[f"exp_{s}"] = math.exp(v[s]) if v[s] is not None else None
    return v


def _row(ref: dict, block: dict, exp: dict, computed: float | None, note: str = "", **ctx) -> dict:
    tol = float(exp["tolerance"])
    diff = abs(computed - exp["value"]) if computed is not None else None
    if tol > plan_tolerance(exp["statistic"]):
        note = (note + "; " if note else "") + "fixture tolerance is looser than the plan's"
    ok = diff is not None and diff <= tol and not note
    return {"dataset": ref["id"], "measure": block["measure"], **ctx, "statistic": exp["statistic"],
            "published": exp["value"], "computed": computed, "diff": diff, "tolerance": tol, "pass": ok,
            "source": block["source"], "note": note}


def run_reference(ref: dict) -> list[dict]:
    """Every check of one reference dataset: per-study effects, then each published analysis."""
    studies = {s["label"]: s for s in ref["studies"]}
    rows: list[dict] = []
    for block in ref.get("study_checks", []):
        for exp in block["expected"]:
            e = _effect(studies[exp["study"]], block["measure"])
            ctx = {"model": "-", "hksj": False, "study": exp["study"]}
            if isinstance(e, Uncomputable):
                rows.append(_row(ref, block, exp, None, f"uncomputable: {e.reason}", **ctx))
            else:
                rows.append(_row(ref, block, exp, _study_values(e)[exp["statistic"]], **ctx))
    for block in ref.get("analyses", []):
        chosen = [s for s in ref["studies"] if s.get("group") == block.get("group")]
        effects = [_effect(s, block["measure"]) for s in chosen]
        ctx = {"model": block["model"], "hksj": block["hksj"], "study": f"pooled (k = {len(chosen)})"}
        bad = [s["label"] for s, e in zip(chosen, effects, strict=True) if isinstance(e, Uncomputable)]
        if bad:
            rows += [_row(ref, block, exp, None, "uncomputable: " + ", ".join(bad), **ctx) for exp in block["expected"]]
            continue
        values = _pooled_values(pool(effects, model=block["model"], hksj=block["hksj"]))
        rows += [_row(ref, block, exp, values[exp["statistic"]], **ctx) for exp in block["expected"]]
    return rows


def _fmt(x: float | None, digits: int = 8) -> str:
    return "—" if x is None else f"{x:.{digits}g}"


def _markdown(result: dict, refs: list[dict]) -> str:
    n_fail = result["n_checks"] - result["n_pass"]
    status = "**all checks pass**" if result["all_pass"] else f"**{n_fail} check(s) FAIL**"
    lines = [f"# Statistics module validation — {result['date']}", "",
             (f"`rk.stats` version `{result['stats_version']}`: {result['n_pass']}/{result['n_checks']} published "
              f"values reproduced within tolerance; {status}. Tolerances follow the plan: 1e-3 absolute on estimates, "
              "SEs, τ², I² (as a proportion) and Q; 1e-2 on interval bounds as reported (exp_* values are on the "
              "ratio scale as printed). Every published value was copied from the cited source; none was produced by "
              "this code. Study intervals are 95% Wald intervals; pooled analyses use 95% intervals."),
             "", HANDBOOK_NOTE]
    for ref in refs:
        rows = [r for r in result["rows"] if r["dataset"] == ref["id"]]
        lines += [f"## {ref['title']}", "", f"Data: {ref['data_source']}", ""]
        if ref.get("notes"):
            lines += [f"Notes: {ref['notes']}", ""]
        sources: dict[str, int] = {}
        for r in rows:
            sources.setdefault(r["source"], len(sources) + 1)
        lines += [f"- [{i}] {s}" for s, i in sources.items()] + [""]
        lines += [("| measure | model | HKSJ | study | statistic | published | computed | abs diff | tolerance | "
                   "result | source |"), "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            res = "pass" if r["pass"] else f"FAIL{(' (' + r['note'] + ')') if r['note'] else ''}"
            lines.append(f"| {r['measure']} | {r['model']} | {'yes' if r['hksj'] else 'no'} | {r['study']} | "
                         f"{r['statistic']} | {r['published']} | {_fmt(r['computed'])} | {_fmt(r['diff'], 3)} | "
                         f"{r['tolerance']:g} | {res} | [{sources[r['source']]}] |")
        lines.append("")
    return "\n".join(lines)


def run_stats_eval(report_dir: Path = Path("docs/eval"), *, refs_dir: Path = REFS_DIR,
                   write_config: bool = False) -> dict:
    paths = sorted(refs_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no reference datasets in {refs_dir} (run from a checkout of the repository)")
    refs = [json.loads(p.read_text()) for p in paths]
    rows = [r for ref in refs for r in run_reference(ref)]
    n_pass = sum(r["pass"] for r in rows)
    result = {"date": datetime.now(UTC).date().isoformat(), "stats_version": STATS_VERSION,
              "datasets": [{"id": r["id"], "title": r["title"], "data_source": r["data_source"]} for r in refs],
              "n_checks": len(rows), "n_pass": n_pass, "all_pass": bool(rows) and n_pass == len(rows), "rows": rows}
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"stats-{result['date']}"
    md, js = stem.with_suffix(".md"), stem.with_suffix(".json")
    js.write_text(json.dumps(result, indent=1) + "\n")
    md.write_text(_markdown(result, refs))
    written = False
    if write_config and result["all_pass"]:
        s = load_settings()
        s.stats_validated = {"version": STATS_VERSION, "report": str(md)}
        save_settings(s)
        written = True
    return {**result, "report": str(md), "json": str(js), "config_written": written}
