"""
Telegram bot: registration poller + the alert message format.

Two jobs live here:

1. Registration poller -- answers "how does a barangay admin actually get a
   chat_id to register" (docs/incident_response_plan.md §2 left this open).
   A Telegram chat_id is an opaque number nobody knows off-hand, and this
   backend has no public HTTPS endpoint for Telegram to webhook into (it's
   a desktop app behind NAT, not a public server) -- so long-polling is
   used instead of a webhook. This thread calls getUpdates every few
   seconds and replies to ANY message the bot receives with the sender's
   own chat_id.

   Flow: an officer/tanod opens t.me/EcoVisionCrimeDetectionSMSBOT, sends
   any message, gets their chat_id back, gives it to their barangay/station
   admin, who pastes it into the notify_targets UI (POST /api/notify_targets,
   channel=telegram). No registration codes or deep links needed -- the
   chat_id itself is the credential, and the admin adding it is already
   permission-gated to their own jurisdiction.

2. format_crime_alert() -- the actual alert text. Plain text, no emojis:
   what crime, what confidence, what pole/camera it came from. Nothing
   else -- see docs/incident_response_plan.md's "Message content" section
   for why (operational essentials only, no personal data expansion).
"""
import os
import time
import threading
import requests

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
POLL_INTERVAL_SECONDS = 4
REGISTRATION_REPLY_TEXT = (
    "EcoVision Sentinel\n\n"
    "Your Telegram ID is: {chat_id}\n\n"
    "Give this number to your barangay or station administrator -- they will "
    "register it in the Notifications panel so you get alerted when an "
    "incident is confirmed."
)


def format_crime_alert(crime_type: str, confidence, camera_name: str) -> str:
    """Plain text, no emojis. Confidence, crime type, and which pole/camera
    it was detected from -- nothing more."""
    try:
        conf_str = f"{float(confidence) * 100:.0f}%"
    except (TypeError, ValueError):
        conf_str = str(confidence)
    return (
        f"Crime detected: {crime_type}\n"
        f"Confidence: {conf_str}\n"
        f"Camera: {camera_name}"
    )


def _api(token, method, **params):
    return requests.post(TELEGRAM_API_BASE.format(token=token, method=method), json=params, timeout=10)


def send_message(chat_id, text: str):
    """One-off send, used by notifications.py for the actual alert. Returns
    (status, error) matching notifications.py's send_telegram() contract."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return "skipped_unconfigured", "TELEGRAM_BOT_TOKEN not set"
    try:
        resp = _api(token, "sendMessage", chat_id=chat_id, text=text)
        if resp.ok and resp.json().get("ok"):
            return "sent", None
        return "failed", f"Telegram API returned {resp.status_code}: {resp.text[:300]}"
    except requests.RequestException as e:
        return "failed", str(e)


def _poll_loop(token):
    offset = None
    print("[telegram] Registration poller started -- replying to any message with the sender's chat_id.")
    while True:
        try:
            resp = _api(token, "getUpdates", offset=offset, timeout=20)
            if not resp.ok:
                # Bad token, rate limit, or Telegram outage -- back off and
                # retry rather than spinning a tight error loop.
                time.sleep(10)
                continue
            updates = resp.json().get("result", [])
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message")
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                if chat_id is None:
                    continue
                try:
                    _api(token, "sendMessage", chat_id=chat_id,
                         text=REGISTRATION_REPLY_TEXT.format(chat_id=chat_id))
                except Exception as e:
                    print(f"[telegram] Could not reply to {chat_id}: {e}")
        except requests.RequestException:
            time.sleep(10)
        except Exception as e:
            print(f"[telegram] Poller error (continuing): {e}")
            time.sleep(5)
        time.sleep(POLL_INTERVAL_SECONDS)


def start_telegram_registration_poller():
    """No-op (logged, not silent) if TELEGRAM_BOT_TOKEN isn't set -- same
    graceful-degradation pattern as the rest of notifications.py."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[telegram] TELEGRAM_BOT_TOKEN not set -- registration poller not started.")
        return
    t = threading.Thread(target=_poll_loop, args=(token,), name="telegram-registration-poller", daemon=True)
    t.start()
