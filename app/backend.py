import sys

# BUG FOUND 2026-08-19: PYTHONIOENCODING=utf-8 / PYTHONUTF8=1, set by
# run_dev_system.bat and confirmed present in this exact process's own
# environment (checked directly with psutil), still weren't enough to stop
# stdout encoding as cp1252 under uvicorn's --reload -- print(f"emoji...")
# kept crashing with UnicodeEncodeError, turning a handled ESP32-unreachable
# warning into an unhandled 500 on /siren/activate. Whatever layer of
# process spawning --reload introduces, the env var wasn't reliably making
# it to the stream object print() actually writes through. Reconfiguring
# the streams directly, in code, at the top of this file, doesn't depend on
# that plumbing working at all -- it can't be undone by any shell, batch
# script, or reload cycle between here and every print() call below.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # non-interactive/redirected stream that doesn't support reconfigure -- harmless

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Header, Body, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, field_validator
from typing import List, Optional
from db import get_conn, IntegrityError, DB_KIND, table_exists
import uvicorn
import json
import uuid
import os
import cv2
import numpy as np
import threading
import collections
import requests
import asyncio
import subprocess
from datetime import datetime, timedelta
import time
import hashlib
import hmac
import base64
import secrets
import contextvars
from dotenv import load_dotenv
from db import get_conn, IntegrityError, DB_KIND, table_exists, SQLITE_PATH
from port_utils import find_free_port, write_runtime_port, read_runtime_ports, start_parent_watchdog
from maintenance import start_maintenance_scheduler
from notifications import notify_incident_targets
from telegram_bot import start_telegram_registration_poller
import uvicorn

load_dotenv()

def _kill_optimize_subprocess_on_parent_death():
    # _optimize_proc is a module global defined much further down (the
    # optimize-weights machinery, ~2600 lines below) -- referenced by name
    # here rather than passed in, since Python resolves a bare global name
    # at CALL time, not at function-definition time. By the time the
    # watchdog thread can actually fire (Electron has to disappear AND a
    # poll interval has to elapse), this module has long since finished
    # executing top to bottom and the name exists either way.
    proc = globals().get("_optimize_proc")
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass

# See port_utils.start_parent_watchdog's own docstring for why this exists:
# without it, an Electron process that dies without running its own
# killAll() (a crash, a Task Manager kill, a hung previous run) leaves this
# process running forever, still holding its share of the GPU.
start_parent_watchdog(on_exit=_kill_optimize_subprocess_on_parent_death)
# The standard fix, whenever you want it (not urgent, doesn't block anything above): 
# split into FastAPI APIRouters — routers/auth.py, routers/incidents.py, 
# routers/cameras.py, routers/admin.py, routers/devteam.py — each mounted onto the 
# main app in backend.py. Same behavior, same single running process, just organized into 
# separate files. Want me to do that split now, or leave it as one file for now since it still 
# works fine functionally?
APP_ENV = os.environ.get("APP_ENV", "development")
DATABASE_URL = os.environ.get("DATABASE_URL")  # set -> Postgres; unset -> SQLite fallback (see db.py)
CORS_ORIGINS_ENV = os.environ.get("CORS_ORIGINS")  # comma-separated
SECRET_KEY_ENV = os.environ.get("SECRET_KEY")

# --- CONFIGURATION ENGINE SETUP ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_CONFIG_PATH = os.path.join(BASE_DIR, f"config.{APP_ENV}.json")
_BASE_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CONFIG_PATH = _ENV_CONFIG_PATH if os.path.exists(_ENV_CONFIG_PATH) else _BASE_CONFIG_PATH

WRITABLE_DIR = os.environ.get("ECOVISION_WRITABLE_DIR")
if not WRITABLE_DIR:
    WRITABLE_DIR = os.path.join(os.path.expanduser("~"), "EcoVisionSentinelData")
os.makedirs(WRITABLE_DIR, exist_ok=True)


def _deep_merge(base, override):
    """Overlay `override` onto `base` recursively, returning a new dict."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# BUG FOUND 2026-08-22, chasing why the DevTeam AI-Models panel showed no
# statistics for weapons, robbery or vandalism. Both of these layers used to
# REPLACE sys_config outright rather than overlay it:
#
#   sys_config = json.load(config.<APP_ENV>.json)   # whole-file replacement
#   sys_config = json.load(<writable>/config.json)  # replaced again
#
# APP_ENV defaults to "development" and config.development.json exists, so
# config.json -- the file carrying every model path, threshold and metrics
# block -- was never read. Both env files and the writable copy are older
# skeletons that predate detection.weapon / detection.robbery /
# detection.vandalism entirely, so the panel had nothing to render and every
# consumer silently fell through to its .get(..., default).
#
# maincode/main.py had the identical defect and the identical fix; the two
# loaders must stay in step or the detector and the API disagree about which
# model is deployed.
with open(_BASE_CONFIG_PATH, 'r', encoding='utf-8') as f:
    sys_config = json.load(f)

if os.path.exists(_ENV_CONFIG_PATH):
    with open(_ENV_CONFIG_PATH, 'r', encoding='utf-8') as f:
        sys_config = _deep_merge(sys_config, json.load(f))

WRITABLE_CONFIG_PATH = os.path.join(WRITABLE_DIR, "config.json")
if os.path.exists(WRITABLE_CONFIG_PATH):
    with open(WRITABLE_CONFIG_PATH, 'r', encoding='utf-8') as f:
        sys_config = _deep_merge(sys_config, json.load(f))

if CORS_ORIGINS_ENV:
    sys_config.setdefault("security", {})["cors_origins"] = [o.strip() for o in CORS_ORIGINS_ENV.split(",")]

# --- AUTH: PASSWORD HASHING + SIGNED SESSION TOKENS ---
if SECRET_KEY_ENV:
    sys_config.setdefault("auth", {})["secret_key"] = SECRET_KEY_ENV
if "auth" not in sys_config or not sys_config.get("auth", {}).get("secret_key"):
    sys_config.setdefault("auth", {})["secret_key"] = secrets.token_hex(32)
    # Write to the WRITABLE copy, never back to CONFIG_PATH (BASE_DIR) --
    # that path can be read-only on a packaged install.
    with open(WRITABLE_CONFIG_PATH, "w") as f:
        json.dump(sys_config, f, indent=2)
SECRET_KEY = sys_config["auth"]["secret_key"]
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days

def sha256_of_file(path: str) -> Optional[str]:
    """Chain of custody (docs/incident_response_plan.md §3): hashes a clip or
    screenshot's actual bytes at the moment it's finalized on disk, so the
    file can later be verified as unaltered rather than trusted on faith.
    Returns None (never raises) if the file is missing or unreadable --
    callers store that as a NULL sha256, which just means "hash unavailable
    for this row", not a broken insert."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print(f"⚠️  [chain-of-custody] Could not hash {path}: {e}")
        return None

def hash_password(password: str, salt: str = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(check.hex(), digest_hex)

# NOTE ON THE API CONTRACT: every JSON field this backend sends OR accepts
# is snake_case now, matching the DB column names directly (barangay_id,
# case_id, occurred_date, parent_admin_id, etc.) -- including the signed
# token payload below. This used to be translated to/from camelCase
# (barangayId, caseId...) which caused a silent mismatch once some frontend
# views were updated to read snake_case and others weren't. There is now
# exactly one shape, everywhere. If any .tsx file still sends/reads
# camelCase field names, it needs to be updated to match -- see the list of
# files already aligned in this pass: AdminUsersView, DevteamView,
# HistoryView, RecordsView, Sidebar, CameraManagement.

def issue_token(user_row: dict) -> str:
    payload = {
        "id": user_row["id"],
        "username": user_row["username"],
        "role": user_row["role"],
        "barangay_id": user_row["barangay_id"],
        # PNP users carry station_id instead of barangay_id -- scope_clause()
        # reads this to resolve their jurisdiction, so it must be in the token
        # or every scoped query would need an extra users lookup per request.
        "station_id": user_row.get("station_id"),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"

def verify_token(token: str) -> dict:
    try:
        body, sig = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("bad signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session -- please log in again.")

def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    payload = verify_token(authorization.removeprefix("Bearer "))
    # BUG FOUND 2026-09-04 (caught live: a token for a user id that had been
    # deleted from `users` entirely still worked -- every /api/* call it made
    # kept returning 200). verify_token() only checks the HMAC signature and
    # the 7-day exp claim; it was never cross-checked against the DB, so
    # role/barangay_id/station_id/permissions were whatever they were AT
    # LOGIN TIME for the token's full TOKEN_TTL_SECONDS (7 days) lifetime --
    # deleting an account, demoting a role, or moving someone to a different
    # barangay/station did nothing to any session they already held open.
    # For a system whose whole job is gating who can see/confirm/dismiss
    # incident data, a revoked account keeping full access for up to a week
    # is a real confused-deputy risk, not just a staleness cosmetic issue
    # (same family of bug as WebSocketContext.tsx's cross-tab token bleed
    # fix above it -- trusting a credential's PAST validity instead of its
    # CURRENT one). One cheap by-primary-key lookup per request re-verifies
    # the account still exists and overlays its live role/barangay_id/
    # station_id onto the payload, so a delete or a role/scope change takes
    # effect on the very next request instead of waiting out the token.
    conn = get_conn()
    try:
        cursor = conn.cursor()
        # deleted_at IS NULL added 2026-09-22 alongside the users-table soft-
        # delete feature: a soft-deleted account must revoke exactly like a
        # hard-deleted one did before this existed -- this check is the
        # entire reason that fix works (see this function's 2026-09-04 note
        # above), and a "delete" that only hides the row from listings while
        # leaving every existing session fully valid would defeat it.
        cursor.execute(
            "SELECT role, barangay_id, station_id, custom_permissions FROM users WHERE id = ? AND deleted_at IS NULL",
            (payload.get("id"),),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="Account no longer exists -- please log in again.")
    row = dict(row)
    payload["role"] = row["role"]
    payload["barangay_id"] = row["barangay_id"]
    payload["station_id"] = row["station_id"]
    payload["custom_permissions"] = bool(row["custom_permissions"])
    return payload

def require_role(payload: dict, allowed_roles: set):
    if payload["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail=f"'{payload['role']}' accounts cannot do this")

# BUG FOUND 2026-08-19: DATA_DIR/DB_PATH/LOGS_DIR were dead code -- defined
# here, referenced NOWHERE else in this file. The database connection this
# app actually uses is opened by db.py's own, completely independent path
# resolution (SQLITE_PATH = WRITABLE_DIR/ecovision.db directly, no "data"
# subfolder, no config.json database.path consulted at all). These three
# lines only ever did one real thing: silently create an empty, unused
# WRITABLE_DIR/data/ folder and an empty WRITABLE_DIR/logs/ folder on every
# startup -- which is exactly the "why does data/ exist but stay empty"
# confusion that cost a long stretch of debugging tonight before this was
# found. config.json's database.path is equally dead as a result; left as
# documentation there rather than removed, since it's harmless sitting
# unread. Schema file resolution below is real and still needed.
# Schema file depends on which DB engine db.py picked: Postgres uses
# DATABASE_URL (schema_final.sql), no DATABASE_URL falls back to SQLite
# (schema_sqlite.sql) for the standalone installer build. Both files are
# kept in sync field-for-field -- see schema_sqlite.sql's header comment.
SCHEMA_FILENAME = "schema_final.sql" if DB_KIND == "postgres" else "schema_sqlite.sql"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), SCHEMA_FILENAME)
# NOTE on the old one-liner this replaces:
#   ESP32_IP = sys_config["esp32"]["enabled"] and sys_config["esp32"].get("ip_override") or "192.168.254.152"
# That `and/or` chain looked like it honoured `enabled`, but every branch fell
# through to the same hardcoded default -- so `enabled: false` still produced a
# usable IP and the siren routes still called out to it. Split into two plain
# values so each means exactly one thing.
ESP32_ENABLED = bool(sys_config["esp32"].get("enabled", False))
ESP32_IP = sys_config["esp32"].get("ip_override") or "192.168.254.152"

# ── ESP32 AUTO-DISCOVERY ──────────────────────────────────────────────────
# Restored 2026-08-19. This project HAD self-registration and lost it in a
# refactor: the old POST /panic did `global ESP32_IP; ESP32_IP =
# request.client.host`, so the pole taught the backend its own address. The
# replacement /api/panic_trigger dropped the `request: Request` parameter and
# with it the only thing keeping the IP correct without manual config.
#
# Why it matters: the firmware uses plain DHCP (WiFi.begin with no
# WiFi.config), so its address is a lease, not a fixed property of the device.
# A DHCP reservation on the router pins it in practice -- but that lives
# outside this repo and does not survive a router reset or a swap.
#
# Learned address is persisted so a backend restart doesn't forget it, and
# takes precedence over config.json's ip_override the moment the device has
# actually spoken to us -- a device telling us where it is beats a human
# writing down where it was.
_ESP32_STATE_PATH = os.path.join(WRITABLE_DIR, "esp32_last_seen.json")

def _load_learned_esp32_ip():
    global ESP32_IP
    try:
        with open(_ESP32_STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ip = data.get("ip")
        if ip:
            ESP32_IP = ip
            print(f"📡 [ESP32] Using last-seen address {ip} (learned {data.get('seen_at', '?')})")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️  [ESP32] Could not read {_ESP32_STATE_PATH}: {e}")

def _remember_esp32_ip(ip: str, source: str):
    """Record where the pole just contacted us from. Called by any endpoint
    the ESP32 itself hits, so every kind of contact keeps the address fresh."""
    global ESP32_IP
    if not ip or ip in ("127.0.0.1", "::1"):
        return   # a local test/curl, not the pole -- don't overwrite a real address
    changed = ip != ESP32_IP
    ESP32_IP = ip
    try:
        with open(_ESP32_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"ip": ip, "seen_at": datetime.now().isoformat(timespec="seconds"),
                       "source": source}, fh)
    except Exception as e:
        print(f"⚠️  [ESP32] Could not persist address: {e}")
    if changed:
        print(f"📡 [ESP32] Address learned via {source}: {ip}")

_load_learned_esp32_ip()
RECORDINGS_DIR = os.path.join(WRITABLE_DIR, sys_config["database"].get("recordings_subdir", "recordings"))
SCREENSHOTS_DIR = os.path.join(WRITABLE_DIR, "static", "screenshots")
# Identity verification (#8/#9, 2026-09-23): same on-disk-file + path-in-DB
# convention as screenshots/recordings above (proven to survive a future
# SQLite->Postgres migration since only the filename string lives in the
# DB -- see this dir's own usage below). NOT mounted as a static route the
# way SCREENSHOTS_DIR/RECORDINGS_DIR are (line ~468) -- a government ID is
# sensitive, unlike a camera screenshot, so it's only ever served through
# the authenticated get_verification_document endpoint.
VERIFICATION_DOCS_DIR = os.path.join(WRITABLE_DIR, "verification_docs")
# Files police attach when answering a report request (a scanned blotter
# page, a certification). Served only through the authenticated download
# endpoint, never a static mount -- same reasoning as ID documents.
REQUEST_FILES_DIR = os.path.join(WRITABLE_DIR, "report_request_files")
os.makedirs(REQUEST_FILES_DIR, exist_ok=True)
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(VERIFICATION_DOCS_DIR, exist_ok=True)

# BUG FOUND 2026-09-03 (full-system audit): register_clip and ai_register_clip
# below both did os.path.join(RECORDINGS_DIR, data.filename) and trusted the
# result -- but os.path.join DISCARDS its first argument entirely when the
# second is an absolute path (confirmed: os.path.join(RECORDINGS_DIR,
# r"C:\Users\User\.env") returns exactly "C:\Users\User\.env"), and a
# "../../.." relative filename walks back out at the OS level when the path
# is actually opened, even though the string itself still starts with
# RECORDINGS_DIR. Either one lets a caller register a video_records row whose
# file_path points ANYWHERE on disk. DELETE /api/records/{id} later calls
# os.remove(row["file_path"]) unconditionally on that value -- so a single
# malicious clip registration, combined with nothing more than an admin's
# routine "delete this broken-looking recording" click, deletes an arbitrary
# file the attacker chose, not the recording. ai_register_clip has NO
# authentication at all (same "local AI pipeline caller" reasoning as
# ai_trigger) and backend.py binds 0.0.0.0, so this is reachable by anything
# on the same network, no login required. register_clip requires a session
# but not any specific permission, so any authenticated role (even the
# lowest-privileged operator) could plant the same trap for an admin to
# trigger. Resolving both the target and RECORDINGS_DIR to their real,
# symlink-free absolute form and checking containment closes both the
# absolute-path and the "../" cases the same way.
def _safe_recordings_path(filename: str) -> str:
    real_dir = os.path.realpath(RECORDINGS_DIR)
    candidate = os.path.realpath(os.path.join(real_dir, filename))
    if os.path.commonpath([real_dir, candidate]) != real_dir:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return candidate

MAX_VERIFICATION_DOC_BYTES = 10 * 1024 * 1024

def _save_verification_document(user_id: int, upload: UploadFile, kind: str = "id") -> str:
    """Saves an uploaded ID (kind="id") or face photo (kind="face") under a
    SERVER-GENERATED filename -- unlike _safe_recordings_path above, this
    never even considers the client's own filename for the on-disk path,
    only its extension, so there's no traversal surface to defend here at
    all. Returns just the filename (matches SCREENSHOTS_DIR's own convention
    of storing a basename, not a full path, in the DB -- portable across
    installs/machines)."""
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if kind == "face":
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(status_code=400, detail="Face photo must be a JPG, PNG, or WEBP image")
    elif ext not in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
        raise HTTPException(status_code=400, detail="Accepted formats: JPG, PNG, WEBP, or PDF")
    filename = f"user{user_id}_{kind}_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(VERIFICATION_DOCS_DIR, filename)
    total = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VERIFICATION_DOC_BYTES:
                    raise HTTPException(status_code=400, detail="File too large (10MB max)")
                f.write(chunk)
    except HTTPException:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    return filename

def _ai_core_capture_url() -> str:
    # AI core (maincode/main.py) may have landed on a fallback port if 8001
    # was taken -- read its actual bound port from runtime_ports.json
    # (same file Electron polls) instead of assuming 8001. Read lazily, at
    # call time, not once at import: this route is rarely called and the
    # AI core's port isn't guaranteed to be written yet when backend.py
    # itself starts up.
    port = read_runtime_ports().get("ai_core", 8001)
    return f"http://127.0.0.1:{port}/panic_capture"

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


limiter = Limiter(key_func=get_remote_address)
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
import traceback as _traceback
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception):
    print(f"\n===== UNHANDLED EXCEPTION on {request.method} {request.url.path} =====")
    _traceback.print_exc()
    print("=" * 60 + "\n")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

# --- WEBSOCKET REAL-TIME CONNECTION BROADCAST MANAGER ---
class ConnectionManager:
    """Signed-in dashboards only (2026-10-01): /ws used to accept anyone,
    and every new incident's type, location, camera and id went to every
    socket -- to other jurisdictions, and to anyone on the LAN, who could
    then fetch /static/screenshots/snap_<id>.jpg. A message carrying a
    barangay_id now goes only to accounts whose jurisdiction covers it."""

    def __init__(self):
        self.active_connections: dict = {}  # WebSocket -> the account's auth payload

    async def connect(self, websocket: WebSocket, payload: dict):
        await websocket.accept()
        self.active_connections[websocket] = payload

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)

    @staticmethod
    def _covers(payload: dict, barangay_id: Optional[str]) -> bool:
        if not barangay_id or payload.get("role") == "DEVTEAM":
            return True
        frag, params = scope_clause(payload, "id")
        conn = get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM barangays WHERE LOWER(id) = ? AND {frag}", [barangay_id.lower()] + params)
            return cursor.fetchone() is not None
        finally:
            conn.close()

    async def broadcast(self, message: dict):
        dead_connections = []
        barangay_id = message.get("barangay_id")
        for connection, payload in list(self.active_connections.items()):
            try:
                if self._covers(payload, barangay_id):
                    await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

app.mount("/static/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")
app.mount("/static/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

# --- SCHEMA MIGRATIONS FOR ALREADY-EXISTING DATABASES ---
# init_db() below only applies schema_sqlite.sql/schema_final.sql wholesale
# when the `users` table is missing (a genuinely fresh DB) -- an existing
# install's DB never sees a column or table added to those files after the
# fact. This runs unconditionally, every boot, on both fresh and existing
# databases, and is written to be safe to run repeatedly: CREATE TABLE IF NOT
# EXISTS and INSERT OR IGNORE are naturally idempotent; the ADD COLUMN calls
# are individually guarded because neither SQLite nor this DB_KIND abstraction
# supports "ADD COLUMN IF NOT EXISTS" portably.
#
# Added 2026-08-26 for the chain-of-custody hash columns and the
# notify_targets/notify_log tables (docs/incident_response_plan.md).
def _column_exists(cursor, table: str, column: str) -> bool:
    if DB_KIND == "postgres":
        cursor.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
            (table, column),
        )
    else:
        cursor.execute(f"PRAGMA table_info({table})")
        return any(row["name"] == column for row in cursor.fetchall())
    return cursor.fetchone() is not None


def _ensure_column(conn, cursor, table: str, column: str, coltype: str):
    if _column_exists(cursor, table, column):
        return
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()
        print(f"💾 [DATABASE] Migrated: added {table}.{column}")
    except Exception as e:
        # Not fatal -- the feature that reads this column degrades to
        # "hash unavailable" rather than the whole app failing to boot.
        print(f"⚠️  [DATABASE] Could not add {table}.{column}: {e}")


