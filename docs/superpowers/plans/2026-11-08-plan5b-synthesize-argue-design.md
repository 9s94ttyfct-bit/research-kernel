# Plan 5b — Synthesize, Argue, Design Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After `read_extract`, the orchestrator runs `synthesize → argue` for every paper type, plus `design` for research proposals (§7.3). The output is:
- verified synthesis findings (narrative per SWiM, an evidence map, or a pre-specified meta-analysis computed by validated code);
- an argument of supported claims with warrants and answered objections;
- for proposals, a frozen method design tied to falsifiable hypotheses.

P2 asks you at the weak spots; P1 votes and records "not reviewed by a human".

**Architecture:**
- `rk.stats` is a standalone statistics module covering effect sizes, inverse-variance pooling, τ² (DerSimonian–Laird, REML), Hartung–Knapp–Sidik–Jonkman intervals, Q, I² and the prediction interval, plus SVG forest plots and evidence-map tables.
  - It is validated against published meta-analysis datasets by `rk eval stats`.
  - Meta-analysis stays off until that validation has passed for the module's current version (§9.1 V-STATS, §12 "统计模块经验证后才启用").
- `rk.production.synthesis`, `rk.production.argue` and `rk.production.design` are stage workers on the Plan 5a pattern: they load inputs, run role tasks through the executor, let the kernel store and verify, gate on stored data, and use P2 checkpoints or P1 votes.
- **Artifacts** (`fig_`, `stt_`) are kernel-made code outputs recording the code path, a module version hash, input hashes and values. V-STATS recomputes them; V-NUM can cite them.

