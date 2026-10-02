import os, hashlib, re, sqlite3
from flask import Flask, request, jsonify, render_template_string
app=Flask(__name__)
DB=os.environ.get("PULSE_DB","/tmp/kenya-pulse.db")
SALT=os.environ.get("PULSE_SALT","kenya-pulse")
COUNTIES=["Mombasa","Kwale","Kilifi","Tana River","Lamu","Taita-Taveta","Garissa","Wajir","Mandera","Marsabit","Isiolo","Meru","Tharaka-Nithi","Embu","Kitui","Machakos","Makueni","Nyandarua","Nyeri","Kirinyaga","Murang'a","Kiambu","Turkana","West Pokot","Samburu","Trans Nzoia","Uasin Gishu","Elgeyo-Marakwet","Nandi","Baringo","Laikipia","Nakuru","Narok","Kajiado","Kericho","Bomet","Kakamega","Vihiga","Bungoma","Busia","Siaya","Kisumu","Homa Bay","Migori","Kisii","Nyamira","Nairobi City"]
RACES=["President","Governor","Senator","Woman Representative","Member of Parliament","MCA"]

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
    with conn() as c:
       c.execute("""CREATE TABLE IF NOT EXISTS pulse_votes(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT NOT NULL,race TEXT NOT NULL,candidate TEXT NOT NULL,issue TEXT,fp TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(county,race,fp));""")
       c.execute("CREATE INDEX IF NOT EXISTS pulse_lookup ON pulse_votes(county,race);")
       c.execute("""CREATE TABLE IF NOT EXISTS pulse_visits(id INTEGER PRIMARY KEY AUTOINCREMENT,county TEXT,source TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
       c.execute("CREATE INDEX IF NOT EXISTS pulse_visit_lookup ON pulse_visits(county,source);")
try:init()
except Exception as e: print("db init",e)

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenya Pulse — 47 Counties</title><style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui;background:#07150f;color:#f4fff8}.top{padding:18px 5%;display:flex;justify-content:space-between;border-bottom:1px solid #21432f;position:sticky;top:0;background:#07150fee;backdrop-filter:blur(12px)}.brand{font-weight:900;font-size:22px}.brand b{color:#ffd447}.wrap{max-width:1050px;margin:auto;padding:42px 20px}.hero{padding:28px 0}.hero h1{font-size:clamp(38px,7vw,72px);line-height:.98;margin:0 0 18px}.hero span{color:#ffd447}.muted{color:#a8c7b4}.card{background:#0d2117;border:1px solid #21432f;border-radius:22px;padding:22px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}select,input,button{width:100%;padding:15px;border-radius:12px;border:1px solid #315942;background:#091810;color:white;font-size:16px}button{background:#ffd447;color:#132017;font-weight:900;border:0;cursor:pointer}.results{margin-top:18px}.row{padding:13px 0;border-bottom:1px solid #1c3b29}.bar{height:8px;background:#173522;border-radius:10px;overflow:hidden;margin-top:7px}.fill{height:100%;background:#ffd447}.pill{display:inline-block;padding:7px 11px;border:1px solid #315942;border-radius:999px;margin:4px;color:#cce8d6}.notice{font-size:13px;line-height:1.5;background:#10271b;padding:14px;border-radius:12px}.ad{margin-top:25px;border:1px dashed #577c65;padding:18px;border-radius:16px;text-align:center;color:#a8c7b4}@media(max-width:650px){.grid{grid-template-columns:1fr}}</style></head>
<body><div class=top><div class=brand>KENYA <b>PULSE</b></div><div class=muted>47 Counties • Live</div></div><main class=wrap><section class=hero><div class=pill>Independent participation dashboard</div><h1>What are people in your <span>county</span> saying?</h1><p class=muted>Submit your current preference, then see aggregated participant results update live. This is an open online pulse, not a scientific election forecast.</p></section>
<div class=card><h2>Join your county pulse</h2><div class=grid><select id=county><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><select id=race>{% for r in races %}<option>{{r}}</option>{% endfor %}</select></div><input id=candidate maxlength=80 placeholder="Type your preferred candidate's name" style="margin-top:12px"><input id=issue maxlength=120 placeholder="Optional: biggest issue influencing you (jobs, prices, roads…)" style="margin-top:12px"><button onclick=vote() style="margin-top:12px">Submit preference & see live results</button><div id=msg class=muted style="margin-top:10px"></div></div>
<div class=card id=sharebox style="display:none"><h2>Stay with your county pulse 🇰🇪</h2><p class=muted>Your response is counted whether or not you follow or share. Follow Kenya Pulse on Facebook for future county updates, or invite others to take part.</p><div class=grid><button onclick="followPulse()">Follow Kenya Pulse on Facebook</button><button onclick="sharePulse()">Share county pulse</button></div><button onclick="copyPulse()" style="margin-top:12px;background:#173522;color:#fff">Copy county link</button><div id=sharemsg class=muted style="margin-top:10px"></div></div>
<div class=card><h2 id=rt>Live participant results</h2><div id=results class=results><p class=muted>Select a county to load results.</p></div></div>
<div class=notice><b>Transparency:</b> Results represent people who voluntarily participated on this website and should not be interpreted as representative of all registered voters. Individual choices are not displayed publicly. Duplicate submissions are restricted using a one-way technical fingerprint. Candidate names are participant-entered and are not endorsements. <a href="/methodology" style="color:#ffd447">Methodology</a>.</div>
<div class=ad>Reserved advertising space — kept separate from poll choices and results.</div></main>
<script>
const C=document.getElementById('county'),R=document.getElementById('race'); C.onchange=()=>{syncUrl();load()};R.onchange=load;
const initialCounty={{ initial_county|tojson }}; if(initialCounty){C.value=initialCounty;document.getElementById('sharebox').style.display='block';load()}
function slugCounty(v){return v.toLowerCase().replace(/&/g,'and').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
function syncUrl(){if(C.value){history.replaceState({},'', '/county/'+slugCounty(C.value)+(location.search||''));document.getElementById('sharebox').style.display='block'}}
function followPulse(){window.open('https://www.facebook.com/profile.php?id=61595125615495','_blank','noopener');}
function pulseUrl(){let u=new URL(location.href);u.searchParams.set('src','share');return u.toString()}
async function sharePulse(){let title='Kenya Pulse • '+C.value,text='Take part in the '+C.value+' county pulse and see aggregate participant results live. Open online pulse — not a scientific election forecast.';if(navigator.share){await navigator.share({title,text,url:pulseUrl()})}else{await navigator.clipboard.writeText(text+' '+pulseUrl());document.getElementById('sharemsg').textContent='Share text copied.'}}
async function copyPulse(){await navigator.clipboard.writeText(pulseUrl());document.getElementById('sharemsg').textContent='County link copied.'}
async function vote(){let candidate=document.getElementById('candidate').value.trim(),issue=document.getElementById('issue').value.trim(),msg=document.getElementById('msg');if(!C.value||candidate.length<2){msg.textContent='Choose a county and enter a candidate name.';return}let x=await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({county:C.value,race:R.value,candidate,issue})});let j=await x.json();msg.textContent=j.message||j.error;if(x.ok){document.getElementById('candidate').value='';load()}}
async function load(){if(!C.value)return;let x=await fetch('/api/results?county='+encodeURIComponent(C.value)+'&race='+encodeURIComponent(R.value)),j=await x.json();document.getElementById('rt').textContent=C.value+' • '+R.value+' • '+j.total+' participants';let h='';for(let a of j.results){h+='<div class=row><b>'+esc(a.candidate)+'</b><span style="float:right">'+a.votes+' • '+a.pct+'%</span><div class=bar><div class=fill style="width:'+a.pct+'%"></div></div></div>'}document.getElementById('results').innerHTML=h||'<p class=muted>No responses yet. You can be the first participant in this county race.</p>'}
function esc(s){return s.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
</script></body></html>'''

@app.get("/")
def home():
 src=re.sub(r"[^a-zA-Z0-9_-]","",request.args.get("src","direct"))[:60]
 with conn() as c:c.execute("INSERT INTO pulse_visits(county,source) VALUES(?,?)",(None,src))
 return render_template_string(HTML,counties=COUNTIES,races=RACES,initial_county=None)

@app.get("/county/<slug>")
def county_page(slug):
 county=next((x for x in COUNTIES if re.sub(r"[^a-z0-9]+","-",x.lower()).strip("-")==slug.lower()),None)
 if not county:return "County not found",404
 src=re.sub(r"[^a-zA-Z0-9_-]","",request.args.get("src","direct"))[:60]
 with conn() as c:c.execute("INSERT INTO pulse_visits(county,source) VALUES(?,?)",(county,src))
 return render_template_string(HTML,counties=COUNTIES,races=RACES,initial_county=county)

@app.get("/growth")
def growth():
 with conn() as c:
  rows=c.execute("""SELECT COALESCE(county,'Homepage') county,source,count(*) visits FROM pulse_visits GROUP BY county,source ORDER BY visits DESC LIMIT 150""").fetchall()
  votes=c.execute("SELECT county,count(*) n FROM pulse_votes GROUP BY county ORDER BY n DESC").fetchall()
 vote_map={x["county"]:x["n"] for x in votes}
 traffic=[dict(x) for x in rows]
 cards=[]
 for county in COUNTIES:
  v=sum(x["visits"] for x in traffic if x["county"]==county)
  n=vote_map.get(county,0)
  cards.append({"county":county,"visits":v,"responses":n,"conversion":round(n*100/v,1) if v else 0})
 cards.sort(key=lambda x:(-x["responses"],-x["visits"],x["county"]))
 html="""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Kenya Pulse Growth</title><style>*{box-sizing:border-box}body{margin:0;background:#07150f;color:#f4fff8;font-family:system-ui}.w{max-width:1100px;margin:auto;padding:30px 18px}h1{font-size:42px}.muted{color:#9db9a7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.c{background:#0d2117;border:1px solid #21432f;border-radius:18px;padding:18px}.n{font-size:28px;font-weight:900;color:#ffd447}.tag{font-size:12px;border:1px solid #315942;border-radius:20px;padding:5px 8px}table{width:100%;border-collapse:collapse;margin-top:24px;background:#0d2117;border-radius:16px;overflow:hidden}th,td{text-align:left;padding:12px;border-bottom:1px solid #21432f}a{color:#ffd447}@media(max-width:600px){h1{font-size:34px}table{font-size:12px}}</style></head><body><main class=w><span class=tag>INTERNAL DISTRIBUTION VIEW</span><h1>47-County Growth Dashboard</h1><p class=muted>Track visits and completed responses by county. Source tags identify distribution channels; they never change how a response is counted.</p><div class=grid>{% for x in cards %}<div class=c><b>{{x.county}}</b><div class=n>{{x.responses}}</div><div class=muted>responses • {{x.visits}} visits • {{x.conversion}}% visit/response ratio</div><p><a href="/county/{{slug(x.county)}}?src=facebook_{{slug(x.county)}}">Open tracked county link →</a></p></div>{% endfor %}</div><h2>Traffic sources</h2><table><tr><th>County</th><th>Source tag</th><th>Visits</th></tr>{% for x in traffic %}<tr><td>{{x.county}}</td><td>{{x.source}}</td><td>{{x.visits}}</td></tr>{% endfor %}</table></main></body></html>"""
 return render_template_string(html,cards=cards,traffic=traffic,slug=lambda s:re.sub(r"[^a-z0-9]+","-",s.lower()).strip("-"))

@app.get("/health")
def health(): return {"ok":True,"counties":47}
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

@app.get("/data-deletion")
def data_deletion():
 return legal_page("Data Deletion","Request removal of information associated with your Kenya Pulse use",[
 ("1. Send your request","Email kenyapulse2026@gmail.com with the subject: Kenya Pulse Data Deletion Request."),
 ("2. Identify the connection","Provide enough information for us to identify the relevant account or connection. Never send passwords, access tokens or other secrets."),
 ("3. Processing","Valid requests will be reviewed and processed subject to applicable legal, security and record-retention requirements.")
 ])

def legal_page(title,kicker,sections):
 cards="".join(f"<section><h2>{h}</h2><p>{p}</p></section>" for h,p in sections)
 return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} — Kenya Pulse</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui,sans-serif;line-height:1.65}}header{{border-bottom:1px solid #21432f;background:#081a12}}nav,main,footer{{max-width:920px;margin:auto;padding:20px}}nav{{display:flex;align-items:center;justify-content:space-between}}.brand{{font-weight:950;font-size:22px;letter-spacing:-.5px}}.brand b,.eyebrow,a{{color:#ffd447}}nav a{{text-decoration:none;color:#d7eadf}}main{{padding-top:64px;padding-bottom:70px}}.eyebrow{{font-weight:850;text-transform:uppercase;letter-spacing:1.5px;font-size:12px}}h1{{font-size:clamp(42px,7vw,70px);line-height:1;margin:10px 0 16px;letter-spacing:-2px}}.lead{{font-size:18px;color:#abc8b5;max-width:680px;margin-bottom:38px}}section{{background:linear-gradient(145deg,#0d2318,#0a1b13);border:1px solid #21432f;border-radius:20px;padding:24px;margin:14px 0}}h2{{font-size:19px;margin:0 0 8px}}p{{margin:0;color:#c8ddd0}}.links{{display:flex;gap:16px;flex-wrap:wrap;margin-top:30px}}footer{{border-top:1px solid #21432f;color:#87a493;font-size:13px;padding-top:28px;padding-bottom:40px}}@media(max-width:600px){{main{{padding-top:38px}}nav{{padding:16px 20px}}}}</style></head><body><header><nav><div class='brand'>KENYA <b>PULSE</b></div><a href='/'>← Back to Pulse</a></nav></header><main><div class='eyebrow'>Transparent participation</div><h1>{title}</h1><p class='lead'>{kicker}. Clear rules, privacy-minded participation and transparent public information.</p>{cards}<div class='links'><a href='/privacy'>Privacy Policy</a><a href='/terms'>Terms of Service</a><a href='/data-deletion'>Data Deletion</a><a href='/methodology'>Methodology</a></div></main><footer>KENYA PULSE · Your county. Your voice. · Open voluntary participation, not an election forecast.</footer></body></html>"""

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
   c.execute("INSERT INTO pulse_votes(county,race,candidate,issue,fp) VALUES(?,?,?,?,?)",(county,race,candidate,issue,fp))
  return jsonify(message="Preference counted. Live results updated.")
 except sqlite3.IntegrityError:return jsonify(error="A response from this device/network is already recorded for this county and race."),409
@app.get("/api/results")
def results():
 county=request.args.get("county","");race=request.args.get("race","President")
 if county not in COUNTIES or race not in RACES:return jsonify(error="Invalid selection"),400
 with conn() as c:
  rows=c.execute("SELECT candidate,count(*) votes FROM pulse_votes WHERE county=? AND race=? GROUP BY candidate ORDER BY votes DESC,candidate",(county,race)).fetchall()
 total=sum(x["votes"] for x in rows)
 return jsonify(total=total,results=[{"candidate":x["candidate"],"votes":x["votes"],"pct":round(x["votes"]*100/total,1) if total else 0} for x in rows])
