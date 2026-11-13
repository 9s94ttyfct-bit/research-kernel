"""The systematic-review reproduction set (spec §10; Plan 5c Task 8): six PMC open-access systematic reviews, at least
one per domain (chemistry, biology, medicine, cs), each with the studies it included (taken from the review's own full
text) and its main conclusion. The manifest is ``data/sr_repro.json`` (selected live and model-free, committed;
``load_manifest`` validates it).

Each review is reproduced in a project of its own, ``srrepro-<id>`` under the normal RK_HOME (``seed_project``): a
P1 systematic review whose research question is the review's question, frozen as the RQ (Plan 5c Deviation 4: the
discovery stages are recorded as passed, seeded, so a run starts at ``protocol``), with the review's search window in
the topic the protocol's Ideator and Scout read. The seeded stages are fixed: no back-edge reopens them (the run fails
instead), and a P1 RQ vote that finds the main claim not answering the RQ goes back to synthesize, not to question.
Calibration (seeded reproduction, 2026-11-13): re-running discovery on a given question measures nothing, and it pulled
the reproduced review into the library. The reproduced review is never named to the agents (its reference
list would hand them the answer), nor is its main conclusion; ``score`` reports whether the project included it or
rests its main claim on it (then the direction does not count) and, as information, whether it entered the library.

``score`` compares the project with the review:

- ``recall_included``: the share of the review's included studies among the studies the project included at full
  text (``search_screen``'s passed attempt), matched one to one by DOI or PMID, then by title (rapidfuzz
  ``token_set_ratio`` >= 90 and ``token_sort_ratio`` >= 80 after ``default_process``);
- ``conclusion_direction_agrees``: the direction the project's main claim (argue's passed attempt) states for the
  review's main outcome, decided by the P1 vote machinery (``rk.production.vote``: every admitted judge, two at
  least, of distinct vendors none of them the main claim's author's, gives the same answer); ``None`` when they do not
  agree, when the claim states no direction (``unclear``), or when nothing is judged (no judges' context, argue not
  passed). The vote is recorded in the project (stage ``sr_repro``) and reused when scored again;
- ``gates_passed``: audit passed and its passed attempt's latest report passed every hard gate.

Targets (§10): recall >= 0.85, the direction agrees, every hard gate passes. ``rk eval sr-repro`` runs or continues
each seeded project until ``export`` in ``rk serve`` (tools ``rk_sr_repro_run`` and ``rk_sr_repro_score``, yours
alone) and writes ``docs/eval/sr-repro-<date>.{md,json}`` (``write_report``)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from rapidfuzz import fuzz, utils

from rk.argument import ArgumentService
from rk.discovery.executor import RateCapped
from rk.discovery.models import HypothesisIn, Intake, Pico, RQBody, Topic
from rk.discovery.store import (
    add_rq,
    add_topic,
    freeze_rq,
    frozen_rq,
    intake_of,
    record_intake,
    set_topic_status,
)
from rk.orchestrator.graph import DISCOVERY
from rk.orchestrator.state import (
    active_run,
    attempt_ran,
    current_stage,
    fixed_stages,
    latest_run,
    record_attempt,
    record_fixed_stages,
    record_passed,
    record_ran,
    stage_row,
)
from rk.production.argue import main_claim
from rk.production.audit import audit_reports
from rk.production.common import entity_row
from rk.production.extraction import included_studies
from rk.production.models import RECORD_CAP
from rk.production.store import frozen_protocol
from rk.production.vote import vote
from rk.sources.identifiers import normalize_doi, normalize_pmid
from rk.store.events import NewEvent
from rk.tools.registry import CallContext, ToolError

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext
    from rk.orchestrator.runs import RunManager
    from rk.store.project import ProjectStore

Domain = Literal["chemistry", "biology", "medicine", "cs"]
Direction = Literal["benefit", "harm", "no_difference", "mixed"]
DOMAINS: tuple[str, ...] = ("chemistry", "biology", "medicine", "cs")
MANIFEST_PATH = Path(__file__).with_name("data") / "sr_repro.json"
ENTRIES = 6
SLUG_PREFIX = "srrepro-"
PAPER_TYPE = "systematic_review"
# the stage the direction vote is recorded under (no stage of the graph: an evaluation, which
# rk.production.disclosure.EVALUATION_STAGES keeps out of the paper's disclosure)
STAGE = "sr_repro"
SEEDED = "sr_repro.seeded"
ACTOR = "sr-repro"
TITLE_MATCH = 90  # rapidfuzz token_set_ratio, after default_process
TITLE_SORT_MATCH = 80  # and token_sort_ratio: a title whose words are a subset of another's scores 100 on the set ratio
TARGETS = {"recall_included": 0.85, "conclusion_direction_agrees": True, "gates_passed": True}
AUDIENCE = {"medicine": "clinicians and health researchers", "biology": "biologists and physiologists",
            "chemistry": "food and analytical chemists", "cs": "machine-learning and imaging researchers"}
# the vote on the main claim's direction (rk.production.vote); "unclear" is the conservative option
DIRECTION_OPTIONS = ["benefit", "harm", "no_difference", "mixed", "unclear"]
UNCLEAR = "unclear"
DIRECTION_QUESTION = (
    "The main claim of a systematic review is given with the outcome it is scored on and the comparison. Which "
    "direction of effect does the claim state for that outcome, for the intervention or exposure against its "
    "comparison? benefit: it improves the outcome or lowers something harmful; harm: it worsens the outcome or raises "
    "something harmful; no_difference: no meaningful difference; mixed: different directions under different "
    "conditions or in different subgroups; unclear: the claim states no direction for this outcome.")
SEED_REASON = ("seeded from the systematic-review reproduction set: the question of a published review frozen as the "
               "research question (Plan 5c Deviation 4); discovery did not run")
# why no back-edge reopens a seeded stage (rk.orchestrator.state.fixed_stages): "<stage> is fixed by <FIXED_REASON>"
FIXED_REASON = "the reproduction seed (its question is the published review's)"


# ---- the manifest -----------------------------------------------------------------------------------------------
class Study(BaseModel):
    """An included study as the review lists it: its DOI and PMID where the review gives them, its title always, and
    ``ref`` the id of its entry in the review's reference list (Europe PMC full text), the provenance."""
    doi: str | None = None
    pmid: str | None = None
    title: str = Field(min_length=1)
    ref: str | None = None


