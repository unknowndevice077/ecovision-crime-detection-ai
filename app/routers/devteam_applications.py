"""DevTeam: self-signup applications awaiting review."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Header

from db import get_conn
from backend_core import require_auth, require_role

router = APIRouter()


# 2026-09-23 bug fix (see login()'s comment for the full report): a
# self-signup PNP_ADMIN had no equivalent of the barangay flow above at
# all -- the station already existing was treated as sufficient vetting
# for whoever showed up claiming to run it. Barangays have a `barangays`
# row to hang a pending/approved/rejected status on; a station is
# DevTeam-created and already permanently 'approved' by definition, so
# there's no location-shaped object to reuse here -- the gate has to live
# directly on signup_status instead. Generic by user id rather than
# barangay_id-shaped like the endpoints above, so this covers any
# self-signup admin account, PNP included.
def _list_signups(cursor, status: str) -> list:
    """PNP applications, plus barangay applicants whose barangay is NOT
    itself in this same state -- a barangay applicant riding on a pending
    or rejected barangay is listed under that barangay instead."""
    cursor.execute("""
        SELECT u.id, u.username, u.role, u.assignment, u.station_id, u.barangay_id, u.created_at,
               u.verification_status, u.id_document_path, u.face_photo_path,
               COALESCE(s.name, 'Barangay ' || b.name) AS station_name, b.status AS barangay_status,
               u.full_name, u.birthdate, u.home_address, u.contact_number, u.position,
               u.signup_status, u.signup_decided_at AS decided_at, u.signup_decision_reason AS decision_reason,
               d.username AS decided_by_username
        FROM users u
        LEFT JOIN police_stations s ON s.id = u.station_id
        LEFT JOIN barangays b ON b.id = u.barangay_id
        LEFT JOIN users d ON d.id = u.signup_decided_by
        WHERE u.signup_status = ? AND u.deleted_at IS NULL
          AND (u.role = 'PNP_ADMIN'
               OR (u.role = 'BARANGAY_ADMIN' AND COALESCE(b.status, 'approved') <> ?))
        ORDER BY COALESCE(u.signup_decided_at, u.created_at) DESC
    """, (status, status))
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        r["has_document"] = bool(r.pop("id_document_path"))
        r["has_face_photo"] = bool(r.pop("face_photo_path"))
    return rows


@router.get("/api/devteam/signups")
async def list_signups(authorization: Optional[str] = Header(None), status: str = "pending"):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if status not in ("pending", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending or rejected")
    conn = get_conn()
    try:
        return _list_signups(conn.cursor(), status)
    finally:
        conn.close()


@router.get("/api/devteam/pending_signups")
async def list_pending_signups(authorization: Optional[str] = Header(None)):
    """Kept for older frontends; same as /api/devteam/signups?status=pending."""
    return await list_signups(authorization, "pending")
