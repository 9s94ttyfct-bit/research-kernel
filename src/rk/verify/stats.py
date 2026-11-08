"""V-STATS (spec §9.1): a kernel-made statistic or figure recomputes, with the current ``rk.stats`` code, from the
extraction rows it was made from.

Artifacts are made only through the ``make_*`` helpers here, so each records what its recomputation needs:

* ``code_path``: one of ``CODE_PATHS`` (the code that made it; V-STATS runs the same code again);
* ``code_version``: ``rk.stats.STATS_VERSION`` when it was made;
* ``input_hashes``: per input extraction row, in order, the SHA-256 of the row's canonical JSON (``row_hash``: the
  ``extractions`` row with its ``value`` decoded), and ``values.row_ids``: those rows' ids in the same order; every
  input row comes from one extraction attempt, ``values.extraction_attempt``;
* ``values``: the request (``measure``, the pool ``options`` as requested, ``hksj="auto"`` included, the group's
  ``item_ids`` and, for an outcome of a protocol, its ``field_map``: each role of the measure -> the extraction field
  holding it; or the map ``fields``; a forest plot's study ``labels``) and the results.

``values`` of ``rk.stats.meta:pool`` (``stt``) and ``rk.stats.plots:forest_svg`` (``fig``, the SVG file):
``{measure, options: {model, hksj, pi_df, level}, item_ids, row_ids, extraction_attempt, studies: [{item_id, yi, vi}]
(pooled, in group order), uncomputable: [{item_id, reason}] (every group study without a computable effect, including
one with no usable row: "missing: <fields>"), pooled: Pooled.as_dict() | None, display: {estimate, ci_low, ci_high,
[pi_low, pi_high], k, i2_percent, ci_level_percent} | None}`` (``display``: RR and OR exponentiated, I² and the CI
level in percent: the numbers a claim states), and for the figure ``labels``: ``{item_id: label}``. The pool needs at
least two computable studies (the figure draws no pooled row without them). ``values`` of
``rk.stats.plots:map_table`` (``stt``) and ``rk.verify.stats:map_markdown`` (``fig``, a Markdown table): ``{fields,
item_ids, row_ids, extraction_attempt, table: map_table(...)}`` (every group study counts; a study without a usable
row for a field is "not reported" there).

A row with a ``problem`` (e.g. marked missing) gives no value, and two usable values for one field of a study are
refused. Pass only the rows the analysis uses (for a pool: the measure's fields of the group's studies): every input
row is checked.

``stat_claim_text`` writes a pool's ``stat_result`` claim from its ``display`` values and binds each printed number
to its display key; V-NUM checks the claim number by number against those bindings.

``check_stats`` re-reads the rows by id and compares their hashes (a changed or missing row is a mismatch, as is an
artifact with no input row), requires ``code_version == STATS_VERSION`` and that the inputs' extraction attempt is the
one later stages read (``read_extract``'s passed attempt, else its latest that ran), recomputes the values (equal to
1e-9) and, for a figure, the file's SHA-256; it also checks the stored file still has its recorded SHA-256. It never
calls a model. The result is recorded on the artifact as ``V-STATS`` ``pass`` / ``mismatch`` (unless ``record`` is
false: the gates recheck without recording)."""

from __future__ import annotations

import math
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from rk.canon import canonical_json, sha256_hex
from rk.orchestrator.state import shown_attempt
from rk.production.artifacts import ArtifactIn, add_artifact, artifact
from rk.stats import STATS_VERSION
from rk.stats.effects import LOG_SCALE, MEASURES, Effect, Uncomputable, compute
from rk.stats.meta import pool
from rk.stats.plots import forest_svg, map_table
from rk.store.project import ProjectStore
from rk.store.query import query_rows
from rk.verify.numbers import extract_numbers
from rk.verify.records import record_verification

TOLERANCE = 1e-9
READ_EXTRACT = "read_extract"
POOL = "rk.stats.meta:pool"


class PoolOptions(BaseModel):
    """How a group is pooled (``rk.stats.meta.pool``), stored as requested."""
    model_config = ConfigDict(extra="forbid")

    model: Literal["FE", "DL", "REML"] = "REML"
    hksj: StrictBool | Literal["auto"] = "auto"
    pi_df: Literal["ci", "k-2"] = "ci"
    level: float = Field(0.95, gt=0, lt=1)


class StatsResult(BaseModel):
    ok: bool
    mismatches: list[str]


@dataclass(frozen=True)
class _Made:
    values: dict
    output: bytes | None = None  # the file the code wrote (a figure)
    suffix: str | None = None


