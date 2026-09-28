import os, sys, unittest, json
ROOT=os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0,os.path.join(ROOT,"mkulima_ai"))
from intent_engine import detect_intents, enrich_context, open_reply
from planner import build_plan, safe_reasoning_reply
from tool_registry import describe_tools, tool_is_executable, stale_reference_tool
from farm_vision import analyze_farm_image, safe_vision_reply
from weather_live import weather_reply
import app as app_module

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

    def test_live_weather_is_executable_after_adapter_wiring(self):
        s=enrich_context("Mvua itanyesha kesho? Niko Kitale",{"location":"Kitale"})
        p=build_plan("Mvua itanyesha kesho? Niko Kitale",s)
        weather=[x for x in p["tool_status"] if x["name"]=="live_weather"][0]
        self.assertEqual(weather["status"],"available")
        self.assertTrue(tool_is_executable("live_weather"))
        self.assertNotIn("live_weather",p["evidence"]["not_executed"])

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


class MkulimaPaymentsRegression(unittest.TestCase):
    def test_pricing_page_has_launch_prices(self):
        client=app_module.app.test_client()
        r=client.get("/pricing")
        self.assertEqual(r.status_code,200)
        body=r.get_data(as_text=True)
        self.assertIn("KES 49",body)
        self.assertIn("KES 199",body)
        self.assertIn("5 useful questions",body)

    def test_unsigned_checkout_is_rejected_before_paystack(self):
        client=app_module.app.test_client()
        r=client.post("/pay/start",data={"plan":"day_pass","email":"farmer@example.com"})
        self.assertEqual(r.status_code,400)
        self.assertIn("WhatsApp",r.get_data(as_text=True))

    def test_plan_prices_are_server_controlled(self):
        self.assertEqual(app_module.MZ_PLANS["day_pass"]["amount_kes"],49)
        self.assertEqual(app_module.MZ_PLANS["plus_monthly"]["amount_kes"],199)
        self.assertEqual(app_module.MZ_PLANS["day_pass"]["days"],1)
        self.assertEqual(app_module.MZ_PLANS["plus_monthly"]["days"],30)



