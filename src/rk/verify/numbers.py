"""V-NUM: every number in a claim must appear, with the same value and unit, in its evidence (spec §9.1).

Format variants normalise to the same value: thousands separators (``,`` or thin/narrow no-break spaces), the Unicode
minus, ``e``-notation and ``×10^n`` / superscript exponents, ``%`` / ``percent``. Digits glued to letters (``CO2``,
``COVID-19``), citation parentheticals and ``[[…]]`` source markers are not quantities and are ignored.

A number may instead match a value of a kernel-made artifact (``artifact_values``: every number in their ``values``,
at any depth; booleans are not numbers) rounded as the claim writes it: to the claim's last digit, so "0.62" matches
0.6213 and "41%" matches 41.3. Artifact values carry no unit; the template that wrote the claim chose it."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_UNITS = ("mg/kg", "mmol/L", "mg/L", "µg", "μg", "ng", "mg", "kg", "g", "mL", "ml", "µL", "μL", "L", "mmol", "µmol",
          "nmol", "mol", "mM", "µM", "μM", "nM", "M", "°C", "K", "nm", "µm", "μm", "mm", "cm", "km", "m", "ms", "min",
          "h", "s", "kHz", "MHz", "GHz", "Hz", "eV", "kJ", "J", "kW", "W", "mV", "V", "mA", "A", "kPa", "MPa",
          "GPa", "Pa", "%")
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))

_NUM = re.compile(
    r"(?<![\w.])(?<![A-Za-z][-–])"                         # not glued to a word or an identifier like COVID-19
    r"(?P<sign>[-−](?=\d|\.\d))?"                          # a sign only where no digit precedes (10-20 is a range)
    r"(?P<mant>\d{1,3}(?:(?:,|\u2009|\u202f)\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?P<exp>[eE][-+−]?\d+|\s*[×xX]\s*10(?:\^\s*)?(?:[-−+]?\d+|[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?"
    r"(?P<unit>\s?(?:percent\b|per cent\b|(?:" + _UNIT_RE + r")(?![A-Za-z])))?"
    r"(?![A-Za-z\d])"
)
_MARKER = re.compile(r"\[\[[^\]]*\]\]")
_CITATION = re.compile(r"\((?:[^()]*?[A-Z][A-Za-z'’\-]+[^()]*?(?:1[89]|20)\d{2}[a-z]?[^()]*)\)")


class Num(BaseModel):
    value: float
    unit: str | None
    text: str
    span: tuple[int, int]


class NumResult(BaseModel):
    ok: bool
    ungrounded: list[Num]


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * (m.end() - m.start()), text)


def _exp10(m: re.Match) -> int:
    """The power of ten of a matched number's exponent (0 without one)."""
    exp = m["exp"]
    if not exp:
        return 0
    exp = exp.translate(_SUP)
    if exp[0] in "eE":
        return int(exp[1:].replace("−", "-"))
    return int(re.search(r"10\s*\^?\s*([-+]?\d+)$", exp.replace("−", "-")).group(1))


def _value(m: re.Match) -> float:
    v = float(re.sub(r"[,\u2009\u202f]", "", m["mant"])) * 10 ** _exp10(m)
    return -v if m["sign"] else v


def _step(text: str) -> float:
    """The value of the last digit of a number as written: 0.01 for "0.62", 1 for "41%", 10 for "1.23e3"."""
    m = _NUM.match(text)
    if m is None:
        return 0.0
    _, _, frac = m["mant"].partition(".")
    return 10.0 ** (_exp10(m) - len(frac))


def _unit(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip()
    if u in ("percent", "per cent"):
        return "%"
    return {"μg": "µg", "μL": "µL", "μM": "µM", "μm": "µm", "ml": "mL"}.get(u, u)


def extract_numbers(text: str) -> list[Num]:
    masked = _mask(_mask(text, _MARKER), _CITATION)
    out = []
    for m in _NUM.finditer(masked):
        out.append(Num(value=_value(m), unit=_unit(m["unit"]), text=text[m.start():m.end()].strip(),
                       span=(m.start(), m.start() + len(m.group(0).rstrip()))))
    return out


def _same(a: Num, b: Num) -> bool:
    return a.unit == b.unit and abs(a.value - b.value) <= 1e-9 * max(1.0, abs(a.value))


def _rounds_to(n: Num, value: float) -> bool:
    """Whether ``value`` rounded to the claim number's last digit is that number."""
    half = _step(n.text) / 2
    return abs(value - n.value) <= half * (1 + 1e-9) + 1e-12 * max(1.0, abs(value))


def _leaves(obj) -> list[float]:
    """Every number in a JSON value, at any depth (booleans are not numbers)."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int | float):
        return [float(obj)]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _leaves(v)]
    if isinstance(obj, list | tuple):
        return [x for v in obj for x in _leaves(v)]
    return []


def check_numbers(claim_text: str, evidence_texts: list[str], *,
                  artifact_values: Sequence[dict] = ()) -> NumResult:
    pool = [n for t in evidence_texts for n in extract_numbers(t)]
    computed = [x for v in artifact_values for x in _leaves(v)]
    ungrounded = [n for n in extract_numbers(claim_text)
                  if not any(_same(n, e) for e in pool) and not any(_rounds_to(n, x) for x in computed)]
    return NumResult(ok=not ungrounded, ungrounded=ungrounded)