class MainConclusion(BaseModel):
    outcome: str = Field(min_length=1)
    direction: Direction


class SearchRun(BaseModel):
    source: Literal["europepmc", "pubmed", "openalex"]
    query: str = Field(min_length=1)


class Search(BaseModel):
    """The search that showed the question fits the record budget (§7.2): the review's own strategy adapted to the
    kernel's sources (their query syntax, in the review's window), run at selection; ``records`` the hits of every
    search, ``deduplicated`` after merging by DOI, PMID and normalised title, ``included_found`` how many of the
    review's included studies it found."""
    searches: list[SearchRun] = Field(min_length=1)
    date_from: int | None = None
    date_to: int | None = None
    records: int = Field(ge=0)
    deduplicated: int = Field(ge=0)
    included_found: int = Field(ge=0)
    retrieved: str

    @field_validator("deduplicated")
    @classmethod
    def _within_budget(cls, v: int) -> int:
        if v > RECORD_CAP:
            raise ValueError(f"the search dedupes to {v} records, over the {RECORD_CAP}-record budget (§7.2)")
        return v


class Provenance(BaseModel):
    review_url: str
    included_from: str  # where in the review the included studies are listed
    retrieved: str


class Entry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=40)
    pmcid: str = Field(pattern=r"^PMC\d+$")
    doi: str
    pmid: str | None = None  # the review's own (with its DOI, PMCID and title: how a run's use of it is detected)
    title: str
    domain: Domain
    question: str = Field(min_length=1)  # a "whether …"/"how …" clause: the y of the RQ's three-step sentence
    pico: Pico | None = None
    date_from: int | None = None  # the review's search window, in publication years (None: from the earliest)
    date_to: int | None = None
    included: list[Study] = Field(min_length=1)
    main_conclusion: MainConclusion
    note: str = Field(min_length=1)  # why it was chosen, and what the selection found
    search: Search
    provenance: Provenance

    @model_validator(mode="after")
    def _found_at_most_all(self) -> Entry:
        if self.search.included_found > len(self.included):
            raise ValueError(f"{self.id}: the search found more included studies than the review lists")
        return self


class Manifest(BaseModel):
    version: int
    entries: list[Entry]

    @model_validator(mode="after")
    def _the_set(self) -> Manifest:
        if len(self.entries) != ENTRIES:
            raise ValueError(f"the reproduction set has {ENTRIES} reviews, not {len(self.entries)}")
        if missing := [d for d in DOMAINS if d not in {e.domain for e in self.entries}]:
            raise ValueError(f"no review of the domain(s) {', '.join(missing)}: each domain needs one at least")
        for what in ("id", "pmcid"):
            values = [getattr(e, what) for e in self.entries]
            if len(set(values)) != len(values):
                raise ValueError(f"the reviews' {what}s repeat: {values}")
        return self


def load_manifest(path: Path | None = None) -> list[Entry]:
    """The reproduction set's entries; ``ValueError`` when the manifest breaks the schema or the set's rules (six
    reviews, every domain, unique ids, each search within the record budget)."""
    return Manifest.model_validate_json(Path(path or MANIFEST_PATH).read_text()).entries