def _migrate_schema(conn, cursor):
    _ensure_column(conn, cursor, "video_records", "sha256", "TEXT")
    _ensure_column(conn, cursor, "incident_visibility", "screenshot_sha256", "TEXT")
    # BUG FOUND 2026-09-03 (user report: "we should use those [real camera]
    # names instead of made up names" / accept-decline should say which
    # camera detected it): incidents never stored which camera saw them --
    # only location_name as free text. The frontend was inferring a camera
    # id by checking whether location_name contained the word "Entrance",
    # which only ever worked for exactly the two demo cameras. Storing the
    # real id here so that guess can be deleted entirely.
    _ensure_column(conn, cursor, "incidents", "camera_id", "TEXT")

    # Added 2026-09-04 alongside the DevTeam Users list (user request: "add
    # a users list... which are active"): there was no way to answer that at
    # all -- no login was ever recorded anywhere. Stamped on every successful
    # login (see /api/login); NULL means "never logged in since this column
    # existed," not "inactive," for every account that predates it.
    _ensure_column(conn, cursor, "users", "last_login", "TEXT")

    # Added 2026-09-04 (user request: DevTeam wants a password-gated way to
    # override an admin-tier account's normally-automatic permissions).
    # require_permission()'s ADMIN_ROLES branch grants BARANGAY_ADMIN/
    # PNP_ADMIN every view/alert permission unconditionally -- their rows in
    # user_permissions were never even consulted, so there was previously no
    # point exposing an edit UI for them at all. This flag is the on/off
    # switch: 0 (default, every existing and newly-created admin) keeps that
    # original always-on behavior untouched; DevTeam setting it to 1 via the
    # new override endpoint below makes require_permission() defer to this
    # admin's explicit user_permissions rows instead, exactly like a
    # standard operator account.
    _ensure_column(conn, cursor, "users", "custom_permissions", "INTEGER NOT NULL DEFAULT 0")

    if not table_exists(cursor, "notify_targets"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notify_targets (
                id           TEXT PRIMARY KEY,
                barangay_id  TEXT REFERENCES barangays(id) ON DELETE CASCADE,
                station_id   TEXT REFERENCES police_stations(id) ON DELETE CASCADE,
                channel      TEXT NOT NULL CHECK (channel IN ('telegram','sms')),
                destination  TEXT NOT NULL,
                label        TEXT,
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created notify_targets")

    if not table_exists(cursor, "camera_model_config"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_model_config (
                camera_id  TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                model_key  TEXT NOT NULL CHECK (model_key IN ('violence','robbery','vandalism','vandalism_marks','weapon')),
                enabled    INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (camera_id, model_key)
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created camera_model_config")

    if not table_exists(cursor, "camera_threshold_config"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_threshold_config (
                camera_id             TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                model_key             TEXT NOT NULL CHECK (model_key IN ('violence','robbery','vandalism')),
                threshold             REAL NOT NULL CHECK (threshold > 0 AND threshold < 1),
                consecutive_required  INTEGER CHECK (consecutive_required IS NULL OR consecutive_required BETWEEN 1 AND 10),
                calibrated_from       TEXT,
                updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (camera_id, model_key)
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created camera_threshold_config")

    if not table_exists(cursor, "notify_log"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notify_log (
                id           TEXT PRIMARY KEY,
                incident_id  TEXT REFERENCES incidents(id) ON DELETE CASCADE,
                target_id    TEXT REFERENCES notify_targets(id) ON DELETE SET NULL,
                channel      TEXT NOT NULL,
                destination  TEXT NOT NULL,
                status       TEXT NOT NULL CHECK (status IN ('sent','failed','skipped_unconfigured')),
                error        TEXT,
                sent_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created notify_log")

    try:
        cursor.execute(
            "INSERT OR IGNORE INTO permission_keys (key, label) VALUES (?, ?)"
            if DB_KIND != "postgres" else
            "INSERT INTO permission_keys (key, label) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
            ("manage_notify_targets", "Manage Responder Notifications"),
        )
        conn.commit()
    except Exception as e:
        print(f"⚠️  [DATABASE] Could not ensure manage_notify_targets permission key: {e}")

    # Added 2026-09-22 (user request: DevTeam can monitor what each user has
    # done, undo it, and audit actions -- "removed a user, removed this
    # report... recover it"). No audit/activity log of any kind existed
    # anywhere in this codebase before this -- a delete just deleted, with
    # no record of who did it, when, or what was lost. deleted_at turns a
    # delete on these two tables into a soft one (row stays, just hidden
    # from normal listings/login/lookup); audit_log is the who/what/when,
    # with a full row snapshot so a delete-type entry can actually be
    # restored, not just remembered. Scoped to users + incidents first
    # (the two entities explicitly named); extending to more tables later
    # is the same _ensure_column + log_audit() call, not a redesign.
    _ensure_column(conn, cursor, "users", "deleted_at", "TEXT")
    _ensure_column(conn, cursor, "incidents", "deleted_at", "TEXT")
    if not table_exists(cursor, "audit_log"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id               TEXT PRIMARY KEY,
                actor_user_id    INTEGER,
                actor_username   TEXT NOT NULL,
                action           TEXT NOT NULL,
                target_type      TEXT NOT NULL,
                target_id        TEXT NOT NULL,
                target_snapshot  TEXT,
                created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created audit_log")

    # Added 2026-09-23 (Phase 2 of the same DevTeam backlog -- explicit
    # request: permissions "diced" down to the smallest unit, e.g. not
    # "can monitor cameras" but "can monitor only THIS camera"). This
    # coexists with user_permissions rather than replacing it: a row here
    # means "explicitly scoped to one resource", a blanket grant still
    # lives in user_permissions exactly as before. Deliberately additive/
    # opt-in -- see get_cameras()'s own comment -- so a user with zero rows
    # here is completely unaffected by this feature ever having shipped.
    if not table_exists(cursor, "permission_grants"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS permission_grants (
                id               TEXT PRIMARY KEY,
                user_id          INTEGER NOT NULL,
                permission_key   TEXT NOT NULL,
                resource_type    TEXT NOT NULL,
                resource_id      TEXT NOT NULL,
                granted_by       INTEGER,
                granted_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_permission_grants_user ON permission_grants(user_id, permission_key, resource_type)")
        conn.commit()
        print("💾 [DATABASE] Migrated: created permission_grants")

    # Custom roles (#2): a NAMED PRESET layered on top of the real
    # BARANGAY_STAFF/PNP_OFFICER tier, not a new DB-level role value -- the
    # user stays a real operator account for every scope/nav/constraint
    # purpose (chk_user_scope, apply_scope, the ~15 hardcoded role-list
    # sites across the frontend) and only display_title + which permission
    # rows get pre-applied at creation time change. custom_role_permission_
    # defaults is a TEMPLATE, consulted only at account-creation time
    # (devteam_create_user) -- deleting a role later does not retroactively
    # touch any account it already created.
    _ensure_column(conn, cursor, "users", "custom_role_id", "TEXT")
    if not table_exists(cursor, "custom_roles"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_roles (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                org_type     TEXT,
                created_by   INTEGER,
                created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created custom_roles")
    if not table_exists(cursor, "custom_role_permission_defaults"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_role_permission_defaults (
                role_id          TEXT NOT NULL,
                permission_key   TEXT NOT NULL,
                resource_type    TEXT,
                resource_id      TEXT
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created custom_role_permission_defaults")

    # Detection feedback (2026-09-30): every operator verdict on an AI alert
    # is a labelled example from a camera this system actually runs on --
    # the training data public datasets can't supply. One row per incident,
    # updated if the verdict changes. ai_event is what the model said and
    # final_type what the human settled on (differs when an officer
    # re-types an ASSAULT as a ROBBERY in the report).
    if not table_exists(cursor, "detection_feedback"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detection_feedback (
                incident_id   TEXT PRIMARY KEY,
                camera_id     TEXT,
                barangay_id   TEXT,
                ai_event      TEXT NOT NULL,
                final_type    TEXT,
                label         TEXT NOT NULL,
                confidence    REAL,
                ai_context    TEXT,
                screenshot    TEXT,
                decided_by    INTEGER,
                decided_by_username TEXT,
                decided_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                exported_at   TEXT
            )
        """)
        conn.commit()
        print("💾 [DATABASE] Migrated: created detection_feedback")

    # Report requests (#7, 2026-09-23): a formal barangay -> police
    # workflow for requesting a specific report/crime record. Deliberately
    # NOT implemented as "accepting grants the barangay resource-scoped
    # view_history access" (the plan's original sketch) -- POLICE_ONLY_
    # PERMISSIONS' hard ban on view_history for BARANGAY_SIDE_ROLES (#5,
    # this same session) fires in require_permission() before a grant row
    # is ever consulted, specifically so it CANNOT be reopened this way; a
    # permission_grants row here would be silently dead on arrival, exactly
    # the "looks like a promise the app doesn't keep" failure mode this
    # codebase has fixed twice already this session. Instead: police writes
    # response_note back onto the request itself as the deliverable --
    # #5's boundary (no standing archive access) and #7's ask (a formal,
    # trackable request/response with an accept step) both hold, cleanly.
    if not table_exists(cursor, "report_requests"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS report_requests (
                id             TEXT PRIMARY KEY,
                barangay_id    TEXT NOT NULL,
                station_id     TEXT,
                incident_id    TEXT,
                description    TEXT NOT NULL,
                requested_by   INTEGER NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'fulfilled', 'declined')),
                requested_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                responded_by   INTEGER,
                responded_at   TEXT,
                response_note  TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_requests_barangay ON report_requests(barangay_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_requests_station ON report_requests(station_id)")
        conn.commit()
        print("💾 [DATABASE] Migrated: created report_requests")

    # Identity verification (#8/#9, 2026-09-23): every account -- admin-
    # created staff/officers AND self-signup admin applicants -- can attach
    # a government ID; an admin (their own subordinates) or DevTeam (anyone)
    # confirms it. Deliberately does NOT gate login (see this feature's own
    # design note in the review endpoints below) -- these accounts are
    # already either vetted at creation time by the admin who made them, or
    # gated by the existing barangay-approval flow for self-signup. This is
    # an additional trust signal on top of that, not a second login wall.
    _ensure_column(conn, cursor, "users", "verification_status", "TEXT DEFAULT 'unverified'")
    _ensure_column(conn, cursor, "users", "id_document_path", "TEXT")
    _ensure_column(conn, cursor, "users", "verified_by", "INTEGER")
    _ensure_column(conn, cursor, "users", "verified_at", "TEXT")

    # BUG FOUND 2026-09-23 (user report -- see login()'s matching comment for
    # the full story): self-signup PNP_ADMIN accounts had no approval gate
    # at all, and a barangay self-signup against an already-approved
    # barangay_id skipped review too. DEFAULT 'approved' so every existing
    # row (every account that was ever admin-created, devteam-created, or
    # already using the app) is completely unaffected -- only signup()
    # ever writes 'pending' here, explicitly, for a brand new self-signup
    # admin account.
    _ensure_column(conn, cursor, "users", "signup_status", "TEXT DEFAULT 'approved'")

    # 2026-09-29: descriptive records for stations and barangays, entered
    # through the DevTeam "Add station"/"Add barangay" forms. Field set
    # follows the real PNP hierarchy (Regional Office -> City/Provincial
    # Police Office -> station) and PSA's 10-digit PSGC barangay code.
    for col in ("station_type", "parent_office", "regional_office", "commander",
                "address", "contact_number", "description"):
        _ensure_column(conn, cursor, "police_stations", col, "TEXT")
    for col in ("psgc_code", "city_municipality", "province", "region", "captain_name",
                "hall_address", "contact_number", "description"):
        _ensure_column(conn, cursor, "barangays", col, "TEXT")

    # AI report drafts: ai_context is the detector's own metadata for an
    # AI-triggered incident (detector, people in frame, weapons), JSON. An
    # incident_reports row is now a structured, officer-edited report:
    # ai_draft is the machine draft exactly as generated at filing time,
    # report_body the officer's edited version -- both kept so a reviewer
    # can see what the officer changed.
    _ensure_column(conn, cursor, "incident_details", "ai_context", "TEXT")
    _ensure_column(conn, cursor, "incident_reports", "ai_draft", "TEXT")
    _ensure_column(conn, cursor, "incident_reports", "report_body", "TEXT")
    _ensure_column(conn, cursor, "incident_reports", "report_status", "TEXT DEFAULT 'confirmed'")
    _ensure_column(conn, cursor, "incident_reports", "updated_at", "TEXT")

    # Personal record for every account (2026-09-29): who the person is,
    # separate from the login. Collected at self-signup so DevTeam can judge
    # an application, and editable from Manage Users. face_photo_path sits in
    # VERIFICATION_DOCS_DIR next to the ID and is served the same guarded way.
    for col in ("full_name", "birthdate", "home_address", "contact_number", "position", "face_photo_path"):
        _ensure_column(conn, cursor, "users", col, "TEXT")

    # Custom roles are no longer tied to one side (2026-09-29): a role is a
    # permission preset, and whatever a given account's side can't hold is
    # dropped when the role is applied. Older databases created the table
    # with CHECK (org_type IN ('barangay','police')) NOT NULL -- relax it.
    try:
        if DB_KIND == "postgres":
            cursor.execute("ALTER TABLE custom_roles DROP CONSTRAINT IF EXISTS custom_roles_org_type_check")
            cursor.execute("ALTER TABLE custom_roles ALTER COLUMN org_type DROP NOT NULL")
            conn.commit()
        else:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'custom_roles'")
            row = cursor.fetchone()
            if row and "CHECK" in (row["sql"] or ""):
                cursor.execute("ALTER TABLE custom_roles RENAME TO custom_roles_old")
                cursor.execute("""
                    CREATE TABLE custom_roles (
                        id           TEXT PRIMARY KEY,
                        name         TEXT NOT NULL,
                        org_type     TEXT,
                        created_by   INTEGER,
                        created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("INSERT INTO custom_roles (id, name, org_type, created_by, created_at) "
                               "SELECT id, name, org_type, created_by, created_at FROM custom_roles_old")
                cursor.execute("DROP TABLE custom_roles_old")
                conn.commit()
                print("💾 [DATABASE] Migrated: custom_roles.org_type is now optional")
    except Exception as e:
        conn.rollback()
        print(f"⚠️  [DATABASE] Could not relax custom_roles.org_type: {e}")

    # Application decisions (2026-09-30): who decided, when, and why, kept on
    # the application itself so the Rejected list can show it without
    # digging through the audit log. barangays.approved_by/approved_at
    # already record the decider for either outcome.
    _ensure_column(conn, cursor, "barangays", "decision_reason", "TEXT")
    _ensure_column(conn, cursor, "users", "signup_decided_by", "INTEGER")
    _ensure_column(conn, cursor, "users", "signup_decided_at", "TEXT")
    _ensure_column(conn, cursor, "users", "signup_decision_reason", "TEXT")

    # Smartpole locations (2026-10-01): the Incident Map drew three
    # hardcoded Cogon poles whatever was registered, so a new pole never
    # appeared and every AI alert landed on one fixed coordinate. A camera
    # now carries where it physically stands, pinned on the map when it is
    # added; NULL means "not placed yet" and simply isn't drawn.
    _ensure_column(conn, cursor, "cameras", "lat", "REAL")
    _ensure_column(conn, cursor, "cameras", "lng", "REAL")
    _ensure_column(conn, cursor, "cameras", "location_label", "TEXT")

    # Structured report requests (2026-10-01): a request used to be one
    # free-text line, so the station had to write back to ask what was
    # wanted, for what period, and why. The fields live in one JSON column
    # (validated by ReportRequestCreate); description stays the readable
    # summary every older row already has.
    _ensure_column(conn, cursor, "report_requests", "details", "TEXT")

    # Audit filters (2026-10-01): which barangay / station the actor
    # belonged to WHEN they acted, so "everything Station 1 did" stays right
    # after someone is moved. Older rows fall back to the actor's current
    # assignment in the query.
    # What police hand back on a request (2026-10-01): a snapshot of the
    # report fields they chose to share, plus attached files.
    _ensure_column(conn, cursor, "report_requests", "shared_report", "TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS report_request_files (
            id            TEXT PRIMARY KEY,
            request_id    TEXT NOT NULL,
            stored_name   TEXT NOT NULL,
            original_name TEXT NOT NULL,
            content_type  TEXT,
            size_bytes    INTEGER,
            uploaded_by   INTEGER,
            uploaded_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_request_files_request ON report_request_files(request_id)")
    conn.commit()
    _ensure_column(conn, cursor, "audit_log", "actor_barangay_id", "TEXT")
    _ensure_column(conn, cursor, "audit_log", "actor_station_id", "TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_user_id)")
    conn.commit()

    # BUG FOUND 2026-09-30: the one-admin-per-unit indexes counted every
    # admin row ever written, so soft-deleting a captain never freed the
    # seat (every "deleted_at IS NULL" pre-check in the create paths passed,
    # then the INSERT hit this index), and a rejected applicant held their
    # barangay or station forever. A seat is held only by an active,
    # not-rejected admin; reopening a rejected application re-checks it.
    for name, column, role in (("idx_one_barangay_admin_per_barangay", "barangay_id", "BARANGAY_ADMIN"),
                               ("idx_one_pnp_admin_per_station", "station_id", "PNP_ADMIN")):
        try:
            if DB_KIND == "postgres":
                cursor.execute("SELECT indexdef AS d FROM pg_indexes WHERE indexname = ?", (name,))
            else:
                cursor.execute("SELECT sql AS d FROM sqlite_master WHERE type = 'index' AND name = ?", (name,))
            row = cursor.fetchone()
            if row and "signup_status" in (row["d"] or ""):
                continue
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
            cursor.execute(
                f"CREATE UNIQUE INDEX {name} ON users({column}) WHERE role = '{role}' "
                "AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'")
            conn.commit()
            print(f"💾 [DATABASE] Migrated: {name} counts only active, non-rejected admins")
        except Exception as e:
            conn.rollback()
            print(f"⚠️  [DATABASE] Could not rebuild {name}: {e}")


# Set per request by audit_every_change() below: a mutable holder so an
# endpoint's own detailed log_audit() call is visible to the middleware
# after the handler returns, and the catch-all doesn't write a duplicate.
_audit_request_state: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("_audit_request_state", default=None)


def log_audit(cursor, payload: dict, action: str, target_type: str, target_id: str, snapshot: Optional[dict] = None):
    """Writes one audit_log row. Never raises -- an audit-trail failure must
    not be allowed to look like the action itself (a delete, a permission
    change) failed; matches notify_incident_targets()'s same never-block-
    the-real-action philosophy elsewhere in this file. Caller is
    responsible for its own conn.commit() -- this only executes the
    INSERT, so it shares the caller's transaction and rolls back with it
    on a real failure, rather than committing an audit entry for an action
    that itself never actually completed.

    snapshot, when given, is the full row (as a dict) BEFORE the action --
    what a Restore needs to reconstruct it. json.dumps handles ordinary
    values; anything it can't serialize is stringified rather than
    crashing the whole audit write over one awkward field."""
    try:
        cursor.execute(
            "INSERT INTO audit_log (id, actor_user_id, actor_username, action, target_type, target_id, target_snapshot, "
            "actor_barangay_id, actor_station_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                payload.get("id"),
                payload.get("username") or "unknown",
                action,
                target_type,
                str(target_id),
                json.dumps(snapshot, default=str) if snapshot is not None else None,
                (payload.get("barangay_id") or None),
                (payload.get("station_id") or None),
            ),
        )
        state = _audit_request_state.get()
        if state is not None:
            state["logged"] = True
    except Exception as e:
        print(f"⚠️  [AUDIT] Could not write audit_log row for {action} {target_type}={target_id}: {e}")


# --- DATABASE INITIALIZATION ---
def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    if not table_exists(cursor, "users"):
        if not os.path.exists(SCHEMA_PATH):
            conn.close()
            raise RuntimeError(
                f"Database is empty and {SCHEMA_PATH} was not found. "
                f"Copy {SCHEMA_FILENAME} next to backend.py, or run it manually against the database."
            )
        conn.executescript(open(SCHEMA_PATH).read())
        conn.commit()
        print(f"💾 [DATABASE] Applied {SCHEMA_FILENAME} to fresh {DB_KIND} database.")

    _migrate_schema(conn, cursor)

    cursor.execute("SELECT id, password FROM users")
    for row_id, pw in cursor.fetchall():
        if pw and "$" not in pw:
            cursor.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(pw), row_id))
    conn.commit()

    # Static if DEVTEAM_BOOTSTRAP_USERNAME/PASSWORD are set in the
    # environment (.env, not committed, not in .env.example -- each deployer
    # sets their own) -- a fixed team login instead of a fresh random one
    # every time the DB is recreated. Falls back to the old random-per-boot
    # behavior when they're unset, so a checkout with no .env configured
    # still boots into a usable, safe default.
    bootstrap_username = os.environ.get("DEVTEAM_BOOTSTRAP_USERNAME")
    bootstrap_password = os.environ.get("DEVTEAM_BOOTSTRAP_PASSWORD")
    static = bool(bootstrap_username and bootstrap_password)

    if static:
        # BUG FOUND 2026-09-03 (user report: the installer's fixed testing
        # login doesn't work). This used to live entirely inside the
        # `COUNT(*) == 0` branch below, i.e. it only ever ran on a genuinely
        # empty database. electron/main.js's TESTING_PHASE_FIXED_CREDENTIALS
        # shows this exact username/password in a dialog on EVERY launch, not
        # just the first, promising "the same login every time" -- but any
        # machine that had EVER run an earlier build (a different static
        # password, or before this env var existed at all, or the old random
        # bootstrap path) already had a DEVTEAM row, so this whole block was
        # skipped and that machine kept whatever password was baked in back
        # then, forever, with no way to tell it had drifted from what the
        # dialog now claims. Syncing the password on every boot whenever
        # static credentials are configured is what actually makes that
        # promise true, instead of only being true on a machine's very first
        # boot ever.
        cursor.execute("SELECT id FROM users WHERE username = ? AND role = 'DEVTEAM'", (bootstrap_username,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(bootstrap_password), existing[0]))
            conn.commit()
        else:
            cursor.execute(
                "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id) "
                "VALUES (?, ?, 'DEVTEAM', NULL, 'DevTeam HQ', NULL)",
                (bootstrap_username, hash_password(bootstrap_password)),
            )
            conn.commit()
            print("=" * 60)
            print("🔑 [BOOTSTRAP] First-run DEVTEAM account created (static, from .env):")
            print(f"    username: {bootstrap_username}")
            print("=" * 60)
    else:
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'DEVTEAM'")
        if cursor.fetchone()[0] == 0:
            bootstrap_username = "devteam"
            bootstrap_password = secrets.token_urlsafe(12)
            cursor.execute(
                "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id) "
                "VALUES (?, ?, 'DEVTEAM', NULL, 'DevTeam HQ', NULL)",
                (bootstrap_username, hash_password(bootstrap_password)),
            )
            conn.commit()
            print("=" * 60)
            print("🔑 [BOOTSTRAP] First-run DEVTEAM account created (random):")
            print(f"    username: {bootstrap_username}")
            print(f"    password: {bootstrap_password}")
            print("    Save this now -- it will not be shown again.")
            print("=" * 60)
            # Also write to a file next to the writable data dir, since a
            # packaged installer build has no visible console for the person
            # to read this from (see Phase 3: first-run credential surfacing).
            # Only done for the random case -- a static password already
            # lives in the deployer's own .env, so a second plaintext copy on
            # disk would just be one more place it can leak from.
            try:
                cred_path = os.path.join(WRITABLE_DIR, "devteam_credentials.txt")
                with open(cred_path, "w") as f:
                    f.write("EcoVision Sentinel — first-run DEVTEAM account\n")
                    f.write("Generated once; this file is not regenerated after first boot.\n\n")
                    f.write("username: devteam\n")
                    f.write(f"password: {bootstrap_password}\n")
                print(f"🔑 [BOOTSTRAP] Also written to: {cred_path}")
            except Exception as e:
                print(f"⚠️  [BOOTSTRAP] Could not write credentials file: {e}")

    cursor.execute("SELECT COUNT(*) FROM barangays")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO barangays (id, name, status, approved_at) VALUES ('cogon', 'Cogon', 'approved', NOW())"
        )
        seed_cam_url = os.environ.get("SEED_CAMERA_1_URL", "rtsp://user:pass@192.168.254.106:554/stream1")
        cursor.execute(
            "INSERT INTO cameras (id, name, url, status, barangay_id) VALUES (?, ?, ?, 'online', 'cogon')",
            ("1", "Main Entrance Hub", seed_cam_url),
        )
        cursor.execute(
            "INSERT INTO cameras (id, name, url, status, barangay_id) VALUES "
            "('2', 'Sector B Gate', 'rtsp://192.168.1.15/stream', 'online', 'cogon')"
        )
        conn.commit()

    conn.close()

init_db()

# Recovery plan §4 / privacy plan §5: daily DB backup + evidence retention
# sweep. Previously neither existed at all -- see docs/recovery_plan.md and
# docs/privacy_compliance_plan.md for the reasoning behind the schedule and
# the retention windows. Placed right after init_db() so a fresh DB (or the
# bootstrap DEVTEAM account it creates) exists before the first sweep runs.
start_maintenance_scheduler(
    sqlite_path=SQLITE_PATH if DB_KIND == "sqlite" else None,
    writable_dir=WRITABLE_DIR,
    get_conn=get_conn,
    table_exists=table_exists,
    recordings_dir=RECORDINGS_DIR,
    screenshots_dir=SCREENSHOTS_DIR,
    db_kind=DB_KIND,
)

# Telegram registration poller (app/telegram_bot.py) -- lets an officer get
# their own chat_id by messaging the bot, since this backend has no public
# endpoint for Telegram to webhook into. No-ops if TELEGRAM_BOT_TOKEN isn't
# set in .env.
start_telegram_registration_poller()

# --- NVIDIA SHADOWPLAY & 24/7 BACKGROUND RECORDING SYSTEMS ---
class VideoRecordingEngine:
    def __init__(self, buffer_seconds=15, fps=20):
        self.buffer_size = buffer_seconds * fps
        self.frame_buffer = collections.deque(maxlen=self.buffer_size)
        self.latest_frame = None
        self.lock = threading.Lock()
        self.fps = fps
        self.running = True

    def start_workers(self):
        threading.Thread(target=self._continuous_capture_worker, daemon=True).start()
        threading.Thread(target=self._continuous_247_writer_worker, daemon=True).start()

    def _continuous_capture_worker(self):
        while self.running:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank, f"LIVE FEED RAW - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (16, 185, 129), 2)
            with self.lock:
                self.latest_frame = blank.copy()
                self.frame_buffer.append(blank)
            time.sleep(1.0 / self.fps)

    def _continuous_247_writer_worker(self):
        while self.running:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"rec_247_{timestamp}.mp4"
            filepath = os.path.join(RECORDINGS_DIR, filename)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(filepath, fourcc, self.fps, (640, 480))
            segment_end_time = time.time() + 120
            while time.time() < segment_end_time and self.running:
                with self.lock:
                    frame = self.latest_frame
                if frame is not None:
                    writer.write(frame)
                time.sleep(1.0 / self.fps)
            writer.release()

    def save_shadow_clip(self, incident_id: str, post_trigger_duration=10):
        with self.lock:
            pre_trigger_frames = list(self.frame_buffer)
            current_frame = self.latest_frame

        screenshot_filename = f"snap_{incident_id}.jpg"
        screenshot_path = os.path.join(SCREENSHOTS_DIR, screenshot_filename)
        if current_frame is not None:
            cv2.imwrite(screenshot_path, current_frame)

        def _async_writer():
            clip_filename = f"clip_crime_{incident_id}.mp4"
            clip_filepath = os.path.join(RECORDINGS_DIR, clip_filename)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(clip_filepath, fourcc, self.fps, (640, 480))

            for frame in pre_trigger_frames:
                writer.write(frame)

            post_frames_count = post_trigger_duration * self.fps
            for _ in range(post_frames_count):
                with self.lock:
                    frame = self.latest_frame
                if frame is not None:
                    writer.write(frame)
                time.sleep(1.0 / self.fps)
            writer.release()

            conn = get_conn()
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """INSERT INTO video_records
                   (id, filename, file_path, recorded_at, duration, type, associated_incident_id, crime_time_marker, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), clip_filename, clip_filepath, now_str,
                 f"{post_trigger_duration + 15}s", "CRIME_CLIP", incident_id, "00:15",
                 "Auto-generated clip via ShadowPlay engine."),
            )
            conn.commit()
            conn.close()

        threading.Thread(target=_async_writer, daemon=True).start()
        return f"/static/screenshots/{screenshot_filename}"

recorder_engine = VideoRecordingEngine()
# BUG FOUND 2026-09-02: start_workers() used to run unconditionally at import
# time. Its two threads are: (1) _continuous_capture_worker, which doesn't
# read any real camera -- it draws a solid black frame with a "LIVE FEED RAW"
# timestamp burned in via cv2.putText and calls that the buffer; and
# (2) _continuous_247_writer_worker, which writes that same fake feed to a new
# rec_247_<timestamp>.mp4 every 2 minutes, forever, and never inserts a
# video_records row for any of them. Measured live: 1,174 files / 1.82 GB of
# useless black-frame video accumulated since 2026-08-19, none of it visible
# anywhere in the app (RecordsView's "24/7" tab filters on type='FULL_24_7',
# which nothing here ever writes) and none of it subject to the retention
# sweep (app/maintenance.py works off video_records rows, and these have
# none). This is disk usage with no corresponding feature -- an operator
# gets nothing for the space it spends. save_shadow_clip() below is also
# dead code (nothing in this file calls it) and would have suffered the same
# fake-frame problem had anything ever wired it up; real incident evidence
# clips come from the AI core's own capture path (main.py -> ai_register_clip)
# and were never affected by this. Leaving the class defined (some future
# real 24/7-recording feature may want this shape, wired to an actual frame
# source) but no longer auto-starting workers that write files nobody reads.
# recorder_engine.start_workers()

# --- DATA SCHEMAS ---
# All request bodies now use the same snake_case field names as the
# responses -- e.g. barangay_id, case_id, occurred_time -- so the frontend
# doesn't have to remember two different cases depending on whether it's
# reading or writing. If page.tsx / CrimeReportsView.tsx still POST
# camelCase bodies, they need to be updated to match these field names.
# Roles are organization x tier. See docs/USER_HIERARCHY_PLAN.md.
#
#              ADMIN            OPERATOR
#   Barangay   BARANGAY_ADMIN   BARANGAY_STAFF
#   PNP        PNP_ADMIN        PNP_OFFICER
#   plus DEVTEAM (unscoped)
ADMIN_ROLES = {"PNP_ADMIN", "BARANGAY_ADMIN"}
STANDARD_ROLES = {"PNP_OFFICER", "BARANGAY_STAFF"}
ADMIN_CREATES_ROLE = {"PNP_ADMIN": "PNP_OFFICER", "BARANGAY_ADMIN": "BARANGAY_STAFF"}
ALL_ROLES = ADMIN_ROLES | STANDARD_ROLES | {"DEVTEAM"}
ADMIN_OR_DEVTEAM = ADMIN_ROLES | {"DEVTEAM"}
POLICE_SIDE_ROLES = {"PNP_OFFICER", "PNP_ADMIN", "DEVTEAM"}
# Viewing + optimizing AI models is barangay-only (not PNP_ADMIN, despite
# both being in ADMIN_ROLES): the models run on hardware the barangay owns
# and installed, same reasoning as manage_cameras being barangay-only.
# STALE as of the 2026-08-23 fix in set_detection_model, corrected here
# 2026-09-02: this comment used to say toggling a model on/off was
# deliberately DEVTEAM-only, reserving MODEL_VIEW_ROLES for read-only
# viewing + optimizing. That was reversed on 2026-08-23 -- see that
# function's own comment -- because DEVTEAM-only toggling left the barangay
# that owns the hardware unable to turn a detector on or off for their own
# camera. Toggling enabled/disabled now uses this SAME MODEL_VIEW_ROLES set;
# only the numeric threshold stays DEVTEAM-only (checked separately in
# set_detection_model). Left uncorrected, this paragraph actively misled a
# later full-system permission sweep into flagging BARANGAY_ADMIN's (correct)
# ability to toggle a model as a bug.
MODEL_VIEW_ROLES = {"DEVTEAM", "BARANGAY_ADMIN"}
BARANGAY_SIDE_ROLES = {"BARANGAY_ADMIN", "BARANGAY_STAFF"}
PNP_SIDE_ROLES = {"PNP_ADMIN", "PNP_OFFICER"}
VALID_PERMISSION_KEYS = {"view_map", "view_records", "view_history", "manage_cameras", "confirm_dismiss_alerts", "manage_notify_targets"}

# Cameras are barangay property -- the barangay funded and installed the
# smartpoles; PNP consumes the feed. Previously require_permission() waved
# through every admin role, so a precinct captain could delete a barangay's
# cameras. Keys listed here are NOT covered by that admin bypass.
BARANGAY_ONLY_PERMISSIONS = {"manage_cameras"}

# Added 2026-09-22 (explicit user request): the deep crime-history/video
# archive is police-only now -- barangay accounts (admin tier included)
# never get it, full stop, not "grantable but usually off." Mirrors
# BARANGAY_ONLY_PERMISSIONS' shape in the opposite direction: require_
# permission() hard-bans these for BARANGAY_SIDE_ROLES the same way it
# already hard-bans manage_cameras for PNP_SIDE_ROLES, and separately
# carves them out of BARANGAY_ADMIN's automatic bypass -- so this can't be
# silently reopened by a stale user_permissions row or a future admin-
# bypass change. Barangay keeps its own live Incident Queue
# (confirm_dismiss_alerts, on the main dashboard) -- that's a different
# permission and unaffected; this is specifically the archived/confirmed
# incident log and the video record vault.
POLICE_ONLY_PERMISSIONS = {"view_history", "view_records"}


def scope_clause(payload: dict, column: str = "barangay_id"):
    """Returns (sql_fragment, params) restricting a query to what this user
    may see. Empty fragment means unrestricted.

    ONE implementation, called by every scoped endpoint. Previously each
    endpoint re-derived its own scoping, which is exactly how a POLICE user
    ended up seeing incidents from every barangay but cameras from only one.

        barangay role -> their own barangay
        PNP role      -> every barangay in their station's jurisdiction
        DEVTEAM       -> everything

    The PNP branch is a subquery rather than a JOIN so callers can drop the
    fragment into an existing WHERE without restructuring their statement.
    """
    role = payload.get("role")

    if role == "DEVTEAM":
        return "", []

    if role in BARANGAY_SIDE_ROLES:
        brgy = payload.get("barangay_id")
        if not brgy:
            # chk_user_scope makes this unreachable via the DB, but a token
            # issued before the migration could still carry it. Deny rather
            # than silently widening to everything.
            return "1 = 0", []
        return f"LOWER({column}) = ?", [brgy.lower()]

    if role in PNP_SIDE_ROLES:
        station = payload.get("station_id")
        if not station:
            return "1 = 0", []
        return (
            f"{column} IN (SELECT barangay_id FROM station_barangays WHERE station_id = ?)",
            [station],
        )

    return "1 = 0", []


def apply_scope(payload: dict, base_sql: str, params: list, column: str = "barangay_id",
                extra_where: str = "", extra_params: Optional[list] = None):
    """Composes base_sql + scope + an optional extra predicate into a single
    WHERE. base_sql must NOT already contain a WHERE clause."""
    clauses, all_params = [], list(params)
    frag, sp = scope_clause(payload, column)
    if frag:
        clauses.append(frag)
        all_params.extend(sp)
    if extra_where:
        clauses.append(extra_where)
        all_params.extend(extra_params or [])
    if clauses:
        base_sql += " WHERE " + " AND ".join(clauses)
    return base_sql, all_params


def _incident_owned_by(cursor, incident_id: str, payload: dict) -> bool:
    """BUG FOUND 2026-09-03: every incident-mutating endpoint below --
    status update, delete, archive, confirm-and-report, and both report
    endpoints -- checked a ROLE or a PERMISSION KEY but never whether the
    caller actually had jurisdiction over THIS incident. GET /api/incidents
    was correctly scoped via apply_scope() the whole time; every write path
    on a single incident was not. Concretely: any BARANGAY_ADMIN anywhere
    could confirm/dismiss, archive, or permanently DELETE another barangay's
    incident (delete_incident's DELETE cascades to incident_reports too --
    it would take a filed police report down with it), and any PNP account
    could confirm-and-report on an incident outside their station's
    jurisdiction. Same missing-ownership-check shape as the camera CRUD bug
    fixed earlier today, generalized here via the same scope_clause()
    apply_scope() already uses for the read path, so a barangay role checks
    against its own barangay_id and a PNP role against its whole station's
    jurisdiction, identically to what GET /api/incidents already shows them.
    DEVTEAM: unrestricted, as everywhere else."""
    if payload.get("role") == "DEVTEAM":
        return True
    frag, params = scope_clause(payload)
    if not frag:
        return True
    cursor.execute(f"SELECT 1 FROM incidents WHERE id = ? AND {frag}", [incident_id] + params)
    return cursor.fetchone() is not None


class UserSignup(BaseModel):
    username: str
    password: str
    role: str
    # BARANGAY_ADMIN supplies barangay_id (created pending, DevTeam approves).
    # PNP_ADMIN supplies station_id and must pick a station that already
    # exists -- see the note in signup() for why.
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    assignment: str
    # Personal record (2026-09-29) -- what DevTeam reviews the application
    # against. See PROFILE_FIELDS.
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class AdminCreateUser(BaseModel):
    username: str
    password: str
    assignment: str
    display_title: Optional[str] = None
    is_sub_admin: Optional[bool] = False
    permissions: Optional[dict] = None
    # Personal record, same fields as DevTeam's Create User (full_name
    # required there and here -- staff made by an admin used to have none).
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None

class PermissionsUpdate(BaseModel):
    permissions: dict

class AdminPermissionOverride(BaseModel):
    # Re-authentication, not a new/separate secret: verified against the
    # CALLING DevTeam account's own real password (whatever that install's
    # DevTeam actually logs in with), the same way any step-up confirmation
    # works elsewhere. Deliberately not a fixed/hardcoded bypass code -- that
    # would be one shared, unchangeable-without-a-code-release secret baked
    # into every install of this app rather than each deployment's own real
    # credential, and would sit there in plaintext in whatever ships this
    # source (repo history, this Electron app's own bundle).
    confirm_password: str
    # None (omitted) means "reset to automatic" -- clears custom_permissions
    # back to 0 and this admin's explicit rows, returning them to the
    # original unconditional-pass behavior. A dict means "go custom": set
    # custom_permissions = 1 and replace their rows with exactly this set.
    permissions: Optional[dict] = None

class IncidentSchema(BaseModel):
    id: str
    case_id: str
    type: str
    officer: str
    lat: float
    lng: float
    location_name: str
    severity: str
    occurred_date: str
    occurred_time: str
    narrative: str
    nature_of_call: str
    arrival_reason: str
    additional_officers: str
    status: str
    confidence: Optional[float] = 1.0
    barangay_id: str
    # The smartpole the report was filed at, when filed from a pole.
    camera_id: Optional[str] = None

class CameraLocationSchema(BaseModel):
    lat: float
    lng: float
    location_label: Optional[str] = None

    @field_validator("lat")
    @classmethod
    def _lat_range(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("lng")
    @classmethod
    def _lng_range(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

class CameraSchema(BaseModel):
    name: str
    url: str
    barangay_id: str
    # Where the pole stands, pinned on the Incident Map. Optional: the
    # Cameras tab registers a stream without a location, placed later.
    location: Optional[CameraLocationSchema] = None

class NotifyTargetSchema(BaseModel):
    # Exactly one of these two should be set -- mirrors CameraSchema's own
    # single-owner assumption, just with two possible owner kinds instead of
    # one. See docs/incident_response_plan.md §2.
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    channel: str          # "telegram" | "sms"
    destination: str      # Telegram chat_id, or a phone number
    label: Optional[str] = None

class StatusUpdateSchema(BaseModel):
    status: str

class AiTriggerSchema(BaseModel):
    id: str
    event: str
    confidence: float
    barangay_id: Optional[str] = "cogon"
    screenshot_path: Optional[str] = None
    # Was hardcoded to "Cogon Core Smartpole Node" below regardless of which
    # camera actually saw the event -- every incident said the same location
    # even on a single-camera deployment where that name may not match the
    # real camera at all. main.py now sends the configured camera name;
    # default here keeps old callers (or a payload that omits it) working.
    location_name: Optional[str] = "Cogon Core Smartpole Node"
    # Added 2026-09-03 alongside camera_id on the incidents table -- see that
    # column's comment. Optional so a caller that predates this (or a manual
    # /api/ai_trigger test) doesn't break; the incident just has no camera
    # link, same as before this existed.
    camera_id: Optional[str] = None
    # Detector metadata for the AI report draft: detector, attribution,
    # people_in_frame, track_id, weapons [{name, conf}]. Free-form on purpose
    # so the AI core can add keys without a backend release.
    context: Optional[dict] = None

class PanicSchema(BaseModel):
    event: str
    device: str
    barangay_id: Optional[str] = "cogon"

class ConfirmAndReportSchema(BaseModel):
    status: str
    capture_snapshot: Optional[bool] = False
    report_details: Optional[dict] = None

class IncidentReportSchema(BaseModel):
    narrative: Optional[str] = None
    nature_of_call: Optional[str] = None
    arrival_reason: Optional[str] = None
    additional_officers: Optional[str] = None

class ManualClipSchema(BaseModel):
    filename: str
    duration: str
    type: str
    crime_time_marker: str
    notes: str
    associated_incident_id: Optional[str] = None

class LocationDecisionSchema(BaseModel):
    reason: Optional[str] = None
    station_id: Optional[str] = None  # approve only: put the barangay under this station

class RecordNotesSchema(BaseModel):
    notes: str

class DevteamUserEdit(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    assignment: Optional[str] = None
    display_title: Optional[str] = None
    barangay_id: Optional[str] = None
    # BUG FOUND 2026-08-23: this model had barangay_id but never station_id,
    # so a PNP account's jurisdiction -- its "location" -- could be set at
    # creation (DevteamCreateUser takes both) but never changed afterward.
    # The edit UI had nowhere to send it even if this were here; both are
    # fixed together, see DevteamView.tsx's edit-user modal.
    station_id: Optional[str] = None
    role: Optional[str] = None
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None
    # Sent explicitly as null to clear (see model_fields_set in
    # devteam_edit_user); left out entirely to leave unchanged.
    parent_admin_id: Optional[int] = None
    custom_role_id: Optional[str] = None

class DevteamCreateUser(BaseModel):
    username: str
    password: str
    role: str
    # Exactly one of these is required, decided by the role's organization:
    # barangay roles need barangay_id, PNP roles need station_id. For a
    # barangay role, station_id does double duty (2026-09-24) -- not the
    # new account's own station (chk_user_scope forbids that), but which
    # station to assign this barangay's jurisdiction to, and ONLY when the
    # barangay has no covering station yet. Required in that case, ignored
    # otherwise (an already-covered barangay auto-resolves from the
    # existing station_barangays link, nothing to pick).
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    assignment: str
    display_title: Optional[str] = None
    parent_admin_id: Optional[int] = None
    permissions: Optional[dict] = None
    custom_role_id: Optional[str] = None
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None
    # Dicing applied right after creation: {permission_key: {resource_type:
    # [ids] | None}} -- see RESOURCE_DIMENSIONS. Omitted = not narrowed.
    resource_scopes: Optional[dict] = None
    # Admin roles only: start the account on explicit permissions instead of
    # the automatic admin set, exactly as override_permissions would do
    # right after creation. Needs the DevTeam password, same bar.
    override_permissions: bool = False
    confirm_password: Optional[str] = None

class ResourceScopesUpdate(BaseModel):
    # {permission_key: {resource_type: [ids] | None}}. None clears that
    # dimension (everything the account's org allows); a list = only those.
    scopes: dict

class ResourceGrantRequest(BaseModel):
    permission_key: str
    resource_type: str
    resource_id: str

class CustomRoleCreate(BaseModel):
    name: str
    # Optional since 2026-09-29: roles apply to either side, and whatever
    # an account's side can't hold is dropped at assignment time.
    org_type: Optional[str] = None
    permissions: Optional[dict] = None
    # Same shape as ResourceScopesUpdate.scopes, minus cameras (a role has
    # no jurisdiction of its own to pick cameras from).
    scopes: Optional[dict] = None


# Personal record fields shared by signup, DevTeam create and edit.
PROFILE_FIELDS = ("full_name", "birthdate", "home_address", "contact_number", "position")


def _clean_profile(model, required: tuple = ()) -> dict:
    """Trimmed profile values from any model carrying PROFILE_FIELDS.
    Absent fields are left out (so an edit only touches what was sent);
    birthdate must be a real YYYY-MM-DD date for someone 18 or older."""
    out = {}
    for f in PROFILE_FIELDS:
        v = getattr(model, f, None)
        if v is None:
            continue
        v = v.strip()
        out[f] = v or None
    for f in required:
        if not out.get(f):
            raise HTTPException(status_code=400, detail=f"{f.replace('_', ' ').capitalize()} is required")
    if out.get("birthdate"):
        try:
            born = datetime.strptime(out["birthdate"], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Birthdate must be a date (YYYY-MM-DD)")
        today = datetime.now()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < 18 or age > 110:
            raise HTTPException(status_code=400, detail="Birthdate must be for someone 18 or older")
    return out

REPORT_REQUEST_TYPES = {
    "blotter_copy": "Certified copy of a police blotter entry",
    "incident_report": "Incident / spot report",
    "case_status": "Status of an investigation or case",
    "crime_statistics": "Crime statistics for the barangay",
    "incident_certification": "Certification that an incident was reported",
    "other": "Other report",
}
REPORT_REQUEST_PURPOSES = {
    "katarungang_pambarangay": "Katarungang Pambarangay (mediation / conciliation)",
    "bpoc": "Barangay Peace and Order Council meeting or plan",
    "resident_request": "Requested by a resident",
    "legal_or_insurance": "Legal, court or insurance requirement",
    "records": "Barangay records",
    "other": "Other",
}
REPORT_REQUEST_URGENCY = {"routine", "urgent"}
# Crime types a request can be about -- the incident types the system files.
REPORT_REQUEST_CRIMES = {"ANY", "ASSAULT", "ARMED THREAT", "ROBBERY", "THEFT", "PHYSICAL VIOLENCE",
                         "VANDALISM", "HARDWARE_PANIC_INTERRUPT", "OTHER"}


class ReportRequestCreate(BaseModel):
    incident_id: Optional[str] = None
    description: str
    # Structured fields (2026-10-01). Optional so older callers that send
    # only a description keep working; the app's form always sends them.
    report_type: Optional[str] = None
    crime_type: Optional[str] = None
    period_from: Optional[str] = None
    period_to: Optional[str] = None
    location: Optional[str] = None
    persons_involved: Optional[str] = None
    reference: Optional[str] = None
    purpose: Optional[str] = None
    purpose_detail: Optional[str] = None
    urgency: Optional[str] = "routine"
    needed_by: Optional[str] = None

class ReportRequestResponse(BaseModel):
    note: Optional[str] = None
    # Fulfil with a report (2026-10-01): which incident's report, which of
    # its fields the barangay gets, and the summary as police chose to word
    # it for them. The values themselves are read from the record
    # server-side, so only the summary can differ from what was filed.
    incident_id: Optional[str] = None
    share_fields: Optional[list] = None
    summary: Optional[str] = None

class VerificationReview(BaseModel):
    decision: str  # 'verified' | 'rejected'
    note: Optional[str] = None


# --- SERIALIZATION HELPERS ---
# Every one of these returns snake_case keys matching the DB columns 1:1.
# This is now the ONLY place a schema change needs to be reflected.

def _row_to_incident_dict(inc_row, details_row, vis_row) -> dict:
    d = dict(inc_row)
    details = dict(details_row) if details_row else {}
    vis = dict(vis_row) if vis_row else {}
    return {
        "id": d["id"], "case_id": d["case_id"], "type": d["type"], "officer": d.get("officer"),
        "lat": d.get("lat"), "lng": d.get("lng"), "location_name": d.get("location_name"),
        "severity": d["severity"], "occurred_date": d["occurred_date"], "occurred_time": d["occurred_time"],
        "narrative": details.get("narrative"), "nature_of_call": details.get("nature_of_call"),
        "arrival_reason": details.get("arrival_reason"), "additional_officers": details.get("additional_officers"),
        "status": d["status"], "confidence": d.get("confidence"), "barangay_id": d.get("barangay_id"),
        "screenshot_path": vis.get("screenshot_path"), "map_hidden": vis.get("map_hidden", 0),
        "camera_id": d.get("camera_id"),
    }

def _row_to_camera_dict(row, include_url: bool = True) -> dict:
    """include_url=False for anyone who can't manage cameras: a stream URL
    usually carries the camera's own username and password."""
    d = dict(row)
    return {"id": d["id"], "name": d["name"], "url": d["url"] if include_url else None,
            "status": d["status"], "barangay_id": d.get("barangay_id"),
            "lat": d.get("lat"), "lng": d.get("lng"), "location_label": d.get("location_label")}

def _row_to_record_dict(row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "filename": d["filename"], "file_path": d["file_path"],
        "recorded_at": d["recorded_at"], "duration": d["duration"], "type": d["type"],
        "associated_incident_id": d.get("associated_incident_id"),
        "crime_time_marker": d.get("crime_time_marker"), "notes": d.get("notes"),
    }

def _user_permissions_json(cursor, user_id: int) -> str:
    cursor.execute("SELECT permission_key FROM user_permissions WHERE user_id = ?", (user_id,))
    granted = {row[0]: True for row in cursor.fetchall()}
    return json.dumps(granted)

def _user_permissions_json_batch(cursor, user_ids: list) -> dict:
    """Same as _user_permissions_json but for many users in ONE query --
    use this whenever building more than one user dict at a time (was a
    per-user query in a loop, i.e. N+1, in list_my_users/devteam_overview)."""
    if not user_ids:
        return {}
    placeholders = ",".join("?" for _ in user_ids)
    cursor.execute(
        f"SELECT user_id, permission_key FROM user_permissions WHERE user_id IN ({placeholders})",
        tuple(user_ids),
    )
    grouped: dict = {uid: {} for uid in user_ids}
    for row in cursor.fetchall():
        grouped.setdefault(row["user_id"], {})[row["permission_key"]] = True
    return {uid: json.dumps(perms) for uid, perms in grouped.items()}

def _row_to_user_dict_base(row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "username": d["username"], "role": d["role"],
        "barangay_id": d.get("barangay_id"), "station_id": d.get("station_id"),
        "assignment": d.get("assignment"),
        "parent_admin_id": d.get("parent_admin_id"), "display_title": d.get("display_title"),
        "is_sub_admin": bool(d.get("is_sub_admin")),
        "last_login": d.get("last_login"),
        "custom_permissions": bool(d.get("custom_permissions")),
        "custom_role_id": d.get("custom_role_id"),
        "verification_status": d.get("verification_status") or "unverified",
        "signup_status": d.get("signup_status") or "approved",
        # Personal record (never file paths). Admins' own team lists used
        # to get usernames only, so Personnel couldn't show who anyone was.
        **{f: d.get(f) for f in PROFILE_FIELDS},
    }

def _location_name(cursor, barangay_id, station_id) -> Optional[str]:
    """Resolves a user's barangay/station id to its human-readable name.
    BUG FOUND 2026-09-04 (caught live: a fresh PNP admin's sidebar footer
    read "STATION-0724F2A3" instead of the station's actual name). Every
    user dict the backend ever returned carried barangay_id/station_id --
    the raw slug/generated-uuid primary key -- and nothing else; the
    frontend had no name to show even if it wanted to. A barangay id
    happens to often BE its own display-ish name (DevTeam types the id by
    hand, e.g. "cogon" for "Cogon"), which made this invisible there, but a
    station's id is always a generated "station-<uuid8>" (see the Stations
    tab's own create handler) -- never something a human should see."""
    if station_id:
        cursor.execute("SELECT name FROM police_stations WHERE id = ?", (station_id,))
        row = cursor.fetchone()
        return row["name"] if row else None
    if barangay_id:
        cursor.execute("SELECT name FROM barangays WHERE id = ?", (barangay_id,))
        row = cursor.fetchone()
        return row["name"] if row else None
    return None

def _row_to_user_dict(cursor, row) -> dict:
    u = _row_to_user_dict_base(row)
    u["permissions"] = _user_permissions_json(cursor, u["id"])
    u["location_name"] = _location_name(cursor, u["barangay_id"], u["station_id"])
    return u

def _rows_to_user_dicts_batch(cursor, rows) -> list:
    """Batched equivalent of [_row_to_user_dict(cursor, r) for r in rows] --
    one permissions query total instead of one per row, same idea extended
    to location_name (one query per distinct barangay/station instead of
    one per user)."""
    users = [_row_to_user_dict_base(r) for r in rows]
    perms_by_id = _user_permissions_json_batch(cursor, [u["id"] for u in users])
    barangay_ids = {u["barangay_id"] for u in users if u["barangay_id"]}
    station_ids = {u["station_id"] for u in users if u["station_id"]}
    names_by_barangay = {}
    if barangay_ids:
        placeholders = ",".join("?" for _ in barangay_ids)
        cursor.execute(f"SELECT id, name FROM barangays WHERE id IN ({placeholders})", tuple(barangay_ids))
        names_by_barangay = {r["id"]: r["name"] for r in cursor.fetchall()}
    names_by_station = {}
    if station_ids:
        placeholders = ",".join("?" for _ in station_ids)
        cursor.execute(f"SELECT id, name FROM police_stations WHERE id IN ({placeholders})", tuple(station_ids))
        names_by_station = {r["id"]: r["name"] for r in cursor.fetchall()}
    for u in users:
        u["permissions"] = perms_by_id.get(u["id"], "{}")
        u["location_name"] = (
            names_by_station.get(u["station_id"]) if u["station_id"]
            else names_by_barangay.get(u["barangay_id"])
        )
    return users


# --- PERSISTENT CAMERA ROUTINES ---
@app.get("/api/cameras")
async def get_cameras(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()

    # Barangay roles see their own barangay; PNP roles see every barangay in
    # their station's jurisdiction; DEVTEAM sees all. Same helper as
    # get_incidents -- these two used to disagree, so a police user saw
    # incidents from everywhere but cameras from a single barangay.
    sql, params = apply_scope(payload, "SELECT * FROM cameras", [])
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall()

    # Phase 2 resource-scoped permissions (2026-09-23): additive-only, see
    # permission_grants' own migration comment. A user with zero
    # resource-scoped view_map/camera rows is completely unaffected -- this
    # only NARROWS the org-scoped list above, for the specific user(s) an
    # admin or DevTeam has explicitly "diced down" to individual cameras.
    # DEVTEAM is never narrowed -- master control means unrestricted, not
    # "also subject to its own grants".
    if payload["role"] != "DEVTEAM":
        cursor.execute(
            "SELECT resource_id FROM permission_grants WHERE user_id = ? AND permission_key = 'view_map' AND resource_type = 'camera'",
            (payload["id"],),
        )
        scoped_ids = {r["resource_id"] for r in cursor.fetchall()}
        if scoped_ids:
            rows = [r for r in rows if r["id"] in scoped_ids]

    # Every account sees its cameras' names (Live Monitor is universal), but
    # only camera managers get the stream URL -- it holds the credentials.
    include_url = _holds_permission(cursor, payload, "manage_cameras")
    conn.close()
    return [_row_to_camera_dict(r, include_url) for r in rows]

def _has_permission(cursor, user_id: int, key: str, role: str) -> bool:
    # BUG FOUND 2026-09-02 (full account/permission sweep, since superseded):
    # this used to carry its own admin-bypass branch here (a barangay-only
    # key falling out of it for BOTH admin roles instead of just PNP_ADMIN --
    # see git history for that fix's original notes). That bypass is gone
    # now, not just fixed: require_permission() is this function's ONLY
    # caller, and by the time it reaches here it has already granted every
    # admin WITHOUT a custom_permissions override (i.e. every admin exactly
    # as before this file's 2026-09-04 override feature) its automatic pass
    # -- so an admin only ever reaches this real DB check now because
    # DevTeam explicitly, password-confirmed, opted them OUT of that
    # automatic access via the new override endpoint. A second admin bypass
    # sitting here would silently re-grant everything that override was
    # just used to revoke, on every single request, forever. A standard
    # operator role (BARANGAY_STAFF/PNP_OFFICER) was never in ADMIN_ROLES to
    # begin with, so removing this changes nothing for them.
    cursor.execute("SELECT 1 FROM user_permissions WHERE user_id = ? AND permission_key = ?", (user_id, key))
    return cursor.fetchone() is not None

def _holds_permission(cursor, payload: dict, key: str) -> bool:
    try:
        require_permission(cursor, payload, key)
        return True
    except HTTPException:
        return False

def require_permission(cursor, payload: dict, key: str):
    """Server-side gate matching the permission checkboxes in
    AdminUsersView.tsx / DevteamView.tsx. DEVTEAM always passes; admin tiers
    pass except a PNP admin on a barangay-only key. Standard operator
    accounts must have the key granted in user_permissions."""
    role = payload["role"]
    if role == "DEVTEAM":
        return

    # Cameras belong to the barangay that installed them. PNP gets the feed,
    # not administrative control -- so no PNP role passes manage_cameras,
    # regardless of tier. This is the one place the admin bypass does not
    # apply for PNP; previously a precinct captain could delete a barangay's
    # cameras. BARANGAY_ADMIN is NOT excluded by this -- see _has_permission's
    # 2026-09-02 fix note for why the two admin roles can't be treated the
    # same way on a "barangay-only" key.
    if key in BARANGAY_ONLY_PERMISSIONS and role in PNP_SIDE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Cameras are managed by the barangay that owns them; "
                   f"'{role}' accounts have view access only.")

    # Added 2026-09-22 (explicit user request): the mirror image of the
    # manage_cameras ban above. Applies to BOTH barangay tiers (admin
    # included, not just staff) and comes before the admin-bypass branch
    # below so it can't be short-circuited by admin tier or by a future
    # custom_permissions override -- a DevTeam override grants a DIFFERENT
    # explicit permission set for an admin whose automatic access was
    # revoked, it was never meant to be a backdoor around a hard ban. A
    # stray user_permissions row (however it got there) can't reopen this
    # either, since this raises before _has_permission is ever consulted.
    if key in POLICE_ONLY_PERMISSIONS and role in BARANGAY_SIDE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Crime history and the video record vault are police-only; "
                   f"'{role}' accounts do not have access.")

    # Added 2026-09-04 (user request: a password-gated way for DevTeam to
    # override an admin's normally-automatic permissions). Every admin used
    # to hit this branch unconditionally -- user_permissions was never even
    # consulted for PNP_ADMIN/BARANGAY_ADMIN, so there was nothing an
    # override endpoint could actually change. custom_permissions (set only
    # by that new DevTeam-only, password-confirmed endpoint; 0 for every
    # existing and newly-created admin) skips this automatic pass and falls
    # through to the exact same _has_permission check a standard operator
    # account gets, so a DevTeam-applied override actually takes effect
    # instead of being silently ignored.
    if role in ADMIN_ROLES and not payload.get("custom_permissions"):
        if key not in BARANGAY_ONLY_PERMISSIONS or role in BARANGAY_SIDE_ROLES:
            return
    if not _has_permission(cursor, payload["id"], key, role):
        raise HTTPException(status_code=403, detail=f"Missing permission: {key}")

@app.post("/api/cameras")
async def add_camera(cam: CameraSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    # BUG FOUND 2026-09-03: manage_cameras only gated WHETHER a caller could
    # touch cameras at all, never WHICH barangay's cameras -- unlike every
    # other barangay-owned resource in this file (notify_targets above,
    # apply_scope() everywhere else). Live-tested: a fresh BARANGAY_ADMIN
    # for a brand-new barangay successfully created a camera with
    # barangay_id="cogon", another barangay entirely. Same missing check
    # as notify_targets' add path -- mirrored here.
    role = payload.get("role")
    if role != "DEVTEAM" and cam.barangay_id.lower() != (payload.get("barangay_id") or "").lower():
        conn.close()
        raise HTTPException(status_code=403, detail="Can only add cameras for your own barangay")
    cam_id = str(uuid.uuid4())
    loc = cam.location
    label = ((loc.location_label or "").strip() or None) if loc else None
    try:
        cursor.execute(
            "INSERT INTO cameras (id, name, url, status, barangay_id, lat, lng, location_label)"
            " VALUES (?, ?, ?, 'online', ?, ?, ?, ?)",
            (cam_id, cam.name, cam.url, cam.barangay_id.lower(),
             loc.lat if loc else None, loc.lng if loc else None, label),
        )
        snapshot = {"name": cam.name, "barangay_id": cam.barangay_id.lower()}
        if loc:
            snapshot.update(lat=loc.lat, lng=loc.lng, location_label=label)
        log_audit(cursor, payload, "camera.created", "camera", cam_id, snapshot=snapshot)
        conn.commit()
        await manager.broadcast({"channel": "cameras", "event": "camera_created", "id": cam_id,
                                 "barangay_id": cam.barangay_id.lower()})
        return {"status": "created", "id": cam_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

def _require_own_camera(cursor, payload: dict, cam_id: str):
    """manage_cameras plus ownership: a camera in the caller's own barangay
    and, if they've been diced down to specific cameras, one of those."""
    require_permission(cursor, payload, "manage_cameras")
    cursor.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No such camera.")
    if payload.get("role") != "DEVTEAM":
        if (row["barangay_id"] or "").lower() != (payload.get("barangay_id") or "").lower():
            raise HTTPException(status_code=403, detail="Can only change cameras for your own barangay")
        scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
        if scoped is not None and cam_id not in scoped:
            raise HTTPException(status_code=403, detail="Your camera access doesn't include this camera")
    return row

@app.put("/api/cameras/{cam_id}/location")
async def set_camera_location(cam_id: str, loc: CameraLocationSchema, authorization: Optional[str] = Header(None)):
    """Pin (or move) a smartpole on the Incident Map."""
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        row = _require_own_camera(cursor, payload, cam_id)
        label = (loc.location_label or "").strip() or None
        cursor.execute("UPDATE cameras SET lat = ?, lng = ?, location_label = ? WHERE id = ?",
                       (loc.lat, loc.lng, label, cam_id))
        log_audit(cursor, payload, "camera.located", "camera", cam_id, snapshot={
            "name": row["name"],
            "from": {"lat": row["lat"], "lng": row["lng"], "location_label": row["location_label"]},
            "to": {"lat": loc.lat, "lng": loc.lng, "location_label": label}})
        conn.commit()
        await manager.broadcast({"channel": "cameras", "event": "camera_located", "id": cam_id,
                                 "barangay_id": (row["barangay_id"] or "").lower()})
        return {"status": "located", "id": cam_id, "lat": loc.lat, "lng": loc.lng, "location_label": label}
    finally:
        conn.close()

@app.delete("/api/cameras/{cam_id}")
async def delete_camera(cam_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    # BUG FOUND 2026-09-03: no ownership check at all -- ANY caller with
    # manage_cameras (i.e. any barangay admin/staff granted it) could delete
    # ANY camera by id, in any barangay. Live-tested and confirmed: a fresh
    # QA barangay admin deleted 'cogon''s real "Main Entrance Hub" camera
    # (id="1", the one config.json's camera.camera_id default points at) on
    # the first try. Restored that row from a live-captured copy immediately
    # after finding this. Fixed the same way notify_targets' delete path
    # already does it: look up the row's real owner first, compare before
    # deleting.
    role = payload.get("role")
    if role != "DEVTEAM":
        cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (cam_id,))
        existing = cursor.fetchone()
        if existing and existing["barangay_id"] != payload.get("barangay_id"):
            conn.close()
            raise HTTPException(status_code=403, detail="Can only delete cameras for your own barangay")
        scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
        if scoped is not None and cam_id not in scoped:
            conn.close()
            raise HTTPException(status_code=403, detail="Your camera access doesn't include this camera")
    cursor.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,))
    cam_row = cursor.fetchone()
    cursor.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
    if cam_row:
        log_audit(cursor, payload, "camera.deleted", "camera", cam_id,
                  snapshot={k: v for k, v in dict(cam_row).items() if k != "url"})
    conn.commit()
    conn.close()
    if cam_row:
        await manager.broadcast({"channel": "cameras", "event": "camera_deleted", "id": cam_id,
                                 "barangay_id": (cam_row["barangay_id"] or "").lower()})
    return {"status": "deleted"}


# --- RESPONDER NOTIFICATIONS (Telegram/SMS on confirm-and-report) ---
# docs/incident_response_plan.md §2. Scoped like cameras (barangay-owned) or
# like a PNP admin's own station -- DEVTEAM sees everything, matching the
# ADMIN_ROLES-bypass pattern used by require_permission()/apply_scope()
# elsewhere in this file.
@app.get("/api/notify_targets")
async def list_notify_targets(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")
    role = payload.get("role")
    if role == "DEVTEAM":
        cursor.execute("SELECT * FROM notify_targets ORDER BY created_at DESC")
    elif role in PNP_SIDE_ROLES:
        cursor.execute(
            "SELECT * FROM notify_targets WHERE station_id = ? ORDER BY created_at DESC",
            (payload.get("station_id"),),
        )
    else:
        cursor.execute(
            "SELECT * FROM notify_targets WHERE barangay_id = ? ORDER BY created_at DESC",
            (payload.get("barangay_id"),),
        )
    rows = [dict(r) for r in cursor.fetchall()]
    channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
    if channels is not None:
        rows = [r for r in rows if r["channel"] in channels]
    conn.close()
    return rows


@app.post("/api/notify_targets")
async def add_notify_target(target: NotifyTargetSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")

    if target.channel not in ("telegram", "sms"):
        conn.close()
        raise HTTPException(status_code=400, detail="channel must be 'telegram' or 'sms'")
    channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
    if channels is not None and target.channel not in channels:
        conn.close()
        raise HTTPException(status_code=403, detail=f"You can't manage {target.channel} recipients")
    if not target.barangay_id and not target.station_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Provide barangay_id or station_id")

    # A non-DEVTEAM caller may only create a target scoped to their own
    # barangay/station -- otherwise a barangay admin could register a
    # responder in someone else's jurisdiction.
    role = payload.get("role")
    if role != "DEVTEAM":
        if role in PNP_SIDE_ROLES and target.station_id != payload.get("station_id"):
            conn.close()
            raise HTTPException(status_code=403, detail="Can only manage targets for your own station")
        if role not in PNP_SIDE_ROLES and target.barangay_id != payload.get("barangay_id"):
            conn.close()
            raise HTTPException(status_code=403, detail="Can only manage targets for your own barangay")

    target_id = str(uuid.uuid4())
    try:
        cursor.execute(
            """INSERT INTO notify_targets (id, barangay_id, station_id, channel, destination, label, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (target_id, target.barangay_id, target.station_id, target.channel, target.destination, target.label),
        )
        log_audit(cursor, payload, "notify_target.created", "notify_target", target_id,
                  snapshot={"channel": target.channel, "destination": target.destination, "label": target.label,
                            "barangay_id": target.barangay_id, "station_id": target.station_id})
        conn.commit()
        return {"status": "created", "id": target_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/notify_targets/{target_id}")
async def delete_notify_target(target_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")

    role = payload.get("role")
    if role != "DEVTEAM":
        cursor.execute("SELECT barangay_id, station_id, channel FROM notify_targets WHERE id = ?", (target_id,))
        existing = cursor.fetchone()
        if existing:
            channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
            if channels is not None and existing["channel"] not in channels:
                conn.close()
                raise HTTPException(status_code=403, detail=f"You can't manage {existing['channel']} recipients")
            if role in PNP_SIDE_ROLES and existing["station_id"] != payload.get("station_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own station")
            if role not in PNP_SIDE_ROLES and existing["barangay_id"] != payload.get("barangay_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own barangay")

    cursor.execute("SELECT * FROM notify_targets WHERE id = ?", (target_id,))
    nt_row = cursor.fetchone()
    cursor.execute("DELETE FROM notify_targets WHERE id = ?", (target_id,))
    if nt_row:
        log_audit(cursor, payload, "notify_target.deleted", "notify_target", target_id, snapshot=dict(nt_row))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


# --- PTZ CAMERA CONTROL (ONVIF) ---
#
# Gated behind manage_cameras, the same permission that governs adding and
# removing cameras: physically aiming a public-safety camera is at least as
# consequential as renaming one, and pointing a camera away from an incident
# is a real abuse vector.
#
# Capabilities are read from the device (ptz_control queries ONVIF rather
# than assuming), so the dashboard can disable controls the hardware does
# not have instead of showing buttons that do nothing.

class PTZMoveSchema(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    duration: Optional[float] = 0.6   # auto-stop; see ptz_control.move()


class PTZPresetSchema(BaseModel):
    name: str


@app.get("/api/ptz/capabilities")
async def ptz_capabilities(authorization: Optional[str] = Header(None)):
    """Never raises on an unreachable/unconfigured camera -- the dashboard
    calls this on load, and 'no camera attached' is a normal state."""
    require_auth(authorization)
    from ptz_control import get_controller, PTZNotConfigured
    # BUG FOUND 2026-08-19: this was the one PTZ endpoint with no try/except
    # -- every sibling (move/stop/presets/goto/save) catches PTZNotConfigured
    # and generic errors, this one didn't, directly contradicting its own
    # docstring's promise. The dashboard calls this unconditionally on every
    # load, so an unconfigured/unreachable camera turned into a 500 there
    # too, right when "never raises" mattered most.
    try:
        return get_controller().get_capabilities()
    except PTZNotConfigured as e:
        return {"configured": False, "reason": str(e), "pan_tilt": False,
                "zoom": False, "presets": False, "two_way_audio": False}
    except Exception as e:
        return {"configured": False, "reason": f"camera error: {e}", "pan_tilt": False,
                "zoom": False, "presets": False, "two_way_audio": False}


@app.post("/api/ptz/move")
async def ptz_move(data: PTZMoveSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "manage_cameras")
    finally:
        conn.close()
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return get_controller().move(data.pan, data.tilt, data.zoom, data.duration)
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


@app.post("/api/ptz/stop")
async def ptz_stop(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "manage_cameras")
    finally:
        conn.close()
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return get_controller().stop()
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


@app.get("/api/ptz/presets")
async def ptz_list_presets(authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return {"presets": get_controller().list_presets()}
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


@app.post("/api/ptz/presets/{token}/goto")
async def ptz_goto_preset(token: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "manage_cameras")
    finally:
        conn.close()
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return get_controller().goto_preset(token)
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


@app.post("/api/ptz/presets")
async def ptz_save_preset(data: PTZPresetSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "manage_cameras")
    finally:
        conn.close()
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return get_controller().save_preset(data.name)
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


# --- HIERARCHICAL INCIDENT FETCH ---
@app.get("/api/incidents")
async def get_incidents(authorization: Optional[str] = Header(None), filter_barangay_id: Optional[str] = "all",
                        purpose: Optional[str] = "map"):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()

    role = payload["role"]
    # BUG FOUND 2026-09-04 (caught live testing the same day's admin
    # permission override feature): this hand-rolled its own "is this an
    # admin" bypass instead of calling require_permission(), so it never
    # knew about custom_permissions at all -- DevTeam could password-confirm
    # revoking an admin's view_map/view_history, require_permission() (used
    # by every OTHER endpoint) would correctly start 403ing them, and this
    # one endpoint would keep returning 200 with full incident data anyway.
    # Skip the bypass for exactly the admins the override applies to, same
    # condition require_permission() itself uses.
    #
    # 2026-10-01: _holds_permission applies exactly that rule (and the
    # police-only ban), so it replaces the hand-rolled query. It also admits
    # confirm_dismiss_alerts on its own, for the Live Monitor's incident
    # queue: an account allowed to confirm/dismiss alerts couldn't see a
    # single alert to act on without also holding the map. Such an account
    # gets the active queue only -- no map history, no archive.
    can_map = _holds_permission(cursor, payload, "view_map")
    can_history = _holds_permission(cursor, payload, "view_history")
    can_queue = _holds_permission(cursor, payload, "confirm_dismiss_alerts")
    if not (can_map or can_history or can_queue):
        conn.close()
        raise HTTPException(status_code=403, detail="Missing permission: view_map, view_history or confirm_dismiss_alerts")
    queue_only = not (can_map or can_history)

    # Visibility and redaction are two SEPARATE decisions and were previously
    # tangled into one if/else:
    #   visibility -- which barangays' incidents you may see (scope_clause)
    #   redaction  -- whether investigative PII is masked (barangay side yes,
    #                 PNP side no; police need names/narrative to investigate)
    redact = role in BARANGAY_SIDE_ROLES

    # filter_barangay_id only ever NARROWS within the caller's scope. It is
    # appended alongside the scope clause, never instead of it, so it cannot
    # be used to reach outside your own jurisdiction.
    # deleted_at IS NULL added 2026-09-22 alongside the soft-delete feature
    # -- a deleted incident stays in the table (so it can be Restored from
    # the Audit Log) but must disappear from every normal listing, same as
    # it did when DELETE actually removed the row.
    where_clauses, where_params = ["deleted_at IS NULL"], []
    if filter_barangay_id and filter_barangay_id.lower() != "all":
        where_clauses.append("LOWER(barangay_id) = ?")
        where_params.append(filter_barangay_id.lower())

    # Added 2026-09-22 (POLICE_ONLY_PERMISSIONS -- explicit user request:
    # barangay accounts lose the crime-history archive, police-only now).
    # This endpoint serves BOTH the live Incident Map (view_map) AND the
    # archived Incident Log (view_history, HistoryView.tsx -- it calls this
    # SAME endpoint and just drops non-Active rows client-side) from one
    # query with no other way to tell those two callers apart server-side.
    # require_permission()'s POLICE_ONLY_PERMISSIONS ban stops a barangay
    # account from ever being GRANTED view_history going forward, but this
    # endpoint has always done its own hand-rolled permission check above
    # (independent of require_permission()) rather than calling it -- so a
    # stale view_history row granted before this restriction existed would
    # silently still work, and even without one, a barangay caller who only
    # has view_map would still get every Confirmed/Dismissed row back, i.e.
    # the exact archive being restricted. Enforcing a hard status filter
    # here, not just refusing the grant, closes both gaps at once: a
    # barangay-side caller only ever gets their own currently-ACTIVE
    # incidents (their live queue/map), never the historical record,
    # regardless of which permission key let them into this endpoint at
    # all or how old that grant is.
    if role in BARANGAY_SIDE_ROLES or queue_only:
        where_clauses.append("status = 'Active'")
    extra_where = " AND ".join(where_clauses)
    extra_params = where_params

    sql, params = apply_scope(
        payload, "SELECT * FROM incidents", [],
        extra_where=extra_where, extra_params=extra_params,
    )
    sql += " ORDER BY occurred_date DESC, occurred_time DESC"
    cursor.execute(sql, tuple(params))

    inc_rows = cursor.fetchall()

    # Dicing (2026-09-29): the map and the history archive share this
    # endpoint, so the caller says which one it is and gets that
    # permission's narrowing -- falling back to whichever of the two it
    # actually holds, so asking for the other can never widen anything.
    scope_key = "view_history" if purpose == "history" else "view_map"
    if queue_only:
        scope_key = "confirm_dismiss_alerts"
    elif not _holds_permission(cursor, payload, scope_key):
        scope_key = "view_map" if scope_key == "view_history" else "view_history"
    allowed_types = _scoped_resource_ids(cursor, payload, scope_key, "crime_type")
    allowed_cams = _scoped_resource_ids(cursor, payload, "view_map", "camera") if scope_key == "view_map" else None
    if allowed_types is not None:
        inc_rows = [r for r in inc_rows if (r["type"] or "").strip().upper() in allowed_types]
    if allowed_cams is not None:
        inc_rows = [r for r in inc_rows if not r["camera_id"] or r["camera_id"] in allowed_cams]

    # Was 2 extra queries PER incident row (N+1) -- batch both child tables
    # in one IN(...) query each instead, keyed by incident_id.
    inc_ids = [inc["id"] for inc in inc_rows]
    details_by_id: dict = {}
    vis_by_id: dict = {}
    if inc_ids:
        placeholders = ",".join("?" for _ in inc_ids)
        cursor.execute(f"SELECT * FROM incident_details WHERE incident_id IN ({placeholders})", tuple(inc_ids))
        for row in cursor.fetchall():
            details_by_id[row["incident_id"]] = row
        cursor.execute(f"SELECT * FROM incident_visibility WHERE incident_id IN ({placeholders})", tuple(inc_ids))
        for row in cursor.fetchall():
            vis_by_id[row["incident_id"]] = row

    results = []
    for inc in inc_rows:
        record = _row_to_incident_dict(inc, details_by_id.get(inc["id"]), vis_by_id.get(inc["id"]))
        if redact:
            # Left out, not replaced with a "[RESTRICTED]" placeholder: a
            # placeholder tells the reader there is a police narrative they
            # aren't allowed to read, which is itself information.
            for field in ("narrative", "nature_of_call", "arrival_reason", "additional_officers"):
                record[field] = None
        results.append(record)
    conn.close()
    return results

@app.post("/api/incidents")
async def add_incident(incident: IncidentSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        # Filing is done from the Incident Map, so it takes that screen's
        # permission (it used to take none). Never trust the client's
        # barangay_id: a barangay account files into its own barangay
        # whatever the body says; a police account, which has no barangay of
        # its own (every police filing used to 403 here), into the named one
        # only if its station covers it.
        require_permission(cursor, payload, "view_map")
        if payload["role"] in BARANGAY_SIDE_ROLES:
            effective_barangay_id = payload.get("barangay_id")
        else:
            effective_barangay_id = (incident.barangay_id or "").strip().lower()
            frag, fparams = scope_clause(payload, "id")
            cursor.execute("SELECT 1 FROM barangays WHERE id = ?" + (f" AND {frag}" if frag else ""),
                           [effective_barangay_id] + fparams)
            if not cursor.fetchone():
                raise HTTPException(status_code=403, detail="That barangay is outside your jurisdiction.")
        if not effective_barangay_id:
            raise HTTPException(status_code=403, detail="Your account has no assigned location.")
        if incident.camera_id:
            cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (incident.camera_id,))
            cam_row = cursor.fetchone()
            if not cam_row or (cam_row["barangay_id"] or "").lower() != effective_barangay_id.lower():
                raise HTTPException(status_code=400, detail="That smartpole isn't in the barangay this report is filed in.")
        cursor.execute(
            """INSERT INTO incidents
               (id, case_id, type, severity, status, lat, lng, location_name,
                occurred_date, occurred_time, confidence, officer, barangay_id, source, camera_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL', ?)""",
            (incident.id, incident.case_id, incident.type, incident.severity, incident.status,
             incident.lat, incident.lng, incident.location_name, incident.occurred_date, incident.occurred_time,
             incident.confidence, incident.officer, effective_barangay_id.lower(), incident.camera_id),
        )
        cursor.execute(
            """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers)
               VALUES (?, ?, ?, ?, ?)""",
            (incident.id, incident.narrative, incident.nature_of_call, incident.arrival_reason, incident.additional_officers),
        )
        cursor.execute(
            "INSERT INTO incident_visibility (incident_id, map_hidden) VALUES (?, 0)",
            (incident.id,),
        )
        log_audit(cursor, payload, "incident.filed", "incident", incident.id,
                  snapshot={"type": incident.type, "barangay_id": effective_barangay_id, "location": incident.location_name})
        conn.commit()
        return {"status": "persisted"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

# --- AI REPORT DRAFTS ---
# There is no language model in this stack: the "AI report" is assembled
# from what the detectors and evidence files actually recorded (event,
# model, confidence, people in frame, weapons, measured scene brightness,
# evidence hashes), phrased as a police blotter narrative. Every statement
# traces back to a stored value, and the draft says plainly that an
# officer has to verify it before it becomes the official report.

AI_EVENT_PHRASES = {
    "ASSAULT": "a suspected physical assault",
    "ARMED THREAT": "a person carrying or brandishing a suspected weapon",
    "ROBBERY": "a suspected robbery",
    "VANDALISM": "suspected vandalism / damage to property",
    "PHYSICAL VIOLENCE": "suspected physical violence",
    "THEFT": "a suspected theft",
    "HARDWARE_PANIC_INTERRUPT": "a manual panic-button activation on the smartpole",
}

AI_RECOMMENDED_ACTIONS = {
    "ARMED THREAT": "Dispatch the nearest mobile patrol immediately and approach as an armed-subject call. Secure the area and preserve the camera footage.",
    "ROBBERY": "Dispatch a patrol unit to the scene, identify and interview the complainant, and canvass for other CCTV along likely escape routes.",
    "ASSAULT": "Dispatch a patrol unit to the scene, check for injured persons and arrange medical assistance, and identify the parties involved.",
    "PHYSICAL VIOLENCE": "Dispatch a patrol unit to the scene, check for injured persons and arrange medical assistance, and identify the parties involved.",
    "VANDALISM": "Coordinate with the barangay to document the damage and identify the property owner; review footage for identifiable subjects.",
    "HARDWARE_PANIC_INTERRUPT": "Contact the barangay tanod on duty and dispatch the nearest unit to the smartpole to check on the person who pressed the panic button.",
}

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _confidence_band(conf: Optional[float]) -> str:
    if conf is None:
        return "unknown"
    if conf >= 0.85:
        return "high"
    if conf >= 0.65:
        return "moderate"
    return "low"


def _time_of_day(hhmmss: str) -> str:
    try:
        h = int(str(hhmmss).replace(":", "")[:2])
    except ValueError:
        return "unknown"
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "midday"
    if 14 <= h < 18:
        return "afternoon"
    if 18 <= h < 21:
        return "evening"
    return "night"


def _format_12h(hhmmss: str) -> str:
    digits = str(hhmmss).replace(":", "")[:4]
    try:
        return datetime.strptime(digits, "%H%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return str(hhmmss)


def incident_type_title(event: Optional[str]) -> str:
    """ASSAULT -> Assault, PHYSICAL VIOLENCE -> Physical violence."""
    event = (event or "").strip().upper()
    if event == "HARDWARE_PANIC_INTERRUPT":
        return "Panic alert"
    return event.replace("_", " ").capitalize() or "Incident"


def _clip_seconds(duration) -> Optional[float]:
    """Clip durations are stored as "00:08" (recorder) or "3.0s" (cutter)."""
    d = str(duration or "").strip().lower()
    try:
        if d.endswith("s"):
            return float(d[:-1])
        parts = [float(x) for x in d.split(":")]
        total = 0.0
        for x in parts:
            total = total * 60 + x
        return total if parts else None
    except ValueError:
        return None


def clip_length_phrase(duration) -> Optional[str]:
    secs = _clip_seconds(duration)
    if secs is None:
        return None
    secs = int(round(secs))
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    m, s_ = divmod(secs, 60)
    return f"{m} min {s_} s" if s_ else f"{m} min"


def label_incident_clips(event: Optional[str], clips: list) -> list:
    """Evidence clips are named for people, not by file: "Assault 1",
    "Assault 2"... in recording order. The file name and hash stay in the
    record (chain of custody) but aren't what an officer reads."""
    title = incident_type_title(event)
    return [{**c, "label": f"{title} {i}", "length": clip_length_phrase(c.get("duration"))}
            for i, c in enumerate(clips, start=1)]


def _people_phrase(n: int) -> str:
    word = NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)
    return f"{word.capitalize()} ({n}) person{'s were' if n != 1 else ' was'}"


def build_ai_report_draft(cursor, incident_id: str) -> Optional[dict]:
    cursor.execute(
        """SELECT i.*, d.narrative AS d_narrative, d.ai_context, v.screenshot_path, v.screenshot_sha256,
                  c.name AS camera_name, b.name AS barangay_name, b.city_municipality, b.province
           FROM incidents i
           LEFT JOIN incident_details d ON d.incident_id = i.id
           LEFT JOIN incident_visibility v ON v.incident_id = i.id
           LEFT JOIN cameras c ON c.id = i.camera_id
           LEFT JOIN barangays b ON b.id = i.barangay_id
           WHERE i.id = ?""",
        (incident_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    inc = dict(row)
    try:
        ctx = json.loads(inc.get("ai_context") or "{}") or {}
    except (TypeError, ValueError):
        ctx = {}

    cursor.execute(
        """SELECT s.name FROM station_barangays sb JOIN police_stations s ON s.id = sb.station_id
           WHERE sb.barangay_id = ? ORDER BY s.name LIMIT 1""",
        (inc.get("barangay_id"),),
    )
    st = cursor.fetchone()
    station_name = st["name"] if st else None

    cursor.execute(
        "SELECT filename, duration, sha256, recorded_at FROM video_records WHERE associated_incident_id = ? ORDER BY recorded_at",
        (incident_id,),
    )
    clips = [dict(r) for r in cursor.fetchall()]

    event = (inc.get("type") or "UNKNOWN").upper()
    conf = inc.get("confidence")
    band = _confidence_band(conf)
    tod = _time_of_day(inc.get("occurred_time") or "")
    clips = label_incident_clips(event, clips)
    people = ctx.get("people_in_frame")
    weapons = [w for w in (ctx.get("weapons") or []) if isinstance(w, dict) and w.get("name")]
    detector = ctx.get("detector")
    source = inc.get("source") or "MANUAL"

    place_bits = [inc.get("camera_name") or inc.get("location_name") or "an unnamed camera location"]
    if inc.get("barangay_name"):
        place_bits.append(f"Barangay {inc['barangay_name']}")
    if inc.get("city_municipality"):
        place_bits.append(inc["city_municipality"])
    place = ", ".join(place_bits)
    when = f"{inc.get('occurred_date')} at approximately {_format_12h(inc.get('occurred_time') or '')} ({tod})"
    what = AI_EVENT_PHRASES.get(event, f"a suspected {event.lower()} incident")

    paras = []
    if source == "HARDWARE_PANIC":
        paras.append(f"On {when}, {what} was recorded at {place}. No AI model was involved in raising this alert.")
    elif source == "AI_AUTOMATION":
        s = f"On {when}, the EcoVision AI surveillance system flagged {what} at {place}."
        if conf is not None:
            s += f" The system's confidence in this alert was {round(conf * 100)}% ({band})."
        paras.append(s)
    else:
        paras.append(f"On {when}, {what} was filed manually by an operator for {place}.")

    obs = []
    if isinstance(people, int):
        obs.append(f"{_people_phrase(people)} in the camera's field of view at the time of detection.")
    attribution = ctx.get("attribution")
    if attribution == "track" and ctx.get("track_id") is not None:
        obs.append(f"The alert was attributed to tracked person #{ctx['track_id']}.")
    elif attribution == "scene":
        obs.append("The alert was raised on the scene as a whole; the system could not attribute it to one specific person.")
    if weapons:
        obs.append("Suspected weapon(s) detected: " + ", ".join(
            f"{w['name']} ({round(float(w.get('conf', 0)) * 100)}%)" for w in weapons) + ".")
    elif source == "AI_AUTOMATION" and event != "ARMED THREAT" and ctx:
        obs.append("No weapon was detected.")
    if obs:
        paras.append(" ".join(obs))

    ev = []
    if inc.get("screenshot_path"):
        ev.append("A still image was captured at the moment of detection.")
    if clips:
        names = [c["label"] + (f" ({c['length']})" if c.get("length") else "") for c in clips]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        ev.append(f"{len(clips)} video clip{'s are' if len(clips) != 1 else ' is'} on file: {listed}.")
    if ev:
        paras.append(" ".join(ev))

    paras.append(
        "This narrative was generated automatically from sensor and model output and has not been verified. "
        "The reporting officer must review the footage, confirm or correct each statement, and add the "
        "complainant, victim, witness and suspect information gathered on scene.")

    if weapons:
        suspect = "Subject seen with suspected " + ", ".join(w["name"] for w in weapons) + ". Physical description to be completed by the responding officer from the footage."
    elif attribution == "track" and ctx.get("track_id") is not None:
        suspect = f"Tracked person #{ctx['track_id']} in the footage. Physical description to be completed by the responding officer."
    else:
        suspect = "Unidentified. To be completed by the responding officer from the footage and witness accounts."

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "incident_type": event,
        "severity": inc.get("severity"),
        "occurred_date": inc.get("occurred_date"),
        "occurred_time": inc.get("occurred_time"),
        "time_of_day": tod,
        "location": {
            "camera_name": inc.get("camera_name") or inc.get("location_name"),
            "camera_id": inc.get("camera_id"),
            "barangay": inc.get("barangay_name") or inc.get("barangay_id"),
            "city_municipality": inc.get("city_municipality"),
            "province": inc.get("province"),
            "station": station_name,
        },
        "detection": {
            "source": source,
            "detector": detector,
            "confidence": conf,
            "confidence_band": band,
            "people_in_frame": people,
            "attribution": attribution,
            "track_id": ctx.get("track_id"),
            "weapons": weapons,
        },
        "evidence": {
            "snapshot": inc.get("screenshot_path"),
            "clips": [{"label": c["label"], "length": c.get("length"), "recorded_at": c.get("recorded_at")} for c in clips],
        },
        "narrative": "\n\n".join(paras),
        "suspect_description": suspect,
        "recommended_action": AI_RECOMMENDED_ACTIONS.get(event, "Dispatch a patrol unit to verify the incident on scene."),
    }


def _ai_one_liner(event: str, conf: float, location_name: Optional[str], ctx: dict) -> str:
    what = AI_EVENT_PHRASES.get((event or "").upper(), f"a suspected {(event or 'unknown').lower()} incident")
    s = f"AI flagged {what} at {location_name or 'an unnamed camera'} ({round((conf or 0) * 100)}% confidence"
    if ctx.get("detector"):
        s += f", {ctx['detector']}"
    s += ")."
    if isinstance(ctx.get("people_in_frame"), int):
        s += f" {ctx['people_in_frame']} person(s) in view."
    weapons = [w.get("name") for w in (ctx.get("weapons") or []) if isinstance(w, dict) and w.get("name")]
    if weapons:
        s += f" Weapon: {', '.join(weapons)}."
    return s


# The AI core's endpoints (ai_trigger, ai_register_clip, camera_name) have no
# user session to authenticate with. They used to rely on the backend being
# reachable only from localhost -- but backend.host is 0.0.0.0 (the ESP32
# pole has to reach /api/esp32/register and /api/panic_trigger over the
# LAN), so anyone on the network could file fake incidents and add clips to
# the evidence vault. The AI core always calls from this machine
# (networking.api_url / Electron's BACKEND_URL are 127.0.0.1), so these
# answer loopback only, plus any host listed in security.trusted_service_hosts
# or ECOVISION_TRUSTED_SERVICE_HOSTS for an AI core run on another machine.
TRUSTED_SERVICE_HOSTS = {"127.0.0.1", "::1", "localhost"}     | set(sys_config.get("security", {}).get("trusted_service_hosts", []) or [])     | {h.strip() for h in os.environ.get("ECOVISION_TRUSTED_SERVICE_HOSTS", "").split(",") if h.strip()}


def _require_local_service(request: Request):
    host = request.client.host if request.client else None
    if host not in TRUSTED_SERVICE_HOSTS:
        raise HTTPException(status_code=403, detail="Only the AI core on this machine may call this endpoint.")


@app.post("/api/ai_trigger")
async def ai_trigger(data: AiTriggerSchema, request: Request):
    # Deliberately NOT behind require_auth -- called by the local AI
    # pipeline (main.py on 8001), not a browser. See TRUSTED_SERVICE_HOSTS.
    _require_local_service(request)
    incident_id = data.id if data.id else str(uuid.uuid4())
    case_id = f"CASE-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4()).replace('-', '')[:8].upper()}"
    now = datetime.now()
    screenshot_url = data.screenshot_path or ""
    # Chain of custody (docs/incident_response_plan.md §3) -- hash the
    # snapshot's actual bytes now, at the moment this incident is created,
    # not later when someone happens to look at it.
    screenshot_hash = None
    if screenshot_url:
        screenshot_hash = sha256_of_file(os.path.join(SCREENSHOTS_DIR, os.path.basename(screenshot_url)))

    conn = get_conn()
    cursor = conn.cursor()
    # The alert's map pin goes where the camera that saw it stands. Falls
    # back to the old fixed point for a camera not yet placed on the map.
    lat, lng = 11.0504, 124.6062
    if data.camera_id:
        cursor.execute("SELECT lat, lng FROM cameras WHERE id = ?", (data.camera_id,))
        cam_row = cursor.fetchone()
        if cam_row and cam_row["lat"] is not None and cam_row["lng"] is not None:
            lat, lng = cam_row["lat"], cam_row["lng"]
    cursor.execute(
        """INSERT INTO incidents
           (id, case_id, type, severity, status, lat, lng, location_name,
            occurred_date, occurred_time, confidence, officer, barangay_id, source, camera_id)
           VALUES (?, ?, ?, 'HIGH', 'Active', ?, ?, ?, ?, ?, ?, 'AI_AUTOMATION', ?, 'AI_AUTOMATION', ?)""",
        (incident_id, case_id, data.event, lat, lng, data.location_name,
         now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), data.confidence, data.barangay_id.lower(),
         data.camera_id),
    )
    ctx = data.context or {}
    cursor.execute(
        """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers, ai_context)
           VALUES (?, ?, 'EMERGENCY_AI_FLAG', 'AUTOMATED_TRIGGER', 'NONE', ?)""",
        (incident_id, _ai_one_liner(data.event, data.confidence, data.location_name, ctx),
         json.dumps(ctx) if ctx else None),
    )
    cursor.execute(
        "INSERT INTO incident_visibility (incident_id, map_hidden, screenshot_path, screenshot_sha256) VALUES (?, 0, ?, ?)",
        (incident_id, screenshot_url, screenshot_hash),
    )
    conn.commit()
    conn.close()

    # BUG FOUND 2026-09-03: camera_link_id was hardcoded to "1" here always
    # -- every AI-triggered incident's live broadcast claimed it came from
    # camera 1 regardless of which camera actually saw it. Now the real
    # value (may be None for an older caller that doesn't send it yet).
    await manager.broadcast({
        "channel": "incidents", "status": "CRITICAL", "id": incident_id, "type": data.event,
        "location": data.location_name, "conf": data.confidence, "camera_link_id": data.camera_id,
        "barangay_id": (data.barangay_id or "").lower(),
    })
    return {"status": "processed", "incident_id": incident_id}

@app.get("/api/camera_name/{camera_id}")
async def camera_name(camera_id: str, request: Request):
    """Lets main.py resolve the real, currently-registered name for the
    camera it's pointed at, instead of a name baked into config.json --
    added per request: "barangay adds a camera then adds a name and now the
    police can access that camera and show the name". If a barangay renames
    a camera in the Cameras tab, the AI core picks up the new name the next
    time it starts (it resolves this once at startup, not per-alert -- a
    live rename doesn't retroactively relabel an already-running session,
    same restart-to-apply rule as every other config change tonight).

    Loopback only (see TRUSTED_SERVICE_HOSTS), same reasoning as /api/ai_trigger: the
    caller is the local AI pipeline, not a browser, and a camera's own
    display name isn't sensitive. Give it a service credential if this
    backend is ever exposed beyond localhost.

    Also returns this camera's per-camera detector overrides (added
    alongside camera_model_config -- see that table's comment): main.py
    resolves its own camera_id here once at startup anyway, so piggybacking
    the model map on the same call avoids a second unauthenticated
    round-trip for the same "what should THIS camera do" question. Same
    once-at-startup, restart-to-apply rule as the name lookup above.

    2026-08-28: also piggybacks camera_threshold_config (§28.1 per-camera
    operating points) for the same reason -- one more unauthenticated
    round-trip main.py would otherwise need at the exact same point in
    startup, for the exact same "what should THIS camera do" question.
    """
    _require_local_service(request)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name, barangay_id FROM cameras WHERE id = ?", (camera_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No camera registered with id {camera_id!r}")
    models = _camera_model_map(cursor, camera_id)
    thresholds = _camera_threshold_map(cursor, camera_id)
    conn.close()
    # BUG FOUND 2026-09-03: this never returned barangay_id, so main.py had
    # nothing to resolve a real AI-triggered incident's jurisdiction from --
    # _post_alert hardcoded "cogon" for every camera, on every AI core,
    # regardless of which barangay actually owns the camera that saw it.
    # A barangay with more than one camera (or a second barangay at all)
    # would have had every detection silently misrouted to cogon's
    # notify_targets. Returned here so main.py can resolve it once at
    # startup alongside the name, the same round-trip it already makes.
    return {"id": camera_id, "name": row["name"], "barangay_id": row["barangay_id"],
            "models": models, "thresholds": thresholds}

@app.post("/api/esp32/register")
async def esp32_register(request: Request):
    """The pole announces itself here on boot and on every heartbeat.

    Unauthenticated for the same reason /api/ai_trigger is: the caller is a
    microcontroller on the local network with no user session and no ability
    to hold a token. It reveals nothing and changes nothing except which
    address the siren calls -- and only to the address the caller is
    demonstrably reachable at, since request.client.host cannot be spoofed
    into pointing somewhere the request didn't come from without also
    breaking the TCP handshake.
    """
    ip = request.client.host if request.client else None
    _remember_esp32_ip(ip, "register")
    return {"status": "registered", "your_ip": ip, "siren_enabled": ESP32_ENABLED}


@app.get("/api/esp32/status")
async def esp32_status(authorization: Optional[str] = Header(None)):
    """Where the backend currently thinks the pole is, and how it knows."""
    require_auth(authorization)
    info = {"ip": ESP32_IP, "enabled": ESP32_ENABLED, "source": "config.json ip_override / default"}
    try:
        with open(_ESP32_STATE_PATH, "r", encoding="utf-8") as fh:
            info.update(json.load(fh))
            info["source"] = "learned from the device itself"
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return info


@app.post("/api/panic_trigger")
async def panic_trigger(data: PanicSchema, request: Request):
    # Restored: a panic press also teaches us the pole's current address.
    # This is the original self-registration behaviour that the /panic ->
    # /api/panic_trigger refactor silently dropped (see _remember_esp32_ip).
    _remember_esp32_ip(request.client.host if request.client else None, "panic_trigger")
    incident_id = str(uuid.uuid4())
    case_id = f"PANIC-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4()).replace('-', '')[:8].upper()}"
    now = datetime.now()

    screenshot_url = ""
    try:
        # requests.post is blocking I/O -- run it off the event loop so this
        # (up to 2s) call doesn't stall every other in-flight request and the
        # /ws WebSocket for its duration.
        cap_res = await asyncio.to_thread(
            requests.post, _ai_core_capture_url(), json={"incident_id": incident_id}, timeout=2.0
        )
        if cap_res.ok:
            screenshot_url = cap_res.json().get("screenshot_path") or ""
            print(f"🚨 [PANIC] AI pipeline evidence capture: {cap_res.json().get('status')}")
    except Exception as e:
        print(f"⚠️  [PANIC] AI pipeline unreachable, logging panic with no evidence: {e}")

    screenshot_hash = None
    if screenshot_url:
        screenshot_hash = sha256_of_file(os.path.join(SCREENSHOTS_DIR, os.path.basename(screenshot_url)))

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO incidents
           (id, case_id, type, severity, status, lat, lng, location_name,
            occurred_date, occurred_time, confidence, officer, barangay_id, source)
           VALUES (?, ?, 'HARDWARE_PANIC_INTERRUPT', 'CRITICAL', 'Active', ?, ?, ?, ?, ?, 1.0, 'FIELD_NODE', ?, 'HARDWARE_PANIC')""",
        (incident_id, case_id, 11.0510, 124.6070, "Hardware Node Interface",
         now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), data.barangay_id.lower()),
    )
    cursor.execute(
        """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, 'PANIC_BUTTON_ENGAGED', 'MANUAL_OVERRIDE', 'NONE')""",
        (incident_id, "Manual hardware safety interface switch depressed at source terminal."),
    )
    cursor.execute(
        "INSERT INTO incident_visibility (incident_id, map_hidden, screenshot_path, screenshot_sha256) VALUES (?, 0, ?, ?)",
        (incident_id, screenshot_url, screenshot_hash),
    )
    conn.commit()
    conn.close()

    await manager.broadcast({
        "channel": "incidents", "status": "CRITICAL", "id": incident_id, "type": "HARDWARE_PANIC_INTERRUPT",
        "location": "Hardware Node Interface", "conf": 1.0, "camera_link_id": "2",
        "barangay_id": (data.barangay_id or "").lower(),
    })
    return {"status": "panic_logged", "id": incident_id}

INCIDENT_STATUSES = ("Active", "Confirmed", "Dismissed")


def _valid_incident_status(status: Optional[str]) -> str:
    """The incidents.status CHECK constraint, stated up front: anything else
    used to reach the UPDATE and come back as a bare 500."""
    if status not in INCIDENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(INCIDENT_STATUSES)}")
    return status


@app.patch("/api/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, data: StatusUpdateSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    _valid_incident_status(data.status)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- confirm_dismiss_alerts
    # is a normal, common permission on both sides; without this, anyone who has it
    # could confirm/dismiss another jurisdiction's incident.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    try:
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
    except HTTPException:
        conn.close()
        raise
    cursor.execute("SELECT case_id, type, status, barangay_id FROM incidents WHERE id = ?", (incident_id,))
    before = cursor.fetchone()
    cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    updated = cursor.rowcount
    if updated and before:
        _record_detection_feedback(cursor, payload, incident_id, (data.status or "").lower())
        log_audit(cursor, payload, f"incident.{(data.status or '').lower() or 'status_changed'}", "incident", incident_id,
                  snapshot={"case_id": before["case_id"], "type": before["type"], "from": before["status"],
                            "to": data.status, "barangay_id": before["barangay_id"]})
    conn.commit()
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    await manager.broadcast({"channel": "incidents", "id": incident_id, "event": "status_updated", "status": data.status})
    return {"status": "updated", "id": incident_id, "new_status": data.status}

@app.delete("/api/incidents/{incident_id}")
async def delete_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"} | ADMIN_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- this used to
    # cascade to incident_reports on a hard DELETE, so without this check
    # any admin anywhere could destroy another jurisdiction's incident AND
    # any filed police report against it. Soft delete (2026-09-22, see
    # log_audit's own note) no longer cascades at all -- the incident row
    # (and any report tied to it) physically stays, just hidden -- but the
    # jurisdiction check still matters just as much: it's still a real
    # remove-this-from-my-view action, still shouldn't reach outside your
    # own scope.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("SELECT * FROM incidents WHERE id = ? AND deleted_at IS NULL", (incident_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    cursor.execute("UPDATE incidents SET deleted_at = NOW() WHERE id = ?", (incident_id,))
    log_audit(cursor, payload, "incident.deleted", "incident", incident_id, snapshot=dict(target))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.patch("/api/incidents/{incident_id}/archive")
async def archive_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # Hiding an incident from the map is a map action. It used to need no
    # permission at all, so an account that can't see the map could clear
    # incidents from it for everyone. See also _incident_owned_by's BUG
    # FOUND 2026-09-03 comment.
    try:
        require_permission(cursor, payload, "view_map")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "view_map")
    except HTTPException:
        conn.close()
        raise
    cursor.execute("UPDATE incident_visibility SET map_hidden = 1 WHERE incident_id = ?", (incident_id,))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "archived_from_map", "id": incident_id}

_REPORT_REQUIRED = ("reporting_officer", "badge_number", "narrative")


class ReportDraftSchema(BaseModel):
    report_body: dict


def _parse_report_row(r) -> dict:
    d = dict(r)
    for k in ("ai_draft", "report_body"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except (TypeError, ValueError):
            d[k] = None
    return d


def _upsert_incident_report(cursor, incident_id: str, payload: dict, body: dict, status: str,
                            ai_draft: Optional[dict] = None):
    """One open draft per incident: saving again (or confirming) updates it
    in place. Once confirmed, a later save starts a fresh draft -- an
    amendment -- so a filed report is never silently overwritten."""
    if ai_draft is None:
        ai_draft = build_ai_report_draft(cursor, incident_id)
    cols = {
        "reported_by": payload["id"],
        "narrative": body.get("narrative"),
        "nature_of_call": body.get("nature_of_incident"),
        "arrival_reason": body.get("action_taken"),
        "additional_officers": body.get("additional_officers"),
        "ai_draft": json.dumps(ai_draft) if ai_draft else None,
        "report_body": json.dumps(body),
        "report_status": status,
    }
    cursor.execute(
        "SELECT id FROM incident_reports WHERE incident_id = ? AND report_status = 'draft' ORDER BY created_at DESC LIMIT 1",
        (incident_id,),
    )
    open_draft = cursor.fetchone()
    if open_draft:
        cursor.execute(
            f"UPDATE incident_reports SET {', '.join(f'{k} = ?' for k in cols)}, updated_at = NOW() WHERE id = ?",
            (*cols.values(), open_draft["id"]),
        )
        return open_draft["id"]
    rid = str(uuid.uuid4())
    cursor.execute(
        f"INSERT INTO incident_reports (id, incident_id, {', '.join(cols)}, updated_at) "
        f"VALUES (?, ?, {', '.join('?' for _ in cols)}, NOW())",
        (rid, incident_id, *cols.values()),
    )
    return rid


def _require_report_reader(cursor, payload: dict, incident_id: str):
    """Officer reports hold names and narrative, so reading them takes the
    same access as the incident itself: in jurisdiction, and holding
    view_history or confirm_dismiss_alerts for this incident's crime type.
    404 either way, so an outsider can't probe which incident ids exist."""
    if not _incident_owned_by(cursor, incident_id, payload):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("SELECT type FROM incidents WHERE id = ?", (incident_id,))
    inc_type = (cursor.fetchone() or {"type": None})["type"]
    if not any(_holds_permission(cursor, payload, k) and _crime_type_allowed(cursor, payload, k, inc_type)
               for k in ("confirm_dismiss_alerts", "view_history")):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your access)")


@app.get("/api/incidents/{incident_id}/report_draft")
async def get_report_draft(incident_id: str, authorization: Optional[str] = Header(None)):
    """The AI's draft for this incident, plus the latest officer report
    (draft or confirmed) if one exists."""
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _require_report_reader(cursor, payload, incident_id)
        ai = build_ai_report_draft(cursor, incident_id)
        cursor.execute(
            """SELECT r.*, u.username AS reported_by_username
               FROM incident_reports r LEFT JOIN users u ON u.id = r.reported_by
               WHERE r.incident_id = ?
               ORDER BY CASE WHEN r.report_status = 'draft' THEN 0 ELSE 1 END,
                        COALESCE(r.updated_at, r.created_at) DESC LIMIT 1""",
            (incident_id,),
        )
        row = cursor.fetchone()
        return {"ai_draft": ai, "report": _parse_report_row(row) if row else None}
    finally:
        conn.close()


@app.put("/api/incidents/{incident_id}/report_draft")
async def save_report_draft(incident_id: str, data: ReportDraftSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "confirm_dismiss_alerts")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
        rid = _upsert_incident_report(cursor, incident_id, payload, data.report_body, "draft")
        conn.commit()
        return {"status": "draft_saved", "id": rid}
    finally:
        conn.close()


@app.post("/api/incidents/{incident_id}/confirm-and-report")
async def confirm_and_report(incident_id: str, data: ConfirmAndReportSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    _valid_incident_status(data.status)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- POLICE_SIDE_ROLES
    # only ruled out barangay accounts, not a PNP account from a DIFFERENT
    # station's jurisdiction.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    # BUG FOUND 2026-09-29: the filing modal sent camelCase keys
    # (reportingOfficer, badgeNumber, ...) while this read snake_case, so
    # every field an officer typed was silently discarded -- only the status
    # flip ever persisted. report_details is now the structured report body
    # (see _REPORT_REQUIRED) and is stored whole in report_body.
    details = data.report_details or {}
    try:
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
        # Re-typing an incident into a crime type this officer can't handle
        # would hand it off their own desk -- refuse rather than allow it.
        new_type = str(details.get("incident_type") or "").strip().upper()
        if new_type and not _crime_type_allowed(cursor, payload, "confirm_dismiss_alerts", new_type):
            raise HTTPException(status_code=403, detail=f"You can't file {new_type} incidents")
    except HTTPException:
        conn.close()
        raise
    missing = [k for k in _REPORT_REQUIRED if not str(details.get(k) or "").strip()]
    if missing:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Report is missing required fields: {', '.join(missing)}")
    officer = details["reporting_officer"].strip()
    # Snapshot the machine draft BEFORE applying the officer's corrections
    # below, so ai_draft records what the AI actually said.
    ai_draft = build_ai_report_draft(cursor, incident_id)

    # Before the UPDATE below, so ai_event records the model's own call.
    _record_detection_feedback(cursor, payload, incident_id, (data.status or "").lower(),
                               final_type=str(details.get("incident_type") or "").strip().upper() or None)
    sets, params = ["status = ?", "officer = ?"], [data.status, officer]
    corrected_type = str(details.get("incident_type") or "").strip().upper()
    if corrected_type:
        sets.append("type = ?")
        params.append(corrected_type)
    corrected_sev = str(details.get("severity") or "").strip().upper()
    if corrected_sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        sets.append("severity = ?")
        params.append(corrected_sev)
    cursor.execute(f"UPDATE incidents SET {', '.join(sets)} WHERE id = ?", (*params, incident_id))
    updated = cursor.rowcount
    if not updated:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")

    _upsert_incident_report(cursor, incident_id, payload, details, "confirmed", ai_draft)
    log_audit(cursor, payload, "report_confirmed", "incident", incident_id,
              {"reporting_officer": officer, "badge_number": details.get("badge_number")})
    # Re-fetch the row for the notification: the UPDATE above only touched
    # status/officer, and the message needs type/location/confidence/etc.,
    # which the request payload doesn't carry.
    cursor.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    incident_row = cursor.fetchone()
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "incidents", "id": incident_id, "event": "confirmed_and_reported"})

    # docs/incident_response_plan.md §2. Deliberately AFTER commit+close and
    # wrapped so a notification failure (bad credentials, network down,
    # whatever) can never turn a successful confirm-and-report into an error
    # response -- the incident is already confirmed at this point regardless
    # of what happens below.
    if incident_row is not None:
        try:
            notify_incident_targets(get_conn, dict(incident_row))
        except Exception as e:
            print(f"[notify] notify_incident_targets raised: {e}")

    return {"status": "confirmed_and_reported", "id": incident_id}

@app.get("/api/incidents/{incident_id}/reports")
async def list_incident_reports(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # Jurisdiction alone used to be enough here, so a PNP officer with no
    # permissions at all could read every report in the station's area.
    try:
        _require_report_reader(cursor, payload, incident_id)
    except HTTPException:
        conn.close()
        raise
    cursor.execute(
        """SELECT r.*, u.username AS reported_by_username
           FROM incident_reports r JOIN users u ON u.id = r.reported_by
           WHERE r.incident_id = ? ORDER BY r.created_at ASC""",
        (incident_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/incidents/{incident_id}/reports")
async def add_incident_report(incident_id: str, data: IncidentReportSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # Filing a report is acting on the alert: same gate as saving a draft.
    try:
        require_permission(cursor, payload, "confirm_dismiss_alerts")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
    except HTTPException:
        conn.close()
        raise
    report_id = str(uuid.uuid4())
    cursor.execute(
        """INSERT INTO incident_reports
           (id, incident_id, reported_by, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (report_id, incident_id, payload["id"], data.narrative, data.nature_of_call,
         data.arrival_reason, data.additional_officers),
    )
    conn.commit()
    conn.close()
    return {"status": "report_added", "id": report_id}

@app.post("/siren/activate")
async def siren_activate(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    conn.close()
    # BUG FOUND 2026-08-19: esp32.enabled was never checked here, so with NO
    # ESP32 on the network every Confirm/Dismiss click still fired a real HTTP
    # POST and blocked on a 2-second connect timeout before returning. (The
    # ESP32_IP expression at the top of this file reads `enabled`, but only as
    # part of an `and/or` chain that falls through to the same hardcoded
    # default either way -- so `enabled: false` disabled nothing at all.)
    if not ESP32_ENABLED:
        return {"status": "skipped", "detail": "esp32.enabled is false in config.json"}
    try:
        await asyncio.to_thread(requests.post, f"http://{ESP32_IP}/siren/on", timeout=2.0)
    except Exception as e:
        print(f"⚠️  [SIREN] ESP32 unreachable at {ESP32_IP}: {e}")
    return {"status": "activate_sent"}

@app.post("/siren/deactivate")
async def siren_deactivate(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    conn.close()
    # See siren_activate above for why this check exists.
    if not ESP32_ENABLED:
        return {"status": "skipped", "detail": "esp32.enabled is false in config.json"}
    try:
        await asyncio.to_thread(requests.post, f"http://{ESP32_IP}/siren/off", timeout=2.0)
    except Exception as e:
        print(f"⚠️  [SIREN] ESP32 unreachable at {ESP32_IP}: {e}")
    return {"status": "deactivate_sent"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Browsers can't set headers on a WebSocket, so the dashboard passes its
    # session token as ?token=. Same check as every HTTP endpoint.
    try:
        payload = require_auth(f"Bearer {token}" if token else None)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await manager.connect(websocket, payload)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- VIDEO RECS MODULES ---
@app.get("/api/records")
async def get_video_records(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "view_records")
    # Was completely unscoped -- any authenticated user, from any barangay,
    # got every recording in the system including other barangays' footage.
    # video_records.barangay_id can be NULL for older/manual rows, so those
    # stay visible only to DEVTEAM (whose scope clause is empty).
    sql, params = apply_scope(payload, "SELECT * FROM video_records", [])
    sql += " ORDER BY recorded_at DESC"
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall()
    rows = _filter_records_by_crime_type(cursor, payload, rows)
    records = _label_records(cursor, [_row_to_record_dict(r) for r in rows])
    conn.close()
    return records


def _label_records(cursor, records: list) -> list:
    """Adds what a person reads in place of the file name: a clip of an
    incident is "<Type> <n>" -- "Assault 1", "Assault 2" -- numbered in
    recording order across ALL of that incident's clips (not just the ones
    this caller can see, so the numbers match the report). Footage with no
    incident keeps its file name."""
    inc_ids = list({r["associated_incident_id"] for r in records if r.get("associated_incident_id")})
    if not inc_ids:
        return [{**r, "label": None, "incident_type": None} for r in records]
    ph = ",".join("?" for _ in inc_ids)
    cursor.execute(f"SELECT id, type, case_id FROM incidents WHERE id IN ({ph})", tuple(inc_ids))
    incs = {r["id"]: dict(r) for r in cursor.fetchall()}
    cursor.execute(f"SELECT id, associated_incident_id FROM video_records WHERE associated_incident_id IN ({ph}) "
                   "ORDER BY recorded_at, id", tuple(inc_ids))
    position: dict = {}
    counters: dict = {}
    for r in cursor.fetchall():
        n = counters[r["associated_incident_id"]] = counters.get(r["associated_incident_id"], 0) + 1
        position[r["id"]] = n
    out = []
    for r in records:
        inc = incs.get(r.get("associated_incident_id"))
        if inc and r["id"] in position:
            out.append({**r, "label": f"{incident_type_title(inc['type'])} {position[r['id']]}",
                        "incident_type": (inc["type"] or "").upper(), "case_id": inc.get("case_id")})
        else:
            out.append({**r, "label": None, "incident_type": (inc or {}).get("type")})
    return out


def _filter_records_by_crime_type(cursor, payload: dict, rows: list) -> list:
    """Drops recordings whose incident type (or NO_INCIDENT for footage
    with none) is outside the caller's view_records dicing."""
    allowed = _scoped_resource_ids(cursor, payload, "view_records", "crime_type")
    if allowed is None or not rows:
        return rows
    inc_ids = list({r["associated_incident_id"] for r in rows if r["associated_incident_id"]})
    types: dict = {}
    if inc_ids:
        placeholders = ",".join("?" for _ in inc_ids)
        cursor.execute(f"SELECT id, type FROM incidents WHERE id IN ({placeholders})", tuple(inc_ids))
        types = {r["id"]: (r["type"] or "").strip().upper() for r in cursor.fetchall()}
    return [r for r in rows
            if (types.get(r["associated_incident_id"]) if r["associated_incident_id"] else NO_INCIDENT) in allowed]

def _ffmpeg_exe():
    """Same resolution order as maincode/main.py: bundled binary first, system
    ffmpeg only as a fallback. Never assume a system install exists -- a
    packaged deployment has no way to guarantee one."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def _parse_timecode(value: str) -> float:
    """Accepts 'SS', 'MM:SS' or 'HH:MM:SS' and returns seconds.

    The Recordings UI sends MM:SS from its scrub fields; being liberal here
    means a user typing '90' or '00:01:30' gets what they expect instead of a
    validation error on an evidence tool.
    """
    parts = str(value).strip().split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad timecode: {value!r}")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise HTTPException(status_code=400, detail=f"Bad timecode: {value!r}")


class ExtractRangeSchema(BaseModel):
    start: str = "00:00"
    end: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/records/{record_id}/extract")
async def extract_record_segment(record_id: str, data: ExtractRangeSchema,
                                 authorization: Optional[str] = Header(None)):
    """Cuts a real sub-clip out of an existing recording.

    BUG FOUND 2026-08-19: the Recordings tab's "Extract Segment" button
    previously just POSTed to /api/records/register_clip with a made-up
    filename (`EXTRACT_<timestamp>_<original>.mp4`) and NEVER CUT ANY VIDEO.
    It created a database row pointing at a file that does not exist, so the
    extracted "clip" appeared in the archive and then failed to play, forever.
    This does the actual trim.
    """
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "view_records")

    sql, params = apply_scope(payload, "SELECT * FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
    if row and not _filter_records_by_crime_type(cursor, payload, [row]):
        row = None
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")

    src = row["file_path"]
    if not os.path.exists(src):
        conn.close()
        raise HTTPException(status_code=404, detail=f"Source file is missing from disk: {os.path.basename(src)}")

    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        conn.close()
        raise HTTPException(status_code=503, detail="ffmpeg unavailable -- cannot cut a segment. `pip install imageio-ffmpeg`.")

    start_s = _parse_timecode(data.start)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", str(start_s), "-i", src]
    if data.end:
        end_s = _parse_timecode(data.end)
        if end_s <= start_s:
            conn.close()
            raise HTTPException(status_code=400, detail="End must be after start.")
        cmd += ["-t", str(end_s - start_s)]
        duration_label = f"{end_s - start_s:.1f}s"
    else:
        duration_label = "to end"
    # Re-encode rather than stream-copy: a copy can only cut on keyframes, so
    # the clip would silently start seconds away from the requested mark --
    # unacceptable when the whole point is isolating a moment of evidence.
    out_name = f"EXTRACT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(src)}"
    out_path = os.path.join(RECORDINGS_DIR, out_name)
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", out_path]

    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(out_path):
        conn.close()
        raise HTTPException(status_code=500,
                            detail=f"Extraction failed: {proc.stderr.decode('utf-8','replace')[:200]}")

    rid = str(uuid.uuid4())
    cursor.execute(
        """INSERT INTO video_records
           (id, filename, file_path, recorded_at, duration, type, associated_incident_id,
            crime_time_marker, notes, barangay_id, sha256)
           VALUES (?, ?, ?, ?, ?, 'CLIP', ?, ?, ?, ?, ?)""",
        (rid, out_name, out_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         duration_label, row["associated_incident_id"], data.start,
         data.notes or f"Segment {data.start}–{data.end or 'end'} extracted from {row['filename']}.",
         row["barangay_id"], sha256_of_file(out_path)),
    )
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "records", "event": "clip_extracted", "id": rid})
    return {"status": "extracted", "id": rid, "filename": out_name}


@app.delete("/api/records/{record_id}")
async def delete_record(record_id: str, authorization: Optional[str] = Header(None)):
    """Removes a recording and its file.

    Restricted to the police admin tier/DEVTEAM rather than anyone with
    view_records: this destroys evidence, which is a materially different
    action from watching it. BARANGAY_ADMIN used to pass too, although the
    vault is police-only (POLICE_ONLY_PERMISSIONS) and they can't open it.
    Scoped, so an admin cannot delete footage outside their jurisdiction.
    """
    payload = require_auth(authorization)
    require_role(payload, {"PNP_ADMIN", "DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()

    sql, params = apply_scope(payload, "SELECT * FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
    if row and not _filter_records_by_crime_type(cursor, payload, [row]):
        row = None
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")

    # Chain of custody (docs/incident_response_plan.md §3): a clip tied to an
    # incident that already has a filed report is evidence, not routine
    # footage -- never deletable through this normal-use endpoint, regardless
    # of role. (The maintenance retention sweep obeys the identical rule --
    # see app/maintenance.py -- so this isn't a new restriction, just this
    # endpoint catching up to the same one.)
    if row["associated_incident_id"]:
        cursor.execute(
            "SELECT 1 FROM incident_reports WHERE incident_id = ? LIMIT 1",
            (row["associated_incident_id"],),
        )
        if cursor.fetchone():
            conn.close()
            raise HTTPException(
                status_code=409,
                detail="This clip is evidence for a reported incident and cannot be deleted here.",
            )

    file_removed = False
    try:
        if row["file_path"] and os.path.exists(row["file_path"]):
            os.remove(row["file_path"])
            file_removed = True
    except OSError as e:
        # Row still goes, so the archive doesn't keep listing something the
        # user asked to be gone -- but say plainly that the file survived.
        print(f"⚠️  [RECORDS] Could not delete {row['file_path']}: {e}")

    cursor.execute("DELETE FROM video_records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "records", "event": "clip_deleted", "id": record_id})
    return {"status": "deleted", "id": record_id, "file_removed": file_removed}


@app.post("/api/records/register_clip")
async def register_clip(data: ManualClipSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # Adding to the evidence vault used to need only a login.
    try:
        require_permission(cursor, payload, "view_records")
        if data.associated_incident_id and not _incident_owned_by(cursor, data.associated_incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    except HTTPException:
        conn.close()
        raise
    rid = str(uuid.uuid4())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fpath = _safe_recordings_path(data.filename)
    # BUG FOUND 2026-08-19: same missing barangay_id as /api/ai_register_clip
    # below -- see that endpoint's comment. An operator manually extracting a
    # segment is scoped to their own barangay; a PNP account's token carries
    # no barangay_id at all (they use station_id instead), so this falls
    # back to the linked incident's barangay_id in that case, same as the
    # AI-triggered path.
    clip_barangay_id = payload.get("barangay_id")
    if not clip_barangay_id and data.associated_incident_id:
        cursor.execute("SELECT barangay_id FROM incidents WHERE id = ?", (data.associated_incident_id,))
        row = cursor.fetchone()
        if row:
            clip_barangay_id = row["barangay_id"]
    try:
        cursor.execute(
            """INSERT INTO video_records
               (id, filename, file_path, recorded_at, duration, type, associated_incident_id, crime_time_marker, notes, barangay_id, sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.filename, fpath, now_str, data.duration, data.type,
             data.associated_incident_id or None, data.crime_time_marker, data.notes, clip_barangay_id,
             sha256_of_file(fpath)),
        )
        conn.commit()
        return {"status": "registered", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Metadata write collision: {e}")
    finally:
        conn.close()

@app.post("/api/ai_register_clip")
async def ai_register_clip(data: ManualClipSchema, request: Request):
    """Auto-captured event clips from the AI pipeline (main.py on 8001).

    Deliberately NOT behind require_auth, for the same reason /api/ai_trigger
    isn't: the caller is a local service process with no user session, not a
    browser. This is the second half of the ai_trigger flow -- ai_trigger
    creates the incident, this attaches the MP4 once encoding finishes.

    Kept separate from /api/records/register_clip (which stays authenticated)
    so the operator-facing manual "Extract Segment" path doesn't lose its
    session check just to accommodate a machine caller. Same caveat as
    ai_trigger: give it a service credential if this backend is ever exposed
    beyond localhost.
    """
    _require_local_service(request)
    conn = get_conn()
    cursor = conn.cursor()
    rid = str(uuid.uuid4())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fpath = _safe_recordings_path(data.filename)
    # BUG FOUND 2026-08-19: this never set barangay_id, so every AI-triggered
    # clip landed with it NULL. apply_scope() (used by GET /api/records)
    # restricts every non-DEVTEAM role to "LOWER(barangay_id) = ?" -- NULL
    # never equals a string in SQL, so every one of these clips was invisible
    # to every barangay/PNP account regardless of jurisdiction, while the
    # backend kept truthfully reporting "200 registered" the whole time.
    # Resolved from the linked incident (which does carry a real
    # barangay_id) rather than hardcoding one.
    clip_barangay_id = None
    if data.associated_incident_id:
        cursor.execute("SELECT barangay_id FROM incidents WHERE id = ?", (data.associated_incident_id,))
        row = cursor.fetchone()
        if row:
            clip_barangay_id = row["barangay_id"]
    try:
        cursor.execute(
            """INSERT INTO video_records
               (id, filename, file_path, recorded_at, duration, type, associated_incident_id, crime_time_marker, notes, barangay_id, sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.filename, fpath, now_str, data.duration, data.type,
             data.associated_incident_id or None, data.crime_time_marker, data.notes, clip_barangay_id,
             sha256_of_file(fpath)),
        )
        conn.commit()
        # RecordsView subscribes via useLiveChannel("*"), so pushing this
        # means an auto-captured clip appears in the archive immediately
        # instead of on the next 60s fallback poll.
        await manager.broadcast({
            "channel": "records", "event": "clip_registered",
            "id": rid, "incident_id": data.associated_incident_id,
        })
        return {"status": "registered", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Metadata write collision: {e}")
    finally:
        conn.close()

@app.patch("/api/records/{record_id}/notes")
async def update_record_notes(record_id: str, data: RecordNotesSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # BUG FOUND 2026-09-03: this checked only that SOME user was logged in --
    # no role, no permission, and (same shape as the incident-endpoint bugs
    # fixed above and the camera CRUD bug fixed earlier today) no ownership
    # check at all. Any authenticated account, of any role in any barangay or
    # station, could rewrite the evidence notes on any recording anywhere in
    # the system. Scoped the same way GET /api/records and the extract
    # endpoint already are, via apply_scope(). The scope alone still let any
    # account in the area (barangay ones included, who can't open the
    # police-only vault) edit notes; 2026-10-01 it takes view_records too.
    try:
        require_permission(cursor, payload, "view_records")
    except HTTPException:
        conn.close()
        raise
    sql, params = apply_scope(payload, "SELECT id, associated_incident_id FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    found = cursor.fetchone()
    if not found or not _filter_records_by_crime_type(cursor, payload, [found]):
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")
    cursor.execute("UPDATE video_records SET notes = ? WHERE id = ?", (data.notes, record_id))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"status": "updated", "id": record_id}

# --- AUTH SECTOR CORES ---
@app.get("/api/stations")
async def list_stations_public():
    """id + name only, for the signup form's station picker.

    Unauthenticated because signup is. Deliberately narrower than
    /api/devteam/stations: no jurisdiction and no staff counts, so this
    leaks nothing beyond the names of police stations, which are public
    knowledge anyway.
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM police_stations ORDER BY name")
    rows = [{"id": r["id"], "name": r["name"]} for r in cursor.fetchall()]
    conn.close()
    return rows


@app.post("/api/signup")
@limiter.limit("10/minute")
async def signup(request: Request, user: UserSignup):
    role = user.role.upper()
    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only Barangay Admin / PNP Admin accounts can self-register. "
                   "Operator accounts must be created by your admin.",
        )

    is_pnp = role in PNP_SIDE_ROLES
    # Same id rule as the Stations tab's "Add barangay" (_slugify_barangay):
    # signup used the raw lowercased text, so "New Haven" became "new haven"
    # here but "new-haven" there -- one barangay, two rows, and a rejected
    # one could be applied for again under the other spelling.
    import re as _re
    barangay_name = _re.sub(r"^(brgy\.?|barangay)\s+", "", (user.barangay_id or "").strip(), flags=_re.IGNORECASE)
    barangay_id = _slugify_barangay(barangay_name) if barangay_name else ""
    station_id = (user.station_id or "").strip().lower()

    if is_pnp and not station_id:
        raise HTTPException(status_code=400, detail="A police station is required")
    if not is_pnp and not barangay_id:
        raise HTTPException(status_code=400, detail="Location is required")
    profile = _clean_profile(user, required=("full_name", "birthdate", "home_address", "position"))

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if is_pnp:
            # A barangay can be created on the fly as 'pending' because
            # DevTeam approval is the gate. A STATION cannot: its whole
            # purpose is the jurisdiction DevTeam assigns it, so a
            # self-created one would be an empty shell that sees nothing.
            # PNP admins therefore join a station DevTeam already made.
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail="That police station does not exist yet. Ask DevTeam to "
                           "register the station and set its jurisdiction first.")
            barangay_id = ""
        else:
            station_id = ""
            # An existing barangay is matched under its stored id first -- rows made
            # by the old rule keep raw ids (spaces, underscores) -- and only a new
            # one gets the slug.
            legacy_id = (user.barangay_id or "").strip().lower()
            cursor.execute("SELECT id FROM barangays WHERE id = ?", (legacy_id,))
            legacy = cursor.fetchone()
            if legacy:
                barangay_id = legacy["id"]
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            claimed = cursor.fetchone()
            if claimed and claimed["status"] == "rejected":
                conn.close()
                raise HTTPException(status_code=403, detail="This barangay's registration was declined. Contact DevTeam if you believe that's wrong.")
            new_barangay = not claimed
            if not claimed:
                cursor.execute(
                    "INSERT INTO barangays (id, name, status) VALUES (?, ?, 'pending')",
                    (barangay_id, barangay_name.title()),
                )

        # One admin per org unit, matching the unique indexes. deleted_at IS
        # NULL added 2026-09-22 -- a soft-deleted admin's slot must free up
        # for a new signup, same as it did when delete meant delete.
        if is_pnp:
            cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = ? AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (station_id, role))
            dup_msg = "This station already has a PNP Admin account."
        else:
            cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = ? AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (barangay_id, role))
            dup_msg = "This location already has a Barangay Admin account."
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=dup_msg)

        # signup_status='pending' unconditionally -- every self-signup admin
        # account needs an explicit DevTeam decision on THIS account, not on
        # the location/station it's attached to (see login()'s 2026-09-23
        # fix comment for the bug this closes). Every other account-creation
        # path (devteam_create_user, create_my_user) leaves this column at
        # its 'approved' default, so nothing about those changes.
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, signup_status, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', {', '.join('?' for _ in PROFILE_FIELDS)})",
            (user.username, hash_password(user.password), role,
             barangay_id or None, station_id or None, user.assignment,
             *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_user_id = cursor.lastrowid
        # The applicant is the actor: there is no session yet, and the
        # catch-all entry would only say "anonymous" with no target.
        log_audit(cursor, {"id": new_user_id, "username": user.username}, "user.signup_submitted", "user", new_user_id,
                  snapshot={"role": role, "station_id": station_id or None, "barangay_id": barangay_id or None,
                            "new_barangay": (not is_pnp) and new_barangay, "full_name": profile.get("full_name"),
                            "position": profile.get("position")})

        if is_pnp:
            conn.commit()
            # id included (2026-09-23, #9) so the signup form can offer the
            # ID-upload step via /api/signup/{id}/verification -- this
            # account can't log in yet to reach the authenticated upload
            # endpoint instead.
            return {"status": "pending_approval", "id": new_user_id,
                    "detail": "Account created. A DevTeam administrator must confirm your identity before you can log in."}

        # requested_by also gets reclaimed when the current holder was since
        # soft-deleted -- otherwise a second applicant for a barangay whose
        # original admin was removed would leave requested_by (and so
        # approve_location's target) pointed at the old, gone account
        # instead of this new applicant.
        cursor.execute(
            "UPDATE barangays SET requested_by = ? WHERE id = ? AND "
            "(requested_by IS NULL OR requested_by IN (SELECT id FROM users WHERE deleted_at IS NOT NULL))",
            (new_user_id, barangay_id),
        )
        conn.commit()

        # Always pending now, regardless of whether the LOCATION itself was
        # already 'approved' from an earlier admin -- that only means the
        # place was vetted once, not that this new applicant is who they
        # say they are.
        return {"status": "pending_approval", "id": new_user_id,
                "detail": "Account created. A DevTeam administrator must approve this location before you can log in."}
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Operator profile already mapped.")
    finally:
        conn.close()

# Identity verification for a self-signup admin applicant (#9). A
# just-signed-up BARANGAY_ADMIN can't log in until DevTeam approves their
# barangay (see signup() above), so there's no token to authenticate this
# upload with -- the applicant's own just-created username+password IS the
# proof of identity instead, checked directly against the row this same
# signup call created. Narrower than the authenticated .../me/verification
# endpoint below on purpose: only reachable for a still-unverified admin
# applicant, not a general "upload anyone's ID if you know their password"
# tool.
@app.post("/api/signup/{user_id}/verification")
@limiter.limit("5/minute")
async def upload_signup_verification(
    request: Request, user_id: int,
    username: str = Form(...), password: str = Form(...),
    id_document: Optional[UploadFile] = File(None),
    face_photo: Optional[UploadFile] = File(None),
):
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        row = cursor.fetchone()
        if not row or row["username"] != username or not verify_password(password, row["password"]):
            raise HTTPException(status_code=403, detail="Could not verify those credentials")
        row = dict(row)
        if row["role"] not in ADMIN_ROLES:
            raise HTTPException(status_code=400, detail="This upload is for self-signup admin applications only")
        if id_document is not None:
            filename = _save_verification_document(user_id, id_document)
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending' WHERE id = ?",
                (filename, user_id),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(user_id, face_photo, kind="face"), user_id))
        log_audit(cursor, {"id": user_id, "username": username}, "user.verification_submitted", "user", str(user_id))
        conn.commit()
        return {"status": "submitted"}
    finally:
        conn.close()

@app.post("/api/login")
@limiter.limit("5/minute")
async def login(request: Request, creds: UserLogin):
    conn = get_conn()
    cursor = conn.cursor()
    # deleted_at IS NULL added 2026-09-22 -- a soft-deleted account must not
    # be able to log in and mint a fresh, fully-valid token for itself.
    # Deliberately still returns the generic "Invalid Credentials" (not
    # "this account was deleted") -- same reasoning as a wrong password:
    # don't tell an unauthenticated caller which usernames exist/existed.
    cursor.execute("SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (creds.username,))
    row = cursor.fetchone()
    if not row or not verify_password(creds.password, row["password"]):
        # Failed sign-ins are recorded against the username tried (known or
        # not) so repeated guessing at an account shows up in the log.
        log_audit(cursor, {"id": row["id"] if row else None, "username": creds.username}, "user.login_failed",
                  "user", str(row["id"]) if row else "-",
                  snapshot={"ip": request.client.host if request.client else None})
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid Credentials")

    user_dict = dict(row)

    # BUG FOUND 2026-09-23 (user report: "sign up approvals doesn't work,
    # the devteam can't accept approvals, it just confirms it right away"):
    # a self-signup PNP_ADMIN had NO approval gate at all -- signup()'s own
    # comment said "No location-approval gate for PNP: the station already
    # exists, which means DevTeam already vetted it," but vetting the
    # STATION is not vetting the PERSON claiming to run it. Station ids are
    # readable by anyone (GET /api/stations is unauthenticated, by design,
    # for the signup form's picker), so anyone who typed one in got a fully
    # working admin login instantly, with DevTeam never in the loop -- there
    # was nothing in the Approvals tab to even click. A second, narrower gap
    # existed on the barangay side too: signing up for a barangay_id that
    # was ALREADY 'approved' (e.g. its original admin was later removed)
    # skipped review for the NEW applicant, since the check only ever
    # looked at the location's status, never this specific account's.
    # signup_status closes both: every self-signup admin account is
    # stamped 'pending' at creation (see signup()), independently of
    # whether the location/station itself is already vetted, and only
    # DevTeam approving THIS account (not the location) lifts it.
    if user_dict["role"] in ADMIN_ROLES and user_dict.get("signup_status") == "pending":
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="Your application is still pending DevTeam approval. Please check back later.",
        )
    if user_dict["role"] in ADMIN_ROLES and user_dict.get("signup_status") == "rejected":
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="Your application was not approved. Contact DevTeam for details.",
        )

    if user_dict["role"] != "DEVTEAM" and user_dict.get("barangay_id"):
        cursor.execute("SELECT status FROM barangays WHERE id = ?", (user_dict["barangay_id"],))
        loc = cursor.fetchone()
        if not loc or loc["status"] != "approved":
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="Your barangay's registration was not approved. Contact DevTeam for details."
                       if loc and loc["status"] == "rejected" else
                       "Your location is still pending DevTeam approval. Please check back later.",
            )

    # Stamped here, not on every authenticated request -- last_login answers
    # "when did this account last sign in", not "is a request in flight
    # right now" (there's no session/heartbeat concept in this app to answer
    # that second question honestly, so the Users list doesn't claim to).
    cursor.execute("UPDATE users SET last_login = NOW() WHERE id = ?", (user_dict["id"],))
    log_audit(cursor, user_dict, "user.login", "user", str(user_dict["id"]),
              snapshot={"ip": request.client.host if request.client else None})
    conn.commit()

    token = issue_token(user_dict)
    response_user = _row_to_user_dict(cursor, row)
    conn.close()
    return {"status": "success", "user": response_user, "token": token}

@app.post("/api/logout")
async def logout():
    return {"status": "logged_out"}

@app.get("/api/me")
async def get_me(authorization: Optional[str] = Header(None)):
    """The signed-in account as it is now -- the same shape /api/login
    returns. The dashboard re-reads this when an account changes, so a
    permission granted or revoked shows in the sidebar without logging out
    (the backend already enforced the change on the next request)."""
    payload = require_auth(authorization)
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (payload["id"],))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Account no longer exists")
        return {"user": _row_to_user_dict(cursor, row)}
    finally:
        conn.close()

@app.get("/api/me/can/{permission_key}")
async def me_can(permission_key: str, authorization: Optional[str] = Header(None)):
    """Whether the signed-in account holds a permission. For the AI core,
    which has no user store of its own: it forwards the dashboard's
    Authorization header here before switching what the detector watches."""
    payload = require_auth(authorization)
    if permission_key not in VALID_PERMISSION_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown permission '{permission_key}'")
    conn = get_conn()
    try:
        return {"allowed": _holds_permission(conn.cursor(), payload, permission_key)}
    finally:
        conn.close()


# --- DEVTEAM: POLICE STATIONS & JURISDICTIONS ---
# A station is an organizational unit that COVERS barangays. It owns no
# cameras, incidents or recordings -- station_barangays is purely a
# visibility lens (see docs/USER_HIERARCHY_PLAN.md). Editing a jurisdiction
# therefore changes only who can see what; it never moves an asset.

STATION_DETAIL_FIELDS = ("station_type", "parent_office", "regional_office", "commander",
                         "address", "contact_number", "description")
BARANGAY_DETAIL_FIELDS = ("psgc_code", "city_municipality", "province", "region", "captain_name",
                          "hall_address", "contact_number", "description")


MIN_REGISTRATION_REASON = 20


def _require_registration_authority(cursor, payload: dict, reason: Optional[str], confirm_password: Optional[str]) -> str:
    """Registering a station or barangay changes who can see which cameras
    and incidents, so it needs a written justification (kept in the audit
    log) and a fresh password re-entry from the DevTeam account doing it --
    a stolen session token alone can't mint new jurisdictions."""
    reason = (reason or "").strip()
    if len(reason) < MIN_REGISTRATION_REASON:
        raise HTTPException(status_code=400, detail=f"Give a reason for this registration (at least {MIN_REGISTRATION_REASON} characters)")
    cursor.execute("SELECT password FROM users WHERE id = ?", (payload["id"],))
    caller = cursor.fetchone()
    if not caller or not verify_password(confirm_password or "", caller["password"]):
        raise HTTPException(status_code=403, detail="Incorrect DevTeam password.")
    return reason


class StationSchema(BaseModel):
    id: Optional[str] = None
    name: str
    reason: Optional[str] = None
    confirm_password: Optional[str] = None
    station_type: Optional[str] = None
    parent_office: Optional[str] = None
    regional_office: Optional[str] = None
    commander: Optional[str] = None
    address: Optional[str] = None
    contact_number: Optional[str] = None
    description: Optional[str] = None

class StationJurisdictionSchema(BaseModel):
    barangay_ids: List[str]

class StationBarangayCreate(BaseModel):
    name: str
    reason: Optional[str] = None
    confirm_password: Optional[str] = None
    barangay_id: Optional[str] = None
    psgc_code: Optional[str] = None
    city_municipality: Optional[str] = None
    province: Optional[str] = None
    region: Optional[str] = None
    captain_name: Optional[str] = None
    hall_address: Optional[str] = None
    contact_number: Optional[str] = None
    description: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


def _clean_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _slugify_barangay(name: str) -> str:
    import re
    s = re.sub(r"^(brgy\.?|barangay)\s+", "", name.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


@app.get("/api/devteam/stations")
async def list_stations(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(f"SELECT id, name, {', '.join(STATION_DETAIL_FIELDS)} FROM police_stations ORDER BY name")
    stations = [dict(r) for r in cursor.fetchall()]

    # Batched, not one query per station.
    cursor.execute("SELECT station_id, barangay_id FROM station_barangays")
    juris: dict = {}
    for r in cursor.fetchall():
        juris.setdefault(r["station_id"], []).append(r["barangay_id"])

    cursor.execute(
        "SELECT station_id, COUNT(*) AS n FROM users WHERE station_id IS NOT NULL GROUP BY station_id")
    staff = {r["station_id"]: r["n"] for r in cursor.fetchall()}

    conn.close()
    for s in stations:
        s["barangay_ids"] = sorted(juris.get(s["id"], []))
        s["staff_count"] = staff.get(s["id"], 0)
    return stations


@app.post("/api/devteam/stations")
async def create_station(data: StationSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    sid = (data.id or f"station-{uuid.uuid4().hex[:8]}").strip().lower()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if not _clean_optional(data.station_type):
            raise HTTPException(status_code=400, detail="Unit type is required")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        cursor.execute("SELECT 1 FROM police_stations WHERE LOWER(name) = LOWER(?)", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A station named "{name}" already exists')
        details = [_clean_optional(getattr(data, f)) for f in STATION_DETAIL_FIELDS]
        cursor.execute(
            f"INSERT INTO police_stations (id, name, {', '.join(STATION_DETAIL_FIELDS)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in STATION_DETAIL_FIELDS)})",
            (sid, name, *details),
        )
        log_audit(cursor, payload, "station.created", "station", sid,
                  snapshot={"name": name, "reason": reason, **dict(zip(STATION_DETAIL_FIELDS, details))})
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not create station: {e}")
    finally:
        conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_created", "id": sid})
    return {"status": "created", "id": sid, "name": name}


@app.patch("/api/devteam/stations/{station_id}")
async def update_station(station_id: str, data: StationSchema, authorization: Optional[str] = Header(None)):
    """Edits a station's name and record. Same reason + password bar as
    registering one, and the audit entry keeps a before/after of every
    field that changed."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT id, name, {', '.join(STATION_DETAIL_FIELDS)} FROM police_stations WHERE id = ?", (station_id,))
        before = cursor.fetchone()
        if not before:
            raise HTTPException(status_code=404, detail="Station not found")
        before = dict(before)
        if not _clean_optional(data.station_type):
            raise HTTPException(status_code=400, detail="Unit type is required")
        cursor.execute("SELECT 1 FROM police_stations WHERE LOWER(name) = LOWER(?) AND id <> ?", (name, station_id))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A station named "{name}" already exists')
        after = {"name": name, **{f: _clean_optional(getattr(data, f)) for f in STATION_DETAIL_FIELDS}}
        changes = {k: {"from": before.get(k), "to": v} for k, v in after.items() if (before.get(k) or None) != v}
        if not changes:
            return {"status": "unchanged", "id": station_id}
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        cursor.execute(
            f"UPDATE police_stations SET {', '.join(f'{k} = ?' for k in after)} WHERE id = ?",
            (*after.values(), station_id),
        )
        log_audit(cursor, payload, "station.updated", "station", station_id,
                  snapshot={"name": name, "reason": reason, "changes": changes})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_updated", "id": station_id})
    return {"status": "updated", "id": station_id, "changed": sorted(changes)}


@app.put("/api/devteam/stations/{station_id}/jurisdiction")
async def set_station_jurisdiction(station_id: str, data: StationJurisdictionSchema,
                                   authorization: Optional[str] = Header(None)):
    """Replaces a station's jurisdiction wholesale. Idempotent, and safe to
    shrink: removing a barangay only removes visibility, it never deletes
    that barangay's cameras/incidents, because nothing hangs off the
    station."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Station not found")

    wanted = [b.strip().lower() for b in data.barangay_ids if b and b.strip()]
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        cursor.execute(f"SELECT id, name, status FROM barangays WHERE LOWER(id) IN ({placeholders})", tuple(wanted))
        known = {r["id"].lower(): dict(r) for r in cursor.fetchall()}
        unknown = [b for b in wanted if b not in known]
        if unknown:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown barangay ids: {', '.join(unknown)}")
        # Only an approved barangay can be covered. A pending one linked
        # before this rule existed may stay until it's decided; a rejected
        # one never (rejecting unlinks it everywhere).
        cursor.execute("SELECT barangay_id FROM station_barangays WHERE station_id = ?", (station_id,))
        linked = {r["barangay_id"].lower() for r in cursor.fetchall()}
        refused = [f"{known[b]['name']} ({known[b]['status']})" for b in wanted
                   if known[b]["status"] == "rejected" or (known[b]["status"] == "pending" and b not in linked)]
        if refused:
            conn.close()
            raise HTTPException(status_code=409, detail=f"Only approved barangays can be in a jurisdiction: {', '.join(refused)}")

    try:
        cursor.execute("SELECT barangay_id FROM station_barangays WHERE station_id = ?", (station_id,))
        juris_before = sorted(r["barangay_id"] for r in cursor.fetchall())
        cursor.execute("DELETE FROM station_barangays WHERE station_id = ?", (station_id,))
        log_audit(cursor, payload, "station.jurisdiction_updated", "station", station_id,
                  snapshot={"from": juris_before, "to": sorted(wanted)})
        for b in wanted:
            cursor.execute(
                "INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, b))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not update jurisdiction: {e}")
    finally:
        conn.close()

    await manager.broadcast({"channel": "stations", "event": "jurisdiction_updated", "id": station_id})
    return {"status": "updated", "id": station_id, "barangay_ids": wanted}


# 2026-09-29 user request: registering a NEW barangay used to happen inside
# Create User (typing a fresh barangay_id there silently created it) -- a
# barangay made that way had no station connection at all until DevTeam
# separately remembered to visit this tab and check it into a jurisdiction
# (see devteam_create_user's own 2026-09-24 fix for the bug that caused).
# Moved here instead: a station's own tab is where a NEW barangay is
# registered now, and it's assigned to THIS station's jurisdiction in the
# same step -- there's no longer a path that creates a barangay with no
# station at all. devteam_create_user's barangay branch now REJECTS an
# unknown barangay_id rather than silently making one.
@app.post("/api/devteam/stations/{station_id}/barangays")
async def create_barangay_for_station(station_id: str, body: StationBarangayCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Station not found")

        import re
        # Stored without a "Brgy."/"Barangay" prefix -- display sites add it.
        name = re.sub(r"^(brgy\.?|barangay)\s+", "", body.name.strip(), flags=re.IGNORECASE)
        if not name:
            raise HTTPException(status_code=400, detail="Barangay name is required")
        barangay_id = _slugify_barangay(body.barangay_id or name)
        if not barangay_id:
            raise HTTPException(status_code=400, detail="Barangay name must contain letters or digits")

        psgc = _clean_optional(body.psgc_code)
        if psgc is not None:
            psgc = psgc.replace(" ", "")
            if not psgc.isdigit() or len(psgc) not in (9, 10):
                raise HTTPException(status_code=400, detail="PSGC code must be 10 digits (or the older 9-digit form)")
        details = {f: _clean_optional(getattr(body, f)) for f in BARANGAY_DETAIL_FIELDS}
        details["psgc_code"] = psgc
        if not details["city_municipality"]:
            raise HTTPException(status_code=400, detail="City / municipality is required")
        reason = _require_registration_authority(cursor, payload, body.reason, body.confirm_password)
        if psgc:
            cursor.execute("SELECT name FROM barangays WHERE psgc_code = ? AND id <> ?", (psgc, barangay_id))
            clash = cursor.fetchone()
            if clash:
                raise HTTPException(status_code=409, detail=f'PSGC code {psgc} is already registered to Barangay {clash["name"]}')

        cursor.execute("SELECT status FROM barangays WHERE id = ?", (barangay_id,))
        existing = cursor.fetchone()
        if existing:
            if existing["status"] == "approved":
                raise HTTPException(
                    status_code=409,
                    detail=f'Barangay "{name}" is already registered -- tick it in this station\'s jurisdiction list instead.')
            # Registering over an application used to approve it silently,
            # reversing a rejection with no reason on record. The decision
            # belongs in Approvals.
            _usable_barangay(cursor, barangay_id, "registering it")
        else:
            cursor.execute(
                f"INSERT INTO barangays (id, name, lat, lng, {', '.join(BARANGAY_DETAIL_FIELDS)}, status, approved_by, approved_at) "
                f"VALUES (?, ?, ?, ?, {', '.join('?' for _ in BARANGAY_DETAIL_FIELDS)}, 'approved', ?, NOW())",
                (barangay_id, name, body.lat, body.lng, *[details[f] for f in BARANGAY_DETAIL_FIELDS], payload["id"]),
            )

        cursor.execute("SELECT 1 FROM station_barangays WHERE station_id = ? AND barangay_id = ?", (station_id, barangay_id))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, barangay_id))
        log_audit(cursor, payload, "barangay.created", "barangay", barangay_id,
                  snapshot={"name": name, "station_id": station_id, "reason": reason, **details})
        conn.commit()
        await manager.broadcast({"channel": "stations", "event": "jurisdiction_updated", "id": station_id})
        return {"status": "created", "barangay_id": barangay_id}
    finally:
        conn.close()


@app.delete("/api/devteam/stations/{station_id}")
async def delete_station(station_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    # users.station_id is ON DELETE RESTRICT, and chk_user_scope means a PNP
    # user cannot exist without a station -- so refuse with a clear message
    # rather than letting the FK raise something opaque.
    cursor.execute("SELECT username, deleted_at FROM users WHERE station_id = ?", (station_id,))
    holders = cursor.fetchall()
    if holders:
        conn.close()
        active = [h["username"] for h in holders if not h["deleted_at"]]
        removed = [h["username"] for h in holders if h["deleted_at"]]
        # Removed accounts count too: they can still be restored from the
        # Audit Log, into this station. Saying only "N users still assigned"
        # sent DevTeam looking for accounts no list showed.
        parts = []
        if active:
            parts.append(f"{len(active)} account(s) still assigned ({', '.join(active[:5])}) -- reassign them first")
        if removed:
            parts.append(f"{len(removed)} removed account(s) ({', '.join(removed[:5])}) can still be restored into it from the Audit Log")
        raise HTTPException(status_code=409, detail="This station can't be deleted: " + "; ".join(parts) + ".")
    cursor.execute("SELECT * FROM police_stations WHERE id = ?", (station_id,))
    st_row = cursor.fetchone()
    cursor.execute("DELETE FROM police_stations WHERE id = ?", (station_id,))
    if st_row:
        log_audit(cursor, payload, "station.removed", "station", station_id, snapshot=dict(st_row))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_deleted", "id": station_id})
    return {"status": "deleted", "id": station_id}


# --- DEVTEAM: APPLICATIONS (self-signup admins and the barangays they claim) ---
#
# Every application moves through one small state machine:
#
#     pending --approve--> approved
#     pending --reject---> rejected --reopen--> pending
#
# Only a pending application can be decided, so a double click, a stale
# browser tab or a replayed request can't flip a decision after the fact.
# A decision is never reversed in place: a rejected application goes back to
# pending (with a written reason and a fresh DevTeam password, the same bar
# as registering a jurisdiction) and is then decided again, so the audit log
# holds the whole history. An approved application isn't reopened -- that
# account is managed from Manage Users like any other.
#
# There are two kinds of application. A barangay applicant claims a
# barangay that isn't approved yet: the application is the barangay row, and
# deciding it decides the applicant's account with it. A PNP applicant (or a
# barangay applicant for a barangay that is already approved) has only an
# account to decide, keyed by user id.
#
# Rejected barangays are out of circulation: they can't be in any station's
# jurisdiction, get new accounts, or be registered over. Rejecting one drops
# it from every jurisdiction it was in; the audit entry keeps the list.

MIN_DECISION_REASON = 10
# A password an admin or DevTeam sets for someone else.
MIN_PASSWORD_LENGTH = 8


class ApplicationReopenSchema(BaseModel):
    reason: Optional[str] = None
    confirm_password: Optional[str] = None


def _decision_reason(data: Optional[LocationDecisionSchema], required: bool) -> Optional[str]:
    reason = ((data.reason if data else None) or "").strip()
    if required and len(reason) < MIN_DECISION_REASON:
        raise HTTPException(
            status_code=400,
            detail=f"Give a reason for rejecting (at least {MIN_DECISION_REASON} characters) -- it stays on the application.")
    return reason or None


def _require_state(current: str, wanted: str, what: str = "application"):
    if current != wanted:
        raise HTTPException(status_code=409, detail=f"This {what} is {current}, not {wanted} -- reload to see its current state.")


def _admin_seat_holder(cursor, role: str, barangay_id: Optional[str], station_id: Optional[str], exclude_id: int):
    """Username of whoever currently holds this org unit's one admin seat
    (active and not rejected -- the same rule as the unique index)."""
    column, value = ("station_id", station_id) if role == "PNP_ADMIN" else ("barangay_id", barangay_id)
    cursor.execute(
        f"SELECT username FROM users WHERE {column} = ? AND role = ? AND id <> ? AND deleted_at IS NULL "
        "AND COALESCE(signup_status, 'approved') <> 'rejected'", (value, role, exclude_id))
    row = cursor.fetchone()
    return row["username"] if row else None


def _set_signup_decision(cursor, payload: dict, user_id: int, status: str, reason: Optional[str]):
    if status == "pending":
        cursor.execute(
            "UPDATE users SET signup_status = 'pending', signup_decided_by = NULL, signup_decided_at = NULL, "
            "signup_decision_reason = NULL WHERE id = ?", (user_id,))
    else:
        cursor.execute(
            "UPDATE users SET signup_status = ?, signup_decided_by = ?, signup_decided_at = NOW(), "
            "signup_decision_reason = ? WHERE id = ?", (status, payload["id"], reason, user_id))


def _barangay_application(cursor, barangay_id: str) -> dict:
    cursor.execute(
        "SELECT b.*, u.id AS applicant_id, u.signup_status AS applicant_status, u.role AS applicant_role, "
        "u.deleted_at AS applicant_deleted_at, u.username AS applicant_username "
        "FROM barangays b LEFT JOIN users u ON u.id = b.requested_by WHERE b.id = ?", (barangay_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return dict(row)


def _usable_barangay(cursor, barangay_id: str, purpose: str):
    """Refuses a barangay that isn't approved, naming where to act on it."""
    cursor.execute("SELECT name, status FROM barangays WHERE id = ?", (barangay_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail=f"Unknown barangay '{barangay_id}' -- register it from the Stations tab first.")
    if row["status"] == "pending":
        raise HTTPException(status_code=409, detail=f"Barangay {row['name']} has an application waiting in Approvals -- decide it there before {purpose}.")
    if row["status"] == "rejected":
        raise HTTPException(status_code=409, detail=f"Barangay {row['name']}'s application was rejected. Reopen it from Approvals > Rejected before {purpose}.")


@app.get("/api/devteam/locations")
async def list_locations(authorization: Optional[str] = Header(None), status: Optional[str] = None):
    """Includes the requesting captain's username/role/assignment so DevTeam
    has enough to actually verify the person before approving -- a bare
    location name + status was not enough to tell who's asking. Decided
    rows also carry who decided, when, and why."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if status and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending, approved or rejected")
    conn = get_conn()
    cursor = conn.cursor()
    query = """
        SELECT b.*, u.id AS requester_id, u.username AS requester_username, u.role AS requester_role,
               u.assignment AS requester_assignment, u.verification_status AS requester_verification_status,
               u.id_document_path AS requester_has_document, u.face_photo_path AS requester_has_face_photo,
               u.full_name AS requester_full_name, u.birthdate AS requester_birthdate,
               u.home_address AS requester_home_address, u.contact_number AS requester_contact_number,
               u.position AS requester_position, u.created_at AS requester_created_at,
               u.signup_status AS requester_signup_status, u.deleted_at AS requester_deleted_at,
               d.username AS decided_by_username, b.approved_at AS decided_at
        FROM barangays b
        LEFT JOIN users u ON u.id = b.requested_by
        LEFT JOIN users d ON d.id = b.approved_by
    """
    if status:
        cursor.execute(query + " WHERE b.status = ? ORDER BY COALESCE(b.approved_at, b.created_at) DESC", (status,))
    else:
        cursor.execute(query + " ORDER BY b.created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        # Collapse the raw filename to a boolean -- DevTeam still reaches
        # the actual file through get_verification_document, this list is
        # just "has one been submitted", not a place to leak the path.
        r["requester_has_document"] = bool(r.get("requester_has_document"))
        r["requester_has_face_photo"] = bool(r.get("requester_has_face_photo"))
        r["requester_deleted"] = bool(r.pop("requester_deleted_at", None))
    conn.close()
    return rows


@app.post("/api/devteam/locations/{barangay_id}/approve")
async def approve_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    """Approves the barangay and its applicant together. station_id, when
    given, puts the barangay in that station's jurisdiction in the same
    step -- a barangay no station covers is invisible to every PNP account."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=False)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "pending")
        station_id = (data.station_id or "").strip().lower() or None
        if station_id:
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"Unknown station '{station_id}'")
        cursor.execute(
            "UPDATE barangays SET status = 'approved', approved_by = ?, approved_at = NOW(), decision_reason = ? WHERE id = ?",
            (payload["id"], reason, barangay_id))
        if app_row["applicant_id"] and app_row["applicant_status"] == "pending" and not app_row["applicant_deleted_at"]:
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "approved", reason)
        if station_id:
            cursor.execute("SELECT 1 FROM station_barangays WHERE station_id = ? AND barangay_id = ?", (station_id, barangay_id))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, barangay_id))
        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        stations = [r["station_id"] for r in cursor.fetchall()]
        log_audit(cursor, payload, "barangay.approved", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "applicant": app_row["applicant_username"],
                            "reason": reason, "station_id": station_id})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_approved", "barangay_id": barangay_id})
    return {"status": "approved", "barangay_id": barangay_id, "covered_by": stations}


@app.post("/api/devteam/locations/{barangay_id}/reject")
async def reject_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=True)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "pending")
        cursor.execute(
            "UPDATE barangays SET status = 'rejected', approved_by = ?, approved_at = NOW(), decision_reason = ? WHERE id = ?",
            (payload["id"], reason, barangay_id))
        if app_row["applicant_id"] and app_row["applicant_status"] == "pending" and not app_row["applicant_deleted_at"]:
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "rejected", reason)
        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        dropped = sorted(r["station_id"] for r in cursor.fetchall())
        cursor.execute("DELETE FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        log_audit(cursor, payload, "barangay.rejected", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "applicant": app_row["applicant_username"],
                            "reason": reason, "removed_from_stations": dropped})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_rejected", "barangay_id": barangay_id})
    return {"status": "rejected", "barangay_id": barangay_id, "removed_from_stations": dropped}


@app.post("/api/devteam/locations/{barangay_id}/reopen")
async def reopen_location(barangay_id: str, data: ApplicationReopenSchema, authorization: Optional[str] = Header(None)):
    """Rejected -> pending, so the application can be decided again from the
    normal queue. The previous decision stays in the audit entry."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "rejected")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        applicant_back = False
        if app_row["applicant_id"] and app_row["applicant_status"] == "rejected" and not app_row["applicant_deleted_at"]:
            holder = _admin_seat_holder(cursor, app_row["applicant_role"], barangay_id, None, app_row["applicant_id"])
            if holder:
                raise HTTPException(status_code=409, detail=f"'{holder}' now holds this barangay's admin seat, so the old applicant can't be reinstated.")
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "pending", None)
            applicant_back = True
        cursor.execute(
            "UPDATE barangays SET status = 'pending', approved_by = NULL, approved_at = NULL, decision_reason = NULL WHERE id = ?",
            (barangay_id,))
        cursor.execute("SELECT username FROM users WHERE id = ?", (app_row["approved_by"],))
        decider = cursor.fetchone()
        log_audit(cursor, payload, "barangay.reopened", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "reason": reason, "applicant": app_row["applicant_username"],
                            "applicant_reinstated": applicant_back,
                            "previous_decision": {"status": "rejected", "by": decider["username"] if decider else None,
                                                  "at": app_row["approved_at"], "reason": app_row["decision_reason"]}})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_reopened", "barangay_id": barangay_id})
    return {"status": "pending", "barangay_id": barangay_id, "applicant_reinstated": applicant_back}


# 2026-09-23 bug fix (see login()'s comment for the full report): a
# self-signup PNP_ADMIN had no equivalent of the barangay flow above at
# all -- the station already existing was treated as sufficient vetting
# for whoever showed up claiming to run it. Barangays have a `barangays`
# row to hang a pending/approved/rejected status on; a station is
# DevTeam-created and already permanently 'approved' by definition, so
# there's no location-shaped object to reuse here -- the gate has to live
# directly on signup_status instead. Generic by user id rather than
# barangay_id-shaped like the endpoints above, so this covers any
# self-signup admin account, PNP included.
def _list_signups(cursor, status: str) -> list:
    """PNP applications, plus barangay applicants whose barangay is NOT
    itself in this same state -- a barangay applicant riding on a pending
    or rejected barangay is listed under that barangay instead."""
    cursor.execute("""
        SELECT u.id, u.username, u.role, u.assignment, u.station_id, u.barangay_id, u.created_at,
               u.verification_status, u.id_document_path, u.face_photo_path,
               COALESCE(s.name, 'Barangay ' || b.name) AS station_name, b.status AS barangay_status,
               u.full_name, u.birthdate, u.home_address, u.contact_number, u.position,
               u.signup_status, u.signup_decided_at AS decided_at, u.signup_decision_reason AS decision_reason,
               d.username AS decided_by_username
        FROM users u
        LEFT JOIN police_stations s ON s.id = u.station_id
        LEFT JOIN barangays b ON b.id = u.barangay_id
        LEFT JOIN users d ON d.id = u.signup_decided_by
        WHERE u.signup_status = ? AND u.deleted_at IS NULL
          AND (u.role = 'PNP_ADMIN'
               OR (u.role = 'BARANGAY_ADMIN' AND COALESCE(b.status, 'approved') <> ?))
        ORDER BY COALESCE(u.signup_decided_at, u.created_at) DESC
    """, (status, status))
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        r["has_document"] = bool(r.pop("id_document_path"))
        r["has_face_photo"] = bool(r.pop("face_photo_path"))
    return rows


@app.get("/api/devteam/signups")
async def list_signups(authorization: Optional[str] = Header(None), status: str = "pending"):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if status not in ("pending", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending or rejected")
    conn = get_conn()
    try:
        return _list_signups(conn.cursor(), status)
    finally:
        conn.close()


@app.get("/api/devteam/pending_signups")
async def list_pending_signups(authorization: Optional[str] = Header(None)):
    """Kept for older frontends; same as /api/devteam/signups?status=pending."""
    return await list_signups(authorization, "pending")


def _signup_application(cursor, user_id: int) -> dict:
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Application not found")
    target = dict(target)
    if target["role"] not in ADMIN_ROLES:
        raise HTTPException(status_code=400, detail="Only self-signup admin accounts go through Approvals")
    return target


@app.post("/api/devteam/users/{user_id}/approve_signup")
async def approve_signup(user_id: int, data: Optional[LocationDecisionSchema] = None,
                         authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=False)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "pending")
        if target["role"] == "BARANGAY_ADMIN":
            # Approving the person onto a barangay nobody approved would give
            # them a login that the location gate still refuses.
            _usable_barangay(cursor, target["barangay_id"], "approving its admin")
        _set_signup_decision(cursor, payload, user_id, "approved", reason)
        log_audit(cursor, payload, "user.signup_approved", "user", str(user_id),
                  snapshot={"username": target["username"], "role": target["role"], "reason": reason})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_approved", "id": user_id})
    return {"status": "approved"}


@app.post("/api/devteam/users/{user_id}/reject_signup")
async def reject_signup(user_id: int, data: Optional[LocationDecisionSchema] = None,
                        authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=True)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "pending")
        _set_signup_decision(cursor, payload, user_id, "rejected", reason)
        log_audit(cursor, payload, "user.signup_rejected", "user", str(user_id),
                  snapshot={"username": target["username"], "role": target["role"], "reason": reason})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_rejected", "id": user_id})
    return {"status": "rejected"}


@app.post("/api/devteam/users/{user_id}/reopen_signup")
async def reopen_signup(user_id: int, data: ApplicationReopenSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "rejected")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        holder = _admin_seat_holder(cursor, target["role"], target["barangay_id"], target["station_id"], user_id)
        if holder:
            raise HTTPException(status_code=409, detail=f"'{holder}' now holds this admin seat, so this application can't be reopened.")
        cursor.execute("SELECT username FROM users WHERE id = ?", (target["signup_decided_by"],))
        decider = cursor.fetchone()
        _set_signup_decision(cursor, payload, user_id, "pending", None)
        log_audit(cursor, payload, "user.signup_reopened", "user", str(user_id),
                  snapshot={"username": target["username"], "reason": reason,
                            "previous_decision": {"status": "rejected", "by": decider["username"] if decider else None,
                                                  "at": target["signup_decided_at"], "reason": target["signup_decision_reason"]}})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_reopened", "id": user_id})
    return {"status": "pending"}

# --- ADMIN: MANAGE YOUR OWN USERS ONLY ---
@app.get("/api/admin/users")
async def list_my_users(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()
    # deleted_at IS NULL added 2026-09-22 -- a soft-deleted subordinate
    # shouldn't keep showing up in an admin's own team list (or DevTeam's
    # full list) as if still active; the Audit Log is where a removed
    # account is meant to be found and restored from, not here.
    if payload["role"] == "DEVTEAM":
        cursor.execute("SELECT * FROM users WHERE deleted_at IS NULL")
    else:
        cursor.execute("SELECT * FROM users WHERE parent_admin_id = ? AND deleted_at IS NULL", (payload["id"],))
    rows = cursor.fetchall()
    result = _rows_to_user_dicts_batch(cursor, rows)
    # Each account's dicing, for the Personnel permission editor.
    ids = [u["id"] for u in result]
    scopes_by_user: dict = {}
    if ids:
        ph = ",".join("?" for _ in ids)
        cursor.execute(f"SELECT user_id, permission_key, resource_type, resource_id FROM permission_grants "
                       f"WHERE user_id IN ({ph}) AND resource_type IN ('camera', 'crime_type', 'channel')", tuple(ids))
        for r in cursor.fetchall():
            (scopes_by_user.setdefault(r["user_id"], {}).setdefault(r["permission_key"], {})
             .setdefault(r["resource_type"], []).append(r["resource_id"]))
    for u in result:
        u["resource_scopes"] = scopes_by_user.get(u["id"], {})
    conn.close()
    return result

@app.post("/api/admin/users")
async def create_my_user(new_user: AdminCreateUser, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    if payload["role"] == "DEVTEAM":
        # DEVTEAM has no org of its own to inherit, so it cannot create an
        # operator here -- there would be nothing to scope them to, and
        # chk_user_scope would reject the row. Use /api/devteam/create_user,
        # which takes an explicit barangay or station.
        raise HTTPException(
            status_code=400,
            detail="DEVTEAM accounts have no barangay or station to inherit. "
                   "Use the DevTeam console to create a user with an explicit assignment.")

    target_role = ADMIN_CREATES_ROLE[payload["role"]]

    # An operator inherits their creator's scope, and WHICH field that is
    # depends on the organization: barangay staff get barangay_id, PNP
    # officers get station_id. Copying barangay_id unconditionally (as
    # before) would now violate chk_user_scope for the PNP side.
    if target_role in PNP_SIDE_ROLES:
        new_barangay, new_station = None, payload.get("station_id")
        if not new_station:
            raise HTTPException(status_code=400,
                                detail="Your account has no station assigned; contact DevTeam.")
    else:
        new_barangay, new_station = payload.get("barangay_id"), None
        if not new_barangay:
            raise HTTPException(status_code=400,
                                detail="Your account has no barangay assigned; contact DevTeam.")

    profile = _clean_profile(new_user, required=("full_name",))
    # Permissions sent with the account used to be dropped unless
    # is_sub_admin was also set; now they're applied (and a key this side
    # can never hold is refused, as in PATCH .../permissions).
    for key, granted in (new_user.permissions or {}).items():
        if granted:
            _check_permission_key_allowed({"role": target_role}, key)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in PROFILE_FIELDS)})",
            (new_user.username, hash_password(new_user.password), target_role,
             new_barangay, new_station, new_user.assignment, payload["id"],
             new_user.display_title if new_user.is_sub_admin else None,
             1 if new_user.is_sub_admin else 0, *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_id = cursor.lastrowid

        if new_user.permissions:
            for key, granted in new_user.permissions.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
        log_audit(cursor, payload, "user.created", "user", str(new_id), snapshot={
            "username": new_user.username, "role": target_role, "barangay_id": new_barangay, "station_id": new_station,
            "assignment": new_user.assignment, "permissions": new_user.permissions, **profile})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "user_created", "id": new_id})
        return {"status": "success", "role": target_role, "id": new_id}
    except IntegrityError:
        raise HTTPException(status_code=400, detail="That username is already taken.")
    finally:
        conn.close()

@app.post("/api/devteam/users")
async def devteam_create_user(new_user: DevteamCreateUser, authorization: Optional[str] = Header(None)):
    """Full-power account creation -- DevTeam can create ANY role
    (PNP_ADMIN, PNP_OFFICER, BARANGAY_ADMIN, BARANGAY_STAFF) directly,
    bypassing the self-signup approval flow, and grant it a permission
    set from the same permission tree admins use for their sub-accounts.

    Which scope field is required depends on the role's organization:
    barangay roles take barangay_id, PNP roles take station_id. The DB's
    chk_user_scope enforces this too, so a mismatch is caught either way --
    this just produces a readable error instead of a constraint violation.

    The one-admin-per-unit unique indexes still apply (one BARANGAY_ADMIN per
    barangay, one PNP_ADMIN per station)."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    role = new_user.role.upper()
    if role not in ALL_ROLES or role == "DEVTEAM":
        raise HTTPException(status_code=400, detail=f"Invalid role '{new_user.role}'")

    is_pnp = role in PNP_SIDE_ROLES
    barangay_id = (new_user.barangay_id or "").strip().lower()
    station_id = (new_user.station_id or "").strip().lower()

    if is_pnp and not station_id:
        raise HTTPException(status_code=400, detail="A police station is required for PNP roles")
    if not is_pnp and not barangay_id:
        raise HTTPException(status_code=400, detail="A barangay is required for barangay roles")
    profile = _clean_profile(new_user, required=("full_name",))

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if is_pnp:
            # Stations are created deliberately in the station manager, never
            # auto-vivified from a typo in a username form.
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail=f"Unknown station '{station_id}'")
            barangay_id = ""
        else:
            # station_id doubles up for this branch (2026-09-24 user
            # request): a barangay account never gets its OWN station_id
            # (chk_user_scope forbids it, and the account INSERT below
            # correctly still sends the now-blanked local `station_id`) --
            # but the SAME form field is repurposed here to mean "which
            # station should cover this barangay's jurisdiction", captured
            # into its own variable before being wiped.
            picked_station_for_jurisdiction = station_id
            station_id = ""
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            existing_barangay = cursor.fetchone()
            # BUG FOUND 2026-09-29 (user request): this used to silently
            # create a brand-new barangay row right here -- "make a
            # barangay" doesn't belong in account creation, it belongs in
            # the Stations tab, where the barangay is registered directly
            # under the station covering it (POST .../stations/{id}/
            # barangays) instead of drifting station-less until someone
            # remembers to assign one. An unknown barangay_id here is now a
            # hard error pointing at where to actually register it.
            if not existing_barangay:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown barangay '{barangay_id}' -- register it from the Stations tab first.")
            # A pending or rejected barangay used to be approved here silently
            # (2026-09-02: creating an account "was the vetting decision"),
            # which undid a rejection with no reason on record and stranded
            # any pending applicant. Since 2026-09-30 that decision is made in
            # Approvals, and this refuses with a message saying so -- still
            # never the silent login failure the 09-02 fix was about.
            try:
                _usable_barangay(cursor, barangay_id, "creating accounts for it")
            except HTTPException:
                conn.close()
                raise

            # BUG FOUND 2026-09-24 (user report): creating a barangay account
            # never connected the barangay to any police station -- that only
            # ever happened later, separately, if DevTeam remembered to go to
            # the Stations tab. A barangay with no covering station is
            # invisible to every PNP account and (since Phase 3) any report
            # request from it has nowhere to route. Auto-resolve from the
            # EXISTING jurisdiction relationship when there is one -- nothing
            # to ask, nothing to reassign. Only a barangay with NO station at
            # all (brand new, or an old orphaned one from before this fix)
            # requires picking one as part of this same creation.
            cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
            already_covered = cursor.fetchone()
            if not already_covered:
                if not picked_station_for_jurisdiction:
                    conn.close()
                    raise HTTPException(
                        status_code=400,
                        detail="This barangay has no police station covering it yet -- pick one to assign its jurisdiction.")
                cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (picked_station_for_jurisdiction,))
                if not cursor.fetchone():
                    conn.close()
                    raise HTTPException(status_code=400, detail=f"Unknown station '{picked_station_for_jurisdiction}'")
                cursor.execute(
                    "INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)",
                    (picked_station_for_jurisdiction, barangay_id),
                )

        parent_id = new_user.parent_admin_id
        if role in STANDARD_ROLES and parent_id is None:
            # Auto-attach to whichever admin already runs this org unit, so
            # the account shows up nested under someone in the directory.
            # deleted_at IS NULL added 2026-09-22 -- don't auto-attach a new
            # account to an admin who was soft-deleted; look for a still-
            # active one instead (falls through to unassigned if there
            # isn't one, same as before this feature existed).
            if is_pnp:
                cursor.execute(
                    "SELECT id FROM users WHERE station_id = ? AND role = 'PNP_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') = 'approved'", (station_id,))
            else:
                cursor.execute(
                    "SELECT id FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') = 'approved'", (barangay_id,))
            existing_admin = cursor.fetchone()
            parent_id = existing_admin["id"] if existing_admin else None

        # BUG FOUND 2026-09-04 (user report: created an account for a
        # barangay, it never showed up anywhere). Root cause: this endpoint
        # had no explicit duplicate checks of its own -- it just attempted
        # the INSERT and let SQLite's own unique constraints reject it,
        # caught below as a bare IntegrityError with a message that GUESSES
        # between two entirely different real causes ("that username is
        # taken, OR this location already has that captain role filled").
        # Whichever one actually happened, the DevTeam operator creating the
        # account only sees a maybe -- easy to misread as "probably just a
        # naming collision, I'll retry with a different username" when the
        # real problem was the admin slot, or vice versa, and easy to not
        # register as a failure at all if skimmed quickly. signup() already
        # does this properly with a dedicated pre-check; this endpoint never
        # got the same treatment. Two separate, specific checks instead, so
        # the account either gets created or the operator is told exactly
        # why it didn't.
        # deleted_at IS NULL added 2026-09-22 on all three checks below --
        # a soft-deleted account's username and org-admin slot both free up
        # for reuse, same as a hard delete did. (Restoring a soft-deleted
        # user later, if their old username was reused by someone else in
        # the meantime, correctly fails on the username UNIQUE constraint
        # rather than silently colliding two accounts.)
        cursor.execute("SELECT 1 FROM users WHERE username = ? AND deleted_at IS NULL", (new_user.username,))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Username '{new_user.username}' is already taken.")
        if role in ADMIN_ROLES:
            if is_pnp:
                cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = 'PNP_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (station_id,))
                dup_detail = "This station already has a PNP Admin account."
            else:
                cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (barangay_id,))
                dup_detail = "This barangay already has a Barangay Admin account."
            if cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail=dup_detail)

        # Custom role (#2, 2026-09-23): a named preset, only ever applied to
        # a real STANDARD_ROLES account -- an admin's own access is either
        # automatic or DevTeam-overridden (see AdminPermissionOverride),
        # never role-templated. org_type must match the account actually
        # being created: a 'police' role can't be handed to a barangay
        # account, chk_user_scope-style validation for the role dimension.
        custom_role = None
        if new_user.custom_role_id:
            if role not in STANDARD_ROLES:
                conn.close()
                raise HTTPException(status_code=400, detail="Custom roles apply to staff/officer accounts only.")
            cursor.execute("SELECT * FROM custom_roles WHERE id = ?", (new_user.custom_role_id,))
            custom_role = cursor.fetchone()
            if not custom_role:
                conn.close()
                raise HTTPException(status_code=400, detail="Unknown custom role.")
            # Legacy side-bound roles (created before 2026-09-29) keep their
            # check; side-agnostic roles (org_type NULL) apply to either side.
            wanted_org = "police" if is_pnp else "barangay"
            if custom_role["org_type"] and custom_role["org_type"] != wanted_org:
                conn.close()
                raise HTTPException(status_code=400, detail=f"That role is for {custom_role['org_type']} accounts, not {wanted_org}.")

        # An admin's permissions are automatic unless overridden, so rows
        # sent for a non-overridden admin would be stored but never read.
        # Overriding at creation needs the same password re-entry as
        # overriding an existing admin.
        override = role in ADMIN_ROLES and new_user.override_permissions
        if new_user.override_permissions and role not in ADMIN_ROLES:
            conn.close()
            raise HTTPException(status_code=400, detail="Only admin accounts have automatic permissions to override.")
        if override:
            cursor.execute("SELECT password FROM users WHERE id = ?", (payload["id"],))
            caller = cursor.fetchone()
            if not caller or not verify_password(new_user.confirm_password or "", caller["password"]):
                conn.close()
                raise HTTPException(status_code=403, detail="Incorrect DevTeam password.")
        explicit_perms = new_user.permissions if (role in STANDARD_ROLES or override) else None
        try:
            for key, granted in (explicit_perms or {}).items():
                if granted:
                    _check_permission_key_allowed({"role": role}, key)
        except HTTPException:
            conn.close()
            raise

        display_title = new_user.display_title or (custom_role["name"] if custom_role else None)
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, custom_role_id, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in PROFILE_FIELDS)})",
            (new_user.username, hash_password(new_user.password), role,
             barangay_id or None, station_id or None, new_user.assignment, parent_id,
             display_title, 1 if display_title else 0,
             custom_role["id"] if custom_role else None,
             *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_id = cursor.lastrowid
        if override:
            cursor.execute("UPDATE users SET custom_permissions = 1 WHERE id = ?", (new_id,))

        if explicit_perms:
            for key, granted in explicit_perms.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )

        if custom_role:
            banned = BARANGAY_ONLY_PERMISSIONS if role in PNP_SIDE_ROLES else POLICE_ONLY_PERMISSIONS
            cursor.execute("SELECT * FROM custom_role_permission_defaults WHERE role_id = ?", (custom_role["id"],))
            explicit = new_user.permissions or {}
            for default in cursor.fetchall():
                key = default["permission_key"]
                if key not in VALID_PERMISSION_KEYS or key in banned:
                    continue
                # The Create User form pre-fills its tree from the role and
                # sends every key explicitly -- a key the operator unticked
                # arrives as False and must stay off, not be re-added here.
                if key in explicit and (not default["resource_type"] or not explicit[key]):
                    continue
                if default["resource_type"] and default["resource_id"]:
                    cursor.execute(
                        "INSERT INTO permission_grants (id, user_id, permission_key, resource_type, resource_id, granted_by) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), new_id, key, default["resource_type"], default["resource_id"], payload["id"]),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
        if new_user.resource_scopes:
            _apply_resource_scopes(cursor, payload, {"id": new_id, "role": role, "barangay_id": barangay_id or None,
                                                     "station_id": station_id or None}, new_user.resource_scopes)
        log_audit(cursor, payload, "user.created", "user", str(new_id), snapshot={
            "username": new_user.username, "role": role, "barangay_id": barangay_id or None, "station_id": station_id or None,
            "assignment": new_user.assignment, "custom_role_id": custom_role["id"] if custom_role else None,
            "permissions": explicit_perms, "permissions_overridden": override, **profile})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "user_created", "id": new_id})
        await manager.broadcast({"channel": "locations", "event": "location_approved", "barangay_id": barangay_id})
        return {"status": "success", "role": role, "id": new_id, "barangay_id": barangay_id}
    except IntegrityError as e:
        raise HTTPException(
            status_code=400,
            detail="That username is taken, or this location already has that captain role filled.",
        )
    finally:
        conn.close()

