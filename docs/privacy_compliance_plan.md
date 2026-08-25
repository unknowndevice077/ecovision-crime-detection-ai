# Data privacy & compliance plan (RA 10173)

Written 2026-08-26. Covers what EcoVision Sentinel actually collects, what the
Philippine Data Privacy Act of 2012 (RA 10173) requires for that, and where the
system currently falls short of it. This is a pilot-readiness document, not a
retrospective — several items below are gaps, stated as gaps.

---

## 1. Why this applies at all

RA 10173 governs "personal information" — any data from which a living
individual is identifiable. CCTV footage of a public street qualifies the
moment a person in frame can be recognized, which is true of every camera this
system watches. Running detection models over that footage does not create a
new privacy question; the camera already did. What the *system* adds is
**automated processing and storage that persists** — clips, snapshots, and
incident narratives that outlive the moment, tied to a timestamp and a
location. That combination is exactly what the Act regulates.

Two things work in this system's favor and are worth stating plainly, because
they're a real answer to "does this identify people":

- **No facial recognition.** The pose/YOLO/X3D pipeline classifies *actions*
  (a person moving violently, a bag changing hands, a mark appearing on a
  wall) — it never extracts, stores, or matches a face embedding. Nothing in
  `maincode/main.py` produces a representation that could re-identify a
  specific person across cameras or sessions.
- **No biometric database.** There is no "who is this" step anywhere in the
  pipeline. An incident record names a *camera and a time*, not a person,
  until a human officer writes a narrative that might.

That distinction matters under RA 10173: biometric/facial data is treated as
more sensitive than ordinary CCTV footage, and this system was never designed
to process it. Say so directly if asked — it's a real, verifiable
architectural fact, not a compliance talking point.

---

## 2. What is actually collected and stored today

| Data | Where | Retention today |
|---|---|---|
| Live video frames | in-memory only, per camera process | not persisted |
| Alert clips (`AUTO_ASSAULT_*.mp4` etc.) | `static/recordings/`, `static/clips/` | **unbounded — nothing deletes these** |
| Alert snapshots | `static/screenshots/` | **unbounded** |
| Incident rows (case_id, officer, narrative, lat/lng, location_name, occurred_date/time, confidence) | `incidents` table, SQLite | **unbounded** |
| User accounts (role, barangay_id/station_id, credentials) | `users` table, SQLite | unbounded (expected — these are operator accounts, not data subjects) |

The unbounded rows are the compliance gap. RA 10173's proportionality
principle requires personal data be kept no longer than necessary for the
purpose it was collected for. Right now nothing in this codebase expires a
clip, a screenshot, or a dismissed incident — `logs/`, `static/screenshots/`,
`static/clips/`, `static/recordings/` are gitignored (correctly, they're
runtime output) but nothing *prunes* them at runtime either.

---

## 3. Legal basis for processing

RA 10173 §12/§13 requires a lawful basis before processing personal data.
For a barangay/PNP public-safety camera, the applicable bases are:

1. **Vested public interest / public order** (§12(e)) — crime prevention and
   response is a recognized basis without needing individual consent, which
   is the only realistic basis for a CCTV system (nobody in frame consented).
2. **Legal obligation** where the barangay or PNP has an ordinance or
   department order establishing the camera's public-safety purpose.

**Gap:** this only holds if the deployment is backed by an actual barangay
resolution/ordinance naming the camera(s), their purpose, and the retention
policy. That's a paperwork item outside this codebase, but it is the thing
that makes basis (1) actually apply rather than assumed. Confirm this exists
(or gets drafted) for the pilot barangay before go-live — a panel member with
any legal background will ask what authorizes the camera, not just the code.

---

## 4. Data subject rights, and their practical limits here

RA 10173 grants rights to access, correct, object to, and request erasure of
one's personal data. For a public-safety CCTV system these are real but
bounded:

- **Right to be informed** — physical signage at the camera site stating it's
  under surveillance for public safety, who operates it, and how to inquire.
  **Not part of this codebase** — a physical/site requirement for whoever
  installs the camera. Flag it as a pilot go-live checklist item.
