"""Shared helpers for the discovery-stage tests (Plan 4)."""

from __future__ import annotations

import json

from pydantic import BaseModel

from rk.checkpoints import create_checkpoint
from rk.config import DEFAULT_DRIVERS, Settings, save_settings
from rk.discovery.models import HypothesisIn, Intake, RQBody, Topic
from rk.discovery.sources import COUNTED_SOURCES
from rk.discovery.store import add_rq, add_topic, freeze_rq, record_intake
from rk.fulltext import FulltextResult, _record_snapshot, latest_snapshot
from rk.ids import new_id
from rk.library.models import ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult, TaskSpec
from rk.orchestrator.engine import GateResult, StageOutcome
from rk.text.layer import Page, TextLayer
from rk.verify.judge import JudgeVerdict
from rk.verify.records import record_verification
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


PRODUCTION_FIRST = ("protocol", "search_screen", "read_extract")  # the production stages Plan 5a runs


def add_an_rq(ps) -> str:
    """Add a topic and a draft RQ with one hypothesis (attempt 1); returns the RQ's id."""
    tid = add_topic(ps, 1, Topic(title="Aspirin over 80", summary="S.", gap_ids=[], feasibility="F.",
                                 data_needed="D.", paper_type="critical_review", risks="R."), actor="tester")
    body = RQBody.model_validate({
        "three_step": {"x": "aspirin over 80", "y": "stroke", "z": "when to start"},
        "problem": {"condition": "Guidelines stop at 80.", "consequences": ["A.", "B."]},
        "finer": {"feasible": "F.", "interesting": "I.", "novel": "N.", "ethical": "E.", "relevant": "R."},
        "clinical": False})
    return add_rq(ps, 1, tid, body, [HypothesisIn(statement="H.", falsification="X.")], actor="tester")


def freeze_an_rq(ps) -> str:
    """Add a topic and an RQ with one hypothesis (attempt 1) and freeze the RQ as the human; returns its id."""
    rq = add_an_rq(ps)
    freeze_rq(ps, rq, adopted_by="human", actor="tester")
    return rq


def back_to_question_workers() -> dict[str, FakeWorker]:
    """Fake workers of every stage after intake up to read_extract: question passes its first attempt, protocol sends
    the project back to question (protocol->question), and question's second attempt waits on a checkpoint. A run
    over them pauses with question reopened after production began."""
    def wait(ctx):
        return StageOutcome("waiting_human", checkpoint_id=create_checkpoint(ctx.ps, "uncertain_support", "Wait.",
                                                                             actor="tester"))

    workers = {s: FakeWorker() for s in ("landscape", "gaps", "topics", *PRODUCTION_FIRST)}
    workers["question"] = FakeWorker(runs=[StageOutcome("done"), wait])
    workers["protocol"] = FakeWorker(runs=[StageOutcome("back_edge", to="question", reason="RQ too broad")])
    return workers


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
    ``year > after_year``, with ``date_from``/``date_to`` only those dated in that year range, cut to ``limit`` from
    ``offset``; its arguments are also in ``search_args``. Per-item presets are keyed
    by item title and need ``lib`` (the project library): ``statuses`` (``verify`` result, also written to the item's
    ``ref_status``; default ``default_status``), ``retractions`` (``retraction``; ``verify`` also records a preset one
    as V-RETRACT and writes it to the item, as ``KernelSources.verify`` does), ``citing`` (``cited_by``, filtered to
    ``year > after_year``) and ``fulltexts`` (pages recorded as the text layer by ``fulltext``). ``year_counts`` is keyed
    by query; ``record_counts`` (``count``, its arguments also in ``count_args``) by ``(query, source)``, default 0
    (``None``: the source gave no count), and ``count`` refuses a source outside ``COUNTED_SOURCES`` as
    ``KernelSources`` does. ``resolvable`` maps an identifier to the work ``add`` adds to the library (unknown:
    ``None``). Every call is recorded in ``calls``."""

    def __init__(self, items: dict[str, list[ItemIn]], *, lib=None, s2: bool = False,
                 statuses: dict[str, str] | None = None, default_status: str = "pass",
                 year_counts: dict[str, dict[int, int]] | None = None,
                 retractions: dict[str, RetractionInfo] | None = None, citing: dict[str, list[ItemIn]] | None = None,
                 fulltexts: dict[str, list[str]] | None = None, resolvable: dict[str, ItemIn] | None = None,
                 record_counts: dict[tuple[str, str], int | None] | None = None):
        self.items, self.lib, self.s2 = items, lib, s2
        self.statuses, self.default_status = statuses or {}, default_status
        self.counts, self.retractions = year_counts or {}, retractions or {}
        self.citing, self.fulltexts, self.resolvable = citing or {}, fulltexts or {}, resolvable or {}
        self.record_counts = record_counts or {}
        self.calls: list[tuple] = []
        self.search_args: list[dict] = []
        self.count_args: list[dict] = []

    def _title(self, item_id: str) -> str:
        if self.lib is None:
            raise AssertionError("FakeSources needs lib= for per-item calls")
        return self.lib.get(item_id).title

    def sources(self) -> list[str]:
        return ["openalex"] + (["s2"] if self.s2 else []) + ["europepmc", "pubmed", "arxiv"]

    def search(self, query: str, source: str, limit: int = 25, after_year: int | None = None, *, offset: int = 0,
               date_from: int | None = None, date_to: int | None = None) -> list[ItemIn]:
        self.calls.append(("search", query, source, limit, after_year))
        self.search_args.append({"query": query, "source": source, "limit": limit, "after_year": after_year,
                                 "offset": offset, "date_from": date_from, "date_to": date_to})
        out: list[ItemIn] = []
        for key, hits in self.items.items():
            if key.lower() in query.lower():
                out += [h for h in hits if h.title not in {o.title for o in out}]
        if after_year is not None:
            out = [h for h in out if h.year is not None and h.year > after_year]
        if date_from is not None or date_to is not None:
            out = [h for h in out if h.year is not None and (date_from is None or h.year >= date_from)
                   and (date_to is None or h.year <= date_to)]
        return out[offset:offset + limit]

    def year_counts(self, query: str) -> dict[int, int]:
        self.calls.append(("year_counts", query))
        return dict(self.counts.get(query, {}))

    def count(self, query: str, source: str, *, date_from: int | None = None, date_to: int | None = None) -> int | None:
        if source not in COUNTED_SOURCES:
            raise ValueError(f"no record count for source {source!r}")
        self.calls.append(("count", query, source))
        self.count_args.append({"query": query, "source": source, "date_from": date_from, "date_to": date_to})
        return self.record_counts.get((query, source), 0)

    def verify(self, item_id: str) -> str:
        self.calls.append(("verify", item_id))
        title = self._title(item_id)
        status = self.statuses.get(title, self.default_status)
        self.lib.update(item_id, ref_status=status, actor="fake-sources")
        ret = self.retractions.get(title)
        if ret is not None:
            self.lib.update(item_id, retraction=ret.status, actor="fake-sources")
        # as ``verify_item``: the item first, then the V-REF record (and V-RETRACT's, for a preset retraction)
        record_verification(self.lib.ps, "item", item_id, "V-REF", status, {}, actor="fake-sources")
        if ret is not None:
            record_verification(self.lib.ps, "item", item_id, "V-RETRACT", ret.status, ret.model_dump(),
                                actor="fake-sources")
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
