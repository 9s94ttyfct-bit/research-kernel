# Plan 5c — Draft, Revise, Audit, Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After `argue` (and `design` for proposals), the orchestrator runs `draft → revise → audit → export`. The result is an English manuscript in which every sentence carries a source marker that the kernel has verified. The manuscript is exported through pandoc + citeproc + tectonic, together with `disclosure.md`, a verification report and a reproducible package. Plan 5 is complete when a systematic review, a critical review and a research proposal each reach `export` in P2, one project does so in P1, and the systematic-review reproduction set (§10) has been run.

**Architecture:**
- `rk.production.manuscript` parses a section's markdown into sentences with their `[[kind:id]]` markers (the grammar `rk.ui.drafts.MARKER` already renders).
- `rk.verify.cover` checks each sentence against §9.1:
  - V-COVER (at least one marker; targets exist and their hashes match);
  - V-NUM (every number is covered by a marker target);
  - V-HEDGE (no stronger than the claims it cites);
  - V-QUOTE (quoted text is a verified note);
  - G-SUPPORT (every `[[clm:]]` is supported);
  - the `[[ck]]` and `[[inf]]` rules;
  - V-BALANCE (`rk.verify.balance`) runs per section.
- Four stage workers follow the Plan 5b pattern: load, role tasks through the executor, kernel stores and verifies, gate on stored data, P2 checkpoints / P1 votes, resume in place.
  - `draft`: the Writer drafts each required section.
  - `revise`: the SimReader questions, the Writer revises; at most K rounds.
  - `audit`: reruns every hard gate over the final manuscript, writes `disclosure.md` and the verification report, and opens `final_review`.
  - `export`: kernel only, no model.

**Tech Stack:** Plan 1–5b stack, plus these installed binaries:
- `pandoc` 3.13, with citeproc built in;
- `tectonic` 0.17.0, used as `--pdf-engine=tectonic`.

No new Python dependency. The CSL style is pandoc's built-in default (Chicago author-date), so no network is needed.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md`:
- §5.3 (P1/P2, `final_review`, the delete-or-narrow rule);
- §7.1 rows `draft`, `revise`, `audit`, `export`;
- §7.2 (revise is an open stage);
- §7.3 (required sections per paper type);
- §8.1 (Writer, SimReader);
- §9.1 (V-COVER, V-NUM, V-HEDGE, V-QUOTE, V-BALANCE, V-REF, V-RETRACT, V-PRISMA, V-STATS);
- §9.2 (all hard gates);
- §10 (the systematic-review reproduction set);
- §12 (Plan 5 acceptance).

**Deviations (decided here, flagged to the user):**
1. **No write tools for the Writer.** The Writer returns `SectionDraft{markdown}` as structured output, and the kernel stores each version (`draft.written`). This is the same rule as the Ideator after the Plan 5b amendment: generators never write store state directly. §8.1 lists `draft_write`; the kernel performs it.
2. **K = 3 revise rounds** by default (`revise_max_rounds` in `config.toml`). The spec leaves K open.
3. **P1 `final_review` is a vote.** The options are `approve` and `not_approved` (conservative). A not-approved vote takes `audit → revise` with the judges' reasons, under the edge limit. At the limit, P1's degrade fails the run with those reasons: an unapproved manuscript is never exported. P2's `final_review` is hard-human.
4. **The reproduction set starts at a seeded frozen RQ.** Each reproduced review's question becomes a frozen RQ fixture, so discovery is skipped (the question is given). Protocol onward runs as for any project. The paper type is `systematic_review`.

## Global Constraints

- **Inherited:**
  - all Plan 1–5b constraints;
  - repo-local git identity `9s94ttyfct-bit`;
  - no personal paths, hostnames or TMPDIR values in commits or fixtures;
  - tests run without network or models (conftest guard).
- **Stage graph:** `draft`, `revise`, `audit` and `export` become runnable through `default_workers()`.
- **New back-edges, each limited to 2 uses** (P2 → `stage_blocked`, P1 → `degrade`, as today):
  - `draft → argue`;
  - `draft → design` (`research_proposal` only);
  - `revise → draft`;
  - `audit → draft`;
  - `audit → argue`;
  - `audit → read_extract`;
  - `audit → revise`.
- **Budgets (§7.2):**
  - `revise` joins `OPEN_STAGES` (40 role tasks per session).
  - `draft` is bounded by the number of sections × `draft_section_retries` (default 2 Writer retries per section inside one attempt).
  - `audit` and `export` make no role tasks, except `audit`'s P1 vote.
- **Marker grammar (§9.1 V-COVER), exactly:**
  - `[[clm:clm_…]]`
  - `[[run:evt_…]]` | `[[run:prt_…]]`
  - `[[fig:fig_…]]` | `[[stat:stt_…]]`
  - `[[plan:dsg_…]]` | `[[plan:hyp_…]]`
  - `[[inf]]`
  - `[[ck]]`

  A sentence may carry several markers.
- **Sentence rules:**
  - each sentence has ≥1 marker;
  - every `clm`/`run`/`fig`/`stat`/`plan` target exists and, where it has a hash, the hash matches;
  - every number is covered by some marker target in the sentence (V-NUM);
  - `[[ck]]` sentences: ≤5% of a section's sentences, and no numbers;
  - `[[plan:]]` only in `research_proposal`;
  - `[[inf]]` sentences have hedge level ≤1 (inferential wording, §9.1 "[[inf]] 转为推断性措辞");
  - a sentence's hedge level is ≤ the highest level among its `clm` targets' texts;
  - text in quotation marks (“…”, "…", „…“, «…», ‘…’, '…'; not apostrophes) and a block quote's text, in a sentence with a `clm` or a `run:prt_` marker, must be a verified quote note of a cited claim's evidence or a text of the cited protocol (e.g. a search string) (V-QUOTE); V-HEDGE leaves the quoted words it checks out;
  - every `clm` target is supported (G-SUPPORT: it passes the claim gate, `rk.verify.support.claim_problems`), never by vote.
