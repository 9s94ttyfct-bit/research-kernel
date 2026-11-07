"""``rk eval stats``: reproduce published meta-analyses with ``rk.stats`` (spec §9.1 V-STATS gate).

Each reference dataset in ``rk/stats/reference/*.json`` (package data) holds the study data and the published results
of the cited source (table, page or program output per analysis), each value as printed, digits intact; nothing in
them was computed by this code. The evaluation recomputes every statistic, compares it with the published value and
writes ``docs/eval/stats-<date>.{md,json}``. With ``write_config`` and only when every check passes, it records
``{"version": STATS_VERSION, "report": <absolute md path>}`` as ``Settings.stats_validated``, which
``rk.stats.stats_enabled`` requires before any pooling. No network, no model.

Tolerance per check: half a unit of the last printed digit plus 10% (``0.55·10^-d``), capped at the plan's tolerance
(1e-3 on estimates, SEs, τ², I² as a proportion and Q; 1e-2 on interval bounds and ratio-scale ``exp_*`` values). A
check may state a wider ``tolerance`` only with a ``tolerance_note`` and never above the plan's cap."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from rk.config import load_settings, save_settings
from rk.stats import REFERENCE_DIR, STATS_VERSION
from rk.stats.effects import Effect, Uncomputable, compute
from rk.stats.meta import pool

REFS_DIR = REFERENCE_DIR
BOUND_STATS = frozenset({"ci_low", "ci_high", "pi_low", "pi_high", "exp_estimate", "exp_ci_low", "exp_ci_high",
                         "exp_pi_low", "exp_pi_high", "exp_yi"})
_Z95 = 1.959963984540054

INTRO = """Every published value is copied from the cited source with its printed digits; none was produced by this code.
Where the source is printed program output — metafor in Viechtbauer (2010), on the metafor website and in its test
suite, Stata `metan` as recorded in metafor's test suite, metafor reproducing Borenstein et al. (2009) — the reference
is that program's rounded output, not a number typeset in the original study or book; each source line below says
which. Study intervals are 95% Wald intervals; pooled analyses use 95% intervals."""

HANDBOOK_NOTE = """## Defaults against the Cochrane Handbook

Checked on 2026-11-07 against the *Cochrane Handbook for Systematic Reviews of Interventions*, Version 6.5.1 (2026),
Chapter 10 "Analysing data and undertaking meta-analyses" (Deeks, Higgins, Altman, McKenzie, Veroniki, Simmonds, eds.;
chapter last updated November 2026), https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-10.
`rk.stats.meta.pool` defaults: `model="REML"`, `hksj="auto"`, `pi_df="ci"`.

- **τ² estimator: REML.** §10.10.4.4: REML is RevMan's default between-study variance estimator, DerSimonian-Laird
  remains an option, and a comprehensive simulation study recommended REML (Langan et al. 2019). Validated below on
  BCG (Viechtbauer 2010) and Raudenbush (2009).
- **Confidence interval: HKSJ only when τ² > 0 and k > 2, else Wald-normal.** §10.10.4.4: RevMan's default interval
  is Wald-type and users are prompted to use HKSJ when the estimated τ² > 0 and there are more than two studies,
  because HKSJ can be overly narrow when τ² is estimated as 0 and overly wide with two studies (§10.10.4.5).
  `hksj="auto"` applies that rule; `True`/`False` force it. HKSJ uses t(k−1) and q·Var(μ) without truncation; the
  Raudenbush (2009) quasi-F (Knapp-Hartung) results validate it, with `auto` resolving to HKSJ there.
- **Prediction interval: the CI's SE and multiplier family.** §10.10.4.3 gives M ± t(k−1)·√(τ² + SE(M)²) and
  §10.10.4.4 says RevMan uses the CI's multiplier (t(k−1) with HKSJ, z with Wald), so the PI equals the CI when
  τ² = 0. `rk.stats` uses μ ± t(k−1)·√(τ² + q·Var(μ)) with HKSJ and μ ± z·√(τ² + Var(μ)) with Wald. The Wald form
  is validated on BCG (metafor's interval, Viechtbauer 2010 p. 21) and Raudenbush (metafor's test suite). No
  published HKSJ prediction interval was found, so the HKSJ form is checked by a formula test only
  (`tests/test_stats.py`); metafor documents the same rule for `test="knha"` (`predict.rma`: a t(k−p) quantile).
  `pi_df="k-2"` gives Higgins, Thompson & Spiegelhalter (2009, eq. 12): t(k−2) with the Wald variance; the
  Borenstein et al. (2009) intervals validate it.
- **I².** §10.10.4.4: RevMan computes I² = τ²/(τ² + s²) with the typical within-study variance s², which `rk.stats`
  uses for random-effects models (equal to (Q − df)/Q under DerSimonian-Laird); a fixed-effect model reports
  (Q − df)/Q.
"""


def plan_tolerance(statistic: str) -> float:
    return 1e-2 if statistic in BOUND_STATS else 1e-3


def check_tolerance(exp: dict) -> float:
    """The tolerance of one check: a stated ``tolerance``, else half a unit of the printed last digit plus 10%,
    capped at the plan's tolerance."""
    if "tolerance" in exp:
        return float(exp["tolerance"])
    decimals = len(str(exp["value"]).partition(".")[2])
    return min(plan_tolerance(exp["statistic"]), 0.55 * 10.0**-decimals)


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
    tol = check_tolerance(exp)
    published = float(exp["value"])
    diff = abs(computed - published) if computed is not None else None
    problems = [note] if note else []
    if tol > plan_tolerance(exp["statistic"]):
        problems.append("tolerance is looser than the plan's")
    if "tolerance" in exp and not exp.get("tolerance_note"):
        problems.append("stated tolerance without a reason")
    ok = diff is not None and diff <= tol and not problems
    return {"dataset": ref["id"], "measure": block["measure"], **ctx, "statistic": exp["statistic"],
            "published": exp["value"], "computed": computed, "diff": diff, "tolerance": tol, "pass": ok,
            "source": block["source"], "note": "; ".join(problems), "tolerance_note": exp.get("tolerance_note", "")}


