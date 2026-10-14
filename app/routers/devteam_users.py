"""DevTeam: full control over any user, their camera grants and custom roles."""
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Header, UploadFile
from pydantic import BaseModel

from db import IntegrityError, get_conn
from backend_core import (
    ADMIN_ROLES, ApplicationReopenSchema, BARANGAY_ONLY_PERMISSIONS, BARANGAY_SIDE_ROLES,
    LocationDecisionSchema, PNP_SIDE_ROLES, POLICE_ONLY_PERMISSIONS, PROFILE_FIELDS,
    RESOURCE_DIMENSIONS, ResourceGrantRequest, ResourceScopesUpdate, VALID_PERMISSION_KEYS,
    VerificationReview, _admin_seat_holder, _apply_resource_scopes, _check_permission_key_allowed,
    _clean_profile, _decision_reason, _dimension_values, _require_registration_authority,
    _require_state, _resource_grant_target, _resource_permissions_handler, _review_verification,
    _row_to_user_dict, _save_verification_document, _set_signup_decision, _usable_barangay,
    hash_password, log_audit, manager, require_auth, require_role, verify_password,
)

router = APIRouter()


STANDARD_ROLES = {"PNP_OFFICER", "BARANGAY_STAFF"}
ALL_ROLES = ADMIN_ROLES | STANDARD_ROLES | {"DEVTEAM"}

class AdminPermissionOverride(BaseModel):
    # Re-authentication, not a new/separate secret: verified against the
    # CALLING DevTeam account's own real password (whatever that install's
    # DevTeam actually logs in with), the same way any step-up confirmation
    # works elsewhere. Deliberately not a fixed/hardcoded bypass code -- that
    # would be one shared, unchangeable-without-a-code-release secret baked
    # into every install of this app rather than each deployment's own real
    # credential, and would sit there in plaintext in whatever ships this
    # source (repo history, this Electron app's own bundle).
    confirm_password: str
    # None (omitted) means "reset to automatic" -- clears custom_permissions
    # back to 0 and this admin's explicit rows, returning them to the
    # original unconditional-pass behavior. A dict means "go custom": set
    # custom_permissions = 1 and replace their rows with exactly this set.
    permissions: Optional[dict] = None

class DevteamUserEdit(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    assignment: Optional[str] = None
    display_title: Optional[str] = None
    barangay_id: Optional[str] = None
    # BUG FOUND 2026-08-23: this model had barangay_id but never station_id,
    # so a PNP account's jurisdiction -- its "location" -- could be set at
    # creation (DevteamCreateUser takes both) but never changed afterward.
    # The edit UI had nowhere to send it even if this were here; both are
    # fixed together, see DevteamView.tsx's edit-user modal.
    station_id: Optional[str] = None
    role: Optional[str] = None
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None
    # Sent explicitly as null to clear (see model_fields_set in
    # devteam_edit_user); left out entirely to leave unchanged.
    parent_admin_id: Optional[int] = None
    custom_role_id: Optional[str] = None

class DevteamCreateUser(BaseModel):
    username: str
    password: str
    role: str
    # Exactly one of these is required, decided by the role's organization:
    # barangay roles need barangay_id, PNP roles need station_id. For a
    # barangay role, station_id does double duty (2026-09-24) -- not the
    # new account's own station (chk_user_scope forbids that), but which
    # station to assign this barangay's jurisdiction to, and ONLY when the
    # barangay has no covering station yet. Required in that case, ignored
    # otherwise (an already-covered barangay auto-resolves from the
    # existing station_barangays link, nothing to pick).
    barangay_id: Optional[str] = None
    station_id: Optional[str] = None
    assignment: str
    display_title: Optional[str] = None
    parent_admin_id: Optional[int] = None
    permissions: Optional[dict] = None
    custom_role_id: Optional[str] = None
    full_name: Optional[str] = None
    birthdate: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    position: Optional[str] = None
    # Dicing applied right after creation: {permission_key: {resource_type:
    # [ids] | None}} -- see RESOURCE_DIMENSIONS. Omitted = not narrowed.
    resource_scopes: Optional[dict] = None
    # Admin roles only: start the account on explicit permissions instead of
    # the automatic admin set, exactly as override_permissions would do
    # right after creation. Needs the DevTeam password, same bar.
    override_permissions: bool = False
    confirm_password: Optional[str] = None

class CustomRoleCreate(BaseModel):
    name: str
    # Optional since 2026-09-29: roles apply to either side, and whatever
    # an account's side can't hold is dropped at assignment time.
    org_type: Optional[str] = None
    permissions: Optional[dict] = None
    # Same shape as ResourceScopesUpdate.scopes, minus cameras (a role has
    # no jurisdiction of its own to pick cameras from).
    scopes: Optional[dict] = None
# A password an admin or DevTeam sets for someone else.
MIN_PASSWORD_LENGTH = 8


def _signup_application(cursor, user_id: int) -> dict:
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Application not found")
    target = dict(target)
    if target["role"] not in ADMIN_ROLES:
        raise HTTPException(status_code=400, detail="Only self-signup admin accounts go through Approvals")
    return target


@router.post("/api/devteam/users/{user_id}/approve_signup")
async def approve_signup(user_id: int, data: Optional[LocationDecisionSchema] = None,
                         authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=False)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "pending")
        if target["role"] == "BARANGAY_ADMIN":
            # Approving the person onto a barangay nobody approved would give
            # them a login that the location gate still refuses.
            _usable_barangay(cursor, target["barangay_id"], "approving its admin")
        _set_signup_decision(cursor, payload, user_id, "approved", reason)
        log_audit(cursor, payload, "user.signup_approved", "user", str(user_id),
                  snapshot={"username": target["username"], "role": target["role"], "reason": reason})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_approved", "id": user_id})
    return {"status": "approved"}


