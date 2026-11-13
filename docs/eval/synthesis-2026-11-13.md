# srrepro9: why synthesize/argue end with "the main claim never answered the fixed research question"

Read-only analysis of the srrepro9 run home. Runs used main 46c9e23. The cited files (synthesis.py, argue.py, claims.py, support.py, judge.py, arguer.md, judge.md:1-16) are byte-identical at HEAD f52e28e (`git diff --stat 46c9e23 HEAD`; judge.md only gained line 17), so the file:line references hold for the runs.
Everything below was read from the project DBs (events, claims, evidence, verifications, votes, tasks.output, task.created inputs) or from code, except claims marked **[INF]**.

Roles: arguer (the "Synthesis Lead") and lead are `anthropic/claude-haiku-5-5`. Judges are `openai gpt-6-luna` and `deepseek-v4.1-flash`, plus `claude-haiku-5-5` as judge_3, which is excluded as the same vendor as the arguer. `support_min_judges = 2` (config.toml:5, 20-33). With two valid judges, every V-SUPPORT and P1 vote needs both to agree (support.py:57-65; judge.md:16).

## 0. How each run ended

| project | included k | synth attempts | argue attempts | end |
|---|---|---|---|---|
| prostate | 31 | 5 (4 = narrative rejected: representativeness) | 3 | **failed**: argue a3 "never answered the fixed RQ; argue->synthesize has no use left" (seq 19177) |
| fructose | 321 | 7 (2, 5 = back-edge to read_extract) | 5 | **failed**: argue a5, same reason, "the judges disagree" (seq 62783) |
| ozone | 4 | 5 | 3 | **failed**: argue a3, same reason, "the judges disagree" (seq 2098) |
| pulmonary | 24 | 3 | 5 | **paused_budget**: argue used 40/40 role tasks. Its last two RQ votes were *answers* (a4, a5); the gate failed on uncertain claims and open objections (seq 10662-10663) |
| tfa | 58 | 2 | 3 | **failed**: degrade withdrew the main claim, so there was no main claim. Every RQ vote was *answers* (seq 20390-20392) |

So the RQ failure applies to prostate, fructose and ozone. Pulmonary and tfa failed through V-SUPPORT of the main claim, which has the same root (§1, §2).

## 1. Final argue attempt per project

V-SUPPORT labels are listed as (openai, deepseek). anthropic was always `same_or_unknown_vendor`. Every final main claim is **uncertain** with labels (partial, partial).

| project / attempt | main claim (abridged) | studies cited | V-SUPPORT judges' reason for "partial" | RQ vote (judge / judge_2) |
|---|---|---|---|---|
| prostate a3 (clm_01M79YMKD…) | "a single head-to-head comparison found AI sens 91% vs 92%, spec 57% vs 64% … the synthesised evidence does not yet establish how AI … compares across studies" | 1 (Oerther 2025) | the passage supports the single comparison; "it does not address whether synthesized evidence across studies establishes…" | does_not_answer / does_not_answer: "reports one comparison … says the cross-study comparison is not established" |
| fructose a5 (clm_01M7AES…) | "Across the included rat studies, fructose is reported to raise SBP in some rats … but the evidence read is sparse, mostly abstract-level, and not synthesized into a pooled direction" | 2 (Haque 2022, Dupas 2017) | "does not establish that the evidence is sparse, abstract-level, or not synthesized into a pooled direction" | does_not_answer / **answers**, split, so the conservative option (does_not_answer) applies |
| ozone a3 (clm_01M79P4Y…) | "The evidence … is too limited to say whether ozone … changes ΔE differently …; the only supported finding is that, in one trial, teeth became lighter in both groups" | 1 (AL-Omiri 2018) | deepseek: "the evidence **does report a between-group comparison**, describing the bleaching outcomes as comparable with P > .05, rather than leaving the difference undetermined" | **answers** / does_not_answer, split, so does_not_answer applies |
| pulmonary a5 (clm_01M79S16…) | "AI … not uniformly higher than manual …; AI AUCs above manual for CXR, but single studies … so no general accuracy advantage can be established" | 7 | "does not establish that the CXR comparisons are all limited to single studies with differing reference standards…" | answers / answers |
| tfa a3 (clm_01M79X5R…, rewrite clm_01M79X7T… withdrawn) | "prolonged heating associated with increases in some experiments, changes not uniform across oils and conditions…" | 5 | "does not state that the reported changes were not uniform across oils and conditions" | answers / answers |

