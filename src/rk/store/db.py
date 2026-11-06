"""SQLite connection setup for project databases."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path


def open_project_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(files("rk.store").joinpath("schema.sql").read_text(encoding="utf-8"))
    return conn
