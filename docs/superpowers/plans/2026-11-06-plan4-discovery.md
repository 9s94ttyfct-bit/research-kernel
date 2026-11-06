# Plan 4 — Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project runs `intake → landscape → gaps → topics → question` on one stage state machine, either guided by the Lead in the Workbench chat (D1) or autonomously with `rk run <project> --until topics|question` (D2). Topics need your `topic_review` in every mode. The stage ends with a frozen RQ whose text hash is in the event log.

**Architecture:**
- `rk.discovery` holds the artifacts and the stage workers:
  - `store`: events, projections and queries for intake, search log, clusters, gaps, topics, RQs, hypotheses and exploration rounds;
  - `sources` / `executor`: injectable facades over the source APIs and `run_task`;
  - `landscape`, `gaps/*`, `topics`, `question`, `already_done`: one module per worker;
  - `tools`: the discovery and run tools.
- `rk.orchestrator` holds the stage graph, the gates and the engine (stage attempts, back-edges, budgets, checkpoint waits) plus `RunManager` (one thread per running project; resumes from the event log when `rk serve` starts).
- `rk.verify.novelty` is V-NOVELTY (reference only).
- Every stage run is an **attempt**. Artifacts carry their stage attempt, and gates and the UI read only the latest attempt. Back-edges, reruns and restarts therefore never mix old and new artifacts.
- Role tasks return structured output (pydantic schemas). The kernel writes entities only after the deterministic checks, so no role can store an unverified gap.

