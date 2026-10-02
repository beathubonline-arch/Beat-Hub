import os, hashlib, re, sqlite3, base64, json, hmac
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

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenya Pulse — Live Participation</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui;background:#03130c;color:#f7fff9;min-height:100vh;overflow-x:hidden}body:before,body:after{content:"";position:fixed;border-radius:50%;filter:blur(20px);z-index:-2}body:before{width:520px;height:520px;background:#1d7b4a55;top:-180px;left:-170px}body:after{width:460px;height:460px;background:#d4a90025;right:-180px;top:35%}.mesh{position:fixed;inset:0;z-index:-3;background:radial-gradient(circle at 70% 5%,#1b6d4338,transparent 32%),linear-gradient(145deg,#020b07,#061c12 48%,#04110b)}.top{height:76px;padding:0 max(20px,5vw);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #ffffff14;position:sticky;top:0;background:#071a1199;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);z-index:10}.brand{font-weight:950;font-size:21px;letter-spacing:-.8px}.brand b{color:#ffd54a}.live{display:flex;align-items:center;gap:8px;font-size:12px;color:#d9eee1}.dot{width:8px;height:8px;background:#6cff9a;border-radius:50%;box-shadow:0 0 16px #6cff9a}.wrap{max-width:1180px;margin:auto;padding:46px 20px 70px}.hero{display:grid;grid-template-columns:1.25fr .75fr;gap:28px;align-items:end;padding:28px 0 30px}.eyebrow,.pill{display:inline-flex;padding:7px 11px;border:1px solid #ffffff1f;background:#ffffff0c;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.7px;text-transform:uppercase}.hero h1{font-size:clamp(46px,7vw,82px);line-height:.94;letter-spacing:-4px;margin:15px 0 20px}.hero h1 span{color:#ffd54a}.hero p{font-size:17px;max-width:650px}.muted{color:#a9c6b4}.glass{background:linear-gradient(135deg,#ffffff12,#ffffff07);border:1px solid #ffffff1b;box-shadow:0 24px 80px #0000002e,inset 0 1px #ffffff13;backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);border-radius:26px}.heroStat{padding:22px}.heroStat .big{font-size:46px;font-weight:950;letter-spacing:-2px}.heroStat small{color:#9bb7a6}.layout{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}.card{padding:24px}.card h2{margin:0 0 7px;font-size:20px}.cardHead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:18px}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}select,input,button{width:100%;padding:15px 16px;border-radius:14px;border:1px solid #ffffff1d;background:#06180f99;color:#fff;font:inherit;outline:none}select:focus,input:focus{border-color:#ffd54a88;box-shadow:0 0 0 3px #ffd54a12}button{background:linear-gradient(135deg,#ffe06b,#f5c728);color:#152016;font-weight:900;border:0;cursor:pointer;transition:.2s}button:hover{transform:translateY(-1px);filter:brightness(1.04)}.secondary{background:#ffffff0b;color:#fff;border:1px solid #ffffff1c}.row{padding:14px 0;border-bottom:1px solid #ffffff12}.row:last-child{border:0}.bar{height:7px;background:#ffffff10;border-radius:99px;overflow:hidden;margin-top:8px}.fill{height:100%;background:linear-gradient(90deg,#ffd54a,#70e596);border-radius:99px}.analytics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.metric{padding:18px}.metric strong{display:block;font-size:27px;letter-spacing:-1px}.metric span{font-size:12px;color:#9eb9a8}.share{margin-top:18px}.notice{font-size:12px;line-height:1.6;padding:18px;margin-top:18px;color:#b7cdbf}.adwrap{margin-top:18px;padding:10px}.ad{min-height:132px;border:1px dashed #ffffff30;border-radius:20px;display:flex;align-items:center;justify-content:center;text-align:center;background:#ffffff05;padding:20px}.ad b{display:block;color:#e9f5ed;margin-bottom:5px}.ad small{color:#89a595}.adlabel{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#769180;margin:5px 8px 10px}.footer{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;padding:24px 4px;color:#7f9d8b;font-size:12px}.footer a{color:#b9d2c2;text-decoration:none}@media(max-width:800px){.hero,.layout{grid-template-columns:1fr}.hero h1{letter-spacing:-2.5px}.heroStat{display:none}.analytics{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.wrap{padding:28px 14px 50px}.top{height:66px}.formgrid{grid-template-columns:1fr}.analytics{gap:7px}.metric{padding:13px 10px}.metric strong{font-size:22px}.card{padding:18px}.glass{border-radius:21px}.hero{padding-top:15px}.hero h1{font-size:49px}}
</style></head><body><div class=mesh></div><header class=top><div class=brand>KENYA <b>PULSE</b></div><div class=live><i class=dot></i> LIVE PARTICIPATION</div></header><main class=wrap>
<section class=hero><div><span class=eyebrow>47 counties · voluntary participation</span><h1>Your county.<br><span>Your voice.</span></h1><p class=muted>Share your current preference and explore live aggregate responses from people participating on Kenya Pulse. This is an open online pulse, not a scientific election forecast.</p></div><aside class="glass heroStat"><small>COUNTIES AVAILABLE</small><div class=big>47</div><small>One transparent participation experience across Kenya.</small></aside></section>
<div class=analytics><div class="glass metric"><strong id=metricTotal>—</strong><span>Selected race responses</span></div><div class="glass metric"><strong>47</strong><span>Counties available</span></div><div class="glass metric"><strong>LIVE</strong><span>Aggregate updates</span></div></div>
<section class=layout><div class="glass card"><div class=cardHead><div><h2>Join the pulse</h2><span class=muted>Choose your county and race</span></div><span class=pill>Private choice</span></div><div class=formgrid><select id=county><option value="">Choose county</option>{% for c in counties %}<option>{{c}}</option>{% endfor %}</select><select id=race>{% for r in races %}<option>{{r}}</option>{% endfor %}</select></div><input id=candidate maxlength=80 placeholder="Preferred candidate name" style="margin-top:10px"><input id=issue maxlength=120 placeholder="Optional: issue influencing your choice" style="margin-top:10px"><button onclick=vote() style="margin-top:10px">Submit preference →</button><div id=msg class=muted style="margin-top:10px;font-size:13px"></div></div>
<div class="glass card"><div class=cardHead><div><h2 id=rt>Live participant results</h2><span class=muted>Voluntary website responses</span></div><span class=pill>Live</span></div><div id=results><p class=muted>Select a county to explore aggregate participant results.</p></div></div></section>
<section id=sharebox class="glass card share" style="display:none"><div class=cardHead><div><h2>Share your county pulse</h2><span class=muted>Your response is counted whether or not you share.</span></div><span class=pill>Optional</span></div><div class=formgrid><button onclick=sharePulse()>Share county pulse</button><button class=secondary onclick=copyPulse()>Copy county link</button></div><div id=sharemsg class=muted style="margin-top:9px;font-size:12px"></div></section>
<section class="glass adwrap"><div class=adlabel>Advertisement</div><div class=ad><div><b>Premium advertising space</b><small>Sponsored content will appear here, clearly separated from participation controls and results.</small></div></div></section>
<section class="glass notice"><b>Transparency:</b> Results show voluntary Kenya Pulse participants and are not representative of all registered voters. They should not be interpreted as an election forecast. Individual choices are not publicly displayed. Candidate names are participant-entered and their appearance is not an endorsement. <a href="/methodology" style="color:#ffd54a">Read methodology →</a></section>
<footer class=footer><span>© Kenya Pulse · Open participation dashboard</span><span><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/methodology">Methodology</a></span></footer></main>
<script>
const C=document.getElementById('county'),R=document.getElementById('race');C.onchange=()=>{syncUrl();load()};R.onchange=load;const initialCounty={{ initial_county|tojson }};if(initialCounty){C.value=initialCounty;document.getElementById('sharebox').style.display='block';load()}
function slugCounty(v){return v.toLowerCase().replace(/&/g,'and').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}function syncUrl(){if(C.value){history.replaceState({},'', '/county/'+slugCounty(C.value)+(location.search||''));document.getElementById('sharebox').style.display='block'}}
function pulseUrl(){let u=new URL(location.href);u.searchParams.set('src','share');return u.toString()}async function sharePulse(){let text='Take part in the '+C.value+' county pulse and see aggregate participant results live. Open online pulse — not a scientific election forecast.';if(navigator.share){await navigator.share({title:'Kenya Pulse • '+C.value,text,url:pulseUrl()})}else{await navigator.clipboard.writeText(text+' '+pulseUrl());document.getElementById('sharemsg').textContent='Share text copied.'}}async function copyPulse(){await navigator.clipboard.writeText(pulseUrl());document.getElementById('sharemsg').textContent='County link copied.'}
async function vote(){let candidate=document.getElementById('candidate').value.trim(),issue=document.getElementById('issue').value.trim(),msg=document.getElementById('msg');if(!C.value||candidate.length<2){msg.textContent='Choose a county and enter a candidate name.';return}let x=await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({county:C.value,race:R.value,candidate,issue})});let j=await x.json();msg.textContent=j.message||j.error;if(x.ok){document.getElementById('candidate').value='';load()}}
async function load(){if(!C.value)return;let x=await fetch('/api/results?county='+encodeURIComponent(C.value)+'&race='+encodeURIComponent(R.value)),j=await x.json();document.getElementById('metricTotal').textContent=j.total;document.getElementById('rt').textContent=C.value+' · '+R.value;let h='';for(let a of j.results){h+='<div class=row><b>'+esc(a.candidate)+'</b><span style="float:right">'+a.votes+' · '+a.pct+'%</span><div class=bar><div class=fill style="width:'+a.pct+'%"></div></div></div>'}document.getElementById('results').innerHTML=h||'<p class=muted>No responses yet for this county and race.</p>'}function esc(s){return s.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
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
 traffic=[dict(x) for x in rows]; vm={x["county"]:x["n"] for x in votes}
 cards=[]
 for county in COUNTIES:
  v=sum(x["visits"] for x in traffic if x["county"]==county); n=vm.get(county,0)
  cards.append({"county":county,"visits":v,"responses":n,"conversion":round(n*100/v,1) if v else 0})
 cards.sort(key=lambda x:(-x["responses"],-x["visits"],x["county"]))
 totalv=sum(x["visits"] for x in traffic); totalr=sum(vm.values()); rate=round(totalr*100/totalv,1) if totalv else 0
 html="""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Kenya Pulse • Immersive Analytics</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;overflow-x:hidden;color:#f7fff9;font-family:Inter,ui-sans-serif,system-ui;background:#020705}.aurora{position:fixed;inset:-30%;z-index:-3;background:radial-gradient(circle at 18% 30%,rgba(22,220,128,.24),transparent 27%),radial-gradient(circle at 80% 18%,rgba(255,206,54,.19),transparent 25%),radial-gradient(circle at 60% 78%,rgba(0,130,255,.13),transparent 24%);filter:blur(28px);animation:drift 16s ease-in-out infinite alternate}.gridbg{position:fixed;inset:0;z-index:-2;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:55px 55px;mask-image:linear-gradient(to bottom,black,transparent 90%);transform:perspective(700px) rotateX(62deg) scale(1.8) translateY(15%);transform-origin:center bottom}.orb{position:fixed;border-radius:50%;filter:blur(1px);opacity:.5;z-index:-1;animation:float 9s ease-in-out infinite}.o1{width:170px;height:170px;left:4%;top:18%;background:radial-gradient(circle at 30% 30%,#63ffc0,#063b28 65%,transparent 68%)}.o2{width:110px;height:110px;right:7%;top:42%;background:radial-gradient(circle at 30% 30%,#ffe77a,#725900 65%,transparent 68%);animation-delay:-4s}.w{max-width:1240px;margin:auto;padding:28px 18px 90px;perspective:1200px}.glass{position:relative;background:linear-gradient(135deg,rgba(255,255,255,.105),rgba(255,255,255,.035));border:1px solid rgba(255,255,255,.16);box-shadow:inset 0 1px rgba(255,255,255,.16),0 28px 70px rgba(0,0,0,.38);backdrop-filter:blur(24px) saturate(135%);-webkit-backdrop-filter:blur(24px) saturate(135%);border-radius:28px;overflow:hidden}.glass:before{content:"";position:absolute;inset:0;background:linear-gradient(115deg,transparent 25%,rgba(255,255,255,.08) 45%,transparent 62%);transform:translateX(-120%);animation:shine 8s infinite}.hero{min-height:340px;padding:42px;display:flex;align-items:flex-end;transform-style:preserve-3d}.hero h1{font-size:clamp(44px,8vw,88px);line-height:.9;letter-spacing:-.065em;margin:16px 0;background:linear-gradient(100deg,#fff,#b9ffdb 48%,#ffe16c);-webkit-background-clip:text;color:transparent}.hero p{max-width:680px;font-size:16px;color:#b7c8bd}.tag{display:inline-flex;gap:8px;align-items:center;font-size:10px;letter-spacing:.18em;border:1px solid rgba(255,255,255,.24);border-radius:999px;padding:8px 11px;background:rgba(0,0,0,.18)}.live{width:7px;height:7px;border-radius:50%;background:#53f5a5;box-shadow:0 0 16px #53f5a5;animation:pulse 1.7s infinite}.stats,.countygrid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:14px}.stat,.county{padding:22px;min-height:155px;transform-style:preserve-3d;transition:transform .18s ease,border-color .2s}.stat:hover,.county:hover{border-color:rgba(111,255,184,.5)}.label{color:#92aa9b;font-size:12px;text-transform:uppercase;letter-spacing:.08em}.num{font-size:clamp(32px,4vw,52px);font-weight:900;letter-spacing:-.05em;margin-top:10px;color:#fff;text-shadow:0 0 28px rgba(90,255,174,.12)}.accent{color:#ffe16c}.sectionhead{display:flex;align-items:end;justify-content:space-between;margin:42px 4px 14px}.sectionhead h2{font-size:30px;margin:0}.sectionhead span{color:#809589;font-size:12px}.countygrid{grid-template-columns:repeat(3,1fr)}.county{min-height:190px}.county .num{font-size:36px}.meter{height:7px;margin:18px 0;background:rgba(255,255,255,.07);border-radius:99px;overflow:hidden}.meter i{height:100%;display:block;border-radius:99px;background:linear-gradient(90deg,#4ef3a2,#ffe169);box-shadow:0 0 18px rgba(80,245,165,.4)}a{color:#baffd8;text-decoration:none;font-size:13px}.ad{margin-top:18px;min-height:230px;padding:28px;display:grid;grid-template-columns:1fr auto;align-items:center}.adstage{width:180px;height:140px;perspective:700px}.adcube{width:120px;height:120px;margin:auto;position:relative;transform-style:preserve-3d;animation:spin 14s linear infinite}.face{position:absolute;width:120px;height:120px;border:1px solid rgba(255,225,105,.35);background:rgba(255,225,105,.07);backdrop-filter:blur(8px);display:grid;place-items:center;font-size:11px;letter-spacing:.16em;color:#ffe169}.f1{transform:translateZ(60px)}.f2{transform:rotateY(90deg) translateZ(60px)}.f3{transform:rotateY(180deg) translateZ(60px)}.f4{transform:rotateY(-90deg) translateZ(60px)}.tablewrap{padding:12px 18px;overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:14px;border-bottom:1px solid rgba(255,255,255,.08);font-size:13px}th{color:#809589}.foot{text-align:center;color:#60766a;margin-top:34px;font-size:12px}
@keyframes drift{to{transform:translate3d(4%,-3%,0) rotate(4deg)}}@keyframes float{50%{transform:translateY(-28px) scale(1.08)}}@keyframes shine{55%,100%{transform:translateX(130%)}}@keyframes pulse{50%{opacity:.35;transform:scale(.7)}}@keyframes spin{to{transform:rotateX(360deg) rotateY(360deg)}}@media(max-width:900px){.stats{grid-template-columns:repeat(2,1fr)}.countygrid{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.w{padding:12px}.hero{min-height:300px;padding:24px}.countygrid{grid-template-columns:1fr}.ad{grid-template-columns:1fr}.adstage{display:none}.sectionhead{align-items:start;flex-direction:column;gap:6px}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body><div class=aurora></div><div class=gridbg></div><div class="orb o1"></div><div class="orb o2"></div><main class=w><section class="glass hero tilt"><div><span class=tag><i class=live></i> LIVE PARTICIPATION ANALYTICS</span><h1>Kenya<br>Pulse.</h1><p>A real-time view of voluntary participation across Kenya's 47 counties. Explore engagement seamlessly in one immersive space.</p></div></section><section class=stats><article class="glass stat tilt"><div class=label>Total visits</div><div class="num count" data-n="{{totalv}}">0</div></article><article class="glass stat tilt"><div class=label>Responses</div><div class="num count" data-n="{{totalr}}">0</div></article><article class="glass stat tilt"><div class=label>Visit / response</div><div class="num accent">{{rate}}%</div></article><article class="glass stat tilt"><div class=label>County coverage</div><div class="num accent">47</div></article></section><section class="glass ad tilt"><div><span class=tag>ADVERTISEMENT</span><h2>Premium sponsored space</h2><p class=label>Commercial content stays visually and functionally separate from poll choices and results.</p></div><div class=adstage><div class=adcube><div class="face f1">SPONSOR</div><div class="face f2">YOUR BRAND</div><div class="face f3">AD SPACE</div><div class="face f4">FEATURED</div></div></div></section><div class=sectionhead><h2>County analytics</h2><span>VOLUNTARY PLATFORM PARTICIPATION</span></div><section class=countygrid>{% for x in cards %}<article class="glass county tilt"><div class=label>{{x.county}}</div><div class="num count" data-n="{{x.responses}}">0</div><div class=label>{{x.visits}} visits · {{x.conversion}}% ratio</div><div class=meter><i style="width:{{[x.conversion,100]|min}}%"></i></div><a href="/county/{{slug(x.county)}}?src=facebook_{{slug(x.county)}}">Explore county →</a></article>{% endfor %}</section><div class=sectionhead><h2>Traffic intelligence</h2><span>SOURCE ATTRIBUTION</span></div><section class="glass tablewrap"><table><tr><th>COUNTY</th><th>SOURCE</th><th>VISITS</th></tr>{% for x in traffic %}<tr><td>{{x.county}}</td><td>{{x.source}}</td><td>{{x.visits}}</td></tr>{% endfor %}</table></section><p class=foot>Open online pulse • Participation analytics are not a scientific election forecast.</p></main><script>
document.querySelectorAll('.tilt').forEach(el=>{el.addEventListener('pointermove',e=>{if(innerWidth<700)return;let r=el.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;el.style.transform='perspective(900px) rotateX('+(-y*5)+'deg) rotateY('+(x*7)+'deg) translateZ(6px)'});el.addEventListener('pointerleave',()=>el.style.transform='')});
const io=new IntersectionObserver(es=>es.forEach(e=>{if(!e.isIntersecting)return;let el=e.target,n=+el.dataset.n,t=0,d=700,st=performance.now();function a(now){t=Math.min(1,(now-st)/d);el.textContent=Math.floor(n*(1-Math.pow(1-t,3))).toLocaleString();if(t<1)requestAnimationFrame(a)}requestAnimationFrame(a);io.unobserve(el)}));document.querySelectorAll('.count').forEach(x=>io.observe(x));
</script></body></html>"""
 return render_template_string(html,cards=cards,traffic=traffic,totalv=totalv,totalr=totalr,rate=rate,slug=lambda s:re.sub(r"[^a-z0-9]+","-",s.lower()).strip("-"))

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
 return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} — Kenya Pulse</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#06140e;color:#f5fff8;font-family:Inter,system-ui,sans-serif;line-height:1.65}}header{{border-bottom:1px solid #21432f;background:#081a12}}nav,main,footer{{max-width:920px;margin:auto;padding:20px}}nav{{display:flex;align-items:center;justify-content:space-between}}.brand{{font-weight:950;font-size:22px;letter-spacing:-.5px}}.brand b,.eyebrow,a{{color:#ffd447}}nav a{{text-decoration:none;color:#d7eadf}}main{{padding-top:64px;padding-bottom:70px}}.eyebrow{{font-weight:850;text-transform:uppercase;letter-spacing:1.5px;font-size:12px}}h1{{font-size:clamp(42px,7vw,70px);line-height:1;margin:10px 0 16px;letter-spacing:-2px}}.lead{{font-size:18px;color:#abc8b5;max-width:680px;margin-bottom:38px}}section{{background:linear-gradient(145deg,#0d2318,#0a1b13);border:1px solid #21432f;border-radius:20px;padding:24px;margin:14px 0}}h2{{font-size:19px;margin:0 0 8px}}p{{margin:0;color:#c8ddd0}}.links{{display:flex;gap:16px;flex-wrap:wrap;margin-top:30px}}footer{{border-top:1px solid #21432f;color:#87a493;font-size:13px;padding-top:28px;padding-bottom:40px}}@media(max-width:600px){{main{{padding-top:38px}}nav{{padding:16px 20px}}}}</style></head><body><header><nav><div class='brand'>KENYA <b>PULSE</b></div><a href='/'>← Back to Pulse</a></nav></header><main><div class='eyebrow'>Transparent participation</div><h1>{title}</h1><p class='lead'>{kicker}. Clear rules, privacy-minded participation and transparent public information.</p>{cards}<div class='links'><a href='/privacy'>Privacy Policy</a><a href='/terms'>Terms of Service</a><a href='/data-deletion'>Data Deletion</a><a href='/methodology'>Methodology</a></div></main><footer>KENYA PULSE · Your county. Your voice. · Open voluntary participation, not an election forecast.</footer></body></html>"""

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
