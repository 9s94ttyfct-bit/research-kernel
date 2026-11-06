# Plan 2B — Argument Entities, V-SUPPORT, V-NUM and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claims, evidence, argument edges and objections become first-class, event-sourced entities. Each claim gets a two-layer support verdict:
- **Layer 1:** local MiniCheck probability.
- **Layer 2:** cross-vendor LLM judges whose quoted spans are checked verbatim.

Numbers in claims are grounded by V-NUM. An evaluation harness calibrates the thresholds on external human labels and reports confusion matrices on SciFact, DeepFact-Bench, LitQA2 and ReportBench (citation part). Demo: `rk support check` takes a passage plus sources and prints a per-claim verdict table and confusion matrix. The same command runs with `claude_cli`, `codex_cli`, or `omp_gateway`/`ollama` as the claim-extraction driver.

**Architecture:**
- `rk.argument` holds the four entities (events, projections, tools).
- `rk.verify.support` combines `rk.verify.minicheck` (torch, optional extra `support`) with `rk.verify.judge`, which runs the `judge`/`judge_2` roles through Plan 2A's `run_task`.
- `rk.verify.numbers` is pure Python.
- `rk.eval` has one module per dataset. Each has a loader (cached under `$RK_HOME/eval/`) and a runner that writes a markdown report under `docs/eval/`, plus a JSON result.

**Tech Stack:** Plan 2A stack, plus an optional extra `support = ["torch>=2.15,<3", "transformers>=5.22,<6", "nltk>=3.9,<4", "sentencepiece>=0.2,<1"]`. Datasets are read as plain JSON/JSONL/parquet over `httpx` (`pyarrow>=20` in the `eval` extra). There is no `datasets` dependency, because SciFact on HF is script-only.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` §6.3 (claim, evidence, edge, objection), §8.4 (argument and verification tools), §9.1 (V-SUPPORT, V-NUM), §9.2 G-SUPPORT semantics, §5.3 (`uncertain_support` checkpoint in P2), §10 (support calibration, LitQA2, ReportBench). The facts below were verified on 2026-11-05 against primary sources: MiniCheck `inference.py`/`minicheck.py` on `main`, the HF dataset APIs, and docs.ollama.com.

**Deviations from the spec (decided here, flagged to the user):**
1. **DeepFact-Bench is evaluation-only.** Its `human_verdict` is judged against the broader literature, not against a given passage, so it cannot calibrate τ for "does this evidence support this claim". τ is calibrated on SciFact train (claim + cited abstract, human labels) and reported on SciFact dev, which is held out.
2. **Layer 2 needs ≥2 judges with distinct vendors, each different from the claim generator's vendor** (`support_min_judges = 2`, applying spec §8.1 "一票不足以通过"). Eval runs on the test model may pass `--min-judges 1`, and their reports say so.
3. **LitQA2's PaperQA2+Ollama baseline is not run in this plan:** Ollama Cloud is out of quota (2026-11-05) and no capable local model is installed. The harness records our own scores; the baseline column is filled when a model is available.

## Global Constraints

- All Plan 1 and Plan 2A constraints. The test LLM is `omp_gateway:openai-codex/gpt-6-luna`.
- Pytest markers: `model` (downloads or loads MiniCheck; also excluded by default), plus `live`/`llm`. `addopts = "-m 'not live and not llm and not model'"`.
- **MiniCheck-Flan-T5-Large** (`lytang/MiniCheck-Flan-T5-Large`, MIT), reproduced exactly:
  - input string `"predict: " + chunk + "</s>" + claim`, tokenizer `max_length=2048, truncation=True`;
  - one decoder step with `decoder_input_ids = zeros((B,1))`;
  - `softmax(logits[:, [3, 209]])[:, 1]` = P(supported);
  - chunks: split into sentences (`\n` blocks → `nltk.sent_tokenize`), add whole sentences greedily while the whitespace-word count ≤ 500, never split a sentence; score = **max over chunks**;
  - device `mps` if `torch.backends.mps.is_available()` else `cpu`, `torch_dtype=float32`, `model.eval()`, `torch.no_grad()`;
  - model files cached at `$RK_HOME/models/hf/`; nltk data (`punkt_tab`) at `$RK_HOME/models/nltk/`.
- **Thresholds:** start at τ_hi = 0.85, τ_lo = 0.15. Calibrated values live in `config.toml` under `[support]` with a `calibration` record `{tau_hi, tau_lo, dataset, split, n, precision_supported, coverage, model, rk_version, date}`.
- **Licences** (recorded in `src/rk/eval/LICENSES.md`, with data used only locally for evaluation and never committed):
  - SciFact claims CC BY 4.0 and corpus ODC-By 1.0 per the GitHub `LICENSE.md` (the HF card says CC BY-NC 2.0; both are noted);
  - DeepFact-Bench MIT;
  - LitQA2 (`futurehouse/lab-bench` config `LitQA2`) CC BY-SA 4.0;
  - ReportBench CC0-1.0 (data), Apache-2.0 (code).
- Dataset URLs:
  - SciFact `https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz`;
  - DeepFact-Bench `https://huggingface.co/datasets/kkkevinkkk/DeepFactBench/resolve/main/test.json`;
  - LitQA2 parquet via `https://huggingface.co/api/datasets/futurehouse/lab-bench/parquet/LitQA2/train` (returns the shard URLs);
  - ReportBench `https://raw.githubusercontent.com/ByteDance-BandAI/ReportBench/main/ReportBench_v1.1.jsonl` and `…/ReportBench_v1.1_GT/{arxiv_id}.jsonl`.

