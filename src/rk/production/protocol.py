"""The protocol stage: the Ideator drafts the review protocol (eligibility, extraction schema, synthesis plan and, for a
systematic review, the PRISMA-P items) for the frozen RQ, the Scout its search strategy; the kernel builds the
``Protocol`` (the question is the frozen RQ's), stores it as the next version, checks it (``check_strategy``,
``check_protocol``), sizes the strategy (the record-cap probe: ``sources.count`` per query on each counted source of
the strategy, summed and stored as ``protocol.probed``) and then:

- P2 (``collab``) opens ``protocol_approval`` (hard human; every section rendered, ``options.protocol_id`` and
  ``options.version`` name the protocol);
- P1 (``auto``) asks the two-vendor ``vote`` (``conservative="revise"``, ``generator_vendors`` = the Ideator's and the
  Scout's): ``approve`` freezes the protocol, ``revise`` reruns with the judges' reasons as guidance.

The probe is advisory: it is shown to you and to the judges but fails nothing. Each count runs inside the per-item
boundary (a source error or an exhausted source API budget leaves that count ``None`` with its ``error``), so the probe
never fails or interrupts the stage. It overestimates (no date range, duplicates across queries and sources count
again) and leaves out the sources without a count; the record cap is enforced by ``search_screen`` on the deduplicated
records.

A rerun after an unfrozen draft (a revision asked by you or by the vote, a failed check) gives both roles that draft as
``previous_draft`` (its body and probe) to change as the guidance asks, but only a draft of the current frozen RQ that no
back-edge left behind (``protocol->question`` reopens the stage without one); a rerun after a freeze gives them the
version in force as ``frozen_protocol`` when it answers the current frozen RQ (after ``protocol->question`` and a new
RQ it does not, and they draft afresh).

Answers to ``protocol_approval``:
- ``{"decision": "approve"}`` freezes the protocol; ``"record_cap": N`` (a whole number >= 1) sets the project's
  record cap: the protocol with that cap is a new version added and frozen by you. Only you set the cap: agents' drafts
  carry the cap of the latest protocol (default ``RECORD_CAP``);
- ``{"decision": "revise", "comment"}`` reruns the stage with the comment as guidance;
- ``{"decision": "back"}`` (optional ``comment``) goes back to ``question`` (``protocol->question``).
An answer that cannot be applied opens ``protocol_approval`` again, saying why; handling an answer again freezes
nothing twice and returns the checkpoint opened the first time.

Freeze: ``frozen_hash`` = sha256 of the canonical JSON of the body. A frozen protocol is never edited; a new frozen
version over an earlier frozen one records a deviation from it (reason: the attempt's guidance; stage: the stage whose
back-edge reopened ``protocol``), as after a ``search_screen->protocol`` back-edge. ``protocol_prereg_ok`` is the
G-PREREG input; only the systematic and scoping reviews count the freeze as preregistration
(``counts_as_preregistration``), the others freeze for traceability only.

Budget per attempt (protocol is an open stage, 40 role tasks per run session): 1 ideator + 1 scout task, plus 2 judge
tasks in P1 (4).

Gate: a frozen protocol in the latest attempt that passes ``check_protocol`` and has a deviation from the frozen
version before it, if any. A P1 degrade fails: P1 never freezes a protocol the vote did not approve."""

from __future__ import annotations

import json
from typing import get_args

from pydantic import BaseModel

from rk.checkpoints import answered_by_actor, create_checkpoint, opened_in_place_of
from rk.discovery.boundary import error_text, per_item
from rk.discovery.models import PaperType, RQBody
from rk.discovery.sources import COUNTED_SOURCES
from rk.discovery.store import frozen_rq, hypotheses, intake_of
from rk.llm.vendors import vendor_of
from rk.net.http import BudgetExhausted
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.production.models import (
    RECORD_CAP,
    Eligibility,
    ExtractionField,
    Protocol,
    SearchStrategy,
    SynthesisPlan,
)
from rk.production.store import (
    add_deviation,
    add_protocol,
    deviations,
    freeze_protocol,
    frozen_protocol,
    protocol_row,
    protocols,
)
from rk.production.vote import vote
from rk.stats.effects import MEASURES
from rk.store.events import ActorKind, NewEvent
from rk.store.project import ProjectStore
from rk.store.query import query_rows
from rk.verify.novelty import novelty_claims

