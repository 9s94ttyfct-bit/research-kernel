"""Shared helpers for the discovery-stage tests (Plan 4)."""

from __future__ import annotations

from rk.config import Settings, save_settings
from rk.discovery.models import Intake
from rk.discovery.store import record_intake
from rk.workspace import Kernel

DEFAULT_INTAKE = {"audience": "clinicians and health-service researchers", "paper_type": "critical_review",
                  "discovery_mode": "d2", "production_mode": "auto", "language": "en", "topics_n": 6}


def make_project(tmp_home, replay_http, *, slug: str = "demo", **intake_overrides):
    """A kernel with one project whose intake is recorded. Returns ``(kernel, ps, lib)``; the caller closes the
    kernel."""
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    k.create_project(slug, "Demo project", actor="tester")
    ps, lib = k.project(slug)
    record_intake(ps, Intake(**{**DEFAULT_INTAKE, **intake_overrides}), actor="tester", actor_kind="human")
    return k, ps, lib