**Tech Stack:** Plan 1–5a stack plus `scipy` (t, χ² and normal distributions; brings numpy). No plotting library: the forest plot is SVG written by code.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md`:
- §5.3: weak spots, P1/P2, votes; P1 "delete or narrow" rule.
- §6.3: entities claim, evidence, edge, objection, hypothesis, design, artifact.
- §7.1: rows synthesize, argue, design.
- §7.2: argue is an open stage.
- §7.3: per paper type.
- §8.1: roles Arguer, Skeptic, Ideator.
- §9.1: V-SUPPORT, V-HEDGE, V-NUM, V-STATS, V-BALANCE.
- §9.2: G-SUPPORT, G-HEDGE.
- §11: item 1, abstract-only = sufficient low.

**Deviations (decided here, flagged to the user):**
1. **No Synthesizer role.** The Arguer drafts the synthesis (§8.1: "organizes claims and evidence"), and the kernel computes every number. The Skeptic reviews it in argue.
2. **Statistical claims are written by the kernel, not a model.** A finding such as "pooled RR 0.62 (95% CI 0.48–0.80; 7 studies; I² 41%)" is generated from an `stt_` artifact by a fixed template, so its wording carries no model-made statement.
   - Its support check is V-STATS (the artifact recomputes from the extraction rows) plus V-NUM against the artifact, with no LLM judges. Judges cannot verify arithmetic, and §5.3 forbids votes from raising support.
   - G-SUPPORT counts such a claim as supported only when V-STATS passes.
3. **New human-only checkpoint kinds:**
   - `pooling_approval` (P2): pool these studies for this outcome, given the comparability assessment. In P1 a vote decides, and the conservative option is no pooling.
   - `design_approval` (P2): §7.1 requires approval of the design. In P1 a vote decides, and the conservative option is "not approved", which leads to the design → argue back-edge or degrade.
4. **Meta-analysis is gated by a recorded validation.** `rk eval stats --write-config` stores `stats_validated = {version, report}` only when every reference dataset reproduces within tolerance.
   - When it is absent or stale, a protocol that pre-specified meta-analysis gets a narrative synthesis instead.
   - The fallback is recorded as a deviation from the protocol (G-PREREG keeps passing) and listed in the limitations.
5. **The default τ² estimator is REML, with Hartung–Knapp–Sidik–Jonkman intervals and no truncation.** This follows the Cochrane Handbook v6.5 recommendation (verify the citation in Task 2 and record it in the eval report). The protocol's `synthesis_plan.model` and `heterogeneity` override it with `DL`, `REML` or `FE`, plus `hksj: true|false`.

## Global Constraints

- **Inherited:** all Plan 1–5a constraints. Git identity repo-local `9s94ttyfct-bit`; no personal paths or hostnames in commits or fixtures.
- **Tests:** no network and no models (conftest guard). Role tasks go through `TaskExecutor` (`ScriptedExecutor` in tests).
- **Live demo models:** the Task 8 demo uses Claude Haiku 5.5 for Anthropic-vendor roles; the judges are the configured cloud judges. No local models.
- **Stage graph:** this plan makes `synthesize`, `argue` and `design` runnable through `default_workers()`. `draft` and later stay "not yet available" (Plan 5c).
- **Back-edges added (§7.1):** `synthesize → read_extract`, `argue → synthesize`, `argue → question`, `design → argue`, `design → question`.
  - Each is allowed 2 uses per project. At the third use, P2 → `stage_blocked` and P1 → `degrade` (engine path unchanged).
- **Budgets (§7.2):**
  - `argue` joins `OPEN_STAGES`: 40 role tasks per session.
  - `synthesize` and `design` are not open stages: their tasks are bounded by the study count and the design size, and only the per-driver hourly caps apply.
- **Vendors:** every claim stores its `generator_vendor`.
  - V-SUPPORT judges exclude the vendors of every role that wrote the claim's subject: `admit()` takes a vendor set, from fix-wave M1.
  - Votes exclude the generator vendors of the voted subject.
- **P1 rule for uncertain claims (§5.3, §9.2):** the claim is deleted (`claim.withdrawn`, listed in the limitations) or rewritten narrower and weaker as a new claim with `narrowed_from`, which must re-verify as supported. Never a vote; never "kept as unproven".
- **Abstract-only evidence (§11.1):** the kernel caps `criteria.sufficient` at score 0 with the reason "abstract only" for every evidence item whose notes are all abstract-sourced (`verified.quote.against == "abstract"`).
- **Paper-type rules (§7.3):**
  - `systematic_review`: synthesis per `synthesis_plan` (meta-analysis only when pre-specified, comparable, approved or voted, and the stats module is validated; otherwise narrative per SWiM).
  - `scoping_review`: an evidence map (counts by the extraction schema's category fields), no meta-analysis, and no V-STATS beyond map recomputation.
  - `critical_review`: a thematic narrative synthesis, no V-STATS.
  - `research_proposal`: a thematic synthesis, then argue, then design.
- **Statistical tolerance:** a reference dataset must match its published results to 1e-3 absolute on log-scale estimates, SEs, τ² and I² (in percent points / 100), and to 1e-2 on CI bounds as reported. V-STATS compares the recomputation with the stored values exactly, to 1e-9.

## Review Focus

1. **A pre-specified meta-analysis whose studies report incompatible outcomes.** The comparability assessment says "not comparable", the stage synthesizes narratively, records the protocol deviation, and lists it in the limitations. No pooled number appears. Test: Task 4 `test_incomparable_studies_fall_back_to_narrative_with_deviation`.
2. **A study reports only a median or a p-value, so no effect size can be computed.** It is excluded from pooling with a recorded reason and kept in the narrative; the pooled k counts only computable studies. Test: Task 2 `test_uncomputable_study_is_reported_not_imputed`, Task 4 `test_pooled_k_excludes_uncomputable_studies`.
3. **An argument claim stays uncertain in P1.** It is withdrawn or narrowed and re-verified; it is never voted up and never kept as unproven; the limitations input lists it. Test: Task 5 `test_p1_uncertain_claim_is_narrowed_or_withdrawn`.
4. **Two supported findings contradict each other.** P2 gets one `evidence_conflict` checkpoint. In P1 the vote's conservative option is "no conclusion": both claims are marked `contested`, and each has an objection with a response. Test: Task 5 `test_contradicting_claims_open_evidence_conflict`.
5. **A proposal hypothesis has no measurement that can falsify it.** The design gate names the hypothesis; the stage reruns with that guidance, then goes back to argue at the edge limit. Test: Task 6 `test_unfalsifiable_hypothesis_fails_design_gate`.

---

### Task 1: Graph, engine texts and argument store extensions

**Files:**
- Modify:
  - `src/rk/orchestrator/graph.py`;
  - `src/rk/orchestrator/runs.py` (the run texts at :63 and :183);
  - `src/rk/discovery/tools.py` (:84, :89, :112);
  - `src/rk/cli/main.py` (:34, :886–890);
  - `src/rk/store/schema.sql`;
  - `src/rk/argument.py`;
  - `src/rk/discovery/store.py` (hypothesis projection).
- Create: `src/rk/production/artifacts.py`.
- Test: `tests/test_argument.py`, `tests/test_engine_production.py`, `tests/test_artifacts.py`.

**Interfaces (produces):**
- `BACK_EDGES` gains the five edges from Global Constraints. `OPEN_STAGES` gains `"argue"`.
- Run and stage description texts are built from `runnable(ps)` (no hard-coded stage lists). `stages_after_question(ps)` replaces the literals.
- `ClaimIn` gains:
  - `kind` adds `"stat_result"` and `"feasibility"`;
  - `artifact_ids: list[str] = []`;
  - `narrowed_from: str | None = None`.
- `ClaimOrigin` adds `"synthesis"`. `list_claims(status=None, origin="argument")` accepts `origin: str | tuple[str, ...]`.
- New `ArgumentService` methods:
  - `withdraw_claim(claim_id, *, reason, actor)` → event `claim.withdrawn`, status `withdrawn`;
  - `respond_objection(obj_id, *, response_claim_id: str | None, status: Literal["responded","conceded","rejected_invalid"], note, actor)` → `objection.responded`;
  - `list_edges(from_id=None, to_id=None, type=None)`;
  - `list_objections(target_id=None, status=None)`;
  - `list_evidence(claim_id)`.
- `EdgeIn.warrant` is validated when `type == "warrant_of"`: `Warrant{circumstance: str, consequence: str, type: Literal["causal","conditional","generalization","analogy","authority","definition"]}`.
- `WarrantTests{true, limited, circumstance_fits, consequence_fits, accepted}` holds the Skeptic's verdict on a warrant. Each test is `{ok: bool, reason: str}`; it is stored by `record_warrant_tests(edge_id, tests, *, actor)` → `warrant.tested`.
- Hypotheses gain `rival_of: str | None`, `status: Literal["proposed","tested_by_design","dropped"]` and `prereg_hash: str | None`.
  - `add_hypothesis(ps, rq_id, statement, falsification, *, rival_of=None, actor) -> hyp_` → `hypothesis.added` (rq.added hypotheses keep working).
  - `set_hypothesis_status(...)`.
- `artifacts` table and `rk.production.artifacts`:
  - `ArtifactIn(kind: Literal["stt","fig"], code_path: str, code_version: str, input_hashes: list[str], values: dict, output_path: str | None)`;
  - `add_artifact(ps, a, *, stage, attempt, created_by_task=None, actor) -> stt_/fig_` (`artifact.added`);
  - `artifact(ps, id)`, `artifacts(ps, stage, attempt)`.
  - Files live under `<project>/artifacts/<id>.<ext>` with a sha256 stored.
- `designs` table (`dsg_`), with only the store in this task (worker in Task 6):
  - `DesignIn{items: list[DesignItem], expected_results: str, alternative_explanations: list[str]}`;
  - `DesignItem{id: str, kind: Literal["experiment","computation","observation"], sample: str, controls: list[str], measurements: list[Measurement{id, what, instrument, unit?}], analysis: str, data_source: str, hypothesis_ids: list[str]}`;
  - `set_design(ps, d, *, stage, attempt, actor) -> dsg_`, `freeze_design(ps, dsg_id, *, actor) -> frozen_hash` (sha256 of the canonical JSON), `design(ps, dsg_id)`, `latest_design(ps)`.

- [ ] **Step 1: Failing tests:**
  - `test_new_back_edges_and_argue_is_open`;
  - `test_run_texts_list_runnable_stages` (a research_proposal project lists design; a critical_review does not);
  - `test_withdraw_and_narrow_claims` (the narrowed claim links `narrowed_from`; the withdrawn claim is excluded from `list_claims(status="supported")`);
  - `test_objection_response_lifecycle`;
  - `test_warrant_shape_validated_and_tests_recorded`;
  - `test_hypothesis_add_with_rival_and_status`;
  - `test_artifact_round_trip_with_file_hash`;
  - `test_design_freeze_hash_is_canonical`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff. **Commit** `feat(production): 5b graph, argument store and artifacts`.

### Task 2: Statistics module and its validation

**Files:**
- Create:
  - `src/rk/stats/__init__.py`, `src/rk/stats/effects.py`, `src/rk/stats/meta.py`, `src/rk/stats/plots.py`;
  - `src/rk/eval/stats_eval.py`;
  - `tests/test_stats.py`;
  - `tests/fixtures/stats/*.json`, the reference datasets with their published results and citations.
- Modify:
  - `pyproject.toml` (`scipy`);
  - `src/rk/config.py` (`stats_validated: dict | None = None`);
  - `src/rk/cli/main.py` (`rk eval stats [--write-config]`).

**Interfaces (produces):**
- `STATS_VERSION: str`, the sha256 over the source files of `rk.stats`. It is computed at import, and the validation records it.
- `effects.compute(measure: Literal["RR","OR","RD","MD","SMD"], data: dict) -> Effect(yi: float, vi: float) | Uncomputable(reason: str)`.
  - Binary data: events/totals per arm. RR and OR are log-scale, with a 0.5 continuity correction only when a cell is 0. A study with a zero cell in both arms is Uncomputable "no events in either arm".
  - Continuous data: mean, SD and n per arm. SMD is Hedges' g with the small-sample correction.
  - Missing inputs give `Uncomputable` naming the missing fields. Nothing is ever imputed from medians or p-values.
- `meta.pool(effects: list[Effect], *, model: Literal["FE","DL","REML"]="REML", hksj: bool=True, level=0.95) -> Pooled(k, estimate, se, ci_low, ci_high, tau2, i2, q, q_df, q_p, pi_low, pi_high, model, hksj)`.
  - REML uses Fisher scoring with a convergence tolerance of 1e-10 (max 100 iterations; non-convergence is an error). The prediction interval is given when k ≥ 3.
- `plots.forest_svg(labels: list[str], effects: list[Effect], pooled: Pooled | None, *, measure) -> str`: self-contained SVG with labels escaped as XML text, no script, deterministic output.
- `plots.map_table(rows: list[dict], fields: list[str]) -> dict` gives counts by category for evidence maps.
- `stats_eval.run_stats_eval(report_dir) -> dict`: it runs every reference dataset and writes `docs/eval/stats-<date>.{md,json}` with each statistic, its published value, the source citation and pass/fail. `--write-config` stores `{"version": STATS_VERSION, "report": path}` only when everything passes.
- `stats_enabled(settings) -> bool`: true only when `settings.stats_validated` exists and its `version == STATS_VERSION`.

- [ ] **Step 1:** Choose at least two published reference datasets with results printed in the source:
  - the BCG vaccine trials (Colditz et al. 1994, as analysed in Viechtbauer 2010, J Stat Softw 36(3), `dat.bcg`): log RR, DL and REML, with τ², I² and the HKSJ CI if published;
  - one continuous-outcome SMD dataset with published Hedges' g pooling, for example Normand 1999 or a dataset in the metafor documentation.

  Copy the data and the published numbers into fixtures, with the citation and the page or table. **Never generate the reference numbers with the code under test.** Verify that the Cochrane Handbook recommendation cited in Deviation 5 matches the current Handbook, and record the section in the report.
- [ ] **Step 2: Failing tests:**
  - `test_effect_sizes_match_formulae` (hand-computable cases);
  - `test_uncomputable_study_is_reported_not_imputed`;
  - `test_bcg_reproduces_published_dl_and_reml`;
  - `test_smd_dataset_reproduces_published_pooling`;
  - `test_hksj_widens_interval_and_uses_t_k_minus_1`;
  - `test_fe_model_has_zero_tau2`;
  - `test_reml_nonconvergence_is_an_error`;
  - `test_forest_svg_escapes_labels_and_is_deterministic`;
  - `test_stats_enabled_requires_current_version` (a changed `STATS_VERSION` disables pooling);
  - `test_eval_write_config_only_when_all_pass`.
- [ ] **Step 3–5:** FAIL, implement, PASS, full suite exit 0, ruff, `uv lock`. **Commit** `feat(stats): validated meta-analysis module`.

### Task 3: V-STATS, V-NUM over artifacts, V-HEDGE gate helper

**Files:**
- Create: `src/rk/verify/stats.py`.
- Modify: `src/rk/verify/numbers.py`, `src/rk/verify/hedge.py`, `src/rk/verify/support.py`.
- Test: `tests/test_vstats.py`, `tests/test_vnum.py`, `tests/test_hedge.py`.

**Interfaces (produces):**
- `check_stats(ps, artifact_id) -> StatsResult(ok, mismatches: list[str])`. It re-reads the extraction rows named by the artifact's `input_hashes`, where each hash is the sha256 of an extraction row's canonical JSON. A changed or missing row is a mismatch.
  - It recomputes with the recorded code path, and requires `code_version == STATS_VERSION` and equal values to 1e-9.
  - For `fig` artifacts it requires the regenerated SVG sha256 to equal the stored one.
  - The result is recorded as `V-STATS` (`pass`/`mismatch`) on the artifact.
- `check_numbers(claim_text, evidence_texts, *, artifact_values: list[dict] = [])`: a number may also match an artifact value, rounded as written in the claim, so "0.62" matches 0.6213.
- `verify_support` handles claims of kind `stat_result`: they get no judges; V-STATS on every `artifact_ids` entry plus V-NUM against those artifacts decide `supported` or `unsupported`, recorded as V-SUPPORT with `{"by": "V-STATS"}`. A `stat_result` claim with no artifact is rejected at `add_claim`.
- `check_claim_hedge(ps, claim_id) -> HedgeResult` wires the existing `check_hedge` (the claim's level ≤ the maximum level of its evidence notes) and adds the universal-quantifier rule: "all", "every", "never", "always" or "none" need ≥ 2 evidence items, and a note that itself states the universal. It is recorded as `V-HEDGE` (`pass`/`overstated`).
- `gate_claims(ps, claim_ids) -> list[str]` gives one failure line per claim that is not `supported`, not V-HEDGE pass, or has an open objection with priority 1.

- [ ] **Step 1: Failing tests:**
  - `test_vstats_passes_on_untouched_inputs_and_fails_on_changed_row`;
  - `test_vstats_fails_on_stale_code_version`;
  - `test_vnum_matches_rounded_artifact_value`;
  - `test_stat_result_claim_needs_an_artifact_and_never_calls_judges`;
  - `test_hedge_overstated_and_universal_rule`;
  - `test_gate_claims_lists_each_problem`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff. **Commit** `feat(verify): V-STATS, artifact-aware V-NUM, claim hedging`.

### Task 4: Synthesize worker

**Files:**
- Create: `src/rk/production/synthesis.py`, `tests/test_synthesis.py`.
- Modify:
  - `src/rk/orchestrator/engine.py` (`default_workers`);
  - `src/rk/roles/arguer.md` (synthesis section);
  - `src/rk/checkpoints.py` (`pooling_approval` in `HARD_HUMAN_KINDS`).

**Interfaces:**
- Consumes:
  - `usable_evidence(ps, attempt)` and `extraction_limitations` from the passed `read_extract` attempt;
  - `frozen_protocol(ps)["body"]` (`extraction_schema`, `synthesis_plan`);
  - `prisma_counts` / `check_selection_reasons`;
  - Task 1–3 interfaces; `vote()`; `create_checkpoint`.
- `SynthesizeWorker.run`:
  1. **Assessment task.** One Arguer task per batch of up to 10 studies. It returns `StudyAssessment{item_id, criteria: {accurate, precise, sufficient, representative, authoritative, clear, relevant: Criterion}, note_ids}`. The kernel caps `sufficient` for abstract-only studies (Global Constraints).
  2. **Method.**
     - **systematic_review with `synthesis_plan.method == "meta_analysis"` and `stats_enabled`:**
       - One Arguer task returns `PoolingPlan{groups: [{outcome, measure, item_ids, comparability: {population, intervention, comparator, outcome, design: Criterion}, comparable: bool, reason}]}`.
       - Each comparable group goes to a P2 `pooling_approval` checkpoint (all groups in one checkpoint) or a P1 vote (conservative option: no pooling).
       - For each approved group the kernel computes the effects from the extraction values, mapped from `extraction_schema` fields by the group's `measure` (explicit field names: `events_treatment`, `n_treatment`, `events_control`, `n_control`, `mean_*`, `sd_*`, `n_*`). Uncomputable studies are listed.
       - The kernel pools with the protocol's model and creates the `stt_` and `fig_` artifacts and the kernel-written `stat_result` claims (origin `synthesis`).
       - A group that is not approved, or not comparable, falls back to narrative and records a protocol deviation with that reason.
     - **Not validated** (`stats_enabled` false): narrative synthesis, plus a deviation "meta-analysis pre-specified but the statistics module is not validated", plus a limitation.
     - **scoping_review:** the kernel builds the evidence map from the category fields (an `stt_` artifact plus a `fig_` table); the Arguer writes the map findings.
     - **narrative / thematic:** one Arguer task returns `NarrativeSynthesis{groups: [{theme_or_outcome, item_ids, direction_summary, certainty_note}], findings: [ClaimDraft{text, kind, evidence: [{note_ids, interpretation}]}], representativeness: str, heterogeneity_note: str}`, following SWiM items 1–9 as fields for systematic reviews.
  3. **Kernel storage and verification.** The kernel stores each finding as a claim (origin `synthesis`) with linked evidence (criteria from step 1) and runs V-SUPPORT through the stage executor (judges exclude the Arguer's vendor), V-NUM and V-HEDGE.
  4. **Representativeness statement.** It is required; it must name the included k and the selection basis (PRISMA counts for SR/scoping, selection reasons otherwise), and the kernel checks it contains the included count.
- **Gate:**
  - every usable study is assessed;
  - every synthesis claim passes `gate_claims`;
  - V-STATS passes on every artifact of the attempt;
  - the representativeness statement is present;
  - fallbacks are recorded as deviations.
  - P2: an uncertain finding opens `uncertain_support` (exists). P1 `degrade` narrows or withdraws (Task 5's helper, introduced here as `narrow_or_withdraw(ctx, claim_id)` in `rk.production.claims`).
  - The `synthesize → read_extract` back-edge is used when the pooling plan finds that a pre-specified outcome's required fields are absent in every study; the guidance names the fields.

- [ ] **Step 1: Failing tests:**
  - `test_assessment_caps_sufficient_for_abstract_only`;
  - `test_meta_analysis_pools_approved_group_with_kernel_claims` (P2 checkpoint answer, then artifacts, a `stat_result` claim and V-STATS pass);
  - `test_p1_pooling_vote_disagreement_means_narrative`;
  - `test_incomparable_studies_fall_back_to_narrative_with_deviation`;
  - `test_pooled_k_excludes_uncomputable_studies`;
  - `test_unvalidated_stats_module_means_narrative_and_deviation`;
  - `test_scoping_review_builds_evidence_map`;
  - `test_critical_review_thematic_synthesis_gate`;
  - `test_representativeness_statement_required`;
  - `test_missing_outcome_fields_go_back_to_read_extract`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff. **Commit** `feat(production): synthesize stage`.

### Task 5: Argue worker

**Files:**
- Create:
  - `src/rk/production/argue.py`;
  - `src/rk/production/claims.py` (`narrow_or_withdraw`, if not created in Task 4);
  - `tests/test_argue.py`.
- Modify:
  - `src/rk/orchestrator/engine.py`;
  - `src/rk/roles/arguer.md`, `src/rk/roles/skeptic.md` (stage sections and output schemas);
  - `src/rk/roles/policy.py` (Skeptic: `rk_claim_list`, `rk_claim_get`, `rk_objection_add`, search tools; Arguer: claim/edge/evidence writers).

**Interfaces:**
- Consumes: the supported synthesis claims (origin `synthesis`), the RQ, the hypotheses, and Task 1–3 interfaces.
- `ArgueWorker.run`, one round being:
  1. **Arguer task.** It returns `ArgumentDraft{main_claim: ClaimDraft, reasons: [{claim: ClaimDraft, supports: "main"|claim_ref, evidence: [...], warrant: Warrant}], responses: [{objection_id, status, claim?: ClaimDraft}]}`.
     - The kernel stores the claims (origin `argument`), the `reason_of`/`evidence_of` edges and the `warrant_of` edges, then verifies V-SUPPORT, V-NUM and V-HEDGE.
     - Claims may reuse synthesis claims by id instead of restating them.
  2. **Skeptic task** (codex vendor by default). It returns `Critique{objections: [{target_id, category, text, priority, source_id?}], warrant_tests: [{edge_id, tests: WarrantTests}], conflicts: [{claim_a, claim_b, why}]}`.
     - The kernel stores the objections and the warrant tests. A failed warrant test becomes a priority-1 `soundness` objection on that warrant.
     - Each conflict between two supported claims gets one P2 `evidence_conflict` checkpoint (options: `a_holds`, `b_holds`, `both_contested`) or a P1 vote, whose conservative option is `both_contested`: both claims are set `contested` and each must carry an objection plus a response.
  3. **Rounds and the fixed point.** The rounds repeat until the Skeptic returns no new objection of priority ≤ 2 and no failed warrant test (the "no hidden weakness" fixed point), or until the open-stage budget is reached. A budget reached with open priority-1 objections is a gate failure.
- **Gate:**
  - every argument claim passes `gate_claims`;
  - every `warrant_of` edge has warrant tests, all ok or answered by a response claim;
  - every objection is `responded` (its response claim supported), `conceded` (entered into the limitations input) or `rejected_invalid` (with a stated reason, priority ≥ 2 only);
  - every `contested` claim has an objection and a response (the structural V-BALANCE input for 5c);
  - the main claim is supported and answers the frozen RQ: an Arguer self-check field `answers_rq: {ok, reason}` plus a judge vote in P1, or the human in P2 via the final `uncertain_support` path; otherwise the `argue → question` back-edge, with the reason.
- **Degrade (P1):** `narrow_or_withdraw` for every uncertain claim, then a re-check of the gate. The limitations input lists the withdrawn and narrowed claims and the conceded objections.
- **`argue → synthesize`** is used when the Arguer reports that the synthesis lacks a finding the main claim needs (`needs_synthesis: str`), passed as guidance.

- [ ] **Step 1: Failing tests:**
  - `test_argument_round_stores_claims_edges_and_verifies`;
  - `test_failed_warrant_test_becomes_priority1_objection`;
  - `test_objections_must_be_answered_for_gate`;
  - `test_contradicting_claims_open_evidence_conflict` (P2) and `test_p1_conflict_vote_marks_both_contested`;
  - `test_p1_uncertain_claim_is_narrowed_or_withdrawn`;
  - `test_skeptic_fixed_point_ends_rounds`;
  - `test_budget_reached_with_open_priority1_objection_fails_gate`;
  - `test_main_claim_not_answering_rq_goes_back_to_question`;
  - `test_argue_back_edge_to_synthesize_with_guidance`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff. **Commit** `feat(production): argue stage with red-team rounds`.

### Task 6: Design worker (research_proposal)

**Files:**
- Create: `src/rk/production/design.py`, `tests/test_design.py`.
- Modify:
  - `src/rk/orchestrator/engine.py`;
  - `src/rk/roles/ideator.md` (design section);
  - `src/rk/checkpoints.py` (`design_approval` in `HARD_HUMAN_KINDS`);
  - `src/rk/tools/builtin.py` and `src/rk/roles/policy.py` (`rk_hypothesis_add` and `rk_design_set` for the Ideator).

**Interfaces:**
- Consumes:
  - the hypotheses of the frozen RQ;
  - the supported argument claims;
  - Task 1's `add_hypothesis`, `set_design` and `freeze_design`.
- `DesignWorker.run`:
  1. **Ideator task.** It returns `DesignDraft{hypotheses: [{id?|new: {statement, falsification, rival_of?}, falsified_by: [measurement_id], tested_by: [design_item_id]}], design: DesignIn, feasibility_claims: [ClaimDraft]}`.
     - The kernel adds the new hypotheses, stores the design, and stores the feasibility claims (kind `feasibility`) and verifies them (V-SUPPORT and friends).
  2. **Structural gate:**
     - every design item links to ≥ 1 hypothesis;
     - every hypothesis is tested by ≥ 1 design item;
     - every hypothesis's `falsified_by` names measurement ids that exist in a design item testing it;
     - every feasibility claim is supported.
  3. **Approval.** When the structure passes: P2 `design_approval` (options approve / revise with comment / back to argue); P1 vote ("is this design able to test and falsify these hypotheses?"; conservative option: not approved → rerun with the judges' reasons as guidance). On approval, `freeze_design`, each hypothesis gets `prereg_hash`, and its status becomes `tested_by_design`.
- **Back-edges:**
  - `design → argue` when the argument cannot motivate a testable hypothesis (the Ideator's `needs_argument`);
  - `design → question` when no hypothesis of the RQ is falsifiable with the available methods (the Ideator's `rq_untestable`).

- [ ] **Step 1: Failing tests:**
  - `test_design_gate_requires_links_both_ways`;
  - `test_unfalsifiable_hypothesis_fails_design_gate`;
  - `test_feasibility_claim_must_be_supported`;
  - `test_p2_design_approval_freezes_and_preregisters_hypotheses`;
  - `test_p1_design_vote_disagreement_reruns_with_reasons`;
  - `test_design_back_edges`;
  - `test_design_only_in_research_proposal`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff. **Commit** `feat(production): design stage for research proposals`.

### Task 7: Tools, CLI and Workbench

**Files:**
- Create: `tests/test_ui_argument.py`.
- Modify:
  - `src/rk/production/tools.py`;
  - `src/rk/roles/policy.py`;
  - `src/rk/cli/main.py`;
  - `src/rk/ui/api.py`, `src/rk/ui/static/app.js`, `src/rk/ui/static/style.css`.

**Interfaces:**
- **Read tools:**
  - `rk_synthesis_get(attempt?)` returns the assessments, groups, artifacts (values, V-STATS verdict) and findings with status;
  - `rk_argument_get()` returns the claims (all origins, with status, hedge and contested flag), edges, warrants with tests, and objections with responses;
  - `rk_design_get()` returns the design, the hypotheses, and the frozen hash.
  - Policy follows the 5a convention: full readers get all three; screeners and the reader get none.
- **CLI:** `rk synthesis show`, `rk argument show [--claim ID]`, `rk design show`, `rk eval stats [--write-config]` (from Task 2).
- **Workbench:**
  - Progress shows a synthesis panel: findings, each study's criteria, and the forest plot shown as `<img>` of the stored SVG served with `image/svg+xml` (never inline SVG).
  - Argument view: a claim tree with status and hedge badges, warrant tests, and objections with responses.
  - Checkpoint cards: `pooling_approval` (groups with their comparability table; a per-group approve/decline choice; Submit enabled when every group is decided), `evidence_conflict` (both claims with their evidence; three options), `design_approval` (design items, hypotheses, falsification links; approve / revise / back).
  - All untrusted text is rendered as text. Answer shapes are exactly what the workers accept, proven by browser → API → worker round trips as in 5a Task 8.
- Real-surface screenshots at 1280 and 760 px are taken and inspected.

- [ ] **Step 1: Failing tests:**
  - `test_pooling_card_answers_reach_the_worker`;
  - `test_conflict_card_answers_reach_the_worker`;
  - `test_design_card_answers_reach_the_worker`;
  - `test_forest_plot_served_as_image_not_inline`;
  - `test_read_tools_policy_5b`.
- [ ] **Step 2–5:** FAIL, implement, PASS, full suite exit 0, ruff, screenshots. **Commit** `feat(ui): synthesis, argument and design in the Workbench`.

### Task 8: Demo and docs

**Files:** create `docs/demo/plan5b-synthesis-argument.md`, `docs/eval/stats-<date>.md` (from Task 2's command); modify `README.md`.

- [ ] **Step 1: Live run.**
  - Continue the Plan 5a demo project (melatonin, systematic review) with `uv run rk run <slug> --until argue`.
  - Anthropic roles run on Claude Haiku 5.5; the judges are the configured cloud judges; no local model.
  - Answer the checkpoints as the stand-in researcher, and say so.
  - Then run a small research_proposal project far enough to show design: reuse its discovery output if a frozen RQ exists, otherwise state that design was demonstrated only with the test fixtures and why.
  - Record:
    - the method chosen and why (meta-analysis or narrative, with deviations);
    - the artifacts and their V-STATS verdicts;
    - the findings and their verdicts;
    - the argument rounds, objections and warrant tests;
    - conflicts;
    - withdrawn and narrowed claims;
    - task counts per role and driver, and wall time;
    - an explicit statement that no task ran on a local model;
    - what failed or was weak.
- [ ] **Step 2:** `rk eval stats --write-config` is run and its report committed. The README gets the section "Synthesis, argument and design".
- [ ] **Step 3:** Full suite (pipefail), ruff, personal-path scan. **Commit** `docs: Plan 5b demo and README`.
