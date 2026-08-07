# db.py — dual-backend compatibility shim
"""
Lets backend.py keep writing sqlite3-style code (`?` placeholders,
row["col"], dict(row), cursor.lastrowid) while running against either:

  - Postgres, when DATABASE_URL is set (web/dev stack, docker-compose)
  - SQLite,   when DATABASE_URL is unset (standalone installer build)

Import get_conn(), IntegrityError, DB_KIND, and table_exists() from here
instead of touching sqlite3/psycopg2 directly anywhere else.
"""
import os
import re
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_KIND = "postgres" if DATABASE_URL else "sqlite"

if DB_KIND == "postgres":
    import psycopg2
    import psycopg2.extensions
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

_QMARK_RE = re.compile(r"\?")


class Row:
    __slots__ = ("_cols", "_vals", "_idx")

    def __init__(self, cols, vals, idx=None):
        self._cols = cols
        self._vals = vals
        # `idx` is a name->position map shared across every Row built from the
        # same query result (built once in fetchall()/_wrap() below), so
        # column lookups by name are O(1) instead of the O(columns) linear
        # scan `_cols.index(key)` would do on every single field access --
        # this matters because Row is used pervasively (dict(row), row["col"])
        # including per-row loops that touch a dozen-plus fields each.
        self._idx = idx

    def keys(self):
        return self._cols

    def __getitem__(self, key):
        if isinstance(key, str):
            if self._idx is not None:
                return self._vals[self._idx[key]]
            return self._vals[self._cols.index(key)]
        return self._vals[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except (ValueError, IndexError):
            return default

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._vals))})"


# ──────────────────────────────────────────────────────────────────────────
# POSTGRES BACKEND
# ──────────────────────────────────────────────────────────────────────────
class _PostgresCursor:
    def __init__(self, raw_cursor):
        self._c = raw_cursor
        self.lastrowid = None

    def _convert(self, query):
        return _QMARK_RE.sub("%s", query)

    def execute(self, query, params=None):
        q = self._convert(query)
        if params is None:
            self._c.execute(q)
        else:
            self._c.execute(q, tuple(params))
        return self

    def returning_execute(self, query, params, pk="id"):
        q = self._convert(query).rstrip().rstrip(";")
        q = f"{q} RETURNING {pk}"
        self._c.execute(q, tuple(params))
        row = self._c.fetchone()
        self.lastrowid = row[0] if row else None
        return self

    def executescript(self, script: str):
        for statement in script.split(";"):
            stmt = statement.strip()
            if stmt:
                self._c.execute(stmt)

    def _wrap(self, raw_row):
        if raw_row is None:
            return None
        cols = [d[0] for d in self._c.description]
        idx = {c: i for i, c in enumerate(cols)}
        return Row(cols, list(raw_row), idx)

    def fetchone(self):
        return self._wrap(self._c.fetchone())

    def fetchall(self):
        cols = [d[0] for d in self._c.description] if self._c.description else []
        idx = {c: i for i, c in enumerate(cols)}
        return [Row(cols, list(r), idx) for r in self._c.fetchall()]

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class _PostgresConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return _PostgresCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Return the connection to the pool instead of actually closing the
        # socket -- every route in backend.py does get_conn()...close() per
        # request, which previously meant a fresh TCP handshake + auth to
        # Postgres on every single API call. Pooling here is transparent to
        # every existing call site since they only ever call .close().
        if _pg_pool is not None:
            # A connection left mid-transaction (an exception before commit)
            # would poison the pool for the next borrower -- roll back first.
            try:
                self._conn.rollback()
            except Exception:
                pass
            _pg_pool.putconn(self._conn)
        else:
            self._conn.close()

    def executescript(self, script: str):
        cur = self.cursor()
        cur.executescript(script)


_pg_pool = None
if DB_KIND == "postgres":
    from psycopg2.pool import ThreadedConnectionPool
    _pg_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)


def _get_conn_postgres():
    raw = _pg_pool.getconn() if _pg_pool is not None else psycopg2.connect(DATABASE_URL)
    return _PostgresConnection(raw)


# ──────────────────────────────────────────────────────────────────────────
# SQLITE BACKEND
# ──────────────────────────────────────────────────────────────────────────
SQLITE_PATH = os.environ.get("SQLITE_PATH")
if DB_KIND == "sqlite" and not SQLITE_PATH:
    _writable = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(
        os.path.expanduser("~"), "EcoVisionSentinelData"
    )
    os.makedirs(_writable, exist_ok=True)
    SQLITE_PATH = os.path.join(_writable, "ecovision.db")


# Queries written against the Postgres schema use NOW() in a few spots
# (approving a location, etc.) - translate at execute time rather than
# touching every call site in backend.py.
_NOW_RE = re.compile(r"\bNOW\(\)", re.IGNORECASE)


class _SQLiteCursor:
    def __init__(self, raw_cursor):
        self._c = raw_cursor

    def _convert(self, query):
        return _NOW_RE.sub("CURRENT_TIMESTAMP", query)

    def execute(self, query, params=None):
        q = self._convert(query)
        if params is None:
            self._c.execute(q)
        else:
            self._c.execute(q, tuple(params))
        return self

    def returning_execute(self, query, params, pk="id"):
        # SQLite: no reliance on RETURNING (older bundled sqlite3 builds
        # may lack it) - just execute + read back lastrowid.
        q = self._convert(query).rstrip().rstrip(";")
        self._c.execute(q, tuple(params))
        return self

    def executescript(self, script: str):
        self._c.executescript(script)

    def _wrap(self, raw_row):
        if raw_row is None:
            return None
        cols = [d[0] for d in self._c.description]
        idx = {c: i for i, c in enumerate(cols)}
        return Row(cols, list(raw_row), idx)

    def fetchone(self):
        return self._wrap(self._c.fetchone())

    def fetchall(self):
        cols = [d[0] for d in self._c.description] if self._c.description else []
        idx = {c: i for i, c in enumerate(cols)}
        return [Row(cols, list(r), idx) for r in self._c.fetchall()]

    @property
    def lastrowid(self):
        return self._c.lastrowid

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class _SQLiteConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return _SQLiteCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def executescript(self, script: str):
        self._conn.executescript(script)


def _get_conn_sqlite():
    raw = sqlite3.connect(SQLITE_PATH)
    raw.execute("PRAGMA foreign_keys = ON")
    return _SQLiteConnection(raw)


# ──────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRYPOINT
# ──────────────────────────────────────────────────────────────────────────
def get_conn():
    if DB_KIND == "postgres":
        return _get_conn_postgres()
    return _get_conn_sqlite()


def table_exists(cursor, table_name: str) -> bool:
    """DB-agnostic replacement for backend.py's Postgres-only
    `SELECT to_regclass('public.<table>')` existence check."""
    if DB_KIND == "postgres":
        cursor.execute("SELECT to_regclass('public.' || ?) AS reg", (table_name,))
        row = cursor.fetchone()
        return bool(row and row["reg"] is not None)
    else:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None