from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from db import get_conn, IntegrityError
import uvicorn
import json
import uuid
import os
import cv2
import numpy as np
import threading
import collections
import requests
from datetime import datetime, timedelta
import time
import hashlib
import hmac
import base64
import secrets
from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.environ.get("APP_ENV", "development")
DATABASE_URL = os.environ.get("DATABASE_URL")  # set -> Postgres; unset -> SQLite fallback
CORS_ORIGINS_ENV = os.environ.get("CORS_ORIGINS")  # comma-separated
SECRET_KEY_ENV = os.environ.get("SECRET_KEY")

# --- CONFIGURATION ENGINE SETUP ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_CONFIG_PATH = os.path.join(BASE_DIR, f"config.{APP_ENV}.json")
CONFIG_PATH = _ENV_CONFIG_PATH if os.path.exists(_ENV_CONFIG_PATH) else os.path.join(BASE_DIR, "config.json")

WRITABLE_DIR = os.environ.get("ECOVISION_WRITABLE_DIR")
if not WRITABLE_DIR:
    WRITABLE_DIR = os.path.join(os.path.expanduser("~"), "EcoVisionSentinelData")
os.makedirs(WRITABLE_DIR, exist_ok=True)

with open(CONFIG_PATH, 'r') as f:
    sys_config = json.load(f)

WRITABLE_CONFIG_PATH = os.path.join(WRITABLE_DIR, "config.json")
if os.path.exists(WRITABLE_CONFIG_PATH):
    with open(WRITABLE_CONFIG_PATH, 'r') as f:
        sys_config = json.load(f)

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
    return verify_token(authorization.removeprefix("Bearer "))

def require_role(payload: dict, allowed_roles: set):
    if payload["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail=f"'{payload['role']}' accounts cannot do this")

# Safe folder verification tracking routines anchored to the WRITABLE
# root, never BASE_DIR -- BASE_DIR can be a read-only install directory.
DATA_DIR = os.path.abspath(os.path.join(WRITABLE_DIR, sys_config["database"]["path"]))
LOGS_DIR = os.path.abspath(os.path.join(WRITABLE_DIR, os.path.dirname(sys_config["monitoring"]["log_file"])))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "ecovision.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_final.sql")
ESP32_IP = sys_config["esp32"]["enabled"] and sys_config["esp32"].get("ip_override") or "192.168.254.152"
RECORDINGS_DIR = os.path.join(WRITABLE_DIR, sys_config["database"].get("recordings_subdir", "recordings"))
SCREENSHOTS_DIR = os.path.join(WRITABLE_DIR, "static", "screenshots")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

AI_PIPELINE_CAPTURE_URL = "http://localhost:8001/panic_capture"

app = FastAPI(
    title=sys_config["system"]["name"],
    version=sys_config["system"]["version"]
)

if sys_config["security"]["enable_cors"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sys_config["security"]["cors_origins"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT to_regclass('public.users') AS reg")
    table_check = cursor.fetchone()
    if not table_check or table_check["reg"] is None:
        if not os.path.exists(SCHEMA_PATH):
            conn.close()
            raise RuntimeError(
                f"Database is empty and {SCHEMA_PATH} was not found. "
                "Copy schema_final.sql next to backend.py, or run it manually against ecovision.db."
            )
        conn.executescript(open(SCHEMA_PATH).read())
        conn.commit()
        print(f"💾 [DATABASE] Applied schema_final.sql to fresh database at {DB_PATH}")

    cursor.execute("SELECT id, password FROM users")
    for row_id, pw in cursor.fetchall():
        if pw and "$" not in pw:
            cursor.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(pw), row_id))
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'DEVTEAM'")
    if cursor.fetchone()[0] == 0:
        bootstrap_password = secrets.token_urlsafe(12)
        cursor.execute(
            "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id) "
            "VALUES (?, ?, 'DEVTEAM', NULL, 'DevTeam HQ', NULL)",
            ("devteam", hash_password(bootstrap_password)),
        )
        conn.commit()
        print("=" * 60)
        print("🔑 [BOOTSTRAP] First-run DEVTEAM account created:")
        print(f"    username: devteam")
        print(f"    password: {bootstrap_password}")
        print("    Save this now -- it will not be shown again.")
        print("=" * 60)

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
                (str(uuid.uuid4())[:8], clip_filename, clip_filepath, now_str,
                 f"{post_trigger_duration + 15}s", "CRIME_CLIP", incident_id, "00:15",
                 "Auto-generated clip via ShadowPlay engine."),
            )
            conn.commit()
            conn.close()

        threading.Thread(target=_async_writer, daemon=True).start()
        return f"/static/screenshots/{screenshot_filename}"

recorder_engine = VideoRecordingEngine()
recorder_engine.start_workers()