@app.patch("/api/admin/users/{user_id}/permissions")
async def update_user_permissions(user_id: int, data: PermissionsUpdate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only edit permissions for your own users")
    # Unknown keys and keys this account's side can never hold used to be
    # dropped or stored silently -- the caller saw "updated" either way.
    try:
        for key, granted in data.permissions.items():
            if granted:
                _check_permission_key_allowed(dict(target), key)
    except HTTPException:
        conn.close()
        raise

    cursor.execute("SELECT permission_key FROM user_permissions WHERE user_id = ?", (user_id,))
    perms_before = sorted(r["permission_key"] for r in cursor.fetchall())
    cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    for key, granted in data.permissions.items():
        if granted and key in VALID_PERMISSION_KEYS:
            cursor.execute(
                "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                (user_id, key, payload["id"]),
            )
    perms_after = sorted(k for k, g in data.permissions.items() if g and k in VALID_PERMISSION_KEYS)
    log_audit(cursor, payload, "user.permissions_updated", "user", str(user_id),
              snapshot={"from": perms_before, "to": perms_after})
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id, "permissions": data.permissions}

@app.delete("/api/admin/users/{user_id}")
async def delete_my_user(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only remove your own users")

    # Soft delete, added 2026-09-22 (user request: DevTeam can audit/undo
    # removals). The row stays -- require_auth/login's deleted_at IS NULL
    # checks are what actually revokes access, same as a hard delete used
    # to -- but a snapshot + Restore is now possible, where a hard DELETE
    # gave up that row forever the instant this ran.
    cursor.execute("UPDATE users SET deleted_at = NOW() WHERE id = ?", (user_id,))
    # Snapshot excludes the password hash -- restoring never needs it (the
    # row's real password is untouched by a soft delete; this snapshot is
    # for audit DISPLAY only, not row reconstruction), and there's no
    # reason for even a hash to sit in an audit trail a UI might render.
    snapshot = {k: v for k, v in dict(target).items() if k != "password"}
    log_audit(cursor, payload, "user.deleted", "user", user_id, snapshot=snapshot)
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_deleted", "id": user_id})
    return {"status": "deleted", "id": user_id}

@app.post("/api/admin/users/{user_id}/reset_password")
async def reset_my_users_password(user_id: int, authorization: Optional[str] = Header(None)):
    """Basic account management for admins: reset a password for a user THEY
    manage, same ownership rule as delete_my_user (parent_admin_id must be
    this admin's own id).

    An admin account's OWN password is never resettable through this route --
    admin accounts are created by DevTeam with parent_admin_id left NULL
    (see devteam_create_user), so the ownership check above already excludes
    them for a non-DEVTEAM caller. The explicit role check below is
    belt-and-suspenders: it makes the refusal a readable 403 instead of a
    generic "not your user", and holds even if that NULL invariant ever
    changes. DEVTEAM is the only role that can reset an admin's password --
    see devteam_edit_user for that path.
    """
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, parent_admin_id FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if payload["role"] != "DEVTEAM":
        if target["role"] in (ADMIN_ROLES | {"DEVTEAM"}):
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="Admin account passwords can only be reset by DevTeam.")
        if target["parent_admin_id"] != payload["id"]:
            conn.close()
            raise HTTPException(status_code=403, detail="You can only reset passwords for your own users")

    new_password = secrets.token_urlsafe(12)
    cursor.execute("UPDATE users SET password = ? WHERE id = ?",
                    (hash_password(new_password), user_id))
    log_audit(cursor, payload, "user.password_reset", "user", str(user_id), snapshot={"username": target["username"]})
    conn.commit()
    conn.close()
    # The new password itself never goes over the broadcast channel -- only
    # the fact that a reset happened, same reasoning as devteam_credentials.txt
    # never being re-shown after its one display.
    await manager.broadcast({"channel": "users", "event": "password_reset", "id": user_id})
    return {"status": "reset", "id": user_id, "username": target["username"], "new_password": new_password}

