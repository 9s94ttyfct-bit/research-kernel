"""Evidence-map holes: the GapMiner maps the cluster works on two dimensions; the kernel counts the works per cell and,
for each empty cell, runs the GapMiner's targeted search and has Triage say whether any hit falls into the cell. A hole
is verified when the map is a real count (it placed at least half of the works and at least ``MIN_PLACED``), the cell
has a placed neighbour (a work sharing one of its values: the evidence the hole is drawn from), the search ran and
nothing matched. Map values match case-folded and trimmed."""

from __future__ import annotations

import math

from rk.discovery.gaps import (
    Checks,
    MinerResult,
    gap_check_search,
    gap_check_sources,
    task_context,
    work,
)
from rk.discovery.models import CellMatchOut, Dim, Gap, GapEvidence, MapPlan
from rk.orchestrator.engine import StageContext

MAX_EMPTY_CELLS = 6
MIN_PLACED = 5  # a map that placed fewer works is no count; every cell of it fails ``map_coverage``
MIN_PLACED_SHARE = 0.5  # ... nor is one that placed less than half of the works
CHECKS = ("map_coverage", "placed_neighbours", "targeted_search", "cell_match")

MAP_TASK = (
    "Build an evidence map of the given works with exactly 2 dimensions that matter for this project (for example "
    "population x outcome, or intervention x setting), each with 2 to 6 short values. cells is keyed by work, not by "
    "cell: map each work's item id (only ids given in works; never invent one) to [its value of dimension 1, its "
    "value of dimension 2]; place as many works as you can and leave out only works that fit no value. For every "
    "combination no work falls into, add to empty_cell_queries the key "
    '"<value of dimension 1>|<value of dimension 2>" with an English search query for works in that cell. Follow the '
    "guidance when there is any.")
CELL_TASK = (
    "Decide which of the given works fall into the evidence-map cell: each dimension in `cell` has the value given "
    "there. Use only the given works and their item ids. Return the ids of the works that fall into the cell in "
    "matches, an empty list when none does.")


def _fold(value: str) -> str:
    """A map value as it is matched: whitespace collapsed, case-folded."""
    return " ".join(value.split()).casefold()


def _values(dim: Dim) -> list[str]:
    """The dimension's values, stripped, without empty ones or ones repeated up to case and whitespace."""
    out: dict[str, str] = {}
    for v in dim.values:
        if v.strip():
            out.setdefault(_fold(v), " ".join(v.split()))
    return list(out.values())


