"""Confusion matrices and support precision/coverage (spec §10)."""

from __future__ import annotations


def confusion(gold: list[str], pred: list[str], gold_labels: list[str], pred_labels: list[str]
              ) -> dict[str, dict[str, int]]:
    m = {g: dict.fromkeys(pred_labels, 0) for g in gold_labels}
    for g, p in zip(gold, pred, strict=True):
        m[g][p] += 1
    return m


VERDICT_LABELS = ("supported", "unsupported", "contradicted")


def support_metrics(gold: list[str], pred: list[str]) -> dict:
    """Precision of "supported" predictions; precision of the negative ("unsupported"/"contradicted") predictions as
    the share whose gold label is not "supported"; the precision of each verdict label (gold equals the label);
    coverage (share not "uncertain"); counts. A precision with no predictions is None."""
    n = len(gold)
    pairs = list(zip(gold, pred, strict=True))

    def share(sel: list[bool]) -> float | None:
        return sum(sel) / len(sel) if sel else None

    said = [g for g, p in pairs if p == "supported"]
    negative = [g for g, p in pairs if p in ("unsupported", "contradicted")]
    return {"n": n, "predicted_supported": len(said), "precision_supported": share([g == "supported" for g in said]),
            "predicted_negative": len(negative), "precision_negative": share([g != "supported" for g in negative]),
            "coverage": (sum(p != "uncertain" for p in pred) / n) if n else 0.0,
            "precision_by_label": {lab: share([g == lab for g, p in pairs if p == lab]) for lab in VERDICT_LABELS}}


def md_matrix(m: dict[str, dict[str, int]]) -> str:
    cols = list(next(iter(m.values())))
    lines = ["| gold \\ predicted | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    lines += [f"| {g} | " + " | ".join(str(row[c]) for c in cols) + " |" for g, row in m.items()]
    return "\n".join(lines)
