"""One research project on disk: database, event log, blobs, text layers, library files."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rk.blobs import BlobStore
from rk.canon import canonical_json
from rk.store.db import open_project_db
from rk.store.events import Event, EventLog, NewEvent
from rk.store.projections import apply, projection


class ProjectStore:
    def __init__(self, root: Path, project_id: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        self.db = open_project_db(self.root / "rk.sqlite")
        self.log = EventLog(self.db, project_id)
        self.blobs = BlobStore(self.root / "blobs")
        self.text_dir = self.root / "text"
        self.files_dir = self.root / "library" / "files"
        self.text_dir.mkdir(exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def apply(self, e: NewEvent) -> Event:
        return apply(self.db, self.log, e)

    def meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def close(self) -> None:
        self.db.close()


@projection("project.created")
def _project_created(conn: sqlite3.Connection, ev: Event) -> None:
    for k in ("slug", "title", "field", "paper_type"):
        v = ev.payload.get(k)
        if v is not None:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (k, str(v)))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('project_id', ?)", (ev.project_id,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('created', ?)", (canonical_json(ev.ts),))