## Review Focus

1. **A claim whose number differs from the source only by format** (`1,234` vs `1234`, `12 %` vs `12%`, `−0.5` vs `-0.5`, `3×10⁻³` vs `0.003`) must pass V-NUM. A real change (`12%` → `21%`, `0.3` → `0.03`) must fail. Test: Task 2 `test_vnum_format_equivalence` / `test_vnum_value_change_fails`.
2. **Evidence longer than one chunk, where the supporting sentence sits in the last chunk.** The max-over-chunks score must find it. Test: Task 3 `test_support_in_last_chunk` (model-marked).
3. **A judge that says "supported" but quotes text not in the evidence**, or quotes nothing, is downgraded to `uncertain` for that judge. Test: Task 4 `test_judge_fabricated_quote_downgraded`.
4. **Only one judge reachable, or a judge from the claim generator's vendor**, means the claim can never become `supported`. Test: Task 5 `test_single_judge_never_supported` / `test_same_vendor_judge_excluded`.
5. **torch/transformers not installed, or the model download fails.** V-SUPPORT returns `uncertain` with reason `layer1_unavailable` and records it, instead of crashing or passing. Test: Task 5 `test_layer1_unavailable_is_uncertain`.

---

## File Structure

```
src/rk/argument.py            claim/evidence/edge/objection: models, events, projections, service
src/rk/verify/numbers.py      V-NUM: extract_numbers(), normalize, check_numbers()
src/rk/verify/minicheck.py    chunk_document(), MiniCheck (lazy torch), score()
src/rk/verify/judge.py        JudgeVerdict schema, run_judges()
src/rk/verify/support.py      SupportResult, verify_support(), combine()
src/rk/eval/__init__.py
src/rk/eval/LICENSES.md
src/rk/eval/data.py           download+cache helpers (sha256 recorded), loaders per dataset
src/rk/eval/metrics.py        confusion matrix, precision/coverage, calibrate_thresholds()
src/rk/eval/support_eval.py   rk eval support (SciFact)  → docs/eval/support-<date>.md
src/rk/eval/deepfact.py       rk eval deepfact
src/rk/eval/litqa2.py         rk eval litqa2
src/rk/eval/reportbench.py    citation precision/recall scorer
src/rk/support_check.py       rk support check demo pipeline
src/rk/roles/{arguer,reader,judge}.md   extended for claim extraction / evidence / judging
Modify: tools/builtin.py, roles/policy.py, store/schema.sql, store/projections.py, config.py,
        cli/main.py, pyproject.toml, README.md
tests/: test_argument.py test_vnum.py test_minicheck.py test_judge.py test_support.py
        test_eval_data.py test_metrics.py test_reportbench.py test_support_check.py
        fixtures/eval/  (tiny hand-made samples in each dataset's real schema; no upstream data)
```