# --- DEVTEAM: FULL POWER OVER ANY USER (EDIT / DELETE) ---
@app.patch("/api/devteam/users/{user_id}")
async def devteam_edit_user(user_id: int, data: DevteamUserEdit, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    fields, values = [], []
    if data.username is not None:
        fields.append("username = ?"); values.append(data.username)
    if data.password:
        if len(data.password) < MIN_PASSWORD_LENGTH:
            conn.close()
            raise HTTPException(status_code=400, detail=f"A new password needs at least {MIN_PASSWORD_LENGTH} characters")
        fields.append("password = ?"); values.append(hash_password(data.password))
    if data.assignment is not None:
        fields.append("assignment = ?"); values.append(data.assignment)
    if data.display_title is not None:
        fields.append("display_title = ?"); values.append(data.display_title)
    # Existence is checked explicitly for both -- this can't lean on the FK
    # the way the comment used to claim: users.station_id/barangay_id are
    # declared REFERENCES in schema_sqlite.sql, but SQLite does not enforce
    # foreign keys unless "PRAGMA foreign_keys = ON" is run on the
    # connection, which nothing here does (the default/no-DATABASE_URL
    # path). Without this check, an edit could silently scope an account to
    # a station or barangay id that doesn't exist -- no error, just an
    # account whose jurisdiction never resolves to anything again.
    if data.barangay_id is not None:
        brgy = data.barangay_id.strip().lower()
        if brgy != (target["barangay_id"] or ""):
            try:
                _usable_barangay(cursor, brgy, "moving accounts onto it")
            except HTTPException:
                conn.close()
                raise
        fields.append("barangay_id = ?"); values.append(brgy)
    if data.station_id is not None:
        stn = data.station_id.strip().lower()
        cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (stn,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown station '{stn}'")
        fields.append("station_id = ?"); values.append(stn)
    new_role = target["role"]
    if data.role is not None and data.role != target["role"]:
        if data.role not in ALL_ROLES:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid role '{data.role}'")
        # DevTeam access is granted by creating a DevTeam account, never by
        # promoting someone in place (or quietly demoting one).
        if "DEVTEAM" in (data.role, target["role"]):
            conn.close()
            raise HTTPException(status_code=400, detail="DevTeam accounts can't be converted to or from other roles")
        new_role = data.role
        fields.append("role = ?"); values.append(data.role)
        # Crossing sides (barangay <-> police) swaps which org column is set
        # -- chk_user_scope allows exactly one -- and drops the old
        # supervisor, who belongs to the other side.
        if (data.role in PNP_SIDE_ROLES) != (target["role"] in PNP_SIDE_ROLES):
            if data.role in PNP_SIDE_ROLES:
                if not data.station_id:
                    conn.close()
                    raise HTTPException(status_code=400, detail="Pick the police station for this account's new role")
                fields.append("barangay_id = ?"); values.append(None)
            else:
                if not data.barangay_id:
                    conn.close()
                    raise HTTPException(status_code=400, detail="Pick the barangay for this account's new role")
                fields.append("station_id = ?"); values.append(None)
            if "parent_admin_id" not in data.model_fields_set:
                fields.append("parent_admin_id = ?"); values.append(None)
    if "parent_admin_id" in data.model_fields_set:
        if data.parent_admin_id is not None:
            if data.parent_admin_id == user_id:
                conn.close()
                raise HTTPException(status_code=400, detail="An account can't report to itself")
            cursor.execute("SELECT role FROM users WHERE id = ? AND deleted_at IS NULL", (data.parent_admin_id,))
            boss = cursor.fetchone()
            wanted_boss = "PNP_ADMIN" if new_role in PNP_SIDE_ROLES else "BARANGAY_ADMIN"
            if not boss or boss["role"] != wanted_boss:
                conn.close()
                raise HTTPException(status_code=400, detail=f"Supervisor must be an active {wanted_boss} account")
        fields.append("parent_admin_id = ?"); values.append(data.parent_admin_id)
    if "custom_role_id" in data.model_fields_set:
        role_id = (data.custom_role_id or "").strip() or None
        if role_id:
            cursor.execute("SELECT 1 FROM custom_roles WHERE id = ?", (role_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail="Unknown custom role")
        fields.append("custom_role_id = ?"); values.append(role_id)
    try:
        profile = _clean_profile(data)
    except HTTPException:
        conn.close()
        raise
    for f, v in profile.items():
        fields.append(f"{f} = ?"); values.append(v)

    if not fields:
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(user_id)
    before = dict(target)
    try:
        cursor.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        changes = {}
        for f in fields:
            col = f.split(" = ")[0]
            if col == "password":
                changes["password"] = "changed"
                continue
            new_v = values[fields.index(f)]
            if before.get(col) != new_v:
                changes[col] = {"from": before.get(col), "to": new_v}
        log_audit(cursor, payload, "user.updated", "user", str(user_id),
                  snapshot={"username": before.get("username"), "changes": changes})
        conn.commit()
    except IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Update rejected: {e}")

    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    updated_row = cursor.fetchone()
    result = _row_to_user_dict(cursor, updated_row)
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_edited", "id": user_id})
    return {"status": "updated", "user": result}

@app.post("/api/devteam/users/{user_id}/override_permissions")
async def devteam_override_admin_permissions(
    user_id: int, data: AdminPermissionOverride, authorization: Optional[str] = Header(None)
):
    """User request 2026-09-04: BARANGAY_ADMIN/PNP_ADMIN permissions are
    normally automatic (require_permission()'s ADMIN_ROLES bypass) and were
    never editable through any UI, because editing user_permissions rows
    for an admin used to do nothing -- the bypass ignored them entirely.
    This is DevTeam-only, and re-checks the CALLING DevTeam's own password
    before touching anything, exactly like any other step-up-confirmed
    sensitive action -- not a new shared secret, and not usable by anyone
    who only has a session token (a stolen/leaked token alone can't call
    this without also knowing that DevTeam's real password).
    """
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT password FROM users WHERE id = ?", (payload["id"],))
        caller = cursor.fetchone()
        if not caller or not verify_password(data.confirm_password, caller["password"]):
            raise HTTPException(status_code=403, detail="Incorrect DevTeam password.")

        cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["role"] not in ADMIN_ROLES:
            # Staff/officer permissions were never automatic in the first
            # place -- PATCH /api/admin/users/{id}/permissions already edits
            # those directly, no override/re-auth dance needed there.
            raise HTTPException(
                status_code=400,
                detail=f"'{target['role']}' permissions are already directly editable -- "
                       f"this endpoint is only for overriding an admin's automatic access.",
            )

        if data.permissions is None:
            cursor.execute("UPDATE users SET custom_permissions = 0 WHERE id = ?", (user_id,))
            cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
            log_audit(cursor, payload, "user.permissions_reset_to_automatic", "user", str(user_id))
            conn.commit()
            await manager.broadcast({"channel": "users", "event": "permissions_reset", "id": user_id})
            return {"status": "reset_to_automatic", "id": user_id}

        # Added 2026-09-22: mirrors the hard bans require_permission() itself
        # enforces (BARANGAY_ONLY_PERMISSIONS for PNP targets, POLICE_ONLY_
        # PERMISSIONS for barangay targets) -- without this, the override
        # endpoint would happily store a user_permissions row require_
        # permission() can never actually honor for this target (it 403s
        # before ever consulting the row), which is harmless functionally
        # but a confusing, dead-on-arrival grant to leave sitting in an
        # audit trail. VALID_PERMISSION_KEYS alone already filtered out
        # anything not a real key at all; this filters out real keys that
        # are real but banned for THIS target's org side.
        banned_for_target = (
            BARANGAY_ONLY_PERMISSIONS if target["role"] in PNP_SIDE_ROLES
            else POLICE_ONLY_PERMISSIONS if target["role"] in BARANGAY_SIDE_ROLES
            else set()
        )
        cursor.execute("UPDATE users SET custom_permissions = 1 WHERE id = ?", (user_id,))
        cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        for key, granted in data.permissions.items():
            if granted and key in VALID_PERMISSION_KEYS and key not in banned_for_target:
                cursor.execute(
                    "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                    (user_id, key, payload["id"]),
                )
        log_audit(cursor, payload, "user.permissions_overridden", "user", str(user_id), snapshot={
            "permissions": sorted(k for k, g in data.permissions.items() if g and k in VALID_PERMISSION_KEYS and k not in banned_for_target)})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "permissions_overridden", "id": user_id})
        return {"status": "overridden", "id": user_id, "permissions": data.permissions}
    finally:
        conn.close()

@app.delete("/api/devteam/users/{user_id}")
async def devteam_delete_user(user_id: int, authorization: Optional[str] = Header(None)):
    """Soft delete -- devteam can remove a captain or any single standard/
    sub-admin account. A soft-deleted captain's own sub-accounts keep their
    parent_admin_id exactly as it was (the referenced row still physically
    exists, just hidden from login/listings) rather than losing the link --
    if the captain is later Restored from the Audit Log, that relationship
    is intact again automatically, with nothing left to reattach by hand."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "DEVTEAM":
        conn.close()
        raise HTTPException(status_code=403, detail="DevTeam accounts cannot be deleted from this panel")

    cursor.execute("UPDATE users SET deleted_at = NOW() WHERE id = ?", (user_id,))
    snapshot = {k: v for k, v in dict(target).items() if k != "password"}
    log_audit(cursor, payload, "user.deleted", "user", user_id, snapshot=snapshot)
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_deleted", "id": user_id})
    return {"status": "deleted", "id": user_id}

# --- DEVTEAM: DETECTION QUALITY ---
@app.get("/api/devteam/detection_quality")
async def devteam_detection_quality(days: int = 30, authorization: Optional[str] = Header(None)):
    """Per camera and per alert type, over the last `days`: how many alerts
    the AI raised, how operators judged them, and the resulting precision.
    This is the live, own-camera counterpart to the 20-minute outside-camera
    measurements in config.json -- it only gets better as operators decide."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    days = max(1, min(days, 365))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT i.camera_id, c.name AS camera_name, UPPER(COALESCE(f.ai_event, i.type)) AS event,
                      COUNT(*) AS alerts,
                      SUM(CASE WHEN f.label = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
                      SUM(CASE WHEN f.label = 'dismissed' THEN 1 ELSE 0 END) AS dismissed,
                      SUM(CASE WHEN f.label IS NULL AND i.status = 'Active' THEN 1 ELSE 0 END) AS pending,
                      SUM(CASE WHEN f.label = 'confirmed' AND f.final_type IS NOT NULL AND f.final_type <> f.ai_event THEN 1 ELSE 0 END) AS retyped,
                      AVG(CASE WHEN f.label = 'confirmed' THEN i.confidence END) AS avg_conf_confirmed,
                      AVG(CASE WHEN f.label = 'dismissed' THEN i.confidence END) AS avg_conf_dismissed
               FROM incidents i
               LEFT JOIN detection_feedback f ON f.incident_id = i.id
               LEFT JOIN cameras c ON c.id = i.camera_id
               WHERE i.source = 'AI_AUTOMATION' AND i.deleted_at IS NULL AND i.occurred_date >= ?
               GROUP BY i.camera_id, c.name, UPPER(COALESCE(f.ai_event, i.type))
               ORDER BY alerts DESC""",
            (since,),
        )
        rows = []
        for r in cursor.fetchall():
            r = dict(r)
            decided = (r["confirmed"] or 0) + (r["dismissed"] or 0)
            r["precision"] = round(r["confirmed"] / decided, 3) if decided else None
            r["alerts_per_day"] = round(r["alerts"] / days, 2)
            for k in ("avg_conf_confirmed", "avg_conf_dismissed"):
                r[k] = round(r[k], 3) if r[k] is not None else None
            rows.append(r)
        cursor.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN exported_at IS NULL THEN 1 ELSE 0 END) AS fresh FROM detection_feedback")
        totals = dict(cursor.fetchone())
        return {"days": days, "rows": rows, "labelled_examples": totals["n"] or 0, "not_yet_exported": totals["fresh"] or 0}
    finally:
        conn.close()


# --- DEVTEAM: AUDIT LOG ---
# User request 2026-09-22: "monitor what each user has done... removed a
# user, removed this report like that and recover it." log_audit() (see its
# own docstring) is called from every soft-deleting endpoint above; this is
# where DevTeam reads that trail back and, for a delete-type entry whose
# target is still soft-deleted, undoes it.
def _audit_bound(value: Optional[str], label: str, end: bool) -> Optional[str]:
    """A YYYY-MM-DD filter as a created_at bound. created_at is stored UTC;
    the console's day is Manila time (UTC+8), so a day runs from 16:00 UTC
    the evening before."""
    if not value:
        return None
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must be a date (YYYY-MM-DD)")
    start = day - timedelta(hours=8)
    return (start + timedelta(days=1) if end else start).strftime("%Y-%m-%d %H:%M:%S")


@app.get("/api/devteam/audit_log")
async def devteam_list_audit_log(
    action: Optional[str] = None, limit: int = 200, user_id: Optional[int] = None, q: Optional[str] = None,
    category: Optional[str] = None, target_type: Optional[str] = None,
    barangay_id: Optional[str] = None, station_id: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    """Filters, all optional and combined with AND:
    action -- exact action name; category -- its prefix ("user", "camera");
    user_id -- everything that account did OR that was done to it;
    barangay_id / station_id -- done by an account of that barangay or
    station, or done to that barangay or station itself;
    date_from / date_to -- inclusive days (Manila time);
    target_type; q -- free text on action, actor, target and details."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    limit = max(1, min(limit, 1000))
    start, end = _audit_bound(date_from, "From date", False), _audit_bound(date_to, "To date", True)
    if start and end and start >= end:
        raise HTTPException(status_code=400, detail="The date range ends before it starts")
    conn = get_conn()
    cursor = conn.cursor()
    where, params = [], []
    if action:
        where.append("a.action = ?"); params.append(action)
    if category:
        where.append("a.action LIKE ?"); params.append(f"{category.strip()}.%")
    if target_type:
        where.append("a.target_type = ?"); params.append(target_type)
    if user_id is not None:
        where.append("(a.actor_user_id = ? OR (a.target_type = 'user' AND a.target_id = ?))")
        params += [user_id, str(user_id)]
    if barangay_id:
        where.append("(LOWER(COALESCE(a.actor_barangay_id, u.barangay_id, '')) = LOWER(?) "
                     "OR (a.target_type = 'barangay' AND LOWER(a.target_id) = LOWER(?)))")
        params += [barangay_id, barangay_id]
    if station_id:
        where.append("(COALESCE(a.actor_station_id, u.station_id, '') = ? "
                     "OR (a.target_type = 'station' AND a.target_id = ?))")
        params += [station_id, station_id]
    if start:
        where.append("a.created_at >= ?"); params.append(start)
    if end:
        where.append("a.created_at < ?"); params.append(end)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        where.append("(LOWER(a.action) LIKE ? OR LOWER(a.actor_username) LIKE ? OR LOWER(a.target_id) LIKE ? OR LOWER(COALESCE(a.target_snapshot, '')) LIKE ?)")
        params += [like] * 4
    cursor.execute(
        "SELECT a.*, u.username AS actor_current_username, COALESCE(a.actor_barangay_id, u.barangay_id) AS actor_barangay, "
        "COALESCE(a.actor_station_id, u.station_id) AS actor_station "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id "
        f"{'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY a.created_at DESC LIMIT ?",
        (*params, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        # Stored as a JSON string (see log_audit) -- decoded here so the
        # frontend gets real nested fields, not a string to re-parse itself.
        if r.get("target_snapshot"):
            try:
                r["target_snapshot"] = json.loads(r["target_snapshot"])
            except Exception:
                pass
    return rows

@app.get("/api/devteam/audit_log/facets")
async def devteam_audit_facets(authorization: Optional[str] = Header(None)):
    """The values the audit filters can take, from what's actually logged."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY action")
        actions = [{"action": r["action"], "count": r["n"]} for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT target_type FROM audit_log ORDER BY target_type")
        target_types = [r["target_type"] for r in cursor.fetchall()]
        cursor.execute(
            # The account's current username: the logged one is whatever it
            # was called at the time, and accounts get renamed.
            "SELECT a.actor_user_id AS id, COALESCE(MAX(u.username), MAX(a.actor_username)) AS username, MAX(u.full_name) AS full_name, "
            "MAX(u.role) AS role, COUNT(*) AS n FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id "
            "WHERE a.actor_user_id IS NOT NULL GROUP BY a.actor_user_id ORDER BY username")
        actors = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, name FROM police_stations ORDER BY name")
        stations = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT MIN(created_at) AS first, MAX(created_at) AS last FROM audit_log")
        span = dict(cursor.fetchone())
    finally:
        conn.close()
    categories = sorted({a["action"].split(".")[0] for a in actions if "." in a["action"]})
    return {"actions": actions, "categories": categories, "target_types": target_types,
            "actors": actors, "barangays": barangays, "stations": stations, "span": span}

@app.post("/api/devteam/audit_log/{entry_id}/restore")
async def devteam_restore_audit_entry(entry_id: str, authorization: Optional[str] = Header(None)):
    """Undoes exactly one soft delete -- not a generic action-reverser (see
    log_audit's own design note: this project deliberately chose log +
    recoverable-delete over a generic per-action undo button). Only
    delete-type audit entries whose target is STILL soft-deleted are
    restorable; an already-restored or since-hard-changed target 400s
    with a specific reason rather than silently no-op'ing."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,))
        entry = cursor.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Audit log entry not found")
        entry = dict(entry)
        if not entry["action"].endswith(".deleted"):
            raise HTTPException(status_code=400, detail="Only a delete-type entry can be restored")

        table = {"user": "users", "incident": "incidents"}.get(entry["target_type"])
        if not table:
            raise HTTPException(status_code=400, detail=f"Restoring a '{entry['target_type']}' isn't supported yet")

        cursor.execute(f"SELECT deleted_at FROM {table} WHERE id = ?", (entry["target_id"],))
        current = cursor.fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="The original row no longer exists -- cannot restore")
        if current["deleted_at"] is None:
            raise HTTPException(status_code=400, detail="This was already restored (or never actually soft-deleted)")
        if table == "users":
            # A deleted account's username and admin seat are free for reuse,
            # so either may have been taken since. Say which, rather than
            # letting the unique index raise a bare 500.
            cursor.execute("SELECT * FROM users WHERE id = ?", (entry["target_id"],))
            u = dict(cursor.fetchone())
            cursor.execute("SELECT 1 FROM users WHERE username = ? AND id <> ? AND deleted_at IS NULL", (u["username"], u["id"]))
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail=f"Username '{u['username']}' now belongs to another account")
            seat = {"BARANGAY_ADMIN": "barangay_id", "PNP_ADMIN": "station_id"}.get(u["role"])
            if seat and (u.get("signup_status") or "approved") != "rejected":
                cursor.execute(
                    f"SELECT username FROM users WHERE {seat} = ? AND role = ? AND id <> ? AND deleted_at IS NULL "
                    "AND COALESCE(signup_status, 'approved') <> 'rejected'", (u[seat], u["role"], u["id"]))
                holder = cursor.fetchone()
                if holder:
                    raise HTTPException(status_code=409, detail=f"The {'barangay admin' if seat == 'barangay_id' else 'station admin'} seat is now held by '{holder['username']}'")

        cursor.execute(f"UPDATE {table} SET deleted_at = NULL WHERE id = ?", (entry["target_id"],))
        log_audit(cursor, payload, f"{entry['target_type']}.restored", entry["target_type"], entry["target_id"])
        conn.commit()
        await manager.broadcast({"channel": entry["target_type"] + "s", "event": entry["target_type"] + "_restored", "id": entry["target_id"]})
        return {"status": "restored", "target_type": entry["target_type"], "target_id": entry["target_id"]}
    finally:
        conn.close()

# --- RESOURCE-SCOPED PERMISSIONS (Phase 2, 2026-09-23) ---
# "Dice every permission down to the smallest unit" -- not just "can
# monitor cameras" but "can monitor only THIS camera". Coexists with the
# existing blanket user_permissions grant (see permission_grants' own
# migration comment): a resource-scoped row NARROWS what a user who
# already has (or is exempt from needing) the blanket key can reach, it
# never widens it -- granting a camera-scoped view_map row to someone with
# no camera visibility at all does not, by itself, give them the blanket
# permission back. Two access tiers, per the user's own answer:
#   - a barangay/PNP admin manages ONLY their own subordinates
#     (parent_admin_id ownership, same check PATCH .../permissions uses)
#     and ONLY resources within their own org scope (scope_clause);
#   - DevTeam has master control -- any user, any resource, no ownership
#     check, mirroring the existing override-permissions precedent.
def _resource_grant_target(cursor, user_id: int) -> dict:
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)

def _check_own_subordinate(payload: dict, target: dict):
    if payload["role"] != "DEVTEAM" and target.get("parent_admin_id") != payload["id"]:
        raise HTTPException(status_code=403, detail="You can only manage your own subordinates' access")

def _check_permission_key_allowed(target: dict, permission_key: str):
    if permission_key not in VALID_PERMISSION_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown permission key '{permission_key}'")
    banned = (
        BARANGAY_ONLY_PERMISSIONS if target["role"] in PNP_SIDE_ROLES
        else POLICE_ONLY_PERMISSIONS if target["role"] in BARANGAY_SIDE_ROLES
        else set()
    )
    if permission_key in banned:
        raise HTTPException(status_code=400, detail=f"'{permission_key}' is not available for {target['role']} accounts")

def _check_resource_in_scope(cursor, payload: dict, resource_type: str, resource_id: str):
    """DEVTEAM's scope is unrestricted (master control). A barangay/PNP
    admin can only grant access to a resource that is itself within their
    own jurisdiction -- an admin cannot hand out visibility into a camera
    they cannot see themselves."""
    if payload["role"] == "DEVTEAM":
        return
    if resource_type != "camera":
        raise HTTPException(status_code=400, detail=f"Resource type '{resource_type}' isn't supported yet")
    frag, params = scope_clause(payload)
    if frag == "1 = 0":
        raise HTTPException(status_code=403, detail="Your account has no jurisdiction to grant from")
    cursor.execute(f"SELECT 1 FROM cameras WHERE id = ?{(' AND ' + frag) if frag else ''}", [resource_id] + params)
    if not cursor.fetchone():
        raise HTTPException(status_code=403, detail="That camera is outside your jurisdiction")

def _resource_permissions_handler(payload: dict, user_id: int, method: str, body: Optional[ResourceGrantRequest] = None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _resource_grant_target(cursor, user_id)
        _check_own_subordinate(payload, target)
        if method == "list":
            cursor.execute("SELECT * FROM permission_grants WHERE user_id = ? ORDER BY granted_at DESC", (user_id,))
            return [dict(r) for r in cursor.fetchall()]

        _check_permission_key_allowed(target, body.permission_key)
        if method == "grant":
            _check_resource_in_scope(cursor, payload, body.resource_type, body.resource_id)
            cursor.execute(
                "SELECT 1 FROM permission_grants WHERE user_id=? AND permission_key=? AND resource_type=? AND resource_id=?",
                (user_id, body.permission_key, body.resource_type, body.resource_id),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO permission_grants (id, user_id, permission_key, resource_type, resource_id, granted_by) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), user_id, body.permission_key, body.resource_type, body.resource_id, payload["id"]),
                )
                log_audit(cursor, payload, "permission_grant.created", "user", str(user_id), snapshot={
                    "permission_key": body.permission_key, "resource_type": body.resource_type, "resource_id": body.resource_id,
                })
                conn.commit()
            return {"status": "granted"}
        else:  # revoke
            cursor.execute(
                "DELETE FROM permission_grants WHERE user_id=? AND permission_key=? AND resource_type=? AND resource_id=?",
                (user_id, body.permission_key, body.resource_type, body.resource_id),
            )
            if cursor.rowcount:
                log_audit(cursor, payload, "permission_grant.revoked", "user", str(user_id), snapshot={
                    "permission_key": body.permission_key, "resource_type": body.resource_type, "resource_id": body.resource_id,
                })
                conn.commit()
            return {"status": "revoked"}
    finally:
        conn.close()

