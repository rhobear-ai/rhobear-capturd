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
CREATE TABLE IF NOT EXISTS mcp_tokens (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
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


def list_jobs(uid: str, limit: int = 30) -> list[dict]:
    """A user's recent jobs, newest first — powers the studio gallery."""
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (uid, limit)).fetchall()
        return [dict(r) for r in rows]


def add_usage(uid: str, kind: str, n: int = 1) -> None:
    with _db() as c:
        c.execute("INSERT INTO usage(user_id,kind,n,at) VALUES(?,?,?,?)",
                  (uid, kind, n, int(time.time())))


def usage_count(uid: str, kind: str) -> int:
    with _db() as c:
        row = c.execute("SELECT COALESCE(SUM(n),0) AS t FROM usage WHERE user_id=? AND kind=?",
                        (uid, kind)).fetchone()
        return int(row["t"])


# ---- render caps — atomic gates (race-proof Free slot + concurrency + rate) -
# The old Free gate was a check (here, in the route) plus an increment (in the
# post-completion task), two transactions up to 600s apart. N concurrent Free
# calls all read count==0 and passed. These primitives do the check-and-reserve
# in ONE BEGIN IMMEDIATE transaction, so a caller only ever sees slots a
# concurrent caller already committed. Every cap applies to Pro too.


def try_acquire(uid: str, kind: str, *, limit: int,
                window_seconds: Optional[int] = None) -> bool:
    """Atomically reserve one unit of (uid, kind) iff its count within the
    sliding window is below *limit*. True if reserved, False if at/over.

    *window_seconds* None ⇒ lifetime (no lower bound on ``at``). The SELECT and
    INSERT run inside ``BEGIN IMMEDIATE`` so concurrent callers serialize on the
    database write lock. Release a reservation with :func:`refund`.
    """
    now = int(time.time())
    since = now - window_seconds if window_seconds else 0
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None                  # autocommit; we manage the txn
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(SUM(n),0) AS t FROM usage "
            "WHERE user_id=? AND kind=? AND at >= ?",
            (uid, kind, since)).fetchone()
        if int(row["t"]) >= limit:
            conn.execute("ROLLBACK")
            return False
        conn.execute("INSERT INTO usage(user_id,kind,n,at) VALUES(?,?,?,?)",
                     (uid, kind, 1, now))
        conn.execute("COMMIT")
        return True
    except sqlite3.OperationalError:
        # lock not available within the busy timeout, or similar — fail safe:
        # never hand out a slot we couldn't prove was under the limit.
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False
    finally:
        conn.close()


def refund(uid: str, kind: str, n: int = 1) -> None:
    """Release *n* units previously reserved by :func:`try_acquire` — append a
    negative row so the running SUM drops back. Used when a reserved render
    fails (a Free user shouldn't lose their one slot to a render that never
    delivered) or when a later gate rejects a request that already reserved."""
    with _db() as c:
        c.execute("INSERT INTO usage(user_id,kind,n,at) VALUES(?,?,?,?)",
                  (uid, kind, -n, int(time.time())))


def window_retry_after(uid: str, kind: str, window_seconds: int) -> int:
    """Seconds until the oldest in-window (uid, kind) event ages out — a
    best-effort ``Retry-After`` for a rate-limited request (>= 1)."""
    now = int(time.time())
    since = now - window_seconds
    with _db() as c:
        row = c.execute(
            "SELECT MIN(at) AS oldest FROM usage "
            "WHERE user_id=? AND kind=? AND at >= ?",
            (uid, kind, since)).fetchone()
    oldest = int(row["oldest"]) if row and row["oldest"] is not None else now
    return max(1, (oldest + window_seconds) - now)


def try_queue_job(uid: str, kind: str, *, max_concurrent: int) -> Optional[str]:
    """Atomically create a 'queued' job for *uid* iff they hold fewer than
    *max_concurrent* in-flight jobs (status queued|running). Returns the new
    job_id, or None at/over the cap.

    Creating the queued job IS the concurrency reservation — the count and the
    insert run in one ``BEGIN IMMEDIATE`` transaction, so two simultaneous
    requests can't both read "1 in flight" and both queue a third. ``record_job``
    later moves the status to running/done/failed/capped; only queued|running
    count against the cap, so a finished render frees its slot.
    """
    now = int(time.time())
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COUNT(*) AS t FROM jobs "
            "WHERE user_id=? AND status IN ('queued','running')",
            (uid,)).fetchone()
        if int(row["t"]) >= max_concurrent:
            conn.execute("ROLLBACK")
            return None
        job_id = secrets.token_hex(6)            # 12 hex chars, like the rest
        conn.execute(
            "INSERT INTO jobs(id,user_id,kind,status,output,detail,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (job_id, uid, kind, "queued", "", "", now))
        conn.execute("COMMIT")
        return job_id
    except sqlite3.OperationalError:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return None
    finally:
        conn.close()


# ---- MCP tokens -------------------------------------------------------------
# The endpoint used to be keyed on the raw user id, which is guessable from any
# response that leaks it. These are random, revocable, and one per user.

def mcp_token_for(user_id: str) -> str:
    """Return this user's MCP token, minting one on first use."""
    with _db() as db:
        row = db.execute("SELECT token FROM mcp_tokens WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return row[0]
        token = secrets.token_urlsafe(24)
        db.execute("INSERT INTO mcp_tokens(token,user_id,created_at) VALUES(?,?,?)",
                   (token, user_id, int(time.time())))
        db.commit()
        return token


def user_for_mcp_token(token: str) -> dict | None:
    if not token:
        return None
    with _db() as db:
        row = db.execute(
            "SELECT u.id,u.email,u.plan FROM mcp_tokens m JOIN users u ON u.id=m.user_id "
            "WHERE m.token=?", (token,)).fetchone()
    return {"id": row[0], "email": row[1], "plan": row[2]} if row else None


def revoke_mcp_token(user_id: str) -> None:
    with _db() as db:
        db.execute("DELETE FROM mcp_tokens WHERE user_id=?", (user_id,))
        db.commit()
