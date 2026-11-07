"""Production artifacts: the review protocol (search strategy, eligibility, extraction schema, synthesis plan) and
the screening and extraction vocabularies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ScreeningPhase = Literal["title_abstract", "full_text"]
Include = Literal["yes", "no", "unclear"]
DecidedBy = Literal["agreement", "single", "human", "vote"]

RECORD_CAP = 3000  # records a search_screen attempt screens at most, unless the protocol sets another cap


class SearchStrategy(BaseModel):
    sources: list[str]
    queries: list[str]
    date_from: int | None = None  # publication years, inclusive
    date_to: int | None = None
    languages: list[str] = ["en"]


class Criterion(BaseModel):
    id: str
    text: str


class Eligibility(BaseModel):
    include: list[Criterion]
    exclude: list[Criterion]


class ExtractionField(BaseModel):
    name: str
    description: str
    type: Literal["text", "number", "category"]
    categories: list[str] | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def _categories_iff_category(self) -> ExtractionField:
        if (self.type == "category") != bool(self.categories):
            raise ValueError(f"field {self.name!r}: categories are required for type 'category' and only for it")
        return self


class SynthesisPlan(BaseModel):
    method: Literal["narrative_swim", "meta_analysis", "evidence_map", "thematic"]
    effect_measure: str | None = None
    model: str | None = None
    heterogeneity: str | None = None
    sensitivity: str | None = None


class Protocol(BaseModel):
    """The review protocol. ``prisma_p`` holds the PRISMA-P items (item -> text); ``record_cap`` the most records a
    search_screen attempt screens."""
    question: str
    search_strategy: SearchStrategy
    eligibility: Eligibility
    extraction_schema: list[ExtractionField]
    synthesis_plan: SynthesisPlan | None = None
    prisma_p: dict[str, str] | None = None
    record_cap: int = Field(RECORD_CAP, ge=1)
