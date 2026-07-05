"""SQLite persistence — users, sessions, jobs, usage. Real, not a stub."""
from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free',      -- free | pro
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- walk | shots
  status TEXT NOT NULL,                   -- queued|running|done|failed|capped
  output TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- generation | shot
  n INTEGER NOT NULL DEFAULT 1,
  at INTEGER NOT NULL
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with _db() as c:
        c.executescript(_SCHEMA)


# ---- users ------------------------------------------------------------------

def upsert_user(email: str) -> dict:
    email = email.strip().lower()
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row:
            return dict(row)
        uid = secrets.token_hex(8)
        c.execute("INSERT INTO users(id,email,plan,created_at) VALUES(?,?,?,?)",
                  (uid, email, "free", int(time.time())))
        return {"id": uid, "email": email, "plan": "free", "created_at": int(time.time())}


def get_user(uid: str) -> Optional[dict]:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None


def set_plan(uid: str, plan: str) -> None:
    with _db() as c:
        c.execute("UPDATE users SET plan=? WHERE id=?", (plan, uid))


# ---- sessions ---------------------------------------------------------------

def new_session(uid: str) -> str:
    token = secrets.token_urlsafe(32)
    with _db() as c:
        c.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                  (token, uid, int(time.time())))
    return token


def user_for_session(token: str) -> Optional[dict]:
    if not token:
        return None
    with _db() as c:
        row = c.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,)).fetchone()
        return dict(row) if row else None


def drop_session(token: str) -> None:
    with _db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ---- jobs + usage -----------------------------------------------------------

def record_job(job_id: str, uid: str, kind: str, status: str,
               output: str = "", detail: str = "") -> None:
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO jobs(id,user_id,kind,status,output,detail,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (job_id, uid, kind, status, output, detail, int(time.time())))


def get_job(job_id: str) -> Optional[dict]:
    with _db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def add_usage(uid: str, kind: str, n: int = 1) -> None:
    with _db() as c:
        c.execute("INSERT INTO usage(user_id,kind,n,at) VALUES(?,?,?,?)",
                  (uid, kind, n, int(time.time())))


def usage_count(uid: str, kind: str) -> int:
    with _db() as c:
        row = c.execute("SELECT COALESCE(SUM(n),0) AS t FROM usage WHERE user_id=? AND kind=?",
                        (uid, kind)).fetchone()
        return int(row["t"])
