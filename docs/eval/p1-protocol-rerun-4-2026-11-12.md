# P1 rerun 4, on calibration 2 (2026-11-12)

The six reproduction-set entries were seeded in a fresh home (outside the system temporary directory, which a restart
had emptied) and run in P1 on `7753799`. Unlike reruns 1–3, every OpenAI role (`judge`, `screener_b`, `skeptic`,
`simreader`) ran gpt-6-luna through the omp gateway instead of the Codex CLI, so this run has no Codex CLI call cap;
timing and rate-cap behaviour are not comparable with the earlier reruns. The protocol stage of all six took about
eight minutes.

## Protocol

| entry | protocol | attempts | kernel rounds | follow-ups | votes skipped for the cap | open blocking points after each vote |
|---|---|---|---|---|---|---|
| ai-pulmonary-hypertension | approved, frozen (later reopened, see below) | 5 | 2 | 1 | 2 | 3 → 1 → 1 → 0 |
| cade-colonoscopy | approved, frozen | 4 | 1 | 0 | 0 | 2 → 2 → 1 → 0 |
| tfa-heated-oils | approved, frozen | 5 | 2 | 0 | 1 | 4 → 1 → 1 → 1 → 0 |
| fructose-rats-sbp | failed at the edge limit | 4 | 1 | 0 | 3 | 3 → 3 → 1 → 1 |
| ozone-bleaching | failed at the edge limit | 3 | 0 | 0 | 0 | 4 → 2 → 2 |
| ai-prostate-mri | failed at the edge limit | 3 | 0 | 0 | 1 | 5 → 1 → 2 |

52 of the 68 points were resolved by the judge that raised them. The first judge follow-up for omitted marks ran
(pulmonary).

- **Ozone:** both judges blocked on one point for three votes: the PRISMA-P items have no title identifying the
  protocol as a systematic review and no contributor list. The Ideator cannot know either and rebutted; no blocking
  point closed in the last attempt, so the kernel granted no round.
- **Fructose:** three of its four attempts were stopped by the record-cap probe before the vote.
- **Prostate:** `judge` raised a new blocking extraction point in each vote.

## Search: the cap against recall

Pulmonary's frozen strategy found at least 3153 deduplicated records in `search_screen` (the search stops once the
cap is exceeded, so the true count may be higher), above the 3000 cap, so the stage went back to `protocol`. There
`judge` asked to broaden the queries ("narrow, exact
terminology … risking omission of eligible studies"), the protocol edge had no uses left, and the run failed. The
frozen strategies of cade and tfa, narrowed to fit the cap under OpenAlex's full-text search, find 5 of 6 and 5 of 33
of the reviews' included studies (`search-scope-2026-11-12.md`). Both point at the search fields, not at the
protocol's vote.

## A judge blocking on another judge's point

Fructose-rats-sbp, attempt 3: `judge` found its own points resolved but answered revise because "the open blocking
search point owned by judge_2 remains unmarked, so … the protocol cannot be approved while that blocking point remains
open". That reason became its blocking point `p3.judge.1`. The Ideator answered that the search is the Scout's; in
attempt 4 `judge` marked `p3.judge.1` unresolved for that reason, and the run failed at the edge limit. Every point is
closed only by its own judge and the vote is unanimous, so another judge's open point already blocks the protocol
without a second judge's revise.

## Calibration 3 (branch `p1-calib3`)

To be written when it is built.
