import os, sys, unittest
ROOT=os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0,os.path.join(ROOT,"mkulima_ai"))
from intent_engine import detect_intents, enrich_context, open_reply
from planner import build_plan, safe_reasoning_reply

class FarmerIntentRegression(unittest.TestCase):
    def test_disease_not_forced_into_sale(self):
        s=enrich_context("My tomatoes have black spots and leaves are wilting",{})
        self.assertEqual(s["primary_intent"],"crop_health")
        self.assertEqual(s["crop"],"tomato")
        self.assertIn("symptoms",open_reply("My tomatoes have black spots and leaves are wilting",s,"en"))

    def test_weather_keeps_context(self):
        s=enrich_context("Niko Turbo na mahindi",{"location":"Turbo"})
        s=enrich_context("mvua itanyesha? nataka kuvuna",s)
        self.assertIn("weather",s["intents"])
        self.assertEqual(s["crop"],"maize")
        self.assertIn("eneo",open_reply("mvua itanyesha? nataka kuvuna",s,"sw"))

    def test_livestock(self):
        s=enrich_context("Ngombe yangu haikuli na inapumua vibaya",{})
        self.assertEqual(s["primary_intent"],"livestock")
        self.assertIn("veterinary",open_reply("Ngombe yangu haikuli na inapumua vibaya",s,"sw"))

    def test_inputs(self):
        s=enrich_context("Which fertilizer should I use for my potatoes?",{})
        self.assertEqual(s["primary_intent"],"inputs")
        self.assertEqual(s["crop"],"potato")

    def test_storage(self):
        s=enrich_context("Can I store my maize instead of selling now?",{})
        self.assertIn("storage",s["intents"])
        self.assertEqual(s["crop"],"maize")

    def test_profit(self):
        s=enrich_context("Nataka kujua faida baada ya transport na labour",{})
        self.assertEqual(s["primary_intent"],"profit")

    def test_unknown_stays_open(self):
        s=enrich_context("Bro kuna kitu strange kwa shamba sijui hata nieleze aje",{})
        self.assertEqual(s["primary_intent"],"general")
        self.assertIn("outcome",open_reply("Bro kuna kitu strange kwa shamba sijui hata nieleze aje",s,"mixed"))

    def test_weather_plan_requests_live_tool(self):
        s=enrich_context("Mvua itanyesha kesho? Niko Kitale",{"location":"Kitale"})
        p=build_plan("Mvua itanyesha kesho? Niko Kitale",s)
        self.assertEqual(p["goal"],"plan_farm_action")
        self.assertIn("live_weather",p["tools"])

    def test_urgent_livestock_escalates(self):
        s=enrich_context("Ngombe yangu can't breathe, ni urgent",{})
        reply=safe_reasoning_reply("Ngombe yangu can't breathe, ni urgent",s,"mixed")
        self.assertIn("veterinary",reply)

    def test_sale_plan_requires_evidence(self):
        s=enrich_context("Nataka kuuza mahindi",{})
        p=build_plan("Nataka kuuza mahindi",s)
        self.assertIn("live_market_prices",p["tools"])
        self.assertIn("buyer_offer",p["missing_evidence"])

    def test_sale_remains_specialist(self):
        s=enrich_context("Nataka kuuza mahindi buyer amenipea offer",{})
        self.assertEqual(s["primary_intent"],"sell")
        self.assertIsNone(open_reply("Nataka kuuza mahindi buyer amenipea offer",s,"sw"))

if __name__=="__main__":
    unittest.main()
