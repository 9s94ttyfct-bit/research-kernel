"""The protocol facts only the kernel knows (P1 protocol diagnosis 2026-11-12, rules R1a, R3, R4; P1 protocol rerun 4,
2026-11-12): the PRISMA-P items of a systematic or scoping review only the project's records establish (the title,
the contributors and the admin items), the language exclusion and the search execution are written by the kernel,
never by the Ideator, so no vote asks the authors for what they cannot know."""

from __future__ import annotations

import re

import pytest

from rk.checkpoints import get_checkpoint
from rk.discovery.models import Intake
from rk.discovery.store import record_intake
from rk.production import store
from rk.production.models import SCREENED_LANGUAGES, Criterion, SearchStrategy
from rk.production.protocol import (
    KERNEL_PRISMA_P,
    KERNEL_PRISMA_P_KEYS,
    KERNEL_RULES,
    LANGUAGE_CRITERION_ID,
    ProtocolWorker,
    kernel_prisma_p,
    render_protocol,
    search_execution_lines,
    with_kernel_facts,
)
from tests.discovery_fakes import DEFAULT_INTAKE, FakeSources, ScriptedExecutor
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
QUESTION = a_protocol().question


@pytest.fixture
def sr_env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review")
    yield k, ps, lib
    k.close()


def _exclude_ids(body) -> list[str]:
    return [c.id for c in body.eligibility.exclude]


# what each source's search matches (rk.sources): the text names the fields, the request sends them
FIELDS = {"openalex": r"OpenAlex[^;\n]*titles, abstracts and[^;\n]*keywords",
          "europepmc": r"Europe PMC[^;\n]*titles and abstracts[^;\n]*TITLE_ABS",
          "pubmed": r"PubMed[^;\n]*automatic term mapping", "arxiv": r"arXiv[^;\n]*all fields",
          "s2": r"Semantic Scholar[^;\n]*relevance search"}
NAMES = {"openalex": "OpenAlex", "europepmc": "Europe PMC", "pubmed": "PubMed", "arxiv": "arXiv",
         "s2": "Semantic Scholar"}


def test_search_execution_names_the_fields_each_chosen_source_searches(sr_env):
    """Calibration (search scope, 2026-11-12): the kernel searches OpenAlex in titles, abstracts and keywords and Europe
    PMC in titles and abstracts; the protocol's search execution says where each of the strategy's sources searches a
    query (and names no source the strategy does not have), and the judges' rules sheet says it for every source."""
    from rk.sources import europepmc, openalex

    assert openalex.SEARCH == "search.title_abstract_keywords"
    assert europepmc.title_abstract("x") == "TITLE_ABS:(x)"
    _, ps, _ = sr_env
    for sources in (["openalex", "pubmed"], ["europepmc", "arxiv"], ["s2", "openalex", "europepmc"]):
        body = with_kernel_facts(a_protocol(search_strategy=SearchStrategy(
            sources=sources, queries=["aspirin AND stroke", "antiplatelet AND elderly"])), "systematic_review", "auto")
        row = {"id": "prt_none", "version": 1, "body": body.model_dump(mode="json")}
        block = search_execution_lines(row["body"])
        assert "\n".join(render_protocol(ps, row)).count("\n".join(block)) == 1
        (fields,) = [line for line in block if "fields" in line]
        for s, name in NAMES.items():
            if s in sources:
                assert re.search(FIELDS[s], fields), (s, fields)
            else:
                assert name not in fields, (s, fields)
    # a stored try may name a source the kernel does not search: shown as such, never a crash of the protocol's view
    stray = {**row["body"], "search_strategy": {**row["body"]["search_strategy"], "sources": ["openalex", "scopus",
                                                                                               "Europe PMC"]}}
    (fields,) = [line for line in search_execution_lines(stray) if "fields" in line]
    assert re.search(FIELDS["openalex"], fields) and "scopus: not a source the kernel searches" in fields
    assert "Europe PMC: not a source the kernel searches" in fields
    assert all(re.search(pattern, KERNEL_RULES) for pattern in FIELDS.values())
    assert "adds no MeSH terms or field tags" not in KERNEL_RULES  # Europe PMC's query goes inside TITLE_ABS


