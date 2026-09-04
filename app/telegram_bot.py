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

# BUG FOUND 2026-09-04 (caught live: a real chat got the "Your Telegram ID
# is..." registration reply TWICE for one message it sent). `offset` used to
# live only in _poll_loop's local variable, reset to None every time this
# process starts. Telegram only marks an update "confirmed" (i.e. stops
# handing it back from getUpdates) once a LATER getUpdates call is made with
# an offset past it -- receiving the update and even replying to it does not
# confirm it by itself. So: message arrives, this poller replies, sets its
# in-memory offset -- then the backend restarts (uvicorn --reload picking up
# a file change, a crash, a manual bounce, all routine during dev) before
# that advanced offset ever goes out in another getUpdates call. Telegram
# still considers the message unconfirmed, the fresh process starts at
# offset=None same as before, and getUpdates hands back that "old" message
# again, producing a second identical reply. Persisting the offset to disk
# after every update processed closes the gap: a restart resumes from the
# last value written, not from scratch, regardless of whether this process
# ever got to make another getUpdates call.
_OFFSET_FILENAME = "telegram_poller_offset.txt"


def _writable_dir():
    # Same env-var-or-home-fallback pattern db.py/port_utils.py each already
    # use independently for this -- telegram_bot.py has no import of
    # backend.py's WRITABLE_DIR to share, so it re-derives it the same way.
    d = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(
        os.path.expanduser("~"), "EcoVisionSentinelData"
    )
    os.makedirs(d, exist_ok=True)
    return d


def _offset_file_path():
    return os.path.join(_writable_dir(), _OFFSET_FILENAME)


def _load_offset():
    """Best-effort: a missing/corrupt file just means "start from whatever
    Telegram currently has pending", same as the old offset=None default --
    never worth crashing the poller over."""
    try:
        with open(_offset_file_path(), "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _save_offset(offset):
    """Best-effort persistence -- a failed write here should degrade back to
    the old in-memory-only behavior for this run, not crash the poller."""
    try:
        with open(_offset_file_path(), "w", encoding="utf-8") as f:
            f.write(str(offset))
    except OSError as e:
        print(f"[telegram] Could not persist poller offset (continuing in-memory only): {e}")


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
    offset = _load_offset()
    print(f"[telegram] Registration poller started (resuming from offset={offset}) -- "
          f"replying to any message with the sender's chat_id.")
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
                _save_offset(offset)
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