class ConversationLoopRegression(unittest.TestCase):
    def test_sale_followups_do_not_reset_to_general(self):
        state={}
        for message in ("beans","kuuza beans","narok town","kuuza mahindi na maharagwe","50"):
            state=app_module.apply_message(message,state)
        self.assertEqual(state.get("primary_intent"),"sell")
        self.assertEqual(state.get("bags"),50)
        self.assertEqual(state.get("stage"),"offer")
        reply=app_module.reply_for("50",state)
        self.assertNotIn("outcome unayotaka",reply.lower())
        self.assertTrue("price" in reply.lower() or "bei" in reply.lower())

    def test_location_followup_keeps_sell_intent(self):
        state=app_module.apply_message("nataka kuuza beans",{})
        state=app_module.apply_message("Narok Town",state)
        self.assertEqual(state.get("primary_intent"),"sell")
        self.assertEqual(state.get("location"),"Narok Town")
        self.assertEqual(state.get("stage"),"bags")

    def test_known_location_is_not_asked_twice_and_numbers_advance(self):
        state={}
        for message in ("I want to sell beans","Narok Town","50","593","sell the bags"):
            state=app_module.apply_message(message,state)
        self.assertEqual(state.get("crop"),"beans")
        self.assertEqual(state.get("location"),"Narok Town")
        self.assertEqual(state.get("bags"),50)
        self.assertEqual(state.get("offer"),593)
        self.assertEqual(state.get("stage"),"complete")
        reply=app_module.reply_for("sell the bags",state)
        self.assertNotIn("where exactly",reply.lower())
        self.assertNotIn("outcome",reply.lower())

    def test_simple_farmer_gets_only_next_missing_fact(self):
        state=app_module.apply_message("sell beans",{})
        self.assertEqual(state.get("stage"),"location")
        state=app_module.apply_message("Narok Town",state)
        self.assertEqual(state.get("stage"),"bags")
        state=app_module.apply_message("50",state)
        self.assertEqual(state.get("stage"),"offer")

    def test_egg_sale_keeps_product_location_and_trays(self):
        state={}
        state=app_module.apply_message("I need to sell eggs",state)
        self.assertEqual(state.get("product"),"eggs")
        self.assertEqual(state.get("stage"),"location")
        state=app_module.apply_message("Ngara Estate",state)
        self.assertEqual(state.get("location"),"Ngara Estate")
        self.assertEqual(state.get("stage"),"quantity")
        state=app_module.apply_message("thirty trays",state)
        self.assertEqual(state.get("quantity"),30)
        self.assertEqual(state.get("quantity_unit"),"trays")
        self.assertEqual(state.get("stage"),"sale_timing")
        reply=app_module.reply_for("thirty trays",state)
        self.assertIn("30 trays of eggs",reply.lower())
        self.assertNotIn("bags of produce",reply.lower())
        self.assertIn("today",reply.lower())

    def test_egg_sale_flow_is_repeatable(self):
        for _ in range(2):
            state={}
            for message in ("I need to sell eggs","Ngara Estate","thirty trays"):
                state=app_module.apply_message(message,state)
            reply=app_module.reply_for("thirty trays",state)
            self.assertEqual(state.get("product"),"eggs")
            self.assertEqual(state.get("location"),"Ngara Estate")
            self.assertEqual(state.get("quantity"),30)
            self.assertEqual(state.get("quantity_unit"),"trays")
            self.assertNotIn("how many bags",reply.lower())

    def test_continue_advances_nonbag_sale_instead_of_repeating(self):
        state={}
        for message in ("I'm selling eggs","Kisumu Ndogo Eld","300","checking best price","ok cont"):
            state=app_module.apply_message(message,state)
        reply=app_module.reply_for("ok cont",state)
        self.assertEqual(state.get("stage"),"offer")
        self.assertIn("buyer offer",reply.lower())
        self.assertNotIn("next i can help you compare buyer options",reply.lower())

    def test_do_it_advances_same_case_twice(self):
        for phrase in ("do it","proceed"):
            state={}
            for message in ("I'm selling eggs","Kisumu Ndogo Eld","300","checking best price",phrase):
                state=app_module.apply_message(message,state)
            reply=app_module.reply_for(phrase,state)
            self.assertIn("price in kes per tray",reply.lower())
            self.assertNotIn("next i can help",reply.lower())

    def test_no_offer_generates_truthful_buyer_ready_listing(self):
        state={}
        for message in ("I'm selling eggs","Kisumu Ndogo Eld","300","checking best price","no offer"):
            state=app_module.apply_message(message,state)
        reply=app_module.reply_for("no offer",state)
        self.assertIn("buyer-ready listing",reply.lower())
        self.assertIn("300 trays of eggs",reply.lower())
        self.assertIn("not connected yet",reply.lower())


class GrowthRevenueRegression(unittest.TestCase):
    def test_first_touch_source_is_remembered_and_not_overwritten(self):
        state=app_module.apply_message("source fb_maize_01",{})
        self.assertEqual(state.get("acquisition_source"),"fb_maize_01")
        state=app_module.apply_message("source wa_narok_01",state)
        self.assertEqual(state.get("acquisition_source"),"fb_maize_01")

    def test_feedback_language_maps_to_safe_ratings(self):
        self.assertEqual(app_module.feedback_rating("helpful"),"helpful")
        self.assertEqual(app_module.feedback_rating("imenisaidia"),"helpful")
        self.assertEqual(app_module.feedback_rating("si sahihi"),"wrong")
        self.assertEqual(app_module.feedback_rating("bado shida"),"still_problem")
        self.assertIsNone(app_module.feedback_rating("sell beans"))

class AppSmokeRegression(unittest.TestCase):
    def test_app_module_imports_cleanly(self):
        import py_compile
        py_compile.compile(os.path.join(ROOT,"mkulima_ai","app.py"),doraise=True)

