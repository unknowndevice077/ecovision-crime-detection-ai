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