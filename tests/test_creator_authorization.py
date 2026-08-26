import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.auth import dashboard_url_for_user
from app.utils import deps


class CreatorAuthorizationTests(unittest.TestCase):
    def test_buyer_cannot_use_producer_profile_to_enter_dashboard(self):
        buyer = SimpleNamespace(id="buyer-1", role="buyer", profile=None)
        legacy_producer_profile = SimpleNamespace(user_id="buyer-1", is_producer=True)

        with patch.object(deps, "_load_creator_profile", return_value=legacy_producer_profile):
            with self.assertRaises(HTTPException) as raised:
                deps.require_creator(buyer, object())

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("Current account role: buyer", raised.exception.detail)

    def test_creator_with_missing_profile_is_repaired(self):
        creator = SimpleNamespace(id="creator-1", role="creator", profile=None)
        repaired_profile = SimpleNamespace(user_id="creator-1", is_producer=True)

        with patch.object(deps, "_load_creator_profile", return_value=None), patch.object(
            deps, "_repair_creator_profile", return_value=repaired_profile
        ):
            result = deps.require_creator(creator, object())

        self.assertIs(result, creator)
        self.assertIs(creator.profile, repaired_profile)

    def test_buyer_lands_on_account_after_login(self):
        buyer = SimpleNamespace(role="buyer", profile=SimpleNamespace(is_producer=True))
        self.assertEqual(dashboard_url_for_user(buyer), "/account")

    def test_creator_lands_on_dashboard_after_login(self):
        creator = SimpleNamespace(role="creator", profile=None)
        self.assertEqual(dashboard_url_for_user(creator), "/dashboard")

    def test_is_creator_user_uses_role_only(self):
        buyer = SimpleNamespace(role="buyer", profile=SimpleNamespace(is_producer=True))
        creator = SimpleNamespace(role="creator", profile=None)

        self.assertFalse(deps.is_creator_user(object(), buyer))
        self.assertTrue(deps.is_creator_user(object(), creator))


if __name__ == "__main__":
    unittest.main()
