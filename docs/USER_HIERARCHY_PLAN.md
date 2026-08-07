# User Hierarchy Redesign — Plan

Status: **proposal, nothing implemented.** Written for review.

Decisions already made:
- PNP scope = **explicit jurisdiction table** (a station covers a set of barangays)
- Camera management = **barangay only**
- Plan reviewed before any code changes

---

## 1. What's wrong today

### 1.1 "Precinct" is modelled as a barangay

```sql
CREATE UNIQUE INDEX idx_one_precinct_captain_per_barangay
    ON users(barangay_id) WHERE role = 'PRECINCT_CAPTAIN';
```

One precinct captain **per barangay**. A real Police Community Precinct covers
*several* barangays. The name and the constraint disagree, which is the tell
that the model is wrong.

Consequence: a city-level PNP officer must be filed under one arbitrary
barangay, because `barangay_id` is their only source of scope.

### 1.2 A police user's jurisdiction changes between screens

| endpoint | scoping for `POLICE` | result |
|---|---|---|
| `get_incidents` (backend.py:660) | only `BARANGAY`/`BARANGAY_CAPTAIN` are scoped; police fall through to `else` | sees **all** barangays |
| `get_cameras` (backend.py:586) | scoped by `barangay_id` for everyone but DEVTEAM | sees **one** barangay |

Same user, same session, two different jurisdictions. This is the org/scope
confusion leaking into runtime behaviour.

### 1.3 PNP can delete cameras a barangay owns

`require_permission` (backend.py:605) returns early for `DEVTEAM` and both
admin roles, so `manage_cameras` is never actually checked for a
`PRECINCT_CAPTAIN`. The barangay paid for the smartpoles; PNP should consume
the feed, not administer the deployment.

### 1.4 `role` conflates three independent concepts

`role` currently encodes **organization** and **tier** together, while scope
lives in `barangay_id` and capability lives in `user_permissions`. Adding a
tier later would mean inventing N×M new role strings.

---

## 2. Target model

### 2.1 The grid

Roles stay a 2×2 of *organization* × *tier*, plus the system account:

|  | ADMIN | OPERATOR |
|---|---|---|
| **Barangay** | `BARANGAY_ADMIN` | `BARANGAY_STAFF` |
| **PNP** | `PNP_ADMIN` | `PNP_OFFICER` |

Plus `DEVTEAM` (unscoped, system-wide).

### 2.2 Schema

```sql
CREATE TABLE IF NOT EXISTS police_stations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The jurisdiction: which barangays this station is responsible for.
CREATE TABLE IF NOT EXISTS station_barangays (
    station_id  TEXT NOT NULL REFERENCES police_stations(id) ON DELETE CASCADE,
    barangay_id TEXT NOT NULL REFERENCES barangays(id)       ON DELETE CASCADE,
    PRIMARY KEY (station_id, barangay_id)
);
CREATE INDEX IF NOT EXISTS idx_station_barangays_barangay
    ON station_barangays(barangay_id);
```

`users` changes:

```sql
role  TEXT NOT NULL CHECK (role IN
        ('DEVTEAM','PNP_ADMIN','PNP_OFFICER','BARANGAY_ADMIN','BARANGAY_STAFF'))

station_id TEXT REFERENCES police_stations(id) ON DELETE RESTRICT   -- NEW
-- barangay_id stays, but its meaning is now enforced:

CONSTRAINT chk_user_scope CHECK (
     (role IN ('BARANGAY_ADMIN','BARANGAY_STAFF')
        AND barangay_id IS NOT NULL AND station_id IS NULL)
  OR (role IN ('PNP_ADMIN','PNP_OFFICER')
        AND station_id  IS NOT NULL AND barangay_id IS NULL)
  OR (role = 'DEVTEAM'
        AND barangay_id IS NULL AND station_id IS NULL)
)
```

That `CHECK` is the point of the whole exercise: **the database now refuses to
create a malformed user.** A PNP officer can no longer be filed under a
barangay, and a barangay staffer can no longer be left unscoped.

One admin per org unit:

```sql
DROP INDEX IF EXISTS idx_one_precinct_captain_per_barangay;
DROP INDEX IF EXISTS idx_one_barangay_captain_per_barangay;

CREATE UNIQUE INDEX idx_one_barangay_admin
    ON users(barangay_id) WHERE role = 'BARANGAY_ADMIN';
CREATE UNIQUE INDEX idx_one_pnp_admin
    ON users(station_id)  WHERE role = 'PNP_ADMIN';
```

### 2.3 The scope rule

One rule, applied identically to **cameras, incidents, recordings, and
telemetry** — this is what fixes §1.2:

```sql
-- BARANGAY_ADMIN / BARANGAY_STAFF
WHERE LOWER(barangay_id) = :user_barangay

-- PNP_ADMIN / PNP_OFFICER
WHERE barangay_id IN (
    SELECT barangay_id FROM station_barangays WHERE station_id = :user_station
)

-- DEVTEAM
-- no filter
```

Implement once as a helper (`scope_clause(payload) -> (sql_fragment, params)`)
and call it from every scoped query, rather than re-deriving per endpoint.
The per-endpoint duplication is exactly how §1.2 happened.

---

## 3. Permission matrix

`view` = always; `grant` = off by default, an admin can grant it; `—` = never.

