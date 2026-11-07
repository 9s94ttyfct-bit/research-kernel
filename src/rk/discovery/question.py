"""The question stage: the Ideator writes one research question (RQ) with its rival hypotheses for the approved topic;
the kernel checks its structure (``check_rq``), stores it, runs the already-done check (``already_done``) and then:

- ``already_done``: back to ``topics`` (D1 and D2), a use of ``question->topics``; at its limit P2 asks
  (``stage_blocked``) and P1 narrows the RQ (``degrade``);
- ``split``: P2 opens ``significance_novelty``; P1 treats it as already done;
- ``not_found_in_scope``: D1, and D2 in P2, open ``rq_approval``; D2 in P1 adopts the RQ (``freeze_rq(adopted_by=
  "agent")``). "Not found" holds only within the searched scope; nothing here calls an RQ novel.

Answers (only a human answers either kind; ``options.rq_id`` names the RQ):
- ``rq_approval``: ``{"decision": "approve"}`` freezes the RQ (``adopted_by="human"``); ``{"decision": "revise",
  "comment"}`` records the comment as an exploration round and reruns the stage;
- ``significance_novelty``: ``{"decision": "proceed"}`` treats the RQ as not found and opens ``rq_approval``;
  ``{"decision": "back"}`` goes back to ``topics``.
An answer that cannot be applied opens the same kind again, saying why. Handling an answer again freezes nothing
twice, records its comment once and returns the checkpoint it opened the first time.

Budget per attempt: 1 ideator + 1 scout + 2 judge tasks (4 role tasks; question is not an open stage); a P1 degrade
spends 4 more.

Gate: one frozen RQ in the latest attempt whose already-done result is ``not_found_in_scope`` or which you sent on with
``proceed``, with at least two hypotheses."""

from __future__ import annotations

import re

from rk.checkpoints import answered_by_actor, create_checkpoint, opened_in_place_of
from rk.discovery.already_done import AlreadyDone, already_done
from rk.discovery.models import Finer, HypothesisIn, Intake, Pico, RQBody, RQOut
from rk.discovery.store import (
    add_round,
    add_rq,
    freeze_rq,
    hypotheses,
    intake_of,
    round_for_checkpoint,
    rq_row,
    rqs,
    set_already_done,
    topics,
)
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import GateResult, StageContext, StageOutcome
from rk.orchestrator.state import degradations, record_degraded, stage_row
from rk.store.project import ProjectStore
from rk.verify.novelty import novelty_claims

DECISIONS: dict[str, tuple[str, ...]] = {"rq_approval": ("approve", "revise"),
                                         "significance_novelty": ("proceed", "back")}
NOT_NARROWED = "RQ could not be narrowed past prior work"

_RQ_PARTS = (
    "Give the three-step sentence 'I am studying X because I want to find out Y in order to help my audience "
    "understand Z' as x, y and z; the problem as its condition and at least two consequences; FINER (feasible, "
    "interesting, novel, ethical, relevant; for 'novel' say what was not found within the searched scope, never that "
    "the question is novel); whether it is clinical and, if it is, its PICO (population, intervention, comparison, "
    "outcome); and at least two rival hypotheses, each with the result that would falsify it.")
RQ_TASK = ("Write one research question for the approved topic, for the intake's audience. " + _RQ_PARTS +
           " Follow the guidance when there is any.")
NARROW_TASK = (
    "Published work already answers, or may answer, this research question: `answering_works` are the works the "
    "judges said answer it, with their quotes. Rewrite the question narrower and weaker (a smaller population, "
    "setting, exposure or outcome, or a more modest claim) so that none of these works answers it. " + _RQ_PARTS)


