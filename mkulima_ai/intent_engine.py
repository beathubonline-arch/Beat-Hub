import re

INTENT_RULES = {
    "sell": ("sell","selling","buyer","broker","market","price","bei","uza","kuuza","mnunuzi","dalali","offer","haraka","cash"),
    "crop_health": ("blight","disease","sick","yellow","wilting","spots","pest","insect","worm","funza","ugonjwa","wadudu","majani","kunyauka","dawa"),
    "storage": ("store","storage","hifadhi","ghala","aflatoxin","dry","drying","moisture","kauka","kukausha"),
    "transport": ("transport","lorry","pickup","boda","fare","delivery","peleka","usafiri","truck"),
    "inputs": ("seed","seeds","fertilizer","fertiliser","mbegu","mbolea","input","pesticide","herbicide"),
    "weather": ("weather","rain","raining","forecast","mvua","hali ya hewa","drought","ukame"),
    "livestock": ("cow","cattle","goat","sheep","chicken","poultry","ngombe","mbuzi","kondoo","kuku","livestock"),
    "finance": ("loan","credit","money","capital","mkopo","pesa","mtaji","finance","financing"),
    "profit": ("profit","loss","cost","margin","faida","hasara","gharama","calculate","hesabu"),
    "harvest": ("harvest","ready","ripe","mavuno","vuna","kuvuna","mature"),
}

CROPS=("maize","mahindi","tomato","tomatoes","nyanya","potato","potatoes","viazi","beans","maharagwe","wheat","ngano","rice","mchele","onion","onions","vitunguu","avocado","avocados","parachichi","tea","chai","coffee","kahawa")

def detect_intents(text):
    t=" "+re.sub(r"[^a-z0-9\s']"," ",(text or "").lower())+" "
    scores={}
    for intent,words in INTENT_RULES.items():
        score=sum(1 for w in words if (" "+w+" ") in t or (len(w)>5 and w in t))
        if score: scores[intent]=score
    priority={"crop_health":0,"weather":1,"livestock":2,"profit":3,"sell":4,"storage":5,"transport":6,"inputs":7,"finance":8,"harvest":9}
    ordered=sorted(scores,key=lambda k:(-scores[k],priority.get(k,99)))
    return ordered[:3] or ["general"]

def detect_crop(text,previous=None):
    t=(text or "").lower()
    for crop in CROPS:
        if re.search(r"\b"+re.escape(crop)+r"\b",t):
            aliases={"mahindi":"maize","tomatoes":"tomato","nyanya":"tomato","potatoes":"potato","viazi":"potato","maharagwe":"beans","ngano":"wheat","mchele":"rice","onions":"onion","vitunguu":"onion","avocados":"avocado","parachichi":"avocado","chai":"tea","kahawa":"coffee"}
            return aliases.get(crop,crop)
    return previous

def enrich_context(text,state):
    state=dict(state or {})
    intents=detect_intents(text)
    # Short follow-ups after a farm photo often contain no intent keywords.
    # Keep the active case intent instead of resetting to a generic flow.
    if intents==["general"] and state.get("has_image") and state.get("primary_intent") not in (None,"general"):
        intents=state.get("intents") or [state["primary_intent"]]
    state["intents"]=intents
    state["primary_intent"]=intents[0]
    crop=detect_crop(text,state.get("crop"))
    if crop: state["crop"]=crop
    return state

