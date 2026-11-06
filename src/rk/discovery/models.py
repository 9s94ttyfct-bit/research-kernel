"""Discovery artifacts: the intake, landscape clusters, gaps, topics and research questions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PaperType = Literal["systematic_review", "scoping_review", "critical_review", "research_proposal"]
GapKind = Literal["contradiction", "stated_limitation", "citation_distortion", "retracted_still_cited",
                  "evidence_map_hole"]
TopicStatus = Literal["candidate", "approved", "rejected"]


class Intake(BaseModel):
    audience: str = Field(min_length=1, max_length=500)
    paper_type: PaperType
    discovery_mode: Literal["d1", "d2"]
    production_mode: Literal["auto", "collab"]  # P1 = auto, P2 = collab
    language: str = Field("en", min_length=2, max_length=20)
    topics_n: int = Field(6, ge=1, le=20)


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