---

### Task 1: Argument entities and tools

**Files:** Create `src/rk/argument.py`. Modify `schema.sql`, `projections.py`, `builtin.py`, `roles/policy.py`. Test `tests/test_argument.py`.

**Interfaces — Produces:**
- `ClaimIn(text, kind ∈ {fact, definition, cause, evaluation, policy}, scope_limits: list[str] = [], contested: bool = False)`. The stored claim adds `id (clm_)`, `hedge_level` (computed with Plan 1 `hedge_level`), `status ∈ {draft, supported, uncertain, unsupported, contradicted, withdrawn}` (initially `draft`), and `generator_vendor: str | None`. The vendor comes from the calling task's driver (`CallContext.task_id` → `tasks.driver` → `vendor_of`). It is `"human"` for the cli role.
- `EvidenceIn(claim_id, note_ids: list[str] (≥1, each must exist and belong to the project), criteria: dict[str, {score: int 0–2, reason: str}] = {}, interpretation: str = "")` → `evd_`. Criterion keys are restricted to `accurate, precise, sufficient, representative, authoritative, clear, relevant`.
- `EdgeIn(from_id, to_id, type ∈ {reason_of, evidence_of, warrant_of, objection_to, response_to}, warrant: dict | None)` → `edg_`. Both ends must exist; prefixes are checked (`evidence_of` from `evd_`).
- `ObjectionIn(target_id, category ∈ {problem, soundness, alternative, other_cause, counterexample, definition}, text, source_id: str | None, priority: int = 1)` → `obj_`.
- Events `claim.added`, `claim.status_set`, `evidence.linked`, `edge.added`, `objection.added`, with tables `claims`, `evidence`, `edges`, `objections`.
- `ArgumentService(ps)` with `add_claim`, `link_evidence`, `add_edge`, `add_objection`, `set_claim_status(claim_id, status, *, verification_id)`, `get_claim(id) -> dict` (claim + evidence + note texts), `list_claims()`.
- Tools and roles:
  - `rk_claim_add`, `rk_evidence_link`, `rk_edge_add`: arguer, lead;
  - `rk_objection_add`: skeptic, lead;
  - `rk_claim_get`, `rk_claim_list`: every non-judge role;
  - all of them also cli.

