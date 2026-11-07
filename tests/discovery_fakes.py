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
from rk.llm.types import Availability, TaskResult, TaskSpec
from rk.orchestrator.engine import GateResult, StageOutcome
from rk.text.layer import Page, TextLayer
from rk.verify.judge import JudgeVerdict
from rk.verify.minicheck import Layer1Score, Layer1Unavailable
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


class FakeWorker:
    """A scripted ``Worker``. Each queue item is a ``StageOutcome`` (or ``GateResult`` for ``gates``), an exception
    to raise, or a callable taking the context; an empty queue gives ``done`` (or a passing gate)."""

    def __init__(self, *, runs=(), answers=(), gates=(), degrades=()):
        self.runs, self.answers, self.gates, self.degrades = list(runs), list(answers), list(gates), list(degrades)
        self.calls: list[tuple] = []

    @staticmethod
    def _next(queue, arg, default):
        item = queue.pop(0) if queue else default
        if isinstance(item, BaseException):
            raise item
        return item(arg) if callable(item) else item

    def run(self, ctx):
        self.calls.append(("run", ctx.attempt, ctx.guidance))
        return self._next(self.runs, ctx, StageOutcome("done"))

    def on_answer(self, ctx, checkpoint):
        self.calls.append(("on_answer", ctx.attempt, checkpoint["id"], checkpoint["answer"]))
        return self._next(self.answers, ctx, StageOutcome("done"))

    def gate(self, ps, attempt):
        self.calls.append(("gate", attempt))
        return self._next(self.gates, ps, GateResult(True, []))

    def degrade(self, ctx, failures):
        self.calls.append(("degrade", ctx.attempt, list(failures)))
        return self._next(self.degrades, ctx, StageOutcome("failed", reason="nothing to degrade"))

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


def add_text_layer(ps, item_id: str, pages: list[str]) -> FulltextResult:
    """Record a text layer (one string per page) as the item's latest snapshot."""
    layer = TextLayer(source_kind="html", pages=[Page(n=i, text=t) for i, t in enumerate(pages, 1)], sha256="")
    return _record_snapshot(ps, item_id, "\f".join(pages).encode(), layer, "html", None, "tester")


class ScriptedExecutor:
    """A ``TaskExecutor`` that replays scripted outputs. ``script`` maps ``(role, schema name)`` to the outputs, popped
    in order; ``drivers`` overrides the default driver per role (provider/model of the result, so ``vendor_of`` gives
    the vendor), and a ``driver`` given to ``run`` overrides both. A call that is not scripted but brings its own
    ``provider`` (a V-SUPPORT judge) is run on that provider. Every call is recorded in ``calls``."""

    def __init__(self, script: dict[tuple[str, str], list[dict]], *, drivers: dict[str, str] | None = None):
        self.script = {k: list(v) for k, v in script.items()}
        self.drivers = {**DEFAULT_DRIVERS, **(drivers or {})}
        self.calls: list[dict] = []

    def run(self, role: str, instructions: str, inputs: dict, output_schema: type[BaseModel], *, stage: str,
            attempt: int, tools: list[str] | None = None, driver: DriverSpec | None = None, provider=None,
            max_turns: int | None = None, timeout_s: int | None = None) -> TaskResult:
        key = (role, output_schema.__name__)
        self.calls.append({"role": role, "schema": output_schema.__name__, "instructions": instructions,
                           "inputs": inputs, "stage": stage, "attempt": attempt, "tools": tools,
                           "driver": str(driver) if driver else None})
        queue = self.script.get(key)
        if not queue and provider is not None:
            spec = TaskSpec(task_id=new_id("tsk"), role=role, project=None, instructions=instructions, inputs=inputs,
                            output_schema=output_schema, tools=tools or [])
            return provider.run(spec, None)
        if not queue:
            raise AssertionError(f"no scripted output left for {key}")
        out = queue.pop(0)
        spec = driver or DriverSpec.parse(self.drivers[role])
        return TaskResult(new_id("tsk"), True, output_schema.model_validate(out), json.dumps(out), spec.provider,
                          spec.model, 0.0, None)


