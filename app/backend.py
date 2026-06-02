from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "ecovision.db"
ESP32_IP = "192.168.254.152"
RECORDINGS_DIR = r"D:\projects\EcoVisionCode\recordings"

os.makedirs(RECORDINGS_DIR, exist_ok=True)
app.mount("/static/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")

active_connections: List[WebSocket] = []

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT, assignment TEXT
        )
    ''')
    # FIXED: Appended explicit structured confidence floating-point data column to schema definition
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, caseId TEXT, type TEXT, officer TEXT, lat REAL, lng REAL, 
            locationName TEXT, severity TEXT, date TEXT, militaryTime TEXT, narrative TEXT, 
            natureOfCall TEXT, arrivalReason TEXT, additionalOfficers TEXT, status TEXT, confidence REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_records (
            id TEXT PRIMARY KEY, filename TEXT, filePath TEXT, timestamp TEXT, duration TEXT, 
            type TEXT, associatedCrimeId TEXT, crimeTimeMarker TEXT, notes TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- DATA MODELS ---
class UserSignup(BaseModel):
    username: str
    password: str
    role: str
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

class AiTriggerSchema(BaseModel):
    id: str
    event: str
    confidence: float

class PanicSchema(BaseModel):
    event: str
    device: str

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

# --- RECORDS INTERACTION ENDPOINTS ---
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

@app.patch("/api/records/{record_id}/notes")
async def update_record_notes(record_id: str, data: NoteUpdateSchema):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE video_records SET notes = ? WHERE id = ?", (data.notes, record_id))
    conn.commit()
    conn.close()
    return {"status": "notes_saved"}

# --- AUTHENTICATION ENDPOINTS ---
@app.post("/api/signup")
async def signup(user: UserSignup):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, role, assignment) VALUES (?, ?, ?, ?)",
                       (user.username, user.password, user.role, user.assignment))
        conn.commit()
        return {"status": "success"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Operator ID already exists")
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

# --- INCIDENT REPORTING ENDPOINTS ---
@app.get("/api/incidents", response_model=List[IncidentSchema])
async def get_incidents():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
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
            INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (incident.id, incident.caseId, incident.type, incident.officer,
              incident.lat, incident.lng, incident.locationName, incident.severity,
              incident.date, incident.militaryTime, incident.narrative,
              incident.natureOfCall, incident.arrivalReason, incident.additionalOfficers, incident.status, incident.confidence))
        conn.commit()
        return {"status": "persisted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data persistence failure: {e}")
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
        # FIXED: Cleared dynamic percentage text injections out of textual narrative statements
        clean_narrative = f"Automated neural detection of {data.event}."
        cursor.execute('''
            INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (incident_id, case_id, data.event, "AI_SENTINEL", 11.0176, 124.6031, "Cogon Core Smartpole", "CRITICAL", 
              current_date, current_time, clean_narrative, "AI Threat Flag", "Automated Tracking", "None", "Active", data.confidence))
        conn.commit()
        
        alert_payload = {
            "status": "CRITICAL",
            "id": incident_id,
            "type": data.event,
            "conf": data.confidence
        }
        for connection in active_connections:
            try:
                await connection.send_text(json.dumps(alert_payload))
            except Exception:
                pass
                
        return {"status": "persisted", "id": incident_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data persistence failure: {e}")
    finally:
        conn.close()

@app.post("/api/panic")
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
            INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (incident_id, case_id, "MANUAL_PANIC", "HARDWARE_BUTTON", 11.0176, 124.6031, "Cogon Core Smartpole", "CRITICAL", 
              current_date, current_time, "Physical hardware panic button pressed manually on device node.", 
              "Emergency Panic System", "Manual Activation", "None", "Active", 1.00))
        conn.commit()
        
        alert_payload = {
            "status": "CRITICAL",
            "id": incident_id,
            "type": "MANUAL_PANIC",
            "conf": 1.00
        }
        for connection in active_connections:
            try:
                await connection.send_text(json.dumps(alert_payload))
            except Exception:
                pass
                
        return {"status": "panic_recorded", "id": incident_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data persistence failure: {e}")
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)

@app.post("/siren/activate")
async def activate_siren():
    try:
        requests.get(f"http://{ESP32_IP}/alarm/on", timeout=1.0)
    except Exception:
        pass
    return {"status": "siren_triggered"}

@app.post("/siren/reset")
async def reset_siren():
    try:
        requests.get(f"http://{ESP32_IP}/alarm/off", timeout=0.2)
    except Exception:
        pass
    return {"status": "siren_nominal"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)