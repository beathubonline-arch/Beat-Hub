import unittest
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from app.utils import deps


class AuthenticationDependencyTests(unittest.TestCase):
    def _request(self):
        return Request({"type": "http", "method": "GET", "path": "/account", "headers": []})

    def _user(self, *, active=True, verified=True):
        return SimpleNamespace(
            id="user-1",
            is_active=active,
            is_verified=verified,
            role="buyer",
        )

    def test_verified_active_user_is_authenticated(self):
        user = self._user()
        with patch.object(deps, "_decode_token", return_value={"sub": "user-1"}), patch.object(
            deps, "_get_user_from_subject", return_value=user
        ):
            result = deps.get_optional_user(self._request(), object(), "valid-token")
        self.assertIs(result, user)

    def test_unverified_user_cannot_authenticate_with_existing_token(self):
        user = self._user(verified=False)
        with patch.object(deps, "_decode_token", return_value={"sub": "user-1"}), patch.object(
            deps, "_get_user_from_subject", return_value=user
        ):
            result = deps.get_optional_user(self._request(), object(), "old-token")
        self.assertIsNone(result)

    def test_inactive_user_cannot_authenticate_with_existing_token(self):
        user = self._user(active=False)
        with patch.object(deps, "_decode_token", return_value={"sub": "user-1"}), patch.object(
            deps, "_get_user_from_subject", return_value=user
        ):
            result = deps.get_optional_user(self._request(), object(), "old-token")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
