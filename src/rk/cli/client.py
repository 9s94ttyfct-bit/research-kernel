"""Thin client for ``rk serve`` with auto-start."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx


class RkError(Exception):
    def __init__(self, type_: str, message: str, details: dict | None = None):
        super().__init__(f"{type_}: {message}")
        self.type = type_
        self.message = message
        self.details = details or {}


class RkClient:
    def __init__(self, base_url: str, token: str, timeout: float = 300.0):
        self.base_url = base_url
        self._http = httpx.Client(base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {token}"})

    @classmethod
    def from_home(cls, home: Path) -> RkClient:
        port = (home / "serve.port").read_text().strip()
        token = (home / "serve.token").read_text().strip()
        return cls(f"http://127.0.0.1:{port}", token)

    def healthy(self) -> bool:
        try:
            return self._http.get("/healthz", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def call(self, name: str, args: dict | None = None, *, write: bool = False, idempotency_key: str | None = None,
             attempts: int = 3) -> dict:
        """Transport failures (connection reset, timeout) are retried with the same idempotency key, so a write
        that reached the server before the connection dropped is not applied twice."""
        key = idempotency_key or (uuid.uuid4().hex if write else None)
        payload = {"args": args or {}, "idempotency_key": key}
        for attempt in range(attempts):
            try:
                r = self._http.post(f"/api/tools/{name}", json=payload)
                break
            except httpx.TransportError as e:
                if attempt == attempts - 1:
                    raise RkError("transport_error", f"{type(e).__name__}: {e}") from e
                time.sleep(0.5 * 2**attempt)
            except httpx.HTTPError as e:
                raise RkError("http_error", f"{type(e).__name__}: {e}") from e
        try:
            body = r.json()
        except ValueError as e:
            raise RkError(f"http_{r.status_code}", r.text[:500]) from e
        if not body.get("ok"):
            err = body.get("error") or {}
            raise RkError(err.get("type", f"http_{r.status_code}"), err.get("message", r.text), err.get("details"))
        return body["result"]

    def tools(self) -> list[dict]:
        return self._http.get("/api/tools").json()["tools"]

    def close(self) -> None:
        self._http.close()


def ensure_server(home: Path, timeout_s: float = 20.0) -> RkClient:
    if (home / "serve.port").exists() and (home / "serve.token").exists():
        c = RkClient.from_home(home)
        if c.healthy():
            return c
        c.close()
    home.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(exist_ok=True)
    log = open(home / "logs" / "serve.log", "ab")  # noqa: SIM115 - handed to the child process
    env = {**os.environ, "RK_HOME": str(home)}
    for stale in ("serve.port",):
        (home / stale).unlink(missing_ok=True)
    subprocess.Popen([sys.executable, "-m", "rk.cli.main", "serve"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     env=env, start_new_session=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (home / "serve.port").exists() and (home / "serve.token").exists():
            c = RkClient.from_home(home)
            if c.healthy():
                return c
            c.close()
        time.sleep(0.2)
    raise RkError("serve_unavailable", f"rk serve did not start; see {home / 'logs' / 'serve.log'}")
