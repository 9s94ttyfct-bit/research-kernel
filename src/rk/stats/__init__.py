"""Statistics for synthesis: effect sizes (``effects``), inverse-variance pooling (``meta``) and figures (``plots``).

Meta-analysis stays off until ``rk eval stats`` has reproduced published reference analyses with this exact code
(spec §9.1 V-STATS): ``STATS_VERSION`` is the SHA-256 over the package's source files, its reference datasets
(``reference/*.json``) and the evaluation that checks them (``rk/eval/stats_eval.py``); the validation records it in
``Settings.stats_validated`` and ``stats_enabled`` is true only while the two agree. Any edit to one of those files
changes the version and so disables pooling until the evaluation passes again. Line endings are normalised
(``\\r\\n`` → ``\\n``) so a CRLF checkout has the same version."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REFERENCE_DIR = PACKAGE_DIR / "reference"
_RK_DIR = PACKAGE_DIR.parent


def version_files() -> list[Path]:
    """The files ``STATS_VERSION`` covers."""
    return [*PACKAGE_DIR.glob("*.py"), *REFERENCE_DIR.glob("*.json"), _RK_DIR / "eval" / "stats_eval.py"]


def version_of(files: Iterable[Path], root: Path) -> str:
    """SHA-256 over ``relative path NUL content NUL`` of ``files`` in path order, with CRLF read as LF."""
    h = hashlib.sha256()
    for rel, p in sorted((p.relative_to(root).as_posix(), p) for p in files):
        h.update(rel.encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


STATS_VERSION: str = version_of(version_files(), _RK_DIR)


def stats_enabled(settings) -> bool:
    """True only when a passing ``rk eval stats --write-config`` recorded the current ``STATS_VERSION``."""
    v = settings.stats_validated
    return isinstance(v, dict) and v.get("version") == STATS_VERSION
