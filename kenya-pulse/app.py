import os, hashlib, re, sqlite3
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app=Flask(__name__)
DB=os.environ.get("PULSE_DB","/tmp/kenya-pulse.db")
SALT=os.environ.get("PULSE_SALT","kenya-pulse")
COUNTIES=["Mombasa","Kwale","Kilifi","Tana River","Lamu","Taita-Taveta","Garissa","Wajir","Mandera","Marsabit","Isiolo","Meru","Tharaka-Nithi","Embu","Kitui","Machakos","Makueni","Nyandarua","Nyeri","Kirinyaga","Murang'a","Kiambu","Turkana","West Pokot","Samburu","Trans Nzoia","Uasin Gishu","Elgeyo-Marakwet","Nandi","Baringo","Laikipia","Nakuru","Narok","Kajiado","Kericho","Bomet","Kakamega","Vihiga","Bungoma","Busia","Siaya","Kisumu","Homa Bay","Migori","Kisii","Nyamira","Nairobi City"]
RACES=["President","Governor","Senator","Woman Representative","Member of Parliament","MCA"]

def conn(): return psycopg2.connect(DB, sslmode="require" if "render.com" in DB else "prefer")
def init():
    if not DB:return
    with conn() as c:
      with c.cursor() as q:
       q.execute("""CREATE TABLE IF NOT EXISTS pulse_votes(id BIGSERIAL PRIMARY KEY,county TEXT NOT NULL,race TEXT NOT NULL,candidate TEXT NOT NULL,issue TEXT,fp TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now(),UNIQUE(county,race,fp));""")
       q.execute("CREATE INDEX IF NOT EXISTS pulse_lookup ON pulse_votes(county,race);")
