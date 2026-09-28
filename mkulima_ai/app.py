import os, re, sqlite3, json
from datetime import date, datetime
from flask import Flask, request, jsonify
from integrations import send_whatsapp_text
from intent_engine import enrich_context, open_reply
from planner import build_plan, safe_reasoning_reply
from farm_vision import download_whatsapp_media, analyze_farm_image, safe_vision_reply

app=Flask(__name__)
DB=os.getenv("DB_PATH","/tmp/mkulima.db")
FRESH_DAYS=14
SOURCE="Warehouse Receipt System Council"
OBSERVED_ON="2026-08-10"
PRICE_PER_90KG=3730.50

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
    if en>sw: return "en"
    return "sw"

def extract_location(text):
    raw=" ".join((text or "").strip().split())
    # Capture location after natural location cues, stopping before quantity/offer details.
    m=re.search(r"(?:^|\b)(?:niko|nipo|from|i am in|i'm in|near|around|eneo(?: langu)? ni|location(?: yangu)? ni)\s+(.+)",raw,re.I)
    if not m: return None
    loc=m.group(1)
    loc=re.split(r"\b(?:na\s+)?\d+(?:\.\d+)?\s*(?:bags?|gunia|sacks?)\b|\b(?:buyer|broker|offer|bei)\b",loc,1,flags=re.I)[0]
    loc=loc.strip(" ,.-")
    return loc[:160].title() if loc else None

def parse(text):
    t=" ".join((text or "").lower().split())
    out={}
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:bags?|gunia|sacks?)\b",t) or re.search(r"(?:bags?|gunia|sacks?)\s*(?:za\s*)?(\d+(?:\.\d+)?)",t)
    if m: out["bags"]=float(m.group(1))
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
    state=dict(state or {})
    normalized=" ".join((text or "").lower().split())
    if any(p in normalized for p in ("new sale","new deal","bei mpya","mauzo mapya","start over","anza upya","reset")):
        state={}
    incoming=parse(text)
    bare=incoming.pop("_bare_number",None)
    stage=state.get("stage")
    if stage=="bags" and bare is not None: incoming["bags"]=bare
    elif stage=="offer" and bare is not None: incoming["offer"]=bare
    elif stage=="location" and "location" not in incoming:
        raw=" ".join((text or "").strip().split())
        if raw and not re.search(r"\b(?:bags?|gunia|buyer|broker|offer|bei)\b",raw,re.I) and not re.fullmatch(r"[0-9,. ]+",raw):
            incoming["location"]=raw[:160].title()
    state.update(incoming)
    state=enrich_case_evidence(text,state)
    state=enrich_context(text,state)
    state["plan"]=build_plan(text,state)
    state["language"]=detect_language(text) if text else state.get("language","sw")
    # Only the specialist selling flow requires location/bags/offer. Other
    # farmer intents must not be forced through the maize-sale questionnaire.
    if state.get("primary_intent")=="sell" or any(k in state for k in ("bags","offer")):
        if "location" not in state: state["stage"]="location"
        elif "bags" not in state: state["stage"]="bags"
        elif "offer" not in state: state["stage"]="offer"
        else: state["stage"]="complete"
    else:
        state["stage"]="open"
    return state

def image_context_reply(state, caption=""):
    lang=state.get("language","sw")
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
    missing=[x for x in ("location","bags","offer") if x not in f]
    questions={
      "sw":{"location":"Uko eneo gani? Unaweza kutaja village, estate, road au landmark iliyo karibu.","bags":"Una gunia ngapi za mahindi?","offer":"Buyer/broker amekupea bei gani kwa gunia moja?"},
      "en":{"location":"Where exactly are you? You can give your village, estate, road or a nearby landmark.","bags":"How many bags of maize do you have?","offer":"What price per bag has the buyer or broker offered you?"},
      "mixed":{"location":"Uko wapi exactly? Taja village, estate, road or nearby landmark.","bags":"Una bags/gunia ngapi za mahindi?","offer":"Buyer/broker amekuoffer how much per bag?"}
    }
    if missing: return questions[lang][missing[0]]
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

HOME_STYLE = """<style>
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

@app.get("/")
def home():
    return HOME_STYLE + """<nav class="wrap"><a class="brand" href="/"><span class="mark">🌱</span>Mkulima AI</a><span class="navtag">Built for Kenyan farmers</span></nav>
