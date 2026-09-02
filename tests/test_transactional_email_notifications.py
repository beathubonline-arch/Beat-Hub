import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.services.transactional_email_notifications import (
    notify_buyer_purchase,
    notify_completed_music_sale,
    notify_creator_sale,
    notify_failed_payment,
)


class TransactionalEmailNotificationTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch.multiple(
            "app.services.transactional_email_notifications.settings",
            EMAIL_ENABLED=True,
            RESEND_API_KEY="test-key",
            RESEND_FROM="BeatHub <no-reply@mybeathub.com>",
            EMAIL_FROM="",
            SUPPORT_EMAIL="support@mybeathub.com",
            BASE_URL="https://mybeathub.com",
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def test_buyer_confirmation_uses_stable_idempotency_key(self):
        order = SimpleNamespace(
            id="order-123",
            order_number="BH-123",
            gross_amount=Decimal("1500.00"),
            currency="KES",
            buyer=SimpleNamespace(email="buyer@example.com", username="buyer"),
        )
        with patch("app.services.transactional_email_notifications.send_email", return_value=True) as send:
            self.assertTrue(notify_buyer_purchase(order, "Midnight Beat", "DeeVeevo"))

        args, kwargs = send.call_args
        self.assertEqual(args[2], "buyer@example.com")
        self.assertEqual(args[3], "BeatHub — Your purchase is complete")
        self.assertIn("Midnight Beat", args[4])
        self.assertIn("1500.00", args[4])
        self.assertTrue(kwargs["idempotency_key"].startswith("transactional-"))

    def test_creator_sale_email_contains_earnings(self):
        order = SimpleNamespace(
            id="order-456",
            order_number="BH-456",
            gross_amount=Decimal("2000.00"),
            net_amount=Decimal("1800.00"),
            currency="KES",
        )
        with patch("app.services.transactional_email_notifications.send_email", return_value=True) as send:
            self.assertTrue(
                notify_creator_sale(order, "Summer Beat", "Producer One", "creator@example.com")
            )

        args, kwargs = send.call_args
        self.assertEqual(args[2], "creator@example.com")
        self.assertEqual(args[3], "BeatHub — You made a sale")
        self.assertIn("KES 1,800.00", args[4])
        self.assertIn("Summer Beat", args[4])
        self.assertIn("idempotency_key", kwargs)

    def test_completed_sale_sends_both_recipient_emails(self):
        with patch(
            "app.services.transactional_email_notifications.send_email",
            return_value=True,
        ) as send:
            result = notify_completed_music_sale(
                "order-789",
                "BH-789",
                Decimal("1500.00"),
                Decimal("1350.00"),
                "KES",
                "buyer@example.com",
                "buyer",
                "creator@example.com",
                "Producer One",
                "New Beat",
            )

        self.assertEqual(result, (True, True))
        self.assertEqual(send.call_count, 2)
        recipients = {call.args[2] for call in send.call_args_list}
        self.assertEqual(recipients, {"buyer@example.com", "creator@example.com"})
        keys = [call.kwargs["idempotency_key"] for call in send.call_args_list]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)

    def test_failed_payment_email_is_buyer_only_and_idempotent(self):
        with patch("app.services.transactional_email_notifications.send_email", return_value=True) as send:
            self.assertTrue(notify_failed_payment("buyer@example.com", "BH-999", "Cancelled by customer"))

        args, kwargs = send.call_args
        self.assertEqual(args[2], "buyer@example.com")
        self.assertEqual(args[3], "BeatHub — Payment not completed")
        self.assertIn("Cancelled by customer", args[4])
        self.assertTrue(kwargs["idempotency_key"].startswith("transactional-"))


if __name__ == "__main__":
    unittest.main()
