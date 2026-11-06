"""``rk`` command-line interface (a thin client of ``rk serve``)."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import typer

from rk.config import load_settings, rk_home, save_settings

app = typer.Typer(help="Research Kernel CLI", no_args_is_help=True, add_completion=False)
project_app = typer.Typer(help="Projects", no_args_is_help=True)
library_app = typer.Typer(help="Project library", no_args_is_help=True)
note_app = typer.Typer(help="Notes", no_args_is_help=True)
token_app = typer.Typer(help="Lead tokens for external MCP clients", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(library_app, name="library")
app.add_typer(note_app, name="note")
app.add_typer(token_app, name="token")

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


def _call(name: str, args: dict, *, write: bool = False) -> dict:
    from rk.cli.client import RkError

    try:
        c = _client()
    except RkError as e:
        _fail(e)
    try:
        return c.call(name, args, write=write)
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
@app.command()
def serve(detach: bool = typer.Option(False, help="Start in the background and return")) -> None:
    """Run the single-writer daemon (normally auto-started by other commands)."""
    home = rk_home()
    if detach:
        c = _client()
        typer.echo(f"rk serve running at {c.base_url}")
        return
    import uvicorn

    from rk.server.app import create_app
    from rk.server.chat import ChatManager
    from rk.server.runtime import AlreadyRunning, acquire_serve_lock, release
    from rk.tools.builtin import build_registry
    from rk.workspace import Kernel

    try:
        rt = acquire_serve_lock(home)
    except AlreadyRunning as e:
        typer.secho(str(e), fg="yellow", err=True)
        raise typer.Exit(0) from e
    kernel = Kernel(home)
    registry = build_registry(kernel)
    chats = ChatManager(kernel, registry)
    server = uvicorn.Server(uvicorn.Config(create_app(registry, rt.token, tokens=kernel.tokens, chats=chats),
                                           log_level="warning"))
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    try:
        server.run(sockets=[rt.sock])
    finally:
        chats.close_all()
        kernel.close()
        release(rt)


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


# ---------------- search / add / fetch ----------------
@app.command()
def search(query: str, source: str = typer.Option("openalex", "--source"), limit: int = typer.Option(10, "--limit"),
           add: bool = typer.Option(False, "--add", help="Add hits to the current project's library"),
           project: str | None = typer.Option(None, "--project"), as_json: bool = JSON_OPT) -> None:
    """Search OpenAlex, Europe PMC, PubMed, arXiv or Semantic Scholar."""
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
    """Guided setup: contact email and free API keys (OpenAlex, Semantic Scholar)."""
    from rk import __version__
    from rk.cli.setup import run_setup
    from rk.net.http import HttpClient

    http = HttpClient(rk_home() / "cache" / "http.sqlite", user_agent=f"rk/{__version__} setup")
    try:
        _, caps = run_setup(http, lambda q, d: typer.prompt(q, default=d, show_default=False),
                            lambda m: typer.echo("\n" + m))
    finally:
        http.close()
    typer.echo("\nCapabilities:")
    for k, v in caps.items():
        typer.echo(f"  {k:<17} {v}")
    typer.echo(f"\nSaved to {rk_home() / 'config.toml'} (mode 600).")


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
