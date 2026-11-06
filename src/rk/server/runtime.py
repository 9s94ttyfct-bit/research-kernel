"""``rk serve`` runtime files: single-instance lock, port and bearer token."""

from __future__ import annotations

import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path


class AlreadyRunning(Exception):
    def __init__(self, pid: int):
        super().__init__(f"rk serve already running (pid {pid})")
        self.pid = pid


@dataclass
class ServeRuntime:
    home: Path
    port: int
    token: str
    sock: socket.socket


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def acquire_serve_lock(home: Path) -> ServeRuntime:
    home.mkdir(parents=True, exist_ok=True)
    lock = home / "serve.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and _alive(pid):
            raise AlreadyRunning(pid)
    lock.write_text(str(os.getpid()))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    _write_private(home / "serve.token", token)
    (home / "serve.port").write_text(str(port))
    return ServeRuntime(home=home, port=port, token=token, sock=sock)


def release(rt: ServeRuntime) -> None:
    for name in ("serve.lock", "serve.port"):
        p = rt.home / name
        try:
            if name != "serve.lock" or p.read_text().strip() == str(os.getpid()):
                p.unlink()
        except FileNotFoundError:
            pass
    rt.sock.close()
