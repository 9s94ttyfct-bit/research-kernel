"""The stage graph (spec §7.1, §7.3): the discovery stages every paper type runs first, the later stages per type,
the allowed back-edges and their per-project limit (§7.2)."""

from __future__ import annotations

# Every paper type starts with these (§7.3); the orchestrator runs only them (the later stages arrive in Plan 5).
DISCOVERY: tuple[str, ...] = ("intake", "landscape", "gaps", "topics", "question")

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

# Back-edges of the discovery stages (§7.1); a rerun of a stage counts as the edge ``X->X``.
BACK_EDGES: frozenset[tuple[str, str]] = frozenset({("gaps", "landscape"), ("topics", "gaps"), ("question", "topics")})
EDGE_LIMIT = 2  # uses of each edge per project before P2 asks and P1 degrades (§7.2)

# Stages whose role tasks count against the per-session budget (§7.2).
OPEN_STAGES: frozenset[str] = frozenset({"landscape", "gaps", "topics"})


def stages_for(paper_type: str) -> list[str]:
    """The stage sequence of a paper type, discovery first (§7.3)."""
    if paper_type not in _LATER:
        raise ValueError(f"unknown paper type {paper_type!r}")
    return [*DISCOVERY, *_LATER[paper_type]]


def edge_name(frm: str, to: str) -> str:
    return f"{frm}->{to}"
