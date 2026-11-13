# Search and title/abstract screening calibration — implementation plan

**Goal:** act on `docs/eval/search-screen-2026-11-12.md`:
- screeners see an abstract wherever a source has one;
- at title/abstract, uncertain records go to the full text;
- the Scout's queries find what the criteria may admit.

**Branch:** `search-screen` from `main` `75e0495` (worktree `../rk-searchscreen`).

## Global constraints

- TDD on behaviour. No live network in tests; use the existing HTTP fixtures or fakes, and say so when a fixture is added or re-recorded.
- One convention per change; migrate every caller and leave no shim.
- Event-sourced library: every change to an item is an event, and projections replay it.
- Docstrings in prose: "Calibration (search and screening, 2026-11-12): …". Update the module docstrings that describe the changed behaviour: `rk.production.search` (the merge paragraph), `rk.production.screening` (items 6, 7 and the abstract step), `rk.sources.pubmed`, and `rk.library.service` if it has a docstring that names merged fields.
- Do not use the six evaluation topics' included studies as examples in prompts.
- Full-text screening, its vote and its reasons are unchanged.
- Resume (`rk.production.resume`): a final already carried stays as it was made, with no migration of old finals. Every new kernel step must be resumable and idempotent within an attempt.

## Task 1: the library merge fills a missing abstract

- **Files:** `src/rk/library/service.py` (`add_item`, `_item_merged`), its tests.
- **Change:** when the existing item has no abstract (empty or None) and the incoming hit has one, the `item.merged` payload carries `abstract`, and the projection sets it, so the FTS row gets it too. An existing abstract is never replaced.
- **Tests:**
  - an item added without an abstract, then the same work (same DOI) with an abstract → the item has the abstract;
  - the reverse order keeps the first abstract;
  - replaying the events (projection rebuild) gives the same item.

## Task 2: PubMed items carry the abstract when asked

- **Files:** `src/rk/sources/pubmed.py`, its tests.
- **Change:** add `abstracts(http, pmids) -> dict[str, str]`, batched `efetch.fcgi` (`db=pubmed`, `retmode=xml`, `rettype=abstract`, at most 200 PMIDs per request). It returns the joined `AbstractText` sections, each with its `Label` (e.g. "BACKGROUND: …"), and omits PMIDs without an abstract. Search keeps using esummary, which costs nothing more; abstracts are fetched only by Task 3.
- **Tests:** parse a recorded or hand-written efetch XML with a structured and an unstructured abstract and one without; check that batching splits at 200.

## Task 3: the kernel fills missing abstracts before title/abstract screening

- **Files:** `src/rk/production/screening.py` (`_title_abstract`, before the inputs are built), a helper where it fits (e.g. `rk.production.search` or a small `abstracts` function in screening), `src/rk/sources/europepmc.py` (lookup by DOI: `DOI:"…"`, resultType core, batched with OR where Europe PMC allows, otherwise one request per DOI), tests.
- **Change:** once per attempt (a screening note `abstracts_filled` records it, with `sought`, `filled` (by source) and `failed`), for every deduplicated record of the attempt whose item has no abstract:
  1. by PMID through PubMed `efetch` (Task 2);
  2. the rest with a DOI through Europe PMC.
- **How it is stored:** each found abstract is written to the item as an event with its source. Add `abstract` to the fields an `item.merged` can carry (Task 1) and use that; do not widen `UPDATABLE_FIELDS`. The event records the source (e.g. a provenance field).
- **Failures:** a failed lookup goes through the per-item boundary (`rk.discovery.boundary.per_item`), is added to the record's `problem` and is never fatal.
- **Resume:** run again in the attempt, the note is found, so nothing is fetched twice; a resumed attempt still checks items that are still without an abstract.
- **Tests:**
  - a record without an abstract, with a PMID → the screener input carries the fetched abstract;
  - a record with a DOI only → Europe PMC is asked;
  - a lookup failure → the record's problem, and screening goes on;
  - a second run in the same attempt makes no request.

## Task 4: title/abstract — both unclear goes to the full text

- **Files:** `src/rk/production/screening.py` (`dual_rule` or `_finalize`; the phase decides), tests.
- **Change:** at title/abstract, both screeners `unclear` → final `yes`, `decided_by` `agreement`, with the reason "both screeners unclear at title/abstract: the full text decides" followed by the two `_say` lines. Unclear plus a missing decision stays a disagreement. At full text, nothing changes. Keep `dual_rule`'s full-text behaviour exactly; make the phase explicit (a parameter, or the title/abstract case handled in `_finalize`).
- **Tests:**
  - both unclear at title/abstract → final yes `agreement`, and the record reaches full-text retrieval;
  - at full text → a disagreement;
  - unclear plus missing → a disagreement.

## Task 5: title/abstract P1 vote — conservative include, and the question asks "may meet"

