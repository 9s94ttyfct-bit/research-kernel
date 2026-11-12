# P1 protocol calibration 1 — implementation plan

**Goal:** turn the rerun's evidence (`docs/eval/p1-protocol-rerun-2026-11-12.md`) into three deterministic changes so
a P1 protocol that is still converging at the edge limit gets another round, a mis-typed mark no longer costs one,
and the judges stop reading answers from authors that do not own the point.

**Branch:** `p1-calib` from `main` `b130a2a`.

## Global constraints

- P2 is unchanged: at the edge limit it still opens `stage_blocked` for the human; the kernel never grants an edge use
  in P2.
- The convergence rule is P1 only and protocol only; every other stage's edge-limit behaviour is unchanged.
- Event-derived and resume-safe: the decision to grant another use is a function of stored events (the point ledger,
  the edge's grants), recorded as an event before the edge is taken.
- TDD: a failing test first, seen failing for the right reason. Tests test behaviour, not wording or wiring.
- Docstrings in prose; "Calibration (P1 protocol rerun 2026-11-12): …" where a rule came from.

## Task 1: P1 earns another protocol round while it converges

**Files:** `src/rk/orchestrator/engine.py` (`_use_edge`), `src/rk/orchestrator/state.py` (`record_edge_allowed`
takes an optional `by` and `reason`), `src/rk/production/protocol.py` (the rule), tests `tests/test_protocol_ledger.py`
or a new `tests/test_protocol_rounds.py`.

**Rule:** at the edge limit, in P1 only, before degrading, the engine asks the worker
`another_use(ctx, edge) -> str | None` (absent on other workers: no change). `ProtocolWorker.another_use` returns a
reason when all hold:
- `edge == "protocol->protocol"`;
- the kernel granted fewer than `KERNEL_EXTRA_ROUNDS = 2` uses of this edge (count `edge.allowed` events with
  `by == "kernel"`; a human grant does not count against it);
- the latest protocol vote of the current attempt closed at least one live blocking point (a mark `resolved: true`
  by the point's own judge, in its cycle);
- the live blocking points open after that vote are no more than after the vote before it (same cycle).
The engine then records `edge.allowed` (`by="kernel"`, the reason) and takes the edge as a normal use. The reason
names the counts, e.g. "the vote closed 2 blocking points and left 1 open (2 before): one more protocol round".

**Tests:**
- prostate shape (2 → 2 → 1, two closed) at the limit: another round is taken; the run does not fail; `edge.allowed`
  by kernel recorded.
- fructose shape (1 → 1, none closed): degrade as today (the run fails with the vote's reason).
- ozone shape (1 → 3): degrade as today.
- after two kernel grants, the third converging limit degrades.
- P2 at the limit: `stage_blocked` as today, no kernel grant.
- resume: an interrupted step after `edge.allowed` but before the edge is taken does not grant twice (the grant count
  comes from events; take the edge in the same step or make the grant idempotent per edge use).

## Task 2: a mark's id is matched ignoring separators

**Files:** `src/rk/production/protocol.py` (where marks are applied: `points_ledger` and `_own_open_blocking`).

**Rule:** a mark whose `point_id` is not a point of the ledger is matched against the judge's own points by the id
with every non-alphanumeric character removed; exactly one match → that point; otherwise the mark is ignored. The
vote's opinion keeps the mark as given; the ledger lists ignored marks (`unmatched_marks` per vote or per judge) so the
audit shows them.

**Tests:** `p1.judge.2.1` from `judge_2` closes `p1.judge_2.1` and its approve is not tightened; an id matching two
own points or none is ignored and listed; another judge's point is never matched.

## Task 3: an author answers only the points it owns

**Files:** `src/rk/production/protocol.py` (where `answers` from `ProtocolDraftOut` / `SearchStrategyOut` are stored),
`DRAFT_TASK`/`STRATEGY_TASK` and the role files only if they say to answer other points.

**Rule:** before a version's answers are stored, answers to a point the author does not own (`owner(point)`) are
dropped; the unanswered-point check is unchanged (it already asks each author only for its own).

**Tests:** a Scout answer to an Ideator-owned point is not in the ledger's `answers`; a Scout answer to its own
search point is.

## Task 4: docs

README's protocol paragraph (P1 rounds), the protocol module docstring, and the rerun doc's "Calibration" section
updated to what was built.

After all tasks: final whole-branch review, full suite on the final commit, path scan, fast-forward `main`, push.
Then a fresh home, the six entries again.
