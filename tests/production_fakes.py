"""Shared helpers for the production-stage tests (Plan 5a), over the discovery helpers."""

from __future__ import annotations

from rk.discovery.executor import RateCapped
from rk.orchestrator.engine import StageContext
from rk.production.models import (
    Criterion,
    Eligibility,
    ExtractionField,
    Protocol,
    SearchStrategy,
    SynthesisPlan,
)
from tests.discovery_fakes import (
    FakeSources,
    ScriptedExecutor,
    freeze_an_rq,
    make_project,
)

TA, FT = "title_abstract", "full_text"


def make_frozen_project(tmp_home, replay_http, *, slug: str = "demo", **intake_overrides):
    """A kernel with one project whose intake is recorded and whose RQ is frozen (``freeze_an_rq``). Returns
    ``(kernel, ps, lib, rq_id)``; the caller closes the kernel."""
    k, ps, lib = make_project(tmp_home, replay_http, slug=slug, **intake_overrides)
    return k, ps, lib, freeze_an_rq(ps)


def production_ctx(k, ps, lib, executor, *, stage: str = "search_screen", attempt: int = 1, sources=None,
                   project: str = "demo") -> StageContext:
    """A stage context of a production stage over ``executor`` (e.g. a ``ScriptedExecutor``)."""
    return StageContext(kernel=k, registry=None, ps=ps, lib=lib, project=project, stage=stage, attempt=attempt,
                        run_id=None, guidance="", executor=executor, sources=sources or FakeSources({}),
                        actor="tester")


def a_protocol(**overrides) -> Protocol:
    """A complete protocol for the frozen RQ of ``freeze_an_rq``; ``overrides`` replace top-level fields."""
    body = Protocol(
        question="When should aspirin be started for stroke prevention in people over 80?",
        search_strategy=SearchStrategy(sources=["openalex", "europepmc"], queries=["aspirin stroke elderly"],
                                       date_from=2000, date_to=None),
        eligibility=Eligibility(include=[Criterion(id="I1", text="People over 80")],
                                exclude=[Criterion(id="E1", text="Animal studies")]),
        extraction_schema=[ExtractionField(name="n", description="Participants", type="number", unit="people"),
                           ExtractionField(name="design", description="Study design", type="category",
                                           categories=["rct", "cohort", "other"])],
        synthesis_plan=SynthesisPlan(method="narrative_swim"),
        prisma_p={"rationale": "Guidelines stop at 80."})
    return body.model_copy(update=overrides)


def call_phase(call: dict) -> str | None:
    """The screening phase of a recorded screener or judge call (a judge sees it in its subject)."""
    inputs = call["inputs"]
    return inputs.get("phase") or (inputs.get("subject") or {}).get("phase")


class Screeners(ScriptedExecutor):
    """Screeners decide by the record's title. At title/abstract screening the title ends ``[a/b]``: screener_a's and
    screener_b's decision (yes, no, unclear; ``-`` leaves the record out of the output), unless ``overrides[title]``
    gives another ``"a/b"``. At full-text screening ``full_text[title]`` gives the ``"a/b"``, else both say yes.
    ``reasons[(title, phase)]`` replaces the reason both screeners give, ``criteria[(title, phase)]`` the criterion
    they name (default ``I1`` for yes, else ``E1``). ``extra[role]`` is added to that role's first output (e.g. a
    decision on a record not in the batch). Judges vote by title: ``votes[(title, phase)]``, else ``votes[title]``, is
    ``(judge, judge_2)``."""

    def __init__(self, *, votes: dict | None = None, extra: dict[str, list[dict]] | None = None,
                 overrides: dict[str, str] | None = None, full_text: dict[str, str] | None = None,
                 reasons: dict[tuple[str, str], str] | None = None,
                 criteria: dict[tuple[str, str], str | None] | None = None, **kw):
        super().__init__({}, **kw)
        self.votes, self.extra, self.overrides = votes or {}, extra or {}, overrides or {}
        self.full_text, self.reasons, self.criteria = full_text or {}, reasons or {}, criteria or {}

    def says(self, title: str, role: str, phase: str = TA) -> str:
        if phase == FT:
            pair = self.full_text.get(title, "yes/yes")
        else:
            pair = self.overrides.get(title) or title[title.rindex("[") + 1:-1]
        a, b = pair.split("/")
        return a if role == "screener_a" else b

    def run(self, role, instructions, inputs, output_schema, **kw):
        if output_schema.__name__ == "ScreenOut":
            phase = inputs["phase"]
            out = []
            for r in inputs["records"]:
                inc = self.says(r["title"], role, phase)
                if inc != "-":
                    reason = self.reasons.get((r["title"], phase), f"{role} read it")
                    criterion = self.criteria.get((r["title"], phase), "I1" if inc == "yes" else "E1")
                    out.append({"record_id": r["record_id"], "include": inc, "criterion_id": criterion,
                                "reason": reason})
            self.script[(role, "ScreenOut")] = [{"decisions": out + self.extra.pop(role, [])}]
        elif output_schema.__name__ == "VoteOut":
            subject = inputs["subject"]
            title = subject["record"]["title"]
            choice = (self.votes.get((title, subject["phase"])) or self.votes[title])[0 if role == "judge" else 1]
            self.script[(role, "VoteOut")] = [{"choice": choice, "reason": f"{role} says {choice}"}]
        return super().run(role, instructions, inputs, output_schema, **kw)

    def of(self, role: str, phase: str | None = TA) -> list[dict]:
        """``role``'s calls in ``phase`` (``None``: every phase)."""
        return [c for c in self.calls if c["role"] == role and (phase is None or call_phase(c) == phase)]


class CappedAt(Screeners):
    """``Screeners`` whose ``role`` hits its hourly call cap (``RateCapped``) at its ``n``-th call."""

    def __init__(self, role: str, n: int, **kw):
        super().__init__(**kw)
        self.cap_role, self.cap_n = role, n

    def run(self, role, instructions, inputs, output_schema, **kw):
        if role == self.cap_role and len(self.of(role, phase=None)) + 1 == self.cap_n:
            raise RateCapped(role, 60.0)
        return super().run(role, instructions, inputs, output_schema, **kw)
