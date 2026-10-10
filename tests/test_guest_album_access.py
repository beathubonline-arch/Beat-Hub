"""Regression checks for guest album access. No real charges are made."""
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest

from app.models.order import OrderStatus
from app.services import guest_album_access as guest


def _request(token):
    return SimpleNamespace(cookies={guest.COOKIE_NAME: token} if token else {})


def test_guest_token_scoped_to_completed_licensed_order(monkeypatch):
    monkeypatch.setattr(guest, "_jwt_secret", lambda: "unit-test-secret-only")
    order = SimpleNamespace(id="order-1", album_id="album-1",
                            buyer_id="guest-1", status=OrderStatus.COMPLETED)
    db = Mock()
    db.get.return_value = order
    db.query.return_value.filter.return_value.first.return_value = object()
    token = guest.issue_guest_token(order.id)
    assert guest.authorized_guest_order(_request(token), db, "album-1") is order
    assert guest.authorized_guest_order(_request(token), db, "album-2") is None


def test_pending_or_unlicensed_guest_cannot_download(monkeypatch):
    monkeypatch.setattr(guest, "_jwt_secret", lambda: "unit-test-secret-only")
    order = SimpleNamespace(id="order-2", album_id="album-1",
                            buyer_id="guest-2", status=OrderStatus.PENDING)
    db = Mock()
    db.get.return_value = order
    token = guest.issue_guest_token(order.id)
    assert guest.authorized_guest_order(_request(token), db, "album-1") is None
    order.status = OrderStatus.COMPLETED
    db.query.return_value.filter.return_value.first.return_value = None
    assert guest.authorized_guest_order(_request(token), db, "album-1") is None


def test_tampered_and_expired_tokens_rejected(monkeypatch):
    monkeypatch.setattr(guest, "_jwt_secret", lambda: "unit-test-secret-only")
    db = Mock()
    assert guest.authorized_guest_order(_request("invalid.token.value"), db, "album-1") is None
    expired = jwt.encode({"guest_album_order": "order-1", "exp": 1},
                         "unit-test-secret-only", algorithm="HS256")
    assert guest.authorized_guest_order(_request(expired), db, "album-1") is None
    db.get.assert_not_called()
