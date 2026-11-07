"""V-SUPPORT judges: LLMs from vendors other than the claim's author (spec §8.1, §9.1), configured as the roles
``rk.roles.policy.JUDGE_ROLES``.

A judge's ``supported``/``partial``/``contradicted`` label counts only if it quotes the evidence verbatim; otherwise
that judge's opinion becomes ``uncertain``."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from rk.llm.drivers import DriverSpec
from rk.llm.runner import run_task
from rk.llm.types import TaskResult
from rk.llm.vendors import vendor_of
from rk.roles.policy import JUDGE_ROLES
from rk.text.layer import build_norm_keys, normalize_query
from rk.verify.quote import find_verbatim

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext

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


def role_judges(settings, drivers: list[DriverSpec] | None) -> list[tuple[str, DriverSpec]]:
    """The judges with their roles: the configured ``JUDGE_ROLES``, each as its own role, or, for an explicit
    ``drivers`` list, the i-th driver as ``JUDGE_ROLES[i]``."""
    if drivers is None:
        return [(r, DriverSpec.parse(settings.drivers[r])) for r in JUDGE_ROLES if r in settings.drivers]
    if len(drivers) > len(JUDGE_ROLES):
        raise ValueError(f"at most {len(JUDGE_ROLES)} judges ({', '.join(JUDGE_ROLES)}), got {len(drivers)}")
    return list(zip(JUDGE_ROLES, drivers, strict=False))


def judge_drivers(settings, drivers: list[DriverSpec] | None) -> list[DriverSpec]:
    """``drivers``, else the configured ``JUDGE_ROLES`` in role order."""
    return [spec for _, spec in role_judges(settings, drivers)]


def judge_vendor_error(vendor: str | None, generator_vendors: Collection[str | None], seen: set[str]) -> str | None:
    """Why a judge of ``vendor`` cannot judge work of ``generator_vendors`` (the vendors that generated it; ``seen``:
    the vendors of the judges already admitted), else ``None``. A judge must be of a known, non-human vendor that
    differs from every generator's and from every other judge's."""
    if vendor is None or vendor == "human" or vendor in generator_vendors:
        return "same_or_unknown_vendor"
    if vendor in seen:
        return "duplicate_vendor"
    return None


def admit(judges: list[tuple[str, DriverSpec]], generator_vendors: Collection[str | None]
          ) -> tuple[list[tuple[str, DriverSpec, str]], dict[str, str]]:
    """The ``(role, driver)`` judges, in this order, that may judge work of ``generator_vendors`` (every vendor that
    generated it; empty when it is no generated text), as ``(role, driver, vendor)``, and the skipped ones as role ->
    why (``judge_vendor_error``: a known, non-human vendor that is no generator's and not another admitted judge's)."""
    admitted: list[tuple[str, DriverSpec, str]] = []
    skipped: dict[str, str] = {}
    seen: set[str] = set()
    for role, spec in judges:
        vendor = vendor_of(spec.provider, spec.model)
        error = judge_vendor_error(vendor, generator_vendors, seen)
        if error is not None:
            skipped[role] = error
            continue
        seen.add(vendor)
        admitted.append((role, spec, vendor))
    return admitted, skipped


def judges_problem(drivers: dict[str, str], generator_roles: tuple[str, ...] = ()) -> str:
    """Why the ``JUDGE_ROLES`` of ``drivers`` (every role configured) cannot give two cross-vendor opinions, else
    ``""``: two of them must be of known, non-human vendors, different from each other and from the vendor of every
    ``generator_roles`` driver."""
    generators = {vendor_of(s.provider, s.model) for s in (DriverSpec.parse(drivers[r]) for r in generator_roles)}
    seen: set[str] = set()
    unusable = []
    for role in JUDGE_ROLES:
        spec = DriverSpec.parse(drivers[role])
        vendor = vendor_of(spec.provider, spec.model)
        if vendor is not None and vendor in generators:
            unusable.append(f"{role} ({spec}) has the vendor {vendor} of a generating role")
        elif judge_vendor_error(vendor, (), seen) is not None:
            unusable.append(f"{role} ({spec}) has an unknown, human or repeated vendor ({vendor or 'unknown'})")
        else:
            seen.add(vendor)
    if len(seen) >= 2:
        return ""
    *rest, last = generator_roles or ("",)
    neither = f", neither the vendor of the {', '.join(rest) + ' or ' if rest else ''}{last}" if generator_roles else ""
    return (f"two judges of different known vendors{neither}: {'; '.join(unusable)}. Set "
            f"drivers.{', drivers.'.join(JUDGE_ROLES)} in config.toml (rk providers)")


def admitted_judges(ctx: StageContext, generator_vendors: Collection[str | None],
                    judges: list[DriverSpec] | None) -> tuple[list[tuple[str, DriverSpec, str]], dict[str, str]]:
    """``admit`` over ``judges``, else the configured ``JUDGE_ROLES``."""
    return admit(role_judges(ctx.kernel.settings(), judges), generator_vendors)


def ask_judge(run: JudgeRunner, role: str, spec: DriverSpec, *, claim_text: str, evidence_text: str,
              provider=None) -> JudgeOpinion:
    """One judge's opinion as ``role`` (admission is the caller's): a no-tool task through ``run`` (``run_task``'s
    keyword interface). A failed task gives an ``uncertain`` opinion carrying the task's error."""
    base = {"driver": str(spec), "vendor": vendor_of(spec.provider, spec.model)}
    r = run(role=role, instructions=INSTRUCTIONS, inputs={"claim": claim_text, "evidence": evidence_text},
            output_schema=JudgeVerdict, driver=spec, provider=provider, tools=[], max_turns=4, timeout_s=300)
    if not r.ok:
        return JudgeOpinion(**base, label="uncertain", task_id=r.task_id, error=r.error or "failed")
    label, spans_ok = check_opinion(r.output, evidence_text)
    return JudgeOpinion(**base, label=label, raw_label=r.output.label, spans_ok=spans_ok, task_id=r.task_id)


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
    judges = role_judges(kernel.settings(), drivers)
    _, skipped = admit(judges, {generator_vendor})
    return [JudgeOpinion(driver=str(spec), vendor=vendor_of(spec.provider, spec.model), label="uncertain",
                         error=skipped[role]) if role in skipped else
            ask_judge(run, role, spec, claim_text=claim_text, evidence_text=evidence_text,
                      provider=(providers or {}).get(str(spec)))
            for role, spec in judges]
