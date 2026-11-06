"""Hourly call caps for subscription drivers (spec §7.2): calls are counted, not tokens."""

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
        self._lock = threading.Lock()
        self._clock = clock

    def used(self, driver: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM call_log WHERE driver=? AND ts>?",
                                    (driver, self._clock() - WINDOW_S)).fetchone()[0]

    def acquire(self, driver: str, cap: int | None) -> bool:
        """Record one call if the driver is under its cap for the last hour; uncapped drivers always pass."""
        if cap is None:
            return True
        with self._lock:
            now = self._clock()
            self._db.execute("DELETE FROM call_log WHERE ts<=?", (now - WINDOW_S,))
            n = self._db.execute("SELECT COUNT(*) FROM call_log WHERE driver=?", (driver,)).fetchone()[0]
            if n >= cap:
                return False
            self._db.execute("INSERT INTO call_log(driver, ts) VALUES (?, ?)", (driver, now))
            return True

    def close(self) -> None:
        self._db.close()
