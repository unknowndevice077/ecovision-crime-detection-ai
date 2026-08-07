"""
Migration: precinct/barangay captain roles -> PNP/barangay org hierarchy
========================================================================

Moves the user model from the old flat role list to organization x tier,
and gives PNP an explicit jurisdiction instead of borrowing a barangay_id.

  DEVTEAM           -> DEVTEAM           (unscoped)
  PRECINCT_CAPTAIN  -> PNP_ADMIN         (gains station_id, loses barangay_id)
  POLICE            -> PNP_OFFICER       (gains station_id, loses barangay_id)
  BARANGAY_CAPTAIN  -> BARANGAY_ADMIN    (keeps barangay_id)
  BARANGAY          -> BARANGAY_STAFF    (keeps barangay_id)

Station seeding is deliberately NON-DESTRUCTIVE: one station is created per
barangay that currently has PNP-side staff, named after that barangay, with
its jurisdiction initially set to just that barangay. Nobody is demoted.
Merging stations and widening jurisdictions is then a deliberate act in the
DEVTEAM console rather than a silent side effect of this script.

Usage:
    python app/migrate_pnp_hierarchy.py --dry-run    # report, change nothing
    python app/migrate_pnp_hierarchy.py              # migrate (makes a backup)

Safe to re-run: detects an already-migrated database and exits.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROLE_MAP = {
    "DEVTEAM": "DEVTEAM",
    "PRECINCT_CAPTAIN": "PNP_ADMIN",
    "POLICE": "PNP_OFFICER",
    "BARANGAY_CAPTAIN": "BARANGAY_ADMIN",
    "BARANGAY": "BARANGAY_STAFF",
}
PNP_OLD = {"PRECINCT_CAPTAIN", "POLICE"}

NEW_USERS_DDL = """
CREATE TABLE users_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password        TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN
                        ('DEVTEAM','PNP_ADMIN','PNP_OFFICER','BARANGAY_ADMIN','BARANGAY_STAFF')),
    barangay_id     TEXT REFERENCES barangays(id) ON DELETE RESTRICT,
    station_id      TEXT REFERENCES police_stations(id) ON DELETE RESTRICT,
    assignment      TEXT NOT NULL DEFAULT '',
    parent_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active       INTEGER NOT NULL DEFAULT 1,
    display_title   TEXT,
    is_sub_admin    INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_user_scope CHECK (
         (role IN ('BARANGAY_ADMIN','BARANGAY_STAFF')
            AND barangay_id IS NOT NULL AND station_id IS NULL)
      OR (role IN ('PNP_ADMIN','PNP_OFFICER')
            AND station_id  IS NOT NULL AND barangay_id IS NULL)
      OR (role = 'DEVTEAM'
            AND barangay_id IS NULL AND station_id IS NULL)
    )
);
"""

POST_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_users_parent ON users(parent_admin_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_barangay ON users(barangay_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_station ON users(station_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_barangay_admin_per_barangay "
    "ON users(barangay_id) WHERE role = 'BARANGAY_ADMIN'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pnp_admin_per_station "
    "ON users(station_id) WHERE role = 'PNP_ADMIN'",
]


def resolve_db_path():
    p = os.environ.get("SQLITE_PATH")
    if p:
        return p
    writable = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(
        os.path.expanduser("~"), "EcoVisionSentinelData"
    )
    return os.path.join(writable, "ecovision.db")


def has_column(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def plan_stations(conn):
    """Decides which stations to create and who lands in each.

    Returns (stations, assignments) where stations is {station_id: (name, [barangay_ids])}
    and assignments is {user_id: station_id}.
    """
    rows = conn.execute(
        "SELECT id, username, role, barangay_id FROM users WHERE role IN ('PRECINCT_CAPTAIN','POLICE')"
    ).fetchall()

    stations = {}
    assignments = {}
    orphans = []

    for uid, username, role, brgy in rows:
        if not brgy:
            # No barangay to derive a station from. Collect and handle below
            # rather than silently inventing scope for them.
            orphans.append((uid, username, role))
            continue
        sid = f"station-{brgy.lower()}"
        if sid not in stations:
            pretty = conn.execute(
                "SELECT name FROM barangays WHERE id = ?", (brgy,)
            ).fetchone()
            label = (pretty[0] if pretty else brgy).strip()
            stations[sid] = (f"{label} Police Station", [brgy])
        assignments[uid] = sid

    if orphans:
        # Fallback station covering every approved barangay -- being visible
        # everywhere is recoverable, being scoped to nothing is a lockout.
        sid = "station-default"
        all_brgy = [r[0] for r in conn.execute(
            "SELECT id FROM barangays WHERE status = 'approved'")]
        stations.setdefault(sid, ("Default Police Station", all_brgy))
        for uid, _, _ in orphans:
            assignments[uid] = sid

    return stations, assignments, orphans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", type=str, default=None)
    args = ap.parse_args()

    db_path = args.db or resolve_db_path()
    if not os.path.exists(db_path):
        sys.exit(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual transaction control

    if not table_exists(conn, "users"):
        sys.exit("No users table -- this looks like a fresh database; "
                 "schema_sqlite.sql already has the new shape.")

    if has_column(conn, "users", "station_id"):
        print("Already migrated (users.station_id exists). Nothing to do.")
        conn.close()
        return

    print(f"Database: {db_path}\n")

    # ── Report ──────────────────────────────────────────────────────────
    print("Role changes:")
    counts = conn.execute("SELECT role, COUNT(*) FROM users GROUP BY role").fetchall()
    for role, n in counts:
        new = ROLE_MAP.get(role)
        if new is None:
            conn.close()
            sys.exit(f"ABORT: unknown role {role!r} in database -- no mapping defined.")
        note = "  (barangay_id -> station_id)" if role in PNP_OLD else ""
        print(f"  {role:<18} -> {new:<16} x{n}{note}")

    stations, assignments, orphans = plan_stations(conn)
    print("\nStations to create:")
    if not stations:
        print("  (none -- no PNP-side users exist)")
    for sid, (name, brgys) in stations.items():
        print(f"  {sid:<20} {name:<28} jurisdiction: {', '.join(brgys) or '(empty)'}")
    if orphans:
        print("\n  NOTE: these PNP users had no barangay_id and went to the default station:")
        for uid, username, role in orphans:
            print(f"    id={uid} {role} {username}")

    if args.dry_run:
        print("\n--dry-run: no changes written.")
        conn.close()
        return

    # ── Backup ──────────────────────────────────────────────────────────
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{db_path}.pre_pnp_migration_{stamp}.bak"
    shutil.copy2(db_path, backup)
    print(f"\nBackup written: {backup}")

    # ── Migrate ─────────────────────────────────────────────────────────
    # FK enforcement must be off for the table-rebuild dance: other tables
    # reference users(id), and SQLite cannot ADD CONSTRAINT in place.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS police_stations (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS station_barangays (
                station_id  TEXT NOT NULL REFERENCES police_stations(id) ON DELETE CASCADE,
                barangay_id TEXT NOT NULL REFERENCES barangays(id)       ON DELETE CASCADE,
                PRIMARY KEY (station_id, barangay_id)
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_barangays_barangay "
                     "ON station_barangays(barangay_id)")

        for sid, (name, brgys) in stations.items():
            conn.execute("INSERT OR IGNORE INTO police_stations (id, name) VALUES (?, ?)", (sid, name))
            for b in brgys:
                conn.execute("INSERT OR IGNORE INTO station_barangays (station_id, barangay_id) "
                             "VALUES (?, ?)", (sid, b))

        conn.execute(NEW_USERS_DDL)

        rows = conn.execute(
            "SELECT id, username, password, role, barangay_id, assignment, parent_admin_id, "
            "created_at, is_active, display_title, is_sub_admin FROM users"
        ).fetchall()

        for (uid, username, password, role, brgy, assignment, parent, created,
             active, title, sub) in rows:
            new_role = ROLE_MAP[role]
            if new_role in ("PNP_ADMIN", "PNP_OFFICER"):
                new_brgy, new_station = None, assignments.get(uid)
                if new_station is None:
                    raise RuntimeError(f"user {uid} ({username}) is PNP-side but got no station")
            elif new_role == "DEVTEAM":
                new_brgy, new_station = None, None
            else:
                new_brgy, new_station = brgy, None
                if new_brgy is None:
                    raise RuntimeError(
                        f"user {uid} ({username}) maps to {new_role} but has no barangay_id; "
                        f"assign one before migrating")
            conn.execute(
                "INSERT INTO users_new (id, username, password, role, barangay_id, station_id, "
                "assignment, parent_admin_id, created_at, is_active, display_title, is_sub_admin) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, username, password, new_role, new_brgy, new_station, assignment,
                 parent, created, active, title, sub))

        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
        for stmt in POST_INDEXES:
            conn.execute(stmt)

        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"foreign_key_check failed after rebuild: {bad[:5]}")

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        print(f"\nMIGRATION FAILED, rolled back: {e}")
        print(f"Database is unchanged. Backup also available at: {backup}")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    # ── Verify ──────────────────────────────────────────────────────────
    print("\nResult:")
    for r in conn.execute(
            "SELECT id, username, role, barangay_id, station_id FROM users ORDER BY id"):
        print(f"  id={r[0]:<3} {r[2]:<16} brgy={str(r[3]):<10} station={str(r[4])}  {r[1]}")
    print("\nJurisdictions:")
    for r in conn.execute(
            "SELECT s.id, s.name, sb.barangay_id FROM police_stations s "
            "LEFT JOIN station_barangays sb ON sb.station_id = s.id ORDER BY s.id"):
        print(f"  {r[0]:<20} {r[1]:<28} -> {r[2]}")

    conn.close()
    print("\nMigration complete.")
    print("NOTE: role strings are embedded in issued JWTs -- every logged-in "
          "session is now invalid and all users must log in again.")


if __name__ == "__main__":
    main()
