# Plan 5b demo: melatonin review from protocol to synthesize, cloud models only (2026-11-08)

Recorded on macOS arm64 with a scratch `RK_HOME` (a copy of the Plan 5a demo home, so the 5a run stays as it was),
`rk serve` with a headless built-in browser, and the live APIs (Europe PMC, PubMed, OpenAlex, Crossref). Everything
below comes from this run's event log, its database and CLI output; ids are the run's own.

**The checkpoints were answered by the agent that ran the demo, standing in for the researcher.** Every answer is
recorded below with its reason. In real use only you answer them.

**How far it got.** `uv run rk run melatonin --until argue` reran protocol (frozen again with a 2024-to-now window and
a record cap of 800), then search_screen, read_extract and two synthesize attempts. **The run did not reach argue.**
It is parked at a `stage_blocked` checkpoint of synthesize (attempts 3 and 4 failed at once: the Claude subscription
had hit its session limit). So:

- **no meta-analysis was done** (the pre-specified one fell back to a narrative synthesis, with a recorded deviation),
  and no statistic or figure was made, so V-STATS had nothing to check;
- **no argument rounds, objections, warrant tests or conflicts** were produced;
- **design was not demonstrated live** (see "Design").

The screening, full-text and extraction part is recorded in `docs/demo/plan5a-evidence.md`, section "Screening and
extraction, run in Plan 5b"; the key numbers are repeated here.

## Setup: models, and no local model

The drivers are those of the Plan 5a demo: every Anthropic-vendor role (Scout, Screener-A, Reader, Ideator, Arguer,
…) on Claude Haiku 5.5 (`claude_cli:claude-haiku-5-5`), Screener-B and the Skeptic on the codex CLI, `judge` on
`codex_cli:gpt-6-luna`, `judge_2` and `triage` on `ollama:deepseek-v4.1-flash:cloud`, `judge_3` on Haiku. `rk
providers` showed every role `ok` (codex 0.167.0-alpha.3.2, which differs from the pinned version: a warning only).
`ollama list` held only the three `:cloud` models.

Before the run, `rk eval stats --write-config` was run against this home: 180/180 published values reproduced
(`docs/eval/stats-2026-11-08.md`), `stats_validated` recorded, so meta-analysis was enabled.

**Tasks of this demo** (tasks created from 09:19 UTC, `tasks` table):

| Driver | Vendor | Done | Failed | Roles (done) |
|---|---|---|---|---|
| `claude_cli:claude-haiku-5-5` | anthropic | 44 | 14 `rate_capped`, 2 "claude success (exit 1)" (session limit) | screener_a 30, arguer 6, ideator 3, scout 3, reader 2 |
| `codex_cli` | openai | 30 | 11 `rate_capped` | screener_b 30 |
| `codex_cli:gpt-6-luna` | openai | 10 | 1 `rate_capped` | judge 10 |
| `ollama:deepseek-v4.1-flash:cloud` | deepseek | 10 | 0 | judge_2 10 |

The `task.attempt` events name the models: `claude-haiku-5-5` (64 attempts), `gpt-6-luna` (11), `deepseek-v4.1-flash`
(10), and 41 codex attempts with an empty model field (the `codex_cli` driver without a model runs the CLI's default
model, which the attempt does not record). `judge_3` (Haiku) never ran: every claim it would check was written by an
Anthropic-vendor role, so it was skipped (`same_or_unknown_vendor`).

**No task ran on a local model.** On the project database:

```sql
SELECT count(*) FROM tasks WHERE driver LIKE 'ollama:%' AND driver NOT LIKE '%:cloud';            -- 0
SELECT count(*) FROM tasks WHERE driver NOT IN ('claude_cli:claude-haiku-5-5', 'codex_cli:gpt-6-luna',
                                                'codex_cli', 'ollama:deepseek-v4.1-flash:cloud');  -- 0
SELECT count(*) FROM verifications
 WHERE json_extract(details, '$.p') IS NOT NULL OR json_extract(details, '$.layer1') IS NOT NULL;  -- 0
```

