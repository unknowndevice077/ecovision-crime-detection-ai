"""Cross-jurisdiction report requests between barangays and PNP."""
import json
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Header, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import get_conn
from backend_core import (
    BARANGAY_SIDE_ROLES, PNP_SIDE_ROLES, REQUEST_FILES_DIR, _crime_type_allowed, _format_12h,
    _parse_report_row, apply_scope, limiter, log_audit, manager, require_auth, require_permission,
    require_role, scope_clause,
)

router = APIRouter()


REPORT_REQUEST_TYPES = {
    "blotter_copy": "Certified copy of a police blotter entry",
    "incident_report": "Incident / spot report",
    "case_status": "Status of an investigation or case",
    "crime_statistics": "Crime statistics for the barangay",
    "incident_certification": "Certification that an incident was reported",
    "other": "Other report",
}
REPORT_REQUEST_PURPOSES = {
    "katarungang_pambarangay": "Katarungang Pambarangay (mediation / conciliation)",
    "bpoc": "Barangay Peace and Order Council meeting or plan",
    "resident_request": "Requested by a resident",
    "legal_or_insurance": "Legal, court or insurance requirement",
    "records": "Barangay records",
    "other": "Other",
}
REPORT_REQUEST_URGENCY = {"routine", "urgent"}
# Crime types a request can be about -- the incident types the system files.
REPORT_REQUEST_CRIMES = {"ANY", "ASSAULT", "ARMED THREAT", "ROBBERY", "THEFT", "PHYSICAL VIOLENCE",
                         "VANDALISM", "HARDWARE_PANIC_INTERRUPT", "OTHER"}


class ReportRequestCreate(BaseModel):
    incident_id: Optional[str] = None
    description: str
    # Structured fields (2026-10-01). Optional so older callers that send
    # only a description keep working; the app's form always sends them.
    report_type: Optional[str] = None
    crime_type: Optional[str] = None
    period_from: Optional[str] = None
    period_to: Optional[str] = None
    location: Optional[str] = None
    persons_involved: Optional[str] = None
    reference: Optional[str] = None
    purpose: Optional[str] = None
    purpose_detail: Optional[str] = None
    urgency: Optional[str] = "routine"
    needed_by: Optional[str] = None

class ReportRequestResponse(BaseModel):
    note: Optional[str] = None
    # Fulfil with a report (2026-10-01): which incident's report, which of
    # its fields the barangay gets, and the summary as police chose to word
    # it for them. The values themselves are read from the record
    # server-side, so only the summary can differ from what was filed.
    incident_id: Optional[str] = None
    share_fields: Optional[list] = None
    summary: Optional[str] = None

# --- REPORT REQUESTS (Phase 3, 2026-09-23) ---
# Barangay formally requests a specific report/crime record from police;
# police accepts (or declines) and hands the actual information back via
# response_note. See report_requests' own migration comment for why this
# does NOT grant any view_history access -- #5's ban on that for barangay
# accounts is unconditional and stays that way here too.
def _report_request_details(body: "ReportRequestCreate") -> dict:
    """Validates the structured fields and returns what gets stored. Empty
    strings are dropped so the station's view shows only what was given."""
    def clean(v, limit=500):
        v = (v or "").strip()
        if len(v) > limit:
            raise HTTPException(status_code=400, detail=f"Keep each field under {limit} characters")
        return v or None

    def iso_date(v, label):
        v = clean(v, 10)
        if v is None:
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} must be a date (YYYY-MM-DD)")

    out = {
        "report_type": clean(body.report_type, 40),
        "crime_type": (clean(body.crime_type, 40) or "").upper() or None,
        "period_from": iso_date(body.period_from, "Period start"),
        "period_to": iso_date(body.period_to, "Period end"),
        "location": clean(body.location, 200),
        "persons_involved": clean(body.persons_involved, 300),
        "reference": clean(body.reference, 100),
        "purpose": clean(body.purpose, 40),
        "purpose_detail": clean(body.purpose_detail, 300),
        "urgency": clean(body.urgency, 10) or "routine",
        "needed_by": iso_date(body.needed_by, "Needed-by date"),
    }
    if out["report_type"] and out["report_type"] not in REPORT_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail="Unknown report type")
    if out["purpose"] and out["purpose"] not in REPORT_REQUEST_PURPOSES:
        raise HTTPException(status_code=400, detail="Unknown purpose")
    if out["purpose"] == "other" and not out["purpose_detail"]:
        raise HTTPException(status_code=400, detail="Say what the report is for")
    if out["crime_type"] and out["crime_type"] not in REPORT_REQUEST_CRIMES:
        raise HTTPException(status_code=400, detail="Unknown crime type")
    if out["urgency"] not in REPORT_REQUEST_URGENCY:
        raise HTTPException(status_code=400, detail="Urgency must be routine or urgent")
    if out["period_from"] and out["period_to"] and out["period_from"] > out["period_to"]:
        raise HTTPException(status_code=400, detail="The period ends before it starts")
    today = datetime.now().date().isoformat()
    if out["period_from"] and out["period_from"] > today:
        raise HTTPException(status_code=400, detail="The period can't start in the future")
    if out["needed_by"] and out["needed_by"] < today:
        raise HTTPException(status_code=400, detail="The needed-by date has already passed")
    if out["urgency"] == "routine" and not body.report_type:
        out.pop("urgency")  # a legacy description-only request: store nothing extra
    return {k: v for k, v in out.items() if v is not None}

