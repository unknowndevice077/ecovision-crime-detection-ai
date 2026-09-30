"""Shared fixture for the backend tests: one FastAPI TestClient over a fresh
SQLite database in a throwaway folder, plus helpers to create accounts,
stations and AI alerts.

Isolation from the real app is the point. ECOVISION_WRITABLE_DIR is pointed at
tests/.tmp/<run> BEFORE backend.py is imported (db.py resolves the database
path at import), so nothing here touches ~/EcoVisionSentinelData. The Telegram
token is blanked so the registration poller never starts -- backend.py loads
.env at import, and a test run must not answer (or swallow) real messages to
the bot.

Run everything with:
    python-env\\python.exe -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_TMP_ROOT = REPO / "tests" / ".tmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
WRITABLE = tempfile.mkdtemp(prefix="run_", dir=_TMP_ROOT)

os.environ["ECOVISION_WRITABLE_DIR"] = WRITABLE
os.environ["TELEGRAM_BOT_TOKEN"] = ""
# TestClient requests come from host "testclient"; stand in for the AI core.
os.environ["ECOVISION_TRUSTED_SERVICE_HOSTS"] = "testclient"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("SQLITE_PATH", None)
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(REPO / "maincode"))

import backend as B  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Never reach real hardware or the live AI core from a test run:
# /api/panic_trigger asks the AI core (port from runtime_ports.json, default
# 8001 -- the running dev system's) to snapshot and record a clip, and
# /siren/* posts to the ESP32 named in config.json. Port 9 (discard) refuses
# at once; the siren is switched off.
Path(WRITABLE, "runtime_ports.json").write_text('{"ai_core": 9}', encoding="utf-8")
B.ESP32_ENABLED = False

_client = None
PASSWORD = "pw-test-12345"
REASON = "Automated test registration, not a real station"


def client() -> TestClient:
    """One app instance for the whole run (startup migrations are slow)."""
    global _client
    if _client is None:
        _client = TestClient(B.app)
        _client.__enter__()
        import atexit
        atexit.register(_shutdown)
    return _client


def _shutdown():
    try:
        _client.__exit__(None, None, None)
    finally:
        shutil.rmtree(WRITABLE, ignore_errors=True)


def db():
    return B.get_conn()


def uid(prefix="t") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def auth(row: dict) -> dict:
    return {"Authorization": "Bearer " + B.issue_token(dict(row))}


def user_row(username: str) -> dict:
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def make_devteam() -> dict:
    name = uid("dev")
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username, password, role, assignment) VALUES (?, ?, 'DEVTEAM', 'HQ')",
                    (name, B.hash_password(PASSWORD)))
        conn.commit()
    finally:
        conn.close()
    return user_row(name)


def make_barangay(cameras: int = 2) -> tuple:
    """An approved barangay with `cameras` cameras. Returns (id, [camera ids])."""
    bid = uid("brgy")
    cams = [uid("cam") for _ in range(cameras)]
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO barangays (id, name, status) VALUES (?, ?, 'approved')", (bid, bid))
        for c in cams:
            cur.execute("INSERT INTO cameras (id, name, url, status, barangay_id) VALUES (?, ?, 'rtsp://x', 'online', ?)",
                        (c, f"Cam {c}", bid))
        conn.commit()
    finally:
        conn.close()
    return bid, cams


def make_station(dev: dict, barangays=()) -> str:
    c = client()
    r = c.post("/api/devteam/stations", headers=auth(dev), json={
        "name": uid("Station"), "station_type": "Police Sub-Station", "reason": REASON, "confirm_password": PASSWORD})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    if barangays:
        r = c.put(f"/api/devteam/stations/{sid}/jurisdiction", headers=auth(dev), json={"barangay_ids": list(barangays)})
        assert r.status_code == 200, r.text
    return sid


def make_user(dev: dict, role: str, *, barangay_id=None, station_id=None, permissions=None,
              resource_scopes=None, custom_role_id=None) -> dict:
    name = uid(role.lower())
    body = {"username": name, "password": PASSWORD, "role": role, "assignment": "test", "full_name": f"Test {name}",
            "barangay_id": barangay_id, "station_id": station_id}
    if permissions is not None:
        body["permissions"] = permissions
    if resource_scopes is not None:
        body["resource_scopes"] = resource_scopes
    if custom_role_id:
        body["custom_role_id"] = custom_role_id
    r = client().post("/api/devteam/users", headers=auth(dev), json=body)
    assert r.status_code == 200, r.text
    return user_row(name)


def make_incident(barangay_id: str, type_: str = "ASSAULT", camera_id=None, status="Active") -> str:
    """A manual-source incident row, for scoping tests."""
    iid = uid("inc")
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO incidents (id, case_id, type, severity, status, occurred_date, occurred_time, barangay_id, camera_id) "
            "VALUES (?, ?, ?, 'HIGH', ?, '2026-09-30', '10:00', ?, ?)",
            (iid, "CASE-" + iid, type_, status, barangay_id, camera_id))
        cur.execute("INSERT INTO incident_visibility (incident_id, map_hidden) VALUES (?, 0)", (iid,))
        conn.commit()
    finally:
        conn.close()
    return iid


def ai_alert(barangay_id: str, camera_id: str, event="ASSAULT", conf=0.7, context=None, screenshot=None) -> str:
    """An AI-source incident through the real /api/ai_trigger path."""
    iid = uid("ai")
    body = {"id": iid, "event": event, "confidence": conf, "barangay_id": barangay_id, "camera_id": camera_id,
            "context": context or {"detector": "test"}}
    if screenshot:
        body["screenshot_path"] = screenshot
    r = client().post("/api/ai_trigger", json=body)
    assert r.status_code == 200, r.text
    return iid


def audit_rows(action=None, target_id=None):
    conn = db()
    try:
        cur = conn.cursor()
        q, p = "SELECT * FROM audit_log WHERE 1=1", []
        if action:
            q += " AND action = ?"; p.append(action)
        if target_id is not None:
            q += " AND target_id = ?"; p.append(str(target_id))
        cur.execute(q + " ORDER BY created_at DESC, rowid DESC", tuple(p))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
