"""
Database backups and evidence retention -- both previously nonexistent.

Recovery plan (docs/recovery_plan.md) §4 flagged "no database backup cadence
exists" as the single highest-risk operational gap: the one SQLite file
holding every incident, user, and camera record had no scheduled copy
anywhere. Privacy plan (docs/privacy_compliance_plan.md) §5 flagged the
matching gap on the other side -- alert clips, screenshots, and incident rows
persisted forever, which is a proportionality problem under RA 10173, not
just a disk-space one.

Both run from one daily background thread (start_maintenance_scheduler,
called once from backend.py at boot, same pattern as port_utils's parent
watchdog). Kept in its own module rather than growing backend.py further --
this is exactly the kind of self-contained piece the router-split comment
near the top of backend.py already flags as the eventual cleanup.
"""
import os
import glob
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

# ── Retention windows -- see docs/privacy_compliance_plan.md §5 for why ────
DISMISSED_RETENTION_DAYS = 7
CONFIRMED_NO_REPORT_RETENTION_DAYS = 30
# Any incident with a filed report (a row in incident_reports) is NEVER
# touched by this job, regardless of status or age -- once it's evidence,
# retention is a legal question for PNP records rules, not an engineering
# default. See docs/incident_response_plan.md §3.

MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60  # once/day covers both jobs
BACKUP_RETAIN_COUNT = 14  # ~2 weeks of daily snapshots


def backup_database(sqlite_path: str, writable_dir: str):
    """Copy the live SQLite file to a dated backup, prune to the newest
    BACKUP_RETAIN_COUNT. Returns the backup path, or None if there was
    nothing to back up (also the correct no-op for a Postgres deployment --
    see the db_kind check in start_maintenance_scheduler)."""
    if not sqlite_path or not os.path.exists(sqlite_path):
        return None
    backup_dir = os.path.join(writable_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"ecovision_{stamp}.db")
    try:
        # sqlite3's own online backup API copies a consistent snapshot even
        # if a write is in flight elsewhere -- a plain shutil.copy can grab a
        # half-written page if something is mid-transaction at the exact
        # wrong moment, which a plain file copy has no way to detect.
        src_conn = sqlite3.connect(sqlite_path)
        dest_conn = sqlite3.connect(dest)
        with dest_conn:
            src_conn.backup(dest_conn)
        src_conn.close()
        dest_conn.close()
    except Exception as e:
        print(f"[maintenance] Database backup failed: {e}")
        return None

    existing = sorted(glob.glob(os.path.join(backup_dir, "ecovision_*.db")))
    for old in existing[:-BACKUP_RETAIN_COUNT]:
        try:
            os.remove(old)
        except Exception:
            pass
    print(f"[maintenance] Database backed up -> {dest}")
    return dest


def _remove_evidence_file(path_or_url, base_dir):
    """screenshot_path/file_path may be stored as a URL ('/static/...'), a
    bare filename, or (from older rows) an absolute path, depending on which
    code path wrote it -- normalize to the filename and resolve against the
    known directory rather than trusting whatever's on the row verbatim."""
    if not path_or_url:
        return
    filename = os.path.basename(path_or_url)
    if not filename:
        return
    full = os.path.join(base_dir, filename)
    try:
        if os.path.exists(full):
            os.remove(full)
    except Exception as e:
        print(f"[maintenance] Could not remove {full}: {e}")


