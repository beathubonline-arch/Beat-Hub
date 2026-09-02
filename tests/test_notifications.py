import unittest
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
    def test_mark_all_read(self, session_factory):
        from app.services.notifications import mark_all_read
        db = session_factory.return_value
        db.query.return_value.filter.return_value.update.return_value = 3
        self.assertEqual(mark_all_read("u1"), 3)
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