@router.post("/api/report_requests")
async def create_report_request(body: ReportRequestCreate, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, BARANGAY_SIDE_ROLES)
    barangay_id = payload.get("barangay_id")
    if not barangay_id:
        raise HTTPException(status_code=400, detail="Your account has no barangay assigned")
    description = (body.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Describe what you're requesting")
    details = _report_request_details(body)

    conn = get_conn()
    cursor = conn.cursor()
    try:
        if body.incident_id:
            # Can only be tied to an incident this barangay can actually see
            # -- reuses the same scope_clause() GET /api/incidents applies,
            # so a request can't be used to probe for the existence of an
            # incident outside the caller's own jurisdiction.
            frag, params = scope_clause(payload)
            where = f"id = ? AND deleted_at IS NULL" + (f" AND {frag}" if frag else "")
            cursor.execute(f"SELECT 1 FROM incidents WHERE {where}", [body.incident_id] + params)
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Incident not found")

        cursor.execute("SELECT station_id FROM station_barangays WHERE barangay_id = ? LIMIT 1", (barangay_id,))
        row = cursor.fetchone()
        station_id = row["station_id"] if row else None

        request_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO report_requests (id, barangay_id, station_id, incident_id, description, requested_by, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, barangay_id, station_id, body.incident_id, description, payload["id"],
             json.dumps(details) if details else None),
        )
        log_audit(cursor, payload, "report_request.created", "report_request", request_id, snapshot={
            "barangay_id": barangay_id, "incident_id": body.incident_id, "description": description,
            "details": details,
        })
        conn.commit()
        if station_id:
            await manager.broadcast({"channel": "report_requests", "event": "created", "id": request_id, "station_id": station_id})
        return {"status": "created", "id": request_id, "station_id": station_id}
    finally:
        conn.close()

