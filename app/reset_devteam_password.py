import sqlite3
import secrets
import sys
import os

# Make this resolvable regardless of the caller's cwd -- insert the
# directory this script itself lives in (app/), not os.getcwd(), since
# run_dev_system.bat invokes this as `python app/reset_devteam_password.py`
# from the repo root, where os.getcwd() would be the repo root, not app/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import hash_password, DB_PATH

new_pw = secrets.token_urlsafe(12)
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("SELECT username FROM users WHERE role='DEVTEAM'")
row = cur.fetchone()

if not row:
    print("No DEVTEAM user found in DB at", DB_PATH)
else:
    cur.execute("UPDATE users SET password = ? WHERE role = 'DEVTEAM'", (hash_password(new_pw),))
    conn.commit()
    print("Username:", row[0])
    print("New password:", new_pw)

conn.close()