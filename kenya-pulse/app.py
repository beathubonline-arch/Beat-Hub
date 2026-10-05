import os, hashlib, re, sqlite3, base64, json, hmac, urllib.request, urllib.error, urllib.parse, secrets, time, datetime as dt, threading, io, xml.etree.ElementTree as ET, email.utils, html as html_lib
try:
 import psycopg2
 import psycopg2.extras
except ImportError:
 psycopg2=None
from flask import Flask, request, jsonify, render_template_string, abort, redirect
from html.parser import HTMLParser
app=Flask(__name__)
DB=os.environ.get("PULSE_DB","/tmp/kenya-pulse.db")
DATABASE_URL=os.environ.get("DATABASE_URL","")
SALT=os.environ.get("PULSE_SALT","kenya-pulse")
ADMIN_KEY=os.environ.get("PULSE_ADMIN_KEY","")
PAYSTACK_SECRET_KEY=os.environ.get("PAYSTACK_SECRET_KEY","")
PAYSTACK_MODE=os.environ.get("PAYSTACK_MODE","").strip().lower()
def paystack_mode():
 if PAYSTACK_MODE in {"test","live"}:return PAYSTACK_MODE
 if PAYSTACK_SECRET_KEY.startswith("sk_live_"):return "live"
 if PAYSTACK_SECRET_KEY.startswith("sk_test_"):return "test"
 return "unconfigured"