def test_kernel_writes_admin_prisma_p_items(sr_env):
    """The Ideator's placeholder for a fact it cannot know is replaced by the kernel's text in every stored version;
    its own PRISMA-P items are kept. A review type whose freeze is no preregistration gets no kernel PRISMA-P keys, and
    an Ideator-written one is dropped."""
    review = kernel_prisma_p("systematic_review", QUESTION, "auto")
    assert {k: review[k] for k in KERNEL_PRISMA_P} == KERNEL_PRISMA_P
    assert set(kernel_prisma_p("scoping_review", QUESTION, "collab")) == set(review) == set(KERNEL_PRISMA_P_KEYS)
    assert set(KERNEL_PRISMA_P) == {"registration", "amendments", "support", "competing_interests"}
    assert kernel_prisma_p("critical_review", QUESTION, "auto") == {} == kernel_prisma_p("research_proposal",
                                                                                         QUESTION, "collab")

    body = with_kernel_facts(a_protocol(prisma_p={**SR_ITEMS, "registration": PLACEHOLDER}), "systematic_review",
                             "auto")
    assert body.prisma_p == {**SR_ITEMS, **review}
    light = with_kernel_facts(a_protocol(prisma_p={"registration": PLACEHOLDER, "support": "none"}),
                              "critical_review", "auto")
    assert light.prisma_p is None
    kept = with_kernel_facts(a_protocol(prisma_p={"rationale": "R.", "competing_interests": "x"}), "critical_review",
                             "auto")
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
    assert v1["body"]["prisma_p"] == {**SR_ITEMS, **kernel_prisma_p("systematic_review", v1["body"]["question"],
                                                                     "auto")}
    assert [c["id"] for c in v1["body"]["eligibility"]["exclude"]] == ["E1", LANGUAGE_CRITERION_ID]
    judged = next(c for c in ex.calls if c["schema"] == "ProtocolVoteOut")
    assert KERNEL_PRISMA_P["registration"] in judged["inputs"]["subject"]["protocol"]  # the rendered protocol


def test_kernel_writes_the_title_and_contributors_of_a_review(sr_env):
    """Calibration (P1 protocol rerun 4, 2026-11-12): both judges blocked ozone-bleaching for three votes on a protocol
    with no title identifying it as a systematic review and no contributor list, which the Ideator cannot know. The
    kernel writes both for a systematic or scoping review from the project's records: the title from the frozen
    question as the body stores it, the contributors from who drafts the protocol and who freezes it (the judges in P1,
    the researcher in P2), with no invented author or contact. They replace the Ideator's, and the rendered protocol
    opens with the title."""
    own = {**SR_ITEMS, "title": "Aspirin after 80: a review", "contributors": "A. Author (a@example.org)"}
    p1 = with_kernel_facts(a_protocol(prisma_p=own), "systematic_review", "auto")
    title = p1.prisma_p["title"]
    assert QUESTION in title and "systematic review" in title and "Aspirin after 80" not in title
    assert {k: p1.prisma_p[k] for k in SR_ITEMS} == SR_ITEMS
    scoping = with_kernel_facts(a_protocol(), "scoping_review", "auto").prisma_p["title"]
    assert QUESTION in scoping and "scoping review" in scoping and "systematic" not in scoping
    asked = with_kernel_facts(a_protocol(question="Does ozone bleach teeth?"), "systematic_review", "auto")
    assert asked.prisma_p["title"].endswith(": Does ozone bleach teeth?")

    p2 = with_kernel_facts(a_protocol(prisma_p=own), "systematic_review", "collab")
    auto, collab = p1.prisma_p["contributors"], p2.prisma_p["contributors"]
    assert auto != collab and "A. Author" not in auto + collab and "@" not in auto + collab
    assert "judge" in auto and "at least two" in auto and "judge" not in collab and "researcher" in collab
    assert all("Ideator" in c and "Scout" in c and "contact" in c for c in (auto, collab))
    assert p2.prisma_p["title"] == title  # only the contributors depend on the production mode

    assert with_kernel_facts(p1, "systematic_review", "auto") == p1  # applied twice changes nothing
    light = with_kernel_facts(a_protocol(prisma_p={"rationale": "R.", "title": "T", "contributors": "C"}),
                              "critical_review", "auto")
    assert light.prisma_p == {"rationale": "R."}

    _, ps, _ = sr_env
    lines = render_protocol(ps, {"id": "prt_none", "version": 1, "body": p1.model_dump(mode="json")})
    assert lines[0] == title and lines[2].startswith("Protocol version 1")
    light_lines = render_protocol(ps, {"id": "prt_none", "version": 1, "body": light.model_dump(mode="json")})
    assert light_lines[0].startswith("Protocol version 1")
    (rule,) = [line for line in KERNEL_RULES.splitlines() if line.startswith("1. ")]
    assert all(k in rule for k in KERNEL_PRISMA_P_KEYS)  # the judges' rules sheet names them as the kernel's


