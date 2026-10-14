"""DevTeam: police stations and the barangay locations they cover."""
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from db import get_conn
from backend_core import (
    ApplicationReopenSchema, LocationDecisionSchema, _admin_seat_holder, _decision_reason,
    _require_registration_authority, _require_state, _set_signup_decision, _slugify_barangay,
    _usable_barangay, log_audit, manager, require_auth, require_role,
)

router = APIRouter()


# --- DEVTEAM: POLICE STATIONS & JURISDICTIONS ---
# A station is an organizational unit that COVERS barangays. It owns no
# cameras, incidents or recordings -- station_barangays is purely a
# visibility lens (see docs/USER_HIERARCHY_PLAN.md). Editing a jurisdiction
# therefore changes only who can see what; it never moves an asset.

STATION_DETAIL_FIELDS = ("station_type", "parent_office", "regional_office", "commander",
                         "address", "contact_number", "description")
BARANGAY_DETAIL_FIELDS = ("psgc_code", "city_municipality", "province", "region", "captain_name",
                          "hall_address", "contact_number", "description")


class StationSchema(BaseModel):
    id: Optional[str] = None
    name: str
    reason: Optional[str] = None
    confirm_password: Optional[str] = None
    station_type: Optional[str] = None
    parent_office: Optional[str] = None
    regional_office: Optional[str] = None
    commander: Optional[str] = None
    address: Optional[str] = None
    contact_number: Optional[str] = None
    description: Optional[str] = None

class StationJurisdictionSchema(BaseModel):
    barangay_ids: List[str]

class StationBarangayCreate(BaseModel):
    name: str
    reason: Optional[str] = None
    confirm_password: Optional[str] = None
    barangay_id: Optional[str] = None
    psgc_code: Optional[str] = None
    city_municipality: Optional[str] = None
    province: Optional[str] = None
    region: Optional[str] = None
    captain_name: Optional[str] = None
    hall_address: Optional[str] = None
    contact_number: Optional[str] = None
    description: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


def _clean_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