# Resource dicing (2026-09-29): every permission can be narrowed along one
# or more dimensions, stored as permission_grants rows of that
# resource_type. No rows for a (key, dimension) pair = no narrowing.
#   camera     -- view_map: which feeds get_cameras() returns and whose
#                 incidents show on the map; manage_cameras: which cameras
#                 _camera_owned_by() lets them configure.
#   crime_type -- which incident types the map / history / video vault
#                 shows, and which alerts they may confirm or dismiss.
#   channel    -- which notification channels they may manage.
RESOURCE_DIMENSIONS = {
    "view_map": ("camera", "crime_type"),
    "manage_cameras": ("camera",),
    "view_records": ("crime_type",),
    "view_history": ("crime_type",),
    "confirm_dismiss_alerts": ("crime_type",),
    "manage_notify_targets": ("channel",),
}
CAMERA_SCOPABLE_KEYS = tuple(k for k, dims in RESOURCE_DIMENSIONS.items() if "camera" in dims)
CRIME_TYPES = ("ASSAULT", "ARMED THREAT", "ROBBERY", "THEFT", "PHYSICAL VIOLENCE", "VANDALISM",
               "HARDWARE_PANIC_INTERRUPT")
# view_records only: continuous/manual footage with no incident attached.
NO_INCIDENT = "NO_INCIDENT"
NOTIFY_CHANNELS = ("telegram", "sms")


