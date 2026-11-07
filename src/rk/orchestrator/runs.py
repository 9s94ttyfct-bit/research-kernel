"""Runs inside ``rk serve`` (spec §7.2): one daemon thread per project steps its run's ``Orchestrator``. A D2 project
runs from intake on; from protocol on (question passed) a run drives the stages of D1 and D2 projects alike (§8.1).

The thread waits while the run waits on a checkpoint (it polls every ``poll_s``, so your answer resumes the run) or on
an hourly call cap (``min(wait, max_rate_wait_s)``, the wait taken from the kernel's ``CallCaps`` for the capped
driver, at least ``poll_s``). It ends when the run is ``paused``, ``paused_budget``, done, failed or cancelled, and on
``close``.

While its thread lives, the thread is the only writer of the run's status: ``pause`` and ``cancel`` ask it and take
effect between steps (a role task in flight finishes first, also when ``close`` comes in the meantime); without a
thread they are written at once. A step that raises fails the run with the error as its reason. ``resume_all``
restarts the threads after ``rk serve`` restarts; the event-sourced stage state makes every step start from what
already passed.

One writer of a project's stages at a time: a guided (D1) stage call (``rk_stage_run``, ``rk_stage_advance``) runs in
the background through ``d1_start`` (stages take minutes, longer than a chat turn or a tool call may wait): refused
while a run of the project is active or another D1 call of it runs, and while it runs ``start`` and ``resume`` of the
project are refused. ``d1_status`` shows the call in flight and the outcome of the last one.

Lock order: the manager's lock (``RunManager._lock``) before a project's lock (``ProjectStore.lock``), never the other
way round. ``start`` and ``resume`` hold the manager's lock while ``start_run`` takes the project's; a write that must
not race a run or a guided stage call (the intake) goes through ``RunManager.idle``, which takes them in the same
order. Code that holds a project's lock never calls into the manager."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TypeVar

from rk.discovery.store import frozen_rq, intake_of
from rk.llm.drivers import DriverSpec
from rk.llm.vendors import vendor_of
from rk.orchestrator.graph import DISCOVERY, stages_of
from rk.orchestrator.state import (
    TERMINAL_RUN_STATES,
    active_run,
    current_stage,
    discovery_passed,
    last_d1,
    latest_run,
    orchestrator_drives,
    rate_capped_driver,
    record_d1_finished,
    record_run_session,
    record_run_status,
    run_row,
    stage_rows,
    start_run,
)
from rk.store.project import ProjectStore
from rk.tools.registry import ToolError
from rk.verify.judge import judge_vendor_error

log = logging.getLogger(__name__)

ACTOR = "orchestrator"
RUN_UNTIL = ("topics", "question")  # the discovery stages a run may end at; any runnable later stage too
GENERATOR_ROLES = ("scout", "gapminer", "ideator")  # the discovery roles whose output the judges check
STOPPED = frozenset({"paused", "paused_budget"}) | TERMINAL_RUN_STATES  # the thread ends on these
STEPPED = frozenset({"running", "waiting_human", "paused_waiting_human"})  # a thread steps these
WAITING = frozenset({"waiting_human", "paused_waiting_human"})  # the thread polls the checkpoints
MAX_RATE_WAIT_S = 300.0  # longest single wait on a call cap before the run steps again
UNKNOWN_RATE_WAIT_S = 60.0  # wait on a call cap whose driver is unknown
SOURCE_BACKOFF_S = 30.0  # first wait after a transient source error; doubles per retry, at most MAX_RATE_WAIT_S
MAX_SOURCE_RETRIES = 5  # transient source errors in a row before the run fails
REASONS = {"paused": "paused by you", "cancelled": "cancelled by you"}
T = TypeVar("T")


@dataclass
class _Runner:
    run_id: str
    wake: threading.Event = field(default_factory=threading.Event)
    request: tuple[str, str] | None = None  # ("paused" | "cancelled", actor), applied by the thread between steps
    thread: threading.Thread | None = None
    source_errors: int = 0  # transient source errors in a row (``waiting_source``)


@dataclass
class _D1Job:
    action: str  # "run" or "advance"
    stage: str | None
    started: float
    thread: threading.Thread | None = None


@contextmanager
def no_run_active(ps: ProjectStore, what: str) -> Iterator[None]:
    """Hold the project's lock while the block changes ``what`` (e.g. ``"the intake"``); refused (``run_active``)
    while a run of the project is active. Without a ``RunManager``; with one, use ``RunManager.idle``."""
    with ps.lock:
        run = active_run(ps)
        if run is not None:
            raise ToolError("run_active", f"run {run['id']} is {run['status']}; {what} cannot change while a run is "
                                          "active (cancel it first: rk run cancel)", {"run_id": run["id"]})
        yield


class RunManager:
    def __init__(self, kernel, registry, orchestrator, *, poll_s: float = 2.0,
                 max_rate_wait_s: float = MAX_RATE_WAIT_S, unknown_rate_wait_s: float = UNKNOWN_RATE_WAIT_S,
                 source_backoff_s: float = SOURCE_BACKOFF_S, max_source_retries: int = MAX_SOURCE_RETRIES,
                 join_timeout_s: float = 30.0):
        self.kernel, self.registry, self.orchestrator = kernel, registry, orchestrator
        self.poll_s, self.max_rate_wait_s, self.unknown_rate_wait_s = poll_s, max_rate_wait_s, unknown_rate_wait_s
        self.source_backoff_s, self.max_source_retries = source_backoff_s, max_source_retries
        self.join_timeout_s = join_timeout_s
        self._lock = threading.Lock()  # guards _runners, _d1, _closed and every status write outside a step; taken
        # before a project's ``ps.lock`` (the lock order, module docstring)
        self._runners: dict[str, _Runner] = {}
        self._d1: dict[str, _D1Job] = {}  # the guided (D1) stage call in flight, per project
        self._closed = False

    # ---- controls ----
    def start(self, project: str, until: str, *, actor: str) -> str:
        """Start a run of ``project`` up to the stage ``until`` and its thread: ``topics``, ``question`` or a later
        stage, every stage up to it with a worker. Refused while another run of the project is not terminal or a D1
        stage call runs, without an intake, in D1 before the orchestrator drives the project, and after question passed
        without a frozen RQ (``_check_startable``). The checks and the start are one locked step, so an intake recorded
        meanwhile (``idle``) cannot slip in between them."""
        ps = self._ps(project)
        with self._lock:
            self._check_startable(ps, project, until)
            return self._start_locked(ps, project, until, actor)

    def pause(self, project: str, *, actor: str = ACTOR) -> dict:
        """Pause the run between steps: a step in flight (a role task) finishes first, then the run is ``paused``."""
        return self._request(project, "paused", actor)

    def cancel(self, project: str, *, actor: str = ACTOR) -> dict:
        """Cancel the run between steps (a step in flight finishes first)."""
        return self._request(project, "cancelled", actor)

    def resume(self, project: str, *, actor: str = ACTOR) -> dict:
        """Resume a ``paused`` or ``paused_budget`` run in a new session (``run.session`` n + 1: the 12 h wall clock
        and the stage budgets restart). A run that a thread should step but none does (``rk serve`` restarted and
        did not resume it) gets its thread back in the same session; a pause not yet applied is withdrawn.

        A ``failed`` or ``cancelled`` run cannot be resumed: when the latest run ended so, a new run with the same
        ``until`` starts instead (it goes on from the stages that already passed), and the result says so
        (``message``, ``replaced``)."""
        ps = self._ps(project)
        with self._lock:
            self._check_open()
            self._check_no_d1(project)
            run = active_run(ps)
            last = latest_run(ps)
            if run is None and last is not None and last["status"] in ("failed", "cancelled") \
                    and project not in self._runners:
                self._check_startable(ps, project, last["until"])
                new = self._start_locked(ps, project, last["until"], actor)
                message = (f"run {last['id']} was {last['status']}, so it cannot be resumed; started a new run {new} "
                           f"(until {last['until']}): it goes on from the stages that already passed")
                replaced = last["id"]
            else:
                message = replaced = None
                run = self._active(ps, project)
                r = self._runners.get(project)
                if r is not None:
                    if r.request is None or r.request[0] != "paused":
                        state = "being cancelled" if r.request else run["status"]
                        raise ToolError("run_active", f"run {run['id']} is {state}", {"run_id": run["id"]})
                    r.request = None
                elif run["status"] in ("paused", "paused_budget"):
                    clock = self.orchestrator.clock
                    record_run_session(ps, run["id"], run["session"] + 1, actor=actor, clock=clock)
                    record_run_status(ps, run["id"], "running", actor=actor, clock=clock)
                    self._spawn(project, run["id"])
                else:
                    self._spawn(project, run["id"])
        out = self.status(project)
        if message:
            out.update(message=message, replaced=replaced)
        return out

    def status(self, project: str) -> dict:
        """The latest run (or ``None``), the current stage, one row per stage of the project, the stages that have a
        worker (``runnable``) and those a run could end at now (``until_options``)."""
        ps = self._ps(project)
        with self._lock:
            r = self._runners.get(project)
            stepping, requested = r is not None, (r.request[0] if r and r.request else None)
        return {"project": project, "run": latest_run(ps), "current": current_stage(ps), "stages": stage_rows(ps),
                "stepping": stepping, "requested": requested, "runnable": self.orchestrator.runnable(ps),
                "until_options": self.until_options(ps)}

    def until_options(self, ps: ProjectStore) -> list[str]:
        """The stages a run of the project could end at now (what ``start`` accepts, judges aside): none without an
        intake or in D1 before the orchestrator drives the project; none after question passed without a frozen RQ.
        A run that starts before question passed may end past it (it gets its RQ frozen on the way)."""
        intake = intake_of(ps)
        if intake is None or (intake.discovery_mode != "d2" and not orchestrator_drives(ps)):
            return []
        if discovery_passed(ps) and frozen_rq(ps) is None:
            return []
        return self._ends(ps)

    def d1_start(self, project: str, action: str, stage: str | None, call: Callable[[], dict], *,
                 check: Callable[[ProjectStore], None] | None = None) -> dict:
        """Start a guided (D1) stage call of ``project`` in the background and return at once (``status:
        running``); ``call`` returns the outcome shown by ``d1_status`` once it finished. Refused while a run of the
        project is active (or its thread is still stopping) or another D1 call of it runs (``stage_busy``), and when
        ``check(ps)`` raises: the caller's precondition (e.g. the project is in D1), checked again under the lock so
        an intake recorded since the caller checked it (``idle``) is seen."""
        ps = self._ps(project)
        with self._lock:
            self._check_open()
            if check is not None:
                check(ps)
            busy = self._d1.get(project)
            if busy is not None:
                raise ToolError("stage_busy", f"a guided stage call of {project} ({busy.action} "
                                              f"{busy.stage or ''}) is still running; follow it with rk_stage_status "
                                              "and do not start another until it finished",
                                {"action": busy.action, "stage": busy.stage})
            run = active_run(ps)
            r = self._runners.get(project)
            if run is not None or r is not None:
                run_id = run["id"] if run is not None else r.run_id
                state = run["status"] if run is not None else "still stopping"
                raise ToolError("run_active", f"run {run_id} is {state}; a D2 run drives the stages until it is "
                                              "done, failed or cancelled (rk run cancel)", {"run_id": run_id})
            job = _D1Job(action, stage, time.time())
            job.thread = threading.Thread(target=self._d1_run, args=(project, job, call), name=f"rk-d1-{project}",
                                          daemon=True)
            self._d1[project] = job
            job.thread.start()
        return {"status": "running", "action": action, "stage": stage}

    def d1_status(self, project: str) -> dict:
        """The guided stage call of ``project`` in flight (``running``: its action, stage and start time, else
        ``None``) and the last one that finished (``last``, kept in the event log so it outlives a restart: its
        ``outcome``, or its ``error``; ``None`` before any)."""
        ps = self._ps(project)
        with self._lock:
            job = self._d1.get(project)
            running = {"action": job.action, "stage": job.stage, "started": job.started} if job else None
            return {"running": running, "last": last_d1(ps)}

    @contextmanager
    def idle(self, project: str, what: str) -> Iterator[ProjectStore]:
        """Hold ``project`` still while the block changes ``what`` (e.g. ``"the intake"``): refused while a run of it
        is active (``run_active``) or a guided stage call of it runs (``stage_busy``). Until the block ends no run or
        guided stage call of the project can start, and the next one that does sees the block's write: ``start``,
        ``resume`` and ``d1_start`` check their preconditions and start under the same manager lock. (Writes that do
        not take the manager's lock are not held off.) Takes the manager's lock, then the project's: the lock order of
        ``start`` and ``resume``."""
        ps = self._ps(project)
        with self._lock:
            if project in self._d1:
                raise ToolError("stage_busy", f"a guided stage call of {project} is running; change {what} when it "
                                              "finished (rk_stage_status)")
            with no_run_active(ps, what):
                yield ps

    def resume_all(self) -> list[str]:
        """At ``rk serve`` start: a thread for every run that is ``running``, ``waiting_human`` or
        ``paused_waiting_human``. Returns their run ids."""
        resumed = []
        for p in self.kernel.list_projects():
            slug = p["slug"]
            try:
                run = active_run(self._ps(slug))
                with self._lock:
                    if run is None or run["status"] not in STEPPED or self._closed or slug in self._runners:
                        continue
                    self._spawn(slug, run["id"])
                resumed.append(run["id"])
            except Exception:  # one broken project does not keep the others from resuming
                log.exception("could not resume the run of %s", slug)
        return resumed

    def close(self, timeout: float | None = None) -> None:
        """Stop every thread between steps and join them all, the guided stage calls in flight included, within one
        deadline (``timeout``, default ``join_timeout_s``). A thread still in a step after that is left to the
        process exit: it still applies a pending pause or cancel, and its attempt reruns after the restart."""
        with self._lock:
            self._closed = True
            runners = list(self._runners.items())
            d1_jobs = list(self._d1.items())
        for _, r in runners:
            r.wake.set()
        deadline = time.monotonic() + (self.join_timeout_s if timeout is None else timeout)
        for project, r in runners:
            r.thread.join(max(0.0, deadline - time.monotonic()))
            if r.thread.is_alive():
                log.warning("run %s of %s is still in a step at shutdown; it resumes after the restart", r.run_id,
                            project)
        for project, job in d1_jobs:
            job.thread.join(max(0.0, deadline - time.monotonic()))
            if job.thread.is_alive():
                log.warning("the guided %s of %s is still running at shutdown; an interrupted attempt reruns",
                            job.action, project)

    def rate_wait_s(self, project: str, run_id: str) -> float:
        """Seconds until the driver of the run's latest rate-capped task may be called again (``CallCaps``), at
        least ``poll_s``; ``unknown_rate_wait_s`` when that driver is unknown."""
        driver = rate_capped_driver(self._ps(project), run_id)
        try:
            provider = DriverSpec.parse(driver).provider if driver else None
        except ValueError:
            provider = None
        if provider is None:
            return self.unknown_rate_wait_s
        return max(self.kernel.caps.wait_s(provider), self.poll_s)

    # ---- internals ----
    def _ps(self, project: str) -> ProjectStore:
        try:
            ps, _ = self.kernel.project(project)
        except LookupError as e:
            raise ToolError("not_found", str(e), status=404) from e
        return ps

    def _check_open(self) -> None:
        if self._closed:
            raise ToolError("unavailable", "rk serve is shutting down", status=503)

    def _check_no_d1(self, project: str) -> None:
        if project in self._d1:
            raise ToolError("run_active", f"a guided (D1) stage call of {project} is in progress; start or resume "
                                          "the run when it returns")

    def _ends(self, ps: ProjectStore) -> list[str]:
        """The stages a run can end at, rules of the mode and the RQ aside: from the current stage on while each has
        a worker; of the discovery stages only ``RUN_UNTIL``."""
        stages, runnable, current = stages_of(ps), set(self.orchestrator.runnable(ps)), current_stage(ps)
        if current is None:
            return []
        ends = []
        for s in stages[stages.index(current):]:
            if s not in runnable:
                break
            if s in RUN_UNTIL or s not in DISCOVERY:
                ends.append(s)
        return ends

    def _check_startable(self, ps: ProjectStore, project: str, until: str) -> None:
        """A run needs an intake; in D1 a run starts only once the orchestrator drives the project
        (``orchestrator_drives``: question passed, or production began; before, the Lead drives discovery). It ends at
        a stage of ``_ends``; one that starts after question passed and ends past it needs a frozen RQ (a run that
        starts before goes through question, whose gate freezes one). And two usable judges (``_judges_problem``)."""
        intake = intake_of(ps)
        if intake is None:
            raise ToolError("intake_missing", f"{project} has no intake yet; record it first (rk project intake)")
        if intake.discovery_mode != "d2" and not orchestrator_drives(ps):
            raise ToolError("not_d2", f"{project} uses guided discovery (d1): run its stages in the Lead chat until "
                                      "question passed (from protocol on, runs drive the stages), or record the "
                                      "intake again with discovery mode d2")
        if until not in self._ends(ps):
            raise ToolError("stage_unavailable", self._why_not_an_end(ps, until))
        stages = stages_of(ps)
        if discovery_passed(ps) and stages.index(until) > stages.index("question") and frozen_rq(ps) is None:
            raise ToolError("rq_not_frozen", f"a run past question needs a frozen research question; {project} has "
                                             "none (approve one at rq_approval)")
        problem = self._judges_problem()
        if problem:
            raise ToolError("judges_missing", problem)

    def _why_not_an_end(self, ps: ProjectStore, until: str) -> str:
        stages, current = stages_of(ps), current_stage(ps)
        if until not in stages:
            return f"{until!r} is not a stage of this project; its stages are {', '.join(stages)}"
        if until in DISCOVERY and until not in RUN_UNTIL:
            return f"a run ends at topics, question or a later stage, not {until!r}"
        if current is None or stages.index(until) < stages.index(current):
            return f"{until!r} already passed; the current stage is {current!r}"
        runnable = set(self.orchestrator.runnable(ps))
        missing = next(s for s in stages[stages.index(current):stages.index(until) + 1] if s not in runnable)
        return f"stage {missing!r} is not available: it has no worker yet, so a run cannot reach {until!r}"

    def _judges_problem(self) -> str:
        """Why the configured judges cannot give the two cross-vendor opinions V-NOVELTY, the already-done check and
        V-SUPPORT in gap mining need (each of a known, non-human vendor, different from each other and from the
        generating roles' vendors), else ``""``. Without them a run cannot pass topics or question."""
        settings = self.kernel.settings()
        generators = {vendor_of(s.provider, s.model) for r in GENERATOR_ROLES if r in settings.drivers
                      for s in [DriverSpec.parse(settings.drivers[r])]}
        seen: set[str] = set()
        unusable = []
        for role in ("judge", "judge_2"):
            if role not in settings.drivers:
                unusable.append(f"{role} is not configured")
                continue
            spec = DriverSpec.parse(settings.drivers[role])
            vendor = vendor_of(spec.provider, spec.model)
            if vendor is not None and vendor in generators:
                unusable.append(f"{role} ({spec}) has the vendor {vendor} of a generating role")
            elif judge_vendor_error(vendor, None, seen) is not None:
                unusable.append(f"{role} ({spec}) has an unknown, human or repeated vendor ({vendor or 'unknown'})")
            else:
                seen.add(vendor)
        if len(seen) >= 2:
            return ""
        return ("a run needs two judges of different known vendors, neither the vendor of the scout, gapminer or "
                f"ideator: {'; '.join(unusable)}. Set drivers.judge and drivers.judge_2 in config.toml (rk providers)")

    def _start_locked(self, ps: ProjectStore, project: str, until: str, actor: str) -> str:
        """Start the run and its thread (lock held)."""
        self._check_open()
        self._check_no_d1(project)
        old = self._runners.get(project)
        if old is not None:
            raise ToolError("run_active", f"run {old.run_id} of {project} is still stopping", {"run_id": old.run_id})
        run_id = start_run(ps, until=until, actor=actor, clock=self.orchestrator.clock)
        self._spawn(project, run_id)
        return run_id

    @staticmethod
    def _active(ps: ProjectStore, project: str) -> dict:
        run = active_run(ps)
        if run is None:
            raise ToolError("no_run", f"{project} has no run that is not done, failed or cancelled", status=404)
        return run

    def _request(self, project: str, status: str, actor: str) -> dict:
        ps = self._ps(project)
        with self._lock:
            run = self._active(ps, project)
            r = self._runners.get(project)
            if r is not None:
                r.request = (status, actor)
                r.wake.set()
            else:
                self._apply(ps, run["id"], status, actor)
        return self.status(project)

    def _apply(self, ps: ProjectStore, run_id: str, status: str, actor: str) -> None:
        """Write a pause or cancel (lock held) unless the run already ended (or, for a pause, is paused)."""
        current = run_row(ps, run_id)["status"]
        if current in TERMINAL_RUN_STATES or (status == "paused" and current in ("paused", "paused_budget")):
            return
        record_run_status(ps, run_id, status, reason=REASONS[status], actor=actor, clock=self.orchestrator.clock)

    def _spawn(self, project: str, run_id: str) -> None:
        r = _Runner(run_id)
        r.thread = threading.Thread(target=self._loop, args=(project, r), name=f"rk-run-{project}", daemon=True)
        self._runners[project] = r
        r.thread.start()

    def _drop(self, project: str, r: _Runner) -> None:
        if self._runners.get(project) is r:
            del self._runners[project]

    def _between_steps(self, ps: ProjectStore, project: str, r: _Runner, status: str | None) -> bool:
        """Apply a pending pause or cancel; ``True`` ends the thread (also once the run stopped or on close). The
        check and the thread's removal are one locked step, so no request is left to a thread that is gone. A request
        is applied on close too (``close`` joins the threads before the kernel closes), so ``resume_all`` does not
        restart a run you paused or cancelled."""
        with self._lock:
            if r.request is not None:
                self._apply(ps, r.run_id, *r.request)
            elif not self._closed and status not in STOPPED:
                return False
            self._drop(project, r)
            return True

    def _source_error(self, ps: ProjectStore, r: _Runner) -> str:
        """Count a transient source error of the run's step; past ``max_source_retries`` in a row the run fails."""
        r.source_errors += 1
        if r.source_errors <= self.max_source_retries:
            return "waiting_source"
        last = run_row(ps, r.run_id)["reason"] or "a source failed"
        reason = f"a source kept failing after {self.max_source_retries} retries: {last}"
        record_run_status(ps, r.run_id, "failed", reason=reason, actor=ACTOR, clock=self.orchestrator.clock)
        return "failed"

    def _loop(self, project: str, r: _Runner) -> None:
        try:
            ps = self._ps(project)
            while True:
                r.wake.clear()
                if self._between_steps(ps, project, r, None):
                    return
                status = self.orchestrator.step(project, r.run_id, actor=ACTOR)
                if status == "waiting_source":
                    status = self._source_error(ps, r)
                else:
                    r.source_errors = 0
                if self._between_steps(ps, project, r, status):
                    return
                if status == "waiting_rate":
                    r.wake.wait(min(self.rate_wait_s(project, r.run_id), self.max_rate_wait_s))
                elif status == "waiting_source":
                    r.wake.wait(min(self.source_backoff_s * 2 ** (r.source_errors - 1), self.max_rate_wait_s))
                elif status in WAITING:
                    r.wake.wait(self.poll_s)
        except Exception as e:  # noqa: BLE001 - the run fails with the reason instead of the thread dying silently
            self._crashed(project, r, e)

    def _crashed(self, project: str, r: _Runner, e: Exception) -> None:
        reason = f"{e.type}: {e.message}" if isinstance(e, ToolError) else f"{type(e).__name__}: {e}"
        with self._lock:
            self._drop(project, r)
            if self._closed:
                log.warning("run %s of %s stopped during shutdown: %s", r.run_id, project, reason)
                return
            log.error("run %s of %s failed: %s", r.run_id, project, reason, exc_info=e)
            try:
                ps = self._ps(project)
                if run_row(ps, r.run_id)["status"] not in TERMINAL_RUN_STATES:
                    record_run_status(ps, r.run_id, "failed", reason=reason, actor=ACTOR,
                                      clock=self.orchestrator.clock)
            except Exception:  # logged; the run stays as it was
                log.exception("could not record the failure of run %s of %s", r.run_id, project)

    def _d1_run(self, project: str, job: _D1Job, call: Callable[[], dict]) -> None:
        """The thread of a guided stage call: run it and record its outcome (or its error) as the project's last."""
        outcome, error = None, None
        try:
            outcome = call()
        except Exception as e:  # kept as the call's error for rk_stage_status, not lost with the thread
            error = f"{e.type}: {e.message}" if isinstance(e, ToolError) else f"{type(e).__name__}: {e}"
            log.warning("the guided %s of %s failed: %s", job.action, project, error, exc_info=e)
        with self._lock:
            try:
                record_d1_finished(self._ps(project), {"action": job.action, "stage": job.stage,
                                                       "started": job.started, "finished": time.time(),
                                                       "outcome": outcome, "error": error}, actor=ACTOR)
            except Exception:  # logged; the stage state itself is event-sourced and stays consistent
                log.exception("could not record the guided %s of %s", job.action, project)
            if self._d1.get(project) is job:
                del self._d1[project]