- **Required sections (§7.3), with exact section ids as file stems:**
  - `systematic_review`: `abstract`, `introduction`, `methods`, `results`, `discussion`, `limitations`, `conclusion`, `disclosure`.
  - `scoping_review`: same as systematic_review, with `results` titled "Evidence map and thematic synthesis".
  - `critical_review`: `introduction`, then one `theme-<n>` section per synthesis theme, `gaps-agenda`, `conclusion`, `disclosure`.
  - `research_proposal`: `background`, `gap`, `questions-hypotheses`, `methods`, `expected-outcomes`, `risks`, `disclosure`.
- **The `disclosure` section is kernel-written** from `disclosure.md` (§9.2 G-DISCLOSE). Its sentences carry `[[run:…]]` markers to the events they report.
- **Export (§9.1, last paragraph of V-COVER):**
  - `clm` → a citeproc citation of its evidence sources;
  - `run`, `fig`, `stat`, `plan` → cross-references to the Methods section, figure or table;
  - `inf` and `ck` → removed (the wording carries them).
  - Markers before and after export must match: every cited item appears in the bibliography, every figure or table referenced exists, and pandoc reports no citeproc warning.
- **Live runs:** Anthropic-vendor roles on Claude Haiku 5.5 (`claude_cli:claude-haiku-5-5`); Writer = claude_cli; SimReader = codex_cli. Judges as configured: `judge` = codex_cli:gpt-6-luna, `judge_2` = ollama:deepseek-v4.1-flash:cloud, `judge_3` = claude_cli:claude-haiku-5-5. **No local model.**

## Review Focus

1. **A sentence cites a claim that was withdrawn or narrowed after drafting** (audit after an `audit → argue` back-edge, or a retraction found at audit). The audit fails that sentence by name and takes the owning back-edge; nothing exports with a withdrawn claim marker. Test: Task 5 `test_audit_fails_sentence_citing_withdrawn_claim`.
2. **The Writer puts a number in a sentence that no marker target contains** (a rounded value, a percentage computed in its head). The sentence fails V-NUM, and the section is redrafted with that guidance; after the retries it stays failing and the draft gate names it. Test: Task 2 `test_ungrounded_number_fails_sentence_and_redrafts`.
3. **Markdown that tries to smuggle markup or fake markers** (raw HTML, `[[clm:clm_x]]` inside code spans, a marker split across lines). The parser treats code spans as text, the renderer escapes HTML, and a broken marker counts as no marker. Test: Task 1 `test_markers_in_code_spans_and_broken_markers_do_not_count`.
4. **Export with an item that has no CSL-renderable metadata** (missing year or authors). Export fails with the item named before pandoc runs, rather than printing "(n.d.)" or "???". Test: Task 6 `test_export_refuses_item_without_csl_metadata`.
5. **A revise round that rewrites a verified sentence into an unverified one, or deletes a contested counterpoint.** Changed sentences are re-verified and V-BALANCE reruns; a round that loses balance or adds failing sentences is not accepted. Test: Task 4 `test_revision_that_breaks_balance_is_rejected`.

---

### Task 1: Manuscript parsing, sentence verification (V-COVER, V-NUM, V-HEDGE, V-QUOTE, G-SUPPORT) and draft store

**Files:**
- Create: `src/rk/production/manuscript.py`, `src/rk/verify/cover.py`.
- Modify: `src/rk/verify/numbers.py` (marker-target pools), `src/rk/ui/drafts.py` (use the shared parser; resolve `fig`/`stat`/`plan`), `src/rk/store/schema.sql` (drafts projection).
- Test: `tests/test_manuscript.py`, `tests/test_cover.py`.

**Interfaces (produces):**
- `Marker{kind: Literal["clm","run","fig","stat","plan","inf","ck"], target: str | None, span: tuple[int,int]}`.
- `Sentence{index: int, text: str, prose: str, markers: list[Marker], span: tuple[int,int], quoted: bool}`. `span` and `Marker.span` are source offsets into the section markdown; `text` is the rendered prose (code spans kept), `prose` is `text` with code spans blanked (what every prose check reads: numbers, V-HEDGE and quotes in a code span do not count); `quoted`: the paragraph of a block quote, one sentence of quoted text.
- `source_markers(markdown: str) -> list[Marker]`: the literal grammar in the source, outside code spans and code blocks, not backslash-escaped (escaped, entity-encoded and emphasis-split forms are no markers), and only where its whole source is rendered prose text (link text counts; a heading, a table, a link's destination or title, an autolink, an image's alt text or a reference definition does not); `rk.ui.drafts.render_draft` substitutes exactly these.
- `parse_section(markdown: str) -> list[Sentence]`:
  - markdown structure via markdown-it (`html=False`);
  - code spans and code blocks are not prose;
  - sentence split on `.?!` (with closing quotes or brackets, and the markers right after it, which belong to that sentence) followed by whitespace and anything but a marker, keeping abbreviations (`e.g.`, `i.e.`, `et al.` unless a capitalised word follows, `vs.`, `Fig.`, `Eq.`, decimal numbers) and initials before a capitalised name (`J. Smith`) intact;
  - headings and table captions are not sentences.
