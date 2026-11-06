"""Shared helpers for the discovery-stage tests (Plan 4)."""

from __future__ import annotations

import json

from pydantic import BaseModel

from rk.config import DEFAULT_DRIVERS, Settings, save_settings
from rk.discovery.models import Intake
from rk.discovery.store import record_intake
from rk.fulltext import FulltextResult, _record_snapshot, latest_snapshot
from rk.ids import new_id
from rk.library.models import ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import TaskResult
from rk.text.layer import Page, TextLayer
from rk.verify.retract import RetractionInfo
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


def add_text_layer(ps, item_id: str, pages: list[str]) -> FulltextResult:
    """Record a text layer (one string per page) as the item's latest snapshot."""
    layer = TextLayer(source_kind="html", pages=[Page(n=i, text=t) for i, t in enumerate(pages, 1)], sha256="")
    return _record_snapshot(ps, item_id, "\f".join(pages).encode(), layer, "html", None, "tester")


class ScriptedExecutor:
    """A ``TaskExecutor`` that replays scripted outputs. ``script`` maps ``(role, schema name)`` to the outputs, popped
    in order; ``drivers`` overrides the default driver per role (provider/model of the result, so ``vendor_of`` gives
    the vendor). Every call is recorded in ``calls``."""

    def __init__(self, script: dict[tuple[str, str], list[dict]], *, drivers: dict[str, str] | None = None):
        self.script = {k: list(v) for k, v in script.items()}
        self.drivers = {**DEFAULT_DRIVERS, **(drivers or {})}
        self.calls: list[dict] = []

    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None) -> TaskResult:
        key = (role, output_schema.__name__)
        self.calls.append({"role": role, "schema": output_schema.__name__, "instructions": instructions,
                           "inputs": inputs, "stage": stage, "attempt": attempt, "tools": tools})
        queue = self.script.get(key)
        if not queue:
            raise AssertionError(f"no scripted output left for {key}")
        out = queue.pop(0)
        spec = DriverSpec.parse(self.drivers[role])
        return TaskResult(new_id("tsk"), True, output_schema.model_validate(out), json.dumps(out), spec.provider,
                          spec.model, 0.0, None)


class FakeSources:
    """A deterministic ``DiscoverySources``. ``search`` returns, for every key of ``items`` that occurs in the query
    (case-insensitive), its items, in key order, de-duplicated by title, cut to ``limit``. Per-item presets are keyed
    by item title and need ``lib`` (the project library): ``statuses`` (``verify`` result, also written to the item's
    ``ref_status``; default ``default_status``), ``retractions``, ``citing`` (``cited_by``, filtered to
    ``year > after_year``) and ``fulltexts`` (pages recorded as the text layer by ``fulltext``). ``year_counts`` is keyed
    by query. Every call is recorded in ``calls``."""

    def __init__(self, items: dict[str, list[ItemIn]], *, lib=None, s2: bool = False,
                 statuses: dict[str, str] | None = None, default_status: str = "pass",
                 year_counts: dict[str, dict[int, int]] | None = None,
                 retractions: dict[str, RetractionInfo] | None = None, citing: dict[str, list[ItemIn]] | None = None,
                 fulltexts: dict[str, list[str]] | None = None):
        self.items, self.lib, self.s2 = items, lib, s2
        self.statuses, self.default_status = statuses or {}, default_status
        self.counts, self.retractions = year_counts or {}, retractions or {}
        self.citing, self.fulltexts = citing or {}, fulltexts or {}
        self.calls: list[tuple] = []

    def _title(self, item_id: str) -> str:
        if self.lib is None:
            raise AssertionError("FakeSources needs lib= for per-item calls")
        return self.lib.get(item_id).title

    def sources(self) -> list[str]:
        return ["openalex"] + (["s2"] if self.s2 else []) + ["europepmc", "pubmed", "arxiv"]

    def search(self, query: str, source: str, limit: int = 25) -> list[ItemIn]:
        self.calls.append(("search", query, source, limit))
        out: list[ItemIn] = []
        for key, hits in self.items.items():
            if key.lower() in query.lower():
                out += [h for h in hits if h.title not in {o.title for o in out}]
        return out[:limit]

    def year_counts(self, query: str) -> dict[int, int]:
        self.calls.append(("year_counts", query))
        return dict(self.counts.get(query, {}))

    def verify(self, item_id: str) -> str:
        self.calls.append(("verify", item_id))
        status = self.statuses.get(self._title(item_id), self.default_status)
        self.lib.update(item_id, ref_status=status, actor="fake-sources")
        return status

    def cited_by(self, item_id: str, after_year: int | None) -> list[ItemIn]:
        self.calls.append(("cited_by", item_id, after_year))
        hits = self.citing.get(self._title(item_id), [])
        return [h for h in hits if after_year is None or (h.year is not None and h.year > after_year)]

    def retraction(self, item_id: str) -> RetractionInfo:
        self.calls.append(("retraction", item_id))
        return self.retractions.get(self._title(item_id), RetractionInfo(status="none"))

    def fulltext(self, item_id: str) -> bool:
        title = self._title(item_id)
        if latest_snapshot(self.lib.ps, item_id) is not None:
            return True
        pages = self.fulltexts.get(title)
        if pages is None:
            return False
        add_text_layer(self.lib.ps, item_id, pages)
        return True
