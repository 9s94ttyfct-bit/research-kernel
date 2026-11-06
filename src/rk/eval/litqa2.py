"""``rk eval litqa2``: multiple-choice literature questions answered with the Kernel's own tools (no judge needed).

The Lead role is used because it is the only role with both search and full-text tools (spec §8.1). Options are
shuffled with a fixed seed and always include "Insufficient information to answer the question"; choosing it
counts as not answering. accuracy = correct / n, precision = correct / answered, coverage = answered / n."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from rk.config import load_settings
from rk.eval.data import LitQAItem, load_litqa2
from rk.eval.scratch import eval_provider, scratch_kernel
from rk.llm.drivers import DriverSpec
from rk.llm.runner import run_task

INSUFFICIENT = "Insufficient information to answer the question"
INSTRUCTIONS = (
    "Answer the multiple-choice question using the scholarly literature. Search for relevant papers "
    "(rk_search_works with add=true), fetch full text where available, and read the passages that answer it. Choose the "
    "option the literature supports; if you could not find enough evidence, choose the 'Insufficient information' "
    "option. Output the 0-based index of your choice and the ids of any quote notes you saved as evidence."
)


class Answer(BaseModel):
    choice_index: int
    evidence_note_ids: list[str] = []


def options_for(item: LitQAItem, seed: int) -> tuple[list[str], int, int]:
    """Shuffled options, the index of the ideal answer, and the index of the 'insufficient' option."""
    opts = [item.ideal, *item.distractors, INSUFFICIENT]
    random.Random(f"{seed}:{item.id}").shuffle(opts)
    return opts, opts.index(item.ideal), opts.index(INSUFFICIENT)


def score(rows: list[dict]) -> dict:
    n = len(rows)
    answered = [r for r in rows if r["answered"]]
    correct = sum(r["correct"] for r in rows)
    return {"n": n, "answered": len(answered), "correct": correct, "accuracy": correct / n if n else 0.0,
            "precision": correct / len(answered) if answered else None, "coverage": len(answered) / n if n else 0.0}


def run_litqa2(*, driver: DriverSpec, limit: int = 20, seed: int = 13, report_dir: Path = Path("docs/eval"),
               items: list[LitQAItem] | None = None, provider=None, http=None) -> dict:
    provider = provider or eval_provider(driver, load_settings())
    pool = items if items is not None else load_litqa2()
    sample = random.Random(seed).sample(pool, min(limit, len(pool)))
    k, reg = scratch_kernel("eval-litqa2", "LitQA2 evaluation", http=http)
    rows = []
    try:
        for n, item in enumerate(sample):
            opts, ideal, insufficient = options_for(item, seed)
            slug = f"lq-{n:03d}"  # one project per question: no question sees another's sources or notes
            k.create_project(slug, f"LitQA2 {item.id}", actor="eval")
            r = run_task(k, reg, project=slug, role="lead", instructions=INSTRUCTIONS,
                         inputs={"question": item.question, "options": {str(i): o for i, o in enumerate(opts)}},
                         output_schema=Answer, driver=driver, provider=provider, max_turns=30, timeout_s=900)
            choice = r.output.choice_index if r.ok and 0 <= r.output.choice_index < len(opts) else insufficient
            rows.append({"id": item.id, "ok": r.ok, "answered": choice != insufficient, "correct": choice == ideal,
                         "tool_calls": r.tool_calls, "error": r.error})
    finally:
        k.close()
    result = {"date": datetime.now(UTC).date().isoformat(), "driver": str(driver), "seed": seed, **score(rows),
              "rows": rows}
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report_dir / f"litqa2-{result['date']}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=1))
    prec = "n/a" if result["precision"] is None else f"{result['precision']:.3f}"
    stem.with_suffix(".md").write_text(
        f"# LitQA2 — {result['date']}\n\nSeeded sample of {result['n']} of 199 questions (CC BY-SA 4.0), seed {seed}; "
        f"driver `{result['driver']}` (Lead role, Kernel search and full-text tools).\n\n"
        "| system | accuracy | precision (answered) | coverage |\n|---|---|---|---|\n"
        f"| rk ({result['driver']}) | {result['accuracy']:.3f} | {prec} | {result['coverage']:.3f} |\n"
        "| PaperQA2 + Ollama baseline | not run | — | — |\n\n"
        "Baseline not run: Ollama Cloud was out of quota and no capable local model was installed (2026-11-05).\n")
    result["report"] = str(stem.with_suffix(".md"))
    return result
