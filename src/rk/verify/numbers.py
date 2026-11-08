"""V-NUM: every number in a claim must appear, with the same value and unit, in its evidence (spec §9.1).

Format variants normalise to the same value: thousands separators (``,`` or thin/narrow no-break spaces), the Unicode
minus, ``e``-notation and ``×10^n`` / superscript exponents, ``%`` / ``percent``. Digits glued to letters (``CO2``,
``COVID-19``), citation parentheticals and ``[[…]]`` source markers are not quantities and are ignored.

A kernel-templated claim (a ``stat_result``) is checked by ``check_bound_numbers`` instead: each printed number
against the artifact value it was bound to, matched exactly or rounded as the claim writes it (``matches_value``) —
to the claim's last digit, and only when that digit's step is no larger than the value — so "0.62" matches 0.6213
and "41%" matches 41.3, but "1" does not match 0.62. Artifact values carry no unit; the template chose it."""

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
    problems: list[str] = []


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


def same_number(a: Num, b: Num) -> bool:
    """Whether two written numbers are the same quantity: equal value (to 1e-9) and unit."""
    return a.unit == b.unit and abs(a.value - b.value) <= 1e-9 * max(1.0, abs(a.value))


def numeric_leaves(obj) -> list[float]:
    """Every number in a JSON-like value (dict values, list items, nested), booleans excluded."""
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in numeric_leaves(x)]
    if isinstance(obj, list | tuple):
        return [v for x in obj for v in numeric_leaves(x)]
    if isinstance(obj, int | float) and not isinstance(obj, bool):
        return [float(obj)]
    return []


class NumberPool(BaseModel):
    """The numbers a sentence's marker targets contain (spec §9.1 V-NUM for manuscripts): ``nums`` written in a
    target's text (matched as written: value and unit), ``values`` stored by kernel code (an artifact's reported values,
    an event's whitelisted fields, protocol fields; matched exactly or rounded as the sentence writes them,
    ``matches_value``)."""
    nums: list[Num] = []
    values: list[float] = []

    def covers(self, n: Num) -> bool:
        return any(same_number(n, e) for e in self.nums) or any(matches_value(n, v) for v in self.values)


def matches_value(n: Num, value: float) -> bool:
    """Whether the claim number ``n`` is ``value``: exactly, or ``value`` rounded to ``n``'s last written digit when
    that digit's step is no larger than ``|value|``."""
    if abs(value - n.value) <= 1e-9 * max(1.0, abs(value)):
        return True
    step = _step(n.text)
    return 0 < step <= abs(value) and abs(value - n.value) <= step / 2 * (1 + 1e-9) + 1e-12 * abs(value)


def check_numbers(claim_text: str, evidence_texts: list[str]) -> NumResult:
    pool = [n for t in evidence_texts for n in extract_numbers(t)]
    ungrounded = [n for n in extract_numbers(claim_text) if not any(same_number(n, e) for e in pool)]
    return NumResult(ok=not ungrounded, ungrounded=ungrounded)


def check_bound_numbers(claim_text: str, bindings: Sequence[tuple[str, float | None]]) -> NumResult:
    """A templated claim: its ``i``-th number must be written as ``bindings[i][0]`` and match the bound value
    ``bindings[i][1]`` (``matches_value``; ``None``: the artifact lacks the bound key). A number without a binding is
    ungrounded; a binding without its number is a problem."""
    nums = extract_numbers(claim_text)
    ungrounded = [n for i, n in enumerate(nums)
                  if i >= len(bindings) or bindings[i][0] != n.text or bindings[i][1] is None
                  or not matches_value(n, bindings[i][1])]
    problems = [f"bound number {t!r} is not in the text" for t, _ in bindings[len(nums):]]
    return NumResult(ok=not ungrounded and not problems, ungrounded=ungrounded, problems=problems)