@router.post("/api/devteam/users/{user_id}/reject_signup")
async def reject_signup(user_id: int, data: Optional[LocationDecisionSchema] = None,
                        authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    reason = _decision_reason(data, required=True)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "pending")
        _set_signup_decision(cursor, payload, user_id, "rejected", reason)
        log_audit(cursor, payload, "user.signup_rejected", "user", str(user_id),
                  snapshot={"username": target["username"], "role": target["role"], "reason": reason})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_rejected", "id": user_id})
    return {"status": "rejected"}


@router.post("/api/devteam/users/{user_id}/reopen_signup")
async def reopen_signup(user_id: int, data: ApplicationReopenSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _signup_application(cursor, user_id)
        _require_state(target["signup_status"] or "approved", "rejected")
        reason = _require_registration_authority(cursor, payload, data.reason, data.confirm_password)
        holder = _admin_seat_holder(cursor, target["role"], target["barangay_id"], target["station_id"], user_id)
        if holder:
            raise HTTPException(status_code=409, detail=f"'{holder}' now holds this admin seat, so this application can't be reopened.")
        cursor.execute("SELECT username FROM users WHERE id = ?", (target["signup_decided_by"],))
        decider = cursor.fetchone()
        _set_signup_decision(cursor, payload, user_id, "pending", None)
        log_audit(cursor, payload, "user.signup_reopened", "user", str(user_id),
                  snapshot={"username": target["username"], "reason": reason,
                            "previous_decision": {"status": "rejected", "by": decider["username"] if decider else None,
                                                  "at": target["signup_decided_at"], "reason": target["signup_decision_reason"]}})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "locations", "event": "signup_reopened", "id": user_id})
    return {"status": "pending"}

