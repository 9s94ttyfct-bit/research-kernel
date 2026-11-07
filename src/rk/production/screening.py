"""The screening of ``search_screen``: title/abstract screening (single, dual or the scoping review's dual sample)
with its disagreements. The full-text phase and PRISMA follow the title/abstract phase (``_after_phase``).

``SearchScreenWorker.run``:

1. Dual screening (a systematic review, and a scoping review's sample) needs ``screener_a`` and ``screener_b`` of two
   different known vendors (``screeners_problem``); without them the stage fails before it searches, and
   ``RunManager.start`` refuses a run that would reach the stage.
2. ``run_strategy`` (``rk.production.search``). Above the protocol's record cap (``check_record_cap``) the stage goes
   back to ``protocol`` with that reason, in P1 and P2. Searches the strategy could not finish
   (``SearchResult.incomplete``; the search log keeps them for the PRISMA notes) are named in the stage's reason.
3. A record in a language outside the protocol's ``languages`` (``language_exclusions``) is excluded without
   screening: final ``no``, decided by ``rule``, reason ``"language"``.
4. The screening mode of the paper type (``screening_mode``):
   - systematic review: dual;
   - scoping review: single (``screener_a``) plus a dual sample of 10% of the records (``sample_size``, rounded up),
     drawn over the records in search order by a generator seeded with the frozen protocol's hash. When more than
     10% of the sample are disagreements (``switches_to_dual``), every remaining record is screened by both
     screeners. The sample, its disagreements and the switch are noted (``scoping_sample``);
   - critical review and research proposal: single; the screener's reason is the selection reason.
5. Records go to a screener in batches of ``BATCH`` (title, abstract, year, venue, language). Every decision is a
   ``screening.decided``. A decision on a record that is not in the batch, or a second one on a record, is ignored and
   noted (``stray_decision``). The records a batch's output leaves out (a failed task leaves out all of them) go to
   that screener once more, in new batches; a record still undecided is noted (``missing_decision``) and gets the
   record's ``problem``, and counts as ``unclear`` in single screening (so it goes to the full text) and as a
   disagreement in dual screening.
6. The final decision of a record (``screening.final``):
   - dual: both yes, or yes and unclear: include (``agreement``); both no: exclude (``agreement``); otherwise
     (yes and no, both unclear, no and unclear, a missing decision): a disagreement;
   - single: yes, or unclear (to the full text, as its reason says): include; no: exclude (``single``).
7. Disagreements:
   - P2: ``screening_disagreements`` checkpoints (the same kind in both phases, the phase in the options) of at most
     ``BATCH`` records each, answered ``{"decisions": {record_id: "include" | "exclude"}}`` with a decision for every
     record of that checkpoint and for no other. An invalid answer gets a replacement checkpoint saying why. Answers
     apply in any order; the attempt waits on the first checkpoint still open. Applying an answer again changes
     nothing (``decided_by`` ``human``).
   - P1: ``vote()`` per record (``generator_vendor`` None: a record is no generated text; conservative option
     "exclude"). Every vote's final is decided by ``vote`` and its reason starts "screeners disagreed" ("screeners
     disagreed; P1 conservative" when the vote took the conservative option): ``excluded_by_disagreement`` lists the
     exclusions for PRISMA and the limitations.
8. An attempt interrupted by a rate cap, a budget or a crash is resumed by the next attempt
   (``rk.production.resume``): the screener decisions and the vote or human finals of the interrupted attempts right
   before it, under the same frozen protocol, are carried over by library item (``carried_from``) instead of asked
   again; a rerun of an attempt that ran to completion screens afresh."""

from __future__ import annotations

import json
import random
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from rk.checkpoints import (
    answered_by_actor,
    create_checkpoint,
    get_checkpoint,
    opened_in_place_of,
)
from rk.discovery.store import intake_of
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.production.models import Include, Protocol, ScreeningPhase
from rk.production.resume import carried_screening, mark_protocol
from rk.production.search import (
    check_record_cap,
    deduplicated,
    incomplete_searches,
    language_exclusions,
    language_note,
    run_strategy,
)
from rk.production.store import (
    add_decision,
    decisions,
    finals,
    frozen_protocol,
    record_row,
    set_final,
    set_record,
)
from rk.production.vote import vote
from rk.store.events import NewEvent
from rk.store.project import ProjectStore

