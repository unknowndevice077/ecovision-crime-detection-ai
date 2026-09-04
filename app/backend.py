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

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
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
        cursor.execute(
            "SELECT role, barangay_id, station_id, custom_permissions FROM users WHERE id = ?",
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
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

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
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
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

class CameraSchema(BaseModel):
    name: str
    url: str
    barangay_id: str

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

class DevteamCreateUser(BaseModel):
    username: str
    password: str
    role: str
    # Exactly one of these is required, decided by the role's organization:
    # barangay roles need barangay_id, PNP roles need station_id.
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    assignment: str
    display_title: Optional[str] = None
    parent_admin_id: Optional[int] = None
    permissions: Optional[dict] = None


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

def _row_to_camera_dict(row) -> dict:
    d = dict(row)
    return {"id": d["id"], "name": d["name"], "url": d["url"], "status": d["status"], "barangay_id": d.get("barangay_id")}

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
    conn.close()
    return [_row_to_camera_dict(r) for r in rows]

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
    try:
        cursor.execute(
            "INSERT INTO cameras (id, name, url, status, barangay_id) VALUES (?, ?, ?, 'online', ?)",
            (cam_id, cam.name, cam.url, cam.barangay_id.lower()),
        )
        conn.commit()
        return {"status": "created", "id": cam_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    cursor.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
    conn.commit()
    conn.close()
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
        cursor.execute("SELECT barangay_id, station_id FROM notify_targets WHERE id = ?", (target_id,))
        existing = cursor.fetchone()
        if existing:
            if role in PNP_SIDE_ROLES and existing["station_id"] != payload.get("station_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own station")
            if role not in PNP_SIDE_ROLES and existing["barangay_id"] != payload.get("barangay_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own barangay")

    cursor.execute("DELETE FROM notify_targets WHERE id = ?", (target_id,))
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
async def get_incidents(authorization: Optional[str] = Header(None), filter_barangay_id: Optional[str] = "all"):
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
    if role != "DEVTEAM" and not (role in ADMIN_ROLES and not payload.get("custom_permissions")):
        cursor.execute(
            "SELECT 1 FROM user_permissions WHERE user_id = ? AND permission_key IN ('view_map','view_history')",
            (payload["id"],),
        )
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=403, detail="Missing permission: view_map or view_history")

    # Visibility and redaction are two SEPARATE decisions and were previously
    # tangled into one if/else:
    #   visibility -- which barangays' incidents you may see (scope_clause)
    #   redaction  -- whether investigative PII is masked (barangay side yes,
    #                 PNP side no; police need names/narrative to investigate)
    redact = role in BARANGAY_SIDE_ROLES

    # filter_barangay_id only ever NARROWS within the caller's scope. It is
    # appended alongside the scope clause, never instead of it, so it cannot
    # be used to reach outside your own jurisdiction.
    extra_where, extra_params = "", []
    if filter_barangay_id and filter_barangay_id.lower() != "all":
        extra_where, extra_params = "LOWER(barangay_id) = ?", [filter_barangay_id.lower()]

    sql, params = apply_scope(
        payload, "SELECT * FROM incidents", [],
        extra_where=extra_where, extra_params=extra_params,
    )
    sql += " ORDER BY occurred_date DESC, occurred_time DESC"
    cursor.execute(sql, tuple(params))

    inc_rows = cursor.fetchall()

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
            record["narrative"] = "🔒 [RESTRICTED] Investigative logs masked for non-police profiles."
            record["nature_of_call"] = "CONFIDENTIAL // RESTRICTED"
            record["arrival_reason"] = "CONFIDENTIAL // RESTRICTED"
            record["additional_officers"] = "CONFIDENTIAL"
        results.append(record)
    conn.close()
    return results

@app.post("/api/incidents")
async def add_incident(incident: IncidentSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    # Never trust the client's barangay_id for anyone but DEVTEAM -- force
    # it to the authenticated user's own assignment so a tampered/buggy
    # request can't pin an incident to a location the user isn't part of.
    if payload["role"] != "DEVTEAM":
        if not payload.get("barangay_id"):
            raise HTTPException(status_code=403, detail="Your account has no assigned location.")
        effective_barangay_id = payload["barangay_id"]
    else:
        effective_barangay_id = incident.barangay_id
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO incidents
               (id, case_id, type, severity, status, lat, lng, location_name,
                occurred_date, occurred_time, confidence, officer, barangay_id, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL')""",
            (incident.id, incident.case_id, incident.type, incident.severity, incident.status,
             incident.lat, incident.lng, incident.location_name, incident.occurred_date, incident.occurred_time,
             incident.confidence, incident.officer, effective_barangay_id.lower()),
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
        conn.commit()
        return {"status": "persisted"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/api/ai_trigger")
async def ai_trigger(data: AiTriggerSchema):
    # Deliberately NOT behind require_auth -- called by the local AI
    # pipeline (main.py on 8001), not a browser. Protected only by being
    # localhost-reachable in this deployment; give it its own service
    # credential if this backend is ever exposed beyond localhost.
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
    cursor.execute(
        """INSERT INTO incidents
           (id, case_id, type, severity, status, lat, lng, location_name,
            occurred_date, occurred_time, confidence, officer, barangay_id, source, camera_id)
           VALUES (?, ?, ?, 'HIGH', 'Active', ?, ?, ?, ?, ?, ?, 'AI_AUTOMATION', ?, 'AI_AUTOMATION', ?)""",
        (incident_id, case_id, data.event, 11.0504, 124.6062, data.location_name,
         now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), data.confidence, data.barangay_id.lower(),
         data.camera_id),
    )
    cursor.execute(
        """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, 'EMERGENCY_AI_FLAG', 'AUTOMATED_TRIGGER', 'NONE')""",
        (incident_id, "Autonomous edge detection triggered via spatiotemporal analysis classification matrix."),
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
    })
    return {"status": "processed", "incident_id": incident_id}

@app.get("/api/camera_name/{camera_id}")
async def camera_name(camera_id: str):
    """Lets main.py resolve the real, currently-registered name for the
    camera it's pointed at, instead of a name baked into config.json --
    added per request: "barangay adds a camera then adds a name and now the
    police can access that camera and show the name". If a barangay renames
    a camera in the Cameras tab, the AI core picks up the new name the next
    time it starts (it resolves this once at startup, not per-alert -- a
    live rename doesn't retroactively relabel an already-running session,
    same restart-to-apply rule as every other config change tonight).

    Deliberately unauthenticated, same reasoning as /api/ai_trigger: the
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
    })
    return {"status": "panic_logged", "id": incident_id}

@app.patch("/api/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, data: StatusUpdateSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- confirm_dismiss_alerts
    # is a normal, common permission on both sides; without this, anyone who has it
    # could confirm/dismiss another jurisdiction's incident.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    conn.commit()
    updated = cursor.rowcount
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
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- this DELETE
    # cascades to incident_reports, so without this check any admin anywhere
    # could destroy another jurisdiction's incident AND any filed police
    # report against it.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "deleted"}

@app.patch("/api/incidents/{incident_id}/archive")
async def archive_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("UPDATE incident_visibility SET map_hidden = 1 WHERE incident_id = ?", (incident_id,))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "archived_from_map", "id": incident_id}

@app.post("/api/incidents/{incident_id}/confirm-and-report")
async def confirm_and_report(incident_id: str, data: ConfirmAndReportSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- POLICE_SIDE_ROLES
    # only ruled out barangay accounts, not a PNP account from a DIFFERENT
    # station's jurisdiction.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    officer = (data.report_details or {}).get("reporting_officer")
    if officer:
        cursor.execute("UPDATE incidents SET status = ?, officer = ? WHERE id = ?", (data.status, officer, incident_id))
    else:
        cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    updated = cursor.rowcount
    if not updated:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")

    details = data.report_details or {}
    cursor.execute(
        """INSERT INTO incident_reports
           (id, incident_id, reported_by, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), incident_id, payload["id"],
         details.get("narrative"), details.get("nature_of_call"),
         details.get("arrival_reason"), details.get("additional_officers")),
    )
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
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
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
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
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
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
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
    conn.close()
    return [_row_to_record_dict(r) for r in rows]

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

    Restricted to admin tiers/DEVTEAM rather than anyone with view_records:
    this destroys evidence, which is a materially different action from
    watching it. Scoped too, so an admin cannot delete another barangay's
    footage.
    """
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()

    sql, params = apply_scope(payload, "SELECT * FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
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
async def ai_register_clip(data: ManualClipSchema):
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
    # endpoint already are, via apply_scope().
    sql, params = apply_scope(payload, "SELECT id FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    if not cursor.fetchone():
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
    barangay_id = (user.barangay_id or "").strip().lower()
    station_id = (user.station_id or "").strip().lower()

    if is_pnp and not station_id:
        raise HTTPException(status_code=400, detail="A police station is required")
    if not is_pnp and not barangay_id:
        raise HTTPException(status_code=400, detail="Location is required")

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
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO barangays (id, name, status) VALUES (?, ?, 'pending')",
                    (barangay_id, user.barangay_id.strip().title()),
                )

        # One admin per org unit, matching the unique indexes.
        if is_pnp:
            cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = ?", (station_id, role))
            dup_msg = "This station already has a PNP Admin account."
        else:
            cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = ?", (barangay_id, role))
            dup_msg = "This location already has a Barangay Admin account."
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=dup_msg)

        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (user.username, hash_password(user.password), role,
             barangay_id or None, station_id or None, user.assignment),
        )
        new_user_id = cursor.lastrowid

        if is_pnp:
            # No location-approval gate for PNP: the station already exists,
            # which means DevTeam already vetted it.
            conn.commit()
            return {"status": "success"}

        cursor.execute("UPDATE barangays SET requested_by = ? WHERE id = ? AND requested_by IS NULL",
                       (new_user_id, barangay_id))
        conn.commit()

        cursor.execute("SELECT status FROM barangays WHERE id = ?", (barangay_id,))
        loc_status = cursor.fetchone()["status"]
        if loc_status == "approved":
            return {"status": "success"}
        return {"status": "pending_approval",
                "detail": "Account created. A DevTeam administrator must approve this location before you can log in."}
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Operator profile already mapped.")
    finally:
        conn.close()

@app.post("/api/login")
@limiter.limit("5/minute")
async def login(request: Request, creds: UserLogin):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (creds.username,))
    row = cursor.fetchone()
    if not row or not verify_password(creds.password, row["password"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid Credentials")

    user_dict = dict(row)

    if user_dict["role"] != "DEVTEAM" and user_dict.get("barangay_id"):
        cursor.execute("SELECT status FROM barangays WHERE id = ?", (user_dict["barangay_id"],))
        loc = cursor.fetchone()
        if not loc or loc["status"] != "approved":
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="Your location is still pending DevTeam approval. Please check back later.",
            )

    # Stamped here, not on every authenticated request -- last_login answers
    # "when did this account last sign in", not "is a request in flight
    # right now" (there's no session/heartbeat concept in this app to answer
    # that second question honestly, so the Users list doesn't claim to).
    cursor.execute("UPDATE users SET last_login = NOW() WHERE id = ?", (user_dict["id"],))
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
    payload = require_auth(authorization)
    return {"user": payload}

# --- DEVTEAM: POLICE STATIONS & JURISDICTIONS ---
# A station is an organizational unit that COVERS barangays. It owns no
# cameras, incidents or recordings -- station_barangays is purely a
# visibility lens (see docs/USER_HIERARCHY_PLAN.md). Editing a jurisdiction
# therefore changes only who can see what; it never moves an asset.

class StationSchema(BaseModel):
    id: Optional[str] = None
    name: str

class StationJurisdictionSchema(BaseModel):
    barangay_ids: List[str]


@app.get("/api/devteam/stations")
async def list_stations(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM police_stations ORDER BY name")
    stations = [{"id": r["id"], "name": r["name"]} for r in cursor.fetchall()]

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
        cursor.execute("INSERT INTO police_stations (id, name) VALUES (?, ?)", (sid, name))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not create station: {e}")
    finally:
        conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_created", "id": sid})
    return {"status": "created", "id": sid, "name": name}


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
        cursor.execute(f"SELECT id FROM barangays WHERE LOWER(id) IN ({placeholders})", tuple(wanted))
        known = {r["id"].lower() for r in cursor.fetchall()}
        unknown = [b for b in wanted if b not in known]
        if unknown:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown barangay ids: {', '.join(unknown)}")

    try:
        cursor.execute("DELETE FROM station_barangays WHERE station_id = ?", (station_id,))
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


@app.delete("/api/devteam/stations/{station_id}")
async def delete_station(station_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    # users.station_id is ON DELETE RESTRICT, and chk_user_scope means a PNP
    # user cannot exist without a station -- so refuse with a clear message
    # rather than letting the FK raise something opaque.
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE station_id = ?", (station_id,))
    n = cursor.fetchone()["n"]
    if n:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail=f"{n} user(s) are still assigned to this station. Reassign them first.")
    cursor.execute("DELETE FROM police_stations WHERE id = ?", (station_id,))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_deleted", "id": station_id})
    return {"status": "deleted", "id": station_id}


# --- DEVTEAM: LOCATION APPROVAL ---
@app.get("/api/devteam/locations")
async def list_locations(authorization: Optional[str] = Header(None), status: Optional[str] = None):
    """Includes the requesting captain's username/role/assignment so DevTeam
    has enough to actually verify the person before approving -- a bare
    location name + status was not enough to tell who's asking."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    query = """
        SELECT b.*, u.username AS requester_username, u.role AS requester_role,
               u.assignment AS requester_assignment
        FROM barangays b
        LEFT JOIN users u ON u.id = b.requested_by
    """
    if status:
        cursor.execute(query + " WHERE b.status = ? ORDER BY b.created_at DESC", (status,))
    else:
        cursor.execute(query + " ORDER BY b.created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/devteam/locations/{barangay_id}/approve")
async def approve_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE barangays SET status = 'approved', approved_by = ?, approved_at = NOW() WHERE id = ?",
        (payload["id"], barangay_id),
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Location not found")
    await manager.broadcast({"channel": "locations", "event": "location_approved", "barangay_id": barangay_id})
    return {"status": "approved", "barangay_id": barangay_id}

@app.post("/api/devteam/locations/{barangay_id}/reject")
async def reject_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE barangays SET status = 'rejected', approved_by = ?, approved_at = NOW() WHERE id = ?",
        (payload["id"], barangay_id),
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Location not found")
    await manager.broadcast({"channel": "locations", "event": "location_rejected", "barangay_id": barangay_id})
    return {"status": "rejected", "barangay_id": barangay_id}

# --- ADMIN: MANAGE YOUR OWN USERS ONLY ---
@app.get("/api/admin/users")
async def list_my_users(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()
    if payload["role"] == "DEVTEAM":
        cursor.execute("SELECT * FROM users")
    else:
        cursor.execute("SELECT * FROM users WHERE parent_admin_id = ?", (payload["id"],))
    rows = cursor.fetchall()
    result = _rows_to_user_dicts_batch(cursor, rows)
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

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_user.username, hash_password(new_user.password), target_role,
             new_barangay, new_station, new_user.assignment, payload["id"],
             new_user.display_title if new_user.is_sub_admin else None,
             1 if new_user.is_sub_admin else 0),
        )
        new_id = cursor.lastrowid

        if new_user.is_sub_admin and new_user.permissions:
            for key, granted in new_user.permissions.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
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
            station_id = ""
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            existing_barangay = cursor.fetchone()
            if not existing_barangay:
                cursor.execute(
                    "INSERT INTO barangays (id, name, status, approved_by, approved_at) "
                    "VALUES (?, ?, 'approved', ?, NOW())",
                    (barangay_id, barangay_id.title(), payload["id"]),
                )
            elif existing_barangay["status"] != "approved":
                # BUG FOUND 2026-09-02 (full account sweep): only the brand-new
                # case was ever promoted to 'approved' -- a barangay_id that
                # already existed as 'pending' or 'rejected' (an old self-signup
                # nobody acted on, or one DevTeam explicitly rejected earlier)
                # kept that status untouched, silently, even though DevTeam
                # creating an admin account for it here IS the vetting
                # decision. Login then 403'd with "still pending DevTeam
                # approval" forever, on a freshly-created account with no error
                # anywhere pointing at why. Reproduced directly: create a
                # BARANGAY_ADMIN for a barangay, reject that barangay, create a
                # second admin for the SAME barangay_id -- login blocked with
                # no indication the barangay (not the account) was the problem.
                cursor.execute(
                    "UPDATE barangays SET status = 'approved', approved_by = ?, approved_at = NOW() WHERE id = ?",
                    (payload["id"], barangay_id),
                )

        parent_id = new_user.parent_admin_id
        if role in STANDARD_ROLES and parent_id is None:
            # Auto-attach to whichever admin already runs this org unit, so
            # the account shows up nested under someone in the directory.
            if is_pnp:
                cursor.execute(
                    "SELECT id FROM users WHERE station_id = ? AND role = 'PNP_ADMIN'", (station_id,))
            else:
                cursor.execute(
                    "SELECT id FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN'", (barangay_id,))
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
        cursor.execute("SELECT 1 FROM users WHERE username = ?", (new_user.username,))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Username '{new_user.username}' is already taken.")
        if role in ADMIN_ROLES:
            if is_pnp:
                cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = 'PNP_ADMIN'", (station_id,))
                dup_detail = "This station already has a PNP Admin account."
            else:
                cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN'", (barangay_id,))
                dup_detail = "This barangay already has a Barangay Admin account."
            if cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail=dup_detail)

        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_user.username, hash_password(new_user.password), role,
             barangay_id or None, station_id or None, new_user.assignment, parent_id,
             new_user.display_title, 1 if new_user.display_title else 0),
        )
        new_id = cursor.lastrowid

        if new_user.permissions:
            for key, granted in new_user.permissions.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
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
    cursor.execute("SELECT parent_admin_id FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only edit permissions for your own users")

    cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    for key, granted in data.permissions.items():
        if granted and key in VALID_PERMISSION_KEYS:
            cursor.execute(
                "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                (user_id, key, payload["id"]),
            )
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
    cursor.execute("SELECT parent_admin_id FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only remove your own users")

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
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
        cursor.execute("SELECT 1 FROM barangays WHERE id = ?", (brgy,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown barangay '{brgy}'")
        fields.append("barangay_id = ?"); values.append(brgy)
    if data.station_id is not None:
        stn = data.station_id.strip().lower()
        cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (stn,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown station '{stn}'")
        fields.append("station_id = ?"); values.append(stn)
    if data.role is not None:
        if data.role not in ALL_ROLES:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid role '{data.role}'")
        fields.append("role = ?"); values.append(data.role)

    if not fields:
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(user_id)
    try:
        cursor.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
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
            conn.commit()
            await manager.broadcast({"channel": "users", "event": "permissions_reset", "id": user_id})
            return {"status": "reset_to_automatic", "id": user_id}

        cursor.execute("UPDATE users SET custom_permissions = 1 WHERE id = ?", (user_id,))
        cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        for key, granted in data.permissions.items():
            if granted and key in VALID_PERMISSION_KEYS:
                cursor.execute(
                    "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                    (user_id, key, payload["id"]),
                )
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "permissions_overridden", "id": user_id})
        return {"status": "overridden", "id": user_id, "permissions": data.permissions}
    finally:
        conn.close()

@app.delete("/api/devteam/users/{user_id}")
async def devteam_delete_user(user_id: int, authorization: Optional[str] = Header(None)):
    """Full-power delete -- devteam can remove a captain (and, via ON DELETE
    CASCADE on parent_admin_id, that captain's own sub-accounts lose their
    parent link and become unassigned rather than vanish silently) or any
    single standard/sub-admin account directly."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if target[0] == "DEVTEAM":
        conn.close()
        raise HTTPException(status_code=403, detail="DevTeam accounts cannot be deleted from this panel")

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_deleted", "id": user_id})
    return {"status": "deleted", "id": user_id}

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
    cursor.execute("SELECT id, username, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, last_login, custom_permissions FROM users")
    user_rows = cursor.fetchall()
    users = [dict(r) for r in user_rows]
    perms_by_id = _user_permissions_json_batch(cursor, [u["id"] for u in users])
    for u in users:
        u["permissions"] = perms_by_id.get(u["id"], "{}")
        u["custom_permissions"] = bool(u["custom_permissions"])

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

def _camera_owned_by(cursor, camera_id: str, payload: dict) -> bool:
    """DEVTEAM can touch any camera. Everyone else must own it -- the
    camera's barangay_id must match the caller's own barangay_id. PNP roles
    never reach this: manage_cameras is barangay-only (BARANGAY_ONLY_PERMISSIONS),
    enforced by require_permission() before this is ever called."""
    if payload.get("role") == "DEVTEAM":
        return True
    cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (camera_id,))
    row = cursor.fetchone()
    if not row:
        return False
    return (row["barangay_id"] or "").lower() == (payload.get("barangay_id") or "").lower()


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