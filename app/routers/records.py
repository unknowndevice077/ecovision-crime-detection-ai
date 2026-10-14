"""Video records attached to incidents."""
import asyncio
import os
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from db import get_conn
from backend_core import (
    ManualClipSchema, NO_INCIDENT, RECORDINGS_DIR, _incident_owned_by, _safe_recordings_path,
    _scoped_resource_ids, apply_scope, incident_type_title, manager, require_auth,
    require_permission, require_role, sha256_of_file,
)

router = APIRouter()


class RecordNotesSchema(BaseModel):
    notes: str

def _row_to_record_dict(row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "filename": d["filename"], "file_path": d["file_path"],
        "recorded_at": d["recorded_at"], "duration": d["duration"], "type": d["type"],
        "associated_incident_id": d.get("associated_incident_id"),
        "crime_time_marker": d.get("crime_time_marker"), "notes": d.get("notes"),
    }

# --- VIDEO RECS MODULES ---
@router.get("/api/records")
async def get_video_records(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "view_records")
    # Was completely unscoped -- any authenticated user, from any barangay,
    # got every recording in the system including other barangays' footage.
    # video_records.barangay_id can be NULL for older/manual rows, so those
    # stay visible only to DEVTEAM (whose scope clause is empty).
    sql, params = apply_scope(payload, "SELECT * FROM video_records", [])
    sql += " ORDER BY recorded_at DESC"
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall()
    rows = _filter_records_by_crime_type(cursor, payload, rows)
    records = _label_records(cursor, [_row_to_record_dict(r) for r in rows])
    conn.close()
    return records


def _label_records(cursor, records: list) -> list:
    """Adds what a person reads in place of the file name: a clip of an
    incident is "<Type> <n>" -- "Assault 1", "Assault 2" -- numbered in
    recording order across ALL of that incident's clips (not just the ones
    this caller can see, so the numbers match the report). Footage with no
    incident keeps its file name."""
    inc_ids = list({r["associated_incident_id"] for r in records if r.get("associated_incident_id")})
    if not inc_ids:
        return [{**r, "label": None, "incident_type": None} for r in records]
    ph = ",".join("?" for _ in inc_ids)
    cursor.execute(f"SELECT id, type, case_id FROM incidents WHERE id IN ({ph})", tuple(inc_ids))
    incs = {r["id"]: dict(r) for r in cursor.fetchall()}
    cursor.execute(f"SELECT id, associated_incident_id FROM video_records WHERE associated_incident_id IN ({ph}) "
                   "ORDER BY recorded_at, id", tuple(inc_ids))
    position: dict = {}
    counters: dict = {}
    for r in cursor.fetchall():
        n = counters[r["associated_incident_id"]] = counters.get(r["associated_incident_id"], 0) + 1
        position[r["id"]] = n
    out = []
    for r in records:
        inc = incs.get(r.get("associated_incident_id"))
        if inc and r["id"] in position:
            out.append({**r, "label": f"{incident_type_title(inc['type'])} {position[r['id']]}",
                        "incident_type": (inc["type"] or "").upper(), "case_id": inc.get("case_id")})
        else:
            out.append({**r, "label": None, "incident_type": (inc or {}).get("type")})
    return out


def _filter_records_by_crime_type(cursor, payload: dict, rows: list) -> list:
    """Drops recordings whose incident type (or NO_INCIDENT for footage
    with none) is outside the caller's view_records dicing."""
    allowed = _scoped_resource_ids(cursor, payload, "view_records", "crime_type")
    if allowed is None or not rows:
        return rows
    inc_ids = list({r["associated_incident_id"] for r in rows if r["associated_incident_id"]})
    types: dict = {}
    if inc_ids:
        placeholders = ",".join("?" for _ in inc_ids)
        cursor.execute(f"SELECT id, type FROM incidents WHERE id IN ({placeholders})", tuple(inc_ids))
        types = {r["id"]: (r["type"] or "").strip().upper() for r in cursor.fetchall()}
    return [r for r in rows
            if (types.get(r["associated_incident_id"]) if r["associated_incident_id"] else NO_INCIDENT) in allowed]