TITLE_ABSTRACT: ScreeningPhase = "title_abstract"
PHASE_WORDS: dict[str, str] = {"title_abstract": "title/abstract", "full_text": "full-text"}
KIND = "screening_disagreements"
BATCH = 25  # records per screener task, and disagreements per checkpoint
CHOICES = ("include", "exclude")
CONSERVATIVE = "exclude"
SCREENERS = ("screener_a", "screener_b")
SAMPLE_PERCENT = 10  # the scoping review's dual sample, rounded up
SWITCH_PERCENT = 10  # more disagreements than this share of the sample: dual screening for the rest
ABSTRACT_SHOWN = 800  # characters of an abstract a disagreement checkpoint shows

ScreeningMode = Literal["dual", "sampled", "single"]
MODES: dict[str, ScreeningMode] = {"systematic_review": "dual", "scoping_review": "sampled"}

SCREEN_TASK = (
    "Screen every given record against the eligibility criteria, using only its title, abstract and metadata. Give "
    "exactly one decision per record, keyed by its record_id exactly as given: include \"yes\", \"no\" or "
    "\"unclear\" (when the title and abstract do not settle a criterion), the id of the criterion the decision rests "
    "on, and a short reason. Decide only the given records. Do not search or use tools.")


def _vote_question(phase: str) -> str:
    return (f"The two screeners disagree on this record at {PHASE_WORDS[phase]} screening. Does it meet the "
            "eligibility criteria: include it or exclude it?")


class ScreenDecision(BaseModel):
    record_id: str
    include: Include
    criterion_id: str | None = None
    reason: str = ""


class ScreenOut(BaseModel):
    """A screener's decisions on a batch of records."""
    decisions: list[ScreenDecision]


# ---- rules ----
def screening_mode(paper_type: str) -> ScreeningMode:
    return MODES.get(paper_type, "single")


def screeners_problem(settings, paper_type: str) -> str:
    """Why the configured screeners cannot dual-screen for ``paper_type`` (both of known vendors, and different),
    else ``""``; single screening has no such rule."""
    if screening_mode(paper_type) == "single":
        return ""
    seen: list[str] = []
    for role in SCREENERS:
        if role not in settings.drivers:
            seen.append(f"{role} is not configured")
            continue
        spec = DriverSpec.parse(settings.drivers[role])
        seen.append(f"{role} ({spec}) is {vendor_of(spec.provider, spec.model) or 'of an unknown vendor'}")
    vendors = [vendor_of(s.provider, s.model) for r in SCREENERS if r in settings.drivers
               for s in [DriverSpec.parse(settings.drivers[r])]]
    if len(vendors) == 2 and None not in vendors and vendors[0] != vendors[1]:
        return ""
    return (f"dual screening ({paper_type}) needs screener_a and screener_b of two different known vendors, but "
            f"{' and '.join(seen)}. Set drivers.screener_a or drivers.screener_b in config.toml (rk providers)")


