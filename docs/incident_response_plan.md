# Incident response plan — confirmation, notification, chain of custody

Written 2026-08-26. Covers what happens between "the model fired" and "an
officer is on the way," and what has to be true of the evidence in between so
it holds up as more than a screenshot. Marked throughout: what exists today
vs. what this plan proposes building.

---

## 1. The flow that exists today

```
model fires  →  incident row created, status="pending"  →  clip/snapshot saved
                                                                   │
                                            operator with confirm_dismiss_alerts
                                            permission reviews it in the dashboard
                                                                   │
                                    ┌──────────────────────────────┴───────────────────────┐
                                    ▼                                                        ▼
                    POST /api/incidents/{id}/confirm-and-report                   POST .../dismiss
                    (ConfirmAndReportSchema: status,                              status → "dismissed"
                     capture_snapshot, report_details)
                                    │
                    incident status → "confirmed_and_reported"
                    broadcast over websocket to connected dashboards
```

This is real and working. What's missing is everything **after**
`confirm-and-report` — right now that endpoint updates a database row and
broadcasts to whoever happens to have the dashboard open. It does not reach
anyone who isn't already looking at a screen.

---

## 2. What you asked for: notify police/tanod via SMS or Telegram on confirm

### Design

On `confirm-and-report`, in addition to the existing DB update + websocket
broadcast, fire an outbound notification to the barangay's registered
responders (PNP officers and/or barangay tanod) for that incident's
`barangay_id`/`station_id`.

```
confirm-and-report
        │
        ├─ existing: update incidents row, broadcast websocket
        │
        └─ NEW: look up notify targets for this barangay/station
                  → send Telegram message (primary)
                  → send SMS (fallback / redundant)
                  → log delivery result against the incident
```

**Why both channels, not one:** a public-safety alert should not depend on a
single point of failure. Telegram needs a phone with data/wifi and the app
installed; SMS works on any phone with signal, no data plan or smartphone
required — realistic for a barangay tanod on patrol. Recommend **Telegram as
primary** (free, instant, can attach the actual snapshot image, delivers to a
group so multiple responders see it at once) **with SMS as fallback**
(guaranteed reach, no dependency on data connectivity).

### What each channel needs, concretely

**Telegram (recommended primary):**
- A bot created via BotFather (one-time setup, free), its token stored like
  any other secret (`.env`, never committed — same pattern already used for
  other credentials in this project).
- A chat_id per barangay/station — either a group the responders are already
  in, or individual chat_ids per officer. A group is simpler operationally
  (one send reaches everyone) and is the recommended default.
- `python-telegram-bot` (or a plain `requests.post` to the Bot API — no
  heavy dependency needed for send-only) called from `backend.py` at
  confirm-and-report time.

**SMS (recommended fallback):**
- For a Philippine deployment, **Semaphore** (semaphore.co) is the practical
  choice over Twilio: PH-based, priced in pesos, no A2P 10DLC/international
  sender-ID registration hassle that a US-based gateway requires for
  Philippine numbers. Twilio remains an option if the pilot ever needs
  cross-border reach, but that's not this deployment.
- Needs a per-officer/tanod phone number on file (see schema gap below).

### Message content

Keep it operational, not personal — this ties directly to
`docs/privacy_compliance_plan.md`: the notification should carry only what a
responder needs to act, nothing that expands who has access to personal data
beyond what's necessary.

```
[EcoVision] {incident.type} detected — {camera.location_name}
{occurred_date} {occurred_time} · confidence {confidence}
Case: {case_id}
View: {dashboard link, if network-reachable from a phone}
```

No narrative text, no lat/lng raw dump (location_name is human-readable
already), no snapshot attached over SMS (MMS is a different, costlier
integration — Telegram can carry the image, SMS stays text-only).

### Schema gap — not yet built

Nothing in the current schema stores a phone number or Telegram chat_id
anywhere (checked: no `phone`, `contact_number`, `mobile`, or
`telegram_chat_id` field exists in `users` or anywhere else). This needs:

- A `notify_targets` table (or columns on `users`): `barangay_id`/
  `station_id`, `channel` (sms/telegram), `destination` (phone number or
  chat_id), `role_label` (e.g. "Tanod Patrol", "Duty Officer") — mirrors the
  scoping pattern already used for cameras and incidents.
- A devteam/admin UI to manage these per barangay/station (same shape as the
  existing user/camera management screens).
- The actual send call wired into `confirm_and_report()` in `app/backend.py`,
  with delivery success/failure logged against the incident row so a failed
  send is visible, not silent (see `docs/recovery_plan.md` §7).

**This is a build item, scoped and ready to hand off — not something that
exists today.** Flag it as "designed, not yet implemented" if asked directly.

---

## 3. Chain of custody

For a clip or snapshot to mean anything beyond "the dashboard showed this
once," it needs to be traceable back to an unaltered original. Today,
nothing establishes that — a clip is just a file on disk, replaceable by
anyone with filesystem access, with no record of whether it's the same bytes
the model actually produced.

### What's missing and why it matters

If an incident ever escalates to an actual police report or court process
(which `confirm-and-report`'s existence implies is the intent), "here's a
video file" is a much weaker claim than "here's a video file, and here's
proof it's the exact, unmodified output of the detection system at
{timestamp}, never touched since."

### Proposed minimum

1. **Hash every clip and snapshot at creation time** (SHA-256, computed the
   moment `main.py` finishes writing the file) and store the hash on the
   incident row alongside `case_id`. Cheap — a few milliseconds — and turns
   "trust me" into "verify it yourself."
2. **Never allow deletion of evidence tied to a confirmed-and-reported
   incident** through the normal app UI — only through an explicit,
   logged admin action, distinct from the routine retention sweep in
   `docs/privacy_compliance_plan.md` §5 (which only touches dismissed/
   unconfirmed clips).
3. **Log every access** to a confirmed incident's clip/snapshot (who viewed
   it, when) — not enforcement, but an audit trail if the question "who has
   seen this" ever comes up.
4. **Timestamp integrity** — the incident row's `occurred_date`/
   `occurred_time` should come from the detection system's clock at capture
   time, not be editable after the fact through the report-details flow.
   Worth confirming `IncidentReportSchema`'s fields (narrative, nature_of_call,
   arrival_reason, additional_officers) don't allow silently overwriting
   the original timestamp — a quick check against `app/backend.py`, not a
   new feature.

None of this needs to be forensics-grade for a capstone pilot — but "we hash
the evidence and never silently allow it to be altered" is a real, cheap,
defensible answer if a panel asks how the system's output could ever be
trusted as more than a UI notification.

---

## 4. Summary of what to build, in order

1. `notify_targets` schema + admin UI (§2) — the concrete ask from this
   conversation.
2. Telegram send on confirm-and-report (fastest to stand up, free, no
   PH-carrier registration).
3. SMS fallback via Semaphore (second channel, redundancy).
4. Clip/snapshot SHA-256 hash on creation + stored on the incident row (§3.1)
   — cheapest chain-of-custody improvement, highest ratio of credibility
   gained to effort spent.
5. Deletion guard + access log for confirmed incidents (§3.2–3.3).
