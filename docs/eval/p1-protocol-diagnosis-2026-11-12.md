# P1 protocol-stage diagnosis: why every vote says `revise`

Read-only analysis, 2026-11-12 UTC. Data: the 102 protocol vote opinions (`vote.decided`), the 20 project DBs under `$TMPDIR/rk-plan5c-{srrepro,srrepro2,srrepro3,p1}/projects/*/rk.sqlite` (`events`: `protocol.added`, `protocol.probed`, `vote.decided`, `stage.attempt`, `stage.reopened`, `task.created` inputs), and `src/rk/production/{protocol,vote}.py`, `src/rk/orchestrator/{engine,state}.py` at c312168. The stale-guidance defect (§5.3) was checked again on R1/ozone: attempt 4's guidance is the attempt-2 vote's reasons; the attempt-3 vote's never reached an attempt.
Home abbreviations: **R1** = rk-plan5c-srrepro, **R2** = rk-plan5c-srrepro2, **R3** = rk-plan5c-srrepro3, **P1** = rk-plan5c-p1. Machine-readable point list: `p1-protocol-points-2026-11-12.tsv` (this directory).

## 1. Method

- 51 votes (102 opinions) over 19 project runs. The 20th run (R2/prostate) never reached a vote: all 9 versions failed the structural outcome-mapping check (diagnostic-accuracy outcomes cannot be mapped onto the roles of RR/OR/RD/MD/SMD).
- Labels: 98 `revise`, 4 `approve` — all four from deepseek (R2/cade a2, a3; R2/fructose a2; R3/tfa a1). gpt-6-luna approved 0 of 51. No vote passed.
- Each reason was split by hand into atomic points; a point is one (opinion, category) pair (several items of the same kind in one opinion, e.g. "no registration, no funding, no dissemination", count once). Result: **409 points in 42 categories**.
- Classes: **A** — an existing check should have caught it; **B1** — a new deterministic pre-vote check over the protocol body/probe catches it; **B2** — deterministic, but the remedy is kernel-written text (a fact only the kernel knows), not a check; **C** — needs domain judgment. Suffixes: `B1+B2` (presence check + kernel-written content), `B2/C` (kernel text answers most of it, the rest is judgment), `B1(K)` (detectable, but the fix needs a kernel capability that is missing), `B1?` (rule has medium false-positive risk).
- To check fixes against the bodies rather than the reasons, 19 categories got a prototype detector (Python over each voted version's body and probe; the rules in §6). Each voted version was run through all detectors, so "fixed" below means the fault is gone from the body, not that the judges stopped mentioning it.

## 2. Headline findings

1. **Most points are mechanical.** A 23 (5.6 %), B 263 (64.3 %: B1-family 189, B2 47, B2/C 27), C 123 (30.1 %). The three largest categories (PRISMA-P admin items, PRISMA-P method items, uncounted sources) are all deterministic.
2. **The authors rarely fix what is raised.** For the detectable categories, 106 raised instances have a next voted version; the fault is gone from that version in only **28 (26 %)**. `search.source_uncounted` 3/13 fixed, `prisma_p.methods_missing` 1/20, `search.no_start_date` 0/8, `prisma_p.placeholder` 0/5, `synthesis.dta_measure_mismatch` 0/6. Several of these the authors *cannot* fix (PubMed uncounted, OpenAlex budget, no DTA measure in `MEASURES`, registration/funding facts).
3. **The judges are a moving target.** 321 (project, attempt, category) instances; **233 (73 %) were raised by one judge only** (gpt 114, deepseek 119). Of the 170 instances at a later vote, **86 (51 %) are categories not raised before** in that project. 16 of those were detectably present in earlier versions that the judges had passed over (e.g. the language conflict: 6 of its 7 late first raises were present from attempt 1). 37 instances fell silent while the fault stayed in the body.
4. **Fixing one point creates the next.** `prisma_p.placeholder` first appears at a later vote in 8 projects. In 6 of them it is an admin fact the judges had asked for at the previous vote. The Ideator cannot know registration, funding or competing interests, so it writes "to be completed by the researcher before freezing" / "identifier recorded when assigned", and the next vote rejects the placeholder: R1/prostate a3–a4, R1/pulmhyp a3, R2/tfa a2, R3/cade a2–a3, R3/pulmhyp a3–a4, R3/tfa a3. The other 2 are deferred work: R2/ozone a2 ("OpenAlex … must be re-run before the search is final") and P1/probiotics2 a3 ("fixed list of agents written in the full protocol before extraction").
5. **Guidance is lossy.** Each attempt gets only the last vote's joined text; nobody sees earlier rounds; judges see neither earlier reasons nor a diff. Two concrete defects: (a) after the edge-limit failure, **all 7 runs that went on to an attempt 4 got attempt-2's guidance** (or a novelty-check message), and the attempt-3 vote reasons were dropped (§5.3); (b) **6 redrafts dropped PRISMA-P items** that the previous draft had, because `DRAFT_TASK` names only the 5 checked items and the later guidance no longer mentions the earlier request (§5.4).
6. **Deterministic rules can cover at most ~2/3 of points, and they will not produce approvals alone.** The recommended rules (§6) cover 280/409 points (68.5 %; 208, i.e. 50.9 %, if the partial ones are left out), but only 6 of 51 votes consisted of A/B points only. In every other vote at least one judge raised a C point. Approval also needs 2a: numbered points that are tracked, answered, and closed.
7. **Diagnostic-accuracy (DTA) topics cannot pass.** `MEASURES` has no DTA measure. prostate/pulmhyp in every home either failed the structural check outright (R2/prostate: no vote; R2/pulmhyp a3, R1/pulmhyp a4: the run ended on mapping failures) or drew `dta_measure_mismatch` + `missing_dta_cells` from both judges at every vote (24 points).

## 3. Categories (count, class, dynamics)

Columns: *inst.* = (project, attempt) instances; *one-judge* = instances raised by only one judge; *with next* = instances that have a later voted attempt; *not re-raised* / *re-raised* / *silent-persist* (not re-raised although the detector still finds the fault); *first raised late* = first raised for the project at a later vote (in brackets: the fault was already detectably present before).

| category | class | points | projects | inst. | one-judge | with next | not re-raised | re-raised | silent-persist | first raised late |
|---|---|---|---|---|---|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | 40 | 18 | 29 | 18 | 20 | 6 | 7 | 7 | 2 (2) |
| prisma_p.methods_missing | B1 | 32 | 18 | 25 | 18 | 20 | 1 | 5 | 14 | 1 (1) |
| search.source_uncounted | B1 | 32 | 9 | 18 | 4 | 13 | 3 | 9 | 1 | 0 (0) |
| search.not_reproducible | B2/C | 27 | 14 | 24 | 21 | 19 | 9 | 10 | 0 | 2 (0) |
| search.cap_exceeded | A | 20 | 6 | 10 | 0 | 8 | 4 | 4 | 0 | 0 (0) |
| prisma_p.placeholder | B1 | 19 | 8 | 12 | 5 | 5 | 0 | 4 | 1 | 8 (0) |
| eligibility.language_conflict | B1 | 17 | 11 | 14 | 11 | 8 | 4 | 3 | 1 | 7 (6) |
| search.no_start_date | B2 | 14 | 8 | 12 | 10 | 8 | 0 | 3 | 5 | 1 (1) |
| synthesis.dta_measure_mismatch | B1(K) | 13 | 5 | 9 | 5 | 6 | 0 | 4 | 2 | 1 (0) |
| search.prose_vs_strategy | B1 | 12 | 6 | 7 | 2 | 3 | 1 | 1 | 1 | 6 (0) |
| search.scope_limited | C | 12 | 10 | 11 | 10 | 7 | 6 | 1 | 0 | 6 (0) |
| extraction.missing_dta_cells | C | 11 | 4 | 8 | 5 | 5 | 2 | 3 | 0 | 0 (0) |
| search.cap_handling_unspecified | B2 | 11 | 8 | 11 | 11 | 8 | 5 | 3 | 0 | 4 (0) |
| search.language_unjustified | B2 | 11 | 9 | 9 | 7 | 8 | 8 | 0 | 0 | 4 (0) |
| search.query_sensitivity | C | 11 | 8 | 9 | 7 | 8 | 7 | 1 | 0 | 4 (0) |
| synthesis.multiplicity | C | 11 | 6 | 8 | 5 | 4 | 2 | 2 | 0 | 3 (0) |
| synthesis.missing_data_rules | C | 10 | 8 | 10 | 10 | 7 | 6 | 1 | 0 | 3 (0) |
| extraction.missing_study_fields | C | 9 | 9 | 9 | 9 | 8 | 8 | 0 | 0 | 1 (0) |
| prisma_p.vague_incomplete | C | 9 | 8 | 9 | 9 | 8 | 7 | 1 | 0 | 2 (0) |
| synthesis.unit_of_analysis | C | 9 | 7 | 9 | 9 | 5 | 3 | 2 | 0 | 5 (0) |
| eligibility.internal_conflict | C | 7 | 6 | 6 | 5 | 3 | 3 | 0 | 0 | 4 (0) |
| extraction.field_vs_text | C | 7 | 6 | 7 | 7 | 3 | 2 | 1 | 0 | 5 (0) |
| synthesis.no_outcomes | B1 | 7 | 2 | 4 | 1 | 3 | 1 | 2 | 0 | 0 (0) |
| extraction.unit_conversion | C | 6 | 3 | 5 | 4 | 3 | 2 | 1 | 0 | 1 (0) |
| prisma_p.rob_tool_unspecified | B1? | 6 | 4 | 5 | 4 | 5 | 4 | 1 | 0 | 1 (0) |
| synthesis.estimand_or_subgroup | C | 6 | 3 | 5 | 4 | 4 | 3 | 1 | 0 | 0 (0) |
| synthesis.min_studies_vs_pi | B2 | 5 | 5 | 5 | 5 | 4 | 2 | 0 | 2 | 3 (3) |
| extraction.denominator_ambiguity | C | 4 | 3 | 4 | 4 | 3 | 2 | 1 | 0 | 1 (0) |
| search.dedup_unspecified | B2 | 4 | 4 | 4 | 4 | 4 | 4 | 0 | 0 | 0 (0) |
| synthesis.rob_integration | C | 4 | 3 | 3 | 2 | 3 | 3 | 0 | 0 | 0 (0) |
| extraction.no_study_id | B1 | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 | 1 (1) |
| search.probe_text_mismatch | B1 | 3 | 2 | 2 | 1 | 1 | 0 | 0 | 1 | 2 (0) |
| synthesis.outcome_coverage | C | 3 | 2 | 3 | 3 | 3 | 2 | 1 | 0 | 0 (0) |
| eligibility.not_operational | C | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 | 2 (0) |
| search.openalex_unreadable_query | A | 2 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 (0) |
| search.query_syntax | B1 | 2 | 1 | 1 | 0 | 1 | 1 | 0 | 0 | 0 (0) |
| search.zero_hit_query | B1 | 2 | 1 | 2 | 2 | 1 | 0 | 1 | 0 | 0 (0) |
| synthesis.pool_keys_on_narrative | B2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 | 2 | 1 (1) |
| eligibility.missing_criterion | C | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 1 (0) |
| prisma_p.internal_note | B1 | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 1 (1) |
| synthesis.outcome_mapping_unchecked | A | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 1 (0) |
| text.unexplained_reference | C | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 | 1 (0) |

### Class shares

| class | points | share |
|---|---|---|
| A (existing check / advisory probe should have stopped it) | 23 | 5.6 % |
| B1 family (B1, B1+B2, B1(K), B1?) | 189 | 46.2 % |
| B2 (kernel-written fact text) | 47 | 11.5 % |
| B2/C (kernel text answers most of it) | 27 | 6.6 % |
| C (judge-only) | 123 | 30.1 % |

By judge: gpt 202 points, deepseek 207. They raise different things. gpt: `search.not_reproducible` 24 vs 3, `cap_handling_unspecified` 10 vs 1, `missing_study_fields` 9 vs 0, `missing_data_rules` 9 vs 1, `vague_incomplete` 9 vs 0. deepseek: `admin_missing` 29 vs 11, `methods_missing` 24 vs 8, `language_conflict` 14 vs 3, `unit_of_analysis` 7 vs 2, `min_studies_vs_pi` 5 vs 0. Median C points per `revise` opinion: gpt 2, deepseek 1. gpt opinions without any C point: 7/51; deepseek: 19/47.

### Class A — why the existing checks did not stop it

| category | points | existing check | why it passed |
|---|---|---|---|
| search.cap_exceeded | 20 | record-cap probe + in-attempt narrowing | The probe is advisory (`PROBE_NOTE`); when narrowing runs out (`protocol_narrow_tries`=3), the best version goes to the vote above the cap (R1/fructose a4 6,864; R3/pulmhyp a1 3,862, v5 = v4; R2/pulmhyp a2 4,410, not raised by either judge). R1 a2–a3 ran before narrowing existed (commit 0dfc8f4, 2026-11-09 17:07 UTC; R1 attempts ran at 14:35 UTC): 32,616 / 39,759 / 81,864 / 31,162 records went straight to the vote. |
| search.openalex_unreadable_query | 2 | `check_strategy` OPENALEX_UNREAD | P1/probiotics a3 (v8: `diarrh?ea`, `probiotic*` sent to OpenAlex → HTTP 400 on every query). The vote was at 17:39 UTC; the check (c225d5c) landed at 17:42 UTC. Current code catches it. |
| synthesis.outcome_mapping_unchecked | 1 | `_outcome_problems` | It runs only when `method == "meta_analysis"`. R2/pulmhyp v8 is `narrative_swim` but has an outcome mapping `n_treatment = n_control = n_patients`, `events_control = fp_ai`, which the check would reject. |


## 4. Dynamics across attempts

| measure | value |
|---|---|
| (project, attempt, category) instances | 321 |
| raised by one judge only | 233 (73 %; gpt-only 114, deepseek-only 119) |
| instances with a later voted attempt | 227 |
| … not re-raised at the next vote | 117 |
| … re-raised | 73 |
| … silent-persist (fault still in body, not re-raised) | 37 |
| detectable instances with a next version: fault gone from the body | **28 / 106 (26 %)** |
| later-vote instances that are new categories for the project | 86 / 170 (51 %); 16 of them were detectably present earlier |
| votes whose points (both judges) are all A/B | 6 / 51 |

Fix rate by detectable category (fault absent in the next voted version / instances with a next version): cap_exceeded 4/8, source_uncounted 3/13, no_start_date 0/8, language_conflict 4/8, prose_vs_strategy 1/3, placeholder 0/5, admin_missing 7/20, methods_missing 1/20 (the detector requires all four method items), no_outcomes 1/3, dta_measure_mismatch 0/6, no_study_id 3/3, query_syntax 1/1, zero_hit 1/1.

Patterns, with evidence:

- **The authors cannot fix faults that come from the environment.** PubMed is uncounted in every R1 probe that includes it (the R1 kernel had no PubMed count), and OpenAlex counts failed with `BudgetExhausted` in R2/pulmhyp a1, R2/cade a1 and R2/ozone a1–a3. Judges re-raise these at every vote (R1/ozone a2→a3→a4, R1/tfa a2→a3→a4, R2/ozone a1→a2→a3). R1/tfa a4 shows how stuck the loop gets: the Ideator, told to report PubMed counts, writes that "PubMed counts are obtained and reported … so none is uncounted" and quotes the old 31,162 total, while the probe says 223 and `uncounted: [pubmed]`. Both judges reject that inconsistency.
- **The language rule is a kernel fact that every attempt rediscovers.** `languages: ["en"]` is set by the kernel (`SCREENED_LANGUAGES`; the Scout's output has no languages field). Until 6aa8b4b (2026-11-11 04:48 UTC) the Ideator was not told about it: only 4 of the 97 protocol-stage ideator tasks had a `languages` input. The `language_conflict` detector fires on 41/51 voted versions and the judges raised it in 14. P1/probiotics, P1/probiotics2 and R2/pulmhyp even wrote criteria admitting *other* languages ("no restriction on publication language", "another language accessible to the review team", "no language restriction at search stage").
- **Admin facts loop into placeholders** (finding 4). R3/cade: a1 asks for registration and funding → a2 "registry identifier is left blank here and must be filled in before freezing" → a2 and a3 both judges: placeholder.
- **DTA is unfixable within the schema.** R1/R2/R3 prostate and pulmhyp: RR/OR/RD is used for sensitivity/specificity, the paired design is lost, and there are no 2×2 cells. This is raised at every vote; the structural check alone ends 3 runs without a vote.
- **The judges also move.** R2/cade v4 already had zero-event, continuity-correction and multiple-report rules (deepseek approved and quoted them), yet gpt asked at a3 "how missing outcome data and trial reports will be handled". R2/pulmhyp a2: the cap was exceeded (4,410 > 3,000) and neither judge mentioned it. R1/prostate a4 (probe 2,999 ≤ cap): both judges rejected only the protocol's own stale prose ("the search as written cannot be screened within the cap").
- **Some reversions are the redraft's own doing** (§5.4). R2/fructose: a2 had 9 PRISMA-P items (deepseek approved); a3 went back to 5, and gpt asked again for registration/amendments. R2/tfa: a2 had 11 items; a3 went back to 5 and deepseek asked again for registration, funding, competing interests and dissemination.


## 5. How guidance reaches the next attempt

### 5.1 Code path

1. `ProtocolWorker._vote` → `vote()` → `StageOutcome("rerun", reason=_revise_reason(res))`. `_revise_reason` joins **both opinions' full reasons** into one string: `the judges asked for a revision: judge (openai): …; judge_2 (deepseek): …` (or `the protocol vote did not approve it (the judges disagree): …`).
2. `engine._settle` → `_edge` → `_take_edge` → `record_reopened(guidance=reason)` → the `stages.guidance` projection. The next `stage.attempt` takes it as `requested`, and `_guidance()` adds only exploration-round feedback. **Guidance is replaced at each attempt, never accumulated.**
3. Ideator and Scout inputs (`ProtocolWorker.run`): `guidance` (that one string), `previous_draft` (the version the latest earlier attempt offered, with its probe), and `frozen_protocol` when there is one. There are no earlier rounds' reasons, no numbered points, and no requirement to answer each point. Inside an attempt, a structural retry gets `guidance + "Version N … failed the protocol's checks: …; Fix these and keep the rest."` A narrowing retry gets the cap/total text.
4. Judges (`vote.py`, `ProtocolWorker._vote`): `subject = {paper_type, protocol: <raw body JSON>, record_probe: {…, record_cap, note: PROBE_NOTE}}`, with the generic `VOTE_QUESTION`. **No earlier reasons, no list of changes, no diff, and no statement of kernel rules** (language exclusion, cap enforcement after dedup, how the searches are run, what registration means for an autonomous run). Judges get the raw JSON, not `render_protocol`, so kernel defaults leak into the review: `hksj`/`min_studies`/`pi_df` on narrative plans (R2/tfa a2), `date_from: null` shown as a missing start date, `languages: ["en"]` with no reason given.

### 5.2 Confirmed in the stored inputs

`task.created` payloads match the code for every protocol task: ideator/scout `keys = {available_sources, guidance, hypotheses, intake, previous_draft, project, research_question, topic}` (+ `eligibility` for the Scout, + `narrowing` when narrowing, + `languages` only in the 4 newest ideator tasks); judge `keys = {options, question, subject}`, `subject = {paper_type, protocol, record_probe}`. In every attempt that followed a vote within the same run, the guidance is exactly that vote's two joined reasons (1.4–2.4 kB).

### 5.3 Defect: stale guidance after the edge limit

When the attempt-3 vote says `revise` at the edge limit, `degrade` fails the run, and no `stage.reopened` is written. `stages.guidance` therefore keeps attempt 3's *requested* guidance. Every later run starts attempt 4 from that stale value:

| run | attempt-4 guidance came from | attempt-3 vote reasons |
|---|---|---|
| R1/prostate, R1/pulmhyp, R1/ozone, R1/tfa, R3/pulmhyp | vote of attempt 2 | lost |
| R1/cade, R1/fructose | novelty-check failure of attempt 2 | lost; the attempt-4 Ideator never saw any judge reason |

`previous_draft` in attempt 4 is still the attempt-3 version, so the Ideator revised the newest draft against complaints about an older one.

### 5.4 Defect: redrafts drop content

Of 77 Ideator redrafts with a `previous_draft`, 6 removed PRISMA-P items that the previous draft had (plus 1 rename):

- In-attempt (guidance = "Fix these and keep the rest"): P1/probiotics v7→v8 (−5 items), R1/pulmhyp v4→v5 (−4 items, −6 fields), R2/pulmhyp v10→v11 (−2).
- Across attempts: R2/fructose v5→v6 (−4), R2/tfa v3→v4 (−6), R2/pulmhyp v8→v9 (−1, −4 fields).

Probable cause [INFERENCE from the prompt text and the outputs]: `DRAFT_TASK` defines the systematic review's PRISMA-P items as exactly "(rationale, objectives, selection process, data collection process, risk of bias)", and the newer guidance no longer repeats the earlier request. The model falls back to the listed five. No check compares a redraft with its `previous_draft`.


## 6. Recommended 2b rules (deterministic, before the vote)

Ranked by points covered × confidence. "Fires" = how often the prototype detector fired on the 51 voted versions, and in how many of those the judges raised the point (fires without a raise are mostly latent faults the judges skipped that time; see the moving target in §4).

| # | rule (over protocol body / probe) | covers | points | FP risk | evidence |
|---|---|---|---|---|---|
| R1 | **PRISMA-P items.** (a) Kernel writes the admin items itself, as true facts of an autonomous run: `registration` (not registered in an external registry; the frozen body's SHA-256 + timestamp in the event log is the time-stamped record), `amendments` (each change after the freeze is a new version with a recorded deviation), `support`/`competing_interests` (none; produced by the kernel), `dissemination`, `contact`. Agents may not write these keys. (b) Extend `PRISMA_P_TEXT` with `meta_bias`, `certainty_of_evidence`, `data_management`, `outcomes_prioritisation`, and list all of them in `DRAFT_TASK`. (c) Placeholder regex over every Ideator text: `to be (completed|stated|declared|recorded|filled|reported|registered)`, `[TO BE`, `before (the protocol is )?freez`, `by the researcher`, `the researcher (must|will)`, `left blank`, `when assigned`, `once issued`, `must be re-run`, `before the search is final`, `written in the full protocol`. (d) Process-text regex: `reproduction set`, `kernel`, `intake`. | admin_missing, placeholder, methods_missing, internal_note | 92 (22.5 %) | (a) none; (b) ~0 for SR; (c) low: the 6 regex hits the judges did not raise are all real deferrals ("to be registered before data extraction"); (d) low: "reproduction set" sits in the prisma_p of 27/51 voted versions (it leaks from the project title) and was raised once | admin fires 41, raised in 27; methods fires 48, raised in 25; placeholder fires 18, raised in 12 |
| R2 | **Probe complete and within cap before any vote.** Every (query, source) count must be a number. A source with no count or a count error is retried, or dropped from `sources` with a kernel-written limitation. No query and no source may total 0. `total <= record_cap`, else no vote: P1 fails fast, or is given more narrowing tries; do not spend a judge round. | source_uncounted, cap_exceeded (A), openalex_unreadable (A), zero_hit | 56 (13.7 %) | none for counts/cap. Zero-total: low (a 0-hit query is dead weight). Per-cell zeros fired in 13 versions and were raised in only 2, so do not use per-cell zeros. Caveat: the 2 raised zero-hit points (R3/ozone, PubMed 0/0/1) escape a strict zero-total rule; a `source total < 5` variant catches them | uncounted fires 20, raised in 18; cap fires 11, raised in 10 |
| R3 | **Language is kernel-owned.** Kernel appends exclusion `E-lang: "Reports not in English (excluded by a kernel rule before screening)"` and a fixed justification/limitation sentence in prisma_p. Check: no criterion or prisma_p text matches `any (publication )?language|another language|other languages|no (language )?(restriction|limit)|translat`. | language_conflict, language_unjustified | 28 (6.8 %) | low | conflict fires 41, raised in 14 (each raise is a latent fault surfacing) |
| R4 | **Kernel "search execution" block,** rendered into the protocol the judges see: publication years written out ("earliest indexed record to 2020" when `date_from` is null); each query string is sent verbatim to each listed source's search API on the date search_screen runs, and that date and the per-source counts are recorded in `search_log`; how duplicates are removed (DOI / normalised title+year); cap rule (search_screen stops after deduplication; above the cap = protocol deviation, never silent truncation). No check, only facts. | no_start_date, not_reproducible (partial: judges also ask for database-specific MeSH syntax), dedup, cap_handling | 56 (13.7 %) | n/a | gpt raises not_reproducible in 24/51 opinions; it is among gpt's points in 3 of the 4 votes deepseek approved |
| R5 | **Prose agrees with strategy.** Source names in prisma_p (`pubmed|medline|europe ?pmc|openalex|arxiv`) ⊆ `search_strategy.sources`; no promise of searches the kernel does not run (`clinicaltrials.gov|ictrp|isrctn|reference lists|citation (chasing|searching)|hand-?search|grey literature|contact(ing)? authors`), else a kernel-written limitation; no probe figures or "none is uncounted" claims in prose (kernel renders the probe). | prose_vs_strategy, probe_text_mismatch | 15 (3.7 %) | low (a text saying "OpenAlex was not searched" would trip it; allow a negation list) | fires 12, raised in 8 |
| R6 | **Synthesis structure.** SR: `outcomes` non-empty for any method. Run `_outcome_problems` for every plan with outcomes, not only meta-analysis (A case). DTA detector: question or outcome names match `sensitivit|specificit|\bauc\b|auroc|diagnostic accuracy` → no RR/OR/RD/MD/SMD pooling; route to narrative with stated reason, or add a DTA measure (bivariate/paired) to `rk.stats`. Hide pool keys from the judges when `method != meta_analysis`. State "prediction interval reported only when k ≥ 3" in kernel text, not as a check (the check would fire on 43/51 versions, raised in 5). | no_outcomes, outcome_mapping (A), dta_measure_mismatch, pool_keys, min_studies_vs_pi | 28 (6.8 %) | DTA regex: low (fires 12, raised in 9; the 3 unraised are DTA topics too). The DTA *fix* needs a kernel capability | |
| R7 | Small: no query mixes AND and OR at one parenthesis depth (outside quotes), and parentheses balance; SR extraction schema has a study identifier field (`study_?id|author|citation|reference`). | query_syntax, no_study_id | 5 (1.2 %) | low | fires 1/raised 1; fires 4/raised 3 |
| R8 | **No silent removal.** A redraft may not drop prisma_p keys, extraction fields or criteria of `previous_draft` unless an open point asks for it. | (prevents regressions §5.4) | — | low | 6 regressions |

**Coverage:** R1–R7 cover 280/409 points (**68.5 %**, upper bound). Leaving out the partial ones (methods_missing content quality, not_reproducible, DTA needing a capability): 208 (**50.9 %**). Not worth a check: `prisma_p.rob_tool_unspecified` (6; tool list would misfire on non-clinical topics such as tfa), `min_studies_vs_pi` as a check, per-cell zero hits.


## 7. What 2a (numbered points, carried forward) would address

The C share (123 points, 30 %) plus the B2/C remainder cannot be removed by rules. The diagnosis above suggests this design:

1. **Structured judge output.** `VoteOut` → `points: [{section, fault, requested_change, blocking: bool}]` instead of free text. The kernel gives each point a stable id (`P<attempt>.<judge>.<n>`). Free-text reasons today mix 3–9 faults per opinion; for 409 points the kernel had nothing to track.
2. **Ledger across attempts and runs**, stored as events, not in `stages.guidance`. This fixes the stale-guidance defect (§5.3), and a point survives the edge-limit failure and the next run.
3. **Route by owner.** Search → Scout; eligibility/extraction/synthesis/PRISMA-P → Ideator; kernel facts (language, cap, sources, admin, search execution, uncountable sources, missing DTA measure) → answered by the kernel with fixed text, never redrafted. Kernel-owned points are a third to a half of all points (148 without cap_exceeded and not_reproducible, 195 with them).
4. **Every open point must be answered.** Ideator/Scout output carries `answers: [{point_id, change | rebuttal, where}]`. A deterministic check that every open point has an answer runs in-attempt and does not spend a `protocol->protocol` edge.
5. **Judges see the history.** Next vote: earlier points with the authors' answers + a diff of the body. Judges first mark each earlier point `resolved | unresolved`, then may add new points, marked blocking or advisory. Approve when no blocking point is open. This targets the 51 % new-category rate and the 37 silent-persists. It also gives a basis for both judges' points being answered, today 73 % of instances come from one judge.
6. **Judges get the kernel rules sheet** (language exclusion, dedup + cap enforcement, verbatim API search, registration status for an autonomous run, the sources the kernel can search), so they stop asking for what the kernel cannot do: grey literature, registries, MeSH syntax, PROSPERO.
7. **Points that need a capability** (DTA pooling; more sources) become recorded limitations, closed by the kernel, rather than loops.

Expected effect [INFERENCE]: in the 4 votes where deepseek approved, gpt's points were not_reproducible (3×), scope_limited (3×), missing_data_rules (3×, in R2/cade a3 already present in v4), no_start_date (2×), plus query_sensitivity, eligibility.not_operational, no_study_id and missing_study_fields once each. R4 + kernel limitation text answer about half of them. The rest are C points that only the answer-and-close loop (items 4–5) can settle.


## 8. Per-project tracking (voted versions only)

Cell = who raised the category at that vote: `B` both, `O` gpt-6-luna (judge), `D` deepseek (judge_2). `·` = detector finds the fault but nobody raised it. `–` = detector finds no fault and nobody raised it. A `?` after a letter = raised although the detector finds no fault (the judge reads the prose, or the detector is narrower). Blank = not raised, no detector. Rows are in order of first raise.

#### P1/probiotics — voted attempts a1 (v4), a2 (v6), a3 (v8)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | – | D |
| prisma_p.methods_missing | B1 | B | · | B |
| search.scope_limited | C | O |   |   |
| search.cap_handling_unspecified | B2 | O | O |   |
| search.language_unjustified | B2 | B |   |   |
| search.query_sensitivity | C | B | D |   |
| synthesis.estimand_or_subgroup | C | B | O |   |
| extraction.denominator_ambiguity | C | D |   |   |
| eligibility.language_conflict | B1 | · | B | D |
| synthesis.missing_data_rules | C |   | O | O |
| extraction.field_vs_text | C |   |   | D |
| synthesis.min_studies_vs_pi | B2 | · | · | D |
| search.openalex_unreadable_query | A | – | – | B |

#### P1/probiotics2 — voted attempts a1 (v3), a2 (v6), a3 (v7)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | D | – |
| prisma_p.methods_missing | B1 | B | D | · |
| search.not_reproducible | B2/C | B | O |   |
| eligibility.language_conflict | B1 | D | B | – |
| search.scope_limited | C | B |   |   |
| search.query_sensitivity | C | D |   |   |
| extraction.missing_study_fields | C | O |   |   |
| search.dedup_unspecified | B2 | O |   |   |
| synthesis.outcome_coverage | C | D | D |   |
| search.prose_vs_strategy | B1 | – | D | · |
| synthesis.unit_of_analysis | C |   | D | O |
| extraction.field_vs_text | C |   | D |   |
| synthesis.min_studies_vs_pi | B2 | · | D | · |
| extraction.denominator_ambiguity | C |   | O | D |
| eligibility.not_operational | C |   | O |   |
| text.unexplained_reference | C |   | O |   |
| prisma_p.placeholder | B1 | – | – | B |

#### R1/cade — voted attempts a3 (v3), a4 (v4)

| category | class | a3 | a4 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | B |
| prisma_p.methods_missing | B1 | B | D |
| search.source_uncounted | B1 | B | B |
| search.cap_exceeded | A | B | – |
| eligibility.language_conflict | B1 | D | D |
| search.no_start_date | B2 | O | O |
| search.not_reproducible | B2/C |   | O |
| search.cap_handling_unspecified | B2 |   | O |
| eligibility.internal_conflict | C |   | D |

#### R1/fructose — voted attempts a3 (v3), a4 (v6)

| category | class | a3 | a4 |
|---|---|---|---|
| search.source_uncounted | B1 | B | – |
| search.cap_exceeded | A | B | B |
| search.cap_handling_unspecified | B2 | O |   |
| prisma_p.rob_tool_unspecified | B1? | B |   |
| synthesis.estimand_or_subgroup | C | O |   |
| synthesis.rob_integration | C | D |   |
| search.prose_vs_strategy | B1 | – | B |

#### R1/ozone — voted attempts a2 (v2), a3 (v3), a4 (v4)

| category | class | a2 | a3 | a4 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | – | D? |
| prisma_p.methods_missing | B1 | B | · | D |
| search.source_uncounted | B1 | B | B | O |
| search.not_reproducible | B2/C | B | B | O |
| extraction.missing_study_fields | C | O |   |   |
| search.language_unjustified | B2 |   | D |   |
| prisma_p.vague_incomplete | C |   | O |   |
| synthesis.unit_of_analysis | C |   | D | O |
| extraction.field_vs_text | C |   | D | O |
| eligibility.language_conflict | B1 | · | · | D |
| synthesis.multiplicity | C |   |   | O |

#### R1/prostate — voted attempts a2 (v2), a3 (v3), a4 (v5)

| category | class | a2 | a3 | a4 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | · | · |
| prisma_p.methods_missing | B1 | O | · | · |
| search.source_uncounted | B1 | O | B | · |
| search.not_reproducible | B2/C | O |   |   |
| search.cap_exceeded | A | B | B | – |
| search.no_start_date | B2 | B | · | B |
| synthesis.dta_measure_mismatch | B1(K) | D | · | · |
| extraction.missing_dta_cells | C | O |   |   |
| extraction.missing_study_fields | C | O |   |   |
| synthesis.outcome_coverage | C | O |   |   |
| prisma_p.placeholder | B1 | – | B | B |
| search.probe_text_mismatch | B1 | – | D | · |

#### R1/pulmhyp — voted attempts a2 (v2), a3 (v3)

| category | class | a2 | a3 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | D? |
| prisma_p.methods_missing | B1 | D | · |
| search.source_uncounted | B1 | B | B |
| search.cap_exceeded | A | B | B |
| search.no_start_date | B2 | D | · |
| synthesis.dta_measure_mismatch | B1(K) | D | · |
| extraction.missing_dta_cells | C | O | O |
| search.cap_handling_unspecified | B2 | D |   |
| search.language_unjustified | B2 | D |   |
| prisma_p.vague_incomplete | C | O |   |
| synthesis.min_studies_vs_pi | B2 | D | – |
| prisma_p.placeholder | B1 | – | B |

#### R1/tfa — voted attempts a2 (v2), a3 (v3), a4 (v5)

| category | class | a2 | a3 | a4 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | D | · |
| prisma_p.methods_missing | B1 | D | · | · |
| search.source_uncounted | B1 | B | B | B |
| search.not_reproducible | B2/C | O | O |   |
| search.cap_exceeded | A | B | B | – |
| synthesis.missing_data_rules | C | O |   |   |
| extraction.field_vs_text | C | D |   |   |
| prisma_p.rob_tool_unspecified | B1? | O | O |   |
| extraction.no_study_id | B1 | D | – | – |
| search.probe_text_mismatch | B1 | – | – | B |

#### R2/cade — voted attempts a1 (v2), a2 (v3), a3 (v4)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | – | – |
| prisma_p.methods_missing | B1 | D | · | · |
| search.source_uncounted | B1 | B | – | – |
| search.not_reproducible | B2/C | O | O | O |
| eligibility.language_conflict | B1 | D | – | – |
| search.no_start_date | B2 | O | O | O |
| synthesis.missing_data_rules | C | O |   | O |
| extraction.missing_study_fields | C | O |   |   |
| prisma_p.vague_incomplete | C | O |   |   |
| search.query_sensitivity | C |   | O |   |
| search.scope_limited | C |   |   | O |

#### R2/fructose — voted attempts a1 (v4), a2 (v5), a3 (v8)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | · | B |
| prisma_p.methods_missing | B1 | B | · | · |
| search.not_reproducible | B2/C | O |   |   |
| synthesis.multiplicity | C | D |   |   |
| synthesis.unit_of_analysis | C | D |   |   |
| extraction.unit_conversion | C | D |   | D |
| prisma_p.rob_tool_unspecified | B1? | D |   |   |
| search.dedup_unspecified | B2 | O |   |   |
| synthesis.rob_integration | C | D |   |   |
| search.query_syntax | B1 | B | – | – |
| search.scope_limited | C |   | O |   |
| synthesis.missing_data_rules | C |   | O |   |
| eligibility.not_operational | C |   | O |   |
| eligibility.language_conflict | B1 | · | · | D |
| extraction.missing_study_fields | C |   |   | O |
| eligibility.internal_conflict | C |   |   | D |
| extraction.field_vs_text | C |   |   | D |

#### R2/ozone — voted attempts a1 (v2), a2 (v3), a3 (v4)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | – | – |
| prisma_p.methods_missing | B1 | B | · | · |
| search.source_uncounted | B1 | D | B | B |
| search.not_reproducible | B2/C | O |   |   |
| search.language_unjustified | B2 | D |   |   |
| synthesis.estimand_or_subgroup | C | O |   | O |
| prisma_p.placeholder | B1 | – | O | D |
| search.prose_vs_strategy | B1 | – | O? | – |
| search.query_sensitivity | C |   | O |   |
| synthesis.multiplicity | C |   | D |   |
| synthesis.unit_of_analysis | C |   | D |   |

#### R2/pulmhyp — voted attempts a1 (v4), a2 (v8)

| category | class | a1 | a2 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | B | D |
| prisma_p.methods_missing | B1 | D | D |
| search.source_uncounted | B1 | D | – |
| search.not_reproducible | B2/C | O |   |
| extraction.missing_dta_cells | C | B | B |
| search.language_unjustified | B2 | O |   |
| search.query_sensitivity | C | B |   |
| synthesis.no_outcomes | B1 | B | – |
| synthesis.pool_keys_on_narrative | B2 | D | · |
| eligibility.language_conflict | B1 | – | B |
| search.no_start_date | B2 | · | D |
| synthesis.dta_measure_mismatch | B1(K) | – | B |
| search.prose_vs_strategy | B1 | – | B |
| synthesis.outcome_mapping_unchecked | A | – | D |

#### R2/tfa — voted attempts a1 (v2), a2 (v3), a3 (v4)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | – | D |
| prisma_p.methods_missing | B1 | D | D | D |
| search.not_reproducible | B2/C | O | O | O |
| extraction.missing_study_fields | C | O |   |   |
| prisma_p.vague_incomplete | C | O |   |   |
| eligibility.internal_conflict | C | D |   |   |
| synthesis.no_outcomes | B1 | B | O | B |
| prisma_p.placeholder | B1 | – | D | · |
| eligibility.language_conflict | B1 | · | D | – |
| search.scope_limited | C |   | O | D |
| search.language_unjustified | B2 |   | O |   |
| prisma_p.rob_tool_unspecified | B1? |   | D |   |
| extraction.no_study_id | B1 | · | D | – |
| synthesis.pool_keys_on_narrative | B2 | · | D | · |
| synthesis.unit_of_analysis | C |   |   | D |

#### R3/cade — voted attempts a1 (v2), a2 (v3), a3 (v4)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | · | · |
| prisma_p.methods_missing | B1 | D | · | · |
| search.not_reproducible | B2/C | O | O |   |
| search.no_start_date | B2 | O | · | · |
| synthesis.missing_data_rules | C | D |   |   |
| extraction.missing_study_fields | C | O |   |   |
| prisma_p.vague_incomplete | C | O |   |   |
| synthesis.unit_of_analysis | C | D |   |   |
| synthesis.rob_integration | C | B |   |   |
| prisma_p.placeholder | B1 | – | B | B |
| eligibility.language_conflict | B1 | · | D | · |
| search.cap_handling_unspecified | B2 |   | O | O |
| search.query_sensitivity | C |   | O |   |

#### R3/fructose — voted attempts a2 (v7), a3 (v9)

| category | class | a2 | a3 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | D |
| prisma_p.methods_missing | B1 | D | · |
| search.cap_handling_unspecified | B2 | O |   |
| synthesis.multiplicity | C | D | B |
| synthesis.missing_data_rules | C | O |   |
| extraction.unit_conversion | C | D | D |
| synthesis.min_studies_vs_pi | B2 | D | · |
| search.dedup_unspecified | B2 | O |   |
| search.not_reproducible | B2/C |   | O |
| search.scope_limited | C |   | O |
| prisma_p.vague_incomplete | C |   | O |

#### R3/ozone — voted attempts a2 (v5), a3 (v6)

| category | class | a2 | a3 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | D |
| prisma_p.methods_missing | B1 | D | · |
| search.not_reproducible | B2/C | O |   |
| eligibility.language_conflict | B1 | D | – |
| search.no_start_date | B2 | O | · |
| search.query_sensitivity | C | D |   |
| synthesis.multiplicity | C | B | B |
| eligibility.internal_conflict | C | D |   |
| extraction.denominator_ambiguity | C | O |   |
| search.dedup_unspecified | B2 | O |   |
| search.zero_hit_query | B1 | D? | O? |
| search.scope_limited | C |   | D |
| search.language_unjustified | B2 |   | D |
| synthesis.missing_data_rules | C |   | O |
| synthesis.unit_of_analysis | C |   | D |

#### R3/prostate — voted attempts a2 (v6), a3 (v10)

| category | class | a2 | a3 |
|---|---|---|---|
| prisma_p.admin_missing | B1+B2 | D | · |
| prisma_p.methods_missing | B1 | D | · |
| search.no_start_date | B2 | D | · |
| synthesis.dta_measure_mismatch | B1(K) | B | O |
| search.scope_limited | C | O |   |
| search.language_unjustified | B2 | B |   |
| extraction.missing_study_fields | C | O |   |
| prisma_p.vague_incomplete | C | O |   |
| eligibility.language_conflict | B1 | · | D |
| search.prose_vs_strategy | B1 | – | B |
| search.cap_handling_unspecified | B2 |   | O |
| extraction.field_vs_text | C |   | D |

#### R3/pulmhyp — voted attempts a1 (v4), a2 (v6), a3 (v8), a4 (v9)

| category | class | a1 | a2 | a3 | a4 |
|---|---|---|---|---|---|
| prisma_p.methods_missing | B1 | D | – | – | – |
| search.cap_exceeded | A | B | – | – | – |
| synthesis.dta_measure_mismatch | B1(K) | B | B | O | O |
| extraction.missing_dta_cells | C | B |   | O | O |
| prisma_p.vague_incomplete | C | O | O |   |   |
| prisma_p.admin_missing | B1+B2 | · | D | · | · |
| search.scope_limited | C |   | D |   |   |
| search.cap_handling_unspecified | B2 |   | O | O |   |
| search.language_unjustified | B2 |   | D |   |   |
| synthesis.min_studies_vs_pi | B2 | · | D | – | – |
| prisma_p.placeholder | B1 | – | – | D | D |
| search.prose_vs_strategy | B1 | – | – | B | B |
| search.query_sensitivity | C |   |   |   | D |
| eligibility.internal_conflict | C |   |   |   | D |
| eligibility.missing_criterion | C |   |   |   | D |

#### R3/tfa — voted attempts a1 (v4), a2 (v6), a3 (v8)

| category | class | a1 | a2 | a3 |
|---|---|---|---|---|
| search.not_reproducible | B2/C | O | O |   |
| search.scope_limited | C | O |   |   |
| synthesis.missing_data_rules | C | O |   |   |
| extraction.missing_study_fields | C | O |   |   |
| extraction.no_study_id | B1 | O | – | – |
| prisma_p.admin_missing | B1+B2 | · | B | · |
| prisma_p.methods_missing | B1 | · | D | · |
| eligibility.internal_conflict | C |   | B |   |
| extraction.unit_conversion | C |   | B |   |
| prisma_p.placeholder | B1 | – | – | B |
| synthesis.multiplicity | C |   |   | O |
| prisma_p.internal_note | B1 | · | · | D |

R2/prostate: no vote. v1–v9 (a1–a3) all failed `synthesis_plan.outcomes` mapping (AUROC/sensitivity/specificity onto MD/SMD/RR roles); the run ended at the edge limit on structural failures.

## 9. Appendix: every opinion's points

One row per opinion (sorted by home, project, attempt). Class per category is in brackets; the full list is also in `p1-protocol-points-2026-11-12.tsv`.

| op | project | att | judge | vote | points (category [class]) |
|---|---|---|---|---|---|
| 96 | P1/probiotics | 1 | openai | revise | search.language_unjustified [B2]; search.scope_limited [C]; search.query_sensitivity [C]; search.cap_handling_unspecified [B2]; synthesis.estimand_or_subgroup [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2] |
| 97 | P1/probiotics | 1 | deepseek | revise | extraction.denominator_ambiguity [C]; synthesis.estimand_or_subgroup [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; search.query_sensitivity [C]; search.language_unjustified [B2] |
| 98 | P1/probiotics | 2 | openai | revise | eligibility.language_conflict [B1]; search.cap_handling_unspecified [B2]; synthesis.estimand_or_subgroup [C]; synthesis.missing_data_rules [C] |
| 99 | P1/probiotics | 2 | deepseek | revise | eligibility.language_conflict [B1]; search.query_sensitivity [C] |
| 100 | P1/probiotics | 3 | openai | revise | search.openalex_unreadable_query [A]; prisma_p.methods_missing [B1]; synthesis.missing_data_rules [C] |
| 101 | P1/probiotics | 3 | deepseek | revise | search.openalex_unreadable_query [A]; eligibility.language_conflict [B1]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; extraction.field_vs_text [C]; synthesis.min_studies_vs_pi [B2] |
| 90 | P1/probiotics2 | 1 | openai | revise | search.not_reproducible [B2/C]; search.dedup_unspecified [B2]; search.scope_limited [C]; extraction.missing_study_fields [C]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1] |
| 91 | P1/probiotics2 | 1 | deepseek | revise | eligibility.language_conflict [B1]; search.query_sensitivity [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; synthesis.outcome_coverage [C]; search.not_reproducible [B2/C]; search.scope_limited [C] |
| 92 | P1/probiotics2 | 2 | openai | revise | eligibility.language_conflict [B1]; text.unexplained_reference [C]; search.not_reproducible [B2/C]; eligibility.not_operational [C]; extraction.denominator_ambiguity [C] |
| 93 | P1/probiotics2 | 2 | deepseek | revise | eligibility.language_conflict [B1]; search.prose_vs_strategy [B1]; extraction.field_vs_text [C]; synthesis.outcome_coverage [C]; synthesis.unit_of_analysis [C]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; synthesis.min_studies_vs_pi [B2] |
| 94 | P1/probiotics2 | 3 | openai | revise | prisma_p.placeholder [B1]; synthesis.unit_of_analysis [C] |
| 95 | P1/probiotics2 | 3 | deepseek | revise | prisma_p.placeholder [B1]; extraction.denominator_ambiguity [C] |
| 0 | R1/prostate | 2 | openai | revise | search.no_start_date [B2]; search.cap_exceeded [A]; search.source_uncounted [B1]; extraction.missing_dta_cells [C]; extraction.missing_study_fields [C]; synthesis.outcome_coverage [C]; search.not_reproducible [B2/C]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1] |
| 1 | R1/prostate | 2 | deepseek | revise | search.cap_exceeded [A]; search.no_start_date [B2]; synthesis.dta_measure_mismatch [B1(K)]; prisma_p.admin_missing [B1+B2] |
| 2 | R1/prostate | 3 | openai | revise | prisma_p.placeholder [B1]; search.cap_exceeded [A]; search.source_uncounted [B1] |
| 3 | R1/prostate | 3 | deepseek | revise | prisma_p.placeholder [B1]; search.cap_exceeded [A]; search.source_uncounted [B1]; search.probe_text_mismatch [B1] |
| 4 | R1/prostate | 4 | openai | revise | prisma_p.placeholder [B1]; search.no_start_date [B2] |
| 5 | R1/prostate | 4 | deepseek | revise | prisma_p.placeholder [B1]; search.no_start_date [B2] |
| 6 | R1/pulmhyp | 2 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; extraction.missing_dta_cells [C]; prisma_p.vague_incomplete [C] |
| 7 | R1/pulmhyp | 2 | deepseek | revise | synthesis.dta_measure_mismatch [B1(K)]; synthesis.min_studies_vs_pi [B2]; search.cap_exceeded [A]; search.no_start_date [B2]; search.cap_handling_unspecified [B2]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; search.language_unjustified [B2]; search.source_uncounted [B1] |
| 8 | R1/pulmhyp | 3 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; prisma_p.placeholder [B1]; extraction.missing_dta_cells [C] |
| 9 | R1/pulmhyp | 3 | deepseek | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; prisma_p.placeholder [B1]; prisma_p.admin_missing [B1+B2] |
| 10 | R1/cade | 3 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; search.no_start_date [B2]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1] |
| 11 | R1/cade | 3 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; eligibility.language_conflict [B1]; search.cap_exceeded [A]; search.source_uncounted [B1] |
| 12 | R1/cade | 4 | openai | revise | search.source_uncounted [B1]; search.no_start_date [B2]; search.cap_handling_unspecified [B2]; prisma_p.admin_missing [B1+B2]; search.not_reproducible [B2/C] |
| 13 | R1/cade | 4 | deepseek | revise | eligibility.internal_conflict [C]; eligibility.language_conflict [B1]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; search.source_uncounted [B1] |
| 14 | R1/fructose | 3 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; search.cap_handling_unspecified [B2]; synthesis.estimand_or_subgroup [C]; prisma_p.rob_tool_unspecified [B1?] |
| 15 | R1/fructose | 3 | deepseek | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; prisma_p.rob_tool_unspecified [B1?]; synthesis.rob_integration [C] |
| 16 | R1/fructose | 4 | openai | revise | search.prose_vs_strategy [B1]; search.cap_exceeded [A] |
| 17 | R1/fructose | 4 | deepseek | revise | search.prose_vs_strategy [B1]; search.cap_exceeded [A] |
| 18 | R1/ozone | 2 | openai | revise | extraction.missing_study_fields [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; search.not_reproducible [B2/C]; search.source_uncounted [B1] |
| 19 | R1/ozone | 2 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; search.not_reproducible [B2/C]; search.source_uncounted [B1] |
| 20 | R1/ozone | 3 | openai | revise | search.not_reproducible [B2/C]; search.source_uncounted [B1]; prisma_p.vague_incomplete [C] |
| 21 | R1/ozone | 3 | deepseek | revise | search.not_reproducible [B2/C]; search.source_uncounted [B1]; extraction.field_vs_text [C]; synthesis.unit_of_analysis [C]; search.language_unjustified [B2] |
| 22 | R1/ozone | 4 | openai | revise | search.source_uncounted [B1]; search.not_reproducible [B2/C]; extraction.field_vs_text [C]; synthesis.unit_of_analysis [C]; synthesis.multiplicity [C] |
| 23 | R1/ozone | 4 | deepseek | revise | eligibility.language_conflict [B1]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1] |
| 24 | R1/tfa | 2 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; search.not_reproducible [B2/C]; synthesis.missing_data_rules [C]; prisma_p.rob_tool_unspecified [B1?] |
| 25 | R1/tfa | 2 | deepseek | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; extraction.no_study_id [B1]; extraction.field_vs_text [C] |
| 26 | R1/tfa | 3 | openai | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; search.not_reproducible [B2/C]; prisma_p.rob_tool_unspecified [B1?] |
| 27 | R1/tfa | 3 | deepseek | revise | search.cap_exceeded [A]; search.source_uncounted [B1]; prisma_p.admin_missing [B1+B2] |
| 28 | R1/tfa | 4 | openai | revise | search.source_uncounted [B1]; search.probe_text_mismatch [B1] |
| 29 | R1/tfa | 4 | deepseek | revise | search.probe_text_mismatch [B1]; search.source_uncounted [B1] |
| 30 | R2/pulmhyp | 1 | openai | revise | search.language_unjustified [B2]; search.query_sensitivity [C]; extraction.missing_dta_cells [C]; synthesis.no_outcomes [B1]; prisma_p.admin_missing [B1+B2]; search.not_reproducible [B2/C] |
| 31 | R2/pulmhyp | 1 | deepseek | revise | search.query_sensitivity [C]; search.source_uncounted [B1]; extraction.missing_dta_cells [C]; synthesis.no_outcomes [B1]; synthesis.pool_keys_on_narrative [B2]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2] |
| 32 | R2/pulmhyp | 2 | openai | revise | synthesis.dta_measure_mismatch [B1(K)]; extraction.missing_dta_cells [C]; search.prose_vs_strategy [B1]; eligibility.language_conflict [B1] |
| 33 | R2/pulmhyp | 2 | deepseek | revise | synthesis.outcome_mapping_unchecked [A]; extraction.missing_dta_cells [C]; synthesis.dta_measure_mismatch [B1(K)]; search.prose_vs_strategy [B1]; eligibility.language_conflict [B1]; search.no_start_date [B2]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2] |
| 34 | R2/cade | 1 | openai | revise | search.not_reproducible [B2/C]; search.no_start_date [B2]; search.source_uncounted [B1]; extraction.missing_study_fields [C]; synthesis.missing_data_rules [C]; prisma_p.vague_incomplete [C] |
| 35 | R2/cade | 1 | deepseek | revise | prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; eligibility.language_conflict [B1]; search.source_uncounted [B1] |
| 36 | R2/cade | 2 | openai | revise | search.not_reproducible [B2/C]; search.no_start_date [B2]; search.query_sensitivity [C] |
| 37 | R2/cade | 2 | deepseek | approve | — (approve, no points) |
| 38 | R2/cade | 3 | openai | revise | search.not_reproducible [B2/C]; search.scope_limited [C]; search.no_start_date [B2]; synthesis.missing_data_rules [C] |
| 39 | R2/cade | 3 | deepseek | approve | — (approve, no points) |
| 40 | R2/fructose | 1 | openai | revise | search.query_syntax [B1]; search.dedup_unspecified [B2]; search.not_reproducible [B2/C]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1] |
| 41 | R2/fructose | 1 | deepseek | revise | search.query_syntax [B1]; extraction.unit_conversion [C]; synthesis.multiplicity [C]; synthesis.unit_of_analysis [C]; prisma_p.rob_tool_unspecified [B1?]; synthesis.rob_integration [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2] |
| 42 | R2/fructose | 2 | openai | revise | eligibility.not_operational [C]; synthesis.missing_data_rules [C]; search.scope_limited [C] |
| 43 | R2/fructose | 2 | deepseek | approve | — (approve, no points) |
| 44 | R2/fructose | 3 | openai | revise | prisma_p.admin_missing [B1+B2]; extraction.missing_study_fields [C] |
| 45 | R2/fructose | 3 | deepseek | revise | eligibility.language_conflict [B1]; extraction.field_vs_text [C]; eligibility.internal_conflict [C]; extraction.unit_conversion [C]; prisma_p.admin_missing [B1+B2] |
| 46 | R2/ozone | 1 | openai | revise | synthesis.estimand_or_subgroup [C]; prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; search.not_reproducible [B2/C] |
| 47 | R2/ozone | 1 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; search.language_unjustified [B2]; search.source_uncounted [B1] |
| 48 | R2/ozone | 2 | openai | revise | search.source_uncounted [B1]; prisma_p.placeholder [B1]; search.query_sensitivity [C]; search.prose_vs_strategy [B1] |
| 49 | R2/ozone | 2 | deepseek | revise | search.source_uncounted [B1]; synthesis.multiplicity [C]; synthesis.unit_of_analysis [C] |
| 50 | R2/ozone | 3 | openai | revise | search.source_uncounted [B1]; synthesis.estimand_or_subgroup [C] |
| 51 | R2/ozone | 3 | deepseek | revise | search.source_uncounted [B1]; prisma_p.placeholder [B1] |
| 52 | R2/tfa | 1 | openai | revise | search.not_reproducible [B2/C]; extraction.missing_study_fields [C]; synthesis.no_outcomes [B1]; prisma_p.vague_incomplete [C] |
| 53 | R2/tfa | 1 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; eligibility.internal_conflict [C]; synthesis.no_outcomes [B1] |
| 54 | R2/tfa | 2 | openai | revise | synthesis.no_outcomes [B1]; search.scope_limited [C]; search.language_unjustified [B2]; search.not_reproducible [B2/C] |
| 55 | R2/tfa | 2 | deepseek | revise | extraction.no_study_id [B1]; synthesis.pool_keys_on_narrative [B2]; prisma_p.methods_missing [B1]; prisma_p.rob_tool_unspecified [B1?]; eligibility.language_conflict [B1]; prisma_p.placeholder [B1] |
| 56 | R2/tfa | 3 | openai | revise | search.not_reproducible [B2/C]; synthesis.no_outcomes [B1] |
| 57 | R2/tfa | 3 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; synthesis.no_outcomes [B1]; search.scope_limited [C]; synthesis.unit_of_analysis [C] |
| 58 | R3/prostate | 2 | openai | revise | synthesis.dta_measure_mismatch [B1(K)]; extraction.missing_study_fields [C]; search.scope_limited [C]; search.language_unjustified [B2]; prisma_p.vague_incomplete [C] |
| 59 | R3/prostate | 2 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; synthesis.dta_measure_mismatch [B1(K)]; search.language_unjustified [B2]; search.no_start_date [B2] |
| 60 | R3/prostate | 3 | openai | revise | search.prose_vs_strategy [B1]; search.cap_handling_unspecified [B2]; synthesis.dta_measure_mismatch [B1(K)] |
| 61 | R3/prostate | 3 | deepseek | revise | search.prose_vs_strategy [B1]; eligibility.language_conflict [B1]; extraction.field_vs_text [C] |
| 62 | R3/pulmhyp | 1 | openai | revise | search.cap_exceeded [A]; extraction.missing_dta_cells [C]; synthesis.dta_measure_mismatch [B1(K)]; prisma_p.vague_incomplete [C] |
| 63 | R3/pulmhyp | 1 | deepseek | revise | synthesis.dta_measure_mismatch [B1(K)]; extraction.missing_dta_cells [C]; prisma_p.methods_missing [B1]; search.cap_exceeded [A] |
| 64 | R3/pulmhyp | 2 | openai | revise | synthesis.dta_measure_mismatch [B1(K)]; search.cap_handling_unspecified [B2]; prisma_p.vague_incomplete [C] |
| 65 | R3/pulmhyp | 2 | deepseek | revise | synthesis.dta_measure_mismatch [B1(K)]; synthesis.min_studies_vs_pi [B2]; prisma_p.admin_missing [B1+B2]; search.scope_limited [C]; search.language_unjustified [B2] |
| 66 | R3/pulmhyp | 3 | openai | revise | search.prose_vs_strategy [B1]; search.cap_handling_unspecified [B2]; extraction.missing_dta_cells [C]; synthesis.dta_measure_mismatch [B1(K)] |
| 67 | R3/pulmhyp | 3 | deepseek | revise | search.prose_vs_strategy [B1]; prisma_p.placeholder [B1] |
| 68 | R3/pulmhyp | 4 | openai | revise | search.prose_vs_strategy [B1]; synthesis.dta_measure_mismatch [B1(K)]; extraction.missing_dta_cells [C] |
| 69 | R3/pulmhyp | 4 | deepseek | revise | eligibility.internal_conflict [C]; search.prose_vs_strategy [B1]; search.query_sensitivity [C]; eligibility.missing_criterion [C]; prisma_p.placeholder [B1] |
| 70 | R3/cade | 1 | openai | revise | extraction.missing_study_fields [C]; synthesis.rob_integration [C]; search.not_reproducible [B2/C]; search.no_start_date [B2]; prisma_p.vague_incomplete [C] |
| 71 | R3/cade | 1 | deepseek | revise | synthesis.rob_integration [C]; prisma_p.methods_missing [B1]; synthesis.unit_of_analysis [C]; synthesis.missing_data_rules [C]; prisma_p.admin_missing [B1+B2] |
| 72 | R3/cade | 2 | openai | revise | prisma_p.placeholder [B1]; search.query_sensitivity [C]; search.cap_handling_unspecified [B2]; search.not_reproducible [B2/C] |
| 73 | R3/cade | 2 | deepseek | revise | prisma_p.placeholder [B1]; eligibility.language_conflict [B1] |
| 74 | R3/cade | 3 | openai | revise | prisma_p.placeholder [B1]; search.cap_handling_unspecified [B2] |
| 75 | R3/cade | 3 | deepseek | revise | prisma_p.placeholder [B1] |
| 76 | R3/fructose | 2 | openai | revise | search.dedup_unspecified [B2]; search.cap_handling_unspecified [B2]; synthesis.missing_data_rules [C] |
| 77 | R3/fructose | 2 | deepseek | revise | prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2]; extraction.unit_conversion [C]; synthesis.multiplicity [C]; synthesis.min_studies_vs_pi [B2] |
| 78 | R3/fructose | 3 | openai | revise | search.not_reproducible [B2/C]; search.scope_limited [C]; synthesis.multiplicity [C]; prisma_p.vague_incomplete [C] |
| 79 | R3/fructose | 3 | deepseek | revise | extraction.unit_conversion [C]; synthesis.multiplicity [C]; prisma_p.admin_missing [B1+B2] |
| 80 | R3/ozone | 2 | openai | revise | search.no_start_date [B2]; search.not_reproducible [B2/C]; search.dedup_unspecified [B2]; synthesis.multiplicity [C]; extraction.denominator_ambiguity [C] |
| 81 | R3/ozone | 2 | deepseek | revise | search.zero_hit_query [B1]; search.query_sensitivity [C]; eligibility.internal_conflict [C]; eligibility.language_conflict [B1]; synthesis.multiplicity [C]; prisma_p.methods_missing [B1]; prisma_p.admin_missing [B1+B2] |
| 82 | R3/ozone | 3 | openai | revise | search.zero_hit_query [B1]; synthesis.multiplicity [C]; synthesis.missing_data_rules [C] |
| 83 | R3/ozone | 3 | deepseek | revise | prisma_p.admin_missing [B1+B2]; search.scope_limited [C]; synthesis.multiplicity [C]; synthesis.unit_of_analysis [C]; search.language_unjustified [B2] |
| 84 | R3/tfa | 1 | openai | revise | search.not_reproducible [B2/C]; search.scope_limited [C]; extraction.no_study_id [B1]; extraction.missing_study_fields [C]; synthesis.missing_data_rules [C] |
| 85 | R3/tfa | 1 | deepseek | approve | — (approve, no points) |
| 86 | R3/tfa | 2 | openai | revise | search.not_reproducible [B2/C]; prisma_p.admin_missing [B1+B2]; eligibility.internal_conflict [C]; extraction.unit_conversion [C] |
| 87 | R3/tfa | 2 | deepseek | revise | prisma_p.admin_missing [B1+B2]; prisma_p.methods_missing [B1]; eligibility.internal_conflict [C]; extraction.unit_conversion [C] |
| 88 | R3/tfa | 3 | openai | revise | prisma_p.placeholder [B1]; synthesis.multiplicity [C] |
| 89 | R3/tfa | 3 | deepseek | revise | prisma_p.internal_note [B1]; prisma_p.placeholder [B1] |

