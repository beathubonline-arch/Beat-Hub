import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.routers import paystack_checkout


class FakeDB:
    def __init__(self, payment=None, order=None):
        self.payment = payment
        self.order = order

    def query(self, model):
        return FakeQuery(self.payment)

    def get(self, model, key):
        return self.order


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def one_or_none(self):
        return self.value

    def first(self):
        return self.value


class PaystackPurchaseFlowTests(unittest.TestCase):
    def test_callback_verifies_and_fulfills_without_auth_dependency(self):
        track = SimpleNamespace(slug="test-beat")
        order = SimpleNamespace(
            id="order-1",
            buyer_id="buyer-1",
            track=track,
            status=OrderStatus.PENDING,
        )
        payment = SimpleNamespace(order_id="order-1", checkout_request_id="BHREF123")
        db = FakeDB(payment=payment, order=order)

        async def fake_verify(reference):
            self.assertEqual(reference, "BHREF123")
            return {"status": "success", "amount": 1000, "currency": "KES"}

        with patch.object(paystack_checkout, "_verify_reference", fake_verify), patch.object(
            paystack_checkout, "_complete_verified_payment", return_value=True
        ) as complete:
            response = asyncio.run(paystack_checkout.paystack_callback(reference="BHREF123", db=db))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/track/test-beat?payment=success")
        complete.assert_called_once_with(db, order, payment, {"status": "success", "amount": 1000, "currency": "KES"})

    def test_callback_does_not_claim_success_when_verification_fails(self):
        track = SimpleNamespace(slug="test-beat")
        order = SimpleNamespace(id="order-2", track=track)
        payment = SimpleNamespace(order_id="order-2", checkout_request_id="BHREF456")
        db = FakeDB(payment=payment, order=order)

        async def failed_verify(reference):
            raise RuntimeError("Paystack temporarily unavailable")

        with patch.object(paystack_checkout, "_verify_reference", failed_verify):
            response = asyncio.run(paystack_checkout.paystack_callback(reference="BHREF456", db=db))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/track/test-beat?payment=pending")

    def test_order_status_api_is_read_only(self):
        order = SimpleNamespace(
            id="order-3",
            buyer_id="buyer-3",
            status=OrderStatus.COMPLETED,
            order_number="BHORDER3",
            track=SimpleNamespace(slug="test-beat"),
        )
        db = FakeDB(order=order)
        user = SimpleNamespace(id="buyer-3")

        from app.routers import checkout

        response = checkout.order_status_api("order-3", db=db, user=user)
        self.assertEqual(response["status"], OrderStatus.COMPLETED.value)
        self.assertTrue(response["completed"])
        self.assertEqual(response["track_slug"], "test-beat")
        self.assertEqual(order.status, OrderStatus.COMPLETED)

    def test_payment_completion_is_idempotent_for_completed_order(self):
        payment = SimpleNamespace(
            id="payment-1",
            callback_processed=True,
            status=PaymentStatus.COMPLETED,
        )
        order = SimpleNamespace(id="order-4", status=OrderStatus.COMPLETED)
        db = FakeDB(payment=payment, order=order)

        result = paystack_checkout._complete_verified_payment(
            db,
            order,
            payment,
            {"status": "success", "amount": 1000, "currency": "KES"},
        )

        self.assertTrue(result)
        self.assertEqual(payment.status, PaymentStatus.COMPLETED)
        self.assertEqual(order.status, OrderStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
