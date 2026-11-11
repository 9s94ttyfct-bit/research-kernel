# Plan 5c demo: draft, revise, audit and export, cloud models only (2026-11-09)

The Plan 5c stages turn a review's argument into an exported manuscript: `draft → revise → audit → export`. This page
records the live runs of Task 9:
- a systematic review (melatonin for delirium), a critical review (large language models as screeners) and a research
  proposal (the melatonin question as a trial), each in P2 with the checkpoints answered. The melatonin review and the
  proposal exported. The critical review stopped at read_extract on a `stage_blocked` checkpoint, left open. A
  calibration rerun on 2026-11-11, on new code, passed read_extract and stopped in synthesize on a kernel defect,
  since fixed.
- one P1 project from intake to export, with every checkpoint the mode lets a vote answer voted. It failed at protocol:
  the vote never approved the protocol.
- the systematic-review reproduction set. All six runs of its third home failed at protocol; the report is
  `docs/eval/sr-repro-2026-11-11.md`.

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

## Critical review (P2): stopped at read_extract (checkpoint left open)

Project `llm-screening`, run `run_01M70BF35ZGCC0SFJBTTEAEK9W`, home `rk-plan5c-critical`. It is a critical review
with a thematic plan. Its question asks how large language models have been evaluated as title and abstract
screeners in systematic reviews. It also asks what recall, workload savings and failure modes those evaluations
report, and how far their designs support using a model in place of a second human screener.

- **Protocol, 11 attempts (14:30–17:36 UTC 2026-11-09).**
  - Drafts v1–v6 all planned a risk-difference meta-analysis on single-arm accuracy proportions. That is the defect
    the controller fixed in e8d620f: a meta-analysis plan is refused outside a systematic review, and the refusal
    names the fix.
  - The stand-in then narrowed the search twice at `protocol_approval`. The first probe counted 376,699 records
    against a record budget of 800.
  - v14 was approved with `record_cap` 800: 4 phrase queries on PubMed and Europe PMC, 2024 to now. Europe PMC's
    probe counted 746 and PubMed's queries 16, 3, 1 and 2, so at most 768 records.
  - The 11 stand-in answers are in the home's `demo-notes/answers.log`: 8 on `stage_blocked`, and 3 on
    `protocol_approval` (2 revises, then the approval).
- **search_screen** passed at 17:58:05 with single screening, as a critical review allows: 32 Screener-A tasks.
- **read_extract** started at 17:58:05:
  - The run paused on the OpenAlex budget at 17:58:24. The retraction check calls Crossref and OpenAlex for every
    included item.
  - It resumed at 00:02:25 on 2026-11-10 on 612a780.
  - Attempts 2 and 3 each covered all 63 included studies (attempt 2 carried 2 over from an interrupted attempt); 6 were
    read on the abstract alone. Each attempt was reopened
    on its extraction problems:
    - attempt 2 (ended 00:16:27): 702 values with a verified quote, 254 not reported, 115 with a problem;
    - attempt 3 (ended 00:31:40): 656 verified, 305 not reported, 110 with a problem.

    Most problems are `quote not verbatim` (104 and 98). About half are on `publication_status` (50 and 48), a field
    whose answer, a journal or a preprint, is rarely a sentence in the paper. Of attempt 2's 115 problems, attempt 3
    cleared 59 and kept 56, and it made 54 new ones.
  - By 00:42 the Reader had finished 171 tasks. Every task attempt in the run ran on `anthropic/claude-haiku-5-5`.
- The run paused on the OpenAlex budget at 00:41:46 on 2026-11-10, in read_extract attempt 4. Attempt 4 had extracted
  45 studies by then (765 values, 85 with a problem).
- **Resumed at 04:25:17 on 2026-11-11** on fa83ed6 (`rk serve` named service). The anonymous OpenAlex budget had
  reset, and no other home was running.
  - Attempt 5 carried over attempt 4's 45 studies and read the other 18. It ended at 04:29:30 with 658 values with
    a verified quote, 303 not reported, and 110 with a problem: 108 `quote not verbatim`, 52 of them on
    `publication_status`.
  - The read_extract→read_extract edge was at its limit (2), so a `stage_blocked` checkpoint opened. The stand-in
    answered `allow_one_more` at 04:30:03. Its comment spelled out the quoting rule: copy quotes character for
    character; quote the venue line as printed for `publication_status`, or give `not_reported` with the header
    searched. The comment reached the reopened stage's guidance.
  - Attempt 6 ended at 04:43:38 with 662 verified, 307 not reported, and 102 with a problem in 51 of the 63 studies.
    100 were `quote not verbatim`; 40 were on `publication_status` and the rest spread over 15 fields, none with more
    than 7. A second `stage_blocked` opened (`chk_01M74EPB9P6CZCA7YXT47W7VCW`).
- **Why it stops here.** The Reader takes `publication_status` from the item's metadata, and that text is not in the
  paper, so no quote of it can verify. Local commit b836d14 lets an extraction field be sourced from the item's
  record, but this run's protocol was frozen before that change and its serve ran fa83ed6. More attempts cannot clear
  these failures, so on the controller's instruction the stand-in did not answer the second checkpoint. The run
  stays `paused_waiting_human` at read_extract attempt 6 with that checkpoint open. It did not reach synthesize,
  argue, draft, revise, audit or export.
- In total the run's Reader finished 252 tasks. All 309 task attempts in the run ran on `anthropic/claude-haiku-5-5`.
  The 12 stand-in answers are in `demo-notes/answers.log`.

