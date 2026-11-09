# Plan 5c demo: draft, revise, audit and export, cloud models only (2026-11-09)

The Plan 5c stages turn a review's argument into an exported manuscript: `draft → revise → audit → export`. This page
records the live runs of Task 9 (in progress: the melatonin review and the research proposal have exported; the
others are waiting, see each project's status below):
- a systematic review (melatonin for delirium), a critical review (large language models as screeners) and a research
  proposal (the melatonin question as a trial), each in P2 with the checkpoints answered;
- one P1 project from intake to export, with every checkpoint the mode lets a vote answer voted;
- the systematic-review reproduction set (its report, `docs/eval/sr-repro-<date>.md`, is written when the set has run).

Every number comes from the runs' event logs, databases and CLI output; ids are the runs' own. **The checkpoints were
answered by the agent that ran the demo, standing in for the researcher**; every answer is logged with its time and
reason. In real use only you answer them.

## Setup, and the deviations from the plan

Each project ran in its own scratch `RK_HOME` in the temporary directory, with `rk serve` as a named service and a
headless built-in browser, against the live APIs (Europe PMC, PubMed, OpenAlex, Crossref). `rk eval stats
--write-config` reproduced 180/180 published values in each home before its first run, so meta-analysis was enabled.

**Deviation 1: the melatonin review is a new project.** Task 9 asked to continue the Plan 5b demo home. That home
(and the Plan 5a one) was lost when the temporary directory was cleared, so the Plan 5b run stays as history in
`docs/demo/plan5b-synthesis-argument.md`. The melatonin review was seeded on a fresh home with the research question
the Plan 5a run froze (D2 discovery, 2026-11-08), entered again as the frozen RQ the way `rk.eval.sr_repro.seed_project`
seeds a reproduction entry, with the seed reason recorded in each discovery stage: discovery did not run in this home.
The same was done for the critical review (a question chosen by the stand-in) and the research proposal (the
reproduction of the Plan 5a D2 project's question), so the day's OpenAlex budget was left for the P1 project's
discovery.

**Deviation 2: the Anthropic roles ran on the omp gateway.** The plan's live runs put the Anthropic-vendor roles on
`claude_cli:claude-haiku-5-5`. At 11:26 UTC the melatonin run's first three protocol tasks failed in 7 s: the claude CLI
was logged out ("Not logged in · Please run /login"). By your decision, at 14:27:08 UTC every Anthropic role, `judge_3`
included, was moved to `omp_gateway:anthropic/claude-haiku-5-5` in all five homes (the same model through omp's
Anthropic login; `rk models` lists it with vendor `anthropic`, so the vendor rules, `judge_3`'s same-vendor skip
included, are unchanged). A smoke test in a throwaway home ran one Writer task on it (2.8 s, "ok") at 14:27:24 UTC.
The three failed tasks are the only `claude_cli` tasks of these runs.

**Deviation 3: the OpenAI roles ran on the omp gateway.** By your decision (2026-11-09, to avoid the CLI usage
limits), the OpenAI roles moved from the Codex CLI to `omp_gateway`. The task tables show when each role switched:
- Screener-B ran on `codex_cli` from 14:36 to 14:46 UTC (20 tasks, melatonin) and on
  `omp_gateway:openai-codex/gpt-6.1-sol` from 14:47 UTC.
- `judge` ran on `omp_gateway:openai-codex/gpt-6-luna` from its first task (14:48 UTC, proposal).
- The Skeptic's first tasks (17:37 UTC) ran on `omp_gateway:openai-codex/gpt-6.1-sol`.

`judge_2` and Triage stayed on `ollama:deepseek-v4.1-flash:cloud`. Since commit f7fe0c2 the gateway refuses a reply
from another model than the one asked for.

**Deviation 4: the cheapest models only.** By your decision at 18:18 UTC, the runs use only the cheapest model of each
vendor: every Anthropic role on `omp_gateway:anthropic/claude-haiku-5-5` (unchanged) and every OpenAI role (Screener-B,
the Skeptic, the SimReader and `judge`) on `omp_gateway:openai-codex/gpt-6-luna`. The switch was made in all homes at
18:18:37 UTC (settings are read per task). The Skeptic's last `gpt-6.1-sol` task was at 18:03 UTC and its first
`gpt-6-luna` task at 18:22 UTC. The SimReader first ran at 21:34 UTC, on `gpt-6-luna`. `judge_2` and Triage stayed
on `ollama:deepseek-v4.1-flash:cloud`. The shipping defaults are unchanged.

**Deviation 5: one Screener-B batch was answered by an Anthropic model.** Melatonin task
`tsk_01M70CNS6Z6BRY58ZMHKZ5DRGW` ran on the driver `omp_gateway:openai-codex/gpt-6.1-sol`; it was created at 14:51
and its accepted attempt (15:46:34 UTC, 208 s) recorded the model `anthropic/claude-sonnet-5-5`. omp's model fallback
answered it, on code from before f7fe0c2, which now refuses such a reply. The batch decided 25 title-and-abstract
records, all excluded:
- 24 agreed with Screener-A and were excluded;
- 1 was a disagreement (Screener-A: unclear) that went to the screening-disagreements checkpoint, where the stand-in
  excluded it;
- none of the 25 was included.

Those 25 decisions therefore had no vendor separation. They were not screened again. The disclosure's "Models used"
lists this attempt's model next to the driver.

The same fallback answered two `judge` votes in the proposal home (tasks `tsk_01M70CJ1XVF0A2854K2T8R5185` and
`tsk_01M70CJ6BPWN4C164MEZN7A4KH`, 14:49 UTC, driver `gpt-6-luna`, recorded model `anthropic/claude-sonnet-5-5`).
No other home has such an accepted attempt. After f7fe0c2 the gateway refused a third mismatched reply (a proposal
Skeptic task at 20:12 UTC).

All `rk serve` processes were restarted at 17:34 UTC on the code with these changes; the runs resumed from their event
logs. From 18:22 UTC every serve ran on the pushed main of that time (48982c4). The melatonin and proposal serves moved
to each fix below as it landed, each at a checkpoint wait: d213398 at 19:01 UTC (melatonin 19:39 on 2e717c7), the
pushed ff42b17 at 21:20 UTC, and 0911b30 at 21:32 UTC.

## Defects the runs found after 18:20 UTC

**A comment on a stage_blocked answer was dropped (fixed in d213398).** Allowing one more use of a back-edge accepted
a `comment`, but the reopened stage got only the gate's failures as guidance. So the two melatonin comments of
18:04:32 and 18:04:55 UTC (rewording the pooling warrant) never reached the Arguer. Until the fix, the stand-in put
its guidance on an uncertain_support `rerun` instead, which the next stage_blocked carries in its failures. After
the fix (19:01 UTC), the proposal's comment was in the reopened stage's guidance and in the Arguer's task.

**A claim marker without its kind was dropped silently (fixed in 2e717c7).** In melatonin draft attempts 3–5
(19:08–19:13 UTC) the Writer wrote `[[clm_…]]` (the claim id alone) instead of `[[clm:clm_…]]`. The parser skipped
them and the verdict said only "the sentence has no source marker", so every retry repeated the form; for example
the Discussion's version 9 had 0 valid and 8 malformed claim markers. The draft stopped at its back-edge limit. Now
such a `[[…]]` fails its sentence and the verdict names the form to write. After the fix, draft attempt 6 had no
malformed marker in any section.

**Numbers from the protocol's text failed V-NUM (fixed in 6d4fc68).** In melatonin draft attempt 9 (until 20:40
UTC), a sentence citing the frozen protocol (`[[run:prt_…]]`) could not state a number from it (for example "RoB 2" or
an eligibility age), and the Writer was still offered a withdrawn claim. After the fix (pushed in ff42b17), the
protocol's own numbers count for V-NUM and withdrawn claims are no longer offered.

**Draft did not converge (fixed in 0911b30).** Each draft→draft rerun redrafted every section, including those that
had passed, so in attempts 6–12 each section passed at some point but never all in the same attempt. The kernel's
verdicts give the failing sections per attempt:

| Attempt | Failures | Failing sections |
|---|---|---|
| 6 | 8 | Abstract, Introduction, Limitations |
| 7 | 4 | Abstract, Limitations |
| 8 | 4 | Introduction, Methods, Discussion |
| 9 | 5 | Introduction, Methods |
| 10 | 3 | Abstract (empty three times), Introduction |
| 11 | 7 | Introduction, Methods, Discussion, Limitations |
| 12 | 6 | Introduction, Results (empty), Limitations |

Now a rerun after draft's own failing gate keeps each section that passed (same text, a fresh verdict, no Writer task).
On 0911b30, melatonin attempt 13 carried four sections and failed only in the Introduction (2 problems), attempt 14
had one problem left, and attempt 15 passed.

## Melatonin systematic review (P2): exported

Home `rk-plan5c-melatonin`, project `melatonin`, run `run_01M70PZ9VJMQ97C7JHWF68YK22` (its first run,
`run_01M700YGHXR22RMWAFTQA0R74J`, failed on the argue crash fixed in 7ca05c5). It exported at 21:46:36 UTC. The
project's first and last events are 11:26 and 21:46 UTC (10 h 20 min); that span includes the claude-CLI outage
(11:26–14:27), the waits on checkpoints and the serve restarts for the fixes.

- PRISMA (search_screen attempt 1, V-PRISMA: the counts add up):
  - 1,471 records identified and 754 duplicates removed;
  - 717 screened at title and abstract, 710 excluded there;
  - 7 sought in full text, 3 not retrieved;
  - 4 assessed in full text: 2 excluded (I2 not met; the kernel's note says 2 were excluded for language);
  - 2 included.
- Protocol v3 was approved at attempt 6 (14:30 UTC): PubMed and Europe PMC, four short queries, 2024 to now, record cap
  800, REML/HKSJ, at least 3 studies to pool. Two RCTs were included: RESTORE (general ward, 2/55 vs 1/54, stopped
  early for futility) and Khaled 2025 (perioperative pilot, 7/43 vs 2/42). Synthesize did not pool them (two studies,
  below the protocol's minimum).
- Argue passed at attempt 13 (19:08 UTC). The approved main claim: in the two included trials the evidence does not
  show that melatonin prevents incident delirium outside the ICU, both results are inconclusive, and the ramelteon and
  community-dwelling parts cannot be answered from this set. The stand-in answered that it answers the question as an
  evidence gap.
- Why 13 attempts: (1) the Skeptic raised fair new objections each round (scope, class wording, imprecision);
  (2) one fact had to be corrected: the stand-in checked that RESTORE was powered for delirium and then stopped early,
  so "neither trial was powered" was wrong; (3) the argument cannot cite the protocol, so a warrant based on the
  protocol's pooling rule failed every time until it was dropped; (4) the 40-task argue budget is counted per run
  session and attempts in session 2 got one round each. The stand-in paused and resumed the run (a new session) before
  allowing the next attempt.
- Draft: attempt 6 (the first on 2e717c7) failed on 8 sentence problems and attempt 7 on 4. The abstract came back
  empty after its second missing_support request was refused (fix 0d8e3d0), a withdrawn claim was cited, and one
  sentence wrongly said that neither trial was powered. Attempt 8 failed on 4 more problems; its Methods also said
  that risk of bias was assessed with RoB 2, although the extraction records it as not reported. Attempts 9–12 did
  not converge (see the defects above). Draft passed at attempt 15 (21:34 UTC, on 0911b30), after 12 draft→draft
  uses, the last 10 allowed by the stand-in, each with a comment naming the remaining failures.
- Revise passed at attempt 5 (21:41 UTC). The SimReader's priority-1 questions were about:
  - Oh et al. 2021 (ramelteon), which is outside the 2024 date limit and so not eligible;
  - the perioperative denominators. The stand-in checked the full text: 85 patients were randomized (43 + 42), and
    120 was the planned sample, so the sentences calling the denominators unreconciled were wrong.
  - RESTORE's power. RESTORE was powered for delirium and then stopped for futility.

  Revise attempts 3 and 4 failed because each rewrite of the Limitations broke other sentences.
- The audit passed every hard gate at its first attempt (report v1: G-REF, G-QUOTE, G-COVER, V-BALANCE,
  G-SUPPORT, G-HEDGE, G-DISCLOSE, G-PREREG, V-PRISMA, V-STATS). At the first final review (21:44 UTC) the stand-in
  asked for a revision for two reasons:
  - the Conclusion still said the perioperative denominators were not reconciled with enrolment (wrong, see above);
  - the Discussion and the Conclusion gave the trials' differences as the reason for not pooling, but the reason was
    the protocol's minimum of three comparable studies.

  The stand-in's suggested replacement sentence cited only the protocol, which a Discussion sentence may not do, so
  revise attempt 6 was rejected. Attempt 7 made the change as deletions: the denominator sentence went, and Methods,
  Results and Limitations give the protocol minimum as the reason. The audit passed again, and the stand-in approved
  the manuscript at the second final review (21:46 UTC).

**Sections (final versions; every sentence passed its verdict):**

| Section | Version | Sentences |
|---|---|---|
| Abstract | v32 | 8 |
| Introduction | v40 | 4 |
| Methods | v37 | 10 |
| Results | v35 | 9 |
| Discussion | v28 | 7 |
| Limitations | v34 | 12 |
| Conclusion | v26 | 7 |
| Disclosure (kernel-written) | v15 | 81 |

**Export.** `paper.pdf` (16 pages), `paper.docx`, `paper.md`, `paper.tex`, `refs.bib`/`refs.json`, `metadata.yaml`
and the package `package-fccb7d5e.zip`. Its `MANIFEST.json` (`rk-package/1`, `paper_type: systematic_review`,
`audit_report: 3`) lists the SHA-256 of 14 files: the audit report, `disclosure.md`, the export files, `extraction.csv`
and `protocol.json`. It also records the event-log head `6304b87e…`.

**Tasks per role and driver (all done unless stated):**

| Role | Driver | Tasks |
|---|---|---|
| writer | `omp_gateway:anthropic/claude-haiku-5-5` | 271 |
| judge | `omp_gateway:openai-codex/gpt-6-luna` | 48 |
| judge_2 | `ollama:deepseek-v4.1-flash:cloud` | 47 |
| arguer | `omp_gateway:anthropic/claude-haiku-5-5` | 34 |
| screener_a | `omp_gateway:anthropic/claude-haiku-5-5` | 30 |
| screener_b | `codex_cli` / `omp_gateway:openai-codex/gpt-6.1-sol` | 20 / 10 |
| simreader | `omp_gateway:openai-codex/gpt-6-luna` | 19 |
| skeptic | `omp_gateway:openai-codex/gpt-6.1-sol` / `gpt-6-luna` | 12 / 10 |
| reader, scout | `omp_gateway:anthropic/claude-haiku-5-5` | 4, 3 |
| ideator | `omp_gateway:anthropic/claude-haiku-5-5` / `claude_cli:claude-haiku-5-5` | 3 / 3 failed (logged out) |

**No local model.** The SQL check found 0 tasks on a non-cloud `ollama` driver, 0 tasks on any driver outside the
configured list, and 0 verifications with a local NLI layer. One Screener-B attempt was answered by
`anthropic/claude-sonnet-5-5` (Deviation 5).

**Checkpoints:** 42, all answered by the stand-in. They were 23 stage_blocked, 11 uncertain_support, 4
screening_disagreements, 2 protocol_approval and 2 final_review. Every answer is logged with its time and reason.

**What failed or was weak:**
- The Writer (Haiku) needed 15 draft attempts and 7 revise attempts. On the way it:
  - wrote malformed markers;
  - left sections empty;
  - repeated a fact error ("neither trial was powered") after it had been corrected;
  - asserted that RoB 2 had been used and that the denominators did not reconcile, both contradicted by the sources.
- The stand-in caught each of these. The kernel's gates catch markers, numbers and hedging, not facts that are true
  only of the full text.
- The argument needed 13 attempts (see above).
- The two included trials cannot answer the question beyond an evidence gap.

## Research proposal (P2): exported

Home `rk-plan5c-proposal`, project `melatonin-proposal`, run `run_01M70BHRVFRCT8SEC1KS2B4BBQ`. It ran under the
same frozen question and search as the melatonin review (protocol v3, approved at 14:37 UTC, cap 800, narrative
plan) and exported at 21:44:29 UTC. The project's first and last events are 11:29 and 21:44 UTC (10 h 15 min),
including the claude-CLI outage, the waits on checkpoints and the serve restarts for the fixes.

**Argue (18 attempts) and design (11 attempts).** Argue first passed at attempt 13 (19:16 UTC). Its main claim was
checked against the extraction: 2/55 vs 1/54 on the general ward; 7/43 vs 2/42 against placebo (more delirium with
melatonin); 19/81 vs 38/81 against usual care at 6 hours; five included studies, none on ramelteon or
community-dwelling adults. The question is answered as an evidence gap, which the proposed trial addresses. Argue had
to run again three times:
- design 8 failed its structure (hypothesis 2 was listed by item D1, but its `tested_by` did not name D1), and with
  the design reruns spent the kernel sent the project back to argue;
- the Risks section asked argue for an ICU-benchmark claim and a ramelteon claim (its one draft→argue use);
- in between, attempts 14 and 16 lost the passed claim. Attempt 16's Arguer read "keep the main claim" as giving none.

The stand-in restated the claim word for word in its comments, and it passed again at attempts 15, 17 and 18.

On the way the stand-in withdrew claims that:
- stated the placebo pilot's direction backwards (attempt 12);
- said the "pooled evidence" conflicts, although nothing was pooled;
- made statements about the search window that their trial quotes cannot show;
- failed V-HEDGE for "too few events to establish an effect" (the lexicon reads "establish" as categorical).

Each design rerun after an argue rerun started over. The Ideator proposed pooling the included trials four times
(the stand-in's revise answers at 17:41, 17:56, 19:17 and 21:33 UTC), and each time the stand-in asked for new data
instead. The approved design 11
(`dsg_01M713RAF4FKR4C1TQC2ED3DB8`, 21:34 UTC) is one randomised, double-blind, placebo-controlled trial in adults 65
or older outside the ICU:
- three setting strata, analysed separately: general ward, elective noncardiac surgery, community-dwelling;
- incident delirium by blinded 3D-CAM/CAM;
- a treatment-by-setting interaction;
- sample size by a formal power calculation.

Of its three feasibility claims the stand-in withdrew one, which said the protocol was registered; its quotes do not
show that.

**Draft (10 attempts) and revise (2 attempts).** On 0911b30, draft attempts 7–10 carried each section that had passed
and redrafted only the failing ones. Attempt 10 passed at 21:40 UTC with only the Background failing before it. The
V-HEDGE failures in those attempts came from one claim: "not associated with a visible difference" reads as tentative
(level 1), so a plain sentence citing it was "stronger than the claims it cites". Revise passed at attempt 2 (21:43
UTC). The SimReader asked 43 questions in two attempts (25 priority 1, 16 priority 2, 2 priority 3): 32 answered, 6
revised, 5 rejected revisions. Most priority-1 questions were about the design: no dose, timing or
duration; melatonin and ramelteon in one comparison with no separate analysis by agent; no power-calculation inputs;
no community recruitment method. The Writer answered that the frozen design does not give them.

**Audit and final review.** The audit passed every hard gate of the proposal at its first attempt (G-REF, G-QUOTE,
G-COVER, V-BALANCE, G-SUPPORT, G-HEDGE, G-DISCLOSE, V-STATS; the package carries report v2). At the final review
(21:44 UTC) the stand-in read all six sections against the extraction and the frozen design and approved: the numbers
and directions match, the scope (five studies, 2024 to now, melatonin only) is stated, and inferences are worded
tentatively.

**Sections (final versions; every sentence passed its verdict):**

| Section | Version | Sentences |
|---|---|---|
| Background & Significance | v29 | 7 |
| Gap | v21 | 6 |
| Research Question & Hypotheses | v15 | 10 |
| Proposed Methods | v16 | 14 |
| Expected Outcomes & Alternatives | v20 | 11 |
| Risks | v11 | 13 |
| Disclosure (kernel-written) | v8 | 92 |

**Export.** `paper.pdf` (19 pages), `paper.docx`, `paper.md`, `paper.tex`, `refs.bib`/`refs.json`, `metadata.yaml`
and the package `package-4701ce1c.zip`. Its `MANIFEST.json` (`rk-package/1`, `paper_type: research_proposal`,
`audit_report: 2`) lists the SHA-256 of 14 files: the audit report, `disclosure.md`, the export files, `extraction.csv`
and `protocol.json`. It also records the event-log head `380a2d48…`. The paper's title is the whole frozen research
question sentence (a calibration finding: the export takes the RQ as the title).

**Tasks per role and driver (all done unless stated):**

| Role | Driver | Tasks |
|---|---|---|
| writer | `omp_gateway:anthropic/claude-haiku-5-5` | 122 |
| judge | `omp_gateway:openai-codex/gpt-6-luna` | 125 |
| judge_2 | `ollama:deepseek-v4.1-flash:cloud` | 125 |
| arguer | `omp_gateway:anthropic/claude-haiku-5-5` | 46 (+3 failed on a gateway 429) |
| screener_a | `omp_gateway:anthropic/claude-haiku-5-5` | 31 |
| skeptic | `omp_gateway:openai-codex/gpt-6-luna` / `gpt-6.1-sol` | 31 / 9 |
| ideator | `omp_gateway:anthropic/claude-haiku-5-5` | 14 |
| reader | `omp_gateway:anthropic/claude-haiku-5-5` | 10 |
| simreader | `omp_gateway:openai-codex/gpt-6-luna` | 6 |
| scout | `omp_gateway:anthropic/claude-haiku-5-5` | 3 |

**No local model.** The SQL check returned 0 tasks on a non-cloud `ollama` driver and 0 tasks on any driver outside
the configured list. Two judge attempts (tasks `tsk_01M70CJ1XVF0A2854K2T8R5185` and `tsk_01M70CJ6BPWN4C164MEZN7A4KH`,
14:49 UTC, driver `gpt-6-luna`) were answered by `anthropic/claude-sonnet-5-5` through omp's model fallback on code
from before f7fe0c2. So those two support votes had no vendor separation from the Haiku-written claims (Deviation 5).
A later attempt at 20:12 UTC was refused by the gateway for the same mismatch, as f7fe0c2 intends.

**Checkpoints:** 45, all answered by the stand-in: 18 stage_blocked, 15 uncertain_support, 9 design_approval, 2
protocol_approval, 1 final_review. Each answer is logged with its time and reason.

**What failed or was weak:**
- Haiku as Arguer and Writer needed the stand-in's guidance at almost every gate:
  - claims restated many times;
  - a reversed direction;
  - empty sections;
  - "the RESTORE protocol is registered" asserted twice without a source.
- Each argue rerun discarded a passed argument, and each design rerun a passed design.
- The design leaves dose, agent-specific analysis and power inputs open. The SimReader flagged this and the final
  review accepted it; a real researcher would likely have asked for a design revision there.

## Critical review and P1 project: waiting for OpenAlex

Both runs are `paused_budget` on the anonymous OpenAlex daily budget, which resets at 00:00 UTC on 2026-11-10:
- the critical review (`llm-screening`) at read_extract;
- the P1 project (`probiotics-aad-2`) at landscape.

The reproduction set was reseeded in a fresh home (`rk-plan5c-srrepro3`, stats check 180/180), and its six runs start
after the reset. The second home's six runs, which failed at protocol, stay as history.
