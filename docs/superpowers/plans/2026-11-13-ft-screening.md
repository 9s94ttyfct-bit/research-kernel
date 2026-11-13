# Full-text screening: exclude on evidence, show the evidence — implementation plan

**Goal:** act on `docs/eval/ft-screening-2026-11-13.md`. srrepro9 lost 18 of the reviews' included studies at full-text screening (prostate 7 of 10, pulmonary 5 of 12). Causes, of which a study may have several:
- the text shown lacked the information: 9 (8 abstract-only, 1 excerpt cut);
- a screener misread a criterion: 9 (7 decisive);
- the judges split and the conservative option excluded: 6;
- the protocol is narrower than the review: 3.

The screener and the judges read a criterion the text does not mention as failed. At full text, a both-unclear pair and any split vote exclude.

**Branch:** `ft-screening` from `main` `46c9e23` (worktree `../rk-ftscreen`).

## Global constraints
- The title/abstract phase is unchanged, except for the shared screener rule in Task 1, which it already follows in spirit.
- The vote module's rule (every admitted judge agrees, else the conservative option) is unchanged: the two-vendor principle stays. This plan changes what the screeners and judges are told and what text they see.
- No examples from the six evaluation topics in prompts (the existing topic-word test guards the Scout; add the same guard for the screener and judge texts this plan touches).
- TDD on behaviour (the built request a screener or judge receives; the excerpt built from a full text; the retrieval path), not wording. No network in tests.
- Docstrings in prose: "Calibration (full-text screening, 2026-11-13): …". Update the module docstring of `rk.production.screening` for anything it describes.

## Task 1: a "no" rests on text that shows the criterion fails
- **Files:** `src/rk/production/screening.py` (SCREEN_TASK, FULL_TEXT_TASK, the full-text `_vote_question`), `src/rk/roles/screener_a.md`, `screener_b.md`, and `src/rk/roles/judge.md` if it describes screening votes.
- **Screeners (both phases):** "Decide "no" only when the text shows the record fails a criterion; name the criterion and quote or point to the words that show it. When the text does not mention what a criterion needs (an abstract or excerpt that says nothing about it), the decision is "unclear", not "no". An exclusion criterion phrased as an absence ("does not report …") is met only when the text shows the absence (e.g. it reports other outcomes and not this one), not when the text shown is silent."
- **Full-text vote question:** "The two screeners disagree on this record at full-text screening. Include it when the text shown establishes that it meets every criterion. Exclude it when the text shown establishes that it fails one, naming the criterion. When the text shown leaves a criterion unsettled, choose exclude and say which criterion it does not settle." The conservative option stays "exclude".
- **The exclusion's reason tells the two cases apart.**
  - The vote's answer gets an optional `unsettled: bool` (the opinion keeps it). A full-text vote exclusion whose judges mark it unsettled, or a both-unclear pair excluded by the vote, gets the final reason "screeners disagreed; eligibility not established from the text shown (<text kind: abstract only / sections / whole text>); not reviewed by a human".
  - It is an exclusion like any other: no new PRISMA state, no "awaiting classification" wording (reserved for a real non-final state later).
  - The phase summary and the PRISMA notes count these apart ("of which N because eligibility was not established from the text shown"), and the limitations input lists the count.
- **Tests:**
  - the built full-text screener request and the built full-text vote request carry the rule (assert the constants appear in the requests); a title/abstract request carries the screener rule too;
  - a both-unclear abstract-only record at full text whose judges answer unsettled gets that reason and is counted apart in the phase summary;
  - a vote exclusion on a failed criterion keeps the existing reason.

## Task 2: the excerpt keeps the results
- **Files:** `src/rk/production/screening.py` (`fulltext_excerpt`, `FULLTEXT_CHARS`), tests.
- **Change:**
  - A structured abstract's own sub-headings (Background, Methods, Results, Conclusion(s) inside the abstract) do not count as the body's sections. The body's Methods and Results are found after the abstract ends; the abstract keeps its own share.
  - Raise `FULLTEXT_CHARS` to 12000 and give results the largest share (e.g. abstract 2000, methods 3500, results 6500, with any share a shorter section leaves going to the others, as now).
  - The cost is more input per full-text screening task; record it in the docstring.
- **Tests:**
  - a full text with a structured abstract (its own Methods/Results lines), then a body Methods and Results: the excerpt contains the body Results text, not only the abstract's;
  - a long Results section gets the larger share.

## Task 3: records with a PMCID get their full text
- **Files:** `src/rk/discovery/sources.py` (`fulltext`) or wherever `ctx.sources.fulltext` resolves a PMCID; `src/rk/sources/europepmc.py` / `pubmed.py` (`bioc_json`); tests.
- **Evidence:** 36 of 782 abstract-only records across the srrepro9 projects have a PMCID, among them two review-included prostate studies (PMC11366675, PMC10006083).
- **Do:** find why a record with a PMCID ends abstract-only (no PMCID on the library item at retrieval time? an author manuscript not in the open-access subset? an error recorded in the record's `problem`?), from the code and from the srrepro9 records' `problem` fields (read only: open the run home's project DBs for SELECTs; the controller gives the home's location, and an `rk serve` is live on it). Fix the cause in the retrieval path, for example: try Europe PMC's full-text XML and PMC's BioC by PMCID before giving up, and look the PMCID up from the PMID when the item lacks one. Report the cause found.
- **Tests:** a record whose item has a PMCID (and one with only a PMID that maps to a PMCID) gets its full text through the mocked sources.

## Task 4: docs
README's screening paragraph, the module docstrings, and a "Decision" section at the end of `docs/eval/ft-screening-2026-11-13.md`:
- Tasks 1-3 as done;
- not done and why: the vote majority rule (the two-vendor unanimity is a design principle), widening the protocol (it changes the reproduced question), and a real "awaiting classification" state for abstract-only records (a non-final PRISMA state the kernel does not have yet; until then these are exclusions whose reason says eligibility was not established from the text shown, counted apart).

After all tasks: review, then the full suite, a path scan, a merge and a push. Then a new home (`new_home.sh srrepro10`) and a measurement of the full-text recall per entry.