def check_rq(body: RQBody, hyps: list[HypothesisIn]) -> list[str]:
    """What keeps the RQ from its required structure, one failure per item; ``[]`` when it has it."""
    out = [f"three_step.{f} is empty" for f in ("x", "y", "z") if not getattr(body.three_step, f).strip()]
    if not body.problem.condition.strip():
        out.append("problem.condition is empty")
    n = sum(1 for c in body.problem.consequences if c.strip())
    if n < 2:
        out.append(f"problem.consequences has {n} (need at least 2)")
    out += [f"finer.{f} is empty" for f in Finer.model_fields if not getattr(body.finer, f).strip()]
    out += novelty_claims(_rq_texts(body, hyps))
    if body.clinical:
        if body.pico is None:
            out.append("a clinical RQ needs a PICO")
        else:
            out += [f"pico.{f} is empty" for f in Pico.model_fields if not getattr(body.pico, f).strip()]
    distinct = {s for s in (_norm(h.statement) for h in hyps) if s}
    if len(distinct) < 2:
        out.append(f"{len(distinct)} distinct hypotheses (need at least 2)")
    out += [f"hypothesis {i} has no falsification condition" for i, h in enumerate(hyps, 1)
            if not h.falsification.strip()]
    return out


def _norm(statement: str) -> str:
    return " ".join(re.findall(r"\w+", statement.lower()))


def _rq_texts(body: RQBody, hyps: list[HypothesisIn]) -> list[tuple[str, str]]:
    """Every generated text of the RQ and its hypotheses, by name."""
    out = [(f"three_step.{f}", getattr(body.three_step, f)) for f in ("x", "y", "z")]
    out.append(("problem.condition", body.problem.condition))
    out += [(f"problem.consequences.{i}", c) for i, c in enumerate(body.problem.consequences, 1)]
    out += [(f"finer.{f}", getattr(body.finer, f)) for f in Finer.model_fields]
    if body.pico is not None:
        out += [(f"pico.{f}", getattr(body.pico, f)) for f in Pico.model_fields]
    for i, h in enumerate(hyps, 1):
        out += [(f"hypothesis {i}.statement", h.statement), (f"hypothesis {i}.falsification", h.falsification)]
    return out


def _approved_topic(ps: ProjectStore) -> dict | None:
    """The approved topic of the attempt that passed the topics gate (``None`` when topics has not passed)."""
    row = stage_row(ps, "topics")
    if row is None or row["passed_attempt"] is None:
        return None
    return next((t for t in topics(ps, row["passed_attempt"]) if t["status"] == "approved"), None)


def _topic_input(t: dict) -> dict:
    return {"title": t["title"], "summary": t["summary"], "feasibility": t["feasibility"],
            "data_needed": t["data_needed"], "risks": t["risks"],
            "closest": [{"item_id": c["item_id"], "title": c["title"], "year": c.get("year")} for c in t["closest"]]}


def _titles(works) -> str:
    return "; ".join(f"{w.title} ({w.item_id})" for w in works)


def _done_reason(report: AlreadyDone) -> str:
    """Why the RQ counts as already done: the works two judges said answer it, or (a split, P1) what the judges
    left open."""
    if report.result == "already_done":
        return "already done: " + _titles(report.answered_by_both())
    if report.split_reason:
        open_ = report.split_reason
    elif report.answering():
        open_ = f"answered per one judge: {_titles(report.answering())}"
    else:
        open_ = f"{len(report.judged)} judge vendor(s) gave judgements ({', '.join(report.judged) or 'none'})"
    return f"already done? the judges split ({open_}); in P1 a split counts as already done"


def _sent_on(ps: ProjectStore, rq_id: str) -> bool:
    """You answered ``proceed`` to a ``significance_novelty`` about the RQ."""
    return ps.db.execute(
        "SELECT 1 FROM checkpoints WHERE kind='significance_novelty' AND status='answered' AND "
        "json_extract(options, '$.rq_id')=? AND json_extract(answer, '$.decision')='proceed'", (rq_id,)
    ).fetchone() is not None


# ---- checkpoint prompts ----
def _already_done_lines(done: dict | None) -> list[str]:
    if not done:
        return ["Already-done check: not run."]
    report = AlreadyDone.model_validate(done)
    scope = (f"{len(report.queries)} queries on {', '.join(report.sources_used)}; the top {len(report.works)} of "
             f"{report.found} works judged by {', '.join(report.judged) or 'no judge'}")
    head = {
        "not_found_in_scope": "prior work answering this RQ was not found within the searched scope",
        "already_done": "two judges of different vendors say published work already answers this RQ",
        "split": report.split_reason or "the judges did not agree, or fewer than two judge vendors gave valid "
                                        "judgements",
    }[report.result]
    lines = [f"Already-done check: {head} ({scope})."]
    if report.skipped:
        lines.append("Judges skipped: " + "; ".join(f"{r}: {why}" for r, why in report.skipped.items()) + ".")
    for w in report.works:
        if not w.judgements:
            continue
        verdicts = "; ".join(
            f"{v}: {'answered' if j.answered else 'not answered'}, quote \"{j.quote}\""
            + ("" if j.ok_quote else " (not verbatim in the title or in the abstract part the judge saw: discarded)")
            for v, j in w.judgements.items())
        lines.append(f"- {w.title} ({w.item_id}{f', {w.year}' if w.year else ''}): {verdicts}")
    return lines