@dataclass(frozen=True)
class _Code:
    kind: Literal["stt", "fig"]
    params: tuple[str, ...]  # the request keys of ``values`` the code is called with
    make: Callable[..., _Made]
    optional: tuple[str, ...] = ()  # request keys passed only when the artifact has them (made without: defaults)


def row_hash(row: dict) -> str:
    """The SHA-256 of an extraction row's canonical JSON (``value`` decoded)."""
    return sha256_hex(canonical_json(row))


# ---- the code each code path runs ----
def _studies(rows: list[dict], item_ids: list[str]) -> dict[str, dict]:
    """item id -> {field: value} from the rows without a problem, for the group's ``item_ids`` in order (a study
    without a usable row has no field)."""
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("a study is listed twice in the group (item_ids)")
    out: dict[str, dict] = {i: {} for i in item_ids}
    for r in rows:
        if r["item_id"] not in out:
            raise ValueError(f"extraction row {r['id']} is of study {r['item_id']}, not in the group (item_ids)")
        data = out[r["item_id"]]
        if r["problem"] is not None:
            continue
        if r["field"] in data:
            raise ValueError(f"two values for field {r['field']!r} of study {r['item_id']}")
        data[r["field"]] = r["value"]
    return out


def _by_role(data: dict, field_map: dict[str, str] | None) -> dict:
    """A study's values keyed by the measure's roles: through ``field_map`` (role -> extraction field), else as is."""
    return data if field_map is None else {role: data[f] for role, f in field_map.items() if f in data}


def field_words(reason: str, field_map: dict[str, str] | None) -> str:
    """An uncomputable reason naming the extraction fields instead of the roles they fill."""
    if not field_map:
        return reason
    roles = re.compile(r"\b(" + "|".join(map(re.escape, field_map)) + r")\b")
    return roles.sub(lambda m: field_map[m[1]], reason)


def _pool_values(rows: list[dict], measure: str, options: dict, item_ids: list[str],
                 field_map: dict[str, str] | None = None) -> tuple[dict, list[str], list[Effect], object]:
    if measure not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; expected one of {', '.join(MEASURES)}")
    if field_map is not None and set(field_map) != set(MEASURES[measure]):
        raise ValueError(f"the field map must name every role of {measure} ({', '.join(MEASURES[measure])}) and "
                         f"only those, not {sorted(field_map)}")
    opts = PoolOptions(**options)
    studies, uncomputable, ids, effects = [], [], [], []
    for item_id, data in _studies(rows, list(item_ids)).items():
        e = compute(measure, _by_role(data, field_map))
        if isinstance(e, Uncomputable):
            uncomputable.append({"item_id": item_id, "reason": field_words(e.reason, field_map)})
            continue
        studies.append({"item_id": item_id, "yi": e.yi, "vi": e.vi})
        ids.append(item_id)
        effects.append(e)
    pooled = pool(effects, model=opts.model, hksj=opts.hksj, pi_df=opts.pi_df, level=opts.level) \
        if len(effects) >= 2 else None
    display = None
    if pooled is not None:
        show = math.exp if measure in LOG_SCALE else float
        display = {"estimate": show(pooled.estimate), "ci_low": show(pooled.ci_low), "ci_high": show(pooled.ci_high),
                   "k": pooled.k, "i2_percent": 100 * pooled.i2, "ci_level_percent": 100 * opts.level}
        if pooled.pi_low is not None:
            display |= {"pi_low": show(pooled.pi_low), "pi_high": show(pooled.pi_high)}
    values = {"studies": studies, "uncomputable": uncomputable,
              "pooled": pooled.as_dict() if pooled is not None else None, "display": display}
    return values, ids, effects, pooled


def _pool(rows: list[dict], *, measure: str, options: dict, item_ids: list[str],
          field_map: dict[str, str] | None = None) -> _Made:
    values, _, effects, _ = _pool_values(rows, measure, options, item_ids, field_map)
    if values["pooled"] is None:
        raise ValueError(f"pooling needs at least two computable studies; {len(effects)} of "
                         f"{len(effects) + len(values['uncomputable'])} are")
    return _Made(values)


def _forest(rows: list[dict], *, measure: str, options: dict, item_ids: list[str], labels: dict,
            field_map: dict[str, str] | None = None) -> _Made:
    values, ids, effects, pooled = _pool_values(rows, measure, options, item_ids, field_map)
    if not effects:
        raise ValueError("a forest plot needs at least one computable study")
    svg = forest_svg([labels[i] for i in ids], effects, pooled, measure=measure)
    return _Made(values, svg.encode("utf-8"), ".svg")


