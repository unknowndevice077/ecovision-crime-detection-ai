-- ============================================================
-- EcoVision Sentinel — Schema v2 (normalized)
-- SQLite. Run against a fresh DB or via the migration notes below.
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;  -- huge win once the recording engine writes
                            -- frames on a background thread while API
                            -- requests hit the DB concurrently.

-- ---------------------------------------------------------------
-- Locations. "barangayId" was a free-text string everywhere before
-- (lowercased by convention, never enforced). Promote it to a real
-- table so a typo can't silently create a phantom location.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS barangays (
    id          TEXT PRIMARY KEY,       -- slug, e.g. 'cogon'
    name        TEXT NOT NULL,          -- display name, e.g. 'Cogon'
    lat         REAL,
    lng         REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- Users — same shape as before, but FK'd to barangays and to itself
-- (parentAdminId), with proper ON DELETE behavior instead of silent
-- orphaning.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password        TEXT NOT NULL,           -- salt$pbkdf2hash, unchanged
    role            TEXT NOT NULL CHECK (role IN
                        ('DEVTEAM','PRECINCT_CAPTAIN','BARANGAY_CAPTAIN','POLICE','BARANGAY')),
    barangay_id     TEXT REFERENCES barangays(id) ON DELETE RESTRICT,
    assignment      TEXT NOT NULL DEFAULT '',
    parent_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    is_active       INTEGER NOT NULL DEFAULT 1   -- soft-disable instead of delete
);
CREATE INDEX IF NOT EXISTS idx_users_parent ON users(parent_admin_id);
CREATE INDEX IF NOT EXISTS idx_users_barangay ON users(barangay_id);

-- ---------------------------------------------------------------
-- Permissions — normalized instead of a JSON blob column. Lets you
-- query "who can manage_cameras" directly, add a new permission
-- without touching every existing row, and enforce the key set at
-- the DB level (FK) instead of trusting whatever string the frontend sent.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS permission_keys (
    key     TEXT PRIMARY KEY,      -- 'view_map', 'manage_cameras', etc.
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
    granted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, permission_key)
);

-- ---------------------------------------------------------------
-- Cameras
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,        -- consider encrypting at rest; see notes
    status      TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online','offline')),
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cameras_barangay ON cameras(barangay_id);

-- ---------------------------------------------------------------
-- Incidents split into 3 tables instead of one 18-column wide table:
--   incidents          -> stable core fields, queried constantly
--   incident_details    -> narrative/report fields, redacted per-role
--   incident_visibility -> map/history display flags
-- This means adding a new AI-metadata field or a new redaction rule
-- never requires touching the hot core table again.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    case_id         TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,             -- ASSAULT, VIOLENCE, FIREARM_DETECTION, etc.
    severity        TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status          TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active','Confirmed','Dismissed')),
    lat             REAL,
    lng             REAL,
    location_name   TEXT,
    occurred_date   TEXT NOT NULL,             -- 'YYYY-MM-DD'
    occurred_time   TEXT NOT NULL,             -- 'HH:MM:SS'
    confidence      REAL DEFAULT 1.0,
    officer         TEXT,
    barangay_id     TEXT REFERENCES barangays(id) ON DELETE RESTRICT,
    source          TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN
                        ('MANUAL','AI_AUTOMATION','HARDWARE_PANIC')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_incidents_barangay_date ON incidents(barangay_id, occurred_date);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);

CREATE TABLE IF NOT EXISTS incident_details (
    incident_id         TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    narrative           TEXT,
    nature_of_call      TEXT,
    arrival_reason      TEXT,
    additional_officers TEXT
);

CREATE TABLE IF NOT EXISTS incident_visibility (
    incident_id     TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    map_hidden      INTEGER NOT NULL DEFAULT 0,   -- hides from Map view only; History always sees it
    screenshot_path TEXT
);

-- ---------------------------------------------------------------
-- Video records — FK'd to incidents properly instead of a loose
-- associatedCrimeId string that could point to nothing.
-- ---------------------------------------------------------------
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

-- ---------------------------------------------------------------
-- Telemetry — currently mocked client-side per session; give it a
-- real table so Hardware Status can show trend, not just "now".
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    battery     REAL,
    solar_v     REAL,
    temp_cpu    REAL,
    temp_esp    REAL,
    temp_neural REAL,
    load_avg    REAL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_telemetry_barangay_time ON telemetry_readings(barangay_id, recorded_at);
-- ============================================================
-- Additions to schema_v2.sql for the per-location admin model:
--   - a barangay must be DEVTEAM-approved before its admins can log in
--   - exactly one PRECINCT_CAPTAIN and one BARANGAY_CAPTAIN per barangay
--   - primary admins can create named sub-admins with a custom title,
--     not just fixed POLICE/BARANGAY accounts
-- Run this AFTER schema_v2.sql, against the same db.
-- ============================================================

-- ---------------------------------------------------------------
-- 1. Location approval gate
-- ---------------------------------------------------------------
ALTER TABLE barangays ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected'));
ALTER TABLE barangays ADD COLUMN requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE barangays ADD COLUMN approved_by  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE barangays ADD COLUMN approved_at  TEXT;

-- ---------------------------------------------------------------
-- 2. Exactly one PRECINCT_CAPTAIN and one BARANGAY_CAPTAIN per
-- barangay -- a partial unique index, not just app-level checking.
-- A second signup attempt for an already-claimed location fails at
-- the DB level, full stop.
-- ---------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_precinct_captain_per_barangay
    ON users(barangay_id) WHERE role = 'PRECINCT_CAPTAIN';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_barangay_captain_per_barangay
    ON users(barangay_id) WHERE role = 'BARANGAY_CAPTAIN';

-- ---------------------------------------------------------------
-- 3. Sub-admins -- a primary admin (PRECINCT_CAPTAIN / BARANGAY_CAPTAIN)
-- can create an account with a freely chosen display title
-- ("Assistant Captain", "Records Officer", whatever they want to call
-- it) that still carries the underlying POLICE/BARANGAY role for
-- routing purposes, but gets its OWN permission set via
-- user_permissions rather than inheriting the parent's blindly.
-- The `is_sub_admin` flag distinguishes "an admin-created helper with
-- elevated permissions" from a plain frontline POLICE/BARANGAY account
-- in the UI, without needing a whole new role enum value.
-- ---------------------------------------------------------------
ALTER TABLE users ADD COLUMN display_title TEXT;       -- e.g. "Assistant Captain"; NULL = use role default
ALTER TABLE users ADD COLUMN is_sub_admin  INTEGER NOT NULL DEFAULT 0;