### Calibration rerun (2026-11-11): read_extract converged on ad3cbc2, synthesize stopped on a defect

Home `rk-plan5c-critical2`, run `run_01M74GJQV9ZNJJGME0NY3YYDGK`, the same seeded question. It ran on eaecdf8 (record
fields since b836d14) from 05:16 UTC and on ad3cbc2 from 06:49. Every task ran on `anthropic/claude-haiku-5-5` (283
task attempts), except the synthesize judges (17 on `openai-codex/gpt-6-luna`, 17 on `deepseek-v4.1-flash`).

- **Protocol**, 2 attempts (05:16–05:19 UTC). Every draft sourced `publication_venue` and `publication_type` from the
  record (`source: record`). The stand-in asked for the earlier 4 phrase queries (the probe counted 3,629 against the
  800-record budget). It approved v5 with `record_cap` 800, which froze v6 (probe 768).
- **search_screen** passed at 05:46:35. It used single screening with 31 Screener-A tasks: 629 records, 73 included
  at title/abstract (2 not retrieved), 51 at full text (5 read on the abstract alone).
- **read_extract**, 6 attempts (05:46–06:55). Attempts 1–4 ran on eaecdf8 and attempts 5–6 on ad3cbc2:

  | attempt | read again / kept | verified | not reported | problems | gate failures (studies) |
  |---|---|---|---|---|---|
  | 1 | 51 / 0 | 435 | 103 | 23 | 25 (19) |
  | 2 | 51 / 0 | 428 | 116 | 15 | 17 (13) |
  | 3 | 51 / 0 | 408 | 111 | 38 | 42 (22) |
  | 4 | 51 / 0 | 422 | 105 | 34 | 34 (16) |
  | 5 | 16 / 35 | 438 | 118 | 5 | 6 (4) |
  | 6 | 4 / 47 | 436 | 125 | 0 | 0: passed at 06:55:06 |

  - The record fields verified in every row: 50/50, 50/50 and 49/49 in attempts 1–3, then 51/51 in each attempt
    from 4 on.
  - Every quote failure was in a text field: `quote not verbatim` or an empty quote. The record fields failed only
    when the Reader left both out ("no value": 2, 2 and 4 in attempts 1–3), and once in attempt 5, where a record
    value was not what its "Venue: not given" line says.
  - **On eaecdf8 the attempts did not converge.** Each `read_extract→read_extract` rerun read all 51 studies again,
    so studies that had passed made new slips. Between attempts, failures were kept / cleared / new: 3/22/14,
    6/11/36, 7/35/27.
    - The reruns after attempts 1 and 2 were automatic, since the edge was under its limit.
    - After attempt 3 the stand-in answered `stage_blocked` with `allow_one_more` and quoting rules.
    - On ad3cbc2 it answered the next two the same way, each comment naming the remaining failures. The rerun read
      only the failing studies, and the failures fell from 34 to 6 to 0.
  - **Values were lost on the re-read.** About 10 values verified earlier came back `not_reported` in attempts 5–6,
    for example a recall of 0.93 and a `relevant_records_n` of 4545. Not-reported values rose from 105 to 125; the
    rest of the rise came from former problems. The gate accepts any `not_reported` with a verified quote of the
    section searched. This is open (see the calibration list).
- **Fixed on ad3cbc2:**
  - A rerun after a failed gate reads again only the studies the gate found something against (`rerun_kept`), the
    way draft keeps its passed sections.
  - The gate checks that a record value is what its quoted line says. Two contradictions had passed: `preprint`
    quoting "Publication type: journal article", and "not given (preprint)" quoting "Venue: not given". A record
    field is `not_reported` only on a "not given" line.
  - `rk_text_read` ranges and `rk_text_find` snippets start and end on whole words. Quotes cut mid-word ("inment in
    SR1…") had failed V-QUOTE.
- **synthesize**, 3 attempts (06:55–07:07). The run stopped on a kernel defect.
  - The judges rated all but one synthesis claim "partial", for sound reasons: a lower bound no sentence gives, and
    "most cited" or "minority" wording no sentence supports. One claim in attempt 3 was also V-HEDGE overstated.
  - The stand-in answered `uncertain_support` claim by claim. Attempt 1 was answered `rerun`, with a fix for each
    claim, and its comment became attempt 2's guidance. In attempt 2, two claims were withdrawn and two supported.
  - The answer to attempt 2 was wasted, and attempt 3's checkpoint would have been too. In attempt 2 the Arguer had
    named one study under an id with a letter dropped; in attempt 3 it left one study out. `_assess` skipped them
    silently and went on to the claims and their checkpoint. Only the gate, after the stand-in's answer (07:03:12),
    said "was not assessed".
  - The checkpoint of attempt 3 (`chk_01M74PVNE9YT0WW4WAM4K3B2ZH`) was left open, with synthesize→synthesize at its
    limit.
  - Fixed in 850bfe7: a study the assessment leaves out is asked for once more. If it is still left out, the attempt
    stops before any claim.
  - Fixed in 47050dc and 43d7b33, from the review: the gate's narrative checks (the representativeness statement and
    the SWiM items) had the same flaw. They now run when the narrative comes in, and the narrative is asked for once
    more. If it still fails, the attempt stops before any claim, and a P2 rerun carries its problems as guidance.
