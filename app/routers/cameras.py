"""Camera management, per-camera detector/threshold overrides and PTZ control."""
import uuid
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Header, Request
from pydantic import BaseModel, field_validator

from db import get_conn
from backend_core import (
    DETECTION_CLASSES, _holds_permission, _read_config_file, _require_local_service, apply_scope,
    log_audit, manager, require_auth, require_permission,
)

router = APIRouter()


class CameraLocationSchema(BaseModel):
    lat: float
    lng: float
    location_label: Optional[str] = None

    @field_validator("lat")
    @classmethod
    def _lat_range(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("lng")
    @classmethod
    def _lng_range(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

class CameraSchema(BaseModel):
    name: str
    url: str
    barangay_id: str
    # Where the pole stands, pinned on the Incident Map. Optional: the
    # Cameras tab registers a stream without a location, placed later.
    location: Optional[CameraLocationSchema] = None

def _row_to_camera_dict(row, include_url: bool = True) -> dict:
    """include_url=False for anyone who can't manage cameras: a stream URL
    usually carries the camera's own username and password."""
    d = dict(row)
    return {"id": d["id"], "name": d["name"], "url": d["url"] if include_url else None,
            "status": d["status"], "barangay_id": d.get("barangay_id"),
            "lat": d.get("lat"), "lng": d.get("lng"), "location_label": d.get("location_label")}


# --- PERSISTENT CAMERA ROUTINES ---
@router.get("/api/cameras")
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

    # Phase 2 resource-scoped permissions (2026-09-23): additive-only, see
    # permission_grants' own migration comment. A user with zero
    # resource-scoped view_map/camera rows is completely unaffected -- this
    # only NARROWS the org-scoped list above, for the specific user(s) an
    # admin or DevTeam has explicitly "diced down" to individual cameras.
    # DEVTEAM is never narrowed -- master control means unrestricted, not
    # "also subject to its own grants".
    if payload["role"] != "DEVTEAM":
        cursor.execute(
            "SELECT resource_id FROM permission_grants WHERE user_id = ? AND permission_key = 'view_map' AND resource_type = 'camera'",
            (payload["id"],),
        )
        scoped_ids = {r["resource_id"] for r in cursor.fetchall()}
        if scoped_ids:
            rows = [r for r in rows if r["id"] in scoped_ids]

    # Every account sees its cameras' names (Live Monitor is universal), but
    # only camera managers get the stream URL -- it holds the credentials.
    include_url = _holds_permission(cursor, payload, "manage_cameras")
    conn.close()
    return [_row_to_camera_dict(r, include_url) for r in rows]

@router.post("/api/cameras")
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
    loc = cam.location
    label = ((loc.location_label or "").strip() or None) if loc else None
    try:
        cursor.execute(
            "INSERT INTO cameras (id, name, url, status, barangay_id, lat, lng, location_label)"
            " VALUES (?, ?, ?, 'online', ?, ?, ?, ?)",
            (cam_id, cam.name, cam.url, cam.barangay_id.lower(),
             loc.lat if loc else None, loc.lng if loc else None, label),
        )
        snapshot = {"name": cam.name, "barangay_id": cam.barangay_id.lower()}
        if loc:
            snapshot.update(lat=loc.lat, lng=loc.lng, location_label=label)
        log_audit(cursor, payload, "camera.created", "camera", cam_id, snapshot=snapshot)
        conn.commit()
        await manager.broadcast({"channel": "cameras", "event": "camera_created", "id": cam_id,
                                 "barangay_id": cam.barangay_id.lower()})
        return {"status": "created", "id": cam_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

def _require_own_camera(cursor, payload: dict, cam_id: str):
    """manage_cameras plus ownership: a camera in the caller's own barangay
    and, if they've been diced down to specific cameras, one of those."""
    require_permission(cursor, payload, "manage_cameras")
    cursor.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No such camera.")
    if payload.get("role") != "DEVTEAM":
        if (row["barangay_id"] or "").lower() != (payload.get("barangay_id") or "").lower():
            raise HTTPException(status_code=403, detail="Can only change cameras for your own barangay")
        scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
        if scoped is not None and cam_id not in scoped:
            raise HTTPException(status_code=403, detail="Your camera access doesn't include this camera")
    return row

@router.put("/api/cameras/{cam_id}/location")
async def set_camera_location(cam_id: str, loc: CameraLocationSchema, authorization: Optional[str] = Header(None)):
    """Pin (or move) a smartpole on the Incident Map."""
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        row = _require_own_camera(cursor, payload, cam_id)
        label = (loc.location_label or "").strip() or None
        cursor.execute("UPDATE cameras SET lat = ?, lng = ?, location_label = ? WHERE id = ?",
                       (loc.lat, loc.lng, label, cam_id))
        log_audit(cursor, payload, "camera.located", "camera", cam_id, snapshot={
            "name": row["name"],
            "from": {"lat": row["lat"], "lng": row["lng"], "location_label": row["location_label"]},
            "to": {"lat": loc.lat, "lng": loc.lng, "location_label": label}})
        conn.commit()
        await manager.broadcast({"channel": "cameras", "event": "camera_located", "id": cam_id,
                                 "barangay_id": (row["barangay_id"] or "").lower()})
        return {"status": "located", "id": cam_id, "lat": loc.lat, "lng": loc.lng, "location_label": label}
    finally:
        conn.close()

@router.delete("/api/cameras/{cam_id}")
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
        scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
        if scoped is not None and cam_id not in scoped:
            conn.close()
            raise HTTPException(status_code=403, detail="Your camera access doesn't include this camera")
    cursor.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,))
    cam_row = cursor.fetchone()
    cursor.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
    if cam_row:
        log_audit(cursor, payload, "camera.deleted", "camera", cam_id,
                  snapshot={k: v for k, v in dict(cam_row).items() if k != "url"})
    conn.commit()
    conn.close()
    if cam_row:
        await manager.broadcast({"channel": "cameras", "event": "camera_deleted", "id": cam_id,
                                 "barangay_id": (cam_row["barangay_id"] or "").lower()})
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


@router.get("/api/ptz/capabilities")
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


@router.post("/api/ptz/move")
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


@router.post("/api/ptz/stop")
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


@router.get("/api/ptz/presets")
async def ptz_list_presets(authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    from ptz_control import get_controller, PTZNotConfigured
    try:
        return {"presets": get_controller().list_presets()}
    except PTZNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"camera error: {e}")


@router.post("/api/ptz/presets/{token}/goto")
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


@router.post("/api/ptz/presets")
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

@router.get("/api/camera_name/{camera_id}")
async def camera_name(camera_id: str, request: Request):
    """Lets main.py resolve the real, currently-registered name for the
    camera it's pointed at, instead of a name baked into config.json --
    added per request: "barangay adds a camera then adds a name and now the
    police can access that camera and show the name". If a barangay renames
    a camera in the Cameras tab, the AI core picks up the new name the next
    time it starts (it resolves this once at startup, not per-alert -- a
    live rename doesn't retroactively relabel an already-running session,
    same restart-to-apply rule as every other config change tonight).

    Loopback only (see TRUSTED_SERVICE_HOSTS), same reasoning as /api/ai_trigger: the
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
    _require_local_service(request)
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

def _scoped_camera_ids(cursor, payload: dict, permission_key: str) -> Optional[set]:
    """Cameras this user's `permission_key` is diced down to, or None when it
    isn't diced (every camera their org allows). DEVTEAM is never narrowed."""
    if payload.get("role") == "DEVTEAM":
        return None
    cursor.execute(
        "SELECT resource_id FROM permission_grants WHERE user_id = ? AND permission_key = ? AND resource_type = 'camera'",
        (payload["id"], permission_key),
    )
    ids = {r["resource_id"] for r in cursor.fetchall()}
    return ids or None


def _camera_owned_by(cursor, camera_id: str, payload: dict) -> bool:
    """DEVTEAM can touch any camera. Everyone else must own it -- the
    camera's barangay_id must match the caller's own barangay_id -- and, if
    their manage_cameras access is diced down to specific cameras, this must
    be one of them. PNP roles never reach this: manage_cameras is
    barangay-only (BARANGAY_ONLY_PERMISSIONS), enforced by
    require_permission() before this is ever called."""
    if payload.get("role") == "DEVTEAM":
        return True
    cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (camera_id,))
    row = cursor.fetchone()
    if not row:
        return False
    if (row["barangay_id"] or "").lower() != (payload.get("barangay_id") or "").lower():
        return False
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    return scoped is None or camera_id in scoped


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


@router.get("/api/cameras/{camera_id}/models")
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


@router.get("/api/cameras/models")
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
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    if scoped is not None:
        cameras = [c for c in cameras if c["id"] in scoped]
    for cam in cameras:
        cam["models"] = _camera_model_map(cursor, cam["id"])
    conn.close()
    return {"cameras": cameras}


@router.patch("/api/cameras/{camera_id}/models/{model_key}")
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


@router.get("/api/cameras/{camera_id}/thresholds")
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


@router.get("/api/cameras/thresholds")
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
    scoped = _scoped_camera_ids(cursor, payload, "manage_cameras")
    if scoped is not None:
        cameras = [c for c in cameras if c["id"] in scoped]
    for cam in cameras:
        cam["thresholds"] = _camera_threshold_map(cursor, cam["id"])
    defaults = _global_threshold_defaults(_read_config_file())
    conn.close()
    return {"cameras": cameras, "global_defaults": defaults}


@router.patch("/api/cameras/{camera_id}/thresholds/{model_key}")
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


@router.delete("/api/cameras/{camera_id}/thresholds/{model_key}")
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
