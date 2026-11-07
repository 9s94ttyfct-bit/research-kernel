"""Confusion matrices and support precision/coverage (spec §10)."""

from __future__ import annotations


def confusion(gold: list[str], pred: list[str], gold_labels: list[str], pred_labels: list[str]
              ) -> dict[str, dict[str, int]]:
    m = {g: dict.fromkeys(pred_labels, 0) for g in gold_labels}
    for g, p in zip(gold, pred, strict=True):
        m[g][p] += 1
    return m


def support_metrics(gold: list[str], pred: list[str]) -> dict:
    """Precision of "supported" predictions, precision of the negative ("unsupported"/"contradicted") predictions as
    the share whose gold label is not "supported", coverage (share not "uncertain"), counts."""
    n = len(gold)
    said = [g for g, p in zip(gold, pred, strict=True) if p == "supported"]
    negative = [g for g, p in zip(gold, pred, strict=True) if p in ("unsupported", "contradicted")]
    precision = (sum(g == "supported" for g in said) / len(said)) if said else None
    precision_negative = (sum(g != "supported" for g in negative) / len(negative)) if negative else None
    coverage = (sum(p != "uncertain" for p in pred) / n) if n else 0.0
    return {"n": n, "predicted_supported": len(said), "precision_supported": precision,
            "predicted_negative": len(negative), "precision_negative": precision_negative, "coverage": coverage}


def md_matrix(m: dict[str, dict[str, int]]) -> str:
    cols = list(next(iter(m.values())))
    lines = ["| gold \\ predicted | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    lines += [f"| {g} | " + " | ".join(str(row[c]) for c in cols) + " |" for g, row in m.items()]
    return "\n".join(lines)
