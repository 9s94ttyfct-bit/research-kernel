"""Workbench authentication (spec §8.7, §9.3). Separate from ``serve.token`` and from task/lead tokens.

``rk serve`` (or ``rk open``) makes a one-time login code; ``GET /ui/login?code=…`` exchanges it for a random session
cookie ``rk_ui`` (HttpOnly, SameSite=Strict). Codes live 120 s and work once; sessions live as long as this
``rk serve``. The serve token never authenticates ``/ui``, and the cookie never authenticates ``/api``. Requests that
change state must also carry ``Origin`` equal to the server's own origin, so other pages (including agent tabs in the
built-in browser) cannot drive the Workbench."""

from __future__ import annotations

import hmac
import secrets
import threading
import time

COOKIE = "rk_ui"
CODE_TTL_S = 120.0


class UiAuth:
    def __init__(self, origin: str, *, clock=time.monotonic):
        self.origin = origin.rstrip("/")
        self._clock = clock
        self._codes: dict[str, float] = {}  # code -> created
        self._sessions: set[str] = set()
        self._lock = threading.Lock()

    def new_login_code(self) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = self._clock()
        return code

    def login_url(self) -> str:
        return f"{self.origin}/ui/login?code={self.new_login_code()}"

    def redeem(self, code: str) -> str | None:
        """A new session id for a fresh, unused code; None otherwise."""
        with self._lock:
            created = self._codes.pop(code, None)
            if created is None or self._clock() - created > CODE_TTL_S:
                return None
            sid = secrets.token_urlsafe(32)
            self._sessions.add(sid)
            return sid

    def check_cookie(self, value: str | None) -> bool:
        if not value:
            return False
        with self._lock:
            return any(hmac.compare_digest(value, s) for s in self._sessions)

    def logout(self, value: str | None) -> None:
        with self._lock:
            self._sessions.discard(value or "")

    def check_origin(self, origin: str | None) -> bool:
        return bool(origin) and hmac.compare_digest(origin.rstrip("/"), self.origin)
