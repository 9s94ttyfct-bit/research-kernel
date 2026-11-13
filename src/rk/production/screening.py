"""The screening of ``search_screen``: title/abstract screening (single, dual or the scoping review's dual sample)
with its disagreements, then full-text retrieval and full-text screening of its includes (the same screeners and
rules), V-PRISMA and the gate.

``SearchScreenWorker.run``:

1. Dual screening (a systematic review, and a scoping review's sample) needs ``screener_a`` and ``screener_b`` of two
   different known vendors (``screeners_problem``); without them the stage fails before it searches, and
   ``RunManager.start`` refuses a run that would reach the stage.
2. ``run_strategy`` (``rk.production.search``). Above the protocol's record cap (``check_record_cap``) the stage goes
   back to ``protocol`` with that reason, in P1 and P2. A search the strategy could not finish
   (``SearchResult.incomplete``, kept in the search log, each naming its source and query) sends a systematic or
   scoping review back to ``protocol`` too ("narrow it or drop the source"; the edge limits apply), here and when an
   answer is applied (``reports_prisma``: the PRISMA paper types). A critical review or research proposal goes on: the
   incomplete searches (``incomplete_searches``, read from the search log) are a limitation named in the stage's
   reason and in the PRISMA notes.
3. A record in a language outside the protocol's ``languages`` (``language_exclusions``) is excluded without
   screening: final ``no``, decided by ``rule``, reason ``"language"``.
4. The screening mode of the paper type (``screening_mode``):
   - systematic review: dual;
   - scoping review: single (``screener_a``) plus a dual sample of 10% of the records (``sample_size``, rounded up),
     drawn over the records in search order by a generator seeded with the frozen protocol's hash. When more than
     10% of the sample differ on included-or-not (``include_differs``: unclear and a missing decision count as
     include, so both unclear or the same direction is no difference; ``switches_to_dual``), every remaining record
     is screened by both screeners. The sample, its differences and the switch are noted (``scoping_sample``);
   - critical review and research proposal: single; the screener's reason is the selection reason.
5. Calibration (search and screening, 2026-11-12): before title/abstract screening, once per attempt, a record whose
   item has no abstract gets the one a source has (``fill_abstracts``): by PMID from PubMed ``efetch``, else by DOI
   from Europe PMC, written to the item as an ``item.merged`` naming the source. A failed lookup is added to the
   ``problem`` of a record still without an abstract (the per-item boundary), never fatal; the note
   ``abstracts_sought`` keeps the records sought across an interruption, and ``abstracts_filled`` says what was
   sought, filled by source and failed. Records go to a screener in batches of ``BATCH`` (title, abstract, year, venue,
   language). The screener task, in both phases, says to apply each criterion as written and not to exclude a study
   for the statistic, unit or scale it reports an eligible outcome in unless a criterion requires that form
   (``CRITERIA_AS_WRITTEN``; calibration, search and screening, 2026-11-12). Every decision is a ``screening.decided``. A decision on a record that is not in the batch, or a second
   one on a record, is ignored and noted (``stray_decision``); so is a decision without the reason its selection
   needs (``unreasoned``: for the PRISMA types an exclusion with a blank reason, at full text unless it names its
   criterion; for the others any decision with a blank reason), noted ``unreasoned_decision``. The records a batch's
   output leaves out (a failed task leaves out all of them) go to that screener once more, in new batches (a
   reasonless one with ``asked_again`` saying why); a record still undecided is noted (``missing_decision``) and gets
   the record's ``problem``, and counts as ``unclear`` in single screening (so it goes to the full text, or is
   included at full text; the final's reason states that rule and is its selection reason) and as a disagreement in
   dual screening.
6. The final decision of a record (``screening.final``):
   - dual: both yes, or yes and unclear: include (``agreement``); both no: exclude (``agreement``); at
     title/abstract both unclear: include, to the full text (``agreement``, reason ``BOTH_UNCLEAR`` then the two
     decisions; calibration, search and screening, 2026-11-12); otherwise (yes and no, no and unclear, a missing
     decision, and at full text both unclear): a disagreement;
   - single: yes, or unclear (to the full text, as its reason says): include; no: exclude (``single``).
7. Disagreements:
   - P2: ``screening_disagreements`` checkpoints (the same kind in both phases, the phase in the options) of at most
     ``BATCH`` records each, answered ``{"decisions": {record_id: "include" | "exclude"}}`` with a decision for every
     record of that checkpoint and for no other. An invalid answer gets a replacement checkpoint saying why. Answers
     apply in any order; the attempt waits on the first checkpoint still open. Applying an answer again changes
     nothing (``decided_by`` ``human``).
   - P1: ``vote()`` per record (no generator vendors: a record is no generated text). The conservative option is per
     phase (``CONSERVATIVE``; calibration, search and screening, 2026-11-12): at title/abstract "include" (to the
     full text, screened there by both screeners), and the question asks whether the record may meet the criteria,
     excluding it only when its title and abstract show it fails one; at full text "exclude". Every vote's final is
     decided by ``vote`` and its reason starts "screeners disagreed" ("screeners disagreed; P1 conservative: to the
     full text" or "…: excluded" when the vote took the conservative option); ``voted_out`` tells its exclusions
     (by the decision, never by the conservative option) apart for PRISMA and the limitations.
8. An attempt interrupted by a rate cap, a budget or a crash is resumed by the next attempt
   (``rk.production.resume``), in both phases: the screener decisions of the interrupted attempts right before it,
   under the same frozen protocol, are carried over by library item (``carried_from``) instead of asked again, and so
   is the vote or human final of a disagreement when it was made on exactly the carried decisions
   (``Carried.settled``: every screener's decision carried, none made after it), written before the rules decide, so
   it stays as it was made even where the rules have changed since (both unclear at title/abstract); a rerun of an
   attempt that ran to completion screens afresh. At full text a decision is carried only when it was made on the
   same kind of text (a full text, or the abstract alone). Within one attempt a phase is resumable too: in P2 the full-text phase runs
   while an answer is applied, and an interruption there (a rate cap, a budget, a crash) applies the answer again in
   the same attempt. A phase run again keeps the attempt's own screener decisions and finals, screens only the
   records that have neither a final nor a checkpoint, and opens checkpoints only for the disagreements none holds
   yet (``_screen_phase``).
9. Full text (``_full_text``, once the title/abstract phase is settled): every title/abstract include's full text is
   sought through ``sources.fulltext`` inside the per-item boundary (``rk.discovery.boundary.per_item``: a source,
   IO or parse failure is added to the record's ``problem``, never fatal). A record with a full text is
   ``retrieved``; without one but with an abstract it is ``abstract_only`` and assessed on its abstract; with neither
   it is not retrieved and not screened. Every addition to ``problem`` is appended ("; "), never overwriting. The
   retrieval is noted (``full_text_retrieval``: the records sought, retrieved, abstract only and not retrieved).
10. Full-text screening: the rules of 4-8 with phase ``full_text`` (a scoping review that switched to dual
   screening at title/abstract stays dual; otherwise it draws a new 10% sample of the full-text records). A screener
   reads the full text's abstract, methods and results sections (``fulltext_excerpt``: found by their headings, cut
   to ``FULLTEXT_CHARS`` in all; the whole text's beginning when it has none of them), or the abstract of an
   abstract-only record. Single screening includes ``unclear`` here too, as its reason says.
11. The end (``_finish``): no included study sends the stage back to ``protocol`` ("no study met the criteria");
   else it is done, its reason the phases' counts and, for a systematic or scoping review, the PRISMA flow
   (``rk.verify.prisma``). The gate: every record has the final of each phase it reached, the full text of every
   title/abstract include was sought, V-PRISMA passes (systematic and scoping reviews) or every selection decision
   carries its reason (critical review, research proposal), and at least one study is included."""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from rk.checkpoints import (
    answered_by_actor,
    create_checkpoint,
    get_checkpoint,
    opened_in_place_of,
)
from rk.discovery.boundary import per_item
from rk.discovery.models import Intake
from rk.discovery.sources import ABSTRACT_SOURCES
from rk.discovery.store import intake_of
from rk.fulltext import latest_snapshot
from rk.library.service import Library
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.production.models import (
    FULL_TEXT,
    PHASE_WORDS,
    TITLE_ABSTRACT,
    Include,
    Protocol,
)
from rk.production.resume import Carried, carried_screening, mark_protocol, protocol_of
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
    add_screening_note,
    decisions,
    finals,
    frozen_protocol,
    record_row,
    retrieval_note,
    screening_notes,
    set_final,
    set_record,
    voted_out,
)
from rk.production.vote import vote
from rk.sources import europepmc, pubmed
from rk.store.project import ProjectStore
from rk.verify.prisma import check_prisma, check_selection_reasons, flow, prisma_counts

