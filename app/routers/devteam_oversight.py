"""DevTeam: detection quality, the audit log and the read-only system overview."""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Header

from db import get_conn
from backend_core import (
    PROFILE_FIELDS, _user_permissions_json_batch, log_audit, manager, require_auth, require_role,
)

router = APIRouter()


# --- DEVTEAM: DETECTION QUALITY ---
@router.get("/api/devteam/detection_quality")
async def devteam_detection_quality(days: int = 30, authorization: Optional[str] = Header(None)):
    """Per camera and per alert type, over the last `days`: how many alerts
    the AI raised, how operators judged them, and the resulting precision.
    This is the live, own-camera counterpart to the 20-minute outside-camera
    measurements in config.json -- it only gets better as operators decide."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    days = max(1, min(days, 365))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT i.camera_id, c.name AS camera_name, UPPER(COALESCE(f.ai_event, i.type)) AS event,
                      COUNT(*) AS alerts,
                      SUM(CASE WHEN f.label = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
                      SUM(CASE WHEN f.label = 'dismissed' THEN 1 ELSE 0 END) AS dismissed,
                      SUM(CASE WHEN f.label IS NULL AND i.status = 'Active' THEN 1 ELSE 0 END) AS pending,
                      SUM(CASE WHEN f.label = 'confirmed' AND f.final_type IS NOT NULL AND f.final_type <> f.ai_event THEN 1 ELSE 0 END) AS retyped,
                      AVG(CASE WHEN f.label = 'confirmed' THEN i.confidence END) AS avg_conf_confirmed,
                      AVG(CASE WHEN f.label = 'dismissed' THEN i.confidence END) AS avg_conf_dismissed
               FROM incidents i
               LEFT JOIN detection_feedback f ON f.incident_id = i.id
               LEFT JOIN cameras c ON c.id = i.camera_id
               WHERE i.source = 'AI_AUTOMATION' AND i.deleted_at IS NULL AND i.occurred_date >= ?
               GROUP BY i.camera_id, c.name, UPPER(COALESCE(f.ai_event, i.type))
               ORDER BY alerts DESC""",
            (since,),
        )
        rows = []
        for r in cursor.fetchall():
            r = dict(r)
            decided = (r["confirmed"] or 0) + (r["dismissed"] or 0)
            r["precision"] = round(r["confirmed"] / decided, 3) if decided else None
            r["alerts_per_day"] = round(r["alerts"] / days, 2)
            for k in ("avg_conf_confirmed", "avg_conf_dismissed"):
                r[k] = round(r[k], 3) if r[k] is not None else None
            rows.append(r)
        cursor.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN exported_at IS NULL THEN 1 ELSE 0 END) AS fresh FROM detection_feedback")
        totals = dict(cursor.fetchone())
        return {"days": days, "rows": rows, "labelled_examples": totals["n"] or 0, "not_yet_exported": totals["fresh"] or 0}
    finally:
        conn.close()


# --- DEVTEAM: AUDIT LOG ---
# User request 2026-09-22: "monitor what each user has done... removed a
# user, removed this report like that and recover it." log_audit() (see its
# own docstring) is called from every soft-deleting endpoint above; this is
# where DevTeam reads that trail back and, for a delete-type entry whose
# target is still soft-deleted, undoes it.
def _audit_bound(value: Optional[str], label: str, end: bool) -> Optional[str]:
    """A YYYY-MM-DD filter as a created_at bound. created_at is stored UTC;
    the console's day is Manila time (UTC+8), so a day runs from 16:00 UTC
    the evening before."""
    if not value:
        return None
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must be a date (YYYY-MM-DD)")
    start = day - timedelta(hours=8)
    return (start + timedelta(days=1) if end else start).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/api/devteam/audit_log")
