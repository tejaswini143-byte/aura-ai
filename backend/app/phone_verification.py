"""Rate-limited state helpers for the explicit Twilio Verify workflow.

Verification codes are managed by Twilio Verify and are never persisted by AURA.
This module deliberately does not make provider requests; routes inject the
provider adapter so tests can exercise state transitions without network access.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable


E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
CODE_PATTERN = re.compile(r"^\d{4,10}$")
SEND_COOLDOWN = timedelta(seconds=60)
CHALLENGE_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def phone_key(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phone_verification_attempts (
            phone_hash TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_sent_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            verified_at TEXT,
            sms_consent_at TEXT
        )
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(phone_verification_attempts)").fetchall()}
    if "sms_consent_at" not in columns:
        conn.execute("ALTER TABLE phone_verification_attempts ADD COLUMN sms_consent_at TEXT")
    conn.commit()


class VerificationStore:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection], clock: Callable[[], datetime] = utc_now):
        self.db_factory = db_factory
        self.clock = clock

    def reserve_send(self, phone: str, sms_consent: bool = False) -> str:
        if not sms_consent:
            raise PermissionError("Explicit consent is required before requesting a verification SMS.")
        if not E164_PATTERN.fullmatch(phone):
            raise ValueError("Use a valid E.164 destination number.")
        now = self.clock().astimezone(timezone.utc)
        key = phone_key(phone)
        conn = self.db_factory()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM phone_verification_attempts WHERE phone_hash=?", (key,)).fetchone()
            if existing:
                last_sent = datetime.fromisoformat(existing["last_sent_at"])
                if now - last_sent < SEND_COOLDOWN:
                    raise TimeoutError("A verification request was sent recently. Wait before requesting another code.")
            expires = (now + CHALLENGE_LIFETIME).isoformat()
            stamp = now.isoformat()
            conn.execute("""
                INSERT INTO phone_verification_attempts(phone_hash,status,created_at,expires_at,last_sent_at,attempts,verified_at,sms_consent_at)
                VALUES(?, 'sending', ?, ?, ?, 0, NULL, ?)
                ON CONFLICT(phone_hash) DO UPDATE SET status='sending',created_at=excluded.created_at,
                expires_at=excluded.expires_at,last_sent_at=excluded.last_sent_at,attempts=0,verified_at=NULL,sms_consent_at=excluded.sms_consent_at
            """, (key, stamp, expires, stamp, stamp))
            conn.commit()
            return key
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_sent(self, key: str) -> None:
        with closing(self.db_factory()) as conn:
            conn.execute("UPDATE phone_verification_attempts SET status='pending' WHERE phone_hash=? AND status='sending'", (key,))
            conn.commit()

    def mark_send_failed(self, key: str) -> None:
        with closing(self.db_factory()) as conn:
            conn.execute("UPDATE phone_verification_attempts SET status='failed' WHERE phone_hash=? AND status='sending'", (key,))
            conn.commit()

    def begin_check(self, phone: str, code: str) -> str:
        if not E164_PATTERN.fullmatch(phone) or not CODE_PATTERN.fullmatch(code):
            raise ValueError("Enter a valid destination and verification code.")
        key = phone_key(phone)
        now = self.clock().astimezone(timezone.utc)
        conn = self.db_factory()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM phone_verification_attempts WHERE phone_hash=?", (key,)).fetchone()
            if not row or row["status"] != "pending" or not row["sms_consent_at"]:
                raise LookupError("No active verification is available for this destination.")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                conn.execute("UPDATE phone_verification_attempts SET status='expired' WHERE phone_hash=?", (key,))
                conn.commit()
                raise TimeoutError("The verification code expired. Request a new code.")
            if int(row["attempts"]) >= MAX_ATTEMPTS:
                conn.execute("UPDATE phone_verification_attempts SET status='locked' WHERE phone_hash=?", (key,))
                conn.commit()
                raise PermissionError("Too many verification attempts. Request a new code later.")
            conn.execute("UPDATE phone_verification_attempts SET attempts=attempts+1 WHERE phone_hash=?", (key,))
            conn.commit()
            return key
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def finish_check(self, key: str, approved: bool) -> bool:
        now = self.clock().astimezone(timezone.utc).isoformat()
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT status, expires_at, attempts FROM phone_verification_attempts WHERE phone_hash=?", (key,)).fetchone()
            if not row or row["status"] != "pending" or datetime.fromisoformat(row["expires_at"]) <= self.clock().astimezone(timezone.utc):
                return False
            if approved:
                conn.execute("UPDATE phone_verification_attempts SET status='verified',verified_at=? WHERE phone_hash=?", (now, key))
            elif int(row["attempts"]) >= MAX_ATTEMPTS:
                conn.execute("UPDATE phone_verification_attempts SET status='locked' WHERE phone_hash=?", (key,))
            conn.commit()
            return approved

    def invalidate(self, phone: str) -> None:
        with closing(self.db_factory()) as conn:
            conn.execute("DELETE FROM phone_verification_attempts WHERE phone_hash=?", (phone_key(phone),))
            conn.commit()

    def status(self, phone: str) -> dict[str, int | str | None]:
        now = self.clock().astimezone(timezone.utc)
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT status,expires_at,last_sent_at,attempts FROM phone_verification_attempts WHERE phone_hash=?", (phone_key(phone),)).fetchone()
            if not row:
                return {"status": "none", "retry_after_seconds": 0, "attempts_remaining": MAX_ATTEMPTS, "expires_at": None}
            status = row["status"]
            expires = datetime.fromisoformat(row["expires_at"])
            if status == "pending" and expires <= now:
                status = "expired"
                conn.execute("UPDATE phone_verification_attempts SET status='expired' WHERE phone_hash=?", (phone_key(phone),))
                conn.commit()
            last_sent = datetime.fromisoformat(row["last_sent_at"])
            retry_after = max(0, int((last_sent + SEND_COOLDOWN - now).total_seconds()))
            return {
                "status": status,
                "retry_after_seconds": retry_after,
                "attempts_remaining": max(0, MAX_ATTEMPTS - int(row["attempts"])),
                "expires_at": row["expires_at"] if status == "pending" else None,
            }


def action_token_valid(supplied: str | None, expected: str | None) -> bool:
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))