KIND = "protocol_approval"
CHOICES = ("approve", "revise", "back")
VOTE_OPTIONS = ["approve", "revise"]
QUERIES_MIN, SOURCES_MIN = 3, 2  # what the Scout's strategy needs
LIGHT_TYPES = frozenset({"critical_review", "research_proposal"})  # question, search and selection principles only
PREREG_TYPES = frozenset({"systematic_review", "scoping_review"})  # the freeze counts as preregistration (G-PREREG)
PAPER_TYPES: tuple[str, ...] = get_args(PaperType)
# The PRISMA-P items a systematic review needs that are written as text in ``prisma_p``; the others (eligibility,
# information sources, search strategy, data items, synthesis) are the structured parts of the protocol, which the
# structural checks cover.
PRISMA_P_TEXT = ("rationale", "objectives", "selection_process", "data_collection_process", "risk_of_bias")

DRAFT_TASK = (
    "Draft the review protocol for the frozen research question, its hypotheses and topic, for the intake's paper type "
    "and audience: eligibility (include and exclude criteria, each with a short id and its text), the extraction "
    "schema (each field named, described and typed: text, number or category with its categories, and a unit where "
    "there is one), the synthesis plan (a meta-analysis with its typed outcomes, effect_measure, pool_model, hksj, "
    "pi_df and min_studies, the fewest studies with a computable effect a pool needs, at least 2) "
    "and, for a systematic review, the PRISMA-P items (rationale, objectives, "
    "selection process, data collection process, risk of bias). Never claim that the question or the review is novel. "
    "When a previous draft is given, revise it: change only what the guidance asks for and keep the rest. When a frozen "
    "protocol is given, change only what the guidance asks for. Follow the guidance when there is any.")
STRATEGY_TASK = (
    "Write the search strategy for the protocol: at least 3 English search queries that together cover the research "
    "question and the eligibility criteria, at least 2 sources taken from `available_sources` exactly as given, and "
    "the publication-year range (date_from, date_to; null for an open end). When a previous draft is given (with the "
    "record counts of its queries), revise its strategy: change only what the guidance asks for. The kernel runs the "
    "searches; do not search yourself. Follow the guidance when there is any.")
VOTE_QUESTION = (
    "Is this review protocol complete and fit to freeze for the research question: its eligibility criteria, search "
    "strategy, extraction schema, synthesis plan and, for a systematic review, the PRISMA-P items? Answer approve to "
    "freeze it, or revise and say what to change.")


class ProtocolDraftOut(BaseModel):
    """The Ideator's protocol draft. The record cap is not the Ideator's to set."""
    eligibility: Eligibility
    extraction_schema: list[ExtractionField] = []
    synthesis_plan: SynthesisPlan | None = None
    prisma_p: dict[str, str] = {}  # PRISMA-P item -> text (systematic review: the PRISMA_P_TEXT items)


class SearchStrategyOut(BaseModel):
    """The Scout's search strategy: queries, sources (from the available ones) and publication years (inclusive)."""
    queries: list[str]
    sources: list[str]
    date_from: int | None = None
    date_to: int | None = None


# ---- checks ----
def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def check_strategy(strategy: SearchStrategy, available: list[str]) -> list[str]:
    """What keeps the Scout's strategy from its required size: ``QUERIES_MIN`` distinct queries, ``SOURCES_MIN``
    distinct available sources."""
    out = []
    n = len({q.lower() for q in _unique(strategy.queries)})
    if n < QUERIES_MIN:
        out.append(f"{n} distinct queries (need at least {QUERIES_MIN})")
    sources = _unique(strategy.sources)
    out += [f"source {s!r} is not available (available: {', '.join(available)})" for s in sources
            if s not in available]
    usable = [s for s in sources if s in available]
    if len(usable) < SOURCES_MIN:
        out.append(f"{len(usable)} usable sources (need at least {SOURCES_MIN})")
    return out


def _free_texts(body: Protocol) -> list[tuple[str, str]]:
    """Every text of the protocol the Ideator wrote, by name."""
    out = [(f"eligibility.{side}.{c.id}", c.text) for side in ("include", "exclude")
           for c in getattr(body.eligibility, side)]
    out += [(f"extraction_schema.{f.name}.description", f.description) for f in body.extraction_schema]
    if body.synthesis_plan is not None:
        out += [(f"synthesis_plan.{k}", v) for k, v in body.synthesis_plan.model_dump().items()
                if isinstance(v, str)]
        out += [(f"synthesis_plan.outcomes[{i}].name", o.name) for i, o in enumerate(body.synthesis_plan.outcomes)]
    out += [(f"prisma_p.{k}", v) for k, v in (body.prisma_p or {}).items()]
    return out