@router.get("/api/devteam/stations")
async def list_stations(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(f"SELECT id, name, {', '.join(STATION_DETAIL_FIELDS)} FROM police_stations ORDER BY name")
    stations = [dict(r) for r in cursor.fetchall()]

    # Batched, not one query per station.
    cursor.execute("SELECT station_id, barangay_id FROM station_barangays")
    juris: dict = {}
    for r in cursor.fetchall():
        juris.setdefault(r["station_id"], []).append(r["barangay_id"])

    cursor.execute(
        "SELECT station_id, COUNT(*) AS n FROM users WHERE station_id IS NOT NULL GROUP BY station_id")
    staff = {r["station_id"]: r["n"] for r in cursor.fetchall()}

    conn.close()
    for s in stations:
        s["barangay_ids"] = sorted(juris.get(s["id"], []))
        s["staff_count"] = staff.get(s["id"], 0)
    return stations


@router.post("/api/devteam/stations")
async def create_station(data: StationSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    sid = (data.id or f"station-{uuid.uuid4().hex[:8]}").strip().lower()
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if not _clean_optional(data.station_type):
            raise HTTPException(status_code=400, detail="Unit type is required")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        cursor.execute("SELECT 1 FROM police_stations WHERE LOWER(name) = LOWER(?)", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A station named "{name}" already exists')
        details = [_clean_optional(getattr(data, f)) for f in STATION_DETAIL_FIELDS]
        cursor.execute(
            f"INSERT INTO police_stations (id, name, {', '.join(STATION_DETAIL_FIELDS)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in STATION_DETAIL_FIELDS)})",
            (sid, name, *details),
        )
        log_audit(cursor, payload, "station.created", "station", sid,
                  snapshot={"name": name, "reason": reason, **dict(zip(STATION_DETAIL_FIELDS, details))})
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not create station: {e}")
    finally:
        conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_created", "id": sid})
    return {"status": "created", "id": sid, "name": name}


@router.patch("/api/devteam/stations/{station_id}")
async def update_station(station_id: str, data: StationSchema, authorization: Optional[str] = Header(None)):
    """Edits a station's name and record. Same reason + password bar as
    registering one, and the audit entry keeps a before/after of every
    field that changed."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT id, name, {', '.join(STATION_DETAIL_FIELDS)} FROM police_stations WHERE id = ?", (station_id,))
        before = cursor.fetchone()
        if not before:
            raise HTTPException(status_code=404, detail="Station not found")
        before = dict(before)
        if not _clean_optional(data.station_type):
            raise HTTPException(status_code=400, detail="Unit type is required")
        cursor.execute("SELECT 1 FROM police_stations WHERE LOWER(name) = LOWER(?) AND id <> ?", (name, station_id))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A station named "{name}" already exists')
        after = {"name": name, **{f: _clean_optional(getattr(data, f)) for f in STATION_DETAIL_FIELDS}}
        changes = {k: {"from": before.get(k), "to": v} for k, v in after.items() if (before.get(k) or None) != v}
        if not changes:
            return {"status": "unchanged", "id": station_id}
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        cursor.execute(
            f"UPDATE police_stations SET {', '.join(f'{k} = ?' for k in after)} WHERE id = ?",
            (*after.values(), station_id),
        )
        log_audit(cursor, payload, "station.updated", "station", station_id,
                  snapshot={"name": name, "reason": reason, "changes": changes})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_updated", "id": station_id})
    return {"status": "updated", "id": station_id, "changed": sorted(changes)}


@router.put("/api/devteam/stations/{station_id}/jurisdiction")
async def set_station_jurisdiction(station_id: str, data: StationJurisdictionSchema,
                                   authorization: Optional[str] = Header(None)):
    """Replaces a station's jurisdiction wholesale. Idempotent, and safe to
    shrink: removing a barangay only removes visibility, it never deletes
    that barangay's cameras/incidents, because nothing hangs off the
    station."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Station not found")

    wanted = [b.strip().lower() for b in data.barangay_ids if b and b.strip()]
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        cursor.execute(f"SELECT id, name, status FROM barangays WHERE LOWER(id) IN ({placeholders})", tuple(wanted))
        known = {r["id"].lower(): dict(r) for r in cursor.fetchall()}
        unknown = [b for b in wanted if b not in known]
        if unknown:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown barangay ids: {', '.join(unknown)}")
        # Only an approved barangay can be covered. A pending one linked
        # before this rule existed may stay until it's decided; a rejected
        # one never (rejecting unlinks it everywhere).
        cursor.execute("SELECT barangay_id FROM station_barangays WHERE station_id = ?", (station_id,))
        linked = {r["barangay_id"].lower() for r in cursor.fetchall()}
        refused = [f"{known[b]['name']} ({known[b]['status']})" for b in wanted
                   if known[b]["status"] == "rejected" or (known[b]["status"] == "pending" and b not in linked)]
        if refused:
            conn.close()
            raise HTTPException(status_code=409, detail=f"Only approved barangays can be in a jurisdiction: {', '.join(refused)}")

    try:
        cursor.execute("SELECT barangay_id FROM station_barangays WHERE station_id = ?", (station_id,))
        juris_before = sorted(r["barangay_id"] for r in cursor.fetchall())
        cursor.execute("DELETE FROM station_barangays WHERE station_id = ?", (station_id,))
        log_audit(cursor, payload, "station.jurisdiction_updated", "station", station_id,
                  snapshot={"from": juris_before, "to": sorted(wanted)})
        for b in wanted:
            cursor.execute(
                "INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, b))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not update jurisdiction: {e}")
    finally:
        conn.close()

    await manager.broadcast({"channel": "stations", "event": "jurisdiction_updated", "id": station_id})
    return {"status": "updated", "id": station_id, "barangay_ids": wanted}


# 2026-09-29 user request: registering a NEW barangay used to happen inside
# Create User (typing a fresh barangay_id there silently created it) -- a
# barangay made that way had no station connection at all until DevTeam
# separately remembered to visit this tab and check it into a jurisdiction
# (see devteam_create_user's own 2026-09-24 fix for the bug that caused).
# Moved here instead: a station's own tab is where a NEW barangay is
# registered now, and it's assigned to THIS station's jurisdiction in the
# same step -- there's no longer a path that creates a barangay with no
# station at all. devteam_create_user's barangay branch now REJECTS an
# unknown barangay_id rather than silently making one.
@router.post("/api/devteam/stations/{station_id}/barangays")
async def create_barangay_for_station(station_id: str, body: StationBarangayCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Station not found")

        import re
        # Stored without a "Brgy."/"Barangay" prefix -- display sites add it.
        name = re.sub(r"^(brgy\.?|barangay)\s+", "", body.name.strip(), flags=re.IGNORECASE)
        if not name:
            raise HTTPException(status_code=400, detail="Barangay name is required")
        barangay_id = _slugify_barangay(body.barangay_id or name)
        if not barangay_id:
            raise HTTPException(status_code=400, detail="Barangay name must contain letters or digits")

        psgc = _clean_optional(body.psgc_code)
        if psgc is not None:
            psgc = psgc.replace(" ", "")
            if not psgc.isdigit() or len(psgc) not in (9, 10):
                raise HTTPException(status_code=400, detail="PSGC code must be 10 digits (or the older 9-digit form)")
        details = {f: _clean_optional(getattr(body, f)) for f in BARANGAY_DETAIL_FIELDS}
        details["psgc_code"] = psgc
        if not details["city_municipality"]:
            raise HTTPException(status_code=400, detail="City / municipality is required")
        reason = _require_registration_authority(cursor, payload, body.reason, body.confirm_password)
        if psgc:
            cursor.execute("SELECT name FROM barangays WHERE psgc_code = ? AND id <> ?", (psgc, barangay_id))
            clash = cursor.fetchone()
            if clash:
                raise HTTPException(status_code=409, detail=f'PSGC code {psgc} is already registered to Barangay {clash["name"]}')

        cursor.execute("SELECT status FROM barangays WHERE id = ?", (barangay_id,))
        existing = cursor.fetchone()
        if existing:
            if existing["status"] == "approved":
                raise HTTPException(
                    status_code=409,
                    detail=f'Barangay "{name}" is already registered -- tick it in this station\'s jurisdiction list instead.')
            # Registering over an application used to approve it silently,
            # reversing a rejection with no reason on record. The decision
            # belongs in Approvals.
            _usable_barangay(cursor, barangay_id, "registering it")
        else:
            cursor.execute(
                f"INSERT INTO barangays (id, name, lat, lng, {', '.join(BARANGAY_DETAIL_FIELDS)}, status, approved_by, approved_at) "
                f"VALUES (?, ?, ?, ?, {', '.join('?' for _ in BARANGAY_DETAIL_FIELDS)}, 'approved', ?, NOW())",
                (barangay_id, name, body.lat, body.lng, *[details[f] for f in BARANGAY_DETAIL_FIELDS], payload["id"]),
            )

        cursor.execute("SELECT 1 FROM station_barangays WHERE station_id = ? AND barangay_id = ?", (station_id, barangay_id))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, barangay_id))
        log_audit(cursor, payload, "barangay.created", "barangay", barangay_id,
                  snapshot={"name": name, "station_id": station_id, "reason": reason, **details})
        conn.commit()
        await manager.broadcast({"channel": "stations", "event": "jurisdiction_updated", "id": station_id})
        return {"status": "created", "barangay_id": barangay_id}
    finally:
        conn.close()


@router.delete("/api/devteam/stations/{station_id}")
async def delete_station(station_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    # users.station_id is ON DELETE RESTRICT, and chk_user_scope means a PNP
    # user cannot exist without a station -- so refuse with a clear message
    # rather than letting the FK raise something opaque.
    cursor.execute("SELECT username, deleted_at FROM users WHERE station_id = ?", (station_id,))
    holders = cursor.fetchall()
    if holders:
        conn.close()
        active = [h["username"] for h in holders if not h["deleted_at"]]
        removed = [h["username"] for h in holders if h["deleted_at"]]
        # Removed accounts count too: they can still be restored from the
        # Audit Log, into this station. Saying only "N users still assigned"
        # sent DevTeam looking for accounts no list showed.
        parts = []
        if active:
            parts.append(f"{len(active)} account(s) still assigned ({', '.join(active[:5])}) -- reassign them first")
        if removed:
            parts.append(f"{len(removed)} removed account(s) ({', '.join(removed[:5])}) can still be restored into it from the Audit Log")
        raise HTTPException(status_code=409, detail="This station can't be deleted: " + "; ".join(parts) + ".")
    cursor.execute("SELECT * FROM police_stations WHERE id = ?", (station_id,))
    st_row = cursor.fetchone()
    cursor.execute("DELETE FROM police_stations WHERE id = ?", (station_id,))
    if st_row:
        log_audit(cursor, payload, "station.removed", "station", station_id, snapshot=dict(st_row))
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "stations", "event": "station_deleted", "id": station_id})
    return {"status": "deleted", "id": station_id}


