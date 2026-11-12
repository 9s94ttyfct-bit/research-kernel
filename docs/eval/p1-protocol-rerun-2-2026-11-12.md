# P1 protocol rerun 2, on calibration 1 (2026-11-12)

The six reproduction-set entries were seeded in a fresh home and run in P1 on `2a22693` (protocol convergence plus
calibration 1: kernel-granted protocol rounds while converging, separator-insensitive marks, owner-only answers).
Drivers and limits as in `p1-protocol-rerun-2026-11-12.md`.

## Outcome

| entry | protocol | attempts | kernel grants | votes | open blocking points after each vote |
|---|---|---|---|---|---|
| tfa-heated-oils | approved, frozen | 3 | 0 | 3 | 2 → 1 → 0 |
| cade-colonoscopy | failed at the edge limit | 3 | 0 | 3 | 2 → 1 → 1 |
| fructose-rats-sbp | failed at the edge limit | 5 | 2 | 3 | 4 → 1 → 1 |
| ozone-bleaching | failed at the edge limit | 5 | 2 | 5 | 4 → 2 → 1 → 1 → 1 |
| ai-prostate-mri | failed at the edge limit | 3 | 0 | 1 | 7 |
| ai-pulmonary-hypertension | failed at the edge limit | 3 | 0 | 1 | 4 |

The kernel's extra rounds were used where the rule allows (fructose, ozone); neither run converged in them.

## What failed

1. **The record-cap probe stopped six of 22 attempts before the vote.** In P1 a best version whose probe total is
   above the record cap is rerun without a vote. The total is an upper bound: every query's count on every source,
   summed, so a record that two queries or two sources find counts twice. The largest single query-on-source count
   is a lower bound on the deduplicated records. In all six stopped attempts the lower bound was under the cap:

   | entry, attempt | version | total (upper) | largest single count (lower) | cap |
   |---|---|---|---|---|
   | pulmonary 1 | 5 | 3969 | 1485 | 3000 |
   | pulmonary 2 | 9 | 3360 | 1429 | 3000 |
   | prostate 2 | 7 | 5274 | 1441 | 3000 |
   | prostate 3 | 8 | 4082 | 1212 | 3000 |
   | fructose 2 | 7 | 3115 | 1026 | 3000 |
   | fructose 5 | 19 | 5137 | 2265 | 3000 |

   None of them was shown to be above the cap; search_screen's deduplicated count, the authoritative check, never ran.
   Prostate and pulmonary each reached only one vote in three attempts.
2. **An approve was tightened for a point its judge did not mark.** In cade attempts 2 and 3 both judges approved;
   `judge` marked `p1.judge.1` resolved and left `p1.judge.2` (query terminology variants) unmarked. The Scout had
   answered `p1.judge.2` "changed" in both attempts. The kernel counted the unmarked point open, tightened the approve
   to revise, and the run failed with both judges approving.
3. **Points still arrive late.** Ozone's last vote raised a new blocking synthesis point (how an ozone plus hydrogen
   peroxide arm enters the sensitivity analysis) after four votes; fructose's last two votes disagreed.
4. **A redraft can start far above the cap.** The first strategy of an attempt was 18105 (prostate attempt 2, version
   4) and 19699 (fructose attempt 5, version 16) after earlier versions had been narrowed under 6000; the narrowing
   tries then had to bring them back.

## Calibration 2 (branch `p1-calib2`)

- **P1 skips the vote only when the probe shows the cap is exceeded:** the probe stores `lower`, the largest single
  query-on-source count, nearly always a lower bound (a probe stored before has it computed from its counts), and P1
  reruns without a vote only when `lower` is above the cap, with `lower`, the total and the cap in the reason. Between
  the bounds the version is voted on; the judges' probe input and the rendered probe state both bounds and that
  search_screen's deduplicated count is the cap that holds (above it, `search_screen->protocol`). The narrowing still
  narrows while the total is above the cap; its best version is one whose `lower` is within the cap when there is
  one, then the lowest total. P2 and the cap itself, which only the researcher sets, are unchanged.
- **A judge that approves and leaves some of its own live blocking points unmarked is asked once more, in the same
  vote and on its own driver, to mark exactly those points** (resolved or not, with why; `MARKS_TASK`, answered as
  `PointMarksOut`). Its marks on those points join its opinion's `resolved` (marks on other ids are ignored) and the
  opinion records them as `repaired`; they then apply as usual: a point still unmarked or marked `resolved: false`,
  and a new blocking point, tighten the approve as before. A failed follow-up leaves the opinion as it was. The
  follow-up is recorded with the vote, so a resumed vote reuses it and never asks again. Points close only by their
  judge's explicit mark; other votes (screening, final review, audit, argue, design, synthesis) never ask.
