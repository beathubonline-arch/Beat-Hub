import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class NotificationServiceTests(unittest.TestCase):
    @patch("app.services.notifications.SessionLocal")
    def test_create_notification_is_idempotent(self, session_factory):
        from app.services.notifications import create_notification
        db = session_factory.return_value
        db.query.return_value.filter.return_value.first.return_value = object()
        self.assertFalse(create_notification("u1", "sale:1", "sale", "Sold", "A sale happened"))
        db.add.assert_not_called()

    @patch("app.services.notifications.SessionLocal")
    def test_create_notification_persists_new_event(self, session_factory):
        from app.services.notifications import create_notification
        db = session_factory.return_value
        db.query.return_value.filter.return_value.first.return_value = None
        self.assertTrue(create_notification("u1", "sale:2", "sale", "Sold", "A sale happened", "/dashboard"))
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.notifications.SessionLocal")
    def test_unread_count(self, session_factory):
        from app.services.notifications import unread_count
        db = session_factory.return_value
        db.query.return_value.filter.return_value.scalar.return_value = 4
        self.assertEqual(unread_count("u1"), 4)

    @patch("app.services.notifications.SessionLocal")
    def test_mark_one_read_only_marks_selected_notification(self, session_factory):
        from app.services.notifications import mark_read

        db = session_factory.return_value
        selected = SimpleNamespace(is_read=False)
        db.query.return_value.filter.return_value.first.return_value = selected

        self.assertTrue(mark_read("admin-1", "notification-2"))
        self.assertTrue(selected.is_read)
        db.commit.assert_called_once()
        db.query.return_value.filter.return_value.update.assert_not_called()

    @patch("app.services.notifications.SessionLocal")
    def test_mark_all_read(self, session_factory):
        from app.services.notifications import mark_all_read
        db = session_factory.return_value
        db.query.return_value.filter.return_value.update.return_value = 3
        self.assertEqual(mark_all_read("u1"), 3)
        db.commit.assert_called_once()

    def test_notification_bell_does_not_mark_everything_read(self):
        source = Path("app/static/js/notifications.js").read_text(encoding="utf-8")
        bell_start = source.index("bell.addEventListener('click'")
        bell_end = source.index("pushButton.addEventListener", bell_start)
        bell_handler = source[bell_start:bell_end]

        self.assertIn("refresh();", bell_handler)
        self.assertNotIn("/notifications/read-all", bell_handler)
        self.assertNotIn("markNotificationsRead", bell_handler)

    def test_opening_one_notification_uses_individual_read_endpoint(self):
        source = Path("app/static/js/notifications.js").read_text(encoding="utf-8")
        self.assertIn("function markNotificationRead(item, anchor, event)", source)
        self.assertIn("'/notifications/' + encodeURIComponent(item.id) + '/read'", source)
        self.assertIn("setCount(Math.max(0, currentCount() - 1));", source)
        self.assertIn("a.addEventListener('click', function (event) { markNotificationRead(item, a, event); });", source)


if __name__ == "__main__":
    unittest.main()
