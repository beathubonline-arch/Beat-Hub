import os, re, sqlite3, json, hashlib, hmac, secrets
from datetime import date, datetime
from urllib import request as urlrequest, parse as urlparse
from flask import Flask, request, jsonify, redirect
from integrations import send_whatsapp_text
from intent_engine import enrich_context, open_reply
from planner import build_plan, safe_reasoning_reply
from farm_vision import download_whatsapp_media, analyze_farm_image, safe_vision_reply
from weather_live import live_weather, weather_reply
from state_store import load_state, save_state, claim_message, record_interaction, record_feedback, actor_ref, get_access, consume_free_question, create_payment_for_actor, get_payment, mark_payment_paid

app=Flask(__name__)
DB=os.getenv("DB_PATH","/tmp/mkulima.db")
FRESH_DAYS=14
SOURCE="Warehouse Receipt System Council"
OBSERVED_ON="2026-08-10"
PRICE_PER_90KG=3730.50

MZ_PLANS={
    "day_pass":{"name":"Mkulima Day Pass","amount_kes":49,"days":1},
    "plus_monthly":{"name":"Mkulima Plus","amount_kes":199,"days":30},
}
MKULIMA_BASE_URL=os.getenv("MKULIMA_BASE_URL","https://mkulima-ai-whatsapp.onrender.com").rstrip("/")

def _pay_secret():
    return (os.getenv("MKULIMA_PAYSTACK_SECRET_KEY") or os.getenv("PAYSTACK_SECRET_KEY") or "").strip()

