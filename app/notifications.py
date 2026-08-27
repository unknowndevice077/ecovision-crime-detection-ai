"""
Responder notifications -- Telegram (primary) + SMS (fallback via Semaphore).

See docs/incident_response_plan.md §2 for the full design and why both
channels exist. Short version: `confirm_and_report()` in backend.py calls
`notify_incident_targets()` after it commits the confirm -- this module never
decides whether to notify, only how, so a failure here can't block the
incident itself from being confirmed.

Both send functions are no-ops (logged, not silent) when their credentials
aren't configured -- this project has never had real Telegram/Semaphore
credentials to test against, so "not configured" has to be a normal,
expected state, not a startup crash.

Env vars (see .env.example):
    TELEGRAM_BOT_TOKEN     -- from @BotFather
    SEMAPHORE_API_KEY      -- from semaphore.co
    SEMAPHORE_SENDER_NAME  -- optional, defaults to Semaphore's shared sender
"""
import os
import uuid
import requests
from telegram_bot import send_message as send_telegram, format_crime_alert

SEMAPHORE_API_URL = "https://api.semaphore.co/api/v4/messages"
SEND_TIMEOUT_SECONDS = 8  # a notification must never be allowed to hang the request thread


def _telegram_configured():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _semaphore_configured():
    return bool(os.environ.get("SEMAPHORE_API_KEY"))


def send_sms(phone: str, text: str):
    """Returns (status, error). Uses Semaphore (semaphore.co) -- see
    docs/incident_response_plan.md §2 for why it's the recommended gateway
    for a Philippine deployment over e.g. Twilio."""
    api_key = os.environ.get("SEMAPHORE_API_KEY")
    if not api_key:
        return "skipped_unconfigured", "SEMAPHORE_API_KEY not set"
    try:
        payload = {"apikey": api_key, "number": phone, "message": text}
        sender = os.environ.get("SEMAPHORE_SENDER_NAME")
        if sender:
            payload["sendername"] = sender
        resp = requests.post(SEMAPHORE_API_URL, data=payload, timeout=SEND_TIMEOUT_SECONDS)
        if resp.ok:
            return "sent", None
        return "failed", f"Semaphore API returned {resp.status_code}: {resp.text[:300]}"
    except requests.RequestException as e:
        return "failed", str(e)


def _format_sms_message(incident: dict) -> str:
    """SMS costs money per message and has no history to scroll back
    through, so it carries a bit more than the Telegram alert -- date/time
    and the case_id for follow-up. Still operational content only -- no
    narrative text, no raw lat/lng. See docs/incident_response_plan.md §2
    ('Message content') and docs/privacy_compliance_plan.md for why."""
    return (
        f"EcoVision: {incident.get('type', 'INCIDENT')} detected -- "
        f"{incident.get('location_name') or 'location unknown'}\n"
        f"{incident.get('occurred_date', '')} {incident.get('occurred_time', '')} "
        f"conf {incident.get('confidence', '?')}\n"
        f"Case: {incident.get('case_id', incident.get('id', '?'))}"
    )


def notify_incident_targets(get_conn, incident: dict):
    """Looks up every active notify_targets row scoped to this incident's
    barangay (directly, or via a station that covers that barangay -- same
    join pattern apply_scope() uses elsewhere in backend.py), sends to each,
    and logs every attempt to notify_log regardless of outcome.

    Never raises -- a notification failure must not be allowed to look like
    the confirm-and-report itself failed. Errors are logged to notify_log
    and to stdout instead.
    """
    barangay_id = incident.get("barangay_id")
    if not barangay_id:
        return []

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT DISTINCT nt.* FROM notify_targets nt
               WHERE nt.active = 1 AND (
                   nt.barangay_id = ?
                   OR nt.station_id IN (
                       SELECT station_id FROM station_barangays WHERE barangay_id = ?
                   )
               )""",
            (barangay_id, barangay_id),
        )
        targets = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"[notify] Could not look up notify_targets: {e}")
        conn.close()
        return []

    if not targets:
        conn.close()
        return []

    results = []
    for target in targets:
        if target["channel"] == "telegram":
            message = format_crime_alert(
                incident.get("type", "INCIDENT"),
                incident.get("confidence", "?"),
                incident.get("location_name") or "location unknown",
            )
            status, error = send_telegram(target["destination"], message)
        elif target["channel"] == "sms":
            status, error = send_sms(target["destination"], _format_sms_message(incident))
        else:
            status, error = "failed", f"Unknown channel: {target['channel']}"

        results.append({"target": target, "status": status, "error": error})
        try:
            cursor.execute(
                """INSERT INTO notify_log (id, incident_id, target_id, channel, destination, status, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), incident.get("id"), target["id"], target["channel"],
                 target["destination"], status, error),
            )
        except Exception as e:
            print(f"[notify] Could not write notify_log row: {e}")

        if status == "failed":
            print(f"[notify] {target['channel']} to {target['destination']} FAILED: {error}")
        elif status == "skipped_unconfigured":
            print(f"[notify] {target['channel']} skipped -- not configured ({error})")

    conn.commit()
    conn.close()
    return results
