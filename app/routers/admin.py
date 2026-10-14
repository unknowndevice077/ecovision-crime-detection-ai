"""Admin-scoped user management, permissions, custom roles and notify targets."""
import os
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Header, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import IntegrityError, get_conn
from backend_core import (
    ADMIN_ROLES, PNP_SIDE_ROLES, PROFILE_FIELDS, ResourceGrantRequest, ResourceScopesUpdate,
    VALID_PERMISSION_KEYS, VERIFICATION_DOCS_DIR, VerificationReview, _apply_resource_scopes,
    _check_own_subordinate, _check_permission_key_allowed, _clean_profile, _resource_grant_target,
    _resource_permissions_handler, _review_verification, _row_to_user_dict_base,
    _save_verification_document, _scoped_resource_ids, _user_permissions_json_batch, hash_password,
    limiter, log_audit, manager, require_auth, require_permission, require_role,
)

router = APIRouter()


ADMIN_CREATES_ROLE = {"PNP_ADMIN": "PNP_OFFICER", "BARANGAY_ADMIN": "BARANGAY_STAFF"}
ADMIN_OR_DEVTEAM = ADMIN_ROLES | {"DEVTEAM"}

class AdminCreateUser(BaseModel):
    username: str
    password: str
    assignment: str
    display_title: Optional[str] = None
    is_sub_admin: Optional[bool] = False
    permissions: Optional[dict] = None
    # Personal record, same fields as DevTeam's Create User (full_name
    # required there and here -- staff made by an admin used to have none).
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None

class PermissionsUpdate(BaseModel):
    permissions: dict

class NotifyTargetSchema(BaseModel):
    # Exactly one of these two should be set -- mirrors CameraSchema's own
    # single-owner assumption, just with two possible owner kinds instead of
    # one. See docs/incident_response_plan.md §2.
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    channel: str          # "telegram" | "sms"
    destination: str      # Telegram chat_id, or a phone number
    label: Optional[str] = None

def _rows_to_user_dicts_batch(cursor, rows) -> list:
    """Batched equivalent of [_row_to_user_dict(cursor, r) for r in rows] --
    one permissions query total instead of one per row, same idea extended
    to location_name (one query per distinct barangay/station instead of
    one per user)."""
    users = [_row_to_user_dict_base(r) for r in rows]
    perms_by_id = _user_permissions_json_batch(cursor, [u["id"] for u in users])
    barangay_ids = {u["barangay_id"] for u in users if u["barangay_id"]}
    station_ids = {u["station_id"] for u in users if u["station_id"]}
    names_by_barangay = {}
    if barangay_ids:
        placeholders = ",".join("?" for _ in barangay_ids)
        cursor.execute(f"SELECT id, name FROM barangays WHERE id IN ({placeholders})", tuple(barangay_ids))
        names_by_barangay = {r["id"]: r["name"] for r in cursor.fetchall()}
    names_by_station = {}
    if station_ids:
        placeholders = ",".join("?" for _ in station_ids)
        cursor.execute(f"SELECT id, name FROM police_stations WHERE id IN ({placeholders})", tuple(station_ids))
        names_by_station = {r["id"]: r["name"] for r in cursor.fetchall()}
    for u in users:
        u["permissions"] = perms_by_id.get(u["id"], "{}")
        u["location_name"] = (
            names_by_station.get(u["station_id"]) if u["station_id"]
            else names_by_barangay.get(u["barangay_id"])
        )
    return users


