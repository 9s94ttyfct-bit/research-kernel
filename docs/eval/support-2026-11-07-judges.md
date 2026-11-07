# V-SUPPORT evaluation, judges only — 2026-11-07

rk 0.1.0. Data: SciFact dev (claims CC BY 4.0, abstracts ODC-By), a seeded sample of 60 of 340 pairs (seed 13). Judges: `omp_gateway:anthropic/claude-haiku-5-5`, `omp_gateway:openai-codex/gpt-6-luna`, `omp_gateway:ollama-cloud/deepseek-v4.1-flash`, each asked once as its own role. Per generator scenario, only the judges admitted for that generator's vendor count, and a verdict needs at least 2 valid admitted judges that all give the same label; V-NUM gates "supported". Nothing is calibrated.

Failed judge calls: 0 (counted as no opinion; not admitted is listed apart).

## Generator `anthropic` (headline)

- Admitted: `omp_gateway:openai-codex/gpt-6-luna`, `omp_gateway:ollama-cloud/deepseek-v4.1-flash`; not admitted: `omp_gateway:anthropic/claude-haiku-5-5` (same_or_unknown_vendor).
- precision(supported) 1.000 (6 predicted supported) → MET (target ≥ 0.95); coverage 0.533 → MISSED (target ≥ 0.7)
- precision by verdict: supported 1.000, unsupported 0.941, contradicted 0.778; negative verdicts (unsupported/contradicted): 26, not gold-supported share 0.962.

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 6 | 0 | 1 | 20 |
| contradicted | 0 | 7 | 0 | 2 |
| unsupported | 0 | 2 | 16 | 6 |

## Generator `openai`

- Admitted: `omp_gateway:anthropic/claude-haiku-5-5`, `omp_gateway:ollama-cloud/deepseek-v4.1-flash`; not admitted: `omp_gateway:openai-codex/gpt-6-luna` (same_or_unknown_vendor).
- precision(supported) 1.000 (6 predicted supported) → MET (target ≥ 0.95); coverage 0.483 → MISSED (target ≥ 0.7)
- precision by verdict: supported 1.000, unsupported 0.941, contradicted 0.833; negative verdicts (unsupported/contradicted): 23, not gold-supported share 0.957.

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 6 | 0 | 1 | 20 |
| contradicted | 0 | 5 | 0 | 4 |
| unsupported | 0 | 1 | 16 | 7 |

## Generator `human`

- Admitted: `omp_gateway:anthropic/claude-haiku-5-5`, `omp_gateway:openai-codex/gpt-6-luna`, `omp_gateway:ollama-cloud/deepseek-v4.1-flash`; not admitted: none.
- precision(supported) 1.000 (6 predicted supported) → MET (target ≥ 0.95); coverage 0.483 → MISSED (target ≥ 0.7)
- precision by verdict: supported 1.000, unsupported 0.941, contradicted 0.833; negative verdicts (unsupported/contradicted): 23, not gold-supported share 0.957.

| gold \ predicted | supported | contradicted | unsupported | uncertain |
|---|---|---|---|---|
| supported | 6 | 0 | 1 | 20 |
| contradicted | 0 | 5 | 0 | 4 |
| unsupported | 0 | 1 | 16 | 7 |

## Judges

| role | judge | valid opinions | failed calls | label equals gold | prompt fingerprint |
|---|---|---|---|---|---|
| judge | `omp_gateway:anthropic/claude-haiku-5-5` | 60 | 0 | 31 | `139ee07af7e3` |
| judge_2 | `omp_gateway:openai-codex/gpt-6-luna` | 60 | 0 | 32 | `424dba78d4e6` |
| judge_3 | `omp_gateway:ollama-cloud/deepseek-v4.1-flash` | 60 | 0 | 35 | `0d37bf2f44bc` |

Pairwise agreement (pairs where both judges gave a valid opinion):

| judges | both valid | same verdict label | same partial/uncertain |
|---|---|---|---|
| `omp_gateway:anthropic/claude-haiku-5-5` · `omp_gateway:openai-codex/gpt-6-luna` | 60 | 29 | 20 |
| `omp_gateway:anthropic/claude-haiku-5-5` · `omp_gateway:ollama-cloud/deepseek-v4.1-flash` | 60 | 29 | 14 |
| `omp_gateway:openai-codex/gpt-6-luna` · `omp_gateway:ollama-cloud/deepseek-v4.1-flash` | 60 | 32 | 16 |

## Next to the layer-1 rule of 2026-11-06 (added by hand)

The 2026-11-06 numbers (`support-2026-11-06.md`) come from a different sample and a different setup, so this is a
side-by-side, not a paired comparison:

| | 2026-11-06, amended layer-1 rule | 2026-11-06, judges alone (diagnostic) | 2026-11-07, judges only, `anthropic` (headline) | 2026-11-07, `human` |
|---|---|---|---|---|
| Sample (SciFact dev, seed 13) | 100 pairs (45 / 34 / 21 gold supported / unsupported / contradicted) | same 100 | 60 pairs (27 / 24 / 9) | same 60 |
| Deciding judges | layer 1 Bespoke-MiniCheck-7B + `openai-codex/gpt-6-luna` + `anthropic/claude-sonnet-5` | Luna + Sonnet 5 | Luna + DeepSeek v4.1 flash (Haiku 5.5 not admitted) | Haiku + Luna + DeepSeek |
| Coverage | 0.52 | 0.55 | 0.533 | 0.483 |
| Supported correct | 10/10 | 12/12 | 6/6 | 6/6 |
| Gold-supported pairs judged supported | 10/45 = 0.22 | 12/45 = 0.27 | 6/27 = 0.22 | 6/27 = 0.22 |
| Negative verdicts with gold not supported | 39/42 = 0.93 | 39/43 = 0.91 | 25/26 = 0.96 | 22/23 = 0.96 |

- `random.sample` picks differently for 60 and 100 items: 56 of these 60 pairs are in the old 100-pair sample. The old
  report kept no per-item ids, so the overlapping pairs cannot be compared one by one.
- Dropping layer 1 kept "supported" precision at 1.0 and the coverage at about 0.5. Coverage is still far below the
  0.7 target. The main loss is "partial": each judge labelled 15–16 of the 27 gold-supported pairs "partial" (the
  claim is broader or more specific than the abstract), and one "partial" makes the verdict uncertain.
