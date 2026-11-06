# V-SUPPORT evaluation — 2026-11-06

rk 0.1.0; layer 1 `lytang/MiniCheck-Flan-T5-Large`. Data: SciFact (claims CC BY 4.0, abstracts ODC-By); thresholds calibrated on train, metrics on held-out dev.

## Layer-1-only calibration (SciFact train, all pairs)

- τ_hi = 0.9757 (target precision 0.95 reached)
- τ_lo = 0.0082 (reached); n = 919

## Layer 1 only (SciFact dev, all pairs)

precision(supported) 1.000 (27 predicted supported) → MET (target ≥ 0.95); coverage 0.097 → MISSED (target ≥ 0.7)

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 27 | 0 | 1 | 110 |
| contradicted | 0 | 0 | 1 | 70 |
| unsupported | 0 | 0 | 4 | 127 |

## Joint calibration (SciFact train, seeded sample of 200, seed 13)

Judges: `omp_gateway:openai-codex/gpt-6-luna`, `omp_gateway:anthropic/claude-sonnet-5`; min_judges = 2.

- Pairs where ≥2 judges agree on "supported": 27 (gold-supported share 1.000)
- Pairs where ≥2 judges agree on unsupported/contradicted: 94 (not-gold-supported share 0.968)
- τ_hi_judged = 0.0742; τ_lo_judged = 0.9713 (a value of n/a would mean the target was not reachable; the layer-1-only threshold then applies)

In-sample (train) result of the amended rule — optimistic by construction:

precision(supported) 1.000 (26 predicted supported) → MET (target ≥ 0.95); coverage 0.600 → MISSED (target ≥ 0.7)

## Held-out comparison (SciFact dev, seeded sample of 100, seed 13)

Failed judge calls: 0 (counted as no opinion).

### Old rule (layer-1-only thresholds + judges)

precision(supported) 1.000 (5 predicted supported) → MET (target ≥ 0.95); coverage 0.050 → MISSED (target ≥ 0.7)

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 5 | 0 | 0 | 40 |
| contradicted | 0 | 0 | 0 | 21 |
| unsupported | 0 | 0 | 0 | 34 |

### Amended rule (§9.1, 2026-11-06)

precision(supported) 1.000 (11 predicted supported) → MET (target ≥ 0.95); coverage 0.540 → MISSED (target ≥ 0.7)

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 11 | 1 | 3 | 30 |
| contradicted | 0 | 16 | 0 | 5 |
| unsupported | 0 | 2 | 21 | 11 |

### Diagnostic: judges alone (layer 1 ignored; not a product rule)

precision(supported) 1.000 (12 predicted supported) → MET (target ≥ 0.95); coverage 0.550 → MISSED (target ≥ 0.7)

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 12 | 1 | 3 | 29 |
| contradicted | 0 | 16 | 0 | 5 |
| unsupported | 0 | 2 | 21 | 11 |
