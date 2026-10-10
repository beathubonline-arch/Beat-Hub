import unittest
from app.services.fan_passport import Purchase, passport_for_buyer


class FanPassportTests(unittest.TestCase):
    def test_paid_purchase_and_duplicate(self):
        orders = [Purchase("o1", "PAID", buyer_id="b1", artist_id="a1")]*2
        self.assertEqual(passport_for_buyer(orders, buyer_id="b1"),
                         {"purchase_count": 1, "artists_supported": 1,
                          "badge": "Verified Supporter", "verified_supporter": True})

    def test_unpaid_refunded_and_other_buyer_excluded(self):
        orders = [Purchase("o1", "PENDING", buyer_id="b1"),
                  Purchase("o2", "PAID", buyer_id="b1", refunded=True),
                  Purchase("o3", "PAID", buyer_id="b2")]
        self.assertEqual(passport_for_buyer(orders, buyer_id="b1")["purchase_count"], 0)

    def test_verified_guest_claim_and_tier(self):
        orders = [Purchase(str(i), "COMPLETED", buyer_email="fan@example.com") for i in range(3)]
        self.assertEqual(passport_for_buyer(orders, verified_email="FAN@example.com")["badge"], "Sound Collector")

    def test_requires_identity(self):
        with self.assertRaises(ValueError):
            passport_for_buyer([])


if __name__ == "__main__":
    unittest.main()
