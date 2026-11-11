from __future__ import annotations
import contextlib
import json
import sqlite3
import time
import uuid
from .config import Config


def uid(prefix=""):
    return prefix + uuid.uuid4().hex


def now():
    return time.time()


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL, kind TEXT NOT NULL, ts REAL NOT NULL, ingested REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_session ON events(session_id,seq);
CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(session_id,version));
CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, issue_key TEXT NOT NULL, intent_version INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(session_id,issue_key,intent_version));
CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS summaries (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, end_seq INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, ts REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audits (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (path TEXT PRIMARY KEY, cursor INTEGER NOT NULL, fingerprint TEXT NOT NULL);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    TABLES = {"projects", "sessions", "intents", "findings", "actions", "summaries", "policies", "checks", "reviews"}

    def __init__(self, config: Config):
        self.config = config
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Database was created by a newer Damask version")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")

    def connect(self):
        db = sqlite3.connect(self.config.db, timeout=2, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=2000")
        return db

    @contextlib.contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, table, db=None, **filters):
        assert table in self.TABLES
        allowed = {"id", "session_id", "project_id", "root", "issue_key", "intent_version"}
        assert set(filters) <= allowed
        sql = f"SELECT data FROM {table}"
        if filters:
            sql += " WHERE " + " AND ".join(k + "=?" for k in filters)
        if db is not None:
            return [json.loads(r[0]) for r in db.execute(sql, tuple(filters.values()))]
        with contextlib.closing(self.connect()) as c:
            return [json.loads(r[0]) for r in c.execute(sql, tuple(filters.values()))]

    def get(self, table, id, db=None):
        rows = self.all(table, db, id=id)
        if not rows:
            raise ValueError(f"Unknown {table} record")
        return rows[0]

    def put(self, table, obj, db):
        assert table in self.TABLES
        columns = {"projects": ["root"], "sessions": ["project_id"],
            "intents": ["session_id", "version"], "findings": ["session_id", "issue_key", "intent_version"],
            "actions": ["session_id"], "summaries": ["session_id", "end_seq"],
            "policies": ["project_id"], "checks": ["project_id"], "reviews": ["session_id", "ts"]}[table]
        fields = ["id"] + columns + ["data"]
        values = [obj[k] for k in fields[:-1]] + [dump(obj)]
        db.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) ON CONFLICT(id) DO UPDATE SET data=excluded.data", values)

    def audit(self, kind, data, db):
        db.execute("INSERT INTO audits(ts,kind,data) VALUES(?,?,?)", (now(), kind, dump(data)))

    def events(self, session_id, after=0, limit=500, db=None):
        sql = "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?"
        def read(c):
            return [{**dict(r), "data": json.loads(r["data"])} for r in c.execute(sql, (session_id, after, limit))]
        if db is not None:
            return read(db)
        with contextlib.closing(self.connect()) as c:
            return read(c)

    def backup(self, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self.connect()) as src, contextlib.closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        return destination