- Other findings, not kernel defects:
  - On two long full texts (45k and 101k characters), Haiku marked every text field `not_reported` with an empty
    quote (`design_support_for_standin` `not_addressed` in each).
  - Synthesis claims cited record lines ("Publication type: journal article") as evidence of a failure mode.
  - Screening let in secondary reviews of evaluations. The approved protocol had no exclusion for them, which the
    stand-in missed. Attempt 1 used a rapid review's median recall as evidence.

### Calibration rerun, continued (2026-11-11): synthesize passed three times, argue paused on its task budget

The same home and run, resumed on 850bfe7 at 07:24 UTC. Tasks from then on: the Arguer 47 on
`anthropic/claude-haiku-5-5`, the Skeptic 8 and the first judge 40 on `openai-codex/gpt-6-luna` (both through the omp
auth-gateway), the second judge 39 on `deepseek-v4.1-flash`.

- **synthesize**, attempts 4–8. Attempts 4–6 were `synthesize→synthesize` reruns answered on `uncertain_support`;
  attempts 7 and 8 came from `argue→synthesize`. It passed at 07:35:48 (attempt 6), 07:41:11 (7) and 07:49:19 (8).
  - 850bfe7 worked live: in attempt 4 one assessment batch gave 9 of 10 studies, the missing one was asked for again
    and given (`tsk_01M74QYX9D6276GFC63V6A12HB`, `tsk_01M74QZAJ6QSAT3DCZZKQVE4XG`). Attempts 4–8 each assessed 51/51.
  - **Judges failed on a gateway rate limit.** The gateway passed Codex's `Codex is at capacity … retry-after-ms=21000`
    on as a 502; each judge task tried three times within 0.75 s and failed (e.g. `tsk_01M74R1W9HV2S2MHS9Q9WYN8P8`,
    07:27:13.201–.954). Five claims fell to uncertain for want of opinions.
  - **Supported findings were lost on every new attempt.** Each attempt wrote its narrative afresh. The high-recall
    finding was supported in attempts 5 and 7 and came back reworded and over-quantified in 6 and 8, where it was
    withdrawn, although attempt 6's guidance said "keep that claim exactly".

    | attempt | findings | supported at the end | withdrawn |
    |---|---|---|---|
    | 4 | 6 | 0 | 0 (6 uncertain) |
    | 5 | 5 | 1 | 0 (4 uncertain) |
    | 6 | 6 | 1 | 5 |
    | 7 | 7 | 2 | 5 |
    | 8 | 4 | 3 | 1 |
- **argue**, 6 attempts (07:35–07:51). Attempts 1 and 4 went back to synthesize for findings the Arguer lacked (recall,
  failure modes, workload); attempts 2, 3 and 5 reran argue. At 07:51:18 the run paused (`paused_budget`): argue had
  used its 40 role tasks in the run session. Serve restarts keep the session; `rk run resume` starts a new one.
- **Fixed after the run:**
  - c7aa747, 4c8ae13: a reply with a `retry-after-ms` up to 120 s is waited out in the task and tried again; a longer
    one, or one the last attempt still gives, is a usage limit (the run waits instead of the task failing).
  - 96135e6, 3a65296, 390e834: a new synthesize attempt keeps, word for word with their evidence, the findings of the
    attempt before that still pass the claim gate, are not contested and cite only usable studies. The Arguer writes
    only the others. Only your answer releases a kept finding: those you name in `revise` on synthesize's rerun or
    argue's back are written again; P1 keeps them all.
  - 4b6b23f, 14bc97a: a read_extract rerun reads a study again only for its failing fields; the other fields keep their
    verified rows (the values lost on re-read above).
- Other findings, not kernel defects: the Haiku Arguer over-quantifies findings; synthesis claims cited record lines
  as evidence three more times; argue attempt 1 used secondary reviews as evidence; the gpt-6-luna Skeptic's
  objections were specific and sound.
- Open: argue's 40-task budget per run session runs out in one P2 review loop (pause and resume is the workaround);
  every synthesize attempt assesses all studies again; the omp_gateway driver clips an error body to 500 characters,
  which can drop a `retry-after-ms`.

### Calibration rerun, second resume (2026-11-11): argue passed, draft went back to argue unasked

The same run, resumed at 08:50:19 UTC (run session 2), with argue at attempt 6. The main checkout was at 710849b from
08:22 to 09:06 UTC (git reflog); which code the serve process had loaded is not recorded. Tasks in the session: the
Arguer 15 and the Writer 7 on `anthropic/claude-haiku-5-5`, the Skeptic 5 and the first judge 19 on
`openai-codex/gpt-6-luna`, the second judge 18 on `deepseek-v4.1-flash`. All 64 tasks finished; none failed.

- **The retry-after fix worked live.** Two judge calls got Codex's capacity 502 with `retry-after-ms=24000`
  (08:52:02) and `retry-after-ms=5000` (08:53:21). Each waited and succeeded on its second try.
- **argue → synthesize, attempt 9** (08:54:28): the stand-in sent argue back for a recall finding (round 3). The
  attempt kept 3 findings of attempt 8 word for word (96135e6 worked). But the 2 kept findings the stand-in had
  marked supported on attempt 8's checkpoint were judged again, came back uncertain, and were asked again
  (`chk_01M74X60HX9JPFKJH52MC30SN1`). Synthesize passed at 08:57:43.