def _checkout_signing_secret():
    return (os.getenv("MKULIMA_CHECKOUT_SECRET") or os.getenv("MKULIMA_ACTOR_SALT") or os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").encode()

def _sign_actor(ref):
    key=_checkout_signing_secret()
    if not key or not ref: return None
    return hmac.new(key,ref.encode(),hashlib.sha256).hexdigest()

def _valid_actor(ref,sig):
    expected=_sign_actor(ref)
    return bool(expected and sig and hmac.compare_digest(expected,sig))

def upgrade_url(phone):
    ref=actor_ref(phone)
    sig=_sign_actor(ref) if ref else None
    if not (ref and sig): return MKULIMA_BASE_URL+"/pricing"
    return MKULIMA_BASE_URL+"/pricing?actor="+urlparse.quote(ref)+"&sig="+urlparse.quote(sig)

def _paystack_json(method,path,payload=None):
    secret=_pay_secret()
    if not secret: raise RuntimeError("paystack_not_configured")
    data=None if payload is None else json.dumps(payload).encode()
    req=urlrequest.Request("https://api.paystack.co"+path,data=data,method=method,headers={
        "Authorization":"Bearer "+secret,"Content-Type":"application/json"
    })
    with urlrequest.urlopen(req,timeout=15) as res:
        return json.loads(res.read().decode())

def _verify_paystack_reference(reference):
    p=get_payment(reference)
    if not p: return False,None
    try:
        v=_paystack_json("GET","/transaction/verify/"+urlparse.quote(reference))
    except Exception:
        return False,None
    d=(v or {}).get("data") or {}
    valid=bool(v.get("status") and d.get("status")=="success" and int(d.get("amount") or 0)==int(p.get("amount_kes") or 0)*100 and d.get("currency")=="KES")
    if valid: mark_payment_paid(reference,d)
    return valid,d


def init_db():
    con=sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS conversations(phone TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT '{}')""")
    con.execute("""CREATE TABLE IF NOT EXISTS processed_messages(message_id TEXT PRIMARY KEY,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit(); con.close()

def age_days():
    return (date.today()-datetime.strptime(OBSERVED_ON,"%Y-%m-%d").date()).days

def detect_language(text):
    t=" "+(text or "").lower()+" "
    sw=sum(1 for w in [" niko "," gunia "," bei "," karibu "," nifanye "," aje "," eneo "," mahindi "," amekupea "," nataka "] if w in t)
    en=sum(1 for w in [" i "," have "," bags "," buyer "," price "," near "," what "," should "," sell "," broker "," offer "] if w in t)
    if sw and en: return "mixed"
    if sw>en: return "sw"
    return "en"

def extract_location(text):
    raw=" ".join((text or "").strip().split())
    # Capture location after natural location cues, stopping before quantity/offer details.
    m=re.search(r"(?:^|\b)(?:niko|nipo|from|i am in|i'm in|near|around|eneo(?: langu)? ni|location(?: yangu)? ni)\s+(.+)",raw,re.I)
    if not m: return None
    loc=m.group(1)
    loc=re.split(r"\b(?:na\s+)?\d+(?:\.\d+)?\s*(?:bags?|gunia|sacks?)\b|\b(?:buyer|broker|offer|bei)\b",loc,1,flags=re.I)[0]
    loc=loc.strip(" ,.-")
    return loc[:160].title() if loc else None

NUMBER_WORDS={
    "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
    "eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,
    "twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90,
    "moja":1,"mbili":2,"tatu":3,"nne":4,"tano":5,"sita":6,"saba":7,"nane":8,"tisa":9,"kumi":10
}
PRODUCT_ALIASES={
    "eggs":"eggs","egg":"eggs","mayai":"eggs",
    "milk":"milk","maziwa":"milk",
    "maize":"maize","mahindi":"maize",
    "tomato":"tomatoes","tomatoes":"tomatoes","nyanya":"tomatoes",
    "potato":"potatoes","potatoes":"potatoes","viazi":"potatoes",
    "beans":"beans","maharagwe":"beans",
    "banana":"bananas","bananas":"bananas","ndizi":"bananas",
    "cabbage":"cabbages","cabbages":"cabbages","kabichi":"cabbages"
}
UNIT_ALIASES={
    "tray":"trays","trays":"trays","trei":"trays",
    "litre":"litres","litres":"litres","liter":"litres","liters":"litres","lita":"litres",
    "crate":"crates","crates":"crates","kreti":"crates",
    "bag":"bags","bags":"bags","gunia":"bags","sack":"bags","sacks":"bags",
    "kg":"kg","kgs":"kg","kilo":"kg","kilos":"kg",
    "bunch":"bunches","bunches":"bunches",
    "head":"heads","heads":"heads","pieces":"pieces","piece":"pieces"
}
DEFAULT_UNITS={"eggs":"trays","milk":"litres","tomatoes":"crates","maize":"bags","beans":"bags","potatoes":"bags","bananas":"bunches","cabbages":"heads"}
NON_BAG_SALE_PRODUCTS={"eggs","milk","tomatoes","bananas","cabbages"}

def _word_number(raw):
    parts=re.findall(r"[a-z]+",raw.lower())
    total=0; seen=False
    for p in parts:
        if p in NUMBER_WORDS:
            total+=NUMBER_WORDS[p]; seen=True
        elif p in {"and","na"}:
            continue
        else:
            return None
    return float(total) if seen and total>0 else None

def parse(text):
    t=" ".join((text or "").lower().split())
    out={}
    for token,product in PRODUCT_ALIASES.items():
        if re.search(r"\b"+re.escape(token)+r"\b",t):
            out["product"]=product
            break
    unit_group="|".join(sorted((re.escape(k) for k in UNIT_ALIASES),key=len,reverse=True))
    qm=re.search(r"(\d+(?:\.\d+)?)\s*("+unit_group+r")\b",t)
    if not qm:
        qm=re.search(r"\b([a-z]+(?:\s+[a-z]+){0,2})\s+("+unit_group+r")\b",t)
    if qm:
        q=float(qm.group(1)) if re.fullmatch(r"\d+(?:\.\d+)?",qm.group(1)) else _word_number(qm.group(1))
        if q is not None:
            unit=UNIT_ALIASES[qm.group(2)]
            out["quantity"]=q
            out["quantity_unit"]=unit
            if unit=="bags": out["bags"]=q
    for p in [
        r"(?:buyer|broker).{0,40}?(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)",
        r"(?:offer|bei|anapea|amepea|ameoffer|anataka kununua)\D{0,25}(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)",
        r"(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)\s*(?:bag|gunia)"
    ]:
        pm=re.search(p,t)
        if pm:
            out["offer"]=float(pm.group(1).replace(",","")); break
    loc=extract_location(text)
    if loc: out["location"]=loc
    bare=re.fullmatch(r"(?:kes|ksh|sh)?\s*([0-9][0-9,]*(?:\.\d+)?)",t)
    if bare: out["_bare_number"]=float(bare.group(1).replace(",",""))
    return out

def capture_growth_signal(text,state):
    """Remember privacy-safe first-touch/referral tags carried in a WhatsApp message."""
    state=dict(state or {})
    raw=" ".join((text or "").strip().split())
    m=re.match(r"^(?:start|source|ref)\s*[:= -]?\s*([a-z0-9][a-z0-9_-]{1,63})$",raw,re.I)
    if m and not state.get("acquisition_source"):
        state["acquisition_source"]=m.group(1).lower()
        state["acquired_at"]=datetime.utcnow().isoformat()+"Z"
    return state

def feedback_rating(text):
    t=" ".join((text or "").strip().lower().split())
    if t in {"helpful","helped","yes helpful","imesaidia","imenisaidia","sawa imesaidia"}: return "helpful"
    if t in {"wrong","si sahihi","incorrect","hapana si sahihi"}: return "wrong"
    if t in {"still problem","still have problem","bado shida","bado iko","bado"}: return "still_problem"
    return None


CONTINUE_PHRASES={"ok cont","cont","continue","do it","proceed","go ahead","yes","yeah","yep","sawa","endelea","fanya","fanya hivyo"}
NO_OFFER_PHRASES={"no offer","none","sina offer","hakuna offer","no buyer","sina buyer","hakuna buyer"}

def is_continue(text):
    t=" ".join((text or "").strip().lower().split())
    return t in CONTINUE_PHRASES

def is_no_offer(text):
    t=" ".join((text or "").strip().lower().split())
    return t in NO_OFFER_PHRASES

def enrich_case_evidence(text,state):
    """Extract lightweight evidence from natural follow-ups without pretending it is diagnosis."""
    state=dict(state or {})
    raw=" ".join((text or "").strip().split())
    t=raw.lower()
    timing=re.search(r"\\b(?:for|since|imeanza|ilianza|zilianza|tangu)\\s+(\\d+)\\s*(day|days|siku|week|weeks|wiki)\\b",t)
    if timing:
        state["symptom_timing"]=timing.group(0)
    affected=re.search(r"\\b(\\d+(?:\\.\\d+)?)\\s*(%|percent|percentage)\\b",t)
    if affected:
        state["affected_area"]=affected.group(0)
    if state.get("has_image") and raw and len(raw)<=500:
        notes=list(state.get("case_notes") or [])
        if raw not in notes:
            notes.append(raw)
        state["case_notes"]=notes[-8:]
    return state

def apply_message(text,state):
    state=capture_growth_signal(text,state)
    normalized=" ".join((text or "").lower().split())
    if any(p in normalized for p in ("new sale","new deal","bei mpya","mauzo mapya","start over","anza upya","reset")):
        state={}
    incoming=parse(text)
    bare=incoming.pop("_bare_number",None)
    stage=state.get("stage")
    # A newly named product starts a fresh selling case instead of inheriting
    # quantity/offer details from a previous crop or livestock sale.
    if incoming.get("product") and state.get("primary_intent")=="sell" and state.get("product") and incoming["product"]!=state.get("product"):
        for k in ("bags","quantity","quantity_unit","offer","sale_timing"):
            state.pop(k,None)
    # Interpret a bare number from the facts still missing in the active sale,
    # not from a stale prompt/stage. Known facts always win.
    if bare is not None and state.get("primary_intent")=="sell":
        if "quantity" not in state and "bags" not in state:
            incoming["quantity"]=bare
            incoming["quantity_unit"]=DEFAULT_UNITS.get(incoming.get("product") or state.get("product"),"units")
            if incoming["quantity_unit"]=="bags": incoming["bags"]=bare
        elif "offer" not in state: incoming["offer"]=bare
    elif stage=="bags" and bare is not None: incoming["bags"]=bare
    elif stage=="offer" and bare is not None: incoming["offer"]=bare
    elif stage=="location" and "location" not in incoming:
        raw=" ".join((text or "").strip().split())
        if raw and not re.search(r"\b(?:bags?|gunia|buyer|broker|offer|bei)\b",raw,re.I) and not re.fullmatch(r"[0-9,. ]+",raw):
            incoming["location"]=raw[:160].title()
    state.update(incoming)
    # Normalize legacy bag state into the generic quantity model.
    if state.get("bags") is not None and state.get("quantity") is None:
        state["quantity"]=state["bags"]; state["quantity_unit"]="bags"
    if state.get("primary_intent")=="sell":
        timing=" ".join((text or "").lower().split())
        if any(x in timing for x in ("today","leo","now","sasa","asap","haraka")): state["sale_timing"]="today"
        elif any(x in timing for x in ("few days","next days","this week","wiki","days")): state["sale_timing"]="few_days"
        elif any(x in timing for x in ("best price","check price","bei nzuri","bei bora","price first")): state["sale_timing"]="best_price"
        if is_no_offer(text):
            state["no_offer"]=True
            state.pop("offer",None)
        elif is_continue(text) and state.get("product") in NON_BAG_SALE_PRODUCTS and state.get("sale_timing"):
            state["next_action"]="collect_offer"
    state=enrich_case_evidence(text,state)
    state=enrich_context(text,state)
    state["plan"]=build_plan(text,state)
    state["language"]=detect_language(text) if text else state.get("language","en")
    # Only the specialist selling flow requires location/bags/offer. Other
    # farmer intents must not be forced through the maize-sale questionnaire.
    if state.get("primary_intent")=="sell" or any(k in state for k in ("bags","quantity","offer")):
        if "location" not in state: state["stage"]="location"
        elif "quantity" not in state and "bags" not in state: state["stage"]="quantity" if state.get("product") in NON_BAG_SALE_PRODUCTS else "bags"
        elif state.get("product") in NON_BAG_SALE_PRODUCTS and "sale_timing" not in state: state["stage"]="sale_timing"
        elif state.get("product") in NON_BAG_SALE_PRODUCTS and not state.get("no_offer") and "offer" not in state: state["stage"]="offer"
        elif state.get("quantity_unit")=="bags" and "offer" not in state: state["stage"]="offer"
        else: state["stage"]="complete"
    else:
        state["stage"]="open"
    return state

def image_context_reply(state, caption=""):
    lang=state.get("language","en")
    crop=state.get("crop")
    known=(f" I can see from our chat that the crop is {crop}." if crop and lang=="en" else (f" Kwa context yetu zao ni {crop}." if crop else ""))
    if lang=="en":
        return ("📷 I received the farm photo."+known+" I can use photos as evidence, but the visual-diagnosis engine is not connected yet, so I won't invent what the image shows. Tell me what you want checked (disease, pest, nutrient problem, product/label, crop quality, animal, soil or whole field), when the problem started, and your location. If possible send one close photo and one wider photo.")
    return ("📷 Nimepokea picha ya shamba."+known+" Naweza kuitumia kama evidence, lakini visual-diagnosis engine bado haijaunganishwa, kwa hivyo sitabuni diagnosis. Niambie unataka nichunguze nini—ugonjwa, pest, nutrient, product/label, quality ya mazao, mnyama, soil ama field yote—ilianza lini na uko eneo gani. Ukiweza tuma close photo moja na wide photo moja.")

def reply_for(text, known=None):
    f=dict(known or {})
    lang=detect_language(text)
    # Preserve established language on short numeric follow-ups.
    if re.fullmatch(r"(?:kes|ksh|sh)?\s*[0-9][0-9,.]*",(text or "").strip(),re.I):
        lang=f.get("language",lang)
    # Safety/urgency reasoning runs before normal intent replies.
    reasoned=safe_reasoning_reply(text,f,lang)
    if reasoned is not None:
        return reasoned
    # Open-ended intents branch before the legacy maize-sale gate.
    broad=open_reply(text,f,lang)
    if broad is not None:
        return broad
    product=f.get("product")
    quantity=f.get("quantity",f.get("bags"))
    unit=f.get("quantity_unit") or ("bags" if f.get("bags") is not None else DEFAULT_UNITS.get(product,"units"))
    if "location" not in f:
        return {"sw":"Uko eneo gani? Unaweza kutaja village, estate, road au landmark iliyo karibu.","en":"Where exactly are you? You can give your village, estate, road or a nearby landmark.","mixed":"Uko wapi exactly? Taja village, estate, road or nearby landmark."}[lang]
    if quantity is None:
        label=unit or "units"
        if lang=="en": return f"How many {label} of {product or 'produce'} do you have?"
        if lang=="mixed": return f"Uko na {label} ngapi za {product or 'produce'}?"
        return f"Una {label} ngapi za {product or 'mazao'}?"
    if product in NON_BAG_SALE_PRODUCTS and not f.get("sale_timing"):
        if lang=="en":
            return f"Got it 👍 You have {quantity:g} {unit} of {product} in {f['location']}. Are you looking to sell them today, within the next few days, or are you checking the best price first?"
        if lang=="mixed":
            return f"Sawa 👍 Uko na {quantity:g} {unit} za {product} {f['location']}. Unataka kuuza leo, within the next few days, ama tucheck best price first?"
        return f"Sawa 👍 Una {quantity:g} {unit} za {product} huko {f['location']}. Unataka kuuza leo, ndani ya siku chache, au tuangalie bei bora kwanza?"
    if product in NON_BAG_SALE_PRODUCTS and f.get("no_offer"):
        if lang=="en":
            return (f"Okay. You have {quantity:g} {unit} of {product} in {f['location']} and no buyer offer yet. "
                    f"Buyer-ready listing: FOR SALE — {quantity:g} {unit} of {product}, location: {f['location']}. Seeking serious buyers and the best verified offer. "
                    "My verified buyer directory and live market-price feed are not connected yet, so I won't invent buyers or today's price. Share this listing with buyers/co-ops you trust, then send me any offers you receive and I will compare the cash value for you.")
        return (f"Sawa. Una {quantity:g} {unit} za {product} huko {f['location']} na bado huna offer. "
                f"Buyer-ready listing: INAUZWA — {quantity:g} {unit} za {product}, eneo: {f['location']}. Tunatafuta serious buyers na best verified offer. "
                "Buyer directory na live market-price feed bado hazijaunganishwa, kwa hivyo sitabuni buyer au bei ya leo. Share listing hii kwa buyers/co-ops unaowaamini, kisha nitumie offers upate comparison.")
    if product in NON_BAG_SALE_PRODUCTS and "offer" not in f:
        per=unit[:-1] if unit.endswith("s") else unit
        if lang=="en":
            return f"Good — let's move. Do you already have a buyer offer? Send the price in KES per {per} (for example, 450 per {per}). If you have no offer yet, reply 'no offer' and I'll prepare a buyer-ready listing from the details you've already given me."
        if lang=="mixed":
            return f"Sawa, tuendelee. Uko na buyer offer? Tuma price in KES per {per} (mfano 450 per {per}). Kama huna offer, reply 'no offer' nitengeneze buyer-ready listing na details ulizonipa."
        return f"Sawa, tuendelee. Una buyer offer? Tuma bei ya KES kwa kila {per}. Kama huna offer, sema 'no offer' nitengeneze buyer-ready listing kwa details ulizonipa."
    if unit=="bags" and "offer" not in f:
        return {"sw":"Buyer/broker amekupea bei gani kwa gunia moja?","en":"What price per bag has the buyer or broker offered you?","mixed":"Buyer/broker amekuoffer how much per bag?"}[lang]
    if product in NON_BAG_SALE_PRODUCTS and f.get("offer") is not None:
        gross=quantity*f["offer"]
        per=unit[:-1] if unit.endswith("s") else unit
        if lang=="en":
            return (f"Offer captured: {quantity:g} {unit} of {product} in {f['location']} at KES {f['offer']:,.0f} per {per} = KES {gross:,.0f} gross. "
                    "I don't have a verified live market-price feed yet, so I won't label this good or bad against today's market. Send another buyer offer and I'll compare totals, or send your transport cost so I can calculate what you actually keep.")
        return (f"Offer nimehifadhi: {quantity:g} {unit} za {product} huko {f['location']} @ KES {f['offer']:,.0f} per {per} = KES {gross:,.0f} gross. "
                "Sina verified live market-price feed bado, kwa hivyo sitaiita good/bad dhidi ya bei ya leo. Tuma offer nyingine nifananishie totals, ama transport cost nihesabu net cash.")

    gross=f["bags"]*f["offer"]
    normalized=" ".join((text or "").lower().split())
    wants_help=any(p in normalized for p in ("nifanye aje","nifanye nini","what should i do","what do i do","ushauri","advise","help me","solution"))
    if wants_help:
        if lang=="en":
            return (f"You have {f['bags']:g} bags in {f['location']} at KES {f['offer']:,.0f}/bag = KES {gross:,.0f}.\n\n"
                    "Next: don't rush the sale until we verify today's market. Get 2–3 buyer offers. Send me your transport cost and, if you can store, the storage cost and how long you can wait. I'll compare the options by the cash you actually keep.")
        if lang=="mixed":
            return (f"Uko na {f['bags']:g} bags {f['location']}, offer ni KES {f['offer']:,.0f}/bag = KES {gross:,.0f}.\n\n"
                    "Next step: usiuze haraka before tuverify market ya leo. Pata offers 2–3, then nitumie transport cost. Kama unaweza store, niambie storage cost na how long unaweza wait. Nitacompare option yenye net cash nzuri.")
        return (f"Una gunia {f['bags']:g} huko {f['location']}, offer ni KES {f['offer']:,.0f}/gunia = KES {gross:,.0f}.\n\n"
                "Hatua inayofuata: usikimbilie kuuza kabla bei ya leo kuthibitishwa. Tafuta offers 2–3, kisha nitumie gharama ya transport. Kama unaweza kuhifadhi, niambie storage cost na muda unaoweza kusubiri. Nitakulinganishia pesa halisi utakayobaki nayo.")
    if age_days()>FRESH_DAYS:
        if lang=="en":
            return (f"🌽 {f['location']}: your offer totals KES {gross:,.0f} for {f['bags']:g} bags.\n"
                    f"⚠️ My verified reference is KES {PRICE_PER_90KG:,.0f}/90kg from {OBSERVED_ON} ({SOURCE}), but it is stale. I won't present it as today's price. Ask me 'what should I do?' for practical next steps.")
        if lang=="mixed":
            return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} for {f['bags']:g} bags.\n"
                    f"⚠️ Verified reference ni KES {PRICE_PER_90KG:,.0f}/90kg ya {OBSERVED_ON} ({SOURCE}), but ni old. Sitaiita bei ya leo. Niulize 'nifanye aje?' for next steps.")
        return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} kwa gunia {f['bags']:g}.\n"
                f"⚠️ Reference iliyothibitishwa ni KES {PRICE_PER_90KG:,.0f}/90kg ya {OBSERVED_ON} ({SOURCE}), lakini ni ya zamani. Sitaiita bei ya leo. Niulize 'nifanye aje?' nikupe hatua zinazofuata.")
    ref=f["bags"]*PRICE_PER_90KG
    diff=ref-gross
    return f"🌽 Offer KES {gross:,.0f} | Reference KES {ref:,.0f} | Difference KES {diff:+,.0f} | {SOURCE}, {OBSERVED_ON}."


