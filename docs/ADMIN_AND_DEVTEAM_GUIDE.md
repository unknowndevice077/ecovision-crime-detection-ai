# Admin and DevTeam guide — every function in the admin and developer panels

Written 22 Oct 2026 from the code (`app/routers/`, `app/components/dashboard/`,
`app/lib/permissions.ts`). It describes what each role can do, screen by screen,
and the rule behind each limit. For the detection side see
`detection_performance_report.md`; for the original design see
`USER_HIERARCHY_PLAN.md`.

---

## 1. Who exists

| Role | Belongs to | Created by | Purpose |
|---|---|---|---|
| **DEVTEAM** | nobody (sees everything) | the installer's first run (`devteam` account) | Runs the whole system: approves barangays and stations, creates any account, tunes permissions, manages AI models, audits everything |
| **BARANGAY_ADMIN** | one barangay | DevTeam, or self-signup approved by DevTeam | The barangay's head account: its cameras, its staff, its alerts. **One per barangay.** |
| **BARANGAY_STAFF** | the same barangay | their BARANGAY_ADMIN (or DevTeam) | Tanods and operators: watch feeds and confirm or dismiss alerts, within what the admin grants |
| **PNP_ADMIN** | one police station | DevTeam, or self-signup approved by DevTeam | The station's head account. **One per station.** |
| **PNP_OFFICER** | the same station | their PNP_ADMIN (or DevTeam) | Officers working the station's incidents, within what the admin grants |

An admin account creates **operators of its own side only** (a barangay admin
creates staff, a police admin creates officers) and the operator inherits the
admin's barangay or station. DevTeam is the only role that can create an admin
or an account for another organization.

### What each side can see (scoping)

One rule, applied to every list (cameras, incidents, recordings, notifications):

- **Barangay accounts** see only their own barangay.
- **Police accounts** see every barangay in their **station's jurisdiction**.
- **DevTeam** sees everything.

A barangay that no station covers is invisible to every police account, so
approving a barangay normally includes choosing its station.

Permissions are re-read from the database on **every request**, so removing a
permission or deleting an account takes effect immediately, not at next login.

---

## 2. Permissions

Six permissions can be switched per account. Admins hold most of them
automatically; operators hold only what is granted.

| Permission | Lets the account | Can be narrowed by |
|---|---|---|
| View Crime Map | open the Incident Map | camera, crime type |
| View Video Records | open Recordings | crime type |
| View Crime History | open the Incident Log (the archive of past incidents) | crime type |
| Manage Cameras | add, move and delete cameras | camera |
| Confirm / Dismiss Alerts | decide live alerts on the dashboard | crime type |
| Manage Responder Notifications | edit who gets Telegram/SMS alerts | channel |

**Hard rules the checkboxes cannot override** (enforced on the server, so a
stale grant or a bug in the screen cannot reopen them):

- **Manage Cameras is barangay-only.** The barangay installed the hardware;
  police only consume the feed. Not even a PNP_ADMIN gets it.
- **View Crime History and View Video Records are police-only.** Barangay
  accounts, admins included, never get the archive. A barangay keeps its live
  **Incident Queue** (Confirm / Dismiss) and asks police for a specific report
  through **Report Requests** (section 5).
- These two are greyed out in the screens for the account types that cannot hold them.

### Narrowing a permission

A grant can be limited along the dimensions in the table: "this officer sees
Camera A but not Camera B", "this staff member only sees assaults and
robberies". No selection on a dimension means everything. Barangay admins
narrow their own staff, within their own cameras; DevTeam can narrow anyone.

### Automatic vs explicit

An admin's permissions are **automatic** (everything their side may hold). When
DevTeam **overrides** an admin's permissions, the account flips to **explicit**:
only the ticked boxes apply from then on. The override asks DevTeam to confirm
with their own password, and is written to the audit log.

---

## 3. The DevTeam console

Two sections, each with its own tabs. The top of the screen shows the system
totals (organizations, accounts, cameras, open incidents).

### Monitoring (read-only views)

| Tab | What it shows |
|---|---|
| **Directory** | The whole organization tree: stations, the barangays each covers, and the people in each |
| **Users** | Every account's role, organization, last login and permission summary. No edit buttons here. |
| **Cameras** | Every camera across all barangays, with its barangay and status |
| **AI Models** | Health of each detector (see 3.1), and per-camera alert precision (see 3.2) |

### Configuration (the tabs that change things)