## What was built (2026-11-12)

Branch `p1-protocol` (commits after 9f8fc1a). Mapping of the rules above to the code (`rk.production.protocol`, `rk.production.vote`):

| rule | what the branch does |
|---|---|
| R1a, R3 (partly) | `with_kernel_facts` writes the PRISMA-P items `registration`, `amendments`, `support` and `competing_interests` (`KERNEL_PRISMA_P`, systematic and scoping reviews) and the exclusion `E-lang`; the Ideator's versions under those keys are replaced. Not built: R1a's `dissemination` and `contact` items, and R3's check for a criterion or PRISMA-P text that admits another language (the Ideator is only told not to). |
| R1b/c | `check_protocol` requires every PRISMA-P item of a systematic review (`PRISMA_P_TEXT`) and rejects placeholders in the Ideator's texts ("to be reported/recorded" passes only as a deferral). The kernel's own text is not checked. |
| R2 | Partly: P1 does not vote on a version whose probe is above the record cap; the stage reruns with the cap as its reason (P2 still opens the approval, probe shown). "Every count a number" is not implemented. |
| R4 | `search_execution_lines`: publication years written out, each query sent verbatim to each source, search log, deduplication, cap after deduplication, language rule; shown to the judges and on the approval card. |
| R5 (partly) | PRISMA-P texts may name only the strategy's sources and may not promise searches the kernel does not run; narrowing keeps the sources the PRISMA-P texts name. Not built: "no probe figures in prose". |
| R6 | A systematic review needs outcomes (unique names); outcomes are checked and shown for narrative plans too; no meta-analysis of diagnostic accuracy. |
| R7 | Queries must balance parentheses and not mix AND and OR at one depth; a systematic review's extraction schema needs a study identifier field. |
| R8 | `dropped_prisma_p`: a redraft may not drop a PRISMA-P item of the previous draft, except a placeholder item or one that an open point of the Ideator's (any section, including a point built from a judge's reason) or the guidance (in P2, your revise comment) asks to remove (the item named in any spelling, with a removal word). |
| §5.1 | Judges get `render_protocol`, `changes` since the version voted on before, the open points with the authors' answers, and `KERNEL_RULES`. |
| §5.3, §7 | Point ledger (`points_ledger`): ids `p{attempt}.{role}.{n}`, blocking or advisory, closed only by the judge that raised it; a revise with no open blocking point of its own, or an answer outside the options, gets a synthesized point from its reason; scoped to the current cycle (status `open`, `resolved` or `retired`, flag `live`); answers (`PointAnswer`) stored with the version's `tried`, not in the body. Effective choice only tightens: an approve with an own open blocking point counts as revise; unanimity, the edge limit and `protocol_tries` are unchanged. Not built: §7 items 3 and 7 (a point about a kernel fact or a missing capability goes to the Ideator or the Scout to rebut; the kernel does not close it or record it as a limitation). |

Still open: the DTA detector can fire on pharmacokinetic AUC and on "salt sensitivity"; the placeholder rule's deferral lookahead includes when/once/after/before/if, so an ordinary phrase such as "to be recorded before randomisation" is rejected; a narrowing try's failure message names the kept sources by id and the dropped ones by display name (the matching is correct); stripping old guidance depends on the wording of `_revise_reason`; R2's complete-probe rule (every count a number) is not implemented; and what the rows above list as not built.
