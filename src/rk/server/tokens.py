"""Scoped bearer tokens for agents (spec §6.0).

- task tokens: one per agentic-CLI task, bound to project, role and tool list; die with the task or on expiry.
- lead tokens: long-lived, for external MCP clients acting as the Lead; revocable by name.
Only ``sha256(token)`` is stored.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Grant:
    kind: str  # "task" | "lead"
    role: str
    project: str | None
    tools: frozenset[str] | None
    task_id: str | None
    name: str | None

    @property
    def actor(self) -> str:
        return f"{self.role}@task:{self.task_id}" if self.kind == "task" else f"{self.role}@token:{self.name}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenStore:
    def __init__(self, db_path: Path, *, clock=time.time):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, role TEXT NOT NULL, "
            "project TEXT, tools TEXT, task_id TEXT, name TEXT, created REAL NOT NULL, expires REAL, "
            "revoked INTEGER NOT NULL DEFAULT 0)")
        self._lock = threading.Lock()
        self._clock = clock

    def _insert(self, **row) -> str:
        token = secrets.token_urlsafe(32)
        row = {"hash": _hash(token), "created": self._clock(), **row}
        cols = ",".join(row)
        with self._lock:
            self._db.execute(f"INSERT INTO tokens({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return token

    def issue_task(self, task_id: str, role: str, project: str | None, tools: list[str], ttl_s: float) -> str:
        return self._insert(kind="task", role=role, project=project, tools=json.dumps(sorted(tools)),
                            task_id=task_id, expires=self._clock() + ttl_s)

    def issue_lead(self, name: str) -> str:
        with self._lock:
            taken = self._db.execute("SELECT 1 FROM tokens WHERE kind='lead' AND name=? AND revoked=0", (name,)).fetchone()
        if taken:
            raise ValueError(f"a lead token named {name!r} already exists; revoke it first")
        return self._insert(kind="lead", role="lead", project=None, tools=None, task_id=None, name=name, expires=None)

    def lookup(self, token: str) -> Grant | None:
        with self._lock:
            r = self._db.execute("SELECT kind, role, project, tools, task_id, name, expires, revoked FROM tokens "
                                 "WHERE hash=?", (_hash(token),)).fetchone()
        if r is None:
            return None
        kind, role, project, tools, task_id, name, expires, revoked = r
        if revoked or (expires is not None and self._clock() >= expires):
            return None
        return Grant(kind=kind, role=role, project=project, tools=frozenset(json.loads(tools)) if tools else None,
                     task_id=task_id, name=name)

    def revoke(self, key: str) -> int:
        """Revoke task tokens by task id, or a lead token by name."""
        with self._lock:
            cur = self._db.execute("UPDATE tokens SET revoked=1 WHERE revoked=0 AND (task_id=? OR (kind='lead' AND "
                                   "name=?))", (key, key))
        return cur.rowcount

    def list_lead(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT name, created, revoked FROM tokens WHERE kind='lead' ORDER BY created")
            return [{"name": n, "created": c, "revoked": bool(v)} for n, c, v in rows]

    def close(self) -> None:
        self._db.close()