SR_DRAFT = {"eligibility": {"include": [{"id": "I1", "text": "People over 80"}], "exclude": []},
            "extraction_schema": [{"name": "study_id", "description": "First author and year", "type": "text",
                                   "source": "record"}],
            "synthesis_plan": {"method": "narrative_swim", "outcomes": [{"name": "stroke"}]},
            "prisma_p": SR_ITEMS}
SR_STRATEGY = {"queries": ["aspirin stroke elderly", "aspirin primary prevention octogenarians",
                           "antiplatelet stroke over 80"], "sources": ["openalex", "europepmc"],
               "date_from": 2000, "date_to": None}


def test_p2_draft_gets_the_contributors_of_your_approval(tmp_home, replay_http):
    """In P2 the stored draft says you freeze it, not the judges."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review",
                                        production_mode="collab")
    try:
        ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [SR_DRAFT],
                               ("scout", "SearchStrategyOut"): [SR_STRATEGY]})
        ctx = production_ctx(k, ps, lib, ex, stage="protocol", attempt=1, sources=FakeSources({}))
        assert ProtocolWorker().run(ctx).status == "waiting_human"
        (v1,) = store.protocols(ps, 1)
        assert v1["body"]["prisma_p"]["contributors"] == kernel_prisma_p(
            "systematic_review", v1["body"]["question"], "collab")["contributors"]
    finally:
        k.close()


def test_a_version_drafted_in_p1_goes_to_your_approval_with_the_p2_contributors(tmp_home, replay_http):
    """A version drafted under P1 that reaches your approval after you switched the project to P2 (the attempt resumed
    in place) is opened as a copy whose kernel items are P2's, added once; the draft itself is unchanged."""
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, paper_type="systematic_review")
    try:
        revise = {"choice": "revise", "reason": "not yet"}
        ex = ScriptedExecutor({("ideator", "ProtocolDraftOut"): [SR_DRAFT],
                               ("scout", "SearchStrategyOut"): [SR_STRATEGY],
                               ("judge", "ProtocolVoteOut"): [revise], ("judge_2", "ProtocolVoteOut"): [revise]})
        ctx = production_ctx(k, ps, lib, ex, stage="protocol", attempt=1, sources=FakeSources({}))
        assert ProtocolWorker().run(ctx).status == "rerun"
        record_intake(ps, Intake(**{**DEFAULT_INTAKE, "paper_type": "systematic_review",
                                    "production_mode": "collab"}), actor="tester", actor_kind="human")
        first = ProtocolWorker().run(ctx)
        assert first.status == "waiting_human"
        drafted, opened = store.protocols(ps, 1)
        question = drafted["body"]["question"]
        assert drafted["body"]["prisma_p"] == {**SR_ITEMS, **kernel_prisma_p("systematic_review", question, "auto")}
        assert opened["body"]["prisma_p"] == {**SR_ITEMS, **kernel_prisma_p("systematic_review", question, "collab")}
        assert {**opened["body"], "prisma_p": None} == {**drafted["body"], "prisma_p": None}
        assert opened["version"] == drafted["version"] + 1
        assert get_checkpoint(ps, first.checkpoint_id)["options"]["protocol_id"] == opened["id"]
        again = ProtocolWorker().run(ctx)  # resumed again: nothing added, the same approval
        assert again.checkpoint_id == first.checkpoint_id and len(store.protocols(ps, 1)) == 2
    finally:
        k.close()