def _ffmpeg_exe():
    """Same resolution order as maincode/main.py: bundled binary first, system
    ffmpeg only as a fallback. Never assume a system install exists -- a
    packaged deployment has no way to guarantee one."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def _parse_timecode(value: str) -> float:
    """Accepts 'SS', 'MM:SS' or 'HH:MM:SS' and returns seconds.

    The Recordings UI sends MM:SS from its scrub fields; being liberal here
    means a user typing '90' or '00:01:30' gets what they expect instead of a
    validation error on an evidence tool.
    """
    parts = str(value).strip().split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad timecode: {value!r}")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise HTTPException(status_code=400, detail=f"Bad timecode: {value!r}")


class ExtractRangeSchema(BaseModel):
    start: str = "00:00"
    end: Optional[str] = None
    notes: Optional[str] = None


@router.post("/api/records/{record_id}/extract")
async def extract_record_segment(record_id: str, data: ExtractRangeSchema,
                                 authorization: Optional[str] = Header(None)):
    """Cuts a real sub-clip out of an existing recording.

    BUG FOUND 2026-08-19: the Recordings tab's "Extract Segment" button
    previously just POSTed to /api/records/register_clip with a made-up
    filename (`EXTRACT_<timestamp>_<original>.mp4`) and NEVER CUT ANY VIDEO.
    It created a database row pointing at a file that does not exist, so the
    extracted "clip" appeared in the archive and then failed to play, forever.
    This does the actual trim.
    """
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "view_records")

    sql, params = apply_scope(payload, "SELECT * FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
    if row and not _filter_records_by_crime_type(cursor, payload, [row]):
        row = None
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")

    src = row["file_path"]
    if not os.path.exists(src):
        conn.close()
        raise HTTPException(status_code=404, detail=f"Source file is missing from disk: {os.path.basename(src)}")

    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        conn.close()
        raise HTTPException(status_code=503, detail="ffmpeg unavailable -- cannot cut a segment. `pip install imageio-ffmpeg`.")

    start_s = _parse_timecode(data.start)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", str(start_s), "-i", src]
    if data.end:
        end_s = _parse_timecode(data.end)
        if end_s <= start_s:
            conn.close()
            raise HTTPException(status_code=400, detail="End must be after start.")
        cmd += ["-t", str(end_s - start_s)]
        duration_label = f"{end_s - start_s:.1f}s"
    else:
        duration_label = "to end"
    # Re-encode rather than stream-copy: a copy can only cut on keyframes, so
    # the clip would silently start seconds away from the requested mark --
    # unacceptable when the whole point is isolating a moment of evidence.
    out_name = f"EXTRACT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(src)}"
    out_path = os.path.join(RECORDINGS_DIR, out_name)
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", out_path]

    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(out_path):
        conn.close()
        raise HTTPException(status_code=500,
                            detail=f"Extraction failed: {proc.stderr.decode('utf-8','replace')[:200]}")

    rid = str(uuid.uuid4())
    cursor.execute(
        """INSERT INTO video_records
           (id, filename, file_path, recorded_at, duration, type, associated_incident_id,
            crime_time_marker, notes, barangay_id, sha256)
           VALUES (?, ?, ?, ?, ?, 'CLIP', ?, ?, ?, ?, ?)""",
        (rid, out_name, out_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         duration_label, row["associated_incident_id"], data.start,
         data.notes or f"Segment {data.start}–{data.end or 'end'} extracted from {row['filename']}.",
         row["barangay_id"], sha256_of_file(out_path)),
    )
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "records", "event": "clip_extracted", "id": rid})
    return {"status": "extracted", "id": rid, "filename": out_name}


@router.delete("/api/records/{record_id}")
async def delete_record(record_id: str, authorization: Optional[str] = Header(None)):
    """Removes a recording and its file.

    Restricted to the police admin tier/DEVTEAM rather than anyone with
    view_records: this destroys evidence, which is a materially different
    action from watching it. BARANGAY_ADMIN used to pass too, although the
    vault is police-only (POLICE_ONLY_PERMISSIONS) and they can't open it.
    Scoped, so an admin cannot delete footage outside their jurisdiction.
    """
    payload = require_auth(authorization)
    require_role(payload, {"PNP_ADMIN", "DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()

    sql, params = apply_scope(payload, "SELECT * FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
    if row and not _filter_records_by_crime_type(cursor, payload, [row]):
        row = None
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")

    # Chain of custody (docs/incident_response_plan.md §3): a clip tied to an
    # incident that already has a filed report is evidence, not routine
    # footage -- never deletable through this normal-use endpoint, regardless
    # of role. (The maintenance retention sweep obeys the identical rule --
    # see app/maintenance.py -- so this isn't a new restriction, just this
    # endpoint catching up to the same one.)
    if row["associated_incident_id"]:
        cursor.execute(
            "SELECT 1 FROM incident_reports WHERE incident_id = ? LIMIT 1",
            (row["associated_incident_id"],),
        )
        if cursor.fetchone():
            conn.close()
            raise HTTPException(
                status_code=409,
                detail="This clip is evidence for a reported incident and cannot be deleted here.",
            )

    file_removed = False
    try:
        if row["file_path"] and os.path.exists(row["file_path"]):
            os.remove(row["file_path"])
            file_removed = True
    except OSError as e:
        # Row still goes, so the archive doesn't keep listing something the
        # user asked to be gone -- but say plainly that the file survived.
        print(f"⚠️  [RECORDS] Could not delete {row['file_path']}: {e}")

    cursor.execute("DELETE FROM video_records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "records", "event": "clip_deleted", "id": record_id})
    return {"status": "deleted", "id": record_id, "file_removed": file_removed}


@router.post("/api/records/register_clip")
async def register_clip(data: ManualClipSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # Adding to the evidence vault used to need only a login.
    try:
        require_permission(cursor, payload, "view_records")
        if data.associated_incident_id and not _incident_owned_by(cursor, data.associated_incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    except HTTPException:
        conn.close()
        raise
    rid = str(uuid.uuid4())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fpath = _safe_recordings_path(data.filename)
    # BUG FOUND 2026-08-19: same missing barangay_id as /api/ai_register_clip
    # below -- see that endpoint's comment. An operator manually extracting a
    # segment is scoped to their own barangay; a PNP account's token carries
    # no barangay_id at all (they use station_id instead), so this falls
    # back to the linked incident's barangay_id in that case, same as the
    # AI-triggered path.
    clip_barangay_id = payload.get("barangay_id")
    if not clip_barangay_id and data.associated_incident_id:
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
        return {"status": "registered", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Metadata write collision: {e}")
    finally:
        conn.close()

@router.patch("/api/records/{record_id}/notes")
async def update_record_notes(record_id: str, data: RecordNotesSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # BUG FOUND 2026-09-03: this checked only that SOME user was logged in --
    # no role, no permission, and (same shape as the incident-endpoint bugs
    # fixed above and the camera CRUD bug fixed earlier today) no ownership
    # check at all. Any authenticated account, of any role in any barangay or
    # station, could rewrite the evidence notes on any recording anywhere in
    # the system. Scoped the same way GET /api/records and the extract
    # endpoint already are, via apply_scope(). The scope alone still let any
    # account in the area (barangay ones included, who can't open the
    # police-only vault) edit notes; 2026-10-01 it takes view_records too.
    try:
        require_permission(cursor, payload, "view_records")
    except HTTPException:
        conn.close()
        raise
    sql, params = apply_scope(payload, "SELECT id, associated_incident_id FROM video_records", [],
                              extra_where="id = ?", extra_params=[record_id])
    cursor.execute(sql, tuple(params))
    found = cursor.fetchone()
    if not found or not _filter_records_by_crime_type(cursor, payload, [found]):
        conn.close()
        raise HTTPException(status_code=404, detail="Recording not found (or outside your jurisdiction)")
    cursor.execute("UPDATE video_records SET notes = ? WHERE id = ?", (data.notes, record_id))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"status": "updated", "id": record_id}
