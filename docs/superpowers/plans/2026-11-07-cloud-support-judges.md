# Cloud V-SUPPORT Judges and Free Model Choice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** V-SUPPORT decides from three cloud judges only (Claude Haiku 5.5, GPT-6 Luna, DeepSeek v4.1 flash), with no local layer-1 model. Every role's model, judges included, can be chosen freely, including any model the omp gateway offers.

**Architecture:**
- The local layer 1 (MiniCheck, the probability `p`, the τ thresholds and their calibration) is removed. The judge pool grows from `judge`/`judge_2` to `judge`/`judge_2`/`judge_3`. The verdict is consensus among the admitted judges; the existing vendor rules still apply: a judge's vendor differs from the generator's and from every other admitted judge's.
- `omp_gateway` becomes an ordinary driver, available whenever `omp` is installed; the `Settings.omp_gateway` flag is removed. A model catalog (`rk models`, `/ui/api/models`) lists what each driver can run. The Workbench Settings gets a Models section with one model per role.

**Tech Stack:** Python 3.11, pydantic, typer, the existing `rk.llm` drivers, `omp models --json`, Workbench vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` (§8.1 vendors, §9.1 V-SUPPORT). The amendment (user decision, 2026-11-07) is recorded in Task 4.

## Global Constraints

- **User decision 2026-11-07:**
  - Drop V-SUPPORT layer 1 entirely; the verdict comes from cloud judges only.
  - The judges are Claude Haiku 5.5, GPT-6 Luna and DeepSeek v4.1 flash.
  - Add omp gateway support so users can choose models freely.
  - No local models in the defaults.
- **Vendor rule (spec §8.1), unchanged:**
  - A judge must be of a known, non-human vendor that is not the claim generator's vendor and not another admitted judge's.
  - A driver whose vendor is unknown can never review.
- **`support_min_judges` stays 2:** one cross-vendor vote is not enough to pass.
- **Default judges:**
  - `judge = "codex_cli:gpt-6-luna"` (openai)
  - `judge_2 = "ollama:deepseek-v4.1-flash:cloud"` (deepseek)
  - `judge_3 = "claude_cli:claude-haiku-5-5"` (anthropic)
  - If the live smoke in Task 4 shows `codex_cli` rejects `-m gpt-6-luna`, the default becomes `omp_gateway:openai-codex/gpt-6-luna`, and the doc records why.
- **Tests:** no network, no models (conftest guard).
- **Clean cutover:** no aliases for removed settings, modules, flags or the `support` extra.
- **Public repo:** no personal paths, hostnames or tokens in commits.

## Review Focus

1. **Old `config.toml` with removed keys** (`support_model`, `support_tau_*`, `support_calibration`, `omp_gateway`) → `rk` still starts, ignores them, and drops them on the next save. Test in Task 1 and Task 3.
2. **Old projects with stored V-SUPPORT records that carry `p`/`layer1_reason`, and old `uncertain_support` checkpoints with `p`** → the Workbench, CLI and audit readers still render them. Test in Task 1.
3. **A generator vendor equal to one judge's** (Haiku writes, Haiku judges) → that judge is skipped as `same_or_unknown_vendor`, and the two others decide. Test in Task 1.
4. **An omp-gateway model whose family no vendor rule matches** → the vendor is unknown, the model can still be a generator, it can never be a judge, and the Settings page says so. Test in Task 3.
5. **`omp` not installed, or `omp models --json` failing or slow** → the catalog lists the other drivers, marks omp_gateway unavailable with the reason, and never hangs the Settings page (timeout). Test in Task 3.

---

### Task 1: Three-judge, judges-only V-SUPPORT

**Files:**
- Modify:
  - `src/rk/config.py` (remove `support_model`, `support_tau_hi`, `support_tau_lo`, `support_tau_hi_judged`, `support_tau_lo_judged`, `support_calibration`; add `judge_3` to `DEFAULT_DRIVERS`; set the default judges from Global Constraints; old keys are ignored on load)
  - `src/rk/verify/judge.py` (`JUDGE_ROLES = ("judge", "judge_2", "judge_3")`, used everywhere `("judge", "judge_2")` or `"judge" if i == 0 else "judge_2"` appears)
  - `src/rk/verify/support.py`
  - `src/rk/production/vote.py` (docstring and rule for N admitted judges)
  - `src/rk/discovery/gaps/__init__.py`
  - `src/rk/support_check.py`
  - `src/rk/eval/deepfact.py`
  - `src/rk/roles/policy.py` if roles are enumerated there
  - `src/rk/ui/api.py` (settings fields)
  - `src/rk/ui/static/app.js` (wherever `p` of a support verdict is shown)
- Delete: `src/rk/verify/minicheck.py`, `src/rk/verify/minicheck_bespoke.py`, `tests/test_minicheck.py`, `tests/test_minicheck_bespoke.py`
- Test: `tests/test_support.py`, `tests/test_gaps_support.py`, `tests/test_support_check.py`, the vote tests

**Interfaces:**
- Produces:
  - `combine(opinions: list[JudgeOpinion], *, min_judges: int) -> Verdict`
    - `supported` ⇔ at least `min_judges` valid opinions (`error is None`) and every valid label is `supported`.
    - `unsupported` / `contradicted` ⇔ at least `min_judges` valid opinions all sharing that one label.
    - Otherwise `uncertain`.
    - `check_opinion` keeps the rule that a label needing spans (`NEEDS_SPANS`) without verbatim spans becomes `uncertain`.
  - `SupportResult(verdict, opinions, numbers_ok, ungrounded_numbers, min_judges, reason)`, with no `p`, `layer1_reason`, `n_chunks` or `tau_*`.
  - `verify_support(kernel, registry, ps, claim_id, *, actor, judge_drivers=None, judge_providers=None, min_judges=None, checkpoint=True, run=None) -> SupportResult`, with no `layer1`.
  - V-NUM unchanged.
  - The `uncertain_support` checkpoint options are `{claim_id, opinions, reason}`.
  - `admitted_judges` / `run_judges` iterate `JUDGE_ROLES` and label each task with its own role.
  - P1 vote: the decision is the choice shared by every admitted judge. Fewer than 2 admitted judges, any disagreement, or a failed or invalid answer gives the conservative option.

- [ ] **Step 1: Failing tests** (`tests/test_support.py` and the vote tests):
  - `test_supported_needs_two_agreeing_cross_vendor_judges`: luna and deepseek `supported` with spans, Anthropic generator, haiku also `supported` → `supported`. The haiku opinion has `error == "same_or_unknown_vendor"` and is not counted.
  - `test_one_dissent_makes_it_uncertain`: luna `supported`, deepseek `unsupported` → `uncertain`.
  - `test_shared_negative_label_is_kept`: both `contradicted` → `contradicted`; a split between `unsupported` and `contradicted` → `uncertain`.
  - `test_one_valid_judge_is_not_enough`: deepseek fails (`error="failed"`), luna `supported` → `uncertain`.
  - `test_three_admitted_judges_all_must_agree` (OpenAI generator, so haiku and deepseek are admitted and luna is skipped; with a `human` generator all three are admitted): two `supported`, one `unsupported` → `uncertain`.
  - `test_vote_uses_every_admitted_judge`: three admitted judges, two agree and one differs → conservative option.
  - `test_old_config_keys_are_ignored`: a `config.toml` with `support_model = "…"`, `support_tau_hi = 0.9` and `support_calibration = {…}` loads; after `save_settings` the keys are gone.
  - `test_old_support_records_still_render`: a stored V-SUPPORT record and an `uncertain_support` checkpoint carrying `p` and `layer1_reason` are shown by the claim view / checkpoint API without error.
- [ ] **Step 2:** Run `uv run pytest tests/test_support.py -q` → FAIL.
- [ ] **Step 3:** Implement. Delete the layer-1 modules and their tests, and remove every `layer1` parameter and caller (`gaps`, `support_check`, `deepfact`, tests' fakes). `grep -rn "minicheck\|layer1\|support_model\|tau_hi\|tau_lo" src tests` must then list only the Task 2 files (`eval/support_eval.py`, `eval/metrics.py`, `cli/main.py` eval command).
- [ ] **Step 4:** Focused tests PASS; full suite (pipefail) exit 0; ruff clean.
- [ ] **Step 5: Commit** `feat(verify): V-SUPPORT from three cloud judges, no layer 1`

### Task 2: Support evaluation without layer 1

**Files:**
- Modify:
  - `src/rk/eval/support_eval.py`
  - `src/rk/eval/metrics.py` (remove `calibrate*`, `layer1_predict` and the τ helpers if nothing else uses them)
  - `src/rk/cli/main.py` (`rk eval support`: remove `--layer1-only` and `--write-config`; `--judges` defaults to the configured three)
  - `pyproject.toml` (remove the `support` extra and the `model` marker if no test uses it)
  - `src/rk/eval/LICENSES.md` (remove the MiniCheck rows)
- Test: `tests/test_metrics.py`, the eval tests that touch support

**Interfaces:**
- Produces: `run_support_eval(*, judges: list[DriverSpec], min_judges: int, limit: int | None, workers: int, report_dir: Path, loader=..., judge_providers=None) -> dict`.
  - It runs the judges on SciFact dev claims and writes a report: a verdict × gold matrix, coverage (non-`uncertain` share), the precision of `supported` and of the negative verdicts, and per-judge agreement and failure counts. It does no calibration.
  - Report files are `docs/eval/support-<date>-judges.{md,json}`.
  - The old `docs/eval/support-2026-11-06*` files stay as history.

- [ ] **Step 1: Failing test:** `test_support_eval_reports_judges_only_coverage`.
  - Scripted judge providers on 6 fixture claims: 3 gold `supported`, 2 `contradicted`, 1 not enough info.
  - Expected verdicts: 2 `supported`, 1 `contradicted`, the rest `uncertain`.
  - Assert the matrix, coverage 0.5, and supported precision 1.0.
  - The report has no `tau` or `p`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; full suite exit 0; ruff clean.
- [ ] **Step 5: Commit** `feat(eval): judges-only support evaluation`

### Task 3: omp gateway as a first-class driver and free model choice

**Files:**
- Create:
  - `src/rk/llm/catalog.py`
  - `tests/test_catalog.py`
- Modify:
  - `src/rk/config.py` (remove `omp_gateway: bool`; old key ignored)
  - `src/rk/llm/drivers.py` (omp_gateway available iff `omp` is on PATH; no disabled backend)
  - `src/rk/eval/scratch.py` (drop the flag override)
  - `src/rk/cli/setup.py` (hint text)
  - `src/rk/llm/vendors.toml`
  - `src/rk/cli/main.py` (`rk models`, `rk driver set ROLE DRIVER`)
  - `src/rk/ui/api.py` (`GET /ui/api/models`, `PATCH` drivers with validation)
  - `src/rk/ui/static/app.js`
  - `src/rk/ui/static/style.css`
- Test: `tests/test_catalog.py`, `tests/test_ui_workbench.py` (or a new `tests/test_ui_models.py`)

**Interfaces:**
- Produces:
  - `ModelChoice(driver: str, label: str, vendor: str | None, available: bool, reason: str | None)`.
  - `list_models(settings, *, omp_runner=None) -> list[ModelChoice]`, covering:
    - `claude_cli:<id>` for the catalog's anthropic models;
    - `codex_cli:<id>` for openai-codex models;
    - `ollama:<name>` for `ollama list` models and the `…:cloud` catalog entries;
    - `omp_gateway:<selector>` for every chat model of `omp models --json` (`.models[]`, `kind == "chat"`, `selector`).
  - `omp models --json` runs with a 10 s timeout and its result is cached for 10 min in-process. On a failure or a missing `omp`, one unavailable `omp_gateway` entry carries the reason.
  - `validate_drivers(drivers: dict[str, str]) -> list[str]` returns the problems:
    - an unparsable driver;
    - screeners of the same or an unknown vendor;
    - fewer than 2 judges of distinct known vendors;
    - a judge with an unknown vendor.
    These are the same rules `rk providers` and `screeners_problem` apply; reuse them rather than copying.
  - `vendors.toml` family rules for `omp_gateway:*/` and `ollama:` model ids:
    - `claude*` → anthropic;
    - `gpt*`, `o[0-9]*`, `codex*` → openai;
    - `deepseek*` → deepseek;
    - `gemini*`, `gemma*` → google;
    - `qwen*` → alibaba;
    - `llama*` → meta;
    - `mistral*` → mistral;
    - `kimi*` → moonshot;
    - `glm*` → zhipu;
    - `minimax*` → minimax.
    Matching is by model id after the last `/`. The existing explicit rules stay first.
  - The Workbench Settings page gets a "Models" section:
    - one row per role, with a select of `ModelChoice`s grouped by driver, showing the vendor, plus "Other…" for a free driver string;
    - Save calls `PATCH /ui/api/settings {drivers}`, and the server refuses invalid drivers with the `validate_drivers` problems shown inline;
    - text is rendered as text.

- [ ] **Step 1: Failing tests:**
  - `test_omp_catalog_lists_gateway_models` (`omp_runner` returns a fixture JSON with `anthropic/claude-haiku-5-5`, `openai-codex/gpt-6-luna` and `ollama-cloud/deepseek-v4.1-flash`) → three `omp_gateway:` choices with vendors anthropic/openai/deepseek.
  - `test_omp_missing_or_failing_is_reported_not_raised`, for both a timeout and a missing binary.
  - `test_family_vendor_rules`: `omp_gateway:ollama-cloud/deepseek-v4.1-flash` → deepseek; `omp_gateway:ollama-cloud/kimi-k3` → moonshot; `omp_gateway:foo/unknown-model` → None.
  - `test_unknown_vendor_cannot_judge`.
  - `test_validate_drivers_rejects_duplicate_judge_vendors`.
  - `test_omp_gateway_needs_no_flag` (an old config with `omp_gateway = false` loads and the key is dropped on save).
  - Browser test `test_settings_models_section_saves_a_gateway_judge`: choose `omp_gateway:openai-codex/gpt-6-luna` for `judge`, save, and `load_settings().drivers["judge"]` equals it. An invalid choice shows the problem and saves nothing.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; full suite exit 0; ruff clean; real-surface screenshot of the Models section (1280 and 760 px) inspected.
- [ ] **Step 5: Commit** `feat(llm): omp gateway models and per-role model choice`

### Task 4: Live smoke, spec amendment, docs

**Files:**
- Modify:
  - `docs/superpowers/specs/2026-11-05-research-kernel-design.md` (§9.1 V-SUPPORT row; the Plan 2 row's "V-SUPPORT 两层判定"; §8.1 note that omp_gateway is a normal driver)
  - `README.md` (V-SUPPORT and the model-choice section)
- Create: `docs/eval/support-<date>-judges.md` (from the run)

- [ ] **Step 1: Live smoke (cloud only).**
  - In a scratch `RK_HOME`, run `rk providers` with the three default judges and record whether `codex_cli -m gpt-6-luna` works (otherwise apply the fallback from Global Constraints and record why).
  - Run `rk support-check` on 4 sentences with known answers through `rk serve`.
  - Run `rk eval support --judges omp_gateway:anthropic/claude-haiku-5-5,omp_gateway:openai-codex/gpt-6-luna,omp_gateway:ollama-cloud/deepseek-v4.1-flash --limit 60` (evaluation needs chat-API drivers).
  - Record coverage and precision next to the old layer-1 numbers (Dev100 amended coverage 0.52, supported 10/10). Prove from the task records that no call went to a local model.
- [ ] **Step 2:** Amend the spec rows and the README to match the code.
- [ ] **Step 3:** Full suite (pipefail) exit 0, ruff clean, personal-path scan empty. **Commit** `docs: cloud V-SUPPORT judges and model choice`
