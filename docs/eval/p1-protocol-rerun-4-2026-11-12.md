# P1 rerun 4, on calibration 2 (2026-11-12)

The six reproduction-set entries were seeded in a fresh home (outside the system temporary directory, which a restart
had emptied) and run in P1 on `7753799`. Unlike reruns 1–3, every OpenAI role (`judge`, `screener_b`, `skeptic`,
`simreader`) ran gpt-6-luna through the omp gateway instead of the Codex CLI, so this run has no Codex CLI call cap;
timing and rate-cap behaviour are not comparable with the earlier reruns. The protocol stage of all six took about
eight minutes.

## Protocol

| entry | protocol | attempts | kernel rounds | follow-ups | votes skipped for the cap | open blocking points after each vote |
|---|---|---|---|---|---|---|
| ai-pulmonary-hypertension | approved, frozen (later reopened, see below) | 5 | 2 | 1 | 1 | 3 → 1 → 1 → 0 |
| cade-colonoscopy | approved, frozen | 4 | 1 | 0 | 0 | 2 → 2 → 1 → 0 |
| tfa-heated-oils | approved, frozen | 5 | 2 | 0 | 0 | 4 → 1 → 1 → 1 → 0 |
| fructose-rats-sbp | failed at the edge limit | 4 | 1 | 0 | 0 | 3 → 3 → 1 → 1 |
| ozone-bleaching | failed at the edge limit | 3 | 0 | 0 | 0 | 4 → 2 → 2 |
| ai-prostate-mri | failed at the edge limit | 3 | 0 | 0 | 0 | 5 → 1 → 2 |

Votes skipped for the cap are counted from the protocol attempts whose reason starts "the record-cap probe of version".

52 of the 68 points were resolved by the judge that raised them. The first judge follow-up for omitted marks ran
(pulmonary).

- **Ozone:** `judge_2` blocked on one point in all three votes (`p1.judge_2.3`), and `judge` blocked on the same
  objection in attempts 2 and 3 through its reason-only point `p2.judge.1`: the PRISMA-P items have no title
  identifying the protocol as a systematic review and no contributor list. The Ideator cannot know either and
  rebutted; no blocking point closed in the last attempt, so the kernel granted no round.
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

Built on `p1-calib3` (plan `docs/superpowers/plans/2026-11-12-p1-protocol-calibration-3.md`, Tasks 1–3):

- **Title and contributors are the kernel's** (the ozone block, PRISMA-P items 1 and 3). For a systematic or scoping
  review the kernel writes the PRISMA-P `title` ("Protocol for a systematic review: <the frozen question>", or "a
  scoping review") and `contributors`: the kernel's agent roles drafted it (the Ideator the eligibility, extraction and
  synthesis, the Scout the search strategy); in P1 it is frozen only when every judge role the vote admits approves
  it (at least two, of different vendors, none the Ideator's or the Scout's) and no human author is recorded, in P2
  only when the researcher running the project approves it; no corresponding author or contact address is recorded.
  A best version drafted before the production mode changed (the attempt resumed in place) goes to the vote or the
  approval as a copy with the current mode's kernel items, added once. Like registration, amendments, support and competing
  interests, they replace whatever the Ideator wrote under those keys, are not checked as its text, change nothing
  when applied again, and are absent from a critical review or proposal. The rendered protocol (the judges' subject
  and your `protocol_approval`) opens with the title; the P2 card already shows every PRISMA-P item; the judges' rules
  sheet and the Ideator's task name both as kernel-written.
- **A judge votes on its own points** (the fructose block). The vote question and the judge's role text say that
  `open_points` lists every judge's open points, each with its owning judge in `role` (its own are those whose `role`
  is its `you`); another judge's points are theirs to mark and close, and while a blocking one is open that judge's
  own vote keeps the protocol from freezing, so a judge answers revise only for a blocking point of its own, still
  open or new, never because another judge's point is open. This is prompt wording; a test pins the inputs it relies
  on (each judge sees both judges' open points with their owners).

Not yet measured: a rerun on this branch.