LEGAL_STYLE = """<style>
:root{--green:#176b35;--deep:#0b3d22;--leaf:#2f8f4e;--cream:#f7f4e8;--ink:#17351f;--muted:#66756b}
*{box-sizing:border-box}body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.6;color:var(--ink);background:#fff}
h1,h2{color:var(--green)}a{color:var(--green)}small{color:#667}
</style>"""

HOME_STYLE = """<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#176b35"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="Mkulima AI"><link rel="manifest" href="/static/manifest.webmanifest"><link rel="icon" href="/static/icon.svg" type="image/svg+xml"><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,Arial,sans-serif;color:#15351f;background:#fbfcf7;line-height:1.55}
a{text-decoration:none}.wrap{width:min(1120px,calc(100% - 36px));margin:auto}
nav{height:76px;display:flex;align-items:center;justify-content:space-between}.brand{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:800;color:#103e24}.mark{width:38px;height:38px;border-radius:12px;background:#176b35;display:grid;place-items:center;color:#fff;font-size:21px}.navtag{font-size:13px;color:#52695a}
.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,#0d4827 0%,#176b35 55%,#2d8b4a 100%);color:white;padding:76px 0 70px}.hero:after{content:"";position:absolute;width:520px;height:520px;border-radius:50%;background:rgba(255,255,255,.06);right:-160px;top:-190px}
.grid{display:grid;grid-template-columns:1.12fr .88fr;gap:58px;align-items:center;position:relative;z-index:1}.eyebrow{display:inline-flex;padding:8px 12px;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.1);border-radius:999px;font-size:13px;font-weight:700;margin-bottom:20px}
h1{font-size:clamp(42px,6vw,68px);line-height:1.02;letter-spacing:-2.5px;margin:0 0 22px}.lead{font-size:20px;color:#e6f3e9;max-width:650px;margin:0 0 30px}.actions{display:flex;gap:12px;flex-wrap:wrap}.btn{display:inline-block;padding:14px 20px;border-radius:12px;font-weight:800}.primary{background:#fff;color:#145d31}.secondary{border:1px solid rgba(255,255,255,.4);color:#fff}
.phone{background:#fff;color:#17351f;border-radius:28px;padding:20px;box-shadow:0 24px 60px rgba(0,0,0,.22);max-width:380px;margin:auto}.phonehead{display:flex;gap:11px;align-items:center;padding-bottom:14px;border-bottom:1px solid #e9eee9}.avatar{width:42px;height:42px;border-radius:50%;background:#e5f4e9;display:grid;place-items:center;font-size:23px}.online{font-size:12px;color:#4d755a}.bubble{padding:12px 14px;border-radius:14px;margin-top:14px;font-size:14px}.farmer{background:#e7f6e9;margin-left:40px}.ai{background:#f1f3ef;margin-right:24px}.tick{color:#278647;font-weight:700}
.section{padding:72px 0}.center{text-align:center;max-width:700px;margin:0 auto 42px}.center h2{font-size:36px;letter-spacing:-1px;margin:0 0 12px;color:#123d24}.center p{color:#607064;margin:0}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card{background:#fff;border:1px solid #e5ebe4;border-radius:18px;padding:25px;box-shadow:0 8px 24px rgba(18,61,36,.05)}.icon{font-size:27px}.card h3{margin:12px 0 7px;color:#173e26}.card p{margin:0;color:#647168;font-size:15px}
.how{background:#f0f5eb}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:25px}.num{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:#176b35;color:white;font-weight:800}.step h3{margin:12px 0 5px}.step p{color:#657269;margin:0}
.promise{margin:70px auto;background:#123e24;color:white;border-radius:24px;padding:42px;display:flex;justify-content:space-between;gap:28px;align-items:center}.promise h2{margin:0 0 8px;font-size:30px}.promise p{margin:0;color:#d9eadf;max-width:650px}.pill{white-space:nowrap;background:#e7f5e8;color:#145c31;padding:11px 15px;border-radius:999px;font-weight:800}
footer{border-top:1px solid #e1e8e0;padding:28px 0 36px;color:#66756b;font-size:13px}.foot{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap}.links a{color:#456451;margin-left:16px}
@media(max-width:800px){.grid{grid-template-columns:1fr;gap:42px}.hero{padding:55px 0}.cards,.steps{grid-template-columns:1fr}.promise{flex-direction:column;align-items:flex-start}h1{letter-spacing:-1.5px}.navtag{display:none}.phone{max-width:100%}}
</style>"""

