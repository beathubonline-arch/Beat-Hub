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

def clean_location(text):
    raw=" ".join((text or "").strip().split())
    t=raw.lower()
    # Strip common conversational prefixes while preserving precise place/landmark text.
    prefixes=[
        r"^(?:niko|nipo|iko|ni|location(?: yangu)? ni|eneo(?: langu)? ni|from|i am in|i'm in|near|karibu na|around)\s+",
    ]
    for p in prefixes:
        raw=re.sub(p,"",raw,flags=re.I).strip(" ,.-")
    if not raw or re.fullmatch(r"[0-9,. ]+",raw):
        return None
    # Avoid treating obvious sale sentences as locations.
    if re.search(r"\b(?:gunia|bags?|buyer|broker|bei|offer|kes|ksh|per bag)\b",raw,re.I):
        return None
    return raw[:120].title()

def parse(text):
    t=" ".join((text or "").lower().split())
    out={}
    t=t.replace("gunias","gunia")
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:bags?|gunia|sacks?)\b",t) or re.search(r"(?:bags?|gunia|sacks?)\s*(?:za\s*)?(\d+(?:\.\d+)?)",t)
    if m:
        out["bags"]=float(m.group(1))
    # Price phrases are stronger evidence than arbitrary large numbers.
    price_patterns=[
        r"(?:buyer|broker).{0,30}?(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)?\s*(?:bag|gunia)?",
        r"(?:offer|bei|anapea|amepea|ameoffer|anataka kununua)\D{0,20}(?:kes|ksh)?\s*([0-9][0-9,]{2,}(?:\.\d+)?)",
        r"(?:kes|ksh)\s*([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)\s*(?:bag|gunia)",
        r"([0-9][0-9,]{2,}(?:\.\d+)?)\s*(?:per|kwa)\s*(?:bag|gunia)"
    ]
    for p in price_patterns:
        pm=re.search(p,t)
        if pm:
            out["offer"]=float(pm.group(1).replace(",",""))
            break
    bare=re.fullmatch(r"(?:kes|ksh|sh)?\s*([0-9][0-9,]*(?:\.\d+)?)",t)
    if bare:
        out["_bare_number"]=float(bare.group(1).replace(",",""))
    return out

def apply_message(text,state):
    state=dict(state or {})
    normalized=" ".join((text or "").lower().split())
    if any(p in normalized for p in ("new sale","new deal","bei mpya","mauzo mapya","start over","anza upya","reset")):
        return {"stage":"location"}

    incoming=parse(text)
    bare=incoming.pop("_bare_number",None)
    stage=state.get("stage")

    # A requested field controls the meaning of a short reply.
    if stage=="bags" and bare is not None:
        incoming["bags"]=bare
    elif stage=="offer" and bare is not None:
        incoming["offer"]=bare
    elif stage=="location":
        loc=clean_location(text)
        if loc:
            incoming["location"]=loc

    # Explicit location wording works at any point and preserves villages,
    # estates, roads and landmarks instead of restricting farmers to a town list.
    if re.match(r"^(?:niko|nipo|from|i am in|i'm in|near|karibu na|around|location|eneo)\b",normalized):
        loc=clean_location(text)
        if loc:
            incoming["location"]=loc

    state.update(incoming)
    if "location" not in state:
        state["stage"]="location"
    elif "bags" not in state:
        state["stage"]="bags"
    elif "offer" not in state:
        state["stage"]="offer"
    else:
        state["stage"]="complete"
    return state

def reply_for(text, known=None):
    f=dict(known or {})
    missing=[x for x in ("location","bags","offer") if x not in f]
    if missing:
        q={
            "location":"Uko eneo gani? Unaweza kutaja village, estate, road au landmark iliyo karibu.",
            "bags":"Una gunia ngapi za mahindi?",
            "offer":"Buyer/broker amekupea bei gani kwa gunia moja?"
        }
        return q[missing[0]]
    gross=f["bags"]*f["offer"]
    if age_days()>FRESH_DAYS:
        return (
            f"🌽 {f['location']}: offer yako ni KES {gross:,.0f} kwa {f['bags']:g} gunia.\n"
            f"⚠️ Reference yangu iliyothibitishwa ni KES {PRICE_PER_90KG:,.0f}/90kg, tarehe {OBSERVED_ON}, kutoka {SOURCE}. "
            "Bei hii ni ya zamani, kwa hivyo sitakushauri uuze kwa reference hiyo. Nahitaji bei ya sasa kuthibitishwa kwanza."
        )
    ref=f["bags"]*PRICE_PER_90KG
    diff=ref-gross
    return (
        f"🌽 {f['location']}: Offer KES {gross:,.0f}\n"
        f"Reference KES {ref:,.0f}\n"
        f"Tofauti KES {diff:+,.0f}\n"
        f"Source: {SOURCE}, {OBSERVED_ON}. Hii si guaranteed buyer quote."
    )


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