try:init()
except Exception as e: print("db init",e)

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenya Pulse — 47 Counties</title><style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui;background:#07150f;color:#f4fff8}.top{padding:18px 5%;display:flex;justify-content:space-between;border-bottom:1px solid #21432f;position:sticky;top:0;background:#07150fee;backdrop-filter:blur(12px)}.brand{font-weight:900;font-size:22px}.brand b{color:#ffd447}.wrap{max-width:1050px;margin:auto;padding:42px 20px}.hero{padding:28px 0}.hero h1{font-size:clamp(38px,7vw,72px);line-height:.98;margin:0 0 18px}.hero span{color:#ffd447}.muted{color:#a8c7b4}.card{background:#0d2117;border:1px solid #21432f;border-radius:22px;padding:22px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}select,input,button{width:100%;padding:15px;border-radius:12px;border:1px solid #315942;background:#091810;color:white;font-size:16px}button{background:#ffd447;color:#132017;font-weight:900;border:0;cursor:pointer}.results{margin-top:18px}.row{padding:13px 0;border-bottom:1px solid #1c3b29}.bar{height:8px;background:#173522;border-radius:10px;overflow:hidden;margin-top:7px}.fill{height:100%;background:#ffd447}.pill{display:inline-block;padding:7px 11px;border:1px solid #315942;border-radius:999px;margin:4px;color:#cce8d6}.notice{font-size:13px;line-height:1.5;background:#10271b;padding:14px;border-radius:12px}.ad{margin-top:25px;border:1px dashed #577c65;padding:18px;border-radius:16px;text-align:center;color:#a8c7b4}@media(max-width:650px){.grid{grid-template-columns:1fr}}</style></head>
<body><div class=top><div class=brand>KENYA <b>PULSE</b></div><div class=muted>47 Counties • Live</div></div><main class=wrap><section class=hero><div class=pill>Independent participation dashboard</div><h1>What are people in your <span>county</span> saying?</h1><p class=muted>Submit your current preference, then see aggregated participant results update live. This is an open online pulse, not a scientific election forecast.</p></section>
<div class=card><h2>Join your county pulse</h2><div class=grid><select id=county><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><select id=race>{% for r in races %}<option>{{r}}</option>{% endfor %}</select></div><input id=candidate maxlength=80 placeholder="Type your preferred candidate's name" style="margin-top:12px"><input id=issue maxlength=120 placeholder="Optional: biggest issue influencing you (jobs, prices, roads…)" style="margin-top:12px"><button onclick=vote() style="margin-top:12px">Submit preference & see live results</button><div id=msg class=muted style="margin-top:10px"></div></div>
<div class=card><h2 id=rt>Live participant results</h2><div id=results class=results><p class=muted>Select a county to load results.</p></div></div>
<div class=notice><b>Transparency:</b> Results represent people who voluntarily participated on this website and should not be interpreted as representative of all registered voters. Individual choices are not displayed publicly. Duplicate submissions are restricted using a one-way technical fingerprint. Candidate names are participant-entered and are not endorsements. <a href="/methodology" style="color:#ffd447">Methodology</a>.</div>
<div class=ad>Reserved advertising space — kept separate from poll choices and results.</div></main>
<script>
const C=document.getElementById('county'),R=document.getElementById('race'); C.onchange=load;R.onchange=load;
async function vote(){let candidate=document.getElementById('candidate').value.trim(),issue=document.getElementById('issue').value.trim(),msg=document.getElementById('msg');if(!C.value||candidate.length<2){msg.textContent='Choose a county and enter a candidate name.';return}let x=await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({county:C.value,race:R.value,candidate,issue})});let j=await x.json();msg.textContent=j.message||j.error;if(x.ok){document.getElementById('candidate').value='';load()}}
async function load(){if(!C.value)return;let x=await fetch('/api/results?county='+encodeURIComponent(C.value)+'&race='+encodeURIComponent(R.value)),j=await x.json();document.getElementById('rt').textContent=C.value+' • '+R.value+' • '+j.total+' participants';let h='';for(let a of j.results){h+='<div class=row><b>'+esc(a.candidate)+'</b><span style="float:right">'+a.votes+' • '+a.pct+'%</span><div class=bar><div class=fill style="width:'+a.pct+'%"></div></div></div>'}document.getElementById('results').innerHTML=h||'<p class=muted>No responses yet. You can be the first participant in this county race.</p>'}
function esc(s){return s.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
</script></body></html>'''

@app.get("/")
def home(): return render_template_string(HTML,counties=COUNTIES,races=RACES)
@app.get("/health")
def health(): return {"ok":True,"counties":47}
@app.get("/methodology")
def methodology():
 return """<body style='font-family:system-ui;max-width:760px;margin:40px auto;padding:20px'><h1>Kenya Pulse methodology</h1><p>Kenya Pulse is an open, voluntary online participation dashboard. It is not a probability sample and results are not representative of all Kenyan voters.</p><p>Participants select a county and race and enter their current preferred candidate. Public results show aggregate counts and percentages only. A one-way fingerprint derived from network/client information is used to restrict duplicate submissions for the same county and race; raw fingerprints are not shown publicly.</p><p>No weighting or normalization is applied. Undecided participants may enter “Undecided”. Candidate names are participant-entered and may be consolidated for spelling variants in future audited releases.</p><p>The platform does not endorse candidates or predict election outcomes. Advertising is separated from voting controls and does not affect whether a response is counted.</p><p><a href='/'>Back to Kenya Pulse</a></p></body>"""
@app.post("/api/vote")
def vote():
 d=request.get_json(silent=True) or {}; county=d.get("county","").strip(); race=d.get("race","").strip(); candidate=re.sub(r"\s+"," ",d.get("candidate","").strip())[:80]; issue=d.get("issue","").strip()[:120]
 if county not in COUNTIES or race not in RACES or len(candidate)<2:return jsonify(error="Invalid county, race or candidate."),400
 raw=(request.headers.get("X-Forwarded-For",request.remote_addr or "").split(",")[0]+request.headers.get("User-Agent","")+SALT).encode(); fp=hashlib.sha256(raw).hexdigest()
 try:
  with conn() as c:
   with c.cursor() as q:q.execute("INSERT INTO pulse_votes(county,race,candidate,issue,fp) VALUES(%s,%s,%s,%s,%s)",(county,race,candidate,issue,fp))
  return jsonify(message="Preference counted. Live results updated.")
 except psycopg2.errors.UniqueViolation:return jsonify(error="A response from this device/network is already recorded for this county and race."),409
@app.get("/api/results")
def results():
 county=request.args.get("county","");race=request.args.get("race","President")
 if county not in COUNTIES or race not in RACES:return jsonify(error="Invalid selection"),400
 with conn() as c:
  with c.cursor(cursor_factory=RealDictCursor) as q:
   q.execute("SELECT candidate,count(*)::int votes FROM pulse_votes WHERE county=%s AND race=%s GROUP BY candidate ORDER BY votes DESC,candidate",(county,race)); rows=q.fetchall()
 total=sum(x["votes"] for x in rows)
 return jsonify(total=total,results=[{"candidate":x["candidate"],"votes":x["votes"],"pct":round(x["votes"]*100/total,1) if total else 0} for x in rows])
