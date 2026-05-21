import cv2
import json
import uvicorn
import time
import requests 
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Dict

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 🟢 GLOBAL HARDWARE STATE ---
SIREN_ACTIVE = False #

# --- 🟢 TELEGRAM CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "8496005825:AAFDj7Sx0Vdf7OPwIVr151jCIWiRCxL6y6c"
TELEGRAM_CHAT_ID = "5395241726"

def send_telegram_msg(message):
    """Sends a tactical text alert to your Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram Failed: {e}")

# --- DATABASE & GLOBAL STATE ---
cameras_db: Dict[str, dict] = {
    "1": {"id": "1", "name": "Main Entrance Hub", "url": "rtsp://...", "status": "online"}
}
active_websockets = []
latest_annotated_frame = None

# --- MJPEG STREAMER ---
def generate_frames():
    global latest_annotated_frame
    while True:
        if latest_annotated_frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_annotated_frame + b'\r\n')
        else:
            time.sleep(0.01)

@app.get("/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.post("/update_frame")
async def update_frame(frame: bytes = File(...)):
    global latest_annotated_frame
    latest_annotated_frame = frame
    return {"status": "ok"}

# --- 🟢 SIREN HARDWARE ENDPOINTS ---
@app.get("/siren/status")
async def get_siren_status():
    """Endpoint for ESP32 polling."""
    return {"siren_active": SIREN_ACTIVE}

@app.post("/siren/activate")
async def activate_siren():
    global SIREN_ACTIVE
    SIREN_ACTIVE = True #
    return {"status": "siren_on"}

@app.post("/siren/reset")
async def reset_siren():
    global SIREN_ACTIVE
    SIREN_ACTIVE = False #
    return {"status": "siren_off"}

# --- SYSTEM TRIGGERS ---
@app.post("/trigger")
async def trigger_alert(data: dict):
    """Unified trigger: Broadcasts to Dashboard AND sends Telegram message."""
    event_type = data.get('event', 'VIOLENCE')
    confidence = data.get('confidence', 0.94)
    timestamp = datetime.now().strftime('%H:%M:%S')

    # 1. Telegram Alert
    msg = (
        f"🚨 *ECOVISION ALERT*\n"
        f"⚠️ Type: {event_type}\n"
        f"📈 Confidence: {confidence*100:.1f}%\n"
        f"🕒 Time: {timestamp}\n"
        f"📍 Sector: Downtown Main Street"
    )
    send_telegram_msg(msg)

    # 2. WebSocket Broadcast
    payload = json.dumps({
        "status": "CRITICAL", 
        "type": event_type, 
        "conf": confidence,
        "id": data.get('id', '0'),
        "timestamp": timestamp
    })
    for ws in active_websockets:
        await ws.send_text(payload)
        
    return {"status": "broadcasted"}

@app.get("/api/cameras")
async def get_cameras():
    return list(cameras_db.values())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.remove(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)