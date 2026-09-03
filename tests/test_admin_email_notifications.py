import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.order import OrderStatus
from app.services import admin_event_notifications as events
from app.services import admin_notifications


class AdminEmailNotificationTests(unittest.TestCase):
    def _settings(self, enabled=True):
        return SimpleNamespace(
            ADMIN_EMAIL="admin@mybeathub.com",
            RESEND_API_KEY="re_test_key",
            ADMIN_FROM="BeatHub Admin <admin@mybeathub.com>",
            SUPPORT_EMAIL="support@mybeathub.com",
            EMAIL_ENABLED=enabled,
            BASE_URL="https://mybeathub.com",
        )

    @patch.object(admin_notifications.httpx, "Client")
    def test_admin_email_success_uses_resend_and_idempotency(self, client_cls):
        response = MagicMock(status_code=200, text='{"id":"email_123"}')
        client = MagicMock()
        client.post.return_value = response
        client_cls.return_value.__enter__.return_value = client

        with patch.object(admin_notifications, "settings", self._settings()):
            self.assertTrue(
                admin_notifications.notify_admin(
                    "BeatHub — test alert", "Test body", idempotency_key="test:event:1"
                )
            )

        client.post.assert_called_once()
        _, kwargs = client.post.call_args
        self.assertEqual(kwargs["json"]["to"], ["admin@mybeathub.com"])
        self.assertEqual(kwargs["json"]["from"], "BeatHub Admin <admin@mybeathub.com>")
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], "test:event:1")

    @patch.object(admin_notifications.httpx, "Client")
    def test_admin_email_failure_is_safe(self, client_cls):
        client = MagicMock()
        client.post.side_effect = RuntimeError("network down")
        client_cls.return_value.__enter__.return_value = client

        with patch.object(admin_notifications, "settings", self._settings()):
            self.assertFalse(admin_notifications.notify_admin("Alert", "Body"))

    @patch.object(admin_notifications, "notify_admin", return_value=True)
    def test_new_user_has_stable_event_key(self, notify):
        user = SimpleNamespace(
            id="user-1", email="user@example.com", username="producer",
            role=SimpleNamespace(value="creator"), created_at="now",
        )
        self.assertTrue(admin_notifications.notify_new_user(user))
        self.assertEqual(notify.call_args.kwargs["idempotency_key"], "new-user:user-1")

    @patch.object(admin_notifications, "notify_admin", return_value=True)
    def test_payment_has_stable_event_key(self, notify):
        order = SimpleNamespace(
            id="order-1", order_number="BH-1", gross_amount="1500.00",
            currency="KES", buyer_id="buyer-1", status=OrderStatus.COMPLETED,
        )
        self.assertTrue(admin_notifications.notify_payment(order, "music"))
        self.assertEqual(notify.call_args.kwargs["idempotency_key"], "payment:music:order-1")

    def test_completed_order_listener_queues_only_on_status_change(self):
        target = SimpleNamespace(id="order-1", status=OrderStatus.COMPLETED)
        session = MagicMock()
        history = SimpleNamespace(has_changes=lambda: True, deleted=[OrderStatus.PENDING])
        fake_state = SimpleNamespace(attrs=SimpleNamespace(status=SimpleNamespace(history=history)))
        with patch.object(events, "inspect", return_value=fake_state), patch.object(
            events.Session, "object_session", return_value=session
        ), patch.object(events, "_queue") as queue:
            events._order_notifications(None, None, target)
        queue.assert_called()
        queued_keys = [call.args[1] for call in queue.call_args_list]
        self.assertIn("admin-order:order-1", queued_keys)


if __name__ == "__main__":
    unittest.main()