def _barangay_application(cursor, barangay_id: str) -> dict:
    cursor.execute(
        "SELECT b.*, u.id AS applicant_id, u.signup_status AS applicant_status, u.role AS applicant_role, "
        "u.deleted_at AS applicant_deleted_at, u.username AS applicant_username "
        "FROM barangays b LEFT JOIN users u ON u.id = b.requested_by WHERE b.id = ?", (barangay_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return dict(row)


@router.get("/api/devteam/locations")
async def list_locations(authorization: Optional[str] = Header(None), status: Optional[str] = None):
    """Includes the requesting captain's username/role/assignment so DevTeam
    has enough to actually verify the person before approving -- a bare
    location name + status was not enough to tell who's asking. Decided
    rows also carry who decided, when, and why."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if status and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending, approved or rejected")
    conn = get_conn()
    cursor = conn.cursor()
    query = """
        SELECT b.*, u.id AS requester_id, u.username AS requester_username, u.role AS requester_role,
               u.assignment AS requester_assignment, u.verification_status AS requester_verification_status,
               u.id_document_path AS requester_has_document, u.face_photo_path AS requester_has_face_photo,
               u.full_name AS requester_full_name, u.birthdate AS requester_birthdate,
               u.home_address AS requester_home_address, u.contact_number AS requester_contact_number,
               u.position AS requester_position, u.created_at AS requester_created_at,
               u.signup_status AS requester_signup_status, u.deleted_at AS requester_deleted_at,
               d.username AS decided_by_username, b.approved_at AS decided_at
        FROM barangays b
        LEFT JOIN users u ON u.id = b.requested_by
        LEFT JOIN users d ON d.id = b.approved_by
    """
    if status:
        cursor.execute(query + " WHERE b.status = ? ORDER BY COALESCE(b.approved_at, b.created_at) DESC", (status,))
    else:
        cursor.execute(query + " ORDER BY b.created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        # Collapse the raw filename to a boolean -- DevTeam still reaches
        # the actual file through get_verification_document, this list is
        # just "has one been submitted", not a place to leak the path.
        r["requester_has_document"] = bool(r.get("requester_has_document"))
        r["requester_has_face_photo"] = bool(r.get("requester_has_face_photo"))
        r["requester_deleted"] = bool(r.pop("requester_deleted_at", None))
    conn.close()
    return rows


@router.post("/api/devteam/locations/{barangay_id}/approve")
async def approve_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    """Approves the barangay and its applicant together. station_id, when
    given, puts the barangay in that station's jurisdiction in the same
    step -- a barangay no station covers is invisible to every PNP account."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=False)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "pending")
        station_id = (data.station_id or "").strip().lower() or None
        if station_id:
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"Unknown station '{station_id}'")
        cursor.execute(
            "UPDATE barangays SET status = 'approved', approved_by = ?, approved_at = NOW(), decision_reason = ? WHERE id = ?",
            (payload["id"], reason, barangay_id))
        if app_row["applicant_id"] and app_row["applicant_status"] == "pending" and not app_row["applicant_deleted_at"]:
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "approved", reason)
        if station_id:
            cursor.execute("SELECT 1 FROM station_barangays WHERE station_id = ? AND barangay_id = ?", (station_id, barangay_id))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (station_id, barangay_id))
        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        stations = [r["station_id"] for r in cursor.fetchall()]
        log_audit(cursor, payload, "barangay.approved", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "applicant": app_row["applicant_username"],
                            "reason": reason, "station_id": station_id})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_approved", "barangay_id": barangay_id})
    return {"status": "approved", "barangay_id": barangay_id, "covered_by": stations}


