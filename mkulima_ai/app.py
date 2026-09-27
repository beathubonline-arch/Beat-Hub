import os, re, sqlite3, json
from datetime import date, datetime
from flask import Flask, request, jsonify
from integrations import send_whatsapp_text

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
    m=re.search(r"(?:^|\\b)(?:niko|nipo|from|i am in|i'm in|near|around|eneo(?: langu)? ni|location(?: yangu)? ni)\\s+(.+)",raw,re.I)
    if not m: return None
    loc=m.group(1)
    loc=re.split(r"\\b(?:na\\s+)?\\d+(?:\\.\\d+)?\\s*(?:bags?|gunia|sacks?)\\b|\\b(?:buyer|broker|offer|bei)\\b",loc,1,flags=re.I)[0]
    loc=loc.strip(" ,.-")
    return loc[:160].title() if loc else None

def parse(text):
    t=" ".join((text or "").lower().split())
    out={}
    m=re.search(r"(\\d+(?:\\.\\d+)?)\\s*(?:bags?|gunia|sacks?)\\b",t) or re.search(r"(?:bags?|gunia|sacks?)\\s*(?:za\\s*)?(\\d+(?:\\.\\d+)?)",t)
    if m: out["bags"]=float(m.group(1))
    for p in [
        r"(?:buyer|broker).{0,40}?(?:kes|ksh)?\\s*([0-9][0-9,]{2,}(?:\\.\\d+)?)",
        r"(?:offer|bei|anapea|amepea|ameoffer|anataka kununua)\\D{0,25}(?:kes|ksh)?\\s*([0-9][0-9,]{2,}(?:\\.\\d+)?)",
        r"(?:kes|ksh)?\\s*([0-9][0-9,]{2,}(?:\\.\\d+)?)\\s*(?:per|kwa)\\s*(?:bag|gunia)"
    ]:
        pm=re.search(p,t)
        if pm:
            out["offer"]=float(pm.group(1).replace(",","")); break
    loc=extract_location(text)
    if loc: out["location"]=loc
    bare=re.fullmatch(r"(?:kes|ksh|sh)?\\s*([0-9][0-9,]*(?:\\.\\d+)?)",t)
    if bare: out["_bare_number"]=float(bare.group(1).replace(",",""))
    return out

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
        if raw and not re.search(r"\\b(?:bags?|gunia|buyer|broker|offer|bei)\\b",raw,re.I) and not re.fullmatch(r"[0-9,. ]+",raw):
            incoming["location"]=raw[:160].title()
    state.update(incoming)
    state["language"]=detect_language(text) if text else state.get("language","sw")
    if "location" not in state: state["stage"]="location"
    elif "bags" not in state: state["stage"]="bags"
    elif "offer" not in state: state["stage"]="offer"
    else: state["stage"]="complete"
    return state

