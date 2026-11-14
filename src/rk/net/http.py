"""Polite HTTP client: response cache, per-host pacing, retries, and API budget tracking."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel

from rk.canon import canonical_json, sha256_hex

DEFAULT_MIN_INTERVALS: dict[str, float] = {
    "api.crossref.org": 0.35,
    "api.openalex.org": 0.12,
    "www.ebi.ac.uk": 0.12,
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "www.ncbi.nlm.nih.gov": 0.34,
    "export.arxiv.org": 3.0,
    "arxiv.org": 15.0,
    "api.semanticscholar.org": 1.0,
    "api.biorxiv.org": 0.5,
}
DEFAULT_INTERVAL = 1.0
SECRET_PARAMS = frozenset({"api_key", "key", "apikey", "token"})
MAX_RETRIES = 4
MAX_RETRY_WAIT_S = 60.0  # a 429 asking for longer raises BudgetExhausted (the run pauses) instead of sleeping
HOST_WAIT = "host-wait:"  # budgets key prefix: a host's long 429 wait (``HttpClient._check_host_wait``)


class HttpError(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url

    @property
    def why(self) -> str:
        """The failure in a few words, for a reason line ("HTTP 404")."""
        return f"HTTP {self.status}"


class BadBody(HttpError):
    """A 200 whose body is not what the endpoint returns (e.g. an HTML error page instead of JSON): a source error
    like a 5xx, not a parse error."""

    def __init__(self, url: str, why: str):
        Exception.__init__(self, f"HTTP 200 for {url}: {why}")
        self.status = 200
        self.url = url


class RedirectLoop(HttpError):
    """The URL redirected more times than the client follows (a loop, e.g. a cookie or login wall): no status was
    answered, so ``status`` is 300 (a redirect the client stopped following), outside the transient statuses
    (``rk.discovery.boundary``), and it is not retried. srrepro10, 2026-11-13: httpx's ``TooManyRedirects`` escaped
    every caller's ``except HttpError``."""

    def __init__(self, url: str):
        Exception.__init__(self, f"too many redirects for {url}")
        self.status = 300
        self.url = url

    @property
    def why(self) -> str:
        return "too many redirects"


class BudgetExhausted(Exception):
    def __init__(self, budget_key: str, reset_at: float):
        super().__init__(f"API budget exhausted for {budget_key}; resets at {reset_at:.0f}")
        self.budget_key = budget_key
        self.reset_at = reset_at


class HttpResult(BaseModel):
    status: int
    headers: dict[str, str]
    body: bytes
    from_cache: bool
    url: str

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def _public_params(params: dict | None) -> dict:
    return {k: v for k, v in (params or {}).items() if k.lower() not in SECRET_PARAMS and v is not None}


