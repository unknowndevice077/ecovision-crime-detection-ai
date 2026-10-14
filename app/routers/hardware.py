"""ESP32 pole siren control, device registration and the manual panic trigger."""
import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from db import get_conn
from port_utils import read_runtime_ports
import backend_core
from backend_core import (
    SCREENSHOTS_DIR, _ESP32_STATE_PATH, _remember_esp32_ip, log_audit, manager, require_auth,
    require_permission, sha256_of_file,
)

router = APIRouter()


def _ai_core_capture_url() -> str:
    # AI core (maincode/main.py) may have landed on a fallback port if 8001
    # was taken -- read its actual bound port from runtime_ports.json
    # (same file Electron polls) instead of assuming 8001. Read lazily, at
    # call time, not once at import: this route is rarely called and the
    # AI core's port isn't guaranteed to be written yet when backend.py
    # itself starts up.
    port = read_runtime_ports().get("ai_core", 8001)
    return f"http://127.0.0.1:{port}/panic_capture"

class PanicSchema(BaseModel):
    event: str
    device: str
    barangay_id: Optional[str] = "cogon"

@router.post("/api/esp32/register")
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
    return {"status": "registered", "your_ip": ip, "siren_enabled": backend_core.ESP32_ENABLED}


@router.get("/api/esp32/status")
async def esp32_status(authorization: Optional[str] = Header(None)):
    """Where the backend currently thinks the pole is, and how it knows."""
    require_auth(authorization)
    info = {"ip": backend_core.ESP32_IP, "enabled": backend_core.ESP32_ENABLED, "source": "config.json ip_override / default"}
    try:
        with open(_ESP32_STATE_PATH, "r", encoding="utf-8") as fh:
            info.update(json.load(fh))
            info["source"] = "learned from the device itself"
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return info


@router.post("/api/panic_trigger")
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
            body = cap_res.json()
            # BUG FOUND 2026-10-04 (user report: a panic press saved no
            # photo): the AI core answered "screenshotPath" and this read
            # "screenshot_path", so every panic incident was stored without
            # the frame the core had just written. Both spellings accepted,
            # so an AI core from before the rename still works.
            screenshot_url = body.get("screenshot_path") or body.get("screenshotPath") or ""
            print(f"🚨 [PANIC] AI pipeline evidence capture: {body.get('status')}")
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

async def _drive_siren(authorization: Optional[str], on: bool) -> dict:
    """Shared body of /siren/activate and /siren/deactivate.

    Reports what actually happened, because the dashboard shows it to the
    operator: "sent" (the pole answered), "unreachable" (no answer, or an
    error status), "skipped" (esp32.enabled is false). Both endpoints used to
    answer 200 "…_sent" even when the pole never replied, so the dashboard's
    Emergency Stop said "Siren stopped" with nothing listening.

    BUG FOUND 2026-08-19: esp32.enabled was never checked here, so with NO
    ESP32 on the network every Confirm/Dismiss click still fired a real HTTP
    POST and blocked on a 2-second connect timeout before returning."""
    payload = require_auth(authorization)
    conn = get_conn()
    try:
        cursor = conn.cursor()
        require_permission(cursor, payload, "confirm_dismiss_alerts")
        if not backend_core.ESP32_ENABLED:
            result, detail = "skipped", "No siren is configured (esp32.enabled is false)."
        else:
            try:
                r = await asyncio.to_thread(requests.post, f"http://{backend_core.ESP32_IP}/siren/{'on' if on else 'off'}", timeout=2.0)
                ok = 200 <= r.status_code < 300
                result, detail = ("sent", None) if ok else ("unreachable", f"Siren answered HTTP {r.status_code}.")
            except Exception as e:
                print(f"⚠️  [SIREN] ESP32 unreachable at {backend_core.ESP32_IP}: {e}")
                result, detail = "unreachable", f"No answer from the siren at {backend_core.ESP32_IP}."
        log_audit(cursor, payload, f"siren.{'on' if on else 'off'}", "siren", backend_core.ESP32_IP or "-",
                  snapshot={"result": result})
        conn.commit()
    finally:
        conn.close()
    return {"status": result, "delivered": result == "sent", "detail": detail}


@router.post("/siren/activate")
async def siren_activate(authorization: Optional[str] = Header(None)):
    return await _drive_siren(authorization, on=True)

@router.post("/siren/deactivate")
async def siren_deactivate(authorization: Optional[str] = Header(None)):
    return await _drive_siren(authorization, on=False)