KIND = "screening_disagreements"
BATCH = 25  # records per screener task, and disagreements per checkpoint
CHOICES = ("include", "exclude")
# the P1 vote's conservative option per phase (calibration, search and screening, 2026-11-12): at title/abstract the
# full text settles the doubt, at full text the record is excluded
CONSERVATIVE = {TITLE_ABSTRACT: "include", FULL_TEXT: "exclude"}
SCREENERS = ("screener_a", "screener_b")
SAMPLE_PERCENT = 10  # the scoping review's dual sample, rounded up
SWITCH_PERCENT = 10  # more differences on included-or-not than this share of the sample: dual for the rest
ABSTRACT_SHOWN = 800  # characters of an abstract a disagreement checkpoint shows
EXCERPT_SHOWN = 2400  # characters of a full-text excerpt a disagreement checkpoint shows, shared by its sections
FULLTEXT_CHARS = 6000  # characters of a full text a full-text screener reads
NO_STUDY = "no study met the criteria"

ScreeningMode = Literal["dual", "sampled", "single"]
MODES: dict[str, ScreeningMode] = {"systematic_review": "dual", "scoping_review": "sampled"}
PRISMA_TYPES = frozenset({"systematic_review", "scoping_review"})  # PRISMA counts; an incomplete search blocks

# Calibration (search and screening, 2026-11-12, docs/eval/search-screen-2026-11-12.md section 4): ozone's included
# studies were excluded by misreading criteria that admitted them (a listed comparator; L*a*b* values ΔE is computed
# from), tfa's for the form their outcome is reported in.
CRITERIA_AS_WRITTEN = (
    "Apply each criterion as written: a criterion that admits data from which an outcome can be computed is met by "
    "reported values that allow the computation, and a comparator or intervention the criterion lists is eligible. "
    "Do not exclude a study that measures an eligible outcome because of the statistic, unit or scale it reports it "
    "in, unless a criterion explicitly requires that form.")
SCREEN_TASK = (
    "Screen every given record against the eligibility criteria, using only its title, abstract and metadata. Give "
    "exactly one decision per record, keyed by its record_id exactly as given: include \"yes\", \"no\" or "
    "\"unclear\" (when the title and abstract do not settle a criterion), the id of the criterion the decision rests "
    "on, and a short reason. " + CRITERIA_AS_WRITTEN + " Decide only the given records. Do not search or use tools.")
FULL_TEXT_TASK = (
    "Screen every given record against the eligibility criteria, using its text and metadata. The text is the "
    "abstract, methods and results sections of its full text (text_kind \"sections\"), the beginning of its full "
    "text when no such sections were found (\"whole_text\"), or its abstract alone when no full text could be "
    "retrieved (\"abstract_only\"). Give exactly one decision per record, keyed by its record_id exactly as given: "
    "include \"yes\", \"no\" or \"unclear\" (when the text does not settle a criterion), the id of the criterion the "
    "decision rests on, and a short reason. " + CRITERIA_AS_WRITTEN + " Decide only the given records. Do not search "
    "or use tools.")
TASKS = {TITLE_ABSTRACT: SCREEN_TASK, FULL_TEXT: FULL_TEXT_TASK}
ASKED_AGAIN = ("your previous decision on this record gave no reason (an exclusion with no reason and, at full text, "
               "no criterion; in a critical review or research proposal any decision): a reason is required")


def _vote_question(phase: str) -> str:
    if phase == TITLE_ABSTRACT:
        return (f"The two screeners disagree on this record at {PHASE_WORDS[phase]} screening. Include it when it may "
                "meet the eligibility criteria (its full text is then screened by both screeners); exclude it only "
                "when its title and abstract show it fails a criterion.")
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


def reports_prisma(paper_type: str) -> bool:
    """Whether the paper type reports PRISMA counts (systematic and scoping reviews): V-PRISMA gates its screening and
    an incomplete search keeps ``search_screen`` from completing. The others report the selection reasons instead,
    and an incomplete search is a limitation."""
    return paper_type in PRISMA_TYPES


def unreasoned(d: ScreenDecision, phase: str, paper_type: str) -> bool:
    """Whether a screener's decision lacks the reason its selection needs (``_batch`` treats it as not given): for
    the PRISMA types an exclusion with a blank reason (at full text the criterion it rests on is its reason, as in
    ``rk.verify.prisma``), for the others any decision with a blank reason (the selection reason)."""
    if d.reason.strip():
        return False
    if not reports_prisma(paper_type):
        return True
    return d.include == "no" and not (phase == FULL_TEXT and d.criterion_id)



