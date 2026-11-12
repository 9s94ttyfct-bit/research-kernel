"""The protocol stage: the Ideator drafts the review protocol (eligibility, extraction schema, synthesis plan and, for a
systematic review, the PRISMA-P items) for the frozen RQ, the Scout its search strategy; the kernel builds the
``Protocol`` (the question is the frozen RQ's) with the facts only it knows (``with_kernel_facts``: the admin PRISMA-P
items of a systematic or scoping review, ``KERNEL_PRISMA_P``: registration, amendments, support and competing
interests, and the language exclusion ``E-lang``; the rendered protocol adds the search execution,
``search_execution_lines``, with the publication years written out), stores it as the
next version, checks it (``check_strategy``, ``check_protocol``), sizes the strategy (the record-cap probe:
``sources.count`` per query on each counted source of the strategy within its publication years, summed and stored
as ``protocol.probed``) and then:

- P2 (``collab``) opens ``protocol_approval`` (hard human; every section rendered, ``options.protocol_id`` and
  ``options.version`` name the protocol);
- P1 (``auto``) asks the two-vendor ``vote`` (``conservative="revise"``, ``generator_vendors`` = the Ideator's and the
  Scout's): ``approve`` of every admitted judge freezes the protocol, else the stage reruns. A version whose probe is
  still above the record cap is not voted on: only you may raise the cap, so P1 reruns with the cap as its reason and
  spends no judge round on a search ``search_screen`` would stop.

The judges' point ledger (P1; calibration: P1 protocol diagnosis 2026-11-12, §5.3 and §7). Each judge answers with
points (``ProtocolVoteOut``: section, fault, change, blocking or advisory) and marks its own earlier open points
resolved or not; the kernel numbers them ``p{attempt}.{role}.{n}`` (``points_ledger``, derived from the events, so a
point survives an edge-limit failure and a new run). A judge whose effective choice is revise but who leaves no
blocking point of its own open (a reason only, or advisory points only), or who answered outside the options, gets
one more blocking point built from its reason (section ``other``), so the next attempt always has what to answer. A
point is live (``live``) only while its vote was on a version of the current frozen research question and came after
the latest protocol freeze and the latest reopening of ``protocol`` by another edge than ``protocol->protocol`` (the
scope of ``_previous_draft``). Its
status is ``open``, ``resolved`` (the latest mark of its own judge in its cycle says so; only its own judge closes it;
it stays resolved when its cycle ends) or ``retired`` (it was open when its cycle ended): a retired point is kept for
the record but never served, never tightens a vote and never exempts a dropped PRISMA-P item. The judges see the
version rendered (``render_protocol``), what changed since the version voted on before (``changes``), every open
point with the authors' answers, and the kernel's rules sheet
(``KERNEL_RULES``). The vote's policy, changed on purpose: a judge answers approve when none of its blocking points is
open, so a protocol may freeze with advisory points open (they are retired at the freeze); the kernel only tightens,
never loosens: an approve with an open blocking point of the judge's own (new, earlier and not marked resolved, or
earlier and marked unresolved again) counts as revise (the opinion keeps its ``choice`` with ``effective`` revise,
and the vote's reason names it). Unanimity of the admitted judges is unchanged. The next attempt's Ideator and Scout
get the open points (``open_points``, each with its ``owner``: the Scout a search point, the Ideator every other)
instead of the vote's reasons as guidance (``_p1_guidance``), and answer each point routed to them (``PointAnswer``:
changed with where, or rebutted with why) inside the attempt: one left unanswered is a failure of that role's try,
retried without spending an edge. The answers are stored with the version's ``tried`` (a narrowing try merges the
Scout's new answers in by point id), never in the protocol body. P2 has no ledger: your comment is the guidance.

Tries inside the attempt (Plan 5c Task 9 fix: in P1 every rerun spends a ``protocol->protocol`` edge use, and nothing
else could narrow a search):

1. **Structural failures.** A version that fails the checks is drafted again in the attempt, up to
   ``Settings.protocol_tries`` versions: the role whose part failed (the Ideator for the draft, the Scout for the
   strategy, both when both failed) gets that version as ``previous_draft`` and its failures as guidance; the other
   part is kept. Only when the tries run out does the stage rerun, with the last version's failures as its reason.
   Besides the structure of the paper type, the checks hold what the judges kept raising that a rule finds in the
   body (P1 protocol diagnosis 2026-11-12, rules R1b/c, R5-R8): every PRISMA-P item of a systematic review
   (``PRISMA_P_TEXT``), no placeholder in the Ideator's texts, PRISMA-P texts that name only the strategy's sources
   and promise no search the kernel does not run, a systematic review's outcomes and study identifier field, no
   meta-analysis of diagnostic accuracy, queries whose parentheses balance and that mix no AND and OR at one depth,
   and no PRISMA-P item of a revised draft dropped (``dropped_prisma_p``). The kernel's own text (its PRISMA-P items
   and ``E-lang``) is not checked as the Ideator's.
2. **Narrowing.** When the probe of the sound version is above the project's record cap, the Scout is asked again in
   the attempt, up to ``Settings.protocol_narrow_tries`` times, for a narrower strategy (``narrowing``: the cap, the
   per-query and per-source counts of the best strategy so far, the failed tries). Each answer is a version (the
   draft kept, the strategy replaced), probed in turn. An answer that does not lower the total below the best so far
   (a repeated strategy, which takes the earlier probe and is not counted again, one that fails the checks or has no
   count, or a higher total) is a failed try. Narrowing stops when the best total is within the cap or the tries run
   out; the best version (the lowest total) then goes to your approval, its probe shown, or in P1 to the vote when
   its total is within the cap (else the stage reruns, with the cap as its reason).

Every try is a version (append-only), recorded with how it was drafted (``protocol.added`` ``tried``: its kind, the
vendors of its Ideator and Scout, its failures and its answers to the open points). An interrupted attempt runs again
in place (``resumes_in_place``): it goes on from the attempt's versions and probes, asks nothing it already has, takes
the vote already recorded and the approval already opened.

The probe is advisory to you: it is shown to you and to the judges, with the cap, as an upper bound, and fails no
check; in P1 a total above the cap keeps the version from the vote. Each count runs inside the per-item boundary (a
source error or an exhausted source API budget leaves that count ``None`` with its ``error``), so the probe never
fails or interrupts the stage. It is an upper bound (duplicates across queries and sources count again) and leaves out
the sources without a count; the record cap is enforced by ``search_screen`` on the deduplicated records.

A rerun after an unfrozen draft (a revision asked by you or by the vote, failed checks) gives both roles the version
the earlier attempt offered (the best of its narrowing), else its last version, as ``previous_draft`` (its body and
probe) to change as the guidance asks, but only a draft of the current frozen RQ that no back-edge left behind
(``protocol->question`` reopens the stage without one); a rerun after a freeze gives them the version in force as
``frozen_protocol`` when it answers the current frozen RQ (after ``protocol->question`` and a new RQ it does not, and
they draft afresh).

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

Budget per attempt (protocol is an open stage, 40 role tasks per run session): at most ``protocol_tries`` ideator and
scout tasks each, plus ``protocol_narrow_tries`` scout tasks (9 with the defaults 3 and 3), and 2 judge tasks in P1,
which do not count against the budget.

Gate: a frozen protocol in the latest attempt that passes ``check_protocol`` and has a deviation from the frozen
version before it, if any. A P1 degrade fails: P1 never freezes a protocol the vote did not approve."""

from __future__ import annotations

import bisect
import difflib
import json
import re
from collections.abc import Sequence
from typing import Literal, get_args

from pydantic import BaseModel, Field

