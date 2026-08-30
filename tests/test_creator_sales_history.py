import unittest
from datetime import datetime
from decimal import Decimal

from app.routers import creator_sales_history as history


class CreatorSalesHistoryTests(unittest.TestCase):
    def test_unified_history_combines_music_and_merchandise(self):
        when_1 = datetime(2026, 8, 30, 9, 0)
        when_2 = datetime(2026, 8, 29, 9, 0)
        original_music = history._music_sales
        original_merch = history._merch_sales
        try:
            history._music_sales = lambda *_: [{"id":"music-1","order_number":"BH-MUSIC-1","buyer":"Mary","product":"Afro Beat","type":"music","type_label":"Beat / Track","gross":Decimal("2000"),"commission":Decimal("200"),"net":Decimal("1800"),"status":"Completed","date":when_2,"quantity":1}]
            history._merch_sales = lambda *_: [{"id":"merch-1","order_number":"BM-MERCH-1","buyer":"John","product":"Bono Hoodie","type":"merchandise","type_label":"Merchandise","gross":Decimal("3000"),"commission":Decimal("300"),"net":Decimal("2700"),"status":"Paid","date":when_1,"quantity":1}]
            result = history.build_sales_history(object(), "bono", page=1, sale_type="all")
            self.assertEqual(result["total_count"], 2)
            self.assertEqual(result["total_gross"], Decimal("5000"))
            self.assertEqual(result["total_commission"], Decimal("500"))
            self.assertEqual(result["total_net"], Decimal("4500"))
            self.assertEqual(result["sales"][0]["buyer"], "John")
            self.assertEqual(result["sales"][0]["product"], "Bono Hoodie")
        finally:
            history._music_sales = original_music
            history._merch_sales = original_merch

    def test_history_filters_merchandise_and_paginates(self):
        original_music = history._music_sales
        original_merch = history._merch_sales
        try:
            history._music_sales = lambda *_: [{"id":"music-1","order_number":"M1","buyer":"Mary","product":"Beat","type":"music","type_label":"Beat / Track","gross":Decimal("1000"),"commission":Decimal("100"),"net":Decimal("900"),"status":"Completed","date":datetime(2026,8,1),"quantity":1}]
            history._merch_sales = lambda *_: [{"id":"merch-1","order_number":"X1","buyer":"John","product":"Hoodie","type":"merchandise","type_label":"Merchandise","gross":Decimal("3000"),"commission":Decimal("300"),"net":Decimal("2700"),"status":"Paid","date":datetime(2026,8,2),"quantity":2}]
            result = history.build_sales_history(object(), "bono", page=1, sale_type="merchandise", search="hoodie")
            self.assertEqual(result["total_count"], 1)
            self.assertEqual(result["total_gross"], Decimal("3000"))
            self.assertEqual(result["sales"][0]["quantity"], 2)
            self.assertEqual(result["sale_type"], "merchandise")
        finally:
            history._music_sales = original_music
            history._merch_sales = original_merch


if __name__ == "__main__":
    unittest.main()
