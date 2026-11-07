"""The protocol stage: the Ideator drafts the review protocol (eligibility, extraction schema, synthesis plan and, for a
systematic review, the PRISMA-P items) for the frozen RQ, the Scout its search strategy; the kernel builds the
``Protocol`` (the question is the frozen RQ's), stores it as the next version, checks it (``check_strategy``,
``check_protocol``), sizes the strategy (the record-cap probe: ``sources.count`` per query on each counted source of
the strategy, summed and stored as ``protocol.probed``) and then:

- P2 (``collab``) opens ``protocol_approval`` (hard human; every section rendered, ``options.protocol_id`` and
  ``options.version`` name the protocol), also over the record cap, so that you may raise it;
- P1 (``auto``) reruns over the record cap, else asks the two-vendor ``vote`` (``conservative="revise"``,
  ``generator_vendor`` = the Ideator's): ``approve`` freezes the protocol, ``revise`` reruns with the judges' reasons
  as guidance.

Answers to ``protocol_approval``:
- ``{"decision": "approve"}`` freezes the protocol; ``"record_cap": N`` (a whole number >= 1) sets the project's
  record cap: the protocol with that cap is a new version added and frozen by you. Only you set the cap: agents' drafts
  carry the cap of the latest protocol (default ``RECORD_CAP``). Approving while the probe exceeds the cap is refused;
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
tasks in P1 (4); a P1 degrade spends 2 more (the vote on the narrowed protocol).

Gate: a frozen protocol in the latest attempt that passes ``check_protocol``, whose probe ran and is within its record
cap, and that has a deviation from the frozen version before it, if any."""

from __future__ import annotations

import json

from pydantic import BaseModel

from rk.checkpoints import answered_by_actor, create_checkpoint, opened_in_place_of
from rk.discovery.models import RQBody
from rk.discovery.sources import COUNTED_SOURCES
from rk.discovery.store import frozen_rq, hypotheses, intake_of
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import degradations, record_degraded
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
PAPER_TYPES = ("systematic_review", "scoping_review", "critical_review", "research_proposal")
# PRISMA-P items a systematic review needs, in order. The text items are written in ``prisma_p``; the others are the
# structured parts of the protocol.
PRISMA_P_ITEMS = ("rationale", "objectives", "eligibility", "information_sources", "search_strategy",
                  "selection_process", "data_collection_process", "data_items", "risk_of_bias", "synthesis")
PRISMA_P_TEXT = ("rationale", "objectives", "selection_process", "data_collection_process", "risk_of_bias")

DRAFT_TASK = (
    "Draft the review protocol for the frozen research question, its hypotheses and topic, for the intake's paper type "
    "and audience: eligibility (include and exclude criteria, each with a short id and its text), the extraction "
    "schema (each field named, described and typed: text, number or category with its categories, and a unit where "
    "there is one), the synthesis plan and, for a systematic review, the PRISMA-P items (rationale, objectives, "
    "selection process, data collection process, risk of bias). Never claim that the question or the review is novel. "
    "When a frozen protocol is given, change only what the guidance asks for. Follow the guidance when there is any.")
STRATEGY_TASK = (
    "Write the search strategy for the protocol: at least 3 English search queries that together cover the research "
    "question and the eligibility criteria, at least 2 sources taken from `available_sources` exactly as given, and "
    "the publication-year range (date_from, date_to; null for an open end). The kernel runs the searches; do not "
    "search yourself. Follow the guidance when there is any.")
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
    out += [(f"prisma_p.{k}", v) for k, v in (body.prisma_p or {}).items()]
    return out


def _prisma_p_missing(body: Protocol) -> list[str]:
    texts = body.prisma_p or {}
    present = {
        "eligibility": any(c.text.strip() for c in body.eligibility.include),
        "information_sources": bool(_unique(body.search_strategy.sources)),
        "search_strategy": bool(_unique(body.search_strategy.queries)),
        "data_items": bool(body.extraction_schema),
        "synthesis": body.synthesis_plan is not None,
    }
    return [i for i in PRISMA_P_ITEMS
            if not (present[i] if i in present else (texts.get(i) or "").strip())]


