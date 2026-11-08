# Plan 5a demo: from a field to a frozen protocol and the record cap, cloud models only (2026-11-07/08)

Recorded on macOS arm64 with a scratch `RK_HOME` (`<scratch>` below), `rk serve` started by hand in the background
(`RK_BROWSER_HEADLESS=1`), and the live APIs (OpenAlex, Europe PMC, PubMed, Crossref). Everything below comes from this
run's event log, its database and CLI output. Item, gap, topic and checkpoint ids are the run's own.

**The checkpoints were answered by the agent that ran the demo, standing in for the researcher.** Every answer is
recorded below with its reason. In real use only you answer them.

**How far it got.** One D2 run went from intake through discovery (RQ frozen), the protocol (frozen, record cap 300)
and the first search of search_screen. That search passed the record cap, so the stage went back to protocol, as
designed. The demo stopped there: no title/abstract screening, no full text and no extraction ran. Why, and what
would have to change, is under "What failed or was weak". The record cap was 150 at first, then 300 (see Protocol).

## Setup: models, and no local model

`config.toml` was written before the first `rk` command (otherwise `rk` auto-starts `rk serve` with the defaults):
every Anthropic-vendor role on Claude Haiku 5.5, Screener-B, Skeptic and SimReader on the codex CLI, the judges on
three vendors, Triage on Ollama Cloud.

```
$ rk providers
arguer      claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
gapminer    claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
ideator     claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
judge       codex_cli:gpt-6-luna                   vendor=openai    ok 0.167.0-alpha.3.2  (version 0.167.0-alpha.3.2 != pinned 0.166.0-alpha.6; flags may have changed)
judge_2     ollama:deepseek-v4.1-flash:cloud       vendor=deepseek  ok
judge_3     claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
lead        claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
reader      claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
scout       claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
screener_a  claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
screener_b  codex_cli                              vendor=openai    ok 0.167.0-alpha.3.2  (…)
simreader   codex_cli                              vendor=openai    ok 0.167.0-alpha.3.2  (…)
skeptic     codex_cli                              vendor=openai    ok 0.167.0-alpha.3.2  (…)
triage      ollama:deepseek-v4.1-flash:cloud       vendor=deepseek  ok
writer      claude_cli:claude-haiku-5-5            vendor=anthropic ok 2.1.309
```

`rk models` (excerpt; the full list also shows every `omp_gateway:` model, all `ok`, and the Ollama Cloud models that
are not pulled, all `UNAVAILABLE`):

```
$ rk models
claude_cli:claude-haiku-5-5                          vendor=anthropic ok
claude_cli                                           vendor=anthropic ok
…                                                    (17 more claude_cli models, all ok)
codex_cli                                            vendor=openai    ok
codex_cli:gpt-6-luna                                 vendor=openai    ok
…                                                    (7 more codex_cli models, all ok)
ollama:deepseek-v4.1-flash:cloud                     vendor=deepseek  ok
ollama:deepseek-v4.1-flash:1017-cloud                vendor=deepseek  ok
ollama:gemma4:31b-cloud                              vendor=google    ok
ollama:beam:cloud                                    vendor=?         UNAVAILABLE (not in `ollama list`; run `ollama pull beam:cloud`)  (unknown vendor: cannot judge)
…
omp_gateway:anthropic/claude-haiku-5-5               vendor=anthropic ok
…
```

`ollama list` held only three cloud models (`deepseek-v4.1-flash:1017-cloud`, `deepseek-v4.1-flash:cloud`,
`gemma4:31b-cloud`, size "-"): no local model was installed. V-SUPPORT has no local layer any more (cloud judges only).

