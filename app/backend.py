from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import uvicorn
import json
import uuid
import os
import cv2
import numpy as np # FIXED: Added missing numpy import to resolve Pylance undefined variable error
import threading
import collections
import requests
from datetime import datetime
import time

# --- CONFIGURATION ENGINE SETUP ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, 'r') as f:
    sys_config = json.load(f)

# Safe folder verification tracking routines anchored to workspace root
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, sys_config["database"]["path"]))
LOGS_DIR = os.path.abspath(os.path.join(BASE_DIR, os.path.dirname(sys_config["monitoring"]["log_file"])))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "ecovision.db")
ESP32_IP = sys_config["esp32"]["enabled"] and sys_config["esp32"].get("ip_override") or "192.168.254.152"
RECORDINGS_DIR = os.path.join(BASE_DIR, sys_config["database"].get("recordings_subdir", "recordings"))
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "static", "screenshots")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

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
    """Tracks every open dashboard WebSocket connection and broadcasts to all of them."""
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

# --- DATABASE INITIALIZATION CORE ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, 
            role TEXT, barangayId TEXT, assignment TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, caseId TEXT, type TEXT, officer TEXT, lat REAL, lng REAL, 
            locationName TEXT, severity TEXT, date TEXT, militaryTime TEXT, narrative TEXT, 
            natureOfCall TEXT, arrivalReason TEXT, additionalOfficers TEXT, status TEXT, 
            confidence REAL, barangayId TEXT, screenshotPath TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_records (
            id TEXT PRIMARY KEY, filename TEXT, filePath TEXT, timestamp TEXT, duration TEXT, 
            type TEXT, associatedCrimeId TEXT, crimeTimeMarker TEXT, notes TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cameras (
            id TEXT PRIMARY KEY, name TEXT, url TEXT, status TEXT, barangayId TEXT
        )
    ''')
    
    # Structural column verification and migration paths
    cursor.execute("PRAGMA table_info(incidents)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "screenshotPath" not in columns:
        print("💾 [DATABASE MIGRATION] Appending missing column 'screenshotPath' to table structure...")
        cursor.execute("ALTER TABLE incidents ADD COLUMN screenshotPath TEXT")
        
    # FIXED: Added migration verify step to append barangayId column to legacy files dynamically
    if "barangayId" not in columns:
        print("💾 [DATABASE MIGRATION] Appending missing column 'barangayId' to table structure...")
        cursor.execute("ALTER TABLE incidents ADD COLUMN barangayId TEXT DEFAULT 'cogon'")

    cursor.execute("SELECT COUNT(*) FROM cameras")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO cameras VALUES ('1', 'Main Entrance Hub', 'rtsp://ecovision:REDACTED_ROTATE_THIS_PASSWORD@192.168.254.106:554/stream1', 'online', 'cogon')")
        cursor.execute("INSERT INTO cameras VALUES ('2', 'Sector B Gate', 'rtsp://192.168.1.15/stream', 'online', 'cogon')")
    
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
        """Simulates continuous video streaming capture pipeline (replaces camera hardware)"""
        while self.running:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank, f"LIVE FEED RAW - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                        (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (16, 185, 129), 2)
            
            with self.lock:
                self.latest_frame = blank.copy()
                self.frame_buffer.append(blank)
        
            time.sleep(1.0 / self.fps)

    def _continuous_247_writer_worker(self):
        """Enforces 24/7 recording logic by slicing video feeds into discrete hourly segment files"""
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
        """Saves a ShadowPlay video clip by taking buffered frames and appending post-alert footage"""
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

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("INSERT INTO video_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4())[:8], clip_filename, clip_filepath, now_str, 
                         f"{post_trigger_duration + 15}s", "CRIME_CLIP", incident_id, "00:15", "Auto-generated clip via ShadowPlay engine."))
            conn.commit()
            conn.close()

        threading.Thread(target=_async_writer, daemon=True).start()
        return f"/static/screenshots/{screenshot_filename}"

recorder_engine = VideoRecordingEngine()
recorder_engine.start_workers()

# --- DATA SCHEMAS ---
class UserSignup(BaseModel):
    username: str
    password: str
    role: str          
    barangayId: str    
    assignment: str

class UserLogin(BaseModel):
    username: str
    password: str

class IncidentSchema(BaseModel):
    id: str
    caseId: str
    type: str
    officer: str
    lat: float
    lng: float
    locationName: str
    severity: str
    date: str
    militaryTime: str
    narrative: str
    natureOfCall: str
    arrivalReason: str
    additionalOfficers: str
    status: str
    confidence: Optional[float] = 1.0
    barangayId: str

class CameraSchema(BaseModel):
    name: str
    url: str
    barangayId: str

class StatusUpdateSchema(BaseModel):
    status: str

class AiTriggerSchema(BaseModel):
    id: str
    event: str
    confidence: float
    barangayId: Optional[str] = "cogon"

class PanicSchema(BaseModel):
    event: str
    device: str
    barangayId: Optional[str] = "cogon"

class ManualClipSchema(BaseModel):
    filename: str
    duration: str
    type: str
    crimeTimeMarker: str
    notes: str

# --- PERSISTENT CAMERA ROUTINES ---
@app.get("/api/cameras")
async def get_cameras(barangayId: Optional[str] = None, role: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if role == "BARANGAY" and barangayId:
        cursor.execute("SELECT * FROM cameras WHERE LOWER(barangayId) = ?", (barangayId.lower(),))
    else:
        cursor.execute("SELECT * FROM cameras")
        
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/cameras")
async def add_camera(cam: CameraSchema):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cam_id = str(uuid.uuid4())[:8]
    try:
        cursor.execute("INSERT INTO cameras VALUES (?, ?, ?, ?, ?)",
                       (cam_id, cam.name, cam.url, "online", cam.barangayId.lower()))
        conn.commit()
        return {"status": "created", "id": cam_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.delete("/api/cameras/{cam_id}")
async def delete_camera(cam_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

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
async def get_video_records():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM video_records ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/records/register_clip")
async def register_clip(data: ManualClipSchema):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    rid = str(uuid.uuid4())[:8]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fpath = os.path.join(RECORDINGS_DIR, data.filename)
    try:
        cursor.execute("INSERT INTO video_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (rid, data.filename, fpath, now_str, data.duration, data.type, "", data.crimeTimeMarker, data.notes))
        conn.commit()
        return {"status": "registered", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Metadata write collision: {e}")
    finally:
        conn.close()

# --- AUTH SECTOR CORES ---
@app.post("/api/signup")
async def signup(user: UserSignup):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, role, barangayId, assignment) VALUES (?, ?, ?, ?, ?)",
                       (user.username, user.password, user.role.upper(), user.barangayId.lower(), user.assignment))
        conn.commit()
        return {"status": "success"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Operator profile already mapped.")
    finally:
        conn.close()

@app.post("/api/login")
async def login(creds: UserLogin):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (creds.username, creds.password))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"status": "success", "user": dict(user)}
    raise HTTPException(status_code=401, detail="Invalid Credentials")

# --- HIERARCHICAL INCIDENT FETCH DATA INTERCEPTOR ---
@app.get("/api/incidents")
async def get_incidents(userBarangayId: Optional[str] = None, role: Optional[str] = None, filterBarangayId: Optional[str] = "all"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if role == "BARANGAY" and userBarangayId:
        cursor.execute("SELECT * FROM incidents WHERE LOWER(barangayId) = ? ORDER BY date DESC, militaryTime DESC", (userBarangayId.lower(),))
        rows = cursor.fetchall()
        conn.close()
        
        secure_redacted_list = []
        for row in rows:
            record = dict(row)
            record["narrative"]          = "🔒 [RESTRICTED] Investigative logs masked for non-police profiles."
            record["natureOfCall"]       = "CONFIDENTIAL // RESTRICTED"
            record["arrivalReason"]      = "CONFIDENTIAL // RESTRICTED"
            record["additionalOfficers"] = "CONFIDENTIAL"
            secure_redacted_list.append(record)
        return secure_redacted_list
    else:
        if filterBarangayId and filterBarangayId.lower() != "all":
            cursor.execute("SELECT * FROM incidents WHERE LOWER(barangayId) = ? ORDER BY date DESC, militaryTime DESC", (filterBarangayId.lower(),))
        else:
            cursor.execute("SELECT * FROM incidents ORDER BY date DESC, militaryTime DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

@app.post("/api/incidents")
async def add_incident(incident: IncidentSchema):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,null)
        ''', (incident.id, incident.caseId, incident.type, incident.officer,
              incident.lat, incident.lng, incident.locationName, incident.severity,
              incident.date, incident.militaryTime, incident.narrative,
              incident.natureOfCall, incident.arrivalReason, incident.additionalOfficers, 
              incident.status, incident.confidence, incident.barangayId.lower()))
        conn.commit()
        return {"status": "persisted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/api/ai_trigger")
