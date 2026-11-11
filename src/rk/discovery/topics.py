"""The topics stage: the Ideator proposes topics over the verified gaps of the gaps stage; the kernel keeps only gap ids
of verified gaps (and topics with at least one), adds each topic's closest works from V-NOVELTY (a reference, never a
novelty verdict) and opens a ``topic_review`` checkpoint, in every mode: only a human answers it.

Answers (``options.topic_ids`` lists the topics under review):
- ``{"decision": "approve", "topic_id"}``: the topic is approved and the attempt goes to the gate;
- ``{"decision": "revise", "target": "topics" | "gaps", "comment", "topic_ids"?}``: the comment is recorded as an
  exploration round and guides a rerun of topics (``rerun``) or of gaps (``back_edge``);
- ``{"decision": "reject", "topic_ids"?}``: the topics are rejected and the stage reruns.
``topic_ids`` (or ``topic_id``) names the topics a revise or reject is about; without it, every topic under review. An
answer that cannot be applied opens a new ``topic_review`` that says why.

Budget per attempt: 1 ideator task, then per topic 1 scout + 2 judge tasks (V-NOVELTY; the judges' tasks do not count
against the stage budget); the intake caps ``topics_n`` at ``MAX_TOPICS_N`` (12: 37 tasks, 13 counted) so an attempt
stays within the stage budget (the default 6 topics: 19 tasks, 7 counted).

Gate: exactly one approved topic in the latest attempt; D2 also needs ``topics_n`` topics, each with a verified gap and
its 5 closest works (fewer only when its searches found fewer, as recorded with the topic); D1 needs one."""

from __future__ import annotations

from rk.checkpoints import (
    answered_by_actor,
    create_checkpoint,
    get_checkpoint,
    opened_in_place_of,
)
from rk.discovery.models import Intake, Topic, TopicsOut
from rk.discovery.store import (
    add_round,
    add_topic,
    gaps,
    intake_of,
    round_for_checkpoint,
    set_topic_status,
    topic_novelty,
    topics,
)
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import record_degraded, stage_row
from rk.store.project import ProjectStore
from rk.verify.novelty import novelty_claims, novelty_reference

CLOSEST_K = 5
TOPIC_TEXTS = ("title", "summary", "feasibility", "data_needed", "risks")  # a topic's generated texts
DECISIONS = ("approve", "revise", "reject")
TARGETS = ("topics", "gaps")

IDEATE_TASK = (
    "Propose {n} candidate topics for the project, each grounded in one or more of the given verified gaps. For each "
    "give a short title, a summary of two or three sentences, the ids of the gaps it rests on (only ids given in "
    "`gaps`; never invent or alter one), its feasibility, the data it needs, the paper type that fits it (one of "
    "systematic_review, scoping_review, critical_review, research_proposal) and its main risks. A gap was not found "
    "within the searched scope; never call a topic novel. Follow the guidance when there is any.")


def _verified_gaps(ps: ProjectStore) -> list[dict] | None:
    """The verified gaps of the attempt that passed the gaps gate (``None`` when gaps has not passed)."""
    row = stage_row(ps, "gaps")
    if row is None or row["passed_attempt"] is None:
        return None
    return gaps(ps, row["passed_attempt"], verified=True)


def _quotes(ps: ProjectStore, note_ids: list[str]) -> list[dict]:
    out = []
    for nid in note_ids:
        r = ps.db.execute("SELECT item_id, json_extract(data, '$.text') AS text FROM notes WHERE id=?",
                          (nid,)).fetchone()
        if r is not None:
            out.append({"item_id": r["item_id"], "quote": r["text"]})
    return out


def _gap_input(ps: ProjectStore, g: dict) -> dict:
    return {"gap_id": g["id"], "kind": g["kind"], "statement": g["statement"],
            "item_ids": g["evidence"].get("item_ids", []), "quotes": _quotes(ps, g["evidence"].get("note_ids", []))}


