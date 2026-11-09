# Plan 5c demo: draft, revise, audit and export, cloud models only (2026-11-09)

The Plan 5c stages turn a review's argument into an exported manuscript: `draft → revise → audit → export`. This page
records the live runs of Task 9:
- a systematic review (melatonin for delirium), a critical review (large language models as screeners) and a research
  proposal (the melatonin question as a trial), each in P2 with the checkpoints answered;
- one P1 project from intake to export, with every checkpoint the mode lets a vote answer voted;
- the systematic-review reproduction set (`docs/eval/sr-repro-<date>.md`).

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
limits), from 17:34 UTC Screener-B, the Skeptic and the SimReader ran on `omp_gateway:openai-codex/gpt-6.1-sol`
and `judge` on `omp_gateway:openai-codex/gpt-6-luna` (before: `codex_cli` and `codex_cli:gpt-6-luna`). `judge_2` and
Triage stayed on `ollama:deepseek-v4.1-flash:cloud`. The gateway refuses a reply from another model than the one asked
for (commit f7fe0c2).

**Deviation 4: the cheapest models only.** By your decision at 18:18 UTC, the runs use only the cheapest model of each
vendor: every Anthropic role on `omp_gateway:anthropic/claude-haiku-5-5` (unchanged) and every OpenAI role (Screener-B,
the Skeptic, the SimReader and `judge`) on `omp_gateway:openai-codex/gpt-6-luna`. Screener-B, the Skeptic and the
SimReader had run on `omp_gateway:openai-codex/gpt-6.1-sol` from 17:34 UTC; they moved to `gpt-6-luna` in all homes
at 18:18:37 UTC (settings are read per task, so running work used it from its next task). `judge_2` and Triage stayed
on `ollama:deepseek-v4.1-flash:cloud`. The shipping defaults are unchanged.

All `rk serve` processes were restarted at 17:34 UTC on the code with these changes; the runs resumed from their event
logs. From 18:22 UTC every serve ran on the pushed main of that time (48982c4), and the melatonin and proposal serves
moved to the two fixes below as they landed (19:01 UTC to d213398, 19:39 UTC to 2e717c7), each at a checkpoint wait.

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

## Melatonin systematic review (P2), in progress

Home `rk-plan5c-melatonin`, run `run_01M70PZ9VJMQ97C7JHWF68YK22` (its first run, `run_01M700YGHXR22RMWAFTQA0R74J`,
failed on the argue crash fixed in 7ca05c5).

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
  that risk of bias was assessed with RoB 2, although the extraction records it as not reported. Attempt 9 was running
  at 20:20 UTC, guided by the stand-in's comment.

## Research proposal (P2), in progress

Home `rk-plan5c-proposal`, run `run_01M70BHRVFRCT8SEC1KS2B4BBQ`. Design 6 (a stratified, placebo-controlled trial) was
approved at 17:57 UTC. Draft went back to argue once (fix 0d8e3d0). Argue passed at attempt 13 (19:16 UTC); its main
claim was checked against the extraction (2/55 vs 1/54; 7/43 vs 2/42 against placebo, more delirium with melatonin;
19/81 vs 38/81 against usual care at 6 h).

Design 7 went back to pooling the same trials, and the stand-in asked for a revision. Design 8 failed its structure:
hypothesis 2 was listed by item D1, but its `tested_by` did not name D1. The design reruns were spent, so the kernel
sent the project back to argue. Attempt 14 lost the passed claim; at the stand-in's request, attempt 15 (running at
19:45 UTC) argues it again. On the way, the stand-in asked to withdraw claims for two reasons:
- attempt 12 stated the placebo pilot's direction backwards;
- V-HEDGE read "too few events to establish an effect" as categorical (the word "establish"), so the claim was
  reworded to "show".

At 20:14 UTC, attempt 15 brought back the attempt-13 main claim, and the stand-in supported it. The stand-in withdrew
six response claims:
- three said the "pooled evidence" conflicts, but nothing was pooled;
- three made statements about the search window that their trial quotes cannot show.

The objections those claims had answered were then open, and the stage asked for another argue->argue use
(stage_blocked, open at 20:20 UTC).

## Critical review and P1 project: waiting for OpenAlex

Both runs are `paused_budget` on the anonymous OpenAlex daily budget, which resets at 00:00 UTC on 2026-11-10:
- the critical review (`llm-screening`) at read_extract;
- the P1 project (`probiotics-aad-2`) at landscape.

The reproduction set was reseeded in a fresh home (`rk-plan5c-srrepro3`, stats check 180/180), and its six runs start
after the reset. The second home's six runs, which failed at protocol, stay as history.