def reply_for(text, known=None):
    f=dict(known or {})
    lang=detect_language(text)
    # Preserve established language on short numeric follow-ups.
    if re.fullmatch(r"(?:kes|ksh|sh)?\\s*[0-9][0-9,.]*",(text or "").strip(),re.I):
        lang=f.get("language",lang)
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
            return (f"You have {f['bags']:g} bags in {f['location']} at KES {f['offer']:,.0f}/bag = KES {gross:,.0f}.\\n\\n"
                    "Next: don't rush the sale until we verify today's market. Get 2–3 buyer offers. Send me your transport cost and, if you can store, the storage cost and how long you can wait. I'll compare the options by the cash you actually keep.")
        if lang=="mixed":
            return (f"Uko na {f['bags']:g} bags {f['location']}, offer ni KES {f['offer']:,.0f}/bag = KES {gross:,.0f}.\\n\\n"
                    "Next step: usiuze haraka before tuverify market ya leo. Pata offers 2–3, then nitumie transport cost. Kama unaweza store, niambie storage cost na how long unaweza wait. Nitacompare option yenye net cash nzuri.")
        return (f"Una gunia {f['bags']:g} huko {f['location']}, offer ni KES {f['offer']:,.0f}/gunia = KES {gross:,.0f}.\\n\\n"
                "Hatua inayofuata: usikimbilie kuuza kabla bei ya leo kuthibitishwa. Tafuta offers 2–3, kisha nitumie gharama ya transport. Kama unaweza kuhifadhi, niambie storage cost na muda unaoweza kusubiri. Nitakulinganishia pesa halisi utakayobaki nayo.")
    if age_days()>FRESH_DAYS:
        if lang=="en":
            return (f"🌽 {f['location']}: your offer totals KES {gross:,.0f} for {f['bags']:g} bags.\\n"
                    f"⚠️ My verified reference is KES {PRICE_PER_90KG:,.0f}/90kg from {OBSERVED_ON} ({SOURCE}), but it is stale. I won't present it as today's price. Ask me 'what should I do?' for practical next steps.")
        if lang=="mixed":
            return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} for {f['bags']:g} bags.\\n"
                    f"⚠️ Verified reference ni KES {PRICE_PER_90KG:,.0f}/90kg ya {OBSERVED_ON} ({SOURCE}), but ni old. Sitaiita bei ya leo. Niulize 'nifanye aje?' for next steps.")
        return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} kwa gunia {f['bags']:g}.\\n"
                f"⚠️ Reference iliyothibitishwa ni KES {PRICE_PER_90KG:,.0f}/90kg ya {OBSERVED_ON} ({SOURCE}), lakini ni ya zamani. Sitaiita bei ya leo. Niulize 'nifanye aje?' nikupe hatua zinazofuata.")
    ref=f["bags"]*PRICE_PER_90KG
    diff=ref-gross
    return f"🌽 Offer KES {gross:,.0f} | Reference KES {ref:,.0f} | Difference KES {diff:+,.0f} | {SOURCE}, {OBSERVED_ON}."


LEGAL_STYLE = """<style>body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.6;color:#17351f}h1,h2{color:#176b35}small{color:#667}</style>"""

@app.get("/")
def home():
    return LEGAL_STYLE + """<h1>Mkulima AI</h1><p>Practical decision support for Kenyan farmers before they sell their harvest.</p><p><a href="/privacy">Privacy Policy</a> · <a href="/terms">Terms of Service</a> · <a href="/data-deletion">Data Deletion</a></p>"""

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
        if msg.get("type")!="text":
            send_whatsapp_text(phone,"Kwa sasa tuma text kuhusu mahindi yako. Voice itaongezwa baada ya text test kupita.")
            return jsonify(ok=True),200

        # Remember facts already supplied by this farmer so follow-up questions
        # ask only for information we genuinely still need.
        con=sqlite3.connect(DB)
        row=con.execute("SELECT state FROM conversations WHERE phone=?",(phone,)).fetchone()
        try:
            state=json.loads(row[0]) if row else {}
        except (TypeError,ValueError,json.JSONDecodeError):
            state={}
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
        return jsonify(ok=False,error="processing_failed"),500\n\ndef detect_language(text):
    t=" "+(" ".join((text or "").lower().split()))+" "
    sw=[" niko "," nina "," gunia "," bei "," karibu "," nifanye "," aje "," eneo "," mahindi "," amekupea "," naweza "," nataka "]
    en=[" i "," have "," bags "," buyer "," price "," near "," what "," should "," sell "," maize "," offer "," from "]
    sw_n=sum(x in t for x in sw); en_n=sum(x in t for x in en)
    if sw_n and en_n: return "mixed"
    if en_n>sw_n: return "en"
    return "sw"

def extract_location(text):
    raw=" ".join((text or "").strip().split())
    # Capture the location part even when bags/price appear in the same sentence.
    patterns=[
        r"^(?:niko|nipo)\s+(.+?)(?=\s+(?:na\s+)?\d+\s*(?:bags?|gunia|sacks?)\b|$)",
        r"^(?:i am in|i'm in|from)\s+(.+?)(?=\s+(?:with|and|na)\s+\d+\s*(?:bags?|gunia|sacks?)\b|$)",
        r"^(?:location(?: yangu)? ni|eneo(?: langu)? ni)\s+(.+?)(?=\s+(?:na\s+)?\d+\s*(?:bags?|gunia|sacks?)\b|$)"
    ]
    for p in patterns:
        m=re.search(p,raw,re.I)
        if m:
            loc=m.group(1).strip(" ,.-")
            if loc: return loc[:160].title()
    return None

