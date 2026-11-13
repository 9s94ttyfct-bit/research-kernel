# Search and title/abstract screening on the reproduction set (srrepro7, srrepro8; 2026-11-12)

The protocols of srrepro8 (`main` `850284c`: OpenAlex searched in titles, abstracts and keywords, Europe PMC in titles and abstracts; driver omp_gateway `gpt-6-luna`) all froze, so the next layers can be measured. srrepro7 (`7753799`, full-text search) gives the screening data of two entries (cade, tfa). The counts come from the run homes, read with the evaluation's matching (`rk.eval.sr_repro`).

## 1. Search recall of the included studies (srrepro8)

| entry | found by the search | records screened |
|---|---|---|
| ai-prostate-mri | 10/10 | 963 |
| ai-pulmonary-hypertension | 12/12 | 560 |
| cade-colonoscopy | 5/6 | 59 |
| ozone-bleaching | 4/4 | 223 |
| fructose-rats-sbp | 15/24 | 611 |
| tfa-heated-oils | 20/33 | 550 |

The target is 0.85 at the search level. Fructose (0.63) and tfa (0.61) fall below it.

**The scope is not the main cause.** Each frozen strategy was searched both ways. The table counts the included studies that have a DOI.

| entry | full text | titles and abstracts |
|---|---|---|
| prostate | 10 | 10 |
| pulmonary | 12 | 12 |
| cade | 5 | 5 |
| ozone | 3 | 3 |
| fructose | 21 of 22 | 15 of 22 |
| tfa | 21 of 33 | 20 of 33 |

**Fructose.** Every query ANDs the outcome; query 1 uses only "systolic blood pressure" OR "systolic pressure". Of the 6 records lost to the scope, 5 have an abstract in Europe PMC (the sixth is not in Europe PMC). None of those 5 says "systolic (blood) pressure", and all 5 say "blood pressure". Queries 2 and 3 also AND a diet phrase. No judge point asked for the outcome in the search; the Scout added it.

**TFA.** 13 included studies are missed in both scopes, and 10 of them have an abstract in Europe PMC.
- 7 of the 10 name no generic oil term ("cooking", "edible", "vegetable" or "frying oil"). They name the oil instead: corn, soybean, olive, palm olein.
- 5 of the 10 have no trans term. They report the fatty acid composition, the isomers or the polar compounds.

**The text behind it.** The Scout is told that the queries "together cover the research question and the eligibility criteria (population, exposure or intervention, outcome)". Narrowing says to "keep every eligibility criterion covered". Both contradict the next instruction, which says to AND only the population, the intervention and, when the eligibility requires it, the design.

## 2. Records without an abstract (srrepro8)

Screeners see a record's title, abstract and metadata. Many deduplicated records have no abstract:

| entry | records | no abstract | of those, returned by |
|---|---|---|---|
| prostate | 963 | 315 | PubMed only 121, OpenAlex only 65, OpenAlex and PubMed 41, all three 86, other 2 |
| pulmonary | 560 | 145 | PubMed only 58, OpenAlex only 48, OpenAlex and PubMed 10, all three 29 |
| cade | 59 | 34 | PubMed only 12, OpenAlex only 15, OpenAlex and PubMed 2, all three 5 |
| fructose | 611 | 105 | OpenAlex and Europe PMC 68, OpenAlex only 37 |
| ozone | 223 | 49 | OpenAlex only 28, all three 10, OpenAlex and PubMed 5, other 6 |
| tfa | 550 | 125 | OpenAlex only 86, all three 21, OpenAlex and Europe PMC 12, other 6 |

The last column counts records by the sources whose search log returned them, not by the identifiers stored on the item.

There are three causes, read from the code:
- **PubMed:** the source builds its items from `esummary`, which carries no abstract, so a PubMed-only record never has one.
- **OpenAlex:** it has no abstract for many publishers' works.
- **The library merge** (`Library.add_item` → `item.merged`) fills in a later hit's identifiers, open-access URLs and language, but never its abstract. A work OpenAlex returned first keeps no abstract even when Europe PMC returned it with one; that accounts for 68 fructose records, plus the Europe PMC rows above.
  - Checked: of the 68 fructose records returned by both OpenAlex and Europe PMC without an abstract, Europe PMC's core result has an abstract for each of the first 20 looked up (by PMID, else DOI).

