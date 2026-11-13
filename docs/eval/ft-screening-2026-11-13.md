# Full-text loss of the reviews' included studies: srrepro9 (main 46c9e23)

Read-only analysis of the srrepro9 run home (rk eval sr-repro on 46c9e23), attempt 1 of each project. Decisions, finals and votes were taken
from `screening`, `screening_final` and `votes` (subject_type 'record', the full-text question), and the shown text from
`rk.production.screening.full_text_input` (the same function the screeners' input comes from). Criteria are the frozen
protocol's `eligibility`. I wrote no data. **[INF]** marks a claim I did not read directly from data or code.

Scope: 18 review-included studies reached full-text screening and were excluded there: prostate 7 of 10, pulmonary 5 of
12, tfa 3 of the 18 found (12 included; 2 more were never screened at full text because they had no full text and no
abstract, and 1 was excluded at title/abstract), fructose 2 of 22, ozone 1 of 4.

Causes: **A** = a screener misread a criterion that, as written, admits the study; **B** = the protocol is narrower than
the review's inclusion; **C** = the text shown lacked the information (abstract only, or an excerpt without it);
**D** = the judges split and the conservative option (exclude) decided. **D\*** = two of the three judges said
include, so the record was excluded only because the rule needs every judge to agree. In the cause column the
primary cause comes first.

## Per-study table

| # | entry | title (≤60) | text kind | screener_a (crit) | screener_b (crit) | vote (judges incl/3) | cause | evidence |
|---|---|---|---|---|---|---|---|---|
| P0 | prostate | Deep learning for fully automatic detection, segmentation… | sections | no (E1) | no (E1) | none (agreement) | A | The excerpt's Results say IVO results "are compared with radiologist-assigned pre-biopsy PI-RADS scores … (N=106)" on the same test patients, with sensitivity and specificity for both. I1 is met. a called the radiologist only a "reference point"; b said "not the same examinations". |
| P1 | prostate | A multicenter study of AI-aided software for detecting… | sections | yes (I1) | no (E1) | exclude, conservative (1/3) | B, D | The design is radiologists with vs without AI, with no standalone AI reading in the text. I1/E1 require an "artificial-intelligence reading" vs a PI-RADS reading. The review counted assisted reading; the protocol does not. |
| P2 | prostate | Assessment of a fully-automated diagnostic AI software… | abstract_only (no PMCID) | yes (I1) | no (E1) | exclude (0/3) | C (B?) | The abstract compares mdprostate only with "two meta-analyses of PI-RADS v2.1", with no same-exam radiologist arm. Whether the full text has one is unverified **[INF]**. If it has none, the review was broader (B). |
| P3 | prostate | Fully Automated Deep Learning Model to Detect csPCa at MRI | abstract_only (216 chars; PMCID PMC11366675) | unclear (I1) | unclear (I1) | exclude (0/3) | C | Both screeners were unclear because no full text was shown. At full text, both-unclear goes to the vote, and the judges excluded "with only abstract-level text". The record has a PMCID but was not retrieved (problem "no full text"). |
| P4 | prostate | Prostate cancer risk assessment and avoidance of biopsies… | sections (excerpt 6000 of 53805 chars) | yes (I1) | no (I2) | exclude, conservative (1/3) | C, D | The full text reports per-reader sensitivity/specificity ("clinical PI-RADS achieved 14% specificity at 98% sensitivity…; UNet-Likert…"), 8 hits. The excerpt has 0 hits for "sensitivity": the structured abstract's own "Methods"/"Results" sub-headings were parsed as sections and used up the results budget. |
| P5 | prostate | Detection and PI-RADS classification of focal lesions… | abstract_only | yes (I1) | no (E1) | exclude, conservative (**2/3**) | A, D\* | The abstract says the radiologists "independently reviewed biparametric MRI" and "DLA results were obtained using bpMRI", with a pathology reference and AUROC/sensitivity/specificity for both. b's "not … on the same examinations" is a misreading. |
| P6 | prostate | PI-RADS(AI): a new human-in-the-loop AI model… | abstract_only (PMCID PMC10006083) | yes (I1) | no (I2) | exclude (0/3) | C, A | b excluded because the abstract "does not state" the reference standard. Under the role prompt that silence is "unclear", and (yes, unclear) is an include under `dual_rule`. The full text was not retrieved despite a PMCID. |
| U0 | pulmonary | A machine learning approach for the prediction of PH | sections (methods, results) | unclear (I2) | no (E2) | exclude (0/3) | B | The models use echocardiographic and RHC *parameters* ("Table 1 … Aortic diameter"), not images, to classify pre- vs post-capillary PH, with no clinician comparator. I2 ("AI image interpretation") excludes it as written. |
| U1 | pulmonary | A machine learning CMR approach to extract disease features… | sections | yes (I3) | no (I3) | exclude, conservative (**2/3**) | A, D\* | The text says the "diagnostic accuracy of this approach was compared with standard CMR measurements", AUC 0.92 "slightly higher than standard CMR metrics". I3's "…or report both accuracy measures" admits this. b read I3 narrowly. |
| U2 | pulmonary | Deep learning for detection of exercise-induced PH using CXR | sections | no (E2) | no (E2) | none (agreement) | B | The excerpt has no clinician/manual reading. I3 requires a comparison with manual interpretation (or both measures). a also misread the modality as echo (A, not decisive). |
| U3 | pulmonary | Diagnostic accuracy of an automated AI-derived RV/LV ratio… | abstract_only | no (E2) | yes (I3) | exclude, conservative (**2/3**) | A, D\* | The abstract calls the tool "automated artificial intelligence derived" and reports AUC 0.752 vs RHC and vs manual MPA. a decided that it is "not ML", which goes beyond the text. |
| U4 | pulmonary | Explanatory deep learning to predict elevated PA pressure (VSD) | sections | yes (I3) | no (I4) | exclude, conservative (**2/3**) | D\* (A/B contestable) | The endpoint is "occurrence of PAH in VSD children before or after surgery", with AI vs human observers AUC 0.81 vs 0.65. Whether this is diagnostic accuracy is a judgement call. Two judges said include. |
| T0 | tfa | Effect of traditional Chinese cooking methods on fatty acid… | abstract_only | unclear (I3) | unclear (I3) | exclude (0/3) | C (A minor) | The abstract gives no temperatures (E2). Its "vegetable salad oil" arm is an unheated control that I3 admits, and no screener or judge noted it. Both-unclear went to the vote and was excluded. |
| T1 | tfa | Analysis of trans fatty acids in deep frying oils by three… | abstract_only | unclear (I3) | unclear (I1) | exclude (0/3) | C | The abstract states no frying temperature or duration. Both were unclear, and the vote excluded. |
| T2 | tfa | Nonhydrogenated cottonseed oil … deep fat frying medium… | abstract_only | yes (I3) | no (I4) | exclude (0/3) | C, A | b's "TFA in the fries, not the oil" misreads the abstract ("Both the oil and the french fries were evaluated … trans-fatty acids"). The judges cited I3 (different oils). Same-oil day-1..5 arms are not shown in the abstract. |
| F0 | fructose | Pharmacokinetic and pharmacodynamic properties of carvedilol… | abstract_only | unclear (I2) | no (E4) | exclude (0/3) | C (A minor) | b cited E4 (route), which is wrong: fructose was in drinking water. The judges excluded on I3 because the abstract reports only carvedilol BP responses. The F vs C SBP is presumably in the full text **[INF]**. |
| F1 | fructose | Aqueous extract of large cardamom inhibits vascular damage… | abstract_only | yes (I3) | no (I2) | exclude (0/3) | C | The abstract says "each group was fed with 10% fructose", so it shows no fructose-free control. The review included the study, so the full text presumably has one **[INF]**. |
| O0 | ozone | Color assessment after bleaching with HP versus ozone (RCT) | abstract_only | no (E4) | no (E4) | none (agreement) | A | I1/I4 say "ozone, alone or combined with hydrogen peroxide, against hydrogen peroxide bleaching alone". 40% HP vs gaseous ozone is that comparison. Both screeners misread it. |

## Cause totals (18 studies)

- **A (misreading): 9 studies have it.** It is decisive in 7: P0, P5, P6, U1, U3, T2, O0. It is minor in T0 and F0. Screener_b made the misreading in P0 (with a), P5, P6, U1, T2 and O0 (with a). Screener_a made it in P0, U3 and O0.
- **B (protocol narrower): 3 firm (P1, U0, U2).** Two more are possible: P2 and U4.
- **C (text lacked the information): 9.** Abstract only: P2, P3, P6, T0, T1, T2, F0, F1. Excerpt cut: P4. 11 of the 18 were assessed on the abstract alone; P5, U3 and O0 were abstract-only too but their abstracts sufficed.
- **D (judges split → conservative exclude): 6** (P1, P4, P5, U1, U3, U4). Of these, **4 are D\*** (P5, U1, U3, U4), where two of three judges said include.
- Primary-cause split: C 9, A 5, B 3, D 1.
- Related rule effects (data): at full text, both-unclear is a disagreement. All three such cases (P3, T0, T1) were then excluded by a unanimous vote. Across projects, full-text votes almost always exclude: prostate 73 excl / 6 incl, pulmonary 54 / 0, tfa 54 / 5, fructose 305 / 34. Majority-include-but-excluded votes: prostate 6 (1 review study), pulmonary 5 (3), tfa 2 (0), fructose 27 (0).
- Retrieval (data): of the records screened at full text, abstract-only were prostate 96/177, pulmonary 47/97, tfa 141/162, fructose 493/703 and ozone 5/8. Of those abstract-only records, 10, 5, 1, 20 and 0 have a PMCID.

## Sample: 20 non-review full-text disagreements (screener_b "no", screener_a yes/unclear; seed 13)

Prostate (10 of 62): a yes 5 / unclear 5.
- b is right under the protocol in 7: S2 (radiomics, no reader arm), S3/S5/S7 (AI-assisted vs unassisted reading, which the protocol excludes, as in P1), S4 (editorial), S6 (NCCN risk among confirmed PCa), S9 (AI-only abstract).
- b is defensible but should have been unclear in 2. In S0 the text names no PI-RADS and reports PCa, not csPCa. In S8 the shown abstract is about a nomogram and does not match the title, likely the wrong abstract on the item **[INF]**.
- b is wrong in 1: S1 applied E2 to a conference abstract *with* accuracy data (AI AUC 0.76 vs PI-RADS 0.75). E2 excludes only those "without accuracy data".

Fructose (10 of 284): a yes 9 / unclear 1.
- b is right in 3: S0 (portal pressure, no SBP), S2 (mean BP only), S4 (no BP reported).
- b's "no" rests on silence, so unclear was warranted, in 3: S5 ("hypertension", SBP not named, and b's E3 reason misreads it as "without data"), S8 (control not described), S9 ("blood pressure", not SBP).
- b is wrong in 4:
  - S1: a regular-chow group of 10 rats exists, so I2 is met. 2/3 judges said include, but it was excluded.
  - S3: five of six groups got fructose, so the sixth is the control.
  - S6: the hypertensive OVX group without fructose is the control. The judges included it 3/3.
  - S7: a conference abstract reporting SBP with an HFFD vs control comparison. The judges included it 3/3.

