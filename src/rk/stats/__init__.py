"""Statistics for synthesis: effect sizes (``effects``), inverse-variance pooling (``meta``) and figures (``plots``).

Meta-analysis stays off until ``rk eval stats`` has reproduced published reference analyses with this exact code
(spec §9.1 V-STATS): ``STATS_VERSION`` is the SHA-256 over the module's source files, the validation records it in
``Settings.stats_validated``, and ``stats_enabled`` is true only while the two agree. Any edit to a file of this
package changes the version and so disables pooling until the evaluation passes again."""

from __future__ import annotations

import hashlib
from pathlib import Path


def _source_version() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


STATS_VERSION: str = _source_version()


def stats_enabled(settings) -> bool:
    """True only when a passing ``rk eval stats --write-config`` recorded the current ``STATS_VERSION``."""
    v = settings.stats_validated
    return isinstance(v, dict) and v.get("version") == STATS_VERSION