## Protocol, rerun (attempts 10–12)

| Attempt | Outcome | Answer (stand-in) |
|---|---|---|
| 10 | rerun: "synthesis_plan.pool_model is required for a meta-analysis (REML, DL or FE); synthesis_plan.effect_measure must be one of RR, OR, RD, MD or SMD …; synthesis_plan.outcomes is required" (the Plan 5b typed synthesis plan) | `stage_blocked`: allow_one_more |
| 11 | passed → `protocol_approval` (version 11): effect measure RR, `pool_model` DL, one outcome "incident delirium" mapped to the four count fields; but every query ended in `AND NOT ("intensive care" OR ICU)` | revise (below), then `stage_blocked`: allow_one_more |
| 12 | passed → `protocol_approval` (version 12) | approve with `record_cap` 800 |

**Revise (version 11):** use years 2024 to now and the four short queries of version 8 on PubMed and Europe PMC; no
NOT clauses, because Europe PMC matches full texts and would drop ward and surgical trials that merely mention the
ICU (the ICU exclusion is eligibility E1's job); pool with REML and HKSJ "auto" (the Cochrane Handbook 6.5.1
defaults). Version 12 did exactly that and kept eligibility, schema and PRISMA-P unchanged.

**Approve (version 12):** the stand-in first counted the sources (Europe PMC union 2024–2026: 715; PubMed 43, 29, 45,
32 per query, union 84), so 800 would hold. The approval froze version 13 (the draft with the cap, hash
`d7613188…`) and recorded a version deviation from the frozen version 9.

**Method pre-specified:** random-effects meta-analysis of the risk ratio for incident delirium (REML, HKSJ auto,
prediction interval with the CI's df), "if fewer than three eligible studies are found, the synthesis is narrative
(SWiM)".

## search_screen and read_extract (summary)

PRISMA: identified 1,468, duplicates removed 753, screened 715, excluded at title/abstract 707, sought 8, not
retrieved 5, assessed at full text 3, excluded 1 (I2: aged 40–80), **included 2**; V-PRISMA: the counts add up.
Screening agreement at title/abstract: 94.8% raw, Cohen's κ 0.31; 45 disagreements (mostly records without an
abstract) went to the stand-in (7 include, 38 exclude), 1 at full text (included after `rk find` showed the trial
used the 3D-CAM). Full texts: all 3 retrieved ones came as Europe PMC JATS; the browser fallback fetched nothing.

The two included trials:

- **Al Alawi 2026 (RESTORE)**, general medical wards, melatonin 5 or 8 mg vs placebo, 2/55 vs 1/54 delirium (3D-CAM).
- **Khaled 2025**, pilot RCT after major non-cardiac surgery, melatonin 3 mg vs placebo, 7 vs 2 delirium events.

read_extract: 39 values with a verified quote, 10 `not_reported`, 0 with a problem. For Khaled 2025 the Reader marked
`n_intervention` and `n_control` (and mean age, sex) `not_reported`. They are in the paper's Table 3 (43 and 42), but
the JATS text layer had no table cells: see "Defect found and fixed".

## Synthesize

**Assessment** (Arguer, attempt 1; seven criteria 0–2): Al Alawi 2026 accurate 2, authoritative 1, clear 2, precise 0,
relevant 2, representative 1, sufficient 1; Khaled 2025 accurate 1 ("unclear whether these are events or group
sizes"), authoritative 1, clear 1, precise 0, relevant 2, representative 1, sufficient 1.

**Method chosen: narrative, not meta-analysis.** The Arguer's pooling plan put both trials in one group (incident
delirium, RR) but called it not comparable, so the kernel did not pool and recorded the fallback as an **analysis
deviation** from version 13 (kind `analysis`, version 13 → 13; G-PREREG ignores it):

