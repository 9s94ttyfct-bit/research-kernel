"""V-SUPPORT layer 2: LLM judges from vendors other than the claim's author (spec §8.1, §9.1).

A judge's ``supported``/``partial``/``contradicted`` label counts only if it quotes the evidence verbatim; otherwise
that judge's opinion becomes ``uncertain``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from rk.llm.drivers import DriverSpec
from rk.llm.runner import run_task
from rk.llm.types import TaskResult
from rk.llm.vendors import vendor_of
from rk.text.layer import build_norm_keys, normalize_query
from rk.verify.quote import find_verbatim

JudgeLabel = Literal["supported", "partial", "unsupported", "contradicted"]
NEEDS_SPANS = frozenset({"supported", "partial", "contradicted"})
JudgeRunner = Callable[..., TaskResult]  # run_task's keyword interface without kernel, registry and project

INSTRUCTIONS = (
    "Decide whether the evidence supports the claim, using only the evidence text. Labels: supported (the evidence "
    "states or directly entails the claim), partial (the claim is broader, stronger or more specific than the "
    "evidence), unsupported (the evidence does not address it), contradicted (the evidence says the opposite). In "
    "quoted_spans copy, character for character, the evidence sentences or clauses your label rests on."
)


class JudgeVerdict(BaseModel):
    label: JudgeLabel
    quoted_spans: list[str] = Field(default_factory=list)
    rationale: str = ""


class JudgeOpinion(BaseModel):
    driver: str
    vendor: str | None
    label: Literal["supported", "partial", "unsupported", "contradicted", "uncertain"]
    raw_label: str | None = None
    spans_ok: bool = False
    task_id: str | None = None
    error: str | None = None


def span_in(evidence: str, span: str) -> bool:
    q = normalize_query(span)
    return bool(q) and any(find_verbatim(q, key.key) >= 0 for key in build_norm_keys(evidence))


def check_opinion(v: JudgeVerdict, evidence: str) -> tuple[str, bool]:
    spans_ok = bool(v.quoted_spans) and all(span_in(evidence, s) for s in v.quoted_spans)
    if v.label in NEEDS_SPANS and not spans_ok:
        return "uncertain", spans_ok
    return v.label, spans_ok


def judge_drivers(settings, drivers: list[DriverSpec] | None) -> list[DriverSpec]:
    if drivers is not None:
        return drivers
    return [DriverSpec.parse(settings.drivers[r]) for r in ("judge", "judge_2") if r in settings.drivers]


def run_judges(kernel, registry, *, project: str, claim_text: str, evidence_text: str,
               generator_vendor: str | None, drivers: list[DriverSpec] | None = None,
               providers: dict[str, object] | None = None, run: JudgeRunner | None = None) -> list[JudgeOpinion]:
    """``providers`` (driver string → Provider) replaces the configured driver; used by tests and evaluations. ``run``
    replaces ``run_task`` (called with ``role``, ``instructions``, ``inputs``, ``output_schema``, ``driver``,
    ``provider``, ``tools``, ``max_turns``, ``timeout_s``), e.g. a stage's task executor, whose budget and rate-cap
    exceptions then propagate."""
    if run is None:
        def run(**task):
            return run_task(kernel, registry, project=project, **task)
    opinions: list[JudgeOpinion] = []
    seen_vendors: set[str] = set()
    for i, spec in enumerate(judge_drivers(kernel.settings(), drivers)):
        vendor = vendor_of(spec.provider, spec.model)
        base = {"driver": str(spec), "vendor": vendor}
        if vendor is None or vendor == "human" or vendor == generator_vendor:
            opinions.append(JudgeOpinion(**base, label="uncertain", error="same_or_unknown_vendor"))
            continue
        if vendor in seen_vendors:
            opinions.append(JudgeOpinion(**base, label="uncertain", error="duplicate_vendor"))
            continue
        seen_vendors.add(vendor)
        r = run(role="judge" if i == 0 else "judge_2", instructions=INSTRUCTIONS,
                inputs={"claim": claim_text, "evidence": evidence_text}, output_schema=JudgeVerdict, driver=spec,
                provider=(providers or {}).get(str(spec)), tools=[], max_turns=4, timeout_s=300)
        if not r.ok:
            opinions.append(JudgeOpinion(**base, label="uncertain", task_id=r.task_id, error=r.error or "failed"))
            continue
        label, spans_ok = check_opinion(r.output, evidence_text)
        opinions.append(JudgeOpinion(**base, label=label, raw_label=r.output.label, spans_ok=spans_ok,
                                     task_id=r.task_id))
    return opinions
