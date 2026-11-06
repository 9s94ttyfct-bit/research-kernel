"""``rk serve``: the single-writer daemon exposing the tool registry over localhost HTTP."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from rk import __version__
from rk.server.chat import ChatManager
from rk.server.tokens import TokenStore
from rk.tools.registry import CallContext, PermissionDenied, ToolError, ToolRegistry
from rk.ui.auth import UiAuth

STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"


class ToolCall(BaseModel):
    args: dict = {}
    idempotency_key: str | None = None


class ChatOpen(BaseModel):
    driver: str
    role: str = "lead"
    project: str


class ChatSend(BaseModel):
    text: str


class OpenHuman(BaseModel):
    url: str



def create_app(registry: ToolRegistry, token: str, *, actor: str = "cli:user",
               tokens: TokenStore | None = None, chats: ChatManager | None = None, ui: UiAuth | None = None,
               kernel=None, browser: Callable[[], Any] | None = None) -> FastAPI:
    """The serve token acts as the human CLI (role ``cli``). Any other bearer must be a live task or lead token;
    its calls run with that grant's role, project binding and tool list. With ``ui`` the Workbench is served under
    ``/ui`` with its own session cookie (``rk.ui.auth``)."""
    app = FastAPI(title="rk serve", version=__version__)
    browser = browser or (lambda: None)

    def _ctx(authorization: str | None, idempotency_key: str | None = None) -> CallContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")
        if hmac.compare_digest(authorization, f"Bearer {token}"):
            return CallContext(role="cli", actor=actor, idempotency_key=idempotency_key)
        grant = tokens.lookup(authorization.removeprefix("Bearer ")) if tokens is not None else None
        if grant is None:
            raise HTTPException(status_code=401, detail="missing, invalid, revoked or expired bearer token")
        return CallContext(role=grant.role, actor=grant.actor, idempotency_key=idempotency_key, task_id=grant.task_id,
                           project=grant.project, allowed_tools=grant.tools)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/tools")
    def list_tools(authorization: str | None = Header(default=None)) -> dict:
        ctx = _ctx(authorization)
        visible = [t for t in registry.list(ctx.role, ctx.allowed_tools)
                   if ctx.project is None or "project" in t.input_model.model_fields]
        return {"tools": [{"name": t.name, "summary": t.summary, "writes": t.writes,
                           "input_schema": t.input_model.model_json_schema()} for t in visible]}

    @app.post("/api/tools/{name}")
    async def call_tool(name: str, body: ToolCall, request: Request,
                        authorization: str | None = Header(default=None)) -> JSONResponse:
        ctx = _ctx(authorization, body.idempotency_key)
        try:
            result = await run_in_threadpool(registry.call, name, body.args, ctx)
        except ToolError as e:
            return JSONResponse(status_code=e.status, content={"ok": False, "error": {
                "type": e.type, "message": e.message, "details": e.details}})
        except PermissionDenied as e:
            return JSONResponse(status_code=403, content={"ok": False, "error": {
                "type": "permission_denied", "message": str(e), "details": {}}})
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"ok": False, "error": {
                "type": "validation_error", "message": "invalid arguments", "details": {"errors": e.errors(
                    include_url=False, include_context=False, include_input=False)}}})
        except Exception as e:  # noqa: BLE001 - reported to the client, never swallowed
            return JSONResponse(status_code=500, content={"ok": False, "error": {
                "type": type(e).__name__, "message": str(e), "details": {}}})
        return JSONResponse(content={"ok": True, "result": result})

    # ---- chat sessions (human CLI / Workbench only) ----
    def _cli_only(authorization: str | None) -> None:
        if _ctx(authorization).role != "cli":
            raise HTTPException(status_code=403, detail="chat sessions are opened by the human user")
        if chats is None:
            raise HTTPException(status_code=503, detail="chat is not enabled on this server")

    @app.post("/api/chat")
    async def chat_open(body: ChatOpen, authorization: str | None = Header(default=None)) -> JSONResponse:
        _cli_only(authorization)
        try:
            chat_id = await run_in_threadpool(chats.open, body.driver, body.role, body.project)
        except (ValueError, LookupError) as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": {
                "type": "chat_unavailable", "message": str(e), "details": {}}})
        return JSONResponse(content={"ok": True, "result": {"chat_id": chat_id}})

    @app.post("/api/chat/{chat_id}/send")
    def chat_send(chat_id: str, body: ChatSend, authorization: str | None = Header(default=None)) -> StreamingResponse:
        _cli_only(authorization)

        def lines():
            try:
                for ev in chats.send(chat_id, body.text):
                    yield json.dumps({"kind": ev.kind, "data": ev.data}, ensure_ascii=False, default=str) + "\n"
            except (KeyError, RuntimeError) as e:
                yield json.dumps({"kind": "error", "data": {"message": f"{type(e).__name__}: {e}"}}) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.delete("/api/chat/{chat_id}")
    def chat_close(chat_id: str, authorization: str | None = Header(default=None)) -> dict:
        _cli_only(authorization)
        return {"ok": True, "result": {"closed": chats.close(chat_id)}}

    # ---- Workbench hand-off (human CLI only) ----
    def _human_cli(authorization: str | None) -> None:
        if _ctx(authorization).role != "cli":
            raise HTTPException(status_code=403, detail="only the human CLI may do this")

    @app.post("/api/ui/link")
    def ui_link(authorization: str | None = Header(default=None)) -> dict:
        _human_cli(authorization)
        if ui is None:
            raise HTTPException(status_code=503, detail="the Workbench is not enabled on this server")
        return {"ok": True, "result": {"url": ui.login_url()}}

    @app.post("/api/browser/open-human")
    async def open_human(body: OpenHuman, authorization: str | None = Header(default=None)) -> dict:
        _human_cli(authorization)
        svc = browser()
        if svc is None or not svc.running:
            raise HTTPException(status_code=503, detail="the built-in browser is not running")
        tab = await run_in_threadpool(svc.session.new_tab, None, body.url)
        return {"ok": True, "result": {"tab": tab.id}}

    if ui is not None:
        from rk.ui.api import ui_router

        app.include_router(ui_router(ui))
        app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