def _prisma_p_missing(body: Protocol) -> list[str]:
    """The PRISMA-P text items without a text; the structured items are the structural checks'."""
    texts = body.prisma_p or {}
    return [i for i in PRISMA_P_TEXT if not (texts.get(i) or "").strip()]


def check_protocol(body: Protocol, paper_type: str) -> list[str]:
    """What keeps the protocol from the structure of its paper type, one failure per item; ``[]`` when it has it.
    Every type: the question, a query and a source, an include criterion (the selection principles), named and
    distinct extraction fields, no novelty claim in the Ideator's texts. The systematic and scoping reviews also need
    an extraction schema and a synthesis plan; the systematic review also the PRISMA-P items. A meta-analysis plan
    needs its typed ``pool_model``, an ``effect_measure`` the statistics module computes (``MEASURES``) and its
    pre-specified ``outcomes``, each named once and mapping the measure's roles to its own number fields."""
    if paper_type not in PAPER_TYPES:
        raise ValueError(f"unknown paper type {paper_type!r}")
    out = [] if body.question.strip() else ["question is empty"]
    ss = body.search_strategy
    if not _unique(ss.queries):
        out.append("search_strategy has no query")
    if not _unique(ss.sources):
        out.append("search_strategy has no source")
    if ss.date_from is not None and ss.date_to is not None and ss.date_from > ss.date_to:
        out.append(f"search_strategy dates run backwards ({ss.date_from} > {ss.date_to})")
    if not any(c.text.strip() for c in body.eligibility.include):
        out.append("eligibility has no include criterion")
    ids = [c.id.strip() for side in (body.eligibility.include, body.eligibility.exclude) for c in side]
    out += [f"eligibility criterion {i!r} is given twice" for i in dict.fromkeys(ids) if i and ids.count(i) > 1]
    out += [f"eligibility criterion {c.id!r} has {'no id' if not c.id.strip() else 'no text'}"
            for side in (body.eligibility.include, body.eligibility.exclude) for c in side
            if not c.id.strip() or not c.text.strip()]
    names = [f.name.strip() for f in body.extraction_schema]
    out += [f"extraction field {i} has no name" for i, n in enumerate(names, 1) if not n]
    out += [f"extraction field {n!r} is given twice" for n in dict.fromkeys(names) if n and names.count(n) > 1]
    if paper_type not in LIGHT_TYPES:
        if not body.extraction_schema:
            out.append("extraction_schema has no field")
        if body.synthesis_plan is None:
            out.append("synthesis_plan is missing")
    plan = body.synthesis_plan
    if plan is not None and plan.method == "meta_analysis":
        if plan.pool_model is None:
            out.append("synthesis_plan.pool_model is required for a meta-analysis (REML, DL or FE)")
        if plan.effect_measure not in MEASURES:
            out.append(f"synthesis_plan.effect_measure must be one of {', '.join(list(MEASURES)[:-1])} or "
                       f"{list(MEASURES)[-1]} for a meta-analysis (got {plan.effect_measure!r})")
        if not plan.outcomes:
            out.append("synthesis_plan.outcomes is required for a meta-analysis (the pre-specified outcome names)")
        out += _outcome_problems(plan, {f.name: f for f in body.extraction_schema})
    if paper_type == "systematic_review":
        out += [f"PRISMA-P item {i!r} is missing" for i in _prisma_p_missing(body)]
    out += novelty_claims(_free_texts(body))
    return out


def counts_as_preregistration(paper_type: str) -> bool:
    """Whether freezing the protocol counts as preregistration (G-PREREG): systematic and scoping reviews only."""
    return paper_type in PREREG_TYPES


# ---- the record-cap probe (advisory) ----
def _count(ctx: StageContext, query: str, source: str) -> dict:
    """One count of the probe through the per-item boundary: a source error, or an exhausted source API budget, gives
    ``count: None`` and the ``error`` (the probe is advisory: it never fails or interrupts the stage)."""
    what = f"record count of {query!r} on {source}"
    try:
        n, err = per_item(what, lambda: ctx.sources.count(query, source))
    except BudgetExhausted as e:
        n, err = None, f"{what} failed ({error_text(e)})"
    return {"query": query, "source": source, "count": n} | ({"error": err} if err else {})