**Verdict.** Screener_b is too strict in general, not only on the review studies. Combined, its "no" is right in 10/20, should have been "unclear" in 5/20 and is wrong in 5/20. In prostate it mostly applies the protocol correctly (7/10), so there the review studies are special: the protocol is narrow (AI-assisted designs) and much of the text is abstract-only. In fructose its "no" is wrong or premature in 7/10.

The pattern also shows in the reasons. Share of "no" decisions whose reason is worded as an absence ("does not establish / specify / report / state…"; regex heuristic **[INF]**):

| | screener_b | screener_a |
|---|---|---|
| prostate | 62/141 (43%) | 14/81 (17%) |
| pulmonary | 25/59 (42%) | 4/27 (14%) |
| tfa | 35/77 (45%) | 7/64 (10%) |
| fructose | 209/364 (57%) | 19/96 (19%) |

## Do the prompts push toward exclusion?

- `screener_a.md` and `screener_b.md` are byte-identical (`diff`), so no role text targets screener_b. Their wording points the other way: "when the text does not settle a criterion, say "unclear" rather than guessing" (line 2). `CRITERIA_AS_WRITTEN` (screening.py 171-175) only widens inclusion. b's strictness is therefore model behaviour (gpt-6-luna) **[INF]**.
- Wording that does tilt toward exclusion:
  - The role prompt states the consequence of "unclear" only for single screening: "(the text does not settle a criterion; in single screening it is then included…)" (line 14). Nothing tells the screener that at dual full text (yes, unclear) includes while (yes, no) goes to a vote that almost always excludes.
  - Negatively phrased exclusion criteria invite treating absence as failure **[INF]**. Examples: fructose E3 "studies that report no systolic blood pressure outcome", prostate E4 "do not report the data needed…", E1 "do not evaluate…". b cites E3 in 172 of its 364 fructose "no" decisions, and E1 in 104 of its 141 prostate "no" decisions.
  - The full-text vote question (`_vote_question`, screening.py 199-200) is "Does it meet the eligibility criteria: include it or exclude it?". Unlike the title/abstract question, it gives no rule for an unsettled criterion. The judges read silence as exclude, saying "cannot be confirmed … the conservative choice is exclude" (P3, T0, T1, P6). They also see the screeners' views (`_vote_on` passes `screeners`) and often adopt b's ground, e.g. "Screener_b's exclusion on I2 is supported".
  - The vote rule (`vote.py` 3-4) says "any disagreement … gives the conservative option". At full text that option is exclude (`CONSERVATIVE`, screening.py 155).