def _map(rows: list[dict], *, fields: list, item_ids: list[str]) -> _Made:
    return _Made({"table": map_table(list(_studies(rows, list(item_ids)).values()), list(fields))})


def _cell(x) -> str:
    return " ".join(str(x).split()).replace("|", "\\|")


def map_markdown(table: dict) -> str:
    """An evidence map (``rk.stats.plots.map_table``) as Markdown tables: per field, its categories with the number of
    studies, then the first field × the second when there are two or more fields."""
    out = [f"# Evidence map ({table['n']} studies)"]
    for field, counts in table["fields"].items():
        out += ["", f"## {_cell(field)}", "", "| Category | Studies |", "|---|---|"]
        out += [f"| {_cell(c)} | {n} |" for c, n in counts.items()]
    cross = table.get("cross")
    if cross:
        cols = list(table["fields"][cross["cols"]])
        out += ["", f"## {_cell(cross['rows'])} × {_cell(cross['cols'])}", "",
                "| " + " | ".join([_cell(cross["rows"]), *map(_cell, cols)]) + " |", "|" + "---|" * (len(cols) + 1)]
        out += ["| " + " | ".join([_cell(r), *(str(counts.get(c, 0)) for c in cols)]) + " |"
                for r, counts in cross["counts"].items()]
    return "\n".join(out) + "\n"


def _map_table(rows: list[dict], *, fields: list, item_ids: list[str]) -> _Made:
    made = _map(rows, fields=fields, item_ids=item_ids)
    return _Made(made.values, map_markdown(made.values["table"]).encode("utf-8"), ".md")


CODE_PATHS: dict[str, _Code] = {
    POOL: _Code("stt", ("measure", "options", "item_ids"), _pool, ("field_map",)),
    "rk.stats.plots:forest_svg": _Code("fig", ("measure", "options", "item_ids", "labels"), _forest, ("field_map",)),
    "rk.stats.plots:map_table": _Code("stt", ("fields", "item_ids"), _map),
    "rk.verify.stats:map_markdown": _Code("fig", ("fields", "item_ids"), _map_table),
}


# ---- making artifacts ----
def _read_rows(ps: ProjectStore, ids: list[str]) -> list[dict | None]:
    out = []
    for rid in ids:
        rows = query_rows(ps, "SELECT * FROM extractions WHERE id=?", (rid,), ("value",))
        out.append(rows[0] if rows else None)
    return out


def _make(ps: ProjectStore, code_path: str, rows: list[dict], params: dict, *, stage: str, attempt: int, actor: str,
          created_by_task: str | None) -> str:
    code = CODE_PATHS[code_path]
    ids = [r["id"] for r in rows]
    if not ids:
        raise ValueError("no extraction rows: an artifact is made from at least one")
    if len(set(ids)) != len(ids):
        raise ValueError("an extraction row is listed twice")
    stored = _read_rows(ps, ids)
    missing = [i for i, r in zip(ids, stored, strict=True) if r is None]
    if missing:
        raise KeyError(f"no extraction rows {missing}")
    attempts = sorted({r["attempt"] for r in stored})
    if len(attempts) != 1:
        raise ValueError(f"the input rows must come from one extraction attempt, not attempts {attempts}")
    made = code.make(stored, **params)
    values = {**params, "row_ids": ids, "extraction_attempt": attempts[0], **made.values}
    a = ArtifactIn(kind=code.kind, code_path=code_path, code_version=STATS_VERSION,
                   input_hashes=[row_hash(r) for r in stored], values=values, output_path=None)
    if made.output is None:
        return add_artifact(ps, a, stage=stage, attempt=attempt, created_by_task=created_by_task, actor=actor)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / f"output{made.suffix}"
        out.write_bytes(made.output)
        return add_artifact(ps, a.model_copy(update={"output_path": str(out)}), stage=stage, attempt=attempt,
                            created_by_task=created_by_task, actor=actor)


def _options(options: PoolOptions | dict | None) -> dict:
    if options is None:
        return PoolOptions().model_dump()
    return (options if isinstance(options, PoolOptions) else PoolOptions(**options)).model_dump()


def _field_map(params: dict, field_map: dict[str, str] | None) -> dict:
    return params if field_map is None else {**params, "field_map": dict(field_map)}