def manifest_sha256(path: Path | None = None) -> str:
    return hashlib.sha256(Path(path or MANIFEST_PATH).read_bytes()).hexdigest()


def entry_by_id(entry_id: str) -> Entry:
    for e in load_manifest():
        if e.id == entry_id:
            return e
    raise LookupError(f"no entry {entry_id!r} in the reproduction set "
                      f"({', '.join(e.id for e in load_manifest())})")


def slug_of(entry: Entry) -> str:
    return SLUG_PREFIX + entry.id


# ---- seeding ----------------------------------------------------------------------------------------------------
def seeded(ps: ProjectStore) -> dict | None:
    """The project's seed record (``entry``, ``date_from``, ``date_to``, ``manifest_sha256``), or ``None``."""
    r = ps.db.execute("SELECT payload FROM events WHERE type=? ORDER BY seq LIMIT 1", (SEEDED,)).fetchone()
    return json.loads(r["payload"]) if r else None


def _window(entry: Entry) -> str:
    start = str(entry.date_from) if entry.date_from is not None else "the earliest record"
    return f"from {start} to {entry.date_to if entry.date_to is not None else 'now'}"


def rq_body(entry: Entry) -> RQBody:
    """The review's question as a frozen RQ: the question is the y of the three-step sentence; nothing names the
    review (its title, DOI or PMCID)."""
    p = entry.pico
    outcome = p.outcome if p else entry.question  # never the main conclusion: that is the review's finding
    x = f"{p.intervention} compared with {p.comparison} in {p.population}" if p else entry.question
    return RQBody.model_validate({
        "three_step": {"x": x, "y": entry.question,
                       "z": f"what the primary studies published {_window(entry)} show about {outcome}"},
        "problem": {"condition": f"Primary studies of {x} report {outcome}; only a synthesis of the studies a "
                                 "systematic search finds says whether they agree.",
                    "consequences": [f"Readers weigh single studies of {outcome} without a synthesis.",
                                     "Decisions rest on the studies a reader happens to know."]},
        "finer": {"feasible": f"Primary studies published {_window(entry)} are indexed in the kernel's sources.",
                  "interesting": f"{AUDIENCE[entry.domain].capitalize()} ask it.",
                  "novel": "A given question (the reproduction set, Plan 5c Deviation 4): novelty is not assessed.",
                  "ethical": "It uses published data only.", "relevant": f"It bears on {outcome}."},
        # a clinical PICO (medicine, and the cs entries that evaluate clinical AI) makes the question clinical
        "clinical": entry.domain == "medicine" or (entry.domain == "cs" and p is not None),
        "pico": p.model_dump() if p else None,
    })


def _hypotheses(entry: Entry) -> list[HypothesisIn]:
    p = entry.pico
    if p is None:
        q = entry.question
        return [HypothesisIn(statement=f"The studies published {_window(entry)} agree on the answer to {q}.",
                             falsification=f"They do not agree on the answer to {q}.")]
    return [HypothesisIn(statement=f"{p.intervention} changes {p.outcome} compared with {p.comparison} in "
                                   f"{p.population}.",
                         falsification=f"The included studies show no difference in {p.outcome} between "
                                       f"{p.intervention} and {p.comparison}.")]


def _topic(entry: Entry) -> Topic:
    q = entry.question
    return Topic(title=q[:1].upper() + q[1:], summary=f"The research question is given: {q}.", gap_ids=[],
                 feasibility="Published primary studies, found by a systematic search of the kernel's sources.",
                 data_needed=f"Primary studies published {_window(entry)} (the search window of this question).",
                 paper_type=PAPER_TYPE,
                 risks="The question is given (the reproduction set): discovery did not run, so no gap or novelty "
                       "check stands behind it.")