async def devteam_list_audit_log(
    action: Optional[str] = None, limit: int = 200, user_id: Optional[int] = None, q: Optional[str] = None,
    category: Optional[str] = None, target_type: Optional[str] = None,
    barangay_id: Optional[str] = None, station_id: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    """Filters, all optional and combined with AND:
    action -- exact action name; category -- its prefix ("user", "camera");
    user_id -- everything that account did OR that was done to it;
    barangay_id / station_id -- done by an account of that barangay or
    station, or done to that barangay or station itself;
    date_from / date_to -- inclusive days (Manila time);
    target_type; q -- free text on action, actor, target and details."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    limit = max(1, min(limit, 1000))
    start, end = _audit_bound(date_from, "From date", False), _audit_bound(date_to, "To date", True)
    if start and end and start >= end:
        raise HTTPException(status_code=400, detail="The date range ends before it starts")
    conn = get_conn()
    cursor = conn.cursor()
    where, params = [], []
    if action:
        where.append("a.action = ?"); params.append(action)
    if category:
        where.append("a.action LIKE ?"); params.append(f"{category.strip()}.%")
    if target_type:
        where.append("a.target_type = ?"); params.append(target_type)
    if user_id is not None:
        where.append("(a.actor_user_id = ? OR (a.target_type = 'user' AND a.target_id = ?))")
        params += [user_id, str(user_id)]
    if barangay_id:
        where.append("(LOWER(COALESCE(a.actor_barangay_id, u.barangay_id, '')) = LOWER(?) "
                     "OR (a.target_type = 'barangay' AND LOWER(a.target_id) = LOWER(?)))")
        params += [barangay_id, barangay_id]
    if station_id:
        where.append("(COALESCE(a.actor_station_id, u.station_id, '') = ? "
                     "OR (a.target_type = 'station' AND a.target_id = ?))")
        params += [station_id, station_id]
    if start:
        where.append("a.created_at >= ?"); params.append(start)
    if end:
        where.append("a.created_at < ?"); params.append(end)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        where.append("(LOWER(a.action) LIKE ? OR LOWER(a.actor_username) LIKE ? OR LOWER(a.target_id) LIKE ? OR LOWER(COALESCE(a.target_snapshot, '')) LIKE ?)")
        params += [like] * 4
    cursor.execute(
        "SELECT a.*, u.username AS actor_current_username, COALESCE(a.actor_barangay_id, u.barangay_id) AS actor_barangay, "
        "COALESCE(a.actor_station_id, u.station_id) AS actor_station "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id "
        f"{'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY a.created_at DESC LIMIT ?",
        (*params, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        # Stored as a JSON string (see log_audit) -- decoded here so the
        # frontend gets real nested fields, not a string to re-parse itself.
        if r.get("target_snapshot"):
            try:
                r["target_snapshot"] = json.loads(r["target_snapshot"])
            except Exception:
                pass
    return rows

@router.get("/api/devteam/audit_log/facets")
async def devteam_audit_facets(authorization: Optional[str] = Header(None)):
    """The values the audit filters can take, from what's actually logged."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY action")
        actions = [{"action": r["action"], "count": r["n"]} for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT target_type FROM audit_log ORDER BY target_type")
        target_types = [r["target_type"] for r in cursor.fetchall()]
        cursor.execute(
            # The account's current username: the logged one is whatever it
            # was called at the time, and accounts get renamed.
            "SELECT a.actor_user_id AS id, COALESCE(MAX(u.username), MAX(a.actor_username)) AS username, MAX(u.full_name) AS full_name, "
            "MAX(u.role) AS role, COUNT(*) AS n FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id "
            "WHERE a.actor_user_id IS NOT NULL GROUP BY a.actor_user_id ORDER BY username")
        actors = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, name FROM police_stations ORDER BY name")
        stations = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT MIN(created_at) AS first, MAX(created_at) AS last FROM audit_log")
        span = dict(cursor.fetchone())
    finally:
        conn.close()
    categories = sorted({a["action"].split(".")[0] for a in actions if "." in a["action"]})
    return {"actions": actions, "categories": categories, "target_types": target_types,
            "actors": actors, "barangays": barangays, "stations": stations, "span": span}

@router.post("/api/devteam/audit_log/{entry_id}/restore")
async def devteam_restore_audit_entry(entry_id: str, authorization: Optional[str] = Header(None)):
    """Undoes exactly one soft delete -- not a generic action-reverser (see
    log_audit's own design note: this project deliberately chose log +
    recoverable-delete over a generic per-action undo button). Only
    delete-type audit entries whose target is STILL soft-deleted are
    restorable; an already-restored or since-hard-changed target 400s
    with a specific reason rather than silently no-op'ing."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,))
        entry = cursor.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Audit log entry not found")
        entry = dict(entry)
        if not entry["action"].endswith(".deleted"):
            raise HTTPException(status_code=400, detail="Only a delete-type entry can be restored")

        table = {"user": "users", "incident": "incidents"}.get(entry["target_type"])
        if not table:
            raise HTTPException(status_code=400, detail=f"Restoring a '{entry['target_type']}' isn't supported yet")

        cursor.execute(f"SELECT deleted_at FROM {table} WHERE id = ?", (entry["target_id"],))
        current = cursor.fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="The original row no longer exists -- cannot restore")
        if current["deleted_at"] is None:
            raise HTTPException(status_code=400, detail="This was already restored (or never actually soft-deleted)")
        if table == "users":
            # A deleted account's username and admin seat are free for reuse,
            # so either may have been taken since. Say which, rather than
            # letting the unique index raise a bare 500.
            cursor.execute("SELECT * FROM users WHERE id = ?", (entry["target_id"],))
            u = dict(cursor.fetchone())
            cursor.execute("SELECT 1 FROM users WHERE username = ? AND id <> ? AND deleted_at IS NULL", (u["username"], u["id"]))
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail=f"Username '{u['username']}' now belongs to another account")
            seat = {"BARANGAY_ADMIN": "barangay_id", "PNP_ADMIN": "station_id"}.get(u["role"])
            if seat and (u.get("signup_status") or "approved") != "rejected":
                cursor.execute(
                    f"SELECT username FROM users WHERE {seat} = ? AND role = ? AND id <> ? AND deleted_at IS NULL "
                    "AND COALESCE(signup_status, 'approved') <> 'rejected'", (u[seat], u["role"], u["id"]))
                holder = cursor.fetchone()
                if holder:
                    raise HTTPException(status_code=409, detail=f"The {'barangay admin' if seat == 'barangay_id' else 'station admin'} seat is now held by '{holder['username']}'")

        cursor.execute(f"UPDATE {table} SET deleted_at = NULL WHERE id = ?", (entry["target_id"],))
        log_audit(cursor, payload, f"{entry['target_type']}.restored", entry["target_type"], entry["target_id"])
        conn.commit()
        await manager.broadcast({"channel": entry["target_type"] + "s", "event": entry["target_type"] + "_restored", "id": entry["target_id"]})
        return {"status": "restored", "target_type": entry["target_type"], "target_id": entry["target_id"]}
    finally:
        conn.close()

# --- DEVTEAM: FULL SYSTEM VISIBILITY (READ-ONLY OVERVIEW) ---
@router.get("/api/devteam/overview")
async def devteam_overview(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()

    # BUG FOUND 2026-09-04 (user report: a PNP Admin account exists but its
    # station shows "no PNP admin created" / "PD vacant", and the new Users
    # tab shows its organization as blank and "Never logged in" regardless
    # of reality). Root cause: this SELECT never listed station_id or
    # last_login, so every user object this endpoint returns is silently
    # missing both fields even when the database row has real values (Jae's
    # station_id is genuinely set to PNP 1's id) -- every screen fed by
    # data.users has nothing to match a PNP account to its station with, so
    # coverage always reads as vacant, and last_login always reads as never
    # logged in. Same list _row_to_user_dict_base already uses elsewhere,
    # kept in sync rather than duplicated ad hoc a second time.
    # custom_permissions added 2026-09-04 alongside the admin permission
    # override feature -- DevteamView needs it to know whether an admin row
    # is still on the automatic default or has been explicitly overridden,
    # so it can show the right control (and the right current checkboxes).
    # deleted_at IS NULL added 2026-09-22 -- same reasoning as list_my_users
    # above: a soft-deleted account belongs in the Audit Log, not sitting in
    # Directory/Users looking exactly like an active one.
    cursor.execute(
        "SELECT id, username, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, "
        "last_login, custom_permissions, custom_role_id, created_at, signup_status, verification_status, "
        f"id_document_path, face_photo_path, {', '.join(PROFILE_FIELDS)} FROM users WHERE deleted_at IS NULL")
    user_rows = cursor.fetchall()
    users = [dict(r) for r in user_rows]
    perms_by_id = _user_permissions_json_batch(cursor, [u["id"] for u in users])
    # Dicing, batched: {user_id: {permission_key: {resource_type: [ids]}}}.
    cursor.execute("SELECT user_id, permission_key, resource_type, resource_id FROM permission_grants "
                   "WHERE resource_type IN ('camera', 'crime_type', 'channel')")
    scopes_by_user: dict = {}
    for r in cursor.fetchall():
        (scopes_by_user.setdefault(r["user_id"], {}).setdefault(r["permission_key"], {})
         .setdefault(r["resource_type"], []).append(r["resource_id"]))
    for u in users:
        u["permissions"] = perms_by_id.get(u["id"], "{}")
        u["custom_permissions"] = bool(u["custom_permissions"])
        u["resource_scopes"] = scopes_by_user.get(u["id"], {})
        u["has_document"] = bool(u.pop("id_document_path"))
        u["has_face_photo"] = bool(u.pop("face_photo_path"))
        u["verification_status"] = u.get("verification_status") or "unverified"

    cursor.execute("SELECT COUNT(*) AS c FROM incidents")
    incident_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM incidents WHERE status = 'Active'")
    active_incident_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM cameras")
    camera_count = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM video_records")
    record_count = cursor.fetchone()["c"]
    cursor.execute("SELECT barangay_id, COUNT(*) AS c FROM incidents GROUP BY barangay_id")
    incidents_by_location = [dict(r) for r in cursor.fetchall()]

    # Full camera roster (not just a count) so DevteamView can group cameras
    # by location and show which Precinct Captain / Barangay Captain is
    # responsible for each -- same pairing used elsewhere: cameras and the
    # two captains at a location all share one barangay_id.
    cursor.execute("SELECT id, name, url, status, barangay_id FROM cameras ORDER BY barangay_id, name")
    cameras = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "users": users,
        "cameras": cameras,
        "totals": {
            "users": len(users),
            "incidents": incident_count,
            "active_incidents": active_incident_count,
            "cameras": camera_count,
            "video_records": record_count,
        },
        "incidents_by_location": incidents_by_location,
    }