class LearningPersistenceRegression(unittest.TestCase):
    def test_interaction_record_is_deidentified(self):
        import state_store
        old_url=os.environ.get("SUPABASE_URL"); old_key=os.environ.get("SUPABASE_SECRET_KEY")
        original=state_store._api; seen={}
        try:
            os.environ["SUPABASE_URL"]="https://example.supabase.co"
            os.environ["SUPABASE_SECRET_KEY"]="test-secret"
            def fake_api(method,path,body=None,prefer=None):
                seen.update(method=method,path=path,body=body,prefer=prefer)
                return [{"id":"interaction-1"}]
            state_store._api=fake_api
            iid=state_store.record_interaction("wamid.1","254700123456","maize leaves yellow","check timing",{"crop":"maize","location":"Turbo","language":"en","primary_intent":"crop_health"})
            self.assertEqual(iid,"interaction-1")
            self.assertNotIn("254700123456",json.dumps(seen))
            self.assertEqual(seen["body"]["crop"],"maize")
        finally:
            state_store._api=original
            if old_url is None: os.environ.pop("SUPABASE_URL",None)
            else: os.environ["SUPABASE_URL"]=old_url
            if old_key is None: os.environ.pop("SUPABASE_SECRET_KEY",None)
            else: os.environ["SUPABASE_SECRET_KEY"]=old_key

    def test_feedback_rejects_unknown_rating(self):
        import state_store
        self.assertFalse(state_store.record_feedback("fb.1","interaction-1","maybe"))

class RevenueInstrumentationRegression(unittest.TestCase):
    def test_revenue_rejects_unverified_event_type(self):
        import state_store
        self.assertFalse(state_store.record_revenue_event("254700123456","estimated_revenue",500))

    def test_revenue_payload_is_pseudonymous(self):
        import state_store
        old_url=os.environ.get("SUPABASE_URL"); old_key=os.environ.get("SUPABASE_SECRET_KEY")
        old_salt=os.environ.get("MKULIMA_ACTOR_SALT"); original=state_store._api; seen={}
        try:
            os.environ["SUPABASE_URL"]="https://example.supabase.co"
            os.environ["SUPABASE_SECRET_KEY"]="test-secret"
            os.environ["MKULIMA_ACTOR_SALT"]="test-only-salt"
            def fake_api(method,path,body=None,prefer=None):
                seen.update(method=method,path=path,body=body,prefer=prefer)
            state_store._api=fake_api
            phone="254700123456"
            self.assertTrue(state_store.record_revenue_event(phone,"payment_success",100,"fb_maize_01"))
            self.assertNotIn(phone,json.dumps(seen))
            self.assertEqual(seen["body"]["amount_kes"],100.0)
            self.assertEqual(seen["body"]["source"],"fb_maize_01")
        finally:
            state_store._api=original
            for key,old in (("SUPABASE_URL",old_url),("SUPABASE_SECRET_KEY",old_key),("MKULIMA_ACTOR_SALT",old_salt)):
                if old is None: os.environ.pop(key,None)
                else: os.environ[key]=old

class DurableStateRegression(unittest.TestCase):
    def test_actor_ref_is_pseudonymous(self):
        import state_store
        old=os.environ.get("MKULIMA_ACTOR_SALT")
        try:
            os.environ["MKULIMA_ACTOR_SALT"]="test-only-salt"
            phone="254700123456"
            ref=state_store.actor_ref(phone)
            self.assertEqual(ref,state_store.actor_ref(phone))
            self.assertNotIn(phone,ref)
            self.assertEqual(len(ref),64)
        finally:
            if old is None: os.environ.pop("MKULIMA_ACTOR_SALT",None)
            else: os.environ["MKULIMA_ACTOR_SALT"]=old

    def test_supabase_state_payload_excludes_raw_phone(self):
        import state_store
        old_url=os.environ.get("SUPABASE_URL"); old_key=os.environ.get("SUPABASE_SECRET_KEY")
        original=state_store._api
        seen={}
        try:
            os.environ["SUPABASE_URL"]="https://example.supabase.co"
            os.environ["SUPABASE_SECRET_KEY"]="test-secret"
            def fake_api(method,path,body=None,prefer=None):
                seen.update(method=method,path=path,body=body,prefer=prefer)
            state_store._api=fake_api
            self.assertEqual(state_store.save_state("254700123456",{"crop":"maize"}),"supabase")
            self.assertNotIn("254700123456",json.dumps(seen))
        finally:
            state_store._api=original
            if old_url is None: os.environ.pop("SUPABASE_URL",None)
            else: os.environ["SUPABASE_URL"]=old_url
            if old_key is None: os.environ.pop("SUPABASE_SECRET_KEY",None)
            else: os.environ["SUPABASE_SECRET_KEY"]=old_key