def test_language_exclusion_is_appended_once():
    """The ``E-lang`` exclusion names the screened languages and is the kernel's: applied twice it is there once, and
    an Ideator-written criterion of that id is replaced by the kernel's text."""
    once = with_kernel_facts(a_protocol(), "critical_review", "auto")
    twice = with_kernel_facts(once, "critical_review", "auto")
    assert _exclude_ids(once) == _exclude_ids(twice) == ["E1", LANGUAGE_CRITERION_ID]
    assert twice == once
    (lang,) = [c for c in twice.eligibility.exclude if c.id == LANGUAGE_CRITERION_ID]
    assert all(code in lang.text for code in SCREENED_LANGUAGES)

    forged = a_protocol()
    forged.eligibility.exclude.append(Criterion(id=LANGUAGE_CRITERION_ID, text="Any language is admitted"))
    fixed = with_kernel_facts(forged, "critical_review", "auto")
    assert [c for c in fixed.eligibility.exclude if c.id == LANGUAGE_CRITERION_ID] == [lang]
    assert fixed.eligibility.include == a_protocol().eligibility.include


def test_language_exclusion_names_where_its_count_is_reported():
    """The ``E-lang`` rationale (srrepro11 tfa-heated-oils: a judge blocked the vote on an unjustified English
    restriction) points to the PRISMA flow only for a type that reports one, and its calibration claim stays English
    whatever languages the strategy lists."""
    def lang(paper_type, languages=None):
        body = a_protocol()
        if languages:
            body.search_strategy.languages = languages
        (c,) = [c for c in with_kernel_facts(body, paper_type, "auto").eligibility.exclude
                if c.id == LANGUAGE_CRITERION_ID]
        return c.text

    for paper_type in ("systematic_review", "scoping_review"):
        assert "PRISMA flow" in lang(paper_type)
    for paper_type in ("critical_review", "research_proposal"):
        assert "PRISMA" not in lang(paper_type)
    assert "calibrated on English-language reports only" in lang("systematic_review", ["en", "de"])


def test_rendered_protocol_states_search_execution(sr_env):
    """The protocol's review shows how the kernel runs the search, right after the search strategy: the publication
    years written out (an open start is the earliest indexed record), each query sent as written to each source,
    deduplication, and the record cap counted after deduplication."""
    _, ps, _ = sr_env
    body = with_kernel_facts(a_protocol(search_strategy=SearchStrategy(
        sources=["openalex", "pubmed"], queries=["aspirin stroke elderly", "antiplatelet over 80"], date_from=None,
        date_to=2020)), "systematic_review", "auto")
    row = {"id": "prt_none", "version": 1, "body": body.model_dump(mode="json")}
    lines = render_protocol(ps, row)
    block = search_execution_lines(row["body"])
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert lines[start - 2:start] == ["  - antiplatelet over 80", ""]  # the block follows the strategy's queries
    assert lines.index(f"Record cap: {body.record_cap} records.") > start
    text = "\n".join(block)
    assert "earliest indexed record to 2020" in text
    assert "as written" in text and "4 searches" in text  # 2 queries x 2 sources
    assert "duplicate" in text and "normalised title" in text
    assert f"{body.record_cap}" in text and "after deduplication" in text
    open_end = search_execution_lines({**row["body"], "search_strategy": {**row["body"]["search_strategy"],
                                                                         "date_from": 2001, "date_to": None}})
    assert "2001 to the date of the search" in "\n".join(open_end)
    # the PRISMA-P items the kernel wrote are shown to you (P2) too
    assert f"- registration: {KERNEL_PRISMA_P['registration']}" in lines