The full RQ-vote reasons for all attempts were read from the `votes.opinions` column. Main claims across attempts, with the RQ outcome:
- prostate: a1 "few primary studies that report AUROC" gets 2× does_not_answer ("compares AUROC only in a few studies and does not address sensitivity or specificity"). a2 is the same AUROC-only claim, with the same reasons. a3 is the single-study sens/spec claim above.
- fructose: a1 "several studies … higher SBP … no consistent effect" and a2 got **answers ×2**, but both main claims were V-SUPPORT uncertain, so argue->argue ran. a3 "In one included rat study, 20% fructose … salt-sensitive hypertension" got does_not_answer ×2. a4 and a5 were split.
- ozone: a1 and a2 got does_not_answer ×2 ("says the available notes do not establish a difference … limited to one trial"). a3 was split.

What the RQ judges see is the claim text, its scope_limits and the Arguer's self-check, and nothing else (argue.py:1660-1663). The does_not_answer reasons quote the claim's own scope_limits and self-check, for example prostate a1 judge_2: "the scope_limits state that 'The synthesis findings do not cover sensitivity and specificity…' and the Arguer's self-check says…".

### How the main claim got narrow

The argue input holds only the **supported** findings of synthesize's passed attempt plus all notes (argue.py:441-450). Supported findings per final synthesis attempt:

- prostate a5: 3 findings. The AUROC finding (k=4) was withdrawn because its rewrite was uncertain. The finding "In several studies AI higher specificity but lower sensitivity" cited k=1 and was **contradicted**. It was narrowed to "In one head-to-head comparison … 91% vs 92% … 57% vs 64%" (supported), which became the main claim. "Some studies report AI comparable" was withdrawn. The earlier supported AUROC finding (a2, kept in a3) was not kept in a5: the Skeptic's priority-1 objection was conceded ("The synthesis is not exhaustive … relies on passages from selected studies, yet the 2024 PI-CAI paired confirmatory study reports…"; `claim_problems` on clm_01M79Y6WTJQJTGYY34AG91YHVM).
- fructose a7: 3 kept findings, all narrowed rewrites from a6. The three were: "In one included rat study, 20% fructose…"; "… higher in some fructose-treated rats than in controls, in the studies whose passages were read" (k=3); and "In the included rat study, a fructose-enriched diet with normal salt did not change SBP…". Their originals in a6 were "most studies", "several included studies" and "direction not uniform across studies", all uncertain.
- ozone a5: 1 kept finding, "In one bleaching study, teeth became lighter … in both groups", a narrowed rewrite of a3's "Some included trials report colour change as L*a*b*…". The 3 new findings were withdrawn. One of them, a rewrite reading "the two arms reported comparable bleaching outcomes … (P > .05)", got (partial, **supported**) and failed on one label.
- pulmonary a3: the "AUCs between 0.71 and 0.988" finding (CXR, k=3) was kept, plus 2 narrowed rewrites. Cross-modality findings were withdrawn.
- tfa a2: 2 supported findings plus 1 narrowed rewrite.

Narrowing mechanics: NARROW_TASK (claims.py:39-46) asks for "a smaller population, setting, exposure or outcome, a hedged rather than categorical verb", and the kernel links **the same evidence** (claims.py:123-126). A rewrite therefore cannot add the missing studies. It can only shrink the claim to the studies already cited.

Totals over all 5 projects:
- 66 narrowings: **9 supported (14%), 57 withdrawn**.
- Of the 9 supported, **5 turned a cross-study claim into a single-study claim**.
- 12 of 66 rewrites added "in the studies whose passages were read".

## 2. Synthesis findings: wording scope vs citations

78 finding claims over all synthesize attempts; 74 were not carried from an earlier attempt. The wording class comes from a regex plus hand fixes:
- *single*: "in one / a single / the one / named study".
- *cross*: every other wording, i.e. "across", "several", "most", "some studies", "in the included studies", or an unqualified generalisation.

