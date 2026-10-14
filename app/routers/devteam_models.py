"""DevTeam: detection-model toggles and TensorRT weight optimization."""
import asyncio
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Header

from config_store import save_overrides
import backend_core
from backend_core import (
    BASE_DIR, DETECTION_CLASSES, WRITABLE_CONFIG_PATH, _BASE_CONFIG_PATH, _optimize_lock,
    _optimize_state, _read_config_file, _run_optimize_weights, require_auth, require_role,
)

router = APIRouter()


# Viewing + optimizing AI models is barangay-only (not PNP_ADMIN, despite
# both being in ADMIN_ROLES): the models run on hardware the barangay owns
# and installed, same reasoning as manage_cameras being barangay-only.
# STALE as of the 2026-08-23 fix in set_detection_model, corrected here
# 2026-09-02: this comment used to say toggling a model on/off was
# deliberately DEVTEAM-only, reserving MODEL_VIEW_ROLES for read-only
# viewing + optimizing. That was reversed on 2026-08-23 -- see that
# function's own comment -- because DEVTEAM-only toggling left the barangay
# that owns the hardware unable to turn a detector on or off for their own
# camera. Toggling enabled/disabled now uses this SAME MODEL_VIEW_ROLES set;
# only the numeric threshold stays DEVTEAM-only (checked separately in
# set_detection_model). Left uncorrected, this paragraph actively misled a
# later full-system permission sweep into flagging BARANGAY_ADMIN's (correct)
# ability to toggle a model as a bug.
MODEL_VIEW_ROLES = {"DEVTEAM", "BARANGAY_ADMIN"}


