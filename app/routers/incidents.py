"""Incident queue: fetch, status changes, archive and AI report drafts."""
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from db import get_conn
from notifications import notify_incident_targets
from backend_core import (
    ADMIN_ROLES, AI_EVENT_PHRASES, BARANGAY_SIDE_ROLES, _crime_type_allowed, _format_12h,
    _holds_permission, _incident_owned_by, _parse_report_row, _scoped_resource_ids, apply_scope,
    incident_type_title, log_audit, manager, require_auth, require_permission, require_role,
    scope_clause,
)

router = APIRouter()


POLICE_SIDE_ROLES = {"PNP_OFFICER", "PNP_ADMIN", "DEVTEAM"}

class IncidentSchema(BaseModel):
    id: str
    case_id: str
    type: str
    officer: str
    lat: float
    lng: float
    location_name: str
    severity: str
    occurred_date: str
    occurred_time: str
    narrative: str
    nature_of_call: str
    arrival_reason: str
    additional_officers: str
    status: str
    confidence: Optional[float] = 1.0
    barangay_id: str
    # The smartpole the report was filed at, when filed from a pole.
    camera_id: Optional[str] = None

class StatusUpdateSchema(BaseModel):
    status: str

class ConfirmAndReportSchema(BaseModel):
    status: str
    capture_snapshot: Optional[bool] = False
    report_details: Optional[dict] = None

class IncidentReportSchema(BaseModel):
    narrative: Optional[str] = None
    nature_of_call: Optional[str] = None
    arrival_reason: Optional[str] = None
    additional_officers: Optional[str] = None


# --- SERIALIZATION HELPERS ---
# Every one of these returns snake_case keys matching the DB columns 1:1.
# This is now the ONLY place a schema change needs to be reflected.

def _row_to_incident_dict(inc_row, details_row, vis_row) -> dict:
    d = dict(inc_row)
    details = dict(details_row) if details_row else {}
    vis = dict(vis_row) if vis_row else {}
    return {
        "id": d["id"], "case_id": d["case_id"], "type": d["type"], "officer": d.get("officer"),
        "lat": d.get("lat"), "lng": d.get("lng"), "location_name": d.get("location_name"),
        "severity": d["severity"], "occurred_date": d["occurred_date"], "occurred_time": d["occurred_time"],
        "narrative": details.get("narrative"), "nature_of_call": details.get("nature_of_call"),
        "arrival_reason": details.get("arrival_reason"), "additional_officers": details.get("additional_officers"),
        "status": d["status"], "confidence": d.get("confidence"), "barangay_id": d.get("barangay_id"),
        "screenshot_path": vis.get("screenshot_path"), "map_hidden": vis.get("map_hidden", 0),
        "camera_id": d.get("camera_id"),
    }


