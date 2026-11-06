"""Tool registry: one definition per operation, shared by the daemon, CLI and (later) agent loops."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


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
    def __init__(self, idempotency_db: Path):
        idempotency_db.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(idempotency_db, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS idempotency (tool TEXT, key TEXT, result TEXT, PRIMARY KEY (tool, key))"
        )
        self._tools: dict[str, Tool] = {}
        self.write_lock = threading.Lock()  # single writer: all writes.

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError("unknown_tool", f"no such tool: {name}", status=404)
        return self._tools[name]

    def list(self, role: str) -> list[Tool]:
        return [t for t in self._tools.values() if role in t.roles]

    def call(self, name: str, args: dict, ctx: CallContext) -> dict:
        tool = self.get(name)
        if ctx.role not in tool.roles:
            raise PermissionDenied(f"role {ctx.role!r} may not call {name}")
        inp = tool.input_model.model_validate(args or {})
        if not tool.writes:
            return tool.output_model.model_validate(tool.handler(ctx, inp)).model_dump(mode="json")
        if not ctx.idempotency_key:
            raise ToolError("idempotency_key_required", f"{name} writes state and needs an idempotency_key")
        with self.write_lock:
            r = self._db.execute("SELECT result FROM idempotency WHERE tool=? AND key=?",
                                 (name, ctx.idempotency_key)).fetchone()
            if r:
                return json.loads(r[0])
            out = tool.output_model.model_validate(tool.handler(ctx, inp)).model_dump(mode="json")
            self._db.execute("INSERT INTO idempotency(tool, key, result) VALUES (?,?,?)",
                             (name, ctx.idempotency_key, json.dumps(out)))
            return out
