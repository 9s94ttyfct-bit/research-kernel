# Plan 5a — Evidence Pipeline (protocol, search & screening, reading & extraction) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a frozen RQ, the orchestrator runs `protocol → search_screen → read_extract` for every paper type (§7.3). The output is a frozen protocol, a deduplicated and screened record set with PRISMA counts that add up, and verified notes and extraction values for every included study. P2 asks you at `protocol_approval` and on screening disagreements; P1 decides by a two-vendor vote recorded as "not reviewed by a human".

**Architecture:**
- The engine is generalized from the five discovery stages to the stage graph of the project's paper type (`stages_for`). Production stages are always run by the orchestrator through `rk run`, in D1 and D2 projects alike (§8.1: from `protocol` on, the Lead only reads).
- `rk.production` holds the new artifacts and workers:
  - `store`: events, projections and queries for protocols, records, screening decisions, PRISMA counts and extractions;
  - `protocol`: drafting, structural gate and freeze;
  - `search`: running the frozen strategy and deduplicating;
  - `screening`: single and dual screening, disagreements, PRISMA;
  - `extraction`: full text, notes and extraction values per included study.
- `rk.production.vote` is the P1 two-vendor vote used instead of a checkpoint for the AI weak spots (§5.3).
- `rk.verify.prisma` is V-PRISMA (arithmetic).
- Every stage keeps the Plan 4 attempt semantics. Artifacts carry their stage attempt; gates and the UI read the latest attempt that ran.