def _dimension_values(key: str, rtype: str) -> tuple:
    if rtype == "crime_type":
        return CRIME_TYPES + ((NO_INCIDENT,) if key == "view_records" else ())
    if rtype == "channel":
        return NOTIFY_CHANNELS
    return ()


def _scoped_resource_ids(cursor, payload: dict, key: str, rtype: str) -> Optional[set]:
    """Resource ids this user's `key` is diced down to along `rtype`, or
    None when it isn't narrowed. DEVTEAM is never narrowed."""
    if payload.get("role") == "DEVTEAM":
        return None
    cursor.execute(
        "SELECT resource_id FROM permission_grants WHERE user_id = ? AND permission_key = ? AND resource_type = ?",
        (payload["id"], key, rtype),
    )
    ids = {r["resource_id"] for r in cursor.fetchall()}
    return ids or None


def _crime_type_allowed(cursor, payload: dict, key: str, incident_type: Optional[str]) -> bool:
    allowed = _scoped_resource_ids(cursor, payload, key, "crime_type")
    if allowed is None:
        return True
    t = (incident_type or "").strip().upper() or NO_INCIDENT
    return t in allowed


def _record_detection_feedback(cursor, payload: dict, incident_id: str, label: str, final_type: Optional[str] = None):
    """Stores an operator verdict on an AI alert as a training label.
    Manual and panic-button incidents are skipped -- no model made those
    calls, so there's nothing to learn from agreeing or disagreeing."""
    if label not in ("confirmed", "dismissed"):
        return
    try:
        cursor.execute(
            """SELECT i.type, i.source, i.camera_id, i.barangay_id, i.confidence, d.ai_context, v.screenshot_path
               FROM incidents i
               LEFT JOIN incident_details d ON d.incident_id = i.id
               LEFT JOIN incident_visibility v ON v.incident_id = i.id
               WHERE i.id = ?""",
            (incident_id,),
        )
        row = cursor.fetchone()
        if not row or row["source"] != "AI_AUTOMATION":
            return
        cursor.execute("SELECT ai_event FROM detection_feedback WHERE incident_id = ?", (incident_id,))
        existing = cursor.fetchone()
        final = (final_type or row["type"] or "").strip().upper() or None
        if existing:
            # ai_event stays what the model originally said, even after a re-type.
            cursor.execute(
                "UPDATE detection_feedback SET label = ?, final_type = ?, decided_by = ?, decided_by_username = ?, "
                "decided_at = NOW(), exported_at = NULL WHERE incident_id = ?",
                (label, final, payload.get("id"), payload.get("username"), incident_id),
            )
        else:
            cursor.execute(
                "INSERT INTO detection_feedback (incident_id, camera_id, barangay_id, ai_event, final_type, label, confidence, "
                "ai_context, screenshot, decided_by, decided_by_username) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (incident_id, row["camera_id"], row["barangay_id"], (row["type"] or "").upper(), final, label,
                 row["confidence"], row["ai_context"], row["screenshot_path"], payload.get("id"), payload.get("username")),
            )
    except Exception as e:
        # Training data is a by-product; never let it fail the verdict itself.
        print(f"⚠️  [FEEDBACK] Could not record verdict for {incident_id}: {e}")


