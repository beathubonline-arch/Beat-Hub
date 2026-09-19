import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.music import SalesModel
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.routers import paystack_checkout


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, payment, order):
        self.payment = payment
        self.order = order
        self.commits = 0

    def query(self, model):
        if getattr(model, "__name__", "") == "PaymentTransaction":
            return FakeQuery(self.payment)
        return FakeQuery(self.order)

    def commit(self):
        self.commits += 1


class ExclusivePaymentStateTests(unittest.TestCase):
    def _objects(self):
        track = SimpleNamespace(
            id="track-1",
            sales_model=SalesModel.EXCLUSIVE,
            is_sold=False,
        )
        order = SimpleNamespace(
            id="order-1",
            track_id="track-1",
            status=OrderStatus.PENDING,
            gross_amount=1000,
            currency="KES",
        )
        payment = SimpleNamespace(
            id="payment-1",
            callback_processed=False,
            status=PaymentStatus.PENDING,
            result_code=None,
            result_description=None,
            completed_at=None,
            phone_number="paystack",
        )
        return track, order, payment

    def test_failed_or_cancelled_payment_never_marks_exclusive_sold(self):
        track, order, payment = self._objects()
        db = FakeDB(payment, order)

        completed = paystack_checkout._complete_verified_payment(
            db, order, payment,
            {"status": "failed", "amount": 100000, "currency": "KES"},
        )

        self.assertFalse(completed)
        self.assertEqual(payment.status, PaymentStatus.FAILED)
        self.assertEqual(order.status, OrderStatus.FAILED)
        self.assertFalse(track.is_sold)

    def test_exclusive_is_sold_only_after_verified_success_reaches_finalizer(self):
        track, order, payment = self._objects()
        db = FakeDB(payment, order)

        def successful_finalizer(_db, finalized_order):
            self.assertIs(finalized_order, order)
            track.is_sold = True
            finalized_order.status = OrderStatus.COMPLETED
            return SimpleNamespace(status=OrderStatus.COMPLETED)

        with patch.object(paystack_checkout, "finalize_order", successful_finalizer):
            completed = paystack_checkout._complete_verified_payment(
                db, order, payment,
                {"status": "success", "amount": 100000, "currency": "KES"},
            )

        self.assertTrue(completed)
        self.assertEqual(payment.status, PaymentStatus.COMPLETED)
        self.assertEqual(order.status, OrderStatus.COMPLETED)
        self.assertTrue(track.is_sold)


if __name__ == "__main__":
    unittest.main()