@app.get("/sw.js")
def service_worker():
    response=app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"]="/"
    response.headers["Cache-Control"]="no-cache"
    return response

@app.get("/")
def home():
    src=re.sub(r"[^a-z0-9_-]","",(request.args.get("src","direct") or "direct").lower())[:32] or "direct"
    return HOME_STYLE + """<style>
.funnel{background:#fff;border:1px solid rgba(255,255,255,.26);border-radius:24px;padding:22px;box-shadow:0 24px 60px rgba(0,0,0,.18)}
.funnel h3{margin:0 0 6px;color:#17351f;font-size:23px}.funnel p{margin:0 0 15px;color:#617063;font-size:14px}
.field{margin:11px 0}.field label{display:block;color:#31523a;font-weight:800;font-size:13px;margin-bottom:6px}
.field select,.field input{width:100%;border:1px solid #cbd9cb;border-radius:12px;padding:13px 12px;font:inherit;background:#fff;color:#17351f}
.wa{width:100%;border:0;background:#25D366;color:#073b1c;padding:15px 16px;border-radius:13px;font-weight:900;font-size:16px;cursor:pointer;margin-top:8px}
.trust{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px;color:#daf0df;font-size:13px}.trust span{display:inline-flex;align-items:center;gap:5px}
.quick{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}.quick button{border:1px solid #cbd9cb;background:#f7faf6;color:#245335;border-radius:999px;padding:9px 11px;font-weight:700;cursor:pointer}
.install-lite{margin-top:10px;background:transparent;border:1px solid #c9d9ca;color:#31523a;padding:10px 12px;border-radius:11px;font-weight:800;cursor:pointer;width:100%}
@media(max-width:800px){.hero{padding-top:34px}.funnel{padding:18px}.actions{gap:8px}.lead{font-size:18px}}
</style>
<nav class="wrap"><a class="brand" href="/"><span class="mark">🌱</span>Mkulima AI</a><span class="navtag">Built for Kenyan farmers</span></nav>
<section class="hero"><div class="wrap grid"><div>
<span class="eyebrow">🇰🇪 Practical AI for agriculture</span>
<h1>Ask before you sell, spray, plant or panic.</h1>
<p class="lead">Tell Mkulima what is happening on your farm. We move the conversation straight to WhatsApp, where you can type naturally, mix English and Kiswahili, or send a farm photo.</p>
<div class="trust"><span>✓ 5 free questions/month</span><span>✓ Works in WhatsApp</span><span>✓ No new account to learn</span></div>
</div>
<div class="funnel">
<h3>Start with Mkulima on WhatsApp</h3>
<p>Choose what you need help with. We prepare the first message for you.</p>
<form action="/go/whatsapp" method="get" id="waStart">
<input type="hidden" name="src" value=""""+src+"""">
<div class="field"><label>What do you need help with?</label>
<select name="intent" id="intent">
<option value="sell">💰 I want to sell produce / compare an offer</option>
<option value="crop">🌿 My crop looks sick / I need crop advice</option>
<option value="weather">🌦 I need weather or timing advice</option>
<option value="buyer">🤝 I need help finding or dealing with a buyer</option>
<option value="other">💬 Something else</option>
</select></div>
<div class="quick"><button type="button" data-intent="sell">Selling</button><button type="button" data-intent="crop">Sick crop</button><button type="button" data-intent="weather">Weather</button></div>
<div class="field"><label>Crop or product <span style="font-weight:400">(optional)</span></label><input name="crop" maxlength="60" placeholder="e.g. maize, tomatoes, milk"></div>
<div class="field"><label>Your area <span style="font-weight:400">(optional)</span></label><input name="location" maxlength="100" placeholder="e.g. Eldoret, Burnt Forest, near Turbo market"></div>
<button class="wa" type="submit">💬 Continue on WhatsApp</button>
</form>
<button class="install-lite" id="installApp" type="button">⬇ Install Mkulima App</button>
<p id="installHelp" style="margin:9px 0 0;color:#66756b;font-size:12px">You can also install Mkulima on your phone for quick access.</p>
</div></div></section>
<section class="section" id="support"><div class="wrap"><div class="center"><h2>One conversation. Practical next steps.</h2><p>Built around problems farmers already ask on WhatsApp.</p></div>
<div class="cards"><div class="card"><div class="icon">💰</div><h3>Check a buyer offer</h3><p>Share quantity, price and transport cost. Mkulima helps you compare what you actually keep.</p></div>
<div class="card"><div class="icon">📷</div><h3>Send a farm photo</h3><p>Send a crop or farm photo with a short description so Mkulima can help structure what to check next.</p></div>
<div class="card"><div class="icon">📍</div><h3>Use your real location</h3><p>Village, estate, road or landmark can be included when local context matters.</p></div></div>
<div class="promise"><div><h2>Try it free. Pay only when you need more.</h2><p>Every farmer gets 5 useful questions per month. When you need more, Mkulima can send a secure Paystack upgrade link inside your WhatsApp conversation.</p></div><span class="pill">Day Pass KES 49 · Plus KES 199</span></div>
</div></section>
<footer><div class="wrap foot"><span>© 2026 Mkulima AI · Practical decision support for Kenyan farmers.</span><span class="links"><a href="/pricing">Plans</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/data-deletion">Data deletion</a></span></div></footer>
<script>
document.querySelectorAll("[data-intent]").forEach(function(b){b.addEventListener("click",function(){document.getElementById("intent").value=b.dataset.intent;});});
let mkulimaInstallPrompt=null;
const installBtn=document.getElementById("installApp"),installHelp=document.getElementById("installHelp");
window.addEventListener("beforeinstallprompt",function(e){e.preventDefault();mkulimaInstallPrompt=e;if(installHelp)installHelp.textContent="Ready to install on this phone.";});
if(installBtn){installBtn.addEventListener("click",async function(){
 if(window.matchMedia("(display-mode: standalone)").matches||window.navigator.standalone){installHelp.textContent="Mkulima AI is already installed.";return;}
 if(mkulimaInstallPrompt){mkulimaInstallPrompt.prompt();const x=await mkulimaInstallPrompt.userChoice;installHelp.textContent=x.outcome==="accepted"?"Installing Mkulima AI…":"Install cancelled.";mkulimaInstallPrompt=null;return;}
 const ua=navigator.userAgent||"",android=/Android/i.test(ua),chrome=/Chrome/i.test(ua)&&!/wv|FBAN|FBAV|Instagram/i.test(ua);
 if(android&&!chrome){window.location.href="intent://mkulima-ai-whatsapp.onrender.com/#Intent;scheme=https;package=com.android.chrome;end";return;}
 installHelp.textContent="Tap Chrome ⋮ then Install app if the install prompt does not appear.";
});}
window.addEventListener("appinstalled",function(){if(installBtn){installBtn.textContent="✓ Mkulima Installed";installBtn.disabled=true;}if(installHelp)installHelp.textContent="Installed successfully.";});
if("serviceWorker" in navigator){window.addEventListener("load",function(){navigator.serviceWorker.register("/sw.js",{scope:"/"}).catch(function(){});});}
</script>"""