**Effect on screening.** In srrepro8 cade, all 5 included studies the search found have no abstract. In every case both screeners said unclear, and the P1 vote excluded the record with its conservative option. In srrepro7, with a different hit order, 3 of the same 5 had abstracts and all 3 passed title/abstract. Of the 2 without an abstract, one passed (unclear and yes) and one was excluded the same way (both unclear, the conservative option).

## 3. Title/abstract disagreements go to exclusion

Screener pairs at title/abstract and their finals (P1 vote, conservative option "exclude"):

| run, entry | both unclear | no/unclear | no/yes | included by a vote |
|---|---|---|---|---|
| srrepro7 cade | 21 excluded (7 conservative) | 75 excluded | 2 excluded | 0 |
| srrepro7 tfa (partial) | 21 excluded (3 conservative) | 51 excluded | 10 excluded | 0 |
| srrepro8 cade | 10 excluded (7 conservative) | 9 excluded | — | 0 |
| srrepro8 tfa (partial) | 9 excluded (5 conservative), 29 pending | 6 excluded | 1 excluded | 0 |
| srrepro8 ozone | 3 excluded (conservative) | 4 excluded | 2 included (by the vote) | 2 |

Included studies lost this way:
- srrepro8 cade: 5 of 5 found, all both unclear.
- srrepro8 tfa so far: 7 both unclear excluded and 5 pending.
- srrepro7 cade: 1, both unclear with no abstract.

Single screening already sends an unclear record to the full text. At title/abstract, Cochrane practice excludes only the clearly ineligible, and the full text settles any uncertainty.

## 4. Full-text exclusions of included studies (srrepro8 ozone, srrepro7 tfa)

Every review-included study of these two entries that reached the full text was excluded there. The causes differ:
- **ozone: screener misreadings.** The frozen criteria already admitted these studies: I1 admits ozone, alone or combined with H2O2, compared with H2O2 alone, and I2/E3 admit data from which ΔE can be computed. The exclusions misread them: an E1 exclusion that misread the comparator, and exclusions of studies that report L\*a\*b\* values before and after treatment, from which ΔE can be computed. The protocol did not cause them.
- **tfa: a reporting-form restriction in the eligibility.** I2/E3 admit only total TFA as % of total fatty acids and exclude individual isomers. A study that measures trans fatty acids but reports them in another form is excluded, while the published review included it. How a study reports an outcome is a matter for extraction and synthesis (conversion, or narrative synthesis when it cannot be pooled), not for eligibility.

## Decision

One calibration, four parts (plan `docs/superpowers/plans/2026-11-12-search-screen.md`):
1. **Abstracts:**
   - the library merge fills a missing abstract from a later hit;
   - before title/abstract screening, the kernel fetches the missing abstracts of the deduplicated records: by PMID from PubMed (`efetch`), by DOI from Europe PMC. As implemented, the records looked up are those going to title/abstract screening (a language exclusion is not screened), and a record PubMed has no abstract for, or whose PubMed lookup failed, is tried by DOI too.
2. **Title/abstract screening:**
   - both unclear goes to the full text by rule;
   - a P1 vote on a disagreement has the conservative option "include" (the full text is screened again by both screeners) and asks whether the record may meet the criteria. Full text is unchanged.
3. **Search queries:**
   - the queries find every record that may meet the criteria, and screening applies the criteria;
   - an outcome is ANDed only when it is the subject of the question, and then with its broader names;
   - a class concept lists its common members;
   - a context such as the diet is not ANDed;
   - narrowing keeps the population and the intervention concepts.
4. **Eligibility and the outcome's reporting form** (plan Task 8):
   - eligibility states the study design, the population, the intervention or exposure, the comparator and the outcomes measured (with a time window only when the question needs one); it does not require the statistic, unit or scale a study reports an outcome in, nor a particular aggregate of it; whether a reported form can be pooled is settled by extraction and synthesis;
   - screeners apply each criterion as written (data from which an outcome can be computed meets a criterion that admits it; a listed comparator or intervention is eligible) and do not exclude a study for the statistic, unit or scale it reports an eligible outcome in, unless a criterion explicitly requires that form;
   - the judges may raise a point when a criterion requires a reporting form; there is no deterministic check, since it cannot be detected reliably.