from rk.checkpoints import (
    answered_by_actor,
    create_checkpoint,
    opened_for_attempt,
    opened_in_place_of,
)
from rk.config import load_settings
from rk.discovery.boundary import per_item
from rk.discovery.models import PaperType, RQBody
from rk.discovery.sources import COUNTED_SOURCES
from rk.discovery.store import frozen_rq, hypotheses, intake_of
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import kernel_grants
from rk.production.models import (
    RECORD_CAP,
    SCREENED_LANGUAGES,
    Criterion,
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
from rk.production.vote import VoteOut, VoteResult, vote
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
KERNEL_EXTRA_ROUNDS = 2  # protocol->protocol uses the kernel grants a converging P1 protocol (``another_use``)
# the synthesis plan of a paper type that does no meta-analysis (spec §7.3: V-STATS only for the systematic review)
OTHER_PLANS = {"scoping_review": "an evidence_map (no pooling)",
               "critical_review": "thematic or narrative_swim, or none (synthesis_plan may be left out)",
               "research_proposal": "thematic or narrative_swim, or none (synthesis_plan may be left out)"}
PAPER_TYPES: tuple[str, ...] = get_args(PaperType)
# The PRISMA-P items a systematic review needs that are written as text in ``prisma_p``; the others (eligibility,
# information sources, search strategy, data items, synthesis) are the structured parts of the protocol, which the
# structural checks cover.
# Calibration (P1 protocol diagnosis 2026-11-12): rule R1b; the last four are the method items the judges asked for
# after the first five (prisma_p.methods_missing points), and a redraft fell back to the five the task named.
PRISMA_P_TEXT = ("rationale", "objectives", "selection_process", "data_collection_process", "risk_of_bias",
                 "meta_bias", "certainty_of_evidence", "data_management", "outcomes_prioritisation")
# The PRISMA-P items only the kernel can write, from the project's records (a systematic or scoping review only,
# ``kernel_prisma_p``): the Ideator cannot know them and wrote placeholders the next vote rejected. Each text states
# only what the project's records establish, never a fact about the world outside them.
# Calibration (P1 protocol diagnosis 2026-11-12): rule R1a; prisma_p.admin_missing and prisma_p.placeholder points.
KERNEL_PRISMA_P = {
    "registration": ("No external registration is recorded in this project (whether the review is registered "
                     "elsewhere is not verified). The frozen protocol's SHA-256 and the time of its freeze are "
                     "recorded in the project's event log."),
    "amendments": ("A change after the freeze is stored as a new protocol version with a recorded deviation from the "
                   "frozen one."),
    "support": ("No funding or sponsor is recorded in this project; the protocol was drafted by the kernel's agent "
                "roles."),
    "competing_interests": ("No competing interests are recorded in this project (not verified beyond the project's "
                            "records)."),
}
# the exclusion criterion of the record languages search_screen excludes by rule (rule R3)
LANGUAGE_CRITERION_ID = "E-lang"
LANGUAGE_NAMES = {"en": "English"}
DEDUP_RULE = ("duplicates: each hit joins the library item of the same work, matched by identifier (DOI, PMID, "
              "PMCID, arXiv id, OpenAlex id, Semantic Scholar id or ISBN), else by normalised title (case, accents, "
              "spaces and punctuation ignored) and publication year; the first record of each work is screened, "
              "every later hit is counted as a duplicate removed")

DRAFT_TASK = (
    "Draft the review protocol for the frozen research question, its hypotheses and topic, for the intake's paper type "
    "and audience: eligibility (include and exclude criteria, each with a short id and its text), the extraction "
    "schema (each field named, described and typed: text, number or category with its categories, and a unit where "
    "there is one; source record for a field about the study's bibliographic record, such as where it was published "
    "or its publication type, which the reader quotes from that record, else text), the synthesis plan (a "
    "meta-analysis, for a systematic review only, with its typed outcomes, "
    "effect_measure, pool_model, hksj, pi_df and min_studies, the fewest studies with a computable effect a pool "
    "needs, at least 2; a scoping review's plan is an evidence_map; a critical review's or a proposal's is thematic "
    "or narrative_swim, or none) "
    f"and, for a systematic review, the PRISMA-P items keyed {', '.join(PRISMA_P_TEXT)} (meta_bias: how bias across "
    "studies, such as reporting bias, is assessed; certainty_of_evidence: how the certainty of each outcome's evidence "
    "is rated, e.g. GRADE; data_management: how records and extracted data are kept; outcomes_prioritisation: the "
    "primary and secondary outcomes, in order). A systematic review also names its outcomes in synthesis_plan.outcomes "
    "whatever its synthesis method (a plan other than a meta-analysis maps no fields) and has an extraction field "
    "identifying each study (e.g. study_id: first author and year, source record). The statistics module has no "
    "diagnostic-accuracy measure: a question about sensitivity, specificity or AUC gets a narrative synthesis that "
    "states this limitation, not a meta-analysis. The kernel writes, from the project's records, the "
    "PRISMA-P items registration, amendments, support and competing_interests (systematic and scoping reviews) and "
    f"the exclusion criterion {LANGUAGE_CRITERION_ID} (records in a language outside `languages` are excluded by a "
    "kernel rule before screening): do not write them; write the other eligibility criteria and PRISMA-P items to "
    "agree with them (no criterion admits another language). Write every text as what the review does, never a "
    "placeholder for someone to complete later (\"to be completed by the researcher\", \"[TO BE ADDED]\", \"will be "
    "registered\"). The PRISMA-P texts name only the sources of the search strategy and promise no search the kernel "
    "does not run (it searches only those sources: no grey literature, trial registries, reference lists, citation or "
    "hand-searching, no contact with authors). Never claim that the question or the review is novel. "
    "When a previous draft is given, revise it: change only what the guidance asks for and keep the rest, dropping "
    "none of its PRISMA-P items unless an open point or the guidance asks you to remove one. When a frozen "
    "protocol is given, change only what the guidance asks for. Follow the guidance when there is any. When "
    "`open_points` is given (the judges' open points), answer in `answers` every point whose `owner` is ideator, by "
    "its `id`: how changed, with where the protocol changes it in note, or how rebutted, with why it stays in note; a "
    "draft that leaves one of them unanswered is sent back to you.")
STRATEGY_TASK = (
    "Write the search strategy for the protocol: at least 3 English search queries that together cover the research "
    "question and the eligibility criteria, at least 2 sources taken from `available_sources` exactly as given, and "
    "the publication-year range (date_from, date_to; null for an open end). When a previous draft is given (with the "
    "record counts of its queries), revise its strategy: change only what the guidance asks for. When `narrowing` is "
    "given, the record-cap probe of the previous draft's strategy (`total`: the record counts of each query on each "
    "counted source within its years, summed, an upper bound since duplicates count again) is above `record_cap`: "
    "return a narrower strategy with fewer records. Drop or tighten the broadest queries (`counts` shows which), "
    "for example with quoted phrases and AND between the concepts every record needs; restrict fields (title or "
    "abstract) only in a syntax every chosen source reads; keep every eligibility criterion covered. A strategy that "
    "repeats one already tried or does not lower the total below the best so far is a failed try (`failed_tries` "
    "lists them). Keep every source in `keep_sources` (the PRISMA-P texts name them): narrow the queries, not those "
    "sources. The kernel runs the searches; do not search yourself. Follow the guidance when there is any. When "
    "`open_points` is given (the judges' open points), answer in `answers` every point whose `owner` is scout (the "
    "search points), by its `id`: how changed, with where the strategy changes it in note, or how rebutted, with why "
    "it stays in note; a strategy that leaves one of them unanswered is sent back to you.")
# what the judges are told the probe is
PROBE_NOTE = ("advisory, an upper bound: the record counts of each query on each counted source within the strategy's "
              "publication years, summed, so duplicates across queries and sources count again; search_screen "
              "enforces the record cap on the deduplicated records")
DRAFT_KEYS = ("eligibility", "extraction_schema", "synthesis_plan", "prisma_p")  # the Ideator's part of a protocol
# Calibration (P1 protocol diagnosis 2026-11-12): §7 items 1, 5 and 6; free-text reasons mixed 3-9 faults, judges saw
# neither earlier points nor answers, and asked for what the kernel cannot do. The blocking/advisory split is the
# plan's deliberate policy change: an approve with an open blocking point of the judge's own counts as revise.
VOTE_QUESTION = (
    "Is this review protocol complete and fit to freeze for the research question: its eligibility criteria, search "
    "strategy, extraction schema, synthesis plan and, for a systematic review, the PRISMA-P items? `protocol` is the "
    "version to judge, `changes` what changed since the version voted on before. First mark each open point of yours "
    "(the `open_points` whose `role` is `you`) in `resolved`, by its `id`: resolved or not, with why, reading the "
    "authors' `answers` to it. Then list your new points in `points`, each with its section, the fault, the change "
    "you ask for and whether it is blocking: blocking only for what must change before the protocol is frozen, "
    "advisory (blocking false) for the rest; do not repeat an open point. Answer approve when none of your blocking "
    "points is open (an approve with one of your blocking points open counts as revise), else revise. The rules sheet "
    "(`kernel_rules`) is what the kernel does and cannot do whatever the protocol says: do not ask for anything it "
    "rules out.")
# The judges' rules sheet: what the kernel does whatever the protocol says, so a judge does not ask the authors for
# what they cannot change or the kernel cannot do. Every statement is what the code does (rk.production.search,
# rk.production.screening, rk.sources.resolver) or what the project's records establish.
# Calibration (P1 protocol diagnosis 2026-11-12): §7 item 6; rules R1a, R3, R4.
KERNEL_RULES = (
    "Rules of the kernel that runs this review. They hold whatever the protocol says: the protocol's authors cannot "
    "change them, so do not ask them to.\n"
    "1. Kernel-written text. The PRISMA-P items registration, amendments, support and competing_interests (systematic "
    f"and scoping reviews), the exclusion criterion {LANGUAGE_CRITERION_ID} and the search execution block are "
    "written by the kernel from the project's records. The protocol is drafted by the kernel's agent roles, which "
    "know no fact outside the project's records (an external registration, funding, interests).\n"
    "2. Sources. The search runs only on the sources in the search strategy, taken from the kernel's search sources: "
    "OpenAlex, Europe PMC, PubMed, arXiv and, when its API key is set, Semantic Scholar. The kernel has no search of "
    "trial registries or grey literature, no reference-list or citation searching, no hand-searching and no contact "
    "with authors.\n"
    "3. Queries. Each query is sent verbatim to the search API of each listed source (arXiv receives it as "
    "all:<query>); the kernel adds no MeSH terms or field tags and does not rewrite a query into a source's own syntax "
    "(PubMed applies its own automatic term mapping). "
    "OpenAlex rejects wildcards (* and ?) and ~, so the protocol's checks fail a query with them on OpenAlex.\n"
    "4. Years. The publication years are applied as each source's own publication-year filter (arXiv: its "
    "submission date); an open start is the earliest record a source indexes, an open end the date of the search.\n"
    "5. Log. Every page a source returns is logged in the project's search log with its time, source, query, "
    "publication years, offset and the records it returned. A search a source cannot finish (e.g. its paging limit) "
    "is logged as incomplete; a systematic or scoping review then goes back to protocol.\n"
    f"6. Deduplication. {DEDUP_RULE[0].upper()}{DEDUP_RULE[1:]}.\n"
    "7. Record cap. The cap counts records after deduplication. Once the search finds more distinct works than the "
    "cap, search_screen stops, screens none of them and goes back to protocol; records are never cut to the cap. The "
    f"record-cap probe shown with the protocol is {PROBE_NOTE}; only OpenAlex, Europe PMC and PubMed give counts.\n"
    "8. Language. A record whose language a source records outside the search strategy's languages is excluded by "
    f"rule before screening ({LANGUAGE_CRITERION_ID}); a record with no recorded language is screened as English.\n"
    "9. Freeze. The frozen protocol's SHA-256 and the time of its freeze are recorded in the project's event log; a "
    "change after the freeze is a new protocol version with a recorded deviation from the frozen one.")


class PointAnswer(BaseModel):
    """The Ideator's or the Scout's answer to an open point of the judges (``point_id``, from ``open_points``):
    ``changed`` (``note``: where the draft changes it) or ``rebutted`` (``note``: why it is not changed). Stored with
    the version's ``protocol.added`` ``tried``, never in the protocol body."""
    point_id: str
    how: Literal["changed", "rebutted"]
    note: str


class ProtocolDraftOut(BaseModel):
    """The Ideator's protocol draft. The record cap is not the Ideator's to set."""
    eligibility: Eligibility
    extraction_schema: list[ExtractionField] = []
    synthesis_plan: SynthesisPlan | None = None
    prisma_p: dict[str, str] = {}  # PRISMA-P item -> text (systematic review: the PRISMA_P_TEXT items; the kernel
    # writes the KERNEL_PRISMA_P items itself, ``with_kernel_facts``)
    answers: list[PointAnswer] = []  # one per open point routed to the Ideator (P1)


class SearchStrategyOut(BaseModel):
    """The Scout's search strategy: queries, sources (from the available ones) and publication years (inclusive)."""
    queries: list[str]
    sources: list[str]
    date_from: int | None = None
    date_to: int | None = None
    answers: list[PointAnswer] = []  # one per open point routed to the Scout (P1)


class JudgePoint(BaseModel):
    """A protocol judge's point: the ``section`` it is about, the ``fault``, the ``change`` it asks for, and whether
    the protocol may not be frozen before it is resolved (``blocking``) or it is advice (advisory)."""
    section: Literal["search", "eligibility", "extraction", "synthesis", "prisma_p", "other"]
    fault: str
    change: str
    blocking: bool = True


class PointMark(BaseModel):
    """A judge's mark on an earlier open point of its own: resolved or not, and why."""
    point_id: str
    resolved: bool
    why: str = ""


class ProtocolVoteOut(VoteOut):
    """A protocol judge's answer: the choice and reason, its marks on its earlier open points (``resolved``) and its
    new points (``points``)."""
    points: list[JudgePoint] = Field(default_factory=list)
    resolved: list[PointMark] = Field(default_factory=list)


# ---- the judges' point ledger (P1) ----
def owner(point: dict) -> Literal["scout", "ideator"]:
    """Who answers a judge's point: the Scout a ``search`` point, the Ideator every other."""
    return "scout" if point["section"] == "search" else "ideator"


def _protocol_votes(ps: ProjectStore) -> list[dict]:
    """Every protocol vote of the project (its ``vote.decided`` payload, with its ``seq`` and the ``question`` of the
    version voted on), in order."""
    rows = ps.db.execute("SELECT e.seq, e.payload, json_extract(p.body, '$.question') AS question FROM events e "
                         "LEFT JOIN protocols p ON p.id=json_extract(e.payload, '$.subject_id') "
                         "WHERE e.type='vote.decided' AND json_extract(e.payload, '$.subject_type')='protocol' "
                         "ORDER BY e.seq").fetchall()
    return [{**json.loads(r["payload"]), "seq": r["seq"], "question": r["question"]} for r in rows]


def _answers(ps: ProjectStore) -> dict[str, list[dict]]:
    """Every answer stored with a protocol version (``protocol.added`` ``tried.answers``) by point id, in order, each
    ``{attempt, version, by, how, note}``. An answer a later try of the attempt carried over unchanged (the part of a
    role not asked again) is given once, with the version that first gave it."""
    out: dict[str, list[dict]] = {}
    rows = ps.db.execute("SELECT payload FROM events WHERE type='protocol.added' AND "
                         "json_extract(payload, '$.tried.answers') IS NOT NULL ORDER BY seq").fetchall()
    for r in rows:
        p = json.loads(r["payload"])
        for a in p["tried"]["answers"]:
            given = out.setdefault(a["point_id"], [])
            same = (p["attempt"], a["by"], a["how"], a["note"])
            if not any((g["attempt"], g["by"], g["how"], g["note"]) == same for g in given):
                given.append({"attempt": p["attempt"], "version": p["version"], "by": a["by"], "how": a["how"],
                              "note": a["note"]})
    return out


def _rq_sentence(ps: ProjectStore) -> str | None:
    """The current frozen research question's sentence (what a protocol's ``question`` is), else ``None``."""
    rq = frozen_rq(ps)
    return RQBody.model_validate(rq["body"]).three_step.sentence() if rq is not None else None


def _boundaries(ps: ProjectStore) -> list[int]:
    """The seqs, in order, that end a cycle of the ledger: every freeze of a protocol, and every reopening of
    ``protocol`` by an edge other than its own rerun (``protocol->protocol``), as ``_previous_draft`` scopes."""
    rows = ps.db.execute(
        "SELECT seq FROM events WHERE type='protocol.frozen' OR (type='stage.reopened' AND "
        "json_extract(payload, '$.stage')='protocol' AND "
        "COALESCE(json_extract(payload, '$.by_edge'), '')!='protocol->protocol') ORDER BY seq").fetchall()
    return [r["seq"] for r in rows]


def points_ledger(ps: ProjectStore) -> list[dict]:
    """Every point of every protocol vote of the project, in order, each ``{id, attempt, role, vendor, section, fault,
    change, blocking, status, live, answers, marks}``. A point's id is ``p{attempt}.{role}.{n}``, ``n`` counting the
    judge's points of that vote from 1. ``marks``: every later judge's mark on it (``{attempt, role, resolved, why}``).
    ``answers``: the Ideator's and the Scout's (``_answers``). Derived from the events only, so a point survives an
    edge-limit failure and a new run (no reopening is recorded there).

    ``live`` (controller ruling, fix round 2): the point belongs to the current cycle: its vote was on a version of the
    current frozen research question and came after the latest protocol freeze and the latest reopening of
    ``protocol`` by another edge than ``protocol->protocol`` (``_boundaries``; the scope of ``_previous_draft``). Only
    a mark in the point's own cycle changes its status.

    ``status`` is one of three (the brief's open|resolved widened by controller ruling, fix round 1):
    - ``open``: live, and not resolved.
    - ``resolved``: the latest mark by its own judge (``role``) in its cycle says so; another judge's mark closes
      nothing. A resolved point stays resolved when its cycle ends.
    - ``retired``: it was open when its cycle ended. A retired point is never served, never tightens a vote and never
      exempts a dropped item; it stays for the record.

    A judge whose effective choice is revise but who leaves no blocking point of its own open (no new one, and every
    earlier live one resolved: only a reason, or only advisory points), or who answered outside the options (its
    choice ``None`` with the answer's fields stored; a failed judge task gives none), gets one more point built from
    its reason, ``p{attempt}.{role}.{n+1}``, section ``other``, blocking, its fault and change the reason: the next
    attempt then has it to answer, and the judge closes it like any other.

    Calibration (P1 protocol diagnosis 2026-11-12): §7 items 1-2 and 5; §5.3, the stale guidance after the edge
    limit, lost the attempt-3 vote's reasons. Scope and reason points: review of Task 3 (risks a and b)."""
    return _walk(ps)[0]


def _open_blocking(points: dict[str, dict], cycle: tuple) -> set[str]:
    """The ids of the blocking points of ``cycle`` open in ``points`` (as the walk has them so far)."""
    return {pid for pid, p in points.items() if p["_cycle"] == cycle and p["blocking"] and p["status"] == "open"}


def _walk(ps: ProjectStore) -> tuple[list[dict], list[dict]]:
    """The ledger (``points_ledger``) and one round per protocol vote, in order: ``{attempt, live, before, after}``,
    the ids of the blocking points of the vote's cycle open before and after it (``live``: the vote is of the current
    cycle)."""
    answers = _answers(ps)
    bounds = _boundaries(ps)
    current = (_rq_sentence(ps), len(bounds))
    points: dict[str, dict] = {}
    rounds: list[dict] = []
    for v in _protocol_votes(ps):
        cycle = (v["question"], bisect.bisect_left(bounds, v["seq"]))
        before = _open_blocking(points, cycle)
        for o in v["opinions"]:
            role = o["role"]
            for m in o.get("resolved") or []:
                if (p := points.get(m["point_id"])) is not None:
                    p["marks"].append({"attempt": v["attempt"], "role": role, "resolved": m["resolved"],
                                       "why": m.get("why", "")})
                    if p["role"] == role and p["_cycle"] == cycle:  # a later cycle's mark changes nothing
                        p["status"] = "resolved" if m["resolved"] else "open"
            given = [*(o.get("points") or [])]
            for n, point in enumerate(given, 1):
                pid = f"p{v['attempt']}.{role}.{n}"
                points[pid] = {"id": pid, "attempt": v["attempt"], "role": role, "vendor": o["vendor"], **point,
                               "status": "open", "answers": answers.get(pid, []), "marks": [], "_cycle": cycle}
            answered_off = o["choice"] is None and "points" in o  # answered (not a failed task) outside the options
            if (o.get("effective", o["choice"]) == "revise" or answered_off) and not any(
                    p["role"] == role and p["_cycle"] == cycle and p["blocking"] and p["status"] == "open"
                    for p in points.values()):
                pid = f"p{v['attempt']}.{role}.{len(given) + 1}"
                why = o["reason"].strip() or "revise, with no reason given"
                points[pid] = {"id": pid, "attempt": v["attempt"], "role": role, "vendor": o["vendor"],
                               "section": "other", "fault": why, "change": why, "blocking": True, "status": "open",
                               "answers": answers.get(pid, []), "marks": [], "_cycle": cycle}
        rounds.append({"attempt": v["attempt"], "live": cycle == current, "before": before,
                       "after": _open_blocking(points, cycle)})
    for p in points.values():
        p["live"] = p.pop("_cycle") == current
        if not p["live"] and p["status"] == "open":
            p["status"] = "retired"
    return list(points.values()), rounds


def another_round(ps: ProjectStore, attempt: int) -> str | None:
    """Why a P1 protocol still converging at the ``protocol->protocol`` limit gets one more round, else ``None``: the
    kernel granted fewer than ``KERNEL_EXTRA_ROUNDS`` uses of the edge (yours do not count), and the attempt's vote
    (the latest of ``attempt``, of the current cycle) closed at least one blocking point (its own judge marked it
    resolved) and left no more blocking points open than were open before it in its cycle.

    Calibration (P1 protocol rerun 2026-11-12): five of six runs ended at the limit with one to three blocking points
    open, in all five fewer than after the first vote; fructose (none closed) and ozone (more open than before) would
    still stop."""
    if kernel_grants(ps, "protocol->protocol") >= KERNEL_EXTRA_ROUNDS:
        return None
    last = next((r for r in reversed(_walk(ps)[1]) if r["attempt"] == attempt), None)
    if last is None or not last["live"]:
        return None
    closed, left, before = len(last["before"] - last["after"]), len(last["after"]), len(last["before"])
    if not closed or left > before:
        return None
    return (f"the vote closed {closed} blocking point{'s' if closed != 1 else ''} and left {left} open ({before} "
            f"before): one more protocol round")


def _open_points(ps: ProjectStore) -> list[dict]:
    """The ledger's open points (live and not resolved), each with its ``owner`` (``owner``): what the Ideator, the
    Scout and the judges see."""
    return [{**p, "owner": owner(p)} for p in points_ledger(ps) if p["status"] == "open"]


def _own_open_blocking(ledger: list[dict], role: str, out: ProtocolVoteOut, attempt: int) -> list[str]:
    """The ids of the judge's (``role``) blocking points still open after its answer ``out``: its earlier open ones
    it did not mark resolved, its earlier live resolved ones it marks unresolved again, and its new ones (numbered as
    ``points_ledger`` numbers them). A retired point, or a resolved one of an earlier cycle, counts for nothing."""
    marks = {m.point_id: m.resolved for m in out.resolved}
    earlier = [p["id"] for p in ledger if p["role"] == role and p["blocking"]
               and ((p["status"] == "open" and marks.get(p["id"]) is not True)
                    or (p["live"] and p["status"] == "resolved" and marks.get(p["id"]) is False))]
    return earlier + [f"p{attempt}.{role}.{n}" for n, p in enumerate(out.points, 1) if p.blocking]


def _answered(answers: list[dict], by: str, given: list[PointAnswer]) -> list[dict]:
    """The try's answers: ``answers`` (the ones it carries over) with ``by``'s replaced by ``given``."""
    return [a for a in answers if a["by"] != by] + [{**a.model_dump(mode="json"), "by": by} for a in given]


def _merged(answers: list[dict], by: str, given: list[PointAnswer]) -> list[dict]:
    """The try's answers: ``answers`` (the ones it carries over) with ``given`` (``by``'s) merged in by point id, the
    newer answer winning."""
    new = {a.point_id for a in given}
    return [a for a in answers if a["point_id"] not in new] + [{**a.model_dump(mode="json"), "by": by} for a in given]


def _unanswered(points: list[dict], role: str, given: list[PointAnswer]) -> list[str]:
    """One failure per open point routed to ``role`` (``owner``) that ``given`` does not answer with a note, naming
    it and what to do.

    Calibration (P1 protocol diagnosis 2026-11-12): §7 item 4; 37 points persisted unanswered across votes."""
    done = {a.point_id for a in given if a.note.strip()}
    return [f"open point {p['id']} ({p['section']}: {p['fault']}) is not answered: add an answer with point_id "
            f"{p['id']!r}, how changed with where the protocol changes it in note, or how rebutted with why it stays"
            for p in points if owner(p) == role and p["id"] not in done]


def _changes(ps: ProjectStore, row: dict) -> str | None:
    """What changed since the version the judges voted on before (the latest protocol vote on another version): a
    unified diff of the two rendered protocols (``render_protocol``); ``None`` when no other version was voted on."""
    r = ps.db.execute("SELECT json_extract(payload, '$.subject_id') AS pid FROM events WHERE type='vote.decided' AND "
                      "json_extract(payload, '$.subject_type')='protocol' AND json_extract(payload, '$.subject_id')!=? "
                      "ORDER BY seq DESC LIMIT 1", (row["id"],)).fetchone()
    if r is None:
        return None
    prev = protocol_row(ps, r["pid"])
    return "\n".join(difflib.unified_diff(render_protocol(ps, prev), render_protocol(ps, row),
                                          f"version {prev['version']} (voted on before)",
                                          f"version {row['version']}", lineterm=""))


def _p1_guidance(ps: ProjectStore, guidance: str) -> str:
    """What P1 gives the Ideator and the Scout as guidance: ``guidance`` without the lines of any protocol vote's
    rerun reason (``_revise_reason``). The judges' points reach them from the ledger (``open_points``), so a vote's
    reasons left in the stage's guidance after an edge-limit failure are not given again; any other guidance (a
    back-edge's reason, a failed attempt's failures) stays."""
    voted = {ln for v in _protocol_votes(ps) if v["decision"] == "revise"
             for ln in _revise_reason(VoteResult(v.get("id", ""), v["decision"], v["conservative_used"],
                                                 v["opinions"], v["reason"])).split("\n") if ln.strip()}
    return "\n".join(ln for ln in (guidance or "").split("\n") if ln not in voted).strip()


# ---- checks ----
def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


# OpenAlex's search answers HTTP 400 to wildcards (* and ?) and fails on fuzzy ~ (checked live 2026-11-09)
OPENALEX_UNREAD = re.compile(r"[*?~]")
# a quoted phrase, whose words are no operators and whose parentheses group nothing
QUOTED = re.compile(r'"[^"]*"')


def _query_syntax(query: str) -> list[str]:
    """What keeps a query's boolean structure from reading the same on every source: parentheses that balance, and
    no AND and OR at one parenthesis depth (outside quotes; the sources group such a query differently). Operators
    are the upper-case words; lower-case ``and``/``or`` are search words.

    Calibration (P1 protocol diagnosis 2026-11-12): rule R7; search.query_syntax points."""
    depth, groups, mixed = 0, [set()], False
    for token in re.findall(r"\(|\)|\bAND\b|\bOR\b", QUOTED.sub(" ", query)):
        if token == "(":
            depth += 1
            groups.append(set())
        elif token == ")":
            depth -= 1
            if depth < 0:
                break
            groups.pop()
        else:
            groups[-1].add(token)
            mixed = mixed or len(groups[-1]) > 1
    out = []
    if depth != 0:
        out.append(f"query {query!r} has unbalanced parentheses: close every ( with a ) after it")
    if mixed:
        out.append(f"query {query!r} mixes AND and OR at one parenthesis depth, which the sources group differently: "
                   "put parentheses around each OR group, e.g. (aspirin OR acetylsalicylic acid) AND stroke")
    return out


def check_strategy(strategy: SearchStrategy, available: list[str]) -> list[str]:
    """What keeps the Scout's strategy from its required size, its sources and a syntax each source reads:
    ``QUERIES_MIN`` distinct queries, ``SOURCES_MIN`` distinct available sources, with OpenAlex no query it cannot read
    (``OPENALEX_UNREAD``), and every query's parentheses balanced with no AND and OR at one depth (``_query_syntax``)."""
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
    if "openalex" in usable:
        out += [f"query {q!r} uses a wildcard or fuzzy operator (*, ?, ~), which OpenAlex cannot read (its search "
                "fails): spell the variants out with OR (e.g. diarrhea OR diarrhoea), or leave OpenAlex out of the "
                "sources" for q in _unique(strategy.queries) if OPENALEX_UNREAD.search(q)]
    out += [f for q in _unique(strategy.queries) for f in _query_syntax(q)]
    return out


def _free_texts(body: Protocol) -> list[tuple[str, str]]:
    """Every text of the protocol the Ideator wrote, by name: the kernel's PRISMA-P items (``KERNEL_PRISMA_P``) and
    its language exclusion (``LANGUAGE_CRITERION_ID``) are left out, so no text check holds the kernel's text against
    the Ideator."""
    out = [(f"eligibility.{side}.{c.id}", c.text) for side in ("include", "exclude")
           for c in getattr(body.eligibility, side) if c.id.strip() != LANGUAGE_CRITERION_ID]
    out += [(f"extraction_schema.{f.name}.description", f.description) for f in body.extraction_schema]
    if body.synthesis_plan is not None:
        out += [(f"synthesis_plan.{k}", v) for k, v in body.synthesis_plan.model_dump().items()
                if isinstance(v, str)]
        out += [(f"synthesis_plan.outcomes[{i}].name", o.name) for i, o in enumerate(body.synthesis_plan.outcomes)]
    out += [(f"prisma_p.{k}", v) for k, v in _own_prisma_p(body.prisma_p).items()]
    return out


def _own_prisma_p(items: dict[str, str] | None) -> dict[str, str]:
    """The PRISMA-P items the Ideator writes: every item but the kernel's (``KERNEL_PRISMA_P``)."""
    return {k: v for k, v in (items or {}).items() if k not in KERNEL_PRISMA_P}


def _prisma_p_missing(body: Protocol) -> list[str]:
    """The PRISMA-P text items without a text; the structured items are the structural checks'."""
    texts = body.prisma_p or {}
    return [i for i in PRISMA_P_TEXT if not (texts.get(i) or "").strip()]


# A text that defers to someone who completes it later: what the Ideator wrote for facts it could not know, which the
# next vote rejected. "to be reported" and "to be recorded" are ordinary prose ("outcomes to be reported: stroke"), so
# they count only as a deferral (followed by later, when, by the investigators, ...) or as the whole text.
# Calibration (P1 protocol diagnosis 2026-11-12): rule R1c; prisma_p.placeholder points (the regex hits the judges
# did not raise were real deferrals too).
PLACEHOLDER = re.compile(
    r"\bto be (?:completed|stated|declared|filled|registered|determined)\b|\[\s*to be\b|\btbd\b"
    r"|\bto be (?:reported|recorded)\b(?=\s+(?:later|at a later\b|in (?:a|the) (?:later|future)\b"
    r"|by the (?:investigators?|researchers?|authors|team)\b|when\b|once\b|after\b|before\b|if\b))"
    r"|^\W*to be (?:reported|recorded)\W*$"
    r"|\b(?:will|shall) be registered\b|\bbefore (?:the protocol is )?freez|\bby the researcher\b"
    r"|\bthe researcher (?:must|will)\b|\bleft blank\b|\bwhen assigned\b|\bonce issued\b|\bmust be re-?run\b"
    r"|\bbefore the search is final\b|\bwritten in the full protocol\b", re.IGNORECASE)


def _placeholders(body: Protocol) -> list[str]:
    """One failure per Ideator text (``_free_texts``) holding a placeholder (``PLACEHOLDER``), naming it."""
    return [f"{name} holds a placeholder ({m.group(0)!r}): write what the review does, not what someone is to "
            "complete later, or leave out an item the review does not need; registration, amendments, support and "
            "competing interests are written by the kernel"
            for name, text in _free_texts(body) if (m := PLACEHOLDER.search(text))]


# The kernel's search sources (``rk.discovery.sources``) by how a text names them, and the searches the kernel never
# runs: a PRISMA-P text naming a source outside the strategy, or promising such a search, says the review does what
# it does not.
# Calibration (P1 protocol diagnosis 2026-11-12): rule R5; search.prose_vs_strategy points.
SOURCE_NAMES = {"openalex": ("OpenAlex", r"open ?alex"), "europepmc": ("Europe PMC", r"europe ?pmc"),
                "pubmed": ("PubMed", r"pub ?med|medline"), "arxiv": ("arXiv", r"arxiv"),
                "s2": ("Semantic Scholar", r"semantic ?scholar")}
UNRUN_SEARCHES = re.compile(
    r"gr[ae]y literature|hand-?search\w*|reference lists?|citation (?:chasing|searching|tracking)|clinicaltrials\.gov"
    r"|\bictrp\b|\bisrctn\b|trial regist(?:ry|ries|er|ers)\b"
    r"|contact(?:ing)? (?:the )?(?:study |corresponding )?authors", re.IGNORECASE)
# a clause that says what is not done (a negated mention is no claim)
NEGATED = re.compile(r"\b(?:not|no|never|none|without|neither|nor|cannot)\b|n't", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.;!?](?:\s|$)|\n")


def _claims(text: str) -> list[str]:
    """The clauses of a text that do not negate what they mention (``NEGATED``)."""
    return [c for c in CLAUSE_END.split(text) if not NEGATED.search(c)]


def _named_sources(text: str) -> list[str]:
    """The search sources (``SOURCE_NAMES`` keys) a text names in a clause that does not negate them."""
    claims = _claims(text)
    return [s for s, (_, words) in SOURCE_NAMES.items() if any(re.search(words, c, re.IGNORECASE) for c in claims)]


def _prisma_p_sources(body: Protocol) -> list[str]:
    """The search sources the Ideator's PRISMA-P texts name (``_named_sources``): a narrowing keeps them."""
    return list(dict.fromkeys(s for text in _own_prisma_p(body.prisma_p).values() for s in _named_sources(text)))


def _sources_outside(body: Protocol) -> list[str]:
    """One failure per source an Ideator PRISMA-P text names that the search strategy does not list."""
    sources = _unique(body.search_strategy.sources)
    return [f"prisma_p.{key} names {SOURCE_NAMES[s][0]}, which is not a source of the search strategy "
            f"({', '.join(sources)}): name only the strategy's sources, or say the source is not searched"
            for key, text in _own_prisma_p(body.prisma_p).items() for s in _named_sources(text) if s not in sources]


def _prisma_p_against_strategy(body: Protocol) -> list[str]:
    """What the Ideator's PRISMA-P texts say of the search that the strategy does not do: a source named outside the
    strategy's sources (``_sources_outside``), or a search the kernel does not run (``UNRUN_SEARCHES``). A clause
    that negates its mention (``NEGATED``) says the review does not do it, and passes."""
    out = _sources_outside(body)
    for key, text in _own_prisma_p(body.prisma_p).items():
        promised = list(dict.fromkeys(m.group(0) for c in _claims(text) for m in UNRUN_SEARCHES.finditer(c)))
        if promised:
            out.append(f"prisma_p.{key} promises {', '.join(map(repr, promised))}, which the kernel does not run (it "
                       "searches only the strategy's sources: no grey literature, trial registries, reference lists, "
                       "citation or hand-searching, no contact with authors): remove it, or say it is not done")
    return out


# A diagnostic-accuracy measure the statistics module cannot pool; "insulin sensitivity" and a "sensitivity analysis"
# are no such measure.
# Calibration (P1 protocol diagnosis 2026-11-12): rule R6; synthesis.dta_measure_mismatch points.
DIAGNOSTIC_ACCURACY = re.compile(r"diagnostic accuracy|(?<!insulin )sensitivit(?!y analys)|specificit|\bauc\b"
                                 r"|\bauroc\b|area under the (?:roc|receiver)", re.IGNORECASE)
# an extraction field that identifies the study (rule R7; extraction.no_study_id points)
STUDY_ID_FIELD = re.compile(r"study_?id|author|citation|reference", re.IGNORECASE)


def _diagnostic_accuracy(body: Protocol, plan: SynthesisPlan) -> str | None:
    """Where the question or an outcome names a diagnostic-accuracy measure (``DIAGNOSTIC_ACCURACY``), else ``None``."""
    for where, text in [("the question", body.question), *((f"outcome {o.name.strip()!r}", o.name)
                                                            for o in plan.outcomes)]:
        if m := DIAGNOSTIC_ACCURACY.search(text):
            return f"{where} names {m.group(0)!r}"
    return None


# what a point's change or your comment says to take an item out
REMOVAL = re.compile(r"\b(?:remov|delet|drop|omit)\w*|\b(?:leave|take) (?:it |the item )?out\b", re.IGNORECASE)


def _words(text: str) -> str:
    """``text`` folded for naming an item: case folded, hyphens and underscores as spaces, whitespace collapsed."""
    return " ".join(re.sub(r"[-_]", " ", text).casefold().split())


def _asks_removal(key: str, text: str) -> bool:
    """Whether a clause of ``text`` names the item ``key`` (in any of its spellings: underscores, hyphens or spaces)
    and says to remove it (``REMOVAL``)."""
    name = _words(key)
    return any(REMOVAL.search(c) and name in _words(c) for c in CLAUSE_END.split(text or ""))


def _asked_to_remove(key: str, points: Sequence[dict], guidance: str = "") -> bool:
    """Whether the item ``key`` may go: an open point routed to the Ideator (any section, a point built from a judge's
    reason too) asks to remove it in its change, or the guidance does (in P2, your comment, which has no points)."""
    return _asks_removal(key, guidance) or any(owner(p) == "ideator" and _asks_removal(key, p["change"])
                                               for p in points)


def dropped_prisma_p(previous: dict | None, body: Protocol, points: Sequence[dict] = (), *,
                     guidance: str = "") -> list[str]:
    """One failure per PRISMA-P item of ``previous`` (a protocol body, the draft a redraft revises) that ``body`` no
    longer has, quoting the dropped text; the kernel's items are not the Ideator's to keep, an item that held a
    placeholder (``PLACEHOLDER``) may go, as the placeholder's failure allows, and so may an item an open point
    (``points``, the ledger's open points) or the guidance (your comment in P2) asks the Ideator to remove
    (``_asked_to_remove``).

    Calibration (P1 protocol diagnosis 2026-11-12): rule R8; six redrafts dropped items the next vote asked for
    again. The guidance exemption: final review of the P1 protocol work (P2 asks nothing new of you)."""
    now = body.prisma_p or {}
    return [f"PRISMA-P item {k!r} of the previous draft is dropped: keep every PRISMA-P item of the previous draft, "
            f"revised as the guidance asks (its text was: {v!r})"
            for k, v in _own_prisma_p((previous or {}).get("prisma_p")).items()
            if (v or "").strip() and not PLACEHOLDER.search(v) and not (now.get(k) or "").strip()
            and not _asked_to_remove(k, points, guidance)]


def check_protocol(body: Protocol, paper_type: str) -> list[str]:
    """What keeps the protocol from the structure of its paper type, one failure per item; ``[]`` when it has it.
    Every type: the question, a query and a source, an include criterion (the selection principles), named and
    distinct extraction fields, outcomes named once, and in the Ideator's texts (``_free_texts``) no placeholder
    (``PLACEHOLDER``), no novelty claim and no PRISMA-P text naming a source outside the strategy or promising a
    search the kernel does not run. The systematic and scoping reviews also need an extraction schema and a synthesis
    plan; the systematic review also the PRISMA-P items (``PRISMA_P_TEXT``), its outcomes for any synthesis method
    and an extraction field identifying the study (``STUDY_ID_FIELD``). A meta-analysis plan is for a systematic
    review only (one failure naming the plan to use instead, ``OTHER_PLANS``) and not for a diagnostic-accuracy
    question or outcome (one failure: the statistics module has no such measure); otherwise it needs its typed
    ``pool_model``, an ``effect_measure`` the statistics module computes (``MEASURES``) and its pre-specified
    ``outcomes``, each mapping the measure's roles to its own number fields.

    Calibration (P1 protocol diagnosis 2026-11-12): rules R1b, R1c, R5, R6, R7."""
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
    if plan is not None and plan.method == "meta_analysis" and paper_type != "systematic_review":
        out.append(f"synthesis_plan.method meta_analysis is for a systematic review only; a "
                   f"{paper_type.replace('_', ' ')}'s plan is {OTHER_PLANS[paper_type]}")
    elif plan is not None and plan.method == "meta_analysis" and (dta := _diagnostic_accuracy(body, plan)):
        out.append(f"synthesis_plan.method meta_analysis cannot pool diagnostic accuracy ({dta}): rk.stats has no "
                   f"diagnostic-accuracy measure (it pools only {', '.join(MEASURES)}, none of them sensitivity, "
                   "specificity or AUC); plan a narrative synthesis (method narrative_swim) and state this limitation "
                   "in the plan")
    elif plan is not None and plan.method == "meta_analysis":
        if plan.pool_model is None:
            out.append("synthesis_plan.pool_model is required for a meta-analysis (REML, DL or FE)")
        if plan.effect_measure not in MEASURES:
            out.append(f"synthesis_plan.effect_measure must be one of {', '.join(list(MEASURES)[:-1])} or "
                       f"{list(MEASURES)[-1]} for a meta-analysis (got {plan.effect_measure!r})")
        if not plan.outcomes:
            out.append("synthesis_plan.outcomes is required for a meta-analysis (the pre-specified outcome names)")
        out += _outcome_problems(plan, {f.name: f for f in body.extraction_schema})
    elif plan is not None:
        out += _outcome_names(plan)
    if paper_type == "systematic_review":
        if plan is not None and plan.method != "meta_analysis" and not plan.outcomes:
            out.append("synthesis_plan.outcomes is empty: a systematic review pre-specifies its outcomes (primary "
                       "first) whatever its synthesis method; list them by name (a plan other than a meta-analysis "
                       "maps no fields)")
        if body.extraction_schema and not any(STUDY_ID_FIELD.search(n) for n in names):
            out.append("extraction_schema has no field identifying the study (a name with study_id, author, citation "
                       "or reference): add one, e.g. study_id (text, source record): first author and year")
        out += [f"PRISMA-P item {i!r} is missing" for i in _prisma_p_missing(body)]
    out += _placeholders(body)
    out += _prisma_p_against_strategy(body)
    out += novelty_claims(_free_texts(body))
    return out


def counts_as_preregistration(paper_type: str) -> bool:
    """Whether freezing the protocol counts as preregistration (G-PREREG): systematic and scoping reviews only."""
    return paper_type in PREREG_TYPES


# ---- the kernel's facts ----
def kernel_prisma_p(paper_type: str) -> dict[str, str]:
    """The PRISMA-P items the kernel writes for the paper type (``KERNEL_PRISMA_P``): a systematic or scoping review
    (whose freeze counts as preregistration) gets them, the other types none."""
    return dict(KERNEL_PRISMA_P) if counts_as_preregistration(paper_type) else {}


def _language_names(codes: list[str]) -> str:
    return ", ".join(f"{LANGUAGE_NAMES[c]} ({c})" if c in LANGUAGE_NAMES else c for c in codes)


def _language_criterion(languages: list[str]) -> Criterion:
    return Criterion(id=LANGUAGE_CRITERION_ID, text=(
        f"Reports in a language other than {_language_names(languages)}: search_screen excludes a record whose "
        "language a source records as another language, by a kernel rule before screening; a record with no recorded "
        "language is screened. Written by the kernel."))


def with_kernel_facts(body: Protocol, paper_type: str) -> Protocol:
    """The protocol with the facts only the kernel writes: the PRISMA-P items of ``kernel_prisma_p`` replace any the
    Ideator wrote under those keys (dropped for a type that gets none; ``prisma_p`` is ``None`` when nothing is
    left), and the exclusion criterion ``LANGUAGE_CRITERION_ID`` naming the strategy's screened languages is the last
    exclude criterion, once (a criterion of that id the Ideator wrote, on either side, is replaced). Applied again it
    changes nothing.

    Calibration (P1 protocol diagnosis 2026-11-12): rules R1a and R3. The Ideator cannot know registration, funding or
    competing interests and wrote placeholders the next vote rejected; the language rule is the kernel's, and drafts
    contradicted it."""
    own = {k: v for k, v in (body.prisma_p or {}).items() if k not in KERNEL_PRISMA_P}
    elig = body.eligibility
    include = [c for c in elig.include if c.id.strip() != LANGUAGE_CRITERION_ID]
    exclude = [c for c in elig.exclude if c.id.strip() != LANGUAGE_CRITERION_ID]
    exclude.append(_language_criterion(body.search_strategy.languages))
    return body.model_copy(update={"prisma_p": {**own, **kernel_prisma_p(paper_type)} or None,
                                   "eligibility": Eligibility(include=include, exclude=exclude)})


# ---- the record-cap probe (advisory) ----
def _count(ctx: StageContext, query: str, source: str, strategy: SearchStrategy) -> dict:
    """One count of the probe, within the strategy's publication years, through the per-item boundary: a source error
    gives ``count: None`` and the ``error`` (the probe is advisory: a source error fails nothing). An exhausted source
    API budget (``BudgetExhausted``) is not a count: it pauses the run (``paused_budget``, resumed after the reset), and
    the stage, which resumes in place, probes the version then, so no vote is spent on a probe the budget cut short."""
    what = f"record count of {query!r} on {source}"
    n, err = per_item(what, lambda: ctx.sources.count(query, source, date_from=strategy.date_from,
                                                      date_to=strategy.date_to))
    return {"query": query, "source": source, "count": n} | ({"error": err} if err else {})


def _probe(ctx: StageContext, strategy: SearchStrategy) -> dict:
    """``sources.count`` of every query on every counted source of the strategy within its publication years
    (``_count``), and the sum of the counts given: an upper bound, since duplicates across queries and sources count
    again. A source that gave no count keeps ``count: None`` (and its ``error`` when the count failed); the
    strategy's sources without a count at all are listed as ``uncounted``. ``total`` is ``None`` when nothing was
    counted."""
    sources = _unique(strategy.sources)
    counts = [_count(ctx, q, s, strategy) for q in _unique(strategy.queries) for s in sources
              if s in COUNTED_SOURCES]
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
    kind += ", from the bibliographic record" if f.get("source") == "record" else ""
    return f"- {f['name']} ({kind}): {f['description']}"


HKSJ_WORDS = {"auto": "auto (the Cochrane Handbook rule)", True: "always", False: "never (Wald intervals)"}
# shown only for a meta-analysis; the outcomes are shown for every plan (a systematic review names them for any method)
POOL_KEYS = frozenset({"pool_model", "hksj", "pi_df", "min_studies"})


def _outcome_names(plan: SynthesisPlan) -> list[str]:
    """What keeps a plan's outcomes from being told apart, whatever its method: each named, and once (after case and
    whitespace folding)."""
    out: list[str] = []
    seen: set[str] = set()
    for o in plan.outcomes:
        name = o.name.strip()
        if not name:
            out.append("synthesis_plan.outcomes: an outcome name is empty")
        elif (key := " ".join(name.split()).casefold()) in seen:
            out.append(f"synthesis_plan.outcomes: outcome {name!r} is given twice")
        else:
            seen.add(key)
    return out


def _outcome_problems(plan: SynthesisPlan, schema: dict[str, ExtractionField]) -> list[str]:
    """What keeps a meta-analysis plan's outcomes from being computed as pre-specified: each named once
    (``_outcome_names``), mapping every role of the effect measure (and nothing else) to a number field of the
    extraction schema, a field to one role only, no two outcomes to the same fields, and no outcome's numerator
    fields (events, means, SDs: every role but ``n_*``) to another outcome's."""
    out = _outcome_names(plan)
    sets: dict[frozenset, str] = {}
    numerators: dict[str, str] = {}  # numerator field -> the outcome that uses it
    roles = MEASURES.get(plan.effect_measure or "")
    if roles is None:
        return out
    for o in plan.outcomes:
        name = o.name.strip()
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
            elif f is not None and schema[f].source == "record":
                out.append(f"{say}: {r} -> {f!r} is a record field; an effect's numbers come from the study's text")
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
    outcomes with their fields (for every method), lists joined; the meta-analysis fields (``POOL_KEYS``) only when
    ``method`` is ``meta_analysis``."""
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
        lines = [(f"Record-cap probe (advisory): {probe['total']} records (an upper bound: summed per query and "
                  f"counted source within the publication years, so duplicates count again; {per}).")]
    if probe["uncounted"]:
        lines.append(f"Not counted (no record count): {', '.join(probe['uncounted'])}.")
    if probe["total"] is not None and probe["total"] > cap:
        lines.append(f"The probe found {probe['total']} records, above the record cap {cap}. search_screen stops "
                     f"above the cap after deduplication: narrow the strategy (revise), or approve with a raised "
                     f"record_cap.")
    return lines


def publication_years(ss: dict) -> str:
    """The strategy's publication years written out, as the protocol's review and your approval card show them: an
    open start is the earliest indexed record, an open end the date of the search."""
    start = ss["date_from"] if ss.get("date_from") is not None else "the earliest indexed record"
    end = ss["date_to"] if ss.get("date_to") is not None else "the date of the search"
    return f"{start} to {end}"


def search_execution_lines(body: dict) -> list[str]:
    """How ``search_screen`` runs the protocol's search strategy (``rk.production.search``), as the protocol's review
    shows it after the strategy: the publication years written out, each query sent verbatim to each source, the
    search log, deduplication, the record cap counted after deduplication and the language rule. Facts of the kernel,
    not of the draft; the judges read them as facts.

    Calibration (P1 protocol diagnosis 2026-11-12): rule R4; no_start_date, not_reproducible, dedup_unspecified and
    cap_handling_unspecified points."""
    ss, cap = body["search_strategy"], body["record_cap"]
    n_q, n_s = len(ss["queries"]), len(ss["sources"])
    return [
        "Search execution (written by the kernel: how search_screen runs this strategy after the freeze):",
        (f"- publication years: {publication_years(ss)} (inclusive), applied as each source's own publication-year "
         "filter (arXiv: its submission date)."),
        (f"- each query is sent verbatim, as written above, to the search API of each listed source ({n_q} queries x "
         f"{n_s} sources = {n_q * n_s} searches; arXiv receives it as all:<query>); the kernel adds no MeSH terms or "
         "field tags and does not rewrite a query into a source's own syntax (PubMed applies its own automatic term "
         "mapping)."),
        ("- every page a source returns is logged in the project's search log with its time (the search date), "
         "source, query, publication years, offset and the records it returned. Each source and query is paged to its "
         "last result; a search a source cannot finish (e.g. its paging limit) is logged as incomplete with its "
         "source and query, and a systematic or scoping review then goes back to protocol."),
        f"- {DEDUP_RULE}.",
        (f"- record cap: {cap} records, counted after deduplication. Once the search finds more than {cap} distinct "
         "works, search_screen stops searching, screens none of them and goes back to protocol to narrow the "
         "strategy; records are never cut to the cap. A version frozen after that records a deviation from the "
         "frozen one."),
        (f"- language: a record whose language a source records as other than {_language_names(ss['languages'])} is "
         f"excluded by rule before screening ({LANGUAGE_CRITERION_ID}); a record with no recorded language is "
         "screened as English."),
    ]


def render_protocol(ps: ProjectStore, row: dict) -> list[str]:
    """Every section of the protocol ``row``, for its review."""
    b = row["body"]
    intake = intake_of(ps)
    paper_type = intake.paper_type if intake is not None else "?"
    purpose = ("freezing it counts as preregistration (G-PREREG)" if counts_as_preregistration(paper_type)
               else "freezing it is for traceability only, not preregistration")
    ss = b["search_strategy"]
    lines = [f"Protocol version {row['version']} ({paper_type}; {purpose}).", "", f"Research question: {b['question']}",
             "", "Search strategy:", f"- sources: {', '.join(ss['sources'])}",
             f"- publication years: {publication_years(ss)}",
             f"- languages: {', '.join(ss['languages'])}", "- queries:"]
    lines += [f"  - {q}" for q in ss["queries"]]
    lines += ["", *search_execution_lines(b)]
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


# ---- the attempt's tries ----
def _tries(ps: ProjectStore, attempt: int) -> list[dict]:
    """The attempt's tries in order: each version the stage drafted in the attempt (``id``, ``version``, ``body``)
    with how (``protocol.added`` ``tried``): ``kind`` (``"draft"``: the Ideator's draft and a strategy; ``"narrow"``:
    the Scout's narrowing of the best strategy so far), ``vendors`` (``ideator``, ``scout``: those of its body's
    parts) and ``failures`` (its structural ones, ``strategy`` and ``draft``). A version you added (a raised record
    cap) is no try."""
    rows = ps.db.execute("SELECT entity_id, payload FROM events WHERE type='protocol.added' AND "
                         "json_extract(payload, '$.attempt')=? AND json_extract(payload, '$.tried') IS NOT NULL "
                         "ORDER BY seq", (attempt,)).fetchall()
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        out.append({"id": r["entity_id"], "version": p["version"], "body": p["body"], **p["tried"]})
    return out


def _failures(body: Protocol, paper_type: str, available: list[str]) -> dict[str, list[str]]:
    """The structural failures of the protocol by the role that answers for them: ``strategy``, the Scout's
    (``check_strategy`` and the search-strategy items of ``check_protocol``), and ``draft``, the Ideator's (the rest
    of ``check_protocol``)."""
    found = check_protocol(body, paper_type)
    return {"strategy": check_strategy(body.search_strategy, available)
            + [f for f in found if f.startswith("search_strategy")],
            "draft": [f for f in found if not f.startswith("search_strategy")]}


def _failed(tried: dict) -> list[str]:
    return tried["failures"]["strategy"] + tried["failures"]["draft"]


def _last_draft(tries: list[dict]) -> int | None:
    """The index of the attempt's last draft try (the narrowing tries come after it), else ``None``."""
    return max((i for i, t in enumerate(tries) if t["kind"] == "draft"), default=None)


