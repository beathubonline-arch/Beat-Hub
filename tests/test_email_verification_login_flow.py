import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from app.routers import auth


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.commits = 0

    def query(self, model):
        return FakeQuery(self.user)

    def commit(self):
        self.commits += 1


class EmailVerificationLoginFlowTests(unittest.TestCase):
    def request(self, path="/login"):
        return Request({"type": "http", "method": "POST", "path": path, "headers": []})

    def user(self, verified=False):
        return SimpleNamespace(
            id="user-1",
            email="hihopdailyke@gmail.com",
            username="hiphopdaily",
            hashed_password="stored",
            role="buyer",
            is_active=True,
            is_verified=verified,
            verification_code_hash=None,
            verification_code_expires=None,
            verification_attempts=0,
        )

    def test_unverified_login_sends_code_and_redirects_to_verify(self):
        user = self.user(verified=False)
        db = FakeDB(user)
        with patch.object(auth, "_password_matches", return_value=True), patch.object(
            auth, "_send_verification_email", return_value=True
        ):
            response = auth.login_submit(
                self.request(),
                identifier="hihopdailyke@gmail.com",
                email="",
                password="correct-password",
                next="/account",
                db=db,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("/verify-email?", response.headers["location"])
        self.assertIn("hihopdailyke%40gmail.com", response.headers["location"])
        self.assertIn("next=%2Faccount", response.headers["location"])
        self.assertIsNotNone(user.verification_code_hash)
        self.assertEqual(db.commits, 1)

    def test_unverified_signup_reuses_account_and_sends_new_code(self):
        user = self.user(verified=False)
        db = FakeDB(user)
        with patch.object(auth, "_send_verification_email", return_value=True):
            response = auth.signup_submit(
                self.request("/signup"),
                db=db,
                stage_name="Hip Hop Daily",
                email="hihopdailyke@gmail.com",
                password="correct-password",
                confirm_password="correct-password",
                role="buyer",
                agree_terms="yes",
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("/verify-email?", response.headers["location"])
        self.assertIn("existing%20account%20is%20not%20verified", response.headers["location"])
        self.assertIsNotNone(user.verification_code_hash)
        self.assertEqual(db.commits, 1)

    def test_valid_verification_marks_account_verified(self):
        user = self.user(verified=False)
        code = "123456"
        user.verification_code_hash = auth._verification_code_digest(code)
        user.verification_code_expires = datetime.utcnow() + timedelta(minutes=5)
        db = FakeDB(user)
        response = auth.verify_email_submit(
            self.request("/verify-email"),
            db=db,
            email="hihopdailyke@gmail.com",
            code=code,
            next="/account",
        )
        self.assertEqual(response.status_code, 303)
        self.assertTrue(user.is_verified)
        self.assertIsNone(user.verification_code_hash)
        self.assertIn("/login?success=Email%20verified", response.headers["location"])
        self.assertIn("next=%2Faccount", response.headers["location"])
        self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