def screener_pair_problem(drivers: dict[str, str], what: str = "dual screening") -> str:
    """Why the screeners of ``drivers`` (every role configured) cannot dual-screen (both of known vendors, and
    different), else ``""``."""
    specs = [DriverSpec.parse(drivers[role]) for role in SCREENERS]
    vendors = [vendor_of(s.provider, s.model) for s in specs]
    if None not in vendors and len(set(vendors)) == len(SCREENERS):
        return ""
    seen = " and ".join(f"{r} ({s}) is {v or 'of an unknown vendor'}" for r, s, v in zip(SCREENERS, specs, vendors,
                                                                                         strict=True))
    return (f"{what} needs screener_a and screener_b of two different known vendors, but {seen}. Set "
            "drivers.screener_a or drivers.screener_b in config.toml (rk providers)")


def screeners_problem(settings, paper_type: str) -> str:
    """Why the configured screeners cannot dual-screen for ``paper_type`` (``screener_pair_problem``), else ``""``;
    single screening has no such rule."""
    if screening_mode(paper_type) == "single":
        return ""
    return screener_pair_problem(settings.drivers, f"dual screening ({paper_type})")


def sample_size(n: int) -> int:
    """The scoping review's dual sample of ``n`` records: ``SAMPLE_PERCENT`` of them, rounded up."""
    return -(-n * SAMPLE_PERCENT // 100)


def include_differs(a: str | None, b: str | None) -> bool:
    """Whether two decisions differ on included-or-not, the scoping sample's measure: ``unclear`` counts as include
    (as in single screening, where it goes to the full text), and so does a missing decision (single screening's
    rule). Both unclear, or the same direction, is no difference."""
    return (a == "no") != (b == "no")


def switches_to_dual(disagreements: int, sampled: int) -> bool:
    """Whether the sample's differences on included-or-not are more than ``SWITCH_PERCENT`` of it."""
    return sampled > 0 and disagreements * 100 > SWITCH_PERCENT * sampled


BOTH_UNCLEAR = "both screeners unclear at title/abstract: the full text decides"


def dual_rule(a: str | None, b: str | None, phase: str) -> Include | None:
    """The final of a dual-screened record in ``phase`` from its two decisions (``None``: missing): ``"yes"``,
    ``"no"``, or ``None`` for a disagreement. Calibration (search and screening, 2026-11-12): at title/abstract both
    ``unclear`` is ``"yes"`` (``BOTH_UNCLEAR``: the full text decides); at full text it is a disagreement."""
    pair = {a, b}
    if "yes" in pair and pair <= {"yes", "unclear"}:
        return "yes"
    if phase == TITLE_ABSTRACT and a == b == "unclear":
        return "yes"
    if pair == {"no"}:
        return "no"
    return None


# ---- notes ----
def _note(ctx: StageContext, phase: str | None, kind: str, **fields) -> None:
    add_screening_note(ctx.ps, ctx.attempt, phase, kind, actor=ctx.actor, **fields)


# ---- screening ----
def record_input(lib: Library, rec: dict) -> dict:
    """What a screener (and a judge) sees of a record at title/abstract screening."""
    item = lib.get(rec["item_id"])
    out = {"record_id": rec["id"], "title": item.title, "abstract": item.abstract or "", "year": item.year,
           "venue": item.venue, "language": rec["language"]}
    if note := language_note(rec["language"]):
        out["language_note"] = note
    return out


def _lookup_id(lib: Library, item_id: str, source: str) -> str | None:
    ids = lib.get(item_id).ids
    return ids.pmid if source == "pubmed" else ids.doi


LOOKUP_BATCH = {"pubmed": pubmed.EFETCH_BATCH, "europepmc": europepmc.DOI_BATCH}  # ids per abstract lookup


def _filled_by(ps: ProjectStore, item_ids: list[str]) -> dict[str, str]:
    """item id -> the source whose abstract it took (its ``item.merged`` naming a ``source``)."""
    rows = ps.db.execute("SELECT entity_id, json_extract(payload, '$.source') AS source FROM events "
                         "WHERE type='item.merged' AND json_extract(payload, '$.source') IS NOT NULL")
    wanted = set(item_ids)
    return {r["entity_id"]: r["source"] for r in rows if r["entity_id"] in wanted}


def fill_abstracts(ctx: StageContext, recs: list[dict]) -> None:
    """Give the records of ``recs`` whose item has no abstract the one a source has, before title/abstract screening:
    by PMID from PubMed, then the rest by DOI from Europe PMC (``ABSTRACT_SOURCES``), ``LOOKUP_BATCH`` ids a lookup.
    Each abstract found goes to its item (``Library.fill_abstract``, naming the source). A lookup runs inside the
    per-item boundary, never fatal; its failure is added to the problem of each record it asked for that is still
    without an abstract at the end. The records sought are noted (``abstracts_sought``) before the first lookup, so a
    run interrupted between lookups (a paused budget) goes on with the same records. Once per attempt: the note
    ``abstracts_filled`` (the records ``sought``, those ``filled`` by source, read from their items' fill events, and
    those ``failed``: a lookup failed and they are still without an abstract) marks it done, so the phase run again
    fetches nothing twice."""
    ps, lib = ctx.ps, ctx.lib
    if screening_notes(ps, ctx.attempt, kind="abstracts_filled", phase=TITLE_ABSTRACT):
        return
    by_id = {r["id"]: r for r in recs}
    if noted := screening_notes(ps, ctx.attempt, kind="abstracts_sought", phase=TITLE_ABSTRACT):
        sought = [by_id[rid] for rid in noted[0]["sought"] if rid in by_id]
    else:
        sought = [r for r in recs if not (lib.get(r["item_id"]).abstract or "").strip()]
        _note(ctx, TITLE_ABSTRACT, "abstracts_sought", sought=[r["id"] for r in sought])

    def missing(r: dict) -> bool:
        return not (lib.get(r["item_id"]).abstract or "").strip()

    def lookup(source: str, ids: list[str]) -> dict[str, str]:
        return ctx.sources.abstracts(source, ids)

    errors: dict[str, list[str]] = {}
    for source in ABSTRACT_SOURCES:
        ask = [(r, ident) for r in sought if missing(r) and (ident := _lookup_id(lib, r["item_id"], source))]
        for i in range(0, len(ask), LOOKUP_BATCH[source]):
            chunk = ask[i:i + LOOKUP_BATCH[source]]
            got, err = per_item(f"abstract lookup in {source}", lambda s=source, c=chunk: lookup(s, [x for _, x in c]))
            for r, ident in chunk:
                if err:
                    errors.setdefault(r["id"], []).append(err)
                elif text := (got or {}).get(ident):
                    lib.fill_abstract(r["item_id"], text, source=source, actor=ctx.actor)
    still = [r["id"] for r in sought if missing(r)]
    for rid in still:
        if rid in errors:
            _add_problem(ctx, rid, *errors[rid])
    source_of = _filled_by(ps, [r["item_id"] for r in sought])
    filled = {s: [r["id"] for r in sought if source_of.get(r["item_id"]) == s] for s in ABSTRACT_SOURCES}
    _note(ctx, TITLE_ABSTRACT, "abstracts_filled", sought=[r["id"] for r in sought], filled=filled,
          failed=[rid for rid in still if rid in errors])


# ---- full text ----
SECTIONS = ("abstract", "methods", "results")  # what full-text screening reads of a full text, in this order
_SECTION_HEADINGS = {
    "abstract": "abstract", "summary": "abstract",
    "methods": "methods", "method": "methods", "materials and methods": "methods", "material and methods": "methods",
    "methods and materials": "methods", "methodology": "methods", "patients and methods": "methods",
    "subjects and methods": "methods", "participants and methods": "methods",
    "results": "results", "result": "results", "findings": "results", "results and discussion": "results",
}
_OTHER_HEADINGS = frozenset({
    "introduction", "background", "discussion", "conclusion", "conclusions", "discussion and conclusions",
    "limitations", "references", "bibliography", "acknowledgements", "acknowledgments", "funding", "keywords",
    "key words", "conflict of interest", "conflicts of interest", "competing interests", "declarations",
    "author contributions", "data availability", "supplementary material", "appendix", "abbreviations"})
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[ivx]+\.)\s+")


