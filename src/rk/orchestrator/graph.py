"""The stage graph (spec §7.1, §7.3): the discovery stages every paper type runs first, the production stages after
them per type, the allowed back-edges and their per-project limit (§7.2).

A stage runs only when it has a worker (``Orchestrator.runnable``); the others are not available yet."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rk.store.project import ProjectStore

# Every paper type starts with these (§7.3); the Lead may drive them in guided discovery (D1).
DISCOVERY: tuple[str, ...] = ("intake", "landscape", "gaps", "topics", "question")
# Every stage after question, in order; the orchestrator runs them in a run (`rk run`), in D1 and D2 projects (§8.1).
PRODUCTION: tuple[str, ...] = ("protocol", "search_screen", "read_extract", "synthesize", "argue", "design", "draft",
                               "revise", "audit", "export")

_LATER: dict[str, tuple[str, ...]] = {
    "systematic_review": ("protocol", "search_screen", "read_extract", "synthesize", "argue", "draft", "revise",
                          "audit", "export"),
    "scoping_review": ("protocol", "search_screen", "read_extract", "synthesize", "argue", "draft", "revise", "audit",
                       "export"),
    "critical_review": ("protocol", "search_screen", "read_extract", "synthesize", "argue", "draft", "revise",
                        "audit", "export"),
    "research_proposal": ("protocol", "search_screen", "read_extract", "synthesize", "argue", "design", "draft",
                          "revise", "audit", "export"),
}

# Back-edges (§7.1); a rerun of a stage counts as the edge ``X->X``.
BACK_EDGES: frozenset[tuple[str, str]] = frozenset({
    ("gaps", "landscape"), ("topics", "gaps"), ("question", "topics"),
    ("protocol", "question"), ("search_screen", "protocol"), ("read_extract", "search_screen"),
    ("synthesize", "read_extract"), ("argue", "synthesize"), ("argue", "question"), ("design", "argue"),
    ("design", "question"), ("draft", "argue"), ("draft", "design"),  # draft->design: research proposals only
    ("revise", "draft"),
})
EDGE_LIMIT = 2  # uses of each edge per project before P2 asks and P1 degrades (§7.2)

# Stages whose role tasks count against the per-session budget (§7.2), and that budget per stage: the open stages,
# revise among them (§7.2: a SimReader and Writer tasks per round, the rounds bounded by ``revise_max_rounds``). The
# other stages have none; those that work through an enumerated set (search_screen: every record, read_extract: every
# included study) grow with the set, and synthesize and design are bounded by the studies and the design's size; draft
# by its sections, each with at most 1 + ``draft_section_retries`` Writer tasks per attempt. The hourly call caps of
# the drivers still apply.
OPEN_STAGES: frozenset[str] = frozenset({"landscape", "gaps", "topics", "protocol", "argue", "revise"})
STAGE_TASK_BUDGET = 40  # role tasks per open stage per run session


def stages_for(paper_type: str) -> list[str]:
    """The stage sequence of a paper type, discovery first (§7.3)."""
    if paper_type not in _LATER:
        raise ValueError(f"unknown paper type {paper_type!r}")
    return [*DISCOVERY, *_LATER[paper_type]]


def stages_of(ps: ProjectStore) -> list[str]:
    """The project's stage sequence: that of its intake's paper type; the discovery stages before an intake."""
    from rk.discovery.store import intake_of  # rk.discovery imports this module

    intake = intake_of(ps)
    return stages_for(intake.paper_type) if intake is not None else list(DISCOVERY)


def edge_name(frm: str, to: str) -> str:
    return f"{frm}->{to}"