- `section_problems(markdown: str) -> list[str]`: a markdown table or a pandoc caption line (`Table: …`, `: …`) fails the section (tables come from code artifacts at export), and so does a marker where the reader cannot see it (the places `source_markers` leaves out).
- `SentenceVerdict{index, ok: bool, problems: list[str]}`.
- `verify_sentence(ps, s: Sentence, *, paper_type: str) -> SentenceVerdict`, implementing every sentence rule in Global Constraints.
- `number_pool(ps, markers: list[Marker]) -> NumberPool` (`rk.verify.numbers.NumberPool{nums: list[Num], values: list[float]}`; `covers(n)`: `nums` match as written (value and unit), `values` exactly or rounded as written, `matches_value`):
  - `clm` → the claim's text and its evidence note texts;
  - `stat`/`fig` → the artifact's reported values (a pooled `stt`: its `display` values plus `k`; a map table: its counts);
  - `run:evt_` → a per-event-type whitelist of named top-level numeric fields of the payload (`cover.EVENT_NUMBERS`, from each producer; never bookkeeping or nested payloads; a list field gives its length; an unknown type: nothing);
  - `run:prt_` → the frozen protocol's numeric fields;
  - `plan` → the design/hypothesis text.
- `SectionVerdict{section, version, sentences: list[SentenceVerdict], ck_share: float, ok: bool, problems: list[str]}` (`problems`: section-level: the common-knowledge share, a table or caption, an empty section).
- `verify_section(ps, section: str, markdown: str, *, paper_type, version=None) -> SectionVerdict`: rejects when `ck_share > 0.05` or `section_problems` finds any.
- Draft store (events and projection `drafts`):
  - `write_draft(ps, section, markdown, *, stage, attempt, round, by_task, actor) -> int`: the version number. It writes `artifacts/drafts/<section>.v<N>.md` and its sha256 (`draft.written`).
  - `record_section_verdict(ps, section, version, verdict, *, actor)` (`draft.verified`; `verdict` validated as a `SectionVerdict` of that section).
  - `latest_draft(ps, section) -> (version, markdown) | None`; `draft_text(ps, section, version)` (sha256-checked); `drafted_sections(ps)`. The Workbench preview lists and reads drafts only through these (a file without a row is no draft).
  - `draft_versions(ps, section)`.

- [ ] **Step 1: Failing tests** in `tests/test_manuscript.py`:
  - `test_sentences_keep_abbreviations_and_decimals` ("Smith et al. found 0.62 (e.g. in trials)." is one sentence);
  - `test_markers_attach_to_their_sentence`;
  - `test_markers_in_code_spans_and_broken_markers_do_not_count` (Review Focus 3);
  - `test_headings_are_not_sentences`.

  In `tests/test_cover.py`:
  - `test_sentence_without_marker_fails`;
  - `test_unknown_or_hash_mismatched_target_fails`;
  - `test_number_must_be_covered_by_a_marker_target` (a number in the evidence passes; 0.6 vs evidence 0.62 fails; a stat artifact value rounded as written passes);
  - `test_ck_share_over_five_percent_fails_section`;
  - `test_ck_sentence_with_number_fails`;
  - `test_plan_marker_outside_proposal_fails`;
  - `test_inf_sentence_must_be_hedged` ("This proves X [[inf]]" fails; "This may suggest X [[inf]]" passes);
  - `test_sentence_stronger_than_its_claims_fails`;
  - `test_quoted_text_must_be_a_verified_quote_note`;
  - `test_unsupported_or_withdrawn_claim_marker_fails`.
- [ ] **Step 2:** Run and confirm they fail.
- [ ] **Step 3:** Implement the interfaces above. `rk.ui.drafts` uses `parse_section`'s marker regex (one grammar) and resolves `fig`/`stat`/`plan` targets for the preview.
- [ ] **Step 4:** Run the tests and confirm they pass. Full suite, pipefail, ruff.
- [ ] **Step 5: Commit** `feat(verify): sentence markers, V-COVER and the draft store`.

### Task 2: Draft stage (Writer per required section)

**Files:**
- Create: `src/rk/production/draft.py`, `src/rk/production/sections.py`.
- Modify: `src/rk/orchestrator/graph.py`, `src/rk/production/__init__.py` (`default_workers`), `src/rk/roles/writer.md`, `src/rk/config.py` (`draft_section_retries = 2`).
- Test: `tests/test_draft.py`.

**Interfaces:**
- Consumes: Task 1, plus:
  - `argument_claims` / `main_claim` (argue);
  - `synthesis_claims` and `synthesis_limitations`;
  - `argue_limitations`;
  - `approved_design` (design);
  - the frozen protocol;
  - `prisma_counts` (verify.prisma);
  - `artifacts`.
