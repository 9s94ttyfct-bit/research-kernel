"""Inverse-variance meta-analysis of one set of effects: fixed effect (``FE``) or random effects with τ² from
DerSimonian-Laird (``DL``) or restricted maximum likelihood (``REML``), Wald or Hartung-Knapp-Sidik-Jonkman (HKSJ)
confidence intervals, Cochran's Q, I² and the prediction interval.

With ``w_i = 1/(v_i + τ²)`` (τ² = 0 for FE) the summary is ``μ = Σw_i·y_i / Σw_i`` with ``Var(μ) = 1/Σw_i``.

* Q = Σ (y_i - μ_FE)²/v_i on k - 1 df, p from the χ² distribution.
* DL (DerSimonian & Laird 1986): ``τ² = max(0, (Q - (k-1)) / (Σw - Σw²/Σw))`` with FE weights.
* REML: Fisher scoring (Viechtbauer 2005, Stat Med 24:3681) from the DL value,
  ``τ² ← max(0, τ² + (yᵀPPy - tr P) / tr(PP))`` with ``P = W - W11ᵀW/Σw``; it stops when a step changes τ² by less
  than ``REML_TOL`` (1e-10) and raises ``ConvergenceError`` after ``REML_MAX_ITER`` (100) steps.
* HKSJ (Hartung & Knapp 2001; Sidik & Jonkman 2002; random-effects models only): ``SE² = q/Σw`` with
  ``q = Σw_i(y_i - μ)²/(k-1)``, not truncated at 1, and a t(k-1) quantile; otherwise the Wald ``SE² = 1/Σw`` with a
  normal quantile. ``Pooled.se`` is the SE the interval uses.
* I²: FE ``max(0, (Q - (k-1))/Q)`` (Higgins & Thompson 2002); random effects ``τ²/(τ² + s²)`` with the typical
  within-study variance ``s² = (k-1)Σw/((Σw)² - Σw²)`` (FE weights), which equals the Q form under DL and is the
  formula RevMan uses (Cochrane Handbook v6.5.1, §10.10.4.4). Reported as a proportion.
* Prediction interval (random effects, k ≥ 3): ``μ ± t_{k-2, 1-α/2} · √(τ² + 1/Σw)`` — Higgins, Thompson &
  Spiegelhalter (2009), J R Stat Soc A 172:137, eq. (12), with the Wald variance of μ whether or not HKSJ is on.
  (The Cochrane Handbook v6.5.1 §10.10.4.3 prints the same interval with t_{k-1}.)"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

from scipy import stats as sps

from rk.stats.effects import Effect

Model = Literal["FE", "DL", "REML"]
REML_TOL = 1e-10
REML_MAX_ITER = 100


class ConvergenceError(RuntimeError):
    """REML did not converge; no pooled result is given."""


@dataclass(frozen=True)
class Pooled:
    k: int
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    tau2: float
    i2: float
    q: float
    q_df: int
    q_p: float
    pi_low: float | None
    pi_high: float | None
    model: str
    hksj: bool

    def as_dict(self) -> dict:
        return asdict(self)


def pool(effects: list[Effect], *, model: Model = "REML", hksj: bool = True, level: float = 0.95) -> Pooled:
    """Pool ``effects`` (at least two). ``hksj`` applies to the random-effects models only; with ``FE`` the interval
    is the Wald interval and ``Pooled.hksj`` is False. See the module docstring for every formula."""
    if model not in ("FE", "DL", "REML"):
        raise ValueError(f"unknown model {model!r}; expected FE, DL or REML")
    if not 0 < level < 1:
        raise ValueError("level must be between 0 and 1")
    k = len(effects)
    if k < 2:
        raise ValueError("pooling needs at least two effects")
    y = [float(e.yi) for e in effects]
    v = [float(e.vi) for e in effects]
    if not all(math.isfinite(a) for a in y) or not all(math.isfinite(a) and a > 0 for a in v):
        raise ValueError("every effect needs a finite estimate and a positive finite variance")

    df = k - 1
    w = [1 / a for a in v]
    sw, sw2 = math.fsum(w), math.fsum(a * a for a in w)
    mu_fe = _mean(y, w)
    q = math.fsum(wi * (yi - mu_fe) ** 2 for wi, yi in zip(w, y, strict=True))
    q_p = float(sps.chi2.sf(q, df))

    if model == "FE":
        tau2 = 0.0
        i2 = max(0.0, (q - df) / q) if q > 0 else 0.0
    else:
        tau2 = max(0.0, (q - df) / (sw - sw2 / sw))
        if model == "REML":
            tau2 = _reml(y, v, tau2)
        typical = df * sw / (sw * sw - sw2)
        i2 = tau2 / (tau2 + typical)

    ws = [1 / (a + tau2) for a in v]
    sws = math.fsum(ws)
    mu = _mean(y, ws)
    se_wald = math.sqrt(1 / sws)
    alpha = 1 - level
    use_hksj = hksj and model != "FE"
    if use_hksj:
        qs = math.fsum(wi * (yi - mu) ** 2 for wi, yi in zip(ws, y, strict=True)) / df
        se = math.sqrt(qs / sws)
        crit = float(sps.t.ppf(1 - alpha / 2, df))
    else:
        se = se_wald
        crit = float(sps.norm.ppf(1 - alpha / 2))
    pi_low = pi_high = None
    if model != "FE" and k >= 3:
        half = float(sps.t.ppf(1 - alpha / 2, k - 2)) * math.sqrt(tau2 + se_wald * se_wald)
        pi_low, pi_high = mu - half, mu + half
    return Pooled(k=k, estimate=mu, se=se, ci_low=mu - crit * se, ci_high=mu + crit * se, tau2=tau2, i2=i2, q=q,
                  q_df=df, q_p=q_p, pi_low=pi_low, pi_high=pi_high, model=model, hksj=use_hksj)


def _mean(y: list[float], w: list[float]) -> float:
    return math.fsum(wi * yi for wi, yi in zip(w, y, strict=True)) / math.fsum(w)


def _reml(y: list[float], v: list[float], tau2: float) -> float:
    """REML τ² by Fisher scoring from ``tau2``; with ``P = W - ww^T/Σw`` the score is ``(yᵀPPy - tr P)/2`` and the
    expected information ``tr(PP)/2``, where ``Py = w·(y - μ)``, ``tr P = Σw - Σw²/Σw`` and
    ``tr(PP) = Σw² - 2Σw³/Σw + (Σw²/Σw)²``."""
    for _ in range(REML_MAX_ITER):
        w = [1 / (a + tau2) for a in v]
        sw, sw2, sw3 = math.fsum(w), math.fsum(a * a for a in w), math.fsum(a**3 for a in w)
        mu = _mean(y, w)
        ypy = math.fsum((wi * (yi - mu)) ** 2 for wi, yi in zip(w, y, strict=True))
        tr_p = sw - sw2 / sw
        tr_pp = sw2 - 2 * sw3 / sw + (sw2 / sw) ** 2
        new = max(0.0, tau2 + (ypy - tr_p) / tr_pp)
        if abs(new - tau2) < REML_TOL:
            return new
        tau2 = new
    raise ConvergenceError(f"REML did not converge within {REML_MAX_ITER} Fisher scoring steps")