def seed_project(kernel, entry: Entry, *, actor: str = ACTOR) -> ProjectStore:
    """The entry's project ``srrepro-<id>``, created and seeded on first use (Deviation 4): a P1 (auto) D2 systematic
    review whose RQ is the entry's question, frozen as adopted by you (the published question, chosen for the
    reproduction set), with every discovery stage recorded as passed, so a run starts at ``protocol``, and fixed
    (``record_fixed_stages``, ``FIXED_REASON``): no back-edge reopens one, and argue's RQ vote goes back to synthesize.
    Resumable: the seed record comes first and every later step checks what is stored, so seeding again (or after an
    interruption) continues the project and records nothing twice; a project seeded before its stages were fixed has
    them fixed when seeded again. A project with that slug not seeded from the entry is refused (``ValueError``).

    Calibration (seeded reproduction, 2026-11-13): unfixed, a P1 RQ vote that found the main claim not answering the RQ
    sent srrepro7 and srrepro8 back to question, whose novelty check found published reviews (the reproduced one among
    them) and went on to topics and gaps, where the run failed (``docs/eval/seeded-fixed-2026-11-13.md``)."""
    slug, title = slug_of(entry), f"Reproduction set: {entry.id}"
    try:
        ps, _ = kernel.project(slug)
    except LookupError:
        ps = kernel.create_project(slug, title, field=entry.domain, paper_type=PAPER_TYPE, actor=actor)
    got = seeded(ps)
    if got is None and _created_unseeded(ps, title):  # an interruption between creating it and its seed record
        ps.apply(NewEvent(type=SEEDED, entity_id=ps.project_id, actor_kind="kernel", actor=actor,
                          payload={"entry": entry.id, "date_from": entry.date_from, "date_to": entry.date_to,
                                   "manifest_sha256": manifest_sha256()}))
        got = seeded(ps)
    if got is None or got["entry"] != entry.id:
        raise ValueError(f"the project {slug} exists but was not seeded from the reproduction entry {entry.id}; "
                         "rename or remove it to reproduce the review")
    with ps.lock:
        if intake_of(ps) is None:
            record_intake(ps, Intake(audience=AUDIENCE[entry.domain], paper_type=PAPER_TYPE, discovery_mode="d2",
                                     production_mode="auto", language="en", topics_n=1),
                          actor=actor, actor_kind="human")
        if frozen_rq(ps) is None:
            row = ps.db.execute("SELECT id FROM rqs ORDER BY rowid DESC LIMIT 1").fetchone()
            if row is None:  # the topic may be there already (an interruption before its RQ was added)
                topic = _topic(entry)
                t = ps.db.execute("SELECT id, status FROM topics WHERE title=? ORDER BY rowid LIMIT 1",
                                  (topic.title,)).fetchone()
                tid = t["id"] if t else add_topic(ps, 1, topic, actor=actor, actor_kind="kernel")
                if t is None or t["status"] != "approved":
                    set_topic_status(ps, tid, "approved", actor=actor, actor_kind="human")
                rid = add_rq(ps, 1, tid, rq_body(entry), _hypotheses(entry), actor=actor, actor_kind="kernel")
            else:  # an interruption after the RQ was added only freezes it now
                rid = row["id"]
            freeze_rq(ps, rid, adopted_by="human", actor=actor)
        for s in DISCOVERY:
            r = stage_row(ps, s)
            if r is not None and r["status"] == "passed":
                continue
            if r is None or not r["attempt"]:
                record_attempt(ps, s, 1, run_id=None, guidance="", requested=None, actor=actor)
            if not attempt_ran(ps, s, 1):  # an interruption between ran and passed records the pass only
                record_ran(ps, s, 1, "done", SEED_REASON, actor=actor)
            record_passed(ps, s, 1, actor=actor)
        if unfixed := [s for s in DISCOVERY if s not in fixed_stages(ps)]:  # once; a project seeded before gets it
            record_fixed_stages(ps, unfixed, FIXED_REASON, actor=actor)
    return ps


def _created_unseeded(ps: ProjectStore, title: str) -> bool:
    """The project is one ``seed_project`` created and was interrupted before its seed record: its title and paper
    type are the reproduction set's, and nothing else was recorded in it (no intake, topic or stage)."""
    return (ps.meta("title") == title and ps.meta("paper_type") == PAPER_TYPE and intake_of(ps) is None
            and ps.db.execute("SELECT 1 FROM topics LIMIT 1").fetchone() is None
            and ps.db.execute("SELECT 1 FROM events WHERE type LIKE 'stage.%' LIMIT 1").fetchone() is None)


# ---- scoring ----------------------------------------------------------------------------------------------------
def _norm_doi(v: str | None) -> str | None:
    return normalize_doi(v) if v else None


def _norm_pmid(v: str | None) -> str | None:
    return normalize_pmid(str(v)) if v else None


def _ids_title(data: dict) -> dict:
    ids = data.get("ids") or {}
    return {"title": data.get("title") or "", "doi": ids.get("doi"), "pmid": ids.get("pmid"),
            "pmcid": ids.get("pmcid")}


def found_studies(ps: ProjectStore) -> list[dict]:
    """The studies the project included at full text (``search_screen``'s passed attempt), as ``{item_id, title,
    doi, pmid, pmcid}``; ``[]`` before it passed."""
    out = []
    for rec in included_studies(ps):
        r = ps.db.execute("SELECT data FROM items WHERE id=?", (rec["item_id"],)).fetchone()
        out.append({"item_id": rec["item_id"], **_ids_title(json.loads(r["data"]) if r else {})})
    return out