def paystack_configured():
 return paystack_mode() in {"test","live"} and PAYSTACK_SECRET_KEY.startswith("sk_"+paystack_mode()+"_")
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
   c.execute("ALTER TABLE pulse_votes ADD COLUMN IF NOT EXISTS candidate_id BIGINT;")
   c.execute("ALTER TABLE pulse_votes ADD COLUMN IF NOT EXISTS submitted_candidate_text TEXT;")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS pulse_vote_unique ON pulse_votes(county,race,COALESCE(constituency,''),COALESCE(ward,''),fp);")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_lookup ON pulse_votes(county,race,constituency,ward);")
   c.execute("""CREATE TABLE IF NOT EXISTS pulse_visits(id BIGSERIAL PRIMARY KEY,county TEXT,source TEXT,path TEXT,session_id TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("ALTER TABLE pulse_visits ADD COLUMN IF NOT EXISTS path TEXT;");c.execute("ALTER TABLE pulse_visits ADD COLUMN IF NOT EXISTS session_id TEXT;")
   c.execute("CREATE INDEX IF NOT EXISTS pulse_visit_lookup ON pulse_visits(county,source);")
   c.execute("""CREATE TABLE IF NOT EXISTS ad_orders(id BIGSERIAL PRIMARY KEY,business TEXT NOT NULL,email TEXT NOT NULL,phone TEXT,scope TEXT NOT NULL,county TEXT,package TEXT NOT NULL,budget INTEGER NOT NULL,headline TEXT,url TEXT,status TEXT DEFAULT 'PENDING_REVIEW',starts_at TIMESTAMPTZ,ends_at TIMESTAMPTZ,impressions INTEGER DEFAULT 0,clicks INTEGER DEFAULT 0,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("ALTER TABLE ad_orders ADD COLUMN IF NOT EXISTS impression_goal INTEGER DEFAULT 0;")
   c.execute("ALTER TABLE ad_orders ADD COLUMN IF NOT EXISTS daily_impression_cap INTEGER DEFAULT 0;")
   c.execute("ALTER TABLE ad_orders ADD COLUMN IF NOT EXISTS frequency_cap INTEGER DEFAULT 3;")
   c.execute("ALTER TABLE ad_orders ADD COLUMN IF NOT EXISTS priority_weight REAL DEFAULT 0;")
   c.execute("ALTER TABLE ad_orders ADD COLUMN IF NOT EXISTS last_served_at TIMESTAMPTZ;")
   c.execute("""CREATE TABLE IF NOT EXISTS ad_impression_events(
    id BIGSERIAL PRIMARY KEY,
    ad_id BIGINT NOT NULL REFERENCES ad_orders(id) ON DELETE CASCADE,
    visitor_hash TEXT NOT NULL,
    slot TEXT NOT NULL,
    county TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
   );""")
   c.execute("CREATE INDEX IF NOT EXISTS ad_impression_recent ON ad_impression_events(visitor_hash,created_at,ad_id);")
   c.execute("CREATE INDEX IF NOT EXISTS ad_impression_daily ON ad_impression_events(ad_id,created_at);")
   c.execute("""CREATE TABLE IF NOT EXISTS county_notices(id BIGSERIAL PRIMARY KEY,county TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,summary TEXT,source_name TEXT NOT NULL,source_url TEXT NOT NULL,reference_no TEXT,published_at TIMESTAMPTZ,closes_at TIMESTAMPTZ,event_at TIMESTAMPTZ,status TEXT NOT NULL DEFAULT 'VERIFIED',created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE INDEX IF NOT EXISTS county_notice_lookup ON county_notices(county,category,status,closes_at,event_at);")
   c.execute("""CREATE TABLE IF NOT EXISTS candidates(id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL,race TEXT NOT NULL,county TEXT,constituency TEXT,ward TEXT,party TEXT,status TEXT NOT NULL DEFAULT 'PROSPECTIVE',source_url TEXT,active BOOLEAN DEFAULT TRUE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_scope_unique ON candidates(name,race,COALESCE(county,''),COALESCE(constituency,''),COALESCE(ward,''));")
   c.execute("""CREATE TABLE IF NOT EXISTS ground_issues(id BIGSERIAL PRIMARY KEY,county TEXT NOT NULL,constituency TEXT,ward TEXT,landmark TEXT,category TEXT NOT NULL,description TEXT NOT NULL,transcript TEXT,language TEXT,media_type TEXT,media_url TEXT,status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',confirmations INTEGER DEFAULT 1,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_aliases(id BIGSERIAL PRIMARY KEY,candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,alias TEXT NOT NULL,verified BOOLEAN DEFAULT FALSE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_alias_unique ON candidate_aliases(candidate_id,LOWER(alias));")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS photo_url TEXT")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS bio TEXT")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS campaign_url TEXT")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS public_contact TEXT")
   c.execute("""CREATE TABLE IF NOT EXISTS support_payments(id BIGSERIAL PRIMARY KEY,reference TEXT NOT NULL UNIQUE,email TEXT NOT NULL,amount_kes INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'KES',status TEXT NOT NULL DEFAULT 'INITIATED',paystack_transaction_id TEXT,channel TEXT,paid_at TIMESTAMPTZ,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE INDEX IF NOT EXISTS support_payment_status ON support_payments(status,created_at);")
   c.execute("ALTER TABLE support_payments ADD COLUMN IF NOT EXISTS county TEXT;")
   c.execute("ALTER TABLE support_payments ADD COLUMN IF NOT EXISTS paystack_domain TEXT;")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS support_payment_tx_unique ON support_payments(paystack_transaction_id) WHERE paystack_transaction_id IS NOT NULL;")
   c.execute("ALTER TABLE pulse_votes ADD COLUMN IF NOT EXISTS candidate_id BIGINT REFERENCES candidates(id);")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS photo_url TEXT;")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS bio TEXT;")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS campaign_url TEXT;")
   c.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS public_contact TEXT;")
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
   c.execute("""CREATE TABLE IF NOT EXISTS ad_impression_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_id INTEGER NOT NULL,
    visitor_hash TEXT NOT NULL,
    slot TEXT NOT NULL,
    county TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(ad_id) REFERENCES ad_orders(id) ON DELETE CASCADE
   );""")
   c.execute("CREATE INDEX IF NOT EXISTS ad_impression_recent ON ad_impression_events(visitor_hash,created_at,ad_id)")
   c.execute("CREATE INDEX IF NOT EXISTS ad_impression_daily ON ad_impression_events(ad_id,created_at)")
   c.execute("""CREATE TABLE IF NOT EXISTS county_notices(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,summary TEXT,source_name TEXT NOT NULL,source_url TEXT NOT NULL,reference_no TEXT,published_at TEXT,closes_at TEXT,event_at TEXT,status TEXT NOT NULL DEFAULT 'VERIFIED',created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE INDEX IF NOT EXISTS county_notice_lookup ON county_notices(county,category,status,closes_at,event_at);")
   c.execute("""CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,race TEXT NOT NULL,county TEXT,constituency TEXT,ward TEXT,party TEXT,status TEXT NOT NULL DEFAULT 'PROSPECTIVE',source_url TEXT,active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_scope_unique ON candidates(name,race,IFNULL(county,''),IFNULL(constituency,''),IFNULL(ward,''));")
   c.execute("""CREATE TABLE IF NOT EXISTS ground_issues(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,constituency TEXT,ward TEXT,landmark TEXT,category TEXT NOT NULL,description TEXT NOT NULL,transcript TEXT,language TEXT,media_type TEXT,media_url TEXT,status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',confirmations INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_aliases(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,alias TEXT NOT NULL,verified INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE);""")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_alias_unique ON candidate_aliases(candidate_id,LOWER(alias));")
   c.execute("""CREATE TABLE IF NOT EXISTS support_payments(id INTEGER PRIMARY KEY AUTOINCREMENT,reference TEXT NOT NULL UNIQUE,email TEXT NOT NULL,amount_kes INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'KES',status TEXT NOT NULL DEFAULT 'INITIATED',paystack_transaction_id TEXT,channel TEXT,paid_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
   spcols=[x[1] for x in c.execute("PRAGMA table_info(support_payments)").fetchall()]
   for col in ("county","paystack_domain"):
    if col not in spcols:c.execute("ALTER TABLE support_payments ADD COLUMN "+col+" TEXT")
   c.execute("CREATE UNIQUE INDEX IF NOT EXISTS support_payment_tx_unique ON support_payments(paystack_transaction_id) WHERE paystack_transaction_id IS NOT NULL")
   c.execute("CREATE INDEX IF NOT EXISTS support_payment_status ON support_payments(status,created_at);")
   vote_cols=[x[1] for x in c.execute("PRAGMA table_info(pulse_votes)").fetchall()]
   if "candidate_id" not in vote_cols:c.execute("ALTER TABLE pulse_votes ADD COLUMN candidate_id INTEGER")
   if "submitted_candidate_text" not in vote_cols:c.execute("ALTER TABLE pulse_votes ADD COLUMN submitted_candidate_text TEXT")
   for col,typ in [("starts_at","TEXT"),("ends_at","TEXT"),("impressions","INTEGER DEFAULT 0"),("clicks","INTEGER DEFAULT 0"),("impression_goal","INTEGER DEFAULT 0"),("daily_impression_cap","INTEGER DEFAULT 0"),("frequency_cap","INTEGER DEFAULT 3"),("priority_weight","REAL DEFAULT 0"),("last_served_at","TEXT")]:
    try:c.execute("ALTER TABLE ad_orders ADD COLUMN "+col+" "+typ)
    except sqlite3.OperationalError:pass
try:init()
except Exception as e: print("db init",e)

HOUSE_ADS=[
 {"business":"Mkulima AI","url":"https://mkulima-ai-whatsapp.onrender.com","headline":"Mkulima AI — practical farming help, market guidance and farmer support powered by AI.","weight":1.5},
 {"business":"Mizizi","url":"https://mizizi-family.onrender.com","headline":"Mizizi — preserve family stories, photos, voices and memories for generations.","weight":1.4},
 {"business":"OneBob","url":"https://myonebob.online","headline":"OneBob — simple online chama saving built for everyday Kenyan groups.","weight":1.3},
 {"business":"BeatHub","url":"https://mybeathub.com","headline":"Find your next beat on BeatHub — buy, sell and discover music at mybeathub.com","weight":1.2}
]
def seed_house_ads():
 try:
  with conn() as c:
   for ad in HOUSE_ADS:
    row=c.execute("""SELECT id FROM ad_orders
                     WHERE LOWER(business)=LOWER(?) AND url=? AND package='House Ad'
                     LIMIT 1""",(ad["business"],ad["url"])).fetchone()
    if row:
     c.execute("""UPDATE ad_orders
                  SET headline=?,scope='National',county=NULL,budget=0,status='ACTIVE',
                      starts_at=COALESCE(starts_at,CURRENT_TIMESTAMP),ends_at=NULL,
                      impression_goal=0,daily_impression_cap=0,frequency_cap=1000,priority_weight=?
                  WHERE id=?""",(ad["headline"],ad["weight"],row["id"]))
    else:
     c.execute("""INSERT INTO ad_orders(
                   business,email,phone,scope,county,package,budget,headline,url,status,starts_at,ends_at,
                   impressions,clicks,impression_goal,daily_impression_cap,frequency_cap,priority_weight
                  ) VALUES(?,?,?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP,NULL,0,0,0,0,1000,?)""",
               (ad["business"],"kenyapulse2026@gmail.com",None,"National",None,"House Ad",0,ad["headline"],ad["url"],ad["weight"]))
 except Exception:
  app.logger.exception("House ad seed failed")
seed_house_ads()

STARTER_PRESIDENTIAL_PROFILES=[
 {"name":"William Ruto","party":"United Democratic Alliance (UDA)","status":"ASPIRANT","source_url":"https://www.president.go.ke/administration/office-of-the-president/","photo_url":"https://commons.wikimedia.org/wiki/Special:Redirect/file/William%20Saomei%20Ruto%20official%20portrait.jpg","bio":"Incumbent President of Kenya since 2022. He is publicly pursuing re-election in the 2027 presidential election."},
 {"name":"Edwin Sifuna","party":"The Equitable Party (TEP)","status":"ASPIRANT","source_url":"https://www.the-star.co.ke/news/2026-10-04-sifuna-unveils-tep-as-political-vehicle-for-2027-presidential-bid","photo_url":"https://commons.wikimedia.org/wiki/Special:Redirect/file/Sifuna%20in%202024.jpg","bio":"Nairobi Senator and The Equitable Party leader. He was unveiled in October 2026 as the party's preferred presidential candidate for the 2027 election."}
]
def seed_starter_presidential_profiles():
 try:
  with conn() as c:
   for p in STARTER_PRESIDENTIAL_PROFILES:
    row=c.execute("SELECT id,status,photo_url,bio,source_url,party FROM candidates WHERE race='President' AND LOWER(name)=LOWER(?)",(p["name"],)).fetchone()
    if row:
     status=row["status"]
     if (status or "").upper() in {"PROSPECTIVE","UNKNOWN",""}:status="ASPIRANT"
     c.execute("""UPDATE candidates SET
                  party=?,
                  status=?,
                  source_url=?,
                  photo_url=?,
                  bio=?,
                  active=TRUE
                  WHERE id=?""",(p["party"],status,p["source_url"],p["photo_url"],p["bio"],row["id"]))
    else:
     c.execute("""INSERT INTO candidates(name,race,party,status,source_url,photo_url,bio,active)
                  VALUES(?,'President',?,?,?,?,?,TRUE)""",(p["name"],p["party"],p["status"],p["source_url"],p["photo_url"],p["bio"]))
 except Exception as e: app.logger.exception("starter presidential profile seed failed")
seed_starter_presidential_profiles()

PARLIAMENT_MP_DIRECTORY="https://www.parliament.go.ke/the-national-assembly/mps?field_parliament_value=2022&page={page}"
PARLIAMENT_SENATE_DIRECTORY="https://www.parliament.go.ke/index.php/the-senate/senators?field_parliament_value=2022&page={page}&title="
GOVERNOR_DIRECTORY="https://ilovekenya.org/governors"
OFFICEHOLDER_SYNC_STATE={"running":False,"last_run":None,"last_error":None,"counts":{}}

def _clean_html_text(v):
 v=re.sub(r"<[^>]+>"," ",v or "")
 return re.sub(r"\s+"," ",html_lib.unescape(v)).strip()

def _fetch_public_html(url,timeout=18):
 req=urllib.request.Request(url,headers={"User-Agent":"KenyaPulseAI/1.0 (+https://kenyapulse.online)"})
 with urllib.request.urlopen(req,timeout=timeout) as r:return r.read().decode("utf-8","ignore")

def _abs_url(base,url):
 return urllib.parse.urljoin(base,html_lib.unescape(url or "")) if url else ""

def _county_canonical(raw):
 n=re.sub(r"[^a-z]","",str(raw or "").lower())
 aliases={
  "nairobi":"Nairobi City","nairobicity":"Nairobi City","homabay":"Homa Bay","taitataveta":"Taita-Taveta",
  "tharakanithi":"Tharaka-Nithi","elgeyomarakwet":"Elgeyo-Marakwet","transnzoia":"Trans Nzoia",
  "muranga":"Murang'a"
 }
 if n in aliases:return aliases[n]
 for c in COUNTIES:
  if re.sub(r"[^a-z]","",c.lower())==n:return c
 return ""

def _constituency_canonical(county,raw):
 if county not in GEOGRAPHY:return ""
 n=re.sub(r"[^a-z0-9]","",str(raw or "").lower())
 for c in GEOGRAPHY[county]:
  if re.sub(r"[^a-z0-9]","",c.lower())==n:return c
 return ""

def _strip_title(name):
 name=_clean_html_text(name)
 name=re.sub(r"(?i)^\s*(hon\.?|sen\.?)\s*","",name)
 name=re.sub(r"(?i),?\s*(cbs|mp|sc|egm|phd)\b.*$","",name).strip(" ,")
 return re.sub(r"\s+"," ",name)

def _parse_directory_rows(html,base):
 rows=[]
 for row_html in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>",html):
  cells=re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>",row_html)
  if len(cells)<4:continue
  texts=[_clean_html_text(x) for x in cells]
  links=re.findall(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\']',row_html)
  imgs=re.findall(r'(?is)<img\b[^>]*(?:src|data-src)=["\']([^"\']+)["\']',row_html)
  href=_abs_url(base,links[-1]) if links else base
  photo=_abs_url(base,imgs[0]) if imgs else ""
  rows.append({"cells":texts,"source_url":href,"photo_url":photo})
 return rows

def _upsert_public_officeholder(c,name,race,county="",constituency="",ward="",party="",photo_url="",source_url=""):
 if not name or race not in RACES:return False
 sql="SELECT id FROM candidates WHERE LOWER(name)=LOWER(?) AND race=?";args=[name,race]
 if race!="President":sql+=" AND county=?";args.append(county)
 if race in {"Member of Parliament","MCA"}:sql+=" AND constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND ward=?";args.append(ward)
 row=c.execute(sql,args).fetchone()
 if row:
  c.execute("""UPDATE candidates SET party=COALESCE(NULLIF(?,''),party),status=CASE WHEN status IN ('IEBC_CLEARED','PARTY_NOMINEE','ASPIRANT') THEN status ELSE 'UNKNOWN' END,
               source_url=COALESCE(NULLIF(?,''),source_url),photo_url=COALESCE(NULLIF(?,''),photo_url),active=TRUE WHERE id=?""",
            (party,source_url,photo_url,row["id"]))
 else:
  c.execute("""INSERT INTO candidates(name,race,county,constituency,ward,party,status,source_url,photo_url,active)
               VALUES(?,?,?,?,?,?,'UNKNOWN',?,?,TRUE)""",(name,race,county or None,constituency or None,ward or None,party or None,source_url or None,photo_url or None))
 return True

def _sync_parliament_members():
 counts={"Senator":0,"Woman Representative":0,"Member of Parliament":0}
 with conn() as c:
  # Senate directory currently spans a small number of pages; stop after repeated empties.
  empty=0
  for page in range(0,10):
   url=PARLIAMENT_SENATE_DIRECTORY.format(page=page)
   try:rows=_parse_directory_rows(_fetch_public_html(url),url)
   except Exception:rows=[]
   elected=0
   for r in rows:
    cells=r["cells"]
    joined=" | ".join(cells)
    if "Elected" not in joined or "Nominated" in joined:continue
    name=_strip_title(cells[0]);county=_county_canonical(cells[2] if len(cells)>2 else "");party=cells[3] if len(cells)>3 else ""
    if not county or not name:continue
    if _upsert_public_officeholder(c,name,"Senator",county=county,party=party,photo_url=r["photo_url"],source_url=r["source_url"]):
     counts["Senator"]+=1;elected+=1
   empty=empty+1 if elected==0 else 0
   if empty>=2 and page>2:break

  empty=0
  for page in range(0,45):
   url=PARLIAMENT_MP_DIRECTORY.format(page=page)
   try:rows=_parse_directory_rows(_fetch_public_html(url),url)
   except Exception:rows=[]
   elected=0
   for r in rows:
    cells=r["cells"];joined=" | ".join(cells)
    if "Elected" not in joined or "Nominated" in joined:continue
    name=_strip_title(cells[0]);county=_county_canonical(cells[2] if len(cells)>2 else "")
    area=cells[3] if len(cells)>3 else "";party=cells[4] if len(cells)>4 else ""
    if not county or not name:continue
    area_county=_county_canonical(area)
    if area_county==county:
     race="Woman Representative";constituency=""
    else:
     race="Member of Parliament";constituency=_constituency_canonical(county,area)
     if not constituency:continue
    if _upsert_public_officeholder(c,name,race,county=county,constituency=constituency,party=party,photo_url=r["photo_url"],source_url=r["source_url"]):
     counts[race]+=1;elected+=1
   empty=empty+1 if elected==0 else 0
   if empty>=3 and page>30:break
 return counts

MCA_GAZETTE_URL="https://new.kenyalaw.org/akn/ke/officialGazette/gazette/2022-08-24/170/eng@2022-08-24/source"

def _ward_canonical(county,constituency,raw):
 if county not in GEOGRAPHY or constituency not in GEOGRAPHY[county]:return ""
 n=re.sub(r"[^a-z0-9]","",str(raw or "").lower())
 for w in GEOGRAPHY[county][constituency]:
  if re.sub(r"[^a-z0-9]","",w.lower())==n:return w
 return ""

def _cell(v):
 return re.sub(r"\s+"," ",str(v or "").replace("\n"," ")).strip()

def _sync_mca_gazette():
 count=0
 try:
  import pdfplumber
  req=urllib.request.Request(MCA_GAZETTE_URL,headers={"User-Agent":"KenyaPulseAI/1.0 (+https://kenyapulse.online)"})
  with urllib.request.urlopen(req,timeout=35) as r:payload=r.read()
  with pdfplumber.open(io.BytesIO(payload)) as pdf, conn() as c:
   for page in pdf.pages[2:]:
    for table in page.extract_tables() or []:
     for row in table or []:
      cells=[_cell(x) for x in (row or [])]
      if len(cells)<10:continue
      # Gazette MCA schedule columns: county code/name, constituency code/name,
      # ward code/name, surname, other names, party, abbreviation, votes.
      if not re.fullmatch(r"\d{1,3}",cells[0] or ""):continue
      county=_county_canonical(cells[1] if len(cells)>1 else "")
      constituency=_constituency_canonical(county,cells[3] if len(cells)>3 else "")
      ward=_ward_canonical(county,constituency,cells[5] if len(cells)>5 else "")
      surname=cells[6] if len(cells)>6 else "";other=cells[7] if len(cells)>7 else ""
      party=cells[8] if len(cells)>8 else ""
      name=re.sub(r"\s+"," ",(surname+" "+other).strip())
      if not county or not constituency or not ward or len(name)<3:continue
      if _upsert_public_officeholder(c,name,"MCA",county=county,constituency=constituency,ward=ward,party=party,source_url=MCA_GAZETTE_URL):
       count+=1
 except Exception as e:
  app.logger.exception("MCA Gazette sync failed")
 return count

def _sync_governors():
 count=0
 try:html=_fetch_public_html(GOVERNOR_DIRECTORY)
 except Exception:return 0
 # Current public directory cards expose governor name/county/profile image. Only accept records whose county resolves to our canonical list.
 cards=re.findall(r"(?is)<(?:article|div)\b[^>]*>(.*?)(?=</(?:article|div)>)",html)
 seen=set()
 with conn() as c:
  for card in cards:
   txt=_clean_html_text(card)
   county=next((x for x in COUNTIES if re.search(r"(?i)(?:^|\b)"+re.escape(x.replace("-","[- ]"))+r"(?:\b|$)",txt.replace("Nairobi City","Nairobi"))),None)
   if not county:continue
   # Prefer heading text as candidate name.
   heads=re.findall(r"(?is)<h[2-4]\b[^>]*>(.*?)</h[2-4]>",card)
   if not heads:continue
   name=_strip_title(heads[0])
   if not name or name.lower() in {"county governors","governors"} or (county,name.lower()) in seen:continue
   seen.add((county,name.lower()))
   imgs=re.findall(r'(?is)<img\b[^>]*(?:src|data-src)=["\']([^"\']+)["\']',card)
   links=re.findall(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\']',card)
   photo=_abs_url(GOVERNOR_DIRECTORY,imgs[0]) if imgs else ""
   source=_abs_url(GOVERNOR_DIRECTORY,links[0]) if links else GOVERNOR_DIRECTORY
   party=""
   for p in ["UDA","ODM","WDM","WIPER","ANC","UDM","DAP-K","JP","JUBILEE","FORD-K","UPA","IND"]:
    if re.search(r"(?i)\b"+re.escape(p)+r"\b",txt):party=p;break
   if _upsert_public_officeholder(c,name,"Governor",county=county,party=party,photo_url=photo,source_url=source):count+=1
 return count

def sync_current_officeholders():
 if OFFICEHOLDER_SYNC_STATE["running"]:return
 OFFICEHOLDER_SYNC_STATE["running"]=True
 try:
  counts=_sync_parliament_members()
  counts["Governor"]=_sync_governors()
  counts["MCA"]=_sync_mca_gazette()
  OFFICEHOLDER_SYNC_STATE.update({"last_run":dt.datetime.now(dt.timezone.utc).isoformat(),"last_error":None,"counts":counts})
  app.logger.info("officeholder sync complete %s",counts)
 except Exception as e:
  OFFICEHOLDER_SYNC_STATE.update({"last_run":dt.datetime.now(dt.timezone.utc).isoformat(),"last_error":str(e)[:500]})
  app.logger.exception("officeholder sync failed")
 finally:OFFICEHOLDER_SYNC_STATE["running"]=False

CURRENT_GOVERNOR_BASELINE={
 "Mombasa":"Abdulswamad Nassir","Kwale":"Fatuma Achani","Kilifi":"Gideon Mung'aro","Tana River":"Dhadho Godhana","Lamu":"Issa Abdallah Timamy",
 "Taita-Taveta":"Andrew Mwadime","Garissa":"Nathif Jama","Wajir":"Ahmed Abdullahi","Mandera":"Mohamed Adan Khalif","Marsabit":"Mohamud Ali",
 "Isiolo":"Abdi Hassan Guyo","Meru":"Isaac Mutuma","Tharaka-Nithi":"Muthomi Njuki","Embu":"Cecily Mbarire","Kitui":"Julius Malombe",
 "Machakos":"Wavinya Ndeti","Makueni":"Mutula Kilonzo","Nyandarua":"Moses Badilisha Kiarie","Nyeri":"Mutahi Kahiga","Kirinyaga":"Anne Waiguru",
 "Murang'a":"Irungu Kang'ata","Kiambu":"Kimani Wamatangi","Turkana":"Jeremiah Lomurkai","West Pokot":"Simon Kachapin","Samburu":"Jonathan Lati Leleliit",
 "Trans Nzoia":"George Natembeya","Uasin Gishu":"Jonathan Bii","Elgeyo-Marakwet":"Wisley Rotich","Nandi":"Stephen Sang","Baringo":"Benjamin Cheboi",
 "Laikipia":"Joshua Irungu","Nakuru":"Susan Kihika","Narok":"Patrick Ole Ntutu","Kajiado":"Joseph Ole Lenku","Kericho":"Erick Kipkoech Mutai",
 "Bomet":"Hillary Barchok","Kakamega":"Fernandes Barasa","Vihiga":"Wilber Ottichilo","Bungoma":"Ken Lusaka","Busia":"Paul Otuoma",
 "Siaya":"James Orengo","Kisumu":"Anyang' Nyong'o","Homa Bay":"Gladys Wanga","Migori":"Ochillo Ayacko","Kisii":"Simba Arati",
 "Nyamira":"Amos Nyaribo","Nairobi City":"Johnson Sakaja"
}
def seed_current_governor_baseline():
 src="https://ilovekenya.org/governors"
 try:
  with conn() as c:
   for county,name in CURRENT_GOVERNOR_BASELINE.items():
    _upsert_public_officeholder(c,name,"Governor",county=county,source_url=src)
 except Exception:app.logger.exception("governor baseline seed failed")
seed_current_governor_baseline()

def _start_officeholder_sync():
 try:threading.Thread(target=sync_current_officeholders,name="kp-officeholder-sync",daemon=True).start()
 except Exception:app.logger.exception("could not start officeholder sync")
_start_officeholder_sync()

@app.get("/api/officeholder-sync-status")
def officeholder_sync_status():
 with conn() as c:
  rows=c.execute("""SELECT race,COUNT(*) total,SUM(CASE WHEN photo_url IS NOT NULL AND TRIM(photo_url)<>'' THEN 1 ELSE 0 END) with_photo
                    FROM candidates WHERE active=TRUE GROUP BY race ORDER BY race""").fetchall()
 return jsonify(state=OFFICEHOLDER_SYNC_STATE,inventory=[dict(x) for x in rows]),200,{"Cache-Control":"no-store"}

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


PRESIDENT_PROFILE_FEE_KES=5000
COUNTY_PROFILE_FEE_KES=2000
MP_PROFILE_FEE_KES=1500
MCA_PROFILE_FEE_KES=500
PRESIDENT_PHOTO_PROFILE_LIMIT=20
def profile_fee_for_race(race):
 if race=="President":return PRESIDENT_PROFILE_FEE_KES
 if race=="Member of Parliament":return MP_PROFILE_FEE_KES
 if race=="MCA":return MCA_PROFILE_FEE_KES
 return COUNTY_PROFILE_FEE_KES
def ensure_profile_claim_schema():
 with conn() as c:
  if c.pg:
   c.execute("""CREATE TABLE IF NOT EXISTS aspirant_profile_claims(
    id BIGSERIAL PRIMARY KEY,
    reference TEXT UNIQUE,
    name TEXT NOT NULL,
    race TEXT NOT NULL,
    county TEXT,
    constituency TEXT,
    ward TEXT,
    party TEXT,
    bio TEXT,
    campaign_url TEXT,
    public_contact TEXT,
    photo_bytes BYTEA NOT NULL,
    photo_mime TEXT NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'UNPAID',
    verification_status TEXT NOT NULL DEFAULT 'PENDING_PAYMENT',
    paystack_transaction_id TEXT,
    submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMPTZ,
    reviewed_at TIMESTAMPTZ
   );""")
  else:
   c.execute("""CREATE TABLE IF NOT EXISTS aspirant_profile_claims(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT UNIQUE,
    name TEXT NOT NULL,
    race TEXT NOT NULL,
    county TEXT,
    constituency TEXT,
    ward TEXT,
    party TEXT,
    bio TEXT,
    campaign_url TEXT,
    public_contact TEXT,
    photo_bytes BLOB NOT NULL,
    photo_mime TEXT NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'UNPAID',
    verification_status TEXT NOT NULL DEFAULT 'PENDING_PAYMENT',
    paystack_transaction_id TEXT,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT,
    reviewed_at TEXT
   );""")
  c.execute("CREATE INDEX IF NOT EXISTS aspirant_claim_status ON aspirant_profile_claims(payment_status,verification_status,submitted_at);")

def verify_profile_payment(ref,d):
 ensure_profile_claim_schema()
 if not ref.startswith("kpasp-") or d.get("reference")!=ref or d.get("domain")!=paystack_mode():return False
 with conn() as c:
  row=c.execute("SELECT id,race,payment_status FROM aspirant_profile_claims WHERE reference=?",(ref,)).fetchone()
  if not row:return False
  try:amount=int(d.get("amount") or 0)
  except:amount=0
  expected_fee=profile_fee_for_race(row["race"])
  if d.get("status")!="success" or d.get("currency")!="KES" or amount!=expected_fee*100:return False
  tx=str(d.get("id") or "")[:80]
  if not tx:return False
  c.execute("""UPDATE aspirant_profile_claims
               SET payment_status='PAID',verification_status=CASE WHEN verification_status='PENDING_PAYMENT' THEN 'PENDING_REVIEW' ELSE verification_status END,
                   paystack_transaction_id=?,paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP)
               WHERE reference=?""",(tx,ref))
 return True

@app.get("/claim-profile/photo/<int:claim_id>")
def claim_profile_photo(claim_id):
 ensure_profile_claim_schema()
 with conn() as c:row=c.execute("SELECT photo_bytes,photo_mime FROM aspirant_profile_claims WHERE id=?",(claim_id,)).fetchone()
 if not row:return "",404
 return (bytes(row["photo_bytes"]),200,{"Content-Type":row["photo_mime"],"Cache-Control":"public, max-age=86400"})

@app.route("/claim-profile",methods=["GET","POST"])
def claim_profile():
 ensure_profile_claim_schema()
 if request.method=="POST":
  name=re.sub(r"\s+"," ",(request.form.get("name") or "").strip())[:120]
  race=(request.form.get("race") or "").strip();county=(request.form.get("county") or "").strip()
  constituency=(request.form.get("constituency") or "").strip()[:100];ward=(request.form.get("ward") or "").strip()[:100]
  party=re.sub(r"\s+"," ",(request.form.get("party") or "").strip())[:120]
  bio=re.sub(r"\s+"," ",(request.form.get("bio") or "").strip())[:900]
  campaign_url=(request.form.get("campaign_url") or "").strip()[:500]
  public_contact=re.sub(r"\s+"," ",(request.form.get("public_contact") or "").strip())[:180]
  photo=request.files.get("photo")
  if race not in RACES or len(name)<2:return "Enter a valid name and seat.",400
  if race!="President" and county not in COUNTIES:return "Choose a valid county.",400
  if race in {"Member of Parliament","MCA"} and (not constituency or not geography_ok(county,constituency,ward if race=="MCA" else "")):return "Choose a valid constituency"+(" and ward." if race=="MCA" else "."),400
  if not photo:return "Upload a profile photo.",400
  raw=photo.read(2_100_000)
  if not raw or len(raw)>2_000_000:return "Photo must be under 2 MB.",400
  mime=(photo.mimetype or "").lower()
  if mime not in {"image/jpeg","image/png","image/webp"}:return "Use JPG, PNG or WEBP.",400
  if campaign_url and not campaign_url.startswith(("https://","http://")):return "Campaign link must start with http:// or https://",400
  ref="kpasp-"+secrets.token_hex(10)
  with conn() as c:
   if c.pg:
    row=c.execute("""INSERT INTO aspirant_profile_claims(reference,name,race,county,constituency,ward,party,bio,campaign_url,public_contact,photo_bytes,photo_mime)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",(ref,name,race,county or None,constituency or None,ward or None,party or None,bio or None,campaign_url or None,public_contact or None,psycopg2.Binary(raw),mime)).fetchone()
    claim_id=row["id"]
   else:
    cur=c.execute("""INSERT INTO aspirant_profile_claims(reference,name,race,county,constituency,ward,party,bio,campaign_url,public_contact,photo_bytes,photo_mime)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(ref,name,race,county or None,constituency or None,ward or None,party or None,bio or None,campaign_url or None,public_contact or None,raw,mime))
    claim_id=cur.lastrowid
  if not paystack_configured():return redirect("/claim-profile/status?reference="+urllib.parse.quote(ref))
  email=(request.form.get("email") or "").strip()[:160]
  if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):return "Enter a valid email for the payment receipt.",400
  fee=profile_fee_for_race(race)
  payload={"email":email,"amount":str(fee*100),"currency":"KES","reference":ref,"callback_url":request.url_root.rstrip("/")+"/claim-profile/callback","channels":["mobile_money","card"],"metadata":{"purpose":"candidate_profile_activation","claim_id":claim_id,"candidate_name":name,"race":race,"profile_fee_kes":fee,"does_not_buy_ballot_rank":True}}
  try:
   out=paystack_request("/transaction/initialize",payload);url=(out.get("data") or {}).get("authorization_url","")
   if not url.startswith("https://checkout.paystack.com/"):raise RuntimeError("invalid checkout")
   return redirect(url)
  except Exception:return redirect("/claim-profile/status?reference="+urllib.parse.quote(ref))
 page="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Claim your profile · Kenya Pulse AI</title><link rel=stylesheet href=/pulse95.css><style>
 *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% 8%,#1d7b4a44,transparent 26%),radial-gradient(circle at 88% 4%,#d4a90022,transparent 22%),#06150e;color:#f5fff8;font-family:Inter,system-ui}.top{max-width:1080px;margin:18px auto 0;padding:0 18px}.nav{display:flex;align-items:center;gap:18px;padding:16px 20px;border-radius:22px}.nav .brand{font-weight:950;margin-right:auto}.nav .brand b{color:#ffd54a}.nav a{color:#e9f6ed;text-decoration:none;font-size:13px}.w{max-width:1080px;margin:auto;padding:24px 18px 70px}.hero{padding:30px;border-radius:28px;margin-bottom:16px}.hero h1{font-size:clamp(38px,6vw,64px);line-height:.98;margin:8px 0 14px;letter-spacing:-2px}.hero p{max-width:760px}.card{padding:28px;border-radius:28px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}input,select,textarea,button{width:100%;padding:14px;border-radius:16px;border:1px solid #ffffff22;background:#ffffff0b;color:white;font:inherit}select{min-height:52px}textarea{min-height:120px}button{background:#69ef91;color:#0a2a18;font-weight:900;border:0;cursor:pointer}.muted{color:#a9c6b4}.fee{font-size:34px;font-weight:950;color:#ffd54a}.notice{padding:14px 16px;border-radius:16px;background:#ffffff0b;border:1px solid #ffffff1f;margin:14px 0}.ey{font-size:11px;font-weight:850;letter-spacing:.14em;color:#baf5ca;text-transform:uppercase}@media(max-width:650px){.grid{grid-template-columns:1fr}.nav{flex-wrap:wrap}}
 </style></head><body><div class=top><nav class="glass nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/growth">Home</a><a href="/participate">Take part</a><a href="/candidate-explorer">Candidate Explorer</a></nav></div><div class=w><section class="glass hero"><div class=ey>CANDIDATE PROFILE STUDIO</div><h1>Claim or create your public candidate profile.</h1><p class=muted>Submit your photo and public details for review. This profile service is separate from voting results and does not buy votes, ranking, or preferential placement.</p></section><div class="glass card"><div class=notice><b>Profile review fees</b><div class=muted>President KSh 5,000 · Governor/Senator/Woman Rep KSh 2,000 · MP KSh 1,500 · MCA KSh 500.</div></div><p class=muted>Payment covers profile activation and review only. Candidate eligibility and ballot inclusion remain subject to verification and the platform's neutral listing rules.</p><div class=fee id=profileFee>President: KSh 5,000</div><p class=muted style="margin-top:6px">Governor, Senator & Woman Rep: KSh 2,000 · MP: KSh 1,500 · MCA: KSh 500</p>
 <form method=post enctype=multipart/form-data><div class=grid><input name=name required placeholder="Full name"><input name=email type=email required placeholder="Email for receipt"></div><div class=grid><select name=race id=raceSelect required onchange="updateProfileFee()">{% for r in races %}<option>{{r}}</option>{% endfor %}</select><select name=county><option value="">County (not needed for President)</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select></div><div class=grid><input name=constituency placeholder="Constituency (MP/MCA)"><input name=ward placeholder="Ward (MCA)"></div><div class=grid><input name=party placeholder="Party / Independent"><input name=public_contact placeholder="Public contact / campaign phone"></div><input name=campaign_url placeholder="Campaign website or social profile" style="margin-top:10px"><textarea name=bio placeholder="Short public bio, priorities and experience" style="margin-top:10px"></textarea><label style="display:block;margin-top:12px">Profile photo (JPG, PNG or WEBP, max 2 MB)<input name=photo type=file accept="image/jpeg,image/png,image/webp" required></label><button style="margin-top:14px">Continue to payment →</button></form>
 <p class=muted style="margin-top:14px;font-size:12px">Profiles are reviewed before publication. Kenya Pulse AI may independently add sourced public-record information alongside candidate-submitted information.</p></div></div><script>
function updateProfileFee(){
 const r=document.getElementById('raceSelect').value;
 const fee=r==='President'?5000:(r==='Member of Parliament'?1500:(r==='MCA'?500:2000));
 document.getElementById('profileFee').textContent=r+': KSh '+fee.toLocaleString();
}
document.addEventListener('DOMContentLoaded',updateProfileFee);
</script></body></html>"""
 return render_template_string(page,races=RACES,counties=COUNTIES)

@app.get("/claim-profile/callback")
def claim_profile_callback():
 ref=(request.args.get("reference") or "")[:100]
 if not ref.startswith("kpasp-"):return redirect("/claim-profile")
 try:
  d=(paystack_request("/transaction/verify/"+ref).get("data") or {});verify_profile_payment(ref,d)
 except Exception:pass
 return redirect("/claim-profile/status?reference="+urllib.parse.quote(ref))

@app.get("/claim-profile/status")
def claim_profile_status():
 ensure_profile_claim_schema();ref=(request.args.get("reference") or "")[:100]
 with conn() as c:row=c.execute("SELECT id,name,race,payment_status,verification_status FROM aspirant_profile_claims WHERE reference=?",(ref,)).fetchone()
 if not row:return "Profile claim not found.",404
 return render_template_string("""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Profile status · Kenya Pulse AI</title><link rel=stylesheet href=/pulse95.css></head><body><main class="kp95wrap" style="max-width:760px;margin:auto;padding:60px 20px"><section class="glass kp95panel" style="padding:28px"><h1>Profile submitted</h1><p><b>{{name}}</b> · {{race}}</p><p>Payment: <b>{{payment}}</b></p><p>Review: <b>{{status}}</b></p><p>Your profile will appear publicly only after review and approval.</p><a href="/">Return to Kenya Pulse AI</a></section></main></body></html>""",name=row["name"],race=row["race"],payment=row["payment_status"],status=row["verification_status"])

@app.get("/api/admin/profile-claims")
def admin_profile_claims():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_profile_claim_schema()
 with conn() as c:rows=c.execute("""SELECT id,reference,name,race,county,constituency,ward,party,bio,campaign_url,public_contact,payment_status,verification_status,submitted_at,paid_at
                                    FROM aspirant_profile_claims ORDER BY id DESC LIMIT 100""").fetchall()
 return jsonify(claims=[dict(x) for x in rows])

@app.post("/api/admin/profile-claims/<int:claim_id>/review")
def admin_profile_claim_review(claim_id):
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_profile_claim_schema();d=request.get_json(silent=True) or {};decision=(d.get("decision") or "").strip().upper()
 if decision not in {"APPROVE","REJECT"}:return jsonify(error="decision must be APPROVE or REJECT"),400
 with conn() as c:
  row=c.execute("SELECT * FROM aspirant_profile_claims WHERE id=?",(claim_id,)).fetchone()
  if not row:return jsonify(error="Claim not found"),404
  if row["payment_status"]!="PAID":return jsonify(error="Profile fee has not been verified"),409
  if decision=="REJECT":
   c.execute("UPDATE aspirant_profile_claims SET verification_status='REJECTED',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(claim_id,))
   return jsonify(updated=True,status="REJECTED")
  existing_sql="SELECT id FROM candidates WHERE LOWER(name)=LOWER(?) AND race=?";args=[row["name"],row["race"]]
  if row["race"]!="President":existing_sql+=" AND county=?";args.append(row["county"])
  if row["race"] in {"Member of Parliament","MCA"}:existing_sql+=" AND constituency=?";args.append(row["constituency"])
  if row["race"]=="MCA":existing_sql+=" AND ward=?";args.append(row["ward"])
  existing=c.execute(existing_sql,args).fetchone()
  photo_url=request.url_root.rstrip("/")+"/claim-profile/photo/"+str(claim_id)
  if existing:
   c.execute("""UPDATE candidates SET party=?,photo_url=?,bio=?,campaign_url=?,public_contact=?,active=TRUE WHERE id=?""",
             (row["party"],photo_url,row["bio"],row["campaign_url"],row["public_contact"],existing["id"]))
   candidate_id=existing["id"]
  else:
   if c.pg:
    created=c.execute("""INSERT INTO candidates(name,race,county,constituency,ward,party,status,photo_url,bio,campaign_url,public_contact,active)
                         VALUES(?,?,?,?,?,?, 'ASPIRANT',?,?,?,?,TRUE) RETURNING id""",
                      (row["name"],row["race"],row["county"],row["constituency"],row["ward"],row["party"],photo_url,row["bio"],row["campaign_url"],row["public_contact"])).fetchone()
    candidate_id=created["id"]
   else:
    cur=c.execute("""INSERT INTO candidates(name,race,county,constituency,ward,party,status,photo_url,bio,campaign_url,public_contact,active)
                     VALUES(?,?,?,?,?,?, 'ASPIRANT',?,?,?,?,1)""",
                  (row["name"],row["race"],row["county"],row["constituency"],row["ward"],row["party"],photo_url,row["bio"],row["campaign_url"],row["public_contact"]))
    candidate_id=cur.lastrowid
  c.execute("UPDATE aspirant_profile_claims SET verification_status='APPROVED',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(claim_id,))
 return jsonify(updated=True,status="APPROVED",candidate_id=candidate_id,photo_url=photo_url)


@app.get("/api/support/config")
def support_config():
 mode=paystack_mode()
 return jsonify(configured=paystack_configured(),mode=mode if mode in {"test","live"} else "unconfigured",currency="KES",minimum_kes=5,webhook_url=request.url_root.rstrip("/")+"/api/paystack/webhook",callback_url=request.url_root.rstrip("/")+"/support/callback",async_status_check=True,reconciliation=True),200,{"Cache-Control":"no-store"}

@app.post("/api/support/initialize")
def support_initialize():
 body=request.get_json(silent=True) or {}; email=str(body.get("email","")).strip()[:160]
 try: amount=int(body.get("amount",0))
 except: amount=0
 if amount<5 or amount>1000000:return jsonify(error="Support starts from KSh 5."),400
 if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):return jsonify(error="Enter a valid email for the payment receipt."),400
 if not paystack_configured():return jsonify(error="Paystack checkout is not configured. Participation remains free."),503
 county=str(body.get("county", ""))
 with conn() as db:
  rows=db.execute("SELECT DISTINCT race FROM pulse_votes WHERE county=? AND fp=?",(county,participation_fingerprint())).fetchall()
 if not set(RACES).issubset({x["race"] for x in rows}):return jsonify(error="Complete all six seats before optional support."),409
 ref="kp-"+secrets.token_hex(10); origin=request.url_root.rstrip("/")
 with conn() as db: db.execute("INSERT INTO support_payments(reference,email,amount_kes,currency,status,county,paystack_domain) VALUES(?,?,?,?,?,?,?)",(ref,email,amount,"KES","INITIATED",county,paystack_mode()))
 payload={"email":email,"amount":str(amount*100),"currency":"KES","reference":ref,"callback_url":origin+"/support/callback","channels":["mobile_money","card"],"metadata":{"purpose":"optional_support","separate_from_participation":True,"county":county,"mode":paystack_mode()}}
 try:
  out=paystack_request("/transaction/initialize",payload); d=out.get("data") or {}
  if not str(d.get("authorization_url", "")).startswith("https://checkout.paystack.com/"): raise RuntimeError("invalid checkout url")
  return jsonify(authorization_url=d.get("authorization_url"),reference=ref)
 except Exception:
  with conn() as db: db.execute("UPDATE support_payments SET status='INIT_FAILED',updated_at=CURRENT_TIMESTAMP WHERE reference=?",(ref,))
  return jsonify(error="Could not start payment. Please try again."),502

def record_support_verification(ref,d):
 mode=paystack_mode()
 if mode not in {"test","live"} or not ref or d.get("reference")!=ref or d.get("domain")!=mode:return False
 with conn() as db:
  row=db.execute("SELECT reference,amount_kes,currency,status,paystack_domain FROM support_payments WHERE reference=?",(ref,)).fetchone()
  if not row:return False
  try:got=int(d.get("amount") or 0)
  except:got=0
  if d.get("status")!="success" or d.get("currency")!=row["currency"] or got!=int(row["amount_kes"])*100:return False
  if row["paystack_domain"] and row["paystack_domain"]!=mode:return False
  tx=str(d.get("id") or "")[:80];channel=str(d.get("channel") or "")[:40]
  if not tx:return False
  dup=db.execute("SELECT reference FROM support_payments WHERE paystack_transaction_id=? AND reference<>?",(tx,ref)).fetchone()
  if dup:return False
  db.execute("UPDATE support_payments SET status='SUCCESS',paystack_transaction_id=?,channel=?,paystack_domain=?,paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE reference=? AND status<>'SUCCESS'",(tx,channel,mode,ref))
 return True

@app.get("/api/support/status")
def support_status():
 ref=(request.args.get("reference") or "")[:100]
 if not re.fullmatch(r"kp-[a-f0-9]{20}",ref):return jsonify(error="Invalid reference"),400
 with conn() as db:
  row=db.execute("SELECT status,amount_kes,currency,county,channel,paid_at FROM support_payments WHERE reference=?",(ref,)).fetchone()
 if not row:return jsonify(error="Payment not found"),404
 status=row["status"]
 if status!="SUCCESS" and paystack_configured():
  try:
   d=(paystack_request("/transaction/verify/"+ref).get("data") or {})
   if record_support_verification(ref,d):status="SUCCESS"
  except Exception:pass
 with conn() as db:
  row=db.execute("SELECT status,amount_kes,currency,county,channel,paid_at FROM support_payments WHERE reference=?",(ref,)).fetchone()
 return jsonify(reference=ref,status=row["status"],amount_kes=row["amount_kes"],currency=row["currency"],county=row["county"],channel=row["channel"],paid_at=str(row["paid_at"]) if row["paid_at"] else None),200,{"Cache-Control":"no-store"}

@app.get("/admin/support-payments")
def admin_support_payments():
 if not admin_authorized():abort(404)
 with conn() as db:
  rows=[dict(x) for x in db.execute("SELECT reference,email,amount_kes,currency,status,county,paystack_domain,channel,paid_at,created_at,updated_at FROM support_payments ORDER BY id DESC LIMIT 100").fetchall()]
 return jsonify(payments=rows,mode=paystack_mode())

@app.post("/admin/support-payments/<ref>/reconcile")
def admin_support_reconcile(ref):
 if not admin_authorized():abort(404)
 if not re.fullmatch(r"kp-[a-f0-9]{20}",ref):return jsonify(error="Invalid reference"),400
 try:
  d=(paystack_request("/transaction/verify/"+ref).get("data") or {})
  ok=record_support_verification(ref,d)
  with conn() as db:row=db.execute("SELECT status,amount_kes,currency,county,channel,paid_at FROM support_payments WHERE reference=?",(ref,)).fetchone()
  if not row:return jsonify(error="Payment not found"),404
  return jsonify(ok=ok,payment=dict(row)),200
 except Exception:
  app.logger.exception("support reconciliation failed")
  return jsonify(error="Reconciliation failed"),502

@app.get("/support/callback")
def support_callback():
 ref=request.args.get("reference","")[:100]
 if not re.fullmatch(r"kp-[a-f0-9]{20}",ref):return redirect("/?support=missing")
 target="/"
 try:
  with conn() as db:
   row=db.execute("SELECT county FROM support_payments WHERE reference=?",(ref,)).fetchone()
  if row and row["county"] in COUNTIES:
   target="/county/"+re.sub(r"[^a-z0-9]+","-",row["county"].lower()).strip("-")
  d=(paystack_request("/transaction/verify/"+ref).get("data") or {});ok=record_support_verification(ref,d)
  return redirect(target+"?support="+("success" if ok else "pending")+"&reference="+urllib.parse.quote(ref))
 except Exception:return redirect(target+"?support=pending&reference="+urllib.parse.quote(ref))

@app.post("/api/paystack/webhook")
def paystack_webhook():
 if not paystack_configured():return "",503
 raw=request.get_data(); sig=request.headers.get("x-paystack-signature",""); expected=hmac.new(PAYSTACK_SECRET_KEY.encode(),raw,hashlib.sha512).hexdigest()
 if not hmac.compare_digest(sig,expected):return "",401
 event=request.get_json(silent=True) or {}
 if event.get("event")=="charge.success":
  d=event.get("data") or {};ref=str(d.get("reference") or "")[:100]
  if ref.startswith("kpasp-"):verify_profile_payment(ref,d)
  else:record_support_verification(ref,d)
 return "",200

PULSE_95_CSS=r'''*{box-sizing:border-box}body{margin:0;color:#f8fff9;font-family:Inter,ui-sans-serif,system-ui;background:#0b2f1d;min-height:100vh}.world{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 84% 12%,#ffe8a1 0 1.8%,#ffd9894d 2% 8%,transparent 17%),linear-gradient(180deg,#82aea8 0 23%,#72987b 37%,#2c7045 65%,#0b3c24 100%);transform:scale(1.015);filter:saturate(1.08) contrast(1.025)}.world:before{content:'';position:absolute;inset:23% -8% -8%;background:radial-gradient(ellipse at 64% 40%,rgba(194,224,190,.32),transparent 24%),linear-gradient(158deg,transparent 0 15%,#63885b 15.4% 27%,transparent 27.4%),linear-gradient(24deg,transparent 0 23%,#39774a 23.4% 50%,transparent 50.4%),linear-gradient(158deg,transparent 0 43%,#145a34 43.4% 70%,transparent 70.4%);filter:blur(.8px);opacity:.96}.world:after{content:'';position:absolute;inset:52% -4% -4%;background:radial-gradient(ellipse at 63% 8%,rgba(173,210,184,.42),transparent 26%),linear-gradient(8deg,#0b4227 0 46%,transparent 46.5%),linear-gradient(-8deg,#216a3d 0 57%,transparent 57.5%);filter:blur(1.2px);opacity:.98}.shade{position:fixed;inset:0;z-index:-2;background:linear-gradient(90deg,rgba(4,30,17,.70),transparent 48%),linear-gradient(0deg,rgba(3,28,15,.62),transparent 42%)}body:after{content:'';position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(ellipse at 50% 48%,transparent 45%,rgba(3,24,13,.22) 100%);mix-blend-mode:multiply}.glass{background:linear-gradient(135deg,rgba(20,55,39,.58),rgba(39,73,56,.34));border:1px solid rgba(240,255,245,.34);box-shadow:inset 0 1px rgba(255,255,255,.38),inset 0 -1px rgba(255,255,255,.06),0 18px 48px rgba(3,25,14,.20);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.kp95nav{max-width:1380px;width:calc(100% - 48px);height:66px;margin:18px auto 0;border-radius:24px;padding:0 20px;display:flex;align-items:center;gap:26px;background:linear-gradient(120deg,rgba(25,58,43,.54),rgba(44,76,60,.31));border:1px solid rgba(244,255,247,.32);box-shadow:inset 0 1px rgba(255,255,255,.40),0 16px 42px rgba(3,24,13,.16);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.kp95nav .brand{font-size:21px;font-weight:950;letter-spacing:-.7px;margin-right:auto}.kp95nav .brand b{color:#7cf39d}.kp95nav a{color:#edf8f1;text-decoration:none;font-size:13px}.kp95wrap{max-width:1380px;margin:auto;padding:16px 24px 70px}.kp95panel{border-radius:30px;padding:28px}.kp95title{font-size:clamp(52px,6.3vw,88px);line-height:.91;letter-spacing:-4px}.kp95accent{color:#75f59b}.kp95muted{color:#bdd1c4}.kp95mark{font-size:30px;filter:drop-shadow(0 10px 20px rgba(3,25,14,.25))}.kp95btn{display:inline-flex;padding:14px 20px;border:0;border-radius:15px;background:#69ef91;color:#092817;font-weight:900;text-decoration:none;box-shadow:0 10px 28px rgba(30,205,92,.18)}@media(max-width:900px){.kp95nav{width:calc(100% - 24px);margin-top:10px}.kp95nav a{display:none}.kp95wrap{padding:10px 12px 50px}}@media(max-width:560px){.kp95nav{height:60px;border-radius:20px}.kp95panel{padding:19px;border-radius:24px}.kp95title{font-size:49px;letter-spacing:-2.7px}}
/* Kenya Pulse AI 10/10 unified interface */
html{scroll-behavior:smooth}body{overflow-x:hidden}body .brand b{color:#69ef91!important}
.kp95nav{position:sticky;top:12px;z-index:100;min-height:66px}
.kp95nav:before{content:'⌂';width:34px;height:34px;border-radius:12px;display:grid;place-items:center;background:rgba(105,239,145,.13);color:#93ffb2;font-size:16px;order:-1}
.kp95nav a{padding:9px 12px;border-radius:12px;transition:.18s ease}
.kp95nav a:hover{background:rgba(255,255,255,.1);color:#fff}
.kp95panel,.glass{position:relative}
.kp10-page{max-width:1460px;margin:auto;padding:22px 28px 70px}
.kp10-hero{padding:28px 32px;border-radius:30px;margin:18px 0;overflow:hidden}
.kp10-hero:after{content:'';position:absolute;width:280px;height:280px;right:-90px;top:-120px;border-radius:50%;background:radial-gradient(circle,rgba(105,239,145,.22),transparent 68%);pointer-events:none}
.kp10-kicker{font:900 11px ui-monospace,monospace;letter-spacing:.15em;color:#a8f7be;text-transform:uppercase}
.kp10-title{font-size:clamp(38px,5vw,68px);line-height:.98;letter-spacing:-2.8px;margin:9px 0 12px}
.kp10-lead{max-width:850px;color:#d6eadc;line-height:1.6}
.kp10-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.kp10-card{border-radius:26px;padding:22px;transition:transform .18s ease,border-color .18s ease}
.kp10-card:hover{transform:translateY(-2px);border-color:rgba(255,255,255,.46)}
.kp10-icon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(145deg,rgba(105,239,145,.34),rgba(105,239,145,.10));border:1px solid rgba(161,255,190,.32);box-shadow:inset 0 1px rgba(255,255,255,.38)}
.kp10-label{font-size:13px;font-weight:900;color:#effff3}.kp10-sub{font-size:12px;color:#b8d1c0}
select,input,textarea{font-family:inherit}
select{
 appearance:none;-webkit-appearance:none;
 min-height:52px;
 padding:0 48px 0 16px!important;
 border-radius:16px!important;
 font-weight:750!important;
 letter-spacing:.01em;
 cursor:pointer;
 background-color:rgba(13,54,36,.82)!important;
 background-image:
  linear-gradient(45deg,transparent 50%,#9dffbc 50%),
  linear-gradient(135deg,#9dffbc 50%,transparent 50%),
  linear-gradient(135deg,rgba(255,255,255,.08),rgba(255,255,255,.015))!important;
 background-position:
  calc(100% - 22px) 50%,
  calc(100% - 15px) 50%,
  0 0!important;
 background-size:7px 7px,7px 7px,100% 100%!important;
 background-repeat:no-repeat!important;
 border:1px solid rgba(181,255,204,.42)!important;
 color:#f8fff9!important;
 box-shadow:inset 0 1px rgba(255,255,255,.20),0 10px 28px rgba(0,0,0,.10)!important;
 transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease,background-color .18s ease;
}
select:hover{border-color:rgba(137,255,174,.72)!important;background-color:rgba(17,68,45,.92)!important;transform:translateY(-1px)}
select:focus{outline:0!important;border-color:#69ef91!important;box-shadow:0 0 0 4px rgba(105,239,145,.15),inset 0 1px rgba(255,255,255,.22),0 14px 34px rgba(0,0,0,.15)!important}
select:disabled{opacity:.48;cursor:not-allowed;transform:none}
select option{background:#f4fbf6;color:#12351f;font-weight:700;padding:10px}
select option:checked{background:#c9f5d5;color:#12351f}
select,input,textarea{font-family:inherit;border:1px solid rgba(237,255,243,.30)!important;background-color:rgba(16,59,40,.58)!important;color:#f8fff9!important;box-shadow:inset 0 1px rgba(255,255,255,.12)!important}
input:focus,textarea:focus{outline:0!important;border-color:#69ef91!important;box-shadow:0 0 0 3px rgba(105,239,145,.14),inset 0 1px rgba(255,255,255,.14)!important}
button,.kp95btn,.btn{transition:.18s ease}
button:hover,.kp95btn:hover,.btn:hover{transform:translateY(-1px)}
@media(max-width:900px){.kp10-page{padding:12px 14px 50px}.kp10-grid{grid-template-columns:1fr 1fr}.kp95nav:before{display:none}}
@media(max-width:620px){.kp10-grid{grid-template-columns:1fr}.kp10-hero{padding:22px}.kp10-title{letter-spacing:-1.8px}}
'''
@app.get("/pulse95.css")
def pulse95_css():
 return PULSE_95_CSS,200,{"Content-Type":"text/css; charset=utf-8","Cache-Control":"public, max-age=300"}

PULSE_95_JS=r'''(()=>{const STYLE=\`.kpSelectWrap{position:relative;width:100%;font-family:inherit}.kpSelectNative{position:absolute!important;opacity:0!important;pointer-events:none!important;width:1px!important;height:1px!important}.kpSelectBtn{width:100%;min-height:52px;padding:0 48px 0 16px!important;border-radius:16px!important;border:1px solid rgba(181,255,204,.42)!important;background:linear-gradient(145deg,rgba(19,70,47,.96),rgba(10,45,30,.96))!important;color:#f8fff9!important;text-align:left;font:750 14px/1.2 Inter,ui-sans-serif,system-ui!important;box-shadow:inset 0 1px rgba(255,255,255,.18),0 10px 28px rgba(0,0,0,.16)!important;position:relative;cursor:pointer}.kpSelectBtn:after{content:"⌄";position:absolute;right:17px;top:50%;transform:translateY(-54%);font-size:20px;color:#8cffad;transition:.18s}.kpSelectWrap.open .kpSelectBtn{border-color:#69ef91!important;box-shadow:0 0 0 4px rgba(105,239,145,.13),0 18px 42px rgba(0,0,0,.24)!important}.kpSelectWrap.open .kpSelectBtn:after{transform:translateY(-40%) rotate(180deg)}.kpSelectMenu{display:none;position:absolute;left:0;right:0;top:calc(100% + 8px);z-index:9999;padding:8px;border-radius:18px;border:1px solid rgba(152,255,185,.45);background:rgba(238,249,241,.985);box-shadow:0 24px 70px rgba(0,0,0,.34);max-height:310px;overflow:auto;backdrop-filter:blur(22px)}.kpSelectWrap.open .kpSelectMenu{display:block}.kpSelectSearch{width:100%;position:sticky;top:0;z-index:2;padding:11px 12px!important;margin:0 0 6px;border:0!important;border-radius:11px!important;background:#e6f2e9!important;color:#143620!important;box-shadow:none!important}.kpSelectOpt{display:flex!important;align-items:center!important;width:100%!important;min-height:40px!important;padding:9px 11px!important;border:0!important;border-radius:11px!important;background:transparent!important;color:#173923!important;text-align:left!important;font:700 14px/1.2 Inter,ui-sans-serif,system-ui!important;box-shadow:none!important;transform:none!important;cursor:pointer}.kpSelectOpt:hover,.kpSelectOpt.sel{background:#c9f5d5!important;color:#0d321b!important}.kpSelectEmpty{padding:12px;color:#63806c;font-size:12px}@media(max-width:600px){.kpSelectMenu{max-height:270px}}\`;function addStyle(){if(document.getElementById("kpSelectStyle"))return;let x=document.createElement("style");x.id="kpSelectStyle";x.textContent=STYLE;document.head.appendChild(x)}function closeAll(ex){document.querySelectorAll(".kpSelectWrap.open").forEach(w=>{if(w!==ex)w.classList.remove("open")})}function enhance(sel){if(sel.dataset.kpEnhanced||sel.multiple||Number(sel.size)>1)return;sel.dataset.kpEnhanced="1";addStyle();const w=document.createElement("div");w.className="kpSelectWrap";sel.parentNode.insertBefore(w,sel);w.appendChild(sel);sel.classList.add("kpSelectNative");const b=document.createElement("button");b.type="button";b.className="kpSelectBtn";const m=document.createElement("div");m.className="kpSelectMenu";const searchable=sel.options.length>8;if(searchable){const q=document.createElement("input");q.className="kpSelectSearch";q.placeholder="Search…";q.autocomplete="off";q.onclick=e=>e.stopPropagation();q.oninput=()=>{let v=q.value.toLowerCase(),shown=0;m.querySelectorAll(".kpSelectOpt").forEach(o=>{let ok=o.textContent.toLowerCase().includes(v);o.style.display=ok?"flex":"none";if(ok)shown++});let z=m.querySelector(".kpSelectEmpty");if(z)z.style.display=shown?"none":"block"};m.appendChild(q);const z=document.createElement("div");z.className="kpSelectEmpty";z.textContent="No matching option";z.style.display="none";m.appendChild(z)}function sync(){const o=sel.options[sel.selectedIndex];b.textContent=o?o.textContent:"Choose…";b.disabled=sel.disabled;m.querySelectorAll(".kpSelectOpt").forEach((x,i)=>x.classList.toggle("sel",i===sel.selectedIndex))}function rebuild(){m.querySelectorAll(".kpSelectOpt").forEach(x=>x.remove());[...sel.options].forEach((o,i)=>{const x=document.createElement("button");x.type="button";x.className="kpSelectOpt"+(o.selected?" sel":"");x.textContent=o.textContent;x.disabled=o.disabled;x.onclick=e=>{e.stopPropagation();sel.selectedIndex=i;sel.dispatchEvent(new Event("input",{bubbles:true}));sel.dispatchEvent(new Event("change",{bubbles:true}));w.classList.remove("open");sync()};m.appendChild(x)});sync()}b.onclick=e=>{e.stopPropagation();closeAll(w);w.classList.toggle("open");if(w.classList.contains("open")){let q=m.querySelector(".kpSelectSearch");if(q){q.value="";q.dispatchEvent(new Event("input"));setTimeout(()=>q.focus(),0)}}};w.appendChild(b);w.appendChild(m);sel.addEventListener("change",sync);new MutationObserver(rebuild).observe(sel,{childList:true,subtree:true,attributes:true});rebuild();setInterval(sync,600)}function boot(){document.querySelectorAll("select").forEach(enhance)}document.addEventListener("click",()=>closeAll());document.addEventListener("DOMContentLoaded",boot);new MutationObserver(boot).observe(document.documentElement,{childList:true,subtree:true});window.kpSyncSelects=boot;})();'''

@app.get("/pulse95.js")
def pulse95_js():
 return PULSE_95_JS,200,{"Content-Type":"application/javascript; charset=utf-8","Cache-Control":"public, max-age=300"}

GLOBAL_SHELL_JS=r'''(()=>{
 const path=location.pathname;
 if(path.startsWith('/api/')||path.startsWith('/admin/'))return;
 const css=document.createElement('style');
 css.textContent=`
 .kpGlobalHome{position:fixed;left:16px;bottom:18px;z-index:9998;display:inline-flex;align-items:center;gap:8px;padding:11px 15px;border-radius:999px;background:linear-gradient(135deg,rgba(24,91,59,.94),rgba(80,147,111,.86));border:1px solid rgba(255,255,255,.34);box-shadow:inset 0 1px rgba(255,255,255,.28),0 14px 36px rgba(0,0,0,.30);backdrop-filter:blur(18px);color:white!important;text-decoration:none!important;font:850 12px Inter,system-ui;letter-spacing:.02em}
 .kpGlobalHome:hover{transform:translateY(-1px);filter:brightness(1.06)}
 .kpGlobalAdRail{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:9997;width:min(720px,calc(100vw - 150px));min-height:46px;display:flex;align-items:center;gap:12px;padding:8px 12px;border-radius:18px;background:linear-gradient(120deg,rgba(9,50,31,.94),rgba(38,100,67,.90),rgba(82,91,35,.78));border:1px solid rgba(255,255,255,.25);box-shadow:inset 0 1px rgba(255,255,255,.24),0 16px 38px rgba(0,0,0,.34);backdrop-filter:blur(22px);overflow:hidden}
 .kpGlobalAdRail .kpAdTag{flex:0 0 auto;padding:6px 8px;border-radius:999px;background:#69ef91;color:#12351f;font:900 9px Inter,system-ui;letter-spacing:.11em}
 .kpGlobalAdRail a{min-width:0;display:flex;align-items:center;gap:8px;color:white;text-decoration:none;font:700 12px Inter,system-ui;overflow:hidden}
 .kpGlobalAdRail b{color:#a8f7be;white-space:nowrap}.kpGlobalAdRail span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#edf8f1}
 .kpGlobalAdRail .kpAdDot{width:7px;height:7px;border-radius:50%;background:#ffd95a;box-shadow:0 0 0 4px rgba(255,217,90,.12);flex:0 0 auto}.kpGlobalAdRail .kpAdBrandIcon{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;font-style:normal;font-weight:950;flex:0 0 auto}.kpGlobalAdRail strong{margin-left:auto;padding:6px 9px;border-radius:999px;font-size:9px;white-space:nowrap}.kpGlobalAdRail.beathub{background:linear-gradient(120deg,#0b0710,#1c1424)}.kpGlobalAdRail.beathub .kpAdBrandIcon,.kpGlobalAdRail.beathub strong{background:#f5b400;color:#1a1200}.kpGlobalAdRail.mkulima{background:linear-gradient(120deg,#082b16,#17612d,#7a7016)}.kpGlobalAdRail.mkulima .kpAdBrandIcon,.kpGlobalAdRail.mkulima strong{background:#d7ff72;color:#173515}.kpGlobalAdRail.mizizi{background:linear-gradient(120deg,#1c140d,#5a3f24,#1f4c35)}.kpGlobalAdRail.mizizi .kpAdBrandIcon,.kpGlobalAdRail.mizizi strong{background:#d8b77a;color:#2a1b0d}.kpGlobalAdRail.onebob{background:linear-gradient(120deg,#0b2f1d,#1f6a40,#8b6f11)}.kpGlobalAdRail.onebob .kpAdBrandIcon,.kpGlobalAdRail.onebob strong{background:#ffe06b;color:#15351f}
 @media(max-width:640px){.kpGlobalHome{left:10px;bottom:10px;padding:10px 12px}.kpGlobalAdRail{right:10px;left:auto;transform:none;bottom:10px;width:calc(100vw - 118px);min-height:42px;padding:7px 10px}.kpGlobalAdRail .kpAdTag{display:none}}
 `;
 document.head.appendChild(css);

 const hasHome=[...document.querySelectorAll('a')].some(a=>{
   const h=(a.getAttribute('href')||'').trim();
   const t=(a.textContent||'').trim().toLowerCase();
   return (h==='/'||h==='/growth')&&(t.includes('home')||t.includes('kenya pulse'));
 });
 if(!hasHome){
   const home=document.createElement('a');
   home.href='/growth';home.className='kpGlobalHome';home.innerHTML='⌂ <span>Home</span>';
   home.setAttribute('aria-label','Back to Kenya Pulse AI homepage');
   document.body.appendChild(home);
 }

 const hasAd=document.querySelector('#liveAd,.adTicker,.adwrap,.kpGlobalAdRail,[aria-label*="Sponsored"],[aria-label*="sponsored"]');
 if(hasAd)return;
 const rail=document.createElement('div');
 rail.className='kpGlobalAdRail';rail.setAttribute('aria-label','Sponsored commercial message');
 rail.innerHTML='<span class="kpAdTag">SPONSORED</span><span style="font:700 12px Inter,system-ui;color:#dcece2">Loading message…</span>';
 document.body.appendChild(rail);

 async function refreshAd(){
   try{
     let county='';
     const q=new URLSearchParams(location.search).get('county');
     if(q)county=q;
     const countyEl=document.querySelector('#county,[name="county"]');
     if(!county&&countyEl&&countyEl.value)county=countyEl.value;
     const r=await fetch('/api/ad?county='+encodeURIComponent(county),{cache:'no-store'});
     const j=await r.json();
     if(!j.ad){rail.style.display='none';return}
     const a=j.ad;
     rail.style.display='flex';
     rail.className='kpGlobalAdRail '+(a.brand_key||'generic');rail.innerHTML='<span class="kpAdTag">SPONSORED</span><a href="'+a.click_url+'" rel="sponsored noopener"><i class="kpAdBrandIcon">'+esc(a.icon||'✦')+'</i><b>'+esc(a.business)+'</b><span>'+esc(a.tagline||a.headline||'Explore this sponsor')+'</span><strong>'+esc(a.cta||'OPEN')+' →</strong></a>';
   }catch(e){rail.style.display='none'}
 }
 function esc(v){return String(v||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
 refreshAd();
 setInterval(()=>{if(!document.hidden)refreshAd()},30000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshAd()});
})();'''

@app.get("/favicon.svg")
def kenya_pulse_favicon():
 svg='''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
 <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#8df7ac"/><stop offset=".55" stop-color="#45d87a"/><stop offset="1" stop-color="#d7ff72"/></linearGradient></defs>
 <rect width="64" height="64" rx="16" fill="#082b1a"/>
 <rect x="3" y="3" width="58" height="58" rx="13" fill="none" stroke="url(#g)" stroke-width="3"/>
 <path d="M12 34h9l4-10 7 20 6-15 5 5h9" fill="none" stroke="url(#g)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
 <circle cx="51" cy="14" r="4" fill="#ffd95a"/>
 </svg>'''
 return svg,200,{"Content-Type":"image/svg+xml; charset=utf-8","Cache-Control":"public, max-age=86400"}

@app.get("/pulse-global.js")
def pulse_global_js():
 return GLOBAL_SHELL_JS,200,{"Content-Type":"application/javascript; charset=utf-8","Cache-Control":"public, max-age=300"}

@app.after_request
def kenya_pulse_global_ui(response):
 try:
  if "text/html" in response.headers.get("Content-Type","") and not response.direct_passthrough:
   body=response.get_data(as_text=True)
   scripts=""
   if "/pulse95.js" not in body:scripts+='<script src="/pulse95.js"></script>'
   if "/pulse-global.js" not in body:scripts+='<script src="/pulse-global.js"></script>'
   if "<link rel=\"icon\"" not in body and "<link rel='icon'" not in body:
    fav='<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=20261005"><link rel="shortcut icon" href="/favicon.svg?v=20261005">'
    if "</head>" in body:body=body.replace("</head>",fav+"</head>")
   if "</body>" in body and scripts:
    body=body.replace("</body>",scripts+"</body>")
    response.set_data(body)
    response.headers["Content-Length"]=str(len(body.encode("utf-8")))
 except Exception:pass
 return response

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenya Pulse AI — Live Participation</title><link rel="stylesheet" href="/pulse95.css"><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui;background:#03130c;color:#f7fff9;min-height:100vh;overflow-x:hidden}body:before,body:after{content:"";position:fixed;border-radius:50%;filter:blur(20px);z-index:-2}body:before{width:520px;height:520px;background:#1d7b4a55;top:-180px;left:-170px}body:after{width:460px;height:460px;background:#d4a90025;right:-180px;top:35%}.mesh{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 70% 5%,#1b6d4338,transparent 32%),linear-gradient(145deg,#020b07,#061c12 48%,#04110b)}.top{height:76px;padding:0 max(20px,5vw);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #ffffff14;position:sticky;top:0;background:#071a1199;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);z-index:10}.brand{font-weight:950;font-size:21px;letter-spacing:-.8px}.brand b{color:#ffd54a}.live{display:flex;align-items:center;gap:8px;font-size:12px;color:#d9eee1}.dot{width:8px;height:8px;background:#6cff9a;border-radius:50%;box-shadow:0 0 16px #6cff9a}.wrap{max-width:1180px;margin:auto;padding:46px 20px 70px}.hero{display:grid;grid-template-columns:1.25fr .75fr;gap:28px;align-items:end;padding:28px 0 30px}.eyebrow,.pill{display:inline-flex;padding:7px 11px;border:1px solid #ffffff1f;background:#ffffff0c;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.7px;text-transform:uppercase}.hero h1{font-size:clamp(46px,7vw,82px);line-height:.94;letter-spacing:-4px;margin:15px 0 20px}.hero h1 span{color:#75f59b}.hero p{font-size:17px;max-width:650px}.muted{color:#a9c6b4}.glass{background:linear-gradient(135deg,#ffffff12,#ffffff07);border:1px solid #ffffff1b;box-shadow:0 24px 80px #0000002e,inset 0 1px #ffffff13;backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);border-radius:26px}.heroStat{padding:22px}.heroStat .big{font-size:46px;font-weight:950;letter-spacing:-2px}.heroStat small{color:#9bb7a6}.layout{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}.card{padding:24px}.card h2{margin:0 0 7px;font-size:20px}.cardHead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:18px}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}select,input,button{width:100%;padding:15px 16px;border-radius:14px;border:1px solid #ffffff1d;background:#06180f99;color:#fff;font:inherit;outline:none}select:focus,input:focus{border-color:#ffd54a88;box-shadow:0 0 0 3px #ffd54a12}button{background:linear-gradient(135deg,#ffe06b,#f5c728);color:#152016;font-weight:900;border:0;cursor:pointer;transition:.2s}button:hover{transform:translateY(-1px);filter:brightness(1.04)}.secondary{background:#ffffff0b;color:#fff;border:1px solid #ffffff1c}.row{padding:14px 0;border-bottom:1px solid #ffffff12}.row:last-child{border:0}.bar{height:7px;background:#ffffff10;border-radius:99px;overflow:hidden;margin-top:8px}.fill{height:100%;background:linear-gradient(90deg,#ffd54a,#70e596);border-radius:99px}.analytics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.metric{padding:18px}.metric strong{display:block;font-size:27px;letter-spacing:-1px}.metric span{font-size:12px;color:#9eb9a8}.share{margin-top:18px}.notice{font-size:12px;line-height:1.6;padding:18px;margin-top:18px;color:#b7cdbf}.adwrap{margin-top:18px;padding:10px}.ad{min-height:132px;border:1px dashed #ffffff30;border-radius:20px;display:flex;align-items:center;justify-content:center;text-align:center;background:#ffffff05;padding:20px}.ad b{display:block;color:#e9f5ed;margin-bottom:5px}.ad small{color:#89a595}.beathubAd{position:relative;width:100%;display:grid;grid-template-columns:auto 1fr auto;gap:17px;align-items:center;text-align:left;padding:18px;border-radius:22px;background:linear-gradient(145deg,#0b0710,#150e1c 58%,#1c1424);border:1px solid #2a2032;box-shadow:inset 0 1px rgba(255,255,255,.035),0 18px 45px rgba(0,0,0,.26);overflow:hidden}.beathubAd:before{content:'';position:absolute;left:0;right:0;top:0;height:5px;background:repeating-linear-gradient(45deg,#ef3f6a 0 12px,#f5b400 12px 24px,#1fd1a3 24px 36px)}.beathubAd:after{content:'';position:absolute;width:150px;height:150px;right:-55px;bottom:-95px;border-radius:50%;border:1px solid rgba(245,180,0,.12);box-shadow:0 0 0 26px rgba(31,209,163,.035),0 0 0 52px rgba(239,63,106,.022);pointer-events:none}.beathubMark{position:relative;z-index:1;width:68px;height:68px;border-radius:18px;display:grid;place-items:center;background:#150e1c;border:1px solid #2a2032;box-shadow:0 16px 34px rgba(0,0,0,.34)}.beathubMark img{width:58px;height:38px;object-fit:contain}.beathubCopy{position:relative;z-index:1}.beathubWordmark{font-size:13px;font-weight:950;letter-spacing:.1em;color:#f5f3f7;margin-bottom:5px}.beathubWordmark span{color:#f5b400}.beathubCopy b{font-size:20px;line-height:1.15;margin:0 0 6px;color:#f5f3f7}.beathubCopy small{display:block;line-height:1.5;color:#a89bb4;max-width:580px}.beathubAccent{display:inline-block;margin-top:7px;color:#1fd1a3;font-size:11px;font-weight:850;letter-spacing:.04em}.beathubBtn{position:relative;z-index:1;display:inline-flex!important;align-items:center;justify-content:center;white-space:nowrap;padding:12px 15px;border-radius:12px;background:#f5b400;color:#1a1200!important;text-decoration:none;font-weight:950;font-size:12px;box-shadow:0 10px 26px rgba(245,180,0,.16);transition:.18s}.beathubBtn:hover{background:#ffc61a;transform:translateY(-1px)}@media(max-width:620px){.beathubAd{grid-template-columns:auto 1fr;padding:16px}.beathubBtn{grid-column:1/-1}.beathubMark{width:58px;height:58px}.beathubMark img{width:50px;height:32px}}.brandAd{position:relative;width:100%;display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center;padding:18px;border-radius:22px;overflow:hidden;text-align:left;border:1px solid #ffffff22;box-shadow:inset 0 1px #ffffff18,0 18px 45px #0005}.brandAdIcon{width:62px;height:62px;border-radius:18px;display:grid;place-items:center;font-size:28px;font-weight:950}.brandAdName{font-weight:950;letter-spacing:.05em;font-size:12px}.brandAdCopy b{display:block;font-size:19px;line-height:1.15;margin:3px 0 6px}.brandAdCopy small{display:block;line-height:1.45}.brandAdTag{display:inline-block;margin-top:7px;font-size:10px;font-weight:900;letter-spacing:.08em}.brandAdBtn{display:inline-flex!important;align-items:center;justify-content:center;padding:12px 14px;border-radius:12px;text-decoration:none!important;font-weight:950;font-size:11px;white-space:nowrap}.brandAd.beathub{background:linear-gradient(145deg,#0b0710,#150e1c 58%,#1c1424);border-color:#2a2032}.brandAd.beathub .brandAdIcon{background:#f5b400;color:#1a1200}.brandAd.beathub .brandAdName,.brandAd.beathub .brandAdTag{color:#f5b400}.brandAd.beathub small{color:#a89bb4}.brandAd.beathub .brandAdBtn{background:#f5b400;color:#1a1200!important}.brandAd.mkulima{background:linear-gradient(135deg,#082b16,#145c2a 58%,#8e7a16);border-color:#69ef9166}.brandAd.mkulima .brandAdIcon{background:#d7ff72;color:#173515}.brandAd.mkulima .brandAdName,.brandAd.mkulima .brandAdTag{color:#d7ff72}.brandAd.mkulima small{color:#d6ead9}.brandAd.mkulima .brandAdBtn{background:#d7ff72;color:#163213!important}.brandAd.mizizi{background:linear-gradient(135deg,#1d160d,#5a3f24 55%,#1d4a33);border-color:#d8b77a55}.brandAd.mizizi .brandAdIcon{background:#d8b77a;color:#2a1b0d}.brandAd.mizizi .brandAdName,.brandAd.mizizi .brandAdTag{color:#e8c98c}.brandAd.mizizi small{color:#eadfcf}.brandAd.mizizi .brandAdBtn{background:#d8b77a;color:#2a1b0d!important}.brandAd.onebob{background:linear-gradient(135deg,#0b2f1d,#17653b 58%,#9a7a10);border-color:#ffe06b55}.brandAd.onebob .brandAdIcon{background:#ffe06b;color:#15351f}.brandAd.onebob .brandAdName,.brandAd.onebob .brandAdTag{color:#ffe06b}.brandAd.onebob small{color:#d8eadf}.brandAd.onebob .brandAdBtn{background:#ffe06b;color:#15351f!important}.brandAd.generic{background:linear-gradient(135deg,#0b2f1d,#164d33)}.brandAd.generic .brandAdIcon{background:#69ef91;color:#12351f}.brandAd.generic .brandAdBtn{background:#69ef91;color:#12351f!important}@media(max-width:620px){.brandAd{grid-template-columns:auto 1fr}.brandAdBtn{grid-column:1/-1}}.adlabel{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#769180;margin:5px 8px 10px}.footer{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;padding:24px 4px;color:#7f9d8b;font-size:12px}.footer a{color:#b9d2c2;text-decoration:none}@media(max-width:800px){.hero,.layout{grid-template-columns:1fr}.hero h1{letter-spacing:-2.5px}.heroStat{display:none}.analytics{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.wrap{padding:28px 14px 50px}.top{height:66px}.formgrid{grid-template-columns:1fr}.analytics{gap:7px}.metric{padding:13px 10px}.metric strong{font-size:22px}.card{padding:18px}.glass{border-radius:21px}.hero{padding-top:15px}.hero h1{font-size:49px}}
.searchdock{position:sticky;top:14px;z-index:20;margin:0 0 18px;padding:10px 14px;display:flex;align-items:center;gap:10px;background:linear-gradient(120deg,rgba(255,255,255,.18),rgba(255,255,255,.055));border-color:rgba(255,255,255,.35);box-shadow:inset 0 1px rgba(255,255,255,.42),0 22px 70px rgba(0,0,0,.48),0 0 50px rgba(0,255,136,.12)}.searchdock:focus-within{border-color:#7affb899;box-shadow:inset 0 1px #ffffff45,0 20px 70px #0009,0 0 55px #00ff8840}.searchicon{font-size:28px;color:#7affb8;text-shadow:0 0 20px #00ff88}.searchdock input{flex:1;min-width:0;background:transparent;border:0;outline:0;color:white;font-size:16px;padding:13px}.searchdock input::placeholder{color:#91a99d}.searchbtn{border:1px solid #8affbd66;background:linear-gradient(135deg,#79ffb4,#d7ff72);color:#04130b;font-weight:900;letter-spacing:.04em;border-radius:16px;padding:14px 18px;cursor:pointer;box-shadow:0 0 30px #49ff9930}.searchbtn:hover{transform:translateY(-1px);box-shadow:0 0 42px #49ff9950}.county:before{content:'';position:absolute;inset:-60% -40%;background:linear-gradient(120deg,transparent 35%,#ffffff12 50%,transparent 65%);transform:translateX(-60%) rotate(12deg);transition:.8s}.county:hover:before{transform:translateX(55%) rotate(12deg)}.county:after{content:'';position:absolute;width:100px;height:100px;border-radius:50%;right:-30px;top:-30px;background:#58ff9d12;filter:blur(4px);box-shadow:0 0 80px #58ff9d25}.county>*{position:relative;z-index:2}.hero{background:linear-gradient(120deg,rgba(255,255,255,.17),rgba(255,255,255,.045) 52%,rgba(100,255,180,.045));border-color:rgba(255,255,255,.32);box-shadow:inset 0 1px rgba(255,255,255,.42),0 40px 100px rgba(0,0,0,.38),0 0 80px rgba(0,255,140,.08)}.hero:before{content:'';position:absolute;width:340px;height:340px;border:1px solid #ffffff10;border-radius:50%;right:-80px;bottom:-220px;box-shadow:0 0 90px #00ff8830,inset 0 0 70px #ffffff08}.stat{position:relative;overflow:hidden;min-height:122px;transition:.3s}.stat:hover{transform:translateY(-4px);border-color:rgba(255,255,255,.4)}.stat:after{content:'';position:absolute;inset:auto -20% -65% 30%;height:100px;background:#ffe45e16;border-radius:50%;filter:blur(25px)}.empty{display:none;text-align:center;padding:50px;color:#9fb4aa}.glass:after{content:'';pointer-events:none;position:absolute;inset:1px;border-radius:inherit;background:linear-gradient(125deg,rgba(255,255,255,.13),transparent 22%,transparent 72%,rgba(120,255,190,.035));mask:linear-gradient(#000,transparent 35%);opacity:.7}
/* County participation reuses the approved 9.5 dashboard proportions */
body{background:#0b2f1d}.top.kp95nav{position:relative;top:auto;height:66px;max-width:1380px;width:calc(100% - 48px);margin:18px auto 0;padding:0 20px;border-bottom:1px solid rgba(244,255,247,.32);border-radius:24px}.top .brand b{color:#7cf39d}.wrap{max-width:1380px;padding:16px 24px 50px}.hero{min-height:220px;grid-template-columns:1.2fr .8fr;align-items:center;gap:40px;padding:28px;border-radius:30px;margin-top:16px;position:relative;overflow:hidden}.hero h1{font-size:clamp(42px,5vw,68px);line-height:.94;letter-spacing:-3px;margin:10px 0 14px}.hero p{margin:0;max-width:680px;line-height:1.55}.heroStat{justify-self:end;width:min(360px,100%);padding:24px;border-radius:28px}.analytics{margin:14px 0;gap:14px}.metric{min-height:90px;padding:16px 20px}.layout{grid-template-columns:1.05fr .95fr;gap:16px}.card{padding:22px;border-radius:26px}.layout>.card{min-height:390px}select,input{padding:13px 15px}button{padding:13px 16px;background:#69ef91;color:#092817}.secondary{background:#ffffff0b;color:#fff}.fill{background:#70ee96}.notice a,.ad a{color:#75f59b!important}select:focus,input:focus{border-color:#75f59b88;box-shadow:0 0 0 3px #75f59b12}@media(max-width:800px){.top.kp95nav{width:calc(100% - 24px);margin-top:10px}.wrap{padding:10px 12px 45px}.hero{grid-template-columns:1fr;min-height:auto;padding:22px;margin-top:10px}.heroStat{display:none}.hero h1{font-size:46px}.layout{grid-template-columns:1fr}.layout>.card{min-height:0}.analytics{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.top.kp95nav{height:60px}.hero{padding:19px}.hero h1{font-size:40px}.analytics{gap:7px}.metric{min-height:78px;padding:12px 10px}.card{padding:17px}.wrap{padding-bottom:35px}}


.raceProgress{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;margin:4px 0 14px}.raceStep{border:1px solid rgba(255,255,255,.7);border-radius:14px;padding:9px 5px;text-align:center;font-size:11px;font-weight:800;line-height:1.15;background:rgba(255,255,255,.35);color:#607066;transition:.25s ease;box-shadow:inset 0 1px 0 #fff8}.raceStep.done{background:#69ef91;color:#143b20;border-color:#69ef91}.raceStep.current{background:#ffd95a;color:#493b00;border-color:#ffe581;transform:translateY(-2px);box-shadow:0 8px 20px #e7b80033,inset 0 1px 0 #fff}.raceStep.pending{opacity:.58}.raceStep .check{display:block;font-size:14px;margin-bottom:3px}@media(max-width:620px){.raceProgress{grid-template-columns:repeat(3,1fr)}}
.photoBallot{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin-top:14px}
.candidatePhoto{aspect-ratio:1/1;border:2px solid #ffffff24;border-radius:22px;overflow:hidden;padding:0;background:#0a2116;position:relative;cursor:pointer;box-shadow:0 12px 28px #0005}
.candidatePhoto:hover{transform:translateY(-2px);border-color:#69ef91}
.candidatePhoto img{width:100%;height:100%;object-fit:cover;display:block}.candidatePhoto{overflow:hidden;position:relative;padding:0!important}.candidatePhoto .candidateMeta{position:absolute;left:0;right:0;bottom:0;padding:26px 9px 9px;background:linear-gradient(transparent,rgba(0,0,0,.82));text-align:left;color:white}.candidatePhoto .candidateMeta b{display:block;font-size:12px;line-height:1.15}.candidatePhoto .candidateMeta small{display:block;font-size:9px;color:#d9e7dd;margin-top:2px}
.candidatePhoto .missing{width:100%;height:100%;display:grid;place-items:center;font-size:34px;color:#8cad98;background:linear-gradient(145deg,#153d29,#0b2417)}
.candidatePhoto.selected{outline:3px solid #ffd54a;outline-offset:2px}
.ballotHint{font-size:12px;color:#a9c6b4;margin-top:9px;line-height:1.45}
.ballotFallback{margin-top:12px;padding:12px;border:1px dashed #ffffff24;border-radius:14px}
.topResult{display:grid;grid-template-columns:52px 1fr auto;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid #ffffff12}
.topResult:last-child{border:0}.topResult img,.topResult .avatar{width:52px;height:52px;border-radius:50%;object-fit:cover;background:#123723;display:grid;place-items:center;font-size:20px}
.rank{font-size:11px;color:#89a595}.topResult b{display:block}.topResult .score{text-align:right;font-weight:900}
@media(max-width:620px){.photoBallot{grid-template-columns:repeat(3,minmax(0,1fr))}}
</style></head><body><div class=world></div><div class=shade></div><header class="top kp95nav"><div class=brand>KENYA <b>PULSE</b></div><div class=live><i class=dot></i> LIVE PARTICIPATION</div></header><main class=wrap>
<section class=hero><div>{% if initial_county %}<div class=kp95mark>{{county_mark}}</div>{% endif %}<span class=eyebrow>{% if initial_county %}{{initial_county}} · COUNTY PARTICIPATION{% else %}47 counties · voluntary participation{% endif %}</span><h1>{% if initial_county %}{{initial_county}}.<br><span>Your voice.</span>{% else %}Your county.<br><span>Your voice.</span>{% endif %}</h1><p class=muted>Share your current preference and explore live aggregate responses from people participating on Kenya Pulse AI. This is an open online pulse, not a scientific election forecast.</p></div><aside class="glass heroStat"><small>COUNTIES AVAILABLE</small><div class=big>47</div><small>One transparent participation experience across Kenya.</small></aside></section>
<div class=analytics><div class="glass metric"><strong id=metricTotal>—</strong><span>Selected race responses</span></div><div class="glass metric"><strong>47</strong><span>Counties available</span></div><div class="glass metric"><strong>LIVE</strong><span>Aggregate updates</span></div></div>
<section class=layout><div class="glass card"><div class=cardHead><div><h2>Join the pulse</h2><span class=muted>Complete all six seats</span></div><span class=pill>Private choice</span></div><div id=raceProgress class=raceProgress aria-label="Participation progress"></div><div class=formgrid><select id=county onchange="onScopeChange()"><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><select id=race onchange="onRaceChange()">{% for r in races %}<option>{{r}}</option>{% endfor %}</select></div><div id=areaBox class=formgrid style="display:none;margin-top:10px"><select id=constituency onchange="populateWards();load()"><option value="">Choose constituency</option></select><select id=ward onchange="load()"><option value="">Choose ward</option></select></div><div id=candidateGrid class=photoBallot aria-label="Candidate photo ballot"></div><div class=ballotHint>Tap one photo to record your current preference and continue automatically. Names are intentionally hidden on the ballot; the live Top 3 dashboard shows names separately.</div><div id=ballotFallback class=ballotFallback style="display:none"><button class=secondary onclick="toggleManualCandidate()">Candidate not shown / photo missing</button><div id=manualCandidateWrap style="display:none;margin-top:10px"><input id=candidate maxlength=80 placeholder="Enter full name or known alias" autocomplete="off"><div class=ballotHint style="margin-top:6px">If the name or alias matches a verified profile, Kenya Pulse will combine it with that candidate automatically. Otherwise your typed choice will still be recorded.</div><button class=secondary onclick="voteManual()" style="margin-top:8px">Record this choice →</button></div></div><input id=issue maxlength=120 placeholder="Optional: issue influencing your choice" style="margin-top:10px"><div id=msg class=muted style="margin-top:10px;font-size:13px"></div></div>
<div class="glass card"><div class=cardHead><div><h2 id=rt>Live participant results</h2><span class=muted>Voluntary website responses</span></div><span class=pill>Live</span></div><div id=results><p class=muted>Select a county to explore aggregate participant results.</p></div></div></section>
<section id=supportbox class="glass card kp95panel" style="display:none"><div class=cardHead><div><h2>Voting complete · Optional support</h2><span class=muted>Your 6 responses are already saved</span></div><span class=pill>Completely optional</span></div><p><b>Participation and results are completely free.</b> If you find Kenya Pulse AI useful, you can optionally help cover the cost of keeping the platform running.</p><p class=muted>Support with as low as KSh 5. Your contribution does not affect your response or the results.</p><div class=formgrid><input id=supportCustom type=number min=5 step=1 inputmode=numeric placeholder="KSh 5 or above"><input id=supportEmail type=email autocomplete=email placeholder="Email for payment receipt"></div><div class=formgrid style="margin-top:10px"><button id=supportPayButton class=secondary onclick="supportAmount()">Continue to secure Paystack checkout</button><button class=secondary onclick="dismissSupport()">Not now</button></div><div id=supportmsg class=muted>No contribution is required to view results.</div></section><section id=sharebox class="glass card share" style="display:none"><div class=cardHead><div><h2>Share your county pulse</h2><span class=muted>Your response is counted whether or not you share.</span></div><span class=pill>Optional</span></div><div class=formgrid><button onclick=sharePulse()>Share county pulse</button><button class=secondary onclick=copyPulse()>Copy county link</button></div><div id=sharemsg class=muted style="margin-top:9px;font-size:12px"></div></section>
<section class="glass adwrap"><div class=adlabel>Advertisement</div><div class=ad id=liveAd><div><b>Premium advertising space</b><small>Sponsored content will appear here, clearly separated from participation controls and results.</small></div></div></section>
<section class="glass notice"><b>Transparency:</b> Results show voluntary Kenya Pulse AI participants and are not representative of all registered voters. They should not be interpreted as an election forecast. Individual choices are not publicly displayed. Candidate names are curated participation options and their appearance is not an endorsement. <a href="/methodology" style="color:#ffd54a">Read methodology →</a></section>
<footer class=footer><span>© Kenya Pulse AI · Open participation dashboard</span><span><a href="https://www.facebook.com/people/Kenya-Pulse/61594936328345/" target="_blank" rel="noopener noreferrer">Kenya Pulse AI on Facebook</a> · <a href="/claim-profile">Claim profile</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/methodology">Methodology</a></span></footer></main>
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
function areaMode(){let mp=R.value==='Member of Parliament',mca=R.value==='MCA',b=document.getElementById('areaBox');b.style.display=(mp||mca)?'grid':'none';document.getElementById('ward').style.display=mca?'block':'none';load()}areaMode();C.onchange=async()=>{clearAreas();syncUrl();await populateConstituencies();await restoreParticipation();await loadCandidates();load()};R.onchange=()=>{areaMode();loadCandidates()};const supportQS=new URLSearchParams(location.search),supportState=supportQS.get('support'),supportRef=supportQS.get('reference');if(supportState){setTimeout(()=>{let box=document.getElementById('supportbox'),m=document.getElementById('supportmsg');if(box)box.style.display='block';if(m)m.textContent=supportState==='success'?'Payment verified. Thank you for supporting Kenya Pulse AI.':supportState==='pending'?'Payment received by checkout; waiting for final Paystack verification…':'Payment status could not be confirmed.';if(supportState==='pending'&&supportRef)pollSupport(supportRef,0)},50)}
async function pollSupport(ref,n){if(n>12)return;try{let r=await fetch('/api/support/status?reference='+encodeURIComponent(ref),{cache:'no-store'}),j=await r.json();let m=document.getElementById('supportmsg');if(j.status==='SUCCESS'){if(m)m.textContent='Payment verified. Thank you for supporting Kenya Pulse AI.';let u=new URL(location.href);u.searchParams.set('support','success');u.searchParams.delete('reference');history.replaceState({},'',u);return}if(m)m.textContent='Payment is still being verified securely…';}catch(e){}setTimeout(()=>pollSupport(ref,n+1),5000)}
const initialCounty={{ initial_county|tojson }};if(initialCounty){C.value=initialCounty;document.getElementById('sharebox').style.display='block';(async()=>{await populateConstituencies();await restoreParticipation();await loadCandidates();load()})()}
function clearAreas(){let a=document.getElementById('constituency'),w=document.getElementById('ward');if(a)a.value='';if(w)w.value=''}
function slugCounty(v){return v.toLowerCase().replace(/&/g,'and').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}function syncUrl(){if(C.value){history.replaceState({},'', '/county/'+slugCounty(C.value)+(location.search||''));document.getElementById('sharebox').style.display='block'}}
function pulseUrl(){let u=new URL(location.href);u.searchParams.set('src','share');return u.toString()}async function sharePulse(){let text='Take part in the '+C.value+' county pulse and see aggregate participant results live. Open online pulse — not a scientific election forecast.';if(navigator.share){await navigator.share({title:'Kenya Pulse AI • '+C.value,text,url:pulseUrl()})}else{await navigator.clipboard.writeText(text+' '+pulseUrl());document.getElementById('sharemsg').textContent='Share text copied.'}}async function copyPulse(){await navigator.clipboard.writeText(pulseUrl());document.getElementById('sharemsg').textContent='County link copied.'}
async function loadCandidates(){
 const grid=document.getElementById('candidateGrid'),fallback=document.getElementById('ballotFallback');
 grid.innerHTML='<div class="muted">Loading photos…</div>';fallback.style.display='none';
 let q='/api/candidates?race='+encodeURIComponent(R.value)+'&county='+encodeURIComponent(C.value)+'&constituency='+encodeURIComponent(document.getElementById('constituency').value||'')+'&ward='+encodeURIComponent(document.getElementById('ward').value||'');
 try{
  let r=await fetch(q,{cache:'no-store'}),j=await r.json(),items=j.candidates||[];
  grid.innerHTML='';
  if(!items.length){grid.innerHTML='<div class="muted">No verified photo profiles are published for this seat yet. You can still submit a name below.</div>';fallback.style.display='block';return}
  items.forEach(x=>{
   let b=document.createElement('button');b.type='button';b.className='candidatePhoto';b.title='Tap to select this person';b.setAttribute('aria-label','Select candidate photo');
   b.innerHTML=(x.photo_url?'<img src="'+escAttr(x.photo_url)+'" alt="'+escAttr(x.name)+'" loading="lazy">':'<div class="missing"><span>👤</span><small>Photo pending verification</small></div>')+'<span class="candidateMeta"><b>'+esc(x.name)+'</b><small>'+esc(x.party||x.status||'Public profile')+'</small></span>';
   b.onclick=()=>voteCandidate(x,b);grid.appendChild(b)
  });
  fallback.style.display='block';
 }catch(e){grid.innerHTML='<div class="muted">Candidate photos are temporarily unavailable.</div>';fallback.style.display='block'}
}
function toggleManualCandidate(){let w=document.getElementById('manualCandidateWrap');w.style.display=w.style.display==='none'?'block':'none'}
async function onScopeChange(){
 document.getElementById('supportbox').style.display='none';
 if(!C.value){areaMode();return}
 try{await restoreParticipation();await load()}catch(e){document.getElementById('msg').textContent=e.message||'Could not restore participation progress.'}
}
async function onRaceChange(){
 if(!C.value){areaMode();return}
 try{
  const response=await fetch('/api/participation/progress?county='+encodeURIComponent(C.value),{cache:'no-store'});
  const progress=await response.json();
  if(progress.next_race&&R.value!==progress.next_race){
   R.value=progress.next_race;
   document.getElementById('msg').textContent='Complete the seats in order. Next: '+progress.next_race+'.';
  }
  areaMode();await loadCandidates();await load();
 }catch(e){areaMode();await loadCandidates();await load()}
}
async function voteCandidate(person,button){
 if(!C.value){document.getElementById('msg').textContent='Choose your county first.';return}
 document.querySelectorAll('.candidatePhoto').forEach(x=>x.classList.remove('selected'));button.classList.add('selected');
 await submitPhotoPreference(person.id,person.name);
}
async function submitPhotoPreference(candidateId,candidateName){
 let msg=document.getElementById('msg'),cv=document.getElementById('constituency').value.trim(),wv=document.getElementById('ward').value.trim(),issue=document.getElementById('issue').value.trim();
 if((R.value==='Member of Parliament'||R.value==='MCA')&&!cv){msg.textContent='Choose your constituency first.';return}
 if(R.value==='MCA'&&!wv){msg.textContent='Choose your ward first.';return}
 msg.textContent='Recording…';
 let savedRace=R.value,x=await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({county:C.value,race:R.value,candidate:candidateName,candidate_id:candidateId,issue,constituency:cv,ward:wv})}),j=await x.json();
 msg.textContent=j.message||j.error;
 if(x.status===409){await restoreParticipation();await load();return}
 if(x.ok&&j.recorded===true){await loadResultsFor(savedRace);await restoreParticipation();await load()}
}
async function voteManual(){
 let name=(document.getElementById('candidate').value||'').trim(),msg=document.getElementById('msg'),cv=document.getElementById('constituency').value.trim(),wv=document.getElementById('ward').value.trim();
 if(name.length<2){msg.textContent='Enter the person’s full name or known alias.';return}
 msg.textContent='Checking the name…';
 try{
  const r=await fetch('/api/candidates/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,race:R.value,county:C.value,constituency:cv,ward:wv})});
  const j=await r.json(),matches=j.matches||[];
  if(matches.length===1){
   msg.textContent='Matched to '+matches[0].name+'. Recording…';
   await submitPhotoPreference(matches[0].id,name);
   return;
  }
  if(matches.length>1){
   msg.textContent='That alias matches more than one person for this seat. Enter the full name.';
   return;
  }
 }catch(e){}
 msg.textContent='No verified alias match yet. Recording exactly what you entered…';
 await submitPhotoPreference(null,name);
}
async function restoreParticipation(){
 if(!C.value)return;
 const response=await fetch('/api/participation/progress?county='+encodeURIComponent(C.value),{cache:'no-store'});
 if(!response.ok)throw new Error('Could not check your saved progress. Please retry.');
 const progress=await response.json();
 renderRaceProgress(progress.completed||[],progress.next_race,progress.complete);
 if(progress.next_race)R.value=progress.next_race;
 if(progress.next_race==='MCA'&&progress.constituency){document.getElementById('constituency').value=progress.constituency;await populateWards()}
 areaMode();await loadCandidates();
 const support=progress.optional_support||{};
 const showSupport=progress.complete&&sessionStorage.getItem('kp_support_dismissed')!=='1';
 document.getElementById('supportbox').style.display=showSupport?'block':'none';
 document.getElementById('msg').textContent=progress.complete?'6 of 6 complete. All responses are saved. Optional support is available below.':progress.completed.length+' of 6 responses saved. Next: '+progress.next_race+'.';
 const supportMsg=document.getElementById('supportmsg');
 const supportButton=document.getElementById('supportPayButton');
 if(progress.complete){
  if(support.configured){
   if(supportMsg)supportMsg.textContent='Voting is complete. If you wish, you can support Kenya Pulse AI from KSh '+(support.minimum_kes||5)+'.';
   if(supportButton){supportButton.disabled=false;supportButton.textContent='Continue to secure Paystack checkout';}
  }else{
   if(supportMsg)supportMsg.textContent='Voting is complete. Optional support is temporarily unavailable; your responses are already saved.';
   if(supportButton){supportButton.disabled=true;supportButton.textContent='Optional support temporarily unavailable';}
  }
  if(showSupport)document.getElementById('supportbox').scrollIntoView({behavior:'smooth',block:'center'});
 }
}
const PARTICIPATION_FLOW=['President','Governor','Senator','Woman Representative','Member of Parliament','MCA'];const RACE_SHORT={'President':'President','Governor':'Governor','Senator':'Senator','Woman Representative':'Woman Rep','Member of Parliament':'MP','MCA':'MCA'};function renderRaceProgress(completed=[],nextRace=null,complete=false){let box=document.getElementById('raceProgress');if(!box)return;box.innerHTML=PARTICIPATION_FLOW.map(r=>{let done=completed.includes(r),current=!complete&&r===nextRace,cls=done?'done':current?'current':'pending',icon=done?'✓':current?'●':'○';return '<div class="raceStep '+cls+'"><span class=check>'+icon+'</span>'+RACE_SHORT[r]+'</div>'}).join('')}
function advanceParticipation(){let i=PARTICIPATION_FLOW.indexOf(R.value);if(i<0)return;if(i<PARTICIPATION_FLOW.length-1){R.value=PARTICIPATION_FLOW[i+1];if(R.value!=='MCA')clearAreas();areaMode();loadCandidates();document.getElementById('msg').textContent='Response recorded. Next: '+R.value+'.';document.getElementById('supportbox').style.display='none';return}document.getElementById('msg').textContent='All six seat responses completed. Your responses are recorded.';document.getElementById('supportbox').style.display='block';document.getElementById('supportbox').scrollIntoView({behavior:'smooth',block:'center'})}
function dismissSupport(){sessionStorage.setItem('kp_support_dismissed','1');document.getElementById('supportbox').style.display='none'}async function supportAmount(preset){let amount=preset||parseInt(document.getElementById('supportCustom').value||'0',10),msg=document.getElementById('supportmsg'),email=(document.getElementById('supportEmail').value||'').trim();if(!Number.isFinite(amount)||amount<5){msg.textContent='Support starts from KSh 5.';return}if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){msg.textContent='Enter a valid email for the payment receipt.';document.getElementById('supportEmail').focus();return}msg.textContent='Opening secure Paystack checkout…';try{let x=await fetch('/api/support/initialize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount,email,county:C.value})}),j=await x.json();if(!x.ok||!j.authorization_url)throw new Error(j.error||'Could not start payment');location.href=j.authorization_url}catch(e){msg.textContent=e.message+'. No money has been taken.'}}async function loadAd(){let x=await fetch('/api/ad?county='+encodeURIComponent(C.value||'')),j=await x.json();if(j.ad){let a=j.ad,box=document.getElementById('liveAd'),k=a.brand_key||'generic';box.innerHTML='<div class="brandAd '+k+'"><div class=brandAdIcon>'+esc(a.icon||'✦')+'</div><div class=brandAdCopy><div class=brandAdName>'+esc(a.business)+'</div><b>'+esc(a.headline||a.business)+'</b><small>'+esc(a.subheadline||'Sponsored message')+'</small><span class=brandAdTag>'+esc(a.tagline||'SPONSORED')+'</span></div><a class=brandAdBtn href="'+a.click_url+'" rel="sponsored noopener">'+esc(a.cta||'LEARN MORE')+' →</a></div>'}}loadAd();
async function loadResultsFor(race){if(!C.value)return;let q='/api/results?county='+encodeURIComponent(C.value)+'&race='+encodeURIComponent(race);if(race==='Member of Parliament'||race==='MCA')q+='&constituency='+encodeURIComponent(document.getElementById('constituency').value.trim());if(race==='MCA')q+='&ward='+encodeURIComponent(document.getElementById('ward').value.trim());let x=await fetch(q,{cache:'no-store'}),j=await x.json();if(!x.ok)throw new Error(j.error||'Could not refresh results');document.getElementById('metricTotal').textContent=j.total;document.getElementById('rt').textContent=C.value+' · '+race;let h='';(j.results||[]).forEach((a,i)=>{let pic=a.photo_url?'<img src="'+escAttr(a.photo_url)+'" alt="">':'<div class="avatar">👤</div>';h+='<div class="topResult">'+pic+'<div><span class=rank>#'+(i+1)+' LIVE</span><b>'+esc(a.candidate)+'</b><div class=bar><div class=fill style="width:'+a.pct+'%"></div></div></div><div class=score>'+a.pct+'%<br><span class=muted style="font-size:11px">'+a.votes+' votes</span></div></div>'});document.getElementById('results').innerHTML=h||'<p class=muted>No responses yet for this county and race.</p>'}async function load(){if(!C.value)return;loadAd();return loadResultsFor(R.value)}function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function escAttr(s){return esc(s)}
let liveCountTimer=setInterval(()=>{if(C.value&&!document.hidden)load()},3000);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&C.value)load()});
</script></body></html>'''


GROUND_CATEGORIES=["Roads & bridges","Water","Health","Agriculture","Education","Security","Waste & environment","Electricity","Other"]
GROUND_HTML=r'''<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Sauti ya Ground · Kenya Pulse AI</title><link rel="stylesheet" href="/pulse95.css"><style>
.groundwrap{max-width:1460px;margin:auto;padding:22px 28px 70px}.hero{padding:30px;border-radius:30px;margin:18px 0}.heroGrid{display:grid;grid-template-columns:1.15fr .85fr;gap:24px;align-items:center}.hero h1{font-size:clamp(44px,5.5vw,76px);line-height:.95;letter-spacing:-3px;margin:8px 0 14px}.hero p{max-width:760px;color:#d5eadc;line-height:1.6}.heroVisual{min-height:220px;border-radius:24px;display:grid;place-items:center;background:radial-gradient(circle at 50% 45%,rgba(105,239,145,.20),transparent 52%),linear-gradient(145deg,rgba(255,255,255,.08),rgba(12,52,35,.22));border:1px solid #ffffff28}.heroVisual span{font-size:86px;filter:drop-shadow(0 18px 25px #001c1244)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.btn{display:inline-flex;align-items:center;justify-content:center;padding:13px 18px;border-radius:14px;background:#69ef91;color:#082717;font-weight:900;text-decoration:none;border:0;cursor:pointer}.btn.alt{background:#ffffff10;color:white;border:1px solid #ffffff2d}
.mainGrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,.72fr);gap:18px}.panel{padding:24px;border-radius:28px}.panel h2{margin:0 0 6px}.muted{color:#bdd1c4}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{margin:8px 0}.field label{display:block;font-size:12px;font-weight:850;margin:0 0 6px;color:#eaffef}.field input,.field select,.field textarea{width:100%;padding:14px 15px;border-radius:14px}.field textarea{resize:vertical;min-height:130px}.selectx{position:relative}.selectbtn{width:100%;min-height:52px;border-radius:14px;padding:0 46px 0 15px;text-align:left;border:1px solid #a8ffd077!important;background:rgba(17,67,45,.72)!important;color:white!important;font-weight:750;box-shadow:inset 0 1px #ffffff33!important;position:relative}.selectbtn:after{content:'⌄';position:absolute;right:16px;font-size:18px}.menu{display:none;position:relative;margin-top:8px;background:rgba(247,253,249,.98);color:#17351f;border:1px solid #d9eee0;border-radius:18px;padding:9px;box-shadow:0 22px 60px #031b1038;max-height:320px;overflow:auto}.selectx.open .menu{display:block}.msearch{position:sticky;top:0;z-index:2;width:100%;padding:11px 13px;border-radius:11px!important;background:#eef4f0!important;color:#183d29!important;border:0!important;margin-bottom:6px}.opt{display:flex;align-items:center;gap:10px;width:100%;border:0!important;background:transparent!important;color:#203c2b!important;text-align:left;padding:10px 11px;border-radius:10px;font-weight:650;box-shadow:none!important}.opt:hover,.opt.active{background:#c9f5d5!important}.opt .oi{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:#e9f8ee}.mediaBox{padding:18px;border:1px dashed #a7ffc65c;border-radius:20px;background:linear-gradient(145deg,rgba(255,255,255,.08),rgba(13,57,39,.18));margin:12px 0}.mediaRow{display:flex;align-items:center;gap:12px}.mediaIcon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:#69ef9121;border:1px solid #83f8a74a}.help{font-size:12px;color:#a9c5b2;line-height:1.45}.captureGrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.captureBtn{min-height:86px;border-radius:18px!important;border:1px solid #ffffff2a!important;background:rgba(255,255,255,.08)!important;color:#f5fff8!important;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;padding:14px!important;box-shadow:inset 0 1px rgba(255,255,255,.12)!important}.captureBtn:hover{background:rgba(105,239,145,.13)!important;border-color:#74f39a66!important}.captureBtn.recording{background:rgba(255,91,91,.16)!important;border-color:#ff7b7b88!important}.captureBtn .capIcon{font-size:24px}.captureBtn b{font-size:13px}.captureBtn small{font-size:10px;color:#a9c5b2;text-align:left}.captureStatus{margin-top:10px;padding:10px 12px;border-radius:13px;background:#061f1580;border:1px solid #ffffff18;font-size:12px;color:#cbe0d1}.capturePreview{display:none;margin-top:10px}.capturePreview audio,.capturePreview video{width:100%;max-height:240px;border-radius:16px;background:#06170f}.filePick{display:none}@media(max-width:680px){.captureGrid{grid-template-columns:1fr 1fr}.captureBtn:last-child{grid-column:1/-1}}
.feedHead{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px}.filters{display:flex;gap:8px;flex-wrap:wrap}.chip{border:1px solid #ffffff25;background:#ffffff0c;color:#eaffef;padding:8px 11px;border-radius:999px;font-size:11px;font-weight:850;cursor:pointer}.chip.active{background:#69ef91;color:#12351e;border-color:#69ef91}.issues{display:grid;gap:12px}.issue{padding:18px;border-radius:20px;background:linear-gradient(145deg,rgba(255,255,255,.09),rgba(9,50,32,.20));border:1px solid #ffffff1e}.issueTop{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.pill{font-size:10px;font-weight:900;letter-spacing:.05em;padding:6px 8px;border-radius:999px;background:#69ef9121;border:1px solid #7bf7a84c;color:#bff7cd}.status{background:#ffffff0d;border-color:#ffffff25}.issue h3{margin:10px 0 6px}.issue p{line-height:1.5}.issueMeta{font-size:12px;color:#a9c5b2}.issue button{margin-top:12px;width:auto}.empty{padding:30px;text-align:center;border:1px dashed #ffffff25;border-radius:20px;color:#bdd1c4}
.sideStack{display:grid;gap:14px}.sideCard{padding:22px;border-radius:24px}.sideCard h3{margin:4px 0 10px}.statline{padding:13px 0;border-bottom:1px solid #ffffff17}.statline:last-child{border-bottom:0}.statline b{display:block;font-size:18px}.notice{padding:14px;border-radius:15px;background:#69ef9112;border:1px solid #69ef913a;margin-top:10px}.msg{margin-top:10px;min-height:20px;font-size:13px}
@media(max-width:980px){.heroGrid,.mainGrid{grid-template-columns:1fr}.heroVisual{display:none}}@media(max-width:680px){.groundwrap{padding:12px 14px 50px}.formgrid{grid-template-columns:1fr}.panel,.hero{padding:19px;border-radius:24px}.hero h1{font-size:48px;letter-spacing:-2px}.feedHead{align-items:flex-start;flex-direction:column}}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/growth">Home</a><a href="/growth#counties">Counties</a><a href="/participate">Participation</a><a href="/county-notices">Notices</a><a href="https://www.facebook.com/people/Kenya-Pulse/61594936328345/" target="_blank" rel="noopener noreferrer">Kenya Pulse AI on Facebook</a></nav><main class=groundwrap><section class="glass hero"><div class=heroGrid><div><div class=kp10-kicker>COMMUNITY VOICE · VERIFIED LOCAL ISSUES</div><h1>Sauti ya <span class=kp95accent>Ground.</span></h1><p>Share what is happening in your area, confirm issues other residents are seeing, and follow reviewed community concerns across counties, constituencies and wards.</p><div class=actions><a class=btn href="#report">Report an issue →</a><a class="btn alt" href="#issues">Explore community issues</a></div></div><div class=heroVisual><span>🗣️</span></div></div></section><section class=mainGrid><div><section class="glass panel" id=report><div class=kp10-kicker>REPORT AN ISSUE</div><h2>Tell us what is happening</h2><p class=muted>Submissions are reviewed before they appear publicly.</p><div class=formgrid><div class=field><label>County</label><div class=selectx id=groundCountySelect><button type=button class=selectbtn onclick="toggleGroundMenu('groundCountySelect')"><span id=groundCountyLabel>Choose county</span></button><div class=menu><input class=msearch placeholder="Search county…" oninput="filterGroundOptions(this,'groundCountyMenu')"><div id=groundCountyMenu><button type=button class="opt active" data-value="" onclick="pickGroundCounty(this)"><span class=oi>🌐</span>Choose county</button>{% for c in counties %}<button type=button class=opt data-value="{{c}}" onclick="pickGroundCounty(this)"><span class=oi>{{marks.get(c,'🌿')}}</span>{{c}}</button>{% endfor %}</div></div></div><input type=hidden id=county></div><div class=field><label>Category</label><div class=selectx id=groundCategorySelect><button type=button class=selectbtn onclick="toggleGroundMenu('groundCategorySelect')"><span id=groundCategoryLabel>{{categories[0]}}</span></button><div class="menu interestMenu" id=groundCategoryMenu>{% for x in categories %}<button type=button class=opt data-value="{{x}}" onclick="pickGroundCategory(this)"><span class=oi>•</span>{{x}}</button>{% endfor %}</div></div><input type=hidden id=category value="{{categories[0]}}"></div><div class=field><label>Constituency</label><input id=constituency placeholder="e.g. Ainabkoi"></div><div class=field><label>Ward</label><input id=ward placeholder="Ward"></div><div class=field><label>Village / landmark</label><input id=landmark placeholder="Optional landmark"></div><div class=field><label>Language</label><select id=language><option>Swahili</option><option>English</option><option>Sheng</option><option>Local language</option></select></div></div><div class=field><label>Describe the issue</label><textarea id=description placeholder="What is happening? Where exactly? What needs attention?"></textarea></div><div class=mediaBox><div class=mediaRow><span class=mediaIcon>🎙️</span><div><b>Add evidence from your phone or computer</b><div class=help>Record voice, capture a short video, or choose an existing photo/video. Your written description remains the required part of the report.</div></div></div><div class=captureGrid id=captureActions><button type=button class=captureBtn id=voiceBtn onclick="toggleVoice()"><span class=capIcon>🎙️</span><b>Record voice</b><small>Tap to start / stop</small></button><button type=button class=captureBtn id=videoBtn onclick="toggleVideo()"><span class=capIcon>🎥</span><b>Capture video</b><small>Use camera + microphone</small></button><button type=button class=captureBtn onclick="document.getElementById('mediaFile').click()"><span class=capIcon>📎</span><b>Choose media</b><small>Photo, audio or video</small></button></div><input class=filePick id=mediaFile type=file accept="audio/*,video/*,image/*" onchange="previewPicked(this)"><div id=captureStatus class=captureStatus>No media attached yet.</div><div id=capturePreview class=capturePreview></div></div><button class=btn onclick=submitIssue()>Submit for review →</button><div id=msg class="msg muted"></div></section><section class="glass panel" id=issues style="margin-top:18px"><div class=feedHead><div><div class=kp10-kicker>COMMUNITY ISSUES</div><h2>What people are reporting</h2></div><div class=filters><button class="chip active" data-filter=ALL onclick="setFilter(this)">All</button><button class=chip data-filter=PUBLISHED onclick="setFilter(this)">Published</button><button class=chip data-filter=ACKNOWLEDGED onclick="setFilter(this)">Acknowledged</button><button class=chip data-filter=UPDATE_PROVIDED onclick="setFilter(this)">Updated</button><button class=chip data-filter=RESOLVED onclick="setFilter(this)">Resolved</button></div></div><div id=feed class=issues>Loading…</div></section></div><aside class=sideStack><section class="glass sideCard"><div class=kp10-kicker>GROUND SNAPSHOT</div><h3>Community activity</h3><div class=statline><b id=totalIssues>0 issues</b><span class=muted>Reviewed and visible</span></div><div class=statline><b id=totalConfirms>0 confirmations</b><span class=muted>Community confirmations across visible issues</span></div><div class=statline><b id=countyCount>0 counties</b><span class=muted>Counties represented in the current feed</span></div></section><section class="glass sideCard"><div class=kp10-kicker>HOW IT WORKS</div><h3>From report to public record</h3><div class=statline><b>1 · Submit</b><span class=muted>Describe the issue and location.</span></div><div class=statline><b>2 · Review</b><span class=muted>Submissions are moderated before publication.</span></div><div class=statline><b>3 · Confirm</b><span class=muted>Other residents can confirm the same issue.</span></div><div class=statline><b>4 · Follow status</b><span class=muted>Published issues can move through acknowledged, updated and resolved states.</span></div></section><section class="glass sideCard"><div class=kp10-kicker>TRANSPARENCY</div><h3>Evidence, not leader rankings</h3><p class=muted>Kenya Pulse AI can show raw issue counts, confirmations and status changes. It does not turn community reports into political leader rankings or endorsements.</p></section></aside></section></main><script>
let allIssues=[],currentFilter='ALL';
let selectedGroundCounty='',selectedGroundCategory='{{categories[0]}}';
function toggleGroundMenu(id){document.querySelectorAll('.selectx').forEach(x=>{if(x.id!==id)x.classList.remove('open')});document.getElementById(id).classList.toggle('open')}
function filterGroundOptions(inp,id){let q=inp.value.toLowerCase();document.querySelectorAll('#'+id+' .opt').forEach(x=>x.style.display=x.textContent.toLowerCase().includes(q)?'flex':'none')}
function pickGroundCounty(el){selectedGroundCounty=el.dataset.value;county.value=selectedGroundCounty;groundCountyLabel.textContent=el.textContent.trim();document.querySelectorAll('#groundCountyMenu .opt').forEach(x=>x.classList.remove('active'));el.classList.add('active');groundCountySelect.classList.remove('open')}
function pickGroundCategory(el){selectedGroundCategory=el.dataset.value;category.value=selectedGroundCategory;groundCategoryLabel.textContent=selectedGroundCategory;document.querySelectorAll('#groundCategoryMenu .opt').forEach(x=>x.classList.remove('active'));el.classList.add('active');groundCategorySelect.classList.remove('open')}
document.addEventListener('click',e=>{if(!e.target.closest('.selectx'))document.querySelectorAll('.selectx').forEach(x=>x.classList.remove('open'))});

let recorder=null,mediaStream=null,mediaChunks=[],capturedBlob=null,capturedType='';
async function toggleVoice(){if(recorder&&recorder.state==='recording'){stopCapture();return}try{mediaStream=await navigator.mediaDevices.getUserMedia({audio:true});startCapture(mediaStream,'audio/webm','voiceBtn','Recording voice… tap again to stop')}catch(e){captureStatus.textContent='Microphone permission was not granted.'}}
async function toggleVideo(){if(recorder&&recorder.state==='recording'){stopCapture();return}try{mediaStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:true});startCapture(mediaStream,'video/webm','videoBtn','Recording video… tap again to stop')}catch(e){captureStatus.textContent='Camera / microphone permission was not granted.'}}
function startCapture(stream,type,buttonId,label){mediaChunks=[];capturedBlob=null;capturedType=type;recorder=new MediaRecorder(stream);recorder.ondataavailable=e=>{if(e.data&&e.data.size)mediaChunks.push(e.data)};recorder.onstop=()=>{capturedBlob=new Blob(mediaChunks,{type:recorder.mimeType||capturedType});showCaptured(capturedBlob);cleanupStream()};recorder.start();document.querySelectorAll('.captureBtn').forEach(x=>x.classList.remove('recording'));document.getElementById(buttonId).classList.add('recording');captureStatus.textContent=label}
function stopCapture(){if(recorder&&recorder.state==='recording')recorder.stop();document.querySelectorAll('.captureBtn').forEach(x=>x.classList.remove('recording'))}
function cleanupStream(){if(mediaStream){mediaStream.getTracks().forEach(t=>t.stop());mediaStream=null}}
function showCaptured(blob){let url=URL.createObjectURL(blob),isVideo=(blob.type||'').startsWith('video');capturePreview.style.display='block';capturePreview.innerHTML=isVideo?'<video controls playsinline src="'+url+'"></video>':'<audio controls src="'+url+'"></audio>';captureStatus.textContent=(isVideo?'Video':'Voice')+' captured · '+Math.max(1,Math.round(blob.size/1024))+' KB · preview ready'}
function previewPicked(inp){let f=inp.files&&inp.files[0];if(!f)return;capturedBlob=f;capturedType=f.type||'';let url=URL.createObjectURL(f),kind=f.type.startsWith('image')?'image':f.type.startsWith('video')?'video':'audio';capturePreview.style.display='block';capturePreview.innerHTML=kind==='image'?'<img src="'+url+'" style="width:100%;max-height:260px;object-fit:cover;border-radius:16px">':kind==='video'?'<video controls playsinline src="'+url+'"></video>':'<audio controls src="'+url+'"></audio>';captureStatus.textContent=f.name+' · '+Math.max(1,Math.round(f.size/1024))+' KB · selected'}

async function submitIssue(){msg.textContent='Submitting…';let d={county:county.value,constituency:constituency.value,ward:ward.value,landmark:landmark.value,category:category.value,description:description.value,language:language.value};let r=await fetch('/api/ground/issues',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}),j=await r.json();msg.textContent=(j.message||j.error)+(r.ok&&capturedBlob?' Media preview captured locally; permanent media upload is not connected yet.':'');if(r.ok){description.value='';landmark.value='';load()}}
function setFilter(el){document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));el.classList.add('active');currentFilter=el.dataset.filter;render()}
function render(){let rows=currentFilter==='ALL'?allIssues:allIssues.filter(x=>x.status===currentFilter);feed.innerHTML=rows.map(x=>'<article class=issue><div class=issueTop><span class=pill>'+esc(x.category)+'</span><span class="pill status">'+esc(x.status.replaceAll('_',' '))+'</span></div><h3>'+esc(x.ward||x.constituency||x.county)+'</h3><p>'+esc(x.description)+'</p><div class=issueMeta>'+esc(x.county)+(x.constituency?' · '+esc(x.constituency):'')+(x.ward?' · '+esc(x.ward):'')+(x.landmark?' · '+esc(x.landmark):'')+' · '+x.confirmations+' community confirmation'+(x.confirmations===1?'':'s')+'</div><button class="btn alt" onclick="confirmIssue('+x.id+')">Same issue here</button></article>').join('')||'<div class=empty>No reviewed community issues match this filter yet.</div>';totalIssues.textContent=allIssues.length+' issue'+(allIssues.length===1?'':'s');totalConfirms.textContent=allIssues.reduce((a,x)=>a+(Number(x.confirmations)||0),0)+' confirmations';countyCount.textContent=new Set(allIssues.map(x=>x.county)).size+' counties'}
async function load(){try{let r=await fetch('/api/ground/issues',{cache:'no-store'}),j=await r.json();allIssues=j.issues||[];render()}catch(e){feed.innerHTML='<div class=empty>Community feed is temporarily unavailable.</div>'}}
async function confirmIssue(id){await fetch('/api/ground/issues/'+id+'/confirm',{method:'POST'});load()}
function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}load()
</script></body></html>'''

@app.get("/ground")
def ground():
 return render_template_string(GROUND_HTML,counties=COUNTIES,categories=GROUND_CATEGORIES,marks=COUNTY_MARKS)

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

@app.get("/participate")
def participate():
 src=re.sub(r"[^a-zA-Z0-9_-]","",request.args.get("src","direct"))[:60]
 with conn() as c:c.execute("INSERT INTO pulse_visits(county,source,path) VALUES(?,?,?)",(None,src,"/participate"))
 return render_template_string(HTML,counties=COUNTIES,races=RACES,initial_county="",county_mark="🇰🇪")

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
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Advertise • Kenya Pulse AI</title><link rel="stylesheet" href="/pulse95.css"><style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 10%,#00ff8840,transparent 28%),radial-gradient(circle at 90% 10%,#ffd90030,transparent 25%),#020806;color:white;font-family:Inter,system-ui}.w{max-width:1000px;margin:auto;padding:35px 18px}.glass{background:linear-gradient(135deg,#ffffff18,#ffffff06);border:1px solid #ffffff30;box-shadow:inset 0 1px #ffffff45,0 30px 90px #0008;backdrop-filter:blur(35px) saturate(170%);border-radius:30px}.hero,.form{padding:28px;margin-bottom:16px}h1{font-size:clamp(42px,7vw,70px);margin:8px 0}.muted{color:#a9beb1}.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.p{padding:20px}.price{font-size:32px;font-weight:900;color:#8df7ac}input,select,button{width:100%;padding:15px;margin:6px 0;border-radius:15px;border:1px solid #ffffff25;background:#ffffff0b;color:white;font:inherit}option{color:#12351f}button{background:linear-gradient(135deg,#69ef91,#a8f7be);color:#04120a;font-weight:900;cursor:pointer}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.notice{padding:14px;border:1px solid #8affb855;border-radius:14px;background:#48ff9a10}.tag{font:700 11px monospace;letter-spacing:.15em;color:#7dffb7}@media(max-width:700px){.plans,.grid{grid-template-columns:1fr}}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/participate">Participation</a><a href="/growth">Counties</a><a href="/ground">Sauti ya Ground</a></nav><main class="w kp95wrap"><section class="glass hero"><div class=tag>KENYA PULSE AI • ADVERTISER STUDIO</div><h1>Put your brand<br>inside the pulse.</h1><p class=muted>Choose a national or county placement. Advertising is clearly labelled and kept separate from participation choices and results.</p></section><section class=plans><div class="glass p"><b>COUNTY STARTER</b><div class=price>KSh 5K</div><span class=muted>County placement</span></div><div class="glass p"><b>COUNTY PRO</b><div class=price>KSh 15K</div><span class=muted>Premium county placement</span></div><div class="glass p"><b>NATIONAL</b><div class=price>KSh 50K</div><span class=muted>Across the network</span></div></section><form class="glass form" method=post><h2>Launch a campaign</h2>{% if notice %}<p class=notice>{{notice}}</p>{% endif %}<div class=grid><input name=business required placeholder="Business / brand"><input type=email name=email required placeholder="Business email"><input name=phone placeholder="Phone number"><input name=headline placeholder="Ad headline"></div><div class=grid><select name=scope id=scope onchange="county.disabled=this.value==='National'"><option>County</option><option>National</option></select><select name=county id=county>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select></div><select name=package><option>County Starter</option><option>County Pro</option><option>National</option></select><input name=url placeholder="Business website / campaign link (optional)"><button>SUBMIT CAMPAIGN FOR REVIEW →</button><p class=muted>No payment is collected at this stage. Approved campaigns can be connected to M-Pesa once merchant payment credentials are configured.</p></form></main></body></html>"""
 return render_template_string(html,counties=COUNTIES,notice=notice)


@app.post("/api/analytics/pageview")
def analytics_pageview():
 data=request.get_json(silent=True) or {}; path=str(data.get("path") or "/")[:180]; county=str(data.get("county") or "")[:60]; src=re.sub(r"[^a-zA-Z0-9_-]","",str(data.get("source") or "direct"))[:60]; sid=str(data.get("session_id") or "")[:80]
 if county and county not in COUNTIES:county=""
 with conn() as db:db.execute("INSERT INTO pulse_visits(county,source,path,session_id) VALUES(?,?,?,?)",(county or None,src,path,sid or None))
 return jsonify(ok=True)

AD_PACKAGE_WEIGHTS={"House Ad":0.35,"County Starter":1.0,"County Pro":2.5,"National":4.0}
AD_RECENT_HOURS=6

def _ad_time(v):
 if not v:return None
 if isinstance(v,dt.datetime):
  return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
 try:return dt.datetime.fromisoformat(str(v).replace("Z","+00:00")).replace(tzinfo=dt.timezone.utc) if "T" not in str(v) else dt.datetime.fromisoformat(str(v).replace("Z","+00:00"))
 except Exception:return None

def _ad_pacing_multiplier(ad):
 goal=int(ad.get("impression_goal") or 0);served=int(ad.get("impressions") or 0)
 if goal<=0:return 1.0/(1.0+served/250.0)
 start=_ad_time(ad.get("starts_at"));end=_ad_time(ad.get("ends_at"));now=dt.datetime.now(dt.timezone.utc)
 if start and end and end>start:
  frac=max(0.02,min(1.0,(now-start).total_seconds()/(end-start).total_seconds()))
 else:
  frac=min(1.0,max(0.02,served/max(goal,1)))
 expected=max(1.0,goal*frac);deficit=max(0.0,expected-served)
 return 1.0+min(6.0,deficit/max(1.0,goal/20.0))

def _ad_choose(weighted):
 if not weighted:return None
 scaled=[max(1,int(w*1000)) for _,w in weighted];total=sum(scaled);pick=secrets.randbelow(total)
 for (ad,_),w in zip(weighted,scaled):
  if pick<w:return ad
  pick-=w
 return weighted[-1][0]

def _eligible_ads(db,county,visitor_hash,scope_mode="mixed",exclude_ids=None):
 exclude_ids=set(exclude_ids or [])
 sql="""SELECT id,business,headline,url,scope,county,package,budget,starts_at,ends_at,
               COALESCE(impressions,0) impressions,COALESCE(clicks,0) clicks,
               COALESCE(impression_goal,0) impression_goal,
               COALESCE(daily_impression_cap,0) daily_impression_cap,
               COALESCE(frequency_cap,3) frequency_cap,
               COALESCE(priority_weight,0) priority_weight,last_served_at
        FROM ad_orders
        WHERE status='ACTIVE'
          AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
          AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)
          AND (COALESCE(impression_goal,0)=0 OR COALESCE(impressions,0)<impression_goal)"""
 args=[]
 if scope_mode=="national":
  sql+=" AND scope='National'"
 else:
  sql+=" AND (scope='National' OR county=?)";args.append(county)
 sql+=" ORDER BY id LIMIT 5000"
 rows=[dict(x) for x in db.execute(sql,args).fetchall()]
 if not rows:return []

 # House inventory never competes with paid campaigns. Use it only when no paid ad is eligible.
 paid_rows=[a for a in rows if a.get("package")!="House Ad"]
 house_rows=[a for a in rows if a.get("package")=="House Ad"]
 rows=paid_rows if paid_rows else house_rows

 recent_sql="""SELECT ad_id,COUNT(*) n FROM ad_impression_events
               WHERE visitor_hash=? AND created_at>=CURRENT_TIMESTAMP-INTERVAL '6 hours'
               GROUP BY ad_id""" if db.pg else """SELECT ad_id,COUNT(*) n FROM ad_impression_events
               WHERE visitor_hash=? AND datetime(created_at)>=datetime('now','-6 hours')
               GROUP BY ad_id"""
 day_sql="""SELECT ad_id,COUNT(*) n FROM ad_impression_events
            WHERE created_at>=date_trunc('day',CURRENT_TIMESTAMP)
            GROUP BY ad_id""" if db.pg else """SELECT ad_id,COUNT(*) n FROM ad_impression_events
            WHERE datetime(created_at)>=datetime('now','start of day')
            GROUP BY ad_id"""
 recent={int(x["ad_id"]):int(x["n"]) for x in db.execute(recent_sql,(visitor_hash,)).fetchall()}
 daily={int(x["ad_id"]):int(x["n"]) for x in db.execute(day_sql).fetchall()}
 eligible=[]
 for ad in rows:
  aid=int(ad["id"])
  if aid in exclude_ids:continue
  fcap=max(1,int(ad.get("frequency_cap") or 3))
  if recent.get(aid,0)>=fcap:continue
  dcap=int(ad.get("daily_impression_cap") or 0)
  if dcap>0 and daily.get(aid,0)>=dcap:continue
  eligible.append(ad)
 if scope_mode!="national" and county:
  local=[a for a in eligible if a.get("scope")=="County" and a.get("county")==county]
  if local:eligible=local
  else:eligible=[a for a in eligible if a.get("scope")=="National"]
 return eligible

def _pick_ad(db,county,visitor_hash,scope_mode="mixed",exclude_ids=None,exclude_businesses=None):
 ads=_eligible_ads(db,county,visitor_hash,scope_mode,exclude_ids)
 excluded={str(x).lower() for x in (exclude_businesses or [])}
 if excluded:
  narrowed=[a for a in ads if str(a.get("business") or "").lower() not in excluded]
  if narrowed:ads=narrowed
 weighted=[]
 for ad in ads:
  base=float(ad.get("priority_weight") or 0) or AD_PACKAGE_WEIGHTS.get(ad.get("package"),1.0)
  if ad.get("package")=="House Ad":
   weight=max(0.2,float(ad.get("priority_weight") or 1.0))
  else:
   fairness=1.0/(1.0+int(ad.get("impressions") or 0)/1000.0)
   weight=max(0.05,base*_ad_pacing_multiplier(ad)*(0.65+fairness))
  weighted.append((ad,weight))
 return _ad_choose(weighted)

def _record_ad_impression(db,ad,visitor_hash,slot,county=""):
 db.execute("UPDATE ad_orders SET impressions=COALESCE(impressions,0)+1,last_served_at=CURRENT_TIMESTAMP WHERE id=?",(ad["id"],))
 db.execute("INSERT INTO ad_impression_events(ad_id,visitor_hash,slot,county) VALUES(?,?,?,?)",(ad["id"],visitor_hash,slot,county or None))

HOUSE_BRANDS={
 "BeatHub":{"brand_key":"beathub","tagline":"THE HOME OF COOL BEATS","cta":"EXPLORE BEATHUB","icon":"🎧","subheadline":"Buy, sell and discover beats from producers.","accent":"gold"},
 "Mkulima AI":{"brand_key":"mkulima","tagline":"FARM SMARTER","cta":"ASK MKULIMA AI","icon":"🌱","subheadline":"Practical farming help, market guidance and selling decisions.","accent":"leaf"},
 "Mizizi":{"brand_key":"mizizi","tagline":"KEEP THE STORY ALIVE","cta":"EXPLORE MIZIZI","icon":"🌳","subheadline":"Preserve family trees, elders’ stories, photos, voices and memories.","accent":"heritage"},
 "OneBob":{"brand_key":"onebob","tagline":"SAVE TOGETHER","cta":"OPEN ONEBOB","icon":"1","subheadline":"A simple online chama for everyday Kenyan groups.","accent":"lime"}
}
def _public_ad(ad):
 meta=HOUSE_BRANDS.get(ad["business"],{"brand_key":"generic","tagline":"SPONSORED","cta":"LEARN MORE","icon":"✦","subheadline":ad.get("headline") or "Sponsored message","accent":"green"})
 return {"id":ad["id"],"business":ad["business"],"headline":ad["headline"],"scope":ad["scope"],"county":ad.get("county"),"package":ad.get("package"),"click_url":"/api/ad-click/"+str(ad["id"]),**meta}

@app.get("/api/ad-strip")
def ad_strip():
 visitor=participation_fingerprint();items=[];used=[];brands=[]
 with conn() as db:
  for _ in range(8):
   ad=_pick_ad(db,"",visitor,"national",used,brands)
   if not ad:break
   _record_ad_impression(db,ad,visitor,"national_strip")
   used.append(int(ad["id"]));brands.append(ad.get("business") or "")
   items.append(_public_ad(ad))
 return jsonify(items=items,rotation="weighted_fair_paced"),200,{"Cache-Control":"private, no-store"}

@app.get("/api/ad")
def serve_ad():
 county=(request.args.get("county") or "").strip()
 if county and county not in COUNTIES:county=""
 visitor=participation_fingerprint()
 with conn() as db:
  ad=_pick_ad(db,county,visitor,"mixed")
  if not ad:return jsonify(ad=None),200,{"Cache-Control":"private, no-store"}
  _record_ad_impression(db,ad,visitor,"county_card" if county else "general_card",county)
  return jsonify(ad=_public_ad(ad)),200,{"Cache-Control":"private, no-store"}

@app.get("/api/ad-click/<int:ad_id>")
def ad_click(ad_id):
 with conn() as db:
  row=db.execute("""SELECT url FROM ad_orders WHERE id=? AND status='ACTIVE'
                    AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
                    AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)""",(ad_id,)).fetchone()
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

KENYA_COUNTY_SVG_PATHS={"Turkana":"M228.4 52.5L219.3 39.9L220.4 18.7L199.2 15.0L149.2 61.5L154.7 71.5L152.9 75.4L157.8 74.6L156.1 79.0L167.3 82.8L164.9 94.3L172.6 111.4L179.0 115.4L179.3 120.2L188.4 131.5L188.3 125.8L190.5 129.2L194.7 122.5L202.0 149.8L207.6 156.1L218.5 160.0L230.2 179.0L233.2 179.0L241.6 188.7L244.1 185.7L241.5 173.5L244.4 170.2L237.8 167.7L237.9 165.0L244.3 161.9L248.2 143.0L254.3 135.5L247.2 131.3L240.3 113.9L231.3 109.3L231.9 100.5L228.5 99.3L228.4 52.5Z","Marsabit":"M249.6 131.4L255.6 127.2L261.7 146.3L275.2 151.7L287.4 171.3L297.8 167.9L301.6 170.6L301.4 175.4L316.5 163.3L318.2 156.2L340.5 143.3L337.7 126.2L343.6 121.0L344.5 113.1L355.3 105.9L354.2 90.4L345.4 87.7L338.3 89.1L324.0 83.6L308.4 85.4L266.2 55.2L258.9 52.7L228.4 52.5L228.5 99.3L231.9 100.5L231.3 109.3L240.3 113.9L247.2 131.3L249.6 131.4Z","Mandera":"M418.6 140.1L418.6 115.3L431.6 102.6L453.8 71.0L426.9 72.5L409.6 59.2L375.3 75.2L371.8 82.8L372.2 95.7L382.1 99.9L390.8 109.7L399.4 113.6L406.7 132.8L418.6 140.1Z","Wajir":"M340.5 143.3L350.2 158.7L359.2 165.2L352.5 167.5L363.6 194.9L370.2 203.8L396.1 216.9L405.4 215.1L418.6 206.1L418.6 140.1L406.7 132.8L399.4 113.6L390.8 109.7L382.1 99.9L372.2 95.7L371.8 82.8L363.2 90.6L354.2 90.4L355.3 105.9L344.5 113.1L343.6 121.0L337.7 126.2L340.5 143.3Z","West Pokot":"M185.8 129.5L183.3 131.7L189.0 150.0L187.5 159.9L180.0 171.6L181.2 175.4L200.5 181.0L214.8 173.3L218.5 160.0L207.6 156.1L202.0 149.8L194.7 122.5L190.5 129.2L188.3 125.8L188.4 131.5L185.8 129.5Z","Samburu":"M241.6 188.7L245.0 192.7L257.9 191.1L259.8 195.6L274.8 194.6L280.0 201.8L285.4 202.3L305.3 193.0L301.6 170.6L297.8 167.9L287.4 171.3L275.2 151.7L261.7 146.3L255.6 127.2L249.6 131.4L254.3 135.5L248.2 143.0L244.3 161.9L237.9 165.0L237.8 167.7L244.4 170.2L241.5 173.5L244.1 185.7L241.6 188.7Z","Isiolo":"M301.4 175.4L305.3 193.0L300.1 196.6L285.4 202.3L280.0 201.8L274.8 194.6L267.8 194.0L262.3 195.4L259.8 202.2L279.2 204.3L279.8 213.5L285.6 213.9L287.5 205.5L305.8 198.3L311.0 215.2L319.7 226.7L324.6 224.7L333.0 226.7L328.9 204.4L347.4 197.4L353.9 188.5L359.7 185.7L352.5 167.5L359.2 165.2L350.2 158.7L340.5 143.3L318.2 156.2L316.5 163.3L301.4 175.4Z","Baringo":"M218.5 160.0L211.0 200.8L215.7 215.8L210.6 217.5L208.1 223.4L214.5 226.9L217.4 232.7L218.4 228.3L224.5 226.0L224.1 220.3L230.8 223.8L237.1 199.7L245.3 191.4L233.2 179.0L230.2 179.0L218.5 160.0Z","Elgeyo-Marakwet":"M193.9 177.9L207.1 192.5L207.3 197.0L203.7 196.9L207.7 216.8L214.7 217.5L215.7 210.3L211.0 200.8L214.8 173.3L200.5 181.0L193.9 177.9Z","Trans Nzoia":"M181.2 175.4L171.9 179.7L180.6 193.1L201.8 187.7L193.9 177.9L181.2 175.4Z","Bungoma":"M171.9 179.7L163.4 194.1L166.2 195.5L164.6 206.0L170.0 207.0L175.2 206.3L186.0 195.1L190.5 194.5L188.7 189.9L180.6 193.1L171.9 179.7Z","Garissa":"M359.7 185.7L353.9 188.5L347.4 197.4L328.9 204.4L331.7 222.4L333.0 226.7L344.6 225.9L358.4 231.3L364.6 237.2L368.8 246.8L371.8 247.6L381.9 281.4L386.4 285.9L388.4 302.3L415.2 290.0L440.6 287.9L418.6 255.9L418.6 206.1L405.4 215.1L393.2 216.3L370.2 203.8L359.7 185.7Z","Uasin Gishu":"M210.6 217.5L207.7 216.8L203.7 196.9L207.3 197.0L206.0 189.4L192.9 188.7L193.9 198.6L182.2 203.0L194.4 203.0L193.4 205.9L204.7 223.0L208.1 223.4L210.6 217.5Z","Kakamega":"M188.7 189.9L190.5 194.5L186.0 195.1L175.2 206.3L162.7 207.6L165.8 209.6L164.3 217.0L171.1 220.4L187.1 214.8L182.2 203.0L193.9 198.6L193.6 192.2L188.7 189.9Z","Laikipia":"M245.3 191.4L237.1 199.7L233.6 211.9L237.5 219.7L236.3 224.9L244.3 218.6L250.9 223.4L248.5 228.1L258.4 228.0L259.4 233.8L264.4 235.4L264.1 226.7L267.4 223.9L269.7 226.1L268.4 223.1L279.8 213.5L279.2 204.3L259.8 202.2L262.3 195.4L257.9 191.1L245.3 191.4Z","Busia":"M163.4 194.1L154.8 201.5L153.4 210.6L146.2 219.7L147.6 225.0L151.1 224.0L155.6 212.5L163.8 212.2L165.8 209.6L162.7 207.6L166.2 195.5L163.4 194.1Z","Meru":"M279.8 213.5L268.4 223.1L276.8 229.8L292.8 231.8L301.9 221.4L319.7 226.7L311.0 215.2L305.8 198.3L287.5 205.5L285.6 213.9L279.8 213.5Z","Nandi":"M182.2 203.0L187.1 214.8L178.3 225.0L204.3 227.0L203.5 220.0L193.4 205.9L194.4 203.0L182.2 203.0Z","Siaya":"M163.8 212.2L154.0 214.7L151.1 224.0L147.6 225.0L147.5 237.0L158.7 237.0L162.0 240.1L168.1 235.5L165.3 227.0L170.1 224.6L171.1 220.4L164.3 217.0L163.8 212.2Z","Nakuru":"M212.6 226.4L213.0 234.3L203.9 235.7L211.1 249.9L212.8 251.1L218.8 241.6L224.3 245.4L225.8 248.3L221.7 248.5L224.4 251.5L230.2 248.6L234.3 259.9L247.0 268.5L249.4 262.2L246.4 248.0L241.9 246.1L240.3 238.6L235.0 237.7L234.1 229.4L237.5 219.7L235.6 216.0L232.1 216.9L230.0 224.5L224.1 220.3L224.5 226.0L218.4 228.3L217.4 232.7L212.6 226.4Z","Vihiga":"M171.1 220.4L170.1 224.6L178.3 225.0L185.1 216.4L171.1 220.4Z","Nyandarua":"M236.3 224.9L235.0 237.7L240.3 238.6L241.9 246.1L246.4 248.0L247.8 259.4L251.5 253.4L254.2 255.1L249.7 238.5L252.9 230.7L247.4 226.7L251.5 224.7L244.3 218.6L236.3 224.9Z","Tharaka":"M276.8 229.8L295.2 241.2L299.1 237.2L302.1 239.8L303.4 234.3L307.8 234.6L315.4 226.0L301.9 221.4L292.8 231.8L276.8 229.8Z","Kericho":"M208.1 223.4L204.7 223.0L202.3 228.3L196.7 228.1L201.2 233.1L192.9 230.0L188.3 237.1L190.0 243.2L203.7 239.6L203.9 235.7L213.0 234.3L212.3 225.4L208.1 223.4Z","Kisumu":"M178.3 225.0L172.2 224.0L165.3 227.0L168.1 235.5L176.7 234.0L186.4 240.0L192.9 230.0L201.2 233.1L196.5 225.4L178.3 225.0Z","Nyeri":"M251.8 229.9L249.7 238.5L253.6 248.2L256.4 245.8L271.5 248.5L276.8 229.8L267.4 223.9L264.1 226.7L264.4 235.4L259.4 233.8L258.4 228.0L251.8 229.9Z","Tana River":"M320.2 226.9L340.2 263.7L342.5 297.7L334.1 314.1L327.6 316.8L344.8 341.1L350.3 342.3L376.2 312.9L388.0 329.2L398.1 320.5L405.2 321.5L407.6 318.7L390.0 316.3L386.4 285.9L381.9 281.4L371.8 247.6L368.8 246.8L362.8 235.2L344.6 225.9L335.6 227.6L324.6 224.7L320.2 226.9Z","Kitui":"M319.7 226.7L315.4 226.0L307.8 234.6L303.4 234.3L298.9 244.6L297.6 264.1L295.6 267.4L287.8 265.8L287.9 268.2L295.9 277.7L291.7 281.7L297.2 289.1L302.0 307.4L305.3 307.4L314.0 317.3L321.8 337.0L334.5 342.1L344.8 341.1L327.6 316.8L334.1 314.1L342.5 297.7L340.2 263.7L319.7 226.7Z","Kirinyaga":"M271.5 248.5L275.3 254.2L283.4 251.5L276.8 229.8L271.5 248.5Z","Embu":"M276.8 229.8L283.7 247.7L283.4 251.5L275.3 254.2L285.5 259.1L293.5 253.9L299.0 254.9L301.0 240.5L299.1 237.2L292.7 241.3L276.8 229.8Z","Homa Bay":"M147.5 237.0L146.7 254.9L157.3 252.9L161.8 257.3L167.4 257.2L170.9 249.8L188.8 239.9L181.2 238.9L176.7 234.0L170.0 234.5L162.0 240.1L158.7 237.0L147.5 237.0Z","Bomet":"M205.4 238.1L190.0 243.2L191.5 254.8L188.5 258.3L197.2 263.8L208.4 255.0L203.2 248.8L210.6 246.7L205.4 238.1Z","Nyamira":"M188.8 239.9L182.7 243.3L180.1 250.5L188.5 258.3L191.5 254.8L188.8 239.9Z","Narok":"M210.6 246.7L203.2 248.8L208.4 255.0L197.2 263.8L188.5 258.3L172.2 263.2L177.6 277.5L226.5 305.0L228.5 281.4L239.8 269.3L239.3 264.4L236.6 259.2L234.3 259.9L230.2 248.6L224.4 251.5L221.7 248.5L225.8 248.3L224.3 245.4L218.8 241.6L212.8 251.1L210.6 246.7Z","Kisii":"M174.0 249.6L174.2 261.3L188.5 258.3L180.1 250.5L182.7 243.3L174.0 249.6Z","Murang'a":"M254.2 255.1L266.2 263.6L275.1 263.2L278.9 266.2L281.1 263.9L273.6 256.8L274.1 250.1L266.8 246.9L254.2 246.0L254.2 255.1Z","Migori":"M146.7 254.9L147.0 262.6L177.6 277.5L172.2 263.2L174.0 249.6L170.9 249.8L167.4 257.2L161.8 257.3L157.3 252.9L146.7 254.9Z","Kiambu":"M247.0 268.5L245.3 273.0L252.0 274.6L261.0 268.7L268.9 272.6L273.1 265.6L278.9 266.2L275.1 263.2L266.2 263.6L251.5 253.4L247.0 268.5Z","Machakos":"M297.5 255.2L291.2 254.6L285.5 259.1L275.3 254.2L273.6 256.8L281.1 263.9L278.2 267.0L273.1 265.6L267.9 274.2L261.3 276.6L263.5 279.4L260.7 278.5L271.1 292.5L280.7 288.8L277.9 284.1L279.6 282.5L288.8 284.3L290.4 287.3L294.0 284.3L291.7 281.7L295.9 277.7L287.8 265.8L295.6 267.4L297.5 255.2Z","Kajiado":"M239.3 264.4L239.8 269.3L228.5 281.4L226.5 305.0L285.0 337.8L290.8 341.7L291.6 346.4L297.8 346.3L300.4 328.1L288.0 313.0L292.8 308.2L272.1 298.5L264.0 280.9L245.3 273.0L247.0 268.5L239.3 264.4Z","Nairobi City":"M252.0 274.6L263.5 279.4L261.3 276.6L268.9 272.6L261.0 268.7L252.0 274.6Z","Makueni":"M294.0 284.3L290.4 287.3L288.8 284.3L279.6 282.5L277.9 284.1L280.7 288.8L271.1 292.5L270.8 296.7L292.8 308.2L288.0 313.0L301.1 330.9L309.7 327.2L321.1 339.1L323.4 338.5L314.0 317.3L305.3 307.4L302.0 307.4L294.0 284.3Z","Lamu":"M388.4 302.3L390.0 316.3L410.0 318.3L411.8 314.7L407.6 311.0L415.9 309.5L415.2 301.4L410.1 298.7L417.3 303.7L420.7 295.8L418.3 298.9L420.0 302.6L427.6 296.6L429.6 300.3L440.6 287.9L415.2 290.0L388.4 302.3ZM422.2 303.0L421.1 306.2L425.1 303.8L422.2 303.0ZM414.8 310.5L411.8 313.8L414.8 310.5ZM417.9 309.6L415.2 309.6L415.6 312.8L417.9 309.6ZM421.0 304.3L418.3 306.1L421.0 304.3Z","Kilifi":"M350.3 342.3L345.5 361.3L355.3 362.8L356.5 370.5L363.8 377.7L369.2 374.3L371.4 376.0L375.4 366.2L372.1 363.4L375.2 363.9L378.7 352.0L384.7 350.8L388.5 339.5L386.0 341.2L387.2 328.5L376.2 312.9L350.3 342.3Z","Taita-Taveta":"M344.8 341.1L321.1 339.1L309.7 327.2L301.1 330.9L297.8 346.3L291.6 346.4L292.3 351.3L287.5 356.4L288.2 359.4L293.6 360.4L295.1 365.4L320.6 383.3L339.8 376.4L343.4 368.7L339.2 366.3L340.6 363.8L344.3 366.0L350.3 342.3L344.8 341.1Z","Kwale":"M345.5 361.3L344.3 366.0L340.6 363.8L339.2 366.3L343.4 368.7L339.8 376.4L320.6 383.3L350.3 404.0L354.8 399.5L353.9 402.3L357.5 402.7L356.7 399.5L362.8 394.3L366.0 383.5L362.0 374.7L356.5 370.5L355.3 362.8L345.5 361.3Z","Mombasa":"M366.9 376.1L368.4 380.3L371.4 376.4L366.9 376.1ZM363.8 377.7L366.5 379.6L365.2 376.1L363.8 377.7ZM366.0 383.5L366.1 380.4L366.0 383.5Z"}
KENYA_COUNTY_CENTERS={"Turkana":[209.4,116.9],"Marsabit":[291,119.1],"Mandera":[405.4,100.7],"Wajir":[371.2,140.7],"West Pokot":[193.6,148.5],"Samburu":[261.4,171.6],"Isiolo":[310.1,192.7],"Baringo":[222.4,205.3],"Elgeyo-Marakwet":[206.4,194.7],"Trans Nzoia":[185.1,181.5],"Bungoma":[175.4,194.6],"Garissa":[374.6,233.8],"Uasin Gishu":[201.2,206.2],"Kakamega":[180.8,203],"Laikipia":[255.7,214.9],"Busia":[157.8,208.9],"Meru":[291.7,217.5],"Nandi":[190.7,212.7],"Siaya":[160.6,225.1],"Nakuru":[227.1,238.7],"Vihiga":[175.1,221.4],"Nyandarua":[245.4,237.7],"Tharaka":[297.1,232.6],"Kericho":[201.9,231.1],"Kisumu":[181.6,229.9],"Nyeri":[260.4,234.9],"Tana River":[360.1,282.9],"Kitui":[312.4,280.5],"Kirinyaga":[275.7,246.5],"Embu":[287.9,245.4],"Homa Bay":[164.3,243.9],"Bomet":[200,249.6],"Nyamira":[186.7,247.8],"Narok":[215.8,258.8],"Kisii":[178.9,252.1],"Murang'a":[267.8,256.7],"Migori":[162.2,258],"Kiambu":[260.5,267.1],"Machakos":[281.7,272.4],"Kajiado":[265.5,301.7],"Nairobi City":[259.8,274.4],"Makueni":[294.7,304.1],"Lamu":[415.7,305.1],"Kilifi":[369.8,354.4],"Taita-Taveta":[318.2,354.3],"Kwale":[349.7,380.1],"Mombasa":[366.5,378.9]}

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
 # Real Kenya county geometry: each county shape is clickable.
 map_cards=[]
 for x in cards:
  d=KENYA_COUNTY_SVG_PATHS.get(x["county"],"")
  center=KENYA_COUNTY_CENTERS.get(x["county"])
  if d and center:map_cards.append(dict(x,path=d,cx=center[0],cy=center[1]))
 active_cards=[x for x in cards if x["responses"] or x["visits"]][:6] or cards[:6]
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Kenya Pulse AI — National Dashboard</title><style>
*{box-sizing:border-box}body{margin:0;color:#f8fff9;font-family:Inter,ui-sans-serif,system-ui;background:#123d28;min-height:100vh}.world{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 82% 13%,#ffe9a0 0 2%,#ffd98544 2.3% 8%,transparent 18%),linear-gradient(180deg,#8db7ad 0 27%,#709777 42%,#245b38 68%,#0d3420 100%)}.world:before{content:'';position:absolute;inset:27% -10% -10%;background:linear-gradient(160deg,transparent 0 17%,#547b50 17.4% 28%,transparent 28.4%),linear-gradient(25deg,transparent 0 24%,#397047 24.4% 50%,transparent 50.4%),linear-gradient(160deg,transparent 0 44%,#185435 44.4% 70%,transparent 70.4%);filter:blur(2px)}.world:after{content:'';position:absolute;inset:55% -5% -5%;background:radial-gradient(ellipse at 60% 15%,#91c1a077,transparent 30%),linear-gradient(8deg,#0d3c25 0 48%,transparent 48.5%),linear-gradient(-9deg,#22613a 0 57%,transparent 57.5%);filter:blur(2px)}.shade{position:fixed;inset:0;z-index:-2;background:linear-gradient(90deg,#0626179e 0,transparent 45%),linear-gradient(0deg,#06241699 0,transparent 45%)}.wrap{max-width:1380px;margin:auto;padding:18px 24px 70px}.glass{background:linear-gradient(135deg,rgba(19,55,38,.62),rgba(37,72,55,.38));border:1px solid rgba(236,255,242,.28);box-shadow:inset 0 1px rgba(255,255,255,.32),0 18px 50px rgba(3,27,15,.22);backdrop-filter:blur(20px) saturate(112%);-webkit-backdrop-filter:blur(20px) saturate(112%)}.nav{height:66px;border-radius:24px;display:flex;align-items:center;padding:0 20px;gap:26px}.brand{font-size:21px;font-weight:950;margin-right:auto}.brand b{color:#7cf39d}.nav a{color:#edf8f1;text-decoration:none;font-size:13px}.search{width:min(340px,32vw);padding:12px 16px;border-radius:15px;border:1px solid #ffffff22;background:#0b2d1e66;color:white}.hero{min-height:390px;display:grid;grid-template-columns:1.2fr .8fr;align-items:center;gap:40px;padding:52px 28px 28px}.hero h1{font-size:clamp(48px,6vw,82px);line-height:.94;letter-spacing:-3px;margin:12px 0 20px;max-width:780px}.hero h1 em{font-style:normal;color:#75f59b}.hero p{max-width:620px;font-size:17px;line-height:1.6;color:#d5e8dc}.ey{font:800 11px ui-monospace,monospace;letter-spacing:.18em;color:#baf5ca}.mapcard{justify-self:end;width:min(380px,100%);padding:25px;border-radius:28px}.mapcard strong{font-size:34px;display:block;margin:7px 0}.actions{display:flex;gap:10px;margin-top:26px}.btn{display:inline-flex;padding:14px 20px;border-radius:15px;background:#69ef91;color:#092817;font-weight:900;text-decoration:none}.btn.alt{background:#ffffff12;color:white;border:1px solid #ffffff33}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:0 0 16px}.stat{border-radius:23px;padding:20px}.stat small{color:#c2d8ca}.num{font-size:34px;font-weight:950;margin-top:6px}.main{display:grid;grid-template-columns:1.55fr .85fr;gap:16px}.explore,.side,.ad{border-radius:30px;padding:25px}.head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:18px}.head h2{margin:0;font-size:24px}.muted{color:#bdd1c4}.searchdock{display:flex;gap:10px;margin-bottom:18px}.searchdock input{flex:1;padding:15px;border-radius:15px;border:1px solid #ffffff2d;background:#092d1e88;color:white}.searchdock button{border:0;border-radius:15px;padding:0 18px;background:#69ef91;font-weight:900;color:#082416}.counties{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-height:510px;overflow:auto;padding-right:4px}.county{position:relative;min-height:145px;border-radius:20px;padding:17px;background:linear-gradient(145deg,#ffffff17,#0d402a44);border:1px solid #ffffff20}.county .ico{font-size:30px}.county h3{margin:10px 0 4px}.county a{color:#a8f4bf;text-decoration:none;font-size:12px;font-weight:800}.meter{height:5px;border-radius:8px;background:#ffffff15;margin:10px 0;overflow:hidden}.meter i{display:block;height:100%;background:#70ee96}.side{min-height:300px}.pulseRow{padding:15px 0;border-bottom:1px solid #ffffff15}.pulseRow b{display:block;font-size:18px}.ad{margin-top:16px;display:flex;align-items:center;justify-content:space-between;gap:20px;background:linear-gradient(120deg,rgba(16,57,38,.7),rgba(101,86,31,.35))}.chip{padding:7px 10px;border-radius:99px;background:#ffffff10;border:1px solid #ffffff20;font-size:11px}.footer{text-align:center;color:#a9c2b2;font-size:12px;padding:30px 0 0}@media(max-width:900px){.nav a{display:none}.search{width:46%}.hero{grid-template-columns:1fr;min-height:460px;padding:40px 10px}.mapcard{justify-self:start}.stats{grid-template-columns:1fr 1fr}.main{grid-template-columns:1fr}.counties{grid-template-columns:1fr 1fr}}@media(max-width:560px){.wrap{padding:10px 12px 50px}.nav{height:60px}.search{display:none}.hero h1{font-size:48px}.hero{padding-top:34px}.stats{gap:8px}.stat{padding:15px}.num{font-size:27px}.counties{grid-template-columns:1fr}.actions{flex-wrap:wrap}.explore,.side,.ad{padding:18px;border-radius:24px}}
.adTicker{margin-top:12px;border-radius:20px;min-height:48px;display:flex;align-items:center;overflow:hidden;position:relative}.adTickerLabel{flex:0 0 auto;z-index:3;height:48px;display:flex;align-items:center;padding:0 15px;font:900 10px ui-monospace,monospace;letter-spacing:.12em;color:#143b20;background:#69ef91;border-radius:19px 0 0 19px}.adTickerViewport{overflow:hidden;flex:1;mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent);-webkit-mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent)}.adTickerTrack{display:flex;width:max-content;gap:34px;align-items:center;padding:0 24px;min-height:48px;animation:adMarquee 30s linear infinite}.adTicker:hover .adTickerTrack{animation-play-state:paused}.adTickerItem{display:flex;gap:9px;align-items:center;color:#f5fff7;text-decoration:none;white-space:nowrap;font-size:13px}.adTickerItem b{color:#aaf6bd}.adTickerItem span{color:#d6e8dc}.adTickerItem:after{content:'•';color:#69ef91;margin-left:25px}.adTickerItem.beathub{position:relative;padding:7px 14px 7px 8px;border-radius:15px;background:#0b0710;border:1px solid #2a2032;box-shadow:inset 0 1px rgba(255,255,255,.04),0 10px 24px rgba(0,0,0,.18);overflow:hidden}.adTickerItem.beathub:before{content:'';position:absolute;left:0;right:0;top:0;height:3px;background:repeating-linear-gradient(45deg,#ef3f6a 0 10px,#f5b400 10px 20px,#1fd1a3 20px 30px)}.adTickerItem.beathub .bhIcon{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;background:#150e1c;border:1px solid #2a2032;overflow:hidden;box-shadow:0 8px 22px rgba(0,0,0,.24)}.adTickerItem.beathub .bhIcon img{width:28px;height:20px;object-fit:contain}.adTickerItem.beathub .bhBrand{font-weight:950;letter-spacing:.04em;color:#f5f3f7}.adTickerItem.beathub .bhBrand em{font-style:normal;color:#f5b400}.adTickerItem.beathub .bhTag{color:#a89bb4}.adTickerItem.beathub .bhCta{padding:6px 9px;border-radius:999px;background:#f5b400;color:#1a1200;font-size:10px;font-weight:950;letter-spacing:.04em;margin-left:5px}.adTickerEmpty{padding:0 18px;color:#c8dbcf;font-size:13px}.adTickerEmpty a{color:#aaf6bd;font-weight:850;text-decoration:none}.sponsoredDot{width:7px;height:7px;border-radius:50%;background:#69ef91;box-shadow:0 0 0 4px #69ef9120}.brandTickerIcon{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;font-size:14px;font-weight:950}.adTickerItem strong{font-size:10px;padding:6px 8px;border-radius:999px}.adTickerItem.beathub{background:#0b0710;border:1px solid #2a2032;padding:7px 10px;border-radius:14px}.adTickerItem.beathub .brandTickerIcon,.adTickerItem.beathub strong{background:#f5b400;color:#1a1200}.adTickerItem.mkulima{background:linear-gradient(135deg,#0d3f20,#17612d);border:1px solid #69ef9150;padding:7px 10px;border-radius:14px}.adTickerItem.mkulima .brandTickerIcon,.adTickerItem.mkulima strong{background:#d7ff72;color:#173515}.adTickerItem.mizizi{background:linear-gradient(135deg,#24190f,#5d4328);border:1px solid #d8b77a55;padding:7px 10px;border-radius:14px}.adTickerItem.mizizi .brandTickerIcon,.adTickerItem.mizizi strong{background:#d8b77a;color:#2a1b0d}.adTickerItem.onebob{background:linear-gradient(135deg,#0b2f1d,#245f39);border:1px solid #ffe06b44;padding:7px 10px;border-radius:14px}.adTickerItem.onebob .brandTickerIcon,.adTickerItem.onebob strong{background:#ffe06b;color:#15351f}@keyframes adMarquee{from{transform:translateX(0)}to{transform:translateX(-50%)}}@media(prefers-reduced-motion:reduce){.adTickerTrack{animation:none;flex-wrap:wrap;width:auto;padding:10px 18px}.adTicker{align-items:stretch}.adTickerLabel{height:auto}}@media(max-width:620px){.adTickerLabel{padding:0 10px}.adTickerTrack{gap:20px}.adTickerItem{font-size:12px}}
/* 9/10 finish pass: richer world, restrained premium glass */
body{background:#0b2f1d}.world{background:radial-gradient(circle at 84% 12%,#ffe8a1 0 1.8%,#ffd9894d 2% 8%,transparent 17%),linear-gradient(180deg,#82aea8 0 23%,#72987b 37%,#2c7045 65%,#0b3c24 100%)}.world:before{inset:23% -8% -8%;background:radial-gradient(ellipse at 64% 40%,rgba(194,224,190,.32),transparent 24%),linear-gradient(158deg,transparent 0 15%,#63885b 15.4% 27%,transparent 27.4%),linear-gradient(24deg,transparent 0 23%,#39774a 23.4% 50%,transparent 50.4%),linear-gradient(158deg,transparent 0 43%,#145a34 43.4% 70%,transparent 70.4%);filter:blur(1.2px)}.world:after{inset:52% -4% -4%;background:radial-gradient(ellipse at 63% 8%,rgba(173,210,184,.42),transparent 26%),linear-gradient(8deg,#0b4227 0 46%,transparent 46.5%),linear-gradient(-8deg,#216a3d 0 57%,transparent 57.5%);filter:blur(1.5px)}.shade{background:linear-gradient(90deg,rgba(4,30,17,.70),transparent 48%),linear-gradient(0deg,rgba(3,28,15,.62),transparent 42%)}.glass{background:linear-gradient(135deg,rgba(20,55,39,.58),rgba(39,73,56,.34));border:1px solid rgba(240,255,245,.34);box-shadow:inset 0 1px rgba(255,255,255,.38),inset 0 -1px rgba(255,255,255,.06),0 18px 48px rgba(3,25,14,.20);backdrop-filter:blur(22px) saturate(118%);-webkit-backdrop-filter:blur(22px) saturate(118%)}.nav{margin-top:2px}.hero{min-height:420px;padding-top:64px}.hero h1{font-weight:950;letter-spacing:-4px}.hero p{color:#e0eee5}.mapcard{background:linear-gradient(135deg,rgba(42,72,55,.52),rgba(91,82,47,.26));box-shadow:inset 0 1px rgba(255,255,255,.40),0 24px 60px rgba(5,29,17,.20)}.stats{margin-top:-10px}.stat{min-height:105px}.explore,.side,.ad{box-shadow:inset 0 1px rgba(255,255,255,.38),0 22px 58px rgba(4,28,16,.19)}.county{transition:transform .2s ease,border-color .2s ease,background .2s ease}.county:hover{transform:translateY(-3px);border-color:#ffffff55;background:linear-gradient(145deg,#ffffff20,#164c334f)}.btn{box-shadow:0 10px 28px rgba(30,205,92,.18)}.btn:hover{filter:brightness(1.05)}.searchdock input:focus,.search:focus{outline:2px solid rgba(112,238,150,.42);outline-offset:1px}.footer{padding-top:38px}
/* 9.5 art-direction pass */
body:after{content:'';position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(ellipse at 50% 48%,transparent 45%,rgba(3,24,13,.22) 100%);mix-blend-mode:multiply}.world{transform:scale(1.015);filter:saturate(1.08) contrast(1.025)}.world:before{filter:blur(.8px);opacity:.96}.world:after{filter:blur(1.2px);opacity:.98}.wrap{padding-top:16px}.nav{background:linear-gradient(120deg,rgba(25,58,43,.54),rgba(44,76,60,.31));border-color:rgba(244,255,247,.32);box-shadow:inset 0 1px rgba(255,255,255,.40),0 16px 42px rgba(3,24,13,.16)}.brand{letter-spacing:-.7px}.hero{min-height:440px;gap:64px}.hero h1{max-width:830px;font-size:clamp(52px,6.3vw,88px);line-height:.91;text-wrap:balance}.hero p{max-width:650px;font-size:18px;line-height:1.62}.mapcard{padding:29px;border-radius:31px}.mapcard:before{content:'✦';float:right;font-size:62px;line-height:1;color:rgba(160,255,188,.17);filter:drop-shadow(0 0 18px rgba(107,241,148,.12))}.stats{gap:16px}.stat{padding:22px 23px;border-radius:25px}.num{letter-spacing:-1.5px}.main{gap:18px}.explore,.side,.ad{border-color:rgba(242,255,246,.30)}.explore{padding:28px}.counties{gap:13px}.county{min-height:154px;padding:18px;border-radius:22px;background:linear-gradient(145deg,rgba(255,255,255,.105),rgba(11,57,35,.25));box-shadow:inset 0 1px rgba(255,255,255,.15)}.county h3{font-size:17px;letter-spacing:-.2px}.county .muted{font-size:12px}.meter{margin:12px 0}.side{padding:28px}.pulseRow{padding:18px 0}.ad{padding:26px}.btn{transition:transform .18s ease,filter .18s ease,box-shadow .18s ease}.btn:hover{transform:translateY(-2px);box-shadow:0 14px 32px rgba(31,210,94,.22)}.ey{font-size:10px}.footer{opacity:.9}@media(max-width:900px){.hero{gap:24px;min-height:500px}.hero h1{font-size:clamp(50px,10vw,72px)}}@media(max-width:560px){.hero{min-height:510px}.hero h1{font-size:49px;letter-spacing:-2.7px}.hero p{font-size:15px}.mapcard{padding:22px}.stat{min-height:94px}.explore{padding:19px}}
.liveNews{border-radius:30px;padding:25px;margin:0 0 18px}.newsGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.newsCard{padding:18px;border-radius:20px;background:linear-gradient(145deg,rgba(255,255,255,.10),rgba(11,57,35,.24));border:1px solid rgba(255,255,255,.20);min-height:170px;display:flex;flex-direction:column}.newsCard .marker{display:inline-flex;align-self:flex-start;padding:6px 8px;border-radius:999px;background:#69ef91;color:#143b20;font-size:10px;font-weight:900;letter-spacing:.08em}.newsCard h3{font-size:17px;line-height:1.28;margin:12px 0 8px}.newsCard .why{font-size:12px;line-height:1.45;color:#c9ddcf;margin-top:auto;padding-top:12px;border-top:1px solid #ffffff18}.newsCard a{color:#a8f4bf;text-decoration:none;font-size:12px;font-weight:850;margin-top:10px}.newsMeta{font-size:11px;color:#a9c2b2}@media(max-width:900px){.newsGrid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.newsGrid{grid-template-columns:1fr}.liveNews{padding:18px}}
/* Live Map dashboard arrangement — preserves approved 9.5 world/glass palette */
.liveMapGrid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.7fr);gap:18px;margin:0 0 18px}.kenyaMap{min-height:540px;border-radius:30px;padding:28px;position:relative;overflow:hidden}.mapTop{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.mapStage{position:relative;min-height:405px;margin-top:14px;display:grid;place-items:center}.kenyaSvg{width:min(100%,560px);height:405px;overflow:visible;filter:drop-shadow(0 18px 32px rgba(2,28,14,.25))}.countyShape{fill:rgba(105,239,145,.14);stroke:rgba(204,255,219,.52);stroke-width:1.15;vector-effect:non-scaling-stroke;cursor:pointer;transition:fill .16s ease,stroke .16s ease,filter .16s ease}.countyShape:hover,.countyShape.active{fill:rgba(105,239,145,.55);stroke:#d9ffe4;filter:drop-shadow(0 0 8px rgba(105,239,145,.55))}.countyDot{fill:#69ef91;stroke:#f5fff8;stroke-width:2.4;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 6px rgba(105,239,145,.9));cursor:pointer;transition:transform .16s ease,filter .16s ease}.countyDotHalo{fill:rgba(105,239,145,.16);pointer-events:none}.countyDot:hover,.countyDot.active{transform:scale(1.35);transform-box:fill-box;transform-origin:center;filter:drop-shadow(0 0 10px rgba(105,239,145,1))}.mapLabel{position:absolute;left:18px;bottom:18px;max-width:330px;padding:17px 19px;border-radius:20px}.mapLabel strong{display:block;font-size:22px;margin-bottom:5px}.liveRail{border-radius:30px;padding:25px;min-height:540px}.liveItem{display:block;color:white;text-decoration:none;padding:16px 0;border-bottom:1px solid #ffffff17}.liveItem b{display:block;margin-bottom:5px}.liveDot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#69ef91;margin-right:7px;box-shadow:0 0 0 5px #69ef9120}.quickStrip{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 18px}.quick{padding:18px;border-radius:22px;text-decoration:none;color:white}.quick b{display:block;font-size:17px;margin-top:5px}.sectionTitle{display:flex;justify-content:space-between;align-items:end;margin:24px 0 12px}.sectionTitle h2{margin:0;font-size:27px}@media(max-width:900px){.liveMapGrid{grid-template-columns:1fr}.liveRail{min-height:auto}.quickStrip{grid-template-columns:1fr 1fr}.kenyaMap{min-height:500px}}@media(max-width:560px){.quickStrip{grid-template-columns:1fr}.kenyaMap{padding:18px;min-height:450px}.mapStage{min-height:330px}.kenyaSvg{height:330px}.mapLabel{left:8px;right:8px;max-width:none}}
/* Advertiser Studio blend carried onto the national homepage */
body{background:radial-gradient(circle at 10% 8%,#00ff8840,transparent 28%),radial-gradient(circle at 91% 8%,#ffd90030,transparent 25%),#020806}
.world{background:linear-gradient(180deg,rgba(104,167,150,.78) 0 18%,rgba(41,103,67,.73) 44%,rgba(3,43,24,.96) 76%,#020806 100%);filter:saturate(1.04)}
.world:before{background:radial-gradient(circle at 11% 13%,rgba(0,255,136,.24),transparent 27%),radial-gradient(circle at 90% 11%,rgba(255,217,0,.16),transparent 25%),linear-gradient(157deg,transparent 0 19%,rgba(75,132,75,.38) 19.5% 31%,transparent 31.5%),linear-gradient(24deg,transparent 0 31%,rgba(31,103,59,.54) 31.5% 57%,transparent 57.5%);filter:blur(1px)}
.world:after{background:radial-gradient(ellipse at 65% 8%,rgba(137,247,170,.24),transparent 28%),linear-gradient(8deg,#063a21 0 47%,transparent 47.5%),linear-gradient(-8deg,#0b5a31 0 59%,transparent 59.5%);filter:blur(1px)}
.shade{background:linear-gradient(90deg,rgba(0,16,9,.66) 0,rgba(0,17,10,.18) 54%,rgba(35,42,4,.10) 100%),linear-gradient(0deg,rgba(0,9,5,.70),transparent 46%)}
.glass{background:linear-gradient(135deg,rgba(255,255,255,.095),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.22);box-shadow:inset 0 1px rgba(255,255,255,.28),0 30px 90px rgba(0,0,0,.34);backdrop-filter:blur(35px) saturate(160%);-webkit-backdrop-filter:blur(35px) saturate(160%)}
.nav{background:linear-gradient(100deg,rgba(29,86,68,.72),rgba(114,191,178,.43),rgba(183,191,111,.24));border-color:rgba(245,255,248,.30);box-shadow:inset 0 1px rgba(255,255,255,.32),0 20px 58px rgba(0,0,0,.18)}
.brand b{color:#68ef93}.nav a{color:#f4fbf6}
.hero{min-height:410px;margin:24px 0 18px;padding:54px 28px;border-radius:30px;background:linear-gradient(120deg,rgba(20,71,48,.66),rgba(29,148,68,.42),rgba(9,78,37,.52));border-color:rgba(143,255,179,.28);box-shadow:inset 0 1px rgba(255,255,255,.27),0 34px 85px rgba(0,0,0,.27)}
.hero h1{font-size:clamp(52px,6.6vw,86px);font-weight:950;letter-spacing:-4px}.hero h1 em{color:#91f6ad}.hero p{color:#bdd1c4}
.mapcard{background:linear-gradient(135deg,rgba(255,255,255,.10),rgba(67,102,57,.15));border-color:rgba(255,255,255,.20)}
.btn{background:linear-gradient(135deg,#69ef91,#a8f7be);color:#04120a;box-shadow:0 14px 32px rgba(46,229,113,.18)}.btn.alt{background:rgba(255,255,255,.08);color:white;border:1px solid rgba(255,255,255,.26)}
.stat,.kenyaMap,.liveRail,.quick,.liveNews,.explore,.side,.ad{border-color:rgba(255,255,255,.20)}
.quick:hover,.county:hover{border-color:rgba(141,247,172,.42)}
.priceAccent,.num,.county a,.liveNews a{color:#8df7ac}
@media(max-width:900px){.hero{margin-top:18px;padding:38px 20px}}@media(max-width:560px){.hero{padding:30px 18px;border-radius:26px}.hero h1{font-size:48px}}
</style></head><body><div class=world></div><div class=shade></div><main class=wrap><nav class="glass nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/">Home</a><a href="#counties">Counties</a><a href="/methodology">Methodology</a><a href="/advertise">Advertise</a><a href="/claim-profile">Candidate profile</a><a href="https://www.facebook.com/people/Kenya-Pulse/61594936328345/" target="_blank" rel="noopener noreferrer">Kenya Pulse AI on Facebook</a><input class=search placeholder="Search county…" oninput="filterCounties(this.value)"></nav><section class="glass adTicker" aria-label="Sponsored commercial messages"><div class=adTickerLabel>SPONSORED</div><div class=adTickerViewport><div class=adTickerTrack id=adTickerTrack><div class=adTickerEmpty>Loading commercial messages…</div></div></div></section><section class="glass hero"><div><div class=ey>OPEN NATIONAL PARTICIPATION • 47 COUNTIES</div><h1>A clearer view of <em>Kenya's pulse.</em></h1><p>Explore voluntary participation across counties, constituencies and wards. Aggregate responses update as people take part. This is an open online pulse, not a scientific election forecast.</p><div class=actions><a class=btn href="#counties">Explore counties →</a><a class="btn alt" href="/participate?src=growth-hero">Take part →</a></div></div><aside class="glass mapcard"><span class=ey>KENYA COVERAGE</span><strong>47 Counties</strong><div class=muted>290 constituencies · 1,450 wards</div><p class=muted>One participation experience with local geographic scope.</p></aside></section><section class=stats><div class="glass stat"><small>Visits</small><div class=num>{{totalv}}</div></div><div class="glass stat"><small>Responses</small><div class=num>{{totalr}}</div></div><div class="glass stat"><small>Response ratio</small><div class=num>{{rate}}%</div></div><div class="glass stat"><small>Counties available</small><div class=num>47</div></div></section><section class=liveMapGrid><div class="glass kenyaMap"><div class=mapTop><div><div class=ey>LIVE COUNTY EXPLORER</div><h2 style="font-size:30px;margin:7px 0">Kenya, county by county.</h2><div class=muted>Tap an activity point or search below to open a county.</div></div><span class=chip>LIVE</span></div><div class=mapStage><svg class=kenyaSvg viewBox="0 0 600 420" role=img aria-label="Interactive map of Kenya counties">{% for x in map_cards %}<path class=countyShape d="{{x.path}}" data-name="{{x.county}}" data-responses="{{x.responses}}" data-visits="{{x.visits}}" data-url="/county/{{slug(x.county)}}?src=live-map" aria-label="{{x.county}}" tabindex="0" onclick="selectMapCounty(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectMapCounty(this)}"></path>{% endfor %}{% for x in map_cards %}<circle class=countyDotHalo cx="{{x.cx}}" cy="{{x.cy}}" r="10"></circle><circle class=countyDot cx="{{x.cx}}" cy="{{x.cy}}" r="5.5" data-name="{{x.county}}" data-responses="{{x.responses}}" data-visits="{{x.visits}}" data-url="/county/{{slug(x.county)}}?src=live-map" aria-label="{{x.county}}" tabindex="0" onclick="selectMapCounty(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectMapCounty(this)}"></circle>{% endfor %}</svg><div class="glass mapLabel" id=mapLabel><strong>Explore all 47 counties</strong><span class=muted>Select a point to see its live Kenya Pulse AI activity.</span></div></div></div><aside class="glass liveRail"><div class=head><div><div class=ey>HAPPENING NOW</div><h2>Live activity</h2></div></div>{% for x in active_cards %}<a class=liveItem href="/county/{{slug(x.county)}}?src=activity"><span class=liveDot></span><b>{{x.county}}</b><span class=muted>{{x.responses}} responses · {{x.visits}} visits</span></a>{% endfor %}<a class=liveItem href="/county-notices"><b>County public interests →</b><span class=muted>Events, tenders, jobs, bursaries and notices</span></a></aside></section><section class=quickStrip><a class="glass quick" href="#counties"><span class=ey>EXPLORE</span><b>47 counties →</b></a><a class="glass quick" href="/claim-profile"><span class=ey>FOR ASPIRANTS</span><b>Claim candidate profile →</b></a><a class="glass quick" href="/tenders"><span class=ey>OPPORTUNITIES</span><b>Tenders →</b></a><a class="glass quick" href="/ground"><span class=ey>PUBLIC VOICE</span><b>Sauti ya Ground →</b></a></section><section class="glass liveNews"><div class=head><div><div class=ey>LIVE NEWS · KENYA</div><h2>What is moving the public conversation</h2><div class=muted>Fresh headlines with neutral context markers. Headlines link to the original publisher.</div></div><span class=chip>REFRESHES LIVE</span></div><div id=liveNewsFeed class=newsGrid><div class="newsCard"><b>Loading latest verified headlines…</b></div></div></section><section class=main id=counties><div class="glass explore"><div class=head><div><h2>Explore by county</h2><div class=muted>Open a county to view its participant results.</div></div><span class=chip id=found>47 COUNTIES</span></div><div class=searchdock><input id=countySearch list=countiesList placeholder="Kericho, Nairobi, Kisumu…" oninput="filterCounties(this.value)"><datalist id=countiesList>{% for x in cards %}<option value="{{x.county}}">{% endfor %}</datalist><button onclick=openCounty()>OPEN →</button></div><div class=counties id=countyGrid>{% for x in cards %}<article class=county data-county="{{x.county|lower}}"><div class=ico>{{x.icon}}</div><h3>{{x.county}}</h3><div class=muted>{{x.responses}} responses · {{x.visits}} visits</div><div class=meter><i style="width:{{[x.conversion,100]|min}}%"></i></div><a href="/county/{{slug(x.county)}}?src=dashboard">VIEW COUNTY →</a></article>{% endfor %}</div></div><aside><section class="glass side"><div class=head><div><h2>Participation snapshot</h2><div class=muted>Aggregate platform activity</div></div></div><div class=pulseRow><b>{{totalr}} responses</b><span class=muted>Across current participant submissions</span></div><div class=pulseRow><b>{{totalv}} visits</b><span class=muted>Recorded platform visits</span></div><div class=pulseRow><b>{{rate}}% ratio</b><span class=muted>Responses relative to recorded visits</span></div><p class=muted style="font-size:12px">These figures describe Kenya Pulse AI participation only and are not representative of all Kenyan voters.</p></section><section class="glass ad"><div><div class=ey>COUNTY NOTICEBOARD</div><h2>What's happening</h2><div class=muted>Useful county opportunities and public-interest updates, kept separate from participation results.</div></div><div class=pulseRow><b>📅 Upcoming events</b><span class=muted>County events, expos, forums and public participation dates.</span></div><div class=pulseRow><b>📄 Tenders & opportunities</b><span class=muted>Procurement notices, supplier opportunities and closing dates from verified sources.</span></div><div class=pulseRow><b>📢 Public notices</b><span class=muted>Jobs, bursaries, service notices, consultations and major local alerts.</span></div><a class=btn href="/county-notices">Explore public interests →</a><div class=muted style="font-size:11px;margin-top:12px">Sponsored placements, when present, are clearly labelled and never affect participation or results.</div></section></aside></section><section class="glass side" style="margin-top:18px"><div class=head><div><h2>Revenue engine</h2><div class=muted>Commercial inventory only — separate from participation and results.</div></div></div><div class=pulseRow><b>{{ad_impressions}} ad impressions</b><span class=muted>{{ad_clicks}} genuine ad clicks · {{ad_ctr}}% CTR</span></div><div class=pulseRow><b>KSh {{booked}} booked</b><span class=muted>Campaign value from active/ended direct commercial campaigns; not cash received unless payment is verified.</span></div><div class=pulseRow><b>Programmatic ads</b><span class=muted>Ready for publisher code after external ad-network approval. No fake placeholders counted as revenue.</span></div><a class=btn href="/advertise">Sell commercial inventory →</a></section><div class=footer>Kenya Pulse AI · Voluntary online participation · Not an election forecast</div></main><script>
async function loadLiveNews(){let box=document.getElementById('liveNewsFeed');if(!box)return;try{let r=await fetch('/api/live-news',{cache:'no-store'}),j=await r.json(),items=j.items||[];box.innerHTML=items.slice(0,6).map(x=>'<article class="newsCard"><span class=marker>'+escNews(x.marker)+'</span><h3>'+escNews(x.title)+'</h3><div class=newsMeta>'+escNews(x.source)+(x.published_at?' · '+new Date(x.published_at).toLocaleString():'')+'</div><div class=why><b>Why this is a marker:</b> '+escNews(x.why)+'</div><a href="'+escAttr(x.url)+'" target="_blank" rel="noopener noreferrer">Read original report →</a></article>').join('')||'<article class="newsCard"><h3>Live news is temporarily unavailable</h3><div class=muted>The county dashboard remains available while the feed reconnects.</div></article>'}catch(e){box.innerHTML='<article class="newsCard"><h3>Live news is temporarily unavailable</h3></article>'}}
function escNews(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function escAttr(s){return escNews(s)}
async function loadAdTicker(){let box=document.getElementById('adTickerTrack');if(!box)return;try{let r=await fetch('/api/ad-strip',{cache:'no-store'}),j=await r.json(),items=j.items||[];if(!items.length){box.style.animation='none';box.innerHTML='<div class="adTickerEmpty">Advertise to Kenya Pulse AI visitors · <a href="/advertise">Book a national placement →</a></div>';return}let html=items.map(x=>'<a class="adTickerItem '+escNews(x.brand_key||'generic')+'" href="'+escAttr(x.click_url)+'" rel="sponsored"><span class=brandTickerIcon>'+escNews(x.icon||'✦')+'</span><b>'+escNews(x.business)+'</b><span>'+escNews(x.tagline||x.headline||'Sponsored message')+'</span><strong>'+escNews(x.cta||'OPEN')+' →</strong></a>').join('');box.innerHTML=html+html}catch(e){box.style.animation='none';box.innerHTML='<div class="adTickerEmpty"><a href="/advertise">Advertise on Kenya Pulse AI →</a></div>'}}
function selectMapCounty(el){document.querySelectorAll('.countyShape,.countyDot').forEach(x=>x.classList.remove('active'));let n=el.dataset.name;document.querySelectorAll('.countyShape,.countyDot').forEach(x=>{if(x.dataset.name===n)x.classList.add('active')});let r=el.dataset.responses,v=el.dataset.visits,u=el.dataset.url;document.getElementById('mapLabel').innerHTML='<strong>'+n+'</strong><span class=muted>'+r+' responses · '+v+' visits</span><div style="margin-top:10px"><a class=btn href="'+u+'">Open '+n+' →</a></div>'}
function filterCounties(q){q=(q||'').trim().toLowerCase();let cs=[...document.querySelectorAll('.county')],n=0;cs.forEach(c=>{let ok=c.dataset.county.includes(q);c.style.display=ok?'block':'none';if(ok)n++});document.getElementById('found').textContent=n+' COUNTIES';let x=document.getElementById('countySearch');if(x&&document.activeElement!==x)x.value=q}
function openCounty(){let q=document.getElementById('countySearch').value.trim().toLowerCase();let cs=[...document.querySelectorAll('.county')];let x=cs.find(c=>c.dataset.county===q)||cs.find(c=>c.style.display!=='none');if(x)location.href=x.querySelector('a').href}
document.getElementById('countySearch').addEventListener('keydown',e=>{if(e.key==='Enter')openCounty()})
loadAdTicker();loadLiveNews();setInterval(()=>{if(document.visibilityState==='visible')loadLiveNews()},300000)
(()=>{let sid=localStorage.getItem('kp_sid');if(!sid){sid=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());localStorage.setItem('kp_sid',sid)}fetch('/api/analytics/pageview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:location.pathname,source:new URLSearchParams(location.search).get('src')||'direct',session_id:sid})}).catch(()=>{})})();
</script></body></html>"""
 return render_template_string(html,cards=cards,map_cards=map_cards,active_cards=active_cards,totalv=totalv,totalr=totalr,rate=rate,ad_impressions=ad_impressions,ad_clicks=ad_clicks,ad_ctr=ad_ctr,booked=booked,slug=lambda s:re.sub(r"[^a-z0-9]+","-",s.lower()).strip("-"))


NOTICE_CATEGORIES={"EVENT","TENDER","JOB","BURSARY","PUBLIC_PARTICIPATION","ALERT"}

def valid_official_source(url):
 try:
  from urllib.parse import urlparse
  host=(urlparse(url).hostname or "").lower()
  return url.startswith("https://") and (host.endswith(".go.ke") or host=="go.ke")
 except Exception:return False

@app.post("/admin/county-notices")
def add_county_notice():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 d=request.get_json(silent=True) or {};county=str(d.get("county","")).strip();category=str(d.get("category","")).strip().upper();title=str(d.get("title","")).strip()[:180];summary=str(d.get("summary","")).strip()[:500];source_name=str(d.get("source_name","")).strip()[:120];source_url=str(d.get("source_url","")).strip()[:500];reference_no=str(d.get("reference_no","")).strip()[:120];published_at=d.get("published_at") or None;closes_at=d.get("closes_at") or None;event_at=d.get("event_at") or None
 if county not in COUNTIES or category not in NOTICE_CATEGORIES or len(title)<4:return jsonify(error="Invalid county, category or title"),400
 if not source_name or not valid_official_source(source_url):return jsonify(error="A verified HTTPS .go.ke source is required"),400
 try:
  with conn() as db:
   exists=db.execute("SELECT id FROM county_notices WHERE county=? AND category=? AND source_url=? AND title=?",(county,category,source_url,title)).fetchone()
   if exists:return jsonify(ok=True,id=exists["id"],duplicate=True),200
   cur=db.execute("INSERT INTO county_notices(county,category,title,summary,source_name,source_url,reference_no,published_at,closes_at,event_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,'VERIFIED')",(county,category,title,summary,source_name,source_url,reference_no,published_at,closes_at,event_at));nid=cur.lastrowid if not DATABASE_URL else None
  return jsonify(ok=True,id=nid,duplicate=False),201
 except Exception:
  app.logger.exception("county notice insert failure");return jsonify(error="Could not save notice"),500

NEWS_CACHE={"at":0,"items":[]}
def news_marker(title):
 t=title.lower()
 if "poll" in t or "survey" in t:return ("POLL / SURVEY","New survey release. Compare fieldwork dates, sample size and methodology before interpreting movement.")
 if "iebc" in t or "voter" in t or "registration" in t:return ("ELECTION ADMIN","Electoral administration update that may affect timelines, access or participation rules.")
 if "court" in t or "tribunal" in t or "registrar" in t:return ("LEGAL MILESTONE","Legal or registration development. The practical effect depends on the underlying ruling or official notice.")
 if "budget" in t or "finance bill" in t:return ("PUBLIC FINANCE","Public-finance development with potential national or county-level consequences.")
 return ("DEVELOPING","Recent public-affairs development. Open the original report for full context.")

@app.get("/api/live-news")
def live_news():
 now=time.time()
 if NEWS_CACHE["items"] and now-NEWS_CACHE["at"]<600:return jsonify(items=NEWS_CACHE["items"],cached=True),200,{"Cache-Control":"public, max-age=60"}
 queries=["Kenya politics when:1d","Kenya election IEBC poll when:3d"]
 items=[];seen=set()
 for q in queries:
  try:
   url="https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":q,"hl":"en-KE","gl":"KE","ceid":"KE:en"})
   req=urllib.request.Request(url,headers={"User-Agent":"KenyaPulse/1.0 (+public-news-feed)"})
   with urllib.request.urlopen(req,timeout=5) as r:root=ET.fromstring(r.read())
   for node in root.findall(".//item"):
    title=(node.findtext("title") or "").strip();link=(node.findtext("link") or "").strip();pub=(node.findtext("pubDate") or "").strip();source_node=node.find("source");source=(source_node.text or "").strip() if source_node is not None else "News source"
    key=(title.lower(),source.lower())
    if not title or not link or key in seen:continue
    seen.add(key);tag,why=news_marker(title)
    try:published=email.utils.parsedate_to_datetime(pub).isoformat() if pub else None
    except Exception:published=pub or None
    items.append({"title":title,"url":link,"source":source,"published_at":published,"marker":tag,"why":why})
  except Exception:app.logger.exception("live news feed fetch failed")
 items=items[:16]
 if items:NEWS_CACHE.update({"at":now,"items":items})
 return jsonify(items=items or NEWS_CACHE["items"],cached=not bool(items)),200,{"Cache-Control":"public, max-age=60"}

TENDER_CACHE={}
def _ppip_live_tenders(county):
 key=county.lower();cached=TENDER_CACHE.get(key)
 if cached and time.time()-cached["at"]<900:return cached["items"]
 found=[];seen=set()
 # PPIP is the canonical public procurement source. Scan recent active-tender pages and
 # retain only cards whose visible text names the requested county.
 for page in range(1,7):
  try:
   url="https://tenders.go.ke/tenders/"+("?page="+str(page) if page>1 else "")
   req=urllib.request.Request(url,headers={"User-Agent":"KenyaPulse/1.0 (+public-procurement-discovery)"})
   with urllib.request.urlopen(req,timeout=7) as r:raw=r.read().decode("utf-8","ignore")
   text=html_lib.unescape(re.sub(r"<[^>]+>","\n",raw))
   text=re.sub(r"[ \t]+"," ",text);text=re.sub(r"\n+","\n",text)
   # Split on detail-card marker and inspect each card independently.
   cards=re.split(r"(?i)View More",text)
   for card in cards:
    if county.lower() not in card.lower():continue
    lines=[x.strip() for x in card.splitlines() if x.strip()]
    joined="\n".join(lines)
    # Active-only safety: reject visibly closed cards.
    if re.search(r"(?i)\bClosed\b",joined):continue
    close_m=re.search(r"(?i)(?:Closes(?:\s+in[^\n]*)?|Close date(?: and time)?)\s*\n?([^\n]{4,80})",joined)
    entity_m=re.search(r"(?i)Procuring Entity\s*\n([^\n]{2,140})",joined)
    ocid_m=re.search(r"(?i)OCID\s*\n([^\n]{4,180})",joined)
    method_m=re.search(r"(?i)Procurement Method\s*\n([^\n]{2,100})",joined)
    cat_m=re.search(r"(?i)Category\s*\n([^\n]{2,100})",joined)
    # Tender title is normally the strongest non-label line before Procuring Entity.
    pre=joined.split("Procuring Entity",1)[0]
    title_lines=[x for x in pre.splitlines() if not re.match(r"(?i)^(Closes|Published|Tender|Search|Items per page|Page )",x)]
    title=title_lines[-1][:220] if title_lines else ""
    entity=entity_m.group(1).strip() if entity_m else ""
    ocid=ocid_m.group(1).strip() if ocid_m else ""
    if not title or not entity:continue
    sig=(title.lower(),entity.lower())
    if sig in seen:continue
    seen.add(sig)
    found.append({"title":title,"entity":entity,"county":county,"ocid":ocid,"method":method_m.group(1).strip() if method_m else "","category":cat_m.group(1).strip() if cat_m else "","close_text":close_m.group(1).strip() if close_m else "","source_url":"https://tenders.go.ke/tenders/"})
  except Exception:
   app.logger.warning("PPIP tender discovery page %s failed",page)
 if found:TENDER_CACHE[key]={"at":time.time(),"items":found[:30]}
 return found[:30]

class _LinkTextParser(HTMLParser):
 def __init__(self):super().__init__();self.links=[];self._href=None;self._buf=[]
 def handle_starttag(self,tag,attrs):
  if tag=="a":
   self._href=dict(attrs).get("href");self._buf=[]
 def handle_data(self,data):
  if self._href:self._buf.append(data)
 def handle_endtag(self,tag):
  if tag=="a" and self._href:
   txt=" ".join("".join(self._buf).split())
   self.links.append((txt,self._href));self._href=None;self._buf=[]

def _county_domain_candidates(county):
 slug=re.sub(r"[^a-z0-9]","",county.lower().replace("county",""))
 known={
  "Nairobi City":["https://nairobi.go.ke"],
  "Uasin Gishu":["https://uasingishu.go.ke"],
  "Kericho":["https://kericho.go.ke","https://procurement.kericho.go.ke"],
  "Tharaka-Nithi":["https://tharakanithi.go.ke"],
  "Taita-Taveta":["https://taitataveta.go.ke"],
  "Elgeyo-Marakwet":["https://elgeyomarakwet.go.ke"],
  "Trans Nzoia":["https://transnzoia.go.ke"]
 }
 out=list(known.get(county,[]))
 for host in [f"https://{slug}.go.ke",f"https://www.{slug}.go.ke"]:
  if host not in out:out.append(host)
 return out

def _county_site_tenders(county):
 cache_key="county:"+county.lower();cached=TENDER_CACHE.get(cache_key)
 if cached and time.time()-cached["at"]<900:return cached["items"]
 found=[];seen=set()

 # High-confidence official pages with structured/current tender listings.
 official_pages={
  "Kwale":["https://kwale.go.ke/active-tenders/"],
  "Kericho":["https://www.kericho.go.ke/templates/tender_documents","https://kericho.go.ke/templates/all_documents"],
  "Uasin Gishu":["https://uasingishu.go.ke/tenders/"],
  "Nairobi City":["https://nairobi.go.ke/tenders/"],
  "Tharaka-Nithi":["https://tharakanithi.go.ke/tharakanithi-county-tenders/"],
  "Taita-Taveta":["https://www.taitataveta.go.ke/tenders/"]
 }
 urls=list(official_pages.get(county,[]))
 for base in _county_domain_candidates(county):
  for path in ["/active-tenders/","/tenders/","/tenders","/procurement/","/procurement","/opportunities/"]:
   u=base.rstrip("/")+path
   if u not in urls:urls.append(u)

 for url in urls[:10]:
  try:
   req=urllib.request.Request(url,headers={"User-Agent":"KenyaPulse/1.0 (+official-county-procurement)"})
   with urllib.request.urlopen(req,timeout=6) as r:
    final=r.geturl();raw=r.read(900000).decode("utf-8","ignore")
   host=(urllib.parse.urlparse(final).hostname or "").lower()
   # County governments sometimes use verified county-owned domains outside .go.ke.
   allowed=host.endswith(".go.ke") or host in {"kwale.go.ke","www.kwale.go.ke"}
   if not allowed:continue

   # First parse structured table rows (works well for Kwale active tenders and many county portals).
   for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>",raw):
    cells=[]
    for cell in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>",row):
     txt=html_lib.unescape(re.sub(r"<[^>]+>"," ",cell))
     txt=" ".join(txt.split())
     cells.append(txt)
    if not cells:continue
    rowtext=" | ".join(cells)
    if not re.search(r"(?i)\b(tender|quotation|rfq|rfp|procurement|bid|open)\b",rowtext):continue
    # Reject rows explicitly marked closed/completed.
    if re.search(r"(?i)\bclosed\b|\bcompleted\b",rowtext):continue
    title=cells[0][:220]
    if not title or title.lower() in {"tender title","document name (click to download)","document name"}:continue
    ref=cells[1][:140] if len(cells)>1 else ""
    method=cells[2][:100] if len(cells)>2 else ""
    category=cells[3][:100] if len(cells)>3 else ""
    close=cells[4][:100] if len(cells)>4 else ""
    hrefs=re.findall(r'''(?is)href=["']([^"']+)["']''',row)
    detail=urllib.parse.urljoin(final,hrefs[0]) if hrefs else final
    sig=(title.lower(),ref.lower(),detail)
    if sig in seen:continue
    seen.add(sig)
    found.append({"title":title,"entity":county+" County Government","county":county,"ocid":ref,"method":method,"category":category,"close_text":close,"source_url":detail,"source_kind":"OFFICIAL COUNTY SITE"})

   # Also capture named tender documents when the county publishes a document repository rather than a table.
   p=_LinkTextParser();p.feed(raw)
   for txt,href in p.links:
    label=" ".join(txt.split())
    if len(label)<10 or not re.search(r"(?i)\b(tender|quotation|rfq|rfp|procurement|insurance|supply|construction|works)\b",label):continue
    if re.search(r"(?i)closed tender|archive",label):continue
    absu=urllib.parse.urljoin(final,href)
    ahost=(urllib.parse.urlparse(absu).hostname or "").lower()
    if not (ahost.endswith(".go.ke") or ahost in {"kwale.go.ke","www.kwale.go.ke"}):continue
    sig=(label.lower(),"",absu)
    if sig in seen:continue
    seen.add(sig)
    found.append({"title":label[:220],"entity":county+" County Government","county":county,"ocid":"","method":"","category":"","close_text":"","source_url":absu,"source_kind":"OFFICIAL COUNTY SITE"})
   if len(found)>=25:break
  except Exception:
   continue
 found=found[:25]
 TENDER_CACHE[cache_key]={"at":time.time(),"items":found}
 return found

@app.get("/api/tenders-live")
def tenders_live():
 county=request.args.get("county","").strip()
 if county and county not in COUNTIES:return jsonify(error="Invalid county"),400
 # First include our own verified official-source tender notices.
 sql="SELECT id,county,title,summary,source_name,source_url,reference_no,closes_at,published_at FROM county_notices WHERE status='VERIFIED' AND category='TENDER'";args=[]
 if county:sql+=" AND county=?";args.append(county)
 if DATABASE_URL:sql+=" AND (closes_at IS NULL OR closes_at>=CURRENT_TIMESTAMP)"
 else:sql+=" AND (closes_at IS NULL OR closes_at>=datetime('now'))"
 sql+=" ORDER BY COALESCE(closes_at,published_at,created_at) ASC LIMIT 50"
 with conn() as db:local=[dict(x) for x in db.execute(sql,args).fetchall()]
 live=_ppip_live_tenders(county) if county else []
 county_site=_county_site_tenders(county) if county else []
 return jsonify(county=county,verified=local,ppip=live,county_site=county_site,official_portal="https://tenders.go.ke/tenders/"),200,{"Cache-Control":"public, max-age=120"}

@app.get("/tenders")
def tenders_page():
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>County Tenders · Kenya Pulse AI</title><link rel="stylesheet" href="/pulse95.css"><style>
.tw{max-width:1460px;margin:auto;padding:22px 28px 70px}.th{padding:30px;border-radius:30px;margin:18px 0}.th h1{font-size:clamp(42px,5.5vw,72px);line-height:.95;letter-spacing:-3px;margin:8px 0 14px}.toolbar{display:grid;grid-template-columns:1fr auto;gap:10px;margin-top:20px}.toolbar button{border:0;border-radius:15px;padding:0 20px;background:#69ef91;color:#12351e;font-weight:900}.selectx{position:relative}.selectbtn{width:100%;min-height:54px;border-radius:15px;padding:0 48px 0 16px;text-align:left;border:1px solid #a8ffd077!important;background:rgba(17,67,45,.72)!important;color:white!important;font-weight:800;position:relative}.selectbtn:after{content:'⌄';position:absolute;right:18px;font-size:20px}.menu{display:none;position:relative;margin-top:8px;background:rgba(247,253,249,.98);color:#17351f;border:1px solid #d9eee0;border-radius:20px;padding:10px;box-shadow:0 24px 70px #031b1040;max-height:360px;overflow:auto}.selectx.open .menu{display:block}.msearch{position:sticky;top:0;z-index:2;width:100%;padding:12px 14px;border-radius:12px!important;background:#eef4f0!important;color:#183d29!important;border:0!important;margin-bottom:7px}.opt{display:flex;align-items:center;gap:10px;width:100%;border:0!important;background:transparent!important;color:#203c2b!important;text-align:left;padding:11px 12px;border-radius:11px;font-weight:700}.opt:hover,.opt.active{background:#c9f5d5!important}.opt .oi{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:#e9f8ee}.sourcebar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:15px 0}.sourcebar a{color:#a8f4bf;font-weight:850;text-decoration:none}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{padding:21px;border-radius:24px}.tag{display:inline-flex;padding:6px 9px;border-radius:999px;background:#69ef91;color:#17351f;font-size:10px;font-weight:900}.meta{font-size:12px;color:#bdd1c4;line-height:1.55}.card h3{font-size:18px;line-height:1.3}.card a{color:#a8f4bf;text-decoration:none;font-weight:850}.empty{grid-column:1/-1;padding:32px}.count{margin-left:auto}.section{margin-top:26px}.sectionHead{display:flex;align-items:end;justify-content:space-between;gap:14px;margin:0 0 12px}.sectionHead h2{margin:0}.notice{font-size:12px;color:#c5d9cb;line-height:1.5}@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:620px){.tw{padding:12px 14px 50px}.grid{grid-template-columns:1fr}.toolbar{grid-template-columns:1fr}.toolbar button{min-height:50px}.th{padding:21px}.th h1{font-size:48px}}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/growth">Home</a><a href="/growth#counties">Counties</a><a href="/county-notices">Notices</a><a href="/ground">Sauti</a></nav><main class=tw><section class="glass th"><div class=kp10-kicker>OFFICIAL PROCUREMENT · LIVE COUNTY VIEW</div><h1>Real tenders, <span class=kp95accent>county by county.</span></h1><p class=kp10-lead>Choose a county to see current verified tender notices already indexed by Kenya Pulse AI plus live discoveries from Kenya's Public Procurement Information Portal (PPIP).</p><div class=toolbar><div class=selectx id=tenderCountySelect><button type=button class=selectbtn onclick="toggleTenderMenu()"><span id=tenderCountyLabel>Choose county</span></button><div class=menu><input class=msearch placeholder="Search county…" oninput="filterTenderOptions(this)"><div id=tenderCountyMenu><button type=button class="opt active" data-value="" onclick="pickTenderCounty(this)"><span class=oi>🌐</span>Choose county</button>{% for c in counties %}<button type=button class=opt data-value="{{c}}" onclick="pickTenderCounty(this)"><span class=oi>{{marks.get(c,'🌿')}}</span>{{c}}</button>{% endfor %}</div></div></div><input type=hidden id=county><button onclick=loadTenders()>Find tenders →</button></div><div class=sourcebar><span class=tag>OFFICIAL SOURCE</span><span class=notice>PPIP / tenders.go.ke is the canonical source. Always open the official notice before bidding.</span><a href="https://tenders.go.ke/tenders/" target=_blank rel="noopener">Open PPIP →</a></div></section><section class=section><div class=sectionHead><div><div class=kp10-kicker>ACTIVE OPPORTUNITIES</div><h2 id=title>Choose a county</h2></div><span class=tag id=count>0 FOUND</span></div><div id=feed class=grid><section class="glass card empty"><h3>Select a county to load current opportunities.</h3></section></div></section></main><script>
function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function toggleTenderMenu(){tenderCountySelect.classList.toggle('open')}
function filterTenderOptions(inp){let q=inp.value.toLowerCase();document.querySelectorAll('#tenderCountyMenu .opt').forEach(x=>x.style.display=x.textContent.toLowerCase().includes(q)?'flex':'none')}
function pickTenderCounty(el){county.value=el.dataset.value;tenderCountyLabel.textContent=el.textContent.trim();document.querySelectorAll('#tenderCountyMenu .opt').forEach(x=>x.classList.remove('active'));el.classList.add('active');tenderCountySelect.classList.remove('open');if(county.value)loadTenders()}
document.addEventListener('click',e=>{if(!e.target.closest('#tenderCountySelect'))tenderCountySelect.classList.remove('open')})
async function loadTenders(){let c=county.value;if(!c){title.textContent='Choose a county';count.textContent='0 FOUND';return}title.textContent=c+' tenders';feed.innerHTML='<section class="glass card empty"><h3>Checking official procurement sources…</h3></section>';let r=await fetch('/api/tenders-live?county='+encodeURIComponent(c),{cache:'no-store'}),j=await r.json(),items=[];(j.verified||[]).forEach(x=>items.push({kind:'VERIFIED COUNTY',title:x.title,entity:x.source_name,ref:x.reference_no||'',close:x.closes_at||'',url:x.source_url,summary:x.summary||''}));(j.ppip||[]).forEach(x=>items.push({kind:'LIVE PPIP',title:x.title,entity:x.entity,ref:x.ocid||'',close:x.close_text||'',url:x.source_url,summary:[x.method,x.category].filter(Boolean).join(' · ')}));(j.county_site||[]).forEach(x=>items.push({kind:'OFFICIAL COUNTY SITE',title:x.title,entity:x.entity,ref:'',close:x.close_text||'',url:x.source_url,summary:'Discovered on the official county government procurement/tender pages. Verify deadline on source.'}));let uniq=[],seen=new Set();for(let x of items){let k=(x.title+'|'+x.entity).toLowerCase();if(!seen.has(k)){seen.add(k);uniq.push(x)}}count.textContent=uniq.length+' FOUND';feed.innerHTML=uniq.map(x=>'<article class="glass card"><span class=tag>'+esc(x.kind)+'</span><h3>'+esc(x.title)+'</h3><div class=meta>'+esc(x.entity)+(x.ref?'<br>Ref: '+esc(x.ref):'')+(x.close?'<br>Closing: '+esc(x.close):'')+'</div>'+(x.summary?'<p>'+esc(x.summary)+'</p>':'')+'<a href="'+esc(x.url)+'" target=_blank rel="noopener">Open official source →</a></article>').join('')||'<section class="glass card empty"><h3>No current tender card was found for '+esc(c)+'.</h3><p class=notice>That does not mean no procurement exists. Open PPIP below and search the county/procuring entity directly; Kenya Pulse AI never fabricates opportunities.</p><a href="https://tenders.go.ke/tenders/" target=_blank rel="noopener">Search official PPIP →</a></section>'}
let qs=new URLSearchParams(location.search),qc=qs.get('county');if(qc&&[...county.options].some(o=>o.value===qc)){county.value=qc;loadTenders()}
</script></body></html>"""
 return render_template_string(html,counties=COUNTIES,marks=COUNTY_MARKS)

@app.get("/api/county-notices")
def county_notices_api():
 county=request.args.get("county","").strip(); category=request.args.get("category","").strip().upper()
 if county and county not in COUNTIES:return jsonify(error="Invalid county"),400
 if category and category not in NOTICE_CATEGORIES:return jsonify(error="Invalid category"),400
 sql="SELECT id,county,category,title,summary,source_name,source_url,reference_no,published_at,closes_at,event_at FROM county_notices WHERE status='VERIFIED'";args=[]
 if county:sql+=" AND county=?";args.append(county)
 if category:sql+=" AND category=?";args.append(category)
 if DATABASE_URL:sql+=" AND (closes_at IS NULL OR closes_at>=CURRENT_TIMESTAMP) AND (event_at IS NULL OR event_at>=CURRENT_TIMESTAMP)"
 else:sql+=" AND (closes_at IS NULL OR closes_at>=datetime('now')) AND (event_at IS NULL OR event_at>=datetime('now'))"
 sql+=" ORDER BY CASE WHEN event_at IS NOT NULL THEN 0 WHEN closes_at IS NOT NULL THEN 1 ELSE 2 END, COALESCE(event_at,closes_at,published_at,created_at) ASC LIMIT 100"
 with conn() as db:rows=db.execute(sql,args).fetchall()
 return jsonify(items=[dict(x) for x in rows]),200,{"Cache-Control":"public, max-age=120"}

@app.get("/county-notices")
def county_notices():
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>County Noticeboard · Kenya Pulse AI</title><link rel="stylesheet" href="/pulse95.css"><style>
.noticewrap{max-width:1460px;margin:auto;padding:22px 28px 60px}.noticeHero{padding:28px 30px;border-radius:30px;margin:18px 0;position:relative;overflow:visible}.intro{display:grid;grid-template-columns:280px 1fr;gap:30px;align-items:center}.visual{min-height:245px;border-right:1px solid #ffffff33;display:grid;place-items:center}.pin{font-size:92px;filter:drop-shadow(0 18px 22px #001f114a)}.filters{display:grid;grid-template-columns:1fr 1fr;gap:28px}.fhead{display:flex;gap:12px;align-items:center;margin-bottom:12px}.ficon{width:40px;height:40px;border-radius:14px;display:grid;place-items:center;background:#69ef9125;border:1px solid #8dffad55}.fhead b{display:block;font-size:18px}.selectx{position:relative}.selectbtn{width:100%;min-height:54px;border-radius:15px;padding:0 48px 0 16px;text-align:left;border:1px solid #a8ffd077!important;background:rgba(17,67,45,.72)!important;color:white!important;font-weight:750;box-shadow:inset 0 1px #ffffff33!important}.selectbtn:after{content:'⌄';position:absolute;right:18px;font-size:20px}.menu{display:none;position:relative;top:auto;left:auto;right:auto;z-index:auto;margin-top:9px;background:rgba(247,253,249,.98);color:#17351f;border:1px solid #d9eee0;border-radius:20px;padding:10px;box-shadow:0 24px 70px #031b1040;max-height:410px;overflow:auto;backdrop-filter:blur(18px)}.selectx.open .menu{display:block}.selectx.open{z-index:auto}.filters:has(.selectx.open){align-items:start}.msearch{position:sticky;top:0;z-index:2;width:100%;padding:12px 14px;border-radius:12px!important;background:#eef4f0!important;color:#183d29!important;border:0!important;margin-bottom:7px}.opt{display:flex;align-items:center;gap:11px;width:100%;border:0!important;background:transparent!important;color:#203c2b!important;text-align:left;padding:11px 12px;border-radius:11px;font-weight:650;box-shadow:none!important}.opt:hover,.opt.active{background:#c9f5d5!important}.opt .oi{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:#e9f8ee}.interestMenu .opt{padding:13px 12px}.interestMenu .opt .oi{width:38px;height:38px;font-size:18px}.interestMenu small{display:block;color:#667c6d;font-weight:500;margin-top:2px}.noticegrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:16px;position:relative;z-index:1}.noticecard{padding:22px;border-radius:24px}.noticecard a{color:#9effbc;font-weight:850;text-decoration:none}.tag{display:inline-block;padding:6px 9px;border-radius:999px;background:#69ef91;color:#17351f;font-size:11px;font-weight:850}.quickcats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:16px 0;position:relative;z-index:1}.qcat{padding:19px;border-radius:22px;cursor:pointer}.qcat b{display:block;margin:8px 0 4px}.empty{grid-column:1/-1;min-height:190px;display:grid;align-content:center}.count{margin-left:auto}.muted{color:#bdd1c4}@media(max-width:900px){.intro{grid-template-columns:1fr}.visual{display:none}.filters{grid-template-columns:1fr}.noticegrid,.quickcats{grid-template-columns:1fr 1fr}.noticewrap{padding:12px 14px 50px}}@media(max-width:600px){.noticegrid,.quickcats{grid-template-columns:1fr}}
</style></head><body><div class=world></div><div class=shade></div><nav class="glass kp95nav"><div class=brand>KENYA <b>PULSE</b></div><a href="/growth">Home</a><a href="/growth#counties">Counties</a><a href="/participate">Participation</a><a href="/ground">Sauti</a></nav><main class=noticewrap><section class="glass noticeHero"><div class=intro><div class=visual><div class=pin>📍</div></div><div><div class=kp10-kicker>PUBLIC INTEREST · VERIFIED SOURCES</div><h1 class=kp10-title style="font-size:clamp(38px,4.4vw,62px)">Explore County Information</h1><p class=kp10-lead>Select a county and interest to view upcoming events, tenders, jobs, bursaries, public participation notices and other important updates.</p><div class=filters>
<div><div class=fhead><span class=ficon>📍</span><div><b>Select County</b><span class=muted>Choose a county to view relevant notices</span></div></div><div class=selectx id=countySelect><button type=button class=selectbtn onclick="toggleMenu('countySelect')"><span id=countyLabel>All counties</span></button><div class=menu><input class=msearch placeholder="Search or type a county…" oninput="filterOptions(this,'countyMenu')"><div id=countyMenu><button type=button class="opt active" data-value="" onclick="pickCounty(this)"><span class=oi>🌐</span>All counties</button>{% for c in counties %}<button type=button class=opt data-value="{{c}}" onclick="pickCounty(this)"><span class=oi>{{marks.get(c,'🌿')}}</span>{{c}}</button>{% endfor %}</div></div></div></div>
<div><div class=fhead><span class=ficon>▰</span><div><b>Select Interest</b><span class=muted>Filter by the type of information you need</span></div></div><div class=selectx id=interestSelect><button type=button class=selectbtn onclick="toggleMenu('interestSelect')"><span id=interestLabel>All interests</span></button><div class="menu interestMenu" id=interestMenu><button type=button class="opt active" data-value="" data-label="All interests" onclick="pickInterest(this)"><span class=oi>▰</span><span><b>All interests</b><small>Show all public interest items</small></span></button><button type=button class=opt data-value="EVENT" data-label="Upcoming Events" onclick="pickInterest(this)"><span class=oi>📅</span><span><b>Upcoming Events</b><small>County events, forums, meetings and activities</small></span></button><button type=button class=opt data-value="TENDER" data-label="Tenders & Opportunities" onclick="pickInterest(this)"><span class=oi>📄</span><span><b>Tenders & Opportunities</b><small>Procurement notices and supplier opportunities</small></span></button><button type=button class=opt data-value="JOB" data-label="Jobs & Vacancies" onclick="pickInterest(this)"><span class=oi>💼</span><span><b>Jobs & Vacancies</b><small>County jobs, internships and public vacancies</small></span></button><button type=button class=opt data-value="BURSARY" data-label="Bursaries & Scholarships" onclick="pickInterest(this)"><span class=oi>🎓</span><span><b>Bursaries & Scholarships</b><small>Education support and bursary opportunities</small></span></button><button type=button class=opt data-value="PUBLIC_PARTICIPATION" data-label="Public Participation" onclick="pickInterest(this)"><span class=oi>👥</span><span><b>Public Participation</b><small>Forums, hearings and consultation notices</small></span></button><button type=button class=opt data-value="ALERT" data-label="Public Notices & Alerts" onclick="pickInterest(this)"><span class=oi>📣</span><span><b>Public Notices & Alerts</b><small>Official notices, service updates and major alerts</small></span></button></div></div></div>
</div></div></div></section><section class=quickcats><article class="glass qcat" onclick="setInterest('EVENT','Upcoming Events')"><span class=kp10-icon>📅</span><b>Upcoming Events</b><span class=muted>Forums, expos and public dates</span></article><article class="glass qcat" onclick="setInterest('TENDER','Tenders & Opportunities')"><span class=kp10-icon>📄</span><b>Tenders & Opportunities</b><span class=muted>Verified procurement notices</span></article><article class="glass qcat" onclick="setInterest('ALERT','Public Notices & Alerts')"><span class=kp10-icon>📣</span><b>Public Notices & Alerts</b><span class=muted>Important county updates</span></article></section><div class=fhead style="margin-top:24px"><span class=ficon>▤</span><div><b>Recent Public Interest Items</b><span class=muted>Latest verified information from counties across Kenya</span></div><span class="tag count" id=itemCount>0 ITEMS</span></div><div id=feed class=noticegrid></div></main><script>
let selectedCounty='',selectedInterest='';
function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function toggleMenu(id){document.querySelectorAll('.selectx').forEach(x=>{if(x.id!==id)x.classList.remove('open')});let box=document.getElementById(id),opening=!box.classList.contains('open');box.classList.toggle('open');if(opening){let m=box.querySelector('.menu');if(m)m.scrollTop=0;let s=box.querySelector('.msearch');if(s){s.value='';filterOptions(s,'countyMenu');if(id==='countySelect')setTimeout(()=>s.focus(),30)}}}
function filterOptions(inp,id){let q=inp.value.toLowerCase();document.querySelectorAll('#'+id+' .opt').forEach(x=>x.style.display=x.textContent.toLowerCase().includes(q)?'flex':'none')}
function markActive(parent,el){document.querySelectorAll('#'+parent+' .opt').forEach(x=>x.classList.remove('active'));el.classList.add('active')}
function pickCounty(el){selectedCounty=el.dataset.value;countyLabel.textContent=el.textContent.trim();markActive('countyMenu',el);countySelect.classList.remove('open');load()}
function pickInterest(el){selectedInterest=el.dataset.value;interestLabel.textContent=el.dataset.label;markActive('interestMenu',el);interestSelect.classList.remove('open');load()}
function setInterest(v,l){let el=document.querySelector('#interestMenu .opt[data-value="'+v+'"]');if(el)pickInterest(el);else{selectedInterest=v;interestLabel.textContent=l;load()}}
async function load(){feed.innerHTML='<section class="glass noticecard empty"><h2>Loading verified information…</h2></section>';let q='/api/county-notices?county='+encodeURIComponent(selectedCounty)+'&category='+encodeURIComponent(selectedInterest),r=await fetch(q,{cache:'no-store'}),j=await r.json(),items=j.items||[];itemCount.textContent=items.length+' ITEM'+(items.length===1?'':'S');feed.innerHTML=items.map(x=>'<article class="glass noticecard"><span class=tag>'+esc(x.category.replaceAll('_',' '))+'</span><h2>'+esc(x.title)+'</h2><div class=muted>'+esc(x.county)+' · '+esc(x.source_name)+'</div><p>'+esc(x.summary)+'</p>'+(x.reference_no?'<div class=muted>Ref: '+esc(x.reference_no)+'</div>':'')+(x.closes_at?'<div class=muted>Closes: '+new Date(x.closes_at).toLocaleString()+'</div>':'')+(x.event_at?'<div class=muted>Date: '+new Date(x.event_at).toLocaleString()+'</div>':'')+'<p><a href="'+esc(x.source_url)+'" target=_blank rel="noopener">Open official source →</a></p></article>').join('')||'<section class="glass noticecard empty"><span class=kp10-icon>✓</span><h2>No verified current notices</h2><p class=muted>Nothing verified is available for this filter yet. Expired notices are hidden automatically.</p></section>'}
document.addEventListener('click',e=>{if(!e.target.closest('.selectx'))document.querySelectorAll('.selectx').forEach(x=>x.classList.remove('open'))});
let qs=new URLSearchParams(location.search),qc=qs.get('county'),qi=qs.get('category');if(qc){let el=[...document.querySelectorAll('#countyMenu .opt')].find(x=>x.dataset.value===qc);if(el)pickCounty(el)}if(qi){let el=document.querySelector('#interestMenu .opt[data-value="'+qi+'"]');if(el)pickInterest(el)}load()
</script></body></html>"""
 return render_template_string(html,counties=COUNTIES,marks=COUNTY_MARKS)

@app.get("/health")
def health():
 dbmode="postgres" if DATABASE_URL else "sqlite-fallback";db_ok=False
 try:
  with conn() as c:c.execute("SELECT 1").fetchone();db_ok=True
 except Exception:db_ok=False
 return {"ok":db_ok,"production_ready":db_ok and dbmode=="postgres","counties":len(COUNTIES),"constituencies":sum(len(x) for x in GEOGRAPHY.values()),"wards":sum(len(w) for x in GEOGRAPHY.values() for w in x.values()),"database":dbmode,"database_ok":db_ok,"candidate_registry":True,"county_noticeboard":True,"county_notice_categories":len(NOTICE_CATEGORIES),"warning":None if db_ok and dbmode=="postgres" else "Persistent PostgreSQL is not attached and verified; responses may be lost on service restart."}
@app.get("/privacy")
def privacy():
 return legal_page("Privacy Policy","Effective 2 October 2026",[
 ("What Kenya Pulse AI collects","Information you choose to submit, including county, selected race, candidate preference and an optional issue, plus limited technical information needed to operate and protect the service."),
 ("How we protect participation","A one-way technical fingerprint is used to restrict duplicate submissions. Aggregate participant results may be displayed publicly; individual submissions and technical fingerprints are not displayed publicly."),
 ("Meta / Facebook connections","Information authorized through a Meta connection is used only to provide requested Kenya Pulse AI features. Following or sharing the Kenya Pulse AI Facebook Page is optional and is never required for a response to be counted."),
 ("How information is used","We use information to operate, secure, measure and improve Kenya Pulse AI. We do not sell individual voting preferences. Voluntary participant results are not representative of all Kenyan voters and are not an election forecast."),
 ("Your choices","For privacy questions or deletion requests, email kenyapulse2026@gmail.com. You can also use our Data Deletion instructions.")
 ])

@app.get("/terms")
def terms():
 return legal_page("Terms of Service","Effective 2 October 2026",[
 ("Using Kenya Pulse AI","Kenya Pulse AI is an open, voluntary public-participation service. Use it lawfully and do not manipulate results, submit automated or fraudulent responses, disrupt the service or impersonate others."),
 ("Understanding the results","Displayed results reflect voluntary website participants. They are not representative of all Kenyan voters and are not predictions of election outcomes. Candidate names may be participant-entered and their appearance is not an endorsement."),
 ("Service operation","Features may change or be suspended when necessary for security, reliability, legal compliance or product development. Abusive or automated activity may be restricted."),
 ("Third-party services","Meta, Facebook and other third-party services are governed by their own terms and policies. Following or sharing Kenya Pulse AI is optional and is not a condition for participation."),
 ("Contact","Questions about these terms can be sent to kenyapulse2026@gmail.com.")
 ])

@app.get("/data-deletion-instructions")
@app.get("/data-deletion")
def data_deletion():
 return legal_page("Data Deletion","Request removal of information associated with your Kenya Pulse AI use",[
 ("1. Send your request","Email kenyapulse2026@gmail.com with the subject: Kenya Pulse AI Data Deletion Request."),
 ("2. Identify the connection","Provide enough information for us to identify the relevant account or connection. Never send passwords, access tokens or other secrets."),
 ("3. Processing","Valid requests will be reviewed and processed subject to applicable legal, security and record-retention requirements.")
 ])

def legal_page(title,kicker,sections):
 cards="".join(f"<section><h2>{h}</h2><p>{p}</p></section>" for h,p in sections)
 return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} — Kenya Pulse AI</title><link rel='stylesheet' href='/pulse95.css'><style>*{{box-sizing:border-box}}body{{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui,sans-serif;line-height:1.65}}header{{border-bottom:1px solid #21432f;background:#081a12}}nav,main,footer{{max-width:920px;margin:auto;padding:20px}}nav{{display:flex;align-items:center;justify-content:space-between}}.brand{{font-weight:950;font-size:22px;letter-spacing:-.5px}}.brand b,.eyebrow,a{{color:#ffd447}}nav a{{text-decoration:none;color:#d7eadf}}main{{padding-top:64px;padding-bottom:70px}}.eyebrow{{font-weight:850;text-transform:uppercase;letter-spacing:1.5px;font-size:12px}}h1{{font-size:clamp(42px,7vw,70px);line-height:1;margin:10px 0 16px;letter-spacing:-2px}}.lead{{font-size:18px;color:#abc8b5;max-width:680px;margin-bottom:38px}}section{{background:linear-gradient(145deg,#0d2318,#0a1b13);border:1px solid #21432f;border-radius:20px;padding:24px;margin:14px 0}}h2{{font-size:19px;margin:0 0 8px}}p{{margin:0;color:#c8ddd0}}.links{{display:flex;gap:16px;flex-wrap:wrap;margin-top:30px}}footer{{border-top:1px solid #21432f;color:#87a493;font-size:13px;padding-top:28px;padding-bottom:40px}}@media(max-width:600px){{main{{padding-top:38px}}nav{{padding:16px 20px}}}}</style></head><body><div class='world'></div><div class='shade'></div><header style='background:transparent;border:0'><nav class='glass kp95nav'><div class='brand'>KENYA <b>PULSE</b></div><a href='/'>Participation</a><a href='/growth'>Counties</a><a href='/ground'>Sauti ya Ground</a></nav></header><main class='kp95wrap kp10-page'><div class='eyebrow'>Transparent participation</div><h1>{title}</h1><p class='lead'>{kicker}. Clear rules, privacy-minded participation and transparent public information.</p>{cards}<div class='links'><a href='/privacy'>Privacy Policy</a><a href='/terms'>Terms of Service</a><a href='/data-deletion'>Data Deletion</a><a href='/methodology'>Methodology</a></div></main><footer>KENYA PULSE AI · Your county. Your voice. · Open voluntary participation, not an election forecast.</footer></body></html>"""

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
  ("Status","The request has been recorded for review. Kenya Pulse AI does not publicly display individual participant submissions or technical fingerprints."),
  ("Need help?","Email kenyapulse2026@gmail.com and include your confirmation code. Never send passwords or access tokens.")
 ])

@app.get("/methodology")
def methodology():
 return legal_page("How Kenya Pulse AI Works","Methodology & transparency",[
  ("Open participation","Kenya Pulse AI is an open, voluntary online participation dashboard. It is not a probability sample, and participant results are not representative of all Kenyan voters."),
  ("What participants submit","Participants select a county and race, then choose a published candidate photo or use the manual candidate option when a verified photo profile is not yet available. Public results display aggregate participant counts and percentages rather than individual submissions."),
  ("Duplicate protection","A one-way technical fingerprint derived from limited network and client information is used to restrict duplicate submissions for the same county and race. Raw fingerprints are not displayed publicly."),
  ("How results are calculated","No weighting or normalization is applied. Undecided participants may enter “Undecided”. Candidate names are participant-entered and spelling variants may be consolidated in future audited releases."),
  ("Independence of results","Kenya Pulse AI does not endorse candidates or predict election outcomes. Advertising is clearly separated from participation controls and results, and advertising does not affect whether a response is counted.")
 ])

@app.get("/api/candidates")
def candidates_api():
 race=request.args.get("race","").strip();county=request.args.get("county","").strip();constituency=request.args.get("constituency","").strip();ward=request.args.get("ward","").strip()
 if race not in RACES:return jsonify(candidates=[])
 sql="SELECT id,name,party,status,photo_url,bio,source_url FROM candidates WHERE active=TRUE AND race=?";args=[race]
 if race=="President":
  sql+=" AND LOWER(name) IN (LOWER(?),LOWER(?))";args.extend(["William Ruto","Edwin Sifuna"])
 if race!="President":sql+=" AND county=?";args.append(county)
 if race in {"Member of Parliament","MCA"}:sql+=" AND constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND ward=?";args.append(ward)
 sql+=" ORDER BY name LIMIT 20"
 with conn() as c:
  rows=c.execute(sql,args).fetchall()
  out=[]
  for x in rows:
   item=dict(x);item["profile_url"]="/candidate/"+str(item["id"]); aliases=c.execute("SELECT alias FROM candidate_aliases WHERE candidate_id=? AND verified=TRUE ORDER BY alias",(item["id"],)).fetchall()
   item["aliases"]=[a["alias"] for a in aliases];out.append(item)
 return jsonify(candidates=out)


EVIDENCE_TYPES={"OFFICIAL_RECORD","CANDIDATE_STATEMENT","INDEPENDENT_REPORTING","DISPUTED_CLAIM","NOT_INDEPENDENTLY_VERIFIED"}
CANDIDATE_STAGES={"ASPIRANT","PARTY_NOMINEE","IEBC_CLEARED","WITHDRAWN","DISQUALIFIED","UNKNOWN"}

def ensure_candidate_evidence_schema():
 with conn() as c:
  if c.pg:
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_evidence(
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    claim TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_date DATE,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'PUBLISHED',
    checked_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
   );""")
  else:
   c.execute("""CREATE TABLE IF NOT EXISTS candidate_evidence(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    claim TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'PUBLISHED',
    checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
   );""")
  c.execute("CREATE INDEX IF NOT EXISTS candidate_evidence_lookup ON candidate_evidence(candidate_id,status,category);")

def candidate_scope_sql(race,county="",constituency="",ward=""):
 sql="SELECT id,name,party,status,source_url,photo_url,bio,campaign_url,public_contact FROM candidates WHERE active=TRUE AND race=?";args=[race]
 if race!="President":sql+=" AND county=?";args.append(county)
 if race in {"Member of Parliament","MCA"}:sql+=" AND constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND ward=?";args.append(ward)
 return sql,args


def normalized_candidate_stage(status):
 raw=(status or "").strip().upper().replace(" ","_")
 return raw if raw in CANDIDATE_STAGES else "UNKNOWN"

@app.get("/api/candidates/compare")
def candidates_compare_api():
 ensure_candidate_evidence_schema()
 race=(request.args.get("race") or "").strip();county=(request.args.get("county") or "").strip()
 constituency=(request.args.get("constituency") or "").strip();ward=(request.args.get("ward") or "").strip()
 if race not in RACES:return jsonify(error="Invalid race"),400
 if race!="President" and county not in COUNTIES:return jsonify(error="Choose a valid county"),400
 if race in {"Member of Parliament","MCA"} and (not constituency or not geography_ok(county,constituency,ward if race=="MCA" else "")):
  return jsonify(error="Choose a valid constituency"+(" and ward" if race=="MCA" else "")),400
 sql,args=candidate_scope_sql(race,county,constituency,ward);sql+=" ORDER BY name"
 with conn() as c:
  rows=c.execute(sql,args).fetchall();out=[]
  for row in rows:
   item=dict(row)
   item["stage"]=normalized_candidate_stage(item.get("status"))
   item["stage_note"]="IEBC_CLEARED means formally cleared by IEBC. ASPIRANT and PARTY_NOMINEE do not mean the person is on the final ballot."
   ev=c.execute("""SELECT id,category,claim,evidence_type,source_title,source_url,source_date,notes,checked_at
                   FROM candidate_evidence WHERE candidate_id=? AND status='PUBLISHED'
                   ORDER BY category,COALESCE(source_date,checked_at) DESC,id DESC""",(item["id"],)).fetchall()
   item["evidence"]=[dict(x) for x in ev]
   item["evidence_count"]=len(item["evidence"])
   item["evidence_note"]="Evidence count measures published sourced records only. It is not a candidate score or endorsement."
   out.append(item)
 return jsonify(race=race,county=county,constituency=constituency,ward=ward,candidates=out,
  methodology="Compare documented facts and source records. Kenya Pulse AI does not rank, endorse or recommend candidates."),200,{"Cache-Control":"public, max-age=60"}


@app.get("/api/admin/evidence-gaps")
def evidence_gaps_admin():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_candidate_evidence_schema()
 try:limit=max(1,min(100,int(request.args.get("limit","30"))))
 except:limit=30
 race=(request.args.get("race") or "").strip()
 county=(request.args.get("county") or "").strip()
 sql="""SELECT c.id,c.name,c.race,c.county,c.constituency,c.ward,c.party,c.status,c.source_url,
        COUNT(e.id) AS evidence_count
        FROM candidates c
        LEFT JOIN candidate_evidence e ON e.candidate_id=c.id AND e.status='PUBLISHED'
        WHERE c.active=TRUE"""
 args=[]
 if race:
  if race not in RACES:return jsonify(error="Invalid race"),400
  sql+=" AND c.race=?";args.append(race)
 if county:
  if county not in COUNTIES:return jsonify(error="Invalid county"),400
  sql+=" AND (c.county=? OR c.race='President')";args.append(county)
 sql+=" GROUP BY c.id,c.name,c.race,c.county,c.constituency,c.ward,c.party,c.status,c.source_url ORDER BY evidence_count ASC,c.race,c.county,c.name LIMIT ?"
 args.append(limit)
 with conn() as c:rows=c.execute(sql,args).fetchall()
 return jsonify(candidates=[dict(x) for x in rows],research_rule="Research lowest-evidence candidates first. Only publish sourced, attributable facts; do not infer a ranking or endorsement.")

@app.post("/api/admin/candidate-evidence/batch")
def candidate_evidence_batch_admin():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_candidate_evidence_schema()
 d=request.get_json(silent=True) or {};items=d.get("items") or []
 if not isinstance(items,list) or not items or len(items)>50:return jsonify(error="items must contain 1 to 50 evidence records"),400
 saved=[];errors=[]
 with conn() as c:
  for i,item in enumerate(items):
   try:
    raw_candidate_id=item.get("candidate_id")
    candidate_id=int(raw_candidate_id) if raw_candidate_id not in (None,"") else None
    candidate_name=re.sub(r"\s+"," ",str(item.get("candidate_name") or "").strip())[:120]
    candidate_race=str(item.get("race") or "").strip()
    candidate_county=str(item.get("county") or "").strip()
    candidate_constituency=str(item.get("constituency") or "").strip()
    candidate_ward=str(item.get("ward") or "").strip()
    category=re.sub(r"\s+"," ",str(item.get("category") or "").strip())[:60]
    claim=re.sub(r"\s+"," ",str(item.get("claim") or "").strip())[:700]
    evidence_type=str(item.get("evidence_type") or "").strip().upper()
    source_title=re.sub(r"\s+"," ",str(item.get("source_title") or "").strip())[:180]
    source_url=str(item.get("source_url") or "").strip()[:800]
    source_date=str(item.get("source_date") or "").strip()[:10] or None
    notes=re.sub(r"\s+"," ",str(item.get("notes") or "").strip())[:500] or None
    if not category or not claim or evidence_type not in EVIDENCE_TYPES or not source_title or not source_url.startswith("https://"):
     raise ValueError("Invalid or incomplete evidence record")
    if candidate_id is None:
     if not candidate_name or candidate_race not in RACES: raise ValueError("Provide candidate_id or candidate_name + valid race")
     resolve_sql="SELECT id FROM candidates WHERE active=TRUE AND race=? AND LOWER(name)=LOWER(?)";resolve_args=[candidate_race,candidate_name]
     if candidate_race!="President":
      if candidate_county not in COUNTIES: raise ValueError("Valid county required for this race")
      resolve_sql+=" AND county=?";resolve_args.append(candidate_county)
     if candidate_race in {"Member of Parliament","MCA"}:
      if not candidate_constituency: raise ValueError("Constituency required for this race")
      resolve_sql+=" AND constituency=?";resolve_args.append(candidate_constituency)
     if candidate_race=="MCA":
      if not candidate_ward: raise ValueError("Ward required for MCA")
      resolve_sql+=" AND ward=?";resolve_args.append(candidate_ward)
     matches=c.execute(resolve_sql,resolve_args).fetchall()
     if len(matches)!=1: raise ValueError("Candidate name did not resolve uniquely in the selected race and area")
     candidate_id=matches[0]["id"]
    elif not c.execute("SELECT id FROM candidates WHERE id=? AND active=TRUE",(candidate_id,)).fetchone():
     raise ValueError("Candidate not found or inactive")
    dup=c.execute("""SELECT id FROM candidate_evidence WHERE candidate_id=? AND LOWER(claim)=LOWER(?) AND source_url=? AND status='PUBLISHED'""",(candidate_id,claim,source_url)).fetchone()
    if dup:
     saved.append({"index":i,"candidate_id":candidate_id,"duplicate":True});continue
    c.execute("""INSERT INTO candidate_evidence(candidate_id,category,claim,evidence_type,source_title,source_url,source_date,notes)
                 VALUES(?,?,?,?,?,?,?,?)""",(candidate_id,category,claim,evidence_type,source_title,source_url,source_date,notes))
    saved.append({"index":i,"candidate_id":candidate_id,"duplicate":False})
   except Exception as e:errors.append({"index":i,"error":str(e)[:180]})
 return jsonify(saved=saved,errors=errors,saved_count=len(saved),error_count=len(errors)),(207 if errors else 201)



def ensure_social_content_schema():
 with conn() as c:
  if c.pg:
   c.execute("""CREATE TABLE IF NOT EXISTS social_content_queue(
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    topic TEXT NOT NULL,
    hook TEXT NOT NULL,
    body TEXT NOT NULL,
    cta TEXT NOT NULL,
    target_url TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    race TEXT,
    county TEXT,
    constituency TEXT,
    ward TEXT,
    status TEXT NOT NULL DEFAULT 'READY',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ
   );""")
  else:
   c.execute("""CREATE TABLE IF NOT EXISTS social_content_queue(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    topic TEXT NOT NULL,
    hook TEXT NOT NULL,
    body TEXT NOT NULL,
    cta TEXT NOT NULL,
    target_url TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    race TEXT,
    county TEXT,
    constituency TEXT,
    ward TEXT,
    status TEXT NOT NULL DEFAULT 'READY',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
   );""")
  c.execute("CREATE INDEX IF NOT EXISTS social_content_status ON social_content_queue(status,platform,created_at);")

@app.post("/api/admin/social-content/batch")
def social_content_batch_admin():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_social_content_schema()
 d=request.get_json(silent=True) or {};items=d.get("items") or []
 if not isinstance(items,list) or not items or len(items)>30:return jsonify(error="items must contain 1 to 30 posts"),400
 saved=[];errors=[]
 with conn() as c:
  for i,item in enumerate(items):
   try:
    platform=str(item.get("platform") or "").strip().lower()
    if platform not in {"facebook","instagram","tiktok"}:raise ValueError("platform must be facebook, instagram or tiktok")
    topic=re.sub(r"\s+"," ",str(item.get("topic") or "").strip())[:120]
    hook=re.sub(r"\s+"," ",str(item.get("hook") or "").strip())[:220]
    body=str(item.get("body") or "").strip()[:2200]
    cta=re.sub(r"\s+"," ",str(item.get("cta") or "").strip())[:240]
    race=str(item.get("race") or "").strip();county=str(item.get("county") or "").strip()
    constituency=str(item.get("constituency") or "").strip();ward=str(item.get("ward") or "").strip()
    sources=item.get("sources") or []
    if not topic or not hook or not body or not cta or not isinstance(sources,list) or not sources:raise ValueError("topic, hook, body, cta and sources are required")
    clean_sources=[]
    for src in sources[:8]:
     title=re.sub(r"\s+"," ",str((src or {}).get("title") or "").strip())[:180]
     url=str((src or {}).get("url") or "").strip()[:800]
     if not title or not url.startswith("https://"):raise ValueError("Every source needs a title and https URL")
     clean_sources.append({"title":title,"url":url})
    if race and race not in RACES:raise ValueError("Invalid race")
    if race!="President" and race and county not in COUNTIES:raise ValueError("Valid county required")
    if race in {"Member of Parliament","MCA"} and not constituency:raise ValueError("Constituency required")
    if race=="MCA" and not ward:raise ValueError("Ward required")
    q={"race":race or "President","county":county,"constituency":constituency,"ward":ward,"utm_source":platform,"utm_medium":"social","utm_campaign":"candidate_explorer"}
    target=request.url_root.rstrip("/")+"/candidate-explorer?"+urllib.parse.urlencode(q)
    c.execute("""INSERT INTO social_content_queue(platform,topic,hook,body,cta,target_url,sources_json,race,county,constituency,ward)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(platform,topic,hook,body,cta,target,json.dumps(clean_sources),race or None,county or None,constituency or None,ward or None))
    saved.append({"index":i,"platform":platform,"target_url":target})
   except Exception as e:errors.append({"index":i,"error":str(e)[:180]})
 return jsonify(saved=saved,errors=errors,saved_count=len(saved),error_count=len(errors)),(207 if errors else 201)

@app.get("/api/admin/social-content/queue")
def social_content_queue_admin():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_social_content_schema()
 platform=(request.args.get("platform") or "").strip().lower();status=(request.args.get("status") or "READY").strip().upper()
 sql="SELECT id,platform,topic,hook,body,cta,target_url,sources_json,race,county,constituency,ward,status,created_at,published_at FROM social_content_queue WHERE status=?";args=[status]
 if platform:
  if platform not in {"facebook","instagram","tiktok"}:return jsonify(error="Invalid platform"),400
  sql+=" AND platform=?";args.append(platform)
 sql+=" ORDER BY created_at DESC LIMIT 50"
 with conn() as c:rows=c.execute(sql,args).fetchall()
 out=[]
 for row in rows:
  item=dict(row)
  try:item["sources"]=json.loads(item.pop("sources_json") or "[]")
  except:item["sources"]=[]
  out.append(item)
 return jsonify(posts=out)

@app.post("/api/admin/social-content/<int:content_id>/status")
def social_content_status_admin(content_id):
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_social_content_schema()
 d=request.get_json(silent=True) or {};status=str(d.get("status") or "").strip().upper()
 if status not in {"READY","SCHEDULED","PUBLISHED","REJECTED"}:return jsonify(error="Invalid status"),400
 with conn() as c:
  row=c.execute("SELECT id FROM social_content_queue WHERE id=?",(content_id,)).fetchone()
  if not row:return jsonify(error="Post not found"),404
  if status=="PUBLISHED":c.execute("UPDATE social_content_queue SET status=?,published_at=CURRENT_TIMESTAMP WHERE id=?",(status,content_id))
  else:c.execute("UPDATE social_content_queue SET status=? WHERE id=?",(status,content_id))
 return jsonify(updated=True,status=status)

@app.get("/api/research/topics")
def research_topics():
 return jsonify(topics=[
  {"key":"election_readiness","label":"Election readiness","preferred_sources":["IEBC","Kenya Gazette","Parliament"]},
  {"key":"public_record","label":"Public record and offices held","preferred_sources":["Parliament","County Assembly","official government records"]},
  {"key":"policy_positions","label":"Documented policy positions","preferred_sources":["candidate official statements","party manifestos","reputable independent reporting"]},
  {"key":"public_finance","label":"Budgets, spending and audit findings","preferred_sources":["Controller of Budget","Auditor-General","Treasury","county records"]},
  {"key":"integrity_legal","label":"Documented court or integrity matters","preferred_sources":["Kenya Law","EACC","court records","reputable independent reporting"]},
  {"key":"delivery_record","label":"Documented delivery record","preferred_sources":["official project records","audits","budget implementation reports","reputable independent reporting"]}
 ],rules=[
  "Facts must be attributable to a source URL.",
  "Do not label anyone an official candidate unless IEBC has formally cleared them; use aspirant or party nominee where appropriate.",
  "Candidate allegations must never be written as established fact unless supported by authoritative records.",
  "Polls must identify pollster, field dates and sample limitations.",
  "Evidence records inform voters; they are not candidate ratings or endorsements."
 ])

@app.get("/api/social/entry-link")
def social_entry_link():
 platform=(request.args.get("platform") or "facebook").strip().lower()
 if platform not in {"facebook","instagram","tiktok","whatsapp","direct"}:return jsonify(error="Invalid platform"),400
 race=(request.args.get("race") or "President").strip();county=(request.args.get("county") or "").strip()
 constituency=(request.args.get("constituency") or "").strip();ward=(request.args.get("ward") or "").strip()
 if race not in RACES:return jsonify(error="Invalid race"),400
 if race!="President" and county not in COUNTIES:return jsonify(error="Choose a valid county"),400
 if race in {"Member of Parliament","MCA"} and (not constituency or not geography_ok(county,constituency,ward if race=="MCA" else "")):
  return jsonify(error="Choose a valid constituency"+(" and ward" if race=="MCA" else "")),400
 q=urllib.parse.urlencode({"race":race,"county":county,"constituency":constituency,"ward":ward,"utm_source":platform,"utm_medium":"social","utm_campaign":"candidate_explorer"})
 return jsonify(url=request.url_root.rstrip("/")+"/candidate-explorer?"+q,platform=platform,cta="Compare the candidates using sourced facts on Kenya Pulse AI.")


@app.post("/api/admin/candidate-evidence")
def candidate_evidence_admin():
 if not admin_authorized():return jsonify(error="Unauthorized"),401
 ensure_candidate_evidence_schema()
 d=request.get_json(silent=True) or {}
 try:candidate_id=int(d.get("candidate_id"))
 except:return jsonify(error="candidate_id is required"),400
 category=re.sub(r"\s+"," ",str(d.get("category") or "").strip())[:60]
 claim=re.sub(r"\s+"," ",str(d.get("claim") or "").strip())[:700]
 evidence_type=str(d.get("evidence_type") or "").strip().upper()
 source_title=re.sub(r"\s+"," ",str(d.get("source_title") or "").strip())[:180]
 source_url=str(d.get("source_url") or "").strip()[:800]
 source_date=str(d.get("source_date") or "").strip()[:10] or None
 notes=re.sub(r"\s+"," ",str(d.get("notes") or "").strip())[:500] or None
 if not category or not claim or evidence_type not in EVIDENCE_TYPES or not source_title or not source_url.startswith(("https://","http://")):
  return jsonify(error="category, claim, a valid evidence_type, source_title and source_url are required",allowed_evidence_types=sorted(EVIDENCE_TYPES)),400
 with conn() as c:
  exists=c.execute("SELECT id FROM candidates WHERE id=?",(candidate_id,)).fetchone()
  if not exists:return jsonify(error="Candidate not found"),404
  c.execute("""INSERT INTO candidate_evidence(candidate_id,category,claim,evidence_type,source_title,source_url,source_date,notes)
               VALUES(?,?,?,?,?,?,?,?)""",(candidate_id,category,claim,evidence_type,source_title,source_url,source_date,notes))
 return jsonify(saved=True,message="Evidence record published."),201


@app.get("/candidate/<int:candidate_id>")
def public_candidate_profile(candidate_id):
 ensure_candidate_evidence_schema()
 with conn() as c:
  row=c.execute("""SELECT id,name,race,county,constituency,ward,party,status,source_url,photo_url,bio,campaign_url,public_contact
                   FROM candidates WHERE id=? AND active=TRUE""",(candidate_id,)).fetchone()
  if not row:return "Candidate profile not found",404
  evidence=c.execute("""SELECT category,claim,evidence_type,source_title,source_url,source_date
                        FROM candidate_evidence WHERE candidate_id=? AND status='PUBLISHED'
                        ORDER BY category,COALESCE(source_date,checked_at) DESC,id DESC""",(candidate_id,)).fetchall()
 p=dict(row);ev=[dict(x) for x in evidence]
 return render_template_string("""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>{{p.name}} · Kenya Pulse AI</title><link rel=stylesheet href=/pulse95.css><style>
 body{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui}.w{max-width:940px;margin:auto;padding:30px 18px 70px}.hero{display:grid;grid-template-columns:280px 1fr;gap:28px;padding:28px}.portrait{width:100%;aspect-ratio:1/1;border-radius:28px;object-fit:cover;background:#103522}.tag{display:inline-flex;padding:6px 9px;border-radius:999px;border:1px solid #69ef9150;background:#69ef9116;font-size:10px;font-weight:900;letter-spacing:.06em}.muted{color:#a9c6b4}.facts{display:grid;gap:12px;margin-top:18px}.fact{padding:18px}.fact a{color:#8df7ac}.src{font-size:12px;color:#9db5a5}.note{margin-top:16px;padding:14px;border-radius:14px;background:#ffffff09;border:1px solid #ffffff18;font-size:12px;line-height:1.5}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.btn{display:inline-flex;padding:12px 16px;border-radius:14px;background:#69ef91;color:#092817;text-decoration:none;font-weight:900}@media(max-width:700px){.hero{grid-template-columns:1fr}.portrait{max-width:340px}}
 </style></head><body><main class=w><section class="glass hero">{% if p.photo_url %}<img class=portrait src="{{p.photo_url}}" alt="">{% else %}<div class=portrait></div>{% endif %}<div><span class=tag>{{p.status}}</span><h1>{{p.name}}</h1><p class=muted>{{p.party or 'Party not verified'}} · {{p.race}}</p><p>{{p.bio or 'No public bio has been published yet.'}}</p><div class=actions><a class=btn href="/candidate-explorer?race={{p.race|urlencode}}">Compare candidates</a>{% if p.source_url %}<a class=btn href="{{p.source_url}}" target=_blank rel=noopener>Source record</a>{% endif %}</div><div class=note>Profile status reflects currently documented public information. ASPIRANT does not mean IEBC-cleared or officially on the final ballot.</div></div></section><section class=facts>{% if evidence %}{% for e in evidence %}<article class="glass fact"><span class=tag>{{e.evidence_type.replace('_',' ')}}</span><h3>{{e.category}}</h3><p>{{e.claim}}</p><div class=src><a href="{{e.source_url}}" target=_blank rel=noopener>{{e.source_title}}</a>{% if e.source_date %} · {{e.source_date}}{% endif %}</div></article>{% endfor %}{% else %}<article class="glass fact"><h3>Independent evidence</h3><p class=muted>No additional Kenya Pulse AI evidence records have been published yet. This is not a judgment about the candidate.</p></article>{% endif %}</section></main></body></html>""",p=p,evidence=ev)

@app.get("/candidate-explorer")
def candidate_explorer():
 counties=json.dumps(COUNTIES)
 races=json.dumps(RACES)
 return render_template_string("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
 <title>Candidate Explorer — Kenya Pulse AI</title><link rel='stylesheet' href='/pulse95.css'>
 <style>
 *{box-sizing:border-box}body{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui,sans-serif}.wrap{max-width:1180px;margin:auto;padding:24px}
 .top{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:18px 0}.brand{font-weight:950;font-size:23px}.brand b,.gold{color:#ffd447}
 a{color:#ffd447}.hero{padding:52px 0 24px}.hero h1{font-size:clamp(42px,7vw,76px);line-height:.96;letter-spacing:-3px;margin:8px 0 18px}.hero p{max-width:800px;color:#bcd2c4;font-size:18px;line-height:1.65}
 .panel,.card{background:linear-gradient(145deg,#0d2318,#091a12);border:1px solid #21432f;border-radius:22px}.panel{padding:20px;margin:22px 0}.filters{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
 label{font-size:12px;color:#9ab5a4;font-weight:800;text-transform:uppercase;letter-spacing:.8px}select{width:100%;margin-top:7px;background:#07160f;color:#fff;border:1px solid #315c42;border-radius:12px;padding:13px}
 .cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{padding:22px}.name{font-size:25px;font-weight:900}.party{color:#9ab5a4;margin:4px 0 16px}
 .evidence{padding:14px 0;border-top:1px solid #1e3a29}.tag{display:inline-block;border:1px solid #41664d;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:900;letter-spacing:.6px;color:#d7eadf}
 .claim{font-size:15px;line-height:1.55;margin:9px 0}.source{font-size:12px;color:#91ad9b}.empty{padding:22px;color:#abc4b4;border:1px dashed #315c42;border-radius:16px}
 .notice{font-size:13px;color:#9cb4a5;line-height:1.55}.share{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.btn{background:#ffd447;color:#07150e;border:0;border-radius:12px;padding:11px 14px;font-weight:900;text-decoration:none}
 @media(max-width:850px){.filters,.cards{grid-template-columns:1fr}.hero h1{letter-spacing:-2px}}
 </style></head><body><div class='wrap'>
 <div class='top'><div class='brand'>KENYA <b>PULSE</b></div><div><a href='/'>Participation</a> · <a href='/methodology'>Methodology</a></div></div>
 <section class='hero'><div class='gold' style='font-weight:900;text-transform:uppercase;letter-spacing:1.3px;font-size:12px'>Vote with facts, not rumours</div>
 <h1>Know the people asking for your vote.</h1><p>Choose your area and seat. Kenya Pulse AI shows sourced records side by side so you can decide what matters to you. No candidate ranking. No endorsement. Open the evidence and judge for yourself.</p></section>
 <section class='panel'><div class='filters'>
 <div><label>County<select id='county'></select></label></div><div><label>Seat<select id='race'></select></label></div>
 <div><label>Constituency<select id='constituency'><option value=''>Not required</option></select></label></div><div><label>Ward<select id='ward'><option value=''>Not required</option></select></label></div>
 </div><div class='share'><a class='btn' id='sharefb' target='_blank' rel='noopener'>Share on Facebook</a><a class='btn' id='sharewa' target='_blank' rel='noopener'>Share on WhatsApp</a></div>
 <div class='notice'>Evidence labels: Official record · Candidate statement · Independent reporting · Disputed claim · Not independently verified. Evidence volume is never treated as a score.</div></section>
 <main id='cards' class='cards'><div class='empty'>Choose a county and seat to compare candidates.</div></main>
 </div><script>
 const COUNTIES={{counties|safe}},RACES={{races|safe}},county=document.getElementById('county'),race=document.getElementById('race'),cons=document.getElementById('constituency'),ward=document.getElementById('ward'),cards=document.getElementById('cards');
 county.innerHTML='<option value="">Choose county</option>'+COUNTIES.map(x=>'<option>'+x+'</option>').join('');
 race.innerHTML=RACES.map(x=>'<option>'+x+'</option>').join('');
 const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
 async function geog(){
  cons.innerHTML='<option value="">Not required</option>';ward.innerHTML='<option value="">Not required</option>';
  if(!county.value)return;
  const d=await fetch('/api/geography?county='+encodeURIComponent(county.value)).then(r=>r.json());
  cons.innerHTML='<option value="">Choose constituency</option>'+(d.constituencies||[]).map(x=>'<option>'+esc(x)+'</option>').join('');
 }
 async function wards(){
  ward.innerHTML='<option value="">Choose ward</option>';if(!county.value||!cons.value)return;
  const d=await fetch('/api/geography?county='+encodeURIComponent(county.value)+'&constituency='+encodeURIComponent(cons.value)).then(r=>r.json());
  ward.innerHTML='<option value="">Choose ward</option>'+(d.wards||[]).map(x=>'<option>'+esc(x)+'</option>').join('');
 }
 function scopeOK(){if(race.value==='President')return true;if(!county.value)return false;if(['Member of Parliament','MCA'].includes(race.value)&&!cons.value)return false;if(race.value==='MCA'&&!ward.value)return false;return true}
 async function load(){
  const needCons=['Member of Parliament','MCA'].includes(race.value),needWard=race.value==='MCA';
  cons.disabled=!needCons;ward.disabled=!needWard;if(!scopeOK()){cards.innerHTML='<div class="empty">Choose the required location to load this race.</div>';return}
  const q=new URLSearchParams({race:race.value,county:county.value,constituency:cons.value,ward:ward.value});
  const d=await fetch('/api/candidates/compare?'+q).then(r=>r.json());
  if(!d.candidates?.length){cards.innerHTML='<div class="empty">No verified candidate registry entries are published for this exact race yet. Kenya Pulse AI will not invent names.</div>';return}
  cards.innerHTML=d.candidates.map(c=>'<article class="card">'+(c.photo_url?'<img src="'+esc(c.photo_url)+'" alt="" style="width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:16px;margin-bottom:14px">':'')+'<div class="name">'+esc(c.name)+'</div><div class="party">'+esc(c.party||'Party not verified')+' · '+esc(c.status||'Status not set')+'</div><div style="margin-bottom:14px"><a class="btn" href="/candidate/'+c.id+'">Open profile →</a></div>'+
   (c.evidence?.length?c.evidence.map(e=>'<div class="evidence"><span class="tag">'+esc(e.evidence_type.replaceAll('_',' '))+'</span><div class="claim">'+esc(e.claim)+'</div><div class="source">'+esc(e.category)+' · <a href="'+esc(e.source_url)+'" target="_blank" rel="noopener">'+esc(e.source_title)+'</a>'+(e.source_date?' · '+esc(e.source_date):'')+'</div></div>').join(''):'<div class="empty">No published evidence records yet. Absence of evidence here is not a judgment about this candidate.</div>')+'</article>').join('');
  const url=location.origin+'/candidate-explorer?'+q.toString()+'&utm_source=social';document.getElementById('sharefb').href='https://www.facebook.com/sharer/sharer.php?u='+encodeURIComponent(url);document.getElementById('sharewa').href='https://wa.me/?text='+encodeURIComponent('Compare the candidates using sourced records on Kenya Pulse AI: '+url);
 }
 county.onchange=async()=>{await geog();await load()};race.onchange=load;cons.onchange=async()=>{await wards();await load()};ward.onchange=load;
 const p=new URLSearchParams(location.search);if(p.get('county')&&COUNTIES.includes(p.get('county')))county.value=p.get('county');if(p.get('race')&&RACES.includes(p.get('race')))race.value=p.get('race');
 (async()=>{if(county.value){await geog();if(p.get('constituency')){cons.value=p.get('constituency');await wards()}if(p.get('ward'))ward.value=p.get('ward')}await load()})();
 </script></body></html>""",counties=counties,races=races)


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

def participation_fingerprint():
 raw=(request.headers.get("X-Forwarded-For",request.remote_addr or "").split(",")[0]+request.headers.get("User-Agent","")+SALT).encode()
 return hashlib.sha256(raw).hexdigest()

@app.get("/api/participation/progress")
def participation_progress():
 county=request.args.get("county","")
 if county not in COUNTIES:return jsonify(error="Invalid county"),400
 with conn() as c:
  rows=c.execute("SELECT race,constituency,ward FROM pulse_votes WHERE county=? AND fp=? ORDER BY id",(county,participation_fingerprint())).fetchall()
 completed=[race for race in RACES if any(x["race"]==race for x in rows)]
 area=next((x for x in rows if x["race"]=="Member of Parliament"),None)
 return jsonify(
  completed=completed,
  next_race=next((r for r in RACES if r not in completed),None),
  complete=len(completed)==6,
  constituency=area["constituency"] if area else "",
  optional_support={"unlocked":len(completed)==6,"configured":paystack_configured(),"minimum_kes":5}
 ),200,{"Cache-Control":"private, no-store"}

@app.post("/api/vote")
def vote():
 d=request.get_json(silent=True) or {}; county=d.get("county","").strip(); race=d.get("race","").strip(); constituency=d.get("constituency","").strip()[:80]; ward=d.get("ward","").strip()[:80]; candidate=re.sub(r"\s+"," ",d.get("candidate","").strip())[:80]; submitted_candidate_text=candidate; candidate_id=d.get("candidate_id"); issue=d.get("issue","").strip()[:120]
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
  candidate=re.sub(r"\s+"," ",candidate).strip()
  if len(candidate)<2:return jsonify(error="Enter the person’s full name or known alias."),400
  # Resolve exact canonical name or a VERIFIED alias inside the selected seat/scope.
  resolve_sql="""SELECT DISTINCT c.id,c.name FROM candidates c
                 LEFT JOIN candidate_aliases a ON a.candidate_id=c.id
                 WHERE c.active=TRUE AND c.race=?
                 AND (LOWER(c.name)=LOWER(?) OR (a.verified=TRUE AND LOWER(a.alias)=LOWER(?)))"""
  resolve_args=[race,candidate,candidate]
  if race!="President":resolve_sql+=" AND c.county=?";resolve_args.append(county)
  if race in {"Member of Parliament","MCA"}:resolve_sql+=" AND c.constituency=?";resolve_args.append(constituency)
  if race=="MCA":resolve_sql+=" AND c.ward=?";resolve_args.append(ward)
  with conn() as c:
   matches=c.execute(resolve_sql,resolve_args).fetchall()
  if len(matches)==1:
   candidate_id=matches[0]["id"];candidate=matches[0]["name"]
  elif len(matches)>1:
   return jsonify(error="That alias matches more than one person for this seat. Enter the full name."),409
 raw=(request.headers.get("X-Forwarded-For",request.remote_addr or "").split(",")[0]+request.headers.get("User-Agent","")+SALT).encode(); fp=hashlib.sha256(raw).hexdigest()
 with conn() as c:
  completed={x["race"] for x in c.execute("SELECT DISTINCT race FROM pulse_votes WHERE county=? AND fp=?",(county,fp)).fetchall()}
 if race not in completed:
  next_race=next((r for r in RACES if r not in completed),None)
  if race!=next_race:return jsonify(error="Complete the seats in order.",next_race=next_race),409
 # Neutral integrity control: cap rapid submissions from the same technical fingerprint.
 with conn() as c:
  rate_sql="SELECT count(*) n FROM pulse_votes WHERE fp=? AND created_at >= CURRENT_TIMESTAMP - INTERVAL '10 minutes'" if c.pg else "SELECT count(*) n FROM pulse_votes WHERE fp=? AND created_at >= datetime('now','-10 minutes')"
  recent=c.execute(rate_sql,(fp,)).fetchone()
  n=recent["n"] if hasattr(recent,"keys") else recent[0]
  if n>=8:return jsonify(error="Too many submissions in a short period. Please try again later."),429
 try:
  with conn() as c:
   c.execute("INSERT INTO pulse_votes(county,race,candidate,candidate_id,submitted_candidate_text,issue,fp,constituency,ward) VALUES(?,?,?,?,?,?,?,?,?)",(county,race,candidate,candidate_id,submitted_candidate_text,issue,fp,constituency or None,ward or None))
  with conn() as c:
   check=c.execute("SELECT count(*) n FROM pulse_votes WHERE county=? AND race=? AND fp=? AND LOWER(candidate)=LOWER(?)",(county,race,fp,candidate)).fetchone()
  saved=check["n"] if hasattr(check,"keys") else check[0]
  if saved<1:return jsonify(error="The response could not be verified after saving. Please retry."),500
  return jsonify(message="Preference counted and verified.",recorded=True,candidate=candidate,candidate_id=candidate_id,submitted_as=submitted_candidate_text,alias_matched=bool(candidate_id and candidate.lower()!=submitted_candidate_text.lower()))
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
 sql="SELECT MIN(v.candidate) candidate,MIN(v.candidate_id) candidate_id,count(*) votes FROM pulse_votes v WHERE v.county=? AND v.race=?";args=[county,race]
 if race in {"Member of Parliament","MCA"}:sql+=" AND v.constituency=?";args.append(constituency)
 if race=="MCA":sql+=" AND v.ward=?";args.append(ward)
 sql+=" GROUP BY LOWER(v.candidate) ORDER BY votes DESC,candidate"
 with conn() as c:
  rows=c.execute(sql,args).fetchall()
  total=sum(x["votes"] for x in rows)
  out=[]
  for x in rows[:3]:
   photo=None
   if x["candidate_id"]:
    p=c.execute("SELECT photo_url FROM candidates WHERE id=?",(x["candidate_id"],)).fetchone()
    photo=p["photo_url"] if p else None
   out.append({"candidate":x["candidate"],"candidate_id":x["candidate_id"],"photo_url":photo,"votes":x["votes"],"pct":round(x["votes"]*100/total,1) if total else 0})
 return jsonify(total=total,results=out,top=3)