def _whatsapp_display_number():
    token=os.getenv("WHATSAPP_TOKEN","").strip()
    phone_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID","").strip()
    if not (token and phone_id): return None
    req=urlrequest.Request(
        "https://graph.facebook.com/v23.0/"+urlparse.quote(phone_id)+"?fields=display_phone_number",
        headers={"Authorization":"Bearer "+token}
    )
    try:
        with urlrequest.urlopen(req,timeout=12) as res:
            data=json.loads(res.read().decode())
            return re.sub(r"\\D","",str(data.get("display_phone_number") or "")) or None
    except Exception:
        app.logger.exception("could not resolve WhatsApp display number")
        return None

@app.get("/go/whatsapp")
def go_whatsapp():
    intent=(request.args.get("intent","other") or "other").strip().lower()
    crop=" ".join((request.args.get("crop","") or "").split())[:60]
    location=" ".join((request.args.get("location","") or "").split())[:100]
    src=re.sub(r"[^a-z0-9_-]","",(request.args.get("src","direct") or "direct").lower())[:32] or "direct"
    prompts={
        "sell":"I want help selling my produce or comparing a buyer offer.",
        "crop":"I need help with a crop or farm problem.",
        "weather":"I need weather or timing advice for my farm.",
        "buyer":"I need help dealing with or finding a buyer.",
        "other":"I need help with my farm."
    }
    parts=["Hi Mkulima 🌱",prompts.get(intent,prompts["other"])]
    if crop: parts.append("Crop/product: "+crop+".")
    if location: parts.append("Area: "+location+".")
    parts.append("Please guide me on the next step.")
    number=_whatsapp_display_number()
    app.logger.info("MKULIMA_ACQUISITION source=%s intent=%s",src,intent)
    if not number:
        return """<h2>Mkulima WhatsApp is temporarily unavailable</h2><p>Please return in a moment. No payment has been taken.</p>""",503
    return redirect("https://wa.me/"+number+"?"+urlparse.urlencode({"text":" ".join(parts)}),302)