- [ ] **Step 1: Failing tests:**
  - `test_claim_evidence_roundtrip_and_rebuild`.
  - `test_evidence_rejects_unknown_note`.
  - `test_edge_type_prefix_rules`.
  - `test_generator_vendor_from_task` (a fake task row with driver `codex_cli` → `"openai"`; the cli role → `"human"`).
  - `test_status_set_only_via_service` (no tool can set `supported`).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(argument): claim, evidence, edge and objection entities`.

---

### Task 2: V-NUM

**Files:** Create `src/rk/verify/numbers.py`. Test `tests/test_vnum.py`.

**Interfaces — Produces:**
- `Num(value: float, unit: str | None, text: str, span: tuple[int,int])`.
- `extract_numbers(text) -> list[Num]`:
  - recognizes signs (`-`, `−` U+2212, `–` before a digit at a token start), decimals, thousands separators (`,` and thin/narrow spaces when the groups are 3-digit), `e`-notation, and `×10^n` / `x 10^-n` / `× 10⁻ⁿ` (superscripts);
  - a percent sign or `percent`/`per cent` gives unit `%`;
  - en-dash ranges `10–20` yield two numbers;
  - text inside `[[…]]` markers and in citation brackets like `(Smith 2020)` is skipped, and so are years 1800–2100 directly inside parentheses with an author name.
- `check_numbers(claim_text: str, evidence_texts: list[str]) -> NumResult(ok: bool, ungrounded: list[Num])`: every claim number must equal (same unit, or a unit absent on both sides; `abs(a-b) <= 1e-9*max(1,|a|)`) some evidence number.

- [ ] **Step 1: Failing tests:**
  - `test_vnum_format_equivalence`, parametrized pairs, all must pass: (`"1,234 patients"`, `"1234 patients"`), (`"12 %"`, `"12%"`), (`"−0.5"`, `"-0.5"`), (`"3×10⁻³"`, `"0.003"`), (`"2.5e3"`, `"2500"`), (`"10–20 mg"`, `"10 to 20 mg"` → 10 and 20 each present).
  - `test_vnum_value_change_fails`: (`"21%"` vs `"12%"`), (`"0.03"` vs `"0.3"`), (`"1,234"` vs `"1,243"`).
  - `test_vnum_ignores_markers_and_citations`: `"Rates fell [[clm:clm_01ABC]] (Smith 2020)."` → no numbers.
  - `test_vnum_reports_spans`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(verify): V-NUM number grounding`.

---

### Task 3: MiniCheck layer 1

**Files:** Create `src/rk/verify/minicheck.py`. Modify `pyproject.toml` (the `support` extra; markers). Test `tests/test_minicheck.py`.

**Interfaces — Produces:**
- `chunk_document(doc: str, max_words: int = 500) -> list[str]`: pure Python. It takes an injectable sentence splitter (default nltk `sent_tokenize`, with a regex fallback used only in tests).
- `class Layer1Unavailable(Exception)`.
- `MiniCheck(model_id="lytang/MiniCheck-Flan-T5-Large", cache_dir: Path, device: str | None = None)`:
  - loads lazily on the first `score`;
  - raises `Layer1Unavailable(reason)` if torch/transformers are missing or loading fails;
  - is a thread-safe singleton per process (`get_minicheck(settings)`).
- `MiniCheck.score(doc: str, claim: str) -> Layer1Score(p: float, chunk_index: int, n_chunks: int, model: str)`.
- `MiniCheck.score_batch(pairs: list[tuple[str,str]], batch_size: int = 8) -> list[Layer1Score]`.

- [ ] **Step 1: Failing tests:**
  - Offline: `test_chunking_greedy_whole_sentences` (no chunk exceeds 500 words unless it is a single longer sentence; concatenating the chunks preserves the sentence order).
  - Offline: `test_layer1_unavailable_without_torch` (monkeypatch the import to fail → `Layer1Unavailable`).
  - `model`-marked:
    - `test_minicheck_known_pairs`: doc "The patient received 50 mg of drug X daily for two weeks." with claim "The patient took drug X." → p > 0.5; with claim "The patient never received drug X." → p < 0.5.
    - `test_support_in_last_chunk`: 1,200 words of unrelated filler sentences, then the supporting sentence → `n_chunks ≥ 3`, `chunk_index == n_chunks-1`, `p > 0.5`.
    - `test_parity_with_official`: score 5 fixed pairs with our implementation and with the official package in a throwaway env, run as a subprocess: `uv run --no-project --with "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@e9fd26ef8e542ac4021fa9d4493bf915b2bd68dc" --with accelerate python tests/minicheck_official.py`. Assert `|p_ours - p_official| < 1e-3` for every pair.
