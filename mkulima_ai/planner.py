import re
from datetime import datetime, timezone
from tool_registry import describe_tools, evidence_summary

URGENT=("dying","dead","can't breathe","cannot breathe","poison","bleeding","severe","emergency","haraka sana","inakufa","amekufa","haipumui","sumu","damu nyingi")
GOALS={
 "crop_health":"protect_crop","weather":"plan_farm_action","livestock":"protect_livestock",
 "profit":"maximize_net_income","sell":"sell_well","storage":"reduce_postharvest_loss",
 "transport":"reduce_logistics_cost","inputs":"choose_input_safely","finance":"finance_farm_action",
 "harvest":"harvest_at_right_time","general":"understand_farmer_goal"
}
TOOLS={
 "crop_health":["trusted_agronomy_knowledge","farmer_symptoms","image_when_available"],
 "weather":["live_weather","location_context"],
 "livestock":["trusted_veterinary_knowledge","farmer_symptoms"],
 "profit":["farm_calculator"],
 "sell":["live_market_prices","buyer_directory","farm_calculator"],
 "storage":["postharvest_knowledge","farm_calculator"],
 "transport":["farm_calculator","buyer_or_market_destination"],
 "inputs":["trusted_agronomy_knowledge","registered_input_reference"],
 "finance":["verified_finance_options","farm_calculator"],
 "harvest":["trusted_agronomy_knowledge","live_weather"],
 "general":["conversation_reasoner"]
}

def build_plan(text,state):
    state=dict(state or {})
    intent=state.get("primary_intent","general")
    t=(text or "").lower()
    urgent=any(x in t for x in URGENT)
    plan={
      "goal":GOALS.get(intent,GOALS["general"]),
      "intent":intent,
      "secondary_intents":(state.get("intents") or [])[1:],
      "crop":state.get("crop"),
      "location":state.get("location"),
      "urgency":"urgent" if urgent else "normal",
      "tools":TOOLS.get(intent,TOOLS["general"]),
      "created_at":datetime.now(timezone.utc).isoformat()
    }
    missing=[]
    if intent in ("weather","sell") and not state.get("location"): missing.append("location")
    if intent=="sell":
        if state.get("bags") is None: missing.append("quantity")
        if state.get("offer") is None: missing.append("buyer_offer")
    if intent=="crop_health": missing.extend(["symptoms","timing"])
    if intent=="livestock": missing.extend(["animal","symptoms"])
    if intent=="inputs": missing.extend(["plot_size","growth_stage","problem_to_solve"])
    if intent=="profit": missing.extend(["selling_price","known_costs"])
    plan["missing_evidence"]=missing
    plan["tool_status"]=describe_tools(plan["tools"])
    plan["evidence"]=evidence_summary(plan)
    plan["can_answer_from_memory"]=not missing
    plan["can_execute_all_tools"]=all(x.get("status")=="available" for x in plan["tool_status"])
    return plan

def safe_reasoning_reply(text,state,lang):
    plan=build_plan(text,state)
    # Never pretend an unavailable live tool has run.
    if plan["urgency"]=="urgent" and plan["intent"]=="livestock":
        return ("This may be urgent. Contact a veterinary professional now. While arranging help, tell me the animal, age if known, symptoms, when they started, and your location. Don't give a drug or chemical unless a qualified professional or verified label supports it."
                if lang=="en" else "Hii inaweza kuwa urgent. Tafuta veterinary professional sasa. Ukiendelea kupanga msaada, niambie mnyama, umri kama unajua, dalili, zilianza lini na eneo lako. Usipeane dawa/chemical kwa kubahatisha.")
    if plan["urgency"]=="urgent" and plan["intent"]=="crop_health":
        return ("This sounds time-sensitive. Tell me the crop, exact symptoms, when they started, how much of the field is affected and your location. Avoid applying an unknown pesticide while we narrow the cause."
                if lang=="en" else "Hii inaonekana time-sensitive. Niambie zao, dalili exact, zilianza lini, sehemu gani ya shamba imeathirika na eneo lako. Usitumie pesticide usiyo na uhakika nayo kabla tuchambue cause.")
    return None
