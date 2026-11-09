"""The export's renderings of the kernel's statistics (Plan 5c Task 6): the forest plot as a TikZ picture (for LaTeX,
where the stored SVG cannot be embedded: no SVG converter is installed) and the printed rows of a pool.

They compute nothing new: they read an artifact's stored ``values`` and print them as ``rk.stats.plots.forest_svg``
does, from its own constants and helpers (the 95% Wald interval, the padded range, the log ticks, two decimals). This
module lives outside ``rk/stats`` on purpose: ``STATS_VERSION`` hashes that package, and rendering for the export must
not change the validated stats version or the artifacts made under it. A test compares the TikZ picture with the SVG
(labels, estimates, intervals and tick labels)."""

from __future__ import annotations

import math
import re

from rk.stats.effects import LOG_SCALE, Effect
from rk.stats.meta import Pooled
from rk.stats.plots import _LOG_TICKS, _Z95, _f, _linear_ticks

# the TikZ picture, in centimetres: label column, plot from _TX0 to _TX1, values at _TVX (about 12 cm, a text width)
_PX_PER_CM = 40  # the SVG's square sizes, in px, drawn in cm
_TX0, _TX1, _TVX = 4.2, 9.2, 9.4
_TROW, _DIAMOND = 0.5, 0.16
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def tex_escape(s: str) -> str:
    """``s`` as LaTeX text on one line: its whitespace collapsed (newlines and blank lines too, as ``_md`` does, so a
    label can never put a line of its own into the kernel's raw block), its special characters escaped, control
    characters dropped."""
    return "".join(_TEX.get(c, c) for c in _CONTROL.sub("", " ".join(s.split())))


def _t(x: float) -> str:
    """A TikZ coordinate in centimetres (``-0.000`` written as ``0.000``)."""
    return f"{x + 0.0:.3f}" if abs(x) >= 5e-4 else "0.000"


# as forest_svg computes them
def _rows(effects: list[Effect]) -> list[tuple[float, float, float]]:
    """Each study's estimate and 95% Wald interval, on the analysis scale."""
    return [(e.yi, e.yi - _Z95 * math.sqrt(e.vi), e.yi + _Z95 * math.sqrt(e.vi)) for e in effects]


def _span(rows: list[tuple[float, float, float]], pooled: Pooled | None) -> tuple[float, float]:
    """The drawn range: every interval, the pooled CI and PI, and the null effect, with 5% padding."""
    span = [x for r in rows for x in r[1:]]
    if pooled is not None:
        span += [pooled.ci_low, pooled.ci_high]
        if pooled.pi_low is not None:
            span += [pooled.pi_low, pooled.pi_high]
    lo, hi = min([*span, 0.0]), max([*span, 0.0])
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.05
    return lo - pad, hi + pad


def _ticks(lo: float, hi: float, log: bool) -> list[float]:
    return [math.log(t) for t in _LOG_TICKS if lo <= math.log(t) <= hi] if log else _linear_ticks(lo, hi)


def _value(show, est: float, low: float, high: float) -> str:
    return f"{_f(show(est))} [{_f(show(low))}, {_f(show(high))}]"


def pooled_name(pooled: Pooled) -> str:
    """The pooled row's name as the forest plot prints it, e.g. "Pooled (REML, HKSJ; k = 4)"."""
    return f"Pooled ({pooled.model}{', HKSJ' if pooled.hksj else ''}; k = {pooled.k})"


def forest_inputs(values: dict) -> tuple[list[str], list[Effect], Pooled | None, str]:
    """The labels, effects, pooled result and measure a forest plot artifact (``rk.stats.plots:forest_svg``) was
    drawn from, read from its stored ``values``: the computable studies in group order with their labels."""
    studies = values["studies"]
    labels = [values["labels"][s["item_id"]] for s in studies]
    effects = [Effect(yi=s["yi"], vi=s["vi"]) for s in studies]
    pooled = Pooled(**values["pooled"]) if values.get("pooled") else None
    return labels, effects, pooled, values["measure"]


def interval_rows(values: dict) -> list[tuple[str | None, str]]:
    """The estimates with their 95% intervals that the stored ``values`` of a pool (``rk.stats.meta:pool``) or of its
    forest plot print, as ``forest_svg`` prints them ("0.62 [0.40, 0.95]", ratio measures exponentiated): each
    computable study's item id with its interval, in group order, then the pooled row (item id ``None``) when pooled."""
    show = math.exp if values["measure"] in LOG_SCALE else (lambda v: v)
    studies = values["studies"]
    out: list[tuple[str | None, str]] = [
        (s["item_id"], _value(show, *r))
        for s, r in zip(studies, _rows([Effect(yi=s["yi"], vi=s["vi"]) for s in studies]), strict=True)]
    if values.get("pooled"):
        p = Pooled(**values["pooled"])
        out.append((None, _value(show, p.estimate, p.ci_low, p.ci_high)))
    return out


