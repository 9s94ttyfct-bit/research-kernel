"""The per-project browse log (spec §6.3 ``browse_log``): what the built-in browser searched, opened and downloaded,
and when it handed control to you."""

from __future__ import annotations

import sqlite3

from rk.ids import new_id
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection

BROWSE_ACTIONS = frozenset({"search", "open", "download", "login_wait", "captcha_wait"})


def record_browse(ps: ProjectStore, *, task_id: str | None, site: str, action: str, url: str, query: str | None = None,
                  result_count: int | None = None, screenshot_path: str | None = None, actor: str,
                  actor_kind: str = "kernel") -> str:
    if action not in BROWSE_ACTIONS:
        raise ValueError(f"unknown browse action {action!r}; expected one of {sorted(BROWSE_ACTIONS)}")
    bid = new_id("brw")
    ps.apply(NewEvent(type="browse.logged", entity_id=bid, actor_kind=actor_kind, actor=actor,
                      payload={"task_id": task_id, "site": site, "action": action, "url": url, "query": query,
                               "result_count": result_count, "screenshot_path": screenshot_path}))
    return bid


def list_browse(ps: ProjectStore, limit: int = 200) -> list[dict]:
    return [dict(r) for r in ps.db.execute("SELECT * FROM browse_log ORDER BY ts, id LIMIT ?", (limit,))]


@projection("browse.logged")
def _logged(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute("INSERT OR REPLACE INTO browse_log(id, task_id, site, action, url, query, result_count, "
                 "screenshot_path, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ev.entity_id, p["task_id"], p["site"], p["action"], p["url"], p["query"], p["result_count"],
                  p["screenshot_path"], ev.ts))
