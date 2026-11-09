"""Optional adapter: ``omp auth-gateway stdio`` speaks JSON lines carrying OpenAI chat-completion bodies.

Protocol (verified 2026-11-05): the child prints ``{"ready": true, ...}``, then for every request line
``{"id": n, "path": "/v1/chat/completions", "body": {...}}`` replies ``{"id": n, "status": 200, "body": {...}}``.
Replies are matched by id; one child process is shared and guarded by a lock.

The child runs with omp's model fallback off (``NO_FALLBACK_OVERLAY`` appended to ``PI_CONFIG_FILES``, verified
2026-11-09: a request for a model without a credential then returns 401 instead of an answer from the user's
``retry.fallbackChains``); a reply naming another model is still refused (``complete``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from importlib.resources import files

from rk.llm.chat.base import Msg, ToolDef, Turn
from rk.llm.chat.openai_chat import openai_body, openai_turn
from rk.llm.types import Availability, ProviderError

READY_TIMEOUT_S = 30.0
REPLY_TIMEOUT_S = 600.0
NO_FALLBACK_OVERLAY = str(files("rk.llm.chat").joinpath("omp_no_fallback.yml"))


def gateway_env() -> dict[str, str]:
    """The environment of the gateway child: this process's, with ``NO_FALLBACK_OVERLAY`` loaded last among the
    user's own omp settings overlays (``PI_CONFIG_FILES``), so it wins over them."""
    env = dict(os.environ)
    env["PI_CONFIG_FILES"] = os.pathsep.join(p for p in (env.get("PI_CONFIG_FILES"), NO_FALLBACK_OVERLAY) if p)
    return env


class OmpGatewayBackend:
    name = "omp_gateway"

    def __init__(self, model: str, *, omp_bin: str = "omp"):
        self.model, self.omp_bin = model, omp_bin
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()  # one request in flight per child: the stdio protocol is a single pipe
        self._next_id = 0
        self._pending: dict[int, dict] = {}

    def available(self) -> Availability:
        if shutil.which(self.omp_bin) is None:
            return Availability(ok=False, reason=f"omp not found ({self.omp_bin}); omp_gateway is optional")
        return Availability(ok=True)

    def _start(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            proc = subprocess.Popen([self.omp_bin, "auth-gateway", "stdio"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                    env=gateway_env())
        except OSError as e:
            raise ProviderError(f"cannot start omp auth-gateway: {e}") from e
        first = self._readline(proc, READY_TIMEOUT_S)
        if not first.get("ready"):
            proc.kill()
            raise ProviderError(f"omp auth-gateway did not report ready: {first}")
        self._proc = proc
        return proc

    @staticmethod
    def _readline(proc: subprocess.Popen, timeout: float) -> dict:
        box: dict = {}

        def read() -> None:
            box["line"] = proc.stdout.readline()

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            proc.kill()
            raise ProviderError(f"omp auth-gateway: no reply within {timeout:.0f}s")
        line = box.get("line", "")
        if not line:
            raise ProviderError("omp auth-gateway exited")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise ProviderError(f"omp auth-gateway sent non-JSON: {line[:200]}") from e

    def complete(self, messages: list[Msg], tools: list[ToolDef], json_schema: dict | None) -> Turn:
        with self._lock:
            proc = self._start()
            self._next_id += 1
            rid = self._next_id
            req = {"id": rid, "path": "/v1/chat/completions",
                   "body": openai_body(self.model, messages, tools, json_schema)}
            try:
                proc.stdin.write(json.dumps(req) + "\n")
                proc.stdin.flush()
            except OSError as e:
                raise ProviderError(f"omp auth-gateway pipe closed: {e}") from e
            reply = self._pending.pop(rid, None)
            while reply is None:
                msg = self._readline(proc, REPLY_TIMEOUT_S)
                if msg.get("id") == rid:
                    reply = msg
                elif "id" in msg:
                    self._pending[msg["id"]] = msg  # a reply for another request id: keep it, never consume it
        if reply.get("status") != 200:
            raise ProviderError(f"omp auth-gateway status {reply.get('status')}: {str(reply.get('body'))[:500]}")
        turn = openai_turn(reply["body"])
        # Model fallback is off in the child (gateway_env), but a reply naming another model (e.g. a Codex judge
        # answered by Claude) would silently change the vendor a judge or generator stands for: a failed call, never
        # accepted.
        if turn.model != self.model:
            raise ProviderError(f"omp auth-gateway answered with {turn.model or 'no model'} instead of {self.model}; "
                                "reply not accepted")
        return turn

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