- **argue, attempts 7–9.** Attempt 7's main claim made cross-study patterns the findings do not show; the stand-in
  sent it back (argue→argue). Attempt 8's Arguer output had no `answers_rq` self-check. That cost a whole attempt
  and one more use of argue→argue (08:59:49). Attempt 9 passed at 09:02:30. The stand-in answered that the main
  claim answers the question, and recorded the conditions for replacement as a limitation.
- **draft, attempt 1** (09:03:06): the `gaps-agenda` section's Writer asked for "a claim comparing the included
  evaluations by design features … so that the conditions under which a model could replace a second human
  screener can be stated". That is the gap the stand-in had just recorded as a limitation. draft→argue was taken
  with no one asked; draft opened no checkpoint in either mode.
- **Paused** at 09:03:31 (`paused_budget`): argue attempt 10 had started, and argue had used its 40 role tasks in run
  session 2.
- **Fixed after the run:**
  - 706345c: the omp_gateway driver's 500-character clip of an error body keeps a `retry-after-ms` whole.
  - 88712fe: a `revise` that would be ignored (on a claims answer, on synthesize's back to read_extract) is refused,
    and the missing-narrative checkpoint offers the findings the next attempt would keep.
  - b9fe63f: a kept finding carries the V-NUM and V-SUPPORT verdicts it passed with (`carried_from`) and its
    `supported` status, attributed as before (to you when you decided it). V-HEDGE is checked again; it is not
    judged again.
  - b3afd2e: in P2, a section's `missing_support` opens `argue_request` (once per section, human only). `back` takes
    draft→argue with your comment. `write` has the section written from the claims given, the gap stated as a
    limitation (given to the Writer, disclosed). P1 still goes back to argue once per section.
  - 35b4fa9: a round-1 Arguer output without its self-check or main claim is asked for once more within the attempt;
    only a second slip reruns argue.
- Other findings, not kernel defects: the Haiku Arguer overgeneralised again, and once left out a required field;
  the judges rated the review's own scope statements "partial"; the gpt-6-luna Skeptic's objections were sound.
- Open: argue's 40-task budget per run session is spent by ordinary P2 review loops (twice now).

### Calibration rerun, third resume (2026-11-11): argue paused on its budget again

The same run, resumed with `rk run resume` at 09:31:13 UTC (run session 3) by a serve started on 09c5f38 (logged
HEAD), with argue at attempt 10. That attempt had been opened by the old automatic draft→argue (above). Tasks: the
Arguer 6 on `anthropic/claude-haiku-5-5`, the Skeptic 5 and the first judge 15 on `openai-codex/gpt-6-luna`, the
second judge 14 on `deepseek-v4.1-flash`; 40 in all, all argue's. No task failed and none waited on a retry-after.

- **argue, attempts 10–12** (09:31–09:38). Attempt 10 (20 tasks) carried the draft's request as guidance, and each
  of its four Arguer rounds asked synthesize again for the design comparison the stand-in had recorded as a
  limitation. At its final review (`chk_01M74Z9THXWZNS6X87YWF2MQTG`) the stand-in withdrew two response claims that
  their own cited sentence contradicts. That left the warrant `edg_01M74XHQ2B1ZCJP79ZY9ZRSPCR` unanswered, and
  argue→argue ran (6th use, allowed). Attempt 11 (16 tasks) got a rerun answer (`chk_01M74ZF51GM89X9NRKX13R7KD1`):
  its main claim mixed assistive use with replacement. Attempt 12 started at 09:37:40 (7th use of argue→argue);
  the run paused at 09:38:05 (`paused_budget`, 40 of 40 argue role tasks in session 3).
- **Argue copied supported findings as new claims.** In attempt 11 the Arguer gave three synthesis findings as new
  reasons, word for word and on the same notes. Stored as new claims and judged again, two came back uncertain
  (`clm_01M74ZD0GDG069J746FVFR79VR` copies `clm_01M74X4RCA9B6TJBRE7ABGK4KY`; `clm_01M74ZD0H8SCMR901NYDX23JTH`
  copies `clm_01M74X4RC1JTSZM3HFDZT6Y23E`), and the copies drew new priority-1 objections.
  - Fixed in 46b3423: a new reason or response claim with a supported claim's text (folded) that cites only that
    claim's notes reuses it by id; one citing other notes stays a new claim.
- Not exercised: the round-1 ask-again, the kept-finding carry and `argue_request`. Neither synthesize nor draft ran
  in this session.
- Other findings, not kernel defects: the Haiku Arguer made recall a reason for workload although the figures come
  from different evaluations, and kept asking for the design comparison after it was a limitation; the gpt-6-luna
  Skeptic's objections were substantive but asked for evidence the included studies cannot hold.
- Open: argue has used 120 tasks over three run sessions; at 16–20 tasks per attempt, a 40-task session holds about
  two attempts.

### Calibration rerun, fourth resume (2026-11-11): argue's budget runs out a fourth time

The same run, resumed at 09:53:43 UTC (run session 4) by a serve started on 59e1afb (logged HEAD), with argue at
attempt 12. Tasks: the Arguer 5 on `anthropic/claude-haiku-5-5`, the Skeptic 5 and the first judge 15 on
`openai-codex/gpt-6-luna`, the second judge 15 on `deepseek-v4.1-flash`; 40 in all, all argue's. Two first-judge
tries gave invalid output and succeeded on their second try; no task failed.

- **argue, attempts 12–15** (09:53–10:01), 23, 13, 4 and 0 tasks. Each final review was answered `rerun`, and
  argue→argue was allowed three more times (uses 8–10):
  - attempt 12: four priority-2 objections on the main claim were left open; rounds 2 and 3 restated reasons with
    added caveats instead of answering them;
  - attempt 13: the four objections on the main claim were cleared, but the main claim failed V-HEDGE on "none" and
    "never", wording the stand-in had supplied, and two response claims stayed uncertain;
  - attempt 14: the stand-in's revised wording dropped a counterexample, which the Skeptic raised again. Two objections
    carried from attempt 13 (`obj_01M74ZCD0DRSH3V83QTCD8AWTW`, `…AWTY`) were still listed for the Arguer to answer,
    since their attempt-13 answers had ended uncertain. The budget ended the rounds after the first critique (`end`
    reason `budget`), so no round answered them;
  - attempt 15 started at 10:01:42 with no tasks left; the run paused (`paused_budget`).
- **The restate reuse (46b3423) never applied:** every round of this session noted an empty `restated`. The Arguer
  restated findings with added caveats, so no text matched. Attempt 14 reused the supported findings by id, so they
  were not judged again.
- Not exercised: the round-1 ask-again, `argue_request`, draft, revise, audit and export.
- Other findings, not kernel defects: the Haiku Arguer answered objections by restating reasons, did not copy
  findings word for word when asked, and left out two response claims it was told to keep; the gpt-6-luna Skeptic
  had become mostly concessive but raised the same counterexample whenever the main claim omitted it.
- Open: argue has used 160 tasks over four run sessions. The two judges take 30 of each 40: every new or reworded
  claim is judged by both.

### Calibration rerun, fifth resume (2026-11-11): argue passed, draft sent it back, the budget ran out again

The same run, resumed at 12:22:29 UTC (run session 5) by a serve started on 69057c5 (logged HEAD): from that commit a
judge's task neither counts against the open-stage budget nor is refused by it. Tasks: the Arguer 22 and the Writer 4
on `anthropic/claude-haiku-5-5`, the Skeptic 18 and the first judge 44 on `openai-codex/gpt-6-luna`, the second judge
44 on `deepseek-v4.1-flash`. argue's 40 budgeted tasks were the Arguer's and the Skeptic's; the 88 judge tasks were
not counted. No task failed or was retried.

- **argue, attempts 15–17** (12:22–12:39). The stand-in answered `rerun` twice and allowed argue→argue twice more:
  - attempt 15: the main claim cited an external preprint, not one of the 51 included studies;
  - attempt 16: one objection was open (name the form of substitution); the stand-in gave the main claim's wording;
  - attempt 17 passed at 12:39:23 (12 claims supported, 6 withdrawn). The stand-in withdrew three synthesis findings
    (workload, recall, "most do not support"). They were listed with `withdraw` as their only answer: the objections
    carried onto them were answered by attempt-16 claims outside this attempt's argument (for the third, also by
    attempt-17 claims that were not supported, and one of its warrants failed unanswered). Withdrawn, they left the
    synthesis for the whole project; only the failure-modes finding stayed supported.
