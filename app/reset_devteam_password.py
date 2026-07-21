import sqlite3
import secrets
import sys
import os

sys.path.insert(0, os.getcwd())
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