| class | n | V-SUPPORT supported | studies cited (median, range) |
|---|---|---|---|
| cross-study wording | 63 | **5 (8%)** | 3 (0-6) |
| cross-study, evidence-state wording ("does not establish", "too sparse", "heterogeneous … descriptive", "not enough comparable data") | 8 | **0** | 2 (1-3) |
| single-study wording | 11 | 3 (27%) | 1 (1-2) |

Per project, for cross-study findings:

| project | K | cross findings | supported | cited k median / max | share of K cited (median / max) |
|---|---|---|---|---|---|
| prostate | 31 | 17 | 1 | 3 / 6 | 9.7% / 19.4% |
| pulmonary | 24 | 14 | 1 | 3 / 4 | 12.5% / 16.7% |
| fructose | 321 | 14 | 1 | 2 / 5 | **0.6% / 1.6%** |
| ozone | 4 | 7 | 0 | 2 / 3 | 50% / 75% |
| tfa | 58 | 11 | 2 | 3 / 5 | 5.2% / 8.6% |

Judge labels over all non-carried findings: partial 97, unsupported 20, supported 15, contradicted 4.

In the 106 non-supported judge opinions on cross-study findings:
- **76 rationales name breadth** ("several", "most", "across", "only one", "single", "consistent"…), for example: "It does not show that several studies report this"; "does not establish … that AI had a higher AUC in most such studies".
- **49 name evidence-state statements** the passages cannot carry ("does not establish that the evidence is sparse / not pooled / heterogeneous").

The Lead *does* write the cross-study answer, but in unverified fields:
- The narrative `groups[].item_ids` cover 26/31 (prostate a5), 24/24 (pulmonary a3) and 38/58 (tfa a2) studies.
- `direction_summary` / SWiM `results` state the pattern. For example, prostate a5: "Across studies reporting both measures, AI commonly showed higher specificity but lower sensitivity than radiologists"; fructose a7: "fructose-treated arms were generally described as having higher systolic blood pressure than controls, with a minority of null findings".

Argue never sees groups or SWiM: `grep narrative|swim|direction_summary src/rk/production/argue.py` finds nothing. Only finding claims reach it.

### Why the judges cannot verify breadth (code)

- support.py:68-85, 107-112: the judge gets `"\n\n".join(texts)` of quote/paraphrase passages, with **no study label, title, design or arm identity** and no interpretation.
- judge.py:29-33: "partial (the claim is broader, stronger or more specific than the evidence)".

So "in several studies", "the ozone arm" or "in two trials" cannot be checked from the passages alone. Ozone shows this: four rewrites stating "comparable … (P > .05)" between ozone and H2O2 got partial because "the evidence text never names the groups or identifies them as ozone-containing versus hydrogen peroxide bleaching" (rationales of the a1, a2, a3 and a5 rewrites).

### The prompts

- synthesis.py:190-196 (NARRATIVE_TASK): "Write the findings as atomic claims, each no stronger than its evidence, each with evidence items whose note_ids are taken from the studies' notes … Never state a pooled estimate, confidence interval, **count** or other number the kernel computed: … describe patterns in words."
- arguer.md:10: "Never write a pooled estimate, interval, I², **study count** or any other number the kernel computed … Describe patterns in words".
- arguer.md:11: "a universal word (all, every, never, always, none) needs at least two studies whose notes state it."
- arguer.md:9: findings with `evidence` items of `note_ids`. There is no rule on how many.

**Neither text tells the Lead to cite every study a pattern claim counts, or to word a claim to its cited studies** ("in k of the n studies that report X"). The only breadth rule is "at least two studies" for universal words. The "study count" ban pushes the Lead to "several / most / some" [INF: the kernel computes no study count for systematic reviews, yet the ban reads as general].

The scope statements of a claim ("sparse", "not pooled", "single studies with differing reference standards") are allowed in the claim text. arguer.md:21 even says "narrow the main claim to what the findings support and state what is missing as a limitation (its `scope_limits`)", but the claims put it in the text as well. The judges then mark them partial (all five final main claims; 0/8 evidence-state findings supported).

### The Lead's input: all studies, no cap

- synthesis.py:1242-1253: the narrative input is `"studies": [_study_input(ps, u) for u in s.usable]`.
- synthesis.py:448-454: `_study_input` passes **every** note of the study.
- prompts.py:35-39 renders inputs whole.
- There is no cap on studies or notes per prompt in synthesis or the renderer.