@router.get("/api/report_requests")
async def list_report_requests(authorization: Optional[str] = Header(None)):
    """Role-branched, not scope-param-driven: a barangay account only ever
    has an outbox (things it asked for) and a PNP account only ever has an
    inbox (things asked of it) -- there's no ambiguity to resolve with a
    query param the way notify_targets' single shared table needed one."""
    payload = require_auth(authorization)
    role = payload["role"]
    conn = get_conn()
    cursor = conn.cursor()
    if role == "DEVTEAM":
        cursor.execute("SELECT * FROM report_requests ORDER BY requested_at DESC")
    elif role in BARANGAY_SIDE_ROLES:
        cursor.execute("SELECT * FROM report_requests WHERE barangay_id = ? ORDER BY requested_at DESC", (payload.get("barangay_id"),))
    elif role in PNP_SIDE_ROLES:
        cursor.execute("SELECT * FROM report_requests WHERE station_id = ? ORDER BY requested_at DESC", (payload.get("station_id"),))
    else:
        conn.close()
        return []
    rows = [dict(r) for r in cursor.fetchall()]
    # Who asked, by name, and the barangay's real name -- the station sees
    # requests from several barangays and several people in each.
    user_ids = {r["requested_by"] for r in rows} | {r["responded_by"] for r in rows if r.get("responded_by")}
    names: dict = {}
    if user_ids:
        ph = ",".join("?" for _ in user_ids)
        cursor.execute(f"SELECT id, username, full_name, position FROM users WHERE id IN ({ph})", tuple(user_ids))
        names = {u["id"]: dict(u) for u in cursor.fetchall()}
    brgy_ids = {r["barangay_id"] for r in rows}
    brgy_names: dict = {}
    if brgy_ids:
        ph = ",".join("?" for _ in brgy_ids)
        cursor.execute(f"SELECT id, name FROM barangays WHERE id IN ({ph})", tuple(brgy_ids))
        brgy_names = {b["id"]: b["name"] for b in cursor.fetchall()}
    conn.close()
    files_by_req: dict = {}
    if rows:
        conn = get_conn()
        cursor = conn.cursor()
        ph = ",".join("?" for _ in rows)
        cursor.execute(f"SELECT id, request_id, original_name, content_type, size_bytes, uploaded_at FROM report_request_files "
                       f"WHERE request_id IN ({ph}) ORDER BY uploaded_at", tuple(r["id"] for r in rows))
        for f in cursor.fetchall():
            files_by_req.setdefault(f["request_id"], []).append(dict(f))
        conn.close()
    for r in rows:
        try:
            r["details"] = json.loads(r["details"]) if r.get("details") else {}
        except (TypeError, ValueError):
            r["details"] = {}
        try:
            r["shared_report"] = json.loads(r["shared_report"]) if r.get("shared_report") else None
        except (TypeError, ValueError):
            r["shared_report"] = None
        r["files"] = files_by_req.get(r["id"], [])
        who = names.get(r["requested_by"]) or {}
        r["requested_by_name"] = who.get("full_name") or who.get("username")
        r["requested_by_position"] = who.get("position")
        responder = names.get(r.get("responded_by")) or {}
        r["responded_by_name"] = responder.get("full_name") or responder.get("username")
        r["barangay_name"] = brgy_names.get(r["barangay_id"]) or r["barangay_id"]
    return rows

@router.get("/api/report_requests/options")
async def report_request_options(authorization: Optional[str] = Header(None)):
    """The request form's choices, from the same tables the server validates
    against, so the two can't drift."""
    require_auth(authorization)
    return {
        "report_types": [{"value": k, "label": v} for k, v in REPORT_REQUEST_TYPES.items()],
        "purposes": [{"value": k, "label": v} for k, v in REPORT_REQUEST_PURPOSES.items()],
        "crime_types": sorted(REPORT_REQUEST_CRIMES - {"ANY", "OTHER"}),
    }

def _respond_to_report_request(payload: dict, request_id: str, new_status: str, note: Optional[str], require_current: str,
                               shared_report: Optional[dict] = None):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
        req = cursor.fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        req = dict(req)
        if payload["role"] != "DEVTEAM" and req["station_id"] != payload.get("station_id"):
            raise HTTPException(status_code=403, detail="This request wasn't routed to your station")
        if req["status"] != require_current:
            raise HTTPException(status_code=400, detail=f"Request is '{req['status']}', not '{require_current}'")
        cursor.execute(
            "UPDATE report_requests SET status = ?, responded_by = ?, responded_at = NOW(), response_note = ?, "
            "shared_report = COALESCE(?, shared_report) WHERE id = ?",
            (new_status, payload["id"], note, json.dumps(shared_report) if shared_report else None, request_id),
        )
        log_audit(cursor, payload, f"report_request.{new_status}", "report_request", request_id,
                  snapshot={"note": note, "shared": [f["key"] for f in shared_report["fields"]] if shared_report else None,
                            "incident_id": shared_report.get("incident_id") if shared_report else None})
        conn.commit()
        return {"status": new_status}
    finally:
        conn.close()