- **Right to access** — a data subject who believes they appear in a
  retained clip can request it. There is currently no request-handling
  procedure (who receives it, how footage is located, what's redacted before
  release). Needs a barangay-level process, not a code change.
- **Right to erasure** — bounded by the retention/evidentiary need (§5 below).
  A confirmed incident tied to an active case cannot simply be deleted on
  request; a dismissed/false-positive clip can and, per the retention policy
  below, should be.

---

## 5. Recommended retention policy

No retention policy exists today. Proposed, matched to how the system already
distinguishes incident states (`status`: pending / confirmed / dismissed):

| Category | Retention | Rationale |
|---|---|---|
| Dismissed alert (operator marked false positive) | **7 days**, then auto-delete clip + snapshot + row | No evidentiary value once dismissed; short window covers an operator wanting to double-check their own call |
| Confirmed incident, no police report filed | **30 days** | Gives the barangay a window to escalate before the record ages out |
| Confirmed incident with a filed report (`confirm-and-report`) | **Retained per PNP records-retention rules**, not this system's default | Once it's evidence, retention is a legal question, not an engineering one — this system should stop being the authority on deletion at that point |
| Raw, never-alerted footage | **Not stored** (already true — only alert-triggered clips persist) | Matches the system's actual behavior today; worth stating as a positive, not just a gap |

**Not yet implemented.** This needs a scheduled job (daily, similar in shape
to the existing `optimize_weights` background task) that sweeps
`static/clips/`, `static/screenshots/`, `static/recordings/` and the
`incidents` table against these windows. Until it exists, say so plainly
rather than describing retention as something the system already does.

---

## 6. Security measures already in place (evidence for §20's "reasonable and appropriate" standard)

RA 10173 §20 requires organizational, physical, and technical security
measures proportional to the risk. What's already real in this codebase:

- JWT-based auth on every API route (`app/backend.py`).
- Role- and permission-gated access (`VALID_PERMISSION_KEYS`, `confirm_dismiss_alerts`,
  `manage_cameras`) — an operator cannot confirm/dismiss alerts or manage
  cameras outside their barangay/station scope (`apply_scope`).
- Devteam-only actions (model weight edits, threshold changes) separately
  gated from barangay/PNP roles (`MODEL_VIEW_ROLES` vs. `DEVTEAM`-only checks).
- Secrets excluded from version control (`.env`, `devteam_credentials.txt`
  in `.gitignore`), and a firmware credential leak was found and purged from
  git history this project (see `.gitignore`'s "Firmware" section) —
  worth mentioning as a real incident that was caught and fixed, not
  hypothetical.

**Gap:** no documented Data Protection Officer (DPO) or equivalent contact —
RA 10173 requires one for any entity processing personal data at this scale.
For a capstone pilot this can be the supervising faculty member or barangay
IT officer, but needs to be named somewhere, even just in this document.

---

## 7. Action items

| Item | Owner | Priority |
|---|---|---|
| Draft/confirm barangay ordinance or PNP authorization naming the camera's public-safety purpose | Barangay/PNP (not this codebase) | Before go-live |
| Install "under CCTV surveillance" signage at the camera site | Barangay | Before go-live |
| Name a DPO/privacy contact (can be a supervising role, not a new hire) | Barangay/school | Before go-live |
| Build the retention sweep job (§5 table) | Dev | High — this is the actual code gap |
| Write the data-subject-access-request procedure (who receives it, how footage is located) | Barangay, with dev support for the "how to find it" half | Medium |

---

## 8. What to say if asked at defense

*"Does this system do facial recognition?"* — No. It classifies actions, not
identities. There's no face embedding or biometric matching anywhere in the
pipeline — worth pointing at `maincode/main.py`'s model list (pose, weapon,
X3D action classifiers) directly if pressed.

*"What happens to a clip of someone who wasn't actually doing anything
wrong?"* — Today: it stays indefinitely, which is the gap this document
exists to fix. The honest answer is the retention policy above is designed
but not yet built — say that, don't claim it's done.

*"Who authorizes this camera to record people?"* — Points to a barangay
ordinance/PNP authorization that needs to exist outside the code — confirm
this is real before claiming it in the defense.
