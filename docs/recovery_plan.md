# Recovery plan — what to do when a piece of this breaks

Written 2026-08-26. Operational procedures for failure modes, written so a
non-developer running the pilot (a barangay IT aide, an on-site officer) can
follow them, with the developer-facing detail underneath for anything that
needs a code fix rather than a restart.

No real site network/hardware specifics are in this file by design — see
`docs/local-only/deployment_plan.html` for that; this file is the generic
procedure, safe to keep in the public repo.

---

## 1. Already fixed this session — process orphaning / crash-on-relaunch

**Symptom (real, observed):** Optimize weights → Cancel → close app → reopen
→ the whole machine crashed.

**Root cause:** Electron's Python children (`backend.py`, `main.py`) could
survive an abnormal Electron shutdown. Reopening the app spawned a *second*
set of the same processes, which meant two full sets of GPU models loading
onto a 6GB card at once — VRAM exhaustion took the machine down, not just
the app.

**Fix, already shipped:** every spawned Python child now gets
`ECOVISION_PARENT_PID` and runs a watchdog thread
(`port_utils.start_parent_watchdog`) that polls whether that PID is still
alive and self-terminates (`os._exit(1)`) the moment it isn't. Verified with
a real dead-PID test, and the watchdog's own bug (an emoji crashing the print
statement and silently killing the thread) was found and fixed too.

**If this ever recurs anyway:** check Task Manager for orphaned `python.exe`
processes after closing the app. If any remain, the watchdog isn't reaching
that code path — check `ECOVISION_PARENT_PID` is actually set in the child's
environment (`electron/main.js`'s `spawnPython()`).

---

## 2. Camera feed drops

**Symptom:** video wall tile goes black / frozen; `main.py` log shows
repeated `VideoCapture` failures.

**Current behavior:** `main.py` already retries opening the capture source
(see the reconnect loop around `cv2.VideoCapture()` in `maincode/main.py`).
This handles a *transient* drop (brief network hiccup, camera reboot).

**What to check if it doesn't recover on its own:**
1. Is the camera reachable at all (ping it / open its RTSP URL directly)?
2. Is `CAMERA_SOURCE` / the configured camera URL still correct — did the
   camera's IP change (common on a DHCP network with no reservation)?
3. Use `/set_camera_index` (or the barangay admin's camera-source UI) to
   point at a known-good source and confirm the pipeline itself is healthy,
   isolating whether the problem is the camera or the app.

**Gap, not yet built:** there's no operator-facing alert when a feed has been
down for an extended period — right now a dead camera and a quiet street
look identical in the UI unless someone is watching that tile. Worth a
"camera offline > N minutes" banner, tracked as a follow-up, not solved here.

---

## 3. Backend or AI-core process dies while Electron keeps running

**Symptom:** dashboard stops updating, alerts stop arriving, but the app
window is still open.

**Current behavior:** the parent watchdog (§1) only handles Electron dying
*first*. It does not currently cover the reverse — a Python child crashing
while Electron is still alive. Electron does not auto-restart a crashed
child today.

**What to do now:** close and reopen the app. The watchdog guarantees no
orphan is left behind first, so this is safe.

**Recommended fix, not yet built:** an auto-restart-with-backoff in
`electron/main.js`'s child process `exit` handler — if `backend.py` or
`main.py` exits unexpectedly (non-zero, not from a deliberate shutdown),
relaunch it after a short delay, capped at a few attempts before surfacing a
visible error instead of restart-looping forever.

---

## 4. Database issues (SQLite)

**Symptom:** API calls fail with a database-locked or corruption error.

**Immediate:**
1. Stop the app (releases any open SQLite handle).
2. Copy the `.db` file elsewhere before touching anything — don't
   troubleshoot on the only copy.
3. `sqlite3 <file> "PRAGMA integrity_check;"` — if it reports anything other
   than `ok`, the file is genuinely corrupted, not just locked.

**Recommended, not yet built:** a scheduled nightly copy of the database file
to a separate location (even just a dated copy alongside it). There is
currently no backup cadence for the one file every incident, user, and
camera record lives in. This is the single highest-value gap in this whole
document — everything else here is a recoverable inconvenience; losing the
DB with no backup is not.

---

## 5. Disk fills up

**Symptom:** clip saving starts failing; eventually the whole app misbehaves
once the OS itself is low on space.

**Cause:** `static/recordings/`, `static/clips/`, `static/screenshots/`
currently grow forever — this is the same gap flagged in
`docs/privacy_compliance_plan.md` §5 (no retention sweep exists yet), and
it's a reliability problem as much as a compliance one.

**Until the retention job exists:** periodically check free space and
manually clear old dismissed-incident clips. Never delete a clip tied to a
confirmed incident with a filed report without following whatever
evidentiary-retention rule applies to that case.

---

## 6. GPU/VRAM exhaustion under normal operation

**Symptom:** model loading fails or inference throws a CUDA out-of-memory
error, without the crash-on-relaunch scenario in §1 (i.e. this happens during
ordinary running, not after a bad reopen).

Measured headroom on the pilot's GTX 1660 SUPER (6GB): a single camera's
`main.py` process (7 models loaded — 3 YOLO detectors + 4 X3D-XS classifiers)
uses **~400MB VRAM** and **~2.2GB system RAM** at steady state, briefly
hitting ~58% GPU compute during an active detection burst. One camera has
comfortable headroom. See `docs/scaling_plan.md` before adding a second
camera to the same machine — the compute ceiling is tighter than the VRAM
one.

---

## 7. Notification channel down (Telegram/SMS gateway outage)

Relevant once `docs/incident_response_plan.md`'s notification system is
built. If the SMS gateway or Telegram Bot API is unreachable:

- The in-app dashboard alert must still fire regardless — notification
  delivery must never gate whether the incident record itself gets created.
- Log the failed send with the incident's case_id so it can be identified and
  manually escalated later; never let a failed notification look identical
  to "nothing happened."
- Operators need to know a channel is down — a status indicator in the
  DevteamView or AdminUsersView, not a silent failure in a log file nobody's
  watching.

---

## 8. Full machine loss at the deployment site

**Symptom:** the machine running the pilot dies (hardware failure, theft,
whatever) — a real risk for a machine sitting at a public camera site.

**What's needed to actually recover from this, and isn't fully in place yet:**

| Needed | Status |
|---|---|
| A copy of the installer (`dist_installer/`) stored somewhere other than the dead machine | Manual — confirm one exists off-site |
| A copy of `weights/` (the trained models — not in git, ~400MB) | Manual — confirm one exists off-site |
| A recent database backup | **Does not exist yet** — see §4 |
| A copy of the per-machine `config.json`/writable-config overlay | Manual — confirm one exists off-site |

Until the DB backup in §4 exists, a full machine loss means losing every
incident record the pilot has generated. Worth fixing before go-live, not
after the first real incident.

---

## Priority order

1. **Database backup cadence (§4)** — nothing else here matters if this is
   still missing when a real failure happens.
2. **Retention sweep (§5 / privacy plan §5)** — reliability and compliance
   both depend on it.
3. **Auto-restart-with-backoff for crashed children (§3)** — turns "close and
   reopen the app" into something that doesn't need a human present.
4. Everything else is already either fixed (§1, §6) or a documented manual
   procedure that's fine for a single-camera pilot's scale.