def _title_ratios(a: str, b: str) -> tuple[float, float]:
    return (fuzz.token_set_ratio(a, b, processor=utils.default_process),
            fuzz.token_sort_ratio(a, b, processor=utils.default_process))


def titles_match(a: str, b: str) -> bool:
    """The same title: ``token_set_ratio >= TITLE_MATCH`` and ``token_sort_ratio >= TITLE_SORT_MATCH`` (a title whose
    words are a subset of another's is not it)."""
    s, o = _title_ratios(a, b)
    return s >= TITLE_MATCH and o >= TITLE_SORT_MATCH


def match_included(expected: list[Study], found: list[dict]) -> tuple[list[dict], list[Study]]:
    """Match the review's included studies to the project's, one to one: every DOI and PMID match first, then, for
    the rest, the unmatched found study whose title matches best (``titles_match``; highest ``token_set_ratio``, then
    ``token_sort_ratio``). Returns the matches (``{title, doi, pmid, by, item_id}``, in the review's order) and the
    studies left unmatched."""
    free = list(range(len(found)))
    by_id: dict[tuple[str, str], int] = {}
    for i, f in enumerate(found):
        for kind, v in (("doi", _norm_doi(f.get("doi"))), ("pmid", _norm_pmid(f.get("pmid")))):
            if v:
                by_id.setdefault((kind, v), i)
    matched: dict[int, dict] = {}
    for n, s in enumerate(expected):
        for kind, v in (("doi", _norm_doi(s.doi)), ("pmid", _norm_pmid(s.pmid))):
            i = by_id.get((kind, v)) if v else None
            if i is not None and i in free:
                free.remove(i)
                matched[n] = {**s.model_dump(), "by": kind, "item_id": found[i].get("item_id")}
                break
    for n, s in enumerate(expected):
        if n in matched or not free:
            continue
        passing = [(r, j) for j in free if (r := _title_ratios(s.title, found[j]["title"] or ""))[0] >= TITLE_MATCH
                   and r[1] >= TITLE_SORT_MATCH]
        if passing:  # the best among the titles that match (a subset title cannot hide the real one)
            _, i = max(passing)
            free.remove(i)
            matched[n] = {**s.model_dump(), "by": "title", "item_id": found[i].get("item_id")}
    return ([matched[n] for n in sorted(matched)],
            [s for n, s in enumerate(expected) if n not in matched])


def is_review(entry: Entry, study: dict) -> bool:
    """``study`` (``{title, doi, pmid, pmcid}``) is the reproduced review itself: its DOI, PMID or PMCID, or its
    title."""
    if _norm_doi(study.get("doi")) and _norm_doi(study.get("doi")) == _norm_doi(entry.doi):
        return True
    if entry.pmid and _norm_pmid(study.get("pmid")) == _norm_pmid(entry.pmid):
        return True
    if (study.get("pmcid") or "").strip().upper() == entry.pmcid.upper():
        return True
    return titles_match(entry.title, study.get("title") or "")


def review_items(ps: ProjectStore, entry: Entry) -> set[str]:
    """The project's library items that are the reproduced review (``is_review``)."""
    return {r["id"] for r in ps.db.execute("SELECT id, data FROM items")
            if is_review(entry, _ids_title(json.loads(r["data"])))}


def argument_items(ps: ProjectStore, claim_id: str) -> set[str]:
    """The library items the evidence notes of the claim and of its reasons (``reason_of`` edges, to any depth)
    come from."""
    svc, seen, todo, items = ArgumentService(ps), set(), [claim_id], set()
    while todo:
        cid = todo.pop()
        if cid in seen:
            continue
        seen.add(cid)
        try:
            c = svc.get_claim(cid)
        except KeyError:
            continue
        items |= {n["item_id"] for e in c["evidence"] for n in e["notes"] if n and n.get("item_id")}
        todo += [e["from_id"] for e in svc.list_edges(to_id=cid, type="reason_of")]
    return items


def gates_passed(ps: ProjectStore) -> bool:
    """Every hard gate passed: audit passed, and the latest report of its passed attempt is ok."""
    row = stage_row(ps, "audit")
    if row is None or row["status"] != "passed":
        return False
    mine = [r for r in audit_reports(ps) if r["attempt"] == row["passed_attempt"]]
    return bool(mine) and bool(mine[-1]["ok"])


def final_main_claim(ps: ProjectStore) -> dict | None:
    """The main claim of argue's passed attempt (its latest narrower rewrite) with its id, or ``None``."""
    row = stage_row(ps, "argue")
    if row is None or row["status"] != "passed" or not row["passed_attempt"]:
        return None
    cid = main_claim(ps, row["passed_attempt"])
    return {"id": cid, **entity_row(ps, "claims", cid)} if cid else None


