"""What each driver can run (``rk models``, ``/ui/api/models``), and the driver rules every model choice keeps
(``validate_drivers``, ``driver_warnings``), shared by the Settings page, ``rk driver set`` and ``rk providers``.

The catalog lists the configured and default drivers, the subscription CLIs, ``ollama list``, and every chat model of
``omp models --json`` as an ``omp_gateway:`` driver; the same omp listing names the Claude Code, Codex CLI and Ollama
Cloud models. ``omp`` is optional: when it is missing, fails, is slow (``OMP_TIMEOUT_S``) or prints no usable listing,
one unavailable ``omp_gateway`` entry and the catalog's ``omp_status`` say why, and the other drivers are still listed;
malformed entries of a listing are left out and counted."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import NamedTuple

import httpx

from rk.config import DEFAULT_DRIVERS, Settings, api_key
from rk.llm.drivers import PROVIDERS, DriverSpec
from rk.llm.vendors import vendor_of
from rk.roles.policy import GENERATOR_ROLES, JUDGE_ROLES, ROLES

OMP_TIMEOUT_S = 10.0
OMP_CACHE_S = 600.0  # a good `omp models --json` listing is reused for 10 min
OMP_FAILURE_CACHE_S = 60.0  # a failed one for 1 min, so a slow omp does not delay every Settings page by the timeout
OLLAMA_TIMEOUT_S = 3.0
WRITING_ROLES = ("lead", "arguer", "writer")  # the roles whose claims V-SUPPORT's judges check
_CLI_BINARY = {"claude_cli": "claude", "codex_cli": "codex"}
_KEY_PROVIDER = {"anthropic_api": "anthropic", "openai_api": "openai"}

OmpRunner = Callable[[list[str], float], str]  # (argv, timeout_s) -> stdout
OllamaRunner = Callable[[Settings], list[str]]  # settings -> the model names `ollama list` shows


@dataclass(frozen=True)
class ModelChoice:
    driver: str
    label: str
    vendor: str | None
    available: bool
    reason: str | None

    @property
    def provider(self) -> str:
        return self.driver.partition(":")[0]

    def as_dict(self) -> dict:
        return {**asdict(self), "provider": self.provider}


def run_omp(argv: list[str], timeout: float) -> str:
    """``argv``'s stdout. Raises ``FileNotFoundError`` for a missing binary, ``subprocess.TimeoutExpired`` after
    ``timeout`` (its whole process group killed) and ``subprocess.CalledProcessError`` on a non-zero exit."""
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(argv[0])
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            raise
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, argv, out, err)
    return out


def ollama_tags(settings: Settings) -> list[str]:
    """The model names of ``ollama list``, from the configured Ollama server."""
    key = api_key(settings, "ollama")
    r = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=OLLAMA_TIMEOUT_S,
                  headers={"Authorization": f"Bearer {key}"} if key else {})
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str)]


# The runners ``list_models`` uses unless it is given others (tests replace them: no model CLI or server runs there).
default_omp_runner: OmpRunner = run_omp
default_ollama_runner: OllamaRunner = ollama_tags


class OmpListing(NamedTuple):
    models: list[dict]  # normalised chat entries: {"selector", "id", "provider" (str | None), "name"}
    error: str | None  # why `omp models --json` gave no listing
    skipped: int  # malformed chat entries left out


_omp_cache: dict[str, tuple[float, OmpListing]] = {}
_omp_lock = threading.Lock()  # guards the two dicts
_omp_loading: dict[str, threading.Lock] = {}  # one lock per omp binary: a cold catalog runs omp once (single flight)


def clear_cache() -> None:
    with _omp_lock:
        _omp_cache.clear()


def _normalise(raw: object) -> tuple[list[dict], int]:
    """The chat entries of an ``omp models --json`` document that make a valid ``omp_gateway:`` driver, normalised,
    and how many chat-looking entries were left out. Raises ``TypeError`` when there is no ``models`` list."""
    entries = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise TypeError("the output has no `models` list")
    models, skipped = [], 0
    for e in entries:
        if isinstance(e, dict) and e.get("kind") != "chat":
            continue  # another kind of model (embedding, …), not a chat model
        selector = e.get("selector") if isinstance(e, dict) else None
        if not isinstance(selector, str) or not selector.strip():
            skipped += 1
            continue
        try:
            DriverSpec.parse(f"omp_gateway:{selector}")
        except ValueError:
            skipped += 1
            continue
        model_id = e["id"] if isinstance(e.get("id"), str) and e["id"] else selector.rsplit("/", 1)[-1]
        name = e["name"] if isinstance(e.get("name"), str) and e["name"].strip() else model_id
        provider = e["provider"] if isinstance(e.get("provider"), str) else None
        models.append({"selector": selector, "id": model_id, "provider": provider, "name": name})
    return models, skipped


def _run_omp_listing(omp_bin: str, runner: OmpRunner) -> OmpListing:
    command = "`omp models --json`"
    try:
        models, skipped = _normalise(json.loads(runner([omp_bin, "models", "--json"], OMP_TIMEOUT_S)))
        return OmpListing(models, None, skipped)
    except FileNotFoundError:
        error = f"omp is not installed ({omp_bin} not found on PATH)"
    except subprocess.TimeoutExpired:
        error = f"{command} timed out after {OMP_TIMEOUT_S:g} s"
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or e.output or "").strip()[-300:]
        error = f"{command} failed (exit {e.returncode})" + (f": {tail}" if tail else "")
    except (OSError, ValueError, TypeError, RuntimeError) as e:  # invalid JSON, no `models` list, nesting too deep
        error = f"{command} failed: {type(e).__name__}: {e}"
    return OmpListing([], error, 0)


def _omp_models(settings: Settings, runner: OmpRunner | None) -> OmpListing:
    """The chat models of ``omp models --json``. Only the default runner's listing is cached: a good one for
    ``OMP_CACHE_S``, a failed one for ``OMP_FAILURE_CACHE_S``; concurrent cold loads wait for one omp run."""
    omp_bin = settings.cli_paths.get("omp", "omp")
    if runner is not None:
        return _run_omp_listing(omp_bin, runner)
    with _omp_lock:
        loading = _omp_loading.setdefault(omp_bin, threading.Lock())
    with loading:
        with _omp_lock:
            hit = _omp_cache.get(omp_bin)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        listing = _run_omp_listing(omp_bin, default_omp_runner)
        ttl = OMP_CACHE_S if listing.error is None else OMP_FAILURE_CACHE_S
        with _omp_lock:
            _omp_cache[omp_bin] = (time.monotonic() + ttl, listing)
    return listing


def ollama_cloud_name(model_id: str) -> str:
    """Ollama's name for an Ollama Cloud model: ``glm-5`` -> ``glm-5:cloud``, ``gpt-oss:120b`` -> ``gpt-oss:120b-cloud``."""
    return f"{model_id}-cloud" if ":" in model_id else f"{model_id}:cloud"


