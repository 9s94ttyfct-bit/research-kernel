# `rk support check` demo — 2026-11-06

**Passage** (4 sentences) about Jergas & Baethge 2015, *Quotation accuracy in medical journal articles* (PeerJ,
doi:10.7717/peerj.1364, CC BY). Full text came from Europe PMC JATS.

| # | Sentence | Gold |
|---|---|---|
| 0 | A meta-analysis of quotation accuracy included 28 studies after screening 559. | supported |
| 1 | It estimated a total quotation error rate of 25.4%. | supported |
| 2 | The study proved that quotation errors are detrimental to scientific progress. | unsupported (the paper says future research should find out) |
| 3 | Indirect references accounted for about half of all quotation problems. | contradicted (the paper says less than one sixth) |

**Setup:**
- Thresholds: the calibrated τ_hi = 0.9757 and τ_lo = 0.0082 from `support-2026-11-06.md`.
- Judges: `omp_gateway:openai-codex/gpt-6-luna` (OpenAI) and `claude_cli` (Anthropic).
- The generator's vendor is always excluded, so each run has one eligible judge. `--min-judges 1` is a demo setting; the product default is 2.
- Each driver ran the Arguer (claim extraction) and Reader (verified quotes) tasks itself.

## Results

| Driver (claim author) | Claims | Verified quotes per claim | Eligible judge, labels for #0–#3 | MiniCheck p for #0–#3 | Final verdicts |
|---|---|---|---|---|---|
| `omp_gateway:openai-codex/gpt-6-luna` | 4 | 1–2 | claude_cli: supported, supported, contradicted, contradicted | 0.759, 0.164, 0.034, 0.061 | 4 × uncertain |
| `claude_cli` | 4 | 2–3 | Luna: supported, supported, partial, contradicted | 0.176, 0.198, 0.033, 0.089 | 4 × uncertain |
| `codex_cli` | 4 | 2–4 | claude_cli: supported, supported, contradicted, contradicted | 0.246, 0.178, 0.619, 0.110 | 4 × uncertain |

- Every run found the passages behind each sentence and stored them as verified quotes with page and character
  locators. Example: #3 → p1[882:962], "Indirect references accounted for less than one sixth of all quotation
  problems."
- Same-vendor judges were excluded every time.
- The eligible judges got the direction right on all 12 claims: gold-supported claims were judged supported, and
  gold-negative claims were judged contradicted or partial.
- No claim was wrongly marked supported. No claim reached a definite verdict either:
  - MiniCheck's p for the clearly supported claims (0.16–0.76) is far below the calibrated τ_hi.
  - The negative claims' p (0.03–0.62) is above τ_lo.
  - With the spec's rule (layer 1 **and** layer 2 must agree), the verdict is uncertain. In collaborative mode each
    claim opened an `uncertain_support` checkpoint.

This matches the SciFact result (precision 1.0, coverage 0.07–0.10). Layer 1 is the coverage bottleneck. The scores
match the official MiniCheck package (|Δp| < 1e-3), so this is how the model behaves, not an integration error.
