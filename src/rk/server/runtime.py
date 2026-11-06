"""``rk serve`` runtime files: single-instance lock, port and bearer token."""

from __future__ import annotations

import fcntl
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path


class AlreadyRunning(Exception):
    def __init__(self, pid: int | None):
        super().__init__(f"rk serve already running (pid {pid or 'unknown'})")
        self.pid = pid


@dataclass
class ServeRuntime:
    home: Path
    port: int
    token: str
    sock: socket.socket
    lock_fd: int


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def acquire_serve_lock(home: Path) -> ServeRuntime:
    """Hold an exclusive ``flock`` on ``serve.lock`` for the server's lifetime. The kernel drops it when the
    process exits, so a crashed server never blocks the next one and a reused PID is never mistaken for it."""
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / "serve.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        held = os.read(fd, 32).decode(errors="replace").strip()
        os.close(fd)
        raise AlreadyRunning(int(held) if held.isdigit() else None) from None
    os.ftruncate(fd, 0)
    os.pwrite(fd, str(os.getpid()).encode(), 0)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    _write_private(home / "serve.token", token)
    _write_atomic(home / "serve.port", str(port))
    return ServeRuntime(home=home, port=port, token=token, sock=sock, lock_fd=fd)


def release(rt: ServeRuntime) -> None:
    port_file = rt.home / "serve.port"
    try:
        if port_file.read_text().strip() == str(rt.port):  # never remove a newer server's port file
            port_file.unlink()
    except FileNotFoundError:
        pass
    rt.sock.close()
    os.close(rt.lock_fd)  # releases the flock
