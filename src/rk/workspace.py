"""Kernel runtime: projects under ``$RK_HOME/projects``, shared HTTP client and source context."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from rk import __version__
from rk.config import Settings, load_settings, rk_home
from rk.ids import new_id
from rk.library.service import Library
from rk.net.http import HttpClient
from rk.sources.resolver import SourceContext
from rk.store.events import NewEvent
from rk.store.project import ProjectStore

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class Kernel:
    def __init__(self, home: Path | None = None, http: HttpClient | None = None):
        self.home = Path(home) if home else rk_home()
        self.projects_dir = self.home / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._http = http
        self._open: dict[str, tuple[ProjectStore, Library]] = {}
        self._lock = threading.Lock()

    def settings(self) -> Settings:
        return load_settings()

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            s = self.settings()
            ua = f"rk/{__version__}" + (f" (mailto:{s.email})" if s.email else "")
            self._http = HttpClient(self.home / "cache" / "http.sqlite", user_agent=ua)
        return self._http

    def ctx(self) -> SourceContext:
        return SourceContext(http=self.http, settings=self.settings())

    def project_dir(self, slug: str) -> Path:
        return self.projects_dir / slug

    def list_projects(self) -> list[dict]:
        out = []
        for d in sorted(self.projects_dir.iterdir()):
            if (d / "rk.sqlite").exists():
                ps, _ = self.project(d.name)
                out.append({"slug": d.name, "title": ps.meta("title"), "project_id": ps.project_id})
        return out

    def create_project(self, slug: str, title: str, *, field: str | None = None, paper_type: str | None = None,
                       actor: str) -> ProjectStore:
        if not SLUG_RE.match(slug):
            raise ValueError("slug must be 2-63 chars of lowercase letters, digits and dashes")
        with self._lock:  # exists-check and creation must not interleave with another create of the same slug
            if (self.project_dir(slug) / "rk.sqlite").exists():
                raise FileExistsError(f"project exists: {slug}")
            pid = new_id("prj")
            ps = ProjectStore(self.project_dir(slug), pid)
            ps.apply(NewEvent(type="project.created", entity_id=pid, actor_kind="human", actor=actor,
                              payload={"slug": slug, "title": title, "field": field, "paper_type": paper_type}))
            lib = Library(ps)
            lib.ensure_default_collections(actor="kernel")
            self._open[slug] = (ps, lib)
        return ps

    def project(self, slug: str) -> tuple[ProjectStore, Library]:
        if not SLUG_RE.match(slug):  # slugs become directory names: never let '../x' or '/abs' through
            raise LookupError(f"invalid project slug: {slug!r}")
        with self._lock:
            if slug in self._open:
                return self._open[slug]
            d = self.project_dir(slug)
            if not (d / "rk.sqlite").exists():
                raise LookupError(f"no such project: {slug}")
            probe = ProjectStore(d, "prj_unknown")
            pid = probe.meta("project_id")
            probe.close()
            if not pid:
                raise LookupError(f"project {slug} has no project.created event")
            ps = ProjectStore(d, pid)
            pair = (ps, Library(ps))
            self._open[slug] = pair
            return pair

    def close(self) -> None:
        with self._lock:
            for ps, _ in self._open.values():
                ps.close()
            self._open.clear()
        if self._http is not None:
            self._http.close()