def objection_sources(ps: ProjectStore) -> set[str]:
    """The library items any objection of the project cites as its ``source_id`` (argue's, in any attempt, or one
    added in a chat with ``rk_objection_add``): one conceded in an earlier attempt is not carried into the next (argue
    imports only open ones), yet it shaped the claim."""
    out = set()
    for r in ps.db.execute("SELECT data FROM objections"):
        src = json.loads(r["data"]).get("source_id")
        if src:
            out.add(src)
    return out


def judge_direction(ctx: StageContext, entry: Entry, claim: dict):
    """The vote (``rk.production.vote``) on the direction the main claim states for the entry's outcome (the main
    conclusion's, which names what the review compares); the main claim's author's vendor is no judge. Recorded in
    the project and reused when asked again."""
    inputs = {"main_claim": claim["text"], "scope_limits": claim.get("scope_limits") or [],
              "outcome": entry.main_conclusion.outcome}
    return vote(ctx, "sr_repro_direction", f"{entry.id}:{claim['id']}", DIRECTION_QUESTION, DIRECTION_OPTIONS,
                UNCLEAR, inputs, generator_vendors={claim.get("generator_vendor")}, reuse=True)


def score(ps: ProjectStore, entry: Entry, *, ctx: StageContext | None = None) -> dict:
    """The project against the review (module docstring): ``recall_included``, ``conclusion_direction_agrees``,
    ``gates_passed`` and ``missed`` (the review's included studies the project did not include), with the matches,
    the direction decided and why none was, whether export passed and the protocol's search window. Leakage:
    ``review_included`` (the reproduced review is one of the project's included studies), ``claim_from_review``
    (an evidence note of the main claim or of its reasons comes from it) and ``objection_from_review`` (an objection
    of the project, argue's in any attempt or one added in a chat, cites it as its source: the Skeptic may search
    outside the window) each make
    ``direction_compromised`` the reason the direction does not count; ``review_in_library`` (retrieved, perhaps
    unused) is information only. The
    direction is judged only with ``ctx`` (the judges' executor; its ``stage`` and ``attempt`` key the recorded
    vote)."""
    found = found_studies(ps)
    matched, missed = match_included(entry.included, found)
    out: dict = {"entry": entry.id, "recall_included": len(matched) / len(entry.included),
                 "included_total": len(entry.included), "found_total": len(found), "matched": matched,
                 "missed": [s.model_dump() for s in missed], "expected_direction": entry.main_conclusion.direction,
                 "conclusion_direction_agrees": None, "direction": None, "main_claim_id": None, "main_claim": None,
                 "vote_id": None, "direction_note": "", "gates_passed": gates_passed(ps)}
    export = stage_row(ps, "export")
    out["exported"] = export is not None and export["status"] == "passed"
    out["stage"] = current_stage(ps)
    fp = frozen_protocol(ps)
    strategy = (fp or {}).get("body", {}).get("search_strategy") or {}
    out["protocol_window"] = [strategy.get("date_from"), strategy.get("date_to")] if fp else None
    review = review_items(ps, entry)
    out["review_in_library"] = bool(review)
    out["review_included"] = any(f["item_id"] in review or is_review(entry, f) for f in found)
    claim = final_main_claim(ps)
    out["claim_from_review"] = bool(claim) and bool(argument_items(ps, claim["id"]) & review)
    out["objection_from_review"] = bool(review) and bool(objection_sources(ps) & review)
    why = ([*(["the reproduced review is one of the project's included studies"] if out["review_included"] else []),
            *(["an evidence note of the main claim or of one of its reasons comes from the reproduced review"]
              if out["claim_from_review"] else []),
            *(["an objection of the argument cites the reproduced review as its source"]
              if out["objection_from_review"] else [])])
    out["direction_compromised"] = "; ".join(why) or None
    if claim is None:
        out["direction_note"] = "argue has not passed: there is no main claim to judge"
        return out
    out["main_claim_id"], out["main_claim"] = claim["id"], claim["text"]
    if ctx is None:
        out["direction_note"] = "not judged here (no judges' context)"
        return out
    res = judge_direction(ctx, entry, claim)
    out["vote_id"] = res.id
    if res.conservative_used:
        out["direction_note"] = f"no agreement of two judges of different vendors: {res.reason}"
    elif res.decision == UNCLEAR:
        out["direction_note"] = "the judges agree the main claim states no direction for the outcome"
    else:
        out["direction"] = res.decision
        out["conclusion_direction_agrees"] = res.decision == entry.main_conclusion.direction
    return out


