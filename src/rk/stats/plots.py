"""Figures for synthesis: a forest plot as a self-contained SVG (and, for the LaTeX export, the same plot as a TikZ
picture) and the category counts of an evidence map.

``forest_svg`` writes plain SVG 1.1 (no script, no external references, no fonts beyond the generic family) whose
text nodes hold the XML-escaped labels; numbers are formatted with fixed precision, so the same input always gives the
same bytes (V-STATS compares their SHA-256). RR and OR are drawn on a log axis labelled in ratio units.
``forest_tikz`` draws the same rows from a forest plot artifact's stored ``values`` (the labels, effects and pooled
result ``forest_svg`` was called with), printing the same labels, estimates and intervals."""

from __future__ import annotations

import math
import re
from collections import Counter
from xml.sax.saxutils import escape

from rk.stats.effects import LOG_SCALE, Effect
from rk.stats.meta import Pooled

_Z95 = 1.959963984540054  # normal 97.5% quantile: each study's 95% Wald interval
_INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_WIDTH, _ROW, _TOP = 860, 22, 46
_LABEL_X, _PLOT_X0, _PLOT_X1, _VALUE_X = 12, 320, 640, 652
_LOG_TICKS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100)
NOT_REPORTED = "not reported"
# the TikZ picture, in centimetres: label column, plot from _TX0 to _TX1, values at _TVX (about 12 cm, a text width)
_PX_PER_CM = 40  # the SVG's square sizes, in px, drawn in cm
_TX0, _TX1, _TVX = 4.2, 9.2, 9.4
_TROW, _DIAMOND = 0.5, 0.16
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _text(s: str) -> str:
    return escape(_INVALID_XML.sub("", s))


def _f(x: float) -> str:
    return f"{x:.2f}"


def _t(x: float) -> str:
    """A TikZ coordinate in centimetres (``-0.000`` written as ``0.000``)."""
    return f"{x + 0.0:.3f}" if abs(x) >= 5e-4 else "0.000"


def _linear_ticks(lo: float, hi: float) -> list[float]:
    raw = (hi - lo) / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step)
    return [round(i * step, 10) for i in range(first, math.floor(hi / step) + 1)]


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


def _value(show, est: float, low: float, high: float) -> str:
    return f"{_f(show(est))} [{_f(show(low))}, {_f(show(high))}]"


def pooled_name(pooled: Pooled) -> str:
    """The pooled row's name as the forest plot prints it, e.g. "Pooled (REML, HKSJ; k = 4)"."""
    return f"Pooled ({pooled.model}{', HKSJ' if pooled.hksj else ''}; k = {pooled.k})"


def _ticks(lo: float, hi: float, log: bool) -> list[float]:
    return [math.log(t) for t in _LOG_TICKS if lo <= math.log(t) <= hi] if log else _linear_ticks(lo, hi)


