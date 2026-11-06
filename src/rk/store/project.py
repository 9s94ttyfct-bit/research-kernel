"""One research project on disk: database, event log, blobs, text layers, library files."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from rk.blobs import BlobStore
from rk.canon import canonical_json
from rk.store.db import open_project_db
from rk.store.events import Event, EventLog, NewEvent
from rk.store.projections import apply, projection


class ProjectStore:
    """Thread-safe: every thread gets its own SQLite connection (``db``); ``lock`` serializes read-then-write
    sequences (dedupe, get-or-create) that a single transaction does not cover."""

    def __init__(self, root: Path, project_id: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        self.db_path = self.root / "rk.sqlite"
        self.lock = threading.RLock()
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self.db  # create the schema now, in the constructing thread  # noqa: B018
        self.blobs = BlobStore(self.root / "blobs")
        self.text_dir = self.root / "text"
        self.files_dir = self.root / "library" / "files"
        self.text_dir.mkdir(exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = open_project_db(self.db_path)
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    @property
    def log(self) -> EventLog:
        return EventLog(self.db, self.project_id)

    def apply(self, e: NewEvent) -> Event:
        db = self.db
        return apply(db, EventLog(db, self.project_id), e)

    def meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def close(self) -> None:
        with self._conns_lock:
            for c in self._conns:
                c.close()
            self._conns.clear()
        self._local = threading.local()


@projection("project.created")
def _project_created(conn: sqlite3.Connection, ev: Event) -> None:
    for k in ("slug", "title", "field", "paper_type"):
        v = ev.payload.get(k)
        if v is not None:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (k, str(v)))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('project_id', ?)", (ev.project_id,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('created', ?)", (canonical_json(ev.ts),))
