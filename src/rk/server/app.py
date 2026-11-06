"""``rk serve``: the single-writer daemon exposing the tool registry over localhost HTTP."""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from rk import __version__
from rk.tools.registry import CallContext, PermissionDenied, ToolError, ToolRegistry


class ToolCall(BaseModel):
    args: dict = {}
    idempotency_key: str | None = None


def create_app(registry: ToolRegistry, token: str, *, actor: str = "cli:user") -> FastAPI:
    app = FastAPI(title="rk serve", version=__version__)

    def _auth(authorization: str | None) -> None:
        expected = f"Bearer {token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/tools")
    def list_tools(authorization: str | None = Header(default=None)) -> dict:
        _auth(authorization)
        return {"tools": [{"name": t.name, "summary": t.summary, "writes": t.writes,
                           "input_schema": t.input_model.model_json_schema()} for t in registry.list("cli")]}

    @app.post("/api/tools/{name}")
    async def call_tool(name: str, body: ToolCall, request: Request,
                        authorization: str | None = Header(default=None)) -> JSONResponse:
        _auth(authorization)
        ctx = CallContext(role="cli", actor=actor, idempotency_key=body.idempotency_key)
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
                    include_url=False, include_context=False)}}})
        except Exception as e:  # noqa: BLE001 - reported to the client, never swallowed
            return JSONResponse(status_code=500, content={"ok": False, "error": {
                "type": type(e).__name__, "message": str(e), "details": {}}})
        return JSONResponse(content={"ok": True, "result": result})

    return app