def sample_size(n: int) -> int:
    """The scoping review's dual sample of ``n`` records: ``SAMPLE_PERCENT`` of them, rounded up."""
    return -(-n * SAMPLE_PERCENT // 100)


def switches_to_dual(disagreements: int, sampled: int) -> bool:
    """Whether the sample's disagreements are more than ``SWITCH_PERCENT`` of it."""
    return sampled > 0 and disagreements * 100 > SWITCH_PERCENT * sampled


def dual_rule(a: str | None, b: str | None) -> Include | None:
    """The final of a dual-screened record from its two decisions (``None``: missing): ``"yes"``, ``"no"``, or
    ``None`` for a disagreement."""
    pair = {a, b}
    if "yes" in pair and pair <= {"yes", "unclear"}:
        return "yes"
    if pair == {"no"}:
        return "no"
    return None


# ---- notes ----
def _note(ctx: StageContext, phase: str, kind: str, **fields) -> None:
    ctx.ps.apply(NewEvent(type="screening.noted", entity_id=None, actor_kind="kernel", actor=ctx.actor,
                          payload={"attempt": ctx.attempt, "phase": phase, "kind": kind, **fields}))


def screening_notes(ps: ProjectStore, attempt: int, *, kind: str | None = None,
                    phase: str | None = None) -> list[dict]:
    """What screening noted in the attempt, in order (for the PRISMA notes and the limitations): ``stray_decision``
    (a decision on a record not in the batch, or a second one, ignored), ``failed_batch`` (a screener task gave no
    output), ``missing_decision`` (a record a screener left undecided, also when asked again) and ``scoping_sample``
    (the scoping review's dual sample and whether it switched to dual screening)."""
    rows = ps.db.execute("SELECT payload FROM events WHERE type='screening.noted' "
                         "AND json_extract(payload, '$.attempt')=? ORDER BY seq", (attempt,))
    notes = [json.loads(r["payload"]) for r in rows]
    return [n for n in notes if (kind is None or n["kind"] == kind) and (phase is None or n["phase"] == phase)]


def excluded_by_disagreement(ps: ProjectStore, attempt: int, phase: ScreeningPhase) -> list[dict]:
    """The finals of the records a P1 vote on a screening disagreement excluded (PRISMA lists them apart, and the
    limitations name the recall loss)."""
    return [f for f in finals(ps, attempt, phase=phase) if f["decided_by"] == "vote" and f["include"] == "no"]


# ---- screening ----
def record_input(ctx: StageContext, rec: dict) -> dict:
    """What a screener (and a judge) sees of a record at title/abstract screening."""
    item = ctx.lib.get(rec["item_id"])
    out = {"record_id": rec["id"], "title": item.title, "abstract": item.abstract or "", "year": item.year,
           "venue": item.venue, "language": rec["language"]}
    if note := language_note(rec["language"]):
        out["language_note"] = note
    return out


def _context(protocol: Protocol, phase: str, paper_type: str) -> dict:
    return {"phase": phase, "paper_type": paper_type, "question": protocol.question,
            "eligibility": protocol.eligibility.model_dump(mode="json")}


def _said(screener: str, include: str | None, criterion: str | None = None, reason: str = "") -> str:
    if include is None:
        return f"{screener}: no decision"
    return f"{screener}: {include}" + (f" ({criterion})" if criterion else "") + (f": {reason}" if reason else "")


def _include(got: dict[str, ScreenDecision], rid: str) -> str | None:
    d = got.get(rid)
    return d.include if d is not None else None


def _say(screener: str, d: ScreenDecision | None) -> str:
    return _said(screener, None) if d is None else _said(screener, d.include, d.criterion_id, d.reason)


def _batch(ctx: StageContext, role: str, phase: str, batch: list[str], records: dict[str, dict], context: dict,
           got: dict[str, ScreenDecision]) -> list[str]:
    """One screener task on ``batch``; its decisions go to ``got``. Returns the records it left undecided."""
    r = ctx.executor.run(role, SCREEN_TASK, {**context, "records": [records[x] for x in batch]}, ScreenOut,
                         stage=ctx.stage, attempt=ctx.attempt, tools=[])
    if not r.ok or r.output is None:
        _note(ctx, phase, "failed_batch", screener=role, record_ids=batch, error=r.error or "no output")
        return list(batch)
    wanted = set(batch)
    for d in r.output.decisions:
        if d.record_id not in wanted or d.record_id in got:
            _note(ctx, phase, "stray_decision", screener=role, record_id=d.record_id, include=d.include,
                  why="a second decision on the record" if d.record_id in got else "not a record of the batch")
            continue
        got[d.record_id] = d
        add_decision(ctx.ps, ctx.attempt, d.record_id, phase, role, d.include, d.reason, d.criterion_id or None,
                     actor=ctx.actor)
    return [x for x in batch if x not in got]


def _screen(ctx: StageContext, role: str, phase: str, records: dict[str, dict], context: dict,
            carried: dict[str, dict]) -> dict[str, ScreenDecision]:
    """``role``'s decisions on ``records`` (record id -> input): first those ``carried`` over from an interrupted
    attempt (record id -> decision row), then the others in batches of ``BATCH``. The records left undecided go once
    more, in new batches; those still undecided are noted and get the record's ``problem``."""
    got: dict[str, ScreenDecision] = {}
    for rid in records:
        if (row := carried.get(rid)) is not None:
            got[rid] = ScreenDecision(record_id=rid, include=row["include"], criterion_id=row["criterion"],
                                      reason=row["reason"])
            add_decision(ctx.ps, ctx.attempt, rid, phase, role, row["include"], row["reason"], row["criterion"],
                         actor=ctx.actor, actor_kind="kernel", carried_from=row["carried_from"])
    pending = [r for r in records if r not in got]
    for _ in range(2):
        left: list[str] = []
        for i in range(0, len(pending), BATCH):
            left += _batch(ctx, role, phase, pending[i:i + BATCH], records, context, got)
        pending = left
        if not pending:
            break
    for rid in pending:
        _note(ctx, phase, "missing_decision", screener=role, record_id=rid)
        why = f"{role} gave no {PHASE_WORDS[phase]} decision, also when asked again"
        before = record_row(ctx.ps, rid)["problem"]
        set_record(ctx.ps, rid, actor=ctx.actor, problem=f"{before}; {why}" if before else why)
    return got


def _finalize(ctx: StageContext, phase: str, rids: list[str], dual: set[str], a: dict[str, ScreenDecision],
              b: dict[str, ScreenDecision]) -> list[str]:
    """Write the final of every record the rules settle; returns the disagreements, in order."""
    disagreements: list[str] = []
    for rid in rids:
        da, db = a.get(rid), b.get(rid)
        if rid in dual:
            inc = dual_rule(_include(a, rid), _include(b, rid))
            if inc is None:
                disagreements.append(rid)
                continue
            set_final(ctx.ps, rid, ctx.attempt, phase, inc, "agreement",
                      f"{_say('screener_a', da)}; {_say('screener_b', db)}", actor=ctx.actor)
            continue
        # single screening; the title/abstract rule (Task 6 states the full-text one)
        if da is None:
            reason = "screener_a gave no decision, also when asked again: treated as unclear, so it goes to the full text"
        elif da.include == "unclear":
            reason = f"{_say('screener_a', da)}; unclear, so it goes to the full text"
        else:
            reason = _say("screener_a", da)
        set_final(ctx.ps, rid, ctx.attempt, phase, "no" if da is not None and da.include == "no" else "yes",
                  "single", reason, actor=ctx.actor)
    return disagreements


def _views(ps: ProjectStore, attempt: int, phase: str) -> dict[str, list[dict]]:
    """record id -> what each screener decided on it (``include`` None: no decision)."""
    by: dict[str, dict[str, dict]] = {}
    for d in decisions(ps, attempt, phase=phase):
        by.setdefault(d["record_id"], {})[d["screener"]] = {
            "screener": d["screener"], "include": d["include"], "criterion": d["criterion"], "reason": d["reason"]}
    return {rid: [got.get(s, {"screener": s, "include": None, "criterion": None, "reason": "no decision"})
                  for s in SCREENERS] for rid, got in by.items()}


def _view_of(views: dict[str, list[dict]], rid: str) -> list[dict]:
    return views.get(rid) or [{"screener": s, "include": None, "criterion": None, "reason": "no decision"}
                              for s in SCREENERS]


# ---- disagreements ----
def _checkpoints(ps: ProjectStore, stage: str, attempt: int, phase: str) -> list[dict]:
    rows = ps.db.execute("SELECT id FROM checkpoints WHERE kind=? AND stage=? AND json_extract(options, '$.attempt')=? "
                         "AND json_extract(options, '$.phase')=? ORDER BY rowid", (KIND, stage, attempt, phase))
    return [get_checkpoint(ps, r["id"]) for r in rows.fetchall()]


def _prompt(ctx: StageContext, phase: str, record_ids: list[str], batch: int, batches: int, why: str) -> str:
    protocol = Protocol.model_validate(frozen_protocol(ctx.ps)["body"])
    views = _views(ctx.ps, ctx.attempt, phase)
    lines = [why, ""] if why else []
    head = (f"Screening disagreements ({PHASE_WORDS[phase]} screening), checkpoint {batch} of {batches}: the two "
            f"screeners disagree on these {len(record_ids)} records.")
    lines += [head, "", "Eligibility criteria:"]
    lines += [f"- {c.id} (include): {c.text}" for c in protocol.eligibility.include]
    lines += [f"- {c.id} (exclude): {c.text}" for c in protocol.eligibility.exclude]
    for rid in record_ids:
        rec = record_input(ctx, record_row(ctx.ps, rid))
        meta = ", ".join(str(x) for x in (rec["year"], rec["venue"]) if x) or "no year or venue"
        abstract = rec["abstract"]
        abstract = abstract[:ABSTRACT_SHOWN] + "…" if len(abstract) > ABSTRACT_SHOWN else abstract or "(no abstract)"
        lines += ["", f"- {rid}: {rec['title']} ({meta}; language {rec['language']})", f"  Abstract: {abstract}"]
        lines += [f"  {_said(v['screener'], v['include'], v['criterion'], v['reason'])}" for v in _view_of(views, rid)]
    lines += ["", ('Decide each record: {"decisions": {"<record_id>": "include" | "exclude", ...}}, one decision for '
                   "every record above and for no other.")]
    return "\n".join(lines)


def _open(ctx: StageContext, phase: str, record_ids: list[str], *, batch: int, batches: int,
          replaces: str | None = None, why: str = "") -> str:
    """A ``screening_disagreements`` checkpoint on ``record_ids``; opened in answer to ``replaces``, it is opened
    once."""
    if replaces is not None and (existing := opened_in_place_of(ctx.ps, KIND, replaces)) is not None:
        return existing
    return create_checkpoint(ctx.ps, KIND, _prompt(ctx, phase, record_ids, batch, batches, why),
                             {"attempt": ctx.attempt, "phase": phase, "record_ids": record_ids,
                              "choices": list(CHOICES), "batch": batch, "batches": batches, "replaces": replaces},
                             stage=ctx.stage, actor=ctx.actor)


def _invalid(answer, record_ids: list[str]) -> str:
    """Why the answer cannot settle the checkpoint's records, else ``""``."""
    if not isinstance(answer, dict) or not isinstance(answer.get("decisions"), dict):
        return 'the answer needs "decisions": {record_id: "include" | "exclude"}'
    given = answer["decisions"]
    problems = []
    if missing := [r for r in record_ids if r not in given]:
        problems.append("no decision for " + ", ".join(missing))
    if extra := [r for r in given if r not in record_ids]:
        problems.append(", ".join(extra) + (" is" if len(extra) == 1 else " are") + " not a record of this checkpoint")
    if bad := [f"{r}: {v!r}" for r, v in given.items() if r in record_ids and v not in CHOICES]:
        problems.append("not include or exclude: " + ", ".join(bad))
    return "; ".join(problems)


def _apply(ctx: StageContext, phase: str, cp: dict) -> None:
    """Write your decisions of the answered checkpoint ``cp``; a decision already written is not written again."""
    by = answered_by_actor(ctx.ps, cp["id"]) or ctx.actor
    current = {f["record_id"]: f for f in finals(ctx.ps, ctx.attempt, phase=phase)}
    for rid in cp["options"]["record_ids"]:
        choice = cp["answer"]["decisions"][rid]
        inc = "yes" if choice == "include" else "no"
        f = current.get(rid)
        if f is not None and (f["include"], f["decided_by"]) == (inc, "human"):
            continue
        set_final(ctx.ps, rid, ctx.attempt, phase, inc, "human",
                  f"screeners disagreed; you decided: {choice} (checkpoint {cp['id']})", actor=by,
                  actor_kind="human")


def _vote_on(ctx: StageContext, phase: str, rid: str, context: dict, views: dict[str, list[dict]]) -> None:
    inputs = {**context, "record": record_input(ctx, record_row(ctx.ps, rid)), "screeners": _view_of(views, rid)}
    res = vote(ctx, "record", rid, _vote_question(phase), list(CHOICES), CONSERVATIVE, inputs, generator_vendor=None)
    inc = "yes" if res.decision == "include" else "no"
    if res.conservative_used:
        reason = f"screeners disagreed; P1 conservative: excluded ({res.reason}; not reviewed by a human)"
    else:
        reason = (f"screeners disagreed; the P1 vote {'included' if inc == 'yes' else 'excluded'} it (not reviewed "
                  "by a human)")
    set_final(ctx.ps, rid, ctx.attempt, phase, inc, "vote", reason, actor=ctx.actor)


def _incomplete(items: list[str]) -> str:
    return f"; incomplete searches (noted for PRISMA): {'; '.join(items)}" if items else ""


def _summary(ps: ProjectStore, attempt: int, phase: str, incomplete: list[str]) -> str:
    fs = finals(ps, attempt, phase=phase)
    included = sum(1 for f in fs if f["include"] == "yes")
    excluded = Counter(f["decided_by"] for f in fs if f["include"] == "no")
    voted = sum(1 for f in fs if f["decided_by"] == "vote")
    human = sum(1 for f in fs if f["decided_by"] == "human")
    details = []
    if excluded["rule"]:
        details.append(f"{excluded['rule']} for language")
    if voted:
        details.append(f"{voted} disagreements decided by the P1 vote (not reviewed by a human): {excluded['vote']} "
                       "excluded by the P1 vote on a disagreement")
    if human:
        details.append(f"{human} disagreements decided by you")
    out = f"{PHASE_WORDS[phase]} screening: {included} included, {sum(excluded.values())} excluded"
    return out + (f" ({'; '.join(details)})" if details else "") + _incomplete(incomplete)


# ---- the worker ----
class SearchScreenWorker:
    def run(self, ctx: StageContext) -> StageOutcome:
        ps = ctx.ps
        intake = intake_of(ps)
        if intake is None:
            return StageOutcome("failed", reason="intake missing")
        row = frozen_protocol(ps)
        if row is None:
            return StageOutcome("failed", reason="no frozen protocol")
        problem = screeners_problem(ctx.kernel.settings(), intake.paper_type)
        if problem:
            return StageOutcome("failed", reason=problem)
        mark_protocol(ps, ctx.stage, ctx.attempt, row["frozen_hash"], actor=ctx.actor)
        protocol = Protocol.model_validate(row["body"])
        result = run_strategy(ctx, protocol)
        cap = check_record_cap(ps, ctx.attempt, protocol)
        if cap:
            return StageOutcome("back_edge", to="protocol", reason=cap)
        return self._title_abstract(ctx, protocol, row["frozen_hash"], result.record_ids, result.incomplete)

    def _title_abstract(self, ctx: StageContext, protocol: Protocol, protocol_hash: str, record_ids: list[str],
                        incomplete: list[str]) -> StageOutcome:
        ps, phase = ctx.ps, TITLE_ABSTRACT
        intake = intake_of(ps)
        language = set(language_exclusions(ps, ctx.attempt, protocol))
        for rid in record_ids:
            if rid in language:
                set_final(ps, rid, ctx.attempt, phase, "no", "rule", "language", actor=ctx.actor)
        eligible = [r for r in record_ids if r not in language]
        recs = {r["id"]: r for r in deduplicated(ps, ctx.attempt)}
        inputs = {rid: record_input(ctx, recs[rid]) for rid in eligible}
        context = _context(protocol, phase, intake.paper_type)
        # the work of the interrupted attempts right before this one (rk.production.resume), by record of this one
        by_item = carried_screening(ps, ctx.stage, ctx.attempt, protocol_hash, phase)
        carried = {rid: c for rid in eligible if (c := by_item.get(recs[rid]["item_id"])) is not None}

        def screen(role: str, ids: list[str]) -> dict[str, ScreenDecision]:
            return _screen(ctx, role, phase, {r: inputs[r] for r in ids}, context,
                           {r: carried[r].decisions[role] for r in ids if r in carried and role in carried[r].decisions})

        mode = screening_mode(intake.paper_type)
        a: dict[str, ScreenDecision] = {}
        b: dict[str, ScreenDecision] = {}
        dual: set[str] = set()
        if mode == "single":
            a = screen("screener_a", eligible)
        elif mode == "dual":
            a, b, dual = screen("screener_a", eligible), screen("screener_b", eligible), set(eligible)
        else:
            n = sample_size(len(eligible))
            sample = [eligible[i] for i in sorted(random.Random(protocol_hash).sample(range(len(eligible)), n))]
            a, b = screen("screener_a", sample), screen("screener_b", sample)
            disagree = sum(1 for r in sample if dual_rule(_include(a, r), _include(b, r)) is None)
            switched = switches_to_dual(disagree, n)
            taken = set(sample)
            rest = [r for r in eligible if r not in taken]
            a |= screen("screener_a", rest)
            if switched:
                b |= screen("screener_b", rest)
            dual = taken | (set(rest) if switched else set())
            _note(ctx, phase, "scoping_sample", protocol_hash=protocol_hash, sample=sample, disagreements=disagree,
                  switched=switched)
        disagreements = []
        for rid in _finalize(ctx, phase, eligible, dual, a, b):
            done = carried[rid].final if rid in carried else None
            if done is not None and done["decided_by"] in ("vote", "human"):  # a disagreement settled before
                set_final(ps, rid, ctx.attempt, phase, done["include"], done["decided_by"], done["reason"],
                          actor=ctx.actor, carried_from=done["carried_from"])
            else:
                disagreements.append(rid)
        if disagreements and intake.production_mode == "collab":
            chunks = [disagreements[i:i + BATCH] for i in range(0, len(disagreements), BATCH)]
            cids = [_open(ctx, phase, ids, batch=i + 1, batches=len(chunks)) for i, ids in enumerate(chunks)]
            return StageOutcome("waiting_human", checkpoint_id=cids[0],
                                reason=f"{len(disagreements)} screening disagreements await you in {len(cids)} "
                                       f"checkpoint{'s' if len(cids) > 1 else ''}" + _incomplete(incomplete))
        views = _views(ps, ctx.attempt, phase)
        for rid in disagreements:
            _vote_on(ctx, phase, rid, context, views)
        return self._after_phase(ctx, phase, incomplete)

    def _after_phase(self, ctx: StageContext, phase: str, incomplete: list[str]) -> StageOutcome:
        """Every record of ``phase`` has its final decision. The seam of the full-text phase (Task 6): after the
        title/abstract phase, full-text retrieval and screening of its includes follow here."""
        return StageOutcome("done", reason=_summary(ctx.ps, ctx.attempt, phase, incomplete))

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        opts = checkpoint.get("options") or {}
        phase = opts.get("phase")
        if checkpoint["kind"] != KIND or opts.get("attempt") != ctx.attempt or phase not in PHASE_WORDS:
            return StageOutcome("failed", reason=f"search_screen cannot apply checkpoint {checkpoint['id']} "
                                                 f"({checkpoint['kind']})")
        ps = ctx.ps
        for cp in _checkpoints(ps, ctx.stage, ctx.attempt, phase):
            if cp["status"] != "answered" or opened_in_place_of(ps, KIND, cp["id"]) is not None:
                continue
            ids = cp["options"]["record_ids"]
            why = _invalid(cp["answer"], ids)
            if why:
                _open(ctx, phase, ids, batch=cp["options"]["batch"], batches=cp["options"]["batches"],
                      replaces=cp["id"], why=f"Your answer to checkpoint {cp['id']} could not be applied: {why}.")
            else:
                _apply(ctx, phase, cp)
        still = [cp for cp in _checkpoints(ps, ctx.stage, ctx.attempt, phase) if cp["status"] == "open"]
        if still:
            mine = opened_in_place_of(ps, KIND, checkpoint["id"])
            if mine is not None and get_checkpoint(ps, mine)["status"] == "open":
                why = _invalid(checkpoint["answer"], opts["record_ids"])
                return StageOutcome("waiting_human", checkpoint_id=mine,
                                    reason=f"your answer could not be applied: {why}")
            return StageOutcome("waiting_human", checkpoint_id=still[0]["id"],
                                reason=f"{len(still)} screening disagreement checkpoint"
                                       f"{'s' if len(still) > 1 else ''} still open")
        return self._after_phase(ctx, phase, incomplete_searches(ps, ctx.attempt))

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        """Every screened record has its title/abstract final decision. (Task 6 adds the full-text phase, V-PRISMA
        and at least one included study.)"""
        recs = deduplicated(ps, attempt)
        decided = {f["record_id"] for f in finals(ps, attempt, phase=TITLE_ABSTRACT)}
        missing = sum(1 for r in recs if r["id"] not in decided)
        failures = [f"{missing} of {len(recs)} records have no title/abstract decision"] if missing else []
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: no screening decision can be deleted or narrowed into a valid one, so the run fails
        with the failures."""
        return StageOutcome("failed", reason="search_screen did not pass at the edge limit: "
                                             + ("; ".join(failures) or "no reason given"))
