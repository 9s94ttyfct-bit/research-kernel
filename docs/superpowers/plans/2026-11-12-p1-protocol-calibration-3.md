# P1 protocol calibration 3 — PRISMA-P identification and contributors; judges vote on their own points

**Goal:** the kernel writes the PRISMA-P identification (title) and contributors items of a systematic or scoping
review, so judges stop blocking on items the Ideator cannot know; and a judge no longer blocks because another
judge's point is open.

**Branch:** `p1-calib3` from `search-scope` `850284c` (merged into `main` before this branch is).

**Evidence** (rerun on `7753799`, `docs/eval/p1-protocol-rerun-4-2026-11-12.md`): in ozone-bleaching both judges
blocked three votes on `p1.judge_2.3` "no title identifying the protocol as a systematic review and no
author/contributor list". The Ideator answered "changed", then "rebutted: a protocol title and contributor list are
records of the project, not fields this draft sets"; `judge` raised the same objection as a reason-only revise
(`p2.judge.1`). No blocking point closed in attempt 3, so no kernel round; the run failed at the edge limit. This is
the remainder of the diagnosis's R1a (PRISMA-P items 1 identification and 3 authors/contact were not built).

## Global constraints

- Kernel items state only what the project's records establish (no invented people, affiliations or contact).
- Like the existing kernel items: skipped by the Ideator's text checks, replace what the Ideator wrote under those
  keys, applied again change nothing, only for a systematic or scoping review.
- P2 card and the rendered protocol show them; the judges' rules sheet names them as the kernel's.
- TDD; behaviour tests; docstrings "Calibration (P1 protocol rerun 4, 2026-11-12): …".

## Task 1: title and contributors are kernel items

**Files:** `src/rk/production/protocol.py` (`KERNEL_PRISMA_P` / `kernel_prisma_p`, `with_kernel_facts` gains the
production mode, its caller, `render_protocol` opens with the title, `KERNEL_RULES`), `src/rk/ui/static/app.js` only if
the card does not already show every PRISMA-P item, tests.

**Texts:**
- `title`: "Protocol for a systematic review: <the research question as frozen>" (scoping: "a scoping review").
- `contributors`: "Drafted by the research kernel's agent roles (the Ideator wrote the eligibility, extraction and
  synthesis; the Scout wrote the search strategy). " + P1: "It is frozen only when the kernel's two judge roles, of
  different vendors, approve it; no human author is recorded in this project." / P2: "It is frozen only when the
  researcher running the project approves it." + "No corresponding author or contact address is recorded in this
  project."

**Tests:** an SR draft gets both items with the frozen question in the title; the Ideator's own `title` is replaced;
P1 and P2 contributor texts differ as above; `render_protocol`'s first line is the title for an SR; a critical review
gets neither; applied twice changes nothing.

## Task 2: a judge votes on its own points only

**Evidence** (same rerun, fructose-rats-sbp): in attempt 3 `judge` found its own points resolved but answered
revise because "the open blocking search point owned by judge_2 remains unmarked, so … the protocol cannot be
approved while that blocking point remains open". Its reason became the blocking point `p3.judge.1`; the Ideator
answered that it cannot edit the Scout's search; in attempt 4 `judge` marked `p3.judge.1` unresolved for the same
reason and the run failed at the edge limit. Each judge's points are closed only by that judge, and the vote is
already unanimous, so another judge's open point never needs a second judge to block.

**Rule:** `VOTE_QUESTION` (and `judge.md` if it describes the protocol vote) says: another judge's points are theirs
to close; do not answer revise because another judge's point is open; answer revise only for a blocking point of
your own (new or still open) or what you find wrong yourself. The judges' input keeps showing every open point (for
context) but marks each point's `role` as the judge that owns it, and the question names the judge's own ids
(`you`).

**Tests:** none for wording (prompt calibration); a test that the judge's inputs list its own open point ids under
`you` if that is not already covered.

## Task 3: docs

README protocol paragraph, protocol module docstring, the rerun-4 doc's last section ("Calibration 3") saying what
was built.

After all tasks: review, full suite on the final commit, path scan, fast-forward `main`, push.
