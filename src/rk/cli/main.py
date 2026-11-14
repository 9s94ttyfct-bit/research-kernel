"""``rk`` command-line interface (a thin client of ``rk serve``)."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import typer
from typer.core import TyperGroup

from rk.config import load_settings, rk_home, save_settings


class _RunGroup(TyperGroup):
    """``rk run <project> --until ...`` is ``rk run start <project> --until ...``; the other words are commands."""

    def parse_args(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = ["start", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(help="Research Kernel CLI", no_args_is_help=True, add_completion=False)
project_app = typer.Typer(help="Projects", no_args_is_help=True)
library_app = typer.Typer(help="Project library", no_args_is_help=True)
note_app = typer.Typer(help="Notes", no_args_is_help=True)
token_app = typer.Typer(help="Lead tokens for external MCP clients", no_args_is_help=True)
checkpoint_app = typer.Typer(help="Checkpoints waiting for you", no_args_is_help=True)
browser_app = typer.Typer(help="The built-in browser (runs inside rk serve)", no_args_is_help=True)
run_app = typer.Typer(cls=_RunGroup, no_args_is_help=True,
                      help="Runs in rk serve: rk run <project> --until <stage> runs the project's stages up to "
                           "<stage>: topics or question (D2 discovery), or a later stage with a worker once question "
                           "passed (D1 and D2; rk run status lists the stages with a worker and those a run can end "
                           "at now). status, pause, resume and cancel act on the project's run.")
stage_app = typer.Typer(help="The project's stages", no_args_is_help=True)
topic_app = typer.Typer(help="Candidate topics of the discovery stages", no_args_is_help=True)
protocol_app = typer.Typer(help="The review protocol of the protocol stage", no_args_is_help=True)
screening_app = typer.Typer(help="Screening decisions of the search_screen stage", no_args_is_help=True)
extraction_app = typer.Typer(help="Extracted values of the read_extract stage", no_args_is_help=True)
synthesis_app = typer.Typer(help="The synthesis of the synthesize stage", no_args_is_help=True)
argument_app = typer.Typer(help="The argument of the argue stage", no_args_is_help=True)
design_app = typer.Typer(help="The method design of the design stage (research proposals)", no_args_is_help=True)
draft_app = typer.Typer(help="The manuscript's sections, each sentence with its verdict", no_args_is_help=True)
revise_app = typer.Typer(help="The reader's questions of the revise stage and how each was handled",
                         no_args_is_help=True)
audit_app = typer.Typer(help="The audit of the manuscript: the hard gates and the final review", no_args_is_help=True)
limits_app = typer.Typer(help="Model drivers blocked by a usage limit (rk.llm.limits), and their hourly call caps",
                         invoke_without_command=True)
app.add_typer(project_app, name="project")
app.add_typer(library_app, name="library")
app.add_typer(note_app, name="note")
app.add_typer(token_app, name="token")
app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(browser_app, name="browser")
app.add_typer(run_app, name="run")
app.add_typer(stage_app, name="stage")
app.add_typer(topic_app, name="topic")
app.add_typer(protocol_app, name="protocol")
app.add_typer(screening_app, name="screening")
app.add_typer(extraction_app, name="extraction")
app.add_typer(synthesis_app, name="synthesis")
app.add_typer(argument_app, name="argument")
app.add_typer(design_app, name="design")
app.add_typer(draft_app, name="draft")
app.add_typer(revise_app, name="revise")
app.add_typer(audit_app, name="audit")
app.add_typer(limits_app, name="limits")


def _caps():
    from rk.llm.ratelimit import CallCaps

    return CallCaps(rk_home() / "kernel.sqlite")  # the call log rk serve counts against


@limits_app.callback()
def limits(ctx: typer.Context) -> None:
    """List every driver blocked by a usage limit (until when) and the hourly call caps with their use."""
    if ctx.invoked_subcommand is not None:
        return
    from datetime import datetime

    caps = _caps()
    try:
        blocks = caps.blocks()
        for driver, until in blocks.items():
            typer.echo(f"{driver}: blocked by a usage limit until {datetime.fromtimestamp(until):%Y-%m-%d %H:%M}")
        if not blocks:
            typer.echo("no driver is blocked by a usage limit")
        for driver, cap in sorted(load_settings().calls_per_hour.items()):
            typer.echo(f"{driver}: {caps.used(driver)} of {cap} calls in the last hour")
    finally:
        caps.close()


@limits_app.command("clear")
def limits_clear(driver: str = typer.Argument(..., help="The provider, e.g. claude_cli")) -> None:
    """Lift a driver's usage-limit block (e.g. after the limit was reset or more usage bought)."""
    caps = _caps()
    try:
        live = caps.clear(driver)
    finally:
        caps.close()
    typer.echo(f"{driver}: block lifted" if live else f"{driver} was not blocked")


JSON_OPT = typer.Option(False, "--json", help="Print raw JSON")
KEYWORD_OPT = typer.Option(None, "--keyword", help="Repeatable")
TAG_ADD_OPT = typer.Option(None, "--add", help="Repeatable")
TAG_REMOVE_OPT = typer.Option(None, "--remove", help="Repeatable")


def _client():
    from rk.cli.client import ensure_server

    return ensure_server(rk_home())


def _project(project: str | None) -> str:
    p = project or load_settings().current_project
    if not p:
        typer.secho("No project selected: pass --project or run `rk project use <slug>`.", fg="red", err=True)
        raise typer.Exit(2)
    return p


def _fail(e) -> None:
    typer.secho(f"error [{e.type}]: {e.message}", fg="red", err=True)
    if e.details:
        typer.echo(json.dumps(e.details, ensure_ascii=False, indent=2), err=True)
    raise typer.Exit(1) from e


def _call(name: str, args: dict, *, write: bool = False, timeout: float | None = None) -> dict:
    from rk.cli.client import RkError

    try:
        c = _client()
    except RkError as e:
        _fail(e)
    try:
        return c.call(name, args, write=write, timeout=timeout)
    except RkError as e:
        _fail(e)
    finally:
        c.close()


def _out(data, as_json: bool, render) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render(data)


def _fmt_item(it: dict) -> str:
    who = it["creators"][0]["family"] if it.get("creators") else "?"
    ids = it.get("ids", {})
    ident = ids.get("doi") or (f"arXiv:{ids['arxiv']}" if ids.get("arxiv") else None) or ids.get("pmid") or ""
    flags = []
    if it.get("retraction") not in (None, "none", "unknown"):
        flags.append(f"!{it['retraction'].upper()}")
    if it.get("ref_status") not in (None, "unverified"):
        flags.append(f"ref={it['ref_status']}")
    head = f"{it['id']}  " if it.get("id") else ""
    return f"{head}{who} {it.get('year') or 'n.d.'} - {it['title'][:90]}  {ident}  {' '.join(flags)}".rstrip()


# ---------------- MCP proxy ----------------
@app.command()
def mcp(project: str = typer.Option(..., "--project"),
        role: str | None = typer.Option(None, "--role", help="Label only; the token decides the role")) -> None:
    """MCP stdio server for agentic CLIs and external MCP clients (token in RK_TASK_TOKEN)."""
    from rk.mcp_proxy import run_proxy

    run_proxy(project, role)


# ---------------- daemon ----------------
def _interrupt_orphaned_tasks(kernel) -> None:
    """Spec §11.6, at ``rk serve`` start: the tasks a stopped serve left ``running`` become ``interrupted``. Only
    while the serve lock is held and before anything can start a task (uvicorn, ``resume_all``)."""
    from rk.llm.runner import interrupt_orphaned_tasks

    # walks the project dirs itself (not Kernel.list_projects) so one broken project is reported and skipped
    for d in sorted(kernel.projects_dir.iterdir()):
        if not (d / "rk.sqlite").exists():
            continue
        try:
            ids = interrupt_orphaned_tasks(kernel.project(d.name)[0])
        except Exception as e:  # noqa: BLE001 - one broken project does not keep serve from starting
            typer.secho(f"could not mark the interrupted tasks of {d.name} ({type(e).__name__}: {e})",
                        fg="yellow", err=True)
            continue
        if ids:
            typer.echo(f"{d.name}: marked {len(ids)} task(s) interrupted (rk serve stopped before they returned)",
                       err=True)