def _ids_of(answer: dict) -> list[str] | None:
    """The topic ids a revise or reject names (``topic_ids`` or ``topic_id``); ``None`` when it names none."""
    if isinstance(answer.get("topic_ids"), list):
        return [str(t) for t in answer["topic_ids"]]
    if answer.get("topic_id"):
        return [str(answer["topic_id"])]
    return None


def _status(ps: ProjectStore, topic_id: str) -> str | None:
    r = ps.db.execute("SELECT status FROM topics WHERE id=?", (topic_id,)).fetchone()
    return r["status"] if r else None


def _set_status(ctx: StageContext, topic_id: str, status: str, *, actor: str, actor_kind: str) -> None:
    if _status(ctx.ps, topic_id) != status:  # an answer handled again sets nothing twice
        set_topic_status(ctx.ps, topic_id, status, actor=actor, actor_kind=actor_kind)


def _last_answered_review(ps: ProjectStore, attempt: int) -> dict | None:
    """The latest answered ``topic_review`` of the attempt (by the order of the answers)."""
    r = ps.db.execute(
        "SELECT e.entity_id AS id FROM events e JOIN checkpoints c ON c.id = e.entity_id "
        "WHERE e.type='checkpoint.answered' AND c.kind='topic_review' AND json_extract(c.options, '$.attempt')=? "
        "ORDER BY e.seq DESC LIMIT 1", (attempt,)).fetchone()
    return get_checkpoint(ps, r["id"]) if r else None


def _review_prompt(ps: ProjectStore, topic_ids: list[str], why: str = "") -> str:
    lines = []
    for tid in topic_ids:
        r = ps.db.execute("SELECT title FROM topics WHERE id=?", (tid,)).fetchone()
        if r is not None:
            lines.append(f"- {tid}: {r['title']}")
    head = f"{why}\n\n" if why else ""
    return (f"{head}Review {len(lines)} candidate topic(s):\n" + "\n".join(lines) + "\n\n"
            "Approve one ({\"decision\": \"approve\", \"topic_id\": ...}), ask for a revision ({\"decision\": "
            "\"revise\", \"target\": \"topics\" or \"gaps\", \"comment\": ..., optional \"topic_ids\"}) or reject "
            "({\"decision\": \"reject\", optional \"topic_ids\"}). The closest works of each topic are a reference "
            "only, never a novelty verdict.")


def _open_review(ctx: StageContext, topic_ids: list[str], *, replaces: str | None = None, why: str = "") -> str:
    """A ``topic_review`` of the attempt's ``topic_ids``. Opened in place of the answered ``replaces``, it is opened
    once: handling the same answer again returns the review opened the first time."""
    if replaces is not None and (existing := opened_in_place_of(ctx.ps, "topic_review", replaces)) is not None:
        return existing
    return create_checkpoint(ctx.ps, "topic_review", _review_prompt(ctx.ps, topic_ids, why),
                             {"topic_ids": topic_ids, "attempt": ctx.attempt, "replaces": replaces},
                             stage=ctx.stage, actor=ctx.actor)


