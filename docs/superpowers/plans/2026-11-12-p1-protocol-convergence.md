# P1 Protocol Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a P1 (autonomous) protocol converge under the two-judge vote: the kernel answers what only it knows, catches mechanical faults before a vote, and the judges' points become a tracked ledger every redraft must answer and every later vote must close.

**Architecture:** Three layers in `rk.production.protocol`. (1) Kernel-owned facts: PRISMA-P admin items, the language exclusion and a "search execution" block are written by the kernel, and the judges get a rules sheet. (2) New deterministic checks run inside the attempt (no `protocol->protocol` edge spent). (3) A point ledger derived from events: the protocol vote's judges return structured points; the Ideator and Scout answer each open point; the next vote sees points, answers and a diff, and each judge closes its own points. The vote's decision rule (every admitted judge approves) is unchanged.

**Tech Stack:** Python 3.11, pydantic v2, SQLite event store (`ProjectStore.apply`, projections), pytest with `tests/production_fakes.py`.

**Spec:** `docs/eval/p1-protocol-diagnosis-2026-11-12.md` (the diagnosis this plan implements: §5.3 stale guidance, §5.4 dropped items, §6 rules R1–R8, §7 the ledger design).

## Global Constraints

- Every change is TDD: the failing test first, seen failing, then the code. Tests follow the repo style (`tests/test_protocol*.py`, `tests/production_fakes.py`); no tests of wording or wiring only.
- P2 keeps working unchanged in what it asks of you: the human `protocol_approval` sees the same rendered protocol plus the new kernel-written parts.
- No new rule for the vote's decision (both admitted judges must answer `approve`), no change to the edge limit (`EDGE_LIMIT`) or to `protocol_tries`.
- Kernel-written text never claims what the kernel does not do. It must match `rk.production.search` / `search_screen` behaviour (verbatim query per source, dedup, cap after dedup) — read that code before writing the text.
- Commit identity: `9s94ttyfct-bit <273716724+9s94ttyfct-bit@users.noreply.github.com>`. No `git stash`. Edits via the edit tool.
- Docstrings: prose describing behaviour, with a calibration note naming the diagnosis where a rule came from ("Calibration (P1 protocol diagnosis 2026-11-12): …").

## Review Focus

- A judge that answers `approve` while listing a blocking point of its own: the kernel must treat its choice as `revise` (the stored opinion says so), never freeze.
- A run that fails at the edge limit and is resumed by a new run: attempt 4's Ideator must see the attempt-3 vote's points (the ledger, not `stages.guidance`).
- A point the Ideator "answers" by rebuttal only: it stays open until the judge that raised it marks it resolved; a rebuttal is shown to that judge.
- A redraft that drops a PRISMA-P key the previous draft had: an in-attempt failure, not a vote.
- A non-systematic paper type (critical review, research proposal, scoping review): no PRISMA-P items forced on it; the ledger and kernel text still apply; P2 human approval unchanged.

---

### Task 1: Kernel-owned protocol facts and the judges' rules sheet

**Files:**
- Modify: `src/rk/production/protocol.py` (constants near 124-165, `render_protocol` 487-509, the place the body is built from the Ideator's and Scout's parts in `ProtocolWorker._draft`)
- Modify: `src/rk/production/models.py` only if a constant belongs there (e.g. `KERNEL_PRISMA_P`)
- Test: `tests/test_protocol_kernel_facts.py` (new)

**Interfaces:**
- Produces: `KERNEL_PRISMA_P: dict[str, str]` (keys `registration`, `amendments`, `support`, `competing_interests`; values are the kernel's fixed texts), `kernel_prisma_p(paper_type: str) -> dict[str, str]` (empty for paper types that are not systematic/scoping reviews), `LANGUAGE_CRITERION_ID = "E-lang"`, `with_kernel_facts(body: Protocol, paper_type: str) -> Protocol` (overwrites the kernel keys in `prisma_p`, strips any Ideator-written kernel key, appends the `E-lang` exclude criterion once), `search_execution_lines(body: dict) -> list[str]`, `KERNEL_RULES: str` (the judges' rules sheet).

