import unittest
from unittest.mock import patch
from app.routers import auth

class EmailSenderRoutingTests(unittest.TestCase):
    def test_verification_uses_no_reply_sender(self):
        with patch.object(auth, "_send_email", return_value=True) as send:
            self.assertTrue(auth._send_verification_email("user@example.com", "123456"))
        self.assertEqual(send.call_args.kwargs["sender"], "BeatHub <no-reply@mybeathub.com>")
        self.assertNotIn("reply_to", send.call_args.kwargs)

    def test_password_reset_uses_reset_sender_and_support_reply_to(self):
        with patch.object(auth, "_send_email", return_value=True) as send:
            self.assertTrue(auth._send_password_reset_email("user@example.com", "https://mybeathub.com/reset-password?token=test"))
        self.assertEqual(send.call_args.kwargs["sender"], "BeatHub Password Reset <reset-password@mybeathub.com>")
        self.assertEqual(send.call_args.kwargs["reply_to"], "support@mybeathub.com")

if __name__ == "__main__":
    unittest.main()