- [ ] **Step 2:** FAIL. **Step 3:** implement with `AutoTokenizer`/`AutoModelForSeq2SeqLM.from_pretrained(model_id, cache_dir=…)`, `.to(device)`. **Step 4:** offline PASS; `uv sync --extra support && uv run pytest -m model tests/test_minicheck.py` PASS.
- [ ] **Step 5:** Commit `feat(verify): MiniCheck layer-1 support scoring on MPS`.

---

### Task 4: Layer-2 LLM judges

**Files:** Create `src/rk/verify/judge.py`; extend `src/rk/roles/judge.md`. Test `tests/test_judge.py`.

**Interfaces:**
- Consumes `run_task` (2A Task 11; roles `judge`, `judge_2`, no tools), `vendor_of`, Plan 1 `normalize_query`/`build_norm_keys` + `find_verbatim`.
- Produces:
  - `JudgeVerdict(label ∈ {supported, partial, unsupported, contradicted}, quoted_spans: list[str], rationale: str)`.
  - `JudgeOpinion(driver: str, vendor: str | None, label: Literal[...,"uncertain"], raw_label: str | None, spans_ok: bool, task_id: str, error: str | None)`.
  - `run_judges(kernel, registry, *, project, claim_text, evidence_text, generator_vendor, drivers: list[DriverSpec] | None = None) -> list[JudgeOpinion]`:
    - drivers default to `settings.drivers["judge"]`, `settings.drivers["judge_2"]`;
    - a driver whose vendor is `None` or equals `generator_vendor` is **skipped** (opinion with `error="same_or_unknown_vendor"`);
    - two drivers with the same vendor: the second is skipped.
- Span check: a `supported`/`partial` label requires ≥1 quoted span, and every span must be found verbatim (normalized) in `evidence_text`. Otherwise the opinion label becomes `uncertain` (`spans_ok=False`). For `contradicted`, spans are also required and checked.
- The judge prompt gets `<data name="claim">` and `<data name="evidence">` blocks. Its instructions are to judge only from the evidence, and to label `partial` when the claim is broader or stronger than the evidence.

