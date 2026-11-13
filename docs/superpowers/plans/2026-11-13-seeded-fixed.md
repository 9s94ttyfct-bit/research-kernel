# A reproduction's seeded stages stay fixed — implementation plan

**Goal:** in a project seeded from the reproduction set (Plan 5c Deviation 4: the published review's question frozen as the RQ; gaps, topics and question recorded passed by the seed, "discovery did not run"), no back edge reopens a seeded stage. A main claim that does not answer the fixed RQ goes back to synthesize, never to question.

**Why (evidence, the controller's run notes; `docs/eval/seeded-fixed-2026-11-13.md` is written in Task 3):** srrepro7 cade and srrepro8 cade, pulmonary and tfa all ended the same way:
1. the P1 RQ vote said the main claim does not answer the RQ;
2. so argue->question;
3. the question stage ran its novelty check and found published reviews, in tfa and pulmonary the reproduced review itself ("already done");
4. so question->topics->gaps;
5. gaps failed ("no representative work has a full text for the limitation miner").

A reproduction's question is fixed by design; re-running discovery on it measures nothing, and it pulled the reproduced review into the library.

**Branch:** `seeded-fixed` from `main` (after search-screen).

## Global constraints
- `rk.orchestrator` and `rk.production` never import `rk.eval`. The seed tells the kernel which stages are fixed through the project's own events or state: the seeding writes it, and the orchestrator and argue read it.
- P2 (collab) is unchanged: the researcher decides. This plan is about P1, and the engine guard applies to both modes only where a seeded stage would be reopened, since a seeded project has no discovery to rerun in either mode.
- Projects that are not seeded: behaviour byte-for-byte unchanged.
- Docstrings in prose: "Calibration (seeded reproduction, 2026-11-13): …". Update the docstrings of `rk.eval.sr_repro`, `rk.orchestrator.engine` (the back-edge section) and `rk.production.argue` (the RQ vote).
- TDD on behaviour.

## Task 1: the seed records its fixed stages; the engine never reopens one
- **Files:** `src/rk/eval/sr_repro.py` (`seed_project`), `src/rk/orchestrator/state.py` or `engine.py` (`_edge` / `_use_edge`), tests (`tests/test_sr_repro.py`, `tests/test_orchestrator.py` or wherever back edges are tested).
- **Change:**
  - Seeding records the stages it passed (gaps, topics, question) as fixed, through an event the orchestrator reads, e.g. `stages.fixed` with `{stages, reason}`. Choose the simplest event the store already supports; it must be idempotent on reseed and resume.
  - The engine's `_edge`: when `to` is a fixed stage, the edge is not taken and the run fails with the reason "<to> is fixed by the reproduction seed (its question is the published review's): <the failures>". This covers `stage == to` reruns of a fixed stage too.
- **Tests:**
  - a seeded project whose argue (or protocol) asks for a back edge to question fails the run with that reason, and question's attempt count does not grow;
  - an unseeded project takes the same edge as before.

## Task 2: argue's RQ vote goes back to synthesize in a seeded project
- **Files:** `src/rk/production/argue.py` (`_rq_vote`, the helper near line 387 that tells whether argue->synthesize has a use left), tests.
- **Change:**
  - In a project whose question is fixed (Task 1's event), a NOT_ANSWERS decision returns `back_edge` to `synthesize`, while that edge has a use left. The reason is "the main claim does not answer the fixed research question (P1 vote, not reviewed by a human): <judges' reasons>", and it is the guidance for synthesize.
  - With no use left, the stage fails with the reason "the main claim never answered the fixed research question; argue->synthesize has no use left: <reasons>".
  - Unseeded projects keep `back_edge` to question.
- **Tests:**
  - seeded: NOT_ANSWERS → back edge to synthesize with the reason;
  - seeded with the edge spent → failed;
  - unseeded: back edge to question, unchanged.

## Task 3: docs
README (the reproduction set and the back edges), the module docstrings, and an evidence note in `docs/eval/` that cites the four runs (the controller supplies the numbers; the implementer writes the note from the facts in this plan's "Why").

After all tasks: review, then the full suite, a path scan, a merge and a push. srrepro9's service is restarted on the merged code; its runs resume.