- **Files:** `src/rk/production/screening.py` (`CONSERVATIVE` becomes per phase: title/abstract `include`, full text `exclude`; `_vote_on`; `_vote_question`), PRISMA, limitations and disclosure readers of vote finals (`voted_out` and the phase summary), tests.
- **Change:**
  - At title/abstract, `vote(..., conservative="include")`. The final's reason for a conservative include is "screeners disagreed; P1 conservative: to the full text ({res.reason}; not reviewed by a human)".
  - `_vote_question(TITLE_ABSTRACT)`: "The two screeners disagree on this record at title/abstract screening. Include it when it may meet the eligibility criteria (its full text is then screened by both screeners); exclude it only when its title and abstract show it fails a criterion."
  - The full-text question and its conservative option are unchanged.
  - Check that `voted_out`, `_phase_summary`, the PRISMA counts and the disclosure line read the decision, and that none treats `conservative_used` as an exclusion.
  - P2 is unchanged.
- **Tests:**
  - P1 title/abstract judges disagree (or fewer than two admitted) → final include by `vote`, and the record goes on to full text;
  - the same at full text → exclude;
  - PRISMA's excluded-at-title/abstract count does not count a conservative include.

## Task 6: the Scout's queries find what the criteria may admit

- **Files:** `src/rk/roles/scout.md` (the protocol coverage bullet, the build bullet and the narrowing bullet), `src/rk/production/protocol.py` (`STRATEGY_TASK`; the probe's narrowing guidance, the text "above the cap: narrow the search strategy … keep every eligibility criterion covered"; the module docstring's search paragraph), tests that pin the old phrases.
- **Change:**
  - **Coverage:** "together find every record that may meet the eligibility criteria; screening applies the criteria, so a query need not express each of them".
  - **Build rule**, after "AND only the concepts …":
    - "An outcome or measure is a concept to AND only when it is the subject of the question; then its OR group includes its broader names too, since a record reports a measure under its general name ("blood pressure" OR hypertension for a systolic-pressure outcome)."
    - "A concept that names a class (a drug class, a food group, a family of methods) lists the common members a record may name instead of the class (statin OR statins OR atorvastatin OR simvastatin OR rosuvastatin)."
    - "A context such as the diet, the vehicle or the setting is not ANDed unless an eligibility criterion requires it."
  - **Narrowing:** "keep every eligibility criterion covered" becomes "keep the population or condition and the intervention or exposure concepts" everywhere it appears.
  - Leave no remaining "cover the eligibility criteria" or "keep every eligibility criterion covered" (grep `src` and `docs` other than `docs/eval`).
- **Tests:** one behavioural test that the strategy request the Scout receives (the built task and inputs, not the constant) carries the build rule and the new narrowing rule. Update the tests that pin the old phrases to the new contract.

## Task 7: docs

- README's search and screening paragraphs.
- The module docstrings (see the global constraints).
- The evidence doc's "Decision" section, if the implementation differs from it.

## Task 8: eligibility criteria are not the outcome's reporting form

Added by the controller after Tasks 1–6 (evidence: `docs/eval/search-screen-2026-11-12.md` section 4). Runs after Task 7.

- **Files:**
  - `src/rk/production/protocol.py`: `DRAFT_TASK`, the eligibility guidance, and the judges' rules sheet (what the judges check in the eligibility);
  - `src/rk/roles/ideator.md`: the protocol bullets on eligibility;
  - `src/rk/roles/judge.md` if it lists eligibility checks;
  - `src/rk/production/screening.py`: `SCREEN_TASK` and `FULL_TEXT_TASK`.
- **Protocol rule** (same meaning in every place): "Eligibility states the study design, the population, the intervention or exposure, the comparator and the outcomes measured (with a time window only when the question needs one). It does not require the statistic, unit or scale a study reports an outcome in, nor a particular aggregate of it; whether a reported form can be pooled is settled by extraction and synthesis (conversion, or narrative synthesis)."
- **Screeners** (both phases):
  - "Apply each criterion as written: a criterion that admits data from which an outcome can be computed is met by reported values that allow the computation, and a comparator or intervention the criterion lists is eligible."
  - "Do not exclude a study that measures an eligible outcome because of the statistic, unit or scale it reports it in, unless a criterion explicitly requires that form."
- **Judges:** they may raise a point when an eligibility criterion requires a reporting form. No deterministic check (it cannot be detected reliably).
- **Tests:** one behavioural test that the built protocol draft task and the screener task carry the rule (the request an agent receives, not the constant). Update any test that pins the old text.

## After all tasks

Review, then the full suite on the final commit, a path scan, a fast-forward of `main` and a push. Then a fresh home with the six entries again. Measure, per entry:
- the search recall;
- the abstracts filled (by source) and the records still without one;
- the included studies the search found that pass title/abstract;
- the records that reach the full text;
- the full-text recall.
