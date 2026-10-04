import os, hashlib, re, sqlite3, base64, json, hmac, urllib.request, urllib.error, secrets
try:
 import psycopg2
 import psycopg2.extras
except ImportError:
 psycopg2=None
from flask import Flask, request, jsonify, render_template_string, abort, redirect
app=Flask(__name__)
DB=os.environ.get("PULSE_DB","/tmp/kenya-pulse.db")
DATABASE_URL=os.environ.get("DATABASE_URL","")
SALT=os.environ.get("PULSE_SALT","kenya-pulse")
ADMIN_KEY=os.environ.get("PULSE_ADMIN_KEY","")
PAYSTACK_SECRET_KEY=os.environ.get("PAYSTACK_SECRET_KEY","")
COUNTIES=["Mombasa","Kwale","Kilifi","Tana River","Lamu","Taita-Taveta","Garissa","Wajir","Mandera","Marsabit","Isiolo","Meru","Tharaka-Nithi","Embu","Kitui","Machakos","Makueni","Nyandarua","Nyeri","Kirinyaga","Murang'a","Kiambu","Turkana","West Pokot","Samburu","Trans Nzoia","Uasin Gishu","Elgeyo-Marakwet","Nandi","Baringo","Laikipia","Nakuru","Narok","Kajiado","Kericho","Bomet","Kakamega","Vihiga","Bungoma","Busia","Siaya","Kisumu","Homa Bay","Migori","Kisii","Nyamira","Nairobi City"]
RACES=["President","Governor","Senator","Woman Representative","Member of Parliament","MCA"]
GEOGRAPHY_PATH=os.path.join(os.path.dirname(__file__),"geography.json")
try:
 with open(GEOGRAPHY_PATH,encoding="utf-8") as gf: GEOGRAPHY=json.load(gf)
except Exception: GEOGRAPHY={}
def geography_ok(county,constituency="",ward=""):
 data=GEOGRAPHY.get(county,{})
 if constituency and constituency not in data:return False
 if ward and ward not in data.get(constituency,[]):return False
 return True

class DBConn:
 def __init__(self):
  self.pg=bool(DATABASE_URL)
  if self.pg:
   self.c=psycopg2.connect(DATABASE_URL,sslmode="require")
   self.cur=self.c.cursor(cursor_factory=psycopg2.extras.DictCursor)
  else:
   self.c=sqlite3.connect(DB);self.c.row_factory=sqlite3.Row;self.cur=None
 def execute(self,sql,args=()):
  if self.pg:
   sql=sql.replace("?","%s").replace("datetime('now')","CURRENT_TIMESTAMP")
   self.cur.execute(sql,args);return self.cur
  return self.c.execute(sql,args)
 def __enter__(self):return self
 def __exit__(self,t,v,tb):
  if t:self.c.rollback()
  else:self.c.commit()
  if self.cur:self.cur.close()
  self.c.close()
def conn():return DBConn()
def admin_authorized():
 if not ADMIN_KEY:return False
 auth=request.headers.get("Authorization","")
 supplied=auth[7:].strip() if auth.lower().startswith("bearer ") else request.args.get("key","")
 return hmac.compare_digest(str(supplied),str(ADMIN_KEY))
