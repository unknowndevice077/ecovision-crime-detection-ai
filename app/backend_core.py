"""Shared state and helpers for the EcoVision backend.

Configuration, auth (password hashing and signed session tokens), database
setup and migrations, permission and scope checks, serializers, the WebSocket
broadcast manager and the background recording workers. The FastAPI app is
built in backend.py and the endpoints live in routers/.

State that is rebound at runtime (ESP32_IP, ESP32_ENABLED, _optimize_proc) is
read as `backend_core.NAME` from the routers, never imported by value, so every
module sees the current value.
"""
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

from fastapi import HTTPException, Request, WebSocket, Header, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from typing import Optional
import json
import uuid
import os
import cv2
import numpy as np
import threading
import collections
import asyncio
from datetime import datetime
import time
import hashlib
import hmac
import base64
import secrets
import contextvars
from dotenv import load_dotenv
from db import get_conn, DB_KIND, table_exists, SQLITE_PATH
from port_utils import start_parent_watchdog
from config_store import load_overlay, save_overrides
from maintenance import start_maintenance_scheduler
from telegram_bot import start_telegram_registration_poller

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
# Only what this machine itself changed -- see app/config_store.py.
sys_config = _deep_merge(sys_config, load_overlay(WRITABLE_CONFIG_PATH, _BASE_CONFIG_PATH))

if CORS_ORIGINS_ENV:
    sys_config.setdefault("security", {})["cors_origins"] = [o.strip() for o in CORS_ORIGINS_ENV.split(",")]

# --- AUTH: PASSWORD HASHING + SIGNED SESSION TOKENS ---
if SECRET_KEY_ENV:
    sys_config.setdefault("auth", {})["secret_key"] = SECRET_KEY_ENV
if "auth" not in sys_config or not sys_config.get("auth", {}).get("secret_key"):
    sys_config.setdefault("auth", {})["secret_key"] = secrets.token_hex(32)
    # Write to the WRITABLE copy, never back to CONFIG_PATH (BASE_DIR) --
    # that path can be read-only on a packaged install. Just the key: a full
    # dump here pinned every other setting on this machine (config_store.py).
    save_overrides(WRITABLE_CONFIG_PATH, {("auth", "secret_key"): sys_config["auth"]["secret_key"]},
                   _BASE_CONFIG_PATH)
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


limiter = Limiter(key_func=get_remote_address)

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
    # 2026-10-12: a deleted incident is gone for everyone but DevTeam (who
    # restore it from the audit log). Without this, anyone still holding its
    # id -- an open tab, a notification -- could keep changing its status or
    # filing reports on an incident no list shows any more.
    frag, params = scope_clause(payload)
    cursor.execute(f"SELECT 1 FROM incidents WHERE id = ? AND deleted_at IS NULL"
                   + (f" AND {frag}" if frag else ""), [incident_id] + params)
    return cursor.fetchone() is not None

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
    # Added 2026-10-12: true when the AI core re-labels an incident it
    # already reported (a verified robbery on the same struggle as an open
    # ASSAULT, main.py ROBBERY_MERGE_SECONDS) instead of reporting a second one.
    upgrade: bool = False

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

class ResourceScopesUpdate(BaseModel):
    # {permission_key: {resource_type: [ids] | None}}. None clears that
    # dimension (everything the account's org allows); a list = only those.
    scopes: dict

class ResourceGrantRequest(BaseModel):
    permission_key: str
    resource_type: str
    resource_id: str


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

class VerificationReview(BaseModel):
    decision: str  # 'verified' | 'rejected'
    note: Optional[str] = None

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


def _parse_report_row(r) -> dict:
    d = dict(r)
    for k in ("ai_draft", "report_body"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except (TypeError, ValueError):
            d[k] = None
    return d


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


def _slugify_barangay(name: str) -> str:
    import re
    s = re.sub(r"^(brgy\.?|barangay)\s+", "", name.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


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
                     "weapon", "verifier")


def _read_config_file():
    """config.json as the DETECTOR sees it: base + env overlay + writable.

    Must mirror the layering in maincode/main.py exactly. Reading CONFIG_PATH
    alone (which resolves to config.<APP_ENV>.json when that file exists) was
    why this endpoint served no statistics for weapons, robbery or vandalism:
    those blocks live only in config.json, and the env file replaced it.
    """
    with open(_BASE_CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if _ENV_CONFIG_PATH and os.path.exists(_ENV_CONFIG_PATH):
        try:
            with open(_ENV_CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = _deep_merge(cfg, json.load(fh))
        except Exception:
            pass          # a malformed overlay must not blank the panel
    return _deep_merge(cfg, load_overlay(WRITABLE_CONFIG_PATH, _BASE_CONFIG_PATH))


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
