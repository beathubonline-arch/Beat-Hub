import ast
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import unittest

from app.services.paystack_transfers import (
    normalize_kenyan_phone,
    initiate_mpesa_transfer,
    _kes_subunits,
)


ROOT = Path(__file__).resolve().parents[1]


class AdminPlatformWithdrawalTests(unittest.TestCase):
    def test_phone_normalization(self):
        self.assertEqual(normalize_kenyan_phone("0712345678"), "254712345678")
        self.assertEqual(normalize_kenyan_phone("254712345678"), "254712345678")
        self.assertEqual(normalize_kenyan_phone("712345678"), "254712345678")

    def test_invalid_phone_is_rejected(self):
        with self.assertRaises(Exception):
            normalize_kenyan_phone("12345")

    def test_kes_amount_uses_currency_subunits(self):
        self.assertEqual(_kes_subunits(Decimal("1.00")), 100)
        self.assertEqual(_kes_subunits(Decimal("1250.50")), 125050)

    @patch("app.services.paystack_transfers.create_mpesa_recipient", return_value="RCP_test")
    @patch("app.services.paystack_transfers.httpx.post")
    def test_transfer_sends_kes_mobile_money_payload(self, post, recipient):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "status": True,
            "message": "Transfer has been queued",
            "data": {
                "reference": "bh_admin_test_reference",
                "transfer_code": "TRF_test",
                "status": "pending",
            },
        }
        result = initiate_mpesa_transfer(Decimal("100.50"), "0712345678")
        self.assertEqual(result["reference"], "bh_admin_test_reference")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["currency"], "KES")
        self.assertEqual(payload["amount"], 10050)
        self.assertEqual(payload["recipient"], "RCP_test")
        recipient.assert_called_once()

    def test_admin_withdrawal_source_contains_real_transfer_and_verify_routes(self):
        source = (ROOT / "app/routers/payout_admin.py").read_text(encoding="utf-8")
        self.assertIn('@platform_router.get("/withdraw")', source)
        self.assertIn('@platform_router.post("/withdraw")', source)
        self.assertIn('@platform_router.post("/withdraw/{withdrawal_id}/send")', source)
        self.assertIn('@platform_router.post("/withdraw/{withdrawal_id}/verify")', source)
        self.assertIn("initiate_mpesa_transfer", source)
        self.assertIn("verify_transfer", source)
        self.assertIn("PAYSTACK_SECRET_KEY", source)

    def test_admin_template_uses_send_and_verify_actions(self):
        source = (ROOT / "app/templates/admin/withdraw.html").read_text(encoding="utf-8")
        self.assertIn('/admin/withdraw/{{ withdrawal.id }}/send', source)
        self.assertIn('/admin/withdraw/{{ withdrawal.id }}/verify', source)
        self.assertIn("Send to M-Pesa", source)
        self.assertIn("Verify Transfer", source)
        self.assertNotIn('name="payout_reference"', source)

    def test_transfer_service_compiles(self):
        for relative in [
            "app/services/paystack_transfers.py",
            "app/routers/payout_admin.py",
        ]:
            path = ROOT / relative
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