def run_reference(ref: dict) -> list[dict]:
    """Every check of one reference dataset: per-study effects, then each published analysis."""
    studies = {s["label"]: s for s in ref["studies"]}
    rows: list[dict] = []
    for block in ref.get("study_checks", []):
        for exp in block["expected"]:
            e = _effect(studies[exp["study"]], block["measure"])
            ctx = {"model": "-", "hksj": False, "pi_df": "-", "ci": "Wald z (study)", "study": exp["study"]}
            if isinstance(e, Uncomputable):
                rows.append(_row(ref, block, exp, None, f"uncomputable: {e.reason}", **ctx))
            else:
                rows.append(_row(ref, block, exp, _study_values(e)[exp["statistic"]], **ctx))
    for block in ref.get("analyses", []):
        chosen = [s for s in ref["studies"] if s.get("group") == block.get("group")]
        effects = [_effect(s, block["measure"]) for s in chosen]
        hksj, pi_df = block["hksj"], block.get("pi_df", "ci")
        ctx = {"model": block["model"], "hksj": hksj, "pi_df": pi_df, "study": f"pooled (k = {len(chosen)})"}
        bad = [s["label"] for s, e in zip(chosen, effects, strict=True) if isinstance(e, Uncomputable)]
        if bad:
            rows += [_row(ref, block, exp, None, "uncomputable: " + ", ".join(bad), ci="-", **ctx)
                     for exp in block["expected"]]
            continue
        p = pool(effects, model=block["model"], hksj=hksj, pi_df=pi_df)
        ci = ("HKSJ t(k-1)" if p.hksj else "Wald z") + (" (auto)" if hksj == "auto" else "")
        values = _pooled_values(p)
        rows += [_row(ref, block, exp, values[exp["statistic"]], ci=ci, **ctx) for exp in block["expected"]]
    return rows


def _fmt(x: float | None, digits: int = 8) -> str:
    return "—" if x is None else f"{x:.{digits}g}"


def _markdown(result: dict, refs: list[dict]) -> str:
    n_fail = result["n_checks"] - result["n_pass"]
    status = "**all checks pass**" if result["all_pass"] else f"**{n_fail} check(s) FAIL**"
    lines = [f"# Statistics module validation — {result['date']}", "",
             (f"`rk.stats` version `{result['stats_version']}`: {result['n_pass']}/{result['n_checks']} published "
              f"values reproduced within tolerance; {status}. Each tolerance is half a unit of the last printed digit "
              "plus 10%, capped at the plan's (1e-3 on estimates, SEs, τ², I² as a proportion and Q; 1e-2 on "
              "interval bounds and ratio-scale exp_* values); a wider one is stated with its reason."),
             "", INTRO, "", HANDBOOK_NOTE]
    for ref in refs:
        rows = [r for r in result["rows"] if r["dataset"] == ref["id"]]
        lines += [f"## {ref['title']}", "", f"Data: {ref['data_source']}", ""]
        if ref.get("notes"):
            lines += [f"Notes: {ref['notes']}", ""]
        sources: dict[str, int] = {}
        for r in rows:
            sources.setdefault(r["source"], len(sources) + 1)
        lines += [f"- [{i}] {s}" for s, i in sources.items()] + [""]
        lines += [("| measure | model | CI | PI | study | statistic | published | computed | abs diff | tolerance | "
                   "result | source |"), "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            res = "pass" if r["pass"] else f"FAIL{(' (' + r['note'] + ')') if r['note'] else ''}"
            tol = f"{r['tolerance']:g}" + (f" ({r['tolerance_note']})" if r["tolerance_note"] else "")
            lines.append(f"| {r['measure']} | {r['model']} | {r['ci']} | {r['pi_df']} | {r['study']} | "
                         f"{r['statistic']} | {r['published']} | {_fmt(r['computed'])} | {_fmt(r['diff'], 3)} | "
                         f"{tol} | {res} | [{sources[r['source']]}] |")
        lines.append("")
    return "\n".join(lines)


def run_stats_eval(report_dir: Path = Path("docs/eval"), *, refs_dir: Path = REFS_DIR,
                   write_config: bool = False) -> dict:
    """Run every reference dataset in ``refs_dir`` and write the report. ``write_config`` is allowed only with the
    packaged references (``REFERENCE_DIR``, covered by ``STATS_VERSION``): other references give a report, never a
    validation."""
    if write_config and Path(refs_dir).resolve() != REFERENCE_DIR.resolve():
        raise ValueError(f"write_config validates only against the packaged references in {REFERENCE_DIR}, "
                         f"not {refs_dir}")
    paths = sorted(Path(refs_dir).glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no reference datasets in {refs_dir}")
    refs = [json.loads(p.read_text()) for p in paths]
    rows = [r for ref in refs for r in run_reference(ref)]
    n_pass = sum(r["pass"] for r in rows)
    result = {"date": datetime.now(UTC).date().isoformat(), "stats_version": STATS_VERSION,
              "datasets": [{"id": r["id"], "title": r["title"], "data_source": r["data_source"]} for r in refs],
              "n_checks": len(rows), "n_pass": n_pass, "all_pass": bool(rows) and n_pass == len(rows), "rows": rows}
    report_dir = report_dir.resolve()
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