def _scout_failures(tried: dict) -> list[str]:
    """A narrowing try's failures as the Scout answers them: a source the PRISMA-P texts name that its strategy
    dropped fails ``_sources_outside`` with the Ideator's remedy (name only the strategy's sources); the Scout is told
    instead to keep that source."""
    body = Protocol.model_validate(tried["body"])
    named = _prisma_p_sources(body)
    dropped = [s for s in named if s not in _unique(body.search_strategy.sources)]
    if not dropped:
        return _failed(tried)
    ideators = set(_sources_outside(body))
    keep = (f"the strategy drops {', '.join(SOURCE_NAMES[s][0] for s in dropped)}, which the PRISMA-P texts name: "
            f"keep every source the PRISMA-P texts name ({', '.join(named)})")
    return [keep, *(f for f in _failed(tried) if f not in ideators)]


def _narrowed(ps: ProjectStore, chain: list[dict]) -> tuple[dict, list[dict]]:
    """The best version of a sound draft try and its narrowing tries (``chain``, the draft try first), with its
    ``probe``: the latest try that lowered the probe total below every version before it. And the failed narrowing
    tries, each ``{version, total, why}``: a repeated strategy, one that fails the checks (as the Scout answers them,
    ``_scout_failures``) or has no count, or a total not below the best so far."""
    best = {**chain[0], "probe": probe_of(ps, chain[0]["id"])}
    failed = []
    for i, t in enumerate(chain[1:], 1):
        probe = probe_of(ps, t["id"])
        total = probe["total"] if probe else None
        low = best["probe"]["total"] if best["probe"] else None
        same = next((e for e in chain[:i] if e["body"]["search_strategy"] == t["body"]["search_strategy"]), None)
        if same is not None:
            why = f"the same strategy as version {same['version']}"
        elif _failed(t):
            why = "it fails the checks: " + "; ".join(_scout_failures(t))
        elif total is None:
            why = "no source gave a record count"
        elif low is not None and total >= low:
            why = f"{total} records, not fewer than the {low} of version {best['version']}"
        else:
            best = {**t, "probe": probe}
            continue
        failed.append({"version": t["version"], "total": total, "why": why})
    return best, failed


