"""Human-pace quotas for the built-in browser (spec §8.5). Limits are per machine and network address, not per project,
so the counters live in ``$RK_HOME/kernel.sqlite`` and survive restarts of ``rk serve``.

A scope is ``site:<name>`` (one search site), ``pdf:<domain>`` (PDF downloads from one publisher domain) or ``pdf:*``
(all browser PDF downloads). ``acquire`` holds a process-wide lock while it waits, so at most one browser task runs at a
time."""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from rk.browser import DailyCapReached, Paced

DEFAULT_BROWSER_LIMITS: dict[str, dict] = {
    "scholar": {"gap": [20, 45], "per_day": 60},
    "pdf_domain": {"gap": [30, 30], "per_day": 40},
    "pdf_total": {"gap": [0, 0], "per_day": 150},
    "default": {"gap": [5, 5], "per_day": None},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_quota (scope TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                                          PRIMARY KEY (scope, day));
CREATE TABLE IF NOT EXISTS browser_last (scope TEXT PRIMARY KEY, last_at REAL NOT NULL, next_gap REAL NOT NULL);
CREATE TABLE IF NOT EXISTS browser_pauses (site TEXT PRIMARY KEY, reason TEXT NOT NULL, project TEXT,
                                           checkpoint_id TEXT, created REAL NOT NULL);
"""

_LOCK = threading.Lock()  # browser task concurrency: 1 (spec §8.5)


def limit_key(scope: str) -> str:
    if scope == "pdf:*":
        return "pdf_total"
    if scope.startswith("pdf:"):
        return "pdf_domain"
    return scope.removeprefix("site:")


class Pacer:
    def __init__(self, db_path: Path, limits: dict[str, dict], *, max_wait_s: float,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[float, float], float] = random.uniform):
        # a partial override ({"scholar": {"per_day": 30}}) keeps the default gap of that entry
        self.limits = {**DEFAULT_BROWSER_LIMITS,
                       **{k: {**DEFAULT_BROWSER_LIMITS.get(k, DEFAULT_BROWSER_LIMITS["default"]), **v}
                          for k, v in limits.items()}}
        self.max_wait_s = max_wait_s
        self._clock, self._sleep, self._rand = clock, sleep, rand
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db_lock = threading.RLock()  # one connection shared by the thread pool

    def close(self) -> None:
        self._db.close()

    def _limit(self, scope: str) -> dict:
        return self.limits.get(limit_key(scope)) or self.limits["default"]

    def _day(self, t: float) -> str:
        return dt.datetime.fromtimestamp(t).date().isoformat()

    def _reset_at(self, t: float) -> str:
        d = dt.datetime.fromtimestamp(t).date() + dt.timedelta(days=1)
        return dt.datetime.combine(d, dt.time()).isoformat(timespec="minutes")

    def _count(self, scope: str, day: str) -> int:
        r = self._db.execute("SELECT count FROM browser_quota WHERE scope=? AND day=?", (scope, day)).fetchone()
        return r["count"] if r else 0

    def _wait(self, scope: str, now: float) -> float:
        r = self._db.execute("SELECT last_at, next_gap FROM browser_last WHERE scope=?", (scope,)).fetchone()
        return max(0.0, r["last_at"] + r["next_gap"] - now) if r else 0.0

    def _check(self, scopes: list[str], now: float) -> tuple[str | None, float]:
        """Raise when a daily cap is reached; else return the scope that needs the longest wait and that wait."""
        day = self._day(now)
        worst, longest = None, 0.0
        for s in scopes:
            per_day = self._limit(s).get("per_day")
            if per_day is not None and self._count(s, day) >= per_day:
                raise DailyCapReached(s, per_day, self._reset_at(now))
            w = self._wait(s, now)
            if w > longest:
                worst, longest = s, w
        return worst, longest

    def acquire(self, scopes: list[str]) -> None:
        """Block until every scope allows one more request, then count it. Never waits longer than ``max_wait_s``
        (for the gap or for another browser task): raises ``Paced`` instead, and ``DailyCapReached`` when a cap is hit;
        in both cases nothing is counted."""
        if not _LOCK.acquire(timeout=self.max_wait_s):
            raise Paced(scopes[0] if scopes else "browser", self.max_wait_s)
        try:
            with self._db_lock:
                worst, wait = self._check(scopes, self._clock())
            if wait > self.max_wait_s:
                raise Paced(worst, wait)
            if wait > 0:
                self._sleep(wait)
            now = self._clock()
            with self._db_lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._check(scopes, now)  # the day may have changed while sleeping
                    day = self._day(now)
                    for s in scopes:
                        lo, hi = self._limit(s).get("gap", [0, 0])
                        self._db.execute("INSERT INTO browser_quota(scope, day, count) VALUES (?,?,1) "
                                         "ON CONFLICT(scope, day) DO UPDATE SET count = count + 1", (s, day))
                        self._db.execute("INSERT OR REPLACE INTO browser_last(scope, last_at, next_gap) "
                                         "VALUES (?,?,?)", (s, now, float(self._rand(lo, hi)) if hi > 0 else 0.0))
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
        finally:
            _LOCK.release()

    def pause(self, site: str, reason: str, *, project: str | None, checkpoint_id: str | None) -> None:
        with self._db_lock:
            self._db.execute("INSERT OR REPLACE INTO browser_pauses(site, reason, project, checkpoint_id, created) "
                             "VALUES (?,?,?,?,?)", (site, reason, project, checkpoint_id, self._clock()))

    def paused(self, site: str) -> dict | None:
        with self._db_lock:
            r = self._db.execute("SELECT * FROM browser_pauses WHERE site=?", (site,)).fetchone()
        return dict(r) if r else None

    def unpause(self, site: str) -> None:
        with self._db_lock:
            self._db.execute("DELETE FROM browser_pauses WHERE site=?", (site,))

    def status(self) -> dict:
        day = self._day(self._clock())
        with self._db_lock:
            rows = self._db.execute("SELECT scope, count FROM browser_quota WHERE day=?", (day,)).fetchall()
            paused = [dict(r) for r in self._db.execute("SELECT * FROM browser_pauses ORDER BY created")]
        quotas = {r["scope"]: {"count": r["count"], "per_day": self._limit(r["scope"]).get("per_day"), "day": day}
                  for r in rows}
        return {"quotas": quotas, "paused_sites": paused}
