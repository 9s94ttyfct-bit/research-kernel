"""``rk support check``: take a passage and its sources, return a per-claim support verdict (Plan 2 demo, spec §12).

1. An Arguer task (on the chosen driver) turns each sentence into one atomic claim; the Kernel records the claims
   with the driver's vendor as generator, so V-SUPPORT excludes same-vendor judges.
2. For each claim a Reader task quotes passages from the given sources (verified quote notes).
3. V-SUPPORT (MiniCheck + cross-vendor judges + V-NUM) decides; with gold labels a confusion matrix is added.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rk.argument import ArgumentService, ClaimIn, ClaimKind, EvidenceIn
from rk.eval.metrics import confusion
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.runner import run_task
from rk.notes import list_notes
from rk.verify.support import verify_support

GOLD_LABELS = ["supported", "unsupported", "contradicted"]
PRED_LABELS = ["supported", "unsupported", "contradicted", "uncertain"]


class ExtractedClaim(BaseModel):
    sentence_index: int
    text: str
    kind: ClaimKind = "fact"


class ExtractedClaims(BaseModel):
    claims: list[ExtractedClaim]


class QuotedEvidence(BaseModel):
    note_ids: list[str]


ARGUER = ("Split the passage into its sentences (numbered from 0 in the order given) and turn each sentence into "
          "exactly one atomic, checkable claim that keeps the sentence's meaning and strength (do not soften or "
          "strengthen it, keep every number). Output one claim per sentence with its sentence_index.")
READER = ("Find the passages in the given sources that bear on the claim (support it, qualify it or contradict it). "
          "Use rk_text_find and rk_text_read, then save each relevant passage as a verbatim quote note with rk_note_add "
          "(kind 'quote', role_in_source 'main'). Quote whole sentences. Output the ids of the notes you saved.")


class CheckRow(BaseModel):
    sentence_index: int
    claim_id: str
    claim: str
    verdict: str
    p: float | None
    judges: list[str]
    evidence: list[str]
    numbers_ok: bool
    gold: str | None = None
    reader_error: str | None = None  # the Reader task failed; "no evidence" then reflects that failure


class CheckReport(BaseModel):
    driver: str
    vendor: str | None
    rows: list[CheckRow]
    missing_gold: list[int] = []  # gold sentences the Arguer returned no claim for (scored as uncertain)
    confusion: dict | None = None
    accuracy: float | None = None


def support_check(kernel, registry, *, project: str, text: str, source_ids: list[str], driver: DriverSpec,
                  gold: dict[int, Literal["supported", "unsupported", "contradicted"]] | None = None,
                  judge_drivers: list[DriverSpec] | None = None, min_judges: int | None = None, provider=None,
                  judge_providers: dict | None = None, layer1=None, actor: str = "kernel") -> CheckReport:
    ps, lib = kernel.project(project)
    for sid in source_ids:
        lib.get(sid)  # KeyError for unknown sources
    prov = provider or build_provider(driver, kernel.settings())
    extracted = run_task(kernel, registry, project=project, role="arguer", instructions=ARGUER,
                         inputs={"passage": text}, output_schema=ExtractedClaims, driver=driver, provider=prov,
                         tools=[], max_turns=4, timeout_s=600, actor=actor)
    if not extracted.ok:
        raise RuntimeError(f"claim extraction failed: {extracted.error}")
    indices = [c.sentence_index for c in extracted.output.claims]
    if len(set(indices)) != len(indices) or any(i < 0 for i in indices):
        raise RuntimeError(f"claim extraction returned invalid sentence indices {indices}")
    svc = ArgumentService(ps)
    rows: list[CheckRow] = []
    for c in sorted(extracted.output.claims, key=lambda c: c.sentence_index):
        cid = svc.add_claim(ClaimIn(text=c.text, kind=c.kind), generator_vendor=prov.vendor, actor=actor)
        known = {n["id"] for n in list_notes(ps)}
        reader = run_task(kernel, registry, project=project, role="reader", instructions=READER,
                          inputs={"claim": c.text, "source_ids": source_ids}, output_schema=QuotedEvidence,
                          driver=driver, provider=prov, max_turns=16, timeout_s=900, actor=actor)
        # Evidence = the verified quote notes the reader actually created on the given sources in this step.
        new = [n for n in list_notes(ps) if n["id"] not in known and n["kind"] == "quote" and n["item_id"] in source_ids]
        if new:
            svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[n["id"] for n in new]), actor=actor)
        r = verify_support(kernel, registry, ps, cid, actor=actor, layer1=layer1, judge_drivers=judge_drivers,
                           judge_providers=judge_providers, min_judges=min_judges)
        rows.append(CheckRow(sentence_index=c.sentence_index, claim_id=cid, claim=c.text, verdict=r.verdict, p=r.p,
                             judges=[f"{o.driver}={o.label}{'' if o.error is None else f' ({o.error})'}"
                                     for o in r.opinions],
                             evidence=[f"p{n['locator']['page']}[{n['locator']['start']}:{n['locator']['end']}] "
                                       f"{n['text'][:120]}" for n in new],
                             numbers_ok=r.numbers_ok, gold=(gold or {}).get(c.sentence_index),
                             reader_error=None if reader.ok else (reader.error or "failed")))
    report = CheckReport(driver=str(driver), vendor=prov.vendor, rows=rows,
                         missing_gold=sorted(set(gold or {}) - set(indices)))
    if gold:
        # Every gold sentence is scored; one without a claim counts as an uncertain prediction.
        by_index = {r.sentence_index: r.verdict for r in rows}
        g = [gold[i] for i in sorted(gold)]
        p = [by_index.get(i, "uncertain") for i in sorted(gold)]
        report.confusion = confusion(g, p, GOLD_LABELS, PRED_LABELS)
        report.accuracy = sum(a == b for a, b in zip(g, p, strict=True)) / len(g)
    return report
