# A reproduction's seeded stages stay fixed (srrepro7, srrepro8; 2026-11-13)

A project of the reproduction set (`rk.eval.sr_repro`, Plan 5c Deviation 4) is seeded: the published review's question is frozen as the RQ, and intake, landscape, gaps, topics and question are recorded as passed ("discovery did not run"). Four runs went back into those stages and ended there. The facts below come from the controller's queries of the run homes.

## 1. The path

srrepro7 cade, and srrepro8 cade, pulmonary and tfa, all went the same way:

1. **argue.** The P1 RQ vote said the main claim does not answer the RQ, so argue took argue→question.
2. **question.** The FINER `novel` check ran again and found a published review of the question ("already done").
3. **topics.** "No verified gap", so the run went on to gaps.
4. **gaps.** The stage failed: "no representative work has a full text for the limitation miner". The run ended there.

## 2. The reviews named "already done"

| run | review named | the reproduced review? |
|---|---|---|
| srrepro8 tfa | Influence of Heating during Cooking on Trans Fatty Acid Content of Edible Oils: A Systematic Review and Meta-Analysis | yes |
| srrepro8 pulmonary | Diagnostic Accuracy of Machine Learning-Enhanced Methods for Pulmonary Hypertension: A Systematic Review and Meta-Analysis | no. The judges split, and in P1 a split counts as "already done". |
| srrepro7 cade | Real-Time Computer-Aided Detection of Colorectal Neoplasia During Colonoscopy: A Systematic Review and Meta-analysis | no. It is another review of the same question. |

The controller's notes name no review for srrepro8 cade. The plan says pulmonary found the reproduced review too. The manifest says otherwise: the reproduced pulmonary review is "Comparative accuracy of artificial intelligence versus manual interpretation in detecting pulmonary hypertension across chest imaging modalities".

In all four runs, synthesize pooled 0 groups. `rk.stats` had not been validated in those homes (`rk eval stats --write-config`), so pooling was off. These runs say nothing about pooling.

## 3. Why this is a defect

A reproduction's question is fixed by design. Its RQ says novelty is not assessed. So:
- the novelty check can only find the reproduced review or another review of the same question;
- going back to topics and gaps runs discovery that the seed recorded as not run, with no landscape behind them: the seed ran none. Gaps then fails.

None of this measures the reproduction. In tfa, it also put the reproduced review in front of the question stage.

## 4. The change (branch `seeded-fixed`)

- **Seeding.** Seeding records its stages as fixed: one `stages.fixed` event with the stages and the reason, "the reproduction seed (its question is the published review's)". Seeding again records it once. It also records it for a project that was seeded before this change.
- **The engine.** The engine never reopens a fixed stage, in P1 or P2. A back-edge or rerun to one fails the run with "<stage> is fixed by the reproduction seed (its question is the published review's): <the failures>". The fixed stage gets no new attempt and the edge is not counted.
- **argue's P1 RQ vote.** In a project whose question is fixed, `does_not_answer` goes back to synthesize while argue→synthesize has a use left. The reason is "the main claim does not answer the fixed research question (P1 vote, not reviewed by a human): <the judges' reasons>", and it is synthesize's guidance. With no use left, argue fails with "the main claim never answered the fixed research question; argue->synthesize has no use left: <reasons>".
- **Unchanged.** Unseeded projects behave as before. So does P2's final review: the researcher decides, and a back-edge to a fixed question fails the run in the engine.