def parse(text):
    t=" ".join((text or "").lower().split())
    out={}
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:bags?|gunia|sacks?)\b",t) or re.search(r"(?:bags?|gunia|sacks?)\s*(?:za\s*)?(\d+(?:\.\d+)?)",t)
    if m: out["bags"]=float(m.group(1))
    for p in [
        r"(?:buyer|broker).{0,35}?(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)?\s*(?:bag|gunia)?",
        r"(?:offer|bei|anapea|amepea|ameoffer|anataka kununua)\D{0,20}(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)",
        r"(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)\s*(?:bag|gunia)"
    ]:
        m=re.search(p,t)
        if m:
            out["offer"]=float(m.group(1).replace(",","")); break
    loc=extract_location(text)
    if loc: out["location"]=loc
    bare=re.fullmatch(r"(?:kes|ksh|sh)?\s*([0-9][0-9,]*(?:\.\d+)?)",t)
    if bare: out["_bare_number"]=float(bare.group(1).replace(",",""))
    return out

def apply_message(text,state):
    state=dict(state or {})
    normalized=" ".join((text or "").lower().split())
    if any(p in normalized for p in ("new sale","new deal","bei mpya","mauzo mapya","start over","anza upya","reset")):
        state={}
    incoming=parse(text); bare=incoming.pop("_bare_number",None)
    stage=state.get("stage")
    if stage=="bags" and bare is not None: incoming["bags"]=bare
    elif stage=="offer" and bare is not None: incoming["offer"]=bare
    elif stage=="location" and "location" not in incoming:
        # When location is the only thing requested, accept a free-form village/estate/landmark.
        raw=" ".join((text or "").strip().split())
        if raw and not re.fullmatch(r"[0-9,. ]+",raw) and not re.search(r"\b(?:gunia|bags?|buyer|broker|bei|offer|kes|ksh)\b",raw,re.I):
            incoming["location"]=raw[:160].title()
    state.update(incoming)
    state["language"]=detect_language(text) if text.strip() else state.get("language","sw")
    if "location" not in state: state["stage"]="location"
    elif "bags" not in state: state["stage"]="bags"
    elif "offer" not in state: state["stage"]="offer"
    else: state["stage"]="complete"
    return state

