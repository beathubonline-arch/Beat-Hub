"""Regression tests for BeatHub VIP email validation."""
from app.routers.music_drops import _EMAIL


def test_normal_customer_emails_accepted():
    for email in ("buyer@gmail.com", "anthony.bii+music@example.co.ke",
                  "fan_123@outlook.com", "music-lover@yahoo.co.uk"):
        assert _EMAIL.fullmatch(email), email


def test_invalid_emails_rejected():
    for email in ("not-an-email", "user@", "@gmail.com",
                  "user name@gmail.com", "user@gmailxcom"):
        assert not _EMAIL.fullmatch(email), email