- Produces:
  - `SECTIONS: dict[str, list[SectionSpec]]`, with `SectionSpec{id, title, purpose: str, kernel_written: bool}`, following the Global Constraints list. `critical_review` themes come from the synthesis themes. `disclosure` has `kernel_written=True`; its body comes from Task 5's `render_disclosure` (Task 2 writes a placeholder-free stub verified as `[[run:]]` sentences listing the stages run so far; audit rewrites it).
  - `SectionDraft{markdown: str}` is the Writer's output.
  - `DraftWorker(resumes_in_place=True)`:
    - For each section in order (resume skips sections whose latest version in the attempt verified ok), it calls the Writer with: the section spec; the claims with ids, texts and statuses; the artifacts with ids and values; protocol ids; event ids for PRISMA and search counts; design/hypothesis ids (proposal); the limitations; the audience; and the marker grammar.
    - The kernel stores the version, verifies it, and on failure retries with the failing sentences and problems as guidance, up to `draft_section_retries`.
  - Gate:
    - every required section has a verified latest version (`ok`);
    - V-BALANCE passes per section (Task 3);
    - every main and supported finding claim is cited somewhere;
    - every `fig`/`stat` artifact cited exists in the passed synthesis attempt.
  - Back-edges:
    - the Writer reports `missing_support` with claim needs → `draft → argue` (proposal: `missing_design` → `draft → design`);
    - otherwise a failing gate → rerun (`draft → draft`).
  - P1/P2: no checkpoint (only `stage_blocked` at the limit, as today).
  - As implemented (Task 2):
    - `default_workers` lives in `rk.orchestrator.engine`; `draft` is registered there.
    - `SECTIONS["critical_review"]` lists the fixed sections; `required_sections(paper_type, themes)` inserts one `theme-<n>` section per distinct synthesis theme (`theme_or_outcome` of the narrative's groups) after `introduction`; a critical review without a theme fails `_load` (so `run` fails before any Writer task, and the gate and degrade fail naming it). Titles and purposes follow spec §7.3 (e.g. Methods with risk of bias; "Background & Significance", "Proposed Methods", "Expected Outcomes & Alternatives", "Gaps & Agenda").
    - `SectionDraft{markdown, missing_support: list[str], missing_design: str}`; `missing_design` outside a proposal is refused (`refused` note, keyed by section and Writer task, recorded before the version is stored): the version written by that task fails with it as a section problem, so the try is retried however its markdown verifies.
    - Writer inputs: artifacts carry only `cover.reported_values(values)` (the one definition V-NUM pools: display values, k, map-table counts); evidence notes carry `verified` (a verified quote note, the only kind V-QUOTE accepts).
    - PRISMA counts: the kernel event `draft.prisma` (once per attempt, `prisma_event`) carries eleven count fields of search_screen's passed attempt (the ten boxes and `excluded_ft_total`; the per-reason counts come with the flow figure), which `cover.EVENT_NUMBERS` pools, plus `search_attempt` and `protocol_id` (never pooled) so the audit can recompute the counts and refuse a stale event.
    - Retries: at most 1 + `draft_section_retries` Writer tasks per section per attempt; the retry input carries `previous_draft` and `failures` (`sentence N “text”: problems`). The gate reads every section's latest text (a missing, unreadable or altered file fails as `section X vN: <error>`; `draft_text` maps an `OSError` to `ValueError`) and names each failing sentence the same way.
    - The disclosure stub: one sentence per stage passed so far, citing its latest `stage.passed` event.
    - P1 `degrade` deletes the failing sentences and the sentences citing a `fig`/`stat` of another attempt and stores the rest as a new version (`stage.degraded`); a section it would empty fails the run, named, with nothing deleted; when there is nothing to delete (only section-level failures or uncited claims) it fails naming the failures; otherwise the engine gates again.

- [ ] **Step 1: Failing tests:**
  - `test_draft_writes_every_required_section_per_paper_type` (four paper types, scripted Writer);
  - `test_ungrounded_number_fails_sentence_and_redrafts` (Review Focus 2: retry guidance names the sentence; after the retries the gate fails naming it);
  - `test_draft_resumes_in_place_without_rewriting_verified_sections` (RateCapped mid-section);
  - `test_missing_support_takes_draft_to_argue`;
  - `test_proposal_missing_design_takes_draft_to_design`;
  - `test_disclosure_section_is_kernel_written`;
  - `test_every_main_claim_and_supported_finding_is_cited`.
- [ ] **Step 2:** Confirm they fail.
- [ ] **Step 3:** Implement. The Writer prompt (`writer.md`) gets: the marker grammar, the sentence rules, "never state a number a marker target does not contain", and "use [[inf]] only for your own inferences and word them tentatively".
- [ ] **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(production): draft stage`.

### Task 3: V-BALANCE

**Files:**
- Create: `src/rk/verify/balance.py`.
- Modify: `src/rk/production/draft.py` (`DraftWorker.gate` runs `check_balance` on every section's latest verified version; a one-sided section fails the gate and is redrafted with the problem as guidance).
- Test: `tests/test_balance.py`, `tests/test_draft.py` (the gate fails on a one-sided section).

**Interfaces:**
- Produces `check_balance(ps, section: str, sentences: list[Sentence]) -> list[str]`, returning problems; empty means pass. `balance_requirements(ps, claim_id) -> BalanceRequirements{contested, traced, contested_by, answered_by, unanswered, conceded_sourced, targets}` holds the balance facts of one claim (`traced`: a conflict is recorded for it; `targets`: objection id → what it is on, the claim or an evidence); `check_balance` and the Writer's claim inputs both read it.
- For every `clm` cited in the section that is `contested`, or that has a responded objection (on the claim or its evidence):
  - the section must contain a sentence citing the counterpart claim (for a conflict) or the objection's response claim;
  - that sentence must come in the same section, after the first citation of the contested claim.
- A conceded or rejected objection, sourced or not, asks for nothing: V-COVER has no objection or source marker, so no sentence could cite it (a conceded priority-1 objection already fails its claim's G-SUPPORT until it is narrowed or withdrawn). The disclosure names each cited claim with a conceded sourced objection instead (Task 5, G-DISCLOSE).
- The problem names the claim and the missing counterpart or response.
- As implemented (Task 3):
  - `balance_requirements` reads argue's passed attempt (no passed attempt: `ValueError`; `check_balance` returns the single problem `section S: V-BALANCE cannot be checked: argue has not passed`): `contested_by` the live counterparts of its conflicts (`conflict_counterparts` over `argument_objection_ids`; a withdrawn counterpart is not asked for), `answered_by` `[{objection_id, response_claim_id}]` of the `responded` objections on the claim or its evidence (a conflict objection included) whose response is live, `unanswered` those whose response claim was withdrawn (fail closed: a problem naming the objection as unanswered), `conceded_sourced` `[{objection_id, text, source_id}]` of the conceded objections with a `source_id` (the accepted gap above: listed, never a problem). A contested claim with no recorded conflict is one-sided by itself.
  - "After the first citation" counts the same sentence. For the two claims of one `both_contested` conflict (each contested by the other) either order gives both sides: the counterpart may come first when the claim's own citation follows it. A one-way counterpart cited before the claim fails.
  - Problems read `section S is one-sided: C is contested by X, and the section does not cite that counterpart [[clm:X]] after its first citation of C` / `… the objection O to it (to its evidence E) was answered by R, and the section does not cite that response [[clm:R]] …` / `… the objection O to it was answered by R, which is withdrawn: the objection is unanswered …`.
  - Each Writer claim input carries `contested_by`, `answered_by` and `conceded_sourced`; `writer.md` says to cite the counterpart or response after the claim, in the same section, and to qualify a claim with conceded sourced objections.
  - `DraftWorker._verify` adds the problems to the section verdict as section-level problems (so a one-sided version is retried in the attempt with them in `failures`); the gate reruns `check_balance` on every section's latest version (an objection answered since verification counts) and names new problems as `section X vN: <problem>`.

- [ ] **Step 1: Failing tests:**
  - `test_contested_claim_without_counterpart_is_one_sided`;
  - `test_answered_objection_needs_its_response_cited`;
  - `test_counterpart_in_another_section_does_not_count`;
  - `test_uncontested_claims_pass`.
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(verify): V-BALANCE`.

### Task 4: Revise stage (SimReader questions, Writer revisions, ≤K rounds)

**Files:**
- Create: `src/rk/production/revise.py`.
- Modify: `src/rk/orchestrator/graph.py` (`OPEN_STAGES` += `revise`), `src/rk/roles/simreader.md`, `src/rk/config.py` (`revise_max_rounds = 3`).
- Test: `tests/test_revise.py`.

**Interfaces:**
- Produces:
  - `ReaderQuestions{questions: list[{id, section, sentence_index: int | None, text, priority: Literal[1,2,3]}]}` is the SimReader output (codex_cli; inputs: the manuscript and the audience).
  - `Revision{section, markdown, answers: list[{question_id, how: Literal["revised","answered","declined"], note}]}` is the Writer output, one per section with questions.
  - `ReviseWorker(resumes_in_place=True)`. Each round:
    - SimReader questions;
    - the Writer revises the sections that have questions;
    - the kernel diffs each section by sentences against the previous version, re-verifies the changed sentences, reruns V-BALANCE for the section, and accepts the revision only when nothing that verified before now fails and balance holds;
    - otherwise it keeps the previous version and records the rejected revision with reasons.

    Rounds stop when the SimReader returns no priority-1 question, or after `revise_max_rounds`.
  - Gate:
    - every priority-1 question is `revised` or `answered`, with a note;
    - the latest version of every section verifies;
    - balance holds.

    `declined` is allowed only for priority 2–3.
  - Back-edge: a section that cannot verify after a round → `revise → draft`, with the failing sentences as guidance.
  - As implemented (Task 4):
    - `rk.production.revise`: `ReaderQuestions` / `ReaderQuestion{id, section, sentence_index, text, priority}`, `Revision{section, markdown, answers: list[Answer{question_id, how, note}]}`, `ReviseWorker`, `revise_notes(ps, attempt, kind=None)` (event `revise.noted`, kinds `checked`, `questions`, `revision`, `decided`, `back_edge`). `revise` is in `default_workers`, `OPEN_STAGES` and `BACK_EDGES` (`revise->draft`); `Settings.revise_max_rounds = 3` (≥1).
    - Inputs: draft's `load_inputs` (renamed from `_load`; `_Inputs` → `DraftInputs`, `_failures` → `version_failures`) plus draft's passed attempt. The Writer gets draft's shared `writer_inputs(ps, inputs, spec, prisma)` (extracted from `DraftWorker._inputs`: claims with `contested_by`/`answered_by`/`conceded_sourced` and verified-quote flags, artifacts' `reported_values`, protocol, search events and draft's `draft.prisma` event, design/hypotheses, limitations) plus `revise{version, markdown, questions[{id, priority, text, sentence_index, sentence, round, earlier_answer}]}` and `other_sections`. The SimReader gets the revisable sections (not the kernel-written disclosure) as `{index, text}` sentences, the audience and `earlier_questions` with their answers. Both task texts frame every input as data, not instructions; both run with `tools=[]`.
    - A round: (0, a new attempt only) the previous attempt's unresolved priority-1 questions are recorded as round 0 (`questions`, no task; each with `carried_from` and `previous_answer`), so a rerun cannot wear the gate down. (1) `checked`: the latest version of every required section (disclosure too) verified with `draft.section_verdict`; any failure → `revise->draft` with `section X vN: sentence i “…”: problems` as the reason (noted, so a resume returns it); the check after the last round is the same. On `revise->draft` draft writes every section afresh (its `retry` and `earlier_sections` come only from its own attempt, so accepted revisions are not carried); the guidance names revise's versions, which draft's Writer never sees, but it quotes each failing sentence's text. (2) Budget: before a round's questions are asked, if the stage's role-task budget left (`STAGE_TASK_BUDGET` per run session, counted as the executor does; outside a run the attempt's own tasks) is less than 1 + the number of revisable sections, the rounds stop as if K were reached (noted `budget{round, used, needed, budget}`), so a run is never paused mid-round by `BudgetExceeded`. (3) `questions`: kernel ids `a<attempt>r<round>q<n>`, each with the text of the `sentence` it is about; a question on a section the Writer does not revise, or an empty one, is `refused`. (4) one Writer task per section with this round's questions plus every earlier priority-1 question on it still unresolved; its output is noted (`revision`) before the decision. (5) `decided`.
    - Re-verification rule: a sentence keeps its earlier verdict only when the same sentence occurs in the check of the version it replaces (`_key`: its exact source text `markdown[span]` with markers, `quoted`, the rendered `text` and `prose`, and its markers with their offsets in the sentence; a section-wide change such as a deleted reference definition changes the rendering) and every marker's `cover.target_state` (a digest of all the sentence rules read about the target: a claim's row, evidence, notes, objections and claim gate; an artifact's row and file sha256; an event; a protocol; a design; a hypothesis with its pre-registration designs) is unchanged since that check; otherwise it is verified again (`reverified`). `cover.verify_section(..., known=)` takes the kept verdicts (one verifier); section-level rules and V-BALANCE always run over the whole section. The gate verifies afresh.
    - One section verdict: `draft.section_verdict(ps, section, md, *, paper_type, known=None, version=None)` (`verify_section` + V-BALANCE problems as section-level ones) is used by draft's `_verify` and gate and by revise's checks, decision, gate and degrade; `draft.latest_version(ps, section)` (latest row and text, or the failure naming it) by both gates and revise's checks; `draft.manuscript_problems(ps, inputs, texts)` (the main claim, unless withdrawn, and every supported finding cited somewhere; every `fig`/`stat` an artifact of synthesize's passed attempt) by draft's gate and revise's decision, gate and degrade. Draft's gate now also re-verifies every section's latest version (recorded and fresh failures, deduplicated).
    - Acceptance: a revision is accepted only when the composed verdict is ok (V-BALANCE included, "argue has not passed" too) and it adds no `manuscript_problems` problem the manuscript did not already have (e.g. dropping the only `[[clm:main]]` sentence, citing another attempt's `stt_`). Accepted → `write_draft(stage="revise", round=r, by_task=writer task)` + `draft.verified`, unless its text equals the version's (`unchanged`, no new version). Rejected → only the `revision` note holds its text and `decided` its `reasons`; it never enters the `drafts` store, so `latest_draft` / the gate / the audit read accepted versions only. A Writer failure or a revision of another section is a rejected revision with that reason.
    - Rounds stop after a round whose questions have no priority 1 (its questions are still revised), after `revise_max_rounds`, or at the budget. Answers count only from accepted revisions and only for the questions that revision was asked.
    - Resume in place: a round's `checked` and `questions` are reused; a section's recorded `revision` is decided (if not yet) and, when accepted, stored once with its verdict (a version with that `by_task` found gets only its verdict), whatever questions the section has now: an accepted revision made only for a carried question is never lost.
    - Gate failures: `the priority-1 question <id> on S “…” has no answer in an accepted revision` / `was declined: …` / `was answered without a note`; `section X vN: …` for every failing sentence or problem; the `manuscript_problems`. A priority-1 question counts as resolved by deletion only when P1's degrade deleted its sentence (listed in that `stage.degraded` entry's `questions`); every other one on content still in the paper must be `revised` or `answered` with a note. No checkpoint.
    - P1 `degrade` (spec §5.3: delete or narrow, never keep and only label): from the latest versions it deletes the sentences that cannot verify (draft's rule, `draft.deletable` / `draft.delete_sentences`) and the sentence of each unresolved priority-1 question (matched by its recorded sentence text); what is left is stored as new versions (stage `revise`, round 0, `by_task` none: the degrade marker) with their verdicts, and `stage.degraded` lists the deleted sentences and those question ids (so they reach the limitations and the disclosure, and the gate accepts exactly those questions). It deletes nothing and fails the run, naming the cause, when a question is about a section as a whole (every revisable section is required, so none is ever deleted) or about a sentence no longer in the section, when a deletion would empty a section, add a `manuscript_problems` problem or leave a section that does not verify (e.g. one-sided), or when there is nothing to delete.

- [ ] **Step 1: Failing tests:**
  - `test_round_revises_and_reverifies_only_changed_sentences`;
  - `test_revision_that_breaks_balance_is_rejected` (Review Focus 5);
  - `test_revision_that_adds_failing_sentence_is_rejected`;
  - `test_rounds_stop_at_k_or_without_priority1_questions`;
  - `test_priority1_question_cannot_be_declined`;
  - `test_revise_resumes_in_place_mid_round`;
  - `test_unverifiable_section_takes_revise_to_draft`.
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(production): revise stage`.

### Task 5: Audit stage (every hard gate, disclosure, verification report, final_review)

**Files:**
- Create: `src/rk/production/audit.py`, `src/rk/production/disclosure.py`.
- Modify: `src/rk/checkpoints.py` (the `final_review` answer shape).
- Test: `tests/test_audit.py`, `tests/test_disclosure.py`.

**Interfaces:**
- `render_disclosure(ps) -> str`: `disclosure.md`, with these sections and sources:
  - models used: per role, the driver and model, the task count, and what the role did (from `run_task` rows);
  - human decisions: every checkpoint answered by a human, with kind, decision and time;
  - not reviewed by a human: every vote-answered checkpoint, every P1 degrade (`degradations`), every gap/topic and "RQ chosen by an agent" note from discovery (§5.2, §8.3);
  - limitations: `synthesis_limitations`, `argue_limitations` and design limitations;
  - preregistration deviations: protocol deviations, including analysis deviations.

  Each line carries `[[run:evt_…]]` to its event.
- `audit_manuscript(ps, *, paper_type) -> AuditReport{gates: dict[str, list[str]], ok: bool}`:
  - **G-REF:** V-REF + V-RETRACT for every item cited through a `clm` marker. A retracted or withdrawn item used as evidence fails.
  - **G-QUOTE:** V-QUOTE for **every** quote note of the project (§9.2: "全部 quote 笔记"), whether a cited claim uses it or not, and for every quotation in the manuscript. A failing note that no cited claim uses still fails the gate; its back-edge is `audit → read_extract`.
  - **G-COVER:** Task 1, for every sentence of every section.
  - **G-SUPPORT:** every cited claim is supported.
  - **G-HEDGE:** V-HEDGE on the claims and the sentences.
  - **G-DISCLOSE:** `disclosure.md` has all five parts and every human/vote checkpoint is listed.
    - It names every cited claim that has a conceded sourced objection (`balance_requirements(...).conceded_sourced`): claim id, objection id, source id. These are the V-BALANCE gap of Task 3.
  - **G-PREREG:** systematic and scoping reviews only (`protocol_prereg_ok`).
  - Also V-PRISMA (where the paper type uses it) and V-STATS for every cited artifact.
  - Every cited `draft.prisma` event is recomputed from its recorded `search_attempt` (`prisma_counts`) under its recorded `protocol_id`; a count that differs, or a search attempt that is no longer search_screen's passed attempt, fails ("stale PRISMA counts") → `audit → draft`.
- `AuditWorker(resumes_in_place=True)`:
  - It runs `audit_manuscript`, writes `artifacts/audit/report.v<N>.md` and `.json` (`audit.reported`), and writes `disclosure.md`, re-rendering the `disclosure` section through `write_draft`.
  - On failure it takes the owning back-edge:
    - sentence, coverage or balance failures → `audit → draft`;
    - claim support or hedge failures on claims → `audit → argue`;
    - retraction or quote failures on evidence → `audit → read_extract`.

    The guidance names the failures.
  - When it passes:
    - P2 opens `final_review` (hard-human). The answer is `{"decision": "approve" | "revise" | "back", "comment"?, "to"?}`: `revise` → `audit → revise` with the comment; `back` with `to` ∈ {`draft`, `argue`, `read_extract`}.
    - P1 votes `approve` / `not_approved` (conservative) with the manuscript and audit report as inputs. `not_approved` → `audit → revise` with the reasons. At the limit, the degrade fails the run (Deviation 3).

    The approval is recorded and listed in the disclosure ("final review: approved by vote, not reviewed by a human" in P1).
- Gate: the latest audit report is ok, and the final review is approved for this attempt.

- [ ] **Step 1: Failing tests:**
  - `test_audit_fails_sentence_citing_withdrawn_claim` (Review Focus 1);
  - `test_retracted_evidence_takes_audit_to_read_extract`;
  - `test_failing_quote_note_no_claim_cites_still_fails_g_quote`;
  - `test_unsupported_claim_takes_audit_to_argue`;
  - `test_disclosure_lists_models_humans_votes_limitations_deviations`;
  - `test_disclosure_misses_a_human_checkpoint_fails_g_disclose`;
  - `test_disclosure_names_conceded_sourced_objections`;
  - `test_p2_final_review_approve_revise_back`;
  - `test_p1_final_review_not_approved_goes_to_revise_then_fails_at_limit`;
  - `test_prereg_gate_only_for_systematic_and_scoping`;
  - `test_audit_resumes_in_place`.
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(production): audit stage, disclosure and final review`.

### Task 6: Export stage (pandoc + citeproc + tectonic) and the reproducible package

**Files:**
- Create: `src/rk/production/export.py`.
- Test: `tests/test_export.py` (pandoc/tectonic tests are marked `@pytest.mark.binaries` and skip with a reason when the binary is absent; the conversion logic itself is tested without binaries).

**Interfaces:**
- `to_pandoc_markdown(ps, sections, *, target: Literal["latex", "docx"]) -> (markdown: str, csl_items: list[dict], refmap: dict)`, format-aware because the figure differs by target:
  - `[[clm:x]]` → `[@key1; @key2]` for the distinct items behind claim x's evidence, where `key` = a stable citekey from `to_csl`;
  - `[[stat:]]` / `[[fig:]]` → `@tbl:<id>` / `@fig:<id>` cross-references; the table and the figure are emitted in Results. No SVG converter is installed (`rsvg-convert` is absent), so `rk.stats.plots` gains `forest_tikz(values) -> str`, a TikZ picture generated by code from the same artifact values as the SVG. `target="latex"` emits it as a raw `{=latex}` block, with no image reference. `target="docx"` emits the stored SVG as an image and no LaTeX. A test asserts that both renderings carry the same study labels, estimates and intervals, and that neither target's markdown contains the other's figure. tectonic fetches its package bundle on first use, so the binaries tests need a warmed cache and skip with that reason otherwise;
  - `[[run:]]` / `[[plan:]]` → a reference to the Methods or Proposed Methods section (`[Methods](#methods)`);
  - `[[inf]]` and `[[ck]]` removed.
- `check_csl(items) -> list[str]`: every item has title, issued year and ≥1 author or an editor (Review Focus 4).
- `export(ps) -> ExportResult{files: dict[str, Path], warnings: list[str], ok: bool}`:
  - writes `artifacts/export/paper.md` (the `docx` target, the human-readable markdown), `paper-latex.md` (the `latex` target), `refs.json` (CSL JSON), `refs.bib`, `paper.docx`, `paper.tex` and `paper.pdf`;
  - pandoc invocations: `pandoc paper.md --citeproc --bibliography refs.json -o paper.docx`, and `pandoc paper-latex.md --citeproc --bibliography refs.json -o paper.tex` / `-o paper.pdf --pdf-engine=tectonic`. argv is a list (never a shell), the cwd a temporary directory, with a timeout;
  - the round trip is checked: the set of citekeys in `paper.md` equals the bibliography entries pandoc rendered (its `--citeproc` warnings parsed from stderr ⇒ failure), and every `@fig:`/`@tbl:` target exists.
- `build_package(ps) -> Path`: `artifacts/export/package-<sha8>.zip` containing:
  - the exports;
  - `disclosure.md`;
  - the audit report;
  - the frozen protocol (JSON);
  - the extraction table (CSV);
  - every cited artifact with its code version and input hashes;
  - `MANIFEST.json` with sha256 per file and the event log head hash.

  It never includes `config.toml`, keys, the browser profile or absolute paths.
- `ExportWorker`: runs `export` + `build_package`. Gate: `ok` and the package exists with a matching manifest. It has no back-edge: an export failure is a defect, reported as `failed` with the pandoc stderr.

- [ ] **Step 1: Failing tests:**
  - `test_markers_convert_to_citations_and_cross_references`;
  - `test_latex_and_docx_targets_carry_only_their_own_figure` (TikZ only in the latex target, the SVG image only in docx; same labels and values in both);
  - `test_inf_and_ck_markers_are_removed`;
  - `test_export_refuses_item_without_csl_metadata` (Review Focus 4);
  - `test_citekeys_round_trip_or_export_fails` (binaries);
  - `test_pdf_docx_tex_are_produced` (binaries);
  - `test_package_manifest_hashes_match_and_contains_no_secrets_or_paths`.
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Confirm they pass, including binaries locally; full suite.
- [ ] **Step 5: Commit** `feat(production): export and reproducible package`.

### Task 7: Tools, CLI and Workbench for draft, revise, audit, export

**Files:**
- Modify: `src/rk/production/tools.py`, `src/rk/roles/policy.py`, `src/rk/cli/main.py`, `src/rk/ui/api.py`, `src/rk/ui/static/app.js`.
- Test: `tests/test_ui_manuscript.py`, `tests/test_cli_production.py`.

**Interfaces:**
- Read tools, granted to `_FULL_READERS` + cli only:
  - `rk_manuscript_get(section?, version?)`: sentences with verdicts;
  - `rk_audit_get(version?)`.
- CLI:
  - `rk draft show [--section S] [--version N]`;
  - `rk revise show`;
  - `rk audit show`;
  - `rk export [--open]`.
- Workbench:
  - the Drafts page (existing preview) shows each sentence's verdict and problems, as text;
  - a Revise panel shows the questions and how each was handled;
  - an Audit page shows the gates with their failures;
  - a `final_review` card offers approve / revise (comment) / back (target + comment), with gating as for other cards;
  - export download links go through the existing artifact route (export files only, with the right content types: application/pdf, docx, text/markdown, application/zip).

- [ ] **Step 1: Failing tests:**
  - browser round trips for `final_review` (approve, revise, back) reaching the worker;
  - sentence verdicts rendered as text (an XSS fixture with `<img onerror>` in a draft stays text);
  - download route content types, and refusal outside `artifacts/export`;
  - CLI output for each command;
  - the read-tool policy (screeners, scout and reader get none).
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. Check at 1280 and 760 px on a scratch `rk serve` with a temporary RK_HOME; no console errors. **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(ui): drafts, revise, audit, final review and export in the Workbench`.

### Task 8: Systematic-review reproduction set (§10) and the eval command

**Files:**
- Create: `src/rk/eval/sr_repro.py`, `src/rk/eval/data/sr_repro.json` (manifest).
- Modify: `src/rk/cli/main.py` (`rk eval sr-repro`).
- Test: `tests/test_sr_repro.py` (offline: the manifest schema, the metrics on a recorded fixture run).

**Interfaces:**
- Manifest entry:
  - `{id, pmcid, doi, title, domain: Literal["chemistry","biology","medicine","cs"], question, pico?, date_from, date_to, included: [{doi?|pmid?|title}], main_conclusion: {outcome, direction: Literal["benefit","harm","no_difference","mixed"]}}`;
  - 6 entries, at least 1 per domain;
  - each must be PMC OA, list its included studies, and have a search that dedupes to ≤3,000 records (§7.2).

  Selecting them is a step of this task. Record why each was chosen in the manifest's `note`.
- `seed_project(entry) -> project`: creates a `systematic_review` project with the entry's question frozen as the RQ (Deviation 4) and the date window copied.
- `score(ps, entry) -> {recall_included: float, conclusion_direction_agrees: bool | None, gates_passed: bool, missed: list}`. Matching is by DOI/PMID, then by title `token_set_ratio ≥ 90`. The direction is compared from the main claim, using the judge rule (two vendors agree; otherwise `None`).
- `rk eval sr-repro [--entry ID] [--write-report]`: runs or continues each seeded project `--until export` and writes `docs/eval/sr-repro-<date>.{md,json}`.
- Targets (§10): recall of included studies ≥0.85; main conclusion direction agrees; all hard gates pass.

- [ ] **Step 1: Failing tests:**
  - `test_manifest_has_six_entries_covering_four_domains`;
  - `test_recall_matches_by_doi_pmid_then_title`;
  - `test_direction_needs_two_vendor_agreement`;
  - `test_seeded_project_starts_at_protocol_with_frozen_rq`.
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement; select the 6 reviews and write the manifest (no network in tests; selection is done live, and the manifest is committed). **Step 4:** Confirm they pass; full suite.
- [ ] **Step 5: Commit** `feat(eval): systematic-review reproduction set`.

### Task 9: End-to-end runs, docs and acceptance

**Files:** create `docs/demo/plan5c-manuscript.md` and `docs/eval/sr-repro-<date>.{md,json}`; modify `README.md`.

- [ ] **Step 1: P2 runs to export:**
  - the melatonin systematic review (continuing the Plan 5b demo home);
  - one critical review;
  - one research proposal (a frozen RQ from a small discovery run, or the reproduction of an existing D2 project).

  Answer as the stand-in researcher and log every answer. Record, per project:
  - the sections and the verdict counts;
  - the revise rounds and the questions;
  - the audit gates;
  - the final review;
  - the export files and the package manifest;
  - task counts per role and driver;
  - wall time;
  - the no-local-model SQL check;
  - what failed or was weak.
- [ ] **Step 2: One P1 project end to end** (`intake → export`, with every checkpoint voted). Record every vote and the disclosure's "not reviewed by a human" list.
- [ ] **Step 3: The reproduction set:** `rk eval sr-repro --write-report` over all 6 entries. Usage limits make runs wait, so this may take days; report per entry, with the targets.
- [ ] **Step 4: Docs.** README section "Draft, revise, audit, export", plus the demo doc. Every number must come from the runs. No personal paths.
- [ ] **Step 5:** Full suite (pipefail), ruff, personal-path scan. **Commit** `docs: Plan 5c runs and reproduction set`.