- **draft, attempt 2** (12:39–12:40): the first `argue_request` came on the section on recall against human reference
  standards. The stand-in answered `back`, because the included notes hold the recall figures.
- **argue, attempts 18–19** (12:40–12:44). The Arguer's own check found no recall, workload or design findings in the
  synthesis, since they had been withdrawn. Attempt 18's main claim failed on the external-preprint objection again.
  Attempt 19 got one Arguer task before the budget ran out at 12:44:59 (`paused_budget`).
- **The restate reuse applied for the first time:** in attempt 18, round 1, the Arguer's new claim reused the
  supported failure-modes finding by id instead of a copy.
- **Fixed after the run:** argue's final review now warns, when it lists a synthesis finding, that withdrawing
  it withdraws it for the project, and that a rerun asks the Arguer to answer an objection it left unanswered instead.
  Triage tasks, like the judges', no longer count against the budget.
- Other findings, not kernel defects:
  - the Haiku Arguer reused supported findings by id but again left out the response claims to their carried
    objections (attempts 14 and 17);
  - the gpt-6-luna Skeptic raised the same external counterexample in attempts 15, 16 and 18;
  - the judges split on claims with identical text and evidence. Identical claims are merged only when one is
    already supported: attempt 17 created the "forms of substitution" text five times.
- Open: the run stays parked at argue attempt 19 with argue→synthesize used 3 of 3 and one supported finding. On the
  researcher's decision, calibration goes on with a fresh project on the same topic.

### Fresh project on the same question (critical3, 2026-11-11): argue paused on its budget in run session 1

Home `rk-plan5c-critical3`, run `run_01M75B6P605PX6WZRDYXQR7ASA`, serve on 1101302 (logged HEAD). The home was set up
like critical2: the same drivers, stats 180/180, and the same seed (intake, approved topic, RQ a71ec5ef frozen, the
discovery stages recorded passed). Everything from protocol on ran afresh. The host slept from about 13:03 to 16:04
UTC during protocol, which then resumed at 16:44; from 16:47 the host was kept awake.