# --- RESPONDER NOTIFICATIONS (Telegram/SMS on confirm-and-report) ---
# docs/incident_response_plan.md §2. Scoped like cameras (barangay-owned) or
# like a PNP admin's own station -- DEVTEAM sees everything, matching the
# ADMIN_ROLES-bypass pattern used by require_permission()/apply_scope()
# elsewhere in this file.
@router.get("/api/notify_targets")
async def list_notify_targets(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")
    role = payload.get("role")
    if role == "DEVTEAM":
        cursor.execute("SELECT * FROM notify_targets ORDER BY created_at DESC")
    elif role in PNP_SIDE_ROLES:
        cursor.execute(
            "SELECT * FROM notify_targets WHERE station_id = ? ORDER BY created_at DESC",
            (payload.get("station_id"),),
        )
    else:
        cursor.execute(
            "SELECT * FROM notify_targets WHERE barangay_id = ? ORDER BY created_at DESC",
            (payload.get("barangay_id"),),
        )
    rows = [dict(r) for r in cursor.fetchall()]
    channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
    if channels is not None:
        rows = [r for r in rows if r["channel"] in channels]
    conn.close()
    return rows


@router.post("/api/notify_targets")
async def add_notify_target(target: NotifyTargetSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")

    if target.channel not in ("telegram", "sms"):
        conn.close()
        raise HTTPException(status_code=400, detail="channel must be 'telegram' or 'sms'")
    channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
    if channels is not None and target.channel not in channels:
        conn.close()
        raise HTTPException(status_code=403, detail=f"You can't manage {target.channel} recipients")
    if not target.barangay_id and not target.station_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Provide barangay_id or station_id")

    # A non-DEVTEAM caller may only create a target scoped to their own
    # barangay/station -- otherwise a barangay admin could register a
    # responder in someone else's jurisdiction.
    role = payload.get("role")
    if role != "DEVTEAM":
        if role in PNP_SIDE_ROLES and target.station_id != payload.get("station_id"):
            conn.close()
            raise HTTPException(status_code=403, detail="Can only manage targets for your own station")
        if role not in PNP_SIDE_ROLES and target.barangay_id != payload.get("barangay_id"):
            conn.close()
            raise HTTPException(status_code=403, detail="Can only manage targets for your own barangay")

    target_id = str(uuid.uuid4())
    try:
        cursor.execute(
            """INSERT INTO notify_targets (id, barangay_id, station_id, channel, destination, label, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (target_id, target.barangay_id, target.station_id, target.channel, target.destination, target.label),
        )
        log_audit(cursor, payload, "notify_target.created", "notify_target", target_id,
                  snapshot={"channel": target.channel, "destination": target.destination, "label": target.label,
                            "barangay_id": target.barangay_id, "station_id": target.station_id})
        conn.commit()
        return {"status": "created", "id": target_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@router.delete("/api/notify_targets/{target_id}")
async def delete_notify_target(target_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "manage_notify_targets")

    role = payload.get("role")
    if role != "DEVTEAM":
        cursor.execute("SELECT barangay_id, station_id, channel FROM notify_targets WHERE id = ?", (target_id,))
        existing = cursor.fetchone()
        if existing:
            channels = _scoped_resource_ids(cursor, payload, "manage_notify_targets", "channel")
            if channels is not None and existing["channel"] not in channels:
                conn.close()
                raise HTTPException(status_code=403, detail=f"You can't manage {existing['channel']} recipients")
            if role in PNP_SIDE_ROLES and existing["station_id"] != payload.get("station_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own station")
            if role not in PNP_SIDE_ROLES and existing["barangay_id"] != payload.get("barangay_id"):
                conn.close()
                raise HTTPException(status_code=403, detail="Can only manage targets for your own barangay")

    cursor.execute("SELECT * FROM notify_targets WHERE id = ?", (target_id,))
    nt_row = cursor.fetchone()
    cursor.execute("DELETE FROM notify_targets WHERE id = ?", (target_id,))
    if nt_row:
        log_audit(cursor, payload, "notify_target.deleted", "notify_target", target_id, snapshot=dict(nt_row))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

# --- ADMIN: MANAGE YOUR OWN USERS ONLY ---
@router.get("/api/admin/users")
async def list_my_users(authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()
    # deleted_at IS NULL added 2026-09-22 -- a soft-deleted subordinate
    # shouldn't keep showing up in an admin's own team list (or DevTeam's
    # full list) as if still active; the Audit Log is where a removed
    # account is meant to be found and restored from, not here.
    if payload["role"] == "DEVTEAM":
        cursor.execute("SELECT * FROM users WHERE deleted_at IS NULL")
    else:
        cursor.execute("SELECT * FROM users WHERE parent_admin_id = ? AND deleted_at IS NULL", (payload["id"],))
    rows = cursor.fetchall()
    result = _rows_to_user_dicts_batch(cursor, rows)
    # Each account's dicing, for the Personnel permission editor.
    ids = [u["id"] for u in result]
    scopes_by_user: dict = {}
    if ids:
        ph = ",".join("?" for _ in ids)
        cursor.execute(f"SELECT user_id, permission_key, resource_type, resource_id FROM permission_grants "
                       f"WHERE user_id IN ({ph}) AND resource_type IN ('camera', 'crime_type', 'channel')", tuple(ids))
        for r in cursor.fetchall():
            (scopes_by_user.setdefault(r["user_id"], {}).setdefault(r["permission_key"], {})
             .setdefault(r["resource_type"], []).append(r["resource_id"]))
    for u in result:
        u["resource_scopes"] = scopes_by_user.get(u["id"], {})
    conn.close()
    return result

@router.post("/api/admin/users")
async def create_my_user(new_user: AdminCreateUser, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    if payload["role"] == "DEVTEAM":
        # DEVTEAM has no org of its own to inherit, so it cannot create an
        # operator here -- there would be nothing to scope them to, and
        # chk_user_scope would reject the row. Use /api/devteam/create_user,
        # which takes an explicit barangay or station.
        raise HTTPException(
            status_code=400,
            detail="DEVTEAM accounts have no barangay or station to inherit. "
                   "Use the DevTeam console to create a user with an explicit assignment.")

    target_role = ADMIN_CREATES_ROLE[payload["role"]]

    # An operator inherits their creator's scope, and WHICH field that is
    # depends on the organization: barangay staff get barangay_id, PNP
    # officers get station_id. Copying barangay_id unconditionally (as
    # before) would now violate chk_user_scope for the PNP side.
    if target_role in PNP_SIDE_ROLES:
        new_barangay, new_station = None, payload.get("station_id")
        if not new_station:
            raise HTTPException(status_code=400,
                                detail="Your account has no station assigned; contact DevTeam.")
    else:
        new_barangay, new_station = payload.get("barangay_id"), None
        if not new_barangay:
            raise HTTPException(status_code=400,
                                detail="Your account has no barangay assigned; contact DevTeam.")

    profile = _clean_profile(new_user, required=("full_name",))
    # Permissions sent with the account used to be dropped unless
    # is_sub_admin was also set; now they're applied (and a key this side
    # can never hold is refused, as in PATCH .../permissions).
    for key, granted in (new_user.permissions or {}).items():
        if granted:
            _check_permission_key_allowed({"role": target_role}, key)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in PROFILE_FIELDS)})",
            (new_user.username, hash_password(new_user.password), target_role,
             new_barangay, new_station, new_user.assignment, payload["id"],
             new_user.display_title if new_user.is_sub_admin else None,
             1 if new_user.is_sub_admin else 0, *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_id = cursor.lastrowid

        if new_user.permissions:
            for key, granted in new_user.permissions.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
        log_audit(cursor, payload, "user.created", "user", str(new_id), snapshot={
            "username": new_user.username, "role": target_role, "barangay_id": new_barangay, "station_id": new_station,
            "assignment": new_user.assignment, "permissions": new_user.permissions, **profile})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "user_created", "id": new_id})
        return {"status": "success", "role": target_role, "id": new_id}
    except IntegrityError:
        raise HTTPException(status_code=400, detail="That username is already taken.")
    finally:
        conn.close()

@router.patch("/api/admin/users/{user_id}/permissions")
async def update_user_permissions(user_id: int, data: PermissionsUpdate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only edit permissions for your own users")
    # Unknown keys and keys this account's side can never hold used to be
    # dropped or stored silently -- the caller saw "updated" either way.
    try:
        for key, granted in data.permissions.items():
            if granted:
                _check_permission_key_allowed(dict(target), key)
    except HTTPException:
        conn.close()
        raise

    cursor.execute("SELECT permission_key FROM user_permissions WHERE user_id = ?", (user_id,))
    perms_before = sorted(r["permission_key"] for r in cursor.fetchall())
    cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    for key, granted in data.permissions.items():
        if granted and key in VALID_PERMISSION_KEYS:
            cursor.execute(
                "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                (user_id, key, payload["id"]),
            )
    perms_after = sorted(k for k, g in data.permissions.items() if g and k in VALID_PERMISSION_KEYS)
    log_audit(cursor, payload, "user.permissions_updated", "user", str(user_id),
              snapshot={"from": perms_before, "to": perms_after})
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id, "permissions": data.permissions}

@router.delete("/api/admin/users/{user_id}")
async def delete_my_user(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if payload["role"] != "DEVTEAM" and target["parent_admin_id"] != payload["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="You can only remove your own users")

    # Soft delete, added 2026-09-22 (user request: DevTeam can audit/undo
    # removals). The row stays -- require_auth/login's deleted_at IS NULL
    # checks are what actually revokes access, same as a hard delete used
    # to -- but a snapshot + Restore is now possible, where a hard DELETE
    # gave up that row forever the instant this ran.
    cursor.execute("UPDATE users SET deleted_at = NOW() WHERE id = ?", (user_id,))
    # Snapshot excludes the password hash -- restoring never needs it (the
    # row's real password is untouched by a soft delete; this snapshot is
    # for audit DISPLAY only, not row reconstruction), and there's no
    # reason for even a hash to sit in an audit trail a UI might render.
    snapshot = {k: v for k, v in dict(target).items() if k != "password"}
    log_audit(cursor, payload, "user.deleted", "user", user_id, snapshot=snapshot)
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_deleted", "id": user_id})
    return {"status": "deleted", "id": user_id}

@router.post("/api/admin/users/{user_id}/reset_password")
async def reset_my_users_password(user_id: int, authorization: Optional[str] = Header(None)):
    """Basic account management for admins: reset a password for a user THEY
    manage, same ownership rule as delete_my_user (parent_admin_id must be
    this admin's own id).

    An admin account's OWN password is never resettable through this route --
    admin accounts are created by DevTeam with parent_admin_id left NULL
    (see devteam_create_user), so the ownership check above already excludes
    them for a non-DEVTEAM caller. The explicit role check below is
    belt-and-suspenders: it makes the refusal a readable 403 instead of a
    generic "not your user", and holds even if that NULL invariant ever
    changes. DEVTEAM is the only role that can reset an admin's password --
    see devteam_edit_user for that path.
    """
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, parent_admin_id FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if payload["role"] != "DEVTEAM":
        if target["role"] in (ADMIN_ROLES | {"DEVTEAM"}):
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="Admin account passwords can only be reset by DevTeam.")
        if target["parent_admin_id"] != payload["id"]:
            conn.close()
            raise HTTPException(status_code=403, detail="You can only reset passwords for your own users")

    new_password = secrets.token_urlsafe(12)
    cursor.execute("UPDATE users SET password = ? WHERE id = ?",
                    (hash_password(new_password), user_id))
    log_audit(cursor, payload, "user.password_reset", "user", str(user_id), snapshot={"username": target["username"]})
    conn.commit()
    conn.close()
    # The new password itself never goes over the broadcast channel -- only
    # the fact that a reset happened, same reasoning as devteam_credentials.txt
    # never being re-shown after its one display.
    await manager.broadcast({"channel": "users", "event": "password_reset", "id": user_id})
    return {"status": "reset", "id": user_id, "username": target["username"], "new_password": new_password}


@router.put("/api/admin/users/{user_id}/resource_scopes")
async def admin_set_resource_scopes(user_id: int, body: ResourceScopesUpdate, authorization: Optional[str] = Header(None)):
    """The same dicing DevTeam has (2026-10-01), for a barangay/PNP admin's
    own staff: "View Crime Map, but only these cameras and these crime
    types". Capped at the admin's own reach -- an admin who has been diced
    down themselves can't hand a subordinate more than they hold."""
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _resource_grant_target(cursor, user_id)
        _check_own_subordinate(payload, target)
        for key, dims in (body.scopes or {}).items():
            if not isinstance(dims, dict):
                continue
            for rtype, ids in dims.items():
                ceiling = _scoped_resource_ids(cursor, payload, key, rtype)
                if ceiling is None:
                    continue
                if ids is None:
                    raise HTTPException(status_code=403, detail=f"Your own {key} access is limited, so you can't give {rtype.replace('_', ' ')} access to everything")
                beyond = [str(i) for i in ids if str(i) not in ceiling]
                if beyond:
                    raise HTTPException(status_code=403, detail=f"Outside your own access: {', '.join(beyond)}")
        _apply_resource_scopes(cursor, payload, target, body.scopes)
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id}

@router.get("/api/admin/users/{user_id}/resource_permissions")
async def admin_list_resource_permissions(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "list")

@router.post("/api/admin/users/{user_id}/resource_permissions")
async def admin_grant_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "grant", body)

@router.delete("/api/admin/users/{user_id}/resource_permissions")
async def admin_revoke_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _resource_permissions_handler(payload, user_id, "revoke", body)

# --- IDENTITY VERIFICATION (Phase 4, 2026-09-23) ---
# Every user can attach a government ID; an admin (their own subordinates)
# or DevTeam (anyone) confirms it. See VERIFICATION_DOCS_DIR's own comment
# for why this is never a static mount, and report_requests' migration
# comment (Phase 3, same session) for the broader pattern this codebase
# now follows of using an audited, human-reviewed handoff instead of
# reaching for a permission grant every time two roles need to share
# something. Deliberately does not block login -- these accounts are
# already either admin-vetted at creation or barangay-approval-gated for
# self-signup; this is an additional trust signal DevTeam/admins can act
# on, not a second gate on top of those.
@router.post("/api/users/me/verification")
@limiter.limit("5/minute")
async def upload_my_verification(request: Request, id_document: Optional[UploadFile] = File(None),
                                 face_photo: Optional[UploadFile] = File(None),
                                 authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if id_document is not None:
            filename = _save_verification_document(payload["id"], id_document)
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending', verified_by = NULL, verified_at = NULL WHERE id = ?",
                (filename, payload["id"]),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(payload["id"], face_photo, kind="face"), payload["id"]))
        log_audit(cursor, payload, "user.verification_submitted", "user", str(payload["id"]))
        conn.commit()
        return {"status": "submitted"}
    finally:
        conn.close()


@router.get("/api/users/{user_id}/verification_document")
async def get_verification_document(user_id: int, authorization: Optional[str] = Header(None)):
    """The one new sensitive-data endpoint in this whole feature -- ownership
    is checked explicitly (self, own subordinate via parent_admin_id, or
    DEVTEAM) rather than reusing require_permission()'s key-based model,
    since 'can see this specific person's ID document' isn't a permission
    key anyone should be able to grant around -- it's strictly who they are
    to that account."""
    return _serve_user_file(require_auth(authorization), user_id, "id_document_path")


@router.get("/api/users/{user_id}/face_photo")
async def get_face_photo(user_id: int, authorization: Optional[str] = Header(None)):
    """Same guard as the ID document -- a face photo is identity data too."""
    return _serve_user_file(require_auth(authorization), user_id, "face_photo_path")


def _serve_user_file(payload: dict, user_id: int, column: str):
    if column not in ("id_document_path", "face_photo_path"):
        raise HTTPException(status_code=400, detail="Unknown file")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT id, parent_admin_id, {column} AS path FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        target = dict(target)
        allowed = (
            payload["role"] == "DEVTEAM"
            or payload["id"] == user_id
            or target.get("parent_admin_id") == payload["id"]
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Not authorized to view this document")
        if not target["path"]:
            raise HTTPException(status_code=404, detail="Nothing uploaded")
        real_dir = os.path.realpath(VERIFICATION_DOCS_DIR)
        real_path = os.path.realpath(os.path.join(real_dir, target["path"]))
        if os.path.commonpath([real_dir, real_path]) != real_dir or not os.path.isfile(real_path):
            raise HTTPException(status_code=404, detail="File missing from disk")
        return FileResponse(real_path)
    finally:
        conn.close()

@router.post("/api/admin/users/{user_id}/verification")
async def admin_review_verification(user_id: int, body: VerificationReview, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_ROLES)
    return _review_verification(payload, user_id, body, is_devteam_route=False)

# --- CUSTOM ROLES (Phase 2, 2026-09-23) ---
# A NAMED PERMISSION PRESET layered on the real BARANGAY_STAFF/PNP_OFFICER
# tier -- not a new DB-level role. See custom_roles' own migration comment
# for why: the account stays a real operator for every scope/nav/
# constraint purpose, only display_title + which permissions get
# pre-applied at creation time come from the role. DevTeam-managed only
# (mirrors the "Create Role" UI living in DevteamView's Configuration
# section) -- listing is open to any authenticated admin so their own
# Create User form can offer the roles DevTeam has defined for their org
# side, but creating/deleting one is DevTeam-only.
@router.get("/api/custom_roles")
async def list_custom_roles(org_type: Optional[str] = None, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, ADMIN_OR_DEVTEAM)
    conn = get_conn()
    cursor = conn.cursor()
    if org_type:
        cursor.execute("SELECT * FROM custom_roles WHERE org_type = ? OR org_type IS NULL ORDER BY name", (org_type,))
    else:
        cursor.execute("SELECT * FROM custom_roles ORDER BY name")
    roles = [dict(r) for r in cursor.fetchall()]
    if roles:
        placeholders = ",".join("?" for _ in roles)
        cursor.execute(
            f"SELECT * FROM custom_role_permission_defaults WHERE role_id IN ({placeholders})",
            tuple(r["id"] for r in roles),
        )
        defaults_by_role: dict = {}
        for row in cursor.fetchall():
            defaults_by_role.setdefault(row["role_id"], []).append(dict(row))
        for r in roles:
            r["permission_defaults"] = defaults_by_role.get(r["id"], [])
    conn.close()
    return roles
