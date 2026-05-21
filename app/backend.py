from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import sqlite3
import uvicorn
import json

app = FastAPI()

# --- CORS CONFIGURATION ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "ecovision.db"

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT,
            assignment TEXT
        )
    ''')
    # Incidents Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            caseId TEXT,
            type TEXT,
            officer TEXT,
            lat REAL,
            lng REAL,
            locationName TEXT,
            severity TEXT,
            date TEXT,
            militaryTime TEXT,
            narrative TEXT,
            natureOfCall TEXT,
            arrivalReason TEXT,
            additionalOfficers TEXT,
            status TEXT
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
            INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (incident.id, incident.caseId, incident.type, incident.officer,
              incident.lat, incident.lng, incident.locationName, incident.severity,
              incident.date, incident.militaryTime, incident.narrative,
              incident.natureOfCall, incident.arrivalReason, incident.additionalOfficers, incident.status))
        conn.commit()
        return {"status": "persisted"}
    except Exception as e:
        print(f"Error saving incident: {e}")
        raise HTTPException(status_code=400, detail="Data persistence failure")
    finally:
        conn.close()

# --- FIXED DELETE HANDLER ---
@app.delete("/api/incidents/{incident_id}")
async def delete_incident(incident_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Check if record exists before deleting
    cursor.execute("SELECT id FROM incidents WHERE id = ?", (incident_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Case ID not found in archives")
    
    cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    conn.commit()
    conn.close()
    return {"status": "expunged", "id": incident_id}

# --- SURVEILLANCE & ACTUATOR ENDPOINTS ---
@app.get("/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    # Restore this logic to stop the 404 errors in browser grid
    return {"status": "uplink_active", "node": camera_id}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Maintain active bridge for neural alerts
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass

@app.post("/siren/activate")
async def activate_siren():
    return {"status": "siren_triggered"}

@app.post("/siren/reset")
async def reset_siren():
    return {"status": "siren_nominal"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)