# --- HIERARCHICAL INCIDENT FETCH ---
@router.get("/api/incidents")
async def get_incidents(authorization: Optional[str] = Header(None), filter_barangay_id: Optional[str] = "all",
                        purpose: Optional[str] = "map"):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()

    role = payload["role"]
    # BUG FOUND 2026-09-04 (caught live testing the same day's admin
    # permission override feature): this hand-rolled its own "is this an
    # admin" bypass instead of calling require_permission(), so it never
    # knew about custom_permissions at all -- DevTeam could password-confirm
    # revoking an admin's view_map/view_history, require_permission() (used
    # by every OTHER endpoint) would correctly start 403ing them, and this
    # one endpoint would keep returning 200 with full incident data anyway.
    # Skip the bypass for exactly the admins the override applies to, same
    # condition require_permission() itself uses.
    #
    # 2026-10-01: _holds_permission applies exactly that rule (and the
    # police-only ban), so it replaces the hand-rolled query. It also admits
    # confirm_dismiss_alerts on its own, for the Live Monitor's incident
    # queue: an account allowed to confirm/dismiss alerts couldn't see a
    # single alert to act on without also holding the map. Such an account
    # gets the active queue only -- no map history, no archive.
    can_map = _holds_permission(cursor, payload, "view_map")
    can_history = _holds_permission(cursor, payload, "view_history")
    can_queue = _holds_permission(cursor, payload, "confirm_dismiss_alerts")
    if not (can_map or can_history or can_queue):
        conn.close()
        raise HTTPException(status_code=403, detail="Missing permission: view_map, view_history or confirm_dismiss_alerts")
    queue_only = not (can_map or can_history)

    # Visibility and redaction are two SEPARATE decisions and were previously
    # tangled into one if/else:
    #   visibility -- which barangays' incidents you may see (scope_clause)
    #   redaction  -- whether investigative PII is masked (barangay side yes,
    #                 PNP side no; police need names/narrative to investigate)
    redact = role in BARANGAY_SIDE_ROLES

    # filter_barangay_id only ever NARROWS within the caller's scope. It is
    # appended alongside the scope clause, never instead of it, so it cannot
    # be used to reach outside your own jurisdiction.
    # deleted_at IS NULL added 2026-09-22 alongside the soft-delete feature
    # -- a deleted incident stays in the table (so it can be Restored from
    # the Audit Log) but must disappear from every normal listing, same as
    # it did when DELETE actually removed the row.
    where_clauses, where_params = ["deleted_at IS NULL"], []
    if filter_barangay_id and filter_barangay_id.lower() != "all":
        where_clauses.append("LOWER(barangay_id) = ?")
        where_params.append(filter_barangay_id.lower())

    # Added 2026-09-22 (POLICE_ONLY_PERMISSIONS -- explicit user request:
    # barangay accounts lose the crime-history archive, police-only now).
    # This endpoint serves BOTH the live Incident Map (view_map) AND the
    # archived Incident Log (view_history, HistoryView.tsx -- it calls this
    # SAME endpoint and just drops non-Active rows client-side) from one
    # query with no other way to tell those two callers apart server-side.
    # require_permission()'s POLICE_ONLY_PERMISSIONS ban stops a barangay
    # account from ever being GRANTED view_history going forward, but this
    # endpoint has always done its own hand-rolled permission check above
    # (independent of require_permission()) rather than calling it -- so a
    # stale view_history row granted before this restriction existed would
    # silently still work, and even without one, a barangay caller who only
    # has view_map would still get every Confirmed/Dismissed row back, i.e.
    # the exact archive being restricted. Enforcing a hard status filter
    # here, not just refusing the grant, closes both gaps at once: a
    # barangay-side caller only ever gets their own currently-ACTIVE
    # incidents (their live queue/map), never the historical record,
    # regardless of which permission key let them into this endpoint at
    # all or how old that grant is.
    if role in BARANGAY_SIDE_ROLES or queue_only:
        where_clauses.append("status = 'Active'")
    extra_where = " AND ".join(where_clauses)
    extra_params = where_params

    sql, params = apply_scope(
        payload, "SELECT * FROM incidents", [],
        extra_where=extra_where, extra_params=extra_params,
    )
    sql += " ORDER BY occurred_date DESC, occurred_time DESC"
    cursor.execute(sql, tuple(params))

    inc_rows = cursor.fetchall()

    # Dicing (2026-09-29): the map and the history archive share this
    # endpoint, so the caller says which one it is and gets that
    # permission's narrowing -- falling back to whichever of the two it
    # actually holds, so asking for the other can never widen anything.
    scope_key = "view_history" if purpose == "history" else "view_map"
    if queue_only:
        scope_key = "confirm_dismiss_alerts"
    elif not _holds_permission(cursor, payload, scope_key):
        scope_key = "view_map" if scope_key == "view_history" else "view_history"
    allowed_types = _scoped_resource_ids(cursor, payload, scope_key, "crime_type")
    allowed_cams = _scoped_resource_ids(cursor, payload, "view_map", "camera") if scope_key == "view_map" else None
    if allowed_types is not None:
        inc_rows = [r for r in inc_rows if (r["type"] or "").strip().upper() in allowed_types]
    if allowed_cams is not None:
        inc_rows = [r for r in inc_rows if not r["camera_id"] or r["camera_id"] in allowed_cams]

    # Was 2 extra queries PER incident row (N+1) -- batch both child tables
    # in one IN(...) query each instead, keyed by incident_id.
    inc_ids = [inc["id"] for inc in inc_rows]
    details_by_id: dict = {}
    vis_by_id: dict = {}
    if inc_ids:
        placeholders = ",".join("?" for _ in inc_ids)
        cursor.execute(f"SELECT * FROM incident_details WHERE incident_id IN ({placeholders})", tuple(inc_ids))
        for row in cursor.fetchall():
            details_by_id[row["incident_id"]] = row
        cursor.execute(f"SELECT * FROM incident_visibility WHERE incident_id IN ({placeholders})", tuple(inc_ids))
        for row in cursor.fetchall():
            vis_by_id[row["incident_id"]] = row

    results = []
    for inc in inc_rows:
        record = _row_to_incident_dict(inc, details_by_id.get(inc["id"]), vis_by_id.get(inc["id"]))
        if redact:
            # Left out, not replaced with a "[RESTRICTED]" placeholder: a
            # placeholder tells the reader there is a police narrative they
            # aren't allowed to read, which is itself information.
            for field in ("narrative", "nature_of_call", "arrival_reason", "additional_officers"):
                record[field] = None
        results.append(record)
    conn.close()
    return results