def forest_tikz(values: dict) -> str:
    """The forest plot of a forest plot artifact's ``values`` as a TikZ picture: the rows ``forest_svg`` draws from
    the same values, with the same labels, estimates and intervals as text, on the same range and axis (the same tick
    labels); deterministic like the SVG. Units are centimetres; scale it to the text width when it is placed."""
    labels, effects, pooled, measure = forest_inputs(values)
    log = measure in LOG_SCALE
    show = math.exp if log else (lambda v: v)
    rows = _rows(effects)
    lo, hi = _span(rows, pooled)

    def x(v: float) -> str:
        return _t(_TX0 + (v - lo) / (hi - lo) * (_TX1 - _TX0))

    def y(i: int) -> str:
        return _t(-(i + 1) * _TROW)

    axis = -(len(rows) + (1 if pooled is not None else 0) + 0.6) * _TROW
    out = ["\\begin{tikzpicture}[font=\\footnotesize]",
           "\\node[anchor=west,font=\\footnotesize\\bfseries] at (0,0) {Study};",
           f"\\node[anchor=west,font=\\footnotesize\\bfseries] at ({_t(_TVX)},0) {{{tex_escape(measure)} [95\\% CI]}};",
           f"\\draw[gray,dashed] ({x(0.0)},{_t(-0.5 * _TROW)}) -- ({x(0.0)},{_t(axis)});"]
    max_w = max((1 / e.vi for e in effects), default=1.0)
    for i, (label, e, (est, low, high)) in enumerate(zip(labels, effects, rows, strict=True)):
        side = (4 + 6 * math.sqrt((1 / e.vi) / max_w)) / _PX_PER_CM
        cx = _TX0 + (est - lo) / (hi - lo) * (_TX1 - _TX0)
        out += [f"\\node[anchor=west] at (0,{y(i)}) {{{tex_escape(label)}}};",
                f"\\draw ({x(low)},{y(i)}) -- ({x(high)},{y(i)});",
                f"\\fill ({_t(cx - side / 2)},{_t(-(i + 1) * _TROW - side / 2)}) rectangle ++({_t(side)},{_t(side)});",
                f"\\node[anchor=west] at ({_t(_TVX)},{y(i)}) {{{tex_escape(_value(show, est, low, high))}}};"]
    if pooled is not None:
        i, cy = len(rows), -(len(rows) + 1) * _TROW
        if pooled.pi_low is not None:
            out.append(f"\\draw[line width=1pt,color={{rgb,255:red,68;green,68;blue,68}}] ({x(pooled.pi_low)},{y(i)}) "
                       f"-- ({x(pooled.pi_high)},{y(i)});")
        value = _value(show, pooled.estimate, pooled.ci_low, pooled.ci_high)
        name = tex_escape(pooled_name(pooled))
        out += [f"\\node[anchor=west,font=\\footnotesize\\bfseries] at (0,{y(i)}) {{{name}}};",
                (f"\\fill[color={{rgb,255:red,31;green,78;blue,121}}] ({x(pooled.ci_low)},{y(i)}) -- "
                 f"({x(pooled.estimate)},{_t(cy + _DIAMOND)}) -- ({x(pooled.ci_high)},{y(i)}) -- "
                 f"({x(pooled.estimate)},{_t(cy - _DIAMOND)}) -- cycle;"),
                f"\\node[anchor=west,font=\\footnotesize\\bfseries] at ({_t(_TVX)},{y(i)}) {{{tex_escape(value)}}};"]
    out.append(f"\\draw ({_t(_TX0)},{_t(axis)}) -- ({_t(_TX1)},{_t(axis)});")
    for t in _ticks(lo, hi, log):
        out += [f"\\draw ({x(t)},{_t(axis)}) -- ({x(t)},{_t(axis - 0.1)});",
                f"\\node[anchor=north] at ({x(t)},{_t(axis - 0.1)}) {{{tex_escape(f'{show(t):g}')}}};"]
    scale = " (log scale)" if log else ""
    out += [f"\\node[anchor=north] at ({_t((_TX0 + _TX1) / 2)},{_t(axis - 0.55)}) {{{tex_escape(measure + scale)}}};",
            "\\end{tikzpicture}"]
    return "\n".join(out) + "\n"
