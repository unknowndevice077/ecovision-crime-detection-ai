# reset_devteam_password.py
import secrets
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import hash_password
from db import get_conn

new_pw = secrets.token_urlsafe(12)
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
    print("New password:", new_pw)

conn.close()