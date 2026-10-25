"""Evidence-based Twilio webhook and Gemini Live health for AURA."""
from __future__ import annotations

import logging
import os
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from twilio.rest import Client as TwilioClient

router = APIRouter(tags=["telephony"])
logger = logging.getLogger(__name__)


def _main():
    from app import main
    return main


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_health_table() -> None:
    main = _main()
    with closing(main.db()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telephony_health (
                id INTEGER PRIMARY KEY CHECK (id=1),
                inbound_webhook_at TEXT,
                wss_handshake_at TEXT,
                gemini_live_at TEXT,
                last_error TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO telephony_health(id) VALUES (1)")
        conn.commit()


def mark_health(**fields: str | None) -> None:
    allowed = {"inbound_webhook_at", "wss_handshake_at", "gemini_live_at", "last_error"}
    if not fields or not set(fields).issubset(allowed):
        raise ValueError("Unsupported telephony health field")
    init_health_table()
    main = _main()
    updates = ", ".join(f"{name}=?" for name in fields)
    with closing(main.db()) as conn:
        conn.execute(f"UPDATE telephony_health SET {updates} WHERE id=1", tuple(fields.values()))
        conn.commit()


@router.post("/api/telephony/incoming")
async def incoming_voice_webhook(request: Request) -> Response:
    """Receive an inbound Twilio call and connect it to the existing Live bridge."""
    main = _main()
    try:
        form = await request.form()
        main.verify_twilio_signature(request, dict(form))
        call_sid = str(form.get("CallSid") or "")
        caller = str(form.get("From") or "")
        if not call_sid or not caller:
            raise HTTPException(400, "Twilio inbound webhook is missing CallSid or From.")
        call_id = __import__("uuid").uuid4().hex
        with closing(main.db()) as conn:
            conn.execute(
                "INSERT INTO calls(id, phone, purpose, consent, status, twilio_sid, transcript, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (call_id, caller, "Incoming AURA voice call", 0, "in-progress", call_sid, "", main.now_iso()),
            )
            conn.commit()
        mark_health(inbound_webhook_at=now_iso(), last_error=None)
        return Response(content=main.build_live_stream_twiml(call_id), media_type="application/xml")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Inbound webhook failed (%s).", type(exc).__name__)
        try:
            mark_health(last_error="inbound_webhook_error")
        except Exception:
            pass
        raise HTTPException(502, "The inbound voice webhook could not create a live call.") from exc


def telephony_status() -> dict[str, Any]:
    """Check configured Twilio webhook and report only observed live connections as verified."""
    main = _main()
    init_health_table()
    with closing(main.db()) as conn:
        row = conn.execute("SELECT * FROM telephony_health WHERE id=1").fetchone()
        prefs = conn.execute("""SELECT calling_consent, phone_storage_consent, phone_verified,
            destination_phone, automatic_mood_calls, reminder_calls_enabled FROM companion_preferences WHERE id=1""").fetchone()
    health = dict(row) if row else {}
    credentials_ready = all(os.getenv(key, "").strip() for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))
    public_https = main.PUBLIC_BASE_URL.startswith("https://")
    expected_url = None
    webhook_configured = False
    provider_check_error = None
    if credentials_ready and public_https:
        try:
            expected_url = main.public_https_url("/api/telephony/incoming")
            client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
            numbers = client.incoming_phone_numbers.list(phone_number=os.environ["TWILIO_FROM_NUMBER"], limit=5)
            number = next((item for item in numbers if getattr(item, "phone_number", None) == os.environ["TWILIO_FROM_NUMBER"]), None)
            actual_url = (getattr(number, "voice_url", "") or "").rstrip("/") if number else ""
            method = (getattr(number, "voice_method", "POST") or "POST").upper() if number else ""
            webhook_configured = bool(number and actual_url == expected_url.rstrip("/") and method == "POST")
        except Exception as exc:
            provider_check_error = type(exc).__name__
    verification_available = bool(
        os.getenv("AURA_PHONE_VERIFICATION_ENABLED", "false").strip().lower() == "true"
        and os.getenv("TWILIO_VERIFY_SERVICE_SID", "").strip()
        and main.admin_password_configured() and credentials_ready
    )
    manual_ready = bool(
        main.outbound_calls_enabled() and main.admin_password_configured() and credentials_ready
        and public_https and os.getenv("GEMINI_API_KEY", "").strip() and prefs
        and prefs["calling_consent"] and prefs["phone_storage_consent"]
        and prefs["phone_verified"] and prefs["destination_phone"]
    )
    proactive_switch = os.getenv("AURA_PROACTIVE_CALLS_ENABLED", "false").strip().lower() == "true"
    auto_opt_in = bool(prefs and (prefs["automatic_mood_calls"] or prefs["reminder_calls_enabled"]))
    automatic_ready = bool(
        proactive_switch and main.outbound_calls_enabled() and manual_ready
        and auto_opt_in and main.admin_password_configured()
        and os.getenv("AURA_ACTIONS_API_TOKEN", "").strip()
    )
    return {
        "manual_calls_enabled": manual_ready,
        "manual_call_route": "/api/calls",
        "automatic_calls_enabled": automatic_ready,
        "automatic_calls_configured": automatic_ready,
        "automatic_calls_reason": (
            "Automatic dispatch is configured for saved opt-ins; actual delivery still depends on a successful Twilio and Gemini Live call."
            if automatic_ready else
            "Requires both outbound-call switches, admin password and server-only action token, valid provider setup, a verified opted-in destination, and an automatic-call preference."
        ),
        "incoming_webhook_configured": webhook_configured,
        "incoming_webhook_expected_url": expected_url,
        "incoming_webhook_last_received_at": health.get("inbound_webhook_at"),
        "incoming_webhook_check_error": provider_check_error,
        "live_wss_connection_verified": bool(health.get("wss_handshake_at")),
        "live_wss_last_connected_at": health.get("wss_handshake_at"),
        "gemini_live_access_verified": bool(health.get("gemini_live_at")),
        "gemini_live_last_connected_at": health.get("gemini_live_at"),
        "voice_bridge_implemented": True,
        "last_telephony_error": health.get("last_error"),
        "destination_verification_available": verification_available,
    }


# Imported by app.main after its database and route declarations are initialized.
init_health_table()