def _offered(ps: ProjectStore, attempt: int) -> dict | None:
    """The version the attempt offered for approval (to you or the vote; in P1 one still above the record cap reruns
    unvoted instead): the best of its narrowing (``_narrowed``) when its last draft try passed the checks; ``None``
    when there is none (its tries failed, or no try was recorded)."""
    tries = _tries(ps, attempt)
    last = _last_draft(tries)
    if last is None or _failed(tries[last]):
        return None
    return _narrowed(ps, tries[last:])[0]


def _previous_draft(ps: ProjectStore, question: str, attempt: int) -> dict | None:
    """The draft this attempt revises, with its probe: the version the latest earlier attempt offered for approval
    (``_offered``), else its latest version, when the latest protocol is an unfrozen draft of the current research
    question (``question``, the frozen RQ's sentence) and ``protocol`` was not reopened since by an edge from another
    stage (a back-edge to question). ``None`` otherwise: a frozen latest protocol, a draft of another question, or one
    an edge left behind."""
    rows = query_rows(ps, "SELECT * FROM protocols WHERE attempt<? ORDER BY rowid DESC LIMIT 1", (attempt,), ("body",))
    if not rows or rows[0]["status"] == "frozen" or rows[0]["body"]["question"] != question:
        return None
    row = _offered(ps, rows[0]["attempt"]) or rows[0]
    added = ps.db.execute("SELECT MIN(seq) FROM events WHERE type='protocol.added' AND entity_id=?",
                          (row["id"],)).fetchone()[0]
    left_behind = ps.db.execute(
        "SELECT 1 FROM events WHERE type='stage.reopened' AND json_extract(payload, '$.stage')='protocol' AND seq>? "
        "AND json_extract(payload, '$.by_edge')!='protocol->protocol' LIMIT 1", (added,)).fetchone()
    if left_behind is not None:
        return None
    return {"version": row["version"], "body": row["body"], "record_probe": probe_of(ps, row["id"])}


