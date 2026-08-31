import re
import unittest
from datetime import datetime, timedelta

from app.models.user import User
from app.routers.auth import (
    VERIFICATION_CODE_TTL,
    VERIFICATION_MAX_ATTEMPTS,
    _new_verification_code,
    _set_verification_code,
    _verification_code_digest,
)


class EmailVerificationSecurityTests(unittest.TestCase):
    def test_code_is_six_digits(self):
        code = _new_verification_code()
        self.assertRegex(code, r"^\d{6}$")

    def test_code_hash_is_not_plaintext(self):
        code = _new_verification_code()
        self.assertNotEqual(code, _verification_code_digest(code))

    def test_setting_code_creates_expiry_and_resets_attempts(self):
        user = User(
            id="test-user",
            email="test@example.com",
            username="testuser",
            hashed_password="not-used",
        )
        user.verification_attempts = 4
        code = _set_verification_code(user)

        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(user.verification_code_hash, _verification_code_digest(code))
        self.assertEqual(user.verification_attempts, 0)
        self.assertIsNotNone(user.verification_code_expires)
        self.assertGreater(user.verification_code_expires, datetime.utcnow())
        self.assertLessEqual(
            user.verification_code_expires,
            datetime.utcnow() + VERIFICATION_CODE_TTL + timedelta(seconds=1),
        )

    def test_verification_attempt_limit_is_five(self):
        self.assertEqual(VERIFICATION_MAX_ATTEMPTS, 5)


if __name__ == "__main__":
    unittest.main()
