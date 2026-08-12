# reset_devteam_password.py
#
# Uses DEVTEAM_BOOTSTRAP_PASSWORD from the environment (.env, not committed)
# if set, matching backend.py's bootstrap logic -- so running this doesn't
# undo a static team password by handing back a fresh random one. Falls
# back to a random password only if that env var isn't set.
import secrets
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from backend import hash_password
from db import get_conn

static_pw = os.environ.get("DEVTEAM_BOOTSTRAP_PASSWORD")
new_pw = static_pw or secrets.token_urlsafe(12)

conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT username FROM users WHERE role='DEVTEAM'")
row = cur.fetchone()

if not row:
    print("No DEVTEAM user found.")
else:
    cur.execute("UPDATE users SET password = ? WHERE role = 'DEVTEAM'", (hash_password(new_pw),))
    conn.commit()
    print("Username:", row["username"])
    print("New password:", new_pw, "(from DEVTEAM_BOOTSTRAP_PASSWORD)" if static_pw else "(randomly generated -- set DEVTEAM_BOOTSTRAP_PASSWORD in .env for a static one)")

conn.close()