| Stage | Window (UTC) | Attempts | Role tasks | Outcome |
|---|---|---|---|---|
| protocol | 13:01–16:47 | 2 | Scout 5, Ideator 2 | v4 probed 7,707 records against the cap of 3,000; v5 (four phrase queries, 2024 on) probed 768 and was approved with `record_cap` 800 |
| search_screen | 16:47–17:34 | 1 | Screener-A 32 | 629 records, 88 kept at title and abstract, 62 included at full text |
| read_extract | 17:34–17:51 | 4 | Reader 84 | problems 20, 5, 3, 0, all "quote not verbatim"; 700 values verified, 168 not reported |
| synthesize | 17:52–17:57 | 1 | Arguer 8, judges 13 + 13 | 62 of 62 studies assessed; 6 findings supported, 7 withdrawn by the stand-in |
| argue | 17:57–18:16 | 6 | Arguer 24, Skeptic 16, judges 40 + 40 | paused (`paused_budget`) at 18:16:23 in attempt 6 |

- **search_screen:** Screener-A tasks failed during a network outage (16:58–17:06: gateway 502s, then DNS lookup
  failures). The failed batch was screened again by a new task at 17:08, so its 25 records all got decisions.
- **read_extract:** the stand-in allowed one more read_extract→read_extract with a whole-word quote for each
  remaining failure; attempt 4 passed. One kind of failure recurred: run-together table text such as
  "…0.8650.840GPT…" is a verbatim substring but ends mid-word, so the whole-word rule refuses it. The Reader got past
  it by quoting the formatted copy of the table row.
- **argue:** 40 budgeted tasks (the Arguer's and the Skeptic's); the 80 judge tasks were not counted. With the
  budget low, attempts 4–6 ran one round each. The stand-in answered every final review `rerun` and allowed
  argue→argue three times (uses 3–5):
  - attempt 1: three response claims cited unrelated sentences;
  - attempt 2: two findings were listed only because their carried objections were answered by attempt-1 claims.
    The new warning was shown, and the stand-in reran instead of withdrawing them;
  - attempt 3: the Arguer rewrote the main claim with "none" (V-HEDGE), and two new warrants drew priority-1
    objections;
  - attempt 4: the Arguer answered three carried objections by the ids of their earlier, uncertain answers, which
    its input listed as `response_claim_id`. The kernel refused all three ("is not a supported claim") in round 1
    and in the closing task;
  - attempt 5: the main claim was rewritten again and failed on one open objection (hybrid LLM–human ensembles);
  - attempt 6 followed the stand-in's wording in round 1 before the budget ran out.
- **`restated` reuse** applied in attempts 2, 3 and 4.
- **Fixed after the run:** an open objection whose earlier answer did not count now reaches the Arguer as `open`,
  with that answer's text, status and why as `earlier_answer`, but not its id. The Haiku Arguer's "left out
  responses" in critical2 (above) may have been the same refusal; that was not re-checked.
- Other findings, not kernel defects:
  - claims with identical text were created two or three times within an attempt (attempts 2, 5 and 6), each judged
    again; in attempt 3 the same unsupported response texts were written again in every round (7 and 6 times),
    each replacing the last and judged again;
  - answers to objections on a main claim the Arguer had since rewritten were refused as not open, so a comment
    asking for them has no effect;
  - the Haiku Arguer rewrote the main claim despite word-for-word guidance (attempts 3 and 5), its round-1 self-check
    said in every attempt but attempt 2 that the argument does not answer the question, and it kept asking
    synthesize to group the studies by design and form of use;
  - the gpt-6-luna Skeptic again brought in a study outside the included set (SESR-Eval).
- Open: the run is parked in argue attempt 6 (argue→argue used 5 times, argue→synthesize not used; the six
  supported findings intact). `rk run resume` starts run session 2.

### critical3, run session 2 (2026-11-11): argue attempt 6 diverged

Resumed at 18:31:18 UTC (run session 2) by a serve started on 8c75a7c (logged HEAD); the host was kept awake. Argue
attempt 6 ran rounds 2–20 until 19:04:40 and ended on its budget: the Arguer 19 and the Skeptic 20 tasks counted (39
of 40), the judges 47 + 47 did not. Two provider 502s were retried and succeeded; no task failed. The gate failed
with 159 failures (unsupported claims, failed warrants without an answer, about 100 open objections). Since no answer
to a final review could mend that, argue asked to argue again; the stand-in left that `stage_blocked` checkpoint
(`chk_01M75ZZ4N1772Q18Y9AWP1Q2KY`, argue→argue used 5 times) open, because the attempt was diverging.

- **A reason link was stored again every round.** The Arguer gave its reasons again in each round, by the ids of
  five supported synthesis findings, and the kernel stored each as a new `reason_of` link with a new warrant (for a
  valid reason it checked only for a cycle). The main claim had 95 links for 6 distinct reason–target pairs. The
  Skeptic tested every new warrant; 52 of the 89 repeated links drew soundness objections for failed tests (56 in
  all), so every round found new issues and the rounds never reached a fixed point. Attempt 3 had shown the same, on
  a smaller scale.
- **The Arguer never saw what the kernel refused.** Over the attempt the kernel refused 70 answers to objections
  that were not open, 8 answers by ids of claims that were not supported, and 19 main claims given after round 1,
  and the same answers came back round after round. 8c75a7c's `earlier_answer` reached the Arguer, without the
  earlier claim's id, but the Arguer copied those ids from the argument it is given, which lists every claim.
- **Fixed after the run:** a reason link the argument already has is refused, not stored again (a dropped link may
  be given again); and what the kernel refused of the Arguer's last answer reaches its next task, the closing task
  included (`refused_last_round`). The Arguer's task says to answer only the open objections, by id, and to give no
  main claim or existing reason again.
