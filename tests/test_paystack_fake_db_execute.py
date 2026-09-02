from types import SimpleNamespace

from tests.test_paystack_purchase_flow import FakeDB


def test_paystack_fake_db_supports_execute():
    result = FakeDB().execute(SimpleNamespace())
    assert result.scalar_one_or_none() is None