- attempt 1: "Only Al Alawi 2026 has a computable RR, because Khaled 2025 reports delirium events but not the
  intervention and control denominators … The two studies also come from different settings …, which the protocol
  pre-specifies as the pooling stratum";
- attempt 2: "Only two studies report this outcome, below the protocol's three-study minimum for a meta-analysis, so
  the protocol's fallback to narrative synthesis applies".

No `pooling_approval` checkpoint opened (no comparable group), no `stt_`/`fig_` artifact was made, and **V-STATS ran
on nothing**. The honest k is 1 computable study (pre-fix); with the fixed text layer it would be 2, but see below.

**Findings and verdicts (attempt 1).** Seven findings, judged by `judge` (openai) and `judge_2` (deepseek); `judge_3`
skipped (the Arguer is anthropic). V-HEDGE passed on all seven.

| Finding (shortened) | V-SUPPORT | Stand-in (`uncertain_support`) |
|---|---|---|
| Two included studies, one on general medical wards (Al Alawi 2026), one after non-cardiac surgery (Khaled 2025) | uncertain | supported |
| Neither trial reports a lower delirium incidence with melatonin; the counts are too few to show a difference | uncertain | supported |
| The evidence does not show whether the ICU benefit carries over, and is too limited to show that it does not | uncertain | supported |
| No included study tests ramelteon; both test oral melatonin, 3 to 8 mg | uncertain | supported |
| Four withdrawals for adverse reactions; no significant sedation; this does not establish safety | uncertain | supported |
| The surgical trial's group sizes and event counts are unclear, so no pooled effect can be estimated | uncertain | **withdraw**: the group sizes are in Table 3; "unclear" came from the text-layer defect |
| No included study tests melatonin in community-dwelling older adults | **unsupported** | (not offered) |

The judges mostly answered "partial" (over attempts 1 and 2: `judge` 8 partial, 2 unsupported, 1 failed on its cap;
`judge_2` 8 partial, 1 supported, 1 unsupported): the quoted spans support pieces of each finding, not its whole.
**Withdrawn:** 1 (by the stand-in). **Narrowed:** none.

**Attempt 1 failed its gate anyway**: "clm_01M6XEX4ASW903A4CHE07Y1JNR: status unsupported (not supported)". The
`uncertain_support` checkpoint lists only uncertain findings, so the stand-in could not withdraw the unsupported one;
the stage reran. Attempt 2 wrote six new findings (three uncertain, three still unverified) and was interrupted when
`judge` (codex) hit its hourly cap; attempts 3 and 4 failed at once ("the arguer gave no study assessment (claude
success (exit 1))": the Claude CLI answered "You've hit your session limit"). That used the synthesize→synthesize
reruns, so P2 opened `stage_blocked` (`chk_01M6XFJ9PYMAXFCHMXZCRZ5XAH`, 11:44 UTC). It was left open: the run can be
resumed from there.

**Conflicts, argument rounds, objections, warrant tests:** none, because argue did not run.

## Defect found and fixed: JATS tables

`rk.text.markup.jats_to_layer` read only titles and paragraphs, so every `<table-wrap>` cell was lost. Fixed in its
own commit, `fix(text): JATS tables in the text layer`: each table (label and caption, one line per row with cells
joined by " | ", footnotes) is appended after the body, so layers built earlier keep their offsets. Tests: the table
text and its place, a quote from a table cell passing V-QUOTE (`verify_quote` and `verify_quote_at`), and old
locators still verifying against the rebuilt layer.

On the demo home the two included texts were rebuilt through the existing refetch path (`rk fetch <item>`: same
snapshot hash, the layer file is rewritten). Khaled 2025's layer grew from 30,785 to 35,884 characters and now holds
"Table 3. Incidence of postoperative delirium." with the row "| Melatonin Group (n = 43) | Placebo Group (n = 42) |
…" and "Incidence of POD, n (%) | 7 (16.3) | 2 (4.9) | …"; "Melatonin Group (n = 43)" passes V-QUOTE at its place in
the new layer (it does not in the old one). All 44 verified quote notes on the two rebuilt layers still verify at
their stored locators.