def check_protocol(body: Protocol, paper_type: str) -> list[str]:
    """What keeps the protocol from the structure of its paper type, one failure per item; ``[]`` when it has it.
    Every type: the question, a query and a source, an include criterion (the selection principles), named and
    distinct extraction fields, no novelty claim in the Ideator's texts. The systematic and scoping reviews also need
    an extraction schema and a synthesis plan; the systematic review also the PRISMA-P items."""
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
    if paper_type == "systematic_review":
        out += [f"PRISMA-P item {i!r} is missing" for i in _prisma_p_missing(body)]
    out += novelty_claims(_free_texts(body))
    return out


def counts_as_preregistration(paper_type: str) -> bool:
    """Whether freezing the protocol counts as preregistration (G-PREREG): systematic and scoping reviews only."""
    return paper_type in PREREG_TYPES


# ---- the record-cap probe ----
def _probe(ctx: StageContext, strategy: SearchStrategy) -> dict:
    """``sources.count`` of every query on every counted source of the strategy, and their sum (duplicates across
    queries and sources count again; sources without a count are listed as ``uncounted``)."""
    sources = _unique(strategy.sources)
    counts = [{"query": q, "source": s, "count": ctx.sources.count(q, s)}
              for q in _unique(strategy.queries) for s in sources if s in COUNTED_SOURCES]
    return {"counts": counts, "uncounted": [s for s in sources if s not in COUNTED_SOURCES],
            "total": sum(c["count"] for c in counts)}


def _record_probe(ps: ProjectStore, protocol_id: str, probe: dict, *, generator_vendor: str | None,
                  actor: str) -> None:
    """Store the probe of a protocol (``generator_vendor``: the Ideator's, the vote's generator)."""
    ps.apply(NewEvent(type="protocol.probed", entity_id=protocol_id, actor_kind="kernel", actor=actor,
                      payload={**probe, "generator_vendor": generator_vendor}))


def probe_of(ps: ProjectStore, protocol_id: str) -> dict | None:
    """The record-cap probe of the protocol (``counts``, ``uncounted``, ``total``, ``generator_vendor``), or
    ``None`` when it did not run."""
    r = ps.db.execute("SELECT payload FROM events WHERE type='protocol.probed' AND entity_id=? ORDER BY seq DESC "
                      "LIMIT 1", (protocol_id,)).fetchone()
    return json.loads(r["payload"]) if r else None


def _over_cap(probe: dict, cap: int) -> str:
    return f"narrow the search strategy ({probe['total']} records > {cap})" if probe["total"] > cap else ""


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
                not any(d["to_version"] == row["version"] for d in deviations(ps)):
            stage = _change_stage(ps)
            reason = ctx.guidance.strip() or f"protocol changed after {stage!r} reopened it"
            add_deviation(ps, prev["version"], row["version"], reason, stage, actor=actor, actor_kind=actor_kind)
        freeze_protocol(ps, protocol_id, actor=actor, actor_kind=actor_kind)


def protocol_prereg_ok(ps: ProjectStore) -> bool:
    """The G-PREREG input: the first frozen version was frozen before any ``search_screen`` attempt, and every later
    frozen version has a deviation into it. Computed for every paper type; it is preregistration only where
    ``counts_as_preregistration``."""
    frozen = query_rows(ps, "SELECT id, version FROM protocols WHERE status='frozen' ORDER BY version", ())
    if not frozen:
        return False
    first = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='protocol.frozen' AND entity_id=?",
                          (frozen[0]["id"],)).fetchone()[0]
    screening = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='stage.attempt' AND "
                              "json_extract(payload, '$.stage')='search_screen'").fetchone()[0]
    if screening is not None and first > screening:
        return False
    into = {d["to_version"] for d in deviations(ps)}
    return all(r["version"] in into for r in frozen[1:])


