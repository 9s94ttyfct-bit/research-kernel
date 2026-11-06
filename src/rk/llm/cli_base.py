"""Shared plumbing for agentic-CLI drivers: binary lookup, version pin, empty temp cwd, process-group timeouts."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rk.config import Settings, rk_home
from rk.llm.types import Availability


@dataclass
class Completed:
    code: int | None  # None: killed on timeout
    stdout: str
    stderr: str
    duration_s: float


def mcp_server_spec(project: str, role: str, token: str) -> dict:
    """How the CLI launches ``rk mcp`` for this task (same interpreter, same RK_HOME)."""
    return {"command": sys.executable, "args": ["-m", "rk.cli.main", "mcp", "--project", project, "--role", role],
            "env": {"RK_TASK_TOKEN": token, "RK_HOME": str(rk_home())}}


class AgenticCli:
    kind = "agentic_cli"
    binary: str  # executable name
    pinned: str  # version the flags were verified against

    def __init__(self, settings: Settings, model: str | None = None, *,
                 workdir_factory: Callable[[], Path] | None = None):
        self.settings = settings
        self.model = model
        self._workdir_factory = workdir_factory or (lambda: Path(tempfile.mkdtemp(prefix=f"rk-{self.name}-")))

    def bin_path(self) -> str | None:
        return self.settings.cli_paths.get(self.binary) or shutil.which(self.binary)

    @staticmethod
    def parse_version(text: str) -> str:
        raise NotImplementedError

    def available(self) -> Availability:
        path = self.bin_path()
        if path is None:
            return Availability(ok=False, reason=f"{self.binary} not found on PATH")
        try:
            out = subprocess.run([path, "--version"], check=False, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            return Availability(ok=False, reason=f"{self.binary} --version failed: {e}")
        if out.returncode != 0:
            return Availability(ok=False, reason=f"{self.binary} --version exited {out.returncode}")
        version = self.parse_version(out.stdout.strip())
        if version != self.pinned:
            return Availability(ok=True, version=version, reason=f"version {version} != pinned {self.pinned}; "
                                                                  "flags may have changed")
        return Availability(ok=True, version=version)

    def workdir(self) -> Path:
        return self._workdir_factory()

    def execute(self, argv: list[str], cwd: Path, timeout_s: float) -> Completed:
        start = time.monotonic()
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout_s)
            code: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # the CLI and the rk mcp child it spawned
            out, err = proc.communicate()
            code = None
        return Completed(code, out, err, round(time.monotonic() - start, 3))

    @staticmethod
    def cleanup(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
