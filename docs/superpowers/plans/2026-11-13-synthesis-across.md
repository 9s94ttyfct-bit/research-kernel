# Synthesis across studies — implementation plan

**Goal:** act on `docs/eval/synthesis-2026-11-13.md`. In srrepro9 every project that reached argue failed or stalled. The main claim was narrowed to one or a few studies, and the RQ vote said it did not answer the question. Causes:
- **Findings outran their citations.** Cross-study synthesis findings cite a median of 3 studies (0.6–12.5% of the included ones), and 5 of 63 were V-SUPPORT supported.
- **Judges could not check breadth.** The V-SUPPORT judges see bare passages, without study or arm identity.
- **No cross-study count for systematic reviews.** The kernel computes none, although the extraction rows give one: fructose 79 of 81 higher, prostate sensitivity 4/13/1, pulmonary AUC 6/1/1.
- **The pooling texts never say what random effects are for.** With a pre-specified random-effects meta-analysis on 45 computable fructose studies, the Lead declared "not comparable" on clinical diversity, and the vote split on the kernel's "not assessed by the planner" note.

**Branch:** `synthesis-across` from `main` `f52e28e` (worktree `../rk-synth`).

## Global constraints
- Two-vendor judging and V-SUPPORT's rule (both judges must say supported) are unchanged. The judges get better evidence, not a looser rule.
- No example from the six evaluation topics in any prompt text. The existing topic-word test covers the texts this plan touches; extend it to them.
- Every kernel-computed number is verified deterministically (as `stat_result` is through V-STATS/V-NUM), never by a model.
- TDD on behaviour: the built request a role or judge receives, the artifact computed from extraction rows, the claim and its verification. No network.
- Docstrings in prose: "Calibration (synthesis across studies, 2026-11-13): …". Update the module docstrings of `rk.production.synthesis`, `rk.verify.support` and `rk.production.argue` where they describe the changed behaviour.
- P2 behaviour changes only where a text the researcher sees describes the same thing.

## Task 1: pooling — clinical diversity is the random-effects model's job
- **Files:** `src/rk/production/synthesis.py`: PLAN_TASK, VOTE_QUESTION, and the kernel's note on studies it added to a group (around line 994).
- **PLAN_TASK and VOTE_QUESTION**, when the protocol pre-specifies a random-effects model: "Differences in dose, route, population, strain, duration or setting are heterogeneity: the pre-specified random-effects model, its heterogeneity statistics and the pre-specified subgroup or sensitivity analyses address them. Judge studies not comparable, or answer no_pool, only when they do not measure the same outcome for the same comparison, or report it on a scale that cannot be converted."
- **Kernel note:** reword it so a study the kernel added because it has a computable effect does not read as unassessed. For example: "the kernel added these studies because their extraction gives the effect measure's fields; they report the same outcome and comparison as the group".
- **Tests:**
  - the built plan request and the built pool-vote request carry the rule (constants in the request) when the model is random-effects;
  - with a fixed-effect model, the rule is absent;
  - the vote subject's note on added studies no longer says "not assessed".

## Task 2: V-SUPPORT judges see each passage's study
- **Files:** `src/rk/verify/support.py` (`_evidence_notes`, `evidence_texts`, the document given to the judges), and the judge prompt if it describes the evidence document.
- **Change:**
  - Group the evidence passages by study.
  - Head each group with the study's label, title, year and design.
  - Where the extraction has them, add the study's arm or group names: the fields the protocol's extraction schema marks as arms, or the intervention and comparator descriptors. Choose the simplest reliable source and report it.
  - The passages themselves are unchanged, and V-NUM is unchanged.
- **Tests:**
  - the judge request for a claim citing notes from two studies shows two study headings, each with its passages;
  - a claim's verdict rule is unchanged.

