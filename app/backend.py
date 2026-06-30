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
import requests
from datetime import datetime

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
os.makedirs(RECORDINGS_DIR, exist_ok=True)

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

# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET REAL-TIME CONNECTION BROADCAST MANAGER
# ─────────────────────────────────────────────────────────────────────────────
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
            confidence REAL, barangayId TEXT
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
    
    # Pre-seed defaults if table is empty
    cursor.execute("SELECT COUNT(*) FROM cameras")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO cameras VALUES ('1', 'Main Entrance Hub', 'rtsp://ecovision:REDACTED_ROTATE_THIS_PASSWORD@192.168.254.106:554/stream1', 'online', 'cogon')")
        cursor.execute("INSERT INTO cameras VALUES ('2', 'Sector B Gate', 'rtsp://192.168.1.15/stream', 'online', 'cogon')")
    
    conn.commit()
    conn.close()

init_db()

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

class AiTriggerSchema(BaseModel):
    id: str
    event: str
    confidence: float
    barangayId: Optional[str] = "cogon"

class PanicSchema(BaseModel):
    event: str
    device: str
    barangayId: Optional[str] = "cogon"

class CameraSchema(BaseModel):
    name: str
    url: str
    barangayId: str

class StatusUpdateSchema(BaseModel):
    status: str

class NoteUpdateSchema(BaseModel):
    notes: str

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

# ─────────────────────────────────────────────────────────────────────────────
# LIVE DASHBOARD STREAM INTERACTION TERMINAL ROUTE
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Captures client status nodes to safeguard socket execution registers
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
            INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
async def ai_trigger(data: AiTriggerSchema, request: Request):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")
    incident_id = str(uuid.uuid4())[:8]
    case_id = f"CASE-{incident_id.upper()}"
    
    try:
        clean_narrative = f"Automated neural detection of {data.event}."
        cursor.execute('''
            INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (incident_id, case_id, data.event, "AI_SENTINEL", 11.0176, 124.6031, "Cogon Core Smartpole", "CRITICAL", 
              current_date, current_time, clean_narrative, "AI Threat Flag", "Automated Tracking", "None", "Active", data.confidence, data.barangayId.lower()))
        conn.commit()

        # Pushes transaction payload natively to every operational UI layout frame instantly
        await manager.broadcast({
            "status": "CRITICAL",
            "id": incident_id,
            "type": data.event,
            "location": "Cogon Core Smartpole",
            "conf": data.confidence,
            "cameraLinkId": "1",
        })

        return {"status": "persisted", "id": incident_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/panic")
async def panic_trigger(data: PanicSchema, request: Request):
    global ESP32_IP
    ESP32_IP = request.client.host
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")
    incident_id = str(uuid.uuid4())[:8]
    case_id = f"CASE-PANIC-{incident_id.upper()}"
    
    try:
        cursor.execute('''
            INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (incident_id, case_id, "MANUAL_PANIC", "HARDWARE_BUTTON", 11.0176, 124.6031, "Cogon Core Smartpole", "CRITICAL", 
              current_date, current_time, "Physical hardware panic button pressed manually on device node.", 
              "Emergency Panic System", "Manual Activation", "None", "Active", 1.00, data.barangayId.lower()))
        conn.commit()

        # Broadcast hardware interrupt alerts instantly across open clients
        await manager.broadcast({
            "status": "CRITICAL",
            "id": incident_id,
            "type": "MANUAL_PANIC",
            "location": "Cogon Core Smartpole",
            "conf": 1.00,
            "cameraLinkId": "1",
        })

        return {"status": "panic_recorded", "id": incident_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.patch("/api/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, data: StatusUpdateSchema):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    conn.commit()
    conn.close()
    return {"status": "status_updated"}

@app.delete("/api/incidents/{incident_id}")
async def delete_incident(incident_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    conn.commit()
    conn.close()
    return {"status": "expunged"}

@app.post("/siren/activate")
async def activate_siren():
    try: requests.get(f"http://{ESP32_IP}/alarm/on", timeout=1.0)
    except Exception: pass
    return {"status": "siren_triggered"}

@app.post("/siren/reset")
async def reset_siren():
    try: requests.get(f"http://{ESP32_IP}/alarm/off", timeout=0.2)
    except Exception: pass
    return {"status": "siren_nominal"}

if __name__ == "__main__":
    uvicorn.run("backend:app", host=sys_config["backend"]["host"], port=sys_config["backend"]["port"], reload=sys_config["backend"]["reload"])