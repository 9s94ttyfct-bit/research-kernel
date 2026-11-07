"""Per-study effect sizes and their sampling variances, from arm-level data only.

Field names are those of the extraction schema: ``events_treatment``, ``n_treatment``, ``events_control``,
``n_control`` for binary outcomes and ``mean_*``, ``sd_*``, ``n_*`` (``*`` = ``treatment`` | ``control``) for
continuous ones. A study that lacks a field, or whose data define no effect, is ``Uncomputable`` with the reason;
nothing is ever imputed (no means from medians, no SDs from ranges or p-values).

Formulas (Borenstein, Hedges, Higgins & Rothstein 2009, *Introduction to Meta-Analysis*, chapters 4-5):

* ``RR``: log risk ratio ``log((a/n1)/(c/n2))``, variance ``1/a - 1/n1 + 1/c - 1/n2``.
* ``OR``: log odds ratio ``log(a·d/(b·c))``, variance ``1/a + 1/b + 1/c + 1/d``.
* ``RD``: risk difference ``a/n1 - c/n2``, variance ``p1(1-p1)/n1 + p2(1-p2)/n2``.
* ``MD``: ``m1 - m2``, variance ``s1²/n1 + s2²/n2`` (no equal-variance assumption).
* ``SMD``: Hedges' g = J·d with d = (m1 - m2)/s_pooled and the exact small-sample correction
  J = Γ(df/2) / (√(df/2)·Γ((df-1)/2)), df = n1 + n2 - 2 (Hedges 1981); variance J²·((n1+n2)/(n1·n2) + d²/(2(n1+n2)))
  (Borenstein et al. eqs. 4.20 and 4.24).

RR and OR add 0.5 to every cell of a study only when one of its cells is 0. A study without events in either arm, or
with events in every participant of both arms, carries no information on a ratio or difference and is
Uncomputable."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Measure = Literal["RR", "OR", "RD", "MD", "SMD"]
BINARY_FIELDS = ("events_treatment", "n_treatment", "events_control", "n_control")
CONTINUOUS_FIELDS = ("mean_treatment", "sd_treatment", "n_treatment", "mean_control", "sd_control", "n_control")
MEASURES: dict[str, tuple[str, ...]] = {"RR": BINARY_FIELDS, "OR": BINARY_FIELDS, "RD": BINARY_FIELDS,
                                        "MD": CONTINUOUS_FIELDS, "SMD": CONTINUOUS_FIELDS}
LOG_SCALE = frozenset({"RR", "OR"})  # measures pooled on the log scale and shown exponentiated


@dataclass(frozen=True)
class Effect:
    yi: float  # the effect (log scale for RR and OR)
    vi: float  # its sampling variance


@dataclass(frozen=True)
class Uncomputable:
    reason: str


class _Bad(Exception):
    pass


def _value(data: dict, field: str) -> float:
    v = data[field]
    if isinstance(v, bool):
        raise _Bad(f"not a number: {field}")
    try:
        x = float(v.strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        raise _Bad(f"not a number: {field}") from None
    if not math.isfinite(x):
        raise _Bad(f"not a number: {field}")
    return x


def _count(data: dict, field: str) -> float:
    x = _value(data, field)
    if x < 0 or x != int(x):
        raise _Bad(f"not a whole number of participants: {field}")
    return x


def compute(measure: Measure, data: dict) -> Effect | Uncomputable:
    """The effect of one study from its arm-level ``data`` (treatment = arm 1)."""
    if measure not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; expected one of {', '.join(MEASURES)}")
    missing = [f for f in MEASURES[measure] if data.get(f) is None or (isinstance(data[f], str) and not data[f].strip())]
    if missing:
        return Uncomputable("missing: " + ", ".join(missing))
    try:
        e = _binary(measure, data) if measure in ("RR", "OR", "RD") else _continuous(measure, data)
    except _Bad as b:
        return Uncomputable(str(b))
    if not (math.isfinite(e.yi) and math.isfinite(e.vi)) or e.vi <= 0:
        return Uncomputable("zero or undefined sampling variance")
    return e


def _binary(measure: str, data: dict) -> Effect:
    a, n1 = _count(data, "events_treatment"), _count(data, "n_treatment")
    c, n2 = _count(data, "events_control"), _count(data, "n_control")
    if n1 == 0 or n2 == 0:
        raise _Bad("an arm has no participants")
    if a > n1 or c > n2:
        raise _Bad("more events than participants in an arm")
    b, d = n1 - a, n2 - c
    if a == 0 and c == 0:
        raise _Bad("no events in either arm")
    if b == 0 and d == 0:
        raise _Bad("every participant had the event in both arms")
    if measure == "RD":
        p1, p2 = a / n1, c / n2
        return Effect(p1 - p2, p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    if 0 in (a, b, c, d):
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
        n1, n2 = a + b, c + d
    if measure == "RR":
        return Effect(math.log((a / n1) / (c / n2)), 1 / a - 1 / n1 + 1 / c - 1 / n2)
    return Effect(math.log(a * d / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d)


def _continuous(measure: str, data: dict) -> Effect:
    m1, s1, n1 = _value(data, "mean_treatment"), _value(data, "sd_treatment"), _count(data, "n_treatment")
    m2, s2, n2 = _value(data, "mean_control"), _value(data, "sd_control"), _count(data, "n_control")
    if n1 < 2 or n2 < 2:
        raise _Bad("an arm has fewer than 2 participants")
    if s1 < 0 or s2 < 0:
        raise _Bad("negative standard deviation")
    if measure == "MD":
        return Effect(m1 - m2, s1 * s1 / n1 + s2 * s2 / n2)
    df = n1 + n2 - 2
    sp = math.sqrt(((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / df)
    if sp == 0:
        raise _Bad("zero pooled standard deviation")
    d = (m1 - m2) / sp
    j = math.exp(math.lgamma(df / 2) - math.log(math.sqrt(df / 2)) - math.lgamma((df - 1) / 2))
    return Effect(j * d, j * j * ((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2))))