def _with_guidance(guidance: str, more: str) -> str:
    return f"{guidance}\n\n{more}" if guidance.strip() else more


def _strategy_of(out: SearchStrategyOut) -> SearchStrategy:
    return SearchStrategy(queries=_unique(out.queries), sources=_unique(out.sources), date_from=out.date_from,
                          date_to=out.date_to)


def _over_cap(best: dict, tries: int) -> str:
    """Why P1 reruns instead of voting: the best version's probe is still above the record cap after the narrowing
    tries, and only you may raise the cap (P2), so no judge round is spent on a search ``search_screen`` would stop.

    Calibration (P1 protocol diagnosis 2026-11-12): rule R2; search.cap_exceeded points."""
    probe, cap = best["probe"], best["body"]["record_cap"]
    return (f"the record-cap probe of version {best['version']} counts {probe['total']} records (an upper bound), "
            f"above the record cap {cap}, after {tries} narrowing tries, and P1 does not vote on a protocol above the "
            "cap: narrow the search strategy to fewer records (drop or tighten the broadest queries, which the probe's "
            "counts show, e.g. with quoted phrases and AND between the concepts every record needs) and keep every "
            "eligibility criterion covered")


def _revise_reason(res: VoteResult) -> str:
    """The rerun reason of a protocol vote that did not approve (the event log's; P1 gives the judges' points to the
    next attempt from the ledger, ``_p1_guidance``): why, and each judge's reason."""
    why = "; ".join(f"{o['role']} ({o['vendor']}): {o['reason']}" for o in res.opinions if o["reason"])
    head = "the protocol vote did not approve it" if res.conservative_used else "the judges asked for a revision"
    head += f" ({res.reason})" if res.reason else ""
    return f"{head}: {why}" if why else head