@app.command()
def serve(detach: bool = typer.Option(False, help="Start in the background and return"),
          no_browser: bool = typer.Option(False, "--no-browser", help="Do not start the built-in browser")) -> None:
    """Run the single-writer daemon with the Workbench and the built-in browser (normally auto-started)."""
    home = rk_home()
    if detach:
        c = _client()
        typer.echo(f"rk serve running at {c.base_url}")
        return
    import threading

    import uvicorn

    from rk.browser.pacing import Pacer
    from rk.browser.service import BrowserService
    from rk.browser.session import BrowserSession
    from rk.discovery.sources import KernelSources
    from rk.orchestrator.engine import Orchestrator
    from rk.orchestrator.runs import RunManager
    from rk.server.app import create_app
    from rk.server.chat import ChatManager
    from rk.server.runtime import AlreadyRunning, acquire_serve_lock, release
    from rk.tools.builtin import build_registry
    from rk.ui.auth import UiAuth
    from rk.workspace import Kernel

    try:
        rt = acquire_serve_lock(home)
    except AlreadyRunning as e:
        typer.secho(str(e), fg="yellow", err=True)
        raise typer.Exit(0) from e
    kernel = Kernel(home)
    _interrupt_orphaned_tasks(kernel)  # before uvicorn and resume_all: nothing can have started a task yet
    settings = kernel.settings()
    origin = f"http://127.0.0.1:{rt.port}"
    ui = UiAuth(origin)
    service: BrowserService | None = None
    if settings.browser_enabled and not no_browser:
        session = BrowserSession(home / "browser-profile", headless=settings.headless(), workbench_origin=origin,
                                 allow_hosts=settings.browser_allow_hosts)
        try:
            session.start()
            service = BrowserService(kernel, session, Pacer(home / "kernel.sqlite", settings.browser_limits,
                                                            max_wait_s=settings.browser_max_wait_s))
        except Exception as e:  # noqa: BLE001 - reported; the Kernel runs without the browser
            typer.secho(f"built-in browser not started ({type(e).__name__}: {e}); run `uv run playwright install "
                        "chromium` or start with --no-browser", fg="yellow", err=True)
    runs: RunManager | None = None
    registry = build_registry(kernel, browser=lambda: service, runs=lambda: runs)
    # the runs' sources get the built-in browser too, the last full-text channel (srrepro10/11: every run's full-text
    # retrieval said "built-in browser: not running" while rk serve ran it, 173 records in tfa alone)
    orchestrator = Orchestrator(kernel, registry,
                                sources_factory=lambda project: KernelSources(kernel, project, lambda: service))
    runs = RunManager(kernel, registry, orchestrator)
    chats = ChatManager(kernel, registry)
    server = uvicorn.Server(uvicorn.Config(create_app(registry, rt.token, tokens=kernel.tokens, chats=chats, ui=ui,
                                                      kernel=kernel, browser=lambda: service, runs=runs),
                                           log_level="warning"))
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    stopped = threading.Event()

    def resume_runs() -> None:  # once uvicorn serves, the runs left running, waiting or polling get their threads
        while not server.started:
            if stopped.wait(0.05):
                return
        resumed = runs.resume_all()
        if resumed:
            typer.echo(f"resumed {len(resumed)} run(s): {', '.join(resumed)}", err=True)

    threading.Thread(target=resume_runs, name="rk-resume-runs", daemon=True).start()
    if service is not None:  # the Workbench is the window's first tab (its request waits for uvicorn to start)
        def show_workbench() -> None:
            try:
                url, title = service.session.open_workbench(ui.login_url())
                typer.echo(f"Workbench open in the built-in browser: {title} ({url})", err=True)
            except Exception as e:  # noqa: BLE001 - reported; `rk open` still gives a link
                typer.secho(f"could not open the Workbench tab ({type(e).__name__}: {e}); run `rk open`",
                            fg="yellow", err=True)

        threading.Thread(target=show_workbench, daemon=True).start()
    else:
        typer.echo(f"Workbench: {ui.login_url()}")
    try:
        server.run(sockets=[rt.sock])
    finally:
        stopped.set()
        runs.close()
        chats.close_all()
        if service is not None:
            service.session.close()
        kernel.close()
        release(rt)


@app.command("open")
def open_workbench() -> None:
    """Print a one-time link that opens the Workbench (for a browser other than the built-in window)."""
    typer.echo(_client().ui_link())


@app.command()
def chat(driver: str = typer.Option(..., "--driver", help="e.g. claude_cli, codex_cli, "
                                                          "omp_gateway:openai-codex/gpt-6-luna"),
         role: str = typer.Option("lead", "--role"), project: str | None = typer.Option(None, "--project")) -> None:
    """Multi-turn chat with a driver, using the role's tools on one project (Ctrl-D to quit)."""
    from rk.cli.client import RkError

    c = _client()
    try:
        chat_id = c.chat_open(driver, role, _project(project))
    except RkError as e:
        _fail(e)
    typer.echo(f"chat {chat_id} ({driver}, role {role}). Ctrl-D to quit.")
    try:
        while True:
            try:
                text = input("you> ")
            except EOFError:
                break
            if not text.strip():
                continue
            for ev in c.chat_send(chat_id, text):
                kind, data = ev["kind"], ev["data"]
                if kind == "text":
                    typer.echo(data["text"])
                elif kind == "tool_call":
                    typer.secho(f"  [tool] {data.get('name')}({json.dumps(data.get('arguments'), ensure_ascii=False)})",
                                fg="cyan")
                elif kind == "error":
                    typer.secho(f"  [error] {data.get('message')}", fg="red")
    finally:
        c.chat_close(chat_id)
        c.close()


# ---------------- projects ----------------
@project_app.command("create")
def project_create(slug: str, title: str = typer.Option(..., "--title"), field: str | None = None,
                   paper_type: str | None = None, use: bool = typer.Option(True, help="Make it the current project"),
                   as_json: bool = JSON_OPT) -> None:
    r = _call("rk_project_create", {"slug": slug, "title": title, "field": field, "paper_type": paper_type}, write=True)
    if use:
        s = load_settings()
        s.current_project = slug
        save_settings(s)
    _out(r, as_json, lambda d: typer.echo(f"created {d['slug']} ({d['project_id']})"))


@project_app.command("list")
def project_list(as_json: bool = JSON_OPT) -> None:
    r = _call("rk_project_list", {})
    cur = load_settings().current_project
    _out(r, as_json, lambda d: [typer.echo(f"{'*' if p['slug'] == cur else ' '} {p['slug']}  {p['title']}")
                                for p in d["projects"]])


@project_app.command("use")
def project_use(slug: str) -> None:
    _call("rk_project_status", {"project": slug})
    s = load_settings()
    s.current_project = slug
    save_settings(s)
    typer.echo(f"current project: {slug}")


@project_app.command("status")
def project_status(slug: str | None = typer.Argument(None), as_json: bool = JSON_OPT) -> None:
    r = _call("rk_project_status", {"project": _project(slug)})

    def render(d):
        chain = "ok" if d["chain"]["ok"] else f"BROKEN at seq {d['chain']['first_bad_seq']}"
        typer.echo(f"{d['project']}: {d['title']}\n  items={d['items']} snapshots={d['snapshots']} notes={d['notes']} "
                   f"verifications={d['verifications']} events={d['events']}\n  event chain: {chain}")
        if d["flagged_items"]:
            typer.echo(f"  flagged (retracted/withdrawn/partial/EoC): {', '.join(d['flagged_items'])}")

    _out(r, as_json, render)


