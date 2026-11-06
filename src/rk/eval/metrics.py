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


def _smallest_hi(rows: list[tuple[float, str]], target: float) -> float | None:
    for t in sorted({p for p, _ in rows}):
        sel = [g for p, g in rows if p >= t]
        if sel and sum(g == "supported" for g in sel) / len(sel) >= target:
            return t
    return None


def _largest_lo(rows: list[tuple[float, str]], target: float) -> float | None:
    for t in sorted({p for p, _ in rows}, reverse=True):
        sel = [g for p, g in rows if p <= t]
        if sel and sum(g != "supported" for g in sel) / len(sel) >= target:
            return t
    return None


def calibrate_joint(rows: list[tuple[float | None, list[str], str]], *, target_precision: float = 0.95) -> dict:
    """Layer-1 thresholds for the judged tier, from (p, valid judge labels, gold) on a calibration sample.

    τ_hi_judged: smallest p threshold such that, among pairs where ≥2 judges all say "supported" and p ≥ τ, at least
    ``target_precision`` are gold-supported. τ_lo_judged: largest threshold such that, among pairs where ≥2 judges
    agree on one negative label (all "unsupported" or all "contradicted") and p ≤ τ, at least ``target_precision`` are
    not gold-supported. None when the target cannot be reached (the judged tier then never relaxes layer 1)."""
    agree_sup = [(p, g) for p, labels, g in rows if p is not None and len(labels) >= 2 and set(labels) == {"supported"}]
    agree_neg = [(p, g) for p, labels, g in rows if p is not None and len(labels) >= 2
                 and set(labels) in ({"unsupported"}, {"contradicted"})]
    return {"tau_hi_judged": _smallest_hi(agree_sup, target_precision),
            "tau_lo_judged": _largest_lo(agree_neg, target_precision),
            "n": len(rows), "n_agree_supported": len(agree_sup), "n_agree_negative": len(agree_neg),
            "agree_supported_precision": (sum(g == "supported" for _, g in agree_sup) / len(agree_sup)
                                          if agree_sup else None),
            "agree_negative_precision": (sum(g != "supported" for _, g in agree_neg) / len(agree_neg)
                                         if agree_neg else None),
            "target_precision": target_precision}


def layer1_predict(p: float, tau_hi: float, tau_lo: float) -> str:
    return "supported" if p >= tau_hi else ("unsupported" if p <= tau_lo else "uncertain")


def md_matrix(m: dict[str, dict[str, int]]) -> str:
    cols = list(next(iter(m.values())))
    lines = ["| gold \\ predicted | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    lines += [f"| {g} | " + " | ".join(str(row[c]) for c in cols) + " |" for g, row in m.items()]
    return "\n".join(lines)
