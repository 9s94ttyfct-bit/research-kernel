"""Kernel-made code outputs: statistics (``stt_``) and figures (``fig_``).

An artifact records what made it (``code_path``, ``code_version``: a hash of the module's code), what it was made from
(``input_hashes``) and its ``values``; V-STATS recomputes them, V-NUM can cite them. Its file lives under
``<project>/artifacts/<id>.<ext>`` with its sha256: the file the code wrote (``output_path``, copied in), or for a
statistic without one the canonical JSON of its values. Event-sourced like the rest of the store."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rk.canon import canonical_json, sha256_hex
from rk.ids import new_id
from rk.store.events import ActorKind, Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.store.query import query_rows

ARTIFACTS_DIR = "artifacts"
# The media type of an artifact file, by its suffix (the Workbench serves the files with it)
ARTIFACT_MEDIA_TYPES = {".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8", ".json": "application/json"}
_JSON_COLS = ("input_hashes", "values")


class ArtifactIn(BaseModel):
    kind: Literal["stt", "fig"]
    code_path: str
    code_version: str
    input_hashes: list[str]
    values: dict
    output_path: str | None  # the file the code wrote; copied into the project, its source path is not kept


def add_artifact(ps: ProjectStore, a: ArtifactIn, *, stage: str, attempt: int, created_by_task: str | None = None,
                 actor: str, actor_kind: ActorKind = "kernel") -> str:
    """Record the artifact and store its file under the project; returns its ``stt_``/``fig_`` id. The file is
    written aside first and put in place only once the event is recorded, so a refused event leaves no file."""
    if a.output_path is None:
        if a.kind == "fig":
            raise ValueError("a figure needs its output file (output_path)")
        data, ext = canonical_json(a.values).encode("utf-8"), ".json"
    else:
        src = Path(a.output_path)
        data, ext = src.read_bytes(), src.suffix
    aid = new_id(a.kind)
    rel = f"{ARTIFACTS_DIR}/{aid}{ext}"
    dest = ps.root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    try:
        ps.apply(NewEvent(type="artifact.added", entity_id=aid, actor_kind=actor_kind, actor=actor,
                          payload={"kind": a.kind, "stage": stage, "attempt": attempt, "code_path": a.code_path,
                                   "code_version": a.code_version, "input_hashes": list(a.input_hashes),
                                   "values": a.values, "path": rel, "sha256": sha256_hex(data),
                                   "created_by_task": created_by_task}))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dest)
    return aid


def artifact(ps: ProjectStore, artifact_id: str) -> dict | None:
    rows = query_rows(ps, "SELECT * FROM artifacts WHERE id=?", (artifact_id,), _JSON_COLS)
    return rows[0] if rows else None


def artifacts(ps: ProjectStore, stage: str, attempt: int) -> list[dict]:
    return query_rows(ps, "SELECT * FROM artifacts WHERE stage=? AND attempt=? ORDER BY rowid", (stage, attempt),
                      _JSON_COLS)


# ---- projections ----
@projection("artifact.added")
def _artifact(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute('INSERT OR REPLACE INTO artifacts(id, kind, stage, attempt, code_path, code_version, input_hashes, '
                 '"values", path, sha256, created_by_task, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                 (ev.entity_id, p["kind"], p["stage"], p["attempt"], p["code_path"], p["code_version"],
                  canonical_json(p["input_hashes"]), canonical_json(p["values"]), p["path"], p["sha256"],
                  p["created_by_task"], ev.ts))