def _prompt(ps: ProjectStore, kind: str, rq_id: str, why: str = "") -> str:
    rq = rq_row(ps, rq_id)
    body = RQBody.model_validate(rq["body"])
    lines = [why, ""] if why else []
    lines += [body.three_step.sentence(), "", f"Problem: {body.problem.condition}"]
    lines += [f"- consequence: {c}" for c in body.problem.consequences]
    lines += ["", "FINER:"] + [f"- {f}: {getattr(body.finer, f)}" for f in Finer.model_fields]
    if body.clinical and body.pico is not None:
        lines += ["", "PICO:"] + [f"- {f.capitalize()}: {getattr(body.pico, f)}" for f in Pico.model_fields]
    lines += ["", "Hypotheses:"]
    lines += [f"{i}. {h['statement']} Falsified if: {h['falsification']}"
              for i, h in enumerate(hypotheses(ps, rq_id), 1)]
    lines += ["", *_already_done_lines(rq["already_done"]), ""]
    if kind == "rq_approval":
        lines.append("Approve this research question ({\"decision\": \"approve\"}; it is then frozen) or ask for a "
                     "revision ({\"decision\": \"revise\", \"comment\": ...}).")
    else:
        lines.append("Is this research question already answered? Proceed with it ({\"decision\": \"proceed\"}: it "
                     "is treated as not found within the searched scope and you then approve it) or go back to "
                     "topics ({\"decision\": \"back\"}).")
    return "\n".join(lines)


def _open(ctx: StageContext, kind: str, rq_id: str, *, replaces: str | None = None, why: str = "") -> str:
    """A ``kind`` checkpoint about the RQ. Opened in answer to ``replaces``, it is opened once: handling the same
    answer again returns the checkpoint opened the first time."""
    if replaces is not None and (existing := opened_in_place_of(ctx.ps, kind, replaces)) is not None:
        return existing
    return create_checkpoint(ctx.ps, kind, _prompt(ctx.ps, kind, rq_id, why),
                             {"rq_id": rq_id, "attempt": ctx.attempt, "choices": list(DECISIONS[kind]),
                              "replaces": replaces}, stage=ctx.stage, actor=ctx.actor)


def _freeze(ctx: StageContext, rq_id: str, *, adopted_by: str, actor: str) -> None:
    try:
        freeze_rq(ctx.ps, rq_id, adopted_by=adopted_by, actor=actor)
    except ValueError:  # frozen already (an answer handled again): an RQ freezes once
        if rq_row(ctx.ps, rq_id)["status"] != "frozen":
            raise