- [ ] **Step 1: Write the failing tests** — `test_kernel_writes_admin_prisma_p_items` (an SR body whose Ideator wrote `registration: "to be completed by the researcher"` comes back with `KERNEL_PRISMA_P["registration"]`; a critical review's body gets no PRISMA-P keys), `test_language_exclusion_is_appended_once` (applying `with_kernel_facts` twice gives one `E-lang` criterion, whose text names `SCREENED_LANGUAGES`), `test_rendered_protocol_states_search_execution` (`render_protocol` lines contain the years written out — `date_from=None` reads "earliest indexed record" — the verbatim-per-source sentence, dedup and the cap-after-dedup rule).
- [ ] **Step 2: Run them to see them fail** — `uv run pytest tests/test_protocol_kernel_facts.py -q -p no:cacheprovider` → FAIL (names undefined).
- [ ] **Step 3: Implement** the interfaces above in `protocol.py`; apply `with_kernel_facts` where `ProtocolWorker._draft` builds the `Protocol` (so every stored version carries the kernel facts, and `check_protocol` sees them); add `search_execution_lines` to `render_protocol` right after the search strategy. `DRAFT_TASK` says the kernel writes the admin items and the language exclusion and that the Ideator must not write them.
- [ ] **Step 4: Run the new tests and `tests/test_protocol.py`** → PASS (update any existing assertion that pinned the old body only if the behaviour change is intended; say which).
- [ ] **Step 5: Commit** — `protocol: the kernel writes the admin PRISMA-P items, the language exclusion and the search execution`.

### Task 2: New in-attempt checks (R1b/c, R2, R5–R8)

**Files:**
- Modify: `src/rk/production/protocol.py` (`PRISMA_P_TEXT`, `DRAFT_TASK`, `check_protocol`, `check_strategy`, `_failures`, the narrowing exit in `ProtocolWorker.run`)
- Test: `tests/test_protocol_checks.py` (new)

**Interfaces:**
- Consumes: Task 1's `with_kernel_facts` (checks run on the body with kernel facts applied).
- Produces: `PRISMA_P_TEXT` extended with `"meta_bias", "certainty_of_evidence", "data_management", "outcomes_prioritisation"`; `PLACEHOLDER: re.Pattern`; `dropped_prisma_p(previous: dict | None, body: Protocol) -> list[str]`; `check_protocol(body, paper_type)` and `check_strategy(strategy, available)` keep their signatures and return more failures.

- [ ] **Step 1: Write the failing tests**, one per rule, each with a passing and a failing body:
  - `test_placeholder_text_fails` — `"to be completed by the researcher"`, `"[TO BE ADDED]"`, `"will be registered before"` in any Ideator text fail, naming the field; ordinary future tense ("studies will be screened by two screeners") passes.
  - `test_systematic_review_needs_the_extended_prisma_p_items` — each of the four new keys missing fails for an SR only.
  - `test_prisma_p_sources_agree_with_the_strategy` — PRISMA-P text naming `arXiv` while `sources` lacks it fails; a text that names only listed sources passes; promising `grey literature`, `hand-search`, `reference lists`, `clinicaltrials.gov` or `contacting authors` fails (the kernel does none of these), a negated mention ("grey literature is not searched") passes.
  - `test_systematic_review_lists_outcomes_for_every_plan` — an SR narrative plan without `outcomes` fails; outcome-name duplicates fail for any method.
  - `test_diagnostic_accuracy_meta_analysis_fails_with_the_reason` — question or an outcome naming sensitivity/specificity/AUC with `method == "meta_analysis"` fails with a message saying `rk.stats` has no diagnostic-accuracy measure and to plan a narrative synthesis with that limitation stated.
  - `test_query_syntax` — unbalanced parentheses fail; `a AND b OR c` (mixed at one depth, outside quotes) fails; `(a OR b) AND c` passes.
  - `test_systematic_review_extraction_has_a_study_id` — no field matching `study_?id|author|citation|reference` fails for an SR.
  - `test_redraft_may_not_drop_prisma_p_items` — `dropped_prisma_p({"prisma_p": {"funding": "x", "rationale": "y"}}, body_without_rationale)` names `rationale`; kernel keys are ignored.
  - `test_p1_does_not_vote_above_the_cap` (worker test with fakes): after the narrowing tries a best version still above `record_cap` is not voted on in P1; the attempt reruns with the cap failure as its reason, and no judge task runs. P2 still opens `protocol_approval` (you may raise the cap).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** in `check_protocol`/`check_strategy`; `dropped_prisma_p` joins the draft failures of a try whose `previous_draft` is known (in `_draft`); the cap rule in `ProtocolWorker.run` for `production_mode == "auto"` only. `DRAFT_TASK` lists every `PRISMA_P_TEXT` item.
- [ ] **Step 4: Run** `tests/test_protocol_checks.py tests/test_protocol.py tests/test_sr_repro*.py` → PASS.
- [ ] **Step 5: Commit** — `protocol: placeholder, PRISMA-P, source, outcome, query and study-id checks; P1 does not vote above the cap`.

### Task 3: The judges' point ledger

**Files:**
- Modify: `src/rk/production/vote.py` (optional output model), `src/rk/production/protocol.py` (judge output, ledger, Ideator/Scout answers, judge inputs, `_revise_reason`), `src/rk/roles/*.md` only where the ideator/scout/judge role files describe the protocol task (check `src/rk/roles/`)
- Test: `tests/test_protocol_ledger.py` (new)

**Interfaces:**
- Consumes: Task 1 `KERNEL_RULES`, `render_protocol`; Task 2 checks.
- Produces:
  - `vote(ctx, …, output: type[BaseModel] = VoteOut)`: each opinion stores the output's extra fields (`points`, `resolved`) besides `role`, `vendor`, `choice`, `reason`.
  - `class JudgePoint(BaseModel): section: Literal["search", "eligibility", "extraction", "synthesis", "prisma_p", "other"]; fault: str; change: str; blocking: bool = True`
  - `class PointMark(BaseModel): point_id: str; resolved: bool; why: str = ""`
  - `class ProtocolVoteOut(VoteOut): points: list[JudgePoint] = []; resolved: list[PointMark] = []`
  - `class PointAnswer(BaseModel): point_id: str; how: Literal["changed", "rebutted"]; note: str`; `ProtocolDraftOut.answers: list[PointAnswer] = []`, `SearchStrategyOut.answers: list[PointAnswer] = []`.
  - `points_ledger(ps: ProjectStore) -> list[dict]`: every point of every protocol vote of the project, in order, each `{id, attempt, role, vendor, section, fault, change, blocking, status: "open"|"resolved", answers: [...], marks: [...]}`. Point id: `p{attempt}.{role}.{n}` (n from 1 in the judge's order). A point is resolved when a later opinion of the same `role` marks it `resolved: true`; answers come from the `answers` stored with the protocol versions (`protocol.added` `tried.answers`). Derived from events only: survives an edge-limit failure and a new run (fixes diagnosis §5.3).
  - `owner(point) -> Literal["scout", "ideator"]` (`section == "search"` → scout).
  - Effective choice: an opinion whose own open blocking points (new, or earlier ones it marked unresolved or did not mark) are non-empty counts as `revise` even if it answered `approve`; record `choice` as given and the effective decision in the vote (`reason`: "judge … approved with a blocking point open").

- [ ] **Step 1: Write the failing tests:**
  - `test_points_are_numbered_and_carried_across_runs` — two votes (attempts 1 and 2) with points; then the run fails at the edge limit and a new run's attempt 3 starts: the Ideator's inputs hold `open_points` with ids `p1.judge.1`, `p2.judge_2.1`, … and none of `stages.guidance`'s stale text.
  - `test_every_open_point_must_be_answered_in_the_attempt` — an Ideator draft that leaves an owned open point unanswered is an in-attempt failure (`draft` failures name the point id); the Scout likewise for `search` points; no edge is spent.
  - `test_judges_see_points_answers_rules_and_a_diff` — the judge task inputs contain `open_points` (with answers), `kernel_rules == KERNEL_RULES`, the rendered protocol (no `hksj`/`pi_df` keys for a narrative plan) and a `changes` diff from the previously voted version.
  - `test_a_point_closes_only_by_its_judge` — judge_2 marking `p1.judge.1` resolved leaves it open; judge marking it resolved closes it.
  - `test_approve_with_an_open_blocking_point_counts_as_revise` — FakeExecutor judge answers `approve` with one blocking point → decision `revise`, nothing frozen; an `approve` with only non-blocking points from both judges freezes.
  - `test_other_votes_unchanged` — `vote()` without `output` still returns and stores plain `{role, vendor, choice, reason}` opinions (screening/final-review votes).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** `VOTE_QUESTION` asks: mark each open point of yours resolved or not (with why), then list new points, blocking only for what must change before freezing; answer approve only when none of your blocking points is open; the rules sheet (`kernel_rules`) is what the kernel does and cannot do — do not ask for anything it rules out. `DRAFT_TASK`/`STRATEGY_TASK`: answer every point in `open_points` routed to you (changed, with where; or rebutted, with why). `_revise_reason` stays the stage's rerun reason (for the event log), but the Ideator/Scout inputs take `open_points` from `points_ledger`, not from `ctx.guidance`, in P1; P2 keeps `guidance` (your comment).
- [ ] **Step 4: Run** `tests/test_protocol_ledger.py tests/test_protocol*.py tests/test_vote*.py tests/test_screening*.py tests/test_audit.py` → PASS.
- [ ] **Step 5: Commit** — `protocol: the judges' points are a ledger every redraft answers and every later vote closes`.

### Task 4: Docs

**Files:** `src/rk/production/protocol.py` module docstring, `README.md` (protocol / P1 sections), `docs/eval/p1-protocol-diagnosis-2026-11-12.md` (a short "What was built" note at the end).

- [ ] **Step 1:** Update the module docstring (kernel facts, new checks, P1 cap rule, ledger, effective choice) and README's protocol paragraph to match the code. No test.
- [ ] **Step 2:** Commit — `docs: P1 protocol convergence`.

---

After all tasks: one reviewer on the whole branch, the full suite on the final commit (named service, `-v`, `-o faulthandler_timeout=300`), path scan, fast-forward `main`, push. Then step 3 of the roadmap: seed the six reproduction entries in `$TMPDIR/rk-p1-srrepro4` (OpenAlex key configured) and run `rk eval sr-repro` on the merged code.
