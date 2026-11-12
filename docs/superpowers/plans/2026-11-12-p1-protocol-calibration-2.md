# P1 protocol calibration 2 — implementation plan

**Goal:** act on `docs/eval/p1-protocol-rerun-2-2026-11-12.md`: P1 stops voting only on a protocol the probe shows to
be above the cap, and a judge's omitted marks are repaired by asking it, never inferred.

**Branch:** `p1-calib2` from `main` `2a22693`.

## Global constraints

- `record_cap` is unchanged and only the researcher sets it. search_screen's deduplicated count stays the hard cap.
- P2 unchanged (it never skipped the vote for the cap; it opens `protocol_approval` with the probe).
- A point closes only by its own judge's explicit mark. No inferred closure.
- Event-derived and resume-safe: a repaired mark is stored with the vote, so a resumed vote (`reuse=True`) reuses it and
  never asks again.
- Other votes (screening, final review, audit, argue, design, synthesis) unchanged.
- TDD; tests test behaviour; docstrings in prose with "Calibration (P1 protocol rerun 2, 2026-11-12): …".

## Task 1: the probe's lower bound decides the P1 vote

**Files:** `src/rk/production/protocol.py` (`_over_cap`, the run's branch after narrowing, `probe_lines`,
`PROBE_NOTE`, the judges' `record_probe` input, `KERNEL_RULES` if it states the cap rule), tests.

**Rule:** the probe gains `lower` (the largest single query-on-source count; `None` when no count is known). The
narrowing loop is unchanged (it narrows while `total` is above the cap, up to `protocol_narrow_tries`). After it, P1
reruns without a vote only when `lower` is above the cap (the reason names `lower`, `total` and the cap); otherwise it
votes. The judges' probe input and the rendered probe lines state both bounds and that search_screen's deduplicated
count is the cap that holds. A probe stored before this change (no `lower`) gets it computed from its `counts`.

**Tests:** total above / lower under the cap → vote in P1; lower above the cap → rerun without a vote (reason names the
bounds); P2 with total above the cap → `protocol_approval` as today; an old stored probe without `lower` works.

## Task 2: a judge's omitted marks are asked for once

**Files:** `src/rk/production/vote.py` (a per-judge follow-up hook, opt-in like `output`/`per_judge`/`tighten`),
`src/rk/production/protocol.py` (the follow-up question and when to ask), tests in `tests/test_protocol_ledger.py`.

**Rule:** in a protocol vote, when a judge's answer is `approve` and some of its own live blocking points (earlier
ones in the current cycle) are neither in its `resolved` marks nor reopened, the vote asks that judge once more (same
driver, a short task: the version, those point ids with fault/change/answers, "mark each resolved true or false with
why"; output: a list of `PointMark`). The returned marks for those ids are merged into the opinion's `resolved`
(other ids ignored), and the opinion records `repaired: [ids]`. Tightening then applies as before. A failed follow-up
leaves the opinion as it was. At most one follow-up per judge per vote.

**Tests:** cade shape (approve, own point answered by the Scout, unmarked) → one follow-up; it marks resolved → the
protocol freezes; it marks unresolved → tightened to revise; a judge that marks every own point gets no follow-up; a
revise gets no follow-up; the follow-up's marks for other ids are ignored; resume reuses the recorded vote without a
second follow-up; non-protocol votes never ask.

## Task 3: docs

README protocol paragraph, the protocol module docstring and the rerun-2 doc's "Calibration 2" section match the
code.

After all tasks: review, full suite on the final commit, path scan, fast-forward `main`, push; a fresh home, the six
entries again.