class FakeLayer1:
    """A V-SUPPORT layer 1 that returns ``p`` (or is unavailable with ``fail``); the scored inputs are in ``calls``."""

    def __init__(self, p=None, fail=False):
        self.p, self.fail = p, fail
        self.calls: list[tuple[str, str]] = []

    def score(self, doc, claim):
        self.calls.append((doc, claim))
        if self.fail:
            raise Layer1Unavailable("no torch")
        return Layer1Score(p=self.p, chunk_index=0, n_chunks=1, model="fake")


class FakeJudge:
    """A judge provider that always returns ``label`` with ``spans``; the task inputs it saw are in ``inputs``."""
    kind = "chat_api"
    model = "fake"
    vendor = "x"

    def __init__(self, label, spans):
        self.v = JudgeVerdict(label=label, quoted_spans=spans)
        self.inputs: list[dict] = []

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.inputs.append(spec.inputs)
        return TaskResult(spec.task_id, True, self.v, "", "fake", "fake", 0.1, None)


class FakeSources:
    """A deterministic ``DiscoverySources``. ``search`` returns, for every key of ``items`` that occurs in the query
    (case-insensitive), its items, in key order, de-duplicated by title, with ``after_year`` only those with
    ``year > after_year``, cut to ``limit``. Per-item presets are keyed
    by item title and need ``lib`` (the project library): ``statuses`` (``verify`` result, also written to the item's
    ``ref_status``; default ``default_status``), ``retractions``, ``citing`` (``cited_by``, filtered to
    ``year > after_year``) and ``fulltexts`` (pages recorded as the text layer by ``fulltext``). ``year_counts`` is keyed
    by query. ``resolvable`` maps an identifier to the work ``add`` adds to the library (unknown: ``None``). Every call
    is recorded in ``calls``."""

    def __init__(self, items: dict[str, list[ItemIn]], *, lib=None, s2: bool = False,
                 statuses: dict[str, str] | None = None, default_status: str = "pass",
                 year_counts: dict[str, dict[int, int]] | None = None,
                 retractions: dict[str, RetractionInfo] | None = None, citing: dict[str, list[ItemIn]] | None = None,
                 fulltexts: dict[str, list[str]] | None = None, resolvable: dict[str, ItemIn] | None = None):
        self.items, self.lib, self.s2 = items, lib, s2
        self.statuses, self.default_status = statuses or {}, default_status
        self.counts, self.retractions = year_counts or {}, retractions or {}
        self.citing, self.fulltexts, self.resolvable = citing or {}, fulltexts or {}, resolvable or {}
        self.calls: list[tuple] = []

    def _title(self, item_id: str) -> str:
        if self.lib is None:
            raise AssertionError("FakeSources needs lib= for per-item calls")
        return self.lib.get(item_id).title

    def sources(self) -> list[str]:
        return ["openalex"] + (["s2"] if self.s2 else []) + ["europepmc", "pubmed", "arxiv"]

    def search(self, query: str, source: str, limit: int = 25, after_year: int | None = None) -> list[ItemIn]:
        self.calls.append(("search", query, source, limit, after_year))
        out: list[ItemIn] = []
        for key, hits in self.items.items():
            if key.lower() in query.lower():
                out += [h for h in hits if h.title not in {o.title for o in out}]
        if after_year is not None:
            out = [h for h in out if h.year is not None and h.year > after_year]
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

    def add(self, identifier: str) -> str | None:
        self.calls.append(("add", identifier))
        item = self.resolvable.get(identifier)
        if item is None:
            return None
        return self.lib.add_item(item.model_copy(update={"discovered_via": "backward"}), added_by="agent",
                                 actor="fake-sources").item_id
