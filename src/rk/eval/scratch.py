"""Scratch kernels for evaluations: a throw-away RK_HOME-like directory with one project, so evaluation runs never
write to a project that ``rk serve`` owns. The hourly call caps are the real ``$RK_HOME`` ones, so evaluation calls
count against the same subscription budget as ``rk serve``'s. Agentic CLIs reach the Kernel through ``rk serve``, so
evaluation tasks that use tools run on chat-API drivers (in-process tool loop)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from rk.config import load_settings, rk_home
from rk.discovery.executor import RateCapped
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.ratelimit import CallCaps
from rk.llm.runner import run_task
from rk.llm.types import TaskResult


def scratch_kernel(slug: str, title: str, *, http=None):
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    k = Kernel(Path(tempfile.mkdtemp(prefix="rk-eval-")), http=http)
    k.caps.close()
    k.caps = CallCaps(rk_home() / "kernel.sqlite")  # the call log rk serve counts against
    reg = build_registry(k)
    k.create_project(slug, title, actor="eval")
    return k, reg


def eval_provider(spec: DriverSpec, settings, *, tools: bool = True):
    """A provider usable inside a scratch kernel. Agentic CLIs reach Kernel tools only through ``rk serve``, so they
    are refused for tasks that use tools (``tools=False``: no-tool tasks such as the judges', which a CLI runs without
    any Kernel connection)."""
    if tools and spec.provider in ("claude_cli", "codex_cli"):
        raise ValueError(f"{spec.provider} reaches the Kernel through rk serve; evaluation runs need a chat-API "
                         "driver (omp_gateway:…, openai_api:…, anthropic_api:…, ollama:…)")
    return build_provider(spec, settings)


def eval_task(k, reg, *, project: str, **task) -> TaskResult:
    """``run_task`` for evaluations. A task stopped by its driver's hourly call cap raises ``RateCapped``, so the
    evaluation stops instead of scoring the item as unanswered."""
    r = run_task(k, reg, project=project, **task)
    if r.error == "rate_capped":
        raise RateCapped(r.provider, k.caps.wait_s(r.provider))
    return r


def rate_capped_message(e: RateCapped) -> str:
    """What ``rk eval`` prints when an evaluation stopped at a driver's hourly call cap."""
    if load_settings().calls_per_hour.get(e.driver) == 0:
        return f"rate-capped: driver {e.driver!r} is disabled (calls_per_hour = 0)."
    return f"rate-capped: {e}; rerun after {datetime.now() + timedelta(seconds=e.wait_s):%H:%M}."
