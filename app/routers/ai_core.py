"""Machine-to-machine endpoints the AI detection core calls (alerts, clip registration)."""
import json
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from db import get_conn
from backend_core import (
    AI_EVENT_PHRASES, AiTriggerSchema, ManualClipSchema, SCREENSHOTS_DIR, _require_local_service,
    _safe_recordings_path, manager, sha256_of_file,
)

router = APIRouter()


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


@router.post("/api/ai_trigger")
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

@router.post("/api/ai_register_clip")
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