def _heading(line: str) -> str | None:
    """What ``line`` heads when it is a heading of its own (numbered or not, any case, a trailing colon): one of
    ``SECTIONS``, ``""`` for another known section, else ``None`` (no heading)."""
    s = _NUMBERING.sub("", " ".join(line.split()).lower()).rstrip(":.").strip()
    if s in _SECTION_HEADINGS:
        return _SECTION_HEADINGS[s]
    return "" if s in _OTHER_HEADINGS else None


def fulltext_excerpt(text: str, limit: int = FULLTEXT_CHARS) -> tuple[str, list[str]]:
    """What full-text screening reads of a full text: its abstract, methods and results sections (each the lines
    after its heading up to the next known heading; a section headed twice, e.g. in a structured abstract and the
    body, is joined), each headed by its name, at most ``limit`` characters in all. Each section gets an equal share
    of the room; what a shorter one leaves goes to the longer ones. Returns the excerpt and the sections found; with
    none of them, the text's first ``limit`` characters and ``[]``."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in re.split(r"[\n\f]", text):
        kind = _heading(line)
        if kind is not None:
            current = kind or None
        elif current is not None and line.strip():
            found.setdefault(current, []).append(line.strip())
    names = [s for s in SECTIONS if s in found]
    if not names:
        return text.strip()[:limit], []
    bodies = {s: "\n".join(found[s]) for s in names}
    left = limit - sum(len(s) + 1 for s in names) - 2 * (len(names) - 1)  # the headings and the blank lines
    cut: dict[str, str] = {}
    for i, s in enumerate(sorted(names, key=lambda s: len(bodies[s]))):
        cut[s] = bodies[s][:max(left, 0) // (len(names) - i)]
        left -= len(cut[s])
    return "\n\n".join(f"{s.capitalize()}\n{cut[s]}" for s in names), names


def full_text_input(ps: ProjectStore, lib: Library, rec: dict) -> dict:
    """What a screener (and a judge) sees of a record at full-text screening: its metadata and ``text``, the
    excerpt of its full text (``text_kind`` ``sections``, with the ``sections`` found, or ``whole_text``) or, for an
    abstract-only record, its abstract (``abstract_only``)."""
    item = lib.get(rec["item_id"])
    out = {"record_id": rec["id"], "title": item.title, "year": item.year, "venue": item.venue,
           "language": rec["language"]}
    snap = latest_snapshot(ps, rec["item_id"]) if rec["retrieved"] else None
    if snap is not None:
        text, sections = fulltext_excerpt(snap[1].full_text())
        out |= {"text_kind": "sections" if sections else "whole_text", "sections": sections, "text": text}
    else:
        out |= {"text_kind": "abstract_only", "text": item.abstract or ""}
    if note := language_note(rec["language"]):
        out["language_note"] = note
    return out


def phase_input(ps: ProjectStore, lib: Library, phase: str, rec: dict) -> dict:
    return full_text_input(ps, lib, rec) if phase == FULL_TEXT else record_input(lib, rec)


def _add_problem(ctx: StageContext, rid: str, *whys: str) -> None:
    """Add ``whys`` to the record's ``problem`` ("; "-joined), keeping what it says and leaving out what it already
    says."""
    before = record_row(ctx.ps, rid)["problem"] or ""
    new = [w for w in dict.fromkeys(whys) if f"; {w}; " not in f"; {before}; "]
    if new:
        set_record(ctx.ps, rid, actor=ctx.actor, problem="; ".join(([before] if before else []) + new))


def _retrieve(ctx: StageContext, rec: dict) -> str:
    """Seek the full text of a title/abstract include through the per-item boundary and record what was found:
    ``"retrieved"``, ``"abstract_only"`` (no full text, an abstract) or ``"not_retrieved"`` (neither). A failed
    lookup is added to the record's problem, as is the lack of a full text."""
    iid = rec["item_id"]
    got, err = per_item(f"full text of {iid}", lambda: ctx.sources.fulltext(iid) and latest_snapshot(ctx.ps, iid))
    has_text = bool(got) and bool(got[1].full_text().strip())
    whys = [err] if err else []
    if got and not has_text:
        whys.append(f"the full text of {iid} is empty")
    if has_text:
        kind = "retrieved"
    elif (ctx.lib.get(iid).abstract or "").strip():
        kind = "abstract_only"
        whys.append("no full text: assessed on the abstract")
    else:
        kind = "not_retrieved"
        whys.append("no full text and no abstract: not retrieved")
    set_record(ctx.ps, rec["id"], actor=ctx.actor, retrieved=kind == "retrieved", abstract_only=kind == "abstract_only")
    _add_problem(ctx, rec["id"], *whys)
    return kind


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
            got: dict[str, ScreenDecision], reasonless: set[str]) -> list[str]:
    """One screener task on ``batch``; its decisions go to ``got``. A record in ``reasonless`` (its earlier decision
    had no reason) is sent with ``asked_again`` saying so; a decision without its reason adds the record to it.
    Returns the records it left undecided."""
    inputs = [records[x] | ({"asked_again": ASKED_AGAIN} if x in reasonless else {}) for x in batch]
    r = ctx.executor.run(role, TASKS[phase], {**context, "records": inputs}, ScreenOut,
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
        if unreasoned(d, phase, context["paper_type"]):
            _note(ctx, phase, "unreasoned_decision", screener=role, record_id=d.record_id, include=d.include,
                  why="no reason")
            reasonless.add(d.record_id)
            continue
        got[d.record_id] = d
        add_decision(ctx.ps, ctx.attempt, d.record_id, phase, role, d.include, d.reason, d.criterion_id or None,
                     actor=ctx.actor)
    return [x for x in batch if x not in got]


def _decision(rid: str, row: dict) -> ScreenDecision:
    return ScreenDecision(record_id=rid, include=row["include"], criterion_id=row["criterion"], reason=row["reason"])


def _screen(ctx: StageContext, role: str, phase: str, records: dict[str, dict], context: dict,
            carried: dict[str, dict], own: dict[str, dict], settled: set[str]) -> dict[str, ScreenDecision]:
    """``role``'s decisions on ``records`` (record id -> input): first its ``own`` decisions this attempt already
    stored (record id -> decision row: the phase is run again), then those ``carried`` over from an interrupted
    attempt (record id -> decision row), then the others in batches of ``BATCH``, except the ``settled`` records (a
    final or a checkpoint already holds them), which are not asked again. The records left undecided go once more, in
    new batches; those still undecided are noted and get the record's ``problem``."""
    got: dict[str, ScreenDecision] = {}
    for rid in records:
        if (row := own.get(rid)) is not None:
            got[rid] = _decision(rid, row)
        elif (row := carried.get(rid)) is not None:
            got[rid] = _decision(rid, row)
            add_decision(ctx.ps, ctx.attempt, rid, phase, role, row["include"], row["reason"], row["criterion"],
                         actor=ctx.actor, actor_kind="kernel", carried_from=row["carried_from"])
    pending = [r for r in records if r not in got and r not in settled]
    reasonless: set[str] = set()
    for _ in range(2):
        left: list[str] = []
        for i in range(0, len(pending), BATCH):
            left += _batch(ctx, role, phase, pending[i:i + BATCH], records, context, got, reasonless)
        pending = left
        if not pending:
            break
    noted = {n["record_id"] for n in screening_notes(ctx.ps, ctx.attempt, kind="missing_decision", phase=phase)
             if n["screener"] == role}  # the phase run again in the attempt notes a record once
    for rid in pending:
        if rid not in noted:
            _note(ctx, phase, "missing_decision", screener=role, record_id=rid)
        _add_problem(ctx, rid, f"{role} gave no {PHASE_WORDS[phase]} decision, also when asked again")
    return got


def _finalize(ctx: StageContext, phase: str, rids: list[str], dual: set[str], a: dict[str, ScreenDecision],
              b: dict[str, ScreenDecision], settled: set[str]) -> list[str]:
    """Write the final of every record the rules settle, except the ``settled`` ones (a final or a checkpoint already
    holds them); returns the disagreements, in order."""
    disagreements: list[str] = []
    for rid in rids:
        if rid in settled:
            continue
        da, db = a.get(rid), b.get(rid)
        if rid in dual:
            inc = dual_rule(_include(a, rid), _include(b, rid), phase)
            if inc is None:
                disagreements.append(rid)
                continue
            said = f"{_say('screener_a', da)}; {_say('screener_b', db)}"
            both_unclear = _include(a, rid) == _include(b, rid) == "unclear"
            set_final(ctx.ps, rid, ctx.attempt, phase, inc, "agreement",
                      f"{BOTH_UNCLEAR}; {said}" if both_unclear else said, actor=ctx.actor)
            continue
        # single screening: unclear (and a missing decision, as unclear) includes, at title/abstract to the full text
        then = "so it goes to the full text" if phase == TITLE_ABSTRACT else "so it is included (single screening)"
        if da is None:
            reason = f"screener_a gave no decision, also when asked again: treated as unclear, {then}"
        elif da.include == "unclear":
            reason = f"{_say('screener_a', da)}; unclear, {then}"
        else:
            reason = _say("screener_a", da)
        set_final(ctx.ps, rid, ctx.attempt, phase, "no" if da is not None and da.include == "no" else "yes",
                  "single", reason, actor=ctx.actor)
    return disagreements


def _no_decision(screener: str) -> dict:
    return {"screener": screener, "include": None, "criterion": None, "reason": "no decision"}


def _views(ps: ProjectStore, attempt: int, phase: str) -> dict[str, list[dict]]:
    """record id -> what each screener decided on it (``include`` None: no decision)."""
    by: dict[str, dict[str, dict]] = {}
    for d in decisions(ps, attempt, phase=phase):
        by.setdefault(d["record_id"], {})[d["screener"]] = {
            "screener": d["screener"], "include": d["include"], "criterion": d["criterion"], "reason": d["reason"]}
    return {rid: [got.get(s) or _no_decision(s) for s in SCREENERS] for rid, got in by.items()}


def _view_of(views: dict[str, list[dict]], rid: str) -> list[dict]:
    return views.get(rid) or [_no_decision(s) for s in SCREENERS]


# ---- disagreements ----
def _checkpoints(ps: ProjectStore, stage: str, attempt: int, phase: str) -> list[dict]:
    rows = ps.db.execute("SELECT id FROM checkpoints WHERE kind=? AND stage=? AND json_extract(options, '$.attempt')=? "
                         "AND json_extract(options, '$.phase')=? ORDER BY rowid", (KIND, stage, attempt, phase))
    return [get_checkpoint(ps, r["id"]) for r in rows.fetchall()]


TEXT_LABELS = {"sections": "Full text (abstract, methods, results)", "whole_text": "Full text (its beginning)",
               "abstract_only": "Abstract only (no full text)"}


def disagreement_view(ps: ProjectStore, lib: Library, attempt: int, phase: str, record_ids: list[str]) -> list[dict]:
    """What a ``screening_disagreements`` checkpoint shows of each of its records (its prompt, and the Workbench's
    card): ``record_id``, ``title``, ``year``, ``venue``, ``language``, the ``text`` the screeners read as far as it is
    shown (an abstract cut to ``ABSTRACT_SHOWN``; at full text the excerpt cut to ``EXCERPT_SHOWN``, every section
    keeping its share), its ``text_label``, and what each screener decided (``screeners``)."""
    views = _views(ps, attempt, phase)
    out = []
    for rid in record_ids:
        rec = phase_input(ps, lib, phase, record_row(ps, rid))
        if phase == FULL_TEXT:
            label, text = TEXT_LABELS[rec["text_kind"]], rec["text"]
            if rec["text_kind"] == "sections":  # every section keeps its share of the room
                shown = fulltext_excerpt(text, EXCERPT_SHOWN)[0]
            else:
                shown = text[:EXCERPT_SHOWN] + "…" if len(text) > EXCERPT_SHOWN else text
        else:
            label, text = "Abstract", rec["abstract"]
            shown = text[:ABSTRACT_SHOWN] + "…" if len(text) > ABSTRACT_SHOWN else text
        out.append({"record_id": rid, "title": rec["title"], "year": rec["year"], "venue": rec["venue"],
                    "language": rec["language"], "text_label": label, "text": shown,
                    "screeners": _view_of(views, rid)})
    return out


def _prompt(ctx: StageContext, protocol: Protocol, phase: str, record_ids: list[str], batch: int, batches: int,
            why: str) -> str:
    lines = [why, ""] if why else []
    head = (f"Screening disagreements ({PHASE_WORDS[phase]} screening), checkpoint {batch} of {batches}: the two "
            f"screeners disagree on these {len(record_ids)} records.")
    lines += [head, "", "Eligibility criteria:"]
    lines += [f"- {c.id} (include): {c.text}" for c in protocol.eligibility.include]
    lines += [f"- {c.id} (exclude): {c.text}" for c in protocol.eligibility.exclude]
    empty = "(no text)" if phase == FULL_TEXT else "(no abstract)"
    for rec in disagreement_view(ctx.ps, ctx.lib, ctx.attempt, phase, record_ids):
        meta = ", ".join(str(x) for x in (rec["year"], rec["venue"]) if x) or "no year or venue"
        body = (rec["text"] or empty).splitlines() or [empty]
        lines += ["", f"- {rec['record_id']}: {rec['title']} ({meta}; language {rec['language']})",
                  f"  {rec['text_label']}: {body[0]}"]
        lines += [f"    {line}" for line in body[1:]]
        lines += [f"  {_said(v['screener'], v['include'], v['criterion'], v['reason'])}" for v in rec["screeners"]]
    lines += ["", ('Decide each record: {"decisions": {"<record_id>": "include" | "exclude", ...}}, one decision for '
                   "every record above and for no other.")]
    return "\n".join(lines)


def _open(ctx: StageContext, protocol: Protocol, phase: str, record_ids: list[str], *, batch: int, batches: int,
          replaces: str | None = None, why: str = "") -> str:
    """A ``screening_disagreements`` checkpoint on ``record_ids``; opened in answer to ``replaces``, it is opened
    once."""
    if replaces is not None and (existing := opened_in_place_of(ctx.ps, KIND, replaces)) is not None:
        return existing
    return create_checkpoint(ctx.ps, KIND, _prompt(ctx, protocol, phase, record_ids, batch, batches, why),
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


def _vote_on(ctx: StageContext, phase: str, rid: str, context: dict, record: dict,
             views: dict[str, list[dict]]) -> None:
    inputs = {**context, "record": record, "screeners": _view_of(views, rid)}
    res = vote(ctx, "record", rid, _vote_question(phase), list(CHOICES), CONSERVATIVE[phase], inputs)
    inc = "yes" if res.decision == "include" else "no"
    if res.conservative_used:
        what = "to the full text" if inc == "yes" else "excluded"
        reason = f"screeners disagreed; P1 conservative: {what} ({res.reason}; not reviewed by a human)"
    else:
        reason = (f"screeners disagreed; the P1 vote {'included' if inc == 'yes' else 'excluded'} it (not reviewed "
                  "by a human)")
    set_final(ctx.ps, rid, ctx.attempt, phase, inc, "vote", reason, actor=ctx.actor)


def _incomplete_block(items: list[str]) -> str:
    return f"the search is incomplete: {'; '.join(items)}. Narrow it or drop the source"


def _incomplete(items: list[str]) -> str:
    return f"; incomplete searches (noted as a limitation): {'; '.join(items)}" if items else ""


def _phase_summary(ps: ProjectStore, attempt: int, phase: str) -> str:
    fs = finals(ps, attempt, phase=phase)
    included = sum(1 for f in fs if f["include"] == "yes")
    excluded = Counter(f["decided_by"] for f in fs if f["include"] == "no")
    voted = sum(1 for f in fs if f["decided_by"] == "vote")
    human = sum(1 for f in fs if f["decided_by"] == "human")
    details = []
    if excluded["rule"]:
        details.append(f"{excluded['rule']} for language")
    if voted:
        details.append(f"{voted} disagreements decided by the P1 vote (not reviewed by a human): "
                       f"{sum(1 for f in fs if voted_out(f))} excluded by the P1 vote on a disagreement")
    if human:
        details.append(f"{human} disagreements decided by you")
    out = f"{PHASE_WORDS[phase]} screening: {included} included, {sum(excluded.values())} excluded"
    return out + (f" ({'; '.join(details)})" if details else "")


def _summary(ps: ProjectStore, attempt: int, paper_type: str, incomplete: list[str]) -> str:
    """The stage's reason: both phases' counts, the full-text retrieval, and the PRISMA flow (systematic and scoping
    reviews) or whether the selection reasons are complete (the others)."""
    parts = [_phase_summary(ps, attempt, TITLE_ABSTRACT)]
    if noted := retrieval_note(ps, attempt):
        n = {k: len(noted[k]) for k in ("sought", "retrieved", "abstract_only", "not_retrieved")}
        parts.append(f"full text: {n['sought']} sought, {n['retrieved']} retrieved, {n['abstract_only']} assessed on "
                     f"the abstract only, {n['not_retrieved']} not retrieved")
        parts.append(_phase_summary(ps, attempt, FULL_TEXT))
    if reports_prisma(paper_type):
        parts.append(flow(prisma_counts(ps, attempt)))
    else:
        parts.append("selection reasons: " + ("; ".join(check_selection_reasons(ps, attempt)) or "complete"))
    return "; ".join(parts) + _incomplete(incomplete)


def _same_text(ps: ProjectStore, c: Carried, retrieved: int) -> Carried:
    """What ``c`` carries over of a full-text screening that was made on the same kind of text as now (a full text,
    or the abstract alone): a decision on the abstract does not stand for one on the full text, nor the reverse."""
    def same(row: dict) -> bool:
        return record_row(ps, row["record_id"])["retrieved"] == retrieved
    return Carried({s: d for s, d in c.decisions.items() if same(d)},
                   c.final if c.final is not None and same(c.final) else None)


# ---- the worker ----
class SearchScreenWorker:
    # An interrupted attempt (a rate cap while screening, a source failure, a crash) runs again in place
    # (rk.orchestrator.engine): the search replays its logged pages and keeps its records, screening keeps the
    # attempt's decisions, finals and checkpoints, so nothing is searched, stored or asked for twice.
    resumes_in_place = True

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
        if protocol_of(ps, ctx.stage, ctx.attempt) != row["frozen_hash"]:
            mark_protocol(ps, ctx.stage, ctx.attempt, row["frozen_hash"], actor=ctx.actor)
        protocol = Protocol.model_validate(row["body"])
        result = run_strategy(ctx, protocol)
        cap = check_record_cap(ps, ctx.attempt, protocol)
        if cap:
            return StageOutcome("back_edge", to="protocol", reason=cap)
        if result.incomplete and reports_prisma(intake.paper_type):
            return StageOutcome("back_edge", to="protocol", reason=_incomplete_block(result.incomplete))
        return self._title_abstract(ctx, intake, protocol, row["frozen_hash"], result.record_ids, result.incomplete)

    def _title_abstract(self, ctx: StageContext, intake: Intake, protocol: Protocol, protocol_hash: str,
                        record_ids: list[str], incomplete: list[str]) -> StageOutcome:
        ps, phase = ctx.ps, TITLE_ABSTRACT
        language = set(language_exclusions(ps, ctx.attempt, protocol))
        settled = {f["record_id"] for f in finals(ps, ctx.attempt, phase=phase)}
        for rid in record_ids:
            if rid in language and rid not in settled:
                set_final(ps, rid, ctx.attempt, phase, "no", "rule", "language", actor=ctx.actor)
        eligible = [r for r in record_ids if r not in language]
        recs = {r["id"]: r for r in deduplicated(ps, ctx.attempt)}
        fill_abstracts(ctx, [recs[rid] for rid in eligible])
        inputs = {rid: record_input(ctx.lib, recs[rid]) for rid in eligible}
        # the work of the interrupted attempts right before this one (rk.production.resume), by record of this one
        by_item = carried_screening(ps, ctx.stage, ctx.attempt, protocol_hash, phase)
        carried = {rid: c for rid in eligible if (c := by_item.get(recs[rid]["item_id"])) is not None}
        return self._screen_phase(ctx, intake, protocol, protocol_hash, phase, eligible, inputs, carried, incomplete)

    def _full_text(self, ctx: StageContext, incomplete: list[str]) -> StageOutcome:
        """Seek the full text of every title/abstract include (once per attempt: the retrieval note), then screen
        those with a full text or an abstract. Run again in the same attempt (an answer applied again after an
        interruption), it goes on from what the attempt stored."""
        ps, phase = ctx.ps, FULL_TEXT
        note = retrieval_note(ps, ctx.attempt)
        if note is None:
            ta = {f["record_id"]: f["include"] for f in finals(ps, ctx.attempt, phase=TITLE_ABSTRACT)}
            kinds = {r["id"]: _retrieve(ctx, r) for r in deduplicated(ps, ctx.attempt) if ta.get(r["id"]) == "yes"}
            _note(ctx, phase, "full_text_retrieval", sought=list(kinds),
                  **{k: [r for r, got in kinds.items() if got == k] for k in ("retrieved", "abstract_only",
                                                                             "not_retrieved")})
            note = retrieval_note(ps, ctx.attempt)
        intake, prot = intake_of(ps), frozen_protocol(ps)
        unretrieved = set(note["not_retrieved"])
        rows = {rid: record_row(ps, rid) for rid in note["sought"] if rid not in unretrieved}
        inputs = {rid: full_text_input(ps, ctx.lib, row) for rid, row in rows.items()}
        by_item = carried_screening(ps, ctx.stage, ctx.attempt, prot["frozen_hash"], phase)
        carried = {rid: _same_text(ps, c, row["retrieved"]) for rid, row in rows.items()
                   if (c := by_item.get(row["item_id"])) is not None}
        return self._screen_phase(ctx, intake, Protocol.model_validate(prot["body"]), prot["frozen_hash"], phase,
                                  list(rows), inputs, carried, incomplete)

    def _screen_phase(self, ctx: StageContext, intake: Intake, protocol: Protocol, protocol_hash: str, phase: str,
                      eligible: list[str], inputs: dict[str, dict], carried: dict[str, Carried],
                      incomplete: list[str]) -> StageOutcome:
        """Screen ``eligible`` (their screener ``inputs``) in ``phase`` by the paper type's mode, settle what the rules
        settle and the disagreements (P2: checkpoints; P1: votes), reusing what is ``carried`` over. Resumable within
        the attempt: the attempt's own decisions are kept, a record with a final or a checkpoint is settled (neither
        screened again nor finalized again), and only the disagreements no checkpoint holds get one."""
        ps = ctx.ps
        context = _context(protocol, phase, intake.paper_type)
        own = {(d["record_id"], d["screener"]): d for d in decisions(ps, ctx.attempt, phase=phase)}
        held = [cp for cp in _checkpoints(ps, ctx.stage, ctx.attempt, phase) if cp["options"]["replaces"] is None]
        settled = ({f["record_id"] for f in finals(ps, ctx.attempt, phase=phase)}
                   | {rid for cp in held for rid in cp["options"]["record_ids"]})

        def screen(role: str, ids: list[str]) -> dict[str, ScreenDecision]:
            return _screen(ctx, role, phase, {r: inputs[r] for r in ids}, context,
                           {r: carried[r].decisions[role] for r in ids if r in carried and role in carried[r].decisions},
                           {r: own[(r, role)] for r in ids if (r, role) in own}, settled)

        mode = screening_mode(intake.paper_type)
        if mode == "sampled" and phase == FULL_TEXT and any(
                n["switched"] for n in screening_notes(ps, ctx.attempt, kind="scoping_sample", phase=TITLE_ABSTRACT)):
            mode = "dual"  # the stage switched to dual screening at title/abstract
        a: dict[str, ScreenDecision] = {}
        b: dict[str, ScreenDecision] = {}
        dual: set[str] = set()
        if mode == "single":
            a = screen("screener_a", eligible)
        elif mode == "dual":
            a, b, dual = screen("screener_a", eligible), screen("screener_b", eligible), set(eligible)
        else:
            n = sample_size(len(eligible))
            seed = protocol_hash if phase == TITLE_ABSTRACT else f"{protocol_hash}:{phase}"
            sample = [eligible[i] for i in sorted(random.Random(seed).sample(range(len(eligible)), n))]
            a, b = screen("screener_a", sample), screen("screener_b", sample)
            disagree = sum(1 for r in sample if include_differs(_include(a, r), _include(b, r)))
            switched = switches_to_dual(disagree, n)
            taken = set(sample)
            rest = [r for r in eligible if r not in taken]
            a |= screen("screener_a", rest)
            if switched:
                b |= screen("screener_b", rest)
            dual = taken | (set(rest) if switched else set())
            if not screening_notes(ps, ctx.attempt, kind="scoping_sample", phase=phase):
                _note(ctx, phase, "scoping_sample", protocol_hash=protocol_hash, seed=seed, sample=sample,
                      disagreements=disagree,
                      measure="differences on included-or-not (unclear and a missing decision count as include)",
                      switched=switched)
        # a vote's or your final carried over on exactly the carried decisions stays as it was made, before the rules
        # (which may have changed since: both unclear at title/abstract) decide the record again
        for rid in eligible:
            c = carried.get(rid) if rid not in settled else None
            done = c.settled(SCREENERS) if c is not None else None
            if done is not None and done["decided_by"] in ("vote", "human"):
                set_final(ps, rid, ctx.attempt, phase, done["include"], done["decided_by"], done["reason"],
                          actor=ctx.actor, carried_from=done["carried_from"])
                settled.add(rid)
        disagreements = _finalize(ctx, phase, eligible, dual, a, b, settled)
        if intake.production_mode == "collab":
            chunks = [disagreements[i:i + BATCH] for i in range(0, len(disagreements), BATCH)]
            for i, ids in enumerate(chunks):  # numbered after the checkpoints the attempt already opened
                _open(ctx, protocol, phase, ids, batch=len(held) + i + 1, batches=len(held) + len(chunks))
        else:
            views = _views(ps, ctx.attempt, phase)
            for rid in disagreements:
                _vote_on(ctx, phase, rid, context, inputs[rid], views)
        return self._settle(ctx, phase, incomplete)

    def _after_phase(self, ctx: StageContext, phase: str, incomplete: list[str]) -> StageOutcome:
        """Every record of ``phase`` has its final decision: after title/abstract comes the full text, after the full
        text the end."""
        if phase == TITLE_ABSTRACT:
            return self._full_text(ctx, incomplete)
        return self._finish(ctx, incomplete)

    def _finish(self, ctx: StageContext, incomplete: list[str]) -> StageOutcome:
        """Done, or back to ``protocol`` when no study met the criteria."""
        ps = ctx.ps
        reason = _summary(ps, ctx.attempt, intake_of(ps).paper_type, incomplete)
        if not any(f["include"] == "yes" for f in finals(ps, ctx.attempt, phase=FULL_TEXT)):
            return StageOutcome("back_edge", to="protocol", reason=f"{NO_STUDY}: {reason}")
        return StageOutcome("done", reason=reason)

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        opts = checkpoint.get("options") or {}
        phase = opts.get("phase")
        if checkpoint["kind"] != KIND or opts.get("attempt") != ctx.attempt or phase not in PHASE_WORDS:
            return StageOutcome("failed", reason=f"search_screen cannot apply checkpoint {checkpoint['id']} "
                                                 f"({checkpoint['kind']})")
        ps = ctx.ps
        incomplete = incomplete_searches(ps, ctx.attempt)
        if incomplete and reports_prisma(intake_of(ps).paper_type):
            return StageOutcome("back_edge", to="protocol", reason=_incomplete_block(incomplete))
        return self._settle(ctx, phase, incomplete, checkpoint)

    def _settle(self, ctx: StageContext, phase: str, incomplete: list[str],
                checkpoint: dict | None = None) -> StageOutcome:
        """Apply every answered checkpoint of ``phase`` (an invalid answer gets its replacement); wait on one still
        open (the replacement of ``checkpoint``, the one just answered, first), else go on after the phase."""
        ps = ctx.ps
        protocol = Protocol.model_validate(frozen_protocol(ps)["body"])
        for cp in _checkpoints(ps, ctx.stage, ctx.attempt, phase):
            if cp["status"] != "answered" or opened_in_place_of(ps, KIND, cp["id"]) is not None:
                continue
            ids = cp["options"]["record_ids"]
            why = _invalid(cp["answer"], ids)
            if why:
                _open(ctx, protocol, phase, ids, batch=cp["options"]["batch"], batches=cp["options"]["batches"],
                      replaces=cp["id"], why=f"Your answer to checkpoint {cp['id']} could not be applied: {why}.")
            else:
                _apply(ctx, phase, cp)
        still = [cp for cp in _checkpoints(ps, ctx.stage, ctx.attempt, phase) if cp["status"] == "open"]
        if still:
            mine = opened_in_place_of(ps, KIND, checkpoint["id"]) if checkpoint is not None else None
            if mine is not None and get_checkpoint(ps, mine)["status"] == "open":
                why = _invalid(checkpoint["answer"], checkpoint["options"]["record_ids"])
                return StageOutcome("waiting_human", checkpoint_id=mine,
                                    reason=f"your answer could not be applied: {why}")
            waiting = sum(len(cp["options"]["record_ids"]) for cp in still)
            return StageOutcome("waiting_human", checkpoint_id=still[0]["id"],
                                reason=f"{waiting} screening disagreements ({PHASE_WORDS[phase]}) await you in "
                                       f"{len(still)} checkpoint{'s' if len(still) > 1 else ''}"
                                       + _incomplete(incomplete))
        return self._after_phase(ctx, phase, incomplete)

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        """Every record has the final of each phase it reached (title/abstract: every screened record; full text:
        every title/abstract include with a full text or an abstract) and the full text of every title/abstract
        include was sought; then V-PRISMA passes (systematic and scoping reviews) or every selection decision carries
        its reason (critical review, research proposal), and at least one study is included."""
        recs = deduplicated(ps, attempt)
        ta = {f["record_id"]: f["include"] for f in finals(ps, attempt, phase=TITLE_ABSTRACT)}
        failures = []
        if missing := sum(1 for r in recs if r["id"] not in ta):
            failures.append(f"{missing} of {len(recs)} records have no title/abstract decision")
        sought = [r for r in recs if ta.get(r["id"]) == "yes"]
        note = retrieval_note(ps, attempt)
        looked = set(note["sought"]) if note else set()
        if unsought := sum(1 for r in sought if r["id"] not in looked):
            failures.append(f"{unsought} of {len(sought)} title/abstract includes were not sought for their full text")
        ft = {f["record_id"]: f["include"] for f in finals(ps, attempt, phase=FULL_TEXT)}
        unretrieved = set(note["not_retrieved"]) if note else set()
        reached = [r for r in sought if r["id"] in looked and r["id"] not in unretrieved]
        if missing := sum(1 for r in reached if r["id"] not in ft):
            failures.append(f"{missing} of {len(reached)} records assessed at full text have no full-text decision")
        if failures:  # the phases are not settled: PRISMA and the selection cannot be judged yet
            return GateResult(False, failures)
        if reports_prisma(intake_of(ps).paper_type):
            failures += [f"V-PRISMA: {f}" for f in check_prisma(prisma_counts(ps, attempt))]
        else:
            failures += check_selection_reasons(ps, attempt)
        if "yes" not in ft.values():
            failures.append(f"{NO_STUDY}: no study is included")
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: no screening decision can be deleted or narrowed into a valid one, so the run fails
        with the failures."""
        return StageOutcome("failed", reason="search_screen did not pass at the edge limit: "
                                             + ("; ".join(failures) or "no reason given"))