- Not a kernel defect: the stand-in's attempt-5 guidance gave three response texts word for word, and that guidance
  goes to every round; the Arguer gave them again, and 16, 9 and 8 of them were stored and judged again.
- Open: the run is parked in argue attempt 6 with the `stage_blocked` checkpoint open; the six supported findings
  are intact.

### critical3, run session 3 (2026-11-11): argue passed; draft stopped on one section

A serve on 6d47709 (logged HEAD). `rk run resume` does not start a new run session from `paused_waiting_human`
(only from `paused` or `paused_budget`), so the stand-in paused the run and resumed it: run session 3 started at
19:17:06 with a fresh budget. It answered the open `stage_blocked` checkpoint `allow_one_more`, with guidance that
named no response texts.

| Stage, attempts | Window (UTC) | Role tasks | Outcome |
|---|---|---|---|
| argue 7 | 19:17 | Arguer 2 | round 1 gave no main claim, twice; rerun |
| argue 8 | 19:18–19:20 | Arguer 6, Skeptic 6, judges 2 + 2 | fixed point; the main claim's support uncertain |
| argue 9 | 19:22–19:27 | Arguer 7, Skeptic 6, judges 7 + 7 | passed at 19:27:36 |
| draft 1–10 | 19:27–19:40 | Writer 101 | gate failures 16, 41, 21, 4, 9, 4, 3, 2, 1, 4 |

- **argue:** one `reason_of` link per reason and target (6 in attempt 8, 10 in attempt 9); reasons given again were
  refused. `refused_last_round` reached the Arguer, which still gave its main claim and some refused reasons again.
  Attempts 8 and 9 conceded every new objection, so they stand as limitations. argue used 27 of its 40 tasks.
- **draft:** from attempt 6 every section passed but gaps-agenda, whose failures went 4, 3, 2, 1, 4 over attempts
  6–10. Each retry rewrote the whole section, and each new attempt drafted it afresh, bringing new sentences without a
  marker or untentative `[[inf]]` sentences. In attempts 2, 5, 6 and 10 the Writer cited a withdrawn finding, whose
  id the limitations input listed. The stand-in left the `stage_blocked` checkpoint `chk_01M761Z8Z88JR09GCXYWWPVS84`
  (draft→draft used 9 times) open.