def _probe(ctx: StageContext, strategy: SearchStrategy) -> dict:
    """``sources.count`` of every query on every counted source of the strategy (``_count``), and the sum of the
    counts given (duplicates across queries and sources count again). A source that gave no count keeps ``count:
    None`` (and its ``error`` when the count failed); the strategy's sources without a count at all are listed as
    ``uncounted``. ``total`` is ``None`` when nothing was counted."""
    sources = _unique(strategy.sources)
    counts = [_count(ctx, q, s) for q in _unique(strategy.queries) for s in sources if s in COUNTED_SOURCES]
    given = [c["count"] for c in counts if c["count"] is not None]
    return {"counts": counts, "uncounted": [s for s in sources if s not in COUNTED_SOURCES],
            "total": sum(given) if given else None}


def _record_probe(ps: ProjectStore, protocol_id: str, probe: dict, *, actor: str) -> None:
    ps.apply(NewEvent(type="protocol.probed", entity_id=protocol_id, actor_kind="kernel", actor=actor, payload=probe))


def probe_of(ps: ProjectStore, protocol_id: str) -> dict | None:
    """The record-cap probe of the protocol (``counts``, ``uncounted``, ``total``), or ``None`` when it did not run."""
    r = ps.db.execute("SELECT payload FROM events WHERE type='protocol.probed' AND entity_id=? ORDER BY seq DESC "
                      "LIMIT 1", (protocol_id,)).fetchone()
    return json.loads(r["payload"]) if r else None


# ---- versions, freeze, deviations ----
def _next_version(ps: ProjectStore) -> int:
    return ps.db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM protocols").fetchone()[0]


def _current_cap(ps: ProjectStore) -> int:
    """The project's record cap: that of the latest protocol (only you change it), else ``RECORD_CAP``."""
    r = ps.db.execute("SELECT json_extract(body, '$.record_cap') AS cap FROM protocols ORDER BY rowid DESC LIMIT 1"
                      ).fetchone()
    return int(r["cap"]) if r and r["cap"] is not None else RECORD_CAP


def _change_stage(ps: ProjectStore) -> str:
    """The stage that made ``protocol`` run again: the target of the edge that last reopened it, or its source when
    the target is ``protocol`` itself (``search_screen->protocol``: ``search_screen``)."""
    r = ps.db.execute("SELECT json_extract(payload, '$.by_edge') AS edge FROM events WHERE type='stage.reopened' AND "
                      "json_extract(payload, '$.stage')='protocol' ORDER BY seq DESC LIMIT 1").fetchone()
    if r is None or not r["edge"]:
        return "protocol"
    frm, _, to = r["edge"].partition("->")
    return frm if to == "protocol" else to


def _freeze(ctx: StageContext, protocol_id: str, *, actor: str, actor_kind: ActorKind) -> None:
    """Freeze the protocol once. Over an earlier frozen version it first records the deviation from it (once)."""
    ps = ctx.ps
    with ps.lock:
        row = protocol_row(ps, protocol_id)
        if row["status"] == "frozen":
            return
        prev = frozen_protocol(ps)
        if prev is not None and prev["version"] < row["version"] and \
                not any(d["to_version"] == row["version"] for d in deviations(ps, kind="version")):
            stage = _change_stage(ps)
            reason = ctx.guidance.strip() or f"protocol changed after {stage!r} reopened it"
            add_deviation(ps, prev["version"], row["version"], reason, stage, actor=actor, actor_kind=actor_kind)
        freeze_protocol(ps, protocol_id, actor=actor, actor_kind=actor_kind)


def protocol_prereg_ok(ps: ProjectStore) -> bool:
    """The G-PREREG input: the first frozen version was frozen before any ``search_screen`` attempt, and every later
    frozen version has a ``version`` deviation into it (an ``analysis`` deviation, a later stage's fallback, does not
    count). Computed for every paper type; it is preregistration only where ``counts_as_preregistration``."""
    frozen = query_rows(ps, "SELECT id, version FROM protocols WHERE status='frozen' ORDER BY version", ())
    if not frozen:
        return False
    first = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='protocol.frozen' AND entity_id=?",
                          (frozen[0]["id"],)).fetchone()[0]
    screening = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='stage.attempt' AND "
                              "json_extract(payload, '$.stage')='search_screen'").fetchone()[0]
    if screening is not None and first > screening:
        return False
    into = {d["to_version"] for d in deviations(ps, kind="version") if d["to_version"] > d["from_version"]}
    return all(r["version"] in into for r in frozen[1:])


# ---- rendering ----
def _field_line(f: dict) -> str:
    kind = f["type"] + (": " + " | ".join(f["categories"]) if f.get("categories") else "")
    kind += f", unit {f['unit']}" if f.get("unit") else ""
    return f"- {f['name']} ({kind}): {f['description']}"


