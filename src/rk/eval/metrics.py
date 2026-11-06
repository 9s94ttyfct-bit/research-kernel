"""Confusion matrices, support precision/coverage, and threshold calibration (spec §10)."""

from __future__ import annotations


def confusion(gold: list[str], pred: list[str], gold_labels: list[str], pred_labels: list[str]
              ) -> dict[str, dict[str, int]]:
    m = {g: dict.fromkeys(pred_labels, 0) for g in gold_labels}
    for g, p in zip(gold, pred, strict=True):
        m[g][p] += 1
    return m


def support_metrics(gold: list[str], pred: list[str]) -> dict:
    """precision of "supported" predictions, coverage (share not "uncertain"), counts."""
    n = len(gold)
    said = [g for g, p in zip(gold, pred, strict=True) if p == "supported"]
    precision = (sum(g == "supported" for g in said) / len(said)) if said else None
    coverage = (sum(p != "uncertain" for p in pred) / n) if n else 0.0
    return {"n": n, "predicted_supported": len(said), "precision_supported": precision, "coverage": coverage}


def calibrate_thresholds(ps: list[float], gold: list[str], *, target_precision: float = 0.95) -> dict:
    """Smallest τ_hi with precision(gold=supported | p ≥ τ_hi) ≥ target, and largest τ_lo with
    share(gold≠supported | p ≤ τ_lo) ≥ target. τ_hi = 1.0 (never "supported") when the target is unreachable."""
    pairs = sorted(zip(ps, gold, strict=True))
    cands = sorted(set(ps))
    tau_hi, hi_reached = 1.0, False
    for t in cands:
        sel = [g for p, g in pairs if p >= t]
        if sel and sum(g == "supported" for g in sel) / len(sel) >= target_precision:
            tau_hi, hi_reached = t, True
            break
    tau_lo, lo_reached = 0.0, False
    for t in reversed(cands):
        sel = [g for p, g in pairs if p <= t]
        if sel and sum(g != "supported" for g in sel) / len(sel) >= target_precision:
            tau_lo, lo_reached = t, True
            break
    if tau_lo >= tau_hi:  # keep a non-empty uncertain band
        tau_lo = max(0.0, min(tau_lo, tau_hi - 1e-6))
    return {"tau_hi": tau_hi, "tau_lo": tau_lo, "hi_reached": hi_reached, "lo_reached": lo_reached,
            "target_precision": target_precision, "n": len(ps)}


def layer1_predict(p: float, tau_hi: float, tau_lo: float) -> str:
    return "supported" if p >= tau_hi else ("unsupported" if p <= tau_lo else "uncertain")


def md_matrix(m: dict[str, dict[str, int]]) -> str:
    cols = list(next(iter(m.values())))
    lines = ["| gold \\ predicted | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    lines += [f"| {g} | " + " | ".join(str(row[c]) for c in cols) + " |" for g, row in m.items()]
    return "\n".join(lines)