**Tech Stack:** Plan 1–3 stack. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` §5.1, §5.2, §5.3 (who answers checkpoints), §6.2–6.3, §7.1 (rows intake–question), §7.2, §7.3 (graph per paper type), §8.1 (roles), §8.3 (all of it), §8.4 (status/stage tools), §9.1 V-NOVELTY, §12 Plan 4.

**Deviations (decided here, flagged to the user):**
1. **Roles do not call `gap_add` / `topic_add` / `rq_propose` / `hypothesis_add`.** GapMiner and Ideator return structured output; the kernel runs the checks and writes the entities. A role cannot bypass a check.
2. **Back-edge limit in discovery asks you in both modes.** Spec §7.2 says P1 then degrades by deleting or narrowing content. Before `protocol` there is no content to delete, so the run pauses with a `stage_blocked` checkpoint (options `allow_one_more`, `stop`).
3. **Clusters are formed by the Scout from titles and abstracts (LLM), not by embeddings.** Trends come from OpenAlex `group_by=publication_year` counts for each cluster's query.
4. **Stated limitations: "last pages" means the quote starts in the last 40% of the text layer.**
5. **Citation distortion: the evidence from the cited work B is chosen by the kernel** (top-5 passages by lexical similarity to the claim), not by the miner, so a miner cannot cherry-pick.

## Global Constraints

- All Plan 1–3 constraints. Git identity repo-local `9s94ttyfct-bit`; no personal paths or hostnames in commits or fixtures.
- **Tests:** no network and no models. Role tasks go through `TaskExecutor` (tests use `ScriptedExecutor`); sources go through `DiscoverySources` (tests use `FakeSources`). Real drivers and APIs only in the Task 12 demo.
- **Stage order** (§7.3): `intake → landscape → gaps → topics → question` for every paper type. The later stages per type are listed in the stage graph and shown as "Plan 5", never runnable.
- **Back-edges** (§7.1): `gaps → landscape`, `topics → gaps`, `question → topics`; a rerun of the same stage counts as the edge `X → X`. Each edge is allowed **2** times per project (§7.2); the third opens `stage_blocked`.
- **Budgets** (§7.2): role-task retries 3 (already in `run_task`); at most **40 role tasks per open stage (landscape, gaps, topics) per run session**; wall clock **12 h per run session** → `paused_budget`. `rk run resume` starts a new session. One run per project at a time. D1 calls have no budget.
- **Run states:** `running`, `waiting_human` (a task waits; other work continues), `paused_waiting_human` (nothing else to do), `paused_budget`, `paused` (you paused), `done`, `failed`, `cancelled`.
- **Checkpoints:** `topic_review` in every mode, D1 and D2 (hard, human only). `rq_approval`: D1 always; D2 only in P2 (`production_mode = "collab"`). In D2 P1 the RQ is adopted automatically and `rq.frozen` records `adopted_by = "agent"` for the disclosure ("RQ selected by agent"). Already-done disagreement in P2 → `significance_novelty` (hard). Agents can never answer hard kinds (existing policy).
- **Project modes** (meta keys written by `project.intake`): `discovery_mode ∈ {d1, d2}`, `production_mode ∈ {auto, collab}` (P1 = auto, P2 = collab; `verify/support.py` already reads `collab`), `audience`, `paper_type ∈ {systematic_review, scoping_review, critical_review, research_proposal}`, `language` (default `en`), `topics_n` (default **6**).
- **Gap `verified`** (§8.3) means only "the evidence chain was checked". In text it is always worded "not found within the searched scope", never "novel". V-NOVELTY never outputs "novel".
- **Landscape gate:** every cluster of the latest attempt has **≥ 5** items with `ref_status = "pass"`, and every cluster item appears in a logged search result of that attempt.
- **Already-done check** (§8.3): **≥ 3** queries over **≥ 2** sources; top-**10** works by reciprocal-rank fusion; two judges of **two different vendors** (`run_judges` vendor rules), each judgement with a verbatim quote from the work's title or abstract.
- **RQ freeze:** `rq.frozen` payload `text_hash = sha256(canonical_json({rq fields, hypotheses}))`.
- **Search log:** every search the kernel runs for discovery writes `search.logged` (query, source, limit, result item ids, purpose, stage, attempt).

## Review Focus

1. **`rk serve` restarts while a D2 run waits on `topic_review`.** After the restart the run is `paused_waiting_human`, and your answer resumes it at `question`: no landscape or gap task runs again. Test: Task 9 `test_run_resumes_after_restart_without_rerunning_passed_stages`.
2. **A miner returns a quote that is not verbatim, or an item id that is not in the library.** The gap is stored with `verified = false` and the failed check named, and topics never cite it. Tests: Task 5 `test_unverbatim_limitation_quote_is_not_verified`, Task 7 `test_topics_drop_unverified_gap_ids`.
3. **You answer `topic_review` with "revise" three times.** The third revise exceeds the `topics → topics` limit. The run pauses on `stage_blocked` and does not loop. Test: Task 7 `test_third_revise_opens_stage_blocked`.
4. **Only one judge vendor is available (codex missing).** "Not found" needs both vendors, so in P1 the RQ is treated as already done and goes back to `topics` (and hits the limit, then `stage_blocked`). In P2 you get a `significance_novelty` checkpoint. The run never claims "not found". Test: Task 8 `test_single_judge_vendor_never_clears_already_done`.
5. **No S2 key.** V-NOVELTY and the already-done check fall back to OpenAlex + Europe PMC and record which sources were used. Test: Task 7 `test_novelty_without_s2_uses_two_other_sources`.

---

## File Structure

```
src/rk/ids.py                         + prefixes cls (cluster), xpr (exploration round)
src/rk/store/projections.py           register rk.discovery.store, rk.orchestrator.state (PROJECTION_MODULES/TABLES)
src/rk/store/schema.sql               new tables (below)
src/rk/discovery/__init__.py
src/rk/discovery/models.py            entities + role output schemas
src/rk/discovery/store.py             events, projections, queries
src/rk/discovery/sources.py           DiscoverySources protocol, KernelSources
src/rk/discovery/executor.py          TaskExecutor protocol, KernelExecutor (budget + run.task events)
src/rk/discovery/passages.py          top_passages()
src/rk/discovery/landscape.py         landscape worker + gate
src/rk/discovery/gaps/__init__.py     gaps worker (runs the five miners) + gate
src/rk/discovery/gaps/limitation.py   stated_limitation
src/rk/discovery/gaps/retraction.py   retracted_still_cited
src/rk/discovery/gaps/maphole.py      evidence_map_hole
src/rk/discovery/gaps/contradiction.py
src/rk/discovery/gaps/distortion.py   citation_distortion
src/rk/discovery/topics.py            topics worker, topic_review handling, gate
src/rk/discovery/question.py          RQ worker, structural gate, freeze, rq_approval handling
src/rk/discovery/already_done.py      "already done?" check
src/rk/discovery/tools.py             discovery + run tools
src/rk/verify/novelty.py              V-NOVELTY
src/rk/verify/support.py              verify_support(..., checkpoint=True)
src/rk/orchestrator/__init__.py
src/rk/orchestrator/graph.py          stage lists per paper type, back-edges
src/rk/orchestrator/state.py          stage/run events, projections, queries
src/rk/orchestrator/engine.py         Orchestrator
src/rk/orchestrator/runs.py           RunManager
src/rk/roles/policy.py, roles/*.md    tool policy; lead/scout/gapminer/ideator/judge prompt updates
src/rk/cli/main.py                    rk project intake, rk stage status, rk run …, rk topic list
src/rk/server/app.py, cli/main.py     RunManager wiring in serve
src/rk/ui/api.py, ui/static/*         stage graph, run controls, topic review and RQ cards
tests/discovery_fakes.py              ScriptedExecutor, FakeSources, make_project()
tests/test_discovery_store.py, test_discovery_landscape.py, test_gaps_*.py, test_topics.py,
tests/test_question.py, test_orchestrator.py, test_runs.py, test_ui_discovery.py
```

New tables (all `CREATE TABLE IF NOT EXISTS`, all in `PROJECTION_TABLES`):
`search_log(id, stage, attempt, purpose, source, query, limit_n, item_ids JSON, ts)`,
`clusters(id, attempt, label, description, query, item_ids JSON, representative_ids JSON, year_counts JSON)`,
`gaps(id, attempt, kind, statement, cluster_id, evidence JSON, details JSON, verified INT, checks JSON, created)`,
`topics(id, attempt, title, summary, gap_ids JSON, closest JSON, feasibility, data_needed, paper_type, risks, status, created)`,
`rqs(id, attempt, topic_id, body JSON, already_done JSON, status, text_hash, adopted_by, created)`,
`hypotheses(id, rq_id, statement, falsification)`,
`exploration_rounds(id, n, stage, feedback, created)`,
`stages(stage, status, attempt, passed_attempt, last_gate JSON, updated)`,
`back_edges(edge, count, allowed)`,
`runs(id, status, until, session, session_started, started, ended, reason)`,
`run_tasks(task_id, run_id, session, stage, attempt)`.

---

### Task 1: Discovery store and intake

**Files:**
- Create: `src/rk/discovery/{__init__,models,store}.py`, `tests/test_discovery_store.py`, `tests/discovery_fakes.py` (only `make_project()` now)
- Modify: `src/rk/ids.py`, `src/rk/store/schema.sql`, `src/rk/store/projections.py`, `src/rk/tools/builtin.py` (or `discovery/tools.py` registered from it), `src/rk/roles/policy.py`, `src/rk/cli/main.py`

**Interfaces:**
- Produces (`rk.discovery.models`): `Intake(audience: str, paper_type: PaperType, discovery_mode: Literal["d1","d2"], production_mode: Literal["auto","collab"], language: str = "en", topics_n: int = 6)`; `Cluster`, `Gap(kind: GapKind, statement, cluster_id: str|None, evidence: GapEvidence, details: dict)`, `GapEvidence(note_ids: list[str], item_ids: list[str], claim_ids: list[str])`, `GapKind = Literal["contradiction","stated_limitation","citation_distortion","retracted_still_cited","evidence_map_hole"]`, `Topic`, `ClosestWork(item_id, title, year, differences: dict[str, str])`, `RQBody(three_step: ThreeStep(x, y, z), problem: Problem(condition, consequences: list[str]), finer: Finer(feasible, interesting, novel, ethical, relevant), clinical: bool, pico: Pico|None)`, `HypothesisIn(statement, falsification)`.
- Produces (`rk.discovery.store`), each function writes one event and returns the new id:
  - `record_intake(ps, intake, *, actor, actor_kind) -> None` (event `project.intake`, meta keys = the `Intake` fields)
  - `intake_of(ps) -> Intake | None`
  - `log_search(ps, *, stage, attempt, purpose, source, query, limit, item_ids, actor) -> str`
  - `add_cluster(ps, attempt, cluster, *, actor) -> str`, `add_gap(ps, attempt, gap, *, verified: bool, checks: dict, actor) -> str`, `add_topic(ps, attempt, topic, *, actor) -> str`, `set_topic_status(ps, topic_id, status: Literal["candidate","approved","rejected"], *, actor, actor_kind)`
  - `add_rq(ps, attempt, topic_id, body, hypotheses: list[HypothesisIn], *, actor) -> str`, `set_already_done(ps, rq_id, result: dict, *, actor)`, `freeze_rq(ps, rq_id, *, adopted_by: Literal["human","agent"], actor) -> str` (returns the hash)
  - `add_round(ps, stage, feedback, *, actor) -> str`
  - Queries: `clusters(ps, attempt)`, `gaps(ps, attempt, verified: bool|None=None)`, `topics(ps, attempt)`, `rqs(ps, attempt)`, `hypotheses(ps, rq_id)`, `rounds(ps)`, `searches(ps, stage, attempt)`, `guidance(ps, stage) -> str` (all rounds' feedback, newest first).
- Tool `rk_project_intake(project, audience, paper_type, discovery_mode, production_mode, language="en", topics_n=6)`, writes, roles lead + cli. CLI `rk project intake --audience … --paper-type … --mode d1|d2 --production p1|p2`.

- [ ] **Step 1: Write failing tests** in `tests/test_discovery_store.py`:
  - `test_intake_sets_meta_and_round_trips`: after `record_intake`, `ps.meta("production_mode") == "collab"` and `intake_of(ps) == intake`.
  - `test_rebuild_restores_discovery_tables`: add one of each entity, `rebuild(ps.db)`, then every query returns equal rows.
  - `test_freeze_hash_is_stable_and_logged`: `freeze_rq` returns `sha256(canonical_json({...body, "hypotheses": [...]}))`, the `rq.frozen` event payload has the same `text_hash`, and a second freeze raises `ValueError("rq already frozen")`.
  - `test_queries_read_one_attempt`: gaps added in attempts 1 and 2; `gaps(ps, 2)` returns only attempt 2.
  - `test_intake_tool_policy`: `rk_project_intake` is allowed for lead and cli, refused for scout.
- [ ] **Step 2:** Run `uv run pytest tests/test_discovery_store.py -q` — FAIL (module missing).
- [ ] **Step 3:** Implement models, store, tables, prefixes `cls` and `xpr`, projection registration, the tool, policy and CLI.
- [ ] **Step 4:** Run the test file, then `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(discovery): intake and discovery artifact store`

### Task 2: Sources, executor and passages

**Files:**
- Create: `src/rk/discovery/{sources,executor,passages}.py`, `tests/test_discovery_io.py`; extend `tests/discovery_fakes.py` with `ScriptedExecutor`, `FakeSources`
- Modify: `src/rk/orchestrator/state.py` (create now with only the `run.task` event and table `run_tasks`; Task 3 adds the rest)

**Interfaces:**
- `DiscoverySources` (Protocol):
  - `search(query: str, source: str, limit: int = 25) -> list[ItemIn]`
  - `sources() -> list[str]`: usable sources in preference order: `openalex`, then `s2` only when `s2_key` is set, then `europepmc`, `pubmed`, `arxiv`
  - `year_counts(query: str) -> dict[int, int]`: OpenAlex `group_by=publication_year`
  - `verify(item_id: str) -> str`: runs `verify_item`, returns `ref_status`
  - `cited_by(item_id: str, after_year: int | None) -> list[ItemIn]`
  - `retraction(item_id: str) -> RetractionInfo`
  - `fulltext(item_id: str) -> bool`: API channels, then the browser fallback when running
- `KernelSources(kernel, project, browser=None)` implements it over `resolver`, `openalex`, `verify_item`, `fulltext`.
- `TaskExecutor` (Protocol): `run(role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str, attempt: int, tools: list[str] | None = None) -> TaskResult`.
- `KernelExecutor(kernel, registry, project, *, run_id: str | None, session: int | None, max_tasks_per_stage: int = 40)`: wraps `run_task`. After each task it writes `run.task`. Before a task in an open stage it raises `BudgetExceeded(stage, used, limit)` when `used ≥ 40` for (run_id, session, stage). With `run_id=None` (D1) there is no budget. A result with error `rate_capped` raises `RateCapped(driver, wait_s)`, where `wait_s` is the time until the driver's oldest call in `CallCaps` leaves the 3600 s window.
- `ScriptedExecutor(script: dict[tuple[str, str], list[dict]], *, drivers: dict[str, str] | None = None)`: key `(role, schema.__name__)`, pops outputs in order and records calls. It returns `TaskResult(ok=True, output=…, provider=…, model=…)` with provider/model from `drivers` (e.g. `{"judge": "codex_cli", "judge_2": "ollama:deepseek-v4.1-flash:cloud"}`), so `vendor_of` gives the vendors.
- `FakeSources(items: dict[str, list[ItemIn]], …)`: deterministic search by query substring; `verify` returns preset statuses.
- `top_passages(ps, item_id: str, query: str, k: int = 5) -> list[Passage(text, page, start, end)]`: 3-sentence windows of the latest text layer ranked by `rapidfuzz.fuzz.token_set_ratio(query, window)`, ties by position; texts are exact substrings of the layer.
- `log_and_search(ps, sources, *, stage, attempt, purpose, query, source, limit, actor) -> list[str]`: searches, adds the hits to the library (collection "Search hits"), logs, and returns item ids.

- [ ] **Step 1: Failing tests** in `tests/test_discovery_io.py`:
  - `test_budget_stops_the_41st_task_in_an_open_stage`: `KernelExecutor` with a fake provider; 40 tasks pass in `gaps`, the 41st raises `BudgetExceeded`; `question` is not budgeted; `run_id=None` is not budgeted.
  - `test_run_task_events_link_task_to_stage`: after one task, `run_tasks` has `(task_id, run_id, session, "landscape", 1)`.
  - `test_sources_order_without_s2_key`: `KernelSources(...).sources()[:2] == ["openalex", "europepmc"]` when `s2_key` is unset.
  - `test_top_passages_are_verbatim`: every passage text appears in the layer text at `[start:end]`; the passage containing the query sentence ranks first.
  - `test_log_and_search_logs_and_adds`: the returned ids are library items, and `searches(ps, "landscape", 1)` has the query with those ids.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the file and the full suite — PASS.
- [ ] **Step 5: Commit** `feat(discovery): source and task facades with stage budgets`

### Task 3: Orchestrator engine

**Files:**
- Create: `src/rk/orchestrator/{__init__,graph,engine}.py`, `tests/test_orchestrator.py`
- Modify: `src/rk/orchestrator/state.py`

**Interfaces:**
- `graph.py`: `DISCOVERY = ("intake","landscape","gaps","topics","question")`; `stages_for(paper_type) -> list[str]` (§7.3 sequences, discovery first); `BACK_EDGES = {("gaps","landscape"), ("topics","gaps"), ("question","topics")}`; `EDGE_LIMIT = 2`; `OPEN_STAGES = {"landscape","gaps","topics"}`.
- `state.py` events and queries:
  - events: `stage.attempt` (stage, attempt, run_id, guidance), `stage.gate` (stage, attempt, ok, failures), `stage.passed` (stage, attempt), `stage.reopened` (stage, by_edge), `edge.taken` (edge, count), `edge.allowed` (edge), `run.started` (until, discovery_mode), `run.status` (status, reason), `run.session` (n)
  - queries: `stage_rows(ps)`, `current_stage(ps) -> str | None` (first non-passed discovery stage), `latest_attempt(ps, stage) -> int`, `edge_count(ps, edge)`, `run_row(ps, run_id)`, `active_run(ps)`
- `engine.py`:
  - `StageContext(kernel, registry, ps, lib, project, stage, attempt, run_id, guidance, executor: TaskExecutor, sources: DiscoverySources, actor)`
  - `StageOutcome(status: Literal["done","waiting_human","rerun","back_edge","failed"], checkpoint_id: str | None = None, to: str | None = None, reason: str = "")`
  - `Worker` (Protocol): `run(ctx) -> StageOutcome`; `on_answer(ctx, checkpoint: dict) -> StageOutcome`; `gate(ps, attempt) -> GateResult(ok: bool, failures: list[str])`
  - `Orchestrator(kernel, registry, *, workers: dict[str, Worker] | None = None, executor_factory, sources_factory)`:
    - `run_stage(project, stage, *, run_id=None, guidance=None, actor) -> StageOutcome`: writes `stage.attempt` (attempt + 1) and calls the worker. Refuses a stage before the current one, and a stage that is not in `DISCOVERY` (`ToolError("stage_unavailable")`).
    - `advance(project, *, actor) -> GateResult`: applies answered checkpoints of the current attempt through `on_answer`, then gates; on pass writes `stage.passed`.
    - `step(project, run_id, *, actor) -> str`: one D2 unit; returns the run status.
  - Rules in `step`:
    - Open checkpoint for the current attempt → `paused_waiting_human`.
    - Answered checkpoint → `on_answer`.
    - `done` → gate. A gate failure becomes a rerun with the failures as guidance (edge `X→X`).
    - `rerun` / `back_edge` → `edge.taken`, unless the count is already 2 and no `edge.allowed` is left. Then a `stage_blocked` checkpoint (options `allow_one_more`, `stop`): `allow_one_more` writes `edge.allowed`; `stop` sets the run `failed`.
    - A back-edge reopens the target and every later stage (`stage.reopened`).
    - `BudgetExceeded` or more than 12 h since `session_started` → `paused_budget`.
    - `RateCapped(driver, wait_s)` (a role task came back `rate_capped`, spec §7.2 hourly caps) → status stays `running`, `step` returns `"waiting_rate"`, and the attempt reruns on the next step. Test `test_rate_cap_waits_and_reruns_the_attempt` in Step 1.
    - Passing the `until` stage → `done`.
- Intake worker: `run` returns `failed("intake missing")` unless `intake_of(ps)` exists. Its gate checks all `Intake` fields.

- [ ] **Step 1: Failing tests** in `tests/test_orchestrator.py` (fake workers per stage):
  - `test_d2_runs_stages_in_order_until`: `step` until `done` with `until="gaps"`; `stage.passed` for intake, landscape, gaps; topics never attempted.
  - `test_gate_failure_reruns_with_failures_as_guidance`: the landscape gate fails once; the second attempt's `guidance` contains the failure text; it then passes.
  - `test_back_edge_reopens_downstream`: topics returns `back_edge(to="gaps")`; gaps and topics are no longer passed; `edge_count("topics->gaps") == 1`.
  - `test_third_use_of_an_edge_opens_stage_blocked`: the third back-edge creates `stage_blocked` and the status is `paused_waiting_human`; answering `allow_one_more` lets exactly one more pass; `stop` → `failed`.
  - `test_open_checkpoint_pauses_and_answer_resumes`: a worker returns `waiting_human` with a `topic_review` id; `step` → `paused_waiting_human`; after `answer_checkpoint`, `step` calls `on_answer`.
  - `test_budget_and_wall_clock_pause`: a worker raising `BudgetExceeded` → `paused_budget`; with a clock 12 h + 1 s after `session_started` → `paused_budget` before any worker call.
  - `test_run_stage_refuses_later_and_plan5_stages`: `run_stage("protocol")` → `stage_unavailable`; `run_stage("topics")` while gaps has not passed → `stage_unavailable`.
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement. **Step 4:** Run the file and the full suite — PASS.
- [ ] **Step 5: Commit** `feat(orchestrator): stage engine with attempts, back-edges, budgets and checkpoint waits`

### Task 4: Landscape

**Files:** Create `src/rk/discovery/landscape.py`, `tests/test_discovery_landscape.py`. Modify `src/rk/roles/scout.md`.

**Interfaces:**
- Role outputs:
  - `LandscapeQueries(queries: list[str])` from `scout`, 3–8 queries in English, using the intake and the guidance;
  - `ClusterPlan(clusters: list[ClusterOut(label, description, query, item_ids, representative_ids)])` from `scout`, given `[{item_id, title, year, abstract≤600 chars}]`.
- `LandscapeWorker` implements `Worker`. `run`:
  1. Get the queries.
  2. Run `log_and_search` for every query on the first two of `sources.sources()` (limit 25, purpose `landscape`).
  3. Run `verify` on every new item.
  4. Get the cluster plan.
  5. Drop item ids that are not in this attempt's search results, and representative ids that are not in their cluster.
  6. Fill `year_counts(cluster.query)`.
  7. Add the clusters and return `done`.
- Gate: at least 1 cluster; every cluster has ≥ 5 items with `ref_status == "pass"`; every cluster item is in `searches(ps, "landscape", attempt)`. Failures name the cluster and the count, e.g. `"cluster 'X': 3 verified sources (need 5)"`.

- [ ] **Step 1: Failing tests:**
  - `test_landscape_builds_clusters_from_logged_searches`: with `ScriptedExecutor` + `FakeSources`, 2 clusters are stored, and every query was searched on 2 sources and logged.
  - `test_cluster_items_outside_the_search_are_dropped`: an invented id in the plan is not stored.
  - `test_gate_needs_five_verified_per_cluster`: 4 pass + 1 mismatch fails with the named message; 5 pass passes.
- [ ] **Step 2–4:** FAIL, implement, PASS (file and full suite).
- [ ] **Step 5: Commit** `feat(discovery): landscape stage`

### Task 5: Gap miners — stated limitation, retracted-still-cited, evidence-map hole

**Files:** Create `src/rk/discovery/gaps/{__init__,limitation,retraction,maphole}.py`, `tests/test_gaps_basic.py`. Modify `src/rk/roles/gapminer.md`, `src/rk/roles/triage.md`.

**Interfaces:**
- Shared: `MinerResult(gaps: list[tuple[Gap, bool, dict]])`; each miner is `mine(ctx, clusters) -> MinerResult`; `GapsWorker.run` calls all five miners (Task 6 adds two), stores every gap with `add_gap(verified, checks)`, and returns `done`. Before mining, `GapsWorker` calls `sources.fulltext` on each cluster's representative items, at most 3 per cluster.
- `limitation`:
  - The `gapminer` returns `LimitationsOut(items: [{source_id, quote, later_query}])`.
  - Checks:
    - `quote_note`: `add_note(NoteIn(kind="quote"))` passes V-QUOTE;
    - `position`: the quote starts at ≥ 60% of the layer text;
    - `later_search`: `log_and_search(later_query, purpose="gap_check")` limited to works after the source year was executed;
    - `later_judgement`: a `triage` task returns `LaterWorkOut(addressed: list[item_id], basis: dict[item_id, quote])`, and quotes are checked with `span_in` against title/abstract.
  - `verified = quote_note and position and later_search executed and no verified "addressed" work`.
- `retraction`: deterministic, no LLM. For each cluster item, `sources.retraction(item)` with status `retracted` or `withdrawn` and a known date → `sources.cited_by(item, after_year=retraction year)`. A gap if ≥ 1 citing work. `checks = {retraction_sources, notice_doi, citing_ids}`.
- `maphole`:
  - The `gapminer` returns `MapPlan(dims: [Dim(name, values)] (exactly 2), cells: {item_id: [value_a, value_b]}, empty_cell_queries: {"a|b": query})`.
  - The kernel counts studies per cell. For each empty cell it runs the targeted search (purpose `gap_check`); `triage` returns `CellMatchOut(matches: list[item_id])`.
  - Verified when the search ran and matched nothing.
- Gate (`GapsWorker.gate`): ≥ 1 verified gap in the latest attempt.

- [ ] **Step 1: Failing tests:**
  - `test_unverbatim_limitation_quote_is_not_verified` (Review Focus 2): the gap is stored with `verified = false` and `checks["quote_note"] == "fail"`.
  - `test_limitation_from_the_first_page_is_not_verified`: position check.
  - `test_limitation_already_addressed_later_is_not_verified`: triage returns an addressed work with a verbatim basis.
  - `test_retracted_cited_after_retraction_is_a_gap`: a citation from before the retraction year does not count.
  - `test_empty_cell_needs_a_targeted_search_with_no_match`: one empty cell is confirmed; a second gets a triage match and is not a gap.
  - `test_gaps_gate_needs_one_verified`.
- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat(discovery): limitation, retraction and evidence-map gap miners`

### Task 6: Gap miners — contradiction and citation distortion

**Files:** Create `src/rk/discovery/gaps/{contradiction,distortion}.py`, `tests/test_gaps_support.py`. Modify `src/rk/verify/support.py` (new keyword `checkpoint: bool = True`; the gap miners pass `False`).

**Interfaces:**
- `contradiction`:
  - The `gapminer` returns `ContradictionsOut(pairs: [{a: {source_id, quote}, b: {source_id, quote}, cluster_id}])`, both sources from the same cluster.
  - Both quotes become quote notes (V-QUOTE). Claim A is stored with `ArgumentService.add_claim(ClaimIn(text=quote_a, kind="finding"), generator_vendor=<gapminer task vendor>)` and linked to note B as evidence.
  - Then `verify_support(..., min_judges=2, checkpoint=False)`.
  - Verified iff both notes pass and the verdict is `"contradicted"`.
- `distortion`:
  - The `gapminer` returns `DistortionsOut(items: [{citing_source_id, quote, cited: {item_id | doi}}])`.
  - The cited work is added if needed (`rk_source_add` path), then `sources.verify` and `sources.fulltext`.
  - Evidence is `top_passages(cited, quote, k=5)` saved as quote notes. Claim = quote (the generator vendor is the gapminer's).
  - Then `verify_support(min_judges=2, checkpoint=False)`.
  - Verified iff the citing quote passes V-QUOTE, the cited full text exists, and the verdict ∈ {`unsupported`, `contradicted`} (that verdict already requires two agreeing cross-vendor judges).
  - `checks` record the passage note ids, p and the opinions.

- [ ] **Step 1: Failing tests** (`FakeLayer1` / `FakeJudge` from `test_support.py`, moved to `tests/discovery_fakes.py` if shared):
  - `test_contradiction_needs_contradicted_verdict`: verdict `contradicted` → verified; `uncertain` → stored unverified.
  - `test_distortion_uses_kernel_passages_not_miner_text`: the evidence notes are the `top_passages` of the cited work; text the miner supplies for B is ignored.
  - `test_distortion_without_fulltext_is_unverified`: `checks["fulltext"] == "missing"`.
  - `test_distortion_needs_two_vendor_agreement`: one judge says unsupported and the other is the same vendor → `uncertain` → unverified.
  - `test_gap_mining_opens_no_uncertain_support_checkpoint`.
- [ ] **Step 2–4:** FAIL, implement, PASS. The existing support tests stay green.
- [ ] **Step 5: Commit** `feat(discovery): contradiction and citation-distortion gap miners`

### Task 7: V-NOVELTY and topics

**Files:** Create `src/rk/verify/novelty.py`, `src/rk/discovery/topics.py`, `tests/test_topics.py`. Modify `src/rk/roles/ideator.md`, `src/rk/roles/judge.md`.

**Interfaces:**
- `novelty_reference(ctx: StageContext, text: str, *, k: int = 5) -> NoveltyReport(closest: list[ClosestWork], queries: list[str], sources_used: list[str], note: str = "reference only; never a novelty verdict")`:
  - The first two of `sources.sources()`; one `scout` task returns `LandscapeQueries` (2–3 queries); RRF over the results; top-k.
  - Then `judge` and `judge_2` each return `DifferencesOut(items: [{item_id, difference}])`; `differences` is keyed by vendor, and a judge whose vendor fails the `run_judges` rules is skipped.
- `TopicsWorker.run`:
  - The `ideator` returns `TopicsOut(topics: [{title, summary, gap_ids, feasibility, data_needed, paper_type, risks}])` given the verified gaps (with their evidence quotes), the intake and the guidance.
  - Drop gap ids that are not verified in the latest gaps attempt, and topics left with none.
  - `novelty_reference` per topic → `closest` (top-5).
  - Store the topics, open `topic_review` with options `{"topic_ids": [...]}`, return `waiting_human`.
- `TopicsWorker.on_answer`:
  - `{"decision": "approve", "topic_id"}` → approved, `done`;
  - `{"decision": "revise", "target": "topics" | "gaps", "comment"}` → `rerun` (guidance = comment) or `back_edge(to="gaps")`; the comment is also recorded with `add_round`;
  - `{"decision": "reject"}` → `rerun`.
- Gate:
  - one approved topic in the latest attempt;
  - D2: ≥ `topics_n` candidates, each with ≥ 1 verified gap and 5 closest works (fewer only when the searches returned fewer, recorded);
  - D1: ≥ 1.

- [ ] **Step 1: Failing tests:**
  - `test_topics_drop_unverified_gap_ids` (Review Focus 2).
  - `test_topics_open_topic_review_in_auto_mode_too`: with `production_mode = "auto"` the worker still returns `waiting_human` with `topic_review`.
  - `test_approve_passes_gate_and_revise_reruns`.
  - `test_third_revise_opens_stage_blocked` (Review Focus 3, through `Orchestrator.step`).
  - `test_novelty_without_s2_uses_two_other_sources` (Review Focus 5): `sources_used == ["openalex", "europepmc"]`; `note` says reference only; no field reads "novel".
  - `test_d2_gate_needs_topics_n_candidates`: 5 of 6 → fail naming "5 of 6 topics".
- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat(discovery): topics with closest works and topic review`

### Task 8: Question — RQ gate, already-done check, freeze

**Files:** Create `src/rk/discovery/{question,already_done}.py`, `tests/test_question.py`. Modify `src/rk/roles/ideator.md`, `src/rk/roles/judge.md`.

**Interfaces:**
- `QuestionWorker.run`:
  - The `ideator` returns `RQOut(body: RQBody, hypotheses: list[HypothesisIn])` for the approved topic.
  - The structural check `check_rq(body, hypotheses) -> list[str]`:
    - x, y, z non-empty;
    - `len(problem.consequences) ≥ 2`;
    - all five FINER fields non-empty;
    - `clinical → pico` all four fields non-empty;
    - ≥ 2 hypotheses with distinct normalised statements, each with a falsification.
  - Store the RQ. On structural failure → `rerun` with the failures as guidance.
  - Otherwise run `already_done`.
- `already_done(ctx, rq_id) -> AlreadyDone(result: Literal["already_done","not_found_in_scope","split"], queries, sources_used, works: [{item_id, judgements: {vendor: {answered: bool, quote: str, ok_quote: bool}}}])`:
  - `scout` gives ≥ 3 queries; run on ≥ 2 sources (logged, purpose `already_done`); RRF top-10.
  - `judge` and `judge_2` return `AnsweredOut(items: [{item_id, answered, quote}])` per work. A judgement is valid only if its quote is verbatim in the title/abstract (`span_in`) and its vendor (`vendor_of(provider, model)`) is known, not human, not the RQ author's (the ideator task's) vendor, and not the other judge's.
  - Rules:
    - any work judged `answered` by two valid judges of different vendors → `already_done`;
    - every work judged `not answered` by two such judges → `not_found_in_scope`;
    - anything else (including fewer than two valid vendors) → `split`.
- After the check:
  - `already_done`: D2 → `back_edge(to="topics")`; D1 → `failed("already done: …")` so the Lead tells you.
  - `split`: P2 → `significance_novelty` checkpoint, `waiting_human` (answer `{"decision": "proceed"}` treats it as not found, `{"decision": "back"}` → `back_edge`); P1 → treated as `already_done`.
  - `not_found_in_scope`: D1, or D2 with P2 → `rq_approval` checkpoint (the prompt renders the three-step sentence and the hypotheses); D2 with P1 → `freeze_rq(adopted_by="agent")` → `done`.
- `on_answer(rq_approval)`: `{"decision": "approve"}` → `freeze_rq(adopted_by="human")` → `done`; `{"decision": "revise", "comment"}` → `rerun`.
- Gate: a frozen RQ in the latest attempt whose `already_done.result` is `not_found_in_scope` or which you sent on with `proceed`, plus ≥ 2 hypotheses.

- [ ] **Step 1: Failing tests:**
  - `test_structural_failures_are_named`: one consequence and one hypothesis → both failures listed and `rerun`.
  - `test_already_done_by_both_vendors_backs_to_topics_in_d2`.
  - `test_split_in_collab_opens_significance_novelty`; `test_split_in_auto_is_treated_as_done`.
  - `test_single_judge_vendor_never_clears_already_done` (Review Focus 4).
  - `test_unverbatim_judge_quote_is_invalid`.
  - `test_d2_auto_freezes_with_agent_adoption`: `adopted_by == "agent"` and the hash is in the event log.
  - `test_d1_always_asks_rq_approval`: with `production_mode = "auto"` and `discovery_mode = "d1"` → `rq_approval`.
  - `test_already_done_needs_three_queries_two_sources`: a scout with 2 queries → `rerun`; the search log shows ≥ 2 sources.
- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat(discovery): research question gate, already-done check and RQ freeze`

### Task 9: Runs in `rk serve`, tools and CLI

**Files:** Create `src/rk/orchestrator/runs.py`, `src/rk/discovery/tools.py` (finish), `tests/test_runs.py`. Modify `src/rk/cli/main.py` (serve wiring; `run` command group; `stage status`; `topic list`), `src/rk/server/app.py`, `src/rk/roles/policy.py`.

**Interfaces:**
- `RunManager(kernel, registry, orchestrator, *, poll_s: float = 2.0)`:
  - `start(project, until: Literal["topics","question"], *, actor) -> run_id`: refuses with `ToolError("run_active")` while another run of the project is not terminal, and when intake is missing or `discovery_mode != "d2"`.
  - `pause(project)`, `resume(project)` (new session: `run.session` n + 1, budget counters restart), `cancel(project)`.
  - `status(project) -> dict`: run row + `stage_rows`.
  - `resume_all()` at serve start: restarts a thread for every run in `running`, `waiting_human` or `paused_waiting_human`.
  - `close()`.
  - One daemon thread per project loops `orchestrator.step`. While paused it polls the checkpoints every `poll_s`. On `waiting_rate` it sleeps `min(wait_s, 300)` and steps again in the same session. It stops on `paused`, `paused_budget` and the terminal states.
- Tools:
  - `rk_run_start(project, until)`, `rk_run_pause`, `rk_run_resume`, `rk_run_cancel`: writes, cli only;
  - `rk_run_status(project)`: lead + cli;
  - `rk_stage_status(project)`: all roles;
  - `rk_stage_run(project, stage, guidance=None)` and `rk_stage_advance(project)`: writes, lead + cli; refused with `run_active` while a D2 run is active;
  - `rk_exploration_round(project, stage, feedback)`: lead + cli;
  - `rk_gap_list(project, verified=None)`, `rk_topic_list(project)`, `rk_rq_get(project)`: read, all discovery roles.
- CLI:
  - `rk run <project> --until topics|question`;
  - `rk run status|pause|resume|cancel [--project]`;
  - `rk stage status`;
  - `rk topic list`.
- Serve: `serve()` builds `Orchestrator` + `RunManager` next to `ChatManager`, passes them to `create_app` and `ui_router`, calls `resume_all()` after the server starts, and `close()` on shutdown.

- [ ] **Step 1: Failing tests:**
  - `test_run_resumes_after_restart_without_rerunning_passed_stages` (Review Focus 1): fake workers count calls; the run reaches `topic_review`; close the manager and kernel; reopen; `resume_all()`; answer `approve`. The run passes `question` with landscape and gaps worker call counts unchanged.
  - `test_one_run_per_project`.
  - `test_stage_run_refused_while_run_active`.
  - `test_resume_starts_a_new_session_budget`: after `paused_budget`, `resume` → session 2, and the counters restart.
  - `test_run_tools_are_cli_only`: lead calling `rk_run_start` → `PermissionDenied`.
  - `test_rk_run_cli_round_trip` (live_server fixture): `rk run demo --until topics` prints the run id; `rk run status` shows the stage table.
- [ ] **Step 2–4:** FAIL, implement, PASS (full suite).
- [ ] **Step 5: Commit** `feat(orchestrator): runs in rk serve with resume, run tools and CLI`

### Task 10: D1 in the Lead chat

**Files:** Modify `src/rk/roles/lead.md`, `src/rk/roles/policy.py`, `src/rk/roles/prompts.py` (if the Lead needs the project's stage in its context), `tests/test_roles.py`, `tests/test_d1.py` (create).

**Interfaces:**
- Lead policy:
  - reads and status tools always;
  - `rk_project_intake`, `rk_stage_run`, `rk_stage_advance`, `rk_exploration_round` and the Scout/Reader/GapMiner/Ideator tools (including browser search/fetch) only while `current_stage(ps)` is a discovery stage (§8.1). The tool handlers check this and raise `ToolError("stage_closed")` after `question` passed.
  - Never `rk_checkpoint_answer` or the run tools.
- `lead.md` D1 workflow:
  - intake through questions;
  - each round: run the stage, show findings with evidence links, record your feedback with `rk_exploration_round`;
  - show `topic_review` / `rq_approval` and explain them, but tell you to answer in the Checkpoints panel;
  - always say "not found within the searched scope", never "novel".

- [ ] **Step 1: Failing tests:**
  - `test_lead_stage_tools_close_after_question` (`stage_closed`);
  - `test_lead_cannot_answer_topic_review`;
  - `test_d1_scripted_chat_reaches_topic_review`: a scripted Lead (`ScriptedBackend`) calls intake → stage_run ×3 → exploration_round → stage_run(topics); the result is an open `topic_review`, and `rounds(ps)` has the feedback;
  - `test_guidance_reaches_the_next_stage_run`: feedback "exclude animal studies" appears in the next landscape task's inputs.
- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat(discovery): guided discovery (D1) through the Lead`

### Task 11: Workbench — stage graph, run controls, topic and RQ review

**Files:** Modify `src/rk/ui/api.py`, `src/rk/ui/static/{app.js,app.css}`, `tests/test_ui_discovery.py` (create), `tests/test_ui_e2e.py`.

**Interfaces:**
- Routes:
  - `GET /ui/api/stages?project` → `{graph: stages_for(paper_type) with status per stage ("passed","active","pending","plan5"), back_edges, last_gate, run}`;
  - `POST /ui/api/run {project, until}`, `POST /ui/api/run/{action}` with action ∈ pause/resume/cancel (via the run tools, ctx human);
  - `GET /ui/api/topics?project` (latest attempt with gaps' evidence quotes and closest works);
  - `GET /ui/api/rq?project`.
- WS: the pump also sends `{type: "run", status}` when `rk_run_status` changes.
- Progress page:
  - replaces the line "The stage graph and run controls come with the orchestrator";
  - the stage graph is an ordered list of stage chips (status colour + text, back-edge count, last gate failures);
  - run controls: Start D2 (until topics / question), Pause, Resume, Cancel, shown according to the run state.
- Checkpoints page:
  - `topic_review` card per topic: title, summary, gaps with their quotes and kind, closest works with differences by vendor, feasibility, data, paper type, risks. Actions Approve (this topic), Revise (comment + target topics/gaps), Reject all.
  - `rq_approval` card: the three-step sentence, problem chain, FINER, PICO, hypotheses with falsification, the already-done result with works and quotes. Actions Approve / Revise (comment).
  - `stage_blocked` card: failures, Allow one more / Stop.

- [ ] **Step 1: Failing tests:**
  - `test_stages_route_marks_plan5_stages` (`test_ui_discovery.py`);
  - `test_topic_review_answer_through_ui_resumes_run`: POST answer approve → the run leaves `paused_waiting_human` within 5 s (RunManager with fake workers);
  - e2e (Playwright, existing harness): `test_topic_review_card_approve` (render two topics, click Approve on the second, the checkpoint answer has its `topic_id`); `test_run_controls_follow_state`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Also check the real surface: open the Workbench in the built-in browser with a fixture project, screenshot the progress and checkpoint pages, and fix what looks wrong.
- [ ] **Step 5: Commit** `feat(ui): stage graph, run controls, topic and RQ review`

### Task 12: Demo and docs

**Files:** Create `docs/demo/plan4-discovery.md`. Modify `README.md`.

- [ ] **Step 1:**
  1. Check `rk providers`, and set drivers to available ones (the generator vendor must differ from both judges).
  2. Live D2 on a scratch project: intake (critical_review, d2, collab), then `rk run perovskite --until question` with the query "perovskite solar cell stability".
  3. Record:
     - the clusters (counts, verified sources);
     - verified gaps by kind;
     - the 6 topics with closest works;
     - the topic review in the Workbench (screenshot);
     - the RQ approval;
     - the frozen RQ hash in the event log;
     - task counts per stage and wall time.

  The demo document records what failed or was weak (calibration input for later), without personal paths.
- [ ] **Step 2:** README section "Discovery (D1/D2)": the commands, the checkpoints you answer, what "verified gap" means and that V-NOVELTY is reference only.
- [ ] **Step 3:** Full suite, ruff. **Commit** `docs: Plan 4 demo and README for discovery`, push.