# Report fields police may hand to a barangay, in display order. "where"
# says which record the value comes from.
SHAREABLE_REPORT_FIELDS = [
    ("case_id", "Case number", "incident"),
    ("incident_type", "Incident type", "report"),
    ("occurred", "Date and time", "incident"),
    ("location", "Location", "incident"),
    ("nature_of_incident", "Nature of incident", "report"),
    ("narrative", "Summary", "report"),
    ("property_damaged", "Property damaged", "report"),
    ("evidence_secured", "Evidence secured", "report"),
    ("action_taken", "Action taken", "report"),
    ("disposition", "Disposition", "report"),
    ("complainant", "Complainant", "report"),
    ("victim_details", "Victim", "report"),
    ("suspect_description", "Suspect", "report"),
    ("witnesses", "Witnesses", "report"),
    ("reporting_officer", "Reporting officer", "report"),
    ("rank", "Rank", "report"),
    ("badge_number", "Badge number", "report"),
]
SHAREABLE_KEYS = {k for k, _, _ in SHAREABLE_REPORT_FIELDS}
REQUEST_FILE_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                      ".webp": "image/webp",
                      ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_REQUEST_FILE_BYTES = 15 * 1024 * 1024
MAX_REQUEST_FILES = 10


def _shareable_report(cursor, payload: dict, request: dict, incident_id: str) -> tuple:
    """The incident and its latest filed report, checked: in the requesting
    barangay, inside the caller's jurisdiction and crime-type access."""
    frag, params = scope_clause(payload)
    where = "id = ? AND deleted_at IS NULL" + (f" AND {frag}" if frag else "")
    cursor.execute(f"SELECT * FROM incidents WHERE {where}", [incident_id] + params)
    inc = cursor.fetchone()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found in your jurisdiction")
    inc = dict(inc)
    if (inc.get("barangay_id") or "").lower() != (request.get("barangay_id") or "").lower():
        raise HTTPException(status_code=400, detail="That incident isn't in the barangay that asked")
    if not _crime_type_allowed(cursor, payload, "view_history", inc.get("type")):
        raise HTTPException(status_code=403, detail="Your access doesn't cover that type of incident")
    cursor.execute(
        "SELECT * FROM incident_reports WHERE incident_id = ? "
        "ORDER BY CASE WHEN report_status = 'confirmed' THEN 0 ELSE 1 END, COALESCE(updated_at, created_at) DESC LIMIT 1",
        (incident_id,))
    row = cursor.fetchone()
    report = _parse_report_row(row) if row else None
    return inc, report