<section class="hero"><div class="wrap grid"><div>
<span class="eyebrow">🇰🇪 Practical AI for agriculture</span>
<h1>Better decisions before you sell your harvest.</h1>
<p class="lead">Mkulima AI helps Kenyan farmers understand offers, compare options and decide their next step using the information they already have — in language that feels natural.</p>
<div class="actions"><a class="btn primary" href="#how">See how it works</a><a class="btn secondary" href="#support">What Mkulima helps with</a></div>
</div><div class="phone">
<div class="phonehead"><div class="avatar">🌽</div><div><strong>Mkulima AI</strong><div class="online">● Farmer decision support</div></div></div>
<div class="bubble farmer">Niko Eldoret, nina gunia 25 za mahindi. Buyer amenipea KES 3,200 kwa gunia. Nifanye aje?</div>
<div class="bubble ai"><span class="tick">Mkulima AI</span><br>You have 25 bags at KES 3,200/bag = <strong>KES 80,000</strong>.<br><br>Usiuze haraka before tuverify market ya leo. Pata offers 2–3, then nitumie transport cost. Nitacompare option yenye net cash nzuri.</div>
</div></div></section>
<section class="section" id="support"><div class="wrap"><div class="center"><h2>From a farmer's question to a practical next step</h2><p>Designed around the decisions that matter when produce is ready and money is on the line.</p></div>
<div class="cards"><div class="card"><div class="icon">💰</div><h3>Understand the offer</h3><p>Turn buyer or broker offers into clear totals and comparisons before making a sale.</p></div>
<div class="card"><div class="icon">📍</div><h3>Use local context</h3><p>Farmers can share their village, estate, road or nearby landmark so advice starts with where they actually are.</p></div>
<div class="card"><div class="icon">🤝</div><h3>Choose the next move</h3><p>Compare buyer offers, transport and storage considerations to focus on the cash the farmer actually keeps.</p></div></div></div></section>
<section class="section how" id="how"><div class="wrap"><div class="center"><h2>Simple enough to use from the farm</h2><p>No complicated dashboard required. The conversation gathers only the information needed to help.</p></div>
<div class="steps"><div class="step"><div class="num">1</div><h3>Tell Mkulima what you have</h3><p>Share crop quantity, location and the offer you've received.</p></div>
<div class="step"><div class="num">2</div><h3>Mkulima evaluates the situation</h3><p>The system structures the details, checks what is known and avoids presenting stale reference data as today's price.</p></div>
<div class="step"><div class="num">3</div><h3>Get a practical next step</h3><p>Receive a clear response in English, Kiswahili or a natural mix, with the next information or action that matters.</p></div></div>
<div class="promise"><div><h2>Built to become more useful over time.</h2><p>Our direction is outcome-driven: farmer question → recommendation → farmer feedback → validated knowledge → better future decision support.</p></div><span class="pill">🌱 Learning with farmers</span></div></div></section>
<footer><div class="wrap foot"><span>© 2026 Mkulima AI · Practical decision support for Kenyan farmers.</span><span class="links"><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/data-deletion">Data deletion</a></span></div></footer>"""

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
@app.get("/api/health")
def health():
    init_db()
    return jsonify(ok=True,service="Mkulima AI WhatsApp",reference_date=OBSERVED_ON,reference_stale=age_days()>FRESH_DAYS)

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
        init_db(); con=sqlite3.connect(DB)
        try: con.execute("INSERT INTO processed_messages(message_id) VALUES(?)",(mid,)); con.commit()
        except sqlite3.IntegrityError: con.close(); return jsonify(ok=True,deduplicated=True),200
        con.close()
        # Remember facts already supplied by this farmer so follow-up questions
        # ask only for information we genuinely still need.
        con=sqlite3.connect(DB)
        row=con.execute("SELECT state FROM conversations WHERE phone=?",(phone,)).fetchone()
        try:
            state=json.loads(row[0]) if row else {}
        except (TypeError,ValueError,json.JSONDecodeError):
            state={}
        if msg.get("type")=="image":
            image=msg.get("image") or {}
            caption=(image.get("caption") or "").strip()
            state["has_image"]=True
            state["last_image"]={"media_id":image.get("id"),"mime_type":image.get("mime_type"),"sha256":image.get("sha256"),"caption":caption}
            if caption:
                state=apply_message(caption,state)
            else:
                state["plan"]=build_plan("",state)
                state["language"]=state.get("language","sw")
                state["stage"]="open"
            con.execute("INSERT INTO conversations(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state",(phone,json.dumps(state)))
            con.commit(); con.close()
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
            con=sqlite3.connect(DB)
            con.execute("INSERT INTO conversations(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state",(phone,json.dumps(state)))
            con.commit(); con.close()
            response=safe_vision_reply(vision,state.get("language","sw")) or image_context_reply(state,caption)
            send_whatsapp_text(phone,response)
            return jsonify(ok=True,image_received=True,media_status=media.get("status"),vision_status=vision.get("status")),200
        if msg.get("type")!="text":
            con.close()
            send_whatsapp_text(phone,"Nimepokea message yako. Kwa sasa Astra ina-support text na farm photos; voice/video itaongezwa kwa hatua inayofuata.")
            return jsonify(ok=True,unsupported_type=msg.get("type")),200
        state=apply_message(msg["text"]["body"],state)
        con.execute(
            "INSERT INTO conversations(phone,state) VALUES(?,?) "
            "ON CONFLICT(phone) DO UPDATE SET state=excluded.state",
            (phone,json.dumps(state))
        )
        con.commit(); con.close()

        response=reply_for(msg["text"]["body"],state)
        send_whatsapp_text(phone,response)
        return jsonify(ok=True),200
    except Exception:
        app.logger.exception("webhook processing failed")
        return jsonify(ok=False,error="processing_failed"),500
