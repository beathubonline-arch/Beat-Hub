import os, re, sqlite3
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

def parse(text):
    t=" ".join((text or "").lower().split()); out={}
    for loc in ["moiben","eldoret","kitale","turbo","kapsabet","bungoma","nakuru"]:
        if loc in t: out["location"]=loc.title(); break
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:bags?|gunia)",t) or re.search(r"(?:bags?|gunia)\s*(\d+(?:\.\d+)?)",t)
    if m: out["bags"]=float(m.group(1))
    nums=[float(x.replace(",","")) for x in re.findall(r"\b([0-9][0-9,]{2,}(?:\.\d+)?)\b",t)]
    if nums:
        candidates=[n for n in nums if n>=100 and n!=out.get("bags")]
        if candidates: out["offer"]=candidates[-1]
    return out

def reply_for(text):
    f=parse(text)
    missing=[x for x in ("location","bags","offer") if x not in f]
    if missing:
        q={"location":"Uko eneo gani? Mfano Moiben, Eldoret au Kitale.",
           "bags":"Una gunia ngapi za mahindi?",
           "offer":"Buyer amekupea bei gani kwa gunia moja?"}
        return q[missing[0]]
    gross=f["bags"]*f["offer"]
    if age_days()>FRESH_DAYS:
        return (f"🌽 Offer yako: KES {gross:,.0f} kwa {f['bags']:g} gunia.\n"
                f"⚠️ Reference yangu iliyothibitishwa ni KES {PRICE_PER_90KG:,.0f}/90kg, tarehe {OBSERVED_ON}, kutoka {SOURCE}. "
                "Bei hii ni ya zamani, kwa hivyo sitakushauri uuze kwa reference hiyo. Nahitaji bei ya sasa kuthibitishwa kwanza.")
    ref=f["bags"]*PRICE_PER_90KG
    diff=ref-gross
    return (f"🌽 Offer: KES {gross:,.0f}\nReference: KES {ref:,.0f}\nTofauti: KES {diff:+,.0f}\n"
            f"Source: {SOURCE}, {OBSERVED_ON}. Hii si guaranteed buyer quote.")

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
        response=reply_for(msg["text"]["body"])
        send_whatsapp_text(phone,response)
        return jsonify(ok=True),200
    except Exception:
        app.logger.exception("webhook processing failed")
        return jsonify(ok=False,error="processing_failed"),500
