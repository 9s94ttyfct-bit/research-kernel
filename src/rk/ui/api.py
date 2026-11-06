"""Workbench routes: ``/ui/login``, ``/ui/api/*`` and ``/ui/ws`` (spec §8.7). Every call runs as the human
(``CallContext(role="cli", actor="ui:user")``); wherever a tool exists the route calls it through the registry, so
permissions and idempotency stay in one place."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from rk.checkpoints import get_checkpoint, list_checkpoints
from rk.cli.setup import (
    capabilities,
    check_crossref,
    check_openalex,
    check_providers,
    check_s2,
    library_fetch_test,
)
from rk.config import check_library_proxy, load_settings, save_settings
from rk.tools.registry import CallContext, PermissionDenied, ToolError
from rk.ui.auth import COOKIE, UiAuth
from rk.ui.drafts import list_drafts, read_draft, render_draft

log = logging.getLogger(__name__)
UI_ACTOR = "ui:user"
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
SECRET_FIELDS = ("openalex_key", "s2_key", "anthropic_api_key", "openai_api_key", "ollama_api_key")
PUBLIC_FIELDS = ("email", "unpaywall_email", "current_project", "library_name", "library_portal_url", "library_proxy",
                 "library_discovery", "primo_base", "primo_vid", "primo_tab", "primo_scope", "summon_base",
                 "translation_server_url", "browser_allow_hosts", "fulltext_browser_fallback", "browser_enabled",
                 "drivers", "support_model")


class SettingsPatch(BaseModel):
    """What the Workbench settings page may change (calibration results and limits stay in config.toml/CLI)."""

    model_config = ConfigDict(extra="forbid")
    email: str | None = None
    unpaywall_email: str | None = None
    openalex_key: str | None = None
    s2_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_api_key: str | None = None
    library_name: str | None = None
    library_portal_url: str | None = None
    library_proxy: str | None = None
    library_discovery: Literal["primo", "summon"] | None = None
    primo_base: str | None = None
    primo_vid: str | None = None
    primo_tab: str | None = None
    primo_scope: str | None = None
    summon_base: str | None = None
    translation_server_url: str | None = None
    browser_allow_hosts: list[str] | None = None
    fulltext_browser_fallback: bool | None = None

    _proxy = field_validator("library_proxy")(lambda cls, v: check_library_proxy(v))


class ProjectNew(BaseModel):
    slug: str
    title: str
    field: str | None = None
    paper_type: str | None = None


class Slug(BaseModel):
    slug: str


class Answer(BaseModel):
    project: str
    answer: dict


class TagBody(BaseModel):
    project: str
    item_id: str
    add: list[str] = []
    remove: list[str] = []


class CollectBody(BaseModel):
    project: str
    item_id: str
    collection: str


class ImportBody(BaseModel):
    project: str
    format: Literal["bibtex", "ris", "csl"]
    text: str


class SavePage(BaseModel):
    project: str
    tab: str | None = None
    collection: str | None = None


def ui_context(auth: UiAuth):
    def dep(request: Request) -> CallContext:
        if not auth.check_cookie(request.cookies.get(COOKIE)):
            raise HTTPException(status_code=401, detail="open the Workbench through `rk open` or the browser window")
        if request.method not in _SAFE and not auth.check_origin(request.headers.get("origin")):
            raise HTTPException(status_code=403, detail="cross-origin request refused")
        return CallContext(role="cli", actor=UI_ACTOR)

    return dep


def ok(result) -> JSONResponse:
    return JSONResponse({"ok": True, "result": result})


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def _event_rows(ps, after: int, limit: int = 500) -> list[dict]:
    rows = ps.db.execute("SELECT seq, ts, actor_kind, actor, type, entity_id FROM events WHERE seq > ? ORDER BY seq "
                         "LIMIT ?", (after, limit))
    return [dict(r) for r in rows]


def ui_router(auth: UiAuth, *, kernel=None, registry=None, chats=None,
              browser: Callable[[], Any] | None = None) -> APIRouter:
    r = APIRouter()
    ctx_dep = ui_context(auth)
    browser = browser or (lambda: None)

    def call(name: str, args: dict, ctx: CallContext) -> dict:
        tool = registry.get(name)
        key = f"ui:{uuid.uuid4().hex}" if tool.writes else None
        try:
            return registry.call(name, args, CallContext(role=ctx.role, actor=ctx.actor, idempotency_key=key))
        except ToolError as e:
            raise HTTPException(status_code=e.status, detail={"type": e.type, "message": e.message,
                                                               "details": e.details}) from e
        except PermissionDenied as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False,
                                                                 include_input=False)) from e

    def project(slug: str):
        try:
            return kernel.project(slug)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    def badges(ps, lib, item) -> dict:
        fulltext = ps.db.execute("SELECT 1 FROM snapshots WHERE item_id=? LIMIT 1", (item.id,)).fetchone() is not None
        quotes = 0
        for row in ps.db.execute("SELECT data FROM notes WHERE item_id=?", (item.id,)):
            n = json.loads(row["data"])
            quotes += n.get("kind") == "quote" and bool((n.get("verified") or {}).get("quote", {}).get("ok"))
        return {"ref_status": item.ref_status, "retraction": item.retraction, "fulltext": fulltext,
                "quotes_verified": quotes}

    # ---- session ----------------------------------------------------------------------------------------------
    @r.get("/ui/login", response_model=None)
    def login(code: str = "") -> HTMLResponse | RedirectResponse:
        sid = auth.redeem(code)
        if sid is None:
            return HTMLResponse("<!doctype html><title>Research Kernel</title><p>This Workbench link was already used "
                                "or has expired. Run <code>rk open</code> for a new one.</p>", status_code=403)
        resp = RedirectResponse("/ui/", status_code=303)
        resp.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/")
        return resp

    @r.get("/ui/api/session")
    def session(request: Request) -> JSONResponse:
        ctx = ctx_dep(request)
        return ok({"actor": ctx.actor})

    @r.delete("/ui/api/session")
    def logout(request: Request) -> JSONResponse:
        ctx_dep(request)
        auth.logout(request.cookies.get(COOKIE))
        resp = ok({"logged_out": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---- projects ---------------------------------------------------------------------------------------------
    @r.get("/ui/api/projects")
    def projects(request: Request) -> JSONResponse:
        ctx = ctx_dep(request)
        return ok({**call("rk_project_list", {}, ctx), "current": load_settings().current_project})

    @r.post("/ui/api/projects")
    def project_new(body: ProjectNew, request: Request) -> JSONResponse:
        return ok(call("rk_project_create", body.model_dump(exclude_none=True), ctx_dep(request)))

    @r.post("/ui/api/projects/current")
    def project_current(body: Slug, request: Request) -> JSONResponse:
        ctx_dep(request)
        project(body.slug)
        save_settings(load_settings().model_copy(update={"current_project": body.slug}))
        return ok({"current": body.slug})

    # ---- library ----------------------------------------------------------------------------------------------
    @r.get("/ui/api/library")
    def library(request: Request, collection: str | None = None, q: str | None = None) -> JSONResponse:
        ctx_dep(request)
        ps, lib = project(request.query_params.get("project", ""))
        col = None
        if collection:
            col = collection if collection.startswith("col_") else lib.collection_by_name(collection)
            if col is None:
                raise HTTPException(status_code=404, detail=f"no collection {collection}")
        items = lib.list(col)
        if q:  # full-text search, kept in rank order, within the chosen collection
            members = {i.id: i for i in items}
            items = [members[h.item_id] for h in lib.search(q, 200) if h.item_id in members]
        return ok({"items": [{**i.model_dump(mode="json"), "badges": badges(ps, lib, i)} for i in items],
                   "collections": lib.collections()})

    @r.get("/ui/api/library/item/{item_id}")
    def library_item(item_id: str, request: Request) -> JSONResponse:
        ctx_dep(request)
        ps, lib = project(request.query_params.get("project", ""))
        try:
            item = lib.get(item_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"no item {item_id}") from e
        atts = [dict(a) for a in ps.db.execute(
            "SELECT id, kind, filename, origin, access_note, fetched_at FROM attachments WHERE item_id=? ORDER BY "
            "fetched_at", (item_id,))]
        notes = [json.loads(n["data"]) for n in ps.db.execute("SELECT data FROM notes WHERE item_id=? ORDER BY id",
                                                              (item_id,))]
        return ok({"item": item.model_dump(mode="json"), "badges": badges(ps, lib, item), "attachments": atts,
                   "notes": notes})

    @r.get("/ui/api/files/{slug}/{att_id}", response_model=None)
    def file(slug: str, att_id: str, request: Request) -> FileResponse:
        ctx_dep(request)
        ps, _ = project(slug)
        row = ps.db.execute("SELECT item_id, kind, filename FROM attachments WHERE id=?", (att_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no attachment {att_id} in {slug}")
        path = ps.files_dir / row["item_id"] / row["filename"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="attachment file missing")
        headers = {"X-Content-Type-Options": "nosniff"}
        if row["kind"] == "pdf":
            return FileResponse(path, media_type="application/pdf", headers=headers,
                                content_disposition_type="inline")
        # a saved web page: untrusted markup, shown without scripts and in an opaque origin
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        return FileResponse(path, media_type="text/html", headers=headers, content_disposition_type="inline")

    @r.post("/ui/api/library/tag")
    def tag(body: TagBody, request: Request) -> JSONResponse:
        return ok(call("rk_library_tag", body.model_dump(), ctx_dep(request)))

    @r.post("/ui/api/library/collect")
    def collect(body: CollectBody, request: Request) -> JSONResponse:
        return ok(call("rk_library_collect", body.model_dump(), ctx_dep(request)))

    @r.post("/ui/api/library/import")
    def library_import(body: ImportBody, request: Request) -> JSONResponse:
        return ok(call("rk_library_import", body.model_dump(), ctx_dep(request)))

    @r.get("/ui/api/library/export", response_model=None)
    def library_export(request: Request, format: Literal["bibtex", "ris", "csl"] = "bibtex",
                       collection: str | None = None) -> PlainTextResponse:
        ctx = ctx_dep(request)
        args = {"project": request.query_params.get("project", ""), "format": format}
        if collection:
            args["collection"] = collection
        out = call("rk_library_export", args, ctx)
        ext = {"bibtex": "bib", "ris": "ris", "csl": "json"}[format]
        return PlainTextResponse(out["text"], headers={"Content-Disposition": f'attachment; filename="library.{ext}"'})

    @r.post("/ui/api/library/save-page")
    def save_page(body: SavePage, request: Request) -> JSONResponse:
        return ok(call("rk_browser_save_page", body.model_dump(exclude_none=True), ctx_dep(request)))

    # ---- checkpoints and progress ----------------------------------------------------------------------------
    @r.get("/ui/api/checkpoints")
    def checkpoints(request: Request, status: Literal["open", "answered", "cancelled"] | None = None) -> JSONResponse:
        ctx = ctx_dep(request)
        args = {"project": request.query_params.get("project", "")}
        if status:
            args["status"] = status
        return ok(call("rk_checkpoint_list", args, ctx))

    @r.post("/ui/api/checkpoints/{chk_id}/answer")
    def answer(chk_id: str, body: Answer, request: Request) -> JSONResponse:
        return ok(call("rk_checkpoint_answer", {"project": body.project, "checkpoint_id": chk_id,
                                                "answer": body.answer}, ctx_dep(request)))

    @r.get("/ui/api/progress")
    def progress(request: Request) -> JSONResponse:
        ctx_dep(request)
        ps, _ = project(request.query_params.get("project", ""))
        tasks = [dict(t) for t in ps.db.execute("SELECT id, role, driver, vendor, status, attempts, error, created, "
                                                "finished FROM tasks ORDER BY created DESC LIMIT 100")]
        last = ps.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        return ok({"tasks": tasks, "open_checkpoints": list_checkpoints(ps, "open"),
                   "events": list(reversed(_event_rows(ps, max(0, last - 200), 200)))})

    # ---- drafts -----------------------------------------------------------------------------------------------
    @r.get("/ui/api/drafts")
    def drafts(request: Request) -> JSONResponse:
        ctx_dep(request)
        ps, _ = project(request.query_params.get("project", ""))
        return ok({"drafts": list_drafts(ps)})

    @r.get("/ui/api/drafts/{section}")
    def draft(section: str, request: Request, version: int | None = None) -> JSONResponse:
        ctx_dep(request)
        ps, lib = project(request.query_params.get("project", ""))
        try:
            n, text = read_draft(ps, section, version)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"no draft {e}") from e
        return ok({"section": section, "version": n, "markdown": text, **render_draft(ps, lib, text)})

    # ---- settings and browser ---------------------------------------------------------------------------------
    @r.get("/ui/api/settings")
    def settings_get(request: Request) -> JSONResponse:
        ctx_dep(request)
        s = load_settings()
        return ok({**{f: getattr(s, f) for f in PUBLIC_FIELDS},
                   **{f"{f}_set": bool(getattr(s, f)) for f in SECRET_FIELDS}})

    @r.post("/ui/api/settings")
    async def settings_post(request: Request) -> JSONResponse:
        ctx_dep(request)
        try:
            patch = SettingsPatch.model_validate(await request.json())
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False,
                                                                 include_input=False)) from e
        changes = patch.model_dump(exclude_unset=True)
        save_settings(load_settings().model_copy(update=changes))
        return ok({"saved": sorted(changes)})

    @r.post("/ui/api/setup/check/{step}")
    async def setup_check(step: str, request: Request) -> JSONResponse:
        """The checks of `rk setup` (spec §8.8), run from the settings page."""
        ctx = ctx_dep(request)
        body = await request.json() if (await request.body()) else {}
        s = load_settings()
        if step == "email":
            okay = await run_in_threadpool(check_crossref, kernel.http, str(body.get("email") or s.email or ""))
            return ok({"status": "available (polite pool)" if okay else "check failed"})
        if step == "openalex":
            info = await run_in_threadpool(check_openalex, kernel.http, str(body.get("key") or s.openalex_key or ""))
            return ok({"status": f"available (daily budget remaining {info['remaining']})" if info else "key rejected"})
        if step == "s2":
            okay = await run_in_threadpool(check_s2, kernel.http, str(body.get("key") or s.s2_key or ""))
            return ok({"status": "available" if okay else "key rejected or missing"})
        if step == "providers":
            return ok(await run_in_threadpool(check_providers, s))
        if step == "capabilities":
            svc = browser()
            providers = await run_in_threadpool(check_providers, s)
            return ok({"capabilities": capabilities(s, browser_running=bool(svc and svc.running),
                                                    providers=providers)})
        if step == "library":
            def as_call(name: str, args: dict, *, write: bool = False) -> dict:
                return call(name, args, ctx)

            doi = str(body.get("doi") or "").strip()
            if not doi:
                raise HTTPException(status_code=422, detail="give the DOI of a paywalled article to test")
            return ok(await run_in_threadpool(library_fetch_test, as_call, doi))
        raise HTTPException(status_code=404, detail=f"no setup step {step}")

    @r.post("/ui/api/browser/open-portal")
    def open_portal(request: Request) -> JSONResponse:
        """Open your library's start page (from Settings) in a browser tab of yours, for signing in."""
        ctx_dep(request)
        svc = browser()
        if svc is None or not svc.running:
            raise HTTPException(status_code=503, detail="the built-in browser is not running")
        portal = load_settings().library_portal_url
        if not portal:
            raise HTTPException(status_code=422, detail="set the library start page first")
        return ok({"tab": svc.session.new_tab(None, portal).id})

    @r.get("/ui/api/browser/status")
    def browser_status(request: Request) -> JSONResponse:
        ctx = ctx_dep(request)
        svc = browser()
        return ok(svc.status(ctx) if svc is not None and svc.running else {"running": False})

    # ---- live updates and chat --------------------------------------------------------------------------------
    @r.websocket("/ui/ws")
    async def ws(websocket: WebSocket) -> None:
        if not (auth.check_cookie(websocket.cookies.get(COOKIE)) and auth.check_origin(websocket.headers.get("origin"))):
            await websocket.close(code=4403)
            return
        slug = websocket.query_params.get("project", "")
        try:
            ps, _ = kernel.project(slug)
        except LookupError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        ctx = CallContext(role="cli", actor=UI_ACTOR)
        send_lock = asyncio.Lock()

        async def send(msg: dict) -> None:
            async with send_lock:
                await websocket.send_json(_jsonable(msg))

        last = await run_in_threadpool(lambda: ps.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])
        await send({"type": "hello", "project": slug, "seq": last})

        async def pump() -> None:
            nonlocal last
            shown = None
            while True:
                try:
                    for ev in await run_in_threadpool(_event_rows, ps, last):
                        last = ev["seq"]
                        await send({"type": "event", "event": ev})
                        if ev["type"].startswith("checkpoint.") and ev["entity_id"]:
                            cp, open_cps = await run_in_threadpool(
                                lambda cid=ev["entity_id"]: (get_checkpoint(ps, cid), list_checkpoints(ps, "open")))
                            await send({"type": "checkpoint", "checkpoint": cp, "open_count": len(open_cps)})
                    svc = browser()
                    status = (await run_in_threadpool(svc.status, ctx)) if svc is not None and svc.running else \
                        {"running": False}
                    if status != shown:
                        shown = status
                        await send({"type": "browser", "status": status})
                except (WebSocketDisconnect, RuntimeError):
                    return  # the socket closed
                except Exception as e:  # noqa: BLE001 — one failed poll (browser closing, a locked db) must not end the updates
                    log.warning("ui websocket update failed: %s", e)
                await asyncio.sleep(1.0)

        async def stream(chat_id: str, text: str) -> None:
            loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue()

            def post(m: dict | None) -> None:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, m)
                except RuntimeError:  # the socket's event loop has shut down: nobody is left to receive this
                    pass

            def run() -> None:
                try:
                    for ev in chats.send(chat_id, text):  # finish the turn even if the socket is gone
                        post({"kind": ev.kind, "data": ev.data})
                except (KeyError, RuntimeError) as e:
                    post({"kind": "error", "data": {"message": str(e)}})
                finally:
                    post(None)

            threading.Thread(target=run, name="rk-ui-chat", daemon=True).start()
            while (m := await q.get()) is not None:
                await send({"type": "chat", "chat_id": chat_id, **m})

        pump_task = asyncio.create_task(pump())
        streams: set[asyncio.Task] = set()
        try:
            while True:
                msg = await websocket.receive_json()
                kind = msg.get("type")
                if kind == "chat.open":
                    driver = msg.get("driver") or load_settings().drivers.get("lead", "claude_cli")
                    ref = msg.get("ref")  # echoed so a page binds only to the chat it asked for
                    try:
                        chat_id = await run_in_threadpool(chats.open, driver, "lead", slug)
                        await send({"type": "chat.opened", "ref": ref, "chat_id": chat_id, "driver": driver})
                    except (ValueError, LookupError) as e:
                        await send({"type": "chat.error", "ref": ref, "message": str(e)})
                elif kind == "chat.send" and msg.get("chat_id"):
                    t = asyncio.create_task(stream(msg["chat_id"], str(msg.get("text", ""))))
                    streams.add(t)
                    t.add_done_callback(streams.discard)
                elif kind == "chat.close" and msg.get("chat_id"):
                    await run_in_threadpool(chats.close, msg["chat_id"])
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            for t in streams:
                t.cancel()

    return r