def _check_cell(ctx: StageContext, dims: list[Dim], a: str, b: str, query: str, base: dict,
                evidence_ids: list[str], coverage: str | None) -> tuple[Gap, bool, dict]:
    """``coverage`` is why the map is no count (``None`` when it is one)."""
    checks = Checks(CHECKS)
    details = {**base, "cell": [a, b], "query": query, "searched_ids": [], "matches": []}
    if coverage is not None:
        checks.failed("map_coverage", coverage)
    elif not evidence_ids:
        checks.passed("map_coverage")
        checks.failed("placed_neighbours", "no placed work shares a value with the cell")
    elif not query:
        checks.passed("map_coverage")
        checks.passed("placed_neighbours")
        checks.failed("targeted_search", "no query for the cell")
    else:
        checks.passed("map_coverage")
        checks.passed("placed_neighbours")
        found = gap_check_search(ctx, query)
        details["searched_ids"] = found
        checks.passed("targeted_search")
        if not found:
            checks.passed("cell_match")
        else:
            res = ctx.executor.run("triage", CELL_TASK,
                                   {"dims": [d.name for d in dims], "cell": {dims[0].name: a, dims[1].name: b},
                                    "works": [work(ctx.lib, i) for i in found]},
                                   CellMatchOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
            if not res.ok or res.output is None:
                checks.failed("cell_match", f"triage gave no judgement ({res.error})")
            else:
                given = set(found)
                matches = [i for i in dict.fromkeys(res.output.matches) if i in given]
                details["matches"] = matches
                if matches:
                    checks.failed("cell_match", f"{len(matches)} searched work(s) fall into the cell")
                else:
                    checks.passed("cell_match")
    cell = f"{dims[0].name} = {a!r} and {dims[1].name} = {b!r}"
    if checks.ok:
        statement = (f"Evidence-map hole: no work with {cell} was found within the searched scope (the landscape and "
                     f'the query "{query}" on {", ".join(gap_check_sources(ctx))}).')
    else:
        statement = f"Evidence-map cell with {cell}: empty in the landscape, not confirmed as a hole."
    gap = Gap(kind="evidence_map_hole", statement=statement, evidence=GapEvidence(item_ids=evidence_ids),
              details=details)
    return gap, checks.ok, checks.dump()


def _key(cell: tuple[str, ...]) -> str:
    """A cell's key in ``empty_cell_queries`` and the stored counts: ``"<value 1>|<value 2>"``."""
    return "|".join(cell)


def _cell_of(lookups: list[dict[str, str]], parts: list[str]) -> tuple[str, ...] | None:
    """The map's cell for the given values (matched case-folded and trimmed), or ``None``."""
    if len(parts) != len(lookups):
        return None
    cell = tuple(lookup.get(_fold(p)) for lookup, p in zip(lookups, parts, strict=True))
    return None if None in cell else cell


def mine(ctx: StageContext, clusters: list[dict]) -> MinerResult:
    items = list(dict.fromkeys(i for c in clusters for i in c["item_ids"]))
    if not items:
        return MinerResult()
    res = ctx.executor.run("gapminer", MAP_TASK,
                           {**task_context(ctx),
                            "clusters": [{"cluster_id": c["id"], "label": c["label"], "description": c["description"],
                                          "item_ids": c["item_ids"]} for c in clusters],
                            "works": [work(ctx.lib, i) for i in items]},
                           MapPlan, stage=ctx.stage, attempt=ctx.attempt, tools=[])
    if not res.ok or res.output is None:
        return MinerResult(problems=[f"the gapminer gave no evidence map ({res.error})"])
    plan = res.output
    if len(plan.dims) != 2:
        return MinerResult(problems=[f"the evidence map needs exactly 2 dimensions (got {len(plan.dims)})"])
    dims = [Dim(name=d.name.strip(), values=_values(d)) for d in plan.dims]
    if not all(d.values for d in dims):
        return MinerResult(problems=["an evidence-map dimension has no values"])
    lookups = [{_fold(v): v for v in d.values} for d in dims]

    out = MinerResult()
    known = set(items)
    counts: dict[tuple[str, ...], int] = {(a, b): 0 for a in dims[0].values for b in dims[1].values}
    placed: dict[str, tuple[str, ...]] = {}
    ignored: list[str] = []  # keys that are no given item id, or values outside the dimensions
    not_ids, off_map = 0, 0
    for key, pair in plan.cells.items():
        iid = key.strip()
        if iid not in known or "|" in iid:
            ignored.append(key)
            not_ids += 1
            continue
        cell = _cell_of(lookups, pair)
        if cell is None:
            ignored.append(key)
            off_map += 1
            continue
        if iid not in placed:
            counts[cell] += 1
            placed[iid] = cell
    if not_ids:
        out.problems.append(f"the evidence map had {not_ids} cells entry(ies) keyed by something other than a given "
                            f"item id (cells maps each work's item id to its two values)")
    if off_map:
        out.problems.append(f"the evidence map gave {off_map} work(s) values outside its dimensions")
    need = max(MIN_PLACED, math.ceil(MIN_PLACED_SHARE * len(items)))
    coverage = None
    if len(placed) < need:
        coverage = f"the map placed {len(placed)} of {len(items)} works (needs at least {need})"
        out.problems.append(f"the evidence map is no count: it placed {len(placed)} of {len(items)} works (needs at "
                            f"least {need}); none of its empty cells is a hole")

    queries: dict[tuple[str, ...], str] = {}
    for k, q in plan.empty_cell_queries.items():
        cell = _cell_of(lookups, k.split("|"))
        if cell is not None and q.strip():
            queries.setdefault(cell, q.strip())
    empty = [cell for cell, n in counts.items() if n == 0]
    empty.sort(key=lambda cell: cell not in queries)  # cells with a query first, else in map order
    base = {"dims": [d.model_dump() for d in dims], "counts": {_key(c): n for c, n in counts.items()},
            "ignored_items": ignored}
    for a, b in empty[:MAX_EMPTY_CELLS]:
        neighbours = [i for i, (va, vb) in placed.items() if va == a or vb == b]
        out.gaps.append(_check_cell(ctx, dims, a, b, queries.get((a, b), ""), base, neighbours, coverage))
    if len(empty) > MAX_EMPTY_CELLS:
        out.problems.append(f"{len(empty) - MAX_EMPTY_CELLS} more empty evidence-map cells were not checked")
    return out