**The models each task actually used** (from the `tasks` table and each `task.attempt` event's `model`):

| Driver (tasks.driver) | Vendor | Tasks | Model in `task.attempt` | Roles |
|---|---|---|---|---|
| `claude_cli:claude-haiku-5-5` | anthropic | 30 done, 1 failed (`rate_capped`) | `claude-haiku-5-5` (32 attempts) | scout, gapminer, ideator |
| `codex_cli:gpt-6-luna` | openai | 6 done | `gpt-6-luna` (6) | judge |
| `ollama:deepseek-v4.1-flash:cloud` | deepseek | 9 done | `deepseek-v4.1-flash` (9) | judge_2, triage |

`judge_3` (Haiku) never ran: every judge use checked text written by an Anthropic-vendor role (GapMiner, Ideator), so
it was skipped as `same_or_unknown_vendor` each time, as the vendor rule requires. Screener-B, Reader, Skeptic and
SimReader never ran because screening did not start.

**No task ran on a local model.** The checks, on the project database:

```sql
SELECT count(*) FROM tasks WHERE driver LIKE 'ollama:%' AND driver NOT LIKE '%:cloud';            -- 0
SELECT count(*) FROM tasks WHERE driver NOT IN ('claude_cli:claude-haiku-5-5', 'codex_cli:gpt-6-luna',
                                                'codex_cli', 'ollama:deepseek-v4.1-flash:cloud');  -- 0
SELECT count(*) FROM verifications
 WHERE json_extract(details, '$.p') IS NOT NULL OR json_extract(details, '$.layer1') IS NOT NULL;  -- 0
```

The last query looks for a local layer-1 probability in any verification record: there is none.

## Project

```
$ rk project create melatonin --title "Melatonin for delirium prevention" \
    --field "melatonin and melatonin receptor agonists for delirium prevention" --paper-type systematic_review
$ rk project intake --audience "clinicians in geriatric and perioperative medicine" --paper-type systematic_review \
    --mode d2 --production p2 --topics-n 3
$ rk run melatonin --until read_extract
started run_01M6VT2CY7J84ZGDJK8KWJHA9G on melatonin (until read_extract); follow it with: rk run status
```

A systematic review, so the run would exercise dual screening (screener_b), `screening_disagreements`, the PRISMA flow
and V-PRISMA. Three topics keep the topics stage cheap. One run goes from intake through question (the RQ is frozen on
the way) to read_extract.

The run paused 72 s after it started (`paused_budget`: "API budget exhausted for openalex"). The keyless OpenAlex
budget of this address ($0.10 a day) had been used up earlier that day; OpenAlex answered HTTP 429 with
`x-ratelimit-remaining-usd: 0`. `rk run resume` after the reset at 00:00 UTC started session 2, which reran the
landscape attempt from the start.

## Discovery

```
$ rk stage status            # after the run was cancelled
intake     passed   1
landscape  passed   2        (attempt 1 was cut off by the OpenAlex budget)
gaps       passed   1
topics     passed   1
question   passed   1
protocol   running  9        (cancelled; protocol v9 is frozen and in force)
search_screen reopened 1     (search_screen->protocol: used 1 of 2)
```

**Landscape (attempt 2).** 7 queries on OpenAlex and 7 on Europe PMC, 347 hits; 11 clusters, each with 3
representatives: "Melatonin and MRA meta-analyses (mixed populations)" 15 works, "Surgical and hospitalized melatonin
and ramelteon trials" 13, "Delirium guidelines and general reviews" 12, "Sleep, circadian rhythm and melatonin in
critical illness" 11, "Non-melatonin and non-pharmacological delirium prevention reviews" 11, "Protocols and methods
for melatonin and sleep-drug delirium trials" 11, "Postoperative delirium meta-analyses of melatonin" 10, "ICU
melatonin and ramelteon randomised trials" 9, "Melatonin dosing, pharmacokinetics and safety in hospital" 9, "ICU
melatonin and ramelteon meta-analyses" 7, "Ramelteon and orexin antagonists for delirium" 7. Over the whole project
(543 items): V-REF pass 200, mismatch 12, not found 10; V-RETRACT none 205, corrected 7, unknown 10.

**Gaps (attempt 1): 4 verified of 7 candidates.**

| Kind | Candidates | Verified | Why not |
|---|---|---|---|
| Contradiction | 2 | 2 | — |
| Evidence-map hole | 3 | 2 | 1: Triage put 4 searched works into the cell |
| Stated limitation | 1 | 0 | the quote was the literal text "placeholder" (`quote_not_found`) |
| Citation distortion | 1 | 0 | V-REF of the cited work is `mismatch` |

The evidence map (population/setting × intervention class) placed 135 works. The verified holes: ICU × orexin
antagonist (targeted query "suvorexant lemborexant daridorexant delirium prevention intensive care unit randomised
trial") and General or other settings × ramelteon or MRA class (query "ramelteon or melatonin receptor agonist delirium
prevention in outpatients or community-dwelling older adults").

**V-SUPPORT in gap mining.** Both contradictions pair the 2023 meta-analysis "Melatonin or Ramelteon for Delirium
Prevention in the Intensive Care Unit" ("Neither melatonin nor ramelteon reduced delirium incidence (RR 0.76 (0.54,
1.07), p = 0.12; I2 = 64%)") with two 2026 meta-analyses ("… a significant reduction in the incidence of delirium …
(Risk Ratio [RR] 0.75, 95% CI 0.63 to 0.90, P = 0.001, I2 = 50%)" and "… melatoninergic agonists reduce the incidence
of delirium (0.77 [0.62,0.94] …)"). For each, the V-SUPPORT record shows:

| Judge | Driver | Vendor | Admitted | Label | Spans verbatim |
|---|---|---|---|---|---|
| judge | `codex_cli:gpt-6-luna` | openai | yes | contradicted | yes |
| judge_2 | `ollama:deepseek-v4.1-flash:cloud` | deepseek | yes | contradicted | yes |
| judge_3 | `claude_cli:claude-haiku-5-5` | anthropic | no: `same_or_unknown_vendor` (the GapMiner is anthropic) | — | — |

Verdict `contradicted` (2 admitted judges, `min_judges` 2). V-NUM in the same records lists the numbers of quote A as
not found in quote B (`numbers_ok: false`), which is expected for a contradiction and did not change the verdict.

**Topics (3) and the stand-in's answer.** Each topic had 1 Scout task and 2 judges (openai, deepseek) for its 5
closest works; judge_3 was skipped (the Ideator is anthropic).

1. `tpc_01M6WG9GABBMA90347WHK4G3RA` Orexin antagonists for delirium prevention in ICU patients: mapping the evidence
   (scoping review).
2. `tpc_01M6WGA66175DD9SQ3CJ55HSCG` Ramelteon and other melatonin receptor agonists for delirium prevention outside the
   ICU (systematic review).
3. `tpc_01M6WGAVEXPX1PYSTP9T3EDVVC` Reconciling two contradictory meta-analyses of melatonin and ramelteon for ICU
   delirium (systematic review).

**Answer (stand-in researcher):** approve topic 2 (`rk checkpoint answer chk_01M6WGAVEYXDA5Q8XH62MZDG84 --yes --json
'{"decision": "approve", "topic_id": "tpc_01M6WGA66175DD9SQ3CJ55HSCG"}'`, with `RK_ALLOW_NONINTERACTIVE_ANSWER=1`
because the shell was not interactive). It is a systematic review, as the intake asked, and its population (wards,
surgery, older adults outside the ICU) is the audience's. Topic 3 rests on a contradiction that is mostly about
statistical significance (see below).

**Question.** The Ideator's first RQ passed the structural check. The already-done check ran 4 queries on OpenAlex and
Europe PMC (60 works); the judges (openai, deepseek) rated the top 10 and split on one: for "Pharmacologic Modulation
of Circadian Rhythms for Delirium Prevention: An Age-Stratified Systematic Review and Meta-Analysis" (2026), deepseek
said "answered", openai "not answered". So P2 opened `significance_novelty`.

> I am studying the effect of melatonin receptor agonists, including ramelteon, on incident delirium in older adults
> outside the intensive care unit because I want to find out whether the delirium-prevention benefit reported in ICU
> trials carries over to general ward, surgical or perioperative, and community-dwelling populations, and how it varies
> by population and setting in order to help my audience understand help clinicians in geriatric and perioperative
> medicine understand whether these agents are supported for delirium prevention outside critical care.

**Answers (stand-in researcher):** `proceed`, then `approve` in `rq_approval`. The age-stratified review pools
hospitalized adults with mixed settings; the RQ asks about non-ICU settings and how the effect varies by setting, which
it does not answer. The RQ was frozen at 02:41 UTC (`rq.frozen`, text hash `425f9601…`).

## Protocol: 8 drafts, then frozen

| Attempt | Outcome |
|---|---|
| 1–5 | rerun: "prisma_p.rationale claims novelty" (attempts 3–5 each after a `stage_blocked` answered `allow_one_more`) |
| 6 | passed the checks → `protocol_approval`; answer: revise (record budget) |
| 7 | passed → `protocol_approval`; answer: revise (record budget) |
| 8 | passed → `protocol_approval`; answer: approve with `record_cap` 300 → frozen as version 9 |

**Attempts 1–5, the novelty loop.** The Ideator's rationale said what was not found within the searched scope, and
then added a disclaimer: "This is a statement about what was not found within the searched scope; it is not a claim
that the question or the review is novel." The kernel refuses every "novel", by design, even negated. Its rerun reason
did not say which word was refused, and it repeated "say what was not found within the searched scope", so each rerun
wrote the disclaimer again. After the fourth identical failure the run was parked at the `stage_blocked` checkpoint
and the kernel was fixed in a separate commit (`fix(production): novelty refusals name the matched word`): the reason
now names the words and says "do not use these words, not even negated or in a disclaimer". Attempt 5 still ran on the
old guidance (stored with the checkpoint) and failed with the new reason, `prisma_p.rationale claims novelty ("novel",
"unprecedented")`; attempt 6, given that reason, passed.

**Attempts 6–8, the record budget.** Version 6's strategy (OpenAlex, Europe PMC, PubMed; 5 queries, e.g. "melatonin
OR ramelteon OR tasimelteon OR agomelatine delirium prevention older adults randomized controlled trial") had a probe of
26,717 records; that query alone gave 22,189 hits on OpenAlex.

**Answer (stand-in researcher), version 6:** revise. "The record budget for this demo review is 150 records … every
query should require delirium together with melatonin, ramelteon or melatonin receptor agonist … Keep at least 3
queries on at least 2 sources. Eligibility, extraction schema and synthesis plan are fine as they are." In P2 a revise
reruns the stage on the protocol→protocol edge, so each one also needed `allow_one_more`.

Version 7 used boolean queries; the probe was 10,951 (OpenAlex 3,388 and 3,722 for two of them).

**Answer, version 7:** revise. The budget was raised to 300 records, with the request to use only PubMed and Europe PMC
and short, specific queries.

Version 8 (approved): sources PubMed and Europe PMC; years 2000 to now; English; queries "melatonin delirium
randomized", "ramelteon delirium", "melatonin postoperative delirium", "melatonin delirium elderly prevention"; probe
3,656 (Europe PMC only; PubMed reports no count). Eligibility I1–I6 / E1–E7 (RCTs or prospective controlled studies;
age ≥ 60 or the study's "older adults"; non-ICU settings; melatonin receptor agonists, exogenous melatonin as a
subgroup; placebo, no prophylaxis or usual care; incident delirium by a validated tool). Extraction schema: 24 fields
(e.g. setting, agent, dose, events and arm sizes, delirium tool, risk of bias). Synthesis plan: random-effects
meta-analysis of risk ratios per setting, SWiM below three studies, pre-specified sensitivity analyses.

Before approving, the stand-in checked the size with the sources' own counts: the union of the four queries on Europe
PMC was 1,989 records from 2000 on, and PubMed added up to about 250 (its four queries give 156, 110, 124 and 91,
overlapping). A 300-record cap could not hold. A cap near 2,000 would have meant about 80 title/abstract tasks per
screener (screener_b on codex at 20 calls an hour: over 4 hours). A narrower window was checked too: from 2024 on, the
union was 715 on Europe PMC plus 84 on PubMed, still above a 700 cap. So it approved with 300, to show the cap, and
the demo stopped there.

**Answer (stand-in researcher), version 8:** `{"decision": "approve", "record_cap": 300}`.

```
protocol.frozen  frozen_hash 6b711e7b5b89cfb55969a67d4a09a16d49c19240f0381dda02cb2dbcddf7e18e
$ rk protocol show
prt_01M6WHJWTCQA9F9EH91541VSEC: version 9, frozen (hash 6b711e7b5b89), in force
Record cap: 300 records.
```

The frozen protocol is stored as a new version (9, the approved draft with the record cap set), so versions 1–8 stay
drafts. `rk protocol show` reports "preregistration (G-PREREG): not met" for the drafts; version 9 is the frozen one.

## search_screen: the record cap

```
search_screen attempt 1, 03:00:15–03:00:23 UTC
  pubmed     "melatonin delirium randomized"   page of 200, 155 works
  europepmc  "melatonin delirium randomized"   page of 200, 199 works, stopped_at_cap
stage.ran  back_edge: "at least 316 records (search stopped at the cap) > cap 300: narrow the search strategy"
stage.reopened  protocol (by search_screen->protocol, 1 of 2)
```

The search stopped after the first query, as soon as the deduplicated works passed the cap: 356 records, 40
duplicates, 316 distinct. No screening ran. The search_screen→protocol back-edge reopened protocol (attempt 9); its
Ideator task ran, and its Scout task came back `rate_capped` (Haiku's 30 calls an hour were used). The run was then
cancelled (03:02 UTC), which ended the demo.

Not reached, so not recorded: screening agreement and disagreements, full-text retrieval channels, extraction coverage
and problems, V-PRISMA on a finished flow.

## Workbench

Two screenshots were taken with headless Chromium against the run's Workbench (not committed):

- **The protocol approval card** (Checkpoints, version 8): the RQ; the search strategy (sources pubmed, europepmc,
  publication years 2000 to now, English, the four queries in monospace); "3000 records after deduplication" with the
  advisory probe (3,656, per query on Europe PMC), "Not counted (no record count): pubmed", and the warning that the
  probe is above the cap; then eligibility (include and exclude side by side), the extraction schema, the synthesis
  plan and PRISMA-P below.
- **The Progress page after the cancel:** the stage cards (Protocol "attempt 9, reruns: 7 of 7", Search Screen
  "Pending: to run again, back to protocol: 1 of 2", Synthesize to Export "Not yet available"), the run "Cancelled —
  cancelled by you" with its resume and start buttons, and "Search and screening": the PRISMA flow of search_screen
  attempt 1 with Identified 356, Duplicates removed 40 and 0 in every later box, under the red line "The counts do not
  add up (V-PRISMA): identified 356 - duplicates removed 40 = 316, but 0 records were screened". The task table below
  shows each task's role and driver (`claude_cli:claude-haiku-5-5` for the protocol tasks, one `rate_capped`).

There was no extraction to show in a library item.

## Task counts and wall time

| Stage | Tasks |
|---|---|
| landscape | scout 3 (1 in attempt 1, 2 in attempt 2) |
| gaps | gapminer 4 (1 retried after `claude error_max_turns`), triage 3, judge 2, judge_2 2 |
| topics | ideator 1, scout 3, judge 3, judge_2 3 |
| question | ideator 1, scout 1, judge 1, judge_2 1 |
| protocol | ideator 9, scout 9 (attempt 9's scout `rate_capped`) |

46 tasks: 45 done, 1 failed (`rate_capped`). One rate-cap hit, at the very end.

Wall time:

- Session 1: 72 s (20:09:17–20:10:29 UTC 2026-11-07), until the OpenAlex budget pause.
- Session 2: 37.7 min (02:24:33–03:02:13 UTC 2026-11-08), of which 17.3 min were waits on the 11 checkpoints. That
  includes 6.9 min at one `stage_blocked` while the novelty fix was written, tested and committed, and `rk serve` was
  restarted.
- Per stage (session 2): landscape 2.9 min, gaps 9.3 min, topics 1.6 min and question 0.9 min (each until its
  checkpoint), protocol 19.2 min including the waits on its checkpoints, search_screen 8 s.

## What failed or was weak (calibration input)

**Fixed during the demo** (separate commits):

1. `fix(production): novelty refusals name the matched word`. The protocol stage looped on "claims novelty" because the
   reason did not name the refused word (above). It was not a crash, but this Ideator could not get past protocol.
2. `fix(tests): tests never touch the real RK_HOME`. One test (`test_capabilities_lists_browser_channels`) saved
   settings without a temp home, so every full test run overwrote `$RK_HOME/config.toml`. The test run for fix 1 had
   the demo's scratch `RK_HOME` in its environment and overwrote the demo's config while the run was parked at a
   checkpoint (no task ran on it; the config was restored). Every test now gets a temp `RK_HOME`.

**The search strategy cannot be sized for a small cap.** The Scout's queries run on every source as written. Europe PMC
and OpenAlex match full texts, so a systematic-review query that PubMed answers with about 150 records gives hundreds
to thousands on them ("melatonin delirium" from 2000 on: 2,577 on Europe PMC). Neither source's field syntax works on
the other (PubMed `melatonin[tiab] AND delirium[tiab]`: 9 on Europe PMC; Europe PMC `TITLE_ABS:(melatonin AND
delirium)`: 0 on PubMed), and the protocol needs at least 2 sources. Per-source queries or a title/abstract switch per
source would be needed to run a narrow, affordable systematic review end to end.

**The record-cap probe leaves out PubMed** and overestimates the others (no date range, duplicates count again). The
stand-in needed its own hit counts to see that 300 would not hold.

**A revise uses up the protocol's reruns.** In P2 each `protocol_approval` revise counted on protocol→protocol, which
the novelty loop had already used up, so each revise also needed a `stage_blocked` answer (11 checkpoints in all).

**The PRISMA panel shows a capped attempt as a V-PRISMA failure.** For an attempt that went back to protocol at the cap,
the Progress page draws the flow with "The counts do not add up (V-PRISMA)". The real reason (the record cap) is in
the stage's reason, not on the panel.

**The contradictions are weak.** Both verified contradictions set a non-significant pooled RR (0.76, 95% CI 0.54 to
1.07) against significant ones (0.75 and 0.77). The point estimates nearly agree; the disagreement is about
significance. Both judges called it "contradicted", so the gap passed, and topic 3 builds on it.

**Gap mining output.** The stated-limitation candidate came with the literal quote "placeholder". The distortion
candidate's cited work failed V-REF.

**The RQ is wider than its gap.** The approved topic rests on the hole "General or other settings × ramelteon or MRA
class", but the RQ also covers surgical and perioperative patients, where the evidence map placed 10 ramelteon/MRA
works (and 12 in medical or mixed inpatients). The FINER "novel" text admits the gap is narrower than the topic says.

**The already-done check missed an obvious neighbour.** Both judges rated "Melatonin or ramelteon for the prevention
of postoperative delirium in elderly patients: A systematic review and meta-analysis of randomized clinical trials"
(2026) "not answered", quoting only its title. It answers the RQ's surgical part.

**Other weak points:**

- **OpenAlex keyless budget:** $0.10 a day per address; the run paused for 6 hours on it. No `openalex_key` was set.
- **Haiku hourly cap:** 30 calls an hour; session 2's 30 Haiku calls (29 tasks and one retry) in 36 min reached it.
- **codex CLI version:** 0.167.0-alpha.3.2 differs from the pinned 0.166.0-alpha.6 (`rk providers` warns). Its 6
  judge tasks succeeded.
- **The RQ's three-step sentence** reads "… in order to help my audience understand help clinicians …": the audience
  text is pasted into the template without fitting it.