# ---- rendering ----
def _field_line(f: dict) -> str:
    kind = f["type"] + (": " + " | ".join(f["categories"]) if f.get("categories") else "")
    kind += f", unit {f['unit']}" if f.get("unit") else ""
    return f"- {f['name']} ({kind}): {f['description']}"


def _probe_lines(probe: dict | None, cap: int) -> list[str]:
    if probe is None:
        return ["Record-cap probe: not run."]
    per = "; ".join(f"{c['query']!r} on {c['source']}: {c['count']}" for c in probe["counts"]) or "nothing counted"
    lines = [(f"Record-cap probe: {probe['total']} records (summed per query and counted source, so duplicates "
              f"count again; {per}).")]
    if probe["uncounted"]:
        lines.append(f"Not counted (no record count): {', '.join(probe['uncounted'])}.")
    if probe["total"] > cap:
        lines.append(f"The probe found {probe['total']} records, above the record cap {cap}: narrow the strategy "
                     f"(revise), or approve with a raised record_cap of at least {probe['total']}.")
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
    lines += ["", f"Record cap: {b['record_cap']} records.", *_probe_lines(probe_of(ps, row["id"]), b["record_cap"])]
    lines += ["", "Eligibility:", "Include:"] + [f"- {c['id']}: {c['text']}" for c in b["eligibility"]["include"]]
    lines += ["Exclude:"] + ([f"- {c['id']}: {c['text']}" for c in b["eligibility"]["exclude"]] or ["- none"])
    lines += ["", "Extraction schema:"] + ([_field_line(f) for f in b["extraction_schema"]] or ["- none"])
    plan = b["synthesis_plan"]
    lines += ["", "Synthesis plan:"] + ([f"- {k}: {v}" for k, v in plan.items() if v is not None] if plan
                                       else ["- none"])
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
        if (prior := frozen_protocol(ps)) is not None:
            context["frozen_protocol"] = {"version": prior["version"], "body": prior["body"]}
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
        vendor = vendor_of(d.provider, d.model)
        probe = _probe(ctx, strategy)
        _record_probe(ps, pid, probe, generator_vendor=vendor, actor=ctx.actor)
        if intake.production_mode == "collab":
            return StageOutcome("waiting_human", checkpoint_id=_open(ctx, pid),
                                reason="the protocol awaits your approval")
        over = _over_cap(probe, body.record_cap)
        if over:
            return StageOutcome("rerun", reason=over)
        return self._vote(ctx, pid, vendor)

    @staticmethod
    def _vote(ctx: StageContext, protocol_id: str, generator_vendor: str | None) -> StageOutcome:
        row = protocol_row(ctx.ps, protocol_id)
        probe = probe_of(ctx.ps, protocol_id) or {}
        inputs = {"paper_type": intake_of(ctx.ps).paper_type, "protocol": row["body"],
                  "record_probe": {"total": probe.get("total"), "uncounted": probe.get("uncounted", [])}}
        res = vote(ctx, "protocol", protocol_id, VOTE_QUESTION, VOTE_OPTIONS, "revise", inputs,
                   generator_vendor=generator_vendor)
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
        why = self._invalid(answer, row, probe_of(ctx.ps, pid))
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
    def _invalid(answer: dict, row: dict, probe: dict | None) -> str:
        """Why the answer cannot be applied, else ``""``."""
        decision = answer.get("decision")
        if decision not in CHOICES:
            return f"decision {decision!r} is not one of {', '.join(CHOICES)}"
        if decision == "revise" and not (isinstance(answer.get("comment"), str) and answer["comment"].strip()):
            return "revise needs a comment saying what to change"
        if decision != "approve":
            return ""
        cap = answer.get("record_cap")
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap < 1):
            return f"record_cap must be a whole number of records, at least 1 (got {cap!r})"
        cap = cap if cap is not None else row["body"]["record_cap"]
        if probe is not None and probe["total"] > cap:
            return (f"the record-cap probe found {probe['total']} records, above the record cap {cap}: approve with a "
                    f"record_cap of at least {probe['total']}, ask for a revision or go back")
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
            probe = probe_of(ctx.ps, row["id"]) or {"counts": [], "uncounted": [], "total": 0}
            _record_probe(ctx.ps, pid, {k: probe[k] for k in ("counts", "uncounted", "total")},
                          generator_vendor=probe.get("generator_vendor"), actor=by)
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
        probe = probe_of(ps, row["id"])
        if probe is None:
            failures.append("the record-cap probe did not run")
        elif over := _over_cap(probe, row["body"]["record_cap"]):
            failures.append(over)
        if frozen:
            earlier = ps.db.execute("SELECT MAX(version) FROM protocols WHERE status='frozen' AND version<?",
                                    (row["version"],)).fetchone()[0]
            if earlier is not None and not any(d["to_version"] == row["version"] for d in deviations(ps)):
                failures.append(f"frozen version {row['version']} has no deviation from version {earlier}")
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit. A protocol over its record cap is narrowed: the queries with the largest counts are
        dropped (at least one stays) until the probe fits, and the narrowed protocol goes to the vote; anything else
        (a structural failure, a vote that did not approve) fails, since P1 never freezes a protocol the vote did not
        approve. Narrows once per attempt."""
        ps = ctx.ps
        if any(d["attempt"] == ctx.attempt for d in degradations(ps, ctx.stage)):
            if any(r["status"] == "frozen" for r in protocols(ps, ctx.attempt)):
                return StageOutcome("done", reason="narrowed protocol frozen")
            return StageOutcome("failed", reason="the protocol could not be narrowed (narrowed once already)")
        rows = protocols(ps, ctx.attempt)
        last = rows[-1] if rows else None
        probe = probe_of(ps, last["id"]) if last is not None else None
        if last is None or probe is None:
            return StageOutcome("failed", reason="; ".join(failures) or "the protocol did not pass its gate")
        body = Protocol.model_validate(last["body"])
        cap = body.record_cap
        if probe["total"] <= cap:
            return StageOutcome("failed", reason="the protocol was not approved at the edge limit, and P1 does not "
                                                 "freeze a protocol the vote did not approve: "
                                                 + ("; ".join(failures) or "no reason given"))
        per_query: dict[str, int] = {}
        for c in probe["counts"]:
            per_query[c["query"]] = per_query.get(c["query"], 0) + c["count"]
        keep = list(body.search_strategy.queries)
        dropped: list[str] = []
        for q in sorted(keep, key=lambda q: -per_query.get(q, 0)):
            if sum(per_query.get(k, 0) for k in keep) <= cap or len(keep) == 1:
                break
            keep.remove(q)
            dropped.append(q)
        total = sum(per_query.get(k, 0) for k in keep)
        if total > cap:
            return StageOutcome("failed", reason=f"the protocol could not be narrowed under its record cap: even "
                                                 f"{keep[0]!r} alone finds {total} records > {cap}")
        narrowed = body.model_copy(update={"search_strategy": body.search_strategy.model_copy(
            update={"queries": keep})})
        pid = add_protocol(ps, ctx.attempt, narrowed, version=_next_version(ps), actor=ctx.actor,
                           actor_kind="kernel")
        _record_probe(ps, pid, {"counts": [c for c in probe["counts"] if c["query"] in keep],
                                "uncounted": probe["uncounted"], "total": total},
                      generator_vendor=probe.get("generator_vendor"), actor=ctx.actor)
        record_degraded(ps, stage=ctx.stage, attempt=ctx.attempt, removed=[last["id"]],
                        reason=f"the strategy found {probe['total']} records > the record cap {cap} at the edge "
                               f"limit; dropped the queries {', '.join(repr(q) for q in dropped)} ({total} records "
                               f"left), as protocol {pid}", actor=ctx.actor)
        out = self._vote(ctx, pid, probe.get("generator_vendor"))
        if out.status == "done":
            return out
        return StageOutcome("failed", reason=f"the narrowed protocol was not approved: {out.reason}")