| Tab | What it does |
|---|---|
| **Manage Users** | Pick an account to edit its details, change its role or organization, reset its password, override its permissions, narrow its access to cameras or crime types, attach an ID or face photo, review its identity, or delete it |
| **Create User** | Create any account (admin or operator, either side) with a starting permission set or a custom role. A barangay account needs a barangay, a police account a station. |
| **Permissions** | Grant or revoke permissions narrowed to a camera, crime type or channel, for any account, with the current grants listed per user |
| **Approvals** | The queue of new barangays and police stations waiting for approval (see 3.3) |
| **Stations** | Police stations and which barangays each covers (see 3.4) |
| **Audit Log** | Who did what, when, with a Restore button for deletions (see 3.5) |

**Custom roles.** DevTeam can define a named preset (for example "Tanod
Patrol" or "Duty Officer"): a title plus a ready-made permission set, bound to
the barangay side, the police side, or neither. Creating a user with a custom
role gives them that title and those permissions. The account is still a real
BARANGAY_STAFF or PNP_OFFICER underneath, so every scoping and hard rule above
still applies, and a permission the account's side cannot hold is dropped.

**Identity verification.** Any account can have a government ID and a face
photo on file, stored on local disk and served only through a permission-checked
endpoint (never a public file path). A new ID resets the account to *pending*;
an admin reviews their own staff and DevTeam reviews anyone, then marks it
*verified* or *rejected*. Verification is a trust signal for admins and
DevTeam — it does not block login.

### 3.1 AI Models tab

Lists each detector (violence, weapons, robbery, vandalism, graffiti marks) and
the **alarm double-check**, with whether its weights file is present, whether
it is on, and its live settings.

- **Turn a detector on or off:** DevTeam *and* the barangay admin can do this,
  since the barangay owns the hardware it runs on. The AI core reads
  `config.json` once at start-up, so **a change applies after the detection is
  restarted**.
- **Change a threshold:** **DevTeam only.** The number is what the model's
  published accuracy was measured at, so it is not a per-site knob. The alarm
  double-check has no single threshold (its cut-offs are per task and were
  fitted on measured data), so the control is refused for it.
- **Optimize for this GPU:** compiles the models to TensorRT engines for the
  machine's own graphics card (optional; accuracy is unchanged, speed only),
  with **revert**, **cancel** and a live status. Engines are machine-specific
  and never shipped in the installer.
- **Per-camera switches and thresholds:** a camera can have individual models
  turned off or their thresholds adjusted, and the override removed again.

### 3.2 Alert precision

Per camera and per alert type over the last 30 days: how many alerts the AI
raised, how operators judged them (confirmed, dismissed, still pending,
re-typed), and the precision that gives. This is the live, own-camera
counterpart to the offline measurements, and it only improves as operators
decide alerts. Every Confirm or Dismiss is also saved as a labelled example
that `tools/export_feedback_dataset.py` can turn into training data.

### 3.3 Approvals — how a barangay or station gets onto the system

1. Someone fills in the **sign-up** form (organization details and an applicant account).
2. DevTeam sees it under **Approvals** as *pending*.
3. DevTeam **approves** it (optionally assigning the barangay to a police
   station in the same step), **rejects** it with a reason, or later **reopens**
   a decision. Approving a barangay approves its applicant account together.
4. Each decision asks for the DevTeam member's password again and is audited.

A pending or rejected barangay can never be covered by a station.

### 3.4 Stations and jurisdictions

- **Create a station** with its details (type, parent and regional office,
  commander, address, contact number, description). **Edit** them later.
- **Set its jurisdiction:** replace the list of barangays it covers. This is
  safe to shrink — removing a barangay only removes visibility; its cameras,
  incidents and recordings are never moved or deleted, because a station owns
  none of them.
- **Add a barangay** under a station directly (name, PSGC code, city,
  province, region, captain, hall address, contact, map position).
- **Delete a station.** Only its coverage is affected, for the same reason.

### 3.5 Audit Log

Every sensitive action is recorded with who did it, what it was done to, a
snapshot of the record, and the time (Manila): account created, edited or
deleted, permissions changed, approvals, camera and station changes, identity
reviews, and more. Filters: action, category, account, barangay or station,
date range and free text.

**Restore.** Deleting an account or an incident is a *soft delete* — it is
hidden, not erased. In the audit log, a delete entry whose target is still
deleted has a **Restore** button that brings back exactly that one record. It
is deliberately not a general "undo anything" button; an already-restored or
since-changed target is refused with the reason.

---

## 4. The barangay and police admin screens

An admin sees a smaller menu. What appears depends on role and permissions.

| Menu item | Barangay | Police | What it is |
|---|---|---|---|
| **Live Monitor** | yes | yes | Video wall of the cameras in scope, and the **Incident Queue** where alerts are confirmed or dismissed |
| **Incident Map** | yes | yes | Every alert pinned where it happened, filtered by date and type |
| **Cameras** | yes | no | Add, move (set its map position) and delete cameras; PTZ controls for cameras that support them |
| **Hardware** | yes | no | ESP32 smartpole status, the siren, and the panic button's recent presses |
| **Recordings** | no | yes | Saved footage with notes and clip export |
| **Incident Log** | no | yes | The archive of confirmed incidents, with the police report for each |
| **Report Requests** | yes | yes | See section 5 |
| **Personnel** | yes | yes | The admin's own team (below) |

### Personnel (the admin's team)

- **Create** a staff member or officer (full name, username, a starting
  password, permissions, optionally a custom role).
- **Edit permissions** for any of their own people, including narrowing them
  to specific cameras or crime types within the admin's own scope.
- **Reset a password** for their own people. An admin's own password cannot be
  reset here; only DevTeam can reset an admin's.
- **Delete** their own people (a soft delete; DevTeam can restore it).
- **Verify** their own people's ID.
- An admin can never touch accounts they did not create, or anyone outside
  their organization.

### Responder notifications

With the **Manage Responder Notifications** permission, a barangay or station
keeps its own list of people who are alerted: a name, a role label (for
example "Tanod Patrol", "Duty Officer"), the channel (**Telegram** or **SMS**)
and the destination. Each organization only sees and edits its own list. A new
confirmed alert is sent to every target on it.

### Cameras

Adding a camera takes a name, its stream address (RTSP or a device index) and a
map position. The stream address is hidden from anyone without Manage Cameras.
Deleting a camera removes its live feed from every dashboard; **its recordings
and incidents are kept**.

### Hardware and the siren

ESP32 smartpoles register themselves when they first call in. A **panic
button** press creates an incident immediately, grabs an evidence frame from the
camera, and appears on the dashboard like any alert. The siren has **activate**
and **deactivate** calls, and the dashboard's **Emergency Stop Siren** button
silences it. The siren reports whether the board was actually reached (sent,
skipped or unreachable) instead of assuming success.

---

## 5. Report Requests — barangay asks, police answers

Because barangays cannot open the police archive, they ask for what they need.

1. **Barangay** writes a request: a description of what is wanted, optionally
   tied to one of **its own** incidents (a request cannot be used to probe for
   incidents outside its jurisdiction).
2. **Police** (the station covering that barangay) sees it in its inbox and can
   **accept**, **decline** (with a note), or **fulfill** it.
3. **Fulfilling** hands something back: share a generated report (choosing which
   fields to share, with a preview of exactly what the barangay will see),
   attach files, or write the information. The barangay sees the outcome and
   anything shared.

Each side only sees its own requests, and the status moves
pending → accepted → fulfilled (or declined).

---

## 6. What happens to an alert (so you know what the panels are showing)

1. The AI core raises an alarm and, since 7 Oct, the Qwen3-VL-2B double-check
   confirms or drops it. A struggle that turns into a robbery within 60 seconds
   becomes one incident, not two.
2. The incident appears in the Incident Queue of the barangay (and the police
   station that covers it), with a snapshot and clip. Evidence is fingerprinted
   (SHA-256) when saved.
3. A person reviews it — **Confirm** or **Dismiss**. The AI never acts alone.
4. A confirmed incident is sent to the notification targets, and police can draft
   a report from it, with its fields pre-filled from the incident's own data.
5. Deleted incidents are hidden everywhere except the audit log's Restore.

---

## 7. Quick reference — who can do what

| Action | DevTeam | Admin (own org) | Operator |
|---|---|---|---|
| Approve a barangay or station | yes | no | no |
| Create an admin account | yes | no | no |
| Create an operator account | yes | yes, own side | no |
| Reset an operator's password | yes | yes, own people | no |
| Reset an admin's password | yes | no | no |
| Change an account's permissions | any | own people | no |
| Narrow access to cameras or crime types | any | own people | no |
| Define a custom role | yes | no | no |
| Add or delete cameras | yes | barangay only | with the permission |
| Turn a detector on or off | yes | barangay admin | no |
| Change a detection threshold | yes | no | no |
| Run the GPU optimizer | yes | no | no |
| Open the incident archive and recordings | yes | police only | police, with the permission |
| Confirm or dismiss alerts | yes | yes | with the permission |
| Edit responder notifications | any | own organization | with the permission |
| Send or answer a report request | — | barangay sends, police answers | per role |
| View the audit log and restore a deletion | yes | no | no |