**Tech Stack:** Plan 1–4 stack. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` §5.3 (checkpoints, P1/P2, votes), §6.3 (`protocol`, `screening`, `extraction`), §7.1 (rows protocol, search_screen, read_extract), §7.2 (budgets: enumerated stages, batch of 25, ≤ 3,000 records), §7.3 (per paper type), §8.1 (Screener-A/B, Reader), §9.1 (V-QUOTE, V-PARA, V-RETRACT, V-PRISMA), §9.2 (G-PREREG), §11 (items 1, 3, 4, 6, 8).

**Deviations (decided here, flagged to the user):**
1. **P1 votes are not checkpoints.** In P1 a weak spot (protocol approval, screening disagreement) is decided by `vote()`: two judges of two vendors, both different from the generator's vendor. Agreement passes; disagreement takes the conservative option (§5.3). The vote is stored as an event (`vote.decided`) and listed under "not reviewed by a human". The checkpoint kinds stay human-only (`HARD_HUMAN_KINDS` unchanged).
2. **The conservative option for a screening disagreement is "exclude, with the reason 'screeners disagreed; P1 conservative'"** (§5.3 "不纳入"). Excluded-by-disagreement records are listed separately in PRISMA and in the limitations input, so the recall loss is visible.
3. **Automated Triage exclusion is not built.** It is off by default (§7.1). It can only be turned on after validation on a reproduction set, which is calibration work; the setting refuses `true` with a clear message until then.
4. **The protocol is drafted by the Ideator** (methods design, §8.1). The Scout drafts the search strings, and the kernel checks the structure.

## Global Constraints

- All Plan 1–4 constraints. Git identity repo-local `9s94ttyfct-bit`; no personal paths or hostnames in commits or fixtures.
- **Tests:** no network and no models (conftest guard). Role tasks go through `TaskExecutor` (`ScriptedExecutor` in tests); sources through `DiscoverySources` (`FakeSources`). Live runs (the Task 9 demo) use Claude Haiku 5.5 for Anthropic-vendor roles; judges are other vendors.
- **Stage graph:** `stages_for(paper_type)` (§7.3). This plan makes `protocol`, `search_screen` and `read_extract` runnable. Later stages stay "not yet available" (Plan 5b/5c).
- **Back-edges added** (§7.1):
  - `protocol → question`;
  - `search_screen → protocol` (a deviation, recorded in the protocol's `deviations`);
  - `read_extract → search_screen`.

  Each is allowed 2 uses per project; at the third, P2 → `stage_blocked`, P1 → `degrade` (same engine path as Plan 4).
- **Budgets** (§7.2):
  - `protocol` is an open stage: 40 role tasks per session.
  - `search_screen` and `read_extract` are enumerated stages: no fixed task cap, only the per-driver hourly caps (`waiting_rate`).
  - Screening batch size is 25 records per role task.
  - Record cap: 3,000 after deduplication. Above it, the protocol gate fails with "narrow the search strategy (N records > 3,000)" in both P1 and P2. In P2 you may raise the cap for this project (setting `record_cap`).
- **Paper-type rules** (§7.3):
  - `systematic_review`: full protocol with the PRISMA-P required items; freeze counts as preregistration (G-PREREG); dual title/abstract screening and dual full-text screening; PRISMA counts.
  - `scoping_review`: protocol frozen and counted as preregistration; single screening plus a random 10% dual sample (seeded by the protocol hash). If the disagreement rate on included-or-not in the sample is > 10%, the stage switches to full dual screening. PRISMA-ScR counts.
  - `critical_review` and `research_proposal`: light protocol (question, search strategy, selection principles). The freeze is for traceability only, not preregistration. Single screening with a recorded selection reason; no PRISMA counts.
- **Checkpoints:**
  - `protocol_approval` (P2, all types) is human-only. P1 uses `vote()`.
  - `screening_disagreements` (P2, dual-screened types) is one checkpoint per batch of up to 25 disagreements, human-only. P1 uses `vote()` per record, with exclusion as the conservative option.
- **Freeze:** `protocol.frozen` payload `frozen_hash = sha256(canonical_json(protocol body))`. A frozen protocol is never edited. A change after freezing is a new version that records a deviation `{from_version, to_version, reason, stage, ts}`. G-PREREG = the first version was frozen before any `search_screen` attempt, and every later version has a deviation.
- **Non-English records** (§11.8) are excluded at screening with the reason "language".
- **Full text** (§11.1): API channels first, then the built-in browser when it is running. With no full text, the record is assessed on its abstract with `sufficient=low` noted on every extraction value from it.
- **Extraction:** every value carries a `note_id` of a verified quote note (V-QUOTE). Paraphrase notes must pass V-PARA. Every included study has a V-RETRACT result; retracted or withdrawn studies are flagged and excluded from evidence (kept as discussed objects).
- **Per-item boundary:** network or parse failures on one record are recorded on that record (`problem`) and the stage continues (Plan 4 `per_item`, narrowed to source, IO and parse errors).

## Review Focus

1. **A protocol is changed after search_screen started.** The change is a new version with a deviation, the earlier frozen version stays readable, and G-PREREG still passes because the deviation is recorded. Test: Task 3 `test_change_after_screening_is_a_recorded_deviation`.
2. **The two screeners disagree on 30 records in P2.** You get two `screening_disagreements` checkpoints (25 + 5), your answers settle exactly those records, and PRISMA counts match. Test: Task 5 `test_disagreements_batch_into_checkpoints_of_25`.
3. **One judge vendor is missing in P1** (as in the Plan 4 demo). The vote cannot pass, every vote takes the conservative option, and the run says why at start. Test: Task 2 `test_vote_with_one_vendor_is_conservative`.
4. **A study has no full text.** It is assessed on the abstract, its extraction values carry `sufficient=low`, and it is counted as "not retrieved" only when no abstract exists either. Test: Task 7 `test_abstract_only_study_is_marked_low_sufficiency`.
5. **The search returns 4,000 records after deduplication.** The protocol gate fails, telling you to narrow the strategy. In P2 you can raise `record_cap` and continue. Test: Task 4 `test_record_cap_fails_the_gate`.

---

## File Structure

```
src/rk/orchestrator/graph.py          production stages, back-edges, OPEN/ENUMERATED stages
src/rk/orchestrator/engine.py         stage sequence from stages_for(paper_type); runs continue past question
src/rk/orchestrator/state.py          current_stage / until over the full graph
src/rk/production/__init__.py
src/rk/production/models.py           Protocol, SearchStrategy, Eligibility, ExtractionField, Record, Decision, ...
src/rk/production/store.py            events, projections, queries
src/rk/production/vote.py             vote()
src/rk/production/protocol.py         ProtocolWorker
src/rk/production/search.py           run_strategy(), dedupe
src/rk/production/screening.py        SearchScreenWorker (search + screening + PRISMA)
src/rk/production/extraction.py       ReadExtractWorker
src/rk/production/tools.py            protocol / prisma / screening / extraction tools
src/rk/verify/prisma.py               V-PRISMA
src/rk/roles/{ideator,scout,screener_a,screener_b,reader,judge,judge_2}.md
src/rk/cli/main.py                    rk protocol show, rk prisma, rk screening list, rk extraction show
src/rk/ui/api.py, ui/static/*         protocol card, PRISMA flow, disagreements card, extraction table
tests/production_fakes.py             builders for frozen-RQ projects
tests/test_engine_production.py, test_vote.py, test_protocol.py, test_search.py, test_screening.py,
tests/test_extraction.py, test_prisma.py, test_ui_production.py
docs/demo/plan5a-evidence.md, README.md
```

New tables (`CREATE TABLE IF NOT EXISTS`, all in `PROJECTION_TABLES`):
- `protocols(id, version, attempt, body JSON, frozen_hash, frozen_at, status)`
- `protocol_deviations(id, from_version, to_version, reason, stage, ts)`
- `records(id, attempt, item_id, dedup_of, language, retrieved, abstract_only, problem)`
- `screening(id, attempt, record_id, phase ∈ {title_abstract, full_text}, screener, include ∈ {yes, no, unclear}, reason, criterion)`
- `screening_final(record_id, attempt, phase, include, decided_by ∈ {agreement, single, human, vote}, reason)`
- `extractions(id, attempt, item_id, field, value JSON, note_id, sufficient, problem)`
- `votes(id, subject_type, subject_id, decision, conservative, opinions JSON, ts)`

---

### Task 1: Engine over the full stage graph

**Files:** Modify `src/rk/orchestrator/{graph,engine,state,runs}.py`, `src/rk/discovery/tools.py` (Literal of stages), `src/rk/ui/api.py` (stage graph statuses). Create `tests/test_engine_production.py`.

**Interfaces:**
- `graph.py`:
  - `PRODUCTION = ("protocol", "search_screen", "read_extract", "synthesize", "argue", "design", "draft", "revise", "audit", "export")`.
  - `RUNNABLE: frozenset[str]`: the discovery stages plus `protocol`, `search_screen`, `read_extract` (grows in 5b/5c).
  - `BACK_EDGES` adds `("protocol","question")`, `("search_screen","protocol")`, `("read_extract","search_screen")`.
  - `OPEN_STAGES` adds `"protocol"`; `ENUMERATED_STAGES = {"search_screen","read_extract"}` (no task cap).
  - `stages_of(ps) -> list[str]` returns `stages_for(intake.paper_type)`.
- `state.current_stage(ps)` walks `stages_of(ps)`.
- `start_run(..., until)` accepts any stage in `RUNNABLE ∩ stages_of(ps)` after the current stage.
- `RunManager.start`:
  - before `question` has passed: D2 projects only (as now);
  - after `question` has passed: allowed for D1 and D2;
  - `until` beyond `question` requires a frozen RQ.
- The engine's index arithmetic, reopen and `until` logic use `stages_of(ps)` instead of `DISCOVERY`. A stage not in `RUNNABLE` is `stage_unavailable` ("arrives in Plan 5b/5c").
- The UI stage graph marks `RUNNABLE` stages as available, others as "not yet available".

- [ ] **Step 1: Failing tests:**
  - `test_run_continues_past_question_until_read_extract` (fake workers for the three new stages);
  - `test_back_edge_search_screen_to_protocol_reopens_downstream`;
  - `test_d1_project_can_start_a_production_run_after_question`;
  - `test_unrunnable_stage_is_unavailable`;
  - `test_enumerated_stage_has_no_task_cap` (41 tasks in `search_screen` pass; the hourly cap still applies).
- [ ] **Step 2–4:** FAIL, implement, PASS (the full suite: every Plan 4 test still passes).
- [ ] **Step 5: Commit** `feat(orchestrator): runs over the full stage graph of the paper type`

### Task 2: Production store and the P1 vote

**Files:** Create `src/rk/production/{__init__,models,store,vote}.py`, `tests/test_vote.py`, `tests/production_fakes.py`. Modify `src/rk/store/{schema.sql,projections.py}`, `src/rk/ids.py` (prefixes `prt`, `rec`, `scr`, `ext`, `vot`).

**Interfaces:**
- Models:
  - `Protocol(question: str, search_strategy: SearchStrategy, eligibility: Eligibility, extraction_schema: list[ExtractionField], synthesis_plan: SynthesisPlan | None, prisma_p: dict[str, str] | None)`;
  - `SearchStrategy(sources: list[str], queries: list[str], date_from: int | None, date_to: int | None, languages: list[str] = ["en"])`;
  - `Eligibility(include: list[Criterion], exclude: list[Criterion])` with `Criterion(id, text)`;
  - `ExtractionField(name, description, type ∈ {text, number, category}, categories: list[str] | None, unit: str | None)`;
  - `SynthesisPlan(method ∈ {narrative_swim, meta_analysis, evidence_map, thematic}, effect_measure, model, heterogeneity, sensitivity)`.
- Store functions (each writes one event):
  - `add_protocol(ps, attempt, body, *, version, actor) -> id`
  - `freeze_protocol(ps, id, *, actor) -> hash`
  - `add_deviation(ps, from_v, to_v, reason, stage, *, actor)`
  - `add_record(ps, attempt, item_id, *, dedup_of=None, language, actor) -> id`
  - `set_record(ps, record_id, **fields)` (retrieved, abstract_only, problem)
  - `add_decision(ps, attempt, record_id, phase, screener, include, reason, criterion, *, actor)`
  - `set_final(ps, record_id, attempt, phase, include, decided_by, reason, *, actor)`
  - `add_extraction(ps, attempt, item_id, field, value, note_id, sufficient, *, actor)`
  - queries for each, by attempt
- `vote(ctx, subject_type, subject_id, question: str, options: list[str], conservative: str, inputs: dict) -> VoteResult(decision, conservative_used: bool, opinions)`:
  - `judge` and `judge_2` each return `VoteOut(choice, reason)` through `ctx.executor` (counted);
  - admitted by the shared `judge_vendor_error` against `generator_vendor` (a keyword: the vendor of the generated subject, or None when the subject is not generated text such as a screened record), and the two judges must be distinct known vendors;
  - agreement → that choice; otherwise → `conservative`;
  - records `vote.decided`.

- [ ] **Step 1: Failing tests:**
  - `test_vote_agreement_passes`;
  - `test_vote_disagreement_is_conservative`;
  - `test_vote_with_one_vendor_is_conservative` (Review Focus 3);
  - `test_vote_is_recorded_as_not_human_reviewed`;
  - `test_production_tables_rebuild`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): evidence-pipeline store and the P1 two-vendor vote`

### Task 3: Protocol stage

**Files:** Create `src/rk/production/protocol.py`, `tests/test_protocol.py`. Modify `src/rk/roles/{ideator,scout}.md`, `src/rk/orchestrator/engine.py` (default worker).

**Interfaces:**
- `ProtocolWorker.run`:
  - The `ideator` returns `ProtocolDraftOut` (eligibility, extraction schema, synthesis plan, PRISMA-P fields for SR) from the frozen RQ, its hypotheses, the topic, the intake and guidance.
  - The `scout` returns `SearchStrategyOut` (≥ 3 queries, ≥ 2 sources from `sources.sources()`, date range).
  - `check_protocol(body, paper_type) -> list[str]`:
    - eligibility has ≥ 1 include criterion;
    - every extraction field is named and typed;
    - SR also needs the PRISMA-P required items: rationale, objectives, eligibility, information sources, search strategy, selection process, data collection process, data items, risk of bias, synthesis;
    - light types need question, search and selection principles only.
  - Then the record-cap probe: `sources.count(query)` per query (an OpenAlex count, Europe PMC `hitCount`), summed. Above `record_cap` the gate fails with the size.
  - P2 → `protocol_approval` (the prompt renders every section).
  - P1 → `vote(... conservative="revise", generator_vendor=<ideator task vendor>)`: approve → freeze; revise → rerun with the judges' reasons as guidance.
- `on_answer`:
  - `{"decision":"approve"}` → freeze;
  - `{"decision":"revise","comment"}` → rerun;
  - `{"decision":"back"}` → `back_edge(to="question")`.
- Gate: a frozen protocol of the latest attempt.
- Deviation:
  - when `protocol` reruns after `search_screen` had an attempt (a back-edge or a later change), the new freeze writes `add_deviation` from the last frozen version;
  - G-PREREG input: `protocol_prereg_ok(ps) -> bool`.
- `sources.count(query, source) -> int` is added to `DiscoverySources` (KernelSources: OpenAlex `meta.count`, Europe PMC `hitCount`; FakeSources: configurable).

- [ ] **Step 1: Failing tests:**
  - `test_sr_protocol_needs_prisma_p_items`;
  - `test_light_protocol_for_critical_review`;
  - `test_p2_opens_protocol_approval_p1_votes`;
  - `test_change_after_screening_is_a_recorded_deviation` (Review Focus 1);
  - `test_freeze_hash_is_stable`;
  - `test_record_cap_probe_fails_the_gate` (count 4,000 > 3,000).
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): protocol drafting, gate, approval and freeze`

### Task 4: Search and deduplication

**Files:** Create `src/rk/production/search.py`, `tests/test_search.py`.

**Interfaces:**
- `run_strategy(ctx, protocol) -> list[str]` (record ids):
  - every query × source through `log_and_search` (purpose `protocol_search`, limit 200, with paging up to the cap) with the date range;
  - items added to the library collection "Search hits";
  - one `record` per library item.
- Dedup:
  - the library already merges by identifier and title+year;
  - a record whose item merged into another gets `dedup_of`;
  - counts are identified, duplicates removed, screened.
- Language: `item.language` or the detected abstract language (`langdetect` is not a dependency; use the OpenAlex/Europe PMC language field, else "unknown" treated as English with a note).
- Over `record_cap` after deduplication (the probe can undercount): the stage stops with `back_edge(to="protocol")`, reason "N records > cap".

- [ ] **Step 1: Failing tests:**
  - `test_strategy_runs_every_query_on_every_source_and_logs`;
  - `test_duplicates_are_counted_not_screened`;
  - `test_record_cap_fails_the_gate` (Review Focus 5; P2 with a raised `record_cap` passes);
  - `test_non_english_marked_for_exclusion`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): run the frozen search strategy with deduplication`

### Task 5: Title/abstract screening

**Files:** Create `src/rk/production/screening.py` (title/abstract part), `tests/test_screening.py`. Modify `src/rk/roles/{screener_a,screener_b}.md`.

**Interfaces:**
- `SearchScreenWorker.run`:
  1. `run_strategy`.
  2. Batches of 25 records (title, abstract, year, venue, language) to `screener_a` and, where dual applies, `screener_b`. Each returns `ScreenOut(decisions: [{record_id, include ∈ {yes,no,unclear}, criterion_id, reason}])`. A decision for a record not in the batch is ignored and recorded.
  3. Final per record:
     - both yes, or one yes + one unclear → include;
     - both no → exclude;
     - otherwise → disagreement;
     - single screening → that decision, where unclear → include to full text.
  4. Non-English → exclude "language" without screening.
  5. Disagreements:
     - P2 → `screening_disagreements` checkpoints of ≤ 25 records, answered `{"decisions": {record_id: "include"|"exclude"}}`;
     - P1 → `vote()` per record with conservative "exclude" and `generator_vendor=None` (the subject is a record, not generated text; the judges need only be two distinct known vendors).
  6. Scoping review: single screening plus a 10% dual sample (seeded by the protocol hash); disagreement > 10% → dual for all remaining records.
- Screeners must be different vendors for dual screening (`screener_a` anthropic, `screener_b` openai by default). With the same vendor, the run fails at start with a clear reason.

- [ ] **Step 1: Failing tests:**
  - `test_dual_screening_agreement_rules`;
  - `test_disagreements_batch_into_checkpoints_of_25` (Review Focus 2);
  - `test_p1_disagreement_votes_conservative_exclude`;
  - `test_scoping_sample_switches_to_dual_above_10_percent`;
  - `test_single_screening_unclear_goes_to_full_text`;
  - `test_same_vendor_screeners_refused`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): title/abstract screening with dual review and disagreements`

### Task 6: Full-text retrieval, full-text screening and PRISMA

**Files:** Modify `src/rk/production/screening.py`. Create `src/rk/verify/prisma.py`, `tests/test_prisma.py`.

**Interfaces:**
- For title/abstract includes:
  - `sources.fulltext(item)` (per-item boundary);
  - `retrieved` true/false; no full text but an abstract → `abstract_only=true`.
- Full-text screening (same screeners and rules, phase `full_text`). The inputs are the full text's abstract, methods and results sections, cut to ~6,000 characters (sections from the text layer; whole text when no sections).
- `prisma_counts(ps, attempt) -> Prisma(identified, duplicates_removed, screened, excluded_ta, sought, not_retrieved, assessed_ft, excluded_ft: dict[reason, n], excluded_disagreement, included)`.
- `check_prisma(p) -> list[str]` (V-PRISMA):
  - identified − duplicates = screened;
  - screened − excluded_ta = sought;
  - sought − not_retrieved = assessed;
  - assessed − Σ excluded_ft = included;
  - every exclusion has a reason.
- Gate:
  - every record has a final decision for each phase it reached;
  - V-PRISMA passes (SR and scoping) or selection reasons are complete (critical and proposal);
  - ≥ 1 included study.

  A gate failure with 0 included → `back_edge(to="protocol")` ("no study met the criteria").

- [ ] **Step 1: Failing tests:**
  - `test_prisma_arithmetic_holds_and_fails_when_broken`;
  - `test_not_retrieved_and_abstract_only_are_counted`;
  - `test_full_text_screening_rules_match_title_abstract`;
  - `test_zero_included_backs_to_protocol`;
  - `test_critical_review_has_reasons_not_prisma`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): full-text screening and V-PRISMA`

### Task 7: Reading and extraction

**Files:** Create `src/rk/production/extraction.py`, `tests/test_extraction.py`. Modify `src/rk/roles/reader.md`.

**Interfaces:**
- `ReadExtractWorker.run`, per included study (per-item boundary):
  1. Retraction: `sources.retraction(item)` (or the recorded V-RETRACT). Retracted or withdrawn → flagged, no extraction, listed for the limitations.
  2. The `reader` (with `rk_text_find`, `rk_text_read`, `rk_note_add`) returns `ExtractOut(values: [{field, value, quote, locator?}], notes: [...])`. The kernel adds every quote with `add_note(kind="quote")` (V-QUOTE) and links value → note. A value whose quote fails is stored with `problem="quote not verbatim"` and no note.
  3. Abstract-only studies: values are allowed from the abstract text, with `sufficient="low"`.
- Gate:
  - every extraction field of every non-flagged included study has a value with a verified note, or an explicit `not_reported` with a quote showing the section searched;
  - retraction checked for all;
  - paraphrase notes pass V-PARA.
- The per-field gate failure lists study, field and reason. P1 degrade → those values are marked `missing` and listed in the limitations input.

- [ ] **Step 1: Failing tests:**
  - `test_every_value_links_a_verified_quote_note`;
  - `test_unverbatim_quote_is_a_problem_not_a_value`;
  - `test_abstract_only_study_is_marked_low_sufficiency` (Review Focus 4);
  - `test_retracted_study_is_flagged_not_extracted`;
  - `test_gate_lists_missing_fields`;
  - `test_p1_degrade_marks_missing`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(production): reading and extraction with verified locators`

### Task 8: Tools, CLI and Workbench

**Files:** Create `src/rk/production/tools.py`, `tests/test_ui_production.py`. Modify `src/rk/roles/policy.py`, `src/rk/cli/main.py`, `src/rk/ui/{api.py,static/app.js,static/style.css}`.

**Interfaces:**
- Read tools (lead, cli, and the production roles): `rk_protocol_get`, `rk_prisma_get`, `rk_screening_list(phase?, final?)`, `rk_extraction_get(item_id?)`.
- CLI: `rk protocol show`, `rk prisma`, `rk screening list`, `rk extraction show`.
- Workbench:
  - Progress shows the PRISMA flow (counts with reasons).
  - Checkpoint cards:
    - `protocol_approval` renders the protocol sections and offers Approve, Revise and Back to question;
    - `screening_disagreements` shows a table of records with each screener's decision and reason, and a per-row include/exclude choice; Submit is enabled only when every row is decided.
  - Library item view shows the extraction values with links to their notes.
- All untrusted text is rendered as text.
- Answer shapes are exactly what the workers accept.
- Real-surface screenshots are taken and checked.

- [ ] **Step 1: Failing tests:**
  - `test_protocol_card_answers_reach_the_worker`;
  - `test_disagreement_card_requires_every_row`;
  - `test_prisma_route_matches_v_prisma`;
  - `test_read_tools_policy`.
- [ ] **Step 2–5:** FAIL, implement, PASS. **Commit** `feat(ui): protocol, screening and extraction in the Workbench`

### Task 9: Demo and docs

**Files:** Create `docs/demo/plan5a-evidence.md`. Modify `README.md`.

- [ ] **Step 1: Live run.**
  - Continue the Plan 4 demo project (perovskite, critical review, collab) from its frozen RQ, or a fresh small project if that scratch home is gone.
  - Run `uv run rk run <slug> --until read_extract` with Haiku for the Anthropic roles. Judges and Screener-B are other vendors; use the local Gemma if Ollama Cloud is still out of quota.
  - Use a `record_cap` small enough to keep the demo affordable (e.g. 150), stated in the doc.
  - Answer the checkpoints as the stand-in researcher, and say so.
  - Record:
    - the protocol and its hash;
    - search counts and duplicates;
    - screening agreement and disagreements;
    - PRISMA counts (or the selection reasons);
    - full-text retrieval channels;
    - extraction coverage and problems;
    - task counts and wall time;
    - what failed or was weak.
- [ ] **Step 2:** README section "Evidence pipeline (protocol, screening, extraction)".
- [ ] **Step 3:** Full suite (pipefail), ruff. **Commit** `docs: Plan 5a demo and README`, push.