class ProtocolWorker:
    # An interrupted attempt runs again in place (rk.orchestrator.engine): ``run`` goes on from the attempt's tries
    # (its versions with their ``tried`` and probes), so nothing it stored is asked or counted again; the vote already
    # recorded and the approval already opened are taken. A role task whose output was not yet stored is asked again.
    resumes_in_place = True

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
            "languages": list(SCREENED_LANGUAGES),  # the record languages the kernel screens (SearchStrategy)
        }
        if (topic := _topic(ps, rq["topic_id"])) is not None:
            context["topic"] = topic
        if (prior := frozen_protocol(ps)) is not None and prior["body"]["question"] == question:
            context["frozen_protocol"] = {"version": prior["version"], "body": prior["body"]}
        if (draft := _previous_draft(ps, question, ctx.attempt)) is not None:
            context["previous_draft"] = draft
        p1 = intake.production_mode == "auto"
        if p1:  # the judges' points come from the ledger; P2 keeps your comment as guidance
            context["open_points"] = _open_points(ps)
        if guidance := (_p1_guidance(ps, ctx.guidance) if p1 else ctx.guidance):
            context["guidance"] = guidance
        settings = load_settings()
        while True:
            tries = _tries(ps, ctx.attempt)
            last = _last_draft(tries)
            if last is None or _failed(tries[last]):
                if last is not None and sum(t["kind"] == "draft" for t in tries) >= settings.protocol_tries:
                    return StageOutcome("rerun", reason="; ".join(_failed(tries[last])))
                out = self._draft(ctx, context, tries[last] if last is not None else None, question,
                                  intake.paper_type)
                if out is not None:
                    return out
                continue
            chain = tries[last:]
            self._probe_tries(ctx, chain)
            best, failed = _narrowed(ps, chain)
            total = best["probe"]["total"]
            if total is None or total <= best["body"]["record_cap"] or len(chain) - 1 >= settings.protocol_narrow_tries:
                break
            out = self._narrow(ctx, context, best, failed, intake.paper_type)
            if out is not None:
                return out
        if intake.production_mode == "collab":
            cid = opened_for_attempt(ps, KIND, ctx.stage, ctx.attempt) or _open(ctx, best["id"])
            return StageOutcome("waiting_human", checkpoint_id=cid, reason="the protocol awaits your approval")
        if total is not None and total > best["body"]["record_cap"]:
            return StageOutcome("rerun", reason=_over_cap(best, len(chain) - 1))
        return self._vote(ctx, best["id"], set(best["vendors"].values()))

    @staticmethod
    def _draft(ctx: StageContext, context: dict, last: dict | None, question: str,
               paper_type: str) -> StageOutcome | None:
        """One draft try, stored as the next version with its try: the Ideator's draft and the Scout's strategy, or,
        after a failed try (``last``), only the part that failed asked again, with that version as ``previous_draft``
        and its failures as guidance. A PRISMA-P item of a draft the try revises (the attempt's ``previous_draft`` or
        ``last``) that the try dropped is one of its draft failures (``dropped_prisma_p``), unless an open point or
        the guidance (your comment in P2) asks to remove it. In P1 each role answers every open point routed to it
        (``open_points``): one it leaves unanswered is one of its failures (``_unanswered``), so it is asked again in
        the attempt and no edge is spent.
        The try records the answers (``tried.answers``, each with the role, ``by``; a part kept from ``last`` keeps
        its answers), never in the protocol body. An outcome when a role gave nothing, else ``None``."""
        ps = ctx.ps
        inputs = dict(context)
        points = context.get("open_points") or []
        vendors = dict(last["vendors"]) if last is not None else {}
        answers = list(last.get("answers", [])) if last is not None else []
        unanswered: dict[str, list[str]] = {"draft": [], "strategy": []}
        if last is not None:
            inputs["previous_draft"] = {"version": last["version"], "body": last["body"], "record_probe": None}
            inputs["guidance"] = _with_guidance(context.get("guidance", ""), (
                f"Version {last['version']} of this attempt failed the protocol's checks: {'; '.join(_failed(last))}. "
                f"Fix these and keep the rest."))
        if last is None or last["failures"]["draft"]:
            d = ctx.executor.run("ideator", DRAFT_TASK, inputs, ProtocolDraftOut, stage=ctx.stage, attempt=ctx.attempt,
                                 tools=[])
            if not d.ok or d.output is None:
                return StageOutcome("rerun", reason=f"the ideator gave no protocol draft ({d.error})")
            part = d.output.model_dump(mode="json", exclude={"answers"})
            part["prisma_p"] = part["prisma_p"] or None
            vendors["ideator"] = vendor_of(d.provider, d.model)
            answers = _answered(answers, "ideator", d.output.answers)
            unanswered["draft"] = _unanswered(points, "ideator", d.output.answers)
        else:
            part = {k: last["body"][k] for k in DRAFT_KEYS}
        if last is None or last["failures"]["strategy"]:
            s = ctx.executor.run("scout", STRATEGY_TASK, {**inputs, "eligibility": part["eligibility"]},
                                 SearchStrategyOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
            if not s.ok or s.output is None:
                return StageOutcome("rerun", reason=f"the scout gave no search strategy ({s.error})")
            strategy = _strategy_of(s.output).model_dump(mode="json")
            vendors["scout"] = vendor_of(s.provider, s.model)
            answers = _answered(answers, "scout", s.output.answers)
            unanswered["strategy"] = _unanswered(points, "scout", s.output.answers)
        else:
            strategy = last["body"]["search_strategy"]
        body = with_kernel_facts(Protocol.model_validate({**part, "question": question, "search_strategy": strategy,
                                                          "record_cap": _current_cap(ps)}), paper_type)
        failures = _failures(body, paper_type, ctx.sources.sources())
        revised = [d["body"] for d in (context.get("previous_draft"), last) if d is not None]
        failures["draft"] += list(dict.fromkeys(f for b in revised for f in dropped_prisma_p(
            b, body, points, guidance=context.get("guidance", ""))))
        failures["draft"] += unanswered["draft"]
        failures["strategy"] += unanswered["strategy"]
        add_protocol(ps, ctx.attempt, body, version=_next_version(ps), actor=ctx.actor,
                     tried={"kind": "draft", "vendors": vendors, "failures": failures, "answers": answers})
        return None

    @staticmethod
    def _probe_tries(ctx: StageContext, chain: list[dict]) -> None:
        """Probe every try of the chain that passed the checks and has no probe yet; a strategy an earlier try of the
        chain had takes that try's probe (nothing is counted twice)."""
        ps = ctx.ps
        for i, t in enumerate(chain):
            if _failed(t) or probe_of(ps, t["id"]) is not None:
                continue
            same = next((e for e in chain[:i] if e["body"]["search_strategy"] == t["body"]["search_strategy"]
                         and probe_of(ps, e["id"]) is not None), None)
            probe = (probe_of(ps, same["id"]) if same is not None
                     else _probe(ctx, SearchStrategy.model_validate(t["body"]["search_strategy"])))
            _record_probe(ps, t["id"], probe, actor=ctx.actor)

    @staticmethod
    def _narrow(ctx: StageContext, context: dict, best: dict, failed: list[dict],
                paper_type: str) -> StageOutcome | None:
        """One narrowing try: the Scout, given the best version so far (``previous_draft``, with its probe) and
        ``narrowing`` (the cap, that version's total and per-query, per-source counts, the failed tries, and the
        sources the PRISMA-P texts name, ``keep_sources``, which it keeps), answers with a strategy, stored with that
        version's draft as the next version; the best version's answers carry over, with any the Scout gives merged
        in by point id (``_merged``). An outcome when the Scout gave nothing, else ``None``."""
        probe, cap = best["probe"], best["body"]["record_cap"]
        keep = _prisma_p_sources(Protocol.model_validate(best["body"]))
        inputs = {**context, "eligibility": best["body"]["eligibility"],
                  "previous_draft": {"version": best["version"], "body": best["body"], "record_probe": probe},
                  "narrowing": {"record_cap": cap, "version": best["version"], "total": probe["total"],
                                "counts": probe["counts"], "failed_tries": failed, "keep_sources": keep},
                  "guidance": _with_guidance(context.get("guidance", ""), (
                      f"The record-cap probe of version {best['version']} counts {probe['total']} records (an upper "
                      f"bound), above the record cap {cap}: narrow its search strategy to fewer records (see "
                      f"narrowing)." + (f" Keep every source the PRISMA-P texts name ({', '.join(keep)})."
                                        if keep else "")))}
        s = ctx.executor.run("scout", STRATEGY_TASK, inputs, SearchStrategyOut, stage=ctx.stage, attempt=ctx.attempt,
                             tools=[])
        if not s.ok or s.output is None:
            return StageOutcome("rerun", reason=f"the scout gave no narrower search strategy ({s.error})")
        body = Protocol.model_validate({**best["body"], "search_strategy": _strategy_of(s.output).model_dump(mode="json")})
        add_protocol(ctx.ps, ctx.attempt, body, version=_next_version(ctx.ps), actor=ctx.actor,
                     tried={"kind": "narrow", "vendors": {**best["vendors"], "scout": vendor_of(s.provider, s.model)},
                            "failures": _failures(body, paper_type, ctx.sources.sources()),
                            "answers": _merged(best.get("answers", []), "scout", s.output.answers)})
        return None

    @staticmethod
    def _vote(ctx: StageContext, protocol_id: str, generator_vendors: set[str | None]) -> StageOutcome:
        """The P1 vote on the version: the judges see it rendered (``render_protocol``), its probe, what changed since
        the version voted on before (``changes``), every open point with its answers (``open_points``), the kernel's
        rules sheet (``kernel_rules``) and which judge each is (``you``), and answer with points
        (``ProtocolVoteOut``). A judge's approve with an open blocking point of its own (``_own_open_blocking``)
        counts as revise."""
        ps = ctx.ps
        row = protocol_row(ps, protocol_id)
        probe = probe_of(ps, protocol_id)
        ledger = points_ledger(ps)
        inputs = {"paper_type": intake_of(ps).paper_type, "protocol": "\n".join(render_protocol(ps, row)),
                  "record_probe": probe and {**probe, "record_cap": row["body"]["record_cap"], "note": PROBE_NOTE},
                  "changes": _changes(ps, row), "open_points": [{**p, "owner": owner(p)} for p in ledger
                                                                if p["status"] == "open"],
                  "kernel_rules": KERNEL_RULES}

        def tighten(role: str, out: ProtocolVoteOut) -> str:
            ids = _own_open_blocking(ledger, role, out, ctx.attempt)
            return f"approved with a blocking point open ({', '.join(ids)})" if ids else ""

        res = vote(ctx, "protocol", protocol_id, VOTE_QUESTION, VOTE_OPTIONS, "revise", inputs,
                   generator_vendors=generator_vendors, reuse=True, output=ProtocolVoteOut,
                   per_judge=lambda role: {"you": role}, tighten=tighten)
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

    @staticmethod
    def another_use(ctx: StageContext, edge: str) -> str | None:
        """P1 at an edge limit, before the degrade: one more ``protocol->protocol`` use while the vote still converges
        (``another_round``, the reason the kernel records with its grant); ``None`` for any other edge."""
        return another_round(ctx.ps, ctx.attempt) if edge == "protocol->protocol" else None

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: nothing can be deleted or narrowed into an approved protocol, and P1 never freezes a
        protocol the vote did not approve, so the run fails with the failures."""
        return StageOutcome("failed", reason="the protocol was not approved at the edge limit, and P1 does not freeze a "
                                             "protocol the vote did not approve: "
                                             + ("; ".join(failures) or "no reason given"))