Measured narrative-task inputs (task.created payloads):

| project | studies | notes | input chars |
|---|---|---|---|
| ozone | 4 | 22 | ~15k |
| pulmonary | 24 | 210 | ~103k |
| prostate | 31 | 411 | ~169k |
| tfa | 58 | 382 | ~203k |
| fructose | 321 | 1785-1857 | **~870-890k** |

Plan task (fructose): 321 studies, ~546-557k chars.

[INF] At about 4 chars/token, fructose is about 220k tokens for a Haiku-class model. The input is complete but probably beyond what the model attends to: findings cite 0-5 of 321 studies, and the narrative groups cover 16-58 of 321.

### The Arguer's requests never reach synthesize

- Every Arguer round asked for exactly the missing cross-study finding. Fructose a5: "A synthesis finding that **counts**, across the included studies, how many report higher, equal or lower systolic pressure for fructose versus control, separately for drinking-water and diet routes". Pulmonary a3-a5 asked for "tabulates paired AI and manual AUC, sensitivity and specificity for each included study".
- The synthesis-request vote (argue.py:1560-1590) shows the judges only labels, designs and the schema (argue.py:1599-1618), **not the extracted values**. All 13 votes or kernel decisions were `proceed`. Typical judge reasoning: "the subject gives no extracted values … establishing that the requested … can be produced".
- The RQ back-edge to synthesize carries only the RQ-vote reason (argue.py:1625-1644). When the vote splits that reason is just "the judges disagree", which was the guidance of pulmonary synth a2/a3 and fructose a7. The requests themselves never became synthesize guidance.

## 3. Cross-study answer: is there a kernel summary, and could there be one?

- **There is no kernel-computed effect-direction table or vote count for systematic reviews.**
  - The only cross-study kernel summary is the scoping-review evidence map: `MAP_TYPES = frozenset({"scoping_review"})` (synthesis.py:170), and `_map` runs only for those (synthesis.py:1119-1164).
  - `rk.stats.effects.MEASURES` covers RR/OR/RD/MD/SMD only.
  - A grep for vote-count / effect-direction terms finds nothing.
  - The SWiM items are free text the Lead writes (synthesis.py:168-169, 278-288).
- A direction can be computed from the extraction rows of read_extract's passed attempt. Rule: usable rows (`problem is None`, not `not_reported`, numeric), with both fields of a pair in the same study. Every such study had one value per field, except 1 pulmonary AUC study.

| project | pair (AI/treatment vs comparator) | studies with both / K | direction (treatment > / < / =) |
|---|---|---|---|
| prostate | auroc_ai vs auroc_radiologist | 19/31 | 8 / 8 / 3 |
| prostate | sensitivity_ai vs _radiologist | 18/31 | 4 / **13** / 1 |
| prostate | specificity_ai vs _radiologist | 19/31 | **11** / 5 / 3 |
| prostate | any of the three | 29/31 | — |
| pulmonary | auc_ai vs auc_manual | 9/24 (8 single-valued) | **6** / 1 / 1 |
| pulmonary | sensitivity_ai vs _manual | 6/24 | 4 / 2 / 0 |
| pulmonary | specificity_ai vs _manual | 5/24 | 4 / 1 / 0 |
| pulmonary | any pair | 12/24 | — |
| fructose | sbp_mean_treatment vs _control | 81/321 (56 full text, 25 abstract) | **79 / 2 / 0** |
| fructose | full MD computable (`_computable_ids`, 6 fields) | 45/321 | 44 / 1 / 0 |
| ozone | mean_treatment vs mean_control | 1/4 | 1 / 0 / 0. The Lead's a5 narrative says that study's comparator is a laser method, not H2O2 alone [not checked] |
| tfa | tfa_mean_treatment vs _control | 7/58 | 7 / 0 / 0. Units mixed across studies (tfa_unit_as_reported); direction within a study is still defined [INF] |

[INF] Caveats on these counts:
- I did not check units, arms or time points per row, and these rows did not pass V-SUPPORT themselves.
- The rows did pass read_extract's gate (quote-verified values), which is what V-STATS already trusts for pooling.