HKSJ_WORDS = {"auto": "auto (the Cochrane Handbook rule)", True: "always", False: "never (Wald intervals)"}
POOL_KEYS = frozenset({"pool_model", "hksj", "pi_df", "min_studies", "outcomes"})  # shown only for a meta-analysis


def _outcome_problems(plan: SynthesisPlan, schema: dict[str, ExtractionField]) -> list[str]:
    """What keeps a meta-analysis plan's outcomes from being computed as pre-specified: each named once (after case
    and whitespace folding), mapping every role of the effect measure (and nothing else) to a number field of the
    extraction schema, a field to one role only, no two outcomes to the same fields, and no outcome's numerator
    fields (events, means, SDs: every role but ``n_*``) to another outcome's."""
    out: list[str] = []
    seen: dict[str, str] = {}
    sets: dict[frozenset, str] = {}
    numerators: dict[str, str] = {}  # numerator field -> the outcome that uses it
    roles = MEASURES.get(plan.effect_measure or "")
    for o in plan.outcomes:
        name = o.name.strip()
        if not name:
            out.append("synthesis_plan.outcomes: an outcome name is empty")
        elif (key := " ".join(name.split()).casefold()) in seen:
            out.append(f"synthesis_plan.outcomes: outcome {name!r} is given twice")
        else:
            seen[key] = name
        if roles is None:
            continue
        say = f"synthesis_plan.outcomes: outcome {name!r}"
        if missing := [r for r in roles if r not in o.fields]:
            out.append(f"{say} maps no field to {', '.join(missing)}")
        out += [f"{say} maps {k!r}, not a role of {plan.effect_measure} ({', '.join(roles)})" for k in o.fields
                if k not in roles]
        for r in roles:
            f = o.fields.get(r)
            if f is not None and f not in schema:
                out.append(f"{say}: {r} -> {f!r} is not a field of the extraction schema")
            elif f is not None and schema[f].type != "number":
                out.append(f"{say}: {r} -> {f!r} is not a number field")
        if not missing:
            fields = frozenset(o.fields[r] for r in roles)
            if fields in sets:
                out.append(f"synthesis_plan.outcomes: outcomes {sets[fields]!r} and {name!r} map to the same fields")
            sets.setdefault(fields, name)
        mapped = [(r, o.fields[r]) for r in roles if r in o.fields]
        for f in dict.fromkeys(f for _, f in mapped):
            if len(used := [r for r, g in mapped if g == f]) > 1:
                out.append(f"{say} maps {f!r} to more than one role ({', '.join(used)})")
        for f in dict.fromkeys(f for r, f in mapped if not r.startswith("n_")):  # events, means, SDs
            if (owner := numerators.setdefault(f, name)) != name:
                out.append(f"synthesis_plan.outcomes: outcomes {owner!r} and {name!r} share the numerator field {f!r} "
                           f"(only n_* fields may be shared)")
    return out


def _outcome_text(o: dict) -> str:
    fields = ", ".join(f"{r} = {f}" for r, f in (o.get("fields") or {}).items())
    return f"{o['name']} ({fields})" if fields else o["name"]


def plan_value(key: str, value, method: str | None = None) -> str | None:
    """A synthesis plan value as a review shows it (``None``: not shown): ``hksj`` and ``min_studies`` in words, the
    outcomes with their fields, lists joined; the meta-analysis fields (``POOL_KEYS``) only when ``method`` is
    ``meta_analysis``."""
    if key in POOL_KEYS and method != "meta_analysis":
        return None
    if key == "hksj":
        return HKSJ_WORDS.get(value, str(value))
    if key == "min_studies":
        return f"at least {value} studies with a computable effect"
    if key == "outcomes":
        return "; ".join(_outcome_text(o) for o in value) or None
    if isinstance(value, list):
        return ", ".join(map(str, value)) or None
    return None if value is None else str(value)


def probe_lines(probe: dict | None, cap: int) -> list[str]:
    """The record-cap probe, as the protocol's review shows it."""
    if probe is None:
        return ["Record-cap probe: not run."]
    per = "; ".join(f"{c['query']!r} on {c['source']}: "
                    + (f"no count ({c['error']})" if c.get("error") else "no count" if c["count"] is None
                       else str(c["count"]))
                    for c in probe["counts"]) or "nothing counted"
    if probe["total"] is None:
        lines = [f"Record-cap probe (advisory): not counted (no source gave a record count; {per})."]
    else:
        lines = [(f"Record-cap probe (advisory): {probe['total']} records (summed per query and counted source, so "
                  f"duplicates count again, with no date range; {per}).")]
    if probe["uncounted"]:
        lines.append(f"Not counted (no record count): {', '.join(probe['uncounted'])}.")
    if probe["total"] is not None and probe["total"] > cap:
        lines.append(f"The probe found {probe['total']} records, above the record cap {cap}. search_screen stops "
                     f"above the cap after deduplication: narrow the strategy (revise), or approve with a raised "
                     f"record_cap.")
    return lines


