from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def parse_user_datetime(value: str, timezone_name: str, *, date_only_hour: int = 9) -> datetime:
    zone = ZoneInfo(timezone_name)
    raw = value.strip()
    if len(raw) == 10:
        parsed = datetime.combine(date.fromisoformat(raw), time(hour=date_only_hour), tzinfo=zone)
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


class AutomationService:
    """Persistent local reminders and deterministic daily planning."""

    def __init__(self, db_factory: Callable[[], sqlite3.Connection], clock: Callable[[], datetime] | None = None):
        self.db_factory = db_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def create_schema(conn: sqlite3.Connection, timestamp: str) -> None:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS automation_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            reminders_enabled INTEGER NOT NULL DEFAULT 1,
            reminder_lead_minutes INTEGER NOT NULL DEFAULT 30,
            daily_planning_enabled INTEGER NOT NULL DEFAULT 0,
            daily_planning_time TEXT NOT NULL DEFAULT '09:00',
            timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            task_title TEXT NOT NULL,
            reminder_type TEXT NOT NULL DEFAULT 'custom',
            scheduled_for TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ready_at TEXT,
            cancelled_at TEXT,
            UNIQUE(task_id, scheduled_for, reminder_type)
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, scheduled_for);
        CREATE TABLE IF NOT EXISTS daily_plans (
            plan_date TEXT PRIMARY KEY,
            timezone TEXT NOT NULL,
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'deterministic'
        );
        CREATE TABLE IF NOT EXISTS automation_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_events_created ON automation_events(created_at DESC);
        """)
        conn.execute(
            "INSERT OR IGNORE INTO automation_settings (id, updated_at) VALUES (1, ?)",
            (timestamp,),
        )
        conn.commit()

    def settings(self) -> dict[str, Any]:
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT * FROM automation_settings WHERE id=1").fetchone()
            if row is None:
                conn.execute("INSERT OR IGNORE INTO automation_settings (id, updated_at) VALUES (1, ?)", (utc_iso(self.clock()),))
                conn.commit()
                row = conn.execute("SELECT * FROM automation_settings WHERE id=1").fetchone()
            return dict(row)

    @staticmethod
    def validate_settings(values: dict[str, Any]) -> dict[str, Any]:
        zone_name = str(values.get("timezone", "Asia/Kolkata")).strip()
        try:
            ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone, such as Asia/Kolkata or UTC.")
        lead = int(values.get("reminder_lead_minutes", 30))
        if not 0 <= lead <= 1440:
            raise ValueError("Reminder lead time must be between 0 and 1440 minutes.")
        plan_time = str(values.get("daily_planning_time", "09:00")).strip()
        try:
            datetime.strptime(plan_time, "%H:%M")
        except ValueError:
            raise ValueError("Daily planning time must use 24-hour HH:MM format.")
        return {
            "reminders_enabled": bool(values.get("reminders_enabled", True)),
            "reminder_lead_minutes": lead,
            "daily_planning_enabled": bool(values.get("daily_planning_enabled", False)),
            "daily_planning_time": plan_time,
            "timezone": zone_name,
        }

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        clean = self.validate_settings(values)
        timestamp = utc_iso(self.clock())
        with closing(self.db_factory()) as conn:
            conn.execute("""
                UPDATE automation_settings
                SET reminders_enabled=?, reminder_lead_minutes=?, daily_planning_enabled=?,
                    daily_planning_time=?, timezone=?, updated_at=? WHERE id=1
            """, (
                int(clean["reminders_enabled"]), clean["reminder_lead_minutes"],
                int(clean["daily_planning_enabled"]), clean["daily_planning_time"],
                clean["timezone"], timestamp,
            ))
            if not clean["reminders_enabled"]:
                conn.execute(
                    "UPDATE reminders SET status='cancelled', cancelled_at=?, updated_at=? WHERE status IN ('scheduled', 'ready')",
                    (timestamp, timestamp),
                )
            conn.commit()
        if clean["reminders_enabled"]:
            self.sync_all_task_reminders()
        return self.settings()

    def _event(self, conn: sqlite3.Connection, event_type: str, status: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO automation_events (id, event_type, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), event_type, status, detail[:240], utc_iso(self.clock())),
        )

    def _insert_or_reactivate(
        self, conn: sqlite3.Connection, task_id: str, task_title: str,
        scheduled_for: str, reminder_type: str,
    ) -> dict[str, Any]:
        existing = conn.execute(
            "SELECT * FROM reminders WHERE task_id=? AND scheduled_for=? AND reminder_type=?",
            (task_id, scheduled_for, reminder_type),
        ).fetchone()
        timestamp = utc_iso(self.clock())
        if existing:
            if existing["status"] == "cancelled":
                conn.execute(
                    "UPDATE reminders SET task_title=?, status='scheduled', updated_at=?, ready_at=NULL, cancelled_at=NULL WHERE id=?",
                    (task_title, timestamp, existing["id"]),
                )
                existing = conn.execute("SELECT * FROM reminders WHERE id=?", (existing["id"],)).fetchone()
            return dict(existing)
        reminder_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO reminders (id, task_id, task_title, reminder_type, scheduled_for, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?)
        """, (reminder_id, task_id, task_title[:200], reminder_type, scheduled_for, timestamp, timestamp))
        return dict(conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone())

    def sync_task_reminder(self, task_id: str) -> None:
        timestamp = utc_iso(self.clock())
        with closing(self.db_factory()) as conn:
            task = conn.execute("SELECT id, title, due_at, status FROM tasks WHERE id=?", (task_id,)).fetchone()
            settings = conn.execute("SELECT * FROM automation_settings WHERE id=1").fetchone()
            if not task:
                conn.execute(
                    "UPDATE reminders SET status='cancelled', cancelled_at=?, updated_at=? WHERE task_id=? AND status='scheduled'",
                    (timestamp, timestamp, task_id),
                )
                conn.commit()
                return
            conn.execute(
                "UPDATE reminders SET status='cancelled', cancelled_at=?, updated_at=? WHERE task_id=? AND reminder_type='task_due' AND status='scheduled'",
                (timestamp, timestamp, task_id),
            )
            if settings and settings["reminders_enabled"] and task["due_at"] and task["status"].lower() != "completed":
                try:
                    due = parse_user_datetime(task["due_at"], settings["timezone"])
                    schedule = due - timedelta(minutes=int(settings["reminder_lead_minutes"]))
                    self._insert_or_reactivate(conn, task_id, task["title"], utc_iso(schedule), "task_due")
                except (ValueError, OverflowError, ZoneInfoNotFoundError):
                    self._event(conn, "reminder_schedule", "failed", "Task due date could not be parsed; no reminder was scheduled.")
            conn.commit()

    def sync_all_task_reminders(self) -> None:
        with closing(self.db_factory()) as conn:
            task_ids = [row["id"] for row in conn.execute("SELECT id FROM tasks WHERE lower(status) != 'completed'").fetchall()]
        for task_id in task_ids:
            self.sync_task_reminder(task_id)

    def create_custom_reminder(self, task_id: str, scheduled_for: str) -> dict[str, Any]:
        try:
            settings = self.settings()
            schedule = parse_user_datetime(scheduled_for, settings["timezone"])
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Enter a valid reminder date and time.") from exc
        with closing(self.db_factory()) as conn:
            task = conn.execute("SELECT id, title, status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise LookupError("Task was not found.")
            if task["status"].lower() == "completed":
                raise ValueError("Completed tasks cannot have a new reminder.")
            if not self.settings()["reminders_enabled"]:
                raise ValueError("Task reminders are disabled in automation settings.")
            reminder = self._insert_or_reactivate(conn, task_id, task["title"], utc_iso(schedule), "custom")
            conn.commit()
            return reminder

    def cancel_reminder(self, reminder_id: str) -> dict[str, Any] | None:
        timestamp = utc_iso(self.clock())
        with closing(self.db_factory()) as conn:
            cur = conn.execute(
                "UPDATE reminders SET status='cancelled', cancelled_at=?, updated_at=? WHERE id=? AND status='scheduled'",
                (timestamp, timestamp, reminder_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
            return dict(row) if row else None

    def reminders(self, include_history: bool = True) -> list[dict[str, Any]]:
        with closing(self.db_factory()) as conn:
            where = "" if include_history else "WHERE status IN ('scheduled', 'ready')"
            rows = conn.execute(f"SELECT * FROM reminders {where} ORDER BY scheduled_for ASC LIMIT 100").fetchall()
            return [dict(row) for row in rows]

    def process_due_reminders(self, now: datetime | None = None) -> int:
        cutoff = utc_iso(now or self.clock())
        timestamp = utc_iso(self.clock())
        with closing(self.db_factory()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id FROM reminders WHERE status='scheduled' AND scheduled_for<=? ORDER BY scheduled_for LIMIT 100",
                (cutoff,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE reminders SET status='ready', ready_at=?, updated_at=? WHERE id IN ({placeholders}) AND status='scheduled'",
                    (timestamp, timestamp, *ids),
                )
                for _ in ids:
                    self._event(conn, "reminder", "ready", "An in-app task reminder is ready to review.")
            conn.commit()
            return len(ids)

    def create_daily_plan(self, plan_date: date | None = None, *, timezone_name: str | None = None) -> dict[str, Any]:
        settings = self.settings()
        zone_name = timezone_name or settings["timezone"]
        local_date = plan_date or self.clock().astimezone(ZoneInfo(zone_name)).date()
        date_key = local_date.isoformat()
        with closing(self.db_factory()) as conn:
            existing = conn.execute("SELECT * FROM daily_plans WHERE plan_date=?", (date_key,)).fetchone()
            if existing:
                return {**dict(existing), "content": json.loads(existing["content_json"]), "created": False}
            tasks = conn.execute("""
                SELECT id, title, due_at, priority, category, status FROM tasks
                WHERE lower(status) != 'completed'
                ORDER BY CASE lower(priority) WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         COALESCE(due_at, '9999') ASC LIMIT 5
            """).fetchall()
            opportunities = conn.execute("""
                SELECT id, title, organization, deadline, type, status, source FROM opportunities
                WHERE lower(status) NOT IN ('applied', 'submitted', 'rejected', 'expired')
                ORDER BY COALESCE(deadline, '9999') ASC LIMIT 3
            """).fetchall()
            task_items = [dict(row) for row in tasks]
            opp_items = [dict(row) for row in opportunities]
            content = {
                "headline": "A focused plan from your saved AURA data",
                "focus_tasks": task_items,
                "upcoming_opportunities": opp_items,
                "task_count": len(task_items),
                "opportunity_count": len(opp_items),
                "generated_by": "deterministic rules; no AI-generated actions",
            }
            created_at = utc_iso(self.clock())
            conn.execute(
                "INSERT INTO daily_plans (plan_date, timezone, content_json, created_at, source) VALUES (?, ?, ?, ?, 'deterministic')",
                (date_key, zone_name, json.dumps(content), created_at),
            )
            self._event(conn, "daily_plan", "completed", "Created a deterministic daily plan from saved tasks and opportunities.")
            conn.commit()
            return {"plan_date": date_key, "timezone": zone_name, "content_json": json.dumps(content), "content": content, "created_at": created_at, "source": "deterministic", "created": True}

    def today_plan(self) -> dict[str, Any] | None:
        settings = self.settings()
        local_date = self.clock().astimezone(ZoneInfo(settings["timezone"])).date().isoformat()
        with closing(self.db_factory()) as conn:
            row = conn.execute("SELECT * FROM daily_plans WHERE plan_date=?", (local_date,)).fetchone()
            if not row:
                return None
            return {**dict(row), "content": json.loads(row["content_json"])}

    def process_due_daily_plan(self, now: datetime | None = None) -> bool:
        settings = self.settings()
        if not settings["daily_planning_enabled"]:
            return False
        local_now = (now or self.clock()).astimezone(ZoneInfo(settings["timezone"]))
        if local_now.strftime("%H:%M") < settings["daily_planning_time"]:
            return False
        result = self.create_daily_plan(local_now.date(), timezone_name=settings["timezone"])
        return bool(result.get("created"))

    def process_due_jobs(self) -> dict[str, Any]:
        ready = self.process_due_reminders()
        plan_created = self.process_due_daily_plan()
        return {"reminders_ready": ready, "daily_plan_created": plan_created}

    def activity(self, limit: int = 30) -> list[dict[str, Any]]:
        with closing(self.db_factory()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id, event_type, status, detail, created_at FROM automation_events ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()]


class AutomationScheduler:
    """Small SQLite-backed polling scheduler; all deliveries stay in-app."""

    def __init__(self, service: AutomationService, interval_seconds: int = 15, after_tick: Callable[[], Any] | None = None):
        self.service = service
        self.interval_seconds = interval_seconds
        self.after_tick = after_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="aura-automation", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.process_due_jobs()
                if self.after_tick is not None:
                    self.after_tick()
            except Exception as exc:
                logger.warning("Automation scheduler tick failed (%s)", type(exc).__name__)
            self._stop.wait(self.interval_seconds)