## Calibration options (effects estimated from this run's data)

1. **"No" needs positive evidence.** Add to the full-text task, both screeners, that a "no" must quote text showing the criterion fails; a criterion the text is silent on is "unclear". Then (yes, unclear) includes under the existing `dual_rule`.
   - Recall: if b's absence-worded "no" decisions after an a "yes" became "unclear", P4, P6 and U1 would be included (P2 too, though it is likely a correct exclusion). Prostate would go 3→5(6)/10 and pulmonary 7→8/12.
   - Precision: an upper bound of non-review full-text includes added is prostate +19, pulmonary +10, tfa +3, fructose +77 (regex heuristic **[INF]**). The sample suggests at least half of the fructose ones are eligible under the protocol (S1, S3, S6, S7).
2. **Majority instead of unanimity in the full-text record vote.** Use a majority of admitted judges; keep the conservative option for ties and failures.
   - Recall: +P5, U1, U3, U4, so prostate 3→4/10 and pulmonary 7→10/12.
   - Precision: non-review additions of prostate +5, pulmonary +2, tfa +2, fructose +27. Two of these were in the sample (fructose S1, which a majority of judges also included, and prostate S1), and both are eligible under the protocol.
   - This is the cheapest change. It does not touch the title/abstract rule, where the conservative option is already include.