# ---- runs (rk serve) --------------------------------------------------------------------------------------------
def run_or_continue(kernel, runs: RunManager, entry: Entry, *, actor: str) -> dict:
    """Seed the entry's project if needed, then run it until ``export``: start a run when none is active (a cancelled
    one is replaced), resume a paused one (or give a run back its thread after ``rk serve`` restarted); a run in
    progress is left to go on; an exported project runs no more. A failed run is the entry's result and is not
    replaced: the back-edge limits count per project, so a new run would start with them already spent (one more vote
    at most where the old run failed at an edge limit). ``rk run resume`` on the project would also start a new run
    with only the back-edges the project has left (enough after a failure that spent none, such as a source that kept
    failing or a crash); to reproduce an entry with its full limits, start from a fresh RK_HOME."""
    slug = slug_of(entry)
    try:
        kernel.project(slug)
        new = False
    except LookupError:
        new = True
    ps = seed_project(kernel, entry, actor=actor)
    export = stage_row(ps, "export")
    last = latest_run(ps)
    if export is not None and export["status"] == "passed":
        action, run = "exported", last
    elif active_run(ps) is None and last is not None and last["status"] == "failed":
        action, run = (f"its run failed ({last['reason'] or 'no reason recorded'}); not started again (`rk run resume "
                       f"--project {slug}` starts a new run with the back-edges the project has left)"), last
    elif active_run(ps) is None:
        runs.start(slug, "export", actor=actor)
        action, run = "started a run until export", latest_run(ps)
    else:
        try:
            runs.resume(slug, actor=actor)
            action = "resumed its run"
        except ToolError as e:
            if e.type != "run_active":
                raise
            action = "its run is in progress"
        run = latest_run(ps)
    return {"entry": entry.id, "project": slug, "seeded": new, "action": ("seeded; " if new else "") + action,
            "run_id": run["id"] if run else None, "run_status": run["status"] if run else None,
            "stage": current_stage(ps)}


class SrReproArg(BaseModel):
    entry: str


def register_sr_repro_tools(tool: Callable, kernel, runs: Callable[[], RunManager | None] = lambda: None) -> None:
    """``rk_sr_repro_run`` and ``rk_sr_repro_score`` (yours alone, rk.roles.policy): ``tool`` is ``build_registry``'s
    decorator, ``runs()`` rk serve's RunManager or None."""

    def entry_of(entry_id: str) -> Entry:
        try:
            return entry_by_id(entry_id)
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e

    @tool("rk_sr_repro_run", "Reproduce one review of the systematic-review reproduction set (spec §10): seed its "
          "project srrepro-<id> (its question frozen as the RQ) if needed, then run it until export, or continue its "
          "run; a failed run is reported, not started again. Needs rk serve's orchestrator.", SrReproArg, True)
    def _run(ctx: CallContext, a: SrReproArg) -> dict:
        entry = entry_of(a.entry)
        m = runs()
        if m is None:
            raise ToolError("unavailable", "runs are driven by rk serve's orchestrator, which is not available here",
                            status=503)
        try:
            return run_or_continue(kernel, m, entry, actor=ctx.actor)
        except ValueError as e:
            raise ToolError("invalid", str(e), status=409) from e

    @tool("rk_sr_repro_score", "Score one reproduced review against the published one: recall of its included "
          "studies, whether the main claim's direction agrees (the judges' vote, recorded in the project) and whether "
          "every hard gate passed. The direction is judged once argue passed, while no run of the project is "
          "active.", SrReproArg, True)
    def _score(ctx: CallContext, a: SrReproArg) -> dict:
        from rk.orchestrator.engine import StageContext

        entry = entry_of(a.entry)
        slug = slug_of(entry)
        try:
            ps, lib = kernel.project(slug)
        except LookupError as e:
            raise ToolError("not_found", f"{slug} is not seeded yet: run rk eval sr-repro", status=404) from e
        if seeded(ps) is None:
            raise ToolError("invalid", f"the project {slug} was not seeded from the reproduction set", status=409)
        m = runs()
        if m is None or active_run(ps) is not None:
            out = score(ps, entry)
            if out["main_claim_id"]:
                out["direction_note"] = ("not judged now: " + ("a run of the project is active" if m else
                                                               "rk serve's orchestrator is not available"))
            return out
        vctx = StageContext(kernel=kernel, registry=m.registry, ps=ps, lib=lib, project=slug, stage=STAGE,
                            attempt=1, run_id=None, guidance="",
                            executor=m.orchestrator.executor_factory(slug, None, None),
                            sources=m.orchestrator.sources_factory(slug), actor=ctx.actor)
        try:
            return score(ps, entry, ctx=vctx)
        except RateCapped as e:
            out = score(ps, entry)
            out["direction_note"] = f"not judged now: a judge's driver {e.why} (rerun rk eval sr-repro later)"
            return out


