"""Tool registry: one definition per operation, shared by the daemon, CLI and (later) agent loops."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from rk.net.http import BudgetExhausted


class PermissionDenied(Exception):
    pass


class ToolError(Exception):
    def __init__(self, type_: str, message: str, details: dict | None = None, status: int = 400):
        super().__init__(message)
        self.type = type_
        self.message = message
        self.details = details or {}
        self.status = status


@dataclass(frozen=True)
class CallContext:
    role: str
    actor: str
    idempotency_key: str | None = None
    task_id: str | None = None
    project: str | None = None
    allowed_tools: frozenset[str] | None = None  # set for agent tasks: only these tools, whatever the role allows


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    roles: frozenset[str]
    writes: bool
    handler: Callable[[CallContext, BaseModel], BaseModel]


class ToolRegistry:
    def __init__(self, idempotency_db: Path, *, guard: Callable[[Tool, dict, CallContext], None] | None = None):
        """``guard(tool, args, ctx)`` runs before a permitted call's arguments are validated and may refuse it by
        raising (``build_registry``: the Lead's stage-dependent access, spec §8.1)."""
        self.guard = guard
        idempotency_db.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(idempotency_db, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS idempotency (tool TEXT, key TEXT, result TEXT, PRIMARY KEY (tool, key))"
        )
        self._tools: dict[str, Tool] = {}
        self._db_lock = threading.Lock()  # guards the shared idempotency connection (short statements only)
        self._key_locks: dict[tuple[str, str], tuple[threading.Lock, int]] = {}
        self._key_locks_mutex = threading.Lock()
        # spec §11.3: the role tasks ``run_task`` is running (task id -> the source budget one of their tool calls
        # spent, or None); see ``watch_budget``. A call can end on a server thread after its task stopped watching (an
        # agentic CLI killed on timeout): ``_budgets_lock`` records only while the task is still watched.
        self._budgets: dict[str, BudgetExhausted | None] = {}
        self._budgets_lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError("unknown_tool", f"no such tool: {name}", status=404)
        return self._tools[name]

    def list(self, role: str, allowed: frozenset[str] | None = None) -> list[Tool]:
        return [t for t in self._tools.values() if role in t.roles and (allowed is None or t.name in allowed)]

    # ---- spent source budgets of role tasks (spec §11.3: suspended, never skipped silently) ----
    def watch_budget(self, task_id: str) -> None:
        """Watch a role task's tool calls (``run_task``, for the task's life): the first ``BudgetExhausted`` one of
        them raises, raw or as the cause of a ``ToolError`` (``rk_search_works``), is recorded for the task, and its
        later calls are refused. The call still fails as before (a model or a CLI sees an error); the task's runner
        reads the record (``budget_exhausted``) and suspends the task. In-process tool loops and agentic CLIs calling
        through ``rk serve`` with the task token both land here; calls of no watched task (the UI, chats) do not."""
        with self._budgets_lock:
            self._budgets[task_id] = None

    def budget_exhausted(self, task_id: str) -> BudgetExhausted | None:
        return self._budgets.get(task_id)

    def unwatch_budget(self, task_id: str) -> BudgetExhausted | None:
        with self._budgets_lock:
            return self._budgets.pop(task_id, None)

    def call(self, name: str, args: dict, ctx: CallContext) -> dict:
        watched = ctx.task_id is not None and ctx.task_id in self._budgets
        if watched and (spent := self._budgets.get(ctx.task_id)) is not None:
            raise suspended(spent)
        try:
            return self._call(name, args, ctx)
        except Exception as e:
            if watched and (spent := _spent_budget(e)) is not None:
                with self._budgets_lock:
                    if ctx.task_id in self._budgets and self._budgets[ctx.task_id] is None:
                        self._budgets[ctx.task_id] = spent
            raise

    def _call(self, name: str, args: dict, ctx: CallContext) -> dict:
        tool = self.get(name)
        if ctx.role not in tool.roles:
            raise PermissionDenied(f"role {ctx.role!r} may not call {name}")
        if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
            raise PermissionDenied(f"{name} is not granted to this task")
        args = dict(args or {})
        if ctx.project is not None:
            # A project-bound caller (task token) works on its own project only, and only through tools that take one.
            if "project" not in tool.input_model.model_fields:
                raise PermissionDenied(f"{name} is not project-scoped; not available to a project-bound caller")
            if args.setdefault("project", ctx.project) != ctx.project:
                raise PermissionDenied(f"this caller is bound to project {ctx.project!r}")
        if self.guard is not None:
            self.guard(tool, args, ctx)
        inp = tool.input_model.model_validate(args)
        if not tool.writes:
            return tool.output_model.model_validate(tool.handler(ctx, inp)).model_dump(mode="json")
        if not ctx.idempotency_key:
            raise ToolError("idempotency_key_required", f"{name} writes state and needs an idempotency_key")
        slot = (name, ctx.idempotency_key)
        lock = self._acquire_key_lock(slot)
        try:
            # Calls with different keys run concurrently; a retry with the same key waits for the first call and
            # then returns its stored result instead of repeating the write.
            with lock:
                with self._db_lock:
                    r = self._db.execute("SELECT result FROM idempotency WHERE tool=? AND key=?", slot).fetchone()
                if r:
                    return json.loads(r[0])
                out = tool.output_model.model_validate(tool.handler(ctx, inp)).model_dump(mode="json")
                with self._db_lock:
                    self._db.execute("INSERT INTO idempotency(tool, key, result) VALUES (?,?,?)",
                                     (*slot, json.dumps(out)))
                return out
        finally:
            self._release_key_lock(slot)

    def _acquire_key_lock(self, slot: tuple[str, str]) -> threading.Lock:
        with self._key_locks_mutex:
            lock, users = self._key_locks.get(slot, (threading.Lock(), 0))
            self._key_locks[slot] = (lock, users + 1)
            return lock

    def _release_key_lock(self, slot: tuple[str, str]) -> None:
        with self._key_locks_mutex:
            lock, users = self._key_locks[slot]
            if users <= 1:
                del self._key_locks[slot]
            else:
                self._key_locks[slot] = (lock, users - 1)


def _spent_budget(e: BaseException | None) -> BudgetExhausted | None:
    """The ``BudgetExhausted`` that ``e`` is, or that it was raised from (``raise ToolError(...) from e``)."""
    while e is not None:
        if isinstance(e, BudgetExhausted):
            return e
        e = e.__cause__
    return None


def suspended(spent: BudgetExhausted) -> ToolError:
    """The error of a call refused, or a role task stopped, because a source budget is spent (spec §11.3)."""
    return ToolError("budget_exhausted", f"{spent}; the task is suspended until the budget resets",
                     {"budget": spent.budget_key, "reset_at": spent.reset_at}, status=429)
