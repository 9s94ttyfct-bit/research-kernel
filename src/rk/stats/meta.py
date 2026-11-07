"""Inverse-variance meta-analysis of one set of effects: fixed effect (``FE``) or random effects with τ² from
DerSimonian-Laird (``DL``) or restricted maximum likelihood (``REML``), Wald or Hartung-Knapp-Sidik-Jonkman (HKSJ)
confidence intervals, Cochran's Q, I² and the prediction interval. The defaults follow the Cochrane Handbook v6.5.1,
chapter 10 (§10.10.4.3-10.10.4.4) and RevMan: REML, HKSJ when τ² > 0 and k > 2, and a prediction interval on the
confidence interval's footing.

With ``w_i = 1/(v_i + τ²)`` (τ² = 0 for FE) the summary is ``μ = Σw_i·y_i / Σw_i`` with ``Var(μ) = 1/Σw_i``.

* Q = Σ (y_i - μ_FE)²/v_i on k - 1 df, p from the χ² distribution.
* DL (DerSimonian & Laird 1986): ``τ² = max(0, (Q - (k-1)) / (Σw - Σw²/Σw))`` with FE weights.
* REML: Fisher scoring from the DL value (Viechtbauer 2005, Stat Med 24:3681),
  ``τ² ← τ² + (yᵀPPy - tr P) / tr(PP)`` with ``P = W - W11ᵀW/Σw``. A step that would leave τ² < 0 ends at the boundary
  τ² = 0 when the score there is not positive (the boundary maximum); otherwise it is halved until τ² stays ≥ 0. It
  stops when a step changes τ² by less than ``REML_TOL`` (1e-10). If ``REML_MAX_ITER`` (100) steps do not converge,
  the REML score is solved directly by Brent's method on [0, U]: the score is positive at 0 (else τ² = 0) and negative at
  ``U = 2·max(max v_i, 4·S/(k-1))`` with ``S = Σ(y_i - ȳ)²``. Proof of the bound: ``yᵀPPy = Σw_i²(y_i-μ)² ≤
  w_max·Σw_i(y_i-ȳ)² ≤ S/τ⁴`` and ``tr P = Σ_{i≠j} w_i w_j / Σw ≥ (k-1)·τ²/(τ² + v_max)²``; for τ² ≥ v_max the latter
  is ≥ (k-1)/(4τ²), which exceeds S/τ⁴ once τ² > 4S/(k-1). A failed solve raises ``ConvergenceError``.
* CI: with HKSJ (Hartung & Knapp 2001; Sidik & Jonkman 2002) ``SE² = q·Var(μ)``, ``q = Σw_i(y_i - μ)²/(k-1)`` (not
  truncated at 1), and a t(k-1) quantile; otherwise the Wald ``SE² = Var(μ)`` with a normal quantile. ``hksj="auto"``
  (default) uses HKSJ only for a random-effects model with τ² > 0 and k > 2 (Handbook §10.10.4.4); ``True``/``False``
  force it on/off for random-effects models. A fixed-effect model always uses Wald. ``Pooled.se`` is the CI's SE.
* I²: FE ``max(0, (Q - (k-1))/Q)`` (Higgins & Thompson 2002); random effects ``τ²/(τ² + s²)`` with the typical
  within-study variance ``s² = (k-1)Σw/((Σw)² - Σw²)`` (FE weights), which equals the Q form under DL and is the
  formula RevMan uses (§10.10.4.4). Reported as a proportion.
* Prediction interval (random effects, k ≥ 3). ``pi_df="ci"`` (default; RevMan, §10.10.4.3-10.10.4.4): the CI's SE
  and multiplier family, ``μ ± t_{k-1}·√(τ² + q·Var(μ))`` with HKSJ, ``μ ± z·√(τ² + Var(μ))`` with Wald, so the PI
  equals the CI when τ² = 0. ``pi_df="k-2"``: ``μ ± t_{k-2}·√(τ² + Var(μ))`` with the Wald variance whatever the CI
  (Higgins, Thompson & Spiegelhalter 2009, J R Stat Soc A 172:137, eq. 12; Riley et al. 2011).

Non-finite inputs, weights or sums raise ``ValueError``."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

from scipy import optimize
from scipy import stats as sps

from rk.stats.effects import Effect

Model = Literal["FE", "DL", "REML"]
REML_TOL = 1e-10
REML_MAX_ITER = 100
REML_BRENT_MAXITER = 200


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
    hksj: bool  # whether the CI (and a "ci" PI) used HKSJ
    pi_df: str = "ci"

    def as_dict(self) -> dict:
        return asdict(self)


def _finite(*xs: float) -> None:
    if not all(math.isfinite(x) for x in xs):
        raise ValueError("non-finite weight or sum: the effects' estimates or variances are out of range")


def pool(effects: list[Effect], *, model: Model = "REML", hksj: bool | Literal["auto"] = "auto",
         pi_df: Literal["ci", "k-2"] = "ci", level: float = 0.95) -> Pooled:
    """Pool ``effects`` (at least two). See the module docstring for every formula and default."""
    if model not in ("FE", "DL", "REML"):
        raise ValueError(f"unknown model {model!r}; expected FE, DL or REML")
    if hksj not in (True, False, "auto") or isinstance(hksj, int) and not isinstance(hksj, bool):
        raise ValueError(f"hksj must be True, False or 'auto', not {hksj!r}")
    if pi_df not in ("ci", "k-2"):
        raise ValueError(f"pi_df must be 'ci' or 'k-2', not {pi_df!r}")
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
    _finite(sw, sw2)
    mu_fe = _mean(y, w)
    q = math.fsum(wi * (yi - mu_fe) * (yi - mu_fe) for wi, yi in zip(w, y, strict=True))
    _finite(mu_fe, q)
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
        _finite(tau2, typical)

    ws = [1 / (a + tau2) for a in v]
    sws = math.fsum(ws)
    mu = _mean(y, ws)
    var_mu = 1 / sws
    _finite(mu, var_mu)
    alpha = 1 - level
    use_hksj = model != "FE" and (hksj is True or (hksj == "auto" and tau2 > 0 and k > 2))
    if use_hksj:
        qs = math.fsum(wi * (yi - mu) * (yi - mu) for wi, yi in zip(ws, y, strict=True)) / df
        _finite(qs)
        se = math.sqrt(qs * var_mu)
        crit = float(sps.t.ppf(1 - alpha / 2, df))
    else:
        se = math.sqrt(var_mu)
        crit = float(sps.norm.ppf(1 - alpha / 2))
    pi_low = pi_high = None
    if model != "FE" and k >= 3:
        if pi_df == "ci":
            half = crit * math.sqrt(tau2 + se * se)
        else:
            half = float(sps.t.ppf(1 - alpha / 2, k - 2)) * math.sqrt(tau2 + var_mu)
        pi_low, pi_high = mu - half, mu + half
    return Pooled(k=k, estimate=mu, se=se, ci_low=mu - crit * se, ci_high=mu + crit * se, tau2=tau2, i2=i2, q=q,
                  q_df=df, q_p=q_p, pi_low=pi_low, pi_high=pi_high, model=model, hksj=use_hksj, pi_df=pi_df)


def _mean(y: list[float], w: list[float]) -> float:
    return math.fsum(wi * yi for wi, yi in zip(w, y, strict=True)) / math.fsum(w)


def _score_parts(y: list[float], v: list[float], tau2: float) -> tuple[float, float, float]:
    """``(yᵀPPy, tr P, tr PP)`` at ``tau2``, with ``Py = w·(y - μ)``, ``tr P = Σw - Σw²/Σw`` and
    ``tr(PP) = Σw² - 2Σw³/Σw + (Σw²/Σw)²``."""
    w = [1 / (a + tau2) for a in v]
    sw, sw2, sw3 = math.fsum(w), math.fsum(a * a for a in w), math.fsum(a * a * a for a in w)
    mu = _mean(y, w)
    ypy = math.fsum((wi * (yi - mu)) * (wi * (yi - mu)) for wi, yi in zip(w, y, strict=True))
    return ypy, sw - sw2 / sw, sw2 - 2 * sw3 / sw + (sw2 / sw) ** 2


def reml_score(y: list[float], v: list[float], tau2: float) -> float:
    """The derivative of the restricted log-likelihood in τ²: ``(yᵀPPy - tr P)/2``."""
    ypy, tr_p, _ = _score_parts(y, v, tau2)
    return (ypy - tr_p) / 2


def _reml(y: list[float], v: list[float], tau2: float) -> float:
    for _ in range(REML_MAX_ITER):
        ypy, tr_p, tr_pp = _score_parts(y, v, tau2)
        _finite(ypy, tr_p, tr_pp)
        step = (ypy - tr_p) / tr_pp
        new = tau2 + step
        if new < 0:
            if tau2 == 0 or reml_score(y, v, 0.0) <= 0:
                return 0.0  # the step points below 0 and the score is not positive there: the maximum is τ² = 0
            while new < 0:  # step-halving keeps τ² inside the parameter space
                step /= 2
                new = tau2 + step
        if abs(new - tau2) < REML_TOL:
            return new
        tau2 = new
    return _reml_brent(y, v)


def _reml_brent(y: list[float], v: list[float]) -> float:
    if reml_score(y, v, 0.0) <= 0:
        return 0.0
    k = len(y)
    ybar = math.fsum(y) / k
    s = math.fsum((a - ybar) * (a - ybar) for a in y)
    upper = 2 * max(max(v), 4 * s / (k - 1))
    if not reml_score(y, v, upper) < 0:
        raise ConvergenceError("REML: the score does not change sign on [0, U]")
    root, r = optimize.brentq(lambda t: reml_score(y, v, t), 0.0, upper, xtol=REML_TOL,
                              maxiter=REML_BRENT_MAXITER, full_output=True, disp=False)
    if not r.converged:
        raise ConvergenceError(f"REML did not converge: Fisher scoring ({REML_MAX_ITER} steps) and Brent's method "
                               f"({REML_BRENT_MAXITER} iterations) both failed")
    return root