def open_reply(text,state,lang):
    intents=state.get("intents") or ["general"]
    primary=intents[0]
    crop=state.get("crop")
    loc=state.get("location")
    subject=crop or ("your crop" if lang=="en" else "zao lako")
    where=(f" in {loc}" if lang=="en" and loc else f" huko {loc}" if loc else "")

    if primary=="crop_health":
        if lang=="en":
            return f"I can help diagnose the problem affecting {subject}{where}. Tell me what you can see on the plant (leaves, stem, fruit or roots), when it started, and whether it is spreading. A photo will also help once image diagnosis is enabled. I won't guess a pesticide before the symptoms are clear."
        if lang=="mixed":
            return f"Naweza kusaidia kuchambua shida ya {subject}{where}. Niambie unaona nini kwa leaves/stem/fruit/roots, ilianza lini, na kama inaenea. Sitaguess dawa kabla symptoms zieleweke."
        return f"Naweza kusaidia kuchambua tatizo la {subject}{where}. Niambie unaona nini kwenye majani, shina, matunda au mizizi, lilianza lini, na kama linaenea. Sitapendekeza dawa kwa kubahatisha kabla dalili ziwe wazi."

    if primary=="storage":
        q="How long do you need to store it, and is it already dry?" if lang=="en" else "Unataka kuhifadhi kwa muda gani, na mazao yamekauka vizuri?"
        return f"Storage can change whether selling now or waiting makes sense. {q}"

    if primary=="transport":
        q="Tell me the destination and the transport quote you have." if lang=="en" else "Niambie unapeleka wapi na umepewa transport quote ya pesa ngapi."
        return f"I can compare transport against what you keep after the sale. {q}" if lang=="en" else f"Naweza kulinganisha transport na pesa utakayobaki nayo baada ya kuuza. {q}"

    if primary=="weather":
        return ("I can help you decide what the weather means for your farm, but I need your location and the decision you're making — planting, spraying, harvesting or drying. I won't invent a live forecast."
                if lang=="en" else "Naweza kusaidia kuamua hali ya hewa ina maana gani kwa shamba lako, lakini niambie eneo lako na decision unayotaka kufanya — kupanda, kunyunyiza, kuvuna au kukausha. Sitabuni forecast ya leo.")

    if primary=="livestock":
        return ("Tell me the animal, its age if known, and what changed — eating, movement, stool, breathing, milk/eggs, wounds or other symptoms. For severe illness or distress, contact a veterinary professional urgently."
                if lang=="en" else "Niambie ni mnyama gani, umri kama unajua, na nini imebadilika — kula, kutembea, choo, kupumua, maziwa/mayai, jeraha au dalili nyingine. Ikiwa hali ni kali, tafuta veterinary professional haraka.")

    if primary=="inputs":
        return ("Tell me the crop, acreage/plot size, growth stage and what you want the input to solve. I can then narrow the options without guessing a product or dose."
                if lang=="en" else "Niambie zao, ukubwa wa shamba, hatua ya ukuaji na shida unayotaka input itatue. Hapo naweza kupunguza options bila kubahatisha product au dose.")

    if primary=="finance":
        return ("Tell me what the money is for, how much you need and when you need it. I can help compare practical options and repayment risk; I won't invent a lender or approval."
                if lang=="en" else "Niambie pesa ni ya nini, unahitaji kiasi gani na lini. Naweza kusaidia kulinganisha options na repayment risk; sitabuni lender au approval.")

    if primary=="profit":
        return ("Send me your expected selling price plus the main costs you know — inputs, labour, transport and storage — and I'll calculate the cash left."
                if lang=="en" else "Nitumie expected selling price na gharama unazojua — inputs, labour, transport na storage — nikuhesabie pesa inayobaki.")

    if primary=="harvest":
        return (f"For {subject}{where}, tell me what makes you think it is ready and whether your priority is quality, avoiding losses, or selling quickly."
                if lang=="en" else f"Kwa {subject}{where}, niambie ni nini inaonyesha iko ready na priority yako ni quality, kuepuka loss, ama kuuza haraka.")

    if primary=="sell":
        # The established maize sale calculator remains the specialist path.
        return None

    return ("I can work with that. Tell me what outcome you want most right now — save the crop, sell it, reduce costs, find a buyer, plan the next farm action, or something else. You can explain it naturally."
            if lang=="en" else "Naweza kusaidia. Niambie outcome unayotaka zaidi sasa — kuokoa zao, kuuza, kupunguza gharama, kupata buyer, kupanga hatua inayofuata shambani, ama kitu kingine. Eleza vile ungeongea kawaida.")