These counts answer the RQ as asked:
- fructose: 79 of 81 studies report higher SBP with fructose.
- prostate: AI sensitivity lower in 13/18, specificity higher in 11/19, AUROC split 8/8/3.
- pulmonary: AI AUC higher in 6/8.

Those are the same patterns the Lead put into the unverified `direction_summary`. For ozone, the notes of the AL-Omiri trial state "comparable … P > .05", which is an answer ("no difference shown in 1 trial"). It was lost to the passage-identity problem in §2.

## 4. Fructose pooling

**Protocol** (frozen v8, synthesis_plan):
- method `meta_analysis`, measure MD, model "Random-effects model of the mean difference in systolic blood pressure between fructose and control groups", `pool_model: REML`, hksj auto.
- heterogeneity: "Cochran's Q, I-squared and tau-squared, with a prediction interval".
- sensitivity: "Leave-one-out analysis and comparison of pooled estimates by fructose route", plus risk-of-bias stratification.
- min_studies 2.

So route is pre-specified as a sensitivity comparison, and between-study heterogeneity is to be handled with random effects and reported (Q, I², τ², PI). Clinical diversity is not given as a reason to skip pooling.

**Task texts:**
- PLAN_TASK (synthesis.py:179-189): "for each group judge whether its studies are comparable on population, intervention, comparator, outcome and design (each 0, 1 or 2 with a reason), whether they may be pooled at all (comparable) and why."
- VOTE_QUESTION (synthesis.py:204-207): "Should the kernel pool these studies … with the protocol's pre-specified pool options, given the comparability assessment? Answer pool to pool them, or no_pool to synthesize them narratively."
- The kernel scores any comparability criterion of 0 as not comparable (synthesis.py:837-840).
- A grep for `random.effect|clinical divers|subgroup` in roles/, synthesis.py, vote.py and protocol.py finds **nothing**. **No text tells the Lead or the judges that clinical diversity is handled by the random-effects model and subgroup or sensitivity analyses rather than by not pooling.**
- The vote subject does include the synthesis_plan (synthesis.py:999). Both judges voting `pool` cited it.

**Per attempt.** k = group size after the kernel added the computable studies the planner omitted. Planner input was about 550k chars each time.

| attempt | planner listed | k | added by kernel | comparable | outcome / reason |
|---|---|---|---|---|---|
| a1 | 5 | 55 | 50 | false | "Only the first five studies were listed … heterogeneous in fructose route and dose … several arms lack SD or n" leads to fallback |
| a2 | 50 | 90 | 40 | true | back-edge to read_extract for gaps (Hernández 2015) |
| a3 | 6 | 53 | 47 | true | **vote split**: judge **pool** ("random-effects pooling is defensible … sensitivity analyses"), judge_2 **no_pool** ("47 of the 53 listed studies … 'were not assessed for comparability by the planner' … 'Puyó 2017' listed twice") |
| a4 | 0 | 49 | 49 | false | intervention 0, design 0, population 0 ("No study has a computable effect" — wrong: 45-50 were computable) |
| a5 | 28 | 69 | 41 | true ("differences in fructose route and dose are heterogeneity to be explored, not grounds to exclude") | back-edge to read_extract (Kho 2015, Chen 2022) |
| a6 | 1 | 46 | 45 | true | **vote split**: judge pool; judge_2 no_pool ("comparability assessment covers only Park 2015 … All 45 other studies appear only under added_by_kernel … no comparability information at all") |
| a7 | 79 | 107 | 28 | false | intervention 0, population 0: "differ widely in fructose route…, concentration, duration, rat strain…, salt co-intervention" leads to fallback |

Both no_pool votes rest on the **kernel's own note** in the vote subject (synthesis.py:994-997): "they were not assessed for comparability by the planner (its comparability scores do not cover them)". That happened because the planner, given 321 studies, listed only 1-6 of them [INF: an input-size effect]. Every `comparable: false` came from clinical diversity (route, dose, strain, salt), which the protocol pre-specified as random-effects heterogeneity and a route sensitivity analysis.

Ozone and tfa were not pooled for a real data reason, which is correct per protocol:
- ozone: 1 computable study of 4, below min 2.
- tfa: 1 computable of 58 in `_computable_ids`; mixed units; most rows lack SD or n.

## 5. Calibration options