**Unmet: re-extraction / pooled k=2 — blocked by gaps (a) and (b) below; to be redone on a fresh demo home after the
5b fix wave.** The planned re-extraction after the fix did **not** happen. The synthesis above is the pre-fix
narrative fallback (1 computable study). Nothing here shows a pooled result.

- **(a) A human has no way to rerun read_extract.** Only back-edges reopen a stage (`record_reopened` in the engine);
  `rk run start` has only `--until`, and no CLI or Workbench action reopens a passed stage. The `uncertain_support`
  answer `{"decision": "rerun", "comment"}` reruns **synthesize only** (`StageOutcome("rerun")`), on the same
  read_extract attempt.
- **(b) synthesize→read_extract fires only when no study of a group has a value** for a field the measure needs
  (`SynthesizeWorker._absent`). Here Al Alawi 2026 has `n_intervention` and `n_control`, so Khaled 2025's missing
  denominators never send the stage back; and a `not_reported` row counts as "verified, reading again cannot find
  it", even when the text layer was the cause.

## Design

**Design was demonstrated only with the test fixtures** (`tests/test_design.py`, the design cards in
`tests/test_ui_argument.py`). No `research_proposal` project with a frozen RQ exists in any scratch home (the only
frozen RQ is this melatonin systematic review's), and design comes after argue, which this run did not reach either.
A proposal would need discovery or a D1 RQ, then protocol, search, screening, extraction, synthesize and argue first,
on the same hourly caps that took most of this demo's time.

## Wall time

- Session 1 of this run: 09:19:42 – 11:44:13 UTC (2 h 25 min) until the `stage_blocked` checkpoint.
- protocol 4.8 min including three answers; search_screen 2 h 5 min (09:24–11:29), most of it waiting for the Haiku
  (09:33–10:20) and codex (about 10:40–11:25) hourly caps; read_extract 2 min (11:29–11:31); synthesize attempt 1
  2 min plus 4 min waiting on `uncertain_support`; attempt 2 until the codex cap at 11:39; attempts 3–4 failed in
  8 s at 11:44.
- `rk serve` was restarted once (11:35) to load the JATS fix; the run resumed itself.

## What failed or was weak (calibration input)

1. **JATS tables were not in the text layer** (fixed, above); the re-extraction it calls for did not happen (gaps (a),
   (b)).
2. **An unsupported finding cannot be withdrawn by you in synthesize**: `uncertain_support` offers only the uncertain
   ones, and the gate fails on the unsupported one, so the stage reruns and writes all findings again.
3. **The hourly caps dominate.** Screener-B waited while Screener-A was capped; each wait was a new search_screen
   attempt every 5 minutes (attempts 3–27), and each reran the whole search and stored all 1,468 records again
   (38,168 record rows); 25 `rate_capped` screening failures, 1 judge failure. Then the Claude subscription's session
   limit stopped synthesize.
4. **Screening agreement is low (κ 0.31)**: screener_b says "unclear" for nearly every record without an abstract,
   which sends conference sections and editorials to you.
5. **The browser fallback fetched nothing** for the 5 records without an open-access text (no `browse_log` row, about
   0.1 s per lookup); cause not investigated. They were "not retrieved", not assessed.
6. **The Reader put prose where note ids belong** (8 extraction problems "… is not a note of this study"), and
   recorded a median age as `mean_age`.
7. **The protocol rerun needed the typed synthesis plan twice** (attempt 10 failed on it) and the Scout answered the
   5a record-cap back-edge by adding `NOT (… ICU)` to every query.
8. **Judges answered "partial" on most findings**: synthesis findings that combine two studies are rarely fully
   supported by quoted spans; every finding went to the stand-in.
9. **codex attempts record no model** when the `codex_cli` driver runs the CLI default.
10. **Protocol leftovers**: eligibility I6 and sensitivity analysis (e) name a 2019 trial that the 2024-to-now window
    excludes.
