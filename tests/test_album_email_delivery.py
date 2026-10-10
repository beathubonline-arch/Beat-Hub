"""Email delivery is private, idempotent, and requires verified fulfillment."""
from unittest.mock import patch
from app.services.transactional_email_notifications import send_album_delivery_email


def test_album_delivery_contains_private_access_link():
    with patch("app.services.guest_album_access.issue_guest_token", return_value="signed.test.token"), \
         patch("app.services.transactional_email_notifications._deliver", return_value=True) as deliver:
        assert send_album_delivery_email("buyer@example.com", "TIME ITATELL", "order-1",
                                         "https://mybeathub.com/album/time-itatell")
        args = deliver.call_args.args
        assert args[0] == "buyer@example.com"
        assert "TIME ITATELL" in args[1]
        assert "/album/time-itatell/access?token=signed.test.token" in args[2]
        assert "90 days" in args[2]
        assert args[3] == "album-delivery:order-1:buyer@example.com"


def test_email_send_failure_does_not_claim_success():
    with patch("app.services.guest_album_access.issue_guest_token", return_value="signed.test.token"), \
         patch("app.services.transactional_email_notifications._deliver", return_value=False):
        assert not send_album_delivery_email("buyer@example.com", "TIME ITATELL", "order-2",
                                             "https://mybeathub.com/album/time-itatell")
