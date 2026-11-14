"""Production artifacts: the review protocol (search strategy, eligibility, extraction schema, synthesis plan), the
screening and extraction vocabularies, and a research proposal's method design."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

ScreeningPhase = Literal["title_abstract", "full_text"]
TITLE_ABSTRACT: ScreeningPhase = "title_abstract"
FULL_TEXT: ScreeningPhase = "full_text"
PHASES: tuple[ScreeningPhase, ...] = (TITLE_ABSTRACT, FULL_TEXT)
PHASE_WORDS: dict[str, str] = {TITLE_ABSTRACT: "title/abstract", FULL_TEXT: "full-text"}
Include = Literal["yes", "no", "unclear"]
DecidedBy = Literal["agreement", "single", "human", "vote", "rule"]  # rule: a kernel rule (language), no screener

RECORD_CAP = 3000  # records a search_screen attempt screens at most, unless the protocol sets another cap
PRISMA_TYPES = frozenset({"systematic_review", "scoping_review"})  # PRISMA counts; an incomplete search blocks


# the languages a new protocol's records are screened in: search_screen excludes a record in any other language by
# rule, before screening (``rk.production.screening``), so the Ideator writes the eligibility criteria to match
SCREENED_LANGUAGES = ("en",)


class SearchStrategy(BaseModel):
    sources: list[str]
    queries: list[str]
    date_from: int | None = None  # publication years, inclusive
    date_to: int | None = None
    languages: list[str] = list(SCREENED_LANGUAGES)


class Criterion(BaseModel):
    id: str
    text: str


class Eligibility(BaseModel):
    include: list[Criterion]
    exclude: list[Criterion]


class ExtractionField(BaseModel):
    """A field the reader extracts from each study. ``source`` is where its value is quoted from: the study's text
    (``text``: its full text, or its abstract when it has none), or its bibliographic record (``record``: title,
    authors, venue, year and publication type, ``rk.notes.record_text``), for what a paper rarely states in its
    own text, such as where it was published."""
    name: str
    description: str
    type: Literal["text", "number", "category"]
    categories: list[str] | None = None
    unit: str | None = None
    source: Literal["text", "record"] = "text"

    @model_validator(mode="after")
    def _categories_iff_category(self) -> ExtractionField:
        if (self.type == "category") != bool(self.categories):
            raise ValueError(f"field {self.name!r}: categories are required for type 'category' and only for it")
        return self


# the generic field pair of a systematic review's outcome that no effect measure maps (``OutcomeSpec.fields``)
PAIR_ROLES = ("intervention", "comparator")


class OutcomeSpec(BaseModel):
    """A pre-specified outcome: its ``name`` and the extraction fields its numbers come from (``fields``: role -> the
    name of a number field of the extraction schema). In a meta-analysis the roles are those of the effect measure
    (``rk.stats.effects.MEASURES``, e.g. ``events_treatment``); in any other systematic review plan the pair
    ``PAIR_ROLES`` (``intervention``: the intervention's or index test's value, ``comparator``: the comparator's), e.g.
    a diagnostic-accuracy outcome's two arms. Synthesize counts the studies by the direction of the effect between the
    pair (the means of an MD or SMD outcome, the risks of an RR, OR or RD one). A plain name (the shape before the
    mapping existed) loads as an outcome without fields: ``check_protocol`` refuses it in a meta-analysis, synthesize
    neither pools it nor counts its directions.

    Calibration (synthesis across studies, 2026-11-13): the kernel computed no cross-study count for a systematic
    review, although its extraction rows gave one for every evaluation topic."""
    name: str
    fields: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _plain_name(cls, v):
        return {"name": v, "fields": {}} if isinstance(v, str) else v


class SynthesisPlan(BaseModel):
    """The protocol's synthesis plan. ``model``, ``heterogeneity`` and ``sensitivity`` describe the analysis in words;
    a meta-analysis is computed with the typed fields only: ``outcomes`` (the pre-specified outcomes, ``OutcomeSpec``:
    each pooled group is of one of them, by name, and computed from its fields), ``effect_measure`` (``RR``, ``OR``,
    ``RD``, ``MD`` or ``SMD``), ``pool_model`` (the τ² estimator: ``REML``, ``DL`` or ``FE``), ``hksj``
    (Hartung-Knapp-Sidik-Jonkman intervals: ``"auto"``, the Cochrane Handbook rule, or always / never) and ``pi_df``
    (the prediction interval's degrees of freedom: ``"ci"`` as the confidence interval, or Higgins 2009's ``"k-2"``)
    and ``min_studies`` (the fewest studies with a computable effect a pool needs; 2 by default, also for a protocol
    frozen before the field). ``check_protocol`` requires them for a meta-analysis."""
    method: Literal["narrative_swim", "meta_analysis", "evidence_map", "thematic"]
    effect_measure: str | None = None
    model: str | None = None
    heterogeneity: str | None = None
    sensitivity: str | None = None
    pool_model: Literal["REML", "DL", "FE"] | None = None
    hksj: StrictBool | Literal["auto"] = "auto"
    pi_df: Literal["ci", "k-2"] = "ci"
    min_studies: StrictInt = Field(2, ge=2)
    outcomes: list[OutcomeSpec] = []


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


class DesignBody(BaseModel):
    """A research proposal's method design as the Ideator writes it (§7.3): its items, expected results and alternative
    explanations. Measurement ids are unique across the whole design, so a falsification link names one measurement."""
    items: list[DesignItem] = Field(min_length=1)
    expected_results: str
    alternative_explanations: list[str]

    @model_validator(mode="after")
    def _unique_ids(self) -> DesignBody:
        ids = [i.id for i in self.items]
        if dup := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate design item ids: {dup}")
        owners: dict[str, set[str]] = {}
        for item in self.items:
            for m in item.measurements:
                owners.setdefault(m.id, set()).add(item.id)
        if shared := sorted(m for m, o in owners.items() if len(o) > 1):
            raise ValueError(f"measurement ids used by more than one design item: {shared}")
        return self


class HypothesisText(BaseModel):
    statement: str
    falsification: str


class DesignIn(DesignBody):
    """The stored design: the body plus, filled by the kernel when it is approved, ``falsified_by`` (hypothesis id ->
    the ids of the measurements whose result would count against it) and ``hypotheses`` (hypothesis id -> its statement
    and falsification). Frozen, it pre-registers the hypotheses it tests with their text and falsification links."""
    falsified_by: dict[str, list[str]] = {}
    hypotheses: dict[str, HypothesisText] = {}
