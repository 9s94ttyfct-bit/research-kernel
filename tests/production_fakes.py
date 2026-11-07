"""Shared helpers for the production-stage tests (Plan 5a), over the discovery helpers."""

from __future__ import annotations

from rk.orchestrator.engine import StageContext
from rk.production.models import (
    Criterion,
    Eligibility,
    ExtractionField,
    Protocol,
    SearchStrategy,
    SynthesisPlan,
)
from tests.discovery_fakes import FakeSources, freeze_an_rq, make_project


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