def _require_incident_type_access(cursor, payload: dict, incident_id: str, key: str):
    """404 (same as out-of-jurisdiction) when `key` is diced to crime
    types and this incident's type isn't one of them."""
    cursor.execute("SELECT type FROM incidents WHERE id = ?", (incident_id,))
    row = cursor.fetchone()
    if row and not _crime_type_allowed(cursor, payload, key, row["type"]):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your access)")


def _apply_resource_scopes(cursor, payload: dict, target: dict, scopes: dict):
    """Replaces a user's dicing. `scopes` is {permission_key: {resource_type:
    [ids] | None}}; None clears that dimension, a list keeps exactly those.
    An empty list is refused rather than stored -- zero grant rows means
    "everything", the opposite of what an empty selection looks like."""
    for key, dims in (scopes or {}).items():
        if key not in RESOURCE_DIMENSIONS:
            raise HTTPException(status_code=400, detail=f"'{key}' can't be narrowed")
        if not isinstance(dims, dict):
            raise HTTPException(status_code=400, detail=f"Scopes for '{key}' must be an object of resource types")
        _check_permission_key_allowed(target, key)
        for rtype, ids in dims.items():
            if rtype not in RESOURCE_DIMENSIONS[key]:
                raise HTTPException(status_code=400, detail=f"'{key}' can't be narrowed by {rtype}")
            cursor.execute(
                "DELETE FROM permission_grants WHERE user_id = ? AND permission_key = ? AND resource_type = ?",
                (target["id"], key, rtype),
            )
            if ids is None:
                continue
            wanted = list(dict.fromkeys(str(i) for i in ids))
            if not wanted:
                raise HTTPException(status_code=400, detail=f"{key}: pick at least one {rtype.replace('_', ' ')}, or allow all")
            if rtype == "camera":
                placeholders = ",".join("?" for _ in wanted)
                if target.get("station_id"):
                    cursor.execute(
                        f"SELECT id FROM cameras WHERE id IN ({placeholders}) AND barangay_id IN "
                        "(SELECT barangay_id FROM station_barangays WHERE station_id = ?)",
                        (*wanted, target["station_id"]),
                    )
                else:
                    cursor.execute(
                        f"SELECT id FROM cameras WHERE id IN ({placeholders}) AND LOWER(barangay_id) = LOWER(?)",
                        (*wanted, target.get("barangay_id") or ""),
                    )
                in_scope = {r["id"] for r in cursor.fetchall()}
                outside = [i for i in wanted if i not in in_scope]
                if outside:
                    raise HTTPException(status_code=400, detail=f"Camera(s) outside this account's jurisdiction: {', '.join(outside)}")
            else:
                valid = _dimension_values(key, rtype)
                unknown = [i for i in wanted if i not in valid]
                if unknown:
                    raise HTTPException(status_code=400, detail=f"Unknown {rtype.replace('_', ' ')}(s): {', '.join(unknown)}")
            for rid in wanted:
                cursor.execute(
                    "INSERT INTO permission_grants (id, user_id, permission_key, resource_type, resource_id, granted_by) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), target["id"], key, rtype, rid, payload["id"]),
                )
    log_audit(cursor, payload, "permission_grant.scopes_set", "user", str(target["id"]), snapshot={"scopes": scopes})


@app.put("/api/devteam/users/{user_id}/resource_scopes")
async def devteam_set_resource_scopes(user_id: int, body: ResourceScopesUpdate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _resource_grant_target(cursor, user_id)
        if target["role"] == "DEVTEAM":
            raise HTTPException(status_code=400, detail="DevTeam access is never narrowed")
        _apply_resource_scopes(cursor, payload, target, body.scopes)
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id}


@app.put("/api/admin/users/{user_id}/resource_scopes")
async def admin_set_resource_scopes(user_id: int, body: ResourceScopesUpdate, authorization: Optional[str] = Header(None)):
    """The same dicing DevTeam has (2026-10-01), for a barangay/PNP admin's
    own staff: "View Crime Map, but only these cameras and these crime
    types". Capped at the admin's own reach -- an admin who has been diced
    down themselves can't hand a subordinate more than they hold."""
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _resource_grant_target(cursor, user_id)
        _check_own_subordinate(payload, target)
        for key, dims in (body.scopes or {}).items():
            if not isinstance(dims, dict):
                continue
            for rtype, ids in dims.items():
                ceiling = _scoped_resource_ids(cursor, payload, key, rtype)
                if ceiling is None:
                    continue
                if ids is None:
                    raise HTTPException(status_code=403, detail=f"Your own {key} access is limited, so you can't give {rtype.replace('_', ' ')} access to everything")
                beyond = [str(i) for i in ids if str(i) not in ceiling]
                if beyond:
                    raise HTTPException(status_code=403, detail=f"Outside your own access: {', '.join(beyond)}")
        _apply_resource_scopes(cursor, payload, target, body.scopes)
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id}

@app.get("/api/admin/users/{user_id}/resource_permissions")
async def admin_list_resource_permissions(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "list")

@app.post("/api/admin/users/{user_id}/resource_permissions")
async def admin_grant_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "grant", body)

@app.delete("/api/admin/users/{user_id}/resource_permissions")
async def admin_revoke_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "revoke", body)

@app.get("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_list_resource_permissions(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "list")

@app.post("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_grant_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "grant", body)

@app.delete("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_revoke_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "revoke", body)

@app.get("/api/devteam/cameras/list_for_grants")
async def devteam_cameras_for_grants(authorization: Optional[str] = Header(None)):
    """Tiny helper for the Permissions UI's camera picker -- every camera,
    DevTeam-unscoped, with enough context (name + barangay) to pick from
    without cross-referencing the Cameras tab separately."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, barangay_id FROM cameras ORDER BY barangay_id, name")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# --- IDENTITY VERIFICATION (Phase 4, 2026-09-23) ---
# Every user can attach a government ID; an admin (their own subordinates)
# or DevTeam (anyone) confirms it. See VERIFICATION_DOCS_DIR's own comment
# for why this is never a static mount, and report_requests' migration
# comment (Phase 3, same session) for the broader pattern this codebase
# now follows of using an audited, human-reviewed handoff instead of
# reaching for a permission grant every time two roles need to share
# something. Deliberately does not block login -- these accounts are
# already either admin-vetted at creation or barangay-approval-gated for
# self-signup; this is an additional trust signal DevTeam/admins can act
# on, not a second gate on top of those.
@app.post("/api/users/me/verification")
@limiter.limit("5/minute")
async def upload_my_verification(request: Request, id_document: Optional[UploadFile] = File(None),
                                 face_photo: Optional[UploadFile] = File(None),
                                 authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if id_document is not None:
            filename = _save_verification_document(payload["id"], id_document)
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending', verified_by = NULL, verified_at = NULL WHERE id = ?",
                (filename, payload["id"]),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(payload["id"], face_photo, kind="face"), payload["id"]))
        log_audit(cursor, payload, "user.verification_submitted", "user", str(payload["id"]))
        conn.commit()
        return {"status": "submitted"}
    finally:
        conn.close()

@app.post("/api/devteam/users/{user_id}/identity_files")
async def devteam_upload_identity_files(user_id: int, id_document: Optional[UploadFile] = File(None),
                                        face_photo: Optional[UploadFile] = File(None),
                                        authorization: Optional[str] = Header(None)):
    """DevTeam attaches an ID or face photo to any account -- e.g. a paper
    ID handed in at the station, or replacing a blurry photo. A new ID
    resets verification to pending, same as when the owner uploads one."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        if id_document is not None:
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending', verified_by = NULL, verified_at = NULL WHERE id = ?",
                (_save_verification_document(user_id, id_document), user_id),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(user_id, face_photo, kind="face"), user_id))
        log_audit(cursor, payload, "user.identity_files_uploaded", "user", str(user_id),
                  snapshot={"id_document": id_document is not None, "face_photo": face_photo is not None})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "user_edited", "id": user_id})
    return {"status": "uploaded"}


@app.get("/api/users/{user_id}/verification_document")
async def get_verification_document(user_id: int, authorization: Optional[str] = Header(None)):
    """The one new sensitive-data endpoint in this whole feature -- ownership
    is checked explicitly (self, own subordinate via parent_admin_id, or
    DEVTEAM) rather than reusing require_permission()'s key-based model,
    since 'can see this specific person's ID document' isn't a permission
    key anyone should be able to grant around -- it's strictly who they are
    to that account."""
    return _serve_user_file(require_auth(authorization), user_id, "id_document_path")


@app.get("/api/users/{user_id}/face_photo")
async def get_face_photo(user_id: int, authorization: Optional[str] = Header(None)):
    """Same guard as the ID document -- a face photo is identity data too."""
    return _serve_user_file(require_auth(authorization), user_id, "face_photo_path")