@router.post("/api/devteam/locations/{barangay_id}/reject")
async def reject_location(barangay_id: str, data: LocationDecisionSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=True)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "pending")
        cursor.execute(
            "UPDATE barangays SET status = 'rejected', approved_by = ?, approved_at = NOW(), decision_reason = ? WHERE id = ?",
            (payload["id"], reason, barangay_id))
        if app_row["applicant_id"] and app_row["applicant_status"] == "pending" and not app_row["applicant_deleted_at"]:
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "rejected", reason)
        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        dropped = sorted(r["station_id"] for r in cursor.fetchall())
        cursor.execute("DELETE FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
        log_audit(cursor, payload, "barangay.rejected", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "applicant": app_row["applicant_username"],
                            "reason": reason, "removed_from_stations": dropped})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_rejected", "barangay_id": barangay_id})
    return {"status": "rejected", "barangay_id": barangay_id, "removed_from_stations": dropped}


@router.post("/api/devteam/locations/{barangay_id}/reopen")
async def reopen_location(barangay_id: str, data: ApplicationReopenSchema, authorization: Optional[str] = Header(None)):
    """Rejected -> pending, so the application can be decided again from the
    normal queue. The previous decision stays in the audit entry."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        app_row = _barangay_application(cursor, barangay_id)
        _require_state(app_row["status"], "rejected")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        applicant_back = False
        if app_row["applicant_id"] and app_row["applicant_status"] == "rejected" and not app_row["applicant_deleted_at"]:
            holder = _admin_seat_holder(cursor, app_row["applicant_role"], barangay_id, None, app_row["applicant_id"])
            if holder:
                raise HTTPException(status_code=409, detail=f"'{holder}' now holds this barangay's admin seat, so the old applicant can't be reinstated.")
            _set_signup_decision(cursor, payload, app_row["applicant_id"], "pending", None)
            applicant_back = True
        cursor.execute(
            "UPDATE barangays SET status = 'pending', approved_by = NULL, approved_at = NULL, decision_reason = NULL WHERE id = ?",
            (barangay_id,))
        cursor.execute("SELECT username FROM users WHERE id = ?", (app_row["approved_by"],))
        decider = cursor.fetchone()
        log_audit(cursor, payload, "barangay.reopened", "barangay", barangay_id,
                  snapshot={"name": app_row["name"], "reason": reason, "applicant": app_row["applicant_username"],
                            "applicant_reinstated": applicant_back,
                            "previous_decision": {"status": "rejected", "by": decider["username"] if decider else None,
                                                  "at": app_row["approved_at"], "reason": app_row["decision_reason"]}})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "location_reopened", "barangay_id": barangay_id})
    return {"status": "pending", "barangay_id": barangay_id, "applicant_reinstated": applicant_back}