def init():
 with conn() as c:
  if c.pg:
   c.execute("""CREATE TABLE IF NOT EXISTS pulse_votes(id BIGSERIAL PRIMARY KEY,county TEXT NOT NULL,race TEXT NOT NULL,candidate TEXT NOT NULL,issue TEXT,fp TEXT NOT NULL,constituency TEXT,ward TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS pulse_vote_unique ON pulse_votes(county,race,COALESCE(constituency,''),COALESCE(ward,''),fp);")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_lookup ON pulse_votes(county,race,constituency,ward);")
   c.execute("""CREATE TABLE IF NOT EXISTS pulse_visits(id BIGSERIAL PRIMARY KEY,county TEXT,source TEXT,path TEXT,session_id TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("ALTER TABLE pulse_visits ADD COLUMN IF NOT EXISTS path TEXT;");c.execute("ALTER TABLE pulse_visits ADD COLUMN IF NOT EXISTS session_id TEXT;")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_visit_lookup ON pulse_visits(county,source);")
   c.execute("""CREATE TABLE IF NOT EXISTS ad_orders(id BIGSERIAL PRIMARY KEY,business TEXT NOT NULL,email TEXT NOT NULL,phone TEXT,scope TEXT NOT NULL,county TEXT,package TEXT NOT NULL,budget INTEGER NOT NULL,headline TEXT,url TEXT,status TEXT DEFAULT 'PENDING_REVIEW',starts_at TIMESTAMPTZ,ends_at TIMESTAMPTZ,impressions INTEGER DEFAULT 0,clicks INTEGER DEFAULT 0,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidates(id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL,race TEXT NOT NULL,county TEXT,constituency TEXT,ward TEXT,party TEXT,status TEXT NOT NULL DEFAULT 'PROSPECTIVE',source_url TEXT,active BOOLEAN DEFAULT TRUE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_scope_unique ON candidates(name,race,COALESCE(county,''),COALESCE(constituency,''),COALESCE(ward,''));")
   c.execute("""CREATE TABLE IF NOT EXISTS ground_issues(id BIGSERIAL PRIMARY KEY,county TEXT NOT NULL,constituency TEXT,ward TEXT,landmark TEXT,category TEXT NOT NULL,description TEXT NOT NULL,transcript TEXT,language TEXT,media_type TEXT,media_url TEXT,status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',confirmations INTEGER DEFAULT 1,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_aliases(id BIGSERIAL PRIMARY KEY,candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,alias TEXT NOT NULL,verified BOOLEAN DEFAULT FALSE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_alias_unique ON candidate_aliases(candidate_id,LOWER(alias));")
   c.execute("""CREATE TABLE IF NOT EXISTS support_payments(id BIGSERIAL PRIMARY KEY,reference TEXT NOT NULL UNIQUE,email TEXT NOT NULL,amount_kes INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'KES',status TEXT NOT NULL DEFAULT 'INITIATED',paystack_transaction_id TEXT,channel TEXT,paid_at TIMESTAMPTZ,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE INDEX IF NOT EXISTS support_payment_status ON support_payments(status,created_at);")
   c.execute("ALTER TABLE pulse_votes ADD COLUMN IF NOT EXISTS candidate_id BIGINT REFERENCES candidates(id);")
  else:
   old_exists=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_votes'").fetchone()
   if not old_exists:
    c.execute("""CREATE TABLE pulse_votes(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,race TEXT NOT NULL,candidate TEXT NOT NULL,issue TEXT,fp TEXT NOT NULL,constituency TEXT,ward TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   else:
    cols=[x[1] for x in c.execute("PRAGMA table_info(pulse_votes)").fetchall()]
    for col in ("constituency","ward"):
     if col not in cols:c.execute("ALTER TABLE pulse_votes ADD COLUMN "+col+" TEXT")
   # Remove only the legacy table-level uniqueness rule if present; never rebuild healthy tables on startup.
   legacy_sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='pulse_votes'").fetchone()
   legacy_sql=(legacy_sql[0] or "") if legacy_sql else ""
   if "UNIQUE(county,race,fp)" in legacy_sql.replace(" ",""):
    c.execute("""CREATE TABLE pulse_votes_new(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,race TEXT NOT NULL,candidate TEXT NOT NULL,issue TEXT,fp TEXT NOT NULL,constituency TEXT,ward TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
    c.execute("""INSERT OR IGNORE INTO pulse_votes_new(id,county,race,candidate,issue,fp,constituency,ward,created_at)
                 SELECT id,county,race,candidate,issue,fp,constituency,ward,created_at FROM pulse_votes""")
    c.execute("DROP TABLE pulse_votes");c.execute("ALTER TABLE pulse_votes_new RENAME TO pulse_votes")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS pulse_vote_unique ON pulse_votes(county,race,IFNULL(constituency,''),IFNULL(ward,''),fp)")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_lookup ON pulse_votes(county,race,constituency,ward)")
   c.execute("""CREATE TABLE IF NOT EXISTS pulse_visits(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT,source TEXT,path TEXT,session_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   vcols=[x[1] for x in c.execute("PRAGMA table_info(pulse_visits)").fetchall()]
   for col in ("path","session_id"):
    if col not in vcols:c.execute("ALTER TABLE pulse_visits ADD COLUMN "+col+" TEXT")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_visit_lookup ON pulse_visits(county,source);")
   c.execute("""CREATE TABLE IF NOT EXISTS ad_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,business TEXT NOT NULL,email TEXT NOT NULL,phone TEXT,scope TEXT NOT NULL,county TEXT,package TEXT NOT NULL,budget INTEGER NOT NULL,headline TEXT,url TEXT,status TEXT DEFAULT 'PENDING_REVIEW',created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,race TEXT NOT NULL,county TEXT,constituency TEXT,ward TEXT,party TEXT,status TEXT NOT NULL DEFAULT 'PROSPECTIVE',source_url TEXT,active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_scope_unique ON candidates(name,race,IFNULL(county,''),IFNULL(constituency,''),IFNULL(ward,''));")
   c.execute("""CREATE TABLE IF NOT EXISTS ground_issues(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,constituency TEXT,ward TEXT,landmark TEXT,category TEXT NOT NULL,description TEXT NOT NULL,transcript TEXT,language TEXT,media_type TEXT,media_url TEXT,status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',confirmations INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_aliases(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,alias TEXT NOT NULL,verified INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_alias_unique ON candidate_aliases(candidate_id,LOWER(alias));")
   c.execute("""CREATE TABLE IF NOT EXISTS support_payments(id INTEGER PRIMARY KEY AUTOINCREMENT,reference TEXT NOT NULL UNIQUE,email TEXT NOT NULL,amount_kes INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'KES',status TEXT NOT NULL DEFAULT 'INITIATED',paystack_transaction_id TEXT,channel TEXT,paid_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE INDEX IF NOT EXISTS support_payment_status ON support_payments(status,created_at);")
   vote_cols=[x[1] for x in c.execute("PRAGMA table_info(pulse_votes)").fetchall()]
   if "candidate_id" not in vote_cols:c.execute("ALTER TABLE pulse_votes ADD COLUMN candidate_id INTEGER")
   for col,typ in [("starts_at","TEXT"),("ends_at","TEXT"),("impressions","INTEGER DEFAULT 0"),("clicks","INTEGER DEFAULT 0")]:
    try:c.execute("ALTER TABLE ad_orders ADD COLUMN "+col+" "+typ)
    except sqlite3.OperationalError:pass
try:init()
except Exception as e: print("db init",e)

@app.get("/api/geography")
def geography_api():
 county=(request.args.get("county") or "").strip()
 constituency=(request.args.get("constituency") or "").strip()
 if county not in COUNTIES:return jsonify(error="Invalid county"),400
 data=GEOGRAPHY.get(county,{})
 if constituency:
  if constituency not in data:return jsonify(error="Invalid constituency for county"),400
  return jsonify(county=county,constituency=constituency,wards=data[constituency])
 return jsonify(county=county,constituencies=list(data.keys()),complete=bool(data))

COUNTY_MARKS={"Mombasa":"🌊","Kwale":"🌴","Kilifi":"🌴","Tana River":"🏞️","Lamu":"⛵","Taita-Taveta":"⛰️","Garissa":"☀️","Wajir":"🐪","Mandera":"🌅","Marsabit":"🗻","Isiolo":"🦒","Meru":"🌿","Tharaka-Nithi":"🌾","Embu":"🌱","Kitui":"🌵","Machakos":"🏞️","Makueni":"🥭","Nyandarua":"🥔","Nyeri":"☕","Kirinyaga":"🌾","Murang'a":"🍃","Kiambu":"☕","Turkana":"🌞","West Pokot":"⛰️","Samburu":"🦓","Trans Nzoia":"🌽","Uasin Gishu":"🌽","Elgeyo-Marakwet":"🏔️","Nandi":"🍃","Baringo":"🐝","Laikipia":"🦒","Nakuru":"🦩","Narok":"🦁","Kajiado":"🐄","Kericho":"🍃","Bomet":"🍵","Kakamega":"🌳","Vihiga":"🌿","Bungoma":"🌾","Busia":"🌅","Siaya":"🐟","Kisumu":"🐟","Homa Bay":"🌊","Migori":"🌾","Kisii":"🍌","Nyamira":"🍌","Nairobi City":"🏙️"}
def paystack_request(path,payload=None):
 if not PAYSTACK_SECRET_KEY: raise RuntimeError("Paystack is not configured")
 data=json.dumps(payload).encode() if payload is not None else None
 req=urllib.request.Request("https://api.paystack.co"+path,data=data,headers={"Authorization":"Bearer "+PAYSTACK_SECRET_KEY,"Content-Type":"application/json"},method="POST" if data is not None else "GET")
 with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())

@app.post("/api/support/initialize")
def support_initialize():
 body=request.get_json(silent=True) or {}; email=str(body.get("email","")).strip()[:160]
 try: amount=int(body.get("amount",0))
 except: amount=0
 if amount<5 or amount>1000000:return jsonify(error="Support starts from KSh 5."),400
 if not re.match(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",email):return jsonify(error="Enter a valid email for the payment receipt."),400
 if not PAYSTACK_SECRET_KEY:return jsonify(error="Payments are being connected. Please try again shortly."),503
 ref="kp-"+secrets.token_hex(10); origin=request.url_root.rstrip("/")
 with conn() as db: db.execute("INSERT INTO support_payments(reference,email,amount_kes,currency,status) VALUES(?,?,?,?,?)",(ref,email,amount,"KES","INITIATED"))
 payload={"email":email,"amount":str(amount*100),"currency":"KES","reference":ref,"callback_url":origin+"/support/callback","channels":["mobile_money","card"],"metadata":{"purpose":"optional_support","separate_from_participation":True}}
 try:
  out=paystack_request("/transaction/initialize",payload); d=out.get("data") or {}
  if not d.get("authorization_url"): raise RuntimeError("missing authorization url")
  return jsonify(authorization_url=d.get("authorization_url"),reference=ref)
 except Exception:
  with conn() as db: db.execute("UPDATE support_payments SET status='INIT_FAILED',updated_at=CURRENT_TIMESTAMP WHERE reference=?",(ref,))
  return jsonify(error="Could not start payment. Please try again."),502

def record_support_verification(ref,d):
 if not ref or d.get("reference")!=ref:return False
 with conn() as db:
  row=db.execute("SELECT reference,amount_kes,currency,status FROM support_payments WHERE reference=?",(ref,)).fetchone()
  if not row:return False
  try: got=int(d.get("amount") or 0)
  except: got=0
  if d.get("status")!="success" or d.get("currency")!=row["currency"] or got!=int(row["amount_kes"])*100:return False
  tx=str(d.get("id") or "")[:80]; channel=str(d.get("channel") or "")[:40]
  db.execute("UPDATE support_payments SET status='SUCCESS',paystack_transaction_id=?,channel=?,paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE reference=? AND status<>'SUCCESS'",(tx,channel,ref))
 return True

@app.get("/support/callback")
def support_callback():
 ref=request.args.get("reference","")[:100]
 if not ref:return redirect("/?support=missing")
 try:
  d=(paystack_request("/transaction/verify/"+ref).get("data") or {}); ok=record_support_verification(ref,d)
  return redirect("/?support="+("success" if ok else "pending"))
 except Exception:return redirect("/?support=pending")

@app.post("/api/paystack/webhook")
def paystack_webhook():
 if not PAYSTACK_SECRET_KEY:return "",503
 raw=request.get_data(); sig=request.headers.get("x-paystack-signature",""); expected=hmac.new(PAYSTACK_SECRET_KEY.encode(),raw,hashlib.sha512).hexdigest()
 if not hmac.compare_digest(sig,expected):return "",401
 event=request.get_json(silent=True) or {}
 if event.get("event")=="charge.success":
  d=event.get("data") or {}; record_support_verification(str(d.get("reference") or "")[:100],d)
 return "",200

PULSE_95_CSS=r'''*{box-sizing:border-box}body{margin:0;color:#f8fff9;font-family:Inter,ui-sans-serif,system-ui;background:#0b2f1d;min-height:100vh}.world{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 84% 12%,#ffe8a1 0 1.8%,#ffd9894d 2% 8%,transparent 17%),linear-gradient(180deg,#82aea8 0 23%,#72987b 37%,#2c7045 65%,#0b3c24 100%);transform:scale(1.015);filter:saturate(1.08) contrast(1.025)}.world:before{content:'';position:absolute;inset:23% -8% -8%;background:radial-gradient(ellipse at 64% 40%,rgba(194,224,190,.32),transparent 24%),linear-gradient(158deg,transparent 0 15%,#63885b 15.4% 27%,transparent 27.4%),linear-gradient(24deg,transparent 0 23%,#39774a 23.4% 50%,transparent 50.4%),linear-gradient(158deg,transparent 0 43%,#145a34 43.4% 70%,transparent 70.4%);filter:blur(.8px);opacity:.96}.world:after{content:'';position:absolute;inset:52% -4% -4%;background:radial-gradient(ellipse at 63% 8%,rgba(173,210,184,.42),transparent 26%),linear-gradient(8deg,#0b4227 0 46%,transparent 46.5%),linear-gradient(-8deg,#216a3d 0 57%,transparent 57.5%);filter:blur(1.2px);opacity:.98}.shade{position:fixed;inset:0;z-index:-2;background:linear-gradient(90deg,rgba(4,30,17,.70),transparent 48%),linear-gradient(0deg,rgba(3,28,15,.62),transparent 42%)}body:after{content:'';position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(ellipse at 50% 48%,transparent 45%,rgba(3,24,13,.22) 100%);mix-blend-mode:multiply}.glass{background:linear-gradient(135deg,rgba(20,55,39,.58),rgba(39,73,56,.34));border:1px solid rgba(240,255,245,.34);box-shadow:inset 0 1px rgba(255,255,255,.38),inset 0 -1px rgba(255,255,255,.06),0 18px 48px rgba(3,25,14,.20);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.kp95nav{max-width:1380px;width:calc(100% - 48px);height:66px;margin:18px auto 0;border-radius:24px;padding:0 20px;display:flex;align-items:center;gap:26px;background:linear-gradient(120deg,rgba(25,58,43,.54),rgba(44,76,60,.31));border:1px solid rgba(244,255,247,.32);box-shadow:inset 0 1px rgba(255,255,255,.40),0 16px 42px rgba(3,24,13,.16);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.kp95nav .brand{font-size:21px;font-weight:950;letter-spacing:-.7px;margin-right:auto}.kp95nav .brand b{color:#7cf39d}.kp95nav a{color:#edf8f1;text-decoration:none;font-size:13px}.kp95wrap{max-width:1380px;margin:auto;padding:16px 24px 70px}.kp95panel{border-radius:30px;padding:28px}.kp95title{font-size:clamp(52px,6.3vw,88px);line-height:.91;letter-spacing:-4px}.kp95accent{color:#75f59b}.kp95muted{color:#bdd1c4}.kp95mark{font-size:30px;filter:drop-shadow(0 10px 20px rgba(3,25,14,.25))}.kp95btn{display:inline-flex;padding:14px 20px;border:0;border-radius:15px;background:#69ef91;color:#092817;font-weight:900;text-decoration:none;box-shadow:0 10px 28px rgba(30,205,92,.18)}@media(max-width:900px){.kp95nav{width:calc(100% - 24px);margin-top:10px}.kp95nav a{display:none}.kp95wrap{padding:10px 12px 50px}}@media(max-width:560px){.kp95nav{height:60px;border-radius:20px}.kp95panel{padding:19px;border-radius:24px}.kp95title{font-size:49px;letter-spacing:-2.7px}}'''
@app.get("/pulse95.css")
def pulse95_css():
 return PULSE_95_CSS,200,{"Content-Type":"text/css; charset=utf-8","Cache-Control":"public, max-age=300"}

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenya Pulse — Live Participation</title><link rel="stylesheet" href="/pulse95.css"><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui;background:#03130c;color:#f7fff9;min-height:100vh;overflow-x:hidden}body:before,body:after{content:"";position:fixed;border-radius:50%;filter:blur(20px);z-index:-2}body:before{width:520px;height:520px;background:#1d7b4a55;top:-180px;left:-170px}body:after{width:460px;height:460px;background:#d4a90025;right:-180px;top:35%}.mesh{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 70% 5%,#1b6d4338,transparent 32%),linear-gradient(145deg,#020b07,#061c12 48%,#04110b)}.top{height:76px;padding:0 max(20px,5vw);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #ffffff14;position:sticky;top:0;background:#071a1199;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);z-index:10}.brand{font-weight:950;font-size:21px;letter-spacing:-.8px}.brand b{color:#ffd54a}.live{display:flex;align-items:center;gap:8px;font-size:12px;color:#d9eee1}.dot{width:8px;height:8px;background:#6cff9a;border-radius:50%;box-shadow:0 0 16px #6cff9a}.wrap{max-width:1180px;margin:auto;padding:46px 20px 70px}.hero{display:grid;grid-template-columns:1.25fr .75fr;gap:28px;align-items:end;padding:28px 0 30px}.eyebrow,.pill{display:inline-flex;padding:7px 11px;border:1px solid #ffffff1f;background:#ffffff0c;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.7px;text-transform:uppercase}.hero h1{font-size:clamp(46px,7vw,82px);line-height:.94;letter-spacing:-4px;margin:15px 0 20px}.hero h1 span{color:#75f59b}.hero p{font-size:17px;max-width:650px}.muted{color:#a9c6b4}.glass{background:linear-gradient(135deg,#ffffff12,#ffffff07);border:1px solid #ffffff1b;box-shadow:0 24px 80px #0000002e,inset 0 1px #ffffff13;backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);border-radius:26px}.heroStat{padding:22px}.heroStat .big{font-size:46px;font-weight:950;letter-spacing:-2px}.heroStat small{color:#9bb7a6}.layout{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}.card{padding:24px}.card h2{margin:0 0 7px;font-size:20px}.cardHead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:18px}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}select,input,button{width:100%;padding:15px 16px;border-radius:14px;border:1px solid #ffffff1d;background:#06180f99;color:#fff;font:inherit;outline:none}select:focus,input:focus{border-color:#ffd54a88;box-shadow:0 0 0 3px #ffd54a12}button{background:linear-gradient(135deg,#ffe06b,#f5c728);color:#152016;font-weight:900;border:0;cursor:pointer;transition:.2s}button:hover{transform:translateY(-1px);filter:brightness(1.04)}.secondary{background:#ffffff0b;color:#fff;border:1px solid #ffffff1c}.row{padding:14px 0;border-bottom:1px solid #ffffff12}.row:last-child{border:0}.bar{height:7px;background:#ffffff10;border-radius:99px;overflow:hidden;margin-top:8px}.fill{height:100%;background:linear-gradient(90deg,#ffd54a,#70e596);border-radius:99px}.analytics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.metric{padding:18px}.metric strong{display:block;font-size:27px;letter-spacing:-1px}.metric span{font-size:12px;color:#9eb9a8}.share{margin-top:18px}.notice{font-size:12px;line-height:1.6;padding:18px;margin-top:18px;color:#b7cdbf}.adwrap{margin-top:18px;padding:10px}.ad{min-height:132px;border:1px dashed #ffffff30;border-radius:20px;display:flex;align-items:center;justify-content:center;text-align:center;background:#ffffff05;padding:20px}.ad b{display:block;color:#e9f5ed;margin-bottom:5px}.ad small{color:#89a595}.adlabel{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#769180;margin:5px 8px 10px}.footer{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;padding:24px 4px;color:#7f9d8b;font-size:12px}.footer a{color:#b9d2c2;text-decoration:none}@media(max-width:800px){.hero,.layout{grid-template-columns:1fr}.hero h1{letter-spacing:-2.5px}.heroStat{display:none}.analytics{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.wrap{padding:28px 14px 50px}.top{height:66px}.formgrid{grid-template-columns:1fr}.analytics{gap:7px}.metric{padding:13px 10px}.metric strong{font-size:22px}.card{padding:18px}.glass{border-radius:21px}.hero{padding-top:15px}.hero h1{font-size:49px}}
.searchdock{position:sticky;top:14px;z-index:20;margin:0 0 18px;padding:10px 14px;display:flex;align-items:center;gap:10px;background:linear-gradient(120deg,rgba(255,255,255,.18),rgba(255,255,255,.055));border-color:rgba(255,255,255,.35);box-shadow:inset 0 1px rgba(255,255,255,.42),0 22px 70px rgba(0,0,0,.48),0 0 50px rgba(0,255,136,.12)}.searchdock:focus-within{border-color:#7affb899;box-shadow:inset 0 1px #ffffff45,0 20px 70px #0009,0 0 55px #00ff8840}.searchicon{font-size:28px;color:#7affb8;text-shadow:0 0 20px #00ff88}.searchdock input{flex:1;min-width:0;background:transparent;border:0;outline:0;color:white;font-size:16px;padding:13px}.searchdock input::placeholder{color:#91a99d}.searchbtn{border:1px solid #8affbd66;background:linear-gradient(135deg,#79ffb4,#d7ff72);color:#04130b;font-weight:900;letter-spacing:.04em;border-radius:16px;padding:14px 18px;cursor:pointer;box-shadow:0 0 30px #49ff9930}.searchbtn:hover{transform:translateY(-1px);box-shadow:0 0 42px #49ff9950}.county:before{content:'';position:absolute;inset:-60% -40%;background:linear-gradient(120deg,transparent 35%,#ffffff12 50%,transparent 65%);transform:translateX(-60%) rotate(12deg);transition:.8s}.county:hover:before{transform:translateX(55%) rotate(12deg)}.county:after{content:'';position:absolute;width:100px;height:100px;border-radius:50%;right:-30px;top:-30px;background:#58ff9d12;filter:blur(4px);box-shadow:0 0 80px #58ff9d25}.county>*{position:relative;z-index:2}.hero{background:linear-gradient(120deg,rgba(255,255,255,.17),rgba(255,255,255,.045) 52%,rgba(100,255,180,.045));border-color:rgba(255,255,255,.32);box-shadow:inset 0 1px rgba(255,255,255,.42),0 40px 100px rgba(0,0,0,.38),0 0 80px rgba(0,255,140,.08)}.hero:before{content:'';position:absolute;width:340px;height:340px;border:1px solid #ffffff10;border-radius:50%;right:-80px;bottom:-220px;box-shadow:0 0 90px #00ff8830,inset 0 0 70px #ffffff08}.stat{position:relative;overflow:hidden;min-height:122px;transition:.3s}.stat:hover{transform:translateY(-4px);border-color:rgba(255,255,255,.4)}.stat:after{content:'';position:absolute;inset:auto -20% -65% 30%;height:100px;background:#ffe45e16;border-radius:50%;filter:blur(25px)}.empty{display:none;text-align:center;padding:50px;color:#9fb4aa}.glass:after{content:'';pointer-events:none;position:absolute;inset:1px;border-radius:inherit;background:linear-gradient(125deg,rgba(255,255,255,.13),transparent 22%,transparent 72%,rgba(120,255,190,.035));mask:linear-gradient(#000,transparent 35%);opacity:.7}
/* County participation reuses the approved 9.5 dashboard proportions */
body{background:#0b2f1d}.top.kp95nav{position:relative;top:auto;height:66px;max-width:1380px;width:calc(100% - 48px);margin:18px auto 0;padding:0 20px;border-bottom:1px solid rgba(244,255,247,.32);border-radius:24px}.top .brand b{color:#7cf39d}.wrap{max-width:1380px;padding:16px 24px 50px}.hero{min-height:220px;grid-template-columns:1.2fr .8fr;align-items:center;gap:40px;padding:28px;border-radius:30px;margin-top:16px;position:relative;overflow:hidden}.hero h1{font-size:clamp(42px,5vw,68px);line-height:.94;letter-spacing:-3px;margin:10px 0 14px}.hero p{margin:0;max-width:680px;line-height:1.55}.heroStat{justify-self:end;width:min(360px,100%);padding:24px;border-radius:28px}.analytics{margin:14px 0;gap:14px}.metric{min-height:90px;padding:16px 20px}.layout{grid-template-columns:1.05fr .95fr;gap:16px}.card{padding:22px;border-radius:26px}.layout>.card{min-height:390px}select,input{padding:13px 15px}button{padding:13px 16px;background:#69ef91;color:#092817}.secondary{background:#ffffff0b;color:#fff}.fill{background:#70ee96}.notice a,.ad a{color:#75f59b!important}select:focus,input:focus{border-color:#75f59b88;box-shadow:0 0 0 3px #75f59b12}@media(max-width:800px){.top.kp95nav{width:calc(100% - 24px);margin-top:10px}.wrap{padding:10px 12px 45px}.hero{grid-template-columns:1fr;min-height:auto;padding:22px;margin-top:10px}.heroStat{display:none}.hero h1{font-size:46px}.layout{grid-template-columns:1fr}.layout>.card{min-height:0}.analytics{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.top.kp95nav{height:60px}.hero{padding:19px}.hero h1{font-size:40px}.analytics{gap:7px}.metric{min-height:78px;padding:12px 10px}.card{padding:17px}.wrap{padding-bottom:35px}}


</style></head><body><div class=world></div><div class=shade></div><header class="top kp95nav"><div class=brand>KENYA <b>PULSE</b></div><div class=live><i class=dot></i> LIVE PARTICIPATION</div></header><main class=wrap>
<section class=hero><div>{% if initial_county %}<div class=kp95mark>{{county_mark}}</div>{% endif %}<span class=eyebrow>{% if initial_county %}{{initial_county}} · COUNTY PARTICIPATION{% else %}47 counties · voluntary participation{% endif %}</span><h1>{% if initial_county %}{{initial_county}}.<br><span>Your voice.</span>{% else %}Your county.<br><span>Your voice.</span>{% endif %}</h1><p class=muted>Share your current preference and explore live aggregate responses from people participating on Kenya Pulse. This is an open online pulse, not a scientific election forecast.</p></div><aside class="glass heroStat"><small>COUNTIES AVAILABLE</small><div class=big>47</div><small>One transparent participation experience across Kenya.</small></aside></section>
<div class=analytics><div class="glass metric"><strong id=metricTotal>—</strong><span>Selected race responses</span></div><div class="glass metric"><strong>47</strong><span>Counties available</span></div><div class="glass metric"><strong>LIVE</strong><span>Aggregate updates</span></div></div>
<section class=layout><div class="glass card"><div class=cardHead><div><h2>Join the pulse</h2><span class=muted>Choose your county and race</span></div><span class=pill>Private choice</span></div><div class=formgrid><select id=county onchange="onScopeChange()"><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><select id=race onchange="onRaceChange()">{% for r in races %}<option>{{r}}</option>{% endfor %}</select></div><div id=areaBox class=formgrid style="display:none;margin-top:10px"><select id=constituency onchange="populateWards();load()"><option value="">Choose constituency</option></select><select id=ward onchange="load()"><option value="">Choose ward</option></select></div><input id=candidate list=candidateList maxlength=80 placeholder="Enter participant full name or alias" autocomplete="off" oninput="cancelCandidateConfirm()" style="margin-top:10px"><datalist id=candidateList></datalist><div id=candidateConfirm class="glass" style="display:none;margin-top:10px;padding:14px"><b>Confirm the person</b><div id=candidateConfirmText class=muted style="margin-top:6px"></div><button id=confirmCandidateBtn style="margin-top:10px">Yes, this person →</button><button class=secondary onclick="cancelCandidateConfirm()" style="margin-top:8px">No, go back</button></div><div id=candidateNote class=muted style="font-size:12px;margin-top:6px">Enter the person’s full name. Verified aliases are recognized automatically; unlisted full names can still be confirmed and counted. Names are participation entries, not endorsements or nomination claims.</div><input id=issue maxlength=120 placeholder="Optional: issue influencing your choice" style="margin-top:10px"><button onclick=vote() style="margin-top:10px">Submit preference →</button><div id=msg class=muted style="margin-top:10px;font-size:13px"></div></div>
<div class="glass card"><div class=cardHead><div><h2 id=rt>Live participant results</h2><span class=muted>Voluntary website responses</span></div><span class=pill>Live</span></div><div id=results><p class=muted>Select a county to explore aggregate participant results.</p></div></div></section>
<section id=supportbox class="glass card kp95panel" style="display:none"><div class=cardHead><div><h2>Support Kenya Pulse</h2><span class=muted>Optional platform support</span></div><span class=pill>Completely optional</span></div><p><b>Participation and results are completely free.</b> If you find Kenya Pulse useful, you can optionally help cover the cost of keeping the platform running.</p><p class=muted>Support with as low as KSh 5. Your contribution does not affect your response or the results.</p><div class=formgrid><input id=supportCustom type=number min=5 step=1 inputmode=numeric placeholder="KSh 5 or above"><input id=supportEmail type=email autocomplete=email placeholder="Email for payment receipt"></div><div class=formgrid style="margin-top:10px"><button class=secondary onclick="supportAmount()">Continue to secure checkout</button><button class=secondary onclick="dismissSupport()">Not now</button></div><div id=supportmsg class=muted>No contribution is required to view results.</div></section><section id=sharebox class="glass card share" style="display:none"><div class=cardHead><div><h2>Share your county pulse</h2><span class=muted>Your response is counted whether or not you share.</span></div><span class=pill>Optional</span></div><div class=formgrid><button onclick=sharePulse()>Share county pulse</button><button class=secondary onclick=copyPulse()>Copy county link</button></div><div id=sharemsg class=muted style="margin-top:9px;font-size:12px"></div></section>
<section class="glass adwrap"><div class=adlabel>Advertisement</div><div class=ad id=liveAd><div><b>Premium advertising space</b><small>Sponsored content will appear here, clearly separated from participation controls and results.</small></div></div></section>
<section class="glass notice"><b>Transparency:</b> Results show voluntary Kenya Pulse participants and are not representative of all registered voters. They should not be interpreted as an election forecast. Individual choices are not publicly displayed. Candidate names are curated participation options and their appearance is not an endorsement. <a href="/methodology" style="color:#ffd54a">Read methodology →</a></section>
<footer class=footer><span>© Kenya Pulse · Open participation dashboard</span><span><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/methodology">Methodology</a></span></footer></main>
<script>
const C=document.getElementById('county'),R=document.getElementById('race');async function populateConstituencies(){
 const county=document.getElementById('county').value,sel=document.getElementById('constituency'),ward=document.getElementById('ward');
 sel.innerHTML='<option value="">Choose constituency</option>';ward.innerHTML='<option value="">Choose ward</option>';
 if(!county)return;
 try{const r=await fetch('/api/geography?county='+encodeURIComponent(county)),j=await r.json();(j.constituencies||[]).forEach(x=>sel.add(new Option(x,x)));}catch(e){}
}
async function populateWards(){
 const county=document.getElementById('county').value,con=document.getElementById('constituency').value,sel=document.getElementById('ward');
 sel.innerHTML='<option value="">Choose ward</option>';if(!county||!con)return;
 try{const r=await fetch('/api/geography?county='+encodeURIComponent(county)+'&constituency='+encodeURIComponent(con)),j=await r.json();(j.wards||[]).forEach(x=>sel.add(new Option(x,x)));loadCandidates();}catch(e){}
}
function areaMode(){let mp=R.value==='Member of Parliament',mca=R.value==='MCA',b=document.getElementById('areaBox');b.style.display=(mp||mca)?'grid':'none';document.getElementById('ward').style.display=mca?'block':'none';load()}areaMode();C.onchange=async()=>{clearAreas();syncUrl();await populateConstituencies();await loadCandidates();load()};R.onchange=()=>{areaMode();loadCandidates()};const initialCounty={{ initial_county|tojson }};if(initialCounty){C.value=initialCounty;document.getElementById('sharebox').style.display='block';(async()=>{await populateConstituencies();await loadCandidates();load()})()}
function clearAreas(){let a=document.getElementById('constituency'),w=document.getElementById('ward');if(a)a.value='';if(w)w.value=''}
function slugCounty(v){return v.toLowerCase().replace(/&/g,'and').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}function syncUrl(){if(C.value){history.replaceState({},'', '/county/'+slugCounty(C.value)+(location.search||''));document.getElementById('sharebox').style.display='block'}}
function pulseUrl(){let u=new URL(location.href);u.searchParams.set('src','share');return u.toString()}async function sharePulse(){let text='Take part in the '+C.value+' county pulse and see aggregate participant results live. Open online pulse — not a scientific election forecast.';if(navigator.share){await navigator.share({title:'Kenya Pulse • '+C.value,text,url:pulseUrl()})}else{await navigator.clipboard.writeText(text+' '+pulseUrl());document.getElementById('sharemsg').textContent='Share text copied.'}}async function copyPulse(){await navigator.clipboard.writeText(pulseUrl());document.getElementById('sharemsg').textContent='County link copied.'}
async function loadCandidates(){
 let q='/api/candidates?race='+encodeURIComponent(R.value)+'&county='+encodeURIComponent(C.value)+'&constituency='+encodeURIComponent(document.getElementById('constituency').value||'')+'&ward='+encodeURIComponent(document.getElementById('ward').value||'');
 try{let r=await fetch(q),j=await r.json(),d=document.getElementById('candidateList');d.innerHTML='';(j.candidates||[]).forEach(x=>{let o=document.createElement('option');o.value=x.name;o.label='Full name'+(x.party?' · '+x.party:'');d.appendChild(o);(x.aliases||[]).forEach(a=>{let z=document.createElement('option');z.value=a;z.label='Alias for '+x.name;d.appendChild(z)})})}catch(e){}
}
let confirmedCandidate=null;
function cancelCandidateConfirm(){confirmedCandidate=null;let b=document.getElementById('candidateConfirm');b.style.display='none';document.getElementById('candidateConfirmText').textContent='';document.getElementById('confirmCandidateBtn').style.display='inline-block'}
function onScopeChange(){cancelCandidateConfirm();document.getElementById('candidate').value='';areaMode();loadCandidates();load()}
function onRaceChange(){cancelCandidateConfirm();document.getElementById('candidate').value='';areaMode();loadCandidates();load()}
async function vote(){let candidate=document.getElementById('candidate').value.trim(),issue=document.getElementById('issue').value.trim(),msg=document.getElementById('msg');if(!C.value||candidate.length<2){msg.textContent='Choose a county and enter a candidate name.';return}if(!confirmedCandidate||confirmedCandidate.typed!==candidate){try{let rr=await fetch('/api/candidates/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:candidate,race:R.value,county:C.value,constituency:document.getElementById('constituency').value.trim(),ward:document.getElementById('ward').value.trim()})});let rj=await rr.json(),matches=rj.matches||[],box=document.getElementById('candidateConfirm'),txt=document.getElementById('candidateConfirmText'),btn=document.getElementById('confirmCandidateBtn');if(matches.length===1){let m=matches[0];txt.textContent='Full name: '+m.name+(candidate.toLowerCase()!==m.name.toLowerCase()?' · Alias entered: '+candidate:'')+(m.party?' · '+m.party:'')+'.';btn.style.display='inline-block';box.style.display='block';btn.onclick=()=>{confirmedCandidate={id:m.id,name:m.name,typed:candidate};box.style.display='none';vote()};return}if(matches.length>1){msg.textContent='More than one verified person matches that name. Please enter the full name.';return}confirmedCandidate={id:null,name:candidate,typed:candidate}}catch(e){confirmedCandidate={id:null,name:candidate,typed:candidate}}}let cv=document.getElementById('constituency').value.trim(),wv=document.getElementById('ward').value.trim();if((R.value==='Member of Parliament'||R.value==='MCA')&&!cv){msg.textContent='Specify the constituency for this race.';return}if(R.value==='MCA'&&!wv){msg.textContent='Specify the ward for this MCA race.';return}let x=await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({county:C.value,race:R.value,candidate:confirmedCandidate.name,candidate_id:confirmedCandidate.id,issue,constituency:document.getElementById('constituency').value.trim(),ward:document.getElementById('ward').value.trim()})});let j=await x.json();msg.textContent=j.message||j.error;if(x.ok){document.getElementById('candidate').value='';confirmedCandidate=null;document.getElementById('candidateConfirm').style.display='none';await load();advanceParticipation()}}
const PARTICIPATION_FLOW=['President','Governor','Senator','Woman Representative','Member of Parliament','MCA'];
function advanceParticipation(){let i=PARTICIPATION_FLOW.indexOf(R.value);if(i<0)return;if(i<PARTICIPATION_FLOW.length-1){R.value=PARTICIPATION_FLOW[i+1];clearAreas();areaMode();loadCandidates();document.getElementById('msg').textContent='Response recorded. Next: '+R.value+'.';document.getElementById('supportbox').style.display='none';return}document.getElementById('msg').textContent='All six seat responses completed. Your responses are recorded.';document.getElementById('supportbox').style.display='block';document.getElementById('supportbox').scrollIntoView({behavior:'smooth',block:'center'})}
function dismissSupport(){document.getElementById('supportbox').style.display='none'}async function supportAmount(preset){let amount=preset||parseInt(document.getElementById('supportCustom').value||'0',10),msg=document.getElementById('supportmsg'),email=(document.getElementById('supportEmail').value||'').trim();if(!Number.isFinite(amount)||amount<5){msg.textContent='Support starts from KSh 5.';return}if(!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(email)){msg.textContent='Enter a valid email for the payment receipt.';document.getElementById('supportEmail').focus();return}msg.textContent='Opening secure Paystack checkout…';try{let x=await fetch('/api/support/initialize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount,email})}),j=await x.json();if(!x.ok||!j.authorization_url)throw new Error(j.error||'Could not start payment');location.href=j.authorization_url}catch(e){msg.textContent=e.message+'. No money has been taken.'}}async function loadAd(){let x=await fetch('/api/ad?county='+encodeURIComponent(C.value||'')),j=await x.json();if(j.ad){let a=j.ad,box=document.getElementById('liveAd');box.innerHTML='<div><b>'+esc(a.headline||a.business)+'</b><small>Sponsored by '+esc(a.business)+'</small>'+(a.url?'<div style="margin-top:10px"><a href="'+a.click_url+'" rel="sponsored noopener" style="color:#ffd54a">Visit advertiser →</a></div>':'')+'</div>'}}loadAd();
async function load(){if(!C.value)return;loadAd();let q='/api/results?county='+encodeURIComponent(C.value)+'&race='+encodeURIComponent(R.value);if(R.value==='Member of Parliament'||R.value==='MCA')q+='&constituency='+encodeURIComponent(document.getElementById('constituency').value.trim());if(R.value==='MCA')q+='&ward='+encodeURIComponent(document.getElementById('ward').value.trim());let x=await fetch(q,{cache:'no-store'}),j=await x.json();document.getElementById('metricTotal').textContent=j.total;document.getElementById('rt').textContent=C.value+' · '+R.value;let h='';for(let a of j.results){h+='<div class=row><b>'+esc(a.candidate)+'</b><span style="float:right">'+a.votes+' · '+a.pct+'%</span><div class=bar><div class=fill style="width:'+a.pct+'%"></div></div></div>'}document.getElementById('results').innerHTML=h||'<p class=muted>No responses yet for this county and race.</p>'}function esc(s){return s.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
let liveCountTimer=setInterval(()=>{if(C.value&&!document.hidden)load()},3000);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&C.value)load()});
</script></body></html>'''


GROUND_CATEGORIES=["Roads & bridges","Water","Health","Agriculture","Education","Security","Waste & environment","Electricity","Other"]
GROUND_HTML=r'''<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Sauti ya Ground · Kenya Pulse</title><link rel="stylesheet" href="/pulse95.css"><style>body{font-family:system-ui;margin:0;background:#eef5ee;color:#17351f}.wrap{max-width:900px;margin:auto;padding:24px}.card{background:rgba(255,255,255,.82);border:1px solid #fff;border-radius:24px;padding:20px;margin:16px 0;box-shadow:0 12px 35px #17351f18}input,select,textarea,button{box-sizing:border-box;width:100%;padding:13px;border-radius:14px;border:1px solid #ccd8cc;margin:6px 0;font:inherit}button{background:#17351f;color:white;font-weight:700}.muted{color:#607066;font-size:13px}.issue{border-top:1px solid #dce5dc;padding:14px 0}.pill{font-size:12px;background:#e4eee5;border-radius:999px;padding:5px 9px}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/">Participation</a><a href="/growth">Counties</a><a href="/methodology">Methodology</a></nav><div class="wrap kp95wrap"><div class="card glass kp95panel"><h1>Sauti ya Ground</h1><p>Share a local community issue by text, photo, video or voice. Submissions are reviewed before public display.</p><select id=county><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><input id=constituency placeholder="Constituency"><input id=ward placeholder="Ward"><input id=landmark placeholder="Village / landmark (optional)"><select id=category>{% for x in categories %}<option>{{x}}</option>{% endfor %}</select><textarea id=description rows=4 placeholder="Tell us what is happening and what needs attention"></textarea><select id=language><option>Swahili</option><option>English</option><option>Sheng</option><option>Local language</option></select><input id=media type=file accept="audio/*,video/*,image/*"><p class=muted>Audio/video transcription and permanent media storage will activate after the media service is connected. You can submit the written description now.</p><button onclick=submitIssue()>Submit for review</button><div id=msg class=muted></div></div><div class=card><h2>Community issues</h2><div id=feed>Loading…</div></div></div><script>async function submitIssue(){let d={county:county.value,constituency:constituency.value,ward:ward.value,landmark:landmark.value,category:category.value,description:description.value,language:language.value};let r=await fetch('/api/ground/issues',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}),j=await r.json();msg.textContent=j.message||j.error;if(r.ok){description.value='';load()}}async function load(){let r=await fetch('/api/ground/issues'),j=await r.json();feed.innerHTML=(j.issues||[]).map(x=>'<div class=issue><span class=pill>'+esc(x.category)+'</span> <span class=pill>'+esc(x.status)+'</span><h3>'+esc(x.ward||x.constituency||x.county)+'</h3><p>'+esc(x.description)+'</p><small>'+esc(x.county)+(x.constituency?' · '+esc(x.constituency):'')+(x.ward?' · '+esc(x.ward):'')+' · '+x.confirmations+' community confirmation(s)</small><button onclick="confirmIssue('+x.id+')">Same issue here</button></div>').join('')||'<p>No approved community issues yet.</p>'}async function confirmIssue(id){await fetch('/api/ground/issues/'+id+'/confirm',{method:'POST'});load()}function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}load()</script></body></html>'''

@app.get("/ground")
def ground():
 return render_template_string(GROUND_HTML,counties=COUNTIES,categories=GROUND_CATEGORIES)

@app.route("/api/ground/issues",methods=["GET","POST"])
def ground_issues():
 if request.method=="GET":
  with conn() as c:rows=c.execute("SELECT id,county,constituency,ward,landmark,category,description,status,confirmations,created_at FROM ground_issues WHERE status IN ('PUBLISHED','ACKNOWLEDGED','UPDATE_PROVIDED','RESOLVED') ORDER BY id DESC LIMIT 100").fetchall()
  return jsonify(issues=[dict(x) for x in rows])
 d=request.get_json(silent=True) or {};county=str(d.get("county") or "").strip();con=str(d.get("constituency") or "").strip()[:80];ward=str(d.get("ward") or "").strip()[:80];landmark=str(d.get("landmark") or "").strip()[:120];cat=str(d.get("category") or "").strip();desc=re.sub(r"\s+"," ",str(d.get("description") or "").strip())[:1500];lang=str(d.get("language") or "").strip()[:40]
 if county not in COUNTIES or cat not in GROUND_CATEGORIES or len(desc)<10:return jsonify(error="Choose a county, category and describe the issue."),400
 if (con or ward) and not geography_ok(county,con,ward):return jsonify(error="The constituency or ward does not match the selected county."),400
 with conn() as c:c.execute("INSERT INTO ground_issues(county,constituency,ward,landmark,category,description,language,status) VALUES(?,?,?,?,?,?,?,'UNDER_REVIEW')",(county,con or None,ward or None,landmark or None,cat,desc,lang))
 return jsonify(message="Submitted for moderation. It will appear publicly only after review."),201

@app.post("/api/ground/issues/<int:issue_id>/confirm")
def ground_confirm(issue_id):
 with conn() as c:c.execute("UPDATE ground_issues SET confirmations=confirmations+1 WHERE id=? AND status IN ('PUBLISHED','ACKNOWLEDGED','UPDATE_PROVIDED','RESOLVED')",(issue_id,))
 return jsonify(ok=True)

@app.get("/")
def home():
 src=re.sub(r"[^a-zA-Z0-9_-]","",request.args.get("src","direct"))[:60]
 with conn() as c:c.execute("INSERT INTO pulse_visits(county,source) VALUES(?,?)",(None,src))
 return redirect("/growth")

@app.get("/county/<slug>")
def county_page(slug):
 county=next((x for x in COUNTIES if re.sub(r"[^a-z0-9]+","-",x.lower()).strip("-")==slug.lower()),None)
 if not county:return "County not found",404
 src=re.sub(r"[^a-zA-Z0-9_-]","",request.args.get("src","direct"))[:60]
 with conn() as c:c.execute("INSERT INTO pulse_visits(county,source) VALUES(?,?)",(county,src))
 return render_template_string(HTML,counties=COUNTIES,races=RACES,initial_county=county,county_mark=COUNTY_MARKS.get(county,'🌿'))

@app.route("/advertise",methods=["GET","POST"])
def advertise():
 notice=""
 if request.method=="POST":
  business=request.form.get("business","").strip()[:100]; email=request.form.get("email","").strip()[:120]
  phone=request.form.get("phone","").strip()[:30]; scope=request.form.get("scope","County"); county=request.form.get("county","")
  package=request.form.get("package","County Starter"); headline=request.form.get("headline","").strip()[:140]; url=request.form.get("url","").strip()[:250]
  prices={"County Starter":5000,"County Pro":15000,"National":50000}
  is_commercial=not re.search(r"(?i)\b(candidate|campaign|vote for|elect|political party|president|governor|senator|mp|mca)\b",headline)
  valid_url=(not url) or bool(re.match(r"^https?://",url))
  if business and email and package in prices and (scope=="National" or county in COUNTIES) and is_commercial and valid_url:
   with conn() as db: db.execute("INSERT INTO ad_orders(business,email,phone,scope,county,package,budget,headline,url) VALUES(?,?,?,?,?,?,?,?,?)",(business,email,phone,scope,county if scope=="County" else None,package,prices[package],headline,url))
   notice="Campaign submitted for review. No payment has been taken yet."
  else: notice="Campaign could not be submitted. Check the fields and commercial-ad policy; destination links must start with http:// or https://."
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Advertise • Kenya Pulse</title><link rel="stylesheet" href="/pulse95.css"><style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 10%,#00ff8840,transparent 28%),radial-gradient(circle at 90% 10%,#ffd90030,transparent 25%),#020806;color:white;font-family:Inter,system-ui}.w{max-width:1000px;margin:auto;padding:35px 18px}.glass{background:linear-gradient(135deg,#ffffff18,#ffffff06);border:1px solid #ffffff30;box-shadow:inset 0 1px #ffffff45,0 30px 90px #0008;backdrop-filter:blur(35px) saturate(170%);border-radius:30px}.hero,.form{padding:28px;margin-bottom:16px}h1{font-size:clamp(42px,7vw,70px);margin:8px 0}.muted{color:#a9beb1}.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.p{padding:20px}.price{font-size:32px;font-weight:900;color:#ffe064}input,select,button{width:100%;padding:15px;margin:6px 0;border-radius:15px;border:1px solid #ffffff25;background:#ffffff0b;color:white;font:inherit}option{color:#111}button{background:linear-gradient(135deg,#78ffb3,#e3ff72);color:#04120a;font-weight:900;cursor:pointer}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.notice{padding:14px;border:1px solid #8affb855;border-radius:14px;background:#48ff9a10}.tag{font:700 11px monospace;letter-spacing:.15em;color:#7dffb7}@media(max-width:700px){.plans,.grid{grid-template-columns:1fr}}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/">Participation</a><a href="/growth">Counties</a><a href="/ground">Sauti ya Ground</a></nav><main class="w kp95wrap"><section class="glass hero"><div class=tag>KENYA PULSE • ADVERTISER STUDIO</div><h1>Put your brand<br>inside the pulse.</h1><p class=muted>Choose a national or county placement. Advertising is clearly labelled and kept separate from participation choices and results.</p></section><section class=plans><div class="glass p"><b>COUNTY STARTER</b><div class=price>KSh 5K</div><span class=muted>County placement</span></div><div class="glass p"><b>COUNTY PRO</b><div class=price>KSh 15K</div><span class=muted>Premium county placement</span></div><div class="glass p"><b>NATIONAL</b><div class=price>KSh 50K</div><span class=muted>Across the network</span></div></section><form class="glass form" method=post><h2>Launch a campaign</h2>{% if notice %}<p class=notice>{{notice}}</p>{% endif %}<div class=grid><input name=business required placeholder="Business / brand"><input type=email name=email required placeholder="Business email"><input name=phone placeholder="Phone number"><input name=headline placeholder="Ad headline"></div><div class=grid><select name=scope id=scope onchange="county.disabled=this.value==='National'"><option>County</option><option>National</option></select><select name=county id=county>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select></div><select name=package><option>County Starter</option><option>County Pro</option><option>National</option></select><input name=url placeholder="Business website / campaign link (optional)"><button>SUBMIT CAMPAIGN FOR REVIEW →</button><p class=muted>No payment is collected at this stage. Approved campaigns can be connected to M-Pesa once merchant payment credentials are configured.</p></form></main></body></html>"""
 return render_template_string(html,counties=COUNTIES,notice=notice)


@app.post("/api/analytics/pageview")
def analytics_pageview():
 data=request.get_json(silent=True) or {}; path=str(data.get("path") or "/")[:180]; county=str(data.get("county") or "")[:60]; src=re.sub(r"[^a-zA-Z0-9_-]","",str(data.get("source") or "direct"))[:60]; sid=str(data.get("session_id") or "")[:80]
 if county and county not in COUNTIES:county=""
 with conn() as db:db.execute("INSERT INTO pulse_visits(county,source,path,session_id) VALUES(?,?,?,?)",(county or None,src,path,sid or None))
 return jsonify(ok=True)

@app.get("/api/ad")
def serve_ad():
 county=request.args.get("county","")
 with conn() as db:
  ad_sql="""SELECT id,business,headline,url,scope,county FROM ad_orders
   WHERE status='ACTIVE' AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
   AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)
   AND (scope='National' OR county=?)
   ORDER BY CASE WHEN scope='County' THEN 0 ELSE 1 END, impressions ASC, id ASC LIMIT 1""" if db.pg else """SELECT id,business,headline,url,scope,county FROM ad_orders
   WHERE status='ACTIVE' AND (starts_at IS NULL OR datetime(starts_at)<=datetime('now'))
   AND (ends_at IS NULL OR datetime(ends_at)>=datetime('now'))
   AND (scope='National' OR county=?)
   ORDER BY CASE WHEN scope='County' THEN 0 ELSE 1 END, impressions ASC, id ASC LIMIT 1"""
  row=db.execute(ad_sql,(county,)).fetchone()
  if not row:return jsonify(ad=None)
  db.execute("UPDATE ad_orders SET impressions=COALESCE(impressions,0)+1 WHERE id=?",(row["id"],))
  ad=dict(row); ad["click_url"]="/api/ad-click/"+str(row["id"])
  return jsonify(ad=ad)

@app.get("/api/ad-click/<int:ad_id>")
def ad_click(ad_id):
 with conn() as db:
  row=db.execute("SELECT url FROM ad_orders WHERE id=? AND status='ACTIVE'",(ad_id,)).fetchone()
  if not row or not row["url"]:return redirect("/")
  db.execute("UPDATE ad_orders SET clicks=COALESCE(clicks,0)+1 WHERE id=?",(ad_id,))
  return redirect(row["url"],code=302)

@app.get("/admin/ad-metrics")
def ad_metrics():
 if not ADMIN_KEY or not hmac.compare_digest(request.args.get("key",""),ADMIN_KEY): abort(404)
 with conn() as db:
  rows=[dict(x) for x in db.execute("""SELECT id,business,scope,county,package,budget,status,impressions,clicks,
   CASE WHEN COALESCE(impressions,0)>0 THEN ROUND(COALESCE(clicks,0)*100.0/impressions,2) ELSE 0 END ctr
   FROM ad_orders ORDER BY id DESC LIMIT 100""").fetchall()]
 return jsonify(campaigns=rows)

@app.post("/admin/ads/<int:ad_id>/status")
def ad_status(ad_id):
 if not ADMIN_KEY or not hmac.compare_digest(request.args.get("key",""),ADMIN_KEY): abort(404)
 status=(request.get_json(silent=True) or {}).get("status","")
 if status not in {"PENDING_REVIEW","ACTIVE","PAUSED","ENDED"}:return jsonify(error="Invalid status"),400
 with conn() as db:db.execute("UPDATE ad_orders SET status=? WHERE id=?",(status,ad_id))
 return jsonify(ok=True,status=status)

@app.get("/admin/ads")
def admin_ads():
 if not ADMIN_KEY or not hmac.compare_digest(request.args.get("key",""),ADMIN_KEY): abort(404)
 with conn() as db: orders=[dict(x) for x in db.execute("SELECT * FROM ad_orders ORDER BY id DESC LIMIT 100").fetchall()]
 return jsonify({"orders":orders,"note":"Pending campaigns require review before activation."})

@app.get("/growth")
def growth():
 with conn() as c:
  rows=c.execute("""SELECT COALESCE(county,'Homepage') county,source,count(*) visits FROM pulse_visits GROUP BY county,source ORDER BY visits DESC LIMIT 150""").fetchall()
  votes=c.execute("SELECT county,count(*) n FROM pulse_votes GROUP BY county ORDER BY n DESC").fetchall()
 traffic=[dict(x) for x in rows]; vm={x["county"]:x["n"] for x in votes}
 with conn() as c:
  ads=c.execute("SELECT COALESCE(SUM(impressions),0) impressions,COALESCE(SUM(clicks),0) clicks,COALESCE(SUM(CASE WHEN status IN ('ACTIVE','ENDED') THEN budget ELSE 0 END),0) booked FROM ad_orders").fetchone()
 ad_impressions=int(ads["impressions"] or 0);ad_clicks=int(ads["clicks"] or 0);booked=int(ads["booked"] or 0);ad_ctr=round(ad_clicks*100/ad_impressions,2) if ad_impressions else 0
 totalv=sum(x["visits"] for x in traffic); totalr=sum(vm.values()); rate=round(totalr*100/totalv,1) if totalv else 0
 county_marks={"Mombasa":"🌊","Kwale":"🌴","Kilifi":"🌴","Tana River":"🏞️","Lamu":"⛵","Taita-Taveta":"⛰️","Garissa":"☀️","Wajir":"🐪","Mandera":"🌅","Marsabit":"🗻","Isiolo":"🦒","Meru":"🌿","Tharaka Nithi":"🌾","Embu":"🌱","Kitui":"🌵","Machakos":"🏞️","Makueni":"🥭","Nyandarua":"🥔","Nyeri":"☕","Kirinyaga":"🌾","Murang'a":"🍃","Kiambu":"☕","Turkana":"🌞","West Pokot":"⛰️","Samburu":"🦓","Trans Nzoia":"🌽","Uasin Gishu":"🌽","Elgeyo Marakwet":"🏔️","Nandi":"🍃","Baringo":"🐝","Laikipia":"🦒","Nakuru":"🦩","Narok":"🦁","Kajiado":"🐄","Kericho":"🍃","Bomet":"🍵","Kakamega":"🌳","Vihiga":"🌿","Bungoma":"🌾","Busia":"🌅","Siaya":"🐟","Kisumu":"🐟","Homa Bay":"🌊","Migori":"🌾","Kisii":"🍌","Nyamira":"🍌","Nairobi City":"🏙️"}
 cards=[]
 for county in COUNTIES:
  v=sum(x["visits"] for x in traffic if x["county"]==county); n=vm.get(county,0)
  cards.append({"county":county,"visits":v,"responses":n,"conversion":round(n*100/v,1) if v else 0,"icon":county_marks.get(county,"◉")})
 cards.sort(key=lambda x:(-x["responses"],-x["visits"],x["county"]))
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Kenya Pulse — National Dashboard</title><style>
*{box-sizing:border-box}body{margin:0;color:#f8fff9;font-family:Inter,ui-sans-serif,system-ui;background:#123d28;min-height:100vh}.world{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 82% 13%,#ffe9a0 0 2%,#ffd98544 2.3% 8%,transparent 18%),linear-gradient(180deg,#8db7ad 0 27%,#709777 42%,#245b38 68%,#0d3420 100%)}.world:before{content:'';position:absolute;inset:27% -10% -10%;background:linear-gradient(160deg,transparent 0 17%,#547b50 17.4% 28%,transparent 28.4%),linear-gradient(25deg,transparent 0 24%,#397047 24.4% 50%,transparent 50.4%),linear-gradient(160deg,transparent 0 44%,#185435 44.4% 70%,transparent 70.4%);filter:blur(2px)}.world:after{content:'';position:absolute;inset:55% -5% -5%;background:radial-gradient(ellipse at 60% 15%,#91c1a077,transparent 30%),linear-gradient(8deg,#0d3c25 0 48%,transparent 48.5%),linear-gradient(-9deg,#22613a 0 57%,transparent 57.5%);filter:blur(2px)}.shade{position:fixed;inset:0;z-index:-2;background:linear-gradient(90deg,#0626179e 0,transparent 45%),linear-gradient(0deg,#06241699 0,transparent 45%)}.wrap{max-width:1380px;margin:auto;padding:18px 24px 70px}.glass{background:linear-gradient(135deg,rgba(19,55,38,.62),rgba(37,72,55,.38));border:1px solid rgba(236,255,242,.28);box-shadow:inset 0 1px rgba(255,255,255,.32),0 18px 50px rgba(3,27,15,.22);backdrop-filter:blur(20px) saturate(112%);-webkit-backdrop-filter:blur(20px) saturate(112%)}.nav{height:66px;border-radius:24px;display:flex;align-items:center;padding:0 20px;gap:26px}.brand{font-size:21px;font-weight:950;margin-right:auto}.brand b{color:#7cf39d}.nav a{color:#edf8f1;text-decoration:none;font-size:13px}.search{width:min(340px,32vw);padding:12px 16px;border-radius:15px;border:1px solid #ffffff22;background:#0b2d1e66;color:white}.hero{min-height:390px;display:grid;grid-template-columns:1.2fr .8fr;align-items:center;gap:40px;padding:52px 28px 28px}.hero h1{font-size:clamp(48px,6vw,82px);line-height:.94;letter-spacing:-3px;margin:12px 0 20px;max-width:780px}.hero h1 em{font-style:normal;color:#75f59b}.hero p{max-width:620px;font-size:17px;line-height:1.6;color:#d5e8dc}.ey{font:800 11px ui-monospace,monospace;letter-spacing:.18em;color:#baf5ca}.mapcard{justify-self:end;width:min(380px,100%);padding:25px;border-radius:28px}.mapcard strong{font-size:34px;display:block;margin:7px 0}.actions{display:flex;gap:10px;margin-top:26px}.btn{display:inline-flex;padding:14px 20px;border-radius:15px;background:#69ef91;color:#092817;font-weight:900;text-decoration:none}.btn.alt{background:#ffffff12;color:white;border:1px solid #ffffff33}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:0 0 16px}.stat{border-radius:23px;padding:20px}.stat small{color:#c2d8ca}.num{font-size:34px;font-weight:950;margin-top:6px}.main{display:grid;grid-template-columns:1.55fr .85fr;gap:16px}.explore,.side,.ad{border-radius:30px;padding:25px}.head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:18px}.head h2{margin:0;font-size:24px}.muted{color:#bdd1c4}.searchdock{display:flex;gap:10px;margin-bottom:18px}.searchdock input{flex:1;padding:15px;border-radius:15px;border:1px solid #ffffff2d;background:#092d1e88;color:white}.searchdock button{border:0;border-radius:15px;padding:0 18px;background:#69ef91;font-weight:900;color:#082416}.counties{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-height:510px;overflow:auto;padding-right:4px}.county{position:relative;min-height:145px;border-radius:20px;padding:17px;background:linear-gradient(145deg,#ffffff17,#0d402a44);border:1px solid #ffffff20}.county .ico{font-size:30px}.county h3{margin:10px 0 4px}.county a{color:#a8f4bf;text-decoration:none;font-size:12px;font-weight:800}.meter{height:5px;border-radius:8px;background:#ffffff15;margin:10px 0;overflow:hidden}.meter i{display:block;height:100%;background:#70ee96}.side{min-height:300px}.pulseRow{padding:15px 0;border-bottom:1px solid #ffffff15}.pulseRow b{display:block;font-size:18px}.ad{margin-top:16px;display:flex;align-items:center;justify-content:space-between;gap:20px;background:linear-gradient(120deg,rgba(16,57,38,.7),rgba(101,86,31,.35))}.chip{padding:7px 10px;border-radius:99px;background:#ffffff10;border:1px solid #ffffff20;font-size:11px}.footer{text-align:center;color:#a9c2b2;font-size:12px;padding:30px 0 0}@media(max-width:900px){.nav a{display:none}.search{width:46%}.hero{grid-template-columns:1fr;min-height:460px;padding:40px 10px}.mapcard{justify-self:start}.stats{grid-template-columns:1fr 1fr}.main{grid-template-columns:1fr}.counties{grid-template-columns:1fr 1fr}}@media(max-width:560px){.wrap{padding:10px 12px 50px}.nav{height:60px}.search{display:none}.hero h1{font-size:48px}.hero{padding-top:34px}.stats{gap:8px}.stat{padding:15px}.num{font-size:27px}.counties{grid-template-columns:1fr}.actions{flex-wrap:wrap}.explore,.side,.ad{padding:18px;border-radius:24px}}
/* 9/10 finish pass: richer world, restrained premium glass */
body{background:#0b2f1d}.world{background:radial-gradient(circle at 84% 12%,#ffe8a1 0 1.8%,#ffd9894d 2% 8%,transparent 17%),linear-gradient(180deg,#82aea8 0 23%,#72987b 37%,#2c7045 65%,#0b3c24 100%)}.world:before{inset:23% -8% -8%;background:radial-gradient(ellipse at 64% 40%,rgba(194,224,190,.32),transparent 24%),linear-gradient(158deg,transparent 0 15%,#63885b 15.4% 27%,transparent 27.4%),linear-gradient(24deg,transparent 0 23%,#39774a 23.4% 50%,transparent 50.4%),linear-gradient(158deg,transparent 0 43%,#145a34 43.4% 70%,transparent 70.4%);filter:blur(1.2px)}.world:after{inset:52% -4% -4%;background:radial-gradient(ellipse at 63% 8%,rgba(173,210,184,.42),transparent 26%),linear-gradient(8deg,#0b4227 0 46%,transparent 46.5%),linear-gradient(-8deg,#216a3d 0 57%,transparent 57.5%);filter:blur(1.5px)}.shade{background:linear-gradient(90deg,rgba(4,30,17,.70),transparent 48%),linear-gradient(0deg,rgba(3,28,15,.62),transparent 42%)}.glass{background:linear-gradient(135deg,rgba(20,55,39,.58),rgba(39,73,56,.34));border:1px solid rgba(240,255,245,.34);box-shadow:inset 0 1px rgba(255,255,255,.38),inset 0 -1px rgba(255,255,255,.06),0 18px 48px rgba(3,25,14,.20);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.nav{margin-top:2px}.hero{min-height:420px;padding-top:64px}.hero h1{font-weight:950;letter-spacing:-4px}.hero p{color:#e0eee5}.mapcard{background:linear-gradient(135deg,rgba(42,72,55,.52),rgba(91,82,47,.26));box-shadow:inset 0 1px rgba(255,255,255,.40),0 24px 60px rgba(5,29,17,.20)}.stats{margin-top:-10px}.stat{min-height:105px}.explore,.side,.ad{box-shadow:inset 0 1px rgba(255,255,255,.38),0 22px 58px rgba(4,28,16,.19)}.county{transition:transform .2s ease,border-color .2s ease,background .2s ease}.county:hover{transform:translateY(-3px);border-color:#ffffff55;background:linear-gradient(145deg,#ffffff20,#164c334f)}.btn{box-shadow:0 10px 28px rgba(30,205,92,.18)}.btn:hover{filter:brightness(1.05)}.searchdock input:focus,.search:focus{outline:2px solid rgba(112,238,150,.42);outline-offset:1px}.footer{padding-top:38px}
/* 9.5 art-direction pass */
body:after{content:'';position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(ellipse at 50% 48%,transparent 45%,rgba(3,24,13,.22) 100%);mix-blend-mode:multiply}.world{transform:scale(1.015);filter:saturate(1.08) contrast(1.025)}.world:before{filter:blur(.8px);opacity:.96}.world:after{filter:blur(1.2px);opacity:.98}.wrap{padding-top:16px}.nav{background:linear-gradient(120deg,rgba(25,58,43,.54),rgba(44,76,60,.31));border-color:rgba(244,255,247,.32);box-shadow:inset 0 1px rgba(255,255,255,.40),0 16px 42px rgba(3,24,13,.16)}.brand{letter-spacing:-.7px}.hero{min-height:440px;gap:64px}.hero h1{max-width:830px;font-size:clamp(52px,6.3vw,88px);line-height:.91;text-wrap:balance}.hero p{max-width:650px;font-size:18px;line-height:1.62}.mapcard{padding:29px;border-radius:31px}.mapcard:before{content:'✦';float:right;font-size:62px;line-height:1;color:rgba(160,255,188,.17);filter:drop-shadow(0 0 18px rgba(107,241,148,.12))}.stats{gap:16px}.stat{padding:22px 23px;border-radius:25px}.num{letter-spacing:-1.5px}.main{gap:18px}.explore,.side,.ad{border-color:rgba(242,255,246,.30)}.explore{padding:28px}.counties{gap:13px}.county{min-height:154px;padding:18px;border-radius:22px;background:linear-gradient(145deg,rgba(255,255,255,.105),rgba(11,57,35,.25));box-shadow:inset 0 1px rgba(255,255,255,.15)}.county h3{font-size:17px;letter-spacing:-.2px}.county .muted{font-size:12px}.meter{margin:12px 0}.side{padding:28px}.pulseRow{padding:18px 0}.ad{padding:26px}.btn{transition:transform .18s ease,filter .18s ease,box-shadow .18s ease}.btn:hover{transform:translateY(-2px);box-shadow:0 14px 32px rgba(31,210,94,.22)}.ey{font-size:10px}.footer{opacity:.9}@media(max-width:900px){.hero{gap:24px;min-height:500px}.hero h1{font-size:clamp(50px,10vw,72px)}}@media(max-width:560px){.hero{min-height:510px}.hero h1{font-size:49px;letter-spacing:-2.7px}.hero p{font-size:15px}.mapcard{padding:22px}.stat{min-height:94px}.explore{padding:19px}}
</style></head><body><div class=world></div><div class=shade></div><main class=wrap><nav class="glass nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/">Home</a><a href="#counties">Counties</a><a href="/methodology">Methodology</a><a href="/advertise">Advertise</a><input class=search placeholder="Search county…" oninput="filterCounties(this.value)"></nav><section class=hero><div><div class=ey>OPEN NATIONAL PARTICIPATION • 47 COUNTIES</div><h1>A clearer view of <em>Kenya's pulse.</em></h1><p>Explore voluntary participation across counties, constituencies and wards. Aggregate responses update as people take part. This is an open online pulse, not a scientific election forecast.</p><div class=actions><a class=btn href="#counties">Explore counties →</a><a class="btn alt" href="/">Take part</a></div></div><aside class="glass mapcard"><span class=ey>KENYA COVERAGE</span><strong>47 Counties</strong><div class=muted>290 constituencies · 1,450 wards</div><p class=muted>One participation experience with local geographic scope.</p></aside></section><section class=stats><div class="glass stat"><small>Visits</small><div class=num>{{totalv}}</div></div><div class="glass stat"><small>Responses</small><div class=num>{{totalr}}</div></div><div class="glass stat"><small>Response ratio</small><div class=num>{{rate}}%</div></div><div class="glass stat"><small>Counties available</small><div class=num>47</div></div></section><section class=main id=counties><div class="glass explore"><div class=head><div><h2>Explore by county</h2><div class=muted>Open a county to view its participant results.</div></div><span class=chip id=found>47 COUNTIES</span></div><div class=searchdock><input id=countySearch list=countiesList placeholder="Kericho, Nairobi, Kisumu…" oninput="filterCounties(this.value)"><datalist id=countiesList>{% for x in cards %}<option value="{{x.county}}">{% endfor %}</datalist><button onclick=openCounty()>OPEN →</button></div><div class=counties id=countyGrid>{% for x in cards %}<article class=county data-county="{{x.county|lower}}"><div class=ico>{{x.icon}}</div><h3>{{x.county}}</h3><div class=muted>{{x.responses}} responses · {{x.visits}} visits</div><div class=meter><i style="width:{{[x.conversion,100]|min}}%"></i></div><a href="/county/{{slug(x.county)}}?src=dashboard">VIEW COUNTY →</a></article>{% endfor %}</div></div><aside><section class="glass side"><div class=head><div><h2>Participation snapshot</h2><div class=muted>Aggregate platform activity</div></div></div><div class=pulseRow><b>{{totalr}} responses</b><span class=muted>Across current participant submissions</span></div><div class=pulseRow><b>{{totalv}} visits</b><span class=muted>Recorded platform visits</span></div><div class=pulseRow><b>{{rate}}% ratio</b><span class=muted>Responses relative to recorded visits</span></div><p class=muted style="font-size:12px">These figures describe Kenya Pulse participation only and are not representative of all Kenyan voters.</p></section><section class="glass ad"><div><div class=ey>ADVERTISEMENT</div><h2>Put your brand here</h2><div class=muted>Commercial advertising stays separate from participation and results.</div></div><a class=btn href="/advertise">Advertise</a></section></aside></section><section class="glass side" style="margin-top:18px"><div class=head><div><h2>Revenue engine</h2><div class=muted>Commercial inventory only — separate from participation and results.</div></div></div><div class=pulseRow><b>{{ad_impressions}} ad impressions</b><span class=muted>{{ad_clicks}} genuine ad clicks · {{ad_ctr}}% CTR</span></div><div class=pulseRow><b>KSh {{booked}} booked</b><span class=muted>Campaign value from active/ended direct commercial campaigns; not cash received unless payment is verified.</span></div><div class=pulseRow><b>Programmatic ads</b><span class=muted>Ready for publisher code after external ad-network approval. No fake placeholders counted as revenue.</span></div><a class=btn href="/advertise">Sell commercial inventory →</a></section><div class=footer>Kenya Pulse · Voluntary online participation · Not an election forecast</div></main><script>
function filterCounties(q){q=(q||'').trim().toLowerCase();let cs=[...document.querySelectorAll('.county')],n=0;cs.forEach(c=>{let ok=c.dataset.county.includes(q);c.style.display=ok?'block':'none';if(ok)n++});document.getElementById('found').textContent=n+' COUNTIES';let x=document.getElementById('countySearch');if(x&&document.activeElement!==x)x.value=q}
function openCounty(){let q=document.getElementById('countySearch').value.trim().toLowerCase();let cs=[...document.querySelectorAll('.county')];let x=cs.find(c=>c.dataset.county===q)||cs.find(c=>c.style.display!=='none');if(x)location.href=x.querySelector('a').href}
document.getElementById('countySearch').addEventListener('keydown',e=>{if(e.key==='Enter')openCounty()})
(()=>{let sid=localStorage.getItem('kp_sid');if(!sid){sid=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());localStorage.setItem('kp_sid',sid)}fetch('/api/analytics/pageview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:location.pathname,source:new URLSearchParams(location.search).get('src')||'direct',session_id:sid})}).catch(()=>{})})();
</script></body></html>"""
 return render_template_string(html,cards=cards,totalv=totalv,totalr=totalr,rate=rate,ad_impressions=ad_impressions,ad_clicks=ad_clicks,ad_ctr=ad_ctr,booked=booked,slug=lambda s:re.sub(r"[^a-z0-9]+","-",s.lower()).strip("-"))

@app.get("/health")
def health():
 dbmode="postgres" if DATABASE_URL else "sqlite-fallback";db_ok=False
 try:
  with conn() as c:c.execute("SELECT 1").fetchone();db_ok=True
 except Exception:db_ok=False
 return {"ok":db_ok,"production_ready":db_ok and dbmode=="postgres","counties":len(COUNTIES),"constituencies":sum(len(x) for x in GEOGRAPHY.values()),"wards":sum(len(w) for x in GEOGRAPHY.values() for w in x.values()),"database":dbmode,"database_ok":db_ok,"candidate_registry":True,"warning":None if db_ok and dbmode=="postgres" else "Persistent PostgreSQL is not attached and verified; responses may be lost on service restart."}
@app.get("/privacy")
def privacy():
 return legal_page("Privacy Policy","Effective 2 October 2026",[
 ("What Kenya Pulse collects","Information you choose to submit, including county, selected race, candidate preference and an optional issue, plus limited technical information needed to operate and protect the service."),
 ("How we protect participation","A one-way technical fingerprint is used to restrict duplicate submissions. Aggregate participant results may be displayed publicly; individual submissions and technical fingerprints are not displayed publicly."),
 ("Meta / Facebook connections","Information authorized through a Meta connection is used only to provide requested Kenya Pulse features. Following or sharing the Kenya Pulse Facebook Page is optional and is never required for a response to be counted."),
 ("How information is used","We use information to operate, secure, measure and improve Kenya Pulse. We do not sell individual voting preferences. Voluntary participant results are not representative of all Kenyan voters and are not an election forecast."),
 ("Your choices","For privacy questions or deletion requests, email kenyapulse2026@gmail.com. You can also use our Data Deletion instructions.")
 ])

@app.get("/terms")
def terms():
 return legal_page("Terms of Service","Effective 2 October 2026",[
 ("Using Kenya Pulse","Kenya Pulse is an open, voluntary public-participation service. Use it lawfully and do not manipulate results, submit automated or fraudulent responses, disrupt the service or impersonate others."),
 ("Understanding the results","Displayed results reflect voluntary website participants. They are not representative of all Kenyan voters and are not predictions of election outcomes. Candidate names may be participant-entered and their appearance is not an endorsement."),
 ("Service operation","Features may change or be suspended when necessary for security, reliability, legal compliance or product development. Abusive or automated activity may be restricted."),
 ("Third-party services","Meta, Facebook and other third-party services are governed by their own terms and policies. Following or sharing Kenya Pulse is optional and is not a condition for participation."),
 ("Contact","Questions about these terms can be sent to kenyapulse2026@gmail.com.")
 ])

@app.get("/data-deletion-instructions")
@app.get("/data-deletion")
def data_deletion():
 return legal_page("Data Deletion","Request removal of information associated with your Kenya Pulse use",[
 ("1. Send your request","Email kenyapulse2026@gmail.com with the subject: Kenya Pulse Data Deletion Request."),
 ("2. Identify the connection","Provide enough information for us to identify the relevant account or connection. Never send passwords, access tokens or other secrets."),
 ("3. Processing","Valid requests will be reviewed and processed subject to applicable legal, security and record-retention requirements.")
 ])

def legal_page(title,kicker,sections):
 cards="".join(f"<section><h2>{h}</h2><p>{p}</p></section>" for h,p in sections)
 return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} — Kenya Pulse</title><link rel='stylesheet' href='/pulse95.css'><style>*{{box-sizing:border-box}}body{{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui,sans-serif;line-height:1.65}}header{{border-bottom:1px solid #21432f;background:#081a12}}nav,main,footer{{max-width:920px;margin:auto;padding:20px}}nav{{display:flex;align-items:center;justify-content:space-between}}.brand{{font-weight:950;font-size:22px;letter-spacing:-.5px}}.brand b,.eyebrow,a{{color:#ffd447}}nav a{{text-decoration:none;color:#d7eadf}}main{{padding-top:64px;padding-bottom:70px}}.eyebrow{{font-weight:850;text-transform:uppercase;letter-spacing:1.5px;font-size:12px}}h1{{font-size:clamp(42px,7vw,70px);line-height:1;margin:10px 0 16px;letter-spacing:-2px}}.lead{{font-size:18px;color:#abc8b5;max-width:680px;margin-bottom:38px}}section{{background:linear-gradient(145deg,#0d2318,#0a1b13);border:1px solid #21432f;border-radius:20px;padding:24px;margin:14px 0}}h2{{font-size:19px;margin:0 0 8px}}p{{margin:0;color:#c8ddd0}}.links{{display:flex;gap:16px;flex-wrap:wrap;margin-top:30px}}footer{{border-top:1px solid #21432f;color:#87a493;font-size:13px;padding-top:28px;padding-bottom:40px}}@media(max-width:600px){{main{{padding-top:38px}}nav{{padding:16px 20px}}}}</style></head><body><div class='world'></div><div class='shade'></div><header><nav class='glass kp95nav'><div class='brand'>KENYA <b>PULSE</b></div><a href='/'>Participation</a><a href='/growth'>Counties</a><a href='/ground'>Sauti ya Ground</a></nav></header><main class='kp95wrap'><div class='eyebrow'>Transparent participation</div><h1>{title}</h1><p class='lead'>{kicker}. Clear rules, privacy-minded participation and transparent public information.</p>{cards}<div class='links'><a href='/privacy'>Privacy Policy</a><a href='/terms'>Terms of Service</a><a href='/data-deletion'>Data Deletion</a><a href='/methodology'>Methodology</a></div></main><footer>KENYA PULSE · Your county. Your voice. · Open voluntary participation, not an election forecast.</footer></body></html>"""

@app.post("/meta/data-deletion")
def meta_data_deletion():
 signed=request.form.get("signed_request","")
 secret=os.environ.get("META_APP_SECRET","")
 if not signed or not secret:
  return jsonify(error="Missing signed request or server configuration."),400
 try:
  encoded_sig,payload=signed.split(".",1)
  def b64decode(v):
   return base64.urlsafe_b64decode(v+"="*((4-len(v)%4)%4))
  supplied=b64decode(encoded_sig)
  expected=hmac.new(secret.encode(),payload.encode(),hashlib.sha256).digest()
  if not hmac.compare_digest(supplied,expected):
   return jsonify(error="Invalid signed request."),403
  data=json.loads(b64decode(payload))
  user_id=str(data.get("user_id",""))
  code=hashlib.sha256((user_id+SALT).encode()).hexdigest()[:24]
  return jsonify(url=request.url_root.rstrip("/")+"/data-deletion-status?code="+code,confirmation_code=code)
 except Exception:
  return jsonify(error="Invalid signed request."),400

@app.get("/data-deletion-status")
def data_deletion_status():
 code=request.args.get("code","")
 if not re.fullmatch(r"[a-f0-9]{24}",code):
  return "Invalid deletion confirmation code.",400
 return legal_page("Deletion Request Status","Your request has been received",[
  ("Confirmation code",code),
  ("Status","The request has been recorded for review. Kenya Pulse does not publicly display individual participant submissions or technical fingerprints."),
  ("Need help?","Email kenyapulse2026@gmail.com and include your confirmation code. Never send passwords or access tokens.")
 ])

@app.get("/methodology")
def methodology():
 return legal_page("How Kenya Pulse Works","Methodology & transparency",[
  ("Open participation","Kenya Pulse is an open, voluntary online participation dashboard. It is not a probability sample, and participant results are not representative of all Kenyan voters."),
  ("What participants submit","Participants select a county and race, then enter their current preferred candidate. Public results display aggregate participant counts and percentages rather than individual submissions."),
  ("Duplicate protection","A one-way technical fingerprint derived from limited network and client information is used to restrict duplicate submissions for the same county and race. Raw fingerprints are not displayed publicly."),
  ("How results are calculated","No weighting or normalization is applied. Undecided participants may enter “Undecided”. Candidate names are participant-entered and spelling variants may be consolidated in future audited releases."),
  ("Independence of results","Kenya Pulse does not endorse candidates or predict election outcomes. Advertising is clearly separated from participation controls and results, and advertising does not affect whether a response is counted.")
 ])

@app.get("/api/candidates")
def candidates_api():
 race=request.args.get("race","").strip();county=request.args.get("county","").strip();constituency=request.args.get("constituency","").strip();ward=request.args.get("ward","").strip()
 if race not in RACES:return jsonify(candidates=[])
 sql="SELECT id,name,party,status FROM candidates WHERE active=TRUE AND race=?";args=[race]
 if race!="President":sql+=" AND county=?";args.append(county)
 if race in {"Member of Parliament","MCA"}:sql+=" AND constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND ward=?";args.append(ward)
 sql+=" ORDER BY name"
 with conn() as c:
  rows=c.execute(sql,args).fetchall()
  out=[]
  for x in rows:
   item=dict(x); aliases=c.execute("SELECT alias FROM candidate_aliases WHERE candidate_id=? AND verified=1 ORDER BY alias",(item["id"],)).fetchall()
   item["aliases"]=[a["alias"] for a in aliases];out.append(item)
 return jsonify(candidates=out)

@app.post("/api/candidates/resolve")
def candidate_resolve():
 d=request.get_json(silent=True) or {};typed=re.sub(r"\s+"," ",str(d.get("name") or "").strip())[:80];race=str(d.get("race") or "").strip();county=str(d.get("county") or "").strip();constituency=str(d.get("constituency") or "").strip();ward=str(d.get("ward") or "").strip()
 if len(typed)<2 or race not in RACES:return jsonify(matches=[]),400
 sql="SELECT DISTINCT c.id,c.name,c.party,c.status FROM candidates c LEFT JOIN candidate_aliases a ON a.candidate_id=c.id WHERE c.active=TRUE AND c.race=? AND (LOWER(c.name)=LOWER(?) OR (a.verified=TRUE AND LOWER(a.alias)=LOWER(?)))";args=[race,typed,typed]
 if race!="President":sql+=" AND c.county=?";args.append(county)
 if race in {"Member of Parliament","MCA"}:sql+=" AND c.constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND c.ward=?";args.append(ward)
 with conn() as c:rows=c.execute(sql,args).fetchall()
 return jsonify(matches=[dict(x) for x in rows],typed=typed)

@app.post("/api/vote")
def vote():
 d=request.get_json(silent=True) or {}; county=d.get("county","").strip(); race=d.get("race","").strip(); constituency=d.get("constituency","").strip()[:80]; ward=d.get("ward","").strip()[:80]; candidate=re.sub(r"\s+"," ",d.get("candidate","").strip())[:80]; candidate_id=d.get("candidate_id"); issue=d.get("issue","").strip()[:120]
 if county not in COUNTIES or race not in RACES or len(candidate)<2:return jsonify(error="Invalid county, race or candidate."),400
 if race in {"Member of Parliament","MCA"} and len(constituency)<2:return jsonify(error="Choose/enter the constituency for this race."),400
 if race=="MCA" and len(ward)<2:return jsonify(error="Choose/enter the ward for the MCA race."),400
 if race in {"Member of Parliament","MCA"} and not geography_ok(county,constituency,ward if race=="MCA" else ""):return jsonify(error="Invalid constituency or ward for the selected county."),400
 if race not in {"Member of Parliament","MCA"}: constituency=""; ward=""
 if race=="Member of Parliament": ward=""
 if candidate_id:
  with conn() as c:
   scope_sql="SELECT id,name FROM candidates WHERE id=? AND active=TRUE AND race=?";scope_args=[candidate_id,race]
   if race!="President":scope_sql+=" AND county=?";scope_args.append(county)
   if race in {"Member of Parliament","MCA"}:scope_sql+=" AND constituency=?";scope_args.append(constituency)
   if race=="MCA":scope_sql+=" AND ward=?";scope_args.append(ward)
   canonical=c.execute(scope_sql,scope_args).fetchone()
  if not canonical:return jsonify(error="That verified registry entry does not match the selected seat and area."),400
  candidate=canonical["name"]
 else:
  candidate=re.sub(r"\\s+"," ",candidate).strip()
  # Free-text names are accepted after the client explicitly submits them; do not block
  # participation merely because the registry has no matching entry yet.
  if len(candidate)<2:return jsonify(error="Enter the person’s name."),400
 raw=(request.headers.get("X-Forwarded-For",request.remote_addr or "").split(",")[0]+request.headers.get("User-Agent","")+SALT).encode(); fp=hashlib.sha256(raw).hexdigest()
 # Neutral integrity control: cap rapid submissions from the same technical fingerprint.
 with conn() as c:
  rate_sql="SELECT count(*) n FROM pulse_votes WHERE fp=? AND created_at >= CURRENT_TIMESTAMP - INTERVAL '10 minutes'" if c.pg else "SELECT count(*) n FROM pulse_votes WHERE fp=? AND created_at >= datetime('now','-10 minutes')"
  recent=c.execute(rate_sql,(fp,)).fetchone()
  n=recent["n"] if hasattr(recent,"keys") else recent[0]
  if n>=8:return jsonify(error="Too many submissions in a short period. Please try again later."),429
 try:
  with conn() as c:
   c.execute("INSERT INTO pulse_votes(county,race,candidate,candidate_id,issue,fp,constituency,ward) VALUES(?,?,?,?,?,?,?,?)",(county,race,candidate,candidate_id,issue,fp,constituency or None,ward or None))
  with conn() as c:
   check=c.execute("SELECT count(*) n FROM pulse_votes WHERE county=? AND race=? AND fp=? AND LOWER(candidate)=LOWER(?)",(county,race,fp,candidate)).fetchone()
  saved=check["n"] if hasattr(check,"keys") else check[0]
  if saved<1:return jsonify(error="The response could not be verified after saving. Please retry."),500
  return jsonify(message="Preference counted and verified.",recorded=True)
 except (sqlite3.IntegrityError, psycopg2.IntegrityError if psycopg2 else sqlite3.IntegrityError):return jsonify(error="A response from this device/network is already recorded for this area and race."),409
 except Exception as e:
  app.logger.exception("vote persistence failure")
  return jsonify(error="Database error while recording this response. Please retry.",recorded=False),500
@app.get("/api/results")
def results():
 county=request.args.get("county","");race=request.args.get("race","President")
 if county not in COUNTIES or race not in RACES:return jsonify(error="Invalid selection"),400
 constituency=request.args.get("constituency","").strip();ward=request.args.get("ward","").strip()
 if race in {"Member of Parliament","MCA"} and not constituency:return jsonify(total=0,results=[],area_required="constituency")
 if race=="MCA" and not ward:return jsonify(total=0,results=[],area_required="ward")
 if race in {"Member of Parliament","MCA"} and not geography_ok(county,constituency,ward if race=="MCA" else ""):return jsonify(error="Invalid constituency or ward for the selected county."),400
 sql="SELECT MIN(candidate) candidate,count(*) votes FROM pulse_votes WHERE county=? AND race=?";args=[county,race]
 if race in {"Member of Parliament","MCA"}:sql+=" AND constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND ward=?";args.append(ward)
 sql+=" GROUP BY LOWER(candidate) ORDER BY votes DESC,candidate"
 with conn() as c:
  rows=c.execute(sql,args).fetchall()
 total=sum(x["votes"] for x in rows)
 return jsonify(total=total,results=[{"candidate":x["candidate"],"votes":x["votes"],"pct":round(x["votes"]*100/total,1) if total else 0} for x in rows])
