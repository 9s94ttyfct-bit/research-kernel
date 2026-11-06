"""Thin client for ``rk serve`` with auto-start."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
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
             attempts: int = 3, timeout: float | None = None) -> dict:
        """Transport failures (connection reset, timeout) are retried with the same idempotency key, so a write
        that reached the server before the connection dropped is not applied twice. ``timeout`` overrides the client
        default for long calls (role tasks)."""
        key = idempotency_key or (uuid.uuid4().hex if write else None)
        payload = {"args": args or {}, "idempotency_key": key}
        extra = {"timeout": timeout} if timeout is not None else {}
        for attempt in range(attempts):
            try:
                r = self._http.post(f"/api/tools/{name}", json=payload, **extra)
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

    # ---- chat sessions ----
    def chat_open(self, driver: str, role: str, project: str) -> str:
        r = self._http.post("/api/chat", json={"driver": driver, "role": role, "project": project})
        body = r.json()
        if not body.get("ok"):
            err = body.get("error") or {"type": f"http_{r.status_code}", "message": str(body.get("detail", body))}
            raise RkError(err["type"], err["message"])
        return body["result"]["chat_id"]

    def chat_send(self, chat_id: str, text: str) -> Iterator[dict]:
        with self._http.stream("POST", f"/api/chat/{chat_id}/send", json={"text": text}, timeout=None) as r:
            if r.status_code != 200:
                r.read()
                raise RkError(f"http_{r.status_code}", r.text[:500])
            for line in r.iter_lines():
                if line.strip():
                    yield json.loads(line)

    def chat_close(self, chat_id: str) -> None:
        self._http.delete(f"/api/chat/{chat_id}")

    def _post_plain(self, path: str, json_body: dict | None = None) -> dict:
        r = self._http.post(path, json=json_body)
        body = r.json()
        if r.status_code != 200 or not body.get("ok"):
            raise RkError(f"http_{r.status_code}", str(body.get("detail") or body.get("error") or body))
        return body["result"]

    def ui_link(self) -> str:
        """A one-time Workbench login URL."""
        return self._post_plain("/api/ui/link")["url"]

    def open_human(self, url: str) -> str:
        """Open ``url`` in a tab of the built-in browser that belongs to you (not to an agent)."""
        return self._post_plain("/api/browser/open-human", {"url": url})["tab"]

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
    # A stale serve.port (crashed server) is left in place: the new server replaces it atomically once bound,
    # and deleting it here could remove the port file of a server another client just started.
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