@router.post("/api/incidents")
async def add_incident(incident: IncidentSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        # Filing is done from the Incident Map, so it takes that screen's
        # permission (it used to take none). Never trust the client's
        # barangay_id: a barangay account files into its own barangay
        # whatever the body says; a police account, which has no barangay of
        # its own (every police filing used to 403 here), into the named one
        # only if its station covers it.
        require_permission(cursor, payload, "view_map")
        if payload["role"] in BARANGAY_SIDE_ROLES:
            effective_barangay_id = payload.get("barangay_id")
        else:
            effective_barangay_id = (incident.barangay_id or "").strip().lower()
            frag, fparams = scope_clause(payload, "id")
            cursor.execute("SELECT 1 FROM barangays WHERE id = ?" + (f" AND {frag}" if frag else ""),
                           [effective_barangay_id] + fparams)
            if not cursor.fetchone():
                raise HTTPException(status_code=403, detail="That barangay is outside your jurisdiction.")
        if not effective_barangay_id:
            raise HTTPException(status_code=403, detail="Your account has no assigned location.")
        if incident.camera_id:
            cursor.execute("SELECT barangay_id FROM cameras WHERE id = ?", (incident.camera_id,))
            cam_row = cursor.fetchone()
            if not cam_row or (cam_row["barangay_id"] or "").lower() != effective_barangay_id.lower():
                raise HTTPException(status_code=400, detail="That smartpole isn't in the barangay this report is filed in.")
        cursor.execute(
            """INSERT INTO incidents
               (id, case_id, type, severity, status, lat, lng, location_name,
                occurred_date, occurred_time, confidence, officer, barangay_id, source, camera_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL', ?)""",
            (incident.id, incident.case_id, incident.type, incident.severity, incident.status,
             incident.lat, incident.lng, incident.location_name, incident.occurred_date, incident.occurred_time,
             incident.confidence, incident.officer, effective_barangay_id.lower(), incident.camera_id),
        )
        cursor.execute(
            """INSERT INTO incident_details (incident_id, narrative, nature_of_call, arrival_reason, additional_officers)
               VALUES (?, ?, ?, ?, ?)""",
            (incident.id, incident.narrative, incident.nature_of_call, incident.arrival_reason, incident.additional_officers),
        )
        cursor.execute(
            "INSERT INTO incident_visibility (incident_id, map_hidden) VALUES (?, 0)",
            (incident.id,),
        )
        log_audit(cursor, payload, "incident.filed", "incident", incident.id,
                  snapshot={"type": incident.type, "barangay_id": effective_barangay_id, "location": incident.location_name})
        conn.commit()
        return {"status": "persisted"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

AI_RECOMMENDED_ACTIONS = {
    "ARMED THREAT": "Dispatch the nearest mobile patrol immediately and approach as an armed-subject call. Secure the area and preserve the camera footage.",
    "ROBBERY": "Dispatch a patrol unit to the scene, identify and interview the complainant, and canvass for other CCTV along likely escape routes.",
    "ASSAULT": "Dispatch a patrol unit to the scene, check for injured persons and arrange medical assistance, and identify the parties involved.",
    "PHYSICAL VIOLENCE": "Dispatch a patrol unit to the scene, check for injured persons and arrange medical assistance, and identify the parties involved.",
    "VANDALISM": "Coordinate with the barangay to document the damage and identify the property owner; review footage for identifiable subjects.",
    "HARDWARE_PANIC_INTERRUPT": "Contact the barangay tanod on duty and dispatch the nearest unit to the smartpole to check on the person who pressed the panic button.",
}

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _confidence_band(conf: Optional[float]) -> str:
    if conf is None:
        return "unknown"
    if conf >= 0.85:
        return "high"
    if conf >= 0.65:
        return "moderate"
    return "low"


def _time_of_day(hhmmss: str) -> str:
    try:
        h = int(str(hhmmss).replace(":", "")[:2])
    except ValueError:
        return "unknown"
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "midday"
    if 14 <= h < 18:
        return "afternoon"
    if 18 <= h < 21:
        return "evening"
    return "night"


def _clip_seconds(duration) -> Optional[float]:
    """Clip durations are stored as "00:08" (recorder) or "3.0s" (cutter)."""
    d = str(duration or "").strip().lower()
    try:
        if d.endswith("s"):
            return float(d[:-1])
        parts = [float(x) for x in d.split(":")]
        total = 0.0
        for x in parts:
            total = total * 60 + x
        return total if parts else None
    except ValueError:
        return None


def clip_length_phrase(duration) -> Optional[str]:
    secs = _clip_seconds(duration)
    if secs is None:
        return None
    secs = int(round(secs))
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    m, s_ = divmod(secs, 60)
    return f"{m} min {s_} s" if s_ else f"{m} min"


def label_incident_clips(event: Optional[str], clips: list) -> list:
    """Evidence clips are named for people, not by file: "Assault 1",
    "Assault 2"... in recording order. The file name and hash stay in the
    record (chain of custody) but aren't what an officer reads."""
    title = incident_type_title(event)
    return [{**c, "label": f"{title} {i}", "length": clip_length_phrase(c.get("duration"))}
            for i, c in enumerate(clips, start=1)]


def _people_phrase(n: int) -> str:
    word = NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)
    return f"{word.capitalize()} ({n}) person{'s were' if n != 1 else ' was'}"


def build_ai_report_draft(cursor, incident_id: str) -> Optional[dict]:
    cursor.execute(
        """SELECT i.*, d.narrative AS d_narrative, d.ai_context, v.screenshot_path, v.screenshot_sha256,
                  c.name AS camera_name, b.name AS barangay_name, b.city_municipality, b.province
           FROM incidents i
           LEFT JOIN incident_details d ON d.incident_id = i.id
           LEFT JOIN incident_visibility v ON v.incident_id = i.id
           LEFT JOIN cameras c ON c.id = i.camera_id
           LEFT JOIN barangays b ON b.id = i.barangay_id
           WHERE i.id = ?""",
        (incident_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    inc = dict(row)
    try:
        ctx = json.loads(inc.get("ai_context") or "{}") or {}
    except (TypeError, ValueError):
        ctx = {}

    cursor.execute(
        """SELECT s.name FROM station_barangays sb JOIN police_stations s ON s.id = sb.station_id
           WHERE sb.barangay_id = ? ORDER BY s.name LIMIT 1""",
        (inc.get("barangay_id"),),
    )
    st = cursor.fetchone()
    station_name = st["name"] if st else None

    cursor.execute(
        "SELECT filename, duration, sha256, recorded_at FROM video_records WHERE associated_incident_id = ? ORDER BY recorded_at",
        (incident_id,),
    )
    clips = [dict(r) for r in cursor.fetchall()]

    event = (inc.get("type") or "UNKNOWN").upper()
    conf = inc.get("confidence")
    band = _confidence_band(conf)
    tod = _time_of_day(inc.get("occurred_time") or "")
    clips = label_incident_clips(event, clips)
    people = ctx.get("people_in_frame")
    weapons = [w for w in (ctx.get("weapons") or []) if isinstance(w, dict) and w.get("name")]
    detector = ctx.get("detector")
    source = inc.get("source") or "MANUAL"

    place_bits = [inc.get("camera_name") or inc.get("location_name") or "an unnamed camera location"]
    if inc.get("barangay_name"):
        place_bits.append(f"Barangay {inc['barangay_name']}")
    if inc.get("city_municipality"):
        place_bits.append(inc["city_municipality"])
    place = ", ".join(place_bits)
    when = f"{inc.get('occurred_date')} at approximately {_format_12h(inc.get('occurred_time') or '')} ({tod})"
    what = AI_EVENT_PHRASES.get(event, f"a suspected {event.lower()} incident")

    paras = []
    if source == "HARDWARE_PANIC":
        paras.append(f"On {when}, {what} was recorded at {place}. No AI model was involved in raising this alert.")
    elif source == "AI_AUTOMATION":
        s = f"On {when}, the EcoVision AI surveillance system flagged {what} at {place}."
        if conf is not None:
            s += f" The system's confidence in this alert was {round(conf * 100)}% ({band})."
        paras.append(s)
    else:
        paras.append(f"On {when}, {what} was filed manually by an operator for {place}.")

    obs = []
    if isinstance(people, int):
        obs.append(f"{_people_phrase(people)} in the camera's field of view at the time of detection.")
    attribution = ctx.get("attribution")
    if attribution == "track" and ctx.get("track_id") is not None:
        obs.append(f"The alert was attributed to tracked person #{ctx['track_id']}.")
    elif attribution == "scene":
        obs.append("The alert was raised on the scene as a whole; the system could not attribute it to one specific person.")
    verifier = ctx.get("verifier") if isinstance(ctx.get("verifier"), dict) else None
    if verifier and verifier.get("passed"):
        # Set by maincode/alarm_verifier.py: a second model reviewed the clip
        # before the alert was raised. Its description is the model's words,
        # so it is quoted and attributed rather than stated as fact.
        line = "A second AI model reviewed the two seconds before the alert and confirmed it"
        if verifier.get("reason"):
            line += f', describing the scene as: "{verifier["reason"]}"'
        obs.append(line + ".")
    if weapons:
        obs.append("Suspected weapon(s) detected: " + ", ".join(
            f"{w['name']} ({round(float(w.get('conf', 0)) * 100)}%)" for w in weapons) + ".")
    elif source == "AI_AUTOMATION" and event != "ARMED THREAT" and ctx:
        obs.append("No weapon was detected.")
    if obs:
        paras.append(" ".join(obs))

    ev = []
    if inc.get("screenshot_path"):
        ev.append("A still image was captured at the moment of detection.")
    if clips:
        names = [c["label"] + (f" ({c['length']})" if c.get("length") else "") for c in clips]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        ev.append(f"{len(clips)} video clip{'s are' if len(clips) != 1 else ' is'} on file: {listed}.")
    if ev:
        paras.append(" ".join(ev))

    paras.append(
        "This narrative was generated automatically from sensor and model output and has not been verified. "
        "The reporting officer must review the footage, confirm or correct each statement, and add the "
        "complainant, victim, witness and suspect information gathered on scene.")

    if weapons:
        suspect = "Subject seen with suspected " + ", ".join(w["name"] for w in weapons) + ". Physical description to be completed by the responding officer from the footage."
    elif attribution == "track" and ctx.get("track_id") is not None:
        suspect = f"Tracked person #{ctx['track_id']} in the footage. Physical description to be completed by the responding officer."
    else:
        suspect = "Unidentified. To be completed by the responding officer from the footage and witness accounts."

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "incident_type": event,
        "severity": inc.get("severity"),
        "occurred_date": inc.get("occurred_date"),
        "occurred_time": inc.get("occurred_time"),
        "time_of_day": tod,
        "location": {
            "camera_name": inc.get("camera_name") or inc.get("location_name"),
            "camera_id": inc.get("camera_id"),
            "barangay": inc.get("barangay_name") or inc.get("barangay_id"),
            "city_municipality": inc.get("city_municipality"),
            "province": inc.get("province"),
            "station": station_name,
        },
        "detection": {
            "source": source,
            "detector": detector,
            "confidence": conf,
            "confidence_band": band,
            "people_in_frame": people,
            "attribution": attribution,
            "track_id": ctx.get("track_id"),
            "weapons": weapons,
        },
        "evidence": {
            "snapshot": inc.get("screenshot_path"),
            "clips": [{"label": c["label"], "length": c.get("length"), "recorded_at": c.get("recorded_at")} for c in clips],
        },
        "narrative": "\n\n".join(paras),
        "suspect_description": suspect,
        "recommended_action": AI_RECOMMENDED_ACTIONS.get(event, "Dispatch a patrol unit to verify the incident on scene."),
    }

INCIDENT_STATUSES = ("Active", "Confirmed", "Dismissed")


def _valid_incident_status(status: Optional[str]) -> str:
    """The incidents.status CHECK constraint, stated up front: anything else
    used to reach the UPDATE and come back as a bare 500."""
    if status not in INCIDENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(INCIDENT_STATUSES)}")
    return status


@router.patch("/api/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, data: StatusUpdateSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    _valid_incident_status(data.status)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- confirm_dismiss_alerts
    # is a normal, common permission on both sides; without this, anyone who has it
    # could confirm/dismiss another jurisdiction's incident.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    try:
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
    except HTTPException:
        conn.close()
        raise
    cursor.execute("SELECT case_id, type, status, barangay_id FROM incidents WHERE id = ?", (incident_id,))
    before = cursor.fetchone()
    cursor.execute("UPDATE incidents SET status = ? WHERE id = ?", (data.status, incident_id))
    updated = cursor.rowcount
    if updated and before:
        _record_detection_feedback(cursor, payload, incident_id, (data.status or "").lower())
        log_audit(cursor, payload, f"incident.{(data.status or '').lower() or 'status_changed'}", "incident", incident_id,
                  snapshot={"case_id": before["case_id"], "type": before["type"], "from": before["status"],
                            "to": data.status, "barangay_id": before["barangay_id"]})
    conn.commit()
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    await manager.broadcast({"channel": "incidents", "id": incident_id, "event": "status_updated", "status": data.status})
    return {"status": "updated", "id": incident_id, "new_status": data.status}

@router.delete("/api/incidents/{incident_id}")
async def delete_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, {"DEVTEAM"} | ADMIN_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- this used to
    # cascade to incident_reports on a hard DELETE, so without this check
    # any admin anywhere could destroy another jurisdiction's incident AND
    # any filed police report against it. Soft delete (2026-09-22, see
    # log_audit's own note) no longer cascades at all -- the incident row
    # (and any report tied to it) physically stays, just hidden -- but the
    # jurisdiction check still matters just as much: it's still a real
    # remove-this-from-my-view action, still shouldn't reach outside your
    # own scope.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("SELECT * FROM incidents WHERE id = ? AND deleted_at IS NULL", (incident_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    cursor.execute("UPDATE incidents SET deleted_at = NOW() WHERE id = ?", (incident_id,))
    log_audit(cursor, payload, "incident.deleted", "incident", incident_id, snapshot=dict(target))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@router.patch("/api/incidents/{incident_id}/archive")
async def archive_incident(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    # Hiding an incident from the map is a map action. It used to need no
    # permission at all, so an account that can't see the map could clear
    # incidents from it for everyone. See also _incident_owned_by's BUG
    # FOUND 2026-09-03 comment.
    try:
        require_permission(cursor, payload, "view_map")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "view_map")
    except HTTPException:
        conn.close()
        raise
    cursor.execute("UPDATE incident_visibility SET map_hidden = 1 WHERE incident_id = ?", (incident_id,))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "archived_from_map", "id": incident_id}

_REPORT_REQUIRED = ("reporting_officer", "badge_number", "narrative")


class ReportDraftSchema(BaseModel):
    report_body: dict


def _upsert_incident_report(cursor, incident_id: str, payload: dict, body: dict, status: str,
                            ai_draft: Optional[dict] = None):
    """One open draft per incident: saving again (or confirming) updates it
    in place. Once confirmed, a later save starts a fresh draft -- an
    amendment -- so a filed report is never silently overwritten."""
    if ai_draft is None:
        ai_draft = build_ai_report_draft(cursor, incident_id)
    cols = {
        "reported_by": payload["id"],
        "narrative": body.get("narrative"),
        "nature_of_call": body.get("nature_of_incident"),
        "arrival_reason": body.get("action_taken"),
        "additional_officers": body.get("additional_officers"),
        "ai_draft": json.dumps(ai_draft) if ai_draft else None,
        "report_body": json.dumps(body),
        "report_status": status,
    }
    cursor.execute(
        "SELECT id FROM incident_reports WHERE incident_id = ? AND report_status = 'draft' ORDER BY created_at DESC LIMIT 1",
        (incident_id,),
    )
    open_draft = cursor.fetchone()
    if open_draft:
        cursor.execute(
            f"UPDATE incident_reports SET {', '.join(f'{k} = ?' for k in cols)}, updated_at = NOW() WHERE id = ?",
            (*cols.values(), open_draft["id"]),
        )
        return open_draft["id"]
    rid = str(uuid.uuid4())
    cursor.execute(
        f"INSERT INTO incident_reports (id, incident_id, {', '.join(cols)}, updated_at) "
        f"VALUES (?, ?, {', '.join('?' for _ in cols)}, NOW())",
        (rid, incident_id, *cols.values()),
    )
    return rid


def _require_report_reader(cursor, payload: dict, incident_id: str):
    """Officer reports hold names and narrative, so reading them takes the
    same access as the incident itself: in jurisdiction, and holding
    view_history or confirm_dismiss_alerts for this incident's crime type.
    404 either way, so an outsider can't probe which incident ids exist."""
    if not _incident_owned_by(cursor, incident_id, payload):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    cursor.execute("SELECT type FROM incidents WHERE id = ?", (incident_id,))
    inc_type = (cursor.fetchone() or {"type": None})["type"]
    if not any(_holds_permission(cursor, payload, k) and _crime_type_allowed(cursor, payload, k, inc_type)
               for k in ("confirm_dismiss_alerts", "view_history")):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your access)")