def _serve_user_file(payload: dict, user_id: int, column: str):
    if column not in ("id_document_path", "face_photo_path"):
        raise HTTPException(status_code=400, detail="Unknown file")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT id, parent_admin_id, {column} AS path FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        target = dict(target)
        allowed = (
            payload["role"] == "DEVTEAM"
            or payload["id"] == user_id
            or target.get("parent_admin_id") == payload["id"]
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Not authorized to view this document")
        if not target["path"]:
            raise HTTPException(status_code=404, detail="Nothing uploaded")
        real_dir = os.path.realpath(VERIFICATION_DOCS_DIR)
        real_path = os.path.realpath(os.path.join(real_dir, target["path"]))
        if os.path.commonpath([real_dir, real_path]) != real_dir or not os.path.isfile(real_path):
            raise HTTPException(status_code=404, detail="File missing from disk")
        return FileResponse(real_path)
    finally:
        conn.close()

def _review_verification(payload: dict, user_id: int, body: VerificationReview, is_devteam_route: bool):
    if body.decision not in ("verified", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'verified' or 'rejected'")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        target = dict(target)
        if not is_devteam_route and target.get("parent_admin_id") != payload["id"]:
            raise HTTPException(status_code=403, detail="You can only review your own subordinates")
        if not target["id_document_path"]:
            raise HTTPException(status_code=400, detail="No document has been uploaded yet")
        cursor.execute(
            "UPDATE users SET verification_status = ?, verified_by = ?, verified_at = NOW() WHERE id = ?",
            (body.decision, payload["id"], user_id),
        )
        log_audit(cursor, payload, f"user.verification_{body.decision}", "user", str(user_id), snapshot={"note": body.note})
        conn.commit()
        return {"status": body.decision}
    finally:
        conn.close()

@app.post("/api/admin/users/{user_id}/verification")
async def admin_review_verification(user_id: int, body: VerificationReview, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _review_verification(payload, user_id, body, is_devteam_route=False)

@app.post("/api/devteam/users/{user_id}/verification")
async def devteam_review_verification(user_id: int, body: VerificationReview, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _review_verification(payload, user_id, body, is_devteam_route=True)

# --- CUSTOM ROLES (Phase 2, 2026-09-23) ---
# A NAMED PERMISSION PRESET layered on the real BARANGAY_STAFF/PNP_OFFICER
# tier -- not a new DB-level role. See custom_roles' own migration comment
# for why: the account stays a real operator for every scope/nav/
# constraint purpose, only display_title + which permissions get
# pre-applied at creation time come from the role. DevTeam-managed only
# (mirrors the "Create Role" UI living in DevteamView's Configuration
# section) -- listing is open to any authenticated admin so their own
# Create User form can offer the roles DevTeam has defined for their org
# side, but creating/deleting one is DevTeam-only.
@app.get("/api/custom_roles")
async def list_custom_roles(org_type: Optional[str] = None, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()
    if org_type:
        cursor.execute("SELECT * FROM custom_roles WHERE org_type = ? OR org_type IS NULL ORDER BY name", (org_type,))
    else:
        cursor.execute("SELECT * FROM custom_roles ORDER BY name")
    roles = [dict(r) for r in cursor.fetchall()]
    if roles:
        placeholders = ",".join("?" for _ in roles)
        cursor.execute(
            f"SELECT * FROM custom_role_permission_defaults WHERE role_id IN ({placeholders})",
            tuple(r["id"] for r in roles),
        )
        defaults_by_role: dict = {}
        for row in cursor.fetchall():
            defaults_by_role.setdefault(row["role_id"], []).append(dict(row))
        for r in roles:
            r["permission_defaults"] = defaults_by_role.get(r["id"], [])
    conn.close()
    return roles

@app.post("/api/devteam/custom_roles")
async def create_custom_role(body: CustomRoleCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    org_type = (body.org_type or "").strip().lower() or None
    if org_type not in (None, "barangay", "police"):
        raise HTTPException(status_code=400, detail="org_type must be 'barangay', 'police', or omitted")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Role name is required")

    # A side-bound role is still limited to what its side can hold. A
    # side-agnostic role (org_type None) stores every permission picked;
    # devteam_create_user drops whatever the account's side can't hold.
    banned = (BARANGAY_ONLY_PERMISSIONS if org_type == "police"
              else POLICE_ONLY_PERMISSIONS if org_type == "barangay" else set())

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM custom_roles WHERE LOWER(name) = LOWER(?)", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A role named "{name}" already exists')
        role_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO custom_roles (id, name, org_type, created_by) VALUES (?, ?, ?, ?)",
            (role_id, name, org_type, payload["id"]),
        )
        for key, granted in (body.permissions or {}).items():
            if granted and key in VALID_PERMISSION_KEYS and key not in banned:
                cursor.execute(
                    "INSERT INTO custom_role_permission_defaults (role_id, permission_key, resource_type, resource_id) "
                    "VALUES (?, ?, NULL, NULL)",
                    (role_id, key),
                )
        for key, dims in (body.scopes or {}).items():
            if not (body.permissions or {}).get(key) or key not in RESOURCE_DIMENSIONS or key in banned or not isinstance(dims, dict):
                continue
            for rtype, ids in dims.items():
                if rtype == "camera" or rtype not in RESOURCE_DIMENSIONS[key] or ids is None:
                    continue
                valid = _dimension_values(key, rtype)
                wanted = [i for i in dict.fromkeys(str(i) for i in ids) if i in valid]
                if not wanted:
                    raise HTTPException(status_code=400, detail=f"{key}: pick at least one {rtype.replace('_', ' ')}, or allow all")
                for rid in wanted:
                    cursor.execute(
                        "INSERT INTO custom_role_permission_defaults (role_id, permission_key, resource_type, resource_id) "
                        "VALUES (?, ?, ?, ?)",
                        (role_id, key, rtype, rid),
                    )
        log_audit(cursor, payload, "custom_role.created", "custom_role", role_id, snapshot={"name": name, "org_type": org_type})
        conn.commit()
        return {"status": "created", "id": role_id, "name": name, "org_type": org_type}
    finally:
        conn.close()

@app.delete("/api/devteam/custom_roles/{role_id}")
async def delete_custom_role(role_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE custom_role_id = ? AND deleted_at IS NULL", (role_id,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Accounts still use this role -- it can't be deleted while assigned.")
    cursor.execute("SELECT name FROM custom_roles WHERE id = ?", (role_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Role not found")
    cursor.execute("DELETE FROM custom_role_permission_defaults WHERE role_id = ?", (role_id,))
    cursor.execute("DELETE FROM custom_roles WHERE id = ?", (role_id,))
    log_audit(cursor, payload, "custom_role.deleted", "custom_role", role_id, snapshot={"name": existing["name"]})
    conn.commit()
    conn.close()
    return {"status": "deleted"}

# --- REPORT REQUESTS (Phase 3, 2026-09-23) ---
# Barangay formally requests a specific report/crime record from police;
# police accepts (or declines) and hands the actual information back via
# response_note. See report_requests' own migration comment for why this
# does NOT grant any view_history access -- #5's ban on that for barangay
# accounts is unconditional and stays that way here too.
def _report_request_details(body: "ReportRequestCreate") -> dict:
    """Validates the structured fields and returns what gets stored. Empty
    strings are dropped so the station's view shows only what was given."""
    def clean(v, limit=500):
        v = (v or "").strip()
        if len(v) > limit:
            raise HTTPException(status_code=400, detail=f"Keep each field under {limit} characters")
        return v or None

    def iso_date(v, label):
        v = clean(v, 10)
        if v is None:
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} must be a date (YYYY-MM-DD)")

    out = {
        "report_type": clean(body.report_type, 40),
        "crime_type": (clean(body.crime_type, 40) or "").upper() or None,
        "period_from": iso_date(body.period_from, "Period start"),
        "period_to": iso_date(body.period_to, "Period end"),
        "location": clean(body.location, 200),
        "persons_involved": clean(body.persons_involved, 300),
        "reference": clean(body.reference, 100),
        "purpose": clean(body.purpose, 40),
        "purpose_detail": clean(body.purpose_detail, 300),
        "urgency": clean(body.urgency, 10) or "routine",
        "needed_by": iso_date(body.needed_by, "Needed-by date"),
    }
    if out["report_type"] and out["report_type"] not in REPORT_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail="Unknown report type")
    if out["purpose"] and out["purpose"] not in REPORT_REQUEST_PURPOSES:
        raise HTTPException(status_code=400, detail="Unknown purpose")
    if out["purpose"] == "other" and not out["purpose_detail"]:
        raise HTTPException(status_code=400, detail="Say what the report is for")
    if out["crime_type"] and out["crime_type"] not in REPORT_REQUEST_CRIMES:
        raise HTTPException(status_code=400, detail="Unknown crime type")
    if out["urgency"] not in REPORT_REQUEST_URGENCY:
        raise HTTPException(status_code=400, detail="Urgency must be routine or urgent")
    if out["period_from"] and out["period_to"] and out["period_from"] > out["period_to"]:
        raise HTTPException(status_code=400, detail="The period ends before it starts")
    today = datetime.now().date().isoformat()
    if out["period_from"] and out["period_from"] > today:
        raise HTTPException(status_code=400, detail="The period can't start in the future")
    if out["needed_by"] and out["needed_by"] < today:
        raise HTTPException(status_code=400, detail="The needed-by date has already passed")
    if out["urgency"] == "routine" and not body.report_type:
        out.pop("urgency")  # a legacy description-only request: store nothing extra
    return {k: v for k, v in out.items() if v is not None}

@app.post("/api/report_requests")
async def create_report_request(body: ReportRequestCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, BARANGAY_SIDE_ROLES)
    barangay_id = payload.get("barangay_id")
    if not barangay_id:
        raise HTTPException(status_code=400, detail="Your account has no barangay assigned")
    description = (body.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Describe what you're requesting")
    details = _report_request_details(body)

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if body.incident_id:
            # Can only be tied to an incident this barangay can actually see
            # -- reuses the same scope_clause() GET /api/incidents applies,
            # so a request can't be used to probe for the existence of an
            # incident outside the caller's own jurisdiction.
            frag, params = scope_clause(payload)
            where = f"id = ? AND deleted_at IS NULL" + (f" AND {frag}" if frag else "")
            cursor.execute(f"SELECT 1 FROM incidents WHERE {where}", [body.incident_id] + params)
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Incident not found")

        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ? LIMIT 1", (barangay_id,))
        row = cursor.fetchone()
        station_id = row["station_id"] if row else None

        request_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO report_requests (id, barangay_id, station_id, incident_id, description, requested_by, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, barangay_id, station_id, body.incident_id, description, payload["id"],
             json.dumps(details) if details else None),
        )
        log_audit(cursor, payload, "report_request.created", "report_request", request_id, snapshot={
            "barangay_id": barangay_id, "incident_id": body.incident_id, "description": description,
            "details": details,
        })
        conn.commit()
        if station_id:
            await manager.broadcast({"channel": "report_requests", "event": "created", "id": request_id, "station_id": station_id})
        return {"status": "created", "id": request_id, "station_id": station_id}
    finally:
        conn.close()

@app.get("/api/report_requests")
async def list_report_requests(authorization: Optional[str] = Header(None)):
    """Role-branched, not scope-param-driven: a barangay account only ever
    has an outbox (things it asked for) and a PNP account only ever has an
    inbox (things asked of it) -- there's no ambiguity to resolve with a
    query param the way notify_targets' single shared table needed one."""
    payload = require_auth(authorization)
    role = payload["role"]
    conn = get_conn()
    cursor = conn.cursor()
    if role == "DEVTEAM":
        cursor.execute("SELECT * FROM report_requests ORDER BY requested_at DESC")
    elif role in BARANGAY_SIDE_ROLES:
        cursor.execute("SELECT * FROM report_requests WHERE barangay_id = ? ORDER BY requested_at DESC", (payload.get("barangay_id"),))
    elif role in PNP_SIDE_ROLES:
        cursor.execute("SELECT * FROM report_requests WHERE station_id = ? ORDER BY requested_at DESC", (payload.get("station_id"),))
    else:
        conn.close()
        return []
    rows = [dict(r) for r in cursor.fetchall()]
    # Who asked, by name, and the barangay's real name -- the station sees
    # requests from several barangays and several people in each.
    user_ids = {r["requested_by"] for r in rows} | {r["responded_by"] for r in rows if r.get("responded_by")}
    names: dict = {}
    if user_ids:
        ph = ",".join("?" for _ in user_ids)
        cursor.execute(f"SELECT id, username, full_name, position FROM users WHERE id IN ({ph})", tuple(user_ids))
        names = {u["id"]: dict(u) for u in cursor.fetchall()}
    brgy_ids = {r["barangay_id"] for r in rows}
    brgy_names: dict = {}
    if brgy_ids:
        ph = ",".join("?" for _ in brgy_ids)
        cursor.execute(f"SELECT id, name FROM barangays WHERE id IN ({ph})", tuple(brgy_ids))
        brgy_names = {b["id"]: b["name"] for b in cursor.fetchall()}
    conn.close()
    files_by_req: dict = {}
    if rows:
        conn = get_conn()
        cursor = conn.cursor()
        ph = ",".join("?" for _ in rows)
        cursor.execute(f"SELECT id, request_id, original_name, content_type, size_bytes, uploaded_at FROM report_request_files "
                       f"WHERE request_id IN ({ph}) ORDER BY uploaded_at", tuple(r["id"] for r in rows))
        for f in cursor.fetchall():
            files_by_req.setdefault(f["request_id"], []).append(dict(f))
        conn.close()
    for r in rows:
        try:
            r["details"] = json.loads(r["details"]) if r.get("details") else {}
        except (TypeError, ValueError):
            r["details"] = {}
        try:
            r["shared_report"] = json.loads(r["shared_report"]) if r.get("shared_report") else None
        except (TypeError, ValueError):
            r["shared_report"] = None
        r["files"] = files_by_req.get(r["id"], [])
        who = names.get(r["requested_by"]) or {}
        r["requested_by_name"] = who.get("full_name") or who.get("username")
        r["requested_by_position"] = who.get("position")
        responder = names.get(r.get("responded_by")) or {}
        r["responded_by_name"] = responder.get("full_name") or responder.get("username")
        r["barangay_name"] = brgy_names.get(r["barangay_id"]) or r["barangay_id"]
    return rows

@app.get("/api/report_requests/options")
async def report_request_options(authorization: Optional[str] = Header(None)):
    """The request form's choices, from the same tables the server validates
    against, so the two can't drift."""
    require_auth(authorization)
    return {
        "report_types": [{"value": k, "label": v} for k, v in REPORT_REQUEST_TYPES.items()],
        "purposes": [{"value": k, "label": v} for k, v in REPORT_REQUEST_PURPOSES.items()],
        "crime_types": sorted(REPORT_REQUEST_CRIMES - {"ANY", "OTHER"}),
    }

def _respond_to_report_request(payload: dict, request_id: str, new_status: str, note: Optional[str], require_current: str,
                               shared_report: Optional[dict] = None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
        req = cursor.fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        req = dict(req)
        if payload["role"] != "DEVTEAM" and req["station_id"] != payload.get("station_id"):
            raise HTTPException(status_code=403, detail="This request wasn't routed to your station")
        if req["status"] != require_current:
            raise HTTPException(status_code=400, detail=f"Request is '{req['status']}', not '{require_current}'")
        cursor.execute(
            "UPDATE report_requests SET status = ?, responded_by = ?, responded_at = NOW(), response_note = ?, "
            "shared_report = COALESCE(?, shared_report) WHERE id = ?",
            (new_status, payload["id"], note, json.dumps(shared_report) if shared_report else None, request_id),
        )
        log_audit(cursor, payload, f"report_request.{new_status}", "report_request", request_id,
                  snapshot={"note": note, "shared": [f["key"] for f in shared_report["fields"]] if shared_report else None,
                            "incident_id": shared_report.get("incident_id") if shared_report else None})
        conn.commit()
        return {"status": new_status}
    finally:
        conn.close()

# Report fields police may hand to a barangay, in display order. "where"
# says which record the value comes from.
SHAREABLE_REPORT_FIELDS = [
    ("case_id", "Case number", "incident"),
    ("incident_type", "Incident type", "report"),
    ("occurred", "Date and time", "incident"),
    ("location", "Location", "incident"),
    ("nature_of_incident", "Nature of incident", "report"),
    ("narrative", "Summary", "report"),
    ("property_damaged", "Property damaged", "report"),
    ("evidence_secured", "Evidence secured", "report"),
    ("action_taken", "Action taken", "report"),
    ("disposition", "Disposition", "report"),
    ("complainant", "Complainant", "report"),
    ("victim_details", "Victim", "report"),
    ("suspect_description", "Suspect", "report"),
    ("witnesses", "Witnesses", "report"),
    ("reporting_officer", "Reporting officer", "report"),
    ("rank", "Rank", "report"),
    ("badge_number", "Badge number", "report"),
]
SHAREABLE_KEYS = {k for k, _, _ in SHAREABLE_REPORT_FIELDS}
REQUEST_FILE_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                      ".webp": "image/webp",
                      ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_REQUEST_FILE_BYTES = 15 * 1024 * 1024
MAX_REQUEST_FILES = 10


def _shareable_report(cursor, payload: dict, request: dict, incident_id: str) -> tuple:
    """The incident and its latest filed report, checked: in the requesting
    barangay, inside the caller's jurisdiction and crime-type access."""
    frag, params = scope_clause(payload)
    where = "id = ? AND deleted_at IS NULL" + (f" AND {frag}" if frag else "")
    cursor.execute(f"SELECT * FROM incidents WHERE {where}", [incident_id] + params)
    inc = cursor.fetchone()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found in your jurisdiction")
    inc = dict(inc)
    if (inc.get("barangay_id") or "").lower() != (request.get("barangay_id") or "").lower():
        raise HTTPException(status_code=400, detail="That incident isn't in the barangay that asked")
    if not _crime_type_allowed(cursor, payload, "view_history", inc.get("type")):
        raise HTTPException(status_code=403, detail="Your access doesn't cover that type of incident")
    cursor.execute(
        "SELECT * FROM incident_reports WHERE incident_id = ? "
        "ORDER BY CASE WHEN report_status = 'confirmed' THEN 0 ELSE 1 END, COALESCE(updated_at, created_at) DESC LIMIT 1",
        (incident_id,))
    row = cursor.fetchone()
    report = _parse_report_row(row) if row else None
    return inc, report


def _build_shared_report(inc: dict, report: Optional[dict], keys: list, summary: Optional[str]) -> dict:
    body = (report or {}).get("report_body") or {}
    if report and report.get("report_status") != "confirmed":
        raise HTTPException(status_code=400, detail="That incident's report is still a draft -- confirm it before sharing")
    values = {
        "case_id": inc.get("case_id"),
        "occurred": f"{inc.get('occurred_date')} {_format_12h(inc.get('occurred_time') or '')}".strip(),
        "location": inc.get("location_name"),
        "incident_type": body.get("incident_type") or inc.get("type"),
    }
    unknown = [k for k in keys if k not in SHAREABLE_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Can't share: {', '.join(map(str, unknown))}")
    fields = []
    for key, label, where in SHAREABLE_REPORT_FIELDS:
        if key not in keys:
            continue
        value = summary.strip() if key == "narrative" and summary and summary.strip() else \
            (values.get(key) if where == "incident" or key == "incident_type" else body.get(key))
        if value not in (None, ""):
            fields.append({"key": key, "label": label, "value": str(value)})
    if not fields:
        raise HTTPException(status_code=400, detail="Choose at least one field that has a value")
    return {"incident_id": inc["id"], "case_id": inc.get("case_id"), "fields": fields,
            "summary_edited": bool(summary and summary.strip() and summary.strip() != (body.get("narrative") or "").strip())}


def _request_for_station(cursor, payload: dict, request_id: str) -> dict:
    cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req = dict(req)
    if payload["role"] != "DEVTEAM" and req["station_id"] != payload.get("station_id"):
        raise HTTPException(status_code=403, detail="This request wasn't routed to your station")
    return req


def _require_report_sharer(payload: dict):
    """Answering a request means handing over what's in the crime history,
    so it takes view_history -- an officer with no permissions at all could
    accept, decline and fulfil requests before 2026-10-01."""
    conn = get_conn()
    try:
        require_permission(conn.cursor(), payload, "view_history")
    finally:
        conn.close()


@app.post("/api/report_requests/{request_id}/accept")
async def accept_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    result = _respond_to_report_request(payload, request_id, "accepted", body.note, require_current="pending")
    await manager.broadcast({"channel": "report_requests", "event": "accepted", "id": request_id})
    return result

@app.post("/api/report_requests/{request_id}/decline")
async def decline_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    result = _respond_to_report_request(payload, request_id, "declined", body.note, require_current="pending")
    await manager.broadcast({"channel": "report_requests", "event": "declined", "id": request_id})
    return result

@app.post("/api/report_requests/{request_id}/fulfill")
async def fulfill_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    """The actual deliverable: police hands the requested information back
    as response_note, visible to the requesting barangay through this same
    request record -- a one-time, audited handoff rather than a standing
    grant into the archive itself."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        shared = None
        if body.incident_id:
            inc, report = _shareable_report(cursor, payload, req, body.incident_id)
            shared = _build_shared_report(inc, report, list(body.share_fields or []), body.summary)
            shared.update(shared_by=payload.get("username"), shared_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        cursor.execute("SELECT COUNT(*) AS n FROM report_request_files WHERE request_id = ?", (request_id,))
        files = cursor.fetchone()["n"]
    finally:
        conn.close()
    if not (body.note or "").strip() and not shared and not files:
        raise HTTPException(status_code=400, detail="Hand over something: share a report, attach a file, or write the information")
    result = _respond_to_report_request(payload, request_id, "fulfilled", (body.note or "").strip() or None,
                                        require_current="accepted", shared_report=shared)
    await manager.broadcast({"channel": "report_requests", "event": "fulfilled", "id": request_id})
    return result


@app.get("/api/report_requests/{request_id}/shareable")
async def report_request_shareable(request_id: str, authorization: Optional[str] = Header(None)):
    """Incidents in the asking barangay that police could answer with, and
    the fields each one's report can share."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        sql, params = apply_scope(payload, "SELECT * FROM incidents", [],
                                  extra_where="deleted_at IS NULL AND LOWER(barangay_id) = LOWER(?)",
                                  extra_params=[req["barangay_id"]])
        cursor.execute(sql + " ORDER BY occurred_date DESC, occurred_time DESC LIMIT 200", tuple(params))
        incs = [dict(r) for r in cursor.fetchall()
                if _crime_type_allowed(cursor, payload, "view_history", r["type"])]
        ids = [i["id"] for i in incs]
        reports: dict = {}
        if ids:
            ph = ",".join("?" for _ in ids)
            cursor.execute(f"SELECT incident_id, report_status FROM incident_reports WHERE incident_id IN ({ph})", tuple(ids))
            for r in cursor.fetchall():
                if r["report_status"] == "confirmed" or r["incident_id"] not in reports:
                    reports[r["incident_id"]] = r["report_status"]
    finally:
        conn.close()
    return {
        "fields": [{"key": k, "label": l} for k, l, _ in SHAREABLE_REPORT_FIELDS],
        "incidents": [{"id": i["id"], "case_id": i["case_id"], "type": i["type"], "status": i["status"],
                       "occurred_date": i["occurred_date"], "occurred_time": i["occurred_time"],
                       "location_name": i["location_name"], "report_status": reports.get(i["id"])} for i in incs],
    }


@app.get("/api/report_requests/{request_id}/share_preview")
async def report_request_share_preview(request_id: str, incident_id: str, authorization: Optional[str] = Header(None)):
    """Every shareable field of one incident's report with its value, so the
    officer sees exactly what each tick would hand over."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        inc, report = _shareable_report(cursor, payload, req, incident_id)
    finally:
        conn.close()
    if report and report.get("report_status") != "confirmed":
        return {"report_status": "draft", "fields": []}
    full = _build_shared_report(inc, report, list(SHAREABLE_KEYS), None) if report or inc else None
    return {"report_status": "confirmed" if report else None, "fields": full["fields"] if full else []}


def _request_file_access(cursor, payload: dict, request_id: str) -> dict:
    """The asking barangay and the answering station both see the files."""
    cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req = dict(req)
    role = payload["role"]
    if role == "DEVTEAM":
        return req
    if role in BARANGAY_SIDE_ROLES and (req["barangay_id"] or "").lower() == (payload.get("barangay_id") or "").lower():
        return req
    if role in PNP_SIDE_ROLES and req["station_id"] == payload.get("station_id"):
        return req
    raise HTTPException(status_code=404, detail="Request not found")


@app.post("/api/report_requests/{request_id}/files")
@limiter.limit("20/minute")
async def upload_report_request_file(request: Request, request_id: str, file: UploadFile = File(...),
                                     authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    original = os.path.basename(file.filename or "").strip() or "file"
    ext = os.path.splitext(original)[1].lower()
    if ext not in REQUEST_FILE_TYPES:
        raise HTTPException(status_code=400, detail="Attach a PDF, Word (.docx) or image (JPG, PNG, WEBP) file")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail="Accept the request before attaching files")
        cursor.execute("SELECT COUNT(*) AS n FROM report_request_files WHERE request_id = ?", (request_id,))
        if cursor.fetchone()["n"] >= MAX_REQUEST_FILES:
            raise HTTPException(status_code=400, detail=f"At most {MAX_REQUEST_FILES} files per request")
        file_id = str(uuid.uuid4())
        stored = f"{file_id}{ext}"
        dest = os.path.join(REQUEST_FILES_DIR, stored)
        total = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_REQUEST_FILE_BYTES:
                        raise HTTPException(status_code=400, detail="File too large (15 MB max)")
                    f.write(chunk)
        except HTTPException:
            if os.path.exists(dest):
                os.remove(dest)
            raise
        if total == 0:
            os.remove(dest)
            raise HTTPException(status_code=400, detail="That file is empty")
        cursor.execute(
            "INSERT INTO report_request_files (id, request_id, stored_name, original_name, content_type, size_bytes, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, request_id, stored, original[:200], REQUEST_FILE_TYPES[ext], total, payload["id"]))
        log_audit(cursor, payload, "report_request.file_attached", "report_request", request_id,
                  snapshot={"file": original, "bytes": total})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "report_requests", "event": "file_attached", "id": request_id})
    return {"id": file_id, "original_name": original, "size_bytes": total}


@app.delete("/api/report_requests/{request_id}/files/{file_id}")
async def delete_report_request_file(request_id: str, file_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail="Files can't be removed once the request is fulfilled")
        cursor.execute("SELECT * FROM report_request_files WHERE id = ? AND request_id = ?", (file_id, request_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        cursor.execute("DELETE FROM report_request_files WHERE id = ?", (file_id,))
        log_audit(cursor, payload, "report_request.file_removed", "report_request", request_id,
                  snapshot={"file": row["original_name"]})
        conn.commit()
        try:
            os.remove(os.path.join(REQUEST_FILES_DIR, row["stored_name"]))
        except OSError:
            pass
    finally:
        conn.close()
    await manager.broadcast({"channel": "report_requests", "event": "file_removed", "id": request_id})
    return {"status": "removed"}


@app.get("/api/report_requests/{request_id}/files/{file_id}")
async def download_report_request_file(request_id: str, file_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _request_file_access(cursor, payload, request_id)
        cursor.execute("SELECT * FROM report_request_files WHERE id = ? AND request_id = ?", (file_id, request_id))
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    path = os.path.join(REQUEST_FILES_DIR, os.path.basename(row["stored_name"]))
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The file is no longer on disk")
    return FileResponse(path, media_type=row["content_type"] or "application/octet-stream", filename=row["original_name"])

# --- DEVTEAM: FULL SYSTEM VISIBILITY (READ-ONLY OVERVIEW) ---
@app.get("/api/devteam/overview")
async def devteam_overview(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()

    # BUG FOUND 2026-09-04 (user report: a PNP Admin account exists but its
    # station shows "no PNP admin created" / "PD vacant", and the new Users
    # tab shows its organization as blank and "Never logged in" regardless
    # of reality). Root cause: this SELECT never listed station_id or
    # last_login, so every user object this endpoint returns is silently
    # missing both fields even when the database row has real values (Jae's
    # station_id is genuinely set to PNP 1's id) -- every screen fed by
    # data.users has nothing to match a PNP account to its station with, so
    # coverage always reads as vacant, and last_login always reads as never
    # logged in. Same list _row_to_user_dict_base already uses elsewhere,
    # kept in sync rather than duplicated ad hoc a second time.
    # custom_permissions added 2026-09-04 alongside the admin permission
    # override feature -- DevteamView needs it to know whether an admin row
    # is still on the automatic default or has been explicitly overridden,
    # so it can show the right control (and the right current checkboxes).
    # deleted_at IS NULL added 2026-09-22 -- same reasoning as list_my_users
    # above: a soft-deleted account belongs in the Audit Log, not sitting in
    # Directory/Users looking exactly like an active one.
    cursor.execute(
        "SELECT id, username, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, "
        "last_login, custom_permissions, custom_role_id, created_at, signup_status, verification_status, "
        f"id_document_path, face_photo_path, {', '.join(PROFILE_FIELDS)} FROM users WHERE deleted_at IS NULL")
    user_rows = cursor.fetchall()
    users = [dict(r) for r in user_rows]
    perms_by_id = _user_permissions_json_batch(cursor, [u["id"] for u in users])
    # Dicing, batched: {user_id: {permission_key: {resource_type: [ids]}}}.
    cursor.execute("SELECT user_id, permission_key, resource_type, resource_id FROM permission_grants "
                   "WHERE resource_type IN ('camera', 'crime_type', 'channel')")
    scopes_by_user: dict = {}
    for r in cursor.fetchall():
        (scopes_by_user.setdefault(r["user_id"], {}).setdefault(r["permission_key"], {})
         .setdefault(r["resource_type"], []).append(r["resource_id"]))
    for u in users:
        u["permissions"] = perms_by_id.get(u["id"], "{}")
        u["custom_permissions"] = bool(u["custom_permissions"])
        u["resource_scopes"] = scopes_by_user.get(u["id"], {})
        u["has_document"] = bool(u.pop("id_document_path"))
        u["has_face_photo"] = bool(u.pop("face_photo_path"))
        u["verification_status"] = u.get("verification_status") or "unverified"

    cursor.execute("SELECT COUNT(*) AS c FROM incidents")
    incident_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM incidents WHERE status = 'Active'")
    active_incident_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM cameras")
    camera_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM video_records")
    record_count = cursor.fetchone()["c"]
    cursor.execute("SELECT barangay_id, COUNT(*) AS c FROM incidents GROUP BY barangay_id")
    incidents_by_location = [dict(r) for r in cursor.fetchall()]

    # Full camera roster (not just a count) so DevteamView can group cameras
    # by location and show which Precinct Captain / Barangay Captain is
    # responsible for each -- same pairing used elsewhere: cameras and the
    # two captains at a location all share one barangay_id.
    cursor.execute("SELECT id, name, url, status, barangay_id FROM cameras ORDER BY barangay_id, name")
    cameras = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "users": users,
        "cameras": cameras,
        "totals": {
            "users": len(users),
            "incidents": incident_count,
            "active_incidents": active_incident_count,
            "cameras": camera_count,
            "video_records": record_count,
        },
        "incidents_by_location": incidents_by_location,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Detection models — read state and measured performance, toggle on/off
#
# config.json is the single source of truth. The measured numbers are served
# from there rather than duplicated into the frontend: retraining a model
# changes one file, and a copy in TypeScript would silently keep showing the
# old accuracy long after the model behind it changed.
#
# A toggle takes effect on the next detector start, not immediately. The AI core
# reads config.json once at import; making it hot-reloadable would mean
# rebuilding model state mid-stream, and a detector that swaps models while a
# clip buffer is half full is a much worse failure than one that needs a
# restart. The response says so explicitly so the UI can tell the user.
# ──────────────────────────────────────────────────────────────────────────────
# Five entries, not four. vandalism_marks is the graffiti/tag detector -- a
# separately trained, separately deployed YOLO model with its own measured
# numbers, which was invisible in this panel while being live in the pipeline.
# A deployed model the dev team cannot see is one nobody checks.
DETECTION_CLASSES = ("violence", "robbery", "vandalism", "vandalism_marks",
                     "weapon")


def _read_config_file():
    """config.json as the DETECTOR sees it: base + env overlay + writable.

    Must mirror the layering in maincode/main.py exactly. Reading CONFIG_PATH
    alone (which resolves to config.<APP_ENV>.json when that file exists) was
    why this endpoint served no statistics for weapons, robbery or vandalism:
    those blocks live only in config.json, and the env file replaced it.
    """
    with open(_BASE_CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    for extra in (_ENV_CONFIG_PATH, WRITABLE_CONFIG_PATH):
        if extra and os.path.exists(extra):
            try:
                with open(extra, "r", encoding="utf-8") as fh:
                    cfg = _deep_merge(cfg, json.load(fh))
            except Exception:
                pass          # a malformed overlay must not blank the panel
    return cfg


@app.get("/api/devteam/detection-models")
async def list_detection_models(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    try:
        cfg = _read_config_file()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read config.json: {e}")

    det = cfg.get("detection", {})
    out = []
    for name in DETECTION_CLASSES:
        block = det.get(name)
        if not isinstance(block, dict):
            continue
        # violence keys its live values under scene_* because it runs in scene
        # mode; robbery and vandalism use the plain names.
        threshold = block.get("scene_confidence_threshold",
                              block.get("confidence_threshold"))
        consecutive = block.get("scene_consecutive_required",
                                block.get("consecutive_required"))
        model_path = block.get("scene_model_path", block.get("model_path"))
        weights_ok = bool(model_path) and os.path.exists(
            os.path.join(BASE_DIR, model_path))

        out.append({
            "name": name,
            "display_name": block.get("display_name", name.title()),
            # violence has no explicit flag historically -- absent means on.
            "enabled": bool(block.get("enabled", True)),
            "experimental": bool(block.get("experimental", False)),
            "threshold": threshold,
            "consecutive_required": consecutive,
            "model_path": model_path,
            "weights_present": weights_ok,
            "metrics": block.get("metrics"),
            # The long "_why_*" prose keys, surfaced so the reasoning travels
            # with the switch rather than living only in the file.
            "notes": {k: v for k, v in block.items()
                      if k.startswith("_") and isinstance(v, str)},
        })
    return {"models": out, "requires_restart": True}


@app.patch("/api/devteam/detection-models/{name}")
async def set_detection_model(
    name: str,
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
):
    payload = require_auth(authorization)
    # BUG FOUND 2026-08-23: this was DEVTEAM-only, so the barangay that owns
    # the camera and hardware this actually runs on had no way to turn a
    # detector on or off -- every "should this camera see less" call sat
    # with DevTeam regardless of whose site it affected. Widened to
    # MODEL_VIEW_ROLES (same set that can already see the panel), matching
    # manage_cameras' existing barangay-owns-its-hardware precedent. The
    # threshold value stays DEVTEAM-only -- see the check below -- since
    # that number is what the model's reported accuracy was measured at,
    # not something to hand-tune per site.
    require_role(payload, MODEL_VIEW_ROLES)

    if name not in DETECTION_CLASSES:
        raise HTTPException(status_code=404, detail=f"Unknown detection class: {name}")

    if "threshold" in body and payload["role"] != "DEVTEAM":
        raise HTTPException(status_code=403, detail="Only DevTeam can change a detection threshold")

    try:
        cfg = _read_config_file()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read config.json: {e}")

    block = cfg.get("detection", {}).get(name)
    if not isinstance(block, dict):
        raise HTTPException(status_code=404, detail=f"No config block for {name}")

    changed = {}

    if "enabled" in body:
        want = bool(body["enabled"])
        # Refuse to enable a class whose weights are not on disk. Letting this
        # through would produce a detector that crashes the AI core at startup,
        # and the user would see the dashboard fail to come up with no
        # connection to the switch they just flipped.
        if want:
            model_path = block.get("scene_model_path", block.get("model_path"))
            if not model_path or not os.path.exists(os.path.join(BASE_DIR, model_path)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot enable {name}: weights not found at {model_path!r}.")
        block["enabled"] = want
        changed["enabled"] = want

    if "threshold" in body:
        try:
            t = float(body["threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="threshold must be a number")
        if not 0.0 < t < 1.0:
            raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
        key = "scene_confidence_threshold" if "scene_confidence_threshold" in block \
            else "confidence_threshold"
        block[key] = t
        changed[key] = t

    if not changed:
        raise HTTPException(status_code=400, detail="Nothing to change")

    # Write via a temp file in the same directory, then replace. A partial
    # write here leaves config.json unparseable, which takes down the backend
    # AND the detector on next start -- the one file where a torn write is
    # unrecoverable without a manual edit.
    #
    # BUG FOUND 2026-08-23: this used to write to CONFIG_PATH (the shipped
    # BASE_DIR config.json), not WRITABLE_CONFIG_PATH. Every other writer in
    # this file follows "write to the WRITABLE copy, never CONFIG_PATH" (see
    # the comment above the secret_key write ~30 lines up) precisely because
    # WRITABLE_CONFIG_PATH is what main.py's loader merges LAST -- i.e. it
    # always wins. WRITABLE_CONFIG_PATH is seeded as a FULL snapshot of the
    # merged config the first time the app ever runs, so once that snapshot
    # exists, every key it contains (which is every key, since it's a full
    # copy) permanently shadows the same key in the base config.json on every
    # future load. Writing the toggle to CONFIG_PATH instead of
    # WRITABLE_CONFIG_PATH meant the change was saved to a file whose value
    # for "enabled" the loader never actually looks at again once the
    # snapshot exists -- so a detector switched off would flip back on (the
    # snapshot's original "enabled": true winning the merge) the next time
    # the app was closed and reopened, exactly undoing the toggle instead of
    # merely requiring a restart to apply it.
    tmp = WRITABLE_CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, WRITABLE_CONFIG_PATH)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Could not save config.json: {e}")

    return {
        "ok": True,
        "name": name,
        "changed": changed,
        "requires_restart": True,
        "message": "Saved. Restart detection for this to take effect.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# PER-CAMERA DETECTOR OVERRIDES
#
# set_detection_model above is a GLOBAL switch -- one flag in config.json,
# applying to every camera the whole deployment runs. This is the missing
# per-camera layer: a barangay owns its cameras (same precedent as
# manage_cameras) and can restrict which detectors run on EACH of its own
# cameras individually -- a market-stall camera runs vandalism + robbery
# only, a corridor camera runs violence only, a flagship node runs
# everything. A model must be enabled BOTH globally AND for the specific
# camera to actually run there -- this table can only narrow what the
# global switch already allows, never widen it. No row for a (camera,
# model) pair means enabled, so a camera untouched by this feature behaves
# exactly as it always has.
# ──────────────────────────────────────────────────────────────────────────────

def _scoped_camera_ids(cursor, payload: dict, permission_key: str) -> Optional[set]:
    """Cameras this user's `permission_key` is diced down to, or None when it
    isn't diced (every camera their org allows). DEVTEAM is never narrowed."""
    if payload.get("role") == "DEVTEAM":
        return None
    cursor.execute(
        "SELECT resource_id FROM permission_grants WHERE user_id = ? AND permission_key = ? AND resource_type = 'camera'",
        (payload["id"], permission_key),
    )
    ids = {r["resource_id"] for r in cursor.fetchall()}
    return ids or None


def _camera_owned_by(cursor, camera_id: str, payload: dict) -> bool:
    """DEVTEAM can touch any camera. Everyone else must own it -- the
    camera's barangay_id must match the caller's own barangay_id -- and, if
    their manage_cameras access is diced down to specific cameras, this must
    be one of them. PNP roles never reach this: manage_cameras is
    barangay-only (BARANGAY_ONLY_PERMISSIONS), enforced by
    require_permission() before this is ever called."""
    if payload.get("role") == "DEVTEAM":
        return True
    cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (camera_id,))
    row = cursor.fetchone()
    if not row:
        return False
    if (row["barangay_id"] or "").lower() != (payload.get("barangay_id") or "").lower():
        return False
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    return scoped is None or camera_id in scoped


def _camera_model_map(cursor, camera_id: str) -> dict:
    """Every DETECTION_CLASSES key, defaulting to True, overridden by
    whatever rows actually exist for this camera."""
    out = {name: True for name in DETECTION_CLASSES}
    cursor.execute(
        "SELECT model_key, enabled FROM camera_model_config WHERE camera_id = ?",
        (camera_id,),
    )
    for row in cursor.fetchall():
        out[row["model_key"]] = bool(row["enabled"])
    return out


@app.get("/api/cameras/{camera_id}/models")
async def get_camera_models(camera_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    if not _camera_owned_by(cursor, camera_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found (or outside your jurisdiction)")
    models = _camera_model_map(cursor, camera_id)
    conn.close()
    return {"camera_id": camera_id, "models": models}


@app.get("/api/cameras/models")
async def list_camera_models(authorization: Optional[str] = Header(None)):
    """Bulk form of the above -- every camera in the caller's scope plus its
    model map, in one round trip. Powers the barangay Models panel without
    an N+1 fetch per camera."""
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    sql, params = apply_scope(payload, "SELECT id, name, barangay_id FROM cameras", [])
    cursor.execute(sql, tuple(params))
    cameras = [dict(r) for r in cursor.fetchall()]
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    if scoped is not None:
        cameras = [c for c in cameras if c["id"] in scoped]
    for cam in cameras:
        cam["models"] = _camera_model_map(cursor, cam["id"])
    conn.close()
    return {"cameras": cameras}


@app.patch("/api/cameras/{camera_id}/models/{model_key}")
async def set_camera_model(
    camera_id: str, model_key: str,
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")

    if model_key not in DETECTION_CLASSES:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Unknown detection class: {model_key}")
    if not _camera_owned_by(cursor, camera_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found (or outside your jurisdiction)")
    if "enabled" not in body:
        conn.close()
        raise HTTPException(status_code=400, detail="Nothing to change")

    enabled = bool(body["enabled"])
    # This layer can only narrow, never widen, the global switch -- refuse to
    # "enable" a camera override for a model that's off system-wide, since
    # that would silently do nothing and look like a bug when the camera
    # still doesn't detect it.
    if enabled:
        try:
            cfg = _read_config_file()
        except Exception as e:
            conn.close()
            raise HTTPException(status_code=500, detail=f"Cannot read config.json: {e}")
        block = cfg.get("detection", {}).get(model_key, {})
        if not bool(block.get("enabled", True)):
            conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"{model_key} is off system-wide -- ask DevTeam to enable it globally first.",
            )

    cursor.execute(
        """INSERT INTO camera_model_config (camera_id, model_key, enabled, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT (camera_id, model_key) DO UPDATE SET
             enabled = excluded.enabled, updated_at = excluded.updated_at""",
        (camera_id, model_key, 1 if enabled else 0),
    )
    conn.commit()
    conn.close()
    await manager.broadcast({
        "channel": "camera_models", "event": "changed",
        "camera_id": camera_id, "model_key": model_key, "enabled": enabled,
    })
    return {"ok": True, "camera_id": camera_id, "model_key": model_key, "enabled": enabled}


# ──────────────────────────────────────────────────────────────────────────────
# PER-CAMERA THRESHOLD OVERRIDES -- docs/progress_report_violence_detection.md
# §28.1: one global threshold is necessarily too low for a noisy camera and
# too high for the rest of the network. Same shape and same precedent as the
# on/off overrides above (camera_model_config): a row here NARROWS/RETUNES a
# single camera's operating point without touching config.json, and no row
# means "use whatever the global config already says" -- a camera nobody has
# calibrated behaves exactly as it always has.
#
# Scoped to violence/robbery/vandalism only -- the three SceneViolenceDetector
# instances, which share one threshold+consecutive shape. weapon and
# vandalism_marks are single-frame YOLO with per-class confidence, a
# different knob entirely, and aren't part of this.
# ──────────────────────────────────────────────────────────────────────────────

# (global_threshold_key, global_consecutive_key) in config.json's
# detection.<model_key> block -- violence nests these under scene_* because
# the same block also carries track-mode's plain confidence_threshold;
# robbery/vandalism have no track mode so theirs are unprefixed.
_THRESHOLD_MODEL_KEYS = ("violence", "robbery", "vandalism")
_GLOBAL_THRESHOLD_CFG_KEYS = {
    "violence":  ("scene_confidence_threshold", "scene_consecutive_required"),
    "robbery":   ("confidence_threshold", "consecutive_required"),
    "vandalism": ("confidence_threshold", "consecutive_required"),
}


def _global_threshold_defaults(cfg: dict) -> dict:
    """{model_key: {"threshold":.., "consecutive_required":..}} read straight
    out of config.json, so the UI can show what an uncalibrated camera is
    actually running without duplicating those numbers in TypeScript (same
    reasoning as the AI Models panel's numbers -- see final_checks.md §3.2)."""
    det = cfg.get("detection", {})
    out = {}
    for key in _THRESHOLD_MODEL_KEYS:
        thresh_key, consec_key = _GLOBAL_THRESHOLD_CFG_KEYS[key]
        block = det.get(key, {})
        out[key] = {
            "threshold": block.get(thresh_key),
            "consecutive_required": block.get(consec_key),
        }
    return out


def _camera_threshold_map(cursor, camera_id: str) -> dict:
    """Only rows that actually exist -- unlike _camera_model_map this does
    NOT fill in every key, because absence here means something different to
    the caller ('inherit the global value', not 'this specific value')."""
    out = {}
    cursor.execute(
        """SELECT model_key, threshold, consecutive_required, calibrated_from, updated_at
           FROM camera_threshold_config WHERE camera_id = ?""",
        (camera_id,),
    )
    for row in cursor.fetchall():
        out[row["model_key"]] = {
            "threshold": row["threshold"],
            "consecutive_required": row["consecutive_required"],
            "calibrated_from": row["calibrated_from"],
            "updated_at": row["updated_at"],
        }
    return out


@app.get("/api/cameras/{camera_id}/thresholds")
async def get_camera_thresholds(camera_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    if not _camera_owned_by(cursor, camera_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found (or outside your jurisdiction)")
    overrides = _camera_threshold_map(cursor, camera_id)
    defaults = _global_threshold_defaults(_read_config_file())
    conn.close()
    return {"camera_id": camera_id, "overrides": overrides, "global_defaults": defaults}


@app.get("/api/cameras/thresholds")
async def list_camera_thresholds(authorization: Optional[str] = Header(None)):
    """Bulk form, same rationale as list_camera_models: one round trip for
    every camera in the caller's scope instead of an N+1 fetch per camera."""
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    sql, params = apply_scope(payload, "SELECT id, name, barangay_id FROM cameras", [])
    cursor.execute(sql, tuple(params))
    cameras = [dict(r) for r in cursor.fetchall()]
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    if scoped is not None:
        cameras = [c for c in cameras if c["id"] in scoped]
    for cam in cameras:
        cam["thresholds"] = _camera_threshold_map(cursor, cam["id"])
    defaults = _global_threshold_defaults(_read_config_file())
    conn.close()
    return {"cameras": cameras, "global_defaults": defaults}


@app.patch("/api/cameras/{camera_id}/thresholds/{model_key}")
async def set_camera_threshold(
    camera_id: str, model_key: str,
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")

    if model_key not in _THRESHOLD_MODEL_KEYS:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No tunable threshold for: {model_key}")
    if not _camera_owned_by(cursor, camera_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found (or outside your jurisdiction)")
    if "threshold" not in body:
        conn.close()
        raise HTTPException(status_code=400, detail="Nothing to change")

    try:
        threshold = float(body["threshold"])
    except (TypeError, ValueError):
        conn.close()
        raise HTTPException(status_code=400, detail="threshold must be a number")
    # Matches the schema CHECK, checked here too so the error is legible
    # instead of a raw sqlite3.IntegrityError.
    if not (0.05 <= threshold <= 0.95):
        conn.close()
        raise HTTPException(status_code=400, detail="threshold must be between 0.05 and 0.95")

    consecutive = body.get("consecutive_required")
    if consecutive is not None:
        try:
            consecutive = int(consecutive)
        except (TypeError, ValueError):
            conn.close()
            raise HTTPException(status_code=400, detail="consecutive_required must be an integer")
        if not (1 <= consecutive <= 10):
            conn.close()
            raise HTTPException(status_code=400, detail="consecutive_required must be between 1 and 10")

    calibrated_from = body.get("calibrated_from", "manual")
    if calibrated_from not in ("manual", "quiet_clip"):
        calibrated_from = "manual"

    cursor.execute(
        """INSERT INTO camera_threshold_config
               (camera_id, model_key, threshold, consecutive_required, calibrated_from, updated_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT (camera_id, model_key) DO UPDATE SET
             threshold = excluded.threshold,
             consecutive_required = excluded.consecutive_required,
             calibrated_from = excluded.calibrated_from,
             updated_at = excluded.updated_at""",
        (camera_id, model_key, threshold, consecutive, calibrated_from),
    )
    conn.commit()
    conn.close()
    await manager.broadcast({
        "channel": "camera_thresholds", "event": "changed",
        "camera_id": camera_id, "model_key": model_key,
        "threshold": threshold, "consecutive_required": consecutive,
    })
    return {
        "ok": True, "camera_id": camera_id, "model_key": model_key,
        "threshold": threshold, "consecutive_required": consecutive,
        "calibrated_from": calibrated_from,
        "requires_restart": True,
        "message": "Saved. Restart detection on this camera for it to take effect.",
    }


@app.delete("/api/cameras/{camera_id}/thresholds/{model_key}")
async def clear_camera_threshold(camera_id: str, model_key: str, authorization: Optional[str] = Header(None)):
    """Resets a camera back to the global config.json value -- the inverse of
    set_camera_threshold, and deliberately a DELETE rather than PATCHing back
    to some sentinel, so 'no override' stays represented as 'no row' rather
    than a magic value the rest of this feature has to know about."""
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    if not _camera_owned_by(cursor, camera_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found (or outside your jurisdiction)")
    cursor.execute(
        "DELETE FROM camera_threshold_config WHERE camera_id = ? AND model_key = ?",
        (camera_id, model_key),
    )
    conn.commit()
    conn.close()
    await manager.broadcast({
        "channel": "camera_thresholds", "event": "cleared",
        "camera_id": camera_id, "model_key": model_key,
    })
    return {"ok": True, "camera_id": camera_id, "model_key": model_key, "requires_restart": True}


# ──────────────────────────────────────────────────────────────────────────────
# OPTIMIZE WEIGHTS -- builds TensorRT .engine files FOR THIS MACHINE.
#
# optimize_weights.py already emits structured "@@{json}" progress lines --
# its own docstring says they're "for the installer UI", which never
# actually happened until now. This wraps it as a background subprocess
# (a full run is several minutes, one ~1min build per model, so it cannot
# run inline on the request) and re-broadcasts each line over the existing
# /ws channel so the dashboard gets live progress instead of a spinner.
#
# Deliberately a single global run, not per-user: it's a machine-wide,
# GPU-wide operation (see optimize_weights.py's own docstring on why an
# engine is tied to one GPU + one TensorRT version), so two runs racing
# would just corrupt each other's engine files.
# ──────────────────────────────────────────────────────────────────────────────
_optimize_state = {
    "running": False,
    "steps": [],
    "summary": None,
    "preconditions": None,
    "returncode": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "cancelled": False,
}
_optimize_lock = asyncio.Lock()
# The live subprocess handle for whatever optimize run is in progress, so
# /optimize_weights/cancel has something to terminate. Deliberately NOT a key
# in _optimize_state -- that dict is returned verbatim as the /status
# response body, and a Process object isn't JSON-serializable.
_optimize_proc: "Optional[asyncio.subprocess.Process]" = None


async def _run_optimize_weights(revert: bool):
    global _optimize_proc
    args = [sys.executable, os.path.join(BASE_DIR, "optimize_weights.py")]
    if revert:
        args.append("--revert")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=BASE_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    _optimize_proc = proc
    # optimize_weights.py's build_yolo_engine/build_x3d_engine write the
    # .engine straight to its final path, so a step killed mid-"building" can
    # leave a truncated file behind -- exactly the case optimize_weights.py's
    # own except-block already guards against on a measured failure (it
    # unlinks the engine rather than leave a broken file for the loader to
    # trip on next launch). This tracks the stem of whatever step last
    # reported "building" with no terminal event since, so the finally block
    # below can apply that same cleanup when this run stops abnormally
    # (cancelled, or the pipe just closes).
    in_flight_stem = None
    # Set before the try: a task cancellation (app shutdown mid-run) raises
    # CancelledError, which "except Exception" doesn't catch, and the
    # finally block below then crashed on an unbound returncode.
    returncode = None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if not text.startswith("@@"):
                continue
            try:
                event = json.loads(text[2:])
            except json.JSONDecodeError:
                continue
            kind = event.get("kind")
            if kind == "preconditions":
                _optimize_state["preconditions"] = event
            elif kind == "step":
                _optimize_state["steps"].append(event)
                state = event.get("state")
                if state == "building":
                    in_flight_stem = event.get("stem")
                elif state in ("done", "failed", "skipped"):
                    in_flight_stem = None
            elif kind == "summary":
                _optimize_state["summary"] = event
            elif kind == "reverted":
                _optimize_state["summary"] = event
            await manager.broadcast({"channel": "optimize_weights", "event": "progress",
                                      **event})
        returncode = await proc.wait()
    except Exception as e:
        _optimize_state["error"] = f"{type(e).__name__}: {e}"
        returncode = -1
    finally:
        cancelled = bool(_optimize_state.get("cancel_requested"))
        if in_flight_stem:
            try:
                os.remove(os.path.join(BASE_DIR, "weights", f"{in_flight_stem}.engine"))
            except OSError:
                pass
        _optimize_state["running"] = False
        _optimize_state["cancelled"] = cancelled
        _optimize_state["cancel_requested"] = False
        _optimize_state["returncode"] = returncode
        _optimize_state["finished_at"] = datetime.utcnow().isoformat()
        _optimize_proc = None
        await manager.broadcast({"channel": "optimize_weights", "event": "finished",
                                  "returncode": returncode,
                                  "cancelled": cancelled,
                                  "error": _optimize_state["error"]})


@app.post("/api/devteam/optimize_weights")
async def start_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if _optimize_state["running"]:
            raise HTTPException(status_code=409, detail="An optimize run is already in progress")
        _optimize_state.update(running=True, steps=[], summary=None, preconditions=None,
                                returncode=None, error=None, cancelled=False,
                                cancel_requested=False,
                                started_at=datetime.utcnow().isoformat(), finished_at=None)
        asyncio.create_task(_run_optimize_weights(revert=False))

    return {"status": "started"}


@app.post("/api/devteam/optimize_weights/revert")
async def revert_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if _optimize_state["running"]:
            raise HTTPException(status_code=409, detail="An optimize run is already in progress")
        _optimize_state.update(running=True, steps=[], summary=None, preconditions=None,
                                returncode=None, error=None, cancelled=False,
                                cancel_requested=False,
                                started_at=datetime.utcnow().isoformat(), finished_at=None)
        asyncio.create_task(_run_optimize_weights(revert=True))

    return {"status": "started"}


@app.post("/api/devteam/optimize_weights/cancel")
async def cancel_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if not _optimize_state["running"] or _optimize_proc is None:
            raise HTTPException(status_code=409, detail="No optimize run is in progress")
        proc = _optimize_proc
        _optimize_state["cancel_requested"] = True

    # terminate() outside the lock -- _run_optimize_weights holds nothing
    # while awaiting proc output, so this doesn't need the lock, and killing
    # a subprocess is exactly the kind of call that shouldn't be made while
    # holding one. On Windows, Process.terminate() calls TerminateProcess --
    # there is no graceful SIGTERM-equivalent stop to ask a console app for
    # there, so this IS the hard stop, not a polite request that a wait/kill
    # escalation follows. The wait below just confirms it actually exited
    # before responding, so the dashboard's "cancelling..." doesn't linger
    # past the point where the process is really gone.
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass

    return {"status": "cancelling"}


@app.get("/api/devteam/optimize_weights/status")
async def get_optimize_weights_status(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)
    return _optimize_state


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