@project_app.command("intake")
def project_intake(audience: str = typer.Option(..., "--audience", help="Who the paper is for"),
                   paper_type: str = typer.Option(..., "--paper-type",
                                                  help="systematic_review, scoping_review, critical_review or "
                                                       "research_proposal"),
                   mode: str = typer.Option(..., "--mode", help="d1 (guided) or d2 (autonomous discovery)"),
                   production: str = typer.Option(..., "--production", help="p1 (auto) or p2 (collab)"),
                   language: str = typer.Option("en", "--language"), topics_n: int = typer.Option(6, "--topics-n"),
                   project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Record the project's intake: audience, paper type, discovery and production modes."""
    modes = {"p1": "auto", "p2": "collab"}
    if production not in modes:
        typer.secho("--production must be p1 or p2", fg="red", err=True)
        raise typer.Exit(2)
    r = _call("rk_project_intake", {"project": _project(project), "audience": audience, "paper_type": paper_type,
                                    "discovery_mode": mode, "production_mode": modes[production],
                                    "language": language, "topics_n": topics_n}, write=True)
    _out(r, as_json, lambda d: typer.echo(
        f"{d['project']}: {d['paper_type']} for {d['audience']}; discovery {d['discovery_mode']}, "
        f"production {'p1' if d['production_mode'] == 'auto' else 'p2'}, language {d['language']}, "
        f"{d['topics_n']} topics"))


# ---------------- search / add / fetch ----------------
@app.command()
def search(query: str, source: str = typer.Option("openalex", "--source"), limit: int = typer.Option(10, "--limit"),
           add: bool = typer.Option(False, "--add", help="Add hits to the current project's library"),
           project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Search OpenAlex, Europe PMC (both in titles and abstracts), PubMed, arXiv, Semantic Scholar or ClinicalTrials.gov
    (source clinicaltrials: registry records)."""
    args = {"query": query, "source": source, "limit": limit, "add": add, "project": _project(project) if add else None}
    r = _call("rk_search_works", args, write=True)

    def render(d):
        for i, it in enumerate(d["results"], 1):
            typer.echo(f"{i:>2}. {_fmt_item(it)}")
        if d["added"]:
            typer.echo(f"added {len(d['added'])} items to 'Search hits'")

    _out(r, as_json, render)


@app.command()
def add(identifiers: list[str], project: str | None = typer.Option(None, "--project"),
        stance: str | None = None, no_verify: bool = False, as_json: bool = JSON_OPT) -> None:
    """Add works by DOI / PMID / PMCID / arXiv / OpenAlex id, with reference and retraction checks."""
    p = _project(project)
    results = []
    for ident in identifiers:
        r = _call("rk_source_add", {"project": p, "identifier": ident, "stance": stance, "verify": not no_verify},
                  write=True)
        results.append({"identifier": ident, **r})
    _out(results, as_json, lambda rs: [typer.echo(
        f"{x['item_id']}  {x['identifier']}  {'new' if x['created'] else 'merged'}  ref={x.get('ref_status', '-')}  "
        f"retraction={x.get('retraction', '-')}") for x in rs])


@app.command()
def fetch(item_id: str, project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Fetch open-access full text for an item."""
    r = _call("rk_source_fetch_fulltext", {"project": _project(project), "source_id": item_id}, write=True)
    _out(r, as_json, lambda d: typer.echo(f"{d['kind']} snapshot {d['snapshot_sha'][:12]}  pages={d['page_count']}  "
                                          f"{d.get('url') or ''}"))


@app.command()
def read(item_id: str, page: int = typer.Option(1, "--page"), start: int | None = None, end: int | None = None,
         project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Print a page (or character range) of an item's text layer."""
    r = _call("rk_text_read", {"project": _project(project), "source_id": item_id, "page": page, "start": start,
                               "end": end})
    _out(r, as_json, lambda d: typer.echo(d["text"]))


@app.command()
def find(item_id: str, query: str, project: str | None = typer.Option(None, "--project"),
         as_json: bool = JSON_OPT) -> None:
    """Find a phrase in an item's text layer (page and character locators)."""
    r = _call("rk_text_find", {"project": _project(project), "source_id": item_id, "query": query})
    _out(r, as_json, lambda d: [typer.echo(f"p{m['page']} [{m['start']}:{m['end']}]  …{m['snippet']}…")
                                for m in d["matches"]] or typer.echo("no match"))


@app.command()
def verify(item_id: str, project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Re-run reference (V-REF) and retraction (V-RETRACT) checks on an item."""
    r = _call("rk_verify", {"project": _project(project), "target_id": item_id}, write=True)
    _out(r, as_json, lambda d: typer.echo(f"ref={d['ref']['verdict']}  retraction={d['retraction']['status']}  "
                                          f"sources={','.join(d['retraction']['sources'])}"))


# ---------------- notes ----------------
@note_app.command("add")
def note_add(item_id: str, text: str = typer.Option(..., "--text"), kind: str = typer.Option("quote", "--kind"),
             role: str | None = typer.Option(None, "--role"), span: str | None = typer.Option(None, "--span"),
             keyword: list[str] | None = KEYWORD_OPT,
             project: str | None = typer.Option(None, "--project"),
             as_json: bool = JSON_OPT) -> None:
    """Add a note; quotes must appear verbatim in the source, paraphrases must not copy it."""
    note = {"source_id": item_id, "kind": kind, "text": text, "role_in_source": role, "source_span": span,
            "keywords": keyword or []}
    r = _call("rk_note_add", {"project": _project(project), "note": note}, write=True)
    _out(r, as_json, lambda d: typer.echo(f"{d['note_id']}  verified"
                                          + (f"  page {d['locator']['page']} [{d['locator']['start']}:{d['locator']['end']}]"
                                             if d["locator"] else "")))


@note_app.command("list")
def note_list(item_id: str | None = typer.Argument(None), project: str | None = typer.Option(None, "--project"),
              as_json: bool = JSON_OPT) -> None:
    r = _call("rk_note_list", {"project": _project(project), "source_id": item_id})
    _out(r, as_json, lambda d: [typer.echo(f"{n['id']}  {n['item_id']}  {n['kind']}: {n['text'][:100]}") for n in d["notes"]])


# ---------------- library ----------------
@library_app.command("list")
def library_list(collection: str | None = typer.Option(None, "--collection"),
                 project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    r = _call("rk_library_list", {"project": _project(project), "collection": collection})

    def render(d):
        for it in d["items"]:
            typer.echo(_fmt_item(it))
        typer.echo(f"({len(d['items'])} items)")

    _out(r, as_json, render)


@library_app.command("search")
def library_search(query: str, project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    r = _call("rk_library_search", {"project": _project(project), "query": query})
    _out(r, as_json, lambda d: [typer.echo(f"{h['item_id']}  {h['title'][:80]}  …{h['snippet']}…") for h in d["hits"]])


@library_app.command("tag")
def library_tag(item_id: str, add: list[str] | None = TAG_ADD_OPT, remove: list[str] | None = TAG_REMOVE_OPT,
                project: str | None = typer.Option(None, "--project")) -> None:
    r = _call("rk_library_tag", {"project": _project(project), "item_id": item_id, "add": add or [],
                                 "remove": remove or []},
              write=True)
    typer.echo(f"tags: {', '.join(r['tags']) or '-'}")


@library_app.command("collect")
def library_collect(item_id: str, collection: str, project: str | None = typer.Option(None, "--project")) -> None:
    r = _call("rk_library_collect", {"project": _project(project), "item_id": item_id, "collection": collection},
              write=True)
    typer.echo(f"added to {collection} ({r['collection_id']})")


@library_app.command("import")
def library_import(path: Path, fmt: str = typer.Option("bibtex", "--format"),
                   project: str | None = typer.Option(None, "--project")) -> None:
    r = _call("rk_library_import", {"project": _project(project), "format": fmt, "text": path.read_text()}, write=True)
    typer.echo(f"imported {len(r['item_ids'])} entries ({r['created']} new)")


@library_app.command("export")
def library_export(fmt: str = typer.Option("bibtex", "--format"), collection: str | None = typer.Option(None, "--collection"),
                   project: str | None = typer.Option(None, "--project")) -> None:
    r = _call("rk_library_export", {"project": _project(project), "format": fmt, "collection": collection})
    sys.stdout.write(r["text"])


@library_app.command("attach")
def library_attach(item_id: str, pdf: Path, project: str | None = typer.Option(None, "--project")) -> None:
    r = _call("rk_library_attach", {"project": _project(project), "source_id": item_id, "path": str(pdf.resolve())},
              write=True)
    typer.echo(f"attached: {r['kind']} snapshot {r['snapshot_sha'][:12]}  pages={r['page_count']}")


# ---------------- setup ----------------
@app.command()
def setup() -> None:
    """Guided setup: email, OpenAlex and Semantic Scholar keys, model providers, library sign-in in the built-in
    browser, and the list of what works."""
    from rk import __version__
    from rk.cli.setup import run_setup
    from rk.net.http import HttpClient

    try:
        client = _client()  # the library step uses the built-in browser of rk serve
    except Exception as e:  # noqa: BLE001 - the other steps work without the server
        typer.secho(f"rk serve is not reachable ({e}); the library step will be skipped", fg="yellow", err=True)
        client = None
    http = HttpClient(rk_home() / "cache" / "http.sqlite", user_agent=f"rk/{__version__} setup")
    try:
        _, caps = run_setup(http, lambda q, d: typer.prompt(q, default=d, show_default=False),
                            lambda m: typer.echo("\n" + m), client=client)
    finally:
        http.close()
    typer.echo("\nStep 6/6 - what works now:")
    for c in caps:
        typer.echo(f"  {c['area']:<15} {c['name']:<40} {c['status']}")
    typer.echo(f"\nSaved to {rk_home() / 'config.toml'} (mode 600).")


# ---------------- built-in browser ----------------
@browser_app.command("search")
def browser_search(site: str = typer.Argument(..., help="scholar, pubmed, europepmc, primo or summon"),
                   query: str = typer.Argument(...), max_results: int = typer.Option(10, "--max"),
                   project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Search a site in the built-in browser at human pace (waits for the site's quota; may ask you to step in)."""
    r = _call("rk_browser_search", {"project": _project(project), "site": site, "query": query,
                                    "max_results": max_results}, write=True, timeout=600)

    def render(d):
        if d.get("fallback"):
            typer.echo(f"the {site} page did not match its adapter; tab {d['tab']} is open for generic navigation")
        for h in d["hits"]:
            ids = "  ".join(f"{k}:{h[k]}" for k in ("doi", "pmid") if h.get(k))
            typer.echo(f"- {h['title'][:100]}\n  {h['url']}" + (f"\n  {ids}" if ids else ""))

    _out(r, as_json, render)


@browser_app.command("open")
def browser_open(url: str, project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Open a page in the built-in browser and show the metadata found on it."""
    r = _call("rk_browser_open", {"project": _project(project), "url": url}, timeout=180)
    _out(r, as_json, lambda d: typer.echo(f"{d['tab']}  {d['title']}\n{json.dumps(d.get('metadata'), indent=2)}"))


@browser_app.command("fetch")
def browser_fetch(source_id: str, project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Get an item's PDF through your library (or the publisher) in the built-in browser."""
    r = _call("rk_browser_fetch_fulltext", {"project": _project(project), "source_id": source_id}, write=True,
              timeout=600)
    _out(r, as_json, lambda d: typer.echo(f"{d['kind']} attached ({d['origin']}, {d['page_count']} pages) from {d['url']}"))


@browser_app.command("save")
def browser_save(tab: str | None = typer.Option(None, "--tab"), project: str | None = typer.Option(None, "--project"),
                 collection: str | None = typer.Option(None, "--collection", help="collection id (default: Search hits)"),
                 as_json: bool = JSON_OPT) -> None:
    """Save the page you are looking at in the built-in browser to the library."""
    r = _call("rk_browser_save_page", {"project": _project(project), "tab": tab, "collection": collection},
              write=True, timeout=300)
    _out(r, as_json, lambda d: typer.echo(f"{d['item_id']} ({'new' if d['created'] else 'already in the library'}), "
                                          f"reference: {d['ref_status']}, file: {(d['attachment'] or {}).get('kind')}"))


@browser_app.command("status")
def browser_status(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Whether the browser runs, which sites wait for you, today's quotas and the open tabs."""
    r = _call("rk_browser_status", {"project": _project(project)})

    def render(d):
        typer.echo("running" if d["running"] else "not running")
        for p in d.get("paused_sites", []):
            typer.echo(f"paused: {p['site']} ({p['reason']}) - answer checkpoint {p['checkpoint_id']}")
        for scope, q in sorted(d.get("quotas", {}).items()):
            typer.echo(f"today {scope}: {q['count']}" + (f"/{q['per_day']}" if q.get("per_day") else ""))
        for t in d.get("tabs", []):
            typer.echo(f"{t['tab']}  {t['owner']}  {t['url']}")

    _out(r, as_json, render)


# ---------------- drivers and role tasks ----------------
@app.command()
def providers(as_json: bool = JSON_OPT) -> None:
    """Show the driver configured for each role, its vendor, and whether it is usable now; then what the drivers break
    (problems) or weaken (warnings)."""
    r = _call("rk_providers", {})

    def render(d):
        for p in d["providers"]:
            state = "ok" if p["ok"] else "UNAVAILABLE"
            note = f"  ({p['reason']})" if p.get("reason") else ""
            typer.echo(f"{p['role']:<11} {p['driver']:<38} vendor={p['vendor'] or '?':<9} {state}"
                       f"{' ' + p['version'] if p.get('version') else ''}{note}")
        _driver_notes(d["problems"], d["warnings"])

    _out(r, as_json, render)


def _driver_notes(problems: list[str], warnings: list[str]) -> None:
    for p in problems:
        typer.secho(f"problem: {p}", fg="red")
    for w in warnings:
        typer.secho(f"warning: {w}", fg="yellow")


@app.command()
def models(as_json: bool = JSON_OPT) -> None:
    """List the models each driver can run (the subscription CLIs, Ollama, and every chat model `omp models` lists as
    omp_gateway:<provider>/<model>), with the vendor and whether each is usable now. Set one with `rk driver set`."""
    from rk.llm.catalog import model_catalog

    cat = model_catalog(load_settings())

    def render(data):
        for m in data["models"]:
            state = "ok" if m["available"] else f"UNAVAILABLE ({m['reason']})"
            judge = "" if m["vendor"] else "  (unknown vendor: cannot judge)"
            local = "  (local model)" if m["local"] else ""
            typer.echo(f"{m['driver']:<52} vendor={m['vendor'] or '?':<9} {state}{judge}{local}")
        if data["omp_status"]:
            typer.secho(f"omp: {data['omp_status']}", fg="yellow")

    _out({"models": [m.as_dict() for m in cat.models], "omp_status": cat.omp_status}, as_json, render)


driver_app = typer.Typer(help="Per-role drivers (the model each role runs on)", no_args_is_help=True)
app.add_typer(driver_app, name="driver")


@driver_app.command("set")
def driver_set(role: str = typer.Argument(..., help="A role, e.g. judge or writer"),
               driver: str = typer.Argument(..., help="provider[:model], e.g. omp_gateway:openai-codex/gpt-6-luna")
               ) -> None:
    """Set the driver of one role in config.toml, refusing a choice that breaks the vendor rules (the judges' and the
    screeners'). Warnings (a writer whose vendor leaves too few judges) are printed, and the choice is saved."""
    from rk.llm.catalog import driver_warnings, validate_drivers

    s = load_settings()
    drivers = {**s.drivers, role: driver.strip()}
    problems = validate_drivers(drivers)
    if problems:
        _driver_notes(problems, [])
        typer.secho("nothing saved", fg="red")
        raise typer.Exit(1)
    save_settings(s.model_copy(update={"drivers": drivers}))
    typer.echo(f"{role} = {driver.strip()}")
    _driver_notes([], driver_warnings(drivers, s.support_min_judges))


task_app = typer.Typer(help="Role tasks", no_args_is_help=True)
app.add_typer(task_app, name="task")
INPUT_OPT = typer.Option(None, "--input", help="key=value (repeatable; value parsed as JSON when possible)")


@task_app.command("run")
def task_run(role: str = typer.Option(..., "--role"), instructions: str = typer.Option(..., "--instructions"),
             driver: str | None = typer.Option(None, "--driver", help="Override the role's configured driver"),
             inputs: list[str] | None = INPUT_OPT,
             schema: str | None = typer.Option(None, "--schema", help="rk.module:Class (default: free text)"),
             timeout_s: int = typer.Option(600, "--timeout"), project: str | None = typer.Option(None, "--project"),
             as_json: bool = JSON_OPT) -> None:
    """Run one role task (recorded in the project's event log) and print its validated output."""
    data: dict = {}
    for kv in inputs or []:
        key, sep, value = kv.partition("=")
        if not sep:
            typer.secho(f"--input expects key=value, got {kv!r}", fg="red", err=True)
            raise typer.Exit(2)
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
    args = {"project": _project(project), "role": role, "instructions": instructions, "inputs": data,
            "driver": driver, "output_schema": schema, "timeout_s": timeout_s}
    r = _call("rk_task_run", args, write=True, timeout=timeout_s * 3 + 120)

    def render(d):
        head = f"{d['task_id']}  {d['provider']}:{d['model'] or '-'}  {d['duration_s']}s  tools={d['tool_calls']}"
        if d["ok"]:
            typer.echo(f"{head}\n{json.dumps(d['output'], ensure_ascii=False, indent=2)}")
        else:
            typer.secho(f"{head}\nfailed: {d['error']}", fg="red")

    _out(r, as_json, render)
    if not r["ok"]:
        raise typer.Exit(1)


# ---------------- evaluation (local; scratch kernel, never the live project database) ----------------
eval_app = typer.Typer(help="Evaluations on external human-labelled datasets", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
JUDGES_OPT = typer.Option(None, "--judges", help="Comma-separated driver specs (default: the configured judges)")
EVAL_OUT_OPT = typer.Option(Path("docs/eval"), "--out", help="Report directory")


def _judges(text: str | None) -> list:
    from rk.llm.drivers import DriverSpec
    from rk.verify.judge import judge_drivers

    return [DriverSpec.parse(t) for t in text.split(",")] if text else judge_drivers(load_settings(), None)


def _eval_report(run) -> None:
    """Run an evaluation (``run()`` returns its result) and print its report. A driver at its hourly call cap or a
    source's spent API budget (a role task suspended, spec §11.3) stops the evaluation; say so and when to rerun, and
    exit 1."""
    from rk.discovery.executor import RateCapped
    from rk.eval.scratch import rate_capped_message
    from rk.net.http import BudgetExhausted

    try:
        r = run()
    except RateCapped as e:
        typer.secho(rate_capped_message(e), fg="red")
        raise typer.Exit(1) from None
    except BudgetExhausted as e:
        typer.secho(f"stopped: {e}; rerun after the reset.", fg="red")
        raise typer.Exit(1) from None
    typer.echo(Path(r["report"]).read_text())


@eval_app.command("support")
def eval_support(limit: int = typer.Option(100, "--limit", help="Seeded SciFact dev sample (0: every dev pair)"),
                 judges: str | None = JUDGES_OPT, min_judges: int | None = typer.Option(None, "--min-judges"),
                 workers: int = typer.Option(4, "--workers", help="Parallel judge calls"),
                 out: Path = EVAL_OUT_OPT) -> None:
    """Run the V-SUPPORT judges on held-out SciFact dev and report, per generator scenario (anthropic, openai, human),
    the verdict × gold matrix, coverage and precision, plus per-judge agreement and failures. Successful judge
    opinions are cached, so a rerun pays only for missing calls."""
    from rk.eval.support_eval import run_support_eval

    k = min_judges if min_judges is not None else load_settings().support_min_judges
    _eval_report(lambda: run_support_eval(judges=_judges(judges) if judges else None, min_judges=k,
                                          limit=limit or None, workers=workers, report_dir=out))


REPORTS_OPT = typer.Option(..., "--reports", help="Directory of {arxiv_id}.json reports")
GT_OPT = typer.Option(None, "--gt", help="Local ReportBench_v1.1_GT directory (default: download per report)")


@eval_app.command("reportbench")
def eval_reportbench(reports: Path = REPORTS_OPT, gt: Path | None = GT_OPT, as_json: bool = JSON_OPT) -> None:
    """Citation precision/recall of reports against ReportBench ground-truth references."""
    from rk import __version__
    from rk.eval.data import eval_dir
    from rk.eval.reportbench import resolver_titles, score_reports
    from rk.net.http import HttpClient
    from rk.sources.resolver import SourceContext

    s = load_settings()
    ua = f"rk/{__version__}" + (f" (mailto:{s.email})" if s.email else "")
    http = HttpClient(eval_dir() / "http.sqlite", user_agent=ua)
    try:
        r = score_reports(reports, resolver_titles(SourceContext(http=http, settings=s)), gt_dir=gt)
    finally:
        http.close()
    _out(r, as_json, lambda d: typer.echo(
        f"micro P={d['micro']['precision']:.3f} R={d['micro']['recall']:.3f}  macro P={d['macro']['precision']:.3f} "
        f"R={d['macro']['recall']:.3f}  ({len(d['reports'])} reports, {d['n_match']}/{d['n_gt']} references found)"))


EVAL_DRIVER_OPT = typer.Option("omp_gateway:openai-codex/gpt-6-luna", "--driver",
                               help="Chat-API driver for the agent tasks (evaluations run on a scratch kernel)")


@eval_app.command("deepfact")
def eval_deepfact(limit: int = typer.Option(30, "--limit"), driver: str = EVAL_DRIVER_OPT,
                  judges: str | None = JUDGES_OPT, min_judges: int | None = typer.Option(None, "--min-judges"),
                  out: Path = EVAL_OUT_OPT) -> None:
    """DeepFact-Bench (evaluation only): gather evidence with Scout + Reader tasks, then V-SUPPORT each claim."""
    from rk.eval.deepfact import run_deepfact
    from rk.llm.drivers import DriverSpec

    k = min_judges if min_judges is not None else load_settings().support_min_judges
    _eval_report(lambda: run_deepfact(driver=DriverSpec.parse(driver), judges=_judges(judges), min_judges=k,
                                      limit=limit, report_dir=out))


@eval_app.command("litqa2")
def eval_litqa2(limit: int = typer.Option(20, "--limit"), driver: str = EVAL_DRIVER_OPT,
                out: Path = EVAL_OUT_OPT) -> None:
    """LitQA2 multiple-choice questions answered with the Kernel's search and full-text tools."""
    from rk.eval.litqa2 import run_litqa2
    from rk.llm.drivers import DriverSpec

    _eval_report(lambda: run_litqa2(driver=DriverSpec.parse(driver), limit=limit, report_dir=out))


@eval_app.command("stats")
def eval_stats(write_config: bool = typer.Option(False, "--write-config", help="When every check passes, record "
                                                 "the validated rk.stats version so meta-analysis is enabled"),
               out: Path = EVAL_OUT_OPT) -> None:
    """Reproduce published meta-analyses (BCG trials, Raudenbush 2009, Borenstein et al. 2009) with rk.stats and
    report every statistic against its published value. Local: no network, no model."""
    from rk.eval.stats_eval import run_stats_eval

    r = run_stats_eval(out, write_config=write_config)
    typer.echo(f"{r['n_pass']}/{r['n_checks']} published values reproduced (rk.stats {r['stats_version'][:12]})\n"
               f"report: {r['report']}")
    for row in r["rows"]:
        if not row["pass"]:
            typer.secho(f"FAIL {row['dataset']} {row['measure']} {row['model']} {row['study']} {row['statistic']}: "
                        f"published {row['published']}, computed {row['computed']} {row['note']}", fg="red")
    if r["config_written"]:
        typer.echo("stats_validated recorded: meta-analysis is enabled for this version")
    elif write_config:
        typer.secho("not every check passed: stats_validated not recorded", fg="red")
    if not r["all_pass"]:
        raise typer.Exit(1)


@eval_app.command("sr-repro")
def eval_sr_repro(entry: str | None = typer.Option(None, "--entry", help="One review of the set (default: all six)"),
                  write_report: bool = typer.Option(False, "--write-report",
                                                    help="Write sr-repro-<date>.md and .json to --out"),
                  out: Path = EVAL_OUT_OPT) -> None:
    """The systematic-review reproduction set (spec §10): seed each review's project srrepro-<id> in rk serve (its
    question frozen as the RQ), run it until export or continue its run, and score it: recall of the review's
    included studies, whether the main conclusion's direction agrees (the judges' vote) and the hard gates. Runs go
    on in rk serve: run this again to follow them and to score. A failed run is the entry's result and is not started
    again (the back-edge limits count per project): ``rk run resume`` on its project starts a new run with the
    back-edges the project has left; reproduce the entry with its full limits from a fresh RK_HOME."""
    from rk.eval.sr_repro import load_manifest, report_row
    from rk.eval.sr_repro import write_report as write

    entries = load_manifest()
    chosen = [e for e in entries if entry in (None, e.id)]
    if not chosen:
        typer.secho(f"no entry {entry!r} in the reproduction set; its entries: {', '.join(e.id for e in entries)}",
                    fg="red", err=True)
        raise typer.Exit(2)
    rows = []
    for e in chosen:
        run = _call("rk_sr_repro_run", {"entry": e.id}, write=True)
        # judging the direction waits on two judges: as long as a support check may take
        got = _call("rk_sr_repro_score", {"entry": e.id}, write=True, timeout=3 * 3600)
        rows.append(report_row(e, run, got))
        agrees = {True: "agrees", False: "disagrees", None: "not judged"}[got["conclusion_direction_agrees"]]
        if got["direction_compromised"]:
            agrees += f", does not count: {got['direction_compromised']}"
        typer.echo(f"{e.id} ({e.domain}, {len(e.included)} included): {run['project']} {run['action']} "
                   f"(run {run['run_id'] or '-'}: {run['run_status'] or '-'}, stage {run['stage'] or 'done'}); "
                   f"recall {got['recall_included']:.2f} ({len(got['matched'])}/{got['included_total']}); "
                   f"direction {got['expected_direction']} -> {got['direction'] or '-'} ({agrees}"
                   f"{': ' + got['direction_note'] if got['direction_note'] else ''}); "
                   f"gates {'passed' if got['gates_passed'] else 'not passed'}")
    if write_report:
        md, js = write(rows, out)
        typer.echo(f"report: {md} and {js}")


# ---------------- support check (runs inside rk serve; agentic CLIs reach their tools via rk mcp) ----------------
support_app = typer.Typer(help="Claim support checks", no_args_is_help=True)
app.add_typer(support_app, name="support")
TEXT_FILE_OPT = typer.Option(..., "--text", help="File with the passage to check")
GOLD_OPT = typer.Option(None, "--gold", help='JSON file {"<sentence index>": "supported|unsupported|contradicted"}')


@support_app.command("check")
def support_check_cmd(text: Path = TEXT_FILE_OPT, sources: str = typer.Option(..., "--sources",
                                                                              help="Comma-separated item ids"),
                      driver: str | None = typer.Option(None, "--driver", help="Default: the arguer's driver"),
                      judges: str | None = JUDGES_OPT, min_judges: int | None = typer.Option(None, "--min-judges"),
                      gold: Path | None = GOLD_OPT, project: str | None = typer.Option(None, "--project"),
                      as_json: bool = JSON_OPT) -> None:
    """Split a passage into claims, gather verified quotes from the sources, and give each claim a V-SUPPORT verdict."""
    args = {"project": _project(project), "text": text.read_text(), "source_ids": sources.split(","),
            "driver": driver, "judges": judges.split(",") if judges else None, "min_judges": min_judges,
            "gold": json.loads(gold.read_text()) if gold else None}
    r = _call("rk_support_check", args, write=True, timeout=3 * 3600)

    def render(d):
        typer.echo(f"driver {d['driver']} (vendor {d['vendor']})")
        for row in d["rows"]:
            gold_s = f"  gold={row['gold']}" if row.get("gold") else ""
            typer.echo(f"[{row['sentence_index']}] {row['verdict'].upper():<12} nums={'ok' if row['numbers_ok'] else 'UNGROUNDED'}"
                       f"{gold_s}\n    {row['claim']}\n    judges: {'; '.join(row['judges']) or '-'}")
            if row.get("reader_error"):
                typer.secho(f"    reader task FAILED: {row['reader_error']} (no evidence gathered)", fg="red")
            for ev in row["evidence"]:
                typer.echo(f"    evidence {ev}")
        if d.get("missing_gold"):
            typer.secho(f"no claim extracted for gold sentence(s) {d['missing_gold']} (scored as uncertain)", fg="red")
        if d.get("confusion"):
            from rk.eval.metrics import md_matrix

            typer.echo(f"\naccuracy {d['accuracy']:.3f}\n{md_matrix(d['confusion'])}")

    _out(r, as_json, render)


# ---------------- checkpoints ----------------
@checkpoint_app.command("list")
def checkpoint_list(project: str | None = typer.Option(None, "--project"), status: str | None = typer.Option(
        "open", "--status", help="open | answered | all"), as_json: bool = JSON_OPT) -> None:
    args = {"project": _project(project)}
    if status and status != "all":
        args["status"] = status
    r = _call("rk_checkpoint_list", args)

    def render(d):
        if not d["checkpoints"]:
            typer.echo("(no checkpoints)")
        for c in d["checkpoints"]:
            typer.echo(f"{c['id']}  [{c['kind']}] {c['status']}\n  {c['prompt'][:300]}")

    _out(r, as_json, render)


@checkpoint_app.command("answer")
def checkpoint_answer(checkpoint_id: str, answer: str = typer.Option(..., "--json", help="Answer as a JSON object"),
                      project: str | None = typer.Option(None, "--project"),
                      yes: bool = typer.Option(False, "--yes", help="Skip the prompt (only with "
                                                                     "RK_ALLOW_NONINTERACTIVE_ANSWER=1)")) -> None:
    """Answer a checkpoint. You are asked to confirm: only a human may answer."""
    import os
    import sys

    slug = _project(project)
    try:
        payload = json.loads(answer)
    except json.JSONDecodeError as e:
        typer.secho(f"--json is not valid JSON: {e}", fg="red", err=True)
        raise typer.Exit(2) from e
    if not isinstance(payload, dict):
        typer.secho("--json must be a JSON object", fg="red", err=True)
        raise typer.Exit(2)
    cps = {c["id"]: c for c in _call("rk_checkpoint_list", {"project": slug})["checkpoints"]}
    if checkpoint_id not in cps:
        typer.secho(f"no checkpoint {checkpoint_id} in {slug}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"[{cps[checkpoint_id]['kind']}] {cps[checkpoint_id]['prompt']}\n\nYour answer: {payload}")
    if yes:
        if os.environ.get("RK_ALLOW_NONINTERACTIVE_ANSWER") != "1":
            typer.secho("--yes needs RK_ALLOW_NONINTERACTIVE_ANSWER=1", fg="red", err=True)
            raise typer.Exit(2)
    elif not sys.stdin.isatty() and os.environ.get("RK_ALLOW_NONINTERACTIVE_ANSWER") != "1":
        typer.secho("answering needs an interactive terminal (a human confirms every answer)", fg="red", err=True)
        raise typer.Exit(2)
    elif not typer.confirm("Confirm answer?", default=False):
        typer.echo("not answered")
        raise typer.Exit(1)
    _call("rk_checkpoint_answer", {"project": slug, "checkpoint_id": checkpoint_id, "answer": payload}, write=True)
    typer.echo(f"answered {checkpoint_id}")


# ---------------- discovery runs and stages ----------------
def _stage_lines(stages: list[dict]) -> None:
    typer.echo(f"{'stage':<10} {'status':<8} attempt")
    for s in stages:
        line = f"{s['stage']:<10} {s['status']:<8} {s['attempt']}"
        if s.get("waiting_checkpoint"):
            line += f"  waits on checkpoint {s['waiting_checkpoint']}"
        gate = s.get("last_gate")
        if gate and not gate["ok"] and s["status"] != "passed":
            line += f"  last gate failed: {'; '.join(gate['failures'])[:300]}"
        typer.echo(line)


def _run_line(d: dict) -> None:
    if d.get("message"):
        typer.echo(d["message"])
    r = d["run"]
    if r is None:
        typer.echo(f"{d['project']}: no run yet")
        return
    line = f"{r['id']}: {r['status']} (until {r['until']}, session {r['session']})"
    if r.get("reason"):
        line += f": {r['reason']}"
    if d.get("requested"):
        line += f"  [{d['requested']} after the current step]"
    typer.echo(line)


@run_app.command("start")
def run_start(project: str = typer.Argument(..., help="The project (discovery mode d2, or question passed)"),
              until: str = typer.Option(..., "--until", help="topics, question or a later stage with a worker "
                                                            "(rk run status: until_options)"),
              as_json: bool = JSON_OPT) -> None:
    """Start a run of the project's stages in rk serve (also: rk run <project> --until ...). The server checks
    --until: topics or question in D2 discovery, or a later stage with a worker (rk run status: runnable) after
    question passed, with a frozen RQ."""
    r = _call("rk_run_start", {"project": project, "until": until}, write=True)
    _out(r, as_json, lambda d: typer.echo(f"started {d['run_id']} on {d['project']} (until {d['until']}); follow it "
                                          "with: rk run status"))


@run_app.command("status")
def run_status(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The project's latest run and its stages."""
    r = _call("rk_run_status", {"project": _project(project)})

    def render(d):
        _run_line(d)
        _stage_lines(d["stages"])
        if d.get("until_options"):
            typer.echo(f"a run can end at: {', '.join(d['until_options'])} (rk run {d['project']} --until <stage>)")

    _out(r, as_json, render)


@run_app.command("pause")
def run_pause(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Pause the run between steps (cooperative: a role task in flight finishes first).

    Then the run is paused; rk run resume continues it in a new session."""
    _out(_call("rk_run_pause", {"project": _project(project)}, write=True), as_json, _run_line)


@run_app.command("resume")
def run_resume(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Resume a paused run in a new session: its 12 h wall clock and stage task budgets restart. A failed or cancelled
    run cannot be resumed: a new run with the same --until starts instead (and says so)."""
    _out(_call("rk_run_resume", {"project": _project(project)}, write=True), as_json, _run_line)


@run_app.command("cancel")
def run_cancel(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Cancel the run between steps (a role task in flight finishes first)."""
    _out(_call("rk_run_cancel", {"project": _project(project)}, write=True), as_json, _run_line)


@stage_app.command("status")
def stage_status(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Where the project's stages stand: status, attempts, waits and the back-edges used."""
    r = _call("rk_stage_status", {"project": _project(project)})

    def render(d):
        typer.echo(f"{d['project']}: current stage {d['current'] or 'none (every stage passed)'}")
        _stage_lines(d["stages"])
        for edge, e in d["edges"].items():
            extra = f" + {e['allowed']} allowed" if e["allowed"] else ""
            typer.echo(f"  {edge}: used {e['count']} of {d['edge_limit']}{extra}")

    _out(r, as_json, render)


@topic_app.command("list")
def topic_list(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The candidate topics of the topics stage with their gaps and closest works."""
    r = _call("rk_topic_list", {"project": _project(project)})

    def render(d):
        if not d["topics"]:
            typer.echo(f"{d['project']}: no topics yet")
        for t in d["topics"]:
            typer.echo(f"{t['id']}  [{t['status']}] {t['title']}\n  {t['summary'][:300]}\n  gaps: "
                       f"{', '.join(t['gap_ids']) or '-'}; closest works: {len(t['closest'])}")

    _out(r, as_json, render)


# ---------------- production: protocol, PRISMA, screening, extraction ----------------
@protocol_app.command("show")
def protocol_show(protocol_id: str | None = typer.Argument(None, help="Default: the version in force, else the "
                                                                      "latest draft"),
                  project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The review protocol with its record-cap probe, its versions and the deviations between them."""
    args = {"project": _project(project)}
    if protocol_id:
        args["protocol_id"] = protocol_id
    r = _call("rk_protocol_get", args)

    def render(d):
        p = d["protocol"]
        if p is None:
            typer.echo(f"{d['project']}: no protocol yet")
            return
        state = p["status"] + (f" (hash {p['frozen_hash'][:12]})" if p["frozen_hash"] else "")
        typer.echo(f"{p['id']}: version {p['version']}, {state}" + (", in force" if p["id"] == d["in_force"] else ""))
        typer.echo(d["text"])
        typer.echo("\nversions: " + ", ".join(f"v{v['version']} {v['status']}" for v in d["versions"]))
        for dev in d["deviations"]:
            if dev.get("kind") == "analysis":
                typer.echo(f"analysis deviation from v{dev['from_version']} ({dev['stage']}): {dev['reason']}")
            else:
                typer.echo(f"deviation v{dev['from_version']} -> v{dev['to_version']} ({dev['stage']}): "
                           f"{dev['reason']}")
        if d["preregistration"]:
            typer.echo(f"preregistration (G-PREREG): {'ok' if d['prereg_ok'] else 'not met'}")

    _out(r, as_json, render)


@app.command()
def prisma(project: str | None = typer.Option(None, "--project"),
           attempt: int | None = typer.Option(None, "--attempt", help="Default: the attempt search_screen shows"),
           as_json: bool = JSON_OPT) -> None:
    """The PRISMA flow of search_screen with its V-PRISMA check and notes; for a critical review or research
    proposal the selection reasons instead."""
    args: dict = {"project": _project(project)}
    if attempt is not None:
        args["attempt"] = attempt
    r = _call("rk_prisma_get", args)

    def render(d):
        if d["attempt"] is None or (d["prisma"] is None and not d["selection"]):
            typer.echo(f"{d['project']}: search_screen has not run yet")
            return
        p = d["prisma"]
        if p is not None:
            reasons = "; ".join(f"{why or 'no reason'}: {n}" for why, n in p["excluded_ft"].items())
            typer.echo(f"PRISMA (search_screen attempt {d['attempt']}):\n"
                       f"  identified {p['identified']}, duplicates removed {p['duplicates_removed']}\n"
                       f"  screened {p['screened']}, excluded at title/abstract {p['excluded_ta']}\n"
                       f"  sought {p['sought']}, not retrieved {p['not_retrieved']}\n"
                       f"  assessed at full text {p['assessed_ft']}, excluded at full text "
                       f"{sum(p['excluded_ft'].values())}" + (f" ({reasons})" if reasons else "") + "\n"
                       f"  included {p['included']}")
            typer.echo("V-PRISMA: the counts add up" if not d["check"] else "V-PRISMA fails: " + "; ".join(d["check"]))
        else:
            typer.echo(f"{d['paper_type']}: no PRISMA counts; the selection reasons (search_screen attempt "
                       f"{d['attempt']}):")
            for s in d["selection"]:
                typer.echo(f"  {s['phase']:<14} {s['include']:<7} [{s['decided_by']}] {s['title']}: {s['reason']}")
            for why in d["selection_check"]:
                typer.echo(f"selection reasons missing: {why}")
        for note in d["notes"]:
            typer.echo(f"note: {note}")

    _out(r, as_json, render)


@screening_app.command("list")
def screening_list(phase: str | None = typer.Option(None, "--phase", help="title_abstract or full_text"),
                   final: bool = typer.Option(False, "--final", help="The final decisions, not each screener's"),
                   project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The screening decisions of search_screen: each screener's, or with --final the final decision per record."""
    args: dict = {"project": _project(project), "final": final}
    if phase:
        args["phase"] = phase
    r = _call("rk_screening_list", args)

    def render(d):
        if not d["rows"]:
            typer.echo(f"{d['project']}: no screening decisions yet")
        for s in d["rows"]:
            who = f"[{s['decided_by']}]" if d["final"] else f"{s['screener']}" + (
                f" ({s['criterion']})" if s["criterion"] else "")
            typer.echo(f"{s['record_id']} {s['phase']:<14} {s['include']:<7} {who} {s['title']}: {s['reason']}")

    _out(r, as_json, render)


@extraction_app.command("show")
def extraction_show(item_id: str | None = typer.Argument(None, help="One study (library item id)"),
                    project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The extraction of read_extract: the usable values with their quote notes (sufficiency low: read on the abstract
    alone), and apart from them the rows that are no evidence, with their problem."""
    args = {"project": _project(project)}
    if item_id:
        args["item_id"] = item_id
    r = _call("rk_extraction_get", args)

    def render(d):
        if not d["studies"]:
            typer.echo(f"{d['project']}: no extraction yet")
        for s in d["studies"]:
            state = (f"read on the {'full text' if s['read_on'] == 'full_text' else s['read_on']}, sufficiency "
                     f"{s['sufficient']}" if s["in_evidence"] else f"not in the evidence: {s['out_reason']}")
            typer.echo(f"{s['item_id']} {s['title']}: {state}")
            for v in s["values"]:
                note = v["note"] or {}
                typer.echo(f"  {v['field']} = {v['value']}  [{v['sufficient']}] note {note.get('id')}"
                           + {"abstract": " (quote of the abstract)",
                              "record": " (quote of the bibliographic record)"}.get(note.get("against"), ""))
            for v in s["set_aside"]:
                typer.echo(f"  not evidence: {v['field']} = {v['value']}: {v['problem']}")
            for p in s["problems"]:
                typer.echo(f"  problem: {p}")

    _out(r, as_json, render)


def _gate_text(gate: dict | None) -> str:
    if not gate:
        return "not gated"
    if gate["passed"]:
        return "passed its gate"
    return "gate failed: " + "; ".join(gate["failures"]) if gate["failures"] else "not passed yet"


def _claim_line(c: dict) -> str:
    """A claim on one line: id, role, status, contested, V-HEDGE (claims still in the argument), V-STATS, text."""
    flags = [c["status"], *(["contested"] if c["contested"] else []),
             *([f"hedge {c['hedge']['verdict']}"] if c["hedge"]["verdict"] else []),
             *([f"V-STATS {c['v_stats']}"] if "v_stats" in c else [])]
    role = f" {c['role']}" if c.get("role") else ""
    return f"{c['id']}{role} [{', '.join(flags)}] {c['text']}"


@synthesis_app.command("show")
def synthesis_show(attempt: int | None = typer.Option(None, "--attempt", help="Default: the attempt synthesize shows"),
                   project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The synthesis of synthesize: each study's assessment, the pooling groups and their decisions, the kernel's
    statistics and figures with V-STATS, the findings with their status, and the fallbacks (recorded deviations)."""
    args: dict = {"project": _project(project)}
    if attempt is not None:
        args["attempt"] = attempt
    r = _call("rk_synthesis_get", args)

    def render(d):
        if d["attempt"] is None:
            typer.echo(f"{d['project']}: synthesize has not run yet")
            return
        typer.echo(f"synthesize attempt {d['attempt']} ({d['method'] or 'no synthesis plan'}; {_gate_text(d['gate'])})")
        if o := d["pool_options"]:
            typer.echo(f"pool options: model {o['model']}, HKSJ {o['hksj']}, prediction interval df {o['pi_df']}, "
                       f"at least {o['min_studies']} studies with a computable effect")
        for a in d["assessments"]:
            typer.echo(f"  {a['label']}: " + ", ".join(f"{c} {v['score']}" for c, v in a["criteria"].items())
                       + (" (sufficiency capped: abstract only)" if a["capped"] else ""))
        for g in d["groups"]:
            dec = g["decision"]
            state = (f"pooled ({g['pooled']['stt']}, {g['pooled']['fig']})" if g["pooled"]
                     else f"not pooled: {g['fallback']}" if g["fallback"]
                     else f"decided {dec['decision']} by {dec['by']}" if dec else "awaiting a decision")
            typer.echo(f"group {g['group']}: {g['outcome']} ({g['measure']}), {len(g['studies'])} studies; {state}")
        for a in d["artifacts"]:
            typer.echo(f"{a['id']} {a['code_path']}: V-STATS {a['v_stats']['verdict']}"
                       + "".join(f"\n  mismatch: {m}" for m in a["v_stats"]["mismatches"]))
        for c in d["findings"]:
            typer.echo(_claim_line(c))
        for f in d["fallbacks"]:
            typer.echo(f"fallback ({f['deviation_id']}): {f['reason']}")

    _out(r, as_json, render)


@argument_app.command("show")
def argument_show(claim: str | None = typer.Option(None, "--claim", help="Only this claim, its edges, warrants and "
                                                                         "objections"),
                  project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The argument of argue: its claims with their role, status and hedge, the warrants with the Skeptic's tests,
    the objections with their answers, and the conflicts."""
    args: dict = {"project": _project(project)}
    if claim:
        args["claim_id"] = claim
    r = _call("rk_argument_get", args)

    def render(d):
        if d["attempt"] is None:
            typer.echo(f"{d['project']}: argue has not run yet")
            return
        typer.echo(f"argue attempt {d['attempt']} ({_gate_text(d['gate'])}); main claim {d['main_claim_id']}")
        if d["question"] and not claim:
            typer.echo(f"research question: {d['question']}")
        for c in d["claims"]:
            if c["role"] != "warrant":
                typer.echo(_claim_line(c))
        for w in d["warrants"]:
            tests = ", ".join(f"{k} {'ok' if t['ok'] else 'fails'}" for k, t in (w["tests"] or {}).items())
            typer.echo(f"warrant {w['claim_id']} on {w['licenses']}: {w['text']} ({tests or 'not tested'})"
                       + (" [link dropped]" if w["dropped"] else ""))
        for o in d["objections"]:
            answer = f"{o['status']}" + (f" by {o['response']['id']} [{o['response']['status']}]"
                                         if o["response"] else "") + (f": {o['note']}" if o["note"] else "")
            typer.echo(f"objection {o['id']} on {o['target_id']} (priority {o['priority']}, {o['category']}): "
                       f"{o['text']} -> {answer}")
        for c in d["conflicts"]:
            dec = c["decision"]
            typer.echo(f"conflict {c['claim_a']} / {c['claim_b']}: {c['why']} -> "
                       + (f"{dec['decision']} (by {dec['by']})" if dec else "awaiting your decision"))

    _out(r, as_json, render)


@design_app.command("show")
def design_show(attempt: int | None = typer.Option(None, "--attempt", help="Default: the attempt design shows"),
                project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The method design of design: the draft awaiting your approval or the frozen design with its hash, the
    hypotheses with their falsification links, the dropped hypotheses and the feasibility claims."""
    args: dict = {"project": _project(project)}
    if attempt is not None:
        args["attempt"] = attempt
    r = _call("rk_design_get", args)

    def render(d):
        if d.get("untestable"):
            typer.echo(f"design attempt {d['attempt']}: the Ideator says the research question is untestable: "
                       f"{d['untestable']} ({d['status']})")
        if d["draft_id"] is None:
            if not d.get("untestable"):
                typer.echo(f"{d['project']}: no design yet")
            return
        typer.echo(f"{d['draft_id']}: {d['status']}" + (f", frozen hash {d['frozen_hash']}" if d["frozen_hash"] else "")
                   + f" ({_gate_text(d['gate'])})")
        for h in d["hypotheses"]:
            typer.echo(f"hypothesis {h['id'] or h['key']}{' (new)' if h['new'] else ''}: {h['statement']}\n"
                       f"  falsified if: {h['falsification']}\n"
                       f"  tested by {', '.join(h['tested_by']) or 'no item'}; falsified by "
                       f"{', '.join(h['falsified_by']) or 'no measurement'}")
        for i in d["design"]["items"]:
            typer.echo(f"item {i['id']} ({i['kind']}) testing {', '.join(i['hypothesis_ids'])}: {i['sample']}; "
                       f"analysis {i['analysis']}")
            for m in i["measurements"]:
                typer.echo(f"  measurement {m['id']}: {m['what']} by {m['instrument']}")
        for x in d["dropped"]:
            typer.echo(f"dropped {x['id']}: {x['reason']}")
        for c in d["feasibility_claims"]:
            typer.echo(_claim_line(c))
        for p in d["problems"]:
            typer.echo(f"problem: {p}")

    _out(r, as_json, render)


# ---------------- production: the manuscript, revise, the audit and the export ----------------
def _verdict_word(ok: bool | None) -> str:
    return "verified" if ok is True else "failing" if ok is False else "not verified yet"


@draft_app.command("show")
def draft_show(section: str | None = typer.Option(None, "--section", help="One section (default: every required "
                                                                          "section)"),
               version: int | None = typer.Option(None, "--version", help="With --section: that version (default: "
                                                                          "the latest)"),
               project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The manuscript: each section's latest version (or --section at --version), each sentence with the verdict
    recorded for it ([ok], [FAIL] with its problems, [--] not verified yet) and the section's own problems."""
    args: dict = {"project": _project(project)}
    if section:
        args["section"] = section
    if version is not None:
        args["version"] = version
    r = _call("rk_manuscript_get", args)

    def render(d):
        if not d["sections"]:
            typer.echo(f"{d['project']}: no draft yet" + (f" ({d['inputs_error']})" if d["inputs_error"] else ""))
        for s in d["sections"]:
            if s["version"] is None:
                typer.echo(f"{s['section']}: no draft yet")
                continue
            where = s["stage"] + (f", round {s['round']}" if s["round"] else "")
            typer.echo(f"{s['section']} v{s['version']} ({where}): "
                       + ("unreadable" if s["error"] else _verdict_word(s["ok"])))
            if s["error"]:
                typer.echo(f"  error: {s['error']}")
            for p in s["problems"]:
                typer.echo(f"  section problem: {p}")
            for x in s["sentences"]:
                mark = "ok" if x["ok"] is True else "FAIL" if x["ok"] is False else "--"
                typer.echo(f"  [{mark}] {x['index']}. {x['text']}")
                for p in x["problems"]:
                    typer.echo(f"        - {p}")

    _out(r, as_json, render)


def _handled(q: dict) -> str:
    """How a reader question was handled, in words."""
    if q["outcome"] in ("revised", "answered", "unanswerable", "declined", "deleted"):
        return f"{q['outcome']}: {q['note'] or 'no note'}"
    if q["outcome"] == "rejected":
        return "rejected: the revision answering it was not accepted: " + "; ".join(q["rejected_reasons"])
    return "not answered yet"


@revise_app.command("show")
def revise_show(project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The reader's questions of revise (the attempt it shows), each with how it was handled: revised, answered or
    declined with the Writer's note, or a rejected revision with the reasons; then the stage's gate."""
    r = _call("rk_manuscript_get", {"project": _project(project)})

    def render(d):
        rv = d["revise"]
        if rv["attempt"] is None:
            typer.echo(f"{d['project']}: revise has not run yet")
            return
        typer.echo(f"revise attempt {rv['attempt']} ({_gate_text(rv['gate'])})")
        if not rv["questions"]:
            typer.echo("the reader asked no question")
        for q in rv["questions"]:
            at = (f" sentence {q['sentence_index']} “{q['sentence']}”" if q["sentence"]
                  else " (the section as a whole)" if q["sentence_index"] is None else "")
            typer.echo(f"{q['id']} (round {q['round']}, priority {q['priority']}) on {q['section']}{at}: {q['text']}")
            typer.echo(f"  -> {_handled(q)}")
        for q in rv["refused"]:
            typer.echo(f"refused question {q['id']} on {q['section']}: {q['why']}")
        for q in rv["dropped"]:
            typer.echo(f"not carried: {q.get('id')}: {q.get('reason')}")
        if rv["back_edge"]:
            typer.echo(f"back to {rv['back_edge']['to']}: {rv['back_edge']['reason']}")

    _out(r, as_json, render)


def _export_lines(e: dict) -> None:
    typer.echo("export: ready" if e["ready"] is None else f"export: not ready: {e['ready']}")
    if e["built"]:
        typer.echo(f"package: {e['built']['package']}")
        if e["current"] is not None:
            typer.echo(f"exported files withheld: they are not the export of the manuscript as it stands "
                       f"({e['current']})")
    for f in e["files"]:
        typer.echo(f"  {f['name']} ({f['media_type']}, {f['bytes']} bytes)")


@audit_app.command("show")
def audit_show(version: int | None = typer.Option(None, "--version", help="Default: the latest audit report"),
               project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """The audit report: every hard gate with its failures, the stage owning them and the warnings; the final review;
    and whether the manuscript can be exported now (else why not), with the exported files."""
    args: dict = {"project": _project(project)}
    if version is not None:
        args["version"] = version
    r = _call("rk_audit_get", args)

    def render(d):
        rep = d["report"]
        if rep is None:
            typer.echo(f"{d['project']}: no audit report yet")
        else:
            typer.echo(f"Audit report v{rep['version']} (attempt {rep['attempt']}): "
                       + ("passed every hard gate" if rep["ok"] else "failed")
                       + ("; final review approved" if rep["approved"] else "; final review not approved yet"))
            typer.echo("sections: " + ", ".join(f"{s} v{v}" for s, v in rep["sections"].items()))
            for g in rep["gates"]:
                typer.echo(f"{g['gate']}: " + (f"{len(g['failures'])} failure(s)" if g["failures"] else "pass"))
                for f in g["failures"]:
                    typer.echo(f"  - {f}")
            for owner, fs in rep["routes"].items():
                if fs:
                    typer.echo(("fails the stage (no stage the audit can go back to mends it)" if owner == "fail"
                                else f"goes back to {owner}") + f": {len(fs)} failure(s)")
            for w in rep["warnings"]:
                typer.echo(f"warning: {w}")
        for c in d["final_review"]:
            typer.echo(f"final review {c['id']}: {c['status']}"
                       + (f" ({json.dumps(c['answer'], ensure_ascii=False)})" if c["answer"] else ""))
        _export_lines(d["export"])

    _out(r, as_json, render)


EXPORT_WAIT_S = 900.0  # the PDF may take up to 600 s (tectonic)


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def _open_file(path: Path) -> None:
    """Open ``path`` with the OS default app (``rk export --open``)."""
    import os
    import subprocess

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _wait_run(project: str, run_id: str) -> dict:
    """The run once it no longer runs (done, failed, cancelled or waiting), polling rk serve."""
    import time

    deadline = time.monotonic() + EXPORT_WAIT_S
    while True:
        run = _call("rk_run_status", {"project": project})["run"]
        if run is not None and run["id"] == run_id and run["status"] != "running":
            return run
        if time.monotonic() > deadline:
            typer.secho(f"the export run {run_id} is still running after {EXPORT_WAIT_S:.0f} s: follow it with rk run "
                        "status", fg="red", err=True)
            raise typer.Exit(1)
        time.sleep(0.5)


@app.command("export")
def export_cmd(open_pdf: bool = typer.Option(False, "--open", help="Then open paper.pdf with your default app (only "
                                                                   "when run in a terminal)"),
               project: str | None = typer.Option(None, "--project")) -> None:
    """Export the manuscript the audit passed and the final review approved: the export stage (pandoc + citeproc +
    tectonic: paper.pdf, paper.docx, paper.md, and the reproducible package) runs in rk serve. Refused, with the reason,
    unless the audit passed and the final review approved the manuscript as it stands."""
    p = _project(project)
    r = _call("rk_export", {"project": p}, write=True)
    typer.echo(f"exporting {p} (run {r['run_id']})")
    run = _wait_run(p, r["run_id"])
    if run["status"] != "done":
        typer.secho(f"the export run {run['status']}: {run['reason'] or 'no reason given'}", fg="red", err=True)
        raise typer.Exit(1)
    _export_lines(_call("rk_audit_get", {"project": p})["export"])
    if open_pdf:
        if _stdout_is_tty():
            _open_file(Path(r["pdf"]))
            typer.echo(f"opened {r['pdf']}")
        else:
            typer.echo(f"--open: the output is not a terminal, so the PDF is not opened ({r['pdf']})")


# ---------------- tokens ----------------
@token_app.command("create")
def token_create(name: str = typer.Option(..., "--name", help="Label, e.g. the MCP client's name")) -> None:
    """Create a lead token for an external MCP client. It is shown once; only its hash is stored."""
    r = _call("rk_token_create", {"name": name})
    typer.echo(f"lead token '{r['name']}' (store it now; it is not shown again):\n{r['token']}")
    typer.echo(f"Use with: RK_TASK_TOKEN=<token> rk mcp --project <slug>   (revoke: rk token revoke --name {name})")


@token_app.command("list")
def token_list(as_json: bool = JSON_OPT) -> None:
    r = _call("rk_token_list", {})
    _out(r, as_json, lambda d: [typer.echo(f"{t['name']}{'  (revoked)' if t['revoked'] else ''}") for t in d["tokens"]]
         or typer.echo("(no lead tokens)"))


@token_app.command("revoke")
def token_revoke(name: str = typer.Option(..., "--name")) -> None:
    _call("rk_token_revoke", {"name": name})
    typer.echo(f"revoked {name}")



if __name__ == "__main__":
    app()