class TopicsWorker:
    """``judges`` replaces the configured judge drivers of V-NOVELTY (tests)."""

    def __init__(self, judges: list[DriverSpec] | None = None):
        self.judges = judges

    def run(self, ctx: StageContext) -> StageOutcome:
        intake = intake_of(ctx.ps)
        if intake is None:
            return StageOutcome("failed", reason="intake missing")
        verified = _verified_gaps(ctx.ps)
        if verified is None:
            return StageOutcome("failed", reason="gaps has not passed")
        if not verified:
            return StageOutcome("back_edge", to="gaps", reason="no verified gap to build topics on")
        wanted = intake.topics_n
        context: dict = {
            "project": {"title": ctx.ps.meta("title"), "field": ctx.ps.meta("field")},
            "intake": intake.model_dump(mode="json", include={"audience", "paper_type", "discovery_mode",
                                                              "language", "topics_n"}),
            "gaps": [_gap_input(ctx.ps, g) for g in verified],
        }
        if ctx.guidance:
            context["guidance"] = ctx.guidance
        res = ctx.executor.run("ideator", IDEATE_TASK.format(n=wanted), context, TopicsOut, stage=ctx.stage,
                               attempt=ctx.attempt, tools=[])
        if not res.ok or res.output is None:
            return StageOutcome("rerun", reason=f"the ideator gave no topics ({res.error})")

        verified_ids = {g["id"] for g in verified}
        kept, seen = [], set()
        for t in res.output.topics:
            gap_ids = [g for g in dict.fromkeys(t.gap_ids) if g in verified_ids]
            key = t.title.strip().lower()
            if gap_ids and key and key not in seen:
                seen.add(key)
                kept.append(t.model_copy(update={"gap_ids": gap_ids}))
        kept = kept[:wanted]
        claims = [f"topic {t.title.strip()!r}: {c}" for t in kept
                  for c in novelty_claims([(f, getattr(t, f)) for f in TOPIC_TEXTS])]
        if claims:  # before any V-NOVELTY task is spent on them
            return StageOutcome("rerun", reason="; ".join(claims))
        short = self._too_few(intake, len(kept))
        if short:
            return StageOutcome("rerun", reason=short)

        generator = vendor_of(res.provider, res.model)
        ids, problems = [], []
        for t in kept:
            report = novelty_reference(ctx, f"{t.title}\n{t.summary}", k=CLOSEST_K, generator_vendor=generator,
                                       judges=self.judges)
            if report.problems:
                problems.append(f"topic {t.title!r}: " + "; ".join(report.problems))
                continue
            topic = Topic(**t.model_dump(), closest=report.closest)
            ids.append(add_topic(ctx.ps, ctx.attempt, topic, actor=ctx.actor,
                                 novelty=report.model_dump(mode="json", exclude={"closest"})))
        short = self._too_few(intake, len(ids))
        if short:
            return StageOutcome("rerun", reason="; ".join([short, *problems]))
        cid = _open_review(ctx, ids)
        return StageOutcome("waiting_human", checkpoint_id=cid, reason="; ".join(problems))

    @staticmethod
    def _too_few(intake: Intake, n: int) -> str:
        """Why ``n`` topics cannot pass the gate (D2 needs ``topics_n``, D1 one), else ``""``; the review is not asked
        for then."""
        need = intake.topics_n if intake.discovery_mode == "d2" else 1
        if n >= need:
            return ""
        return f"{n} of {need} topics with a verified gap and their closest works"

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        """Idempotent: handling the same answer again sets no status twice, records its comment once and returns the
        review it already opened."""
        answer = checkpoint.get("answer") or {}
        under_review = list((checkpoint.get("options") or {}).get("topic_ids") or [])
        why = self._invalid(ctx.ps, answer, under_review)
        if why:
            cid = _open_review(ctx, [t for t in under_review if _status(ctx.ps, t) != "rejected"],
                               replaces=checkpoint["id"], why=f"Your answer could not be applied: {why}.")
            return StageOutcome("waiting_human", checkpoint_id=cid, reason=why)
        by = answered_by_actor(ctx.ps, checkpoint["id"]) or ctx.actor
        decision = answer["decision"]
        if decision == "approve":
            _set_status(ctx, answer["topic_id"], "approved", actor=by, actor_kind="human")
            return StageOutcome("done")
        if decision == "reject":
            for tid in _ids_of(answer) or under_review:
                _set_status(ctx, tid, "rejected", actor=by, actor_kind="human")
            return StageOutcome("rerun", reason="topic review: topics rejected")
        comment = answer["comment"].strip()
        if round_for_checkpoint(ctx.ps, checkpoint["id"]) is None:
            add_round(ctx.ps, "topics", comment, actor=by, actor_kind="human", checkpoint_id=checkpoint["id"])
        if answer["target"] == "gaps":
            return StageOutcome("back_edge", to="gaps", reason=comment)
        return StageOutcome("rerun", reason=comment)

    @staticmethod
    def _invalid(ps: ProjectStore, answer: dict, under_review: list[str]) -> str:
        """Why the answer cannot be applied, else ``""``."""
        decision = answer.get("decision")
        if decision not in DECISIONS:
            return f"decision {decision!r} is not one of {', '.join(DECISIONS)}"
        open_ids = [t for t in under_review if _status(ps, t) not in (None, "rejected")]
        if decision == "approve":
            tid = answer.get("topic_id")
            if not tid:
                return "approve needs the topic_id of the topic you approve"
            if tid not in open_ids:
                return f"topic {tid!r} is not one of the topics under review ({', '.join(open_ids)})"
            return ""
        named = _ids_of(answer)
        if named is not None:
            unknown = [t for t in named if t not in open_ids]
            if unknown or not named:
                return f"unknown topic id(s) {', '.join(unknown) or '(none)'}; under review: {', '.join(open_ids)}"
        if decision == "revise":
            if answer.get("target") not in TARGETS:
                return f"revise needs target 'topics' or 'gaps', not {answer.get('target')!r}"
            if not isinstance(answer.get("comment"), str) or not answer["comment"].strip():
                return "revise needs a comment saying what to change"
        return ""

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        rows = topics(ps, attempt)
        failures: list[str] = []
        approved = [t for t in rows if t["status"] == "approved"]
        if len(approved) != 1:
            failures.append("no approved topic" if not approved else
                            f"{len(approved)} approved topics (approve exactly one)")
        intake = intake_of(ps)
        if intake is not None and intake.discovery_mode == "d2":
            need = intake.topics_n
            if len(rows) < need:
                failures.append(f"{len(rows)} of {need} topics")
            verified_ids = {g["id"] for g in _verified_gaps(ps) or []}
            for t in rows:
                failures += self._topic_failures(ps, t, verified_ids)
        elif not rows:
            failures.append("0 of 1 topics")
        return GateResult(not failures, failures)

    @staticmethod
    def _topic_failures(ps: ProjectStore, t: dict, verified_ids: set[str]) -> list[str]:
        out = []
        if not any(g in verified_ids for g in t["gap_ids"]):
            out.append(f"topic {t['title']!r}: no verified gap")
        novelty = topic_novelty(ps, t["id"])
        if novelty is None or not novelty.get("queries"):
            out.append(f"topic {t['title']!r}: no closest-work search recorded")
        else:
            need = min(CLOSEST_K, novelty.get("found", 0))
            if len(t["closest"]) < need:
                out.append(f"topic {t['title']!r}: {len(t['closest'])} closest works (need {need})")
        return out

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: drop the topics the last review rejected or asked to revise and ask again with the
        rest (``waiting_human``: the review is human in every mode); ``failed`` when none is left, or when no review
        answer led here (a gate failure no review can fix)."""
        cp = _last_answered_review(ctx.ps, ctx.attempt)
        answer = (cp or {}).get("answer") or {}
        if cp is None or answer.get("decision") not in ("revise", "reject"):
            return StageOutcome("failed", reason="; ".join(failures) or "the topics did not pass their gate")
        if (existing := opened_in_place_of(ctx.ps, "topic_review", cp["id"])) is not None:  # degraded already
            return StageOutcome("waiting_human", checkpoint_id=existing, reason="topic review reopened")
        under_review = list(cp["options"].get("topic_ids") or [])
        removed = [t for t in (_ids_of(answer) or under_review) if t in under_review]
        for tid in removed:
            _set_status(ctx, tid, "rejected", actor=ctx.actor, actor_kind="kernel")
        left = [t for t in under_review if t not in removed and _status(ctx.ps, t) != "rejected"]
        record_degraded(ctx.ps, stage=ctx.stage, attempt=ctx.attempt, removed=removed,
                        reason=f"topic review: {answer['decision']} at the edge limit; "
                               f"{len(left)} topic(s) left for review", actor=ctx.actor)
        if not left:
            return StageOutcome("failed", reason="no topics left")
        cid = _open_review(ctx, left, replaces=cp["id"],
                           why=f"The limit of reruns was reached, so the topics you rejected or asked to revise were "
                               f"dropped ({', '.join(removed)}).")
        return StageOutcome("waiting_human", checkpoint_id=cid, reason="topic review reopened without the dropped "
                                                                      "topics")