def _build_shared_report(inc: dict, report: Optional[dict], keys: list, summary: Optional[str]) -> dict:
    body = (report or {}).get("report_body") or {}
    if report and report.get("report_status") != "confirmed":
        raise HTTPException(status_code=400, detail="That incident's report is still a draft -- confirm it before sharing")
    values = {
        "case_id": inc.get("case_id"),
        "occurred": f"{inc.get('occurred_date')} {_format_12h(inc.get('occurred_time') or '')}".strip(),
        "location": inc.get("location_name"),
        "incident_type": body.get("incident_type") or inc.get("type"),
    }
    unknown = [k for k in keys if k not in SHAREABLE_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Can't share: {', '.join(map(str, unknown))}")
    fields = []
    for key, label, where in SHAREABLE_REPORT_FIELDS:
        if key not in keys:
            continue
        value = summary.strip() if key == "narrative" and summary and summary.strip() else \
            (values.get(key) if where == "incident" or key == "incident_type" else body.get(key))
        if value not in (None, ""):
            fields.append({"key": key, "label": label, "value": str(value)})
    if not fields:
        raise HTTPException(status_code=400, detail="Choose at least one field that has a value")
    return {"incident_id": inc["id"], "case_id": inc.get("case_id"), "fields": fields,
            "summary_edited": bool(summary and summary.strip() and summary.strip() != (body.get("narrative") or "").strip())}


def _request_for_station(cursor, payload: dict, request_id: str) -> dict:
    cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req = dict(req)
    if payload["role"] != "DEVTEAM" and req["station_id"] != payload.get("station_id"):
        raise HTTPException(status_code=403, detail="This request wasn't routed to your station")
    return req


def _require_report_sharer(payload: dict):
    """Answering a request means handing over what's in the crime history,
    so it takes view_history -- an officer with no permissions at all could
    accept, decline and fulfil requests before 2026-10-01."""
    conn = get_conn()
    try:
        require_permission(conn.cursor(), payload, "view_history")
    finally:
        conn.close()


@router.post("/api/report_requests/{request_id}/accept")
async def accept_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    result = _respond_to_report_request(payload, request_id, "accepted", body.note, require_current="pending")
    await manager.broadcast({"channel": "report_requests", "event": "accepted", "id": request_id})
    return result

@router.post("/api/report_requests/{request_id}/decline")
async def decline_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    result = _respond_to_report_request(payload, request_id, "declined", body.note, require_current="pending")
    await manager.broadcast({"channel": "report_requests", "event": "declined", "id": request_id})
    return result

@router.post("/api/report_requests/{request_id}/fulfill")
async def fulfill_report_request(request_id: str, body: ReportRequestResponse, authorization: Optional[str] = Header(None)):
    """The actual deliverable: police hands the requested information back
    as response_note, visible to the requesting barangay through this same
    request record -- a one-time, audited handoff rather than a standing
    grant into the archive itself."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        shared = None
        if body.incident_id:
            inc, report = _shareable_report(cursor, payload, req, body.incident_id)
            shared = _build_shared_report(inc, report, list(body.share_fields or []), body.summary)
            shared.update(shared_by=payload.get("username"), shared_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        cursor.execute("SELECT COUNT(*) AS n FROM report_request_files WHERE request_id = ?", (request_id,))
        files = cursor.fetchone()["n"]
    finally:
        conn.close()
    if not (body.note or "").strip() and not shared and not files:
        raise HTTPException(status_code=400, detail="Hand over something: share a report, attach a file, or write the information")
    result = _respond_to_report_request(payload, request_id, "fulfilled", (body.note or "").strip() or None,
                                        require_current="accepted", shared_report=shared)
    await manager.broadcast({"channel": "report_requests", "event": "fulfilled", "id": request_id})
    return result


@router.get("/api/report_requests/{request_id}/shareable")
async def report_request_shareable(request_id: str, authorization: Optional[str] = Header(None)):
    """Incidents in the asking barangay that police could answer with, and
    the fields each one's report can share."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        sql, params = apply_scope(payload, "SELECT * FROM incidents", [],
                                  extra_where="deleted_at IS NULL AND LOWER(barangay_id) = LOWER(?)",
                                  extra_params=[req["barangay_id"]])
        cursor.execute(sql + " ORDER BY occurred_date DESC, occurred_time DESC LIMIT 200", tuple(params))
        incs = [dict(r) for r in cursor.fetchall()
                if _crime_type_allowed(cursor, payload, "view_history", r["type"])]
        ids = [i["id"] for i in incs]
        reports: dict = {}
        if ids:
            ph = ",".join("?" for _ in ids)
            cursor.execute(f"SELECT incident_id, report_status FROM incident_reports WHERE incident_id IN ({ph})", tuple(ids))
            for r in cursor.fetchall():
                if r["report_status"] == "confirmed" or r["incident_id"] not in reports:
                    reports[r["incident_id"]] = r["report_status"]
    finally:
        conn.close()
    return {
        "fields": [{"key": k, "label": l} for k, l, _ in SHAREABLE_REPORT_FIELDS],
        "incidents": [{"id": i["id"], "case_id": i["case_id"], "type": i["type"], "status": i["status"],
                       "occurred_date": i["occurred_date"], "occurred_time": i["occurred_time"],
                       "location_name": i["location_name"], "report_status": reports.get(i["id"])} for i in incs],
    }


@router.get("/api/report_requests/{request_id}/share_preview")
async def report_request_share_preview(request_id: str, incident_id: str, authorization: Optional[str] = Header(None)):
    """Every shareable field of one incident's report with its value, so the
    officer sees exactly what each tick would hand over."""
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        inc, report = _shareable_report(cursor, payload, req, incident_id)
    finally:
        conn.close()
    if report and report.get("report_status") != "confirmed":
        return {"report_status": "draft", "fields": []}
    full = _build_shared_report(inc, report, list(SHAREABLE_KEYS), None) if report or inc else None
    return {"report_status": "confirmed" if report else None, "fields": full["fields"] if full else []}


