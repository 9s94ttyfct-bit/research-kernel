"""V-NUM: every number in a claim must appear, with the same value and unit, in its evidence (spec §9.1).

Format variants normalise to the same value: thousands separators (``,`` or thin/narrow no-break spaces), the Unicode
minus, ``e``-notation and ``×10^n`` / superscript exponents, ``%`` / ``percent``. Digits glued to letters (``CO2``,
``COVID-19``), citation parentheticals and ``[[…]]`` source markers are not quantities and are ignored."""

from __future__ import annotations

import re

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


def _value(m: re.Match) -> float:
    mant = re.sub(r"[,\u2009\u202f]", "", m["mant"])
    v = float(mant)
    exp = m["exp"]
    if exp:
        exp = exp.translate(_SUP)
        if exp[0] in "eE":
            v *= 10 ** int(exp[1:].replace("−", "-"))
        else:
            power = re.search(r"10\s*\^?\s*([-+]?\d+)$", exp.replace("−", "-"))
            v *= 10 ** int(power.group(1))
    return -v if m["sign"] else v


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


def check_numbers(claim_text: str, evidence_texts: list[str]) -> NumResult:
    pool = [n for t in evidence_texts for n in extract_numbers(t)]
    ungrounded = [n for n in extract_numbers(claim_text) if not any(_same(n, e) for e in pool)]
    return NumResult(ok=not ungrounded, ungrounded=ungrounded)