def _paystack_connection_ok():
    if not _pay_secret(): return False
    try:
        data=_paystack_json("GET","/balance")
        return bool(data and data.get("status"))
    except Exception:
        return False

_startup_readiness_logged=False

@app.before_request
def _log_startup_readiness_once():
    global _startup_readiness_logged
    if _startup_readiness_logged: return None
    _startup_readiness_logged=True
    wa_ok=bool(_whatsapp_display_number())
    pay_ok=_paystack_connection_ok()
    signing_ok=bool(_checkout_signing_secret())
    print("MKULIMA_LIVE_READINESS whatsapp=%s paystack=%s signed_checkout=%s" % (wa_ok,pay_ok,signing_ok), flush=True)
    return None

@app.get("/api/readiness")
def readiness():
    number=_whatsapp_display_number()
    return jsonify(
        ok=bool(number and _paystack_connection_ok() and _checkout_signing_secret()),
        whatsapp={"configured":bool(number)},
        payments={"configured":bool(_pay_secret()),"api_ok":_paystack_connection_ok(),"signed_checkout":bool(_checkout_signing_secret())},
        pwa={"manifest":True,"service_worker":True,"install_button":True}
    )

@app.get("/privacy")
def privacy():
    return LEGAL_STYLE + """<h1>Mkulima AI Privacy Policy</h1><small>Effective 27 September 2026</small>
    <p>Mkulima AI processes information you send to the service, such as your WhatsApp phone number, message content, crop quantity, location information you choose to provide, and buyer offers, so it can respond to your request and operate the service.</p>
    <h2>How we use information</h2><p>We use it to provide farmer decision support, maintain service reliability, prevent duplicate processing, and improve the service. We do not sell personal information.</p>
    <h2>Service providers</h2><p>Messages may be processed through WhatsApp/Meta and infrastructure providers needed to deliver the service. Their own terms and privacy practices also apply.</p>
    <h2>Market information</h2><p>Market references can become outdated and are shown with source/date context where available. They are decision-support information, not guaranteed buyer quotes.</p>
    <h2>Retention and deletion</h2><p>We keep information only as reasonably necessary for the service, security, and legal obligations. You can request deletion using the instructions on our <a href="/data-deletion">Data Deletion page</a>.</p>
    <h2>Contact</h2><p>Privacy questions: beathubonline@gmail.com</p>"""

