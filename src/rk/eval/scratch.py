"""Scratch kernels for evaluations: a throw-away RK_HOME-like directory with one project, so evaluation runs never
write to a project that ``rk serve`` owns. Agentic CLIs reach the Kernel through ``rk serve``, so evaluation tasks
run on chat-API drivers (in-process tool loop)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rk.llm.drivers import DriverSpec, build_provider


def scratch_kernel(slug: str, title: str, *, http=None):
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    k = Kernel(Path(tempfile.mkdtemp(prefix="rk-eval-")), http=http)
    reg = build_registry(k)
    k.create_project(slug, title, actor="eval")
    return k, reg


def eval_provider(spec: DriverSpec, settings):
    """A provider usable inside a scratch kernel. ``omp_gateway`` named explicitly on the command line is enabled
    for the run."""
    if spec.provider in ("claude_cli", "codex_cli"):
        raise ValueError(f"{spec.provider} reaches the Kernel through rk serve; evaluation runs need a chat-API "
                         "driver (omp_gateway:…, openai_api:…, anthropic_api:…, ollama:…)")
    if spec.provider == "omp_gateway":
        settings = settings.model_copy(update={"omp_gateway": True})
    return build_provider(spec, settings)
