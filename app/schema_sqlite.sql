-- ============================================================
-- EcoVision Sentinel — Schema v2 (normalized) — SQLITE VERSION
-- Used only when DATABASE_URL is unset (standalone installer build).
-- Mirrors schema_final.sql (Postgres) field-for-field; differences:
--   SERIAL          -> INTEGER PRIMARY KEY AUTOINCREMENT
--   TIMESTAMPTZ     -> TEXT (ISO8601 string)
--   NOW()           -> CURRENT_TIMESTAMP (handled by db.py at execute time)
--   circular FK (barangays.requested_by/approved_by -> users.id) is left
--   unenforced here (SQLite ALTER TABLE can't add FK constraints after
--   creation) -- functionally harmless since the app never relies on the
--   DB rejecting a bad requested_by/approved_by value.
-- ============================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS barangays (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    lat         REAL,
    lng         REAL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by INTEGER,
    approved_by  INTEGER,
    approved_at  TEXT
);

-- ── ORGANIZATIONS ────────────────────────────────────────────────────────
-- See schema_final.sql for the full rationale. Short version: a barangay IS
-- one place, a police station COVERS many, and that asymmetry is why the old
-- one-PRECINCT_CAPTAIN-per-barangay index was wrong.
--
-- OWNERSHIP RULE: cameras, incidents, video_records and telemetry_readings
-- hang off barangays and only barangays. A station owns no assets --
-- station_barangays is a lens granting visibility, not a parent link.
CREATE TABLE IF NOT EXISTS police_stations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS station_barangays (
    station_id  TEXT NOT NULL REFERENCES police_stations(id) ON DELETE CASCADE,
    barangay_id TEXT NOT NULL REFERENCES barangays(id)       ON DELETE CASCADE,
    PRIMARY KEY (station_id, barangay_id)
);
CREATE INDEX IF NOT EXISTS idx_station_barangays_barangay
    ON station_barangays(barangay_id);

CREATE TABLE IF NOT EXISTS users (
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

    -- Scope as a database invariant. SQLite enforces CHECK the same as
    -- Postgres here, so a malformed user is rejected on both backends
    -- rather than only on the "real" one.
    CONSTRAINT chk_user_scope CHECK (
         (role IN ('BARANGAY_ADMIN','BARANGAY_STAFF')
            AND barangay_id IS NOT NULL AND station_id IS NULL)
      OR (role IN ('PNP_ADMIN','PNP_OFFICER')
            AND station_id  IS NOT NULL AND barangay_id IS NULL)
      OR (role = 'DEVTEAM'
            AND barangay_id IS NULL AND station_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_users_parent ON users(parent_admin_id);
CREATE INDEX IF NOT EXISTS idx_users_barangay ON users(barangay_id);
CREATE INDEX IF NOT EXISTS idx_users_station ON users(station_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_barangay_admin_per_barangay
    ON users(barangay_id) WHERE role = 'BARANGAY_ADMIN';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pnp_admin_per_station
    ON users(station_id) WHERE role = 'PNP_ADMIN';

CREATE TABLE IF NOT EXISTS permission_keys (
    key     TEXT PRIMARY KEY,
    label   TEXT NOT NULL
);
INSERT OR IGNORE INTO permission_keys (key, label) VALUES
    ('view_map', 'View Crime Map'),
    ('view_records', 'View Video Records'),
    ('view_history', 'View Crime History'),
    ('manage_cameras', 'Manage Cameras'),
    ('confirm_dismiss_alerts', 'Confirm / Dismiss Alerts');

CREATE TABLE IF NOT EXISTS user_permissions (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_key  TEXT NOT NULL REFERENCES permission_keys(key) ON DELETE CASCADE,
    granted_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    granted_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online','offline')),
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cameras_barangay ON cameras(barangay_id);
-- backend.py filters cameras with WHERE LOWER(barangay_id) = ?, which a plain
-- index on barangay_id can't serve -- needs a matching expression index.
CREATE INDEX IF NOT EXISTS idx_cameras_barangay_lower ON cameras(LOWER(barangay_id));

CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    case_id         TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status          TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active','Confirmed','Dismissed')),
    lat             REAL,
    lng             REAL,
    location_name   TEXT,
    occurred_date   TEXT NOT NULL,
    occurred_time   TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    officer         TEXT,
    barangay_id     TEXT REFERENCES barangays(id) ON DELETE RESTRICT,
    source          TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN
                        ('MANUAL','AI_AUTOMATION','HARDWARE_PANIC')),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_incidents_barangay_date ON incidents(barangay_id, occurred_date);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
-- backend.py's get_incidents filters with WHERE LOWER(barangay_id) = ?
CREATE INDEX IF NOT EXISTS idx_incidents_barangay_lower ON incidents(LOWER(barangay_id));

CREATE TABLE IF NOT EXISTS incident_details (
    incident_id         TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    narrative           TEXT,
    nature_of_call      TEXT,
    arrival_reason      TEXT,
    additional_officers TEXT
);

CREATE TABLE IF NOT EXISTS incident_reports (
    id                  TEXT PRIMARY KEY,
    incident_id         TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    reported_by         INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    narrative           TEXT,
    nature_of_call      TEXT,
    arrival_reason      TEXT,
    additional_officers TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_incident_reports_incident ON incident_reports(incident_id);

CREATE TABLE IF NOT EXISTS incident_visibility (
    incident_id     TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    map_hidden      INTEGER NOT NULL DEFAULT 0,
    screenshot_path TEXT
);

CREATE TABLE IF NOT EXISTS video_records (
    id                  TEXT PRIMARY KEY,
    filename            TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    duration            TEXT,
    type                TEXT NOT NULL CHECK (type IN ('CLIP','FULL_24_7','CRIME_CLIP')),
    associated_incident_id TEXT REFERENCES incidents(id) ON DELETE SET NULL,
    crime_time_marker   TEXT,
    notes               TEXT,
    barangay_id         TEXT REFERENCES barangays(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_records_incident ON video_records(associated_incident_id);
CREATE INDEX IF NOT EXISTS idx_records_recorded_at ON video_records(recorded_at);

CREATE TABLE IF NOT EXISTS telemetry_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    battery     REAL,
    solar_v     REAL,
    temp_cpu    REAL,
    temp_esp    REAL,
    temp_neural REAL,
    load_avg    REAL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telemetry_barangay_time ON telemetry_readings(barangay_id, recorded_at);