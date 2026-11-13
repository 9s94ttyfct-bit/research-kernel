# P1 protocol rerun 3, on calibration 2 (2026-11-12)

The six reproduction-set entries were seeded in a fresh home and run in P1 on `7753799` (protocol convergence plus
calibrations 1 and 2). Drivers and limits as in `p1-protocol-rerun-2026-11-12.md`. The home was lost when the machine
restarted (it lived in the system temporary directory) while three projects were in `search_screen`; the protocol
figures below were taken from it before that, and no later stage has a result from this run.

## Outcome

| entry | protocol | attempts | kernel grants | votes skipped for the cap | open blocking points after each vote |
|---|---|---|---|---|---|
| ai-prostate-mri | approved, frozen | 5 | 1 | 0 | 4 → 0 → 2 → 1 → 0 |
| cade-colonoscopy | approved, frozen | 3 | 0 | 0 | 3 → 1 → 0 |
| fructose-rats-sbp | approved, frozen | 3 | 0 | 0 | 3 → 1 → 0 |
| ai-pulmonary-hypertension | failed at the edge limit | 4 | 1 | 2 | 1 → 1 |
| ozone-bleaching | failed at the edge limit | 4 | 1 | 0 | 4 → 2 → 1 → 1 |
| tfa-heated-oils | failed at the edge limit | 5 | 2 | 0 | 2 → 2 → 2 → 1 → 1 |

Counted from attempt reasons mentioning the probe, which also matches judges' revise reasons that quote it; the
counts may be too high and cannot be rechecked (the home was lost).

Three of six protocols froze (one in each of the two earlier reruns). Prostate froze in the round the kernel granted.
57 of the 72 points were resolved by the judge that raised them. No judge was asked for omitted marks.

## What remains

1. **Pulmonary is above the cap by the probe's own lower bound.** Its two skipped votes had a largest single
   query-on-source count of 4178 (version 16, OpenAlex) against a cap of 3000. Across its versions OpenAlex counted
   about twice what Europe PMC counted for the same query.
2. **OpenAlex's `search` matches full text.** OpenAlex documents that `search=` on works matches the title, the
   abstract, the full text and the work's keywords; `search.title_and_abstract` matches titles and abstracts only.
   Every kernel search and probe on OpenAlex uses `search=`. The review selection that built this set searched
   titles and abstracts (Europe PMC `TITLE_ABS:`, PubMed `[tiab]`) and found every included study in four of the
   six reviews. See `search-scope-2026-11-12.md` for the measurement.
3. **Ozone and tfa ended with one blocking point open and the judges split.**