- **Fixed after the run** (the researcher's choice): a retry gives repairs of its failing sentences, which the kernel
  puts into the previous version, every other sentence kept word for word; a whole new section is refused when only
  such sentences fail. A draft→draft rerun repairs a failing section from its last version instead of drafting it
  afresh. The Writer's limitations carry no id of a withdrawn claim.
- Open: the run is parked in draft with 8 of 9 sections passing.

## P1 project: failed at protocol (the vote never approved it)

Home `rk-plan5c-p1`, intake `probiotics for preventing antibiotic-associated diarrhoea in children`, systematic
review, D2 (discovery from landscape), P1.

- **The first project** (`probiotics-aad`) failed at protocol at 17:39:48 on 2026-11-09. Two code faults caused it:
  - OpenAlex answered HTTP 400 to wildcard queries;
  - PubMed probes were not counted.

  Both were fixed (0a41959, c225d5c). The project stays as failed history. Its three protocol votes (17:38–17:39)
  were all revise, from judge and judge_2.
- **The second project** (`probiotics-aad-2`), run `run_01M70PZA7T2RV14143FR0Q82E6`, was created with the same intake.
  - **Landscape:** 5 attempts. Attempt 2 paused on the OpenAlex budget at 18:11 on 2026-11-09, and the run resumed at
    00:02 on 2026-11-10. Attempt 4 was reopened on a cluster with too few sources ("Probiotic yogurt and
    fermented-food interventions: 4 verified sources (need 5)"). Attempt 5 passed at 00:10:52. The landscape logged
    34 searches (17 OpenAlex, 17 Europe PMC). The whole project logged 84, half on OpenAlex. OpenAlex answered HTTP
    429 once, at 00:05.
  - **Gaps:** passed at 00:19:27.
  - **Topics:** attempt 2 (00:21:28) offered three topics: a systematic review of antibiotic spectrum as a moderator,
    a scoping evidence map, and a critical review of contradictory yogurt trials.
    `topic_review` is a human checkpoint in every mode. The stand-in approved the systematic review at 00:22:10, the
    paper type the intake asked for.
  - **Question:** the RQ is probiotics versus placebo in children on antibiotics, stratified by the antibiotic class's
    spectrum. Attempts 1 and 2 were refused for the word "novel". Attempt 3 was adopted by the agent at 00:23:02,
    as P1 allows: prior work answering it was not found within the searched scope.
  - **Protocol:** 3 attempts (00:23–00:25), each voted. The run failed at 00:24:59: "the protocol was not approved at
    the edge limit, and P1 does not freeze a protocol the vote did not approve".

Every vote in `probiotics-aad-2`. All are on the protocol, and each was decided `revise` with `human_reviewed` false.
judge_3 (Haiku) was skipped as the same vendor as the protocol's author.

| Attempt | Time (UTC) | judge (gpt-6-luna) | judge_2 (deepseek-v4.1-flash) |
|---|---|---|---|
| 1 | 00:23:54 | revise: no search date, no reproducible strategy per source, no deduplication or supplementary search plan; PRISMA-P incomplete | revise: the language limits contradict each other; PRISMA-P gaps |
| 2 | 00:24:33 | revise: the language and date limits disagree; an unnamed "2026 trial"; the spectrum rules are unspecified; randomised and analysed counts are mixed up | revise: the same language conflict; registries are searched, but full reports are required; event-count wording |
| 3 | 00:24:59 | revise: the spectrum classification is not operational (the agent list is "to be written"); clustering handling is unspecified | revise: the stratification rests on an unwritten agent list; the extraction has only randomised counts |

The judges' objections narrowed from attempt to attempt. The Lead, on Haiku 5.5, fixed some of each round's points.
It never wrote the antibiotic-spectrum list its own eligibility depended on, and after the third attempt the
protocol→protocol edge was at its limit.

Not reviewed by a human in this run, since every other decision was the agent's or the vote's:
- the landscape's clusters and the gaps;
- the RQ, adopted by the agent;
- every protocol draft, and the three revise votes on them;
- the failure itself.

The one human checkpoint, `topic_review`, was answered by the stand-in.

P1 protocols on this page failed the same way: two judges voting revise until the edge limit.
- `probiotics-aad` (with the code faults above);
- `probiotics-aad-2`;
- all six projects of the third reproduction home (below);
- in the second reproduction home, ozone, tfa and fructose (three revise votes each) and cade (judge_2 voted
  approve twice, judge revise).

In none of the P1 runs on this page did both judges approve a protocol within 3 attempts with Haiku as the Lead.
P1 then fails as designed rather than freezing the protocol.

## Reproduction set: third home, failed at protocol

The first two homes (`rk-plan5c-srrepro`, `rk-plan5c-srrepro2`) failed at protocol and stay as history. Details are
in the Task 9 report and in the reproduction report, `docs/eval/sr-repro-2026-11-11.md`.

The third home (`rk-plan5c-srrepro3`) ran as an `rk serve` named service.
- Setup: a stats check of 180/180 and six projects seeded at 19:11 UTC on 2026-11-09.
- `rk eval sr-repro` started the six runs at 00:26:44 on 2026-11-10 on 612a780. By 00:30 the anonymous OpenAlex budget
  was spent. Three things had used it since the reset: the P1 discovery stages above (landscape, gaps, topics and
  question), the critical review's read_extract and these protocol probes. Every home calls OpenAlex without a key,
  so they all share the budget per IP (`rk setup`: "$0.10/day").
- Pulmonary hypertension failed at 00:29:34. The other five paused on the budget between 00:28:27 and 00:29:22.
- On 2026-11-11, after the critical review had stopped, a second `rk eval sr-repro` on fa83ed6 resumed the five
  at 04:44 UTC and started a fresh pulmonary-hypertension run. No run paused on the budget again. By 04:47:53 all six
  had failed at protocol.

| Entry | Run | Protocol attempts | Votes (judge/judge_2, each decided revise) | Failed (UTC) |
|---|---|---|---|---|
| cade-colonoscopy | `run_01M71DK7MAW7JJ8BR2X84HWMHE` | 3 | 1–3: revise/revise | 11-11 04:44:25 |
| ozone-bleaching | `run_01M71DK7N4YKWCKG5GWZGVYP2P` | 3 (1 refused for "novel") | 2–3: revise/revise | 11-11 04:45:27 |
| fructose-rats-sbp | `run_01M71DK7P1FWYZ3R2S5RH04D5V` | 3 (1 refused for "novel") | 2–3: revise/revise | 11-11 04:47:53 |
| tfa-heated-oils | `run_01M71DK7PYD1JMCEKADFCNYSA0` | 3 | 1: revise/approve; 2–3: revise/revise | 11-11 04:47:16 |
| ai-pulmonary-hypertension | `run_01M71DK7R03B4WFC4XN9VE2NJY`, then `run_01M74EQJH521WSWGAZH6V6C56K` | 3, then 1 | 1–3, then 4: revise/revise | 11-10 00:29:34; 11-11 04:44:49 |
| ai-prostate-mri | `run_01M71DK7SCWDV84K44QXW5GP0P` | 3 (1 refused for "novel") | 2–3: revise/revise | 11-11 04:46:57 |

- No run reached search_screen, so every recall is 0.00 and no direction was judged: no entry meets the §10
  targets. The report lists each entry's selection ceiling, the share of included studies the selection's own
  search found (0.88 to 1.00).
- **The fresh pulmonary-hypertension run had one protocol attempt.** Back-edge counts belong to the project, not
  the run. The new run started with protocol→protocol already used twice by the failed run (edge.taken 1 and 2 at
  00:28:29 and 00:29:01 on 2026-11-10), so it failed after its first vote. A replaced run cannot get the full
  number of tries.
- The judges' reasons were concrete protocol faults. For pulmonary hypertension:
  - the search sources are inconsistent: PubMed and Europe PMC in the strategy, OpenAlex in the PRISMA-P scope;
  - the 3,000-record cap keeps only the first records returned;
  - the extraction lacks false positives and false negatives for both arms;
  - the synthesis mixes risk ratios with a bivariate sensitivity/specificity model.
- `rk eval sr-repro` resumes paused runs and starts a fresh run in place of a failed one, and `--write-report` does
  the same. The report was therefore written by scoring each project (`rk_sr_repro_score`) and
  `rk.eval.sr_repro.write_report`, without starting runs. Fixed after this run: `rk eval sr-repro` no longer
  replaces a failed run; it reports it as the entry's result (a new run would start with the back-edge limits
  already spent).
