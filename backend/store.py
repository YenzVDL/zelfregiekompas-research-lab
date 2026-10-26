"""Small transactional SQLite JSON repository. One connection per operation."""
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, path=None):
        self.path = str(path or os.getenv("RESEARCH_DB", "data/research.sqlite3"))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.local = threading.local()
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_kind ON records(kind)")

    @contextmanager
    def connect(self):
        with self.lock:
            existing = getattr(self.local, "connection", None)
            if existing is not None:
                yield existing
                return
            conn = sqlite3.connect(self.path, timeout=30)
            try:
                conn.execute("BEGIN IMMEDIATE")
                self.local.connection = conn
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self.local.connection = None
                conn.close()

    def put(self, kind, body, *, insert=False):
        with self.connect() as c:
            self.write(c, kind, body, insert=insert)
        return body

    @staticmethod
    def write(c, kind, body, *, insert=False):
        sql = "INSERT INTO records VALUES(?,?,?)" if insert else "INSERT INTO records VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body"
        c.execute(sql, (kind, body["id"], json.dumps(body, ensure_ascii=False, allow_nan=False)))

    def get(self, kind, identifier):
        with self.connect() as c:
            row = c.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        if row is None:
            raise ValueError(f"{kind} {identifier} does not exist")
        return json.loads(row[0])

    def all(self, kind):
        with self.connect() as c:
            rows = c.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def patch(self, kind, identifier, **changes):
        with self.lock:
            row = self.get(kind, identifier)
            row.update(changes)
            return self.put(kind, row)

    def delete(self, kind, identifier):
        with self.connect() as c:
            c.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, identifier))
