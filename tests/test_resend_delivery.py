import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import auth


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeClient:
    last_headers = None

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, json, headers):
        FakeClient.last_headers = headers
        return FakeResponse(200, {"id": "re_123"})


class ResendDeliveryTests(unittest.TestCase):
    def test_resend_request_includes_user_agent_and_bearer_auth(self):
        settings = SimpleNamespace(
            RESEND_API_KEY="re_test_key",
            RESEND_FROM="BeatHub <noreply@example.com>",
            EMAIL_ENABLED=True,
            EMAIL_PROVIDER="resend",
        )
        with patch.object(auth, "settings", settings), patch.object(auth.httpx, "Client", FakeClient):
            result = auth._send_email_resend("test@example.com", "Test", "Body")
        self.assertTrue(result)
        self.assertEqual(FakeClient.last_headers["Authorization"], "Bearer re_test_key")
        self.assertEqual(FakeClient.last_headers["Content-Type"], "application/json")
        self.assertEqual(FakeClient.last_headers["User-Agent"], auth.RESEND_USER_AGENT)

    def test_resend_failure_logs_provider_error_without_recipient(self):
        class FailingClient(FakeClient):
            def post(self, url, *, json, headers):
                return FakeResponse(403, {"name": "forbidden", "message": "request rejected"})

        settings = SimpleNamespace(
            RESEND_API_KEY="re_test_key",
            RESEND_FROM="BeatHub <noreply@example.com>",
            EMAIL_ENABLED=True,
            EMAIL_PROVIDER="resend",
        )
        with patch.object(auth, "settings", settings), patch.object(auth.httpx, "Client", FailingClient), patch.object(auth.logger, "error") as log_error:
            result = auth._send_email_resend("private@example.com", "Test", "Body")
        self.assertFalse(result)
        rendered = " ".join(str(call) for call in log_error.call_args_list)
        self.assertIn("403", rendered)
        self.assertIn("forbidden", rendered)
        self.assertNotIn("private@example.com", rendered)


if __name__ == "__main__":
    unittest.main()
