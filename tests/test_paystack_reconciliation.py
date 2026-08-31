import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.paystack_settlement import PaystackSettlement
from app.services import paystack_reconciliation as reconciliation


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        page = kwargs.get("params", {}).get("page")
        if page == 1:
            return FakeResponse({"data": [{
                "id": "stl_001",
                "status": "success",
                "currency": "KES",
                "total_amount": 90000,
                "settlement_date": "2026-09-01T12:00:00Z",
            }]})
        return FakeResponse({"data": []})


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_reconciliation_detects_unsettled_amount():
    db = session()
    db.add(PaystackSettlement(
        paystack_id="stl_001", status="success", currency="KES",
        total_amount=Decimal("900.00"), settlement_date=None,
        raw_payload="{}",
    ))
    db.add(PaymentTransaction(
        order_id="order-1", phone_number="254700000000", amount=Decimal("1000.00"),
        currency="KES", status=PaymentStatus.COMPLETED,
    ))
    db.commit()
    report = reconciliation.build_reconciliation(db, "KES")
    assert report["local_completed_payment_total"] == Decimal("1000.00")
    assert report["paystack_settled_total"] == Decimal("900.00")
    assert report["payment_vs_settlement_difference"] == Decimal("100.00")
    assert report["status"] == "review"


def test_sync_is_idempotent_and_updates_snapshot(monkeypatch):
    db = session()
    monkeypatch.setattr(reconciliation.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(reconciliation.settings, "PAYSTACK_SECRET_KEY", "test-key")

    first = asyncio.run(reconciliation.sync_paystack_settlements(db))
    second = asyncio.run(reconciliation.sync_paystack_settlements(db))

    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["updated"] == 1
    assert db.query(PaystackSettlement).count() == 1
