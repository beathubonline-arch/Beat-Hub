import os, sys, unittest
ROOT=os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0,os.path.join(ROOT,"mkulima_ai"))
from intent_engine import detect_intents, enrich_context, open_reply
from planner import build_plan, safe_reasoning_reply
from tool_registry import describe_tools, tool_is_executable, stale_reference_tool
from farm_vision import analyze_farm_image, safe_vision_reply
from weather_live import weather_reply

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

    def test_planned_live_weather_is_not_claimed_executed(self):
        s=enrich_context("Mvua itanyesha kesho? Niko Kitale",{"location":"Kitale"})
        p=build_plan("Mvua itanyesha kesho? Niko Kitale",s)
        weather=[x for x in p["tool_status"] if x["name"]=="live_weather"][0]
        self.assertEqual(weather["status"],"planned")
        self.assertFalse(p["can_execute_all_tools"])
        self.assertIn("live_weather",p["evidence"]["not_executed"])

    def test_local_calculator_is_executable(self):
        self.assertTrue(tool_is_executable("farm_calculator"))

    def test_old_market_reference_is_labeled_stale(self):
        r=stale_reference_tool("maize_reference","Warehouse Receipt System Council","2026-08-10",3730.50,"KES/90kg")
        self.assertEqual(r["status"],"stale")
        self.assertEqual(r["provenance"]["observed_on"],"2026-08-10")

    def test_unknown_tool_fails_closed(self):
        x=describe_tools(["magic_price_oracle"])[0]
        self.assertEqual(x["status"],"unavailable")
        self.assertFalse(tool_is_executable("magic_price_oracle"))

    def test_image_context_adds_farm_vision(self):
        s=enrich_context("My tomato is sick",{})
        s["has_image"]=True
        p=build_plan("My tomato is sick",s)
        self.assertIn("farm_vision",p["tools"])
        vision=[x for x in p["tool_status"] if x["name"]=="farm_vision"][0]
        self.assertEqual(vision["status"],"input_available")
        self.assertFalse(tool_is_executable("farm_vision"))

    def test_photo_does_not_fake_visual_execution(self):
        p=build_plan("",{"primary_intent":"general","has_image":True})
        vision=[x for x in p["tool_status"] if x["name"]=="farm_vision"][0]
        self.assertNotEqual(vision["status"],"available")
        self.assertFalse(p["can_execute_all_tools"])

    def test_vision_without_provider_fails_closed(self):
        old=os.environ.pop("GEMINI_API_KEY",None)
        try:
            r=analyze_farm_image(b"fake","image/jpeg",{"crop":"maize"})
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"],"provider_unavailable")
            self.assertEqual(r["observations"],[])
        finally:
            if old is not None: os.environ["GEMINI_API_KEY"]=old

    def test_safe_vision_reply_labels_hypotheses(self):
        r={"ok":True,"observations":{"visible_observations":["brown spots on leaves"],"possible_explanations":["leaf disease","physical damage"],"confidence":"low","questions_needed":["When did it start?"],"urgent_visual_flags":[]}}
        reply=safe_vision_reply(r,"en")
        self.assertIn("not confirmed diagnoses",reply)
        self.assertIn("low",reply)

    def test_photo_followup_keeps_crop_health_intent(self):
        s={"has_image":True,"primary_intent":"crop_health","intents":["crop_health"],"crop":"tomato"}
        s=enrich_context("3 days",s)
        self.assertEqual(s["primary_intent"],"crop_health")
        self.assertEqual(s["crop"],"tomato")

    def test_accumulated_case_evidence_reduces_missing_fields(self):
        s={"has_image":True,"primary_intent":"crop_health","intents":["crop_health"],"crop":"tomato",
           "case_notes":["brown spots on lower leaves"],"symptom_timing":"for 3 days","location":"Turbo"}
        p=build_plan("for 3 days",s)
        self.assertNotIn("symptoms",p["missing_evidence"])
        self.assertNotIn("timing",p["missing_evidence"])
        self.assertNotIn("location",p["missing_evidence"])
        self.assertIn("farm_vision",p["tools"])

    def test_live_weather_registry_is_now_executable(self):
        self.assertTrue(tool_is_executable("live_weather"))
        meta=[x for x in describe_tools(["live_weather"]) if x["name"]=="live_weather"][0]
        self.assertEqual(meta["provider"],"MET Norway Locationforecast 2.0")

    def test_weather_reply_carries_provenance(self):
        r={"ok":True,"hours":24,"retrieved_at":"2026-09-28T14:00:00+00:00",
           "location":{"query":"Kitale"},"forecast":{"precipitation_mm":4.2,"temp_min_c":12,"temp_max_c":23,"wind_max_m_s":5}}
        reply=weather_reply(r,"en")
        self.assertIn("MET Norway",reply)
        self.assertIn("retrieved",reply)
        self.assertIn("4.2 mm",reply)

    def test_sale_remains_specialist(self):
        s=enrich_context("Nataka kuuza mahindi buyer amenipea offer",{})
        self.assertEqual(s["primary_intent"],"sell")
        self.assertIsNone(open_reply("Nataka kuuza mahindi buyer amenipea offer",s,"sw"))

if __name__=="__main__":
    unittest.main()
