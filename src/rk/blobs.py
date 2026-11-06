"""Content-addressed, write-once blob store."""

from __future__ import annotations

import os
from pathlib import Path

from rk.canon import sha256_hex


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, sha: str) -> Path:
        return self.root / "sha256" / sha[:2] / sha

    def has(self, sha: str) -> bool:
        return self.path(sha).exists()

    def put(self, data: bytes) -> str:
        sha = sha256_hex(data)
        p = self.path(sha)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return sha