@router.post("/api/devteam/users")
async def devteam_create_user(new_user: DevteamCreateUser, authorization: Optional[str] = Header(None)):
    """Full-power account creation -- DevTeam can create ANY role
    (PNP_ADMIN, PNP_OFFICER, BARANGAY_ADMIN, BARANGAY_STAFF) directly,
    bypassing the self-signup approval flow, and grant it a permission
    set from the same permission tree admins use for their sub-accounts.

    Which scope field is required depends on the role's organization:
    barangay roles take barangay_id, PNP roles take station_id. The DB's
    chk_user_scope enforces this too, so a mismatch is caught either way --
    this just produces a readable error instead of a constraint violation.

    The one-admin-per-unit unique indexes still apply (one BARANGAY_ADMIN per
    barangay, one PNP_ADMIN per station)."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    role = new_user.role.upper()
    if role not in ALL_ROLES or role == "DEVTEAM":
        raise HTTPException(status_code=400, detail=f"Invalid role '{new_user.role}'")

    is_pnp = role in PNP_SIDE_ROLES
    barangay_id = (new_user.barangay_id or "").strip().lower()
    station_id = (new_user.station_id or "").strip().lower()

    if is_pnp and not station_id:
        raise HTTPException(status_code=400, detail="A police station is required for PNP roles")
    if not is_pnp and not barangay_id:
        raise HTTPException(status_code=400, detail="A barangay is required for barangay roles")
    profile = _clean_profile(new_user, required=("full_name",))

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if is_pnp:
            # Stations are created deliberately in the station manager, never
            # auto-vivified from a typo in a username form.
            cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (station_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail=f"Unknown station '{station_id}'")
            barangay_id = ""
        else:
            # station_id doubles up for this branch (2026-09-24 user
            # request): a barangay account never gets its OWN station_id
            # (chk_user_scope forbids it, and the account INSERT below
            # correctly still sends the now-blanked local `station_id`) --
            # but the SAME form field is repurposed here to mean "which
            # station should cover this barangay's jurisdiction", captured
            # into its own variable before being wiped.
            picked_station_for_jurisdiction = station_id
            station_id = ""
            cursor.execute("SELECT * FROM barangays WHERE id = ?", (barangay_id,))
            existing_barangay = cursor.fetchone()
            # BUG FOUND 2026-09-29 (user request): this used to silently
            # create a brand-new barangay row right here -- "make a
            # barangay" doesn't belong in account creation, it belongs in
            # the Stations tab, where the barangay is registered directly
            # under the station covering it (POST .../stations/{id}/
            # barangays) instead of drifting station-less until someone
            # remembers to assign one. An unknown barangay_id here is now a
            # hard error pointing at where to actually register it.
            if not existing_barangay:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown barangay '{barangay_id}' -- register it from the Stations tab first.")
            # A pending or rejected barangay used to be approved here silently
            # (2026-09-02: creating an account "was the vetting decision"),
            # which undid a rejection with no reason on record and stranded
            # any pending applicant. Since 2026-09-30 that decision is made in
            # Approvals, and this refuses with a message saying so -- still
            # never the silent login failure the 09-02 fix was about.
            try:
                _usable_barangay(cursor, barangay_id, "creating accounts for it")
            except HTTPException:
                conn.close()
                raise

            # BUG FOUND 2026-09-24 (user report): creating a barangay account
            # never connected the barangay to any police station -- that only
            # ever happened later, separately, if DevTeam remembered to go to
            # the Stations tab. A barangay with no covering station is
            # invisible to every PNP account and (since Phase 3) any report
            # request from it has nowhere to route. Auto-resolve from the
            # EXISTING jurisdiction relationship when there is one -- nothing
            # to ask, nothing to reassign. Only a barangay with NO station at
            # all (brand new, or an old orphaned one from before this fix)
            # requires picking one as part of this same creation.
            cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ?", (barangay_id,))
            already_covered = cursor.fetchone()
            if not already_covered:
                if not picked_station_for_jurisdiction:
                    conn.close()
                    raise HTTPException(
                        status_code=400,
                        detail="This barangay has no police station covering it yet -- pick one to assign its jurisdiction.")
                cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (picked_station_for_jurisdiction,))
                if not cursor.fetchone():
                    conn.close()
                    raise HTTPException(status_code=400, detail=f"Unknown station '{picked_station_for_jurisdiction}'")
                cursor.execute(
                    "INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)",
                    (picked_station_for_jurisdiction, barangay_id),
                )

        parent_id = new_user.parent_admin_id
        if role in STANDARD_ROLES and parent_id is None:
            # Auto-attach to whichever admin already runs this org unit, so
            # the account shows up nested under someone in the directory.
            # deleted_at IS NULL added 2026-09-22 -- don't auto-attach a new
            # account to an admin who was soft-deleted; look for a still-
            # active one instead (falls through to unassigned if there
            # isn't one, same as before this feature existed).
            if is_pnp:
                cursor.execute(
                    "SELECT id FROM users WHERE station_id = ? AND role = 'PNP_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') = 'approved'", (station_id,))
            else:
                cursor.execute(
                    "SELECT id FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') = 'approved'", (barangay_id,))
            existing_admin = cursor.fetchone()
            parent_id = existing_admin["id"] if existing_admin else None

        # BUG FOUND 2026-09-04 (user report: created an account for a
        # barangay, it never showed up anywhere). Root cause: this endpoint
        # had no explicit duplicate checks of its own -- it just attempted
        # the INSERT and let SQLite's own unique constraints reject it,
        # caught below as a bare IntegrityError with a message that GUESSES
        # between two entirely different real causes ("that username is
        # taken, OR this location already has that captain role filled").
        # Whichever one actually happened, the DevTeam operator creating the
        # account only sees a maybe -- easy to misread as "probably just a
        # naming collision, I'll retry with a different username" when the
        # real problem was the admin slot, or vice versa, and easy to not
        # register as a failure at all if skimmed quickly. signup() already
        # does this properly with a dedicated pre-check; this endpoint never
        # got the same treatment. Two separate, specific checks instead, so
        # the account either gets created or the operator is told exactly
        # why it didn't.
        # deleted_at IS NULL added 2026-09-22 on all three checks below --
        # a soft-deleted account's username and org-admin slot both free up
        # for reuse, same as a hard delete did. (Restoring a soft-deleted
        # user later, if their old username was reused by someone else in
        # the meantime, correctly fails on the username UNIQUE constraint
        # rather than silently colliding two accounts.)
        cursor.execute("SELECT 1 FROM users WHERE username = ? AND deleted_at IS NULL", (new_user.username,))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Username '{new_user.username}' is already taken.")
        if role in ADMIN_ROLES:
            if is_pnp:
                cursor.execute("SELECT 1 FROM users WHERE station_id = ? AND role = 'PNP_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (station_id,))
                dup_detail = "This station already has a PNP Admin account."
            else:
                cursor.execute("SELECT 1 FROM users WHERE barangay_id = ? AND role = 'BARANGAY_ADMIN' AND deleted_at IS NULL AND COALESCE(signup_status, 'approved') <> 'rejected'", (barangay_id,))
                dup_detail = "This barangay already has a Barangay Admin account."
            if cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail=dup_detail)

        # Custom role (#2, 2026-09-23): a named preset, only ever applied to
        # a real STANDARD_ROLES account -- an admin's own access is either
        # automatic or DevTeam-overridden (see AdminPermissionOverride),
        # never role-templated. org_type must match the account actually
        # being created: a 'police' role can't be handed to a barangay
        # account, chk_user_scope-style validation for the role dimension.
        custom_role = None
        if new_user.custom_role_id:
            if role not in STANDARD_ROLES:
                conn.close()
                raise HTTPException(status_code=400, detail="Custom roles apply to staff/officer accounts only.")
            cursor.execute("SELECT * FROM custom_roles WHERE id = ?", (new_user.custom_role_id,))
            custom_role = cursor.fetchone()
            if not custom_role:
                conn.close()
                raise HTTPException(status_code=400, detail="Unknown custom role.")
            # Legacy side-bound roles (created before 2026-09-29) keep their
            # check; side-agnostic roles (org_type NULL) apply to either side.
            wanted_org = "police" if is_pnp else "barangay"
            if custom_role["org_type"] and custom_role["org_type"] != wanted_org:
                conn.close()
                raise HTTPException(status_code=400, detail=f"That role is for {custom_role['org_type']} accounts, not {wanted_org}.")

        # An admin's permissions are automatic unless overridden, so rows
        # sent for a non-overridden admin would be stored but never read.
        # Overriding at creation needs the same password re-entry as
        # overriding an existing admin.
        override = role in ADMIN_ROLES and new_user.override_permissions
        if new_user.override_permissions and role not in ADMIN_ROLES:
            conn.close()
            raise HTTPException(status_code=400, detail="Only admin accounts have automatic permissions to override.")
        if override:
            cursor.execute("SELECT password FROM users WHERE id = ?", (payload["id"],))
            caller = cursor.fetchone()
            if not caller or not verify_password(new_user.confirm_password or "", caller["password"]):
                conn.close()
                raise HTTPException(status_code=403, detail="Incorrect DevTeam password.")
        explicit_perms = new_user.permissions if (role in STANDARD_ROLES or override) else None
        try:
            for key, granted in (explicit_perms or {}).items():
                if granted:
                    _check_permission_key_allowed({"role": role}, key)
        except HTTPException:
            conn.close()
            raise

        display_title = new_user.display_title or (custom_role["name"] if custom_role else None)
        cursor.returning_execute(
            "INSERT INTO users (username, password, role, barangay_id, station_id, assignment, parent_admin_id, display_title, is_sub_admin, custom_role_id, "
            f"{', '.join(PROFILE_FIELDS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in PROFILE_FIELDS)})",
            (new_user.username, hash_password(new_user.password), role,
             barangay_id or None, station_id or None, new_user.assignment, parent_id,
             display_title, 1 if display_title else 0,
             custom_role["id"] if custom_role else None,
             *[profile.get(f) for f in PROFILE_FIELDS]),
        )
        new_id = cursor.lastrowid
        if override:
            cursor.execute("UPDATE users SET custom_permissions = 1 WHERE id = ?", (new_id,))

        if explicit_perms:
            for key, granted in explicit_perms.items():
                if granted and key in VALID_PERMISSION_KEYS:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )

        if custom_role:
            banned = BARANGAY_ONLY_PERMISSIONS if role in PNP_SIDE_ROLES else POLICE_ONLY_PERMISSIONS
            cursor.execute("SELECT * FROM custom_role_permission_defaults WHERE role_id = ?", (custom_role["id"],))
            explicit = new_user.permissions or {}
            for default in cursor.fetchall():
                key = default["permission_key"]
                if key not in VALID_PERMISSION_KEYS or key in banned:
                    continue
                # The Create User form pre-fills its tree from the role and
                # sends every key explicitly -- a key the operator unticked
                # arrives as False and must stay off, not be re-added here.
                if key in explicit and (not default["resource_type"] or not explicit[key]):
                    continue
                if default["resource_type"] and default["resource_id"]:
                    cursor.execute(
                        "INSERT INTO permission_grants (id, user_id, permission_key, resource_type, resource_id, granted_by) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), new_id, key, default["resource_type"], default["resource_id"], payload["id"]),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?) ON CONFLICT (user_id, permission_key) DO NOTHING",
                        (new_id, key, payload["id"]),
                    )
        if new_user.resource_scopes:
            _apply_resource_scopes(cursor, payload, {"id": new_id, "role": role, "barangay_id": barangay_id or None,
                                                     "station_id": station_id or None}, new_user.resource_scopes)
        log_audit(cursor, payload, "user.created", "user", str(new_id), snapshot={
            "username": new_user.username, "role": role, "barangay_id": barangay_id or None, "station_id": station_id or None,
            "assignment": new_user.assignment, "custom_role_id": custom_role["id"] if custom_role else None,
            "permissions": explicit_perms, "permissions_overridden": override, **profile})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "user_created", "id": new_id})
        await manager.broadcast({"channel": "locations", "event": "location_approved", "barangay_id": barangay_id})
        return {"status": "success", "role": role, "id": new_id, "barangay_id": barangay_id}
    except IntegrityError as e:
        raise HTTPException(
            status_code=400,
            detail="That username is taken, or this location already has that captain role filled.",
        )
    finally:
        conn.close()

# --- DEVTEAM: FULL POWER OVER ANY USER (EDIT / DELETE) ---
@router.patch("/api/devteam/users/{user_id}")
async def devteam_edit_user(user_id: int, data: DevteamUserEdit, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    fields, values = [], []
    if data.username is not None:
        fields.append("username = ?"); values.append(data.username)
    if data.password:
        if len(data.password) < MIN_PASSWORD_LENGTH:
            conn.close()
            raise HTTPException(status_code=400, detail=f"A new password needs at least {MIN_PASSWORD_LENGTH} characters")
        fields.append("password = ?"); values.append(hash_password(data.password))
    if data.assignment is not None:
        fields.append("assignment = ?"); values.append(data.assignment)
    if data.display_title is not None:
        fields.append("display_title = ?"); values.append(data.display_title)
    # Existence is checked explicitly for both -- this can't lean on the FK
    # the way the comment used to claim: users.station_id/barangay_id are
    # declared REFERENCES in schema_sqlite.sql, but SQLite does not enforce
    # foreign keys unless "PRAGMA foreign_keys = ON" is run on the
    # connection, which nothing here does (the default/no-DATABASE_URL
    # path). Without this check, an edit could silently scope an account to
    # a station or barangay id that doesn't exist -- no error, just an
    # account whose jurisdiction never resolves to anything again.
    if data.barangay_id is not None:
        brgy = data.barangay_id.strip().lower()
        if brgy != (target["barangay_id"] or ""):
            try:
                _usable_barangay(cursor, brgy, "moving accounts onto it")
            except HTTPException:
                conn.close()
                raise
        fields.append("barangay_id = ?"); values.append(brgy)
    if data.station_id is not None:
        stn = data.station_id.strip().lower()
        cursor.execute("SELECT 1 FROM police_stations WHERE id = ?", (stn,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown station '{stn}'")
        fields.append("station_id = ?"); values.append(stn)
    new_role = target["role"]
    if data.role is not None and data.role != target["role"]:
        if data.role not in ALL_ROLES:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid role '{data.role}'")
        # DevTeam access is granted by creating a DevTeam account, never by
        # promoting someone in place (or quietly demoting one).
        if "DEVTEAM" in (data.role, target["role"]):
            conn.close()
            raise HTTPException(status_code=400, detail="DevTeam accounts can't be converted to or from other roles")
        new_role = data.role
        fields.append("role = ?"); values.append(data.role)
        # Crossing sides (barangay <-> police) swaps which org column is set
        # -- chk_user_scope allows exactly one -- and drops the old
        # supervisor, who belongs to the other side.
        if (data.role in PNP_SIDE_ROLES) != (target["role"] in PNP_SIDE_ROLES):
            if data.role in PNP_SIDE_ROLES:
                if not data.station_id:
                    conn.close()
                    raise HTTPException(status_code=400, detail="Pick the police station for this account's new role")
                fields.append("barangay_id = ?"); values.append(None)
            else:
                if not data.barangay_id:
                    conn.close()
                    raise HTTPException(status_code=400, detail="Pick the barangay for this account's new role")
                fields.append("station_id = ?"); values.append(None)
            if "parent_admin_id" not in data.model_fields_set:
                fields.append("parent_admin_id = ?"); values.append(None)
    if "parent_admin_id" in data.model_fields_set:
        if data.parent_admin_id is not None:
            if data.parent_admin_id == user_id:
                conn.close()
                raise HTTPException(status_code=400, detail="An account can't report to itself")
            cursor.execute("SELECT role FROM users WHERE id = ? AND deleted_at IS NULL", (data.parent_admin_id,))
            boss = cursor.fetchone()
            wanted_boss = "PNP_ADMIN" if new_role in PNP_SIDE_ROLES else "BARANGAY_ADMIN"
            if not boss or boss["role"] != wanted_boss:
                conn.close()
                raise HTTPException(status_code=400, detail=f"Supervisor must be an active {wanted_boss} account")
        fields.append("parent_admin_id = ?"); values.append(data.parent_admin_id)
    if "custom_role_id" in data.model_fields_set:
        role_id = (data.custom_role_id or "").strip() or None
        if role_id:
            cursor.execute("SELECT 1 FROM custom_roles WHERE id = ?", (role_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail="Unknown custom role")
        fields.append("custom_role_id = ?"); values.append(role_id)
    try:
        profile = _clean_profile(data)
    except HTTPException:
        conn.close()
        raise
    for f, v in profile.items():
        fields.append(f"{f} = ?"); values.append(v)

    if not fields:
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(user_id)
    before = dict(target)
    try:
        cursor.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        changes = {}
        for f in fields:
            col = f.split(" = ")[0]
            if col == "password":
                changes["password"] = "changed"
                continue
            new_v = values[fields.index(f)]
            if before.get(col) != new_v:
                changes[col] = {"from": before.get(col), "to": new_v}
        log_audit(cursor, payload, "user.updated", "user", str(user_id),
                  snapshot={"username": before.get("username"), "changes": changes})
        conn.commit()
    except IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Update rejected: {e}")

    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    updated_row = cursor.fetchone()
    result = _row_to_user_dict(cursor, updated_row)
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_edited", "id": user_id})
    return {"status": "updated", "user": result}

@router.post("/api/devteam/users/{user_id}/override_permissions")
async def devteam_override_admin_permissions(
    user_id: int, data: AdminPermissionOverride, authorization: Optional[str] = Header(None)
):
    """User request 2026-09-04: BARANGAY_ADMIN/PNP_ADMIN permissions are
    normally automatic (require_permission()'s ADMIN_ROLES bypass) and were
    never editable through any UI, because editing user_permissions rows
    for an admin used to do nothing -- the bypass ignored them entirely.
    This is DevTeam-only, and re-checks the CALLING DevTeam's own password
    before touching anything, exactly like any other step-up-confirmed
    sensitive action -- not a new shared secret, and not usable by anyone
    who only has a session token (a stolen/leaked token alone can't call
    this without also knowing that DevTeam's real password).
    """
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT password FROM users WHERE id = ?", (payload["id"],))
        caller = cursor.fetchone()
        if not caller or not verify_password(data.confirm_password, caller["password"]):
            raise HTTPException(status_code=403, detail="Incorrect DevTeam password.")

        cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["role"] not in ADMIN_ROLES:
            # Staff/officer permissions were never automatic in the first
            # place -- PATCH /api/admin/users/{id}/permissions already edits
            # those directly, no override/re-auth dance needed there.
            raise HTTPException(
                status_code=400,
                detail=f"'{target['role']}' permissions are already directly editable -- "
                       f"this endpoint is only for overriding an admin's automatic access.",
            )

        if data.permissions is None:
            cursor.execute("UPDATE users SET custom_permissions = 0 WHERE id = ?", (user_id,))
            cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
            log_audit(cursor, payload, "user.permissions_reset_to_automatic", "user", str(user_id))
            conn.commit()
            await manager.broadcast({"channel": "users", "event": "permissions_reset", "id": user_id})
            return {"status": "reset_to_automatic", "id": user_id}

        # Added 2026-09-22: mirrors the hard bans require_permission() itself
        # enforces (BARANGAY_ONLY_PERMISSIONS for PNP targets, POLICE_ONLY_
        # PERMISSIONS for barangay targets) -- without this, the override
        # endpoint would happily store a user_permissions row require_
        # permission() can never actually honor for this target (it 403s
        # before ever consulting the row), which is harmless functionally
        # but a confusing, dead-on-arrival grant to leave sitting in an
        # audit trail. VALID_PERMISSION_KEYS alone already filtered out
        # anything not a real key at all; this filters out real keys that
        # are real but banned for THIS target's org side.
        banned_for_target = (
            BARANGAY_ONLY_PERMISSIONS if target["role"] in PNP_SIDE_ROLES
            else POLICE_ONLY_PERMISSIONS if target["role"] in BARANGAY_SIDE_ROLES
            else set()
        )
        cursor.execute("UPDATE users SET custom_permissions = 1 WHERE id = ?", (user_id,))
        cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        for key, granted in data.permissions.items():
            if granted and key in VALID_PERMISSION_KEYS and key not in banned_for_target:
                cursor.execute(
                    "INSERT INTO user_permissions (user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                    (user_id, key, payload["id"]),
                )
        log_audit(cursor, payload, "user.permissions_overridden", "user", str(user_id), snapshot={
            "permissions": sorted(k for k, g in data.permissions.items() if g and k in VALID_PERMISSION_KEYS and k not in banned_for_target)})
        conn.commit()
        await manager.broadcast({"channel": "users", "event": "permissions_overridden", "id": user_id})
        return {"status": "overridden", "id": user_id, "permissions": data.permissions}
    finally:
        conn.close()

@router.delete("/api/devteam/users/{user_id}")
async def devteam_delete_user(user_id: int, authorization: Optional[str] = Header(None)):
    """Soft delete -- devteam can remove a captain or any single standard/
    sub-admin account. A soft-deleted captain's own sub-accounts keep their
    parent_admin_id exactly as it was (the referenced row still physically
    exists, just hidden from login/listings) rather than losing the link --
    if the captain is later Restored from the Audit Log, that relationship
    is intact again automatically, with nothing left to reattach by hand."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "DEVTEAM":
        conn.close()
        raise HTTPException(status_code=403, detail="DevTeam accounts cannot be deleted from this panel")

    cursor.execute("UPDATE users SET deleted_at = NOW() WHERE id = ?", (user_id,))
    snapshot = {k: v for k, v in dict(target).items() if k != "password"}
    log_audit(cursor, payload, "user.deleted", "user", user_id, snapshot=snapshot)
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "users", "event": "user_deleted", "id": user_id})
    return {"status": "deleted", "id": user_id}


