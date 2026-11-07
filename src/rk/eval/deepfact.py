"""``rk eval deepfact``: verify report sentences from DeepFact-Bench end to end (evaluation only, not calibration —
its labels are judged against the wider literature, not a given passage).

Per claim: a Scout task finds and adds up to 5 works, a Reader task quotes passages that bear on the claim, the
claim is created with those notes as evidence, and V-SUPPORT decides. supported → supported, contradicted →
contradictory, anything else → inconclusive."""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from rk.argument import ArgumentService, ClaimIn, EvidenceIn
from rk.config import load_settings
from rk.eval.data import DeepFactClaim, load_deepfact
from rk.eval.metrics import confusion, md_matrix
from rk.eval.scratch import eval_provider, eval_task, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.notes import list_notes
from rk.verify.support import verify_support

LABELS = ["supported", "contradictory", "inconclusive"]
MAP = {"supported": "supported", "contradicted": "contradictory"}


class FoundSources(BaseModel):
    source_ids: list[str]


class Quoted(BaseModel):
    note_ids: list[str]


SCOUT = ("Find published works that could confirm or refute the claim below. Search with at least two different "
         "queries, then add the (at most 5) most relevant works with rk_source_add. Output their source ids.")
READER = ("For each source id given, fetch its full text with rk_source_fetch_fulltext, then find passages that "
          "support or contradict the claim with rk_text_find / rk_text_read and save each as a verbatim quote note "
          "(rk_note_add, kind 'quote', role_in_source 'main'). Skip sources without full text. Output the note ids.")


def check_claim(k, reg, c: DeepFactClaim, *, slug: str, driver: DriverSpec, provider, judges: list[DriverSpec],
                judge_providers, min_judges: int) -> dict:
    """Runs in its own project so no claim sees another claim's sources or notes. A rate-capped task (judges
    included) raises ``RateCapped`` instead of being scored as inconclusive."""
    ps = k.create_project(slug, f"DeepFact {c.id}", actor="eval")
    scout = eval_task(k, reg, project=slug, role="scout", instructions=SCOUT, inputs={"claim": c.sentence},
                      output_schema=FoundSources, driver=driver, provider=provider, max_turns=16, timeout_s=600)
    sources = scout.output.source_ids if scout.ok else []
    before = {n["id"] for n in list_notes(ps)}
    reader = eval_task(k, reg, project=slug, role="reader", instructions=READER,
                       inputs={"claim": c.sentence, "source_ids": sources}, output_schema=Quoted, driver=driver,
                       provider=provider, max_turns=24, timeout_s=900) if sources else None
    notes = [n["id"] for n in list_notes(ps) if n["id"] not in before and n["kind"] == "quote"]
    svc = ArgumentService(ps)
    cid = svc.add_claim(ClaimIn(text=c.sentence), generator_vendor="human", actor="eval")
    if notes:
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=notes), actor="eval")
    r = verify_support(k, reg, ps, cid, actor="eval", judge_drivers=judges, judge_providers=judge_providers,
                       min_judges=min_judges, run=lambda **task: eval_task(k, reg, project=slug, **task))
    return {"id": c.id, "domain": c.domain, "gold": c.gold, "pred": MAP.get(r.verdict, "inconclusive"),
            "verdict": r.verdict, "sources": len(sources), "notes": len(notes),
            "scout_ok": scout.ok, "reader_ok": bool(reader and reader.ok)}


def run_deepfact(*, driver: DriverSpec, judges: list[DriverSpec], min_judges: int, limit: int = 30, seed: int = 13,
                 report_dir: Path = Path("docs/eval"), claims: list[DeepFactClaim] | None = None, provider=None,
                 judge_providers: dict | None = None, http=None) -> dict:
    settings = load_settings()
    provider = provider or eval_provider(driver, settings)
    judge_providers = judge_providers or {str(j): eval_provider(j, settings, tools=False) for j in judges}
    pool = claims if claims is not None else load_deepfact()
    sample = random.Random(seed).sample(pool, min(limit, len(pool)))
    k, reg = scratch_kernel("eval-deepfact", "DeepFact-Bench evaluation", http=http)
    try:
        rows = [check_claim(k, reg, c, slug=f"df-{i:03d}", driver=driver, provider=provider, judges=judges,
                            judge_providers=judge_providers, min_judges=min_judges)
                for i, c in enumerate(sample)]
    finally:
        k.close()
    gold, pred = [r["gold"] for r in rows], [r["pred"] for r in rows]
    result = {"date": datetime.now(UTC).date().isoformat(), "driver": str(driver), "judges": [str(j) for j in judges],
              "min_judges": min_judges, "n": len(rows), "seed": seed,
              "accuracy": sum(g == p for g, p in zip(gold, pred, strict=True)) / len(rows) if rows else 0.0,
              "confusion": confusion(gold, pred, LABELS, LABELS),
              "by_domain": dict(Counter(r["domain"] for r in rows)), "rows": rows}
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"deepfact-{result['date']}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=1))
    stem.with_suffix(".md").write_text(
        f"# DeepFact-Bench (evaluation only) — {result['date']}\n\n"
        f"Seeded sample of {result['n']} claims (seed {seed}) from the 621-claim test split (MIT). Evidence gathered "
        f"by `{result['driver']}` (scout + reader roles); judges {', '.join(f'`{j}`' for j in result['judges'])}, "
        f"min_judges = {min_judges}. Gold labels judge each sentence against the broader literature, so "
        "\"inconclusive\" predictions include claims whose evidence was not found within the search.\n\n"
        f"Accuracy: {result['accuracy']:.3f}\n\n{md_matrix(result['confusion'])}\n\n"
        f"Sources found per claim (mean): {sum(r['sources'] for r in rows) / max(len(rows), 1):.1f}; quote notes per "
        f"claim (mean): {sum(r['notes'] for r in rows) / max(len(rows), 1):.1f}\n")
    result["report"] = str(stem.with_suffix(".md"))
    return result