## Task 3: the narrative contract
- **Files:** `src/rk/production/synthesis.py` (NARRATIVE_TASK, the narrative input for large k), `src/rk/roles/arguer.md` (the count ban), `src/rk/production/argue.py` (the back-edge guidance of the RQ vote; the synthesis-request vote's subject).
- **Change:**
  - **Citation rule.** In NARRATIVE_TASK: "A finding about several studies cites the notes of every study it counts, or is worded for the studies it cites ('in the three studies cited, …'). A count of studies is allowed when it counts the studies whose notes the finding cites. Numbers the kernel computed (pooled estimates, the direction counts of Task 4) are still never restated. Statements about the evidence itself (sparse, not pooled, heterogeneous, not established) go in `scope_limits`, not in the finding's text."
  - **Arguer.** Change arguer.md the same way: it may say how many cited studies a claim rests on, and kernel-computed counts it cites as the kernel's claim.
  - **Large k.** Give the Lead the studies in per-outcome subsets instead of every study's notes at once: the studies with the outcome's extracted fields first. Or cap the notes per study, reporting which and why. Fructose's input was about 880k characters. Choose the simpler correct way, keep every study listed by label, and say in the input what was left out.
  - **RQ back edge.** In a seeded project it goes back to synthesize. Its guidance now also carries the Arguer's open `needs_synthesis` requests, not only the vote's reason.
  - **Synthesis-request vote.** Its subject shows, per requested field or outcome, how many included studies have usable extracted values.
- **Tests:**
  - the narrative request carries the rule;
  - a large-k input is split or capped as specified, with every study's label present;
  - the RQ back edge's guidance contains the Arguer's request text;
  - the synthesis-request vote subject contains the field counts.

## Task 4: kernel effect-direction counts for systematic reviews
- **Files:** `src/rk/production/synthesis.py` (next to the scoping evidence map, synthesis.py:1117-1164), `src/rk/production/models.py` (OutcomeSpec), `src/rk/production/protocol.py` (the synthesis plan's outcome mapping and its check), the verification of kernel claims (follow the `stat_result` and V-STATS path), argue's inputs.
- **Change:**
  - **Field pair per outcome.** For every pre-specified outcome of a systematic review, the protocol maps a field pair: the intervention or index value and the comparator value. Reuse `OutcomeSpec.fields` roles where they exist (MD and SMD means; RR, OR and RD events/n, giving risks), and add a generic pair (`intervention`, `comparator`) for any other outcome, such as diagnostic accuracy (sensitivity_ai, sensitivity_radiologist).
    - The Ideator is told to map every outcome, meta-analysis or not.
    - The protocol check requires the pair to name fields of the extraction schema.
    - A protocol frozen before this change, with no pair, gets no direction count; it is not refused.
  - **Counting.** From read_extract's passed rows (usable numeric values, both fields present in the same study), the kernel counts per outcome the studies where intervention > comparator, < comparator, and =.
    - "Equal" is exact equality of the extracted values.
    - It stores a `direction` artifact (the table, per study: label, the two values, the direction).
    - It writes one kernel claim per outcome: "Of the k studies with extracted values for <outcome>, <a> report a higher value with <intervention> than with <comparator>, <b> a lower value and <c> the same value." The claim's evidence is the artifact, and its numbers are verified against the artifact deterministically.
    - Not computed when fewer than `min_studies` have both fields.
  - **Use.** The narrative input and argue get the direction claims as kernel findings (like `stat_result`). The Arguer may build the main claim on them. The text says this is vote counting by direction of effect (SWiM), not a pooled estimate, and gives no significance.
  - **Limitations.** Units can differ across studies; the direction within a study is still defined, so the claim states only directions.
- **Tests:**
  - the artifact and the claim computed from extraction rows (a mix of usable, not_reported and missing values);
  - the deterministic verification passes for the stored claim and fails for an altered count;
  - argue's input carries the claim;
  - a protocol outcome without a pair gets no count;
  - the protocol check refuses a pair naming a field absent from the schema.

## Task 5: docs
- README's synthesis and argue paragraphs.
- The module docstrings.
- A "Decision" section at the end of `docs/eval/synthesis-2026-11-13.md`: what was done, and what was not done and why (the V-SUPPORT rule unchanged; no DTA bivariate pooling).

After all tasks: review, then the full suite, a path scan, a merge and a push. Then a fresh home, and a measurement of the synthesize and argue outcomes per entry.