@dataclass(frozen=True)
class Catalog:
    models: list[ModelChoice]
    omp_status: str | None  # why omp_gateway models are missing (or some were left out), else None


def list_models(settings: Settings, *, omp_runner: OmpRunner | None = None,
                ollama_runner: OllamaRunner | None = None) -> list[ModelChoice]:
    """``model_catalog(...).models``."""
    return model_catalog(settings, omp_runner=omp_runner, ollama_runner=ollama_runner).models


def model_catalog(settings: Settings, *, omp_runner: OmpRunner | None = None,
                  ollama_runner: OllamaRunner | None = None) -> Catalog:
    """One ``ModelChoice`` per driver, grouped by provider (``PROVIDERS`` order), and the omp status. Never raises for
    a missing, failing, slow or malformed ``omp`` listing or Ollama: their entries carry the reason."""
    omp = _omp_models(settings, omp_runner)
    try:
        tags, ollama_error = set((ollama_runner or default_ollama_runner)(settings)), None
    except Exception as e:  # noqa: BLE001 - reported on the ollama entries, never raised
        tags, ollama_error = set(), f"ollama not reachable at {settings.ollama_url}: {type(e).__name__}: {e}"

    labels: dict[str, str] = {}

    def add(driver: str, label: str | None = None) -> None:
        if driver not in labels or (label and labels[driver] == driver):
            labels[driver] = label or driver

    for driver in {**DEFAULT_DRIVERS, **settings.drivers}.values():
        add(driver)
    add("claude_cli", "Claude Code (its default model)")
    add("codex_cli", "Codex CLI (its default model)")
    for m in omp.models:
        if m["provider"] == "anthropic":
            add(f"claude_cli:{m['id']}", f"{m['name']} (Claude Code)")
        elif m["provider"] == "openai-codex":
            add(f"codex_cli:{m['id']}", f"{m['name']} (Codex CLI)")
        elif m["provider"] == "ollama-cloud":
            add(f"ollama:{ollama_cloud_name(m['id'])}", f"{m['name']} (Ollama Cloud)")
        add(f"omp_gateway:{m['selector']}", m["name"])
    for tag in sorted(tags):
        add(f"ollama:{tag}", f"{tag} (Ollama)")
    if omp.error and not any(d.startswith("omp_gateway") for d in labels):
        add("omp_gateway", "omp gateway")

    selectors = {m["selector"] for m in omp.models}
    out: list[tuple[int, int, ModelChoice]] = []
    for i, (driver, label) in enumerate(labels.items()):
        try:
            spec = DriverSpec.parse(driver)
        except ValueError:
            continue  # an unparsable driver (configured, or derived from a bad id): the Settings page shows "Other…"
        reason = _unavailable(spec, settings, selectors, omp.error, tags, ollama_error)
        choice = ModelChoice(driver, label, vendor_of(spec.provider, spec.model), reason is None, reason)
        out.append((PROVIDERS.index(spec.provider), i, choice))
    if omp.error:
        status = f"omp gateway models are not listed: {omp.error}"
    elif omp.skipped:
        status = (f"{omp.skipped} malformed `omp models --json` entr{'y was' if omp.skipped == 1 else 'ies were'} "
                  "left out")
    else:
        status = None
    return Catalog([c for *_, c in sorted(out, key=lambda t: t[:2])], status)


