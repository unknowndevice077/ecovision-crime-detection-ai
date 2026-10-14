"""EcoVision Sentinel backend: the FastAPI app and its entry point.

Shared state and helpers live in backend_core.py and every endpoint lives in a
router under routers/. This file builds the app, installs middleware, mounts
the routers and starts uvicorn. Electron, run_dev_system.bat and preflight.py
launch this file by path, so it stays at app/backend.py."""
import os
import traceback as _traceback

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from db import get_conn
from port_utils import find_free_port, write_runtime_port
from backend_core import (
    RECORDINGS_DIR, SCREENSHOTS_DIR, _audit_request_state, limiter, log_audit, sys_config,
    verify_token,
)
from routers import ALL_ROUTERS

app = FastAPI(
    title=sys_config["system"]["name"],
    version=sys_config["system"]["version"]
)

# Audit catch-all (2026-09-30): only some endpoints wrote their own detailed
# audit entry, so most changes -- confirming/dismissing an incident,
# creating or editing an account, resetting a password, adding a camera --
# left no trace at all. Every successful (or permission-denied) change
# request now leaves at least one row: the endpoint's own detailed entry
# when it writes one, otherwise a generic "<METHOD> <route>" entry with the
# actor, route parameters and outcome. Machine-to-machine traffic from the
# AI core / ESP32 and continuous PTZ nudges are left out -- they'd bury the
# human actions this log exists for.
_AUDIT_SKIP_PATHS = {
    "/api/ai_trigger", "/api/ai_register_clip", "/api/esp32/register",
    "/api/ptz/move", "/api/ptz/stop", "/api/login", "/api/logout",
}


@app.middleware("http")
async def audit_every_change(request: Request, call_next):
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return await call_next(request)
    state = {"logged": False}
    token = _audit_request_state.set(state)
    try:
        response = await call_next(request)
    finally:
        _audit_request_state.reset(token)
    try:
        route = request.scope.get("route")
        template = getattr(route, "path", None) or request.url.path
        status = response.status_code
        if state["logged"] or template in _AUDIT_SKIP_PATHS or not (status < 400 or status == 403):
            return response
        auth = request.headers.get("authorization") or ""
        try:
            actor = verify_token(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else {}
        except HTTPException:
            actor = {}
        params = dict(request.scope.get("path_params") or {})
        target_type = template.strip("/").split("/")[1] if template.count("/") >= 2 else "system"
        conn = get_conn()
        try:
            cur = conn.cursor()
            log_audit(cur, {"id": actor.get("id"), "username": actor.get("username") or "anonymous"},
                      f"{'denied ' if status == 403 else ''}{request.method} {template}",
                      target_type, ",".join(str(v) for v in params.values()) or "-",
                      snapshot={"status": status, **({"params": params} if params else {})})
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️  [AUDIT] catch-all could not record {request.method} {request.url.path}: {e}")
    return response


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if sys_config["security"]["enable_cors"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sys_config["security"]["cors_origins"],
        # This is a local, single-user desktop app -- the frontend's own
        # port can fall back (findFreePortForFrontend in electron/main.js)
        # if 3000 is taken, at which point a fixed single-origin allowlist
        # (e.g. only "http://127.0.0.1:3000") blocks every request from
        # whatever port it actually landed on. Accepting any localhost/
        # 127.0.0.1 port has no real security cost here -- there's no
        # multi-tenant server exposed to arbitrary origins to protect
        # against, just this one machine's own Electron renderer.
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Was invisible: an unhandled exception anywhere in a route became Starlette's
# generic 500 with no detail and (per FastAPI's default exception handling
# order) no CORS headers attached -- so the browser reported "blocked by CORS
# policy" while the real cause, a Python traceback, went nowhere anyone was
# looking. This prints the full traceback to this console on every 500 (so
# the actual error is finally visible here, not just "Internal Server
# Error"), and returns a normal JSONResponse instead of letting Starlette's
# default path swallow the response -- which also means CORSMiddleware gets
# a real chance to add its headers, so the browser stops misreporting these
# as CORS failures too.
@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception):
    print(f"\n===== UNHANDLED EXCEPTION on {request.method} {request.url.path} =====")
    _traceback.print_exc()
    print("=" * 60 + "\n")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

app.mount("/static/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")
app.mount("/static/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

for router in ALL_ROUTERS:
    app.include_router(router)


if __name__ == "__main__":
    preferred_port = sys_config["backend"]["port"]
    actual_port = find_free_port(preferred_port)
    write_runtime_port("backend", actual_port)
    # BUG FOUND 2026-08-19: with no reload_dirs, uvicorn's --reload watches
    # the process's cwd, which run_dev_system.bat sets to the WHOLE repo
    # root ("Will watch for changes in these directories: ['...EcoVisionCode']").
    # Every edit anywhere -- maincode/, electron/, even a weights file being
    # touched -- restarted this process. Each restart re-runs init_db(),
    # which looks like a fresh boot from wherever the DB actually was, right
    # in the middle of a live test. reload_dirs alone isn't enough here:
    # backend.py lives in app/, which is ALSO the entire Next.js frontend
    # (app/page.tsx, app/components/*.tsx, ...) -- same folder, so scoping
    # by directory still catches every frontend edit. reload_includes narrows
    # it further to .py files only, so only genuine backend code changes
    # (backend.py, db.py, port_utils.py, etc.) reload this process.
    this_dir = os.path.dirname(os.path.abspath(__file__))
    uvicorn.run(
        "backend:app",
        host=sys_config["backend"]["host"],
        port=actual_port,
        reload=sys_config["backend"]["reload"],
        reload_dirs=[this_dir] if sys_config["backend"]["reload"] else None,
        reload_includes=["*.py"] if sys_config["backend"]["reload"] else None,
    )
