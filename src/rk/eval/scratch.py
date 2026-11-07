"""Scratch kernels for evaluations: a throw-away RK_HOME-like directory with one project, so evaluation runs never
write to a project that ``rk serve`` owns. The hourly call caps are the real ``$RK_HOME`` ones, so evaluation calls
count against the same subscription budget as ``rk serve``'s. Agentic CLIs reach the Kernel through ``rk serve``, so
evaluation tasks that use tools run on chat-API drivers (in-process tool loop)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rk.config import rk_home
from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.ratelimit import CallCaps


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
    """A provider usable inside a scratch kernel. ``omp_gateway`` named explicitly on the command line is enabled
    for the run. Agentic CLIs reach Kernel tools only through ``rk serve``, so they are refused for tasks that use tools
    (``tools=False``: no-tool tasks such as the judges', which a CLI runs without any Kernel connection)."""
    if tools and spec.provider in ("claude_cli", "codex_cli"):
        raise ValueError(f"{spec.provider} reaches the Kernel through rk serve; evaluation runs need a chat-API "
                         "driver (omp_gateway:…, openai_api:…, anthropic_api:…, ollama:…)")
    if spec.provider == "omp_gateway":
        settings = settings.model_copy(update={"omp_gateway": True})
    return build_provider(spec, settings)
