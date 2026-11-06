"""Discovery artifacts: the intake, landscape clusters, gaps, topics and research questions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from rk.orchestrator.graph import STAGE_TASK_BUDGET

PaperType = Literal["systematic_review", "scoping_review", "critical_review", "research_proposal"]
GapKind = Literal["contradiction", "stated_limitation", "citation_distortion", "retracted_still_cited",
                  "evidence_map_hole"]
TopicStatus = Literal["candidate", "approved", "rejected"]

TASKS_PER_TOPIC = 3  # V-NOVELTY of a topic: 1 scout + 2 judges
MAX_TOPICS_N = 12  # a topics attempt: 1 ideator + 12 x 3 = 37 role tasks, within the stage budget
assert 1 + TASKS_PER_TOPIC * MAX_TOPICS_N < STAGE_TASK_BUDGET


class Intake(BaseModel):
    audience: str = Field(min_length=1, max_length=500)
    paper_type: PaperType
    discovery_mode: Literal["d1", "d2"]
    production_mode: Literal["auto", "collab"]  # P1 = auto, P2 = collab
    language: str = Field("en", min_length=2, max_length=20)
    topics_n: int = Field(6, description=f"candidate topics, 1 to {MAX_TOPICS_N}",
                          json_schema_extra={"minimum": 1, "maximum": MAX_TOPICS_N})

    @field_validator("topics_n")
    @classmethod
    def _topics_n_fits_the_budget(cls, v: int) -> int:
        if not 1 <= v <= MAX_TOPICS_N:
            raise ValueError(f"topics_n must be 1 to {MAX_TOPICS_N}: each topic costs {TASKS_PER_TOPIC} role tasks "
                             f"and a topics attempt has {STAGE_TASK_BUDGET}")
        return v


class Cluster(BaseModel):
    label: str
    description: str
    query: str
    item_ids: list[str]
    representative_ids: list[str] = []
    year_counts: dict[int, int] = {}


# ---- role outputs ----
class LandscapeQueries(BaseModel):
    """The Scout's search queries (landscape: 3-8, checked by the worker)."""
    queries: list[str]


class ClusterOut(BaseModel):
    label: str
    description: str
    query: str
    item_ids: list[str]
    representative_ids: list[str] = []


class ClusterPlan(BaseModel):
    """The Scout's clusters over the landscape search hits."""
    clusters: list[ClusterOut]


class LimitationOut(BaseModel):
    source_id: str
    quote: str  # verbatim from the source's text layer
    later_query: str  # English search query for later work that may have addressed the limitation


class LimitationsOut(BaseModel):
    """The GapMiner's stated limitations (gaps: stated_limitation)."""
    items: list[LimitationOut]


class LaterWorkOut(BaseModel):
    """Triage: which of the given later works address the limitation, each with a verbatim quote from its title or
    abstract."""
    addressed: list[str] = []
    basis: dict[str, str] = {}


class Dim(BaseModel):
    name: str
    values: list[str]


class MapPlan(BaseModel):
    """The GapMiner's evidence map: exactly two dimensions, each given work's value on both, and a search query per
    empty cell keyed ``"<value of dim 1>|<value of dim 2>"`` (gaps: evidence_map_hole)."""
    dims: list[Dim]
    cells: dict[str, list[str]] = {}
    empty_cell_queries: dict[str, str] = {}


class CellMatchOut(BaseModel):
    """Triage: which of the given works fall into the empty cell."""
    matches: list[str] = []


class QuotedSource(BaseModel):
    source_id: str
    quote: str  # verbatim from the source's text layer


class ContradictionOut(BaseModel):
    cluster_id: str
    a: QuotedSource
    b: QuotedSource  # a finding that contradicts a's, from another work of the same cluster


class ContradictionsOut(BaseModel):
    """The GapMiner's contradicting pairs (gaps: contradiction)."""
    pairs: list[ContradictionOut]


class CitedWork(BaseModel):
    """The cited work: its item id when it is one of the given works, else its DOI."""
    item_id: str | None = None
    doi: str | None = None


class DistortionOut(BaseModel):
    citing_source_id: str
    quote: str  # verbatim from the citing source's text layer: what it says the cited work found
    cited: CitedWork


class DistortionsOut(BaseModel):
    """The GapMiner's statements about cited works (gaps: citation_distortion). The kernel picks the cited work's
    passages itself (Plan 4 deviation 4), so the output carries no text from it."""
    items: list[DistortionOut]


class TopicOut(BaseModel):
    title: str
    summary: str
    gap_ids: list[str]  # ids of the given verified gaps the topic rests on
    feasibility: str
    data_needed: str
    paper_type: PaperType
    risks: str


class TopicsOut(BaseModel):
    """The Ideator's candidate topics (topics stage); the kernel drops gap ids that are not verified gaps of the latest
    gaps attempt, and topics left without one."""
    topics: list[TopicOut]


class GapEvidence(BaseModel):
    note_ids: list[str] = []
    item_ids: list[str] = []
    claim_ids: list[str] = []


class Gap(BaseModel):
    kind: GapKind
    statement: str
    cluster_id: str | None = None
    evidence: GapEvidence
    details: dict = {}


class ClosestWork(BaseModel):
    item_id: str
    title: str
    year: int | None = None
    differences: dict[str, str] = {}  # judge vendor -> how the topic differs from this work


class Topic(BaseModel):
    title: str
    summary: str
    gap_ids: list[str]
    closest: list[ClosestWork] = []
    feasibility: str
    data_needed: str
    paper_type: PaperType
    risks: str


class ThreeStep(BaseModel):
    """'I am studying X, because I want to find out Y, in order to help my reader understand Z.'"""
    x: str
    y: str
    z: str


class Problem(BaseModel):
    condition: str
    consequences: list[str]


class Finer(BaseModel):
    feasible: str
    interesting: str
    novel: str
    ethical: str
    relevant: str


class Pico(BaseModel):
    population: str
    intervention: str
    comparison: str
    outcome: str


class RQBody(BaseModel):
    three_step: ThreeStep
    problem: Problem
    finer: Finer
    clinical: bool
    pico: Pico | None = None


class HypothesisIn(BaseModel):
    statement: str
    falsification: str