def retention_sweep(get_conn, table_exists, recordings_dir: str, screenshots_dir: str):
    """Delete evidence past its retention window. See
    docs/privacy_compliance_plan.md §5 for the policy table this implements."""
    conn = get_conn()
    cursor = conn.cursor()
    now = datetime.now()
    counts = {"dismissed": 0, "confirmed_no_report": 0}

    has_video_records = table_exists(cursor, "video_records")
    has_visibility = table_exists(cursor, "incident_visibility")

    def _purge_incident(incident_id):
        if has_video_records:
            cursor.execute(
                "SELECT file_path FROM video_records WHERE associated_incident_id = ?",
                (incident_id,),
            )
            for vr in cursor.fetchall():
                _remove_evidence_file(vr["file_path"], recordings_dir)
            cursor.execute("DELETE FROM video_records WHERE associated_incident_id = ?", (incident_id,))
        if has_visibility:
            cursor.execute("SELECT screenshot_path FROM incident_visibility WHERE incident_id = ?", (incident_id,))
            vis = cursor.fetchone()
            if vis and vis["screenshot_path"]:
                _remove_evidence_file(vis["screenshot_path"], screenshots_dir)
            cursor.execute("DELETE FROM incident_visibility WHERE incident_id = ?", (incident_id,))
        cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))

    # NOT IN (SELECT ... incident_reports) applies to BOTH sweeps below.
    # incident_reports.incident_id is declared ON DELETE CASCADE, and this
    # codebase's SQLite connections do run PRAGMA foreign_keys=ON (db.py),
    # so deleting a reported incident would silently cascade its report away
    # rather than being blocked -- the opposite of what should happen to
    # evidence. Excluding it here is what actually protects it; the schema's
    # CASCADE is the wrong direction to rely on for that.
    reported_subquery = "SELECT DISTINCT incident_id FROM incident_reports"

    cutoff_dismissed = (now - timedelta(days=DISMISSED_RETENTION_DAYS)).isoformat()
    cursor.execute(
        f"""SELECT id FROM incidents
            WHERE status = 'Dismissed' AND created_at < ?
            AND id NOT IN ({reported_subquery})""",
        (cutoff_dismissed,),
    )
    for row in cursor.fetchall():
        _purge_incident(row["id"])
        counts["dismissed"] += 1

    cutoff_confirmed = (now - timedelta(days=CONFIRMED_NO_REPORT_RETENTION_DAYS)).isoformat()
    cursor.execute(
        f"""SELECT id FROM incidents
            WHERE status = 'Confirmed' AND created_at < ?
            AND id NOT IN ({reported_subquery})""",
        (cutoff_confirmed,),
    )
    for row in cursor.fetchall():
        _purge_incident(row["id"])
        counts["confirmed_no_report"] += 1

    conn.commit()
    conn.close()
    if counts["dismissed"] or counts["confirmed_no_report"]:
        print(
            f"[maintenance] Retention sweep: removed {counts['dismissed']} dismissed, "
            f"{counts['confirmed_no_report']} unreported-confirmed incident(s)."
        )
    return counts


def start_maintenance_scheduler(sqlite_path, writable_dir, get_conn, table_exists,
                                 recordings_dir, screenshots_dir, db_kind):
    """Runs backup + retention once immediately, then once every 24h, in a
    daemon thread. The immediate run matters on its own: a pilot machine
    that's powered off overnight (a realistic pattern for a single-site
    deployment) would otherwise never stay up long enough to hit a 24h-idle
    trigger."""

    def _run_once():
        try:
            if db_kind == "sqlite":
                backup_database(sqlite_path, writable_dir)
            # Postgres deployments (docker-compose) are expected to have
            # their own backup story (pg_dump / managed snapshots) -- this
            # sqlite3 .backup() call only applies to the standalone build,
            # which is the one with no such story today.
        except Exception as e:
            print(f"[maintenance] Backup pass failed: {e}")
        try:
            retention_sweep(get_conn, table_exists, recordings_dir, screenshots_dir)
        except Exception as e:
            print(f"[maintenance] Retention pass failed: {e}")

    def _loop():
        _run_once()
        while True:
            time.sleep(MAINTENANCE_INTERVAL_SECONDS)
            _run_once()

    t = threading.Thread(target=_loop, name="maintenance-scheduler", daemon=True)
    t.start()
    print("[maintenance] Backup + retention scheduler started (runs now, then daily).")