- [ ] **Step 1: Failing tests** (fake provider via `run_task`'s provider override):
  - `test_judge_fabricated_quote_downgraded`.
  - `test_judge_without_spans_downgraded`.
  - `test_same_vendor_judge_excluded` (generator `openai`, judge `codex_cli` → skipped).
  - `test_duplicate_vendor_skipped`.
  - Live `llm`: `test_luna_judge_real_pair` (judge driver `omp_gateway:openai-codex/gpt-6-luna`, generator `"human"`; doc/claim from the Task 3 known pair → `supported`, `spans_ok`; the negated claim → `contradicted` or `unsupported`).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(verify): cross-vendor LLM judges with verbatim span checks`.

---

### Task 5: V-SUPPORT and its integration

**Files:** Create `src/rk/verify/support.py`. Modify `config.py` (`support_tau_hi=0.85`, `support_tau_lo=0.15`, `support_min_judges=2`, `support_model`), `builtin.py` (`rk_verify` gains `target_type`/`verifiers`), `argument.py`. Test `tests/test_support.py`.

**Interfaces — Produces:**
- `SupportResult(verdict ∈ {supported, uncertain, unsupported, contradicted}, p: float | None, layer1_reason: str | None, opinions: list[JudgeOpinion], tau_hi, tau_lo, min_judges)`.
- `combine(p, opinions, *, tau_hi, tau_lo, min_judges) -> verdict`:

```
valid = [o for o in opinions if o.error is None]
labels = {o.label for o in valid}
if p is None: return "uncertain"
if p >= tau_hi and len(valid) >= min_judges and labels == {"supported"}: return "supported"
if p <= tau_lo and len(valid) >= min_judges and labels <= {"unsupported","contradicted"}:
    return "contradicted" if labels == {"contradicted"} else "unsupported"
return "uncertain"
```
- `verify_support(kernel, registry, ps, claim_id, *, actor) -> SupportResult`:
  1. evidence text = the evidence notes' verified text (quote → `text`, paraphrase → `source_span`), joined with `"\n\n"`;
  2. layer 1 on (evidence, claim text);
  3. judges;
  4. combine;
  5. `record_verification(target_type="claim", verifier="V-SUPPORT", verdict, details)`;
  6. `set_claim_status`;
  7. also runs V-NUM on (claim, evidence texts) and records `V-NUM`. An ungrounded number forces the claim status to `uncertain` even when the support verdict was `supported`.
  8. When the project meta `production_mode` is `"collab"` (the default when absent) and the verdict is `uncertain`, create checkpoint `uncertain_support` (2A Task 10) with the claim, p and opinions.
- `rk_verify(project, target_type ∈ {item, claim}, target_id, verifiers?)`: the `item` path stays the Plan 1 behaviour.
- A claim without evidence → `uncertain` with reason `no_evidence`.

- [ ] **Step 1: Failing tests** (fake MiniCheck and fake judges injected):
  - `test_combine_truth_table`: parametrized over p ∈ {0.9, 0.5, 0.1} × judge label sets × min_judges ∈ {1,2}.
  - `test_single_judge_never_supported` (min_judges 2).
  - `test_layer1_unavailable_is_uncertain`.
  - `test_ungrounded_number_blocks_supported`.
  - `test_uncertain_creates_checkpoint_in_collab`.
  - `test_verification_records_and_status_rebuild`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(verify): V-SUPPORT two-layer verdicts integrated with claims`.

---

### Task 6: Evaluation data loaders

**Files:** Create `src/rk/eval/{__init__,data}.py`, `src/rk/eval/LICENSES.md`, `tests/fixtures/eval/*`. Test `tests/test_eval_data.py`.

**Interfaces — Produces:**
- `fetch(url, dest: Path) -> Path`: download once into `$RK_HOME/eval/<name>`, record sha256 in `manifest.json`, never re-download if present.
- Loaders (each with an injectable path so tests use the fixtures):
  - `load_scifact(split ∈ {train, dev}) -> list[SupportPair(id, claim, evidence, label ∈ {supported, contradicted, unsupported})]`. One pair per (claim, cited doc). `evidence` = title + "\n" + abstract sentences joined with " ". The label is `SUPPORT` → supported, `CONTRADICT` → contradicted; a cited doc with no evidence entry → unsupported. Requesting `test` raises `ValueError("test split is unlabeled")`.
  - `load_deepfact() -> list[DeepFactClaim(id, sentence, report_id, domain, gold ∈ {supported, contradictory, inconclusive})]`. The id is `f"{report_id}:{sentence_idx}"`.
  - `load_litqa2() -> list[LitQAItem(id, question, ideal, distractors, sources, is_opensource)]`.
  - `load_reportbench_gt(arxiv_id) -> list[str]` (titles).
- Fixtures: 3–5 hand-written records per dataset in the exact upstream schemas listed in the fact sheet (no upstream text copied).

- [ ] **Step 1: Failing tests:**
  - `test_scifact_label_mapping` (all three labels from the fixtures).
  - `test_scifact_test_split_refused`.
  - `test_deepfact_fields`.
  - `test_litqa2_fields`.
  - `test_fetch_caches_and_records_sha` (MockTransport: called once for two `fetch` calls).
  - Live: `test_real_downloads_parse` (each loader returns > 0 rows; SciFact dev pairs > 300; DeepFact 621; LitQA2 199).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(eval): dataset loaders for SciFact, DeepFact-Bench, LitQA2, ReportBench`.

---

### Task 7: Metrics, calibration, `rk eval support`

**Files:** Create `src/rk/eval/{metrics,support_eval}.py`. Modify `cli/main.py`, `config.py`. Test `tests/test_metrics.py`.

**Interfaces — Produces:**
- `confusion(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, dict[str,int]]`.
- `support_metrics(gold, pred) -> {precision_supported, coverage, n}`. `coverage` = share of predictions ≠ `uncertain`; `precision_supported` = among predicted `supported`, the share whose gold is `supported`.
- `calibrate_thresholds(ps: list[float], gold: list[str], *, target_precision=0.95) -> (tau_hi, tau_lo)`. Search τ_hi over the sorted unique p values; choose the smallest τ_hi such that precision_supported(p ≥ τ_hi) ≥ target on the calibration set. τ_lo is symmetric: the largest τ_lo such that ≥ target of the pairs with p ≤ τ_lo have gold ≠ supported. If no τ_hi reaches the target, return τ_hi = 1.0 (never supported) and flag it.
- `rk eval support [--calib-split train] [--eval-split dev] [--limit N] [--judges SPEC,…] [--min-judges K] [--layer1-only] [--write-config]`:
  1. compute p for calibration (cached in `$RK_HOME/eval/scores-<model>-<split>.jsonl`);
  2. calibrate;
  3. on the eval split, layer-1-only metrics for all pairs, and two-layer metrics on a seeded random sample (`--limit`, default 100, seed 13) using the judges;
  4. write `docs/eval/support-<YYYY-MM-DD>.md`: config, τ, confusion matrices (layer-1-only and two-layer), the targets (precision ≥0.95, coverage ≥0.70) marked MET/MISSED, and the judge drivers plus min_judges;
  5. with `--write-config`, store τ and the calibration record in `config.toml`.

- [ ] **Step 1: Failing tests:**
  - `test_confusion_and_metrics` (hand-computed).
  - `test_calibrate_reaches_target_on_separable_data`.
  - `test_calibrate_unreachable_returns_one`.
  - `test_eval_support_report_written` (fixtures + fake MiniCheck + fake judges → the report file contains both matrices and `MET`/`MISSED`).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Real run** (`model` + `live` + `llm`): `uv run rk eval support --limit 100 --judges omp_gateway:openai-codex/gpt-6-luna --min-judges 1 --write-config`. Commit the generated `docs/eval/support-*.md` (no dataset text in it, only numbers).
- [ ] **Step 6:** Commit `feat(eval): support calibration and confusion-matrix reports`.

---

### Task 8: `rk eval deepfact` and `rk eval litqa2`

**Files:** Create `src/rk/eval/{deepfact,litqa2}.py`. Test `tests/test_eval_agents.py`.

**Interfaces:**
- Consumes `run_task`, a temp project, the Plan 1 search/fetch tools, V-SUPPORT.
- Produces:
  - `run_deepfact(kernel, registry, *, limit=30, seed=13, driver)`. For each sampled claim:
    1. a `scout` task (search tools) adds ≤5 candidate works to a temp project;
    2. a `reader` task fetches full text and adds quote notes as evidence;
    3. a claim is created with that evidence, then `verify_support`.

    The prediction maps `supported` → supported, `contradicted` → contradictory, and anything else → inconclusive. The report holds a 3×3 confusion matrix vs `human_verdict` and the per-domain counts.
  - `run_litqa2(kernel, registry, *, limit=20, seed=13, driver)`. A `reader`-role task with search/fetch/text tools gets the question and the shuffled options (ideal, distractors, and "Insufficient information to answer"). The output is `{choice_index, evidence_note_ids}`. Metrics:
    - accuracy = correct / n;
    - precision = correct / answered, where "insufficient" counts as not answered;
    - coverage.

    The report includes an empty "PaperQA2 baseline" column marked `not run (no model available 2026-11-05)`.
  - CLI `rk eval deepfact|litqa2 [--limit] [--driver]`, both writing `docs/eval/<name>-<date>.md`.

- [ ] **Step 1: Failing tests** (fake providers and fixtures):
  - `test_deepfact_mapping_and_report`.
  - `test_litqa2_scoring_counts_insufficient_as_unanswered`.
  - `test_option_shuffle_seeded`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Real runs** (`live` + `llm`, driver `omp_gateway:openai-codex/gpt-6-luna`): `rk eval litqa2 --limit 20` and `rk eval deepfact --limit 10`. Commit the reports.
- [ ] **Step 6:** Commit `feat(eval): DeepFact-Bench and LitQA2 agent harnesses`.

---

### Task 9: ReportBench citation scorer

**Files:** Create `src/rk/eval/reportbench.py`. Test `tests/test_reportbench.py`.

**Interfaces — Produces:**
- `normalize_title(t) -> str`: lowercase, remove `string.punctuation`, collapse whitespace (upstream `related_work_evaluator.py` L534-544).
- `citation_pr(pred_titles: list[str], gt_titles: list[str]) -> {precision, recall, n_pred, n_gt, n_match}`: exact set intersection of the normalized titles.
- `score_reports(dir: Path) -> report`: micro and macro averages. Each `{arxiv_id}.json` holds `response` text plus an optional `cited_titles`. When `cited_titles` is missing, titles are resolved from the DOIs/arXiv ids in the text via Plan 1 `resolve`, so Firecrawl/LLM extraction is not needed.
- CLI `rk eval reportbench --reports DIR`.

- [ ] **Step 1: Failing tests:**
  - `test_normalize_title_matches_upstream_rule`.
  - `test_citation_pr_hand_computed`.
  - `test_micro_macro_average`.
  - `test_titles_resolved_from_dois` (respx-mocked Crossref).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(eval): ReportBench citation precision/recall scorer`.

---

### Task 10: Demo — `rk support check`

**Files:** Create `src/rk/support_check.py`; extend `roles/arguer.md` and `roles/reader.md`. Modify `cli/main.py`, `README.md`. Test `tests/test_support_check.py`.

**Interfaces — Produces:** `support_check(kernel, registry, *, project, text: str, source_ids: list[str], driver: DriverSpec, gold: dict[int, str] | None) -> CheckReport`. Pipeline:
1. An `arguer` task (driver) splits the text into atomic claims, output `{claims: [{text, kind, source_ids}]}`, and the claims are created via the `rk_claim_add` tool (`generator_vendor` = driver vendor).
2. For each claim, a `reader` task (driver) finds supporting passages in the given sources with `rk_text_find`/`rk_text_read`, adds quote notes, and links evidence via `rk_evidence_link`.
3. `verify_support` per claim.
4. Report rows: claim, verdict, p, judge labels, evidence locators, V-NUM. With `gold` (claim index → label), add a confusion matrix.

CLI: `rk support check --project P --text FILE --sources src_a,src_b [--driver SPEC] [--gold gold.json] [--json]`.

- [ ] **Step 1: Failing tests** (fake providers scripted to call real tools):
  - `test_support_check_end_to_end_offline` (two claims: one quoted verbatim from `two_pages.pdf` → fake layer 1 = 0.95, fake judges supported → `supported`; one with a wrong number → `uncertain` via V-NUM).
  - `test_generator_vendor_drives_judge_exclusion` (driver `codex_cli` → the codex judge is skipped).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Demo run** (`live` + `llm` + `model`): a 4-sentence paragraph about the PeerJ quotation-accuracy paper (two sentences faithful, one overstated, one with a changed percentage) with a gold file. Run with `--driver omp_gateway:openai-codex/gpt-6-luna`, `--driver claude_cli` and `--driver codex_cli`. Judges: `judge` = `omp_gateway:openai-codex/gpt-6-luna` and `judge_2` = `claude_cli`. Same-vendor exclusion applies per run, so a run whose generator is OpenAI keeps only the Claude judge and is reported with `--min-judges 1` noted. Record the three tables in `docs/eval/support-check-demo-<date>.md`. `--driver ollama:…` is documented and executed when the Ollama quota allows; the report states whether it ran.
- [ ] **Step 6:** Commit `feat: rk support check demo across drivers`; update the README (V-SUPPORT, eval commands, licences).