@router.put("/api/devteam/users/{user_id}/resource_scopes")
async def devteam_set_resource_scopes(user_id: int, body: ResourceScopesUpdate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    try:
        target = _resource_grant_target(cursor, user_id)
        if target["role"] == "DEVTEAM":
            raise HTTPException(status_code=400, detail="DevTeam access is never narrowed")
        _apply_resource_scopes(cursor, payload, target, body.scopes)
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "permissions_updated", "id": user_id})
    return {"status": "updated", "id": user_id}

@router.get("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_list_resource_permissions(user_id: int, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "list")

@router.post("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_grant_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "grant", body)

@router.delete("/api/devteam/users/{user_id}/resource_permissions")
async def devteam_revoke_resource_permission(user_id: int, body: ResourceGrantRequest, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _resource_permissions_handler(payload, user_id, "revoke", body)

@router.get("/api/devteam/cameras/list_for_grants")
async def devteam_cameras_for_grants(authorization: Optional[str] = Header(None)):
    """Tiny helper for the Permissions UI's camera picker -- every camera,
    DevTeam-unscoped, with enough context (name + barangay) to pick from
    without cross-referencing the Cameras tab separately."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, barangay_id FROM cameras ORDER BY barangay_id, name")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@router.post("/api/devteam/users/{user_id}/identity_files")
async def devteam_upload_identity_files(user_id: int, id_document: Optional[UploadFile] = File(None),
                                        face_photo: Optional[UploadFile] = File(None),
                                        authorization: Optional[str] = Header(None)):
    """DevTeam attaches an ID or face photo to any account -- e.g. a paper
    ID handed in at the station, or replacing a blurry photo. A new ID
    resets verification to pending, same as when the owner uploads one."""
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    if id_document is None and face_photo is None:
        raise HTTPException(status_code=400, detail="Attach a government ID and/or a face photo")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        if id_document is not None:
            cursor.execute(
                "UPDATE users SET id_document_path = ?, verification_status = 'pending', verified_by = NULL, verified_at = NULL WHERE id = ?",
                (_save_verification_document(user_id, id_document), user_id),
            )
        if face_photo is not None:
            cursor.execute("UPDATE users SET face_photo_path = ? WHERE id = ?",
                           (_save_verification_document(user_id, face_photo, kind="face"), user_id))
        log_audit(cursor, payload, "user.identity_files_uploaded", "user", str(user_id),
                  snapshot={"id_document": id_document is not None, "face_photo": face_photo is not None})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "users", "event": "user_edited", "id": user_id})
    return {"status": "uploaded"}

@router.post("/api/devteam/users/{user_id}/verification")
async def devteam_review_verification(user_id: int, body: VerificationReview, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    return _review_verification(payload, user_id, body, is_devteam_route=True)

@router.post("/api/devteam/custom_roles")
async def create_custom_role(body: CustomRoleCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    org_type = (body.org_type or "").strip().lower() or None
    if org_type not in (None, "barangay", "police"):
        raise HTTPException(status_code=400, detail="org_type must be 'barangay', 'police', or omitted")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Role name is required")

    # A side-bound role is still limited to what its side can hold. A
    # side-agnostic role (org_type None) stores every permission picked;
    # devteam_create_user drops whatever the account's side can't hold.
    banned = (BARANGAY_ONLY_PERMISSIONS if org_type == "police"
              else POLICE_ONLY_PERMISSIONS if org_type == "barangay" else set())

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM custom_roles WHERE LOWER(name) = LOWER(?)", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f'A role named "{name}" already exists')
        role_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO custom_roles (id, name, org_type, created_by) VALUES (?, ?, ?, ?)",
            (role_id, name, org_type, payload["id"]),
        )
        for key, granted in (body.permissions or {}).items():
            if granted and key in VALID_PERMISSION_KEYS and key not in banned:
                cursor.execute(
                    "INSERT INTO custom_role_permission_defaults (role_id, permission_key, resource_type, resource_id) "
                    "VALUES (?, ?, NULL, NULL)",
                    (role_id, key),
                )
        for key, dims in (body.scopes or {}).items():
            if not (body.permissions or {}).get(key) or key not in RESOURCE_DIMENSIONS or key in banned or not isinstance(dims, dict):
                continue
            for rtype, ids in dims.items():
                if rtype == "camera" or rtype not in RESOURCE_DIMENSIONS[key] or ids is None:
                    continue
                valid = _dimension_values(key, rtype)
                wanted = [i for i in dict.fromkeys(str(i) for i in ids) if i in valid]
                if not wanted:
                    raise HTTPException(status_code=400, detail=f"{key}: pick at least one {rtype.replace('_', ' ')}, or allow all")
                for rid in wanted:
                    cursor.execute(
                        "INSERT INTO custom_role_permission_defaults (role_id, permission_key, resource_type, resource_id) "
                        "VALUES (?, ?, ?, ?)",
                        (role_id, key, rtype, rid),
                    )
        log_audit(cursor, payload, "custom_role.created", "custom_role", role_id, snapshot={"name": name, "org_type": org_type})
        conn.commit()
        return {"status": "created", "id": role_id, "name": name, "org_type": org_type}
    finally:
        conn.close()

@router.delete("/api/devteam/custom_roles/{role_id}")
async def delete_custom_role(role_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"})
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE custom_role_id = ? AND deleted_at IS NULL", (role_id,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Accounts still use this role -- it can't be deleted while assigned.")
    cursor.execute("SELECT name FROM custom_roles WHERE id = ?", (role_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Role not found")
    cursor.execute("DELETE FROM custom_role_permission_defaults WHERE role_id = ?", (role_id,))
    cursor.execute("DELETE FROM custom_roles WHERE id = ?", (role_id,))
    log_audit(cursor, payload, "custom_role.deleted", "custom_role", role_id, snapshot={"name": existing["name"]})
    conn.commit()
    conn.close()
    return {"status": "deleted"}