# --- DATA SCHEMAS ---
# All request bodies now use the same snake_case field names as the
# responses -- e.g. barangay_id, case_id, occurred_time -- so the frontend
# doesn't have to remember two different cases depending on whether it's
# reading or writing. If page.tsx / CrimeReportsView.tsx still POST
# camelCase bodies, they need to be updated to match these field names.
ADMIN_ROLES = {"PRECINCT_CAPTAIN", "BARANGAY_CAPTAIN"}
STANDARD_ROLES = {"POLICE", "BARANGAY"}
ADMIN_CREATES_ROLE = {"PRECINCT_CAPTAIN": "POLICE", "BARANGAY_CAPTAIN": "BARANGAY"}
ALL_ROLES = ADMIN_ROLES | STANDARD_ROLES | {"DEVTEAM"}
ADMIN_OR_DEVTEAM = ADMIN_ROLES | {"DEVTEAM"}
VALID_PERMISSION_KEYS = {"view_map", "view_records", "view_history", "manage_cameras", "confirm_dismiss_alerts"}

class UserSignup(BaseModel):
    username: str
    password: str
    role: str
    barangay_id: str
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

class StatusUpdateSchema(BaseModel):
    status: str

class AiTriggerSchema(BaseModel):
    id: str
    event: str
    confidence: float
    barangay_id: Optional[str] = "cogon"
    screenshot_path: Optional[str] = None

class PanicSchema(BaseModel):
    event: str
    device: str
    barangay_id: Optional[str] = "cogon"

class ConfirmAndReportSchema(BaseModel):
    status: str
    capture_snapshot: Optional[bool] = False
    report_details: Optional[dict] = None

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
    role: Optional[str] = None

class DevteamCreateUser(BaseModel):
    username: str
    password: str
    role: str
    barangay_id: Optional[str] = None
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

def _row_to_user_dict(cursor, row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "username": d["username"], "role": d["role"],
        "barangay_id": d.get("barangay_id"), "assignment": d.get("assignment"),
        "parent_admin_id": d.get("parent_admin_id"), "display_title": d.get("display_title"),
        "is_sub_admin": bool(d.get("is_sub_admin")),
        "permissions": _user_permissions_json(cursor, d["id"]),
    }


# --- PERSISTENT CAMERA ROUTINES ---
@app.get("/api/cameras")
async def get_cameras(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()

    # DEVTEAM sees everything system-wide. Every other role (BARANGAY,
    # BARANGAY_CAPTAIN, POLICE, PRECINCT_CAPTAIN) is scoped to cameras at
    # their own barangay_id -- a Precinct Captain and their paired Barangay
    # Captain share the same barangay_id (enforced by the one-per-barangay
    # unique indexes in schema_final.sql), so this is what actually gives
    # police visibility into "cameras under their precinct."
    if payload["role"] != "DEVTEAM" and payload.get("barangay_id"):
        cursor.execute("SELECT * FROM cameras WHERE LOWER(barangay_id) = ?", (payload["barangay_id"].lower(),))
    else:
        cursor.execute("SELECT * FROM cameras")
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_camera_dict(r) for r in rows]

def _has_permission(cursor, user_id: int, key: str, role: str) -> bool:
    if role in ADMIN_ROLES:
        return True
    cursor.execute("SELECT 1 FROM user_permissions WHERE user_id = ? AND permission_key = ?", (user_id, key))
    return cursor.fetchone() is not None

def require_permission(cursor, payload: dict, key: str):
    """Server-side gate matching the permission checkboxes in
    AdminUsersView.tsx / DevteamView.tsx. DEVTEAM and admin tiers always
    pass. Standard POLICE/BARANGAY accounts must have the key granted in
    user_permissions."""
    if payload["role"] == "DEVTEAM" or payload["role"] in ADMIN_ROLES:
        return
    if not _has_permission(cursor, payload["id"], key, payload["role"]):
        raise HTTPException(status_code=403, detail=f"Missing permission: {key}")

