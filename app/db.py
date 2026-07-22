"""
Postgres compatibility shim.

Lets backend.py keep writing sqlite3-style code (`?` placeholders,
row["col"], dict(row), cursor.lastrowid) while actually running against
Postgres via psycopg2. Import get_conn() and IntegrityError from here
instead of using the sqlite3 module directly.
"""
import os
import re
import psycopg2
import psycopg2.extensions

IntegrityError = psycopg2.IntegrityError

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. This build requires Postgres -- "
        "set DATABASE_URL=postgresql://user:pass@host:5432/dbname"
    )

_QMARK_RE = re.compile(r"\?")


class Row:
    """Mimics sqlite3.Row: supports row['col'], row[0], and dict(row)."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals

    def keys(self):
        return self._cols

    def __getitem__(self, key):
        if isinstance(key, str):
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


class Cursor:
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
        # Emulate sqlite3's cursor.lastrowid for simple single-row INSERTs
        # into tables with a `id` primary key, by appending RETURNING id
        # only when the caller explicitly asks (see returning_execute below).
        return self

    def returning_execute(self, query, params, pk="id"):
        """Use for INSERT statements where the caller needs .lastrowid."""
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
        return Row(cols, list(raw_row))

    def fetchone(self):
        return self._wrap(self._c.fetchone())

    def fetchall(self):
        cols = [d[0] for d in self._c.description] if self._c.description else []
        return [Row(cols, list(r)) for r in self._c.fetchall()]

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class Connection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def executescript(self, script: str):
        cur = self.cursor()
        cur.executescript(script)


def get_conn() -> Connection:
    raw = psycopg2.connect(DATABASE_URL)
    return Connection(raw)