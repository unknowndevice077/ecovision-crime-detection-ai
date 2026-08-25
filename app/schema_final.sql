-- ============================================================
-- EcoVision Sentinel — Schema v2 (normalized) — POSTGRES VERSION
-- ============================================================

CREATE TABLE IF NOT EXISTS barangays (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    lat         REAL,
    lng         REAL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by INTEGER,
    approved_by  INTEGER,
    approved_at  TIMESTAMPTZ
);

-- ── ORGANIZATIONS ────────────────────────────────────────────────────────
-- Two bodies operate over the same territory, and they are shaped
-- differently: a barangay IS one place, while a police station COVERS many.
-- That asymmetry is the whole reason the old 'PRECINCT_CAPTAIN' role was
-- broken -- it was keyed one-per-barangay, which is a barangay, not a
-- precinct.
--
-- NOTE ON OWNERSHIP: every physical/geographic row (cameras, incidents,
-- video_records, telemetry_readings) hangs off barangays and ONLY barangays.
-- A station owns no assets; station_barangays is a LENS that says which
-- barangays a station may see. Putting station_id on cameras or incidents
-- would create a second source of truth for "where is this" that drifts the
-- first time a jurisdiction is edited.
CREATE TABLE IF NOT EXISTS police_stations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The jurisdiction. Many-to-many on purpose: one station covers several
-- barangays, and a barangay could fall under more than one station
-- (co-located city/municipal units) without duplicating any asset rows.
CREATE TABLE IF NOT EXISTS station_barangays (
    station_id  TEXT NOT NULL REFERENCES police_stations(id) ON DELETE CASCADE,
    barangay_id TEXT NOT NULL REFERENCES barangays(id)       ON DELETE CASCADE,
    PRIMARY KEY (station_id, barangay_id)
);
-- Reverse lookup ("which stations cover this barangay") for scope checks.
CREATE INDEX IF NOT EXISTS idx_station_barangays_barangay
    ON station_barangays(barangay_id);

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    password        TEXT NOT NULL,
    -- role = organization x tier. Keeping it a flat enum (rather than two
    -- columns) preserves every existing `role IN (...)` query, while the
    -- naming now matches the actual org chart.
    role            TEXT NOT NULL CHECK (role IN
                        ('DEVTEAM','PNP_ADMIN','PNP_OFFICER','BARANGAY_ADMIN','BARANGAY_STAFF')),
    barangay_id     TEXT REFERENCES barangays(id) ON DELETE RESTRICT,
    station_id      TEXT REFERENCES police_stations(id) ON DELETE RESTRICT,
    assignment      TEXT NOT NULL DEFAULT '',
    parent_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active       INTEGER NOT NULL DEFAULT 1,
    display_title   TEXT,
    is_sub_admin    INTEGER NOT NULL DEFAULT 0,

    -- Scope is now a DATABASE invariant, not a convention the application
    -- layer is trusted to remember. Previously a PNP user needed a
    -- barangay_id to get any scope at all, so city-level officers were filed
    -- under an arbitrary barangay. This makes that state unrepresentable.
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

ALTER TABLE barangays ADD CONSTRAINT fk_barangays_requested_by
    FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE barangays ADD CONSTRAINT fk_barangays_approved_by
    FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL;

-- One admin per org unit. The PNP one is keyed on station_id, not
-- barangay_id -- that single change is what makes a precinct an actual
-- precinct instead of a second name for a barangay.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_barangay_admin_per_barangay
    ON users(barangay_id) WHERE role = 'BARANGAY_ADMIN';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pnp_admin_per_station
    ON users(station_id) WHERE role = 'PNP_ADMIN';

CREATE TABLE IF NOT EXISTS permission_keys (
    key     TEXT PRIMARY KEY,
    label   TEXT NOT NULL
);
INSERT INTO permission_keys (key, label) VALUES
    ('view_map', 'View Crime Map'),
    ('view_records', 'View Video Records'),
    ('view_history', 'View Crime History'),
    ('manage_cameras', 'Manage Cameras'),
    ('confirm_dismiss_alerts', 'Confirm / Dismiss Alerts'),
    ('manage_notify_targets', 'Manage Responder Notifications')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS user_permissions (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_key  TEXT NOT NULL REFERENCES permission_keys(key) ON DELETE CASCADE,
    granted_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online','offline')),
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cameras_barangay ON cameras(barangay_id);
-- backend.py filters cameras with WHERE LOWER(barangay_id) = ?, which a plain
-- btree index on barangay_id can't serve -- needs a matching expression index.
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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_incident_reports_incident ON incident_reports(incident_id);

CREATE TABLE IF NOT EXISTS incident_visibility (
    incident_id       TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    map_hidden        INTEGER NOT NULL DEFAULT 0,
    screenshot_path   TEXT,
    -- See video_records.sha256's comment below -- same purpose, for the snapshot.
    screenshot_sha256 TEXT
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
    barangay_id         TEXT REFERENCES barangays(id) ON DELETE SET NULL,
    -- Chain of custody (docs/incident_response_plan.md §3): SHA-256 of the
    -- file's bytes, computed once at the moment it's finalized on disk.
    -- Lets anyone verify later that a clip handed over is bit-for-bit what
    -- the detection system actually produced, not "trust me". NULL for rows
    -- written before this column existed.
    sha256              TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_incident ON video_records(associated_incident_id);
CREATE INDEX IF NOT EXISTS idx_records_recorded_at ON video_records(recorded_at);

-- Notification targets -- docs/incident_response_plan.md §2. Responders
-- (PNP officers, barangay tanod) to notify by SMS/Telegram when an incident
-- is confirmed-and-reported. Scoped like cameras and incidents: exactly one
-- of barangay_id/station_id is set, matching the barangay-owns-its-assets /
-- station-is-a-lens split documented on the cameras table above.
CREATE TABLE IF NOT EXISTS notify_targets (
    id           TEXT PRIMARY KEY,
    barangay_id  TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    station_id   TEXT REFERENCES police_stations(id) ON DELETE CASCADE,
    channel      TEXT NOT NULL CHECK (channel IN ('telegram','sms')),
    destination  TEXT NOT NULL,   -- Telegram chat_id, or a phone number for SMS
    label        TEXT,            -- e.g. "Tanod Patrol", "Duty Officer"
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notify_targets_barangay ON notify_targets(barangay_id);
CREATE INDEX IF NOT EXISTS idx_notify_targets_station ON notify_targets(station_id);

-- One row per attempted send -- so a failed notification is visible instead
-- of silently swallowed (docs/recovery_plan.md §7: "never let a failed
-- notification look identical to nothing happened").
CREATE TABLE IF NOT EXISTS notify_log (
    id           TEXT PRIMARY KEY,
    incident_id  TEXT REFERENCES incidents(id) ON DELETE CASCADE,
    target_id    TEXT REFERENCES notify_targets(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,
    destination  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('sent','failed','skipped_unconfigured')),
    error        TEXT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notify_log_incident ON notify_log(incident_id);

CREATE TABLE IF NOT EXISTS telemetry_readings (
    id          SERIAL PRIMARY KEY,
    barangay_id TEXT REFERENCES barangays(id) ON DELETE CASCADE,
    battery     REAL,
    solar_v     REAL,
    temp_cpu    REAL,
    temp_esp    REAL,
    temp_neural REAL,
    load_avg    REAL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_barangay_time ON telemetry_readings(barangay_id, recorded_at);