@app.post("/api/cameras")
async def add_camera(cam: CameraSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_cameras")
    cam_id = str(uuid.uuid4())[:8]
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
    cursor.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


# --- HIERARCHICAL INCIDENT FETCH ---
@app.get("/api/incidents")
async def get_incidents(authorization: Optional[str] = Header(None), filter_barangay_id: Optional[str] = "all"):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()

    role = payload["role"]
    if role != "DEVTEAM" and role not in ADMIN_ROLES:
        cursor.execute(
            "SELECT 1 FROM user_permissions WHERE user_id = ? AND permission_key IN ('view_map','view_history')",
            (payload["id"],),
        )
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=403, detail="Missing permission: view_map or view_history")

    user_barangay = payload.get("barangay_id")

    if role in ("BARANGAY", "BARANGAY_CAPTAIN") and user_barangay:
        cursor.execute(
            "SELECT * FROM incidents WHERE LOWER(barangay_id) = ? ORDER BY occurred_date DESC, occurred_time DESC",
            (user_barangay.lower(),),
        )
        redact = True
    else:
        # POLICE / PRECINCT_CAPTAIN / DEVTEAM see full detail. filter_barangay_id
        # only ever narrows the result set further for these already-
        # privileged roles -- it cannot be used to escalate.
        if filter_barangay_id and filter_barangay_id.lower() != "all":
            cursor.execute(
                "SELECT * FROM incidents WHERE LOWER(barangay_id) = ? ORDER BY occurred_date DESC, occurred_time DESC",
                (filter_barangay_id.lower(),),
            )
        else:
            cursor.execute("SELECT * FROM incidents ORDER BY occurred_date DESC, occurred_time DESC")
        redact = False

    inc_rows = cursor.fetchall()
    results = []
    for inc in inc_rows:
        cursor.execute("SELECT * FROM incident_details WHERE incident_id = ?", (inc["id"],))
        details = cursor.fetchone()
        cursor.execute("SELECT * FROM incident_visibility WHERE incident_id = ?", (inc["id"],))
        vis = cursor.fetchone()
        record = _row_to_incident_dict(inc, details, vis)
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
    require_auth(authorization)
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
             incident.confidence, incident.officer, incident.barangay_id.lower()),
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
    incident_id = data.id if data.id else str(uuid.uuid4())[:8]
    case_id = f"CASE-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:4].upper()}"
    now = datetime.now()
    screenshot_url = data.screenshot_path or ""

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO incidents
           (id, case_id, type, severity, status, lat, lng, location_name,
            occurred_date, occurred_time, confidence, officer, barangay_id, source)
           VALUES (?, ?, ?, 'HIGH', 'Active', ?, ?, ?, ?, ?, ?, 'AI_AUTOMATION', ?, 'AI_AUTOMATION')""",
        (incident_id, case_id, data.event, 11.0504, 124.6062, "Cogon Core Smartpole Node",
         now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), data.confidence, data.barangay_id.lower()),
    )
    cursor.execute(
        """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, 'EMERGENCY_AI_FLAG', 'AUTOMATED_TRIGGER', 'NONE')""",
        (incident_id, "Autonomous edge detection triggered via spatiotemporal analysis classification matrix."),
    )
    cursor.execute(
        "INSERT INTO incident_visibility (incident_id, map_hidden, screenshot_path) VALUES (?, 0, ?)",
        (incident_id, screenshot_url),
    )
    conn.commit()
    conn.close()

    await manager.broadcast({
        "channel": "incidents", "status": "CRITICAL", "id": incident_id, "type": data.event,
        "location": "Cogon Core Smartpole Node", "conf": data.confidence, "camera_link_id": "1",
    })
    return {"status": "processed", "incident_id": incident_id}

@app.post("/api/panic_trigger")
async def panic_trigger(data: PanicSchema):
    incident_id = str(uuid.uuid4())[:8]
    case_id = f"PANIC-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:4].upper()}"
    now = datetime.now()

    screenshot_url = ""
    try:
        cap_res = requests.post(AI_PIPELINE_CAPTURE_URL, json={"incident_id": incident_id}, timeout=2.0)
        if cap_res.ok:
            screenshot_url = cap_res.json().get("screenshot_path") or ""
            print(f"🚨 [PANIC] AI pipeline evidence capture: {cap_res.json().get('status')}")
    except Exception as e:
        print(f"⚠️  [PANIC] AI pipeline unreachable, logging panic with no evidence: {e}")

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
        "INSERT INTO incident_visibility (incident_id, map_hidden, screenshot_path) VALUES (?, 0, ?)",
        (incident_id, screenshot_url),
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
    cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "deleted"}

@app.patch("/api/incidents/{incident_id}/archive")
async def archive_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
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
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    officer = (data.report_details or {}).get("reporting_officer")
    if officer:
        cursor.execute("UPDATE incidents SET status = ?, officer = ? WHERE id = ?", (data.status, officer, incident_id))
    else:
        cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    await manager.broadcast({"channel": "incidents", "id": incident_id, "event": "confirmed_and_reported"})
    return {"status": "confirmed_and_reported", "id": incident_id}

@app.post("/siren/activate")
async def siren_activate(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    conn.close()
    try:
        requests.post(f"http://{ESP32_IP}/siren/on", timeout=2.0)
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
    try:
        requests.post(f"http://{ESP32_IP}/siren/off", timeout=2.0)
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
    cursor.execute("SELECT * FROM video_records ORDER BY recorded_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_record_dict(r) for r in rows]

@app.post("/api/records/register_clip")
async def register_clip(data: ManualClipSchema, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    rid = str(uuid.uuid4())[:8]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fpath = os.path.join(RECORDINGS_DIR, data.filename)
    try:
        cursor.execute(
            """INSERT INTO video_records
               (id, filename, file_path, recorded_at, duration, type, associated_incident_id, crime_time_marker, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.filename, fpath, now_str, data.duration, data.type,
             data.associated_incident_id or None, data.crime_time_marker, data.notes),
        )
        conn.commit()
        return {"status": "registered", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Metadata write collision: {e}")
    finally:
        conn.close()

@app.patch("/api/records/{record_id}/notes")
async def update_record_notes(record_id: str, data: RecordNotesSchema, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE video_records SET notes = ? WHERE id = ?", (data.notes, record_id))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"status": "updated", "id": record_id}

# --- AUTH SECTOR CORES ---
@app.post("/api/signup")
async def signup(user: UserSignup):
    role = user.role.upper()
    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only Precinct Captain / Barangay Captain accounts can self-register. "
                   "Standard user accounts must be created by your admin.",
        )
    barangay_id = user.barangay_id.strip().lower()
    if not barangay_id:
        raise HTTPException(status_code=400, detail="Location is required")

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
        existing = cursor.fetchone()
        if not existing:
            cursor.execute(
                "INSERT INTO barangays (id, name, status) VALUES (?, ?, 'pending')",
                (barangay_id, user.barangay_id.strip().title()),
            )

        cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = ?", (barangay_id, role))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"This location already has a {role.replace('_', ' ').title()} account.",
            )

        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (user.username, hash_password(user.password), role, barangay_id, user.assignment),
        )
        new_user_id = cursor.lastrowid
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
async def login(creds: UserLogin):
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
    result = [_row_to_user_dict(cursor, r) for r in rows]
    conn.close()
    return result