def _unavailable(spec: DriverSpec, settings: Settings, selectors: set[str], omp_error: str | None, tags: set[str],
                 ollama_error: str | None) -> str | None:
    """Why ``spec`` cannot run now, else ``None``; a cheap check that never runs a model CLI."""
    p = spec.provider
    if p in _CLI_BINARY:
        exe = settings.cli_paths.get(_CLI_BINARY[p]) or _CLI_BINARY[p]
        return None if shutil.which(exe) else f"{exe} not found on PATH"
    if p == "ollama":
        name = spec.model or "deepseek-v4.1-flash:cloud"
        if name in tags:
            return None
        return ollama_error or f"not in `ollama list`; run `ollama pull {name}`"
    if p == "omp_gateway":
        if omp_error:
            return omp_error
        if spec.model is not None and spec.model not in selectors:
            return f"{spec.model} is not among the models `omp models` lists"
        return None
    if p in _KEY_PROVIDER:
        if not spec.model:
            return f"{p} needs an explicit model, e.g. '{p}:<model-id>'"
        return None if api_key(settings, _KEY_PROVIDER[p]) else f"no {_KEY_PROVIDER[p]} API key (Settings)"
    return None  # human


# ---- the driver rules ----
def _full(drivers: dict[str, str]) -> dict[str, str]:
    """``drivers`` with the roles it lacks set to their defaults (``Settings.drivers`` does the same on load)."""
    return {**DEFAULT_DRIVERS, **{r: d for r, d in drivers.items() if r in ROLES}}


def validate_drivers(drivers: dict[str, str]) -> list[str]:
    """Why ``drivers`` (role -> driver; missing roles take their defaults) cannot be saved, else ``[]``: an unknown
    role or unparsable driver; a judge of an unknown vendor (it could never judge); fewer than two judges of distinct
    known vendors (``judges_problem``); screeners that are not of two different known vendors
    (``screener_pair_problem``, the rule of ``screeners_problem``)."""
    from rk.production.screening import screener_pair_problem
    from rk.verify.judge import judges_problem

    problems = [f"unknown role {r!r}; the roles are {', '.join(ROLES)}" for r in drivers if r not in ROLES]
    full = _full(drivers)
    for role, text in full.items():
        try:
            DriverSpec.parse(text)
        except ValueError as e:
            problems.append(f"{role}: {e}")
    if problems:
        return problems
    for role in JUDGE_ROLES:
        spec = DriverSpec.parse(full[role])
        if vendor_of(spec.provider, spec.model) is None:
            problems.append(f"{role} ({spec}) has an unknown vendor, so it can never judge; choose a model whose "
                            "developer is known (rk models shows the vendor)")
    problem = judges_problem(full)
    if problem:
        problems.append(f"the judges need {problem}")
    problem = screener_pair_problem(full)
    if problem:
        problems.append(problem)
    return problems


def driver_warnings(drivers: dict[str, str], min_judges: int = 2) -> list[str]:
    """What still works badly with valid ``drivers``: writing roles (``WRITING_ROLES``, one line per shared driver)
    whose vendor leaves fewer than ``min_judges`` judges of other vendors to check their claims (V-SUPPORT then never
    passes them), and judges a run cannot use (``judges_problem`` against the ``GENERATOR_ROLES``)."""
    from rk.verify.judge import admit, judges_problem

    full = _full(drivers)
    judges = [(r, DriverSpec.parse(full[r])) for r in JUDGE_ROLES]
    short: dict[tuple[DriverSpec, str | None, int], list[str]] = {}  # (driver, vendor, judges left) -> roles
    for role in WRITING_ROLES:
        spec = DriverSpec.parse(full[role])
        vendor = vendor_of(spec.provider, spec.model)
        admitted, _ = admit(judges, {vendor})
        if len(admitted) < min_judges:
            short.setdefault((spec, vendor, len(admitted)), []).append(role)
    warnings = []
    for (spec, vendor, n), roles in short.items():
        who = roles[0] if len(roles) == 1 else f"{', '.join(roles[:-1])} and {roles[-1]}"
        warnings.append(f"{who} ({spec}) {'is' if len(roles) == 1 else 'are'} {vendor or 'of an unknown vendor'}: "
                        f"only {n} judge{'' if n == 1 else 's'} of another vendor can check "
                        f"{'its' if len(roles) == 1 else 'their'} claims, and V-SUPPORT needs {min_judges}, so none "
                        "of them can pass")
    problem = judges_problem(full, GENERATOR_ROLES)
    if problem:
        warnings.append(f"a run cannot pass topics or question: it needs {problem}")
    return warnings