@app.get("/terms")
def terms():
    return LEGAL_STYLE + """<h1>Mkulima AI Terms of Service</h1><small>Effective 27 September 2026</small>
    <p>By using Mkulima AI you agree to use the service lawfully and provide information you are entitled to share.</p>
    <h2>Decision support</h2><p>Mkulima AI provides informational decision support for farmers. Market prices, comparisons and calculations are not guaranteed offers, financial advice, or promises that a buyer will transact at a stated price. Verify important information before making a sale.</p>
    <h2>Availability</h2><p>The service may change, experience interruptions, or contain incomplete information. We aim to label stale reference data rather than present it as current.</p>
    <h2>Acceptable use</h2><p>Do not misuse the service, interfere with its operation, impersonate others, or submit unlawful content.</p>
    <h2>Contact</h2><p>Questions: beathubonline@gmail.com</p>"""

@app.get("/data-deletion")
def data_deletion():
    return LEGAL_STYLE + """<h1>Mkulima AI — Data Deletion Instructions</h1>
    <p>To request deletion of personal information associated with your use of Mkulima AI, email <strong>beathubonline@gmail.com</strong> with the subject <strong>Mkulima AI Data Deletion Request</strong>.</p>
    <p>Include the WhatsApp phone number used with Mkulima AI so we can identify the relevant records. Do not send passwords, access tokens, PINs, or other secrets.</p>
    <p>We will verify the request where necessary and delete or anonymize eligible records, subject to information we must retain for security, fraud prevention, or legal obligations.</p>"""

@app.get("/pricing")
def pricing():
    actor=request.args.get("actor","")
    sig=request.args.get("sig","")
    bound=_valid_actor(actor,sig)
    hidden=(f'<input type="hidden" name="actor" value="{actor}"><input type="hidden" name="sig" value="{sig}">' if bound else "")
    note="✓ Secure WhatsApp account detected. Your purchase will activate this account automatically." if bound else "To activate payment securely, return to your Mkulima WhatsApp chat and send: upgrade"
    def buy(plan,label):
        if not bound:
            return '<div class="muted"><strong>Send “upgrade” in WhatsApp to get your secure payment link.</strong></div>'
        return '<form method="post" action="/pay/start">'+hidden+'<input type="hidden" name="plan" value="'+plan+'"><input type="email" name="email" placeholder="Email for payment receipt" required><button class="b">'+label+'</button></form>'
    return """<style>body{font-family:Arial,sans-serif;background:#f4f8f0;color:#17351f;margin:0}.w{max-width:820px;margin:auto;padding:40px 20px}h1{color:#176b35}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}.c{background:white;border:1px solid #dbe8d7;border-radius:18px;padding:24px}.p{font-size:34px;font-weight:800}.b{background:#176b35;color:white;border:0;border-radius:12px;padding:13px 18px;font-weight:800;cursor:pointer;width:100%}input{width:100%;padding:12px;margin:10px 0;border:1px solid #bdcdbc;border-radius:10px;box-sizing:border-box}.muted{color:#617063}</style><div class="w"><h1>🌱 Mkulima AI Plans</h1><p>Keep practical farm help available when you need it. Free accounts get 5 useful questions each month.</p><p class="muted">"""+note+"""</p><div class="g">
    <div class="c"><h2>Day Pass</h2><div class="p">KES 49</div><p>24 hours of unlimited Mkulima conversations for an urgent farm or selling decision.</p>"""+buy("day_pass","Pay KES 49")+"""</div>
    <div class="c"><h2>Mkulima Plus</h2><div class="p">KES 199</div><p>30 days of unlimited chat, saved conversation context and premium decision support as features roll out.</p>"""+buy("plus_monthly","Pay KES 199")+"""</div>
    </div><p class="muted">Payments are verified server-side before access is activated.</p></div>"""

@app.post("/pay/start")
def pay_start():
    plan=request.form.get("plan","")
    actor=request.form.get("actor","")
    sig=request.form.get("sig","")
    email=request.form.get("email","").strip()
    if plan not in MZ_PLANS: return "Invalid plan",400
    if not _valid_actor(actor,sig): return "Open the payment link from your Mkulima WhatsApp conversation.",400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): return "Enter a valid email.",400
    if not _pay_secret(): return "Mkulima Paystack is awaiting its live server key. No charge was made.",503
    reference="mk_"+secrets.token_hex(12)
    cfg=MZ_PLANS[plan]
    if not create_payment_for_actor(actor,reference,plan,cfg["amount_kes"],email): return "Could not create payment record.",500
    try:
        data=_paystack_json("POST","/transaction/initialize",{
            "email":email,
            "amount":str(cfg["amount_kes"]*100),
            "currency":"KES",
            "reference":reference,
            "callback_url":MKULIMA_BASE_URL+"/pay/callback",
            "metadata":{"app":"mkulima_ai","plan":plan}
        })
        url=((data or {}).get("data") or {}).get("authorization_url")
        if not (data.get("status") and url): return "Paystack checkout could not start.",502
        return redirect(url,302)
    except Exception:
        app.logger.exception("paystack initialize failed")
        return "Paystack checkout could not start.",502

@app.get("/pay/callback")
def pay_callback():
    reference=request.args.get("reference","")
    valid,_=_verify_paystack_reference(reference)
    if not valid: return "<h2>Payment not verified</h2><p>No premium access was activated. Return to WhatsApp and try again.</p>",400
    p=get_payment(reference) or {}
    name=MZ_PLANS.get(p.get("plan"),{}).get("name","Mkulima premium")
    return f"<h2>✅ Payment verified</h2><p>{name} is active. Return to WhatsApp and continue chatting with Mkulima AI.</p>"

