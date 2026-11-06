"""ReportBench citation precision/recall (the deterministic part of ReportBench; spec §10).

Upstream (``related_work_evaluator.py``) normalises titles by lowercasing, stripping ``string.punctuation`` and
collapsing whitespace, then intersects the set of cited titles with the ground-truth reference titles. Here cited
titles come from the report's ``cited_titles`` or are resolved from DOIs / arXiv ids in the text, instead of
fetching every cited URL with Firecrawl and an LLM."""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable
from pathlib import Path

from rk.eval.data import load_reportbench_gt

_PUNCT = str.maketrans("", "", string.punctuation)
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>)\]]+")
_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
TEXT_FIELDS = ("response", "content", "text", "message", "output", "result")


def normalize_title(title: str) -> str:
    return " ".join(title.lower().translate(_PUNCT).split())


def citation_pr(pred_titles: list[str], gt_titles: list[str]) -> dict:
    pred = {normalize_title(t) for t in pred_titles if t.strip()}
    gt = {normalize_title(t) for t in gt_titles if t.strip()}
    match = pred & gt
    return {"precision": len(match) / len(pred) if pred else 0.0, "recall": len(match) / len(gt) if gt else 0.0,
            "n_pred": len(pred), "n_gt": len(gt), "n_match": len(match)}


def identifiers_in(text: str) -> list[str]:
    dois = [d.rstrip(".,;") for d in _DOI.findall(text)]
    return sorted(set(dois)) + sorted({f"arXiv:{a}" for a in _ARXIV.findall(text)})


def report_titles(doc: dict, resolve_title: Callable[[str], str | None]) -> list[str]:
    if doc.get("cited_titles"):
        return list(doc["cited_titles"])
    text = next((doc[f] for f in TEXT_FIELDS if isinstance(doc.get(f), str)), "")
    return [t for t in (resolve_title(i) for i in identifiers_in(text)) if t]


def score_reports(reports_dir: Path, resolve_title: Callable[[str], str | None], *,
                  gt_dir: Path | None = None) -> dict:
    per: dict[str, dict] = {}
    for f in sorted(Path(reports_dir).glob("*.json")):
        arxiv_id = f.stem
        gt = load_reportbench_gt(arxiv_id, path=(gt_dir / f"{arxiv_id}.jsonl") if gt_dir else None)
        per[arxiv_id] = citation_pr(report_titles(json.loads(f.read_text()), resolve_title), gt)
    if not per:
        raise ValueError(f"no {{arxiv_id}}.json reports in {reports_dir}")
    tot = {k: sum(r[k] for r in per.values()) for k in ("n_pred", "n_gt", "n_match")}
    micro = {"precision": tot["n_match"] / tot["n_pred"] if tot["n_pred"] else 0.0,
             "recall": tot["n_match"] / tot["n_gt"] if tot["n_gt"] else 0.0}
    macro = {k: sum(r[k] for r in per.values()) / len(per) for k in ("precision", "recall")}
    return {"reports": per, "micro": micro, "macro": macro, **tot}


def resolver_titles(ctx) -> Callable[[str], str | None]:
    """Resolve an identifier to its registry title through the Plan 1 resolver (Crossref/OpenAlex/arXiv)."""
    from rk.sources.identifiers import parse_identifier
    from rk.sources.resolver import resolve

    def title(identifier: str) -> str | None:
        try:
            ident = parse_identifier(identifier)
        except ValueError:
            return None
        work = resolve(ctx, ident)
        return work.item.title if work else None

    return title
