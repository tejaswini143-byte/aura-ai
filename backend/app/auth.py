"""Single-admin, database-backed browser sessions and login throttling."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable


SESSION_COOKIE = "aura_admin_session"
SESSION_TTL = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCKOUT = timedelta(minutes=15)
MAX_LOGIN_FAILURES = 5


def iso_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_hash(client: str) -> str:
    return hashlib.sha256(client.encode("utf-8")).hexdigest()


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            session_hash TEXT PRIMARY KEY,
            csrf_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry ON admin_sessions(expires_at);
        CREATE TABLE IF NOT EXISTS admin_login_limits (
            client_hash TEXT PRIMARY KEY,
            window_started_at TEXT NOT NULL,
            failures INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT
        );
    """)
    conn.commit()


class AdminSessionStore:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection], clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db_factory = db_factory
        self.clock = clock

    def create(self) -> tuple[str, str]:
        now = self.clock().astimezone(timezone.utc)
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with closing(self.db_factory()) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (now.isoformat(),))
            conn.execute("INSERT INTO admin_sessions(session_hash,csrf_token,created_at,expires_at) VALUES(?,?,?,?)",
                         (token_hash(session_token), csrf_token, now.isoformat(), (now + SESSION_TTL).isoformat()))
            conn.commit()
        return session_token, csrf_token

    def get(self, session_token: str | None) -> dict[str, str] | None:
        if not session_token:
            return None
        key = token_hash(session_token)
        now = self.clock().astimezone(timezone.utc)
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT csrf_token,expires_at FROM admin_sessions WHERE session_hash=?", (key,)).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                conn.execute("DELETE FROM admin_sessions WHERE session_hash=?", (key,))
                conn.commit()
                return None
            return {"session_hash": key, "csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    def delete(self, session_token: str | None) -> None:
        if not session_token:
            return
        with closing(self.db_factory()) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE session_hash=?", (token_hash(session_token),))
            conn.commit()

    def csrf_valid(self, session: dict[str, str], supplied: str | None) -> bool:
        return bool(supplied and hmac.compare_digest(session["csrf_token"], supplied))

    def login_locked(self, client: str) -> bool:
        key = client_hash(client)
        now = self.clock().astimezone(timezone.utc)
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT window_started_at,locked_until FROM admin_login_limits WHERE client_hash=?", (key,)).fetchone()
            if not row:
                return False
            locked_until = datetime.fromisoformat(row["locked_until"]) if row["locked_until"] else None
            if locked_until and locked_until > now:
                return True
            started = datetime.fromisoformat(row["window_started_at"])
            if now - started >= LOGIN_WINDOW:
                conn.execute("DELETE FROM admin_login_limits WHERE client_hash=?", (key,))
                conn.commit()
            return False

    def login_failed(self, client: str) -> None:
        key = client_hash(client)
        now = self.clock().astimezone(timezone.utc)
        with closing(self.db_factory()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT window_started_at,failures FROM admin_login_limits WHERE client_hash=?", (key,)).fetchone()
            if not row or now - datetime.fromisoformat(row["window_started_at"]) >= LOGIN_WINDOW:
                failures, started = 1, now
                conn.execute("INSERT INTO admin_login_limits(client_hash,window_started_at,failures,locked_until) VALUES(?,?,?,NULL) "
                             "ON CONFLICT(client_hash) DO UPDATE SET window_started_at=excluded.window_started_at,failures=excluded.failures,locked_until=NULL",
                             (key, started.isoformat(), failures))
            else:
                failures = int(row["failures"]) + 1
                locked = (now + LOGIN_LOCKOUT).isoformat() if failures >= MAX_LOGIN_FAILURES else None
                conn.execute("UPDATE admin_login_limits SET failures=?,locked_until=? WHERE client_hash=?", (failures, locked, key))
            conn.commit()

    def login_succeeded(self, client: str) -> None:
        with closing(self.db_factory()) as conn:
            conn.execute("DELETE FROM admin_login_limits WHERE client_hash=?", (client_hash(client),))
            conn.commit()
