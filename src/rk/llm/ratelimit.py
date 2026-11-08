"""Hourly call caps for subscription drivers (spec §7.2): calls are counted, not tokens. A driver can also be blocked
until a time (``block_for``: its subscription's usage limit, ``rk.llm.limits``); it then gets no call, capped or not."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

WINDOW_S = 3600.0


class CallCaps:
    def __init__(self, db_path: Path, *, clock=time.time):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("CREATE TABLE IF NOT EXISTS call_log (driver TEXT NOT NULL, ts REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS blocked (driver TEXT PRIMARY KEY, until REAL NOT NULL)")
        self._lock = threading.Lock()
        self._clock = clock

    def used(self, driver: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM call_log WHERE driver=? AND ts>?",
                                    (driver, self._clock() - WINDOW_S)).fetchone()[0]

    def wait_s(self, driver: str) -> float:
        """Seconds until the driver may be called again: until its block ends, and until its oldest call in the window
        leaves it (0 when neither holds it)."""
        with self._lock:
            now = self._clock()
            oldest = self._db.execute("SELECT MIN(ts) FROM call_log WHERE driver=? AND ts>?",
                                      (driver, now - WINDOW_S)).fetchone()[0]
            until = self._blocked_until(driver)
        window = 0.0 if oldest is None else max(0.0, oldest + WINDOW_S - now)
        return max(window, until - now, 0.0)

    def block_for(self, driver: str, seconds: float) -> None:
        """No call for the driver for ``seconds`` from now (a later block already set stays)."""
        with self._lock:
            self._db.execute("INSERT INTO blocked(driver, until) VALUES (?, ?) ON CONFLICT(driver) DO UPDATE SET "
                             "until=MAX(until, excluded.until)", (driver, self._clock() + seconds))

    def _blocked_until(self, driver: str) -> float:
        r = self._db.execute("SELECT until FROM blocked WHERE driver=?", (driver,)).fetchone()
        return r[0] if r else 0.0

    def acquire(self, driver: str, cap: int | None) -> bool:
        """Record one call if the driver is not blocked and under its cap for the last hour; uncapped drivers pass
        unless blocked. The count and the insert are one write transaction (``BEGIN IMMEDIATE``), so processes sharing
        the call log (``rk serve`` and ``rk eval``) never overrun a cap."""
        if cap is None:
            with self._lock:
                return self._blocked_until(driver) <= self._clock()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                now = self._clock()
                self._db.execute("DELETE FROM call_log WHERE ts<=?", (now - WINDOW_S,))
                n = self._db.execute("SELECT COUNT(*) FROM call_log WHERE driver=?", (driver,)).fetchone()[0]
                ok = n < cap and self._blocked_until(driver) <= now
                if ok:
                    self._db.execute("INSERT INTO call_log(driver, ts) VALUES (?, ?)", (driver, now))
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
            return ok

    def close(self) -> None:
        self._db.close()
