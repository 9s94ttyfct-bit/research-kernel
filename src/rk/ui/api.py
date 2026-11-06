"""Workbench routes: ``/ui/login``, ``/ui/api/*`` and ``/ui/ws`` (spec §8.7). Every call runs as the human
(``CallContext(role="cli", actor="ui:user")``); wherever a tool exists the route calls it through the registry, so
permissions and idempotency stay in one place."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from rk.tools.registry import CallContext
from rk.ui.auth import COOKIE, UiAuth

UI_ACTOR = "ui:user"
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


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


def ui_router(auth: UiAuth) -> APIRouter:
    r = APIRouter()
    ctx_dep = ui_context(auth)

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

    return r