def forest_svg(labels: list[str], effects: list[Effect], pooled: Pooled | None, *, measure: str) -> str:
    """One row per study (label, point estimate with its 95% CI, and the value printed on the right), then the pooled
    diamond with its CI and, when given, the prediction interval as a line."""
    if len(labels) != len(effects):
        raise ValueError("one label per effect")
    log = measure in LOG_SCALE
    show = math.exp if log else (lambda x: x)
    rows = _rows(effects)
    lo, hi = _span(rows, pooled)

    def x(v: float) -> str:
        return _f(_PLOT_X0 + (v - lo) / (hi - lo) * (_PLOT_X1 - _PLOT_X0))

    n_rows = len(rows) + (1 if pooled is not None else 0)
    axis_y = _TOP + n_rows * _ROW + 8
    height = axis_y + 40
    out = [(f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{_WIDTH}" height="{height}" '
            f'viewBox="0 0 {_WIDTH} {height}" font-family="sans-serif" font-size="12">'),
           f'<rect x="0" y="0" width="{_WIDTH}" height="{height}" fill="#ffffff"/>',
           f'<text x="{_LABEL_X}" y="24" font-weight="bold">Study</text>',
           f'<text x="{_VALUE_X}" y="24" font-weight="bold">{_text(measure)} [95% CI]</text>',
           f'<line x1="{x(0.0)}" y1="{_TOP - 12}" x2="{x(0.0)}" y2="{axis_y}" stroke="#888888" stroke-dasharray="3,3"/>']
    max_w = max((1 / e.vi for e in effects), default=1.0)
    for i, (label, e, (est, low, high)) in enumerate(zip(labels, effects, rows, strict=True)):
        cy = _TOP + i * _ROW
        side = 4 + 6 * math.sqrt((1 / e.vi) / max_w)
        out += [f'<text x="{_LABEL_X}" y="{_f(cy + 4)}">{_text(label)}</text>',
                f'<line x1="{x(low)}" y1="{cy}" x2="{x(high)}" y2="{cy}" stroke="#000000"/>',
                (f'<rect x="{_f(float(x(est)) - side / 2)}" y="{_f(cy - side / 2)}" width="{_f(side)}" '
                 f'height="{_f(side)}" fill="#000000"/>'),
                f'<text x="{_VALUE_X}" y="{_f(cy + 4)}">{_value(show, est, low, high)}</text>']
    if pooled is not None:
        cy = _TOP + len(rows) * _ROW
        name = pooled_name(pooled)
        if pooled.pi_low is not None:
            out.append(f'<line x1="{x(pooled.pi_low)}" y1="{cy}" x2="{x(pooled.pi_high)}" y2="{cy}" '
                       f'stroke="#444444" stroke-width="2"/>')
        out += [f'<text x="{_LABEL_X}" y="{_f(cy + 4)}" font-weight="bold">{_text(name)}</text>',
                (f'<polygon points="{x(pooled.ci_low)},{cy} {x(pooled.estimate)},{cy - 7} {x(pooled.ci_high)},{cy} '
                 f'{x(pooled.estimate)},{cy + 7}" fill="#1f4e79"/>'),
                (f'<text x="{_VALUE_X}" y="{_f(cy + 4)}" font-weight="bold">'
                 f'{_value(show, pooled.estimate, pooled.ci_low, pooled.ci_high)}</text>')]
    out.append(f'<line x1="{_PLOT_X0}" y1="{axis_y}" x2="{_PLOT_X1}" y2="{axis_y}" stroke="#000000"/>')
    ticks = _ticks(lo, hi, log)
    for t in ticks:
        tx = x(t)
        tick = f"{show(t):g}"
        out += [f'<line x1="{tx}" y1="{axis_y}" x2="{tx}" y2="{axis_y + 5}" stroke="#000000"/>',
                f'<text x="{tx}" y="{axis_y + 18}" text-anchor="middle">{_text(tick)}</text>']
    scale = " (log scale)" if log else ""
    out += [(f'<text x="{_f((_PLOT_X0 + _PLOT_X1) / 2)}" y="{axis_y + 34}" text-anchor="middle">'
             f'{_text(measure + scale)}</text>'), "</svg>"]
    return "\n".join(out) + "\n"


def tex_escape(s: str) -> str:
    """``s`` as LaTeX text: its special characters escaped, control characters dropped."""
    return "".join(_TEX.get(c, c) for c in _CONTROL.sub("", s))


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
    """The forest plot of a forest plot artifact's ``values`` as a TikZ picture (for LaTeX, where the SVG cannot be
    embedded): the rows ``forest_svg`` draws from the same values, with the same labels, estimates and intervals as
    text, on the same scale and range; deterministic like the SVG. Units are centimetres; scale it to the text
    width when it is placed."""
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


def _categories(value) -> list[str]:
    if isinstance(value, list | tuple | set | frozenset):
        cats = sorted({str(v).strip() for v in value if v is not None and str(v).strip()})
    else:
        cats = [str(value).strip()] if value is not None and str(value).strip() else []
    return cats or [NOT_REPORTED]


def _ordered(c: Counter) -> dict[str, int]:
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def map_table(rows: list[dict], fields: list[str]) -> dict:
    """Counts by category for an evidence map: ``{"n", "fields": {field: {category: count}}}`` plus, with two or more
    fields, ``"cross"``: the first field × the second. A list value counts once per distinct element; a missing or
    empty value counts as "not reported". Categories are ordered by count, then name."""
    per = {f: Counter() for f in fields}
    cross: dict[str, Counter] = {}
    for row in rows:
        cats = {f: _categories(row.get(f)) for f in fields}
        for f in fields:
            per[f].update(cats[f])
        if len(fields) >= 2:
            for a in cats[fields[0]]:
                cross.setdefault(a, Counter()).update(cats[fields[1]])
    out: dict = {"n": len(rows), "fields": {f: _ordered(per[f]) for f in fields}}
    if len(fields) >= 2:
        order = list(out["fields"][fields[0]])
        out["cross"] = {"rows": fields[0], "cols": fields[1], "counts": {a: _ordered(cross[a]) for a in order}}
    return out