@app.post("/webhook/paystack")
def paystack_webhook():
    secret=_pay_secret()
    if not secret: return "not configured",503
    raw=request.get_data()
    expected=hmac.new(secret.encode(),raw,hashlib.sha512).hexdigest()
    supplied=request.headers.get("x-paystack-signature","")
    if not hmac.compare_digest(expected,supplied): return "invalid signature",401
    payload=request.get_json(silent=True) or {}
    if payload.get("event")=="charge.success":
        data=payload.get("data") or {}
        reference=str(data.get("reference") or "")
        if reference.startswith("mk_"):
            p=get_payment(reference)
            valid=bool(p and data.get("status")=="success" and int(data.get("amount") or 0)==int(p.get("amount_kes") or 0)*100 and data.get("currency")=="KES")
            if valid: mark_payment_paid(reference,data)
    return "ok",200

@app.get("/api/health")
def health():
    init_db()
    return jsonify(ok=True,service="Mkulima AI WhatsApp",reference_date=OBSERVED_ON,reference_stale=age_days()>FRESH_DAYS,payments={"configured":bool(_pay_secret()),"plans":{"day_pass_kes":49,"plus_30d_kes":199},"free_questions_per_month":5})

@app.route("/webhook/whatsapp",methods=["GET","POST"])
def webhook():
    if request.method=="GET":
        if request.args.get("hub.verify_token")==os.getenv("WHATSAPP_VERIFY_TOKEN"):
            return request.args.get("hub.challenge",""),200
        return "verification failed",403
    payload=request.get_json(silent=True) or {}
    try:
        value=payload["entry"][0]["changes"][0]["value"]
        if "messages" not in value: return jsonify(ok=True),200
        msg=value["messages"][0]; mid=msg["id"]; phone=msg["from"]
        if not claim_message(mid):
            return jsonify(ok=True,deduplicated=True),200
        # Supabase is preferred when securely configured; SQLite remains a rollout fallback.
        # The durable store uses a pseudonymous actor_ref rather than the raw WhatsApp number.
        state=load_state(phone)
        if msg.get("type")=="image":
            image=msg.get("image") or {}
            caption=(image.get("caption") or "").strip()
            state["has_image"]=True
            state["last_image"]={"media_id":image.get("id"),"mime_type":image.get("mime_type"),"sha256":image.get("sha256"),"caption":caption}
            if caption:
                state=apply_message(caption,state)
            else:
                state["plan"]=build_plan("",state)
                state["language"]=state.get("language","en")
                state["stage"]="open"
            save_state(phone,state)
            media=download_whatsapp_media(image.get("id"))
            vision={"ok":False,"status":"media_unavailable"}
            if media.get("ok"):
                vision=analyze_farm_image(media["bytes"],media["mime_type"],{
                    "caption":caption,"crop":state.get("crop"),"location":state.get("location"),
                    "primary_intent":state.get("primary_intent")
                })
            # Persist only structured observations/status, never raw image bytes.
            state["last_image"]["download_status"]=media.get("status")
            state["last_image"]["vision_status"]=vision.get("status")
            if vision.get("ok"):
                state["last_image"]["vision_provider"]=vision.get("provider")
                state["last_image"]["observations"]=vision.get("observations")
            save_state(phone,state)
            response=safe_vision_reply(vision,state.get("language","en")) or image_context_reply(state,caption)
            send_whatsapp_text(phone,response)
            interaction_id=record_interaction(mid,phone,caption or "[farm photo]",response,state)
            if interaction_id:
                state["last_interaction_id"]=interaction_id
                state["last_interaction_message_id"]=mid
                save_state(phone,state)
            return jsonify(ok=True,image_received=True,media_status=media.get("status"),vision_status=vision.get("status")),200
        if msg.get("type")!="text":
            send_whatsapp_text(phone,"Nimepokea message yako. Kwa sasa Astra ina-support text na farm photos; voice/video itaongezwa kwa hatua inayofuata.")
            return jsonify(ok=True,unsupported_type=msg.get("type")),200
        body=msg["text"]["body"]
        normalized_body=" ".join(body.strip().lower().split())
        if normalized_body in {"upgrade","premium","plus","subscribe","pay","pricing","plans"}:
            send_whatsapp_text(phone,"🌱 Mkulima plans:\n• Free — 5 useful questions/month\n• Day Pass — KES 49 / 24 hours\n• Mkulima Plus — KES 199 / 30 days\n\nActivate securely here: "+upgrade_url(phone))
            return jsonify(ok=True,pricing=True),200
        access=get_access(phone)
        if not access.get("active") and access.get("free_used",0)>=access.get("free_limit",5):
            send_whatsapp_text(phone,"Umetumia free questions 5 za mwezi huu. 🌱 Continue with Mkulima for KES 49/24h or KES 199/30 days: "+upgrade_url(phone))
            return jsonify(ok=True,upgrade_required=True),200
        rating=feedback_rating(body)
        if rating and state.get("last_interaction_id"):
            saved=record_feedback(mid,state.get("last_interaction_id"),rating)
            if saved:
                state["last_feedback"]=rating
                save_state(phone,state)
                ack={"helpful":"Thanks — that helps Mkulima learn what worked.","wrong":"Thanks. I have marked that answer for correction. Tell me what was wrong or what happened.","still_problem":"I understand. Tell me what is still happening and I will continue from this case."}[rating]
                send_whatsapp_text(phone,ack)
                return jsonify(ok=True,feedback=rating),200
        state=apply_message(body,state)
        weather_result=None
        if state.get("primary_intent")=="weather" and state.get("location"):
            weather_result=live_weather(state.get("location"))
            state["last_weather"]={k:v for k,v in weather_result.items() if k not in ("raw",)}
        save_state(phone,state)

        response=weather_reply(weather_result,state.get("language","en")) if weather_result and weather_result.get("ok") else reply_for(msg["text"]["body"],state)
        send_whatsapp_text(phone,response)
        consume_free_question(phone)
        interaction_id=record_interaction(mid,phone,msg["text"]["body"],response,state)
        if interaction_id:
            state["last_interaction_id"]=interaction_id
            state["last_interaction_message_id"]=mid
            save_state(phone,state)
        return jsonify(ok=True),200
    except Exception:
        app.logger.exception("webhook processing failed")
        return jsonify(ok=False,error="processing_failed"),500
