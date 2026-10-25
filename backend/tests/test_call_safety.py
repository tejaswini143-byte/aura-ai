import os
import tempfile
import unittest
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request
from twilio.request_validator import RequestValidator

TEST_DB_PATH = tempfile.mktemp(prefix="aura-call-safety-", suffix=".db")
os.environ["DATABASE_PATH"] = TEST_DB_PATH

from app import main
from app import voice_bridge


class TestCallSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # unittest discovery imports test_api first, so its app import fixes
        # main.DB_PATH before this module is loaded. Rebind this direct-route
        # suite to its own temporary database after that module finishes.
        main.DB_PATH = TEST_DB_PATH
        main.init_db()

    def test_quiet_hours_support_overnight_windows(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Asia/Kolkata")
        self.assertTrue(main.quiet_hours_active(datetime(2026, 10, 25, 23, 30, tzinfo=zone), "22:00", "08:00"))
        self.assertTrue(main.quiet_hours_active(datetime(2026, 10, 25, 7, 59, tzinfo=zone), "22:00", "08:00"))
        self.assertFalse(main.quiet_hours_active(datetime(2026, 10, 25, 12, 0, tzinfo=zone), "22:00", "08:00"))
        self.assertFalse(main.quiet_hours_active(datetime(2026, 10, 25, 12, 0, tzinfo=zone), "08:00", "08:00"))

    def test_master_switch_blocks_calls_before_provider_client(self):
        payload = main.CallIn(phone="+919876543210", purpose="User requested check-in", consent=True, idempotency_key="manual-test-0001")
        with patch.dict(os.environ, {"AURA_OUTBOUND_CALLS_ENABLED": "false"}, clear=False):
            with self.assertRaises(main.HTTPException) as error:
                main.schedule_call(payload, self.request_with_auth())
        self.assertEqual(error.exception.status_code, 503)

    def test_call_route_requires_action_token_before_provider_access(self):
        from unittest.mock import MagicMock
        payload = main.CallIn(phone="+919876543210", purpose="User requested check-in", consent=True, idempotency_key="auth-test-key-0001")
        provider = MagicMock()
        environment = {
            "AURA_OUTBOUND_CALLS_ENABLED": "true",
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "server-only-test-token",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-only-token",
            "TWILIO_FROM_NUMBER": "+14155550123",
            "GEMINI_API_KEY": "test-only-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(main, "PUBLIC_BASE_URL", "https://voice.example.test"), patch.object(main, "TwilioClient", return_value=provider):
            with self.assertRaises(main.HTTPException) as error:
                main.schedule_call(payload, self.request_with_auth("wrong-token"))
        self.assertEqual(error.exception.status_code, 401)
        provider.assert_not_called()

    @staticmethod
    def request_with_auth(token="test-action-token"):
        path = "/api/calls"
        return Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234), "root_path": "", "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        })

    def test_twilio_signature_validation_accepts_valid_and_rejects_invalid(self):
        url = "https://example.test/api/telephony/status/call-123"
        params = {"CallStatus": "completed"}
        token = "unit-test-only-token"
        valid = RequestValidator(token).compute_signature(url, params)
        path = "/api/telephony/status/call-123"

        def request_for(signature):
            return Request({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "POST", "scheme": "http", "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234), "root_path": "", "path": path,
                "raw_path": path.encode(), "query_string": b"",
                "headers": [(b"x-twilio-signature", signature.encode())],
            })

        with patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": token}, clear=False), patch.object(main, "PUBLIC_BASE_URL", "https://example.test"):
            main.verify_twilio_signature(request_for(valid), params)
            with self.assertRaises(main.HTTPException) as error:
                main.verify_twilio_signature(request_for("invalid"), params)
        self.assertEqual(error.exception.status_code, 403)

    def test_provider_status_never_claims_live_audio(self):
        with patch.dict(os.environ, {"AURA_OUTBOUND_CALLS_ENABLED": "false"}, clear=False):
            status = main.provider_status()
        self.assertFalse(status["twilio"]["manual_calls_enabled"])
        self.assertFalse(status["twilio"]["automatic_calls_enabled"])
        self.assertFalse(status["gemini_live"]["voice_bridge_enabled"])
        self.assertFalse(status["dotvoice"]["calls_enabled"])
        self.assertNotIn("api_key", str(status).lower())

    def test_call_idempotency_key_validation(self):
        payload = main.CallIn(phone="+919876543210", purpose="Consent check", consent=True, idempotency_key="safe-key:0001")
        self.assertEqual(payload.idempotency_key, "safe-key:0001")
        with self.assertRaises(ValueError):
            main.CallIn(phone="+919876543210", purpose="Consent check", consent=True, idempotency_key="bad key")

    def test_audio_conversion_between_twilio_and_gemini_formats(self):
        mulaw = bytes([voice_bridge.encode_mulaw_sample(value) for value in (0, 1000, -1000, 8000)])
        pcm16 = voice_bridge.mulaw_8k_to_pcm16_16k(mulaw)
        self.assertEqual(len(pcm16), len(mulaw) * 4)
        converter = voice_bridge.Pcm24kToMulaw8k()
        pcm24 = b"".join(int(value).to_bytes(2, "little", signed=True) for value in (0, 400, 800, 1200, 1600, 2000))
        all_at_once = converter.convert(pcm24)
        chunked_converter = voice_bridge.Pcm24kToMulaw8k()
        chunked = chunked_converter.convert(pcm24[:5]) + chunked_converter.convert(pcm24[5:])
        self.assertEqual(len(all_at_once), 2)
        self.assertEqual(chunked, all_at_once)

    def test_twilio_events_stream_audio_to_gemini_and_stop_cleanly(self):
        class FakeWebSocket:
            def __init__(self, messages):
                self.messages = iter(messages)

            async def receive_json(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    return {"event": "stop"}

        class FakeGeminiSession:
            def __init__(self):
                self.inputs = []

            async def send_realtime_input(self, **kwargs):
                self.inputs.append(kwargs)

        audio = base64.b64encode(bytes([255, 127, 0, 128])).decode("ascii")
        messages = [
            {"event": "connected"},
            {"event": "start", "start": {"callSid": "CA-test", "streamSid": "MZ-test", "customParameters": {"callId": "call-test"}, "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}},
            {"event": "media", "streamSid": "MZ-test", "media": {"track": "inbound", "payload": audio}},
            {"event": "stop"},
        ]
        session = FakeGeminiSession()
        stream = {"call_sid": "CA-test", "call_id": "call-test"}
        ready = asyncio.Event()
        asyncio.run(voice_bridge.relay_twilio_media_to_gemini(FakeWebSocket(messages), session, stream, ready))
        self.assertTrue(ready.is_set())
        self.assertEqual(stream["stream_sid"], "MZ-test")
        self.assertEqual(len(session.inputs), 1)
        self.assertEqual(session.inputs[0]["audio"].mime_type, "audio/pcm;rate=16000")
        self.assertEqual(len(session.inputs[0]["audio"].data), 16)

    def test_gemini_audio_is_sent_as_twilio_media_and_interrupt_clears_buffer(self):
        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, value):
                self.sent.append(value)

        class FakeGeminiSession:
            async def receive(self):
                audio = bytes(960)  # 20 ms PCM16 at 24 kHz -> 160 mu-law samples
                part = SimpleNamespace(inline_data=SimpleNamespace(data=audio, mime_type="audio/pcm;rate=24000"))
                content = SimpleNamespace(interrupted=True, model_turn=SimpleNamespace(parts=[part]))
                yield SimpleNamespace(server_content=content)

        websocket = FakeWebSocket()
        stream = {"stream_sid": "MZ-test"}
        ready = asyncio.Event(); ready.set()
        asyncio.run(voice_bridge.relay_gemini_audio_to_twilio(websocket, FakeGeminiSession(), stream, ready))
        self.assertEqual(websocket.sent[0], {"event": "clear", "streamSid": "MZ-test"})
        self.assertEqual(websocket.sent[1]["event"], "media")
        self.assertEqual(len(base64.b64decode(websocket.sent[1]["media"]["payload"])), 160)

    def test_live_stream_rejects_wrong_call_sid(self):
        class FakeWebSocket:
            async def receive_json(self):
                return {"event": "start", "start": {"callSid": "CA-other", "streamSid": "MZ-test", "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}}

            async def send_json(self, _message):
                pass

        class FakeGeminiSession:
            async def receive(self):
                if False:
                    yield None
                raise RuntimeError("mock provider failure")

            async def send_realtime_input(self, **_kwargs):
                pass

        with self.assertRaises(ValueError):
            asyncio.run(voice_bridge.run_bidirectional_bridge(FakeWebSocket(), FakeGeminiSession(), expected_call_sid="CA-expected"))

    def test_live_stream_propagates_provider_failure_and_cleans_up(self):
        class FakeWebSocket:
            def __init__(self):
                self.cancelled = False
                self.first = True

            async def receive_json(self):
                if self.first:
                    self.first = False
                    return {"event": "start", "start": {"callSid": "CA-expected", "streamSid": "MZ-test", "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}}
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

            async def send_json(self, _message):
                pass

        class FailingGeminiSession:
            async def receive(self):
                if False:
                    yield None
                raise RuntimeError("mock provider failure")

            async def send_realtime_input(self, **_kwargs):
                pass

        websocket = FakeWebSocket()
        with self.assertRaises(RuntimeError):
            asyncio.run(voice_bridge.run_bidirectional_bridge(websocket, FailingGeminiSession(), expected_call_sid="CA-expected"))
        self.assertTrue(websocket.cancelled)

    def test_stream_signature_and_twiml_use_secure_bidirectional_endpoint(self):
        token = "unit-test-only-token"
        url = "wss://voice.example.test/aura/api/telephony/stream/call-123"
        signature = RequestValidator(token).compute_signature(url, {})
        self.assertTrue(voice_bridge.twilio_websocket_signature_valid(url, signature, token))
        self.assertFalse(voice_bridge.twilio_websocket_signature_valid(url, "invalid", token))
        with patch.object(main, "PUBLIC_BASE_URL", "https://voice.example.test/aura"):
            xml = main.build_live_stream_twiml("call-123")
        self.assertIn("<Connect>", xml)
        self.assertIn("wss://voice.example.test/aura/api/telephony/stream/call-123", xml)
        self.assertIn("name=\"callId\"", xml)
        self.assertIn("<Hangup />", xml)

    def test_duplicate_call_request_does_not_call_twilio_twice(self):
        from contextlib import closing
        from unittest.mock import MagicMock

        phone = "+919876543210"
        with closing(main.db()) as conn:
            conn.execute("UPDATE companion_preferences SET calling_consent=1, phone_verified=1, destination_phone=?, timezone='UTC', quiet_hours_start='00:00', quiet_hours_end='00:00' WHERE id=1", (phone,))
            conn.commit()
        provider = MagicMock()
        provider.calls.create.return_value.sid = "CA-mocked"
        payload = main.CallIn(phone=phone, purpose="Mocked duplicate safety test", consent=True, idempotency_key="duplicate-test-key-001")
        environment = {
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "test-action-token",
            "AURA_OUTBOUND_CALLS_ENABLED": "true",
            "AURA_MAX_CALLS_PER_DAY": "3",
            "AURA_MAX_CALL_DURATION_SECONDS": "120",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-only-token",
            "TWILIO_FROM_NUMBER": "+14155550123",
            "GEMINI_API_KEY": "test-only-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(main, "PUBLIC_BASE_URL", "https://voice.example.test"), patch.object(main, "TwilioClient", return_value=provider):
            first = main.schedule_call(payload, self.request_with_auth())
            duplicate = main.schedule_call(payload, self.request_with_auth())
        provider.calls.create.assert_called_once()
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(duplicate["idempotent_replay"])
        self.assertEqual(first["id"], duplicate["id"])

def tearDownModule():
    try:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