def reply_for(text, known=None):
    f=dict(known or {}); lang=f.get("language","sw")
    normalized=" ".join((text or "").lower().split())
    solution=any(p in normalized for p in ("nifanye aje","nifanye nini","what should i do","what do i do","ushauri","advise me","advice","help me","solution"))
    missing=[x for x in ("location","bags","offer") if x not in f]
    if missing:
        prompts={
          "sw":{"location":"Uko eneo gani? Taja village, estate, road au landmark iliyo karibu.","bags":"Una gunia ngapi za mahindi?","offer":"Buyer/broker amekupea bei gani kwa gunia moja?"},
          "en":{"location":"Where exactly are you? You can give your village, estate, road or a nearby landmark.","bags":"How many bags of maize do you have?","offer":"What price per bag has the buyer or broker offered you?"},
          "mixed":{"location":"Uko wapi exactly? Taja village, estate, road or nearby landmark.","bags":"Una bags/gunia ngapi za mahindi?","offer":"Buyer/broker amekuoffer how much per bag?"}
        }
        return prompts.get(lang,prompts["sw"])[missing[0]]
    gross=f["bags"]*f["offer"]
    if solution:
        if lang=="en":
            return (f"You have {f['bags']:g} bags in {f['location']} at KES {f['offer']:,.0f}/bag = KES {gross:,.0f} total.\n\n"
                    "Next steps:\n1. Don't rush to accept until we verify today's price.\n2. Get 2–3 buyer offers and send them here; I'll compare them.\n3. Tell me your transport cost; I'll calculate what you actually take home.\n4. If storage is possible, tell me its cost and how long you can wait; we'll compare sell-now vs wait.\n\nI won't pretend an old reference price is today's market price.")
        if lang=="mixed":
            return (f"Una {f['bags']:g} bags huko {f['location']}; offer ni KES {f['offer']:,.0f}/bag = KES {gross:,.0f} total.\n\n"
                    "Next move:\n1. Usikubali haraka before tuverify bei ya leo.\n2. Pata offers 2–3 za buyers, nitazicompare.\n3. Niambie transport cost, nikokotoe net cash unabaki nayo.\n4. Kama unaweza store, niambie storage cost na muda; tulinganishe sell now vs wait.\n\nSitaita old reference 'bei ya leo'.")
        return (f"Una gunia {f['bags']:g} huko {f['location']}; offer ni KES {f['offer']:,.0f}/gunia = KES {gross:,.0f} jumla.\n\n"
                "Hatua zinazofuata:\n1. Usikubali haraka kabla tuthibitishe bei ya leo.\n2. Pata offers 2–3 za buyers tofauti; nitazilinganisha.\n3. Niambie gharama ya transport; nitakokotoa pesa halisi utakayobaki nayo.\n4. Kama unaweza kuhifadhi, niambie storage cost na muda; tulinganishe kuuza sasa vs kusubiri.\n\nSitatumia bei ya zamani kama bei ya leo.")
    if age_days()>FRESH_DAYS:
        if lang=="en":
            return (f"🌽 {f['location']}: your offer is KES {gross:,.0f} for {f['bags']:g} bags.\n"
                    f"⚠️ My verified reference is KES {PRICE_PER_90KG:,.0f}/90kg from {SOURCE}, dated {OBSERVED_ON}. It is stale, so I won't use it as today's selling price. Ask me 'what should I do?' for practical next steps.")
        if lang=="mixed":
            return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} for {f['bags']:g} bags.\n"
                    f"⚠️ Verified reference ni KES {PRICE_PER_90KG:,.0f}/90kg, {OBSERVED_ON}, {SOURCE}. Ni old data, so sitaitumia kama bei ya leo. Uliza 'nifanye aje?' for next steps.")
        return (f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} kwa gunia {f['bags']:g}.\n"
                f"⚠️ Reference iliyothibitishwa ni KES {PRICE_PER_90KG:,.0f}/90kg, {OBSERVED_ON}, kutoka {SOURCE}. Ni ya zamani, kwa hivyo sitaitumia kama bei ya leo. Uliza 'nifanye aje?' upate hatua zinazofuata.")
    ref=f["bags"]*PRICE_PER_90KG; diff=ref-gross
    return f"🌽 Offer KES {gross:,.0f}\nReference KES {ref:,.0f}\nDifference KES {diff:+,.0f}\nSource: {SOURCE}, {OBSERVED_ON}."


LEGAL_STYLE = """<style>body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.6;color:#17351f}h1,h2{color:#176b35}small{color:#667}</style>"""

@app.get("/")
def home():
    return LEGAL_STYLE + """<h1>Mkulima AI</h1><p>Practical decision support for Kenyan farmers before they sell their harvest.</p><p><a href="/privacy">Privacy Policy</a> · <a href="/terms">Terms of Service</a> · <a href="/data-deletion">Data Deletion</a></p>"""

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
        if msg.get("type")!="text":
            send_whatsapp_text(phone,"Kwa sasa tuma text kuhusu mahindi yako. Voice itaongezwa baada ya text test kupita.")
            return jsonify(ok=True),200

        # Remember facts already supplied by this farmer so follow-up questions
        # ask only for information we genuinely still need.
        con=sqlite3.connect(DB)
        row=con.execute("SELECT state FROM conversations WHERE phone=?",(phone,)).fetchone()
        try:
            state=json.loads(row[0]) if row else {}
        except (TypeError,ValueError,json.JSONDecodeError):
            state={}
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
