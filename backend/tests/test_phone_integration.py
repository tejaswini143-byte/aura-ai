import os
import tempfile
import unittest
from contextlib import closing
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from fastapi import HTTPException

TEST_DB_PATH = tempfile.mktemp(prefix="aura-phone-integration-", suffix=".db")
os.environ["DATABASE_PATH"] = TEST_DB_PATH
from app import main, telephony_health


def make_request(path="/api/telephony/configure-incoming-webhook"):
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("aura.example", 443),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    })


class FakeTwilioNumber:
    def __init__(self, phone_number="+14155550100"):
        self.phone_number = phone_number
        self.voice_url = "https://old.example/voice"
        self.voice_method = "GET"
        self.updated_with = None

    def update(self, **kwargs):
        self.updated_with = kwargs
        self.voice_url = kwargs["voice_url"]
        self.voice_method = kwargs["voice_method"]
        return self


class TestPhoneIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.DB_PATH = TEST_DB_PATH
        main.init_db()

    def setUp(self):
        with closing(main.db()) as conn:
            conn.execute(
                "UPDATE companion_preferences SET destination_phone='+919876543210', phone_storage_consent=1, phone_verified=1, calling_consent=1 WHERE id=1"
            )
            conn.commit()

    def test_configure_incoming_webhook_updates_only_the_configured_twilio_number(self):
        number = FakeTwilioNumber()
        fake_client = MagicMock()
        fake_client.incoming_phone_numbers.list.return_value = [number]
        env = {
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
            "TWILIO_FROM_NUMBER": "+14155550100",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(main, "PUBLIC_BASE_URL", "https://aura.example"), \
             patch.object(main, "require_action_authorization"), \
             patch.object(telephony_health, "TwilioClient", return_value=fake_client):
            result = telephony_health.configure_incoming_webhook(make_request())

        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://aura.example/api/telephony/incoming")
        self.assertEqual(number.updated_with, {
            "voice_url": "https://aura.example/api/telephony/incoming",
            "voice_method": "POST",
        })
        fake_client.incoming_phone_numbers.list.assert_called_once_with(
            phone_number="+14155550100", limit=5
        )

    def test_configure_webhook_fails_if_caller_id_is_not_an_incoming_number(self):
        fake_client = MagicMock()
        fake_client.incoming_phone_numbers.list.return_value = []
        env = {
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
            "TWILIO_FROM_NUMBER": "+14155550100",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(main, "PUBLIC_BASE_URL", "https://aura.example"), \
             patch.object(main, "require_action_authorization"), \
             patch.object(telephony_health, "TwilioClient", return_value=fake_client):
            with self.assertRaises(HTTPException) as rejected:
                telephony_health.configure_incoming_webhook(make_request())
        self.assertEqual(rejected.exception.status_code, 404)

    def test_manual_test_call_uses_only_the_saved_verified_destination(self):
        captured = {}

        def fake_schedule(payload, request):
            captured["payload"] = payload
            return {"status": "test-only-stub"}

        with patch.object(main, "require_action_authorization"), \
             patch.object(main, "schedule_call", side_effect=fake_schedule):
            result = main.schedule_verified_number_test_call(make_request("/api/calls/test"))

        self.assertEqual(result["status"], "test-only-stub")
        self.assertEqual(captured["payload"].phone, "+919876543210")
        self.assertTrue(captured["payload"].consent)
        self.assertEqual(captured["payload"].purpose, "AURA voice integration test")

    def test_manual_test_call_rejects_unverified_destination(self):
        with closing(main.db()) as conn:
            conn.execute("UPDATE companion_preferences SET phone_verified=0 WHERE id=1")
            conn.commit()
        with patch.object(main, "require_action_authorization"), \
             patch.object(main, "schedule_call") as schedule:
            with self.assertRaises(HTTPException) as rejected:
                main.schedule_verified_number_test_call(make_request("/api/calls/test"))
        self.assertEqual(rejected.exception.status_code, 403)
        schedule.assert_not_called()

    def test_gnani_diagnostics_rejects_missing_credentials_without_provider_access(self):
        with patch.object(main, "require_action_authorization"), \
             patch.dict(os.environ, {"GNANI_API_KEY": ""}, clear=False):
            with self.assertRaises(HTTPException) as rejected:
                import asyncio
                asyncio.run(telephony_health.test_gnani_connections(make_request("/api/providers/gnani/test")))
        self.assertEqual(rejected.exception.status_code, 503)

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