@app.post("/api/admin/users")
async def create_my_user(new_user: AdminCreateUser, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    target_role = ADMIN_CREATES_ROLE.get(payload["role"], "POLICE") if payload["role"] != "DEVTEAM" else "POLICE"

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id, display_title, is_sub_admin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_user.username, hash_password(new_user.password), target_role,
             payload["barangay_id"], new_user.assignment, payload["id"],
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
    (PRECINCT_CAPTAIN, BARANGAY_CAPTAIN, POLICE, BARANGAY) directly,
    bypassing the self-signup approval flow, and grant it a permission
    set from the same permission tree admins use for their sub-accounts.
    A captain role still enforces the one-per-location unique index at
    the DB level. Standard roles (POLICE/BARANGAY) can optionally be
    slotted under an existing admin via parent_admin_id so they show up
    under that admin's directory entry."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    role = new_user.role.upper()
    if role not in ALL_ROLES or role == "DEVTEAM":
        raise HTTPException(status_code=400, detail=f"Invalid role '{new_user.role}'")

    barangay_id = (new_user.barangay_id or "").strip().lower()
    if role in ADMIN_ROLES | STANDARD_ROLES and not barangay_id:
        raise HTTPException(status_code=400, detail="Location is required for this role")

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if barangay_id:
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO barangays (id, name, status, approved_by, approved_at) "
                    "VALUES (?, ?, 'approved', ?, NOW())",
                    (barangay_id, barangay_id.title(), payload["id"]),
                )

        parent_id = new_user.parent_admin_id
        if role in STANDARD_ROLES and parent_id is None:
            # auto-attach to whichever captain already runs this location,
            # so the account shows up nested under someone in the directory
            captain_role = "PRECINCT_CAPTAIN" if role == "POLICE" else "BARANGAY_CAPTAIN"
            cursor.execute(
                "SELECT id FROM users WHERE barangay_id = ? AND role = ?",
                (barangay_id, captain_role),
            )
            existing_captain = cursor.fetchone()
            parent_id = existing_captain["id"] if existing_captain else None

        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, assignment, parent_admin_id, display_title, is_sub_admin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_user.username, hash_password(new_user.password), role,
             barangay_id or None, new_user.assignment, parent_id,
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
    if data.barangay_id is not None:
        fields.append("barangay_id = ?"); values.append(data.barangay_id.lower())
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

    cursor.execute("SELECT id, username, role, barangay_id, assignment, parent_admin_id, display_title, is_sub_admin FROM users")
    users = []
    for r in cursor.fetchall():
        u = dict(r)
        u["permissions"] = _user_permissions_json(cursor, u["id"])
        users.append(u)

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

if __name__ == "__main__":
    uvicorn.run("backend:app", host=sys_config["backend"]["host"], port=sys_config["backend"]["port"], reload=sys_config["backend"]["reload"])