"""Production artifacts: the review protocol (search strategy, eligibility, extraction schema, synthesis plan), the
screening and extraction vocabularies, and a research proposal's method design."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, model_validator

ScreeningPhase = Literal["title_abstract", "full_text"]
TITLE_ABSTRACT: ScreeningPhase = "title_abstract"
FULL_TEXT: ScreeningPhase = "full_text"
PHASES: tuple[ScreeningPhase, ...] = (TITLE_ABSTRACT, FULL_TEXT)
PHASE_WORDS: dict[str, str] = {TITLE_ABSTRACT: "title/abstract", FULL_TEXT: "full-text"}
Include = Literal["yes", "no", "unclear"]
DecidedBy = Literal["agreement", "single", "human", "vote", "rule"]  # rule: a kernel rule (language), no screener

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
    """The protocol's synthesis plan. ``model``, ``heterogeneity`` and ``sensitivity`` describe the analysis in words;
    a meta-analysis is computed with the typed fields only: ``effect_measure`` (``RR``, ``OR``, ``RD``, ``MD`` or
    ``SMD``), ``pool_model`` (the τ² estimator: ``REML``, ``DL`` or ``FE``), ``hksj`` (Hartung-Knapp-Sidik-Jonkman
    intervals: ``"auto"``, the Cochrane Handbook rule, or always / never) and ``pi_df`` (the prediction interval's
    degrees of freedom: ``"ci"`` as the confidence interval, or Higgins 2009's ``"k-2"``). ``check_protocol``
    requires them for a meta-analysis."""
    method: Literal["narrative_swim", "meta_analysis", "evidence_map", "thematic"]
    effect_measure: str | None = None
    model: str | None = None
    heterogeneity: str | None = None
    sensitivity: str | None = None
    pool_model: Literal["REML", "DL", "FE"] | None = None
    hksj: StrictBool | Literal["auto"] = "auto"
    pi_df: Literal["ci", "k-2"] = "ci"


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


class Measurement(BaseModel):
    id: str = Field(min_length=1)
    what: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    unit: str | None = None


class DesignItem(BaseModel):
    """One experiment, computation or observation of the design, and the hypotheses its measurements test."""
    id: str = Field(min_length=1)
    kind: Literal["experiment", "computation", "observation"]
    sample: str
    controls: list[str]
    measurements: list[Measurement]
    analysis: str
    data_source: str
    hypothesis_ids: list[str]

    @model_validator(mode="after")
    def _unique_measurements(self) -> DesignItem:
        ids = [m.id for m in self.measurements]
        if dup := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate measurement ids in design item {self.id}: {dup}")
        return self


class DesignIn(BaseModel):
    """A research proposal's method design (§7.3); frozen, it pre-registers the hypotheses it tests."""
    items: list[DesignItem] = Field(min_length=1)
    expected_results: str
    alternative_explanations: list[str]

    @model_validator(mode="after")
    def _unique_items(self) -> DesignIn:
        ids = [i.id for i in self.items]
        if dup := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate design item ids: {dup}")
        return self