class HttpClient:
    def __init__(
        self,
        cache_db: Path,
        *,
        user_agent: str,
        min_intervals: dict[str, float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        transport: httpx.BaseTransport | None = None,
    ):
        cache_db.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(cache_db, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, url TEXT, status INTEGER, headers TEXT,"
            " body BLOB, fetched_at REAL)"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS budgets (key TEXT PRIMARY KEY, remaining REAL, reset_at REAL)")
        self._intervals = {**DEFAULT_MIN_INTERVALS, **(min_intervals or {})}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._sleep, self._now, self._wall = sleep, now, wall
        self._client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
            transport=transport,
        )

    # ---- cache / budget storage ----
    def _key(self, url: str, params: dict | None) -> str:
        return sha256_hex(url + "?" + canonical_json(sorted(_public_params(params).items())))

    def _cached(self, key: str, ttl_s: int) -> HttpResult | None:
        with self._lock:
            r = self._db.execute("SELECT url, status, headers, body, fetched_at FROM http_cache WHERE key=?", (key,)).fetchone()
        if not r or self._wall() - r[4] > ttl_s:
            return None
        return HttpResult(status=r[1], headers=json.loads(r[2]), body=r[3], from_cache=True, url=r[0])

    def _store(self, key: str, res: HttpResult) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO http_cache(key, url, status, headers, body, fetched_at) VALUES (?,?,?,?,?,?)",
                (key, res.url, res.status, json.dumps(res.headers), res.body, self._wall()),
            )

    def forget(self, url: str, params: dict | None = None) -> None:
        """Drop the cached response of ``url`` with ``params`` (a 200 whose body turned out to be an error)."""
        with self._lock:
            self._db.execute("DELETE FROM http_cache WHERE key=?", (self._key(url, params),))

    def _check_budget(self, budget_key: str | None) -> None:
        if not budget_key:
            return
        with self._lock:
            r = self._db.execute("SELECT remaining, reset_at FROM budgets WHERE key=?", (budget_key,)).fetchone()
        if r and r[0] is not None and r[0] <= 0 and (r[1] or 0) > self._wall():
            raise BudgetExhausted(budget_key, r[1])

    def _record_budget(self, budget_key: str | None, headers: httpx.Headers) -> None:
        if not budget_key or "x-ratelimit-remaining" not in headers:
            return
        try:
            remaining = float(headers["x-ratelimit-remaining"])
            reset = float(headers.get("x-ratelimit-reset", "0") or 0)
        except ValueError:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO budgets(key, remaining, reset_at) VALUES (?,?,?)",
                (budget_key, remaining, self._wall() + reset),
            )

    def _check_host_wait(self, host: str) -> None:
        """A host whose 429 asked for longer than a request waits (``MAX_RETRY_WAIT_S``) is not asked again before
        then: ``BudgetExhausted`` at once, so each later request to it (e.g. another record's open-access URL on the
        same repository) costs no request. Review of oa-host-429, 2026-11-13."""
        with self._lock:
            r = self._db.execute("SELECT reset_at FROM budgets WHERE key=?", (HOST_WAIT + host,)).fetchone()
        if r and r[0] > self._wall():
            raise BudgetExhausted(host, r[0])

    def budget(self, budget_key: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT remaining, reset_at FROM budgets WHERE key=?", (budget_key,)).fetchone()
        return {"remaining": r[0], "reset_at": r[1]} if r else None

    # ---- pacing ----
    def _pace(self, host: str) -> None:
        interval = self._intervals.get(host, DEFAULT_INTERVAL)
        last = self._last.get(host)
        if last is not None:
            wait = interval - (self._now() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._now()

    # ---- public API ----
    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl_s: int = 7 * 86400,
        headers: dict | None = None,
        budget_key: str | None = None,
        use_cache: bool = True,
    ) -> HttpResult:
        key = self._key(url, params)
        if use_cache and ttl_s > 0 and (hit := self._cached(key, ttl_s)):
            return hit
        self._check_budget(budget_key)
        host = urlsplit(url).hostname or ""
        if budget_key is None:
            self._check_host_wait(host)
        host_lock = self._host_locks.setdefault(host, threading.Lock())
        public_url = url + ("?" + urlencode(_public_params(params)) if _public_params(params) else "")
        attempt = 0
        while True:
            with host_lock:
                self._pace(host)
                try:
                    resp = self._client.get(url, params={k: v for k, v in (params or {}).items() if v is not None},
                                            headers=headers)
                    status = resp.status_code
                except httpx.TooManyRedirects as e:
                    raise RedirectLoop(public_url) from e
                except httpx.TransportError:
                    resp, status = None, 0
            if resp is not None:
                self._record_budget(budget_key, resp.headers)
            if status == 429 or status >= 500 or status == 0:
                if status == 429:
                    self._check_budget(budget_key)  # the 429 spent the recorded budget: the run pauses (resume after it)
                if attempt >= MAX_RETRIES:
                    raise HttpError(status, public_url)
                delay = 2.0**attempt
                if resp is not None and (ra := resp.headers.get("retry-after")):
                    try:
                        delay = float(ra)
                    except ValueError:
                        pass
                if delay > MAX_RETRY_WAIT_S:  # never sleep for hours inside a request
                    if status == 429:  # a rate limit that long is a spent budget: the run pauses (resume after it)
                        reset_at = self._wall() + delay
                        if budget_key is None:  # the host is not asked again before then (``_check_host_wait``)
                            with self._lock:
                                self._db.execute("INSERT OR REPLACE INTO budgets(key, remaining, reset_at) "
                                                 "VALUES (?,?,?)", (HOST_WAIT + host, 0, reset_at))
                        raise BudgetExhausted(budget_key or host, reset_at)
                    delay = MAX_RETRY_WAIT_S  # a server error stays transient: retried, then an HttpError
                self._sleep(delay)
                attempt += 1
                continue
            res = HttpResult(
                status=status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=resp.content,
                from_cache=False,
                url=public_url,
            )
            if status == 200 and ttl_s > 0:
                self._store(key, res)
            return res

    def get_json(self, url: str, params: dict | None = None, **kw) -> tuple[Any, HttpResult]:
        """200 -> parsed JSON; 404 -> (None, res); any other status raises HttpError (never 'not found'), and so does a
        200 whose body is not JSON (``BadBody``; the body is dropped from the cache)."""
        res = self.get(url, params, **kw)
        if res.status == 404:
            return None, res
        if res.status != 200:
            raise HttpError(res.status, res.url)
        try:
            return res.json(), res
        except ValueError as e:  # JSONDecodeError, or bytes that are no UTF-8
            self.forget(url, params)
            raise BadBody(res.url, f"the body is not JSON ({type(e).__name__})") from e

    def close(self) -> None:
        self._client.close()
        self._db.close()