def _request_file_access(cursor, payload: dict, request_id: str) -> dict:
    """The asking barangay and the answering station both see the files."""
    cursor.execute("SELECT * FROM report_requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req = dict(req)
    role = payload["role"]
    if role == "DEVTEAM":
        return req
    if role in BARANGAY_SIDE_ROLES and (req["barangay_id"] or "").lower() == (payload.get("barangay_id") or "").lower():
        return req
    if role in PNP_SIDE_ROLES and req["station_id"] == payload.get("station_id"):
        return req
    raise HTTPException(status_code=404, detail="Request not found")


@router.post("/api/report_requests/{request_id}/files")
@limiter.limit("20/minute")
async def upload_report_request_file(request: Request, request_id: str, file: UploadFile = File(...),
                                     authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    original = os.path.basename(file.filename or "").strip() or "file"
    ext = os.path.splitext(original)[1].lower()
    if ext not in REQUEST_FILE_TYPES:
        raise HTTPException(status_code=400, detail="Attach a PDF, Word (.docx) or image (JPG, PNG, WEBP) file")
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail="Accept the request before attaching files")
        cursor.execute("SELECT COUNT(*) AS n FROM report_request_files WHERE request_id = ?", (request_id,))
        if cursor.fetchone()["n"] >= MAX_REQUEST_FILES:
            raise HTTPException(status_code=400, detail=f"At most {MAX_REQUEST_FILES} files per request")
        file_id = str(uuid.uuid4())
        stored = f"{file_id}{ext}"
        dest = os.path.join(REQUEST_FILES_DIR, stored)
        total = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_REQUEST_FILE_BYTES:
                        raise HTTPException(status_code=400, detail="File too large (15 MB max)")
                    f.write(chunk)
        except HTTPException:
            if os.path.exists(dest):
                os.remove(dest)
            raise
        if total == 0:
            os.remove(dest)
            raise HTTPException(status_code=400, detail="That file is empty")
        cursor.execute(
            "INSERT INTO report_request_files (id, request_id, stored_name, original_name, content_type, size_bytes, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, request_id, stored, original[:200], REQUEST_FILE_TYPES[ext], total, payload["id"]))
        log_audit(cursor, payload, "report_request.file_attached", "report_request", request_id,
                  snapshot={"file": original, "bytes": total})
        conn.commit()
    finally:
        conn.close()
    await manager.broadcast({"channel": "report_requests", "event": "file_attached", "id": request_id})
    return {"id": file_id, "original_name": original, "size_bytes": total}


@router.delete("/api/report_requests/{request_id}/files/{file_id}")
async def delete_report_request_file(request_id: str, file_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    require_role(payload, PNP_SIDE_ROLES | {"DEVTEAM"})
    _require_report_sharer(payload)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        req = _request_for_station(cursor, payload, request_id)
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail="Files can't be removed once the request is fulfilled")
        cursor.execute("SELECT * FROM report_request_files WHERE id = ? AND request_id = ?", (file_id, request_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        cursor.execute("DELETE FROM report_request_files WHERE id = ?", (file_id,))
        log_audit(cursor, payload, "report_request.file_removed", "report_request", request_id,
                  snapshot={"file": row["original_name"]})
        conn.commit()
        try:
            os.remove(os.path.join(REQUEST_FILES_DIR, row["stored_name"]))
        except OSError:
            pass
    finally:
        conn.close()
    await manager.broadcast({"channel": "report_requests", "event": "file_removed", "id": request_id})
    return {"status": "removed"}


@router.get("/api/report_requests/{request_id}/files/{file_id}")
async def download_report_request_file(request_id: str, file_id: str, authorization: Optional[str] = Header(None)):
    payload = require_auth(authorization)
    conn = get_conn()
    cursor = conn.cursor()
    try:
        _request_file_access(cursor, payload, request_id)
        cursor.execute("SELECT * FROM report_request_files WHERE id = ? AND request_id = ?", (file_id, request_id))
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    path = os.path.join(REQUEST_FILES_DIR, os.path.basename(row["stored_name"]))
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The file is no longer on disk")
    return FileResponse(path, media_type=row["content_type"] or "application/octet-stream", filename=row["original_name"])