async def ai_trigger(data: AiTriggerSchema):
    incident_id = data.id if data.id else str(uuid.uuid4())[:8]
    case_id = f"CASE-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:4].upper()}"
    now = datetime.now()
    
    screenshot_url = recorder_engine.save_shadow_clip(incident_id)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (incident_id, case_id, data.event, "AI_AUTOMATION", 11.0504, 124.6062, "Cogon Core Smartpole Node", 
          "HIGH", now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), 
          f"Autonomous edge detection triggered via spatiotemporal analysis classification matrix.", 
          "EMERGENCY_AI_FLAG", "AUTOMATED_TRIGGER", "NONE", "Active", data.confidence, data.barangayId.lower(), screenshot_url))
    conn.commit()
    conn.close()
    
    await manager.broadcast({
        "status": "CRITICAL", "id": incident_id, "type": data.event, 
        "location": "Cogon Core Smartpole Node", "conf": data.confidence, "cameraLinkId": "1"
    })
    return {"status": "processed", "incident_id": incident_id}

@app.post("/api/panic_trigger")
async def panic_trigger(data: PanicSchema):
    incident_id = str(uuid.uuid4())[:8]
    case_id = f"PANIC-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:4].upper()}"
    now = datetime.now()
    
    screenshot_url = recorder_engine.save_shadow_clip(incident_id)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (incident_id, case_id, "HARDWARE_PANIC_INTERRUPT", "FIELD_NODE", 11.0510, 124.6070, 
          "Hardware Node Interface", "CRITICAL", now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), 
          f"Manual hardware safety interface switch depressed at source terminal.", 
          "PANIC_BUTTON_ENGAGED", "MANUAL_OVERRIDE", "NONE", "Active", 1.0, data.barangayId.lower(), screenshot_url))
    conn.commit()
    conn.close()
    
    await manager.broadcast({
        "status": "CRITICAL", "id": incident_id, "type": "HARDWARE_PANIC_INTERRUPT", 
        "location": "Hardware Node Interface", "conf": 1.0, "cameraLinkId": "2"
    })
    return {"status": "panic_logged", "id": incident_id}

if __name__ == "__main__":
    uvicorn.run("backend:app", host=sys_config["backend"]["host"], port=sys_config["backend"]["port"], reload=sys_config["backend"]["reload"])