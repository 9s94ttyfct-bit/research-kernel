"""The protocol facts only the kernel knows (P1 protocol diagnosis 2026-11-12, rules R1a, R3, R4): the admin PRISMA-P
items of a systematic or scoping review, the language exclusion and the search execution are written by the kernel,
never by the Ideator, so no vote asks the authors for what they cannot know."""

from __future__ import annotations

import pytest

from rk.production import store
from rk.production.models import SCREENED_LANGUAGES, Criterion, SearchStrategy
from rk.production.protocol import (
    KERNEL_PRISMA_P,
    LANGUAGE_CRITERION_ID,
    ProtocolWorker,
    kernel_prisma_p,
    render_protocol,
    search_execution_lines,
    with_kernel_facts,
)
from tests.discovery_fakes import FakeSources, ScriptedExecutor
from tests.production_fakes import a_protocol, make_frozen_project, production_ctx

SR_ITEMS = {"rationale": "Guidelines stop at 80.", "objectives": "Estimate benefit and harm.",
            "selection_process": "Two screeners, title/abstract then full text.",
            "data_collection_process": "One reader extracts, a second checks.",
            "risk_of_bias": "RoB 2 for trials, ROBINS-I otherwise.",
            "meta_bias": "Funnel plots when ten or more studies report an outcome.",
            "certainty_of_evidence": "GRADE for each outcome.",
            "data_management": "Records and extracted values are kept with their quotes.",
            "outcomes_prioritisation": "Stroke first, then major bleeding."}
PLACEHOLDER = "to be completed by the researcher"


@pytest.fixture
def sr_env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review")
    yield k, ps, lib
    k.close()


def _exclude_ids(body) -> list[str]:
    return [c.id for c in body.eligibility.exclude]


def test_kernel_writes_admin_prisma_p_items(sr_env):
    """The Ideator's placeholder for a fact it cannot know is replaced by the kernel's text in every stored version;
    its own PRISMA-P items are kept. A review type whose freeze is no preregistration gets no kernel PRISMA-P keys, and
    an Ideator-written one is dropped."""
    assert kernel_prisma_p("systematic_review") == KERNEL_PRISMA_P == kernel_prisma_p("scoping_review")
    assert set(KERNEL_PRISMA_P) == {"registration", "amendments", "support", "competing_interests"}
    assert kernel_prisma_p("critical_review") == {} == kernel_prisma_p("research_proposal")

    body = with_kernel_facts(a_protocol(prisma_p={**SR_ITEMS, "registration": PLACEHOLDER}), "systematic_review")
    assert body.prisma_p == {**SR_ITEMS, **KERNEL_PRISMA_P}
    light = with_kernel_facts(a_protocol(prisma_p={"registration": PLACEHOLDER, "support": "none"}),
                              "critical_review")
    assert light.prisma_p is None
    kept = with_kernel_facts(a_protocol(prisma_p={"rationale": "R.", "competing_interests": "x"}), "critical_review")
    assert kept.prisma_p == {"rationale": "R."}

    # the worker stores the kernel facts with the draft: the version the vote sees carries them
    k, ps, lib = sr_env
    draft = {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}],
                             "exclude": [{"id": "E1", "text": "Animal studies"}]},
             "extraction_schema": [{"name": "study_id", "description": "First author and year", "type": "text",
                                    "source": "record"},
                                   {"name": "n", "description": "Participants", "type": "number"}],
             "synthesis_plan": {"method": "narrative_swim", "outcomes": [{"name": "stroke"}]},
             "prisma_p": {**SR_ITEMS, "registration": PLACEHOLDER}}
    strategy = {"queries": ["aspirin stroke elderly", "aspirin primary prevention octogenarians",
                            "antiplatelet stroke over 80"], "sources": ["openalex", "europepmc"],
                "date_from": 2000, "date_to": None}
    ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [draft], ("scout", "SearchStrategyOut"): [strategy],
                           ("judge", "ProtocolVoteOut"): [{"choice": "approve", "reason": "ok"}],
                           ("judge_2", "ProtocolVoteOut"): [{"choice": "approve", "reason": "ok"}]})
    ctx = production_ctx(k, ps, lib, ex, stage="protocol", attempt=1, sources=FakeSources({}))
    assert ProtocolWorker().run(ctx).status == "done"
    (v1,) = store.protocols(ps, 1)
    assert v1["body"]["prisma_p"] == {**SR_ITEMS, **KERNEL_PRISMA_P}
    assert [c["id"] for c in v1["body"]["eligibility"]["exclude"]] == ["E1", LANGUAGE_CRITERION_ID]
    judged = next(c for c in ex.calls if c["schema"] == "ProtocolVoteOut")
    assert KERNEL_PRISMA_P["registration"] in judged["inputs"]["subject"]["protocol"]  # the rendered protocol


def test_language_exclusion_is_appended_once():
    """The ``E-lang`` exclusion names the screened languages and is the kernel's: applied twice it is there once, and
    an Ideator-written criterion of that id is replaced by the kernel's text."""
    once = with_kernel_facts(a_protocol(), "critical_review")
    twice = with_kernel_facts(once, "critical_review")
    assert _exclude_ids(once) == _exclude_ids(twice) == ["E1", LANGUAGE_CRITERION_ID]
    assert twice == once
    (lang,) = [c for c in twice.eligibility.exclude if c.id == LANGUAGE_CRITERION_ID]
    assert all(code in lang.text for code in SCREENED_LANGUAGES)

    forged = a_protocol()
    forged.eligibility.exclude.append(Criterion(id=LANGUAGE_CRITERION_ID, text="Any language is admitted"))
    fixed = with_kernel_facts(forged, "systematic_review")
    assert [c for c in fixed.eligibility.exclude if c.id == LANGUAGE_CRITERION_ID] == [lang]
    assert fixed.eligibility.include == a_protocol().eligibility.include


def test_rendered_protocol_states_search_execution(sr_env):
    """The protocol's review shows how the kernel runs the search, right after the search strategy: the publication
    years written out (an open start is the earliest indexed record), each query sent as written to each source,
    deduplication, and the record cap counted after deduplication."""
    _, ps, _ = sr_env
    body = with_kernel_facts(a_protocol(search_strategy=SearchStrategy(
        sources=["openalex", "pubmed"], queries=["aspirin stroke elderly", "antiplatelet over 80"], date_from=None,
        date_to=2020)), "systematic_review")
    row = {"id": "prt_none", "version": 1, "body": body.model_dump(mode="json")}
    lines = render_protocol(ps, row)
    block = search_execution_lines(row["body"])
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert lines[start - 2:start] == ["  - antiplatelet over 80", ""]  # the block follows the strategy's queries
    assert lines.index(f"Record cap: {body.record_cap} records.") > start
    text = "\n".join(block)
    assert "earliest indexed record to 2020" in text
    assert "verbatim" in text and "4 searches" in text  # 2 queries x 2 sources
    assert "duplicate" in text and "normalised title" in text
    assert f"{body.record_cap}" in text and "after deduplication" in text
    open_end = search_execution_lines({**row["body"], "search_strategy": {**row["body"]["search_strategy"],
                                                                         "date_from": 2001, "date_to": None}})
    assert "2001 to the date of the search" in "\n".join(open_end)
    # the PRISMA-P items the kernel wrote are shown to you (P2) too
    assert f"- registration: {KERNEL_PRISMA_P['registration']}" in lines