# ---- the report -------------------------------------------------------------------------------------------------
def report_row(entry: Entry, run: dict, got: dict) -> dict:
    """One entry of the report: the review, what was run and the score, and whether it meets each target. A
    compromised direction (``direction_compromised``: the review included, or the main claim resting on it) does not
    meet its target whatever the judges said. ``selection_ceiling`` is the share of the included studies the
    selection's own search found (``search.included_found / k``), the recall a run can reach with that search."""
    meets = {"recall_included": got["recall_included"] >= TARGETS["recall_included"],
             "conclusion_direction_agrees": (got["conclusion_direction_agrees"] is True
                                             and not got["direction_compromised"]),
             "gates_passed": got["gates_passed"] is True}
    return {"id": entry.id, "domain": entry.domain, "pmcid": entry.pmcid, "doi": entry.doi, "title": entry.title,
            "project": run["project"], "action": run["action"], "run_id": run["run_id"],
            "run_status": run["run_status"], "stage": got["stage"], "exported": got["exported"],
            "recall_included": got["recall_included"], "included_total": got["included_total"],
            "selection_ceiling": entry.search.included_found / len(entry.included),
            "matched": len(got["matched"]), "found_total": got["found_total"], "missed": got["missed"],
            "expected_direction": got["expected_direction"], "direction": got["direction"],
            "conclusion_direction_agrees": got["conclusion_direction_agrees"],
            "direction_compromised": got["direction_compromised"],
            "direction_note": got["direction_note"], "main_claim": got["main_claim"],
            "gates_passed": got["gates_passed"], "protocol_window": got["protocol_window"],
            "search_window": [entry.date_from, entry.date_to], "review_in_library": got["review_in_library"],
            "review_included": got["review_included"], "claim_from_review": got["claim_from_review"],
            "objection_from_review": got["objection_from_review"],
            "meets": meets, "meets_all": all(meets.values())}


def _cell(v) -> str:
    return "—" if v is None else ("yes" if v is True else "no" if v is False else str(v))


def write_report(rows: list[dict], out_dir: Path, *, date: str | None = None) -> tuple[Path, Path]:
    """``sr-repro-<date>.md`` and ``.json`` in ``out_dir``: every entry's row and the targets (§10)."""
    date = date or datetime.now(UTC).date().isoformat()
    data = {"date": date, "manifest_sha256": manifest_sha256(), "targets": TARGETS, "entries": rows,
            "summary": {"entries": len(rows), "meets_all": sum(r["meets_all"] for r in rows),
                        "exported": sum(r["exported"] for r in rows)}}
    lines = [f"# Systematic-review reproduction set ({date})", "",
             ("Spec §10 targets: recall of the included studies ≥ 0.85; the main conclusion's direction agrees (two "
              "judges of different vendors, none the main claim's author's); every hard gate passes."), "",
             ("| entry | domain | project | stage | exported | recall (selection ceiling) | direction (review → "
              "reproduced) | agrees | gates | all targets |"),
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        agrees = _cell(r["conclusion_direction_agrees"]) + (" (compromised)" if r["direction_compromised"] else "")
        lines.append(f"| {r['id']} | {r['domain']} | {r['project']} | {_cell(r['stage'] or 'done')} | "
                     f"{_cell(r['exported'])} | {r['recall_included']:.2f} ({r['matched']}/{r['included_total']}; "
                     f"{r['selection_ceiling']:.2f}) | {r['expected_direction']} → {_cell(r['direction'])} | {agrees}"
                     f" | {_cell(r['gates_passed'])} | {_cell(r['meets_all'])} |")
    for r in rows:
        lines += ["", f"## {r['id']}", "", f"- review: {r['title']} ({r['doi']}, {r['pmcid']})",
                  f"- run: {r['action']} (run {_cell(r['run_id'])}, {_cell(r['run_status'])})",
                  f"- search window: {r['search_window']}; the frozen protocol's: {_cell(r['protocol_window'])}",
                  (f"- the reproduced review in the project's library: {_cell(r['review_in_library'])} "
                   f"(information); included: {_cell(r['review_included'])}; the main claim rests on it: "
                   f"{_cell(r['claim_from_review'])}; an objection cites it: {_cell(r['objection_from_review'])}"),
                  *([f"- the direction does not count: {r['direction_compromised']}"] if r["direction_compromised"]
                    else []),
                  f"- main claim: {_cell(r['main_claim'])}",
                  f"- direction: {_cell(r['direction'])} {('(' + r['direction_note'] + ')') if r['direction_note'] else ''}"
                  .rstrip()]
        if r["missed"]:
            lines += ["- missed included studies:"] + [f"  - {m['title']} ({m.get('doi') or m.get('pmid') or 'no id'})"
                                                        for m in r["missed"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / f"sr-repro-{date}.md", out_dir / f"sr-repro-{date}.json"
    md.write_text("\n".join(lines) + "\n")
    js.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return md, js