def make_stats_artifact(ps: ProjectStore, rows: list[dict], measure: str, options: PoolOptions | dict | None = None,
                        *, item_ids: list[str], stage: str, attempt: int, actor: str,
                        created_by_task: str | None = None, field_map: dict[str, str] | None = None) -> str:
    """Pool the group ``item_ids`` on ``measure`` with ``options`` from its extraction ``rows`` (each of a group
    study; a group study without a usable row is uncomputable); returns the ``stt_`` id. ``field_map`` (each role of
    the measure -> the extraction field holding it, an outcome's ``OutcomeSpec.fields``) is recorded so V-STATS
    recomputes with it; without it the rows' fields are the role names."""
    params = {"measure": measure, "options": _options(options), "item_ids": list(item_ids)}
    return _make(ps, POOL, rows, _field_map(params, field_map), stage=stage, attempt=attempt, actor=actor,
                 created_by_task=created_by_task)


def make_forest_artifact(ps: ProjectStore, rows: list[dict], measure: str, options: PoolOptions | dict | None = None,
                         *, item_ids: list[str], labels: dict[str, str] | None = None, stage: str, attempt: int,
                         actor: str, created_by_task: str | None = None,
                         field_map: dict[str, str] | None = None) -> str:
    """The forest plot of the same pool (``labels``: item id -> study label; the item id by default; ``field_map`` as
    for ``make_stats_artifact``); returns the ``fig_`` id."""
    labels = labels or {}
    params = {"measure": measure, "options": _options(options), "item_ids": list(item_ids),
              "labels": {i: labels.get(i, i) for i in item_ids}}
    return _make(ps, "rk.stats.plots:forest_svg", rows, _field_map(params, field_map), stage=stage, attempt=attempt,
                 actor=actor, created_by_task=created_by_task)


def make_map_artifact(ps: ProjectStore, rows: list[dict], fields: list[str], *, item_ids: list[str], stage: str,
                      attempt: int, actor: str, created_by_task: str | None = None) -> str:
    """The evidence map's category counts (``rk.stats.plots.map_table``) over the group ``item_ids`` from its
    extraction ``rows``; ``stt_``."""
    return _make(ps, "rk.stats.plots:map_table", rows, {"fields": list(fields), "item_ids": list(item_ids)},
                 stage=stage, attempt=attempt, actor=actor, created_by_task=created_by_task)


def make_map_table_artifact(ps: ProjectStore, rows: list[dict], fields: list[str], *, item_ids: list[str],
                            stage: str, attempt: int, actor: str, created_by_task: str | None = None) -> str:
    """The same evidence map as a Markdown table (``map_markdown``); ``fig_``."""
    return _make(ps, "rk.verify.stats:map_markdown", rows, {"fields": list(fields), "item_ids": list(item_ids)},
                 stage=stage, attempt=attempt, actor=actor, created_by_task=created_by_task)


# ---- the stat_result claim template ----
def _fmt(v: float, places: int) -> str:
    """``v`` with at least ``places`` decimals, and enough that its last digit is no larger than ``|v|`` (so V-NUM
    accepts it as ``v`` rounded)."""
    if v == 0 or not math.isfinite(v):
        return f"{v:.{places}f}"
    return f"{v:.{max(places, -math.floor(math.log10(abs(v))))}f}"


def stat_claim_text(a: dict) -> tuple[str, list[dict]]:
    """The ``stat_result`` claim of a pool artifact (``artifact(ps, id)``), e.g. "Pooled RR 0.44 (95% CI 0.12 to 1.66;
    4 studies; I² 76%; 95% prediction interval 0.03 to 7.14).", and its number bindings: one
    ``{artifact_id, key, text}`` per printed number, in order, naming the ``display`` value it prints."""
    if a["code_path"] != POOL:
        raise ValueError(f"stat_claim_text writes the claim of a pool artifact ({POOL}), not of {a['code_path']}")
    v = a["values"]
    d = v["display"]
    pieces: list[str | tuple[str, int | None]] = [
        f"Pooled {v['measure']} ", ("estimate", 2), " (", ("ci_level_percent", 0), "% CI ", ("ci_low", 2), " to ",
        ("ci_high", 2), "; ", ("k", None), " studies; I² ", ("i2_percent", 0), "%"]
    if "pi_low" in d:
        pieces += ["; ", ("ci_level_percent", 0), "% prediction interval ", ("pi_low", 2), " to ", ("pi_high", 2)]
    pieces.append(").")
    keys = [p[0] for p in pieces if isinstance(p, tuple)]
    text = "".join(p if isinstance(p, str) else str(d[p[0]]) if p[1] is None else _fmt(d[p[0]], p[1])
                   for p in pieces)
    nums = extract_numbers(text)
    if len(nums) != len(keys):
        raise RuntimeError(f"the template printed {len(nums)} numbers for {len(keys)} values: {text!r}")
    return text, [{"artifact_id": a["id"], "key": k, "text": n.text} for k, n in zip(keys, nums, strict=True)]