| capability | DEVTEAM | PNP_ADMIN | PNP_OFFICER | BRGY_ADMIN | BRGY_STAFF |
|---|---|---|---|---|---|
| View live cameras | all | jurisdiction | jurisdiction | own brgy | own brgy |
| **Manage cameras** | all | **—** | **—** | **own brgy** | grant |
| View incident map | all | jurisdiction | grant | own brgy | grant |
| Full incident detail (PII) | yes | yes | yes | redacted | redacted |
| Confirm / dismiss alerts | yes | yes | grant | own brgy | grant |
| File official police report | yes | yes | yes | — | — |
| View recordings | all | jurisdiction | grant | own brgy | grant |
| Create users | any | `PNP_OFFICER` | — | `BARANGAY_STAFF` | — |
| Approve barangays | yes | — | — | — | — |
| Manage stations / jurisdiction | yes | — | — | — | — |

Two changes from today's behaviour:
- **`manage_cameras` moves to barangay only** (fixes §1.3). Requires removing
  the blanket admin bypass in `require_permission` for that one key.
- **Redaction policy is unchanged** — barangay sees redacted, PNP sees full
  detail. That is deliberate and correct: police need names and narrative for
  investigation, barangay officials get less PII.

---

## 4. Migration

### 4.1 The one genuinely tricky part

Today there is **one `PRECINCT_CAPTAIN` per barangay**. If three barangays
each have one and they all migrate into a single station, three users become
`PNP_ADMIN` for that station — violating `idx_one_pnp_admin`.

**Proposed default (non-destructive):** create one station per existing
precinct captain, named after their barangay, with jurisdiction initially set
to just that barangay. Nobody is demoted and nothing is lost. You then merge
stations and widen jurisdictions through the DEVTEAM UI, which is a
deliberate act rather than a silent migration side effect.

**This needs your confirmation before I write the migration** — the
alternative (promote one, demote the rest to `PNP_OFFICER`) is destructive
and I would not do it without you saying so.

### 4.2 Role mapping

| old | new | notes |
|---|---|---|
| `DEVTEAM` | `DEVTEAM` | unchanged |
| `PRECINCT_CAPTAIN` | `PNP_ADMIN` | gets `station_id`; `barangay_id` cleared |
| `POLICE` | `PNP_OFFICER` | gets `station_id`; `barangay_id` cleared |
| `BARANGAY_CAPTAIN` | `BARANGAY_ADMIN` | unchanged scope |
| `BARANGAY` | `BARANGAY_STAFF` | unchanged scope |

### 4.3 Order of operations

1. Back up the DB file (SQLite) / `pg_dump` (Postgres).
2. Create `police_stations` + `station_barangays`.
3. Add `users.station_id` (nullable, no constraint yet).
4. Seed one station per existing precinct captain's barangay; populate
   `station_barangays`.
5. Rewrite `users.role` per §4.2; set `station_id`; null out `barangay_id`
   for PNP rows.
6. Drop old unique indexes, add new ones.
7. **Add `chk_user_scope` last** — it will fail loudly if step 5 missed a row,
   which is exactly what you want as a verification gate.
8. Re-issue tokens (role strings are embedded in the JWT payload — every
   logged-in session becomes invalid, so all users must log in again).

Step 8 is easy to overlook and will look like a mass outage if unannounced.

---

## 5. Code changes

39 references across 8 files.

| file | what changes |
|---|---|
| `app/schema_final.sql` | new tables, `station_id`, `CHECK`, index swap |
| `app/schema_sqlite.sql` | same (SQLite has no `ALTER … ADD CONSTRAINT`; needs table rebuild) |
| `app/backend.py` | `ADMIN_ROLES`/`STANDARD_ROLES`/`ADMIN_CREATES_ROLE`/`POLICE_SIDE_ROLES` sets; new `scope_clause()` helper; apply to cameras/incidents/records/telemetry; `manage_cameras` bypass removal; station CRUD endpoints |
| `app/types.ts` | `UserRole` union, `ADMIN_ROLES`, `STANDARD_ROLES` |
| `app/hooks/usePermissions.ts` | admin-implies-all list; `manage_cameras` no longer implied for PNP |
| `app/page.tsx` | `isPolice` / `isBarangay` derivations (lines 342-343), nav gating |
| `app/components/dashboard/DevteamView.tsx` | 16 refs — role dropdowns, filters, user creation |
| `app/loginpage/signup/page.tsx` | admin role options |

**New UI required:** DEVTEAM needs a station manager (create station, assign
barangays to its jurisdiction). Without it, stations can only be edited by
hand in SQL. Roughly one new view comparable in size to `AdminUsersView`.

---

## 6. Risks

| risk | mitigation |
|---|---|
| Migration mis-assigns a user's scope | `chk_user_scope` added last fails loudly rather than silently mis-scoping |
| Everyone logged out at once | Expected — announce it; it's step 8, not a bug |
| SQLite constraint changes need table rebuild | Do it as create-new → copy → drop-old → rename, inside one transaction |
| Scope helper missed on an endpoint | Grep for every `barangay_id` filter and route it through `scope_clause()`; add a test that each scoped endpoint returns nothing cross-jurisdiction |
| Rollback | DB backup from step 1; code is one revert |

---

## 7. Open questions

1. **§4.1** — confirm the non-destructive default (one station per existing
   precinct captain) versus promoting one and demoting the rest.
2. How many police stations realistically? If the answer is permanently one,
   `station_barangays` still works but the station manager UI could be
   deferred and seeded in SQL instead.
3. Should `BARANGAY_ADMIN` see incidents from *neighbouring* barangays in the
   same station's jurisdiction (read-only), or strictly their own? Plan
   currently assumes strictly their own.
