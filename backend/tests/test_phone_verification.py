import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from starlette.responses import Response

TEST_DB_PATH = tempfile.mktemp(prefix="aura-phone-verification-", suffix=".db")
os.environ["DATABASE_PATH"] = TEST_DB_PATH
from app import main
from app.auth import token_hash
from app.phone_verification import VerificationStore, E164_PATTERN, action_token_valid


def request_with_token(token="local-test-token"):
    path = "/api/phone-verification/start"
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234), "root_path": "", "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


def request_with_session(cookie, csrf=None, origin="http://localhost:3000"):
    path = "/api/auth/session"
    headers = [(b"cookie", f"aura_admin_session={cookie}".encode()), (b"origin", origin.encode())]
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234), "root_path": "", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": headers,
    })


def login_request(origin="http://localhost:3000", client="127.0.0.1"):
    path = "/api/auth/login"
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "server": ("testserver", 80),
        "client": (client, 1234), "root_path": "", "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"origin", origin.encode())],
    })


class TestPhoneVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.DB_PATH = TEST_DB_PATH
        main.init_db()

    def setUp(self):
        with closing(main.db()) as conn:
            conn.execute("UPDATE companion_preferences SET destination_phone='+919876543210', phone_storage_consent=1, phone_verified=0 WHERE id=1")
            conn.execute("DELETE FROM phone_verification_attempts")
            conn.execute("DELETE FROM admin_sessions")
            conn.execute("DELETE FROM admin_login_limits")
            conn.commit()

    def test_e164_and_action_auth_are_fail_closed(self):
        self.assertTrue(E164_PATTERN.fullmatch("+919876543210"))
        self.assertFalse(E164_PATTERN.fullmatch("9876543210"))
        self.assertTrue(action_token_valid("x", "x"))
        self.assertFalse(action_token_valid("x", "y"))
        self.assertFalse(action_token_valid(None, "x"))

    def test_code_lifecycle_expiry_attempts_and_single_use(self):
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        store = VerificationStore(main.db, clock=lambda: now[0])
        key = store.reserve_send("+919876543210", sms_consent=True)
        store.mark_sent(key)
        with self.assertRaises(TimeoutError):
            store.reserve_send("+919876543210", sms_consent=True)
        checked = store.begin_check("+919876543210", "123456")
        self.assertEqual(checked, key)
        self.assertFalse(store.finish_check(key, False))
        self.assertTrue(store.finish_check(key, True))
        with self.assertRaises(LookupError):
            store.begin_check("+919876543210", "123456")

        store.invalidate("+919876543210")
        key = store.reserve_send("+919876543210", sms_consent=True)
        store.mark_sent(key)
        now[0] += timedelta(minutes=11)
        with self.assertRaises(TimeoutError):
            store.begin_check("+919876543210", "123456")

    def test_verification_attempt_limit_locks_the_challenge(self):
        store = VerificationStore(main.db)
        key = store.reserve_send("+919876543210", sms_consent=True)
        store.mark_sent(key)
        for _ in range(5):
            store.begin_check("+919876543210", "123456")
            self.assertFalse(store.finish_check(key, False))
        with self.assertRaises(LookupError):
            store.begin_check("+919876543210", "123456")

    def test_admin_login_success_failure_missing_config_and_cookie_flags(self):
        from app.main import AdminLoginIn
        good_password = "test-admin-password-with-enough-length"
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(main.HTTPException) as missing:
                main.admin_login(AdminLoginIn(password=good_password), login_request(), Response())
            self.assertEqual(missing.exception.status_code, 503)
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": good_password, "AURA_ENVIRONMENT": "production", "AURA_ACTIONS_API_TOKEN": ""}, clear=False):
            with self.assertRaises(main.HTTPException) as wrong:
                main.admin_login(AdminLoginIn(password="wrong-password"), login_request(client="192.0.2.50"), Response())
            self.assertEqual(wrong.exception.status_code, 401)
            response = Response()
            result = main.admin_login(AdminLoginIn(password=good_password), login_request(client="192.0.2.50"), response)
            self.assertTrue(result["authenticated"])
            cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
            self.assertIn("httponly", response.headers["set-cookie"].lower())
            self.assertIn("secure", response.headers["set-cookie"].lower())
            self.assertIn("samesite=strict", response.headers["set-cookie"].lower())
            self.assertNotIn(good_password, str(result))
            session_request = request_with_session(cookie, result["csrf_token"])
            self.assertTrue(main.auth_session(session_request, Response())["authenticated"])
            main.require_action_authorization(session_request)
            with self.assertRaises(main.HTTPException) as csrf_error:
                main.require_action_authorization(request_with_session(cookie))
            self.assertEqual(csrf_error.exception.status_code, 403)
            logout_response = Response()
            self.assertFalse(main.admin_logout(session_request, logout_response)["authenticated"])
            self.assertFalse(main.auth_session(session_request, Response())["authenticated"])

    def test_login_rejects_untrusted_origin_and_missing_password_disables_actions(self):
        from app.main import AdminLoginIn
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length", "AURA_ACTIONS_API_TOKEN": ""}, clear=False):
            with self.assertRaises(main.HTTPException) as origin_error:
                main.admin_login(AdminLoginIn(password="test-admin-password-with-enough-length"), login_request(origin="https://attacker.invalid"), Response())
            self.assertEqual(origin_error.exception.status_code, 403)
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "", "AURA_ACTIONS_API_TOKEN": ""}, clear=False):
            with self.assertRaises(main.HTTPException) as config_error:
                main.require_action_authorization(request_with_token(""))
        self.assertEqual(config_error.exception.status_code, 503)

    def test_expired_session_is_rejected_and_unauthenticated_action_fails(self):
        session_token, _csrf = main.admin_sessions.create()
        with closing(main.db()) as conn:
            conn.execute("UPDATE admin_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE session_hash=?", (token_hash(session_token),))
            conn.commit()
        expired = main.auth_session(request_with_session(session_token), Response())
        self.assertFalse(expired["authenticated"])
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length", "AURA_ACTIONS_API_TOKEN": ""}, clear=False):
            with self.assertRaises(main.HTTPException) as unauthorized:
                main.require_action_authorization(request_with_token(""))
        self.assertEqual(unauthorized.exception.status_code, 401)

    def test_login_rate_limit_locks_after_five_failures(self):
        from app.main import AdminLoginIn
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length"}, clear=False):
            for _ in range(5):
                with self.assertRaises(main.HTTPException) as failure:
                    main.admin_login(AdminLoginIn(password="wrong-password"), login_request(client="192.0.2.51"), Response())
                self.assertEqual(failure.exception.status_code, 401)
            with self.assertRaises(main.HTTPException) as locked:
                main.admin_login(AdminLoginIn(password="test-admin-password-with-enough-length"), login_request(client="192.0.2.51"), Response())
        self.assertEqual(locked.exception.status_code, 429)

    def test_provider_send_is_explicit_mocked_and_never_marks_verified(self):
        fake = MagicMock()
        fake.verify.v2.services.return_value.verifications.create.return_value = MagicMock(status="pending")
        env = {
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "local-test-token",
            "AURA_PHONE_VERIFICATION_ENABLED": "true",
            "TWILIO_VERIFY_SERVICE_SID": "VA-test",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(main, "TwilioClient", return_value=fake):
            result = main.start_phone_verification(main.PhoneVerificationStart(sms_consent=True), request_with_token())
        self.assertEqual(result["status"], "pending")
        fake.verify.v2.services.return_value.verifications.create.assert_called_once_with(
            to="+919876543210", channel="sms"
        )
        with closing(main.db()) as conn:
            self.assertEqual(conn.execute("SELECT phone_verified FROM companion_preferences WHERE id=1").fetchone()[0], 0)

    def test_sms_consent_is_required_before_provider_send(self):
        fake = MagicMock()
        env = {
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "local-test-token",
            "AURA_PHONE_VERIFICATION_ENABLED": "true",
            "TWILIO_VERIFY_SERVICE_SID": "VA-test",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(main, "TwilioClient", return_value=fake):
            with self.assertRaises(main.HTTPException) as rejected:
                main.start_phone_verification(main.PhoneVerificationStart(sms_consent=False), request_with_token())
        self.assertEqual(rejected.exception.status_code, 400)
        fake.assert_not_called()

    def test_only_provider_approval_marks_saved_number_verified(self):
        from app.main import PhoneVerificationCheck
        fake = MagicMock()
        fake.verify.v2.services.return_value.verifications.create.return_value = MagicMock(status="pending")
        fake.verify.v2.services.return_value.verification_checks.create.return_value = MagicMock(status="approved")
        env = {
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "local-test-token",
            "AURA_PHONE_VERIFICATION_ENABLED": "true",
            "TWILIO_VERIFY_SERVICE_SID": "VA-test",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(main, "TwilioClient", return_value=fake):
            main.start_phone_verification(main.PhoneVerificationStart(sms_consent=True), request_with_token())
            result = main.check_phone_verification(PhoneVerificationCheck(code="123456"), request_with_token())
        self.assertEqual(result["status"], "verified")
        fake.verify.v2.services.return_value.verification_checks.create.assert_called_once_with(
            to="+919876543210", code="123456"
        )
        with closing(main.db()) as conn:
            self.assertEqual(conn.execute("SELECT phone_verified FROM companion_preferences WHERE id=1").fetchone()[0], 1)

    def test_provider_nonapproval_never_marks_number_verified(self):
        fake = MagicMock()
        fake.verify.v2.services.return_value.verifications.create.return_value = MagicMock(status="pending")
        fake.verify.v2.services.return_value.verification_checks.create.return_value = MagicMock(status="pending")
        env = {
            "AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length",
            "AURA_ACTIONS_API_TOKEN": "local-test-token",
            "AURA_PHONE_VERIFICATION_ENABLED": "true",
            "TWILIO_VERIFY_SERVICE_SID": "VA-test",
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_AUTH_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(main, "TwilioClient", return_value=fake):
            main.start_phone_verification(main.PhoneVerificationStart(sms_consent=True), request_with_token())
            with self.assertRaises(main.HTTPException) as rejected:
                main.check_phone_verification(main.PhoneVerificationCheck(code="123456"), request_with_token())
        self.assertEqual(rejected.exception.status_code, 400)
        with closing(main.db()) as conn:
            self.assertEqual(conn.execute("SELECT phone_verified FROM companion_preferences WHERE id=1").fetchone()[0], 0)

    def test_missing_setup_and_bad_auth_do_not_call_provider(self):
        provider = MagicMock()
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length", "AURA_ACTIONS_API_TOKEN": "local-test-token", "AURA_PHONE_VERIFICATION_ENABLED": "false"}, clear=False), patch.object(main, "TwilioClient", return_value=provider):
            with self.assertRaises(main.HTTPException) as disabled:
                main.start_phone_verification(main.PhoneVerificationStart(sms_consent=True), request_with_token())
            self.assertEqual(disabled.exception.status_code, 503)
        with patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length", "AURA_ACTIONS_API_TOKEN": "local-test-token"}, clear=False), patch.object(main, "TwilioClient", return_value=provider):
            with self.assertRaises(main.HTTPException) as unauthorized:
                main.start_phone_verification(main.PhoneVerificationStart(sms_consent=True), request_with_token("wrong"))
            self.assertEqual(unauthorized.exception.status_code, 401)
        provider.assert_not_called()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
