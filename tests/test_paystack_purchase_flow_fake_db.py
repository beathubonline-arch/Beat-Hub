from tests.test_paystack_purchase_flow import FakeDB, FakeQuery


def test_fake_db_execute_compatibility():
    db = FakeDB()
    assert hasattr(db, "query")
    assert hasattr(db, "get")
    assert hasattr(FakeQuery(None), "one_or_none")
