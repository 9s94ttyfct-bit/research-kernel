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