@router.get("/api/incidents/{incident_id}/report_draft")
async def get_report_draft(incident_id: str, authorization: Optional[str] = Header(None)):
    """The AI's draft for this incident, plus the latest officer report
    (draft or confirmed) if one exists."""
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _require_report_reader(cursor, payload, incident_id)
        ai = build_ai_report_draft(cursor, incident_id)
        cursor.execute(
            """SELECT r.*, u.username AS reported_by_username
               FROM incident_reports r LEFT JOIN users u ON u.id = r.reported_by
               WHERE r.incident_id = ?
               ORDER BY CASE WHEN r.report_status = 'draft' THEN 0 ELSE 1 END,
                        COALESCE(r.updated_at, r.created_at) DESC LIMIT 1""",
            (incident_id,),
        )
        row = cursor.fetchone()
        return {"ai_draft": ai, "report": _parse_report_row(row) if row else None}
    finally:
        conn.close()


@router.put("/api/incidents/{incident_id}/report_draft")
async def save_report_draft(incident_id: str, data: ReportDraftSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        require_permission(cursor, payload, "confirm_dismiss_alerts")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
        rid = _upsert_incident_report(cursor, incident_id, payload, data.report_body, "draft")
        conn.commit()
        return {"status": "draft_saved", "id": rid}
    finally:
        conn.close()


@router.post("/api/incidents/{incident_id}/confirm-and-report")
async def confirm_and_report(incident_id: str, data: ConfirmAndReportSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    _valid_incident_status(data.status)
    conn = get_conn()
    cursor = conn.cursor()
    require_permission(cursor, payload, "confirm_dismiss_alerts")
    # See _incident_owned_by's BUG FOUND 2026-09-03 comment -- POLICE_SIDE_ROLES
    # only ruled out barangay accounts, not a PNP account from a DIFFERENT
    # station's jurisdiction.
    if not _incident_owned_by(cursor, incident_id, payload):
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
    # BUG FOUND 2026-09-29: the filing modal sent camelCase keys
    # (reportingOfficer, badgeNumber, ...) while this read snake_case, so
    # every field an officer typed was silently discarded -- only the status
    # flip ever persisted. report_details is now the structured report body
    # (see _REPORT_REQUIRED) and is stored whole in report_body.
    details = data.report_details or {}
    try:
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
        # Re-typing an incident into a crime type this officer can't handle
        # would hand it off their own desk -- refuse rather than allow it.
        new_type = str(details.get("incident_type") or "").strip().upper()
        if new_type and not _crime_type_allowed(cursor, payload, "confirm_dismiss_alerts", new_type):
            raise HTTPException(status_code=403, detail=f"You can't file {new_type} incidents")
    except HTTPException:
        conn.close()
        raise
    missing = [k for k in _REPORT_REQUIRED if not str(details.get(k) or "").strip()]
    if missing:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Report is missing required fields: {', '.join(missing)}")
    officer = details["reporting_officer"].strip()
    # Snapshot the machine draft BEFORE applying the officer's corrections
    # below, so ai_draft records what the AI actually said.
    ai_draft = build_ai_report_draft(cursor, incident_id)

    # Before the UPDATE below, so ai_event records the model's own call.
    _record_detection_feedback(cursor, payload, incident_id, (data.status or "").lower(),
                               final_type=str(details.get("incident_type") or "").strip().upper() or None)
    sets, params = ["status = ?", "officer = ?"], [data.status, officer]
    corrected_type = str(details.get("incident_type") or "").strip().upper()
    if corrected_type:
        sets.append("type = ?")
        params.append(corrected_type)
    corrected_sev = str(details.get("severity") or "").strip().upper()
    if corrected_sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        sets.append("severity = ?")
        params.append(corrected_sev)
    cursor.execute(f"UPDATE incidents SET {', '.join(sets)} WHERE id = ?", (*params, incident_id))
    updated = cursor.rowcount
    if not updated:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")

    _upsert_incident_report(cursor, incident_id, payload, details, "confirmed", ai_draft)
    log_audit(cursor, payload, "report_confirmed", "incident", incident_id,
              {"reporting_officer": officer, "badge_number": details.get("badge_number")})
    # Re-fetch the row for the notification: the UPDATE above only touched
    # status/officer, and the message needs type/location/confidence/etc.,
    # which the request payload doesn't carry.
    cursor.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    incident_row = cursor.fetchone()
    conn.commit()
    conn.close()
    await manager.broadcast({"channel": "incidents", "id": incident_id, "event": "confirmed_and_reported"})

    # docs/incident_response_plan.md §2. Deliberately AFTER commit+close and
    # wrapped so a notification failure (bad credentials, network down,
    # whatever) can never turn a successful confirm-and-report into an error
    # response -- the incident is already confirmed at this point regardless
    # of what happens below.
    if incident_row is not None:
        try:
            notify_incident_targets(get_conn, dict(incident_row))
        except Exception as e:
            print(f"[notify] notify_incident_targets raised: {e}")

    return {"status": "confirmed_and_reported", "id": incident_id}

@router.get("/api/incidents/{incident_id}/reports")
async def list_incident_reports(incident_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # Jurisdiction alone used to be enough here, so a PNP officer with no
    # permissions at all could read every report in the station's area.
    try:
        _require_report_reader(cursor, payload, incident_id)
    except HTTPException:
        conn.close()
        raise
    cursor.execute(
        """SELECT r.*, u.username AS reported_by_username
           FROM incident_reports r JOIN users u ON u.id = r.reported_by
           WHERE r.incident_id = ? ORDER BY r.created_at ASC""",
        (incident_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/api/incidents/{incident_id}/reports")
async def add_incident_report(incident_id: str, data: IncidentReportSchema, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, POLICE_SIDE_ROLES)
    conn = get_conn()
    cursor = conn.cursor()
    # Filing a report is acting on the alert: same gate as saving a draft.
    try:
        require_permission(cursor, payload, "confirm_dismiss_alerts")
        if not _incident_owned_by(cursor, incident_id, payload):
            raise HTTPException(status_code=404, detail="Incident not found (or outside your jurisdiction)")
        _require_incident_type_access(cursor, payload, incident_id, "confirm_dismiss_alerts")
    except HTTPException:
        conn.close()
        raise
    report_id = str(uuid.uuid4())
    cursor.execute(
        """INSERT INTO incident_reports
           (id, incident_id, reported_by, narrative, nature_of_call, arrival_reason, additional_officers)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (report_id, incident_id, payload["id"], data.narrative, data.nature_of_call,
         data.arrival_reason, data.additional_officers),
    )
    conn.commit()
    conn.close()
    return {"status": "report_added", "id": report_id}


def _record_detection_feedback(cursor, payload: dict, incident_id: str, label: str, final_type: Optional[str] = None):
    """Stores an operator verdict on an AI alert as a training label.
    Manual and panic-button incidents are skipped -- no model made those
    calls, so there's nothing to learn from agreeing or disagreeing."""
    if label not in ("confirmed", "dismissed"):
        return
    try:
        cursor.execute(
            """SELECT i.type, i.source, i.camera_id, i.barangay_id, i.confidence, d.ai_context, v.screenshot_path
               FROM incidents i
               LEFT JOIN incident_details d ON d.incident_id = i.id
               LEFT JOIN incident_visibility v ON v.incident_id = i.id
               WHERE i.id = ?""",
            (incident_id,),
        )
        row = cursor.fetchone()
        if not row or row["source"] != "AI_AUTOMATION":
            return
        cursor.execute("SELECT ai_event FROM detection_feedback WHERE incident_id = ?", (incident_id,))
        existing = cursor.fetchone()
        final = (final_type or row["type"] or "").strip().upper() or None
        if existing:
            # ai_event stays what the model originally said, even after a re-type.
            cursor.execute(
                "UPDATE detection_feedback SET label = ?, final_type = ?, decided_by = ?, decided_by_username = ?, "
                "decided_at = NOW(), exported_at = NULL WHERE incident_id = ?",
                (label, final, payload.get("id"), payload.get("username"), incident_id),
            )
        else:
            cursor.execute(
                "INSERT INTO detection_feedback (incident_id, camera_id, barangay_id, ai_event, final_type, label, confidence, "
                "ai_context, screenshot, decided_by, decided_by_username) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (incident_id, row["camera_id"], row["barangay_id"], (row["type"] or "").upper(), final, label,
                 row["confidence"], row["ai_context"], row["screenshot_path"], payload.get("id"), payload.get("username")),
            )
    except Exception as e:
        # Training data is a by-product; never let it fail the verdict itself.
        print(f"⚠️  [FEEDBACK] Could not record verdict for {incident_id}: {e}")


def _require_incident_type_access(cursor, payload: dict, incident_id: str, key: str):
    """404 (same as out-of-jurisdiction) when `key` is diced to crime
    types and this incident's type isn't one of them."""
    cursor.execute("SELECT type FROM incidents WHERE id = ?", (incident_id,))
    row = cursor.fetchone()
    if row and not _crime_type_allowed(cursor, payload, key, row["type"]):
        raise HTTPException(status_code=404, detail="Incident not found (or outside your access)")
