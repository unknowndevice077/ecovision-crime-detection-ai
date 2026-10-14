"""Login, logout, the current user, self-signup and the public station list."""
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Header, Request, UploadFile
from pydantic import BaseModel

from db import IntegrityError, get_conn
from backend_core import (
    ADMIN_ROLES, PNP_SIDE_ROLES, PROFILE_FIELDS, VALID_PERMISSION_KEYS, _clean_profile,
    _holds_permission, _row_to_user_dict, _save_verification_document, _slugify_barangay,
    hash_password, issue_token, limiter, log_audit, require_auth, verify_password,
)

router = APIRouter()


class UserSignup(BaseModel):
    username: str
    password: str
    role: str
    # BARANGAY_ADMIN supplies barangay_id (created pending, DevTeam approves).
    # PNP_ADMIN supplies station_id and must pick a station that already
    # exists -- see the note in signup() for why.
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    assignment: str
    # Personal record (2026-09-29) -- what DevTeam reviews the application
    # against. See PROFILE_FIELDS.
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

# --- AUTH SECTOR CORES ---
@router.get("/api/stations")
async def list_stations_public():
    """id + name only, for the signup form's station picker.

    Unauthenticated because signup is. Deliberately narrower than
    /api/devteam/stations: no jurisdiction and no staff counts, so this
    leaks nothing beyond the names of police stations, which are public
    knowledge anyway.
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM police_stations ORDER BY name")
    rows = [{"id": r["id"], "name": r["name"]} for r in cursor.fetchall()]
    conn.close()
    return rows


@router.post("/api/signup")
@limiter.limit("10/minute")
async def signup(request: Request, user: UserSignup):
    role = user.role.upper()
    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only Barangay Admin / PNP Admin accounts can self-register. "
                   "Operator accounts must be created by your admin.",
        )

    is_pnp = role in PNP_SIDE_ROLES
    # Same id rule as the Stations tab's "Add barangay" (_slugify_barangay):
    # signup used the raw lowercased text, so "New Haven" became "new haven"
    # here but "new-haven" there -- one barangay, two rows, and a rejected
    # one could be applied for again under the other spelling.
    import re as _re
    barangay_name = _re.sub(r"^(brgy\.?|barangay)\s+", "", (user.barangay_id or "").strip(), flags=_re.IGNORECASE)
    barangay_id = _slugify_barangay(barangay_name) if barangay_name else ""
    station_id = (user.station_id or "").strip().lower()

    if is_pnp and not station_id:
        raise HTTPException(status_code=400, detail="A police station is required")
    if not is_pnp and not barangay_id:
        raise HTTPException(status_code=400, detail="Location is required")
    profile = _clean_profile(user, required=("full_name", "birthdate", "home_address", "position"))

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if is_pnp:
            # A barangay can be created on the fly as 'pending' because
            # DevTeam approval is the gate. A STATION cannot: its whole
            # purpose is the jurisdiction DevTeam assigns it, so a
            # self-created one would be an empty shell that sees nothing.
            # PNP admins therefore join a station DevTeam already made.
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail="That police station does not exist yet. Ask DevTeam to "
                           "register the station and set its jurisdiction first.")
            barangay_id = ""
        else:
            station_id = ""
            # An existing barangay is matched under its stored id first -- rows made
            # by the old rule keep raw ids (spaces, underscores) -- and only a new
            # one gets the slug.
            legacy_id = (user.barangay_id or "").strip().lower()
            cursor.execute("SELECT id FROM barangays WHERE id = ?", (legacy_id,))
            legacy = cursor.fetchone()
            if legacy:
                barangay_id = legacy["id"]
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            claimed = cursor.fetchone()
            if claimed and claimed["status"] == "rejected":
                conn.close()
                raise HTTPException(status_code=403, detail="This barangay's registration was declined. Contact DevTeam if you believe that's wrong.")
            new_barangay = not claimed
            if not claimed:
                cursor.execute(
                    "INSERT INTO barangays (id, name, status) VALUES (?, ?, 'pending')",
                    (barangay_id, barangay_name.title()),
                )

        # One admin per org unit, matching the unique indexes. deleted_at IS
        # NULL added 2026-09-22 -- a soft-deleted admin's slot must free up
        # for a new signup, same as it did when delete meant delete.
        if is_pnp:
            cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = ? AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (station_id, role))
            dup_msg = "This station already has a PNP Admin account."
        else:
            cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = ? AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (barangay_id, role))
            dup_msg = "This location already has a Barangay Admin account."
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=dup_msg)

        # signup_status='pending' unconditionally -- every self-signup admin
        # account needs an explicit DevTeam decision on THIS account, not on
        # the location/station it's attached to (see login()'s 2026-09-23
        # fix comment for the bug this closes). Every other account-creation
        # path (devteam_create_user, create_my_user) leaves this column at
        # its 'approved' default, so nothing about those changes.
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, signup_status, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', {', '.join('?' for _ in PROFILE_FIELDS)})",
            (user.username, hash_password(user.password), role,
             barangay_id or None, station_id or None, user.assignment,
             *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_user_id = cursor.lastrowid
        # The applicant is the actor: there is no session yet, and the
        # catch-all entry would only say "anonymous" with no target.
        log_audit(cursor, {"id": new_user_id, "username": user.username}, "user.signup_submitted", "user", new_user_id,
                  snapshot={"role": role, "station_id": station_id or None, "barangay_id": barangay_id or None,
                            "new_barangay": (not is_pnp) and new_barangay, "full_name": profile.get("full_name"),
                            "position": profile.get("position")})

        if is_pnp:
            conn.commit()
            # id included (2026-09-23, #9) so the signup form can offer the
            # ID-upload step via /api/signup/{id}/verification -- this
            # account can't log in yet to reach the authenticated upload
            # endpoint instead.
            return {"status": "pending_approval", "id": new_user_id,
                    "detail": "Account created. A DevTeam administrator must confirm your identity before you can log in."}

        # requested_by also gets reclaimed when the current holder was since
        # soft-deleted -- otherwise a second applicant for a barangay whose
        # original admin was removed would leave requested_by (and so
        # approve_location's target) pointed at the old, gone account
        # instead of this new applicant.
        cursor.execute(
            "UPDATE barangays SET requested_by = ? WHERE id = ? AND "
            "(requested_by IS NULL OR requested_by IN (SELECT id FROM users WHERE deleted_at IS NOT NULL))",
            (new_user_id, barangay_id),
        )
        conn.commit()

        # Always pending now, regardless of whether the LOCATION itself was
        # already 'approved' from an earlier admin -- that only means the
        # place was vetted once, not that this new applicant is who they
        # say they are.
        return {"status": "pending_approval", "id": new_user_id,
                "detail": "Account created. A DevTeam administrator must approve this location before you can log in."}
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Operator profile already mapped.")
    finally:
        conn.close()

# Identity verification for a self-signup admin applicant (#9). A
# just-signed-up BARANGAY_ADMIN can't log in until DevTeam approves their
# barangay (see signup() above), so there's no token to authenticate this
# upload with -- the applicant's own just-created username+password IS the
# proof of identity instead, checked directly against the row this same
# signup call created. Narrower than the authenticated .../me/verification
# endpoint below on purpose: only reachable for a still-unverified admin
# applicant, not a general "upload anyone's ID if you know their password"
# tool.
@router.post("/api/signup/{user_id}/verification")
@limiter.limit("5/minute")
async def upload_signup_verification(
    request: Request, user_id: int,
    username: str = Form(...), password: str = Form(...),
    id_document: Optional[UploadFile] = File(None),
    face_photo: Optional[UploadFile] = File(None),
):
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        row = cursor.fetchone()
        if not row or row["username"] != username or not verify_password(password, row["password"]):
            raise HTTPException(status_code=403, detail="Could not verify those credentials")
        row = dict(row)
        if row["role"] not in ADMIN_ROLES:
            raise HTTPException(status_code=400, detail="This upload is for self-signup admin applications only")
        if id_document is not None:
            filename = _save_verification_document(user_id, id_document)
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending' WHERE id = ?",
                (filename, user_id),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(user_id, face_photo, kind="face"), user_id))
        log_audit(cursor, {"id": user_id, "username": username}, "user.verification_submitted", "user", str(user_id))
        conn.commit()
        return {"status": "submitted"}
    finally:
        conn.close()

@router.post("/api/login")
@limiter.limit("5/minute")
async def login(request: Request, creds: UserLogin):
    conn = get_conn()
    cursor = conn.cursor()
    # deleted_at IS NULL added 2026-09-22 -- a soft-deleted account must not
    # be able to log in and mint a fresh, fully-valid token for itself.
    # Deliberately still returns the generic "Invalid Credentials" (not
    # "this account was deleted") -- same reasoning as a wrong password:
    # don't tell an unauthenticated caller which usernames exist/existed.
    cursor.execute("SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (creds.username,))
    row = cursor.fetchone()
    if not row or not verify_password(creds.password, row["password"]):
        # Failed sign-ins are recorded against the username tried (known or
        # not) so repeated guessing at an account shows up in the log.
        log_audit(cursor, {"id": row["id"] if row else None, "username": creds.username}, "user.login_failed",
                  "user", str(row["id"]) if row else "-",
                  snapshot={"ip": request.client.host if request.client else None})
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid Credentials")

    user_dict = dict(row)

    # BUG FOUND 2026-09-23 (user report: "sign up approvals doesn't work,
    # the devteam can't accept approvals, it just confirms it right away"):
    # a self-signup PNP_ADMIN had NO approval gate at all -- signup()'s own
    # comment said "No location-approval gate for PNP: the station already
    # exists, which means DevTeam already vetted it," but vetting the
    # STATION is not vetting the PERSON claiming to run it. Station ids are
    # readable by anyone (GET /api/stations is unauthenticated, by design,
    # for the signup form's picker), so anyone who typed one in got a fully
    # working admin login instantly, with DevTeam never in the loop -- there
    # was nothing in the Approvals tab to even click. A second, narrower gap
    # existed on the barangay side too: signing up for a barangay_id that
    # was ALREADY 'approved' (e.g. its original admin was later removed)
    # skipped review for the NEW applicant, since the check only ever
    # looked at the location's status, never this specific account's.
    # signup_status closes both: every self-signup admin account is
    # stamped 'pending' at creation (see signup()), independently of
    # whether the location/station itself is already vetted, and only
    # DevTeam approving THIS account (not the location) lifts it.
    if user_dict["role"] in ADMIN_ROLES and user_dict.get("signup_status") == "pending":
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="Your application is still pending DevTeam approval. Please check back later.",
        )
    if user_dict["role"] in ADMIN_ROLES and user_dict.get("signup_status") == "rejected":
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="Your application was not approved. Contact DevTeam for details.",
        )

    if user_dict["role"] != "DEVTEAM" and user_dict.get("barangay_id"):
        cursor.execute("SELECT status FROM barangays WHERE id = ?", (user_dict["barangay_id"],))
        loc = cursor.fetchone()
        if not loc or loc["status"] != "approved":
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="Your barangay's registration was not approved. Contact DevTeam for details."
                       if loc and loc["status"] == "rejected" else
                       "Your location is still pending DevTeam approval. Please check back later.",
            )

    # Stamped here, not on every authenticated request -- last_login answers
    # "when did this account last sign in", not "is a request in flight
    # right now" (there's no session/heartbeat concept in this app to answer
    # that second question honestly, so the Users list doesn't claim to).
    cursor.execute("UPDATE users SET last_login = NOW() WHERE id = ?", (user_dict["id"],))
    log_audit(cursor, user_dict, "user.login", "user", str(user_dict["id"]),
              snapshot={"ip": request.client.host if request.client else None})
    conn.commit()

    token = issue_token(user_dict)
    response_user = _row_to_user_dict(cursor, row)
    conn.close()
    return {"status": "success", "user": response_user, "token": token}

@router.post("/api/logout")
async def logout():
    return {"status": "logged_out"}

@router.get("/api/me")
async def get_me(authorization: Optional[str] = Header(None)):
    """The signed-in account as it is now -- the same shape /api/login
    returns. The dashboard re-reads this when an account changes, so a
    permission granted or revoked shows in the sidebar without logging out
    (the backend already enforced the change on the next request)."""
    payload = require_auth(authorization)
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (payload["id"],))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Account no longer exists")
        return {"user": _row_to_user_dict(cursor, row)}
    finally:
        conn.close()

@router.get("/api/me/can/{permission_key}")
async def me_can(permission_key: str, authorization: Optional[str] = Header(None)):
    """Whether the signed-in account holds a permission. For the AI core,
    which has no user store of its own: it forwards the dashboard's
    Authorization header here before switching what the detector watches."""
    payload = require_auth(authorization)
    if permission_key not in VALID_PERMISSION_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown permission '{permission_key}'")
    conn = get_conn()
    try:
        return {"allowed": _holds_permission(conn.cursor(), payload, permission_key)}
    finally:
        conn.close()
