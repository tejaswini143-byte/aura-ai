"""Evidence-based Twilio webhook and Gemini Live health for AURA."""
from __future__ import annotations

import logging
import json
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
                gnani_stt_at TEXT,
                gnani_tts_at TEXT,
                last_error TEXT
            )
        """)
        columns = {column[1] for column in conn.execute("PRAGMA table_info(telephony_health)").fetchall()}
        for name in ("gnani_stt_at", "gnani_tts_at"):
            if name not in columns:
                conn.execute(f"ALTER TABLE telephony_health ADD COLUMN {name} TEXT")
        conn.execute("INSERT OR IGNORE INTO telephony_health(id) VALUES (1)")
        conn.commit()


def mark_health(**fields: str | None) -> None:
    allowed = {"inbound_webhook_at", "wss_handshake_at", "gemini_live_at", "gnani_stt_at", "gnani_tts_at", "last_error"}
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



@router.post("/api/telephony/configure-incoming-webhook")
def configure_incoming_webhook(request: Request) -> dict[str, Any]:
    """Point the configured Twilio number at AURA's signed inbound voice webhook."""
    main = _main()
    main.require_action_authorization(request)
    required = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    if any(not os.getenv(key, "").strip() for key in required):
        raise HTTPException(503, "Twilio account credentials and caller ID must be configured first.")
    if not main.PUBLIC_BASE_URL.startswith("https://"):
        raise HTTPException(503, "AURA_PUBLIC_BASE_URL must be a public HTTPS URL before configuring Twilio.")
    expected_url = main.public_https_url("/api/telephony/incoming")
    try:
        client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        phone_number = os.environ["TWILIO_FROM_NUMBER"].strip()
        numbers = client.incoming_phone_numbers.list(phone_number=phone_number, limit=5)
        number = next((item for item in numbers if getattr(item, "phone_number", None) == phone_number), None)
        if number is None:
            raise HTTPException(404, "The configured Twilio caller ID was not found as an incoming phone number in this account.")
        updated = number.update(voice_url=expected_url, voice_method="POST")
        configured = (
            (getattr(updated, "voice_url", "") or "").rstrip("/") == expected_url.rstrip("/")
            and (getattr(updated, "voice_method", "POST") or "POST").upper() == "POST"
        )
        if not configured:
            raise HTTPException(502, "Twilio did not confirm the requested incoming webhook configuration.")
        return {"ok": True, "incoming_webhook_configured": True, "url": expected_url, "method": "POST"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Twilio incoming webhook configuration failed (%s).", type(exc).__name__)
        raise HTTPException(502, "Twilio could not update the incoming voice webhook. Check the Twilio number's voice permissions.") from exc


@router.post("/api/providers/gnani/test")
async def test_gnani_connections(request: Request) -> dict[str, Any]:
    """Verify real Gnani STT handshake and TTS audio for Telugu, Hindi and English."""
    main = _main()
    main.require_action_authorization(request)
    api_key = os.getenv("GNANI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "GNANI_API_KEY is missing from the backend environment.")
    if main.selected_voice_engine() != "gnani":
        raise HTTPException(409, "Select Gnani as AURA_VOICE_ENGINE before running this test.")

    import asyncio
    import httpx
    import websockets
    from app.gnani_bridge import STT_URL, TTS_URL, raw_wave_data, tts_payload

    result: dict[str, Any] = {"stt": {"ok": False}, "tts": {}, "ok": False}
    mark_health(gnani_stt_at=None, gnani_tts_at=None, last_error=None)
    try:
        headers = {
            "x-api-key-id": api_key,
            "lang_code": os.getenv("GNANI_LANGUAGE", "te-IN").strip() or "te-IN",
            "x-sample-rate": "8000",
            "x-format": "transcribe",
        }
        async with websockets.connect(
            STT_URL,
            additional_headers=headers,
            open_timeout=12,
            close_timeout=3,
            ping_interval=20,
            max_size=1_000_000,
        ) as stt:
            raw_event = await asyncio.wait_for(stt.recv(), timeout=12)
            event = json.loads(raw_event)
            if event.get("type") != "connected":
                raise RuntimeError("STT did not return a connected event")
        mark_health(gnani_stt_at=now_iso(), last_error=None)
        result["stt"] = {"ok": True, "sample_rate": 8000, "format": "PCM16 mono"}
    except Exception as exc:
        logger.warning("Gnani STT connection test failed (%s).", type(exc).__name__)
        result["stt"] = {"ok": False, "error": type(exc).__name__}

    async with httpx.AsyncClient(timeout=25.0) as client:
        for language, locale, sample_text in (
            ("telugu", "te-IN", "నమస్కారం. ఇది ఆరా వాయిస్ పరీక్ష."),
            ("hindi", "hi-IN", "नमस्ते। यह आरा की आवाज़ की जाँच है।"),
            ("english", "en-IN", "Hello. This is an AURA voice test."),
        ):
            preferences = {"preferred_language": language}
            try:
                payload = tts_payload(sample_text, preferences)
                response = await client.post(
                    TTS_URL,
                    headers={"X-API-Key-ID": api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                audio = raw_wave_data(response.content)
                if not audio:
                    raise RuntimeError("TTS returned empty audio")
                result["tts"][locale] = {
                    "ok": True,
                    "voice": payload["voice"],
                    "audio_bytes": len(audio),
                    "format": "8 kHz G.711 mu-law",
                }
            except Exception as exc:
                logger.warning("Gnani TTS test failed for %s (%s).", locale, type(exc).__name__)
                result["tts"][locale] = {"ok": False, "error": type(exc).__name__}

    tts_all_ok = bool(all(item.get("ok") for item in result["tts"].values()) and len(result["tts"]) == 3)
    if tts_all_ok:
        mark_health(gnani_tts_at=now_iso(), last_error=None)
    result["ok"] = bool(result["stt"]["ok"] and tts_all_ok)
    if not result["ok"]:
        try:
            mark_health(last_error="gnani_connection_test_failed")
        except Exception:
            pass
        result["message"] = "Gnani test completed with one or more failures. Review the STT and language-specific TTS results."
    else:
        result["message"] = "Gnani STT and Telugu, Hindi, and English TTS connections all passed."
    return result

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
    if main.selected_voice_engine() == "gnani":
        voice_provider_ready = bool(health.get("gnani_stt_at") and health.get("gnani_tts_at"))
    else:
        voice_provider_ready = bool(os.getenv("GEMINI_API_KEY", "").strip())
    manual_ready = bool(
        voice_provider_ready and main.outbound_calls_enabled() and main.admin_password_configured() and credentials_ready
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
        "voice_provider_ready": voice_provider_ready,
        "manual_call_route": "/api/calls",
        "automatic_calls_enabled": automatic_ready,
        "automatic_calls_configured": automatic_ready,
        "automatic_calls_reason": (
            "Automatic dispatch is configured for saved opt-ins; actual delivery still depends on a successful Twilio and Gemini Live call."
            if automatic_ready else
            "Requires manual calling to be enabled, provider diagnostics to pass, admin authentication, a verified destination, and explicit calling consent."
        ),
        "incoming_webhook_configured": webhook_configured,
        "incoming_webhook_expected_url": expected_url,
        "incoming_webhook_last_received_at": health.get("inbound_webhook_at"),
        "incoming_webhook_check_error": provider_check_error,
        "live_wss_connection_verified": bool(health.get("wss_handshake_at")),
        "live_wss_last_connected_at": health.get("wss_handshake_at"),
        "gemini_live_access_verified": bool(health.get("gemini_live_at")),
        "gemini_live_last_connected_at": health.get("gemini_live_at"),
        "gnani_stt_connection_verified": bool(health.get("gnani_stt_at")),
        "gnani_stt_last_connected_at": health.get("gnani_stt_at"),
        "gnani_tts_connection_verified": bool(health.get("gnani_tts_at")),
        "gnani_tts_last_connected_at": health.get("gnani_tts_at"),
        "voice_bridge_implemented": True,
        "last_telephony_error": health.get("last_error"),
        "destination_verification_available": verification_available,
    }


# Imported by app.main after its database and route declarations are initialized.
init_health_table()
