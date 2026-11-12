# P1 protocol rerun on the convergence branch (2026-11-12)

The six reproduction-set entries were seeded in a fresh home and run in P1 on `b130a2a` (protocol convergence:
kernel facts, in-attempt checks, the judges' point ledger). Drivers: lead roles claude-haiku-5-5; `judge` gpt-6-luna
(codex CLI); `judge_2` deepseek-v4.1-flash (Ollama Cloud). `protocol_tries` 3, `protocol_narrow_tries` 3, `EDGE_LIMIT`
2 (three protocol attempts per run).

## Outcome

| entry | protocol | attempts | open blocking points after each vote | last vote |
|---|---|---|---|---|
| cade-colonoscopy | approved, frozen | 2 | 3 → 0 | both approve |
| ai-prostate-mri | failed at the edge limit | 3 | 2 → 2 → 1 | judge revise, judge_2 approve |
| ai-pulmonary-hypertension | failed at the edge limit | 3 | 5 → 1 → 1 | judge revise, judge_2 approve |
| fructose-rats-sbp | failed at the edge limit | 3 | 4 → 1 → 1 | judge revise, judge_2 approve |
| ozone-bleaching | failed at the edge limit | 3 | 5 → 1 → 3 | judge revise, judge_2 approve |
| tfa-heated-oils | failed at the edge limit | 3 | 4 → 3 → 1 | judge revise, judge_2 approve |

Before the branch, gpt-6-luna approved none of 51 P1 protocol votes (diagnosis, §4). Here it approved twice (cade
attempt 2, pulmonary attempt 2), and deepseek approved every last vote.

## What the ledger shows

1. **The points converge, the run does not.** 44 of the 64 points raised were resolved by the judge that raised them
   (three more, advisory, were retired at cade's freeze); most blocking points close in the next attempt. Every failed
   run ended at the edge limit with one to three blocking points open, in all five fewer than after the first vote.
2. **New blocking points in the last round.** The `judge` (gpt-6-luna) raised new blocking points in the last vote of
   three runs: prostate (extraction: lesion- versus patient-level counts), pulmonary (search: the AI terms of the
   modality query) and ozone (extraction and synthesis: split-mouth variance, pooling several ozone arms).
3. **One stuck point.** fructose `p1.judge.1` (which measurement method when a study reports systolic blood pressure by
   several methods) was marked unresolved twice with a precise reason; the Ideator's answers fixed the time point but
   not the method. No progress, so stopping there is right.
4. **A mis-typed mark cost a round.** In pulmonary attempt 2, `judge_2` approved and marked its blocking point as
   `p1.judge.2.1` instead of `p1.judge_2.1`; the point stayed open, the approve was tightened to revise, and the
   protocol (approved by `judge`) did not freeze.
5. **The Scout answers points it does not own.** Ideator-owned points collect Scout answers of the form "rebutted:
   this is the Ideator's" (fructose `p1.judge.1`: four of its six answers), which the judges then read.

## Calibration (built on branch `p1-calib`)

- **P1 earns another protocol round while it converges.** At the edge limit, before the degrade, the engine asks the
  worker for another use (`another_use`; only the protocol worker has it). `ProtocolWorker.another_use` grants one
  more `protocol->protocol` use when the attempt's vote closed at least one live blocking point (marked resolved by
  its own judge) and left no more live blocking points open than were open before it in its cycle, and the kernel
  has granted fewer than `KERNEL_EXTRA_ROUNDS` = 2 uses of that edge in the project (counted from the `edge.allowed`
  events by kernel; your grants, and those recorded before this change without `by`, do not count). The engine
  records the grant as `edge.allowed` (`by: kernel`, with the reason, e.g. "the vote closed 2 blocking points and
  left 1 open (2 before): one more protocol round") before it takes the edge as an ordinary use; the grant raises the
  edge's limit, so a step interrupted between the two takes the edge normally next time and is not granted again.
  P2 is unchanged (`stage_blocked`, never a kernel grant). On this rerun it would have given prostate, pulmonary and
  tfa another round and stopped fructose (no blocking point closed) and ozone (more open than before).
- **A mark's id is matched against the judge's own points ignoring separators** (`marked_point`: `p1.judge.2.1` is
  `p1.judge_2.1` when exactly one own point matches), in the ledger and when an approve is tightened; a mark matching
  no point, or more than one, is ignored and listed (`unmatched_marks`, per vote); the vote keeps the mark as given.
- **An author answers only the points it owns**: answers to a point of the ledger owned by the other author are
  dropped before they are stored, in a draft try and in a narrowing (`_owned`); the unanswered-point check is
  unchanged.