# ---- V-STATS ----
def _compare(path: str, stored, again, out: list[str]) -> None:
    if isinstance(stored, dict) and isinstance(again, dict):
        for key in sorted(set(stored) | set(again)):
            if key not in again:
                out.append(f"{path}.{key}: stored, not recomputed")
            elif key not in stored:
                out.append(f"{path}.{key}: recomputed, not stored")
            else:
                _compare(f"{path}.{key}", stored[key], again[key], out)
    elif isinstance(stored, list) and isinstance(again, list):
        if len(stored) != len(again):
            out.append(f"{path}: {len(stored)} entries stored, {len(again)} recomputed")
        else:
            for i, (a, b) in enumerate(zip(stored, again, strict=True)):
                _compare(f"{path}[{i}]", a, b, out)
    elif (isinstance(stored, int | float) and isinstance(again, int | float)
          and not isinstance(stored, bool) and not isinstance(again, bool)):
        same = (math.isnan(stored) and math.isnan(again)) or math.isclose(stored, again, rel_tol=TOLERANCE,
                                                                          abs_tol=TOLERANCE)
        if not same:
            out.append(f"{path}: stored {stored!r}, recomputed {again!r}")
    elif type(stored) is not type(again) or stored != again:
        out.append(f"{path}: stored {stored!r}, recomputed {again!r}")


def _mismatches(ps: ProjectStore, a: dict) -> list[str]:
    out: list[str] = []
    if a["code_version"] != STATS_VERSION:
        out.append(f"code_version {a['code_version']} is not the current rk.stats version {STATS_VERSION}")
    stored_file = ps.root / a["path"]
    if not stored_file.is_file():
        out.append(f"the stored file {a['path']} is missing")
    elif sha256_hex(stored_file.read_bytes()) != a["sha256"]:
        out.append(f"the stored file {a['path']} no longer has its recorded sha256")
    code = CODE_PATHS.get(a["code_path"])
    if code is None or code.kind != a["kind"]:
        out.append(f"unknown code path {a['code_path']!r} for a {a['kind']} artifact")
        return out
    values = a["values"]
    ids = values.get("row_ids")
    if not isinstance(ids, list) or len(ids) != len(a["input_hashes"]):
        out.append("the artifact does not name one extraction row per input hash (values.row_ids)")
        return out
    if not ids:
        out.append("the artifact names no input rows: there is nothing to recompute it from")
        return out
    made_from, current = values.get("extraction_attempt"), shown_attempt(ps, READ_EXTRACT)
    if made_from != current:
        out.append(f"the inputs come from extraction attempt {made_from}, but the read_extract attempt later stages "
                   f"read is {'none' if current is None else f'attempt {current}'}")
    rows = _read_rows(ps, ids)
    for rid, h, row in zip(ids, a["input_hashes"], rows, strict=True):
        if row is None:
            out.append(f"extraction row {rid} is missing")
        elif row_hash(row) != h:
            out.append(f"extraction row {rid} changed since the artifact was made")
    if any(r is None for r in rows):
        return out  # nothing to recompute from
    lacking = [p for p in code.params if p not in values]
    if lacking:
        out.append(f"the artifact lacks its request values {lacking}")
        return out
    params = {p: values[p] for p in code.params} | {p: values[p] for p in code.optional if p in values}
    try:
        made = code.make(rows, **params)
    except (ValueError, ArithmeticError, RuntimeError, KeyError, TypeError) as e:
        out.append(f"recomputation failed: {type(e).__name__}: {e}")
        return out
    attempts = sorted({r["attempt"] for r in rows})
    again = {**params, "row_ids": ids, "extraction_attempt": attempts[0] if len(attempts) == 1 else attempts,
             **made.values}
    _compare("values", values, again, out)
    if made.output is not None and sha256_hex(made.output) != a["sha256"]:
        out.append("the regenerated figure's sha256 differs from the stored one")
    return out


def check_stats(ps: ProjectStore, artifact_id: str, *, actor: str = "kernel", record: bool = True) -> StatsResult:
    """V-STATS of one artifact (see the module docstring), recorded on it unless ``record`` is false. ``KeyError``
    for an unknown artifact."""
    a = artifact(ps, artifact_id)
    if a is None:
        raise KeyError(artifact_id)
    mismatches = _mismatches(ps, a)
    result = StatsResult(ok=not mismatches, mismatches=mismatches)
    if record:
        record_verification(ps, "artifact", artifact_id, "V-STATS", "pass" if result.ok else "mismatch",
                            {**result.model_dump(), "code_path": a["code_path"], "code_version": a["code_version"],
                             "stats_version": STATS_VERSION}, actor=actor)
    return result