3. **Give the screeners the evidence.**
   - (a) In `fulltext_excerpt`, stop treating a structured abstract's sub-headings as body sections, or give `results` a larger or uncapped share of `FULLTEXT_CHARS`=6000. The shown excerpt covers about 11-20% of a 30-54k-char full text **[INF]**; P4's per-reader accuracy was cut.
   - (b) Fix retrieval for records with a PMCID that end up abstract-only: P3 and P6, and 36 of 782 abstract-only records across the projects.
   - (c) At full text, do not let a vote exclude an abstract-only record whose disagreement or unclear rests on information the abstract lacks. Record it as not assessable or not retrieved, as PRISMA's "reports not retrieved" does.
   - Recall: up to the 9 C studies (P2-P4, P6, T0-T2, F0, F1) become decidable; how many would then be included is unknown **[INF]**.
   - Precision: roughly neutral for (a) and (b), since decisions rest on evidence. For (c), excluded counts drop and the not-retrieved counts rise.
4. **Protocol breadth (question/protocol stage, not screening).** Where the review's practice is broader, word the criterion that way. Examples: "AI reading, standalone or assisting a radiologist" (P1); I3 in pulmonary allowing no clinician comparator (U2); "image-derived parameters" (U0).
   - Recall: +3 firm (P1, U0, U2).
   - Precision: in the prostate sample, 3 of 10 non-review disagreements are AI-assisted designs (S3, S5, S7), and similar records would enter. This changes the reproduced question, so it is a decision about fidelity, not a screening fix.

Not addressed by any option: O0 and P0, where both screeners misread a criterion that plainly admits the study (ozone alone vs HP alone; the IVO subset with PI-RADS). They passed `CRITERIA_AS_WRITTEN` unaided. A comparator example in the criterion text, or a per-criterion yes/no checklist at full text, might catch them **[INF]**.