class QuestionWorker:
    """``judges`` replaces the configured judge drivers of the already-done check (tests)."""

    def __init__(self, judges: list[DriverSpec] | None = None):
        self.judges = judges

    def run(self, ctx: StageContext) -> StageOutcome:
        intake = intake_of(ctx.ps)
        if intake is None:
            return StageOutcome("failed", reason="intake missing")
        topic = _approved_topic(ctx.ps)
        if topic is None:
            return StageOutcome("failed", reason="topics has not passed")
        context: dict = {
            "project": {"title": ctx.ps.meta("title"), "field": ctx.ps.meta("field")},
            "intake": intake.model_dump(mode="json", include={"audience", "paper_type", "discovery_mode",
                                                              "language"}),
            "topic": _topic_input(topic),
        }
        if ctx.guidance:
            context["guidance"] = ctx.guidance
        res = ctx.executor.run("ideator", RQ_TASK, context, RQOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
        if not res.ok or res.output is None:
            return StageOutcome("rerun", reason=f"the ideator gave no research question ({res.error})")
        rq_id = add_rq(ctx.ps, ctx.attempt, topic["id"], res.output.body, res.output.hypotheses, actor=ctx.actor)
        failures = check_rq(res.output.body, res.output.hypotheses)
        if failures:
            return StageOutcome("rerun", reason="; ".join(failures))
        report = self._check(ctx, rq_id, vendor_of(res.provider, res.model))
        if report.problems:
            return StageOutcome("rerun", reason="; ".join(report.problems))
        return self._after_check(ctx, intake, rq_id, report)

    def _check(self, ctx: StageContext, rq_id: str, author_vendor: str | None) -> AlreadyDone:
        """Run the already-done check; a check that ran is stored with the RQ."""
        report = already_done(ctx, rq_id, author_vendor=author_vendor, judges=self.judges)
        if not report.problems:
            set_already_done(ctx.ps, rq_id, report.model_dump(mode="json"), actor=ctx.actor)
        return report

    def _after_check(self, ctx: StageContext, intake: Intake, rq_id: str, report: AlreadyDone) -> StageOutcome:
        if report.result == "split" and intake.production_mode == "collab":
            cid = _open(ctx, "significance_novelty", rq_id)
            return StageOutcome("waiting_human", checkpoint_id=cid, reason="the already-done judges split")
        if report.result != "not_found_in_scope":  # D1 and D2: back to topics, under question->topics's limit
            return StageOutcome("back_edge", to="topics", reason=_done_reason(report))
        return self._not_found(ctx, intake, rq_id)

    @staticmethod
    def _not_found(ctx: StageContext, intake: Intake, rq_id: str) -> StageOutcome:
        if intake.discovery_mode == "d2" and intake.production_mode == "auto":
            _freeze(ctx, rq_id, adopted_by="agent", actor=ctx.actor)
            return StageOutcome("done", reason="RQ adopted by the agent (P1): prior work answering it was not found "
                                               "within the searched scope")
        cid = _open(ctx, "rq_approval", rq_id)
        return StageOutcome("waiting_human", checkpoint_id=cid, reason="the research question awaits your approval")

    def on_answer(self, ctx: StageContext, checkpoint: dict) -> StageOutcome:
        kind = checkpoint["kind"]
        rq_id = (checkpoint.get("options") or {}).get("rq_id")
        if kind not in DECISIONS or rq_id is None or rq_row(ctx.ps, rq_id) is None:
            return StageOutcome("failed", reason=f"question cannot apply checkpoint {checkpoint['id']} ({kind})")
        answer = checkpoint.get("answer") or {}
        why = self._invalid(kind, answer)
        if why:
            cid = _open(ctx, kind, rq_id, replaces=checkpoint["id"], why=f"Your answer could not be applied: {why}.")
            return StageOutcome("waiting_human", checkpoint_id=cid, reason=why)
        by = answered_by_actor(ctx.ps, checkpoint["id"]) or ctx.actor
        decision = answer["decision"]
        if decision == "approve":
            _freeze(ctx, rq_id, adopted_by="human", actor=by)
            return StageOutcome("done", reason="research question approved")
        if decision == "revise":
            comment = answer["comment"].strip()
            if round_for_checkpoint(ctx.ps, checkpoint["id"]) is None:
                add_round(ctx.ps, "question", comment, actor=by, actor_kind="human", checkpoint_id=checkpoint["id"])
            return StageOutcome("rerun", reason=comment)
        if decision == "back":
            return StageOutcome("back_edge", to="topics", reason="significance_novelty: back to topics")
        cid = _open(ctx, "rq_approval", rq_id, replaces=checkpoint["id"])  # proceed: treated as not found
        return StageOutcome("waiting_human", checkpoint_id=cid, reason="the research question awaits your approval")

    @staticmethod
    def _invalid(kind: str, answer: dict) -> str:
        """Why the answer cannot be applied, else ``""``."""
        decision, choices = answer.get("decision"), DECISIONS[kind]
        if decision not in choices:
            return f"decision {decision!r} is not one of {', '.join(choices)}"
        if decision == "revise" and not (isinstance(answer.get("comment"), str) and answer["comment"].strip()):
            return "revise needs a comment saying what to change"
        return ""

    def gate(self, ps: ProjectStore, attempt: int) -> GateResult:
        frozen = [r for r in rqs(ps, attempt) if r["status"] == "frozen"]
        if not frozen:
            return GateResult(False, ["no frozen RQ"])
        failures = [f"{len(frozen)} frozen RQs (need exactly one)"] if len(frozen) > 1 else []
        rq = frozen[-1]
        result = (rq["already_done"] or {}).get("result")
        if result is None:
            failures.append("the already-done check did not run")
        elif result != "not_found_in_scope" and not _sent_on(ps, rq["id"]):
            failures.append(f"the already-done check found {result!r} and nobody sent the RQ on")
        n = len(hypotheses(ps, rq["id"]))
        if n < 2:
            failures.append(f"{n} hypotheses (need at least 2)")
        return GateResult(not failures, failures)

    def degrade(self, ctx: StageContext, failures: list[str]) -> StageOutcome:
        """P1 at an edge limit: the Ideator rewrites the RQ the already-done check stopped narrower and weaker, given
        the works judged as answering it; the structural and already-done checks run again. ``not_found_in_scope``:
        D2 adopts it (``adopted_by="agent"``), D1 asks ``rq_approval``; anything else fails. Narrows once per
        attempt; ``failed`` with the failures when no already-done result led here.

        A dead end in D1: answering that ``rq_approval`` with ``revise`` reruns the stage, the rerun hits the same
        ``question->question`` limit on the same attempt, and this second degrade call fails ("narrowed once
        already") instead of narrowing again; only ``approve`` carries the narrowed RQ on."""
        if any(d["attempt"] == ctx.attempt for d in degradations(ctx.ps, ctx.stage)):
            if any(r["status"] == "frozen" for r in rqs(ctx.ps, ctx.attempt)):
                return StageOutcome("done", reason="narrowed RQ frozen")
            return StageOutcome("failed", reason=f"{NOT_NARROWED} (narrowed once already)")
        rows = rqs(ctx.ps, ctx.attempt)
        last = rows[-1] if rows else None
        if last is None or not last["already_done"] or last["already_done"]["result"] == "not_found_in_scope":
            return StageOutcome("failed", reason="; ".join(failures) or "the research question did not pass its gate")
        prior = AlreadyDone.model_validate(last["already_done"])
        topic = _approved_topic(ctx.ps)
        context: dict = {
            "rq": {**last["body"], "hypotheses": [{"statement": h["statement"], "falsification": h["falsification"]}
                                                  for h in hypotheses(ctx.ps, last["id"])]},
            "answering_works": [
                {"item_id": w.item_id, "title": w.title, "year": w.year,
                 "quotes": {v: j.quote for v, j in w.valid().items() if j.answered}} for w in prior.answering()],
        }
        if topic is not None:
            context["topic"] = _topic_input(topic)
        res = ctx.executor.run("ideator", NARROW_TASK, context, RQOut, stage=ctx.stage, attempt=ctx.attempt, tools=[])
        if not res.ok or res.output is None:
            return StageOutcome("failed", reason=f"{NOT_NARROWED} (the ideator gave no narrower RQ: {res.error})")
        rq_id = add_rq(ctx.ps, ctx.attempt, last["topic_id"], res.output.body, res.output.hypotheses,
                       actor=ctx.actor)
        record_degraded(ctx.ps, stage=ctx.stage, attempt=ctx.attempt, removed=[last["id"]],
                        reason=f"the already-done check found {prior.result!r} at the edge limit; the RQ was "
                               f"narrowed to {rq_id}", actor=ctx.actor)
        problems = check_rq(res.output.body, res.output.hypotheses)
        if problems:
            return StageOutcome("failed", reason=f"{NOT_NARROWED}: " + "; ".join(problems))
        report = self._check(ctx, rq_id, vendor_of(res.provider, res.model))
        if report.problems:
            return StageOutcome("failed", reason=f"{NOT_NARROWED}: " + "; ".join(report.problems))
        if report.result != "not_found_in_scope":
            return StageOutcome("failed", reason=f"{NOT_NARROWED}: {_done_reason(report)}")
        return self._not_found(ctx, intake_of(ctx.ps), rq_id)