1. **Kernel effect-direction table (SWiM vote counting by direction) for systematic reviews, as a kernel-written claim argue can use.**
   - How: generalise the scoping map (synthesis.py:1119-1164, MAP_TYPES:170) to a `direction` artifact. Per pre-specified outcome, count studies with treatment > / < / = comparator from paired extraction fields. Use `OutcomeSpec.fields` for MD/RR, and a protocol-declared AI/comparator field pair for DTA outcomes, e.g. sensitivity_ai/sensitivity_radiologist. Verify it like `stat_result` (V-STATS/V-NUM against the artifact, not judges).
   - Evidence: fructose 79/81 higher; prostate sensitivity 4/13/1 and specificity 11/5/3; pulmonary AUC 6/1/1; tfa 7/0/0 (§3). The Arguer asked for exactly this (fructose a5 request).
   - Expected effect [INF]: a supported cross-study finding naming the RQ's outcome. The main claim can then state a direction across k studies, and the RQ votes that failed on "one study / few studies / not across studies" (prostate a1-a3, fructose a3-a5, ozone a1-a2) should pass.
   - It also removes the "study count" ban's bite: the kernel states the count, and the Lead describes it.
2. **Give V-SUPPORT judges the passage's study identity.**
   - How: in support.py `_evidence_notes`/`evidence_texts` (68-85, 111), prefix each passage with study label, title, design and arm fields (from the extraction rows), and group passages by study.
   - Evidence:
     - 76/106 non-supported cross-finding opinions turn on breadth the judge cannot count.
     - The ozone "comparable, P > .05" rewrites failed four times on "never names the groups … ozone versus hydrogen peroxide"; one got (partial, supported).
     - pulmonary judge: "does not establish that these are direct comparisons within the same studies".
   - Expected effect [INF]: findings that cite enough studies get verified as written. The ozone between-group answer becomes supportable. This does not loosen the rule.
3. **Narrative contract: the cited studies set the claim's scope; evidence-state statements go to scope_limits.**
   - How:
     - Change NARRATIVE_TASK (synthesis.py:190-203) and arguer.md:9-11 to: "a finding about studies cites every study it counts, or says 'in k of the studies whose notes are cited'". Allow counts the Lead derives from its own cited notes; restrict the ban to kernel-computed numbers.
     - Keep "sparse / not pooled / heterogeneous / not established" out of the claim text: they go in `scope_limits`, which judges do not verify.
     - For large k, give the Lead a per-outcome subset (the studies with the outcome's fields, or batches like ASSESS's `BATCH`) instead of 870k chars.
     - Pass the Arguer's `needs_synthesis` requests into the RQ back-edge guidance (argue.py:1625-1644), and show the synthesis-request judges the counts of studies with the requested fields (argue.py:1599-1618).
   - Evidence:
     - Cross-study findings: 5/63 supported, citing 0.6-19% of K.
     - Evidence-state findings: 0/8 supported.
     - All 5 final main claims got partial on evidence-state clauses.
     - All 13 synthesis-request decisions were proceed, and back-edge guidance was "the judges disagree".
   - Expected effect [INF]: fewer partial verdicts, narrowing less often collapsing to "in one study", and the second synthesize attempt acting on what argue lacked.
4. **Pooling: say that clinical diversity is the random-effects model's job, and stop the kernel note from voting no_pool.**
   - How:
     - Add to PLAN_TASK and VOTE_QUESTION (synthesis.py:179-207): "when the protocol pre-specifies a random-effects model, differences in dose, route, strain or duration are heterogeneity the model and the pre-specified subgroup or sensitivity analyses address. Answer not comparable or no_pool only when the studies do not measure the same outcome for the same comparison."
     - Have the kernel score comparability for added studies from the extracted design fields (e.g. fructose_route, fructose_percent, rat_strain), or reword the note at synthesis.py:994-997 so that "not assessed by the planner" does not read as "not comparable".
   - Evidence: both fructose no_pool votes cite that note; 3 of 7 attempts declared non-comparability on route, dose and strain alone; the protocol pre-specifies REML with Q/I²/τ²/PI and a by-route comparison; 45 studies were computable.
   - Expected effect [INF]: fructose pools 45 studies (MD, REML), giving a `stat_result` that answers the RQ directly.