@router.get("/api/devteam/detection-models")
async def list_detection_models(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    try:
        cfg = _read_config_file()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read config.json: {e}")

    det = cfg.get("detection", {})
    out = []
    for name in DETECTION_CLASSES:
        block = det.get(name)
        if not isinstance(block, dict):
            continue
        # violence keys its live values under scene_* because it runs in scene
        # mode; robbery and vandalism use the plain names.
        threshold = block.get("scene_confidence_threshold",
                              block.get("confidence_threshold"))
        consecutive = block.get("scene_consecutive_required",
                                block.get("consecutive_required"))
        model_path = block.get("scene_model_path", block.get("model_path"))
        weights_ok = bool(model_path) and os.path.exists(
            os.path.join(BASE_DIR, model_path))

        out.append({
            "name": name,
            "display_name": block.get("display_name", name.title()),
            # violence has no explicit flag historically -- absent means on.
            "enabled": bool(block.get("enabled", True)),
            "experimental": bool(block.get("experimental", False)),
            "threshold": threshold,
            "consecutive_required": consecutive,
            "model_path": model_path,
            "weights_present": weights_ok,
            "metrics": block.get("metrics"),
            # The long "_why_*" prose keys, surfaced so the reasoning travels
            # with the switch rather than living only in the file.
            "notes": {k: v for k, v in block.items()
                      if k.startswith("_") and isinstance(v, str)},
        })
    return {"models": out, "requires_restart": True}


@router.patch("/api/devteam/detection-models/{name}")
async def set_detection_model(
    name: str,
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
):
    payload = require_auth(authorization)
    # BUG FOUND 2026-08-23: this was DEVTEAM-only, so the barangay that owns
    # the camera and hardware this actually runs on had no way to turn a
    # detector on or off -- every "should this camera see less" call sat
    # with DevTeam regardless of whose site it affected. Widened to
    # MODEL_VIEW_ROLES (same set that can already see the panel), matching
    # manage_cameras' existing barangay-owns-its-hardware precedent. The
    # threshold value stays DEVTEAM-only -- see the check below -- since
    # that number is what the model's reported accuracy was measured at,
    # not something to hand-tune per site.
    require_role(payload, MODEL_VIEW_ROLES)

    if name not in DETECTION_CLASSES:
        raise HTTPException(status_code=404, detail=f"Unknown detection class: {name}")

    if "threshold" in body and payload["role"] != "DEVTEAM":
        raise HTTPException(status_code=403, detail="Only DevTeam can change a detection threshold")
    if "threshold" in body and name == "verifier":
        # Its cut-offs are per task (detection.verifier.tasks) and were fitted
        # on measured data; a single 0-1 slider has no meaning for it.
        raise HTTPException(status_code=400, detail="The alarm double-check has no single threshold")

    try:
        cfg = _read_config_file()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read config.json: {e}")

    block = cfg.get("detection", {}).get(name)
    if not isinstance(block, dict):
        raise HTTPException(status_code=404, detail=f"No config block for {name}")

    changed = {}

    if "enabled" in body:
        want = bool(body["enabled"])
        # Refuse to enable a class whose weights are not on disk. Letting this
        # through would produce a detector that crashes the AI core at startup,
        # and the user would see the dashboard fail to come up with no
        # connection to the switch they just flipped.
        if want:
            model_path = block.get("scene_model_path", block.get("model_path"))
            if not model_path or not os.path.exists(os.path.join(BASE_DIR, model_path)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot enable {name}: weights not found at {model_path!r}.")
        block["enabled"] = want
        changed["enabled"] = want

    if "threshold" in body:
        try:
            t = float(body["threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="threshold must be a number")
        if not 0.0 < t < 1.0:
            raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
        key = "scene_confidence_threshold" if "scene_confidence_threshold" in block \
            else "confidence_threshold"
        block[key] = t
        changed[key] = t

    if not changed:
        raise HTTPException(status_code=400, detail="Nothing to change")

    # To the WRITABLE copy (merged last, so it wins) -- the shipped
    # config.json can be read-only on a packaged install (BUG FOUND
    # 2026-08-23). Only the changed keys: this used to dump the whole merged
    # config there, which then pinned every other model path and threshold
    # on the machine past any later update (config_store.py, 2026-10-04).
    # Atomic, since a torn config.json takes down the backend AND detector.
    try:
        save_overrides(WRITABLE_CONFIG_PATH,
                       {("detection", name, key): value for key, value in changed.items()},
                       _BASE_CONFIG_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save config.json: {e}")

    return {
        "ok": True,
        "name": name,
        "changed": changed,
        "requires_restart": True,
        "message": "Saved. Restart detection for this to take effect.",
    }


@router.post("/api/devteam/optimize_weights")
async def start_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if _optimize_state["running"]:
            raise HTTPException(status_code=409, detail="An optimize run is already in progress")
        _optimize_state.update(running=True, steps=[], summary=None, preconditions=None,
                                returncode=None, error=None, cancelled=False,
                                cancel_requested=False,
                                started_at=datetime.utcnow().isoformat(), finished_at=None)
        asyncio.create_task(_run_optimize_weights(revert=False))

    return {"status": "started"}


@router.post("/api/devteam/optimize_weights/revert")
async def revert_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if _optimize_state["running"]:
            raise HTTPException(status_code=409, detail="An optimize run is already in progress")
        _optimize_state.update(running=True, steps=[], summary=None, preconditions=None,
                                returncode=None, error=None, cancelled=False,
                                cancel_requested=False,
                                started_at=datetime.utcnow().isoformat(), finished_at=None)
        asyncio.create_task(_run_optimize_weights(revert=True))

    return {"status": "started"}


@router.post("/api/devteam/optimize_weights/cancel")
async def cancel_optimize_weights(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)

    async with _optimize_lock:
        if not _optimize_state["running"] or backend_core._optimize_proc is None:
            raise HTTPException(status_code=409, detail="No optimize run is in progress")
        proc = backend_core._optimize_proc
        _optimize_state["cancel_requested"] = True

    # terminate() outside the lock -- _run_optimize_weights holds nothing
    # while awaiting proc output, so this doesn't need the lock, and killing
    # a subprocess is exactly the kind of call that shouldn't be made while
    # holding one. On Windows, Process.terminate() calls TerminateProcess --
    # there is no graceful SIGTERM-equivalent stop to ask a console app for
    # there, so this IS the hard stop, not a polite request that a wait/kill
    # escalation follows. The wait below just confirms it actually exited
    # before responding, so the dashboard's "cancelling..." doesn't linger
    # past the point where the process is really gone.
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass

    return {"status": "cancelling"}


@router.get("/api/devteam/optimize_weights/status")
async def get_optimize_weights_status(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, MODEL_VIEW_ROLES)
    return _optimize_state
