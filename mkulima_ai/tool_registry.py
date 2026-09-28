from datetime import date, datetime

# Tool registry is deliberately conservative. A tool is only "available" when
# Mkulima has a supported execution path, not merely because a useful website exists.
TOOL_REGISTRY = {
    "live_weather": {
        "status": "planned",
        "provider": "Kenya Meteorological Department / KALRO KAOP",
        "source_url": "https://meteo.go.ke/",
        "live": True,
        "reason": "Authoritative sources identified; supported API adapter not wired yet.",
    },
    "live_market_prices": {
        "status": "planned",
        "provider": "KALRO / KAMIS",
        "source_url": "https://kamis.kilimo.go.ke/",
        "live": True,
        "reason": "Official market source identified; supported machine-readable adapter not wired yet.",
    },
    "trusted_agronomy_knowledge": {
        "status": "source_available",
        "provider": "KALRO Kenya Digital Agriculture Platform / GAPs",
        "source_url": "https://keep.kalro.org/",
        "live": False,
        "reason": "Trusted Kenyan knowledge source identified; retrieval adapter pending.",
    },
    "trusted_veterinary_knowledge": {
        "status": "source_available",
        "provider": "KALRO",
        "source_url": "https://keep.kalro.org/",
        "live": False,
        "reason": "Trusted livestock source identified; retrieval adapter pending.",
    },
    "postharvest_knowledge": {
        "status": "source_available",
        "provider": "KALRO",
        "source_url": "https://keep.kalro.org/",
        "live": False,
        "reason": "Trusted post-harvest source identified; retrieval adapter pending.",
    },
    "farm_calculator": {"status": "available", "provider": "Mkulima AI", "live": False},
    "farmer_symptoms": {"status": "available", "provider": "Farmer conversation", "live": False},
    "location_context": {"status": "available", "provider": "Farmer conversation", "live": False},
    "buyer_directory": {"status": "planned", "provider": None, "live": True, "reason": "Verified buyer directory not connected."},
    "buyer_or_market_destination": {"status": "planned", "provider": None, "live": True, "reason": "Destination lookup not connected."},
    "registered_input_reference": {"status": "planned", "provider": None, "live": False, "reason": "Registered-input reference adapter not connected."},
    "verified_finance_options": {"status": "planned", "provider": None, "live": True, "reason": "Verified finance source not connected."},
    "farm_vision": {"status": "input_available", "provider": "WhatsApp image input", "live": True, "reason": "Image messages are accepted and contextualized; visual inference adapter is not connected yet."},
    "conversation_reasoner": {"status": "available", "provider": "Mkulima AI planner", "live": False},
}

def describe_tools(tool_names):
    out=[]
    for name in tool_names or []:
        meta=dict(TOOL_REGISTRY.get(name, {"status":"unavailable","provider":None,"live":False,"reason":"Unknown tool."}))
        meta["name"]=name
        out.append(meta)
    return out

def tool_is_executable(name):
    return TOOL_REGISTRY.get(name,{}).get("status")=="available"

def stale_reference_tool(name, provider, observed_on, value=None, unit=None, max_age_days=14):
    observed=datetime.strptime(observed_on,"%Y-%m-%d").date()
    age=(date.today()-observed).days
    return {
        "name":name,
        "status":"available" if age <= max_age_days else "stale",
        "provider":provider,
        "observed_on":observed_on,
        "age_days":age,
        "value":value,
        "unit":unit,
        "live":False,
        "provenance":{"provider":provider,"observed_on":observed_on},
    }

def evidence_summary(plan):
    tools=plan.get("tool_status",[]) if plan else []
    executed=[x["name"] for x in tools if x.get("status")=="available"]
    blocked=[x["name"] for x in tools if x.get("status") in ("planned","unavailable")]
    sources=[{"tool":x["name"],"provider":x.get("provider"),"source_url":x.get("source_url")} for x in tools if x.get("provider")]
    return {"executable":executed,"not_executed":blocked,"sources":sources}