def render_protocol(ps: ProjectStore, row: dict) -> list[str]:
    """Every section of the protocol ``row``, for its review."""
    b = row["body"]
    intake = intake_of(ps)
    paper_type = intake.paper_type if intake is not None else "?"
    purpose = ("freezing it counts as preregistration (G-PREREG)" if counts_as_preregistration(paper_type)
               else "freezing it is for traceability only, not preregistration")
    ss = b["search_strategy"]
    dates = f"{ss['date_from'] or 'any year'} to {ss['date_to'] or 'now'}"
    lines = [f"Protocol version {row['version']} ({paper_type}; {purpose}).", "", f"Research question: {b['question']}",
             "", "Search strategy:", f"- sources: {', '.join(ss['sources'])}", f"- publication years: {dates}",
             f"- languages: {', '.join(ss['languages'])}", "- queries:"]
    lines += [f"  - {q}" for q in ss["queries"]]
    lines += ["", f"Record cap: {b['record_cap']} records.", *probe_lines(probe_of(ps, row["id"]), b["record_cap"])]
    lines += ["", "Eligibility:", "Include:"] + [f"- {c['id']}: {c['text']}" for c in b["eligibility"]["include"]]
    lines += ["Exclude:"] + ([f"- {c['id']}: {c['text']}" for c in b["eligibility"]["exclude"]] or ["- none"])
    lines += ["", "Extraction schema:"] + ([_field_line(f) for f in b["extraction_schema"]] or ["- none"])
    plan = b["synthesis_plan"]
    shown = [(k, plan_value(k, v, plan["method"])) for k, v in plan.items()] if plan else []
    lines += ["", "Synthesis plan:"] + ([f"- {k}: {v}" for k, v in shown if v is not None] or ["- none"])
    if b["prisma_p"]:
        lines += ["", "PRISMA-P:"] + [f"- {k}: {v}" for k, v in b["prisma_p"].items()]
    return lines


def _prompt(ps: ProjectStore, row: dict, why: str) -> str:
    lines = [why, ""] if why else []
    lines += render_protocol(ps, row)
    prev = frozen_protocol(ps)
    if prev is not None and prev["version"] < row["version"]:
        lines += ["", f"Version {prev['version']} is frozen: approving records a deviation from it."]
    lines += ["", ('Approve this protocol ({"decision": "approve"}; it is then frozen; add "record_cap": N to set the '
                   'record cap of this project), ask for a revision ({"decision": "revise", "comment": ...}) or go '
                   'back to the research question ({"decision": "back"}, optionally with a "comment").')]
    return "\n".join(lines)


def _open(ctx: StageContext, protocol_id: str, *, replaces: str | None = None, why: str = "") -> str:
    """A ``protocol_approval`` about the protocol; opened in answer to ``replaces``, it is opened once."""
    if replaces is not None and (existing := opened_in_place_of(ctx.ps, KIND, replaces)) is not None:
        return existing
    row = protocol_row(ctx.ps, protocol_id)
    return create_checkpoint(ctx.ps, KIND, _prompt(ctx.ps, row, why),
                             {"protocol_id": protocol_id, "version": row["version"], "attempt": row["attempt"],
                              "choices": list(CHOICES), "replaces": replaces}, stage=ctx.stage, actor=ctx.actor)


# ---- the worker ----
def _topic(ps: ProjectStore, topic_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM topics WHERE id=?", (topic_id,), ("gap_ids", "closest"))
    if not rows:
        return None
    t = rows[0]
    return {"title": t["title"], "summary": t["summary"], "data_needed": t["data_needed"], "risks": t["risks"]}


def _previous_draft(ps: ProjectStore, question: str) -> dict | None:
    """The draft this attempt revises, with its probe: the latest protocol when it is an unfrozen draft of the current
    research question (``question``, the frozen RQ's sentence) and ``protocol`` was not reopened since by an edge
    from another stage (a back-edge to question). ``None`` otherwise: a frozen latest protocol, a draft of another
    question, or one an edge left behind."""
    rows = query_rows(ps, "SELECT * FROM protocols ORDER BY rowid DESC LIMIT 1", (), ("body",))
    if not rows or rows[0]["status"] == "frozen" or rows[0]["body"]["question"] != question:
        return None
    row = rows[0]
    added = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='protocol.added' AND entity_id=?",
                          (row["id"],)).fetchone()[0]
    left_behind = ps.db.execute(
        "SELECT 1 FROM events WHERE type='stage.reopened' AND json_extract(payload, '$.stage')='protocol' AND seq>? "
        "AND json_extract(payload, '$.by_edge')!='protocol->protocol' LIMIT 1", (added,)).fetchone()
    if left_behind is not None:
        return None
    return {"version": row["version"], "body": row["body"], "record_probe": probe_of(ps, row["id"])}


def _revise_reason(res) -> str:
    why = "; ".join(f"{o['role']} ({o['vendor']}): {o['reason']}" for o in res.opinions if o["reason"])
    head = (f"the protocol vote did not approve it ({res.reason})" if res.conservative_used
            else "the judges asked for a revision")
    return f"{head}: {why}" if why else head


class ProtocolWorker:
    def run(self, ctx: StageContext) -> StageOutcome:
        ps = ctx.ps
        intake = intake_of(ps)
        if intake is None:
            return StageOutcome("failed", reason="intake missing")
        rq = frozen_rq(ps)
        if rq is None:
            return StageOutcome("failed", reason="no frozen research question")
        question = RQBody.model_validate(rq["body"]).three_step.sentence()
        context: dict = {
            "project": {"title": ps.meta("title"), "field": ps.meta("field")},
            "intake": intake.model_dump(mode="json", include={"audience", "paper_type", "language"}),
            "research_question": {"sentence": question, **rq["body"]},
            "hypotheses": [{"statement": h["statement"], "falsification": h["falsification"]}
                           for h in hypotheses(ps, rq["id"])],
            "available_sources": ctx.sources.sources(),
        }
        if (topic := _topic(ps, rq["topic_id"])) is not None:
            context["topic"] = topic
        if (prior := frozen_protocol(ps)) is not None and prior["body"]["question"] == question:
            context["frozen_protocol"] = {"version": prior["version"], "body": prior["body"]}
        if (draft := _previous_draft(ps, question)) is not None:
            context["previous_draft"] = draft
        if ctx.guidance:
            context["guidance"] = ctx.guidance
        d = ctx.executor.run("ideator", DRAFT_TASK, context, ProtocolDraftOut, stage=ctx.stage, attempt=ctx.attempt,
                             tools=[])
        if not d.ok or d.output is None:
            return StageOutcome("rerun", reason=f"the ideator gave no protocol draft ({d.error})")
        s = ctx.executor.run("scout", STRATEGY_TASK,
                             {**context, "eligibility": d.output.eligibility.model_dump(mode="json")},
                             SearchStrategyOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
        if not s.ok or s.output is None:
            return StageOutcome("rerun", reason=f"the scout gave no search strategy ({s.error})")
        strategy = SearchStrategy(queries=_unique(s.output.queries), sources=_unique(s.output.sources),
                                  date_from=s.output.date_from, date_to=s.output.date_to)
        body = Protocol(question=question, search_strategy=strategy, eligibility=d.output.eligibility,
                        extraction_schema=d.output.extraction_schema, synthesis_plan=d.output.synthesis_plan,
                        prisma_p=d.output.prisma_p or None, record_cap=_current_cap(ps))
        pid = add_protocol(ps, ctx.attempt, body, version=_next_version(ps), actor=ctx.actor)
        failures = check_strategy(strategy, ctx.sources.sources()) + check_protocol(body, intake.paper_type)
        if failures:
            return StageOutcome("rerun", reason="; ".join(failures))
        _record_probe(ps, pid, _probe(ctx, strategy), actor=ctx.actor)
        if intake.production_mode == "collab":
            return StageOutcome("waiting_human", checkpoint_id=_open(ctx, pid),
                                reason="the protocol awaits your approval")
        return self._vote(ctx, pid, {vendor_of(d.provider, d.model), vendor_of(s.provider, s.model)})

    @staticmethod
    def _vote(ctx: StageContext, protocol_id: str, generator_vendors: set[str | None]) -> StageOutcome:
        row = protocol_row(ctx.ps, protocol_id)
        probe = probe_of(ctx.ps, protocol_id)
        inputs = {"paper_type": intake_of(ctx.ps).paper_type, "protocol": row["body"],
                  "record_probe": probe and {**probe, "note": "advisory: no date range, duplicates count again"}}
        res = vote(ctx, "protocol", protocol_id, VOTE_QUESTION, VOTE_OPTIONS, "revise", inputs,
                   generator_vendors=generator_vendors)
        if res.decision == "approve":
            _freeze(ctx, protocol_id, actor=ctx.actor, actor_kind="kernel")
            return StageOutcome("done", reason="protocol approved by the P1 vote (not reviewed by a human)")
        return StageOutcome("rerun", reason=_revise_reason(res))

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        pid = (checkpoint.get("options") or {}).get("protocol_id")
        row = protocol_row(ctx.ps, pid) if pid else None
        if checkpoint["kind"] != KIND or row is None:
            return StageOutcome("failed", reason=f"protocol cannot apply checkpoint {checkpoint['id']} "
                                                 f"({checkpoint['kind']})")
        answer = checkpoint.get("answer") or {}
        why = self._invalid(answer)
        if why:
            cid = _open(ctx, pid, replaces=checkpoint["id"], why=f"Your answer could not be applied: {why}.")
            return StageOutcome("waiting_human", checkpoint_id=cid, reason=why)
        by = answered_by_actor(ctx.ps, checkpoint["id"]) or ctx.actor
        decision = answer["decision"]
        if decision == "approve":
            target = self._with_cap(ctx, row, answer.get("record_cap"), by)
            _freeze(ctx, target, actor=by, actor_kind="human")
            return StageOutcome("done", reason="protocol approved")
        comment = (answer.get("comment") or "").strip() if isinstance(answer.get("comment"), str) else ""
        if decision == "revise":
            return StageOutcome("rerun", reason=comment)
        return StageOutcome("back_edge", to="question",
                            reason="protocol_approval: back to question" + (f": {comment}" if comment else ""))

    @staticmethod
    def _invalid(answer: dict) -> str:
        """Why the answer cannot be applied, else ``""``."""
        decision = answer.get("decision")
        if decision not in CHOICES:
            return f"decision {decision!r} is not one of {', '.join(CHOICES)}"
        if decision == "revise" and not (isinstance(answer.get("comment"), str) and answer["comment"].strip()):
            return "revise needs a comment saying what to change"
        cap = answer.get("record_cap")
        if decision == "approve" and cap is not None and (not isinstance(cap, int) or isinstance(cap, bool)
                                                          or cap < 1):
            return f"record_cap must be a whole number of records, at least 1 (got {cap!r})"
        return ""

    @staticmethod
    def _with_cap(ctx: StageContext, row: dict, cap: int | None, by: str) -> str:
        """The protocol to freeze: ``row``, or, when you set another record cap, the version with your cap (added
        once, with ``row``'s probe)."""
        if cap is None or cap == row["body"]["record_cap"]:
            return row["id"]
        body = {**row["body"], "record_cap": cap}
        with ctx.ps.lock:
            done = next((p for p in protocols(ctx.ps, row["attempt"])
                         if p["version"] > row["version"] and p["body"] == body), None)
            if done is not None:
                return done["id"]
            pid = add_protocol(ctx.ps, row["attempt"], Protocol.model_validate(body), version=_next_version(ctx.ps),
                               actor=by, actor_kind="human")
            probe = probe_of(ctx.ps, row["id"])
            if probe is not None:
                _record_probe(ctx.ps, pid, probe, actor=by)
        return pid

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        rows = protocols(ps, attempt)
        if not rows:
            return GateResult(False, ["no protocol drafted"])
        frozen = [r for r in rows if r["status"] == "frozen"]
        failures = [] if frozen else ["no frozen protocol"]
        row = frozen[-1] if frozen else rows[-1]
        intake = intake_of(ps)
        failures += check_protocol(Protocol.model_validate(row["body"]), intake.paper_type)
        # the record-cap probe is advisory: search_screen enforces the cap on the deduplicated records
        if frozen:
            earlier = ps.db.execute("SELECT MAX(version) FROM protocols WHERE status='frozen' AND version<?",
                                    (row["version"],)).fetchone()[0]
            if earlier is not None and not any(d["to_version"] == row["version"] and d["to_version"] > d["from_version"]
                                               for d in deviations(ps, kind="version")):
                failures.append(f"frozen version {row['version']} has no deviation from version {earlier}")
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: nothing can be deleted or narrowed into an approved protocol, and P1 never freezes a
        protocol the vote did not approve, so the run fails with the failures."""
        return StageOutcome("failed", reason="the protocol was not approved at the edge limit, and P1 does not freeze a "
                                             "protocol the vote did not approve: "
                                             + ("; ".join(failures) or "no reason given"))
