import importlib.util
import os
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(params=["sqlite", "postgres"])
def pulse(request,tmp_path,monkeypatch):
 monkeypatch.setenv('DATABASE_URL','')
 monkeypatch.setenv('PULSE_DB',str(tmp_path/'pulse.db'))
 monkeypatch.setenv('PAYSTACK_SECRET_KEY','sk_test_fixture')
 monkeypatch.setenv('PAYSTACK_MODE','test')
 pg=None
 if request.param=="postgres":
  dsn=os.environ.get('PULSE_TEST_POSTGRES_DSN')
  if not dsn:pytest.skip('Isolated PostgreSQL available in CI')
  import psycopg2,uuid
  from psycopg2.extensions import parse_dsn
  assert parse_dsn(dsn).get('host') in {'127.0.0.1','localhost'}
  original_connect=psycopg2.connect
  pg=original_connect(dsn);pg.autocommit=True
  schema='pulse_test_'+uuid.uuid4().hex
  with pg.cursor() as cur:cur.execute('CREATE SCHEMA '+schema)
  monkeypatch.setenv('DATABASE_URL',dsn+" options='-c search_path="+schema+"'")
  def test_connect(*args,**kwargs):
   kwargs['sslmode']='disable'
   return original_connect(*args,**kwargs)
  monkeypatch.setattr(psycopg2,'connect',test_connect)
 spec=importlib.util.spec_from_file_location('pulse_live',ROOT/'app.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 module.app.testing=True
 yield module
 if pg:
  with pg.cursor() as cur:cur.execute("DROP SCHEMA "+schema+" CASCADE")
  pg.close()

def submit(p,c,i):
 return c.post('/api/vote',json=dict(county='Kericho',race=p.RACES[i],candidate='Test Person '+str(i),constituency='Ainamoi' if i>=4 else '',ward='Kapsoit' if i==5 else ''))

def test_six_seats_and_reload(pulse):
 c=pulse.app.test_client()
 assert submit(pulse,c,5).status_code==409
 for i in range(6):
  r=submit(pulse,c,i);assert r.status_code==200,r.json
  assert r.json['recorded'] is True
  progress=c.get('/api/participation/progress?county=Kericho').json
  assert len(progress['completed'])==i+1
  assert progress['complete']==(i==5)
  assert c.get('/api/results?county=Kericho&race=President').json['total']==1
 assert submit(pulse,c,0).status_code==409
 pulse.init()
 assert c.get('/api/participation/progress?county=Kericho').json['complete']
 assert c.get('/api/participation/progress?county=Kericho',headers={'User-Agent':'other'}).json['complete']

def test_aliases(pulse):
 with pulse.conn() as c:
  c.execute("INSERT INTO candidates(name,race) VALUES(?,?)",('Test Person','President'))
  cid=c.execute("SELECT id FROM candidates WHERE name='Test Person'").fetchone()['id']
  c.execute('INSERT INTO candidate_aliases(candidate_id,alias,verified) VALUES(?,?,?)',(cid,'Alias',True))
 client=pulse.app.test_client()
 assert client.get('/api/candidates?race=President').json['candidates'][0]['aliases']==['Alias']
 assert client.post('/api/candidates/resolve',json={'name':'Alias','race':'President'}).json['matches'][0]['id']==cid

@pytest.mark.parametrize('email',['person@example.com','first.last+tag@example.co.ke'])
def test_payment_email_and_gate(pulse,email,monkeypatch):
 c=pulse.app.test_client()
 assert c.post('/api/support/initialize',json={'email':email,'amount':5,'county':'Kericho'}).status_code==409
 for i in range(6):assert submit(pulse,c,i).status_code==200
 monkeypatch.setattr(pulse,'paystack_request',lambda path,payload:{'data':{'authorization_url':'https://checkout.paystack.com/test'}})
 r=c.post('/api/support/initialize',json={'email':email,'amount':5,'county':'Kericho'})
 assert r.status_code==200,r.json
 monkeypatch.setattr(pulse,'PAYSTACK_SECRET_KEY','sk_live_not_real')
 assert c.post('/api/support/initialize',json={'email':email,'amount':5,'county':'Kericho'}).status_code==503

def test_invalid_email(pulse):
 c=pulse.app.test_client()
 for email in ['not an email','a b@example.com','a@example','a@example.com\n']:
  # Leading/trailing whitespace is intentionally stripped.
  if email.endswith('\n'):continue
  assert c.post('/api/support/initialize',json={'email':email,'amount':5}).status_code==400

def test_browser_email_regex(pulse):
 import subprocess
 html=pulse.app.test_client().get('/county/kericho').text
 scripts=__import__('re').findall(r'<script>(.*?)</script>',html,__import__('re').S)
 for script in scripts:
  subprocess.run(['node','--check'],input=script,text=True,check=True,capture_output=True)
 script=next(s for s in scripts if 'async function supportAmount' in s)
 regex=script.split('if(!/',1)[1].split('/.test(email)',1)[0]
 subprocess.run(['node','-e',f"if(!/{regex}/.test('person@example.com'))process.exit(1)"],check=True)


def test_alias_aggregation_audit_and_ambiguity(pulse):
 with pulse.conn() as db:
  db.execute("INSERT INTO candidates(name,race,status) VALUES('Canonical Person','President','UNKNOWN')")
  cid=db.execute("SELECT id FROM candidates WHERE name='Canonical Person'").fetchone()['id']
  db.execute('INSERT INTO candidate_aliases(candidate_id,alias,verified) VALUES(?,?,?)',(cid,'Known alias',True))
 a=pulse.app.test_client();a.get('/api/participation/progress?county=Kericho')
 r=a.post('/api/vote',json=dict(county='Kericho',race='President',candidate='  Known alias  '))
 assert r.status_code==200 and r.json['candidate_id']==cid
 with pulse.conn() as db:
  assert db.execute('SELECT submitted_candidate_text FROM pulse_votes').fetchone()[0]=='  Known alias  '
  db.execute("INSERT INTO candidates(name,race) VALUES('Another Person','President')")
  other=db.execute("SELECT id FROM candidates WHERE name='Another Person'").fetchone()[0]
  db.execute('INSERT INTO candidate_aliases(candidate_id,alias,verified) VALUES(?,?,?)',(other,'Known alias',True))
 b=pulse.app.test_client();h={'User-Agent':'different participant'}
 assert b.post('/api/vote',headers=h,json=dict(county='Kericho',race='President',candidate='Known alias')).status_code==409
 assert b.post('/api/vote',headers=h,json=dict(county='Kericho',race='President',candidate='Canonical Person')).status_code==200
 results=b.get('/api/results?county=Kericho&race=President').json
 assert results['total']==2 and results['results'][0]['votes']==2
 assert 'not representative' in results['disclosure']


def test_cookie_integrity_duplicate_and_support_after_network_change(pulse,monkeypatch):
 c=pulse.app.test_client();r=c.get('/api/participation/progress?county=Kericho')
 cookie=r.headers['Set-Cookie'];assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=Lax' in cookie
 assert pulse._valid_vote_cookie('tampered.invalid') is None
 for i in range(6):assert submit(pulse,c,i).status_code==200
 h={'User-Agent':'changed network browser header','X-Forwarded-For':'192.0.2.22'}
 assert c.post('/api/vote',headers=h,json=dict(county='Kericho',race='President',candidate='Other Person')).status_code==409
 assert c.get('/api/participation/progress?county=Kericho',headers=h).json['complete']
 monkeypatch.setattr(pulse,'paystack_request',lambda *a:{'data':{'authorization_url':'https://checkout.paystack.com/fixture'}})
 assert c.post('/api/support/initialize',headers=h,json=dict(county='Kericho',email='test@example.com',amount=5)).status_code==200
 # Unique constraints also protect direct inserts independently of the HTTP check.
 with pytest.raises(Exception):
  with pulse.conn() as db:
   db.execute("INSERT INTO pulse_votes(county,race,candidate,fp,browser_token_hash) SELECT county,race,candidate,fp,browser_token_hash FROM pulse_votes WHERE race='President'")


@pytest.mark.parametrize('race,fee',[('President',5000),('Governor',2000),('Senator',2000),('Woman Representative',2000),('Member of Parliament',1500),('MCA',500)])
def test_profile_payment_validation_and_review(pulse,monkeypatch,race,fee):
 import io
 c=pulse.app.test_client()
 def form(email='receipt@example.com'):
  return dict(name='Profile Test Person',race=race,county='Kericho',constituency='Ainamoi',ward='Kapsoit',email=email,photo=(io.BytesIO(png_fixture()),'photo.png','image/png'))
 assert c.post('/claim-profile',data=form('invalid')).status_code==400
 with pulse.conn() as db:assert db.execute('SELECT COUNT(*) FROM aspirant_profile_claims').fetchone()[0]==0
 payloads=[]
 def checkout(path,payload):
  payloads.append(payload);return {'data':{'authorization_url':'https://checkout.paystack.com/fixture'}}
 monkeypatch.setattr(pulse,'paystack_request',checkout)
 assert c.post('/claim-profile',data=form()).status_code==302
 payload=payloads[0];assert int(payload['amount'])==fee*100
 ref=payload['reference'];verified=dict(reference=ref,domain='test',status='success',currency='KES',amount=fee*100,id='tx-'+race)
 assert not pulse.verify_profile_payment(ref,dict(verified,amount=1))
 assert not pulse.verify_profile_payment(ref,dict(verified,domain='live'))
 assert pulse.verify_profile_payment(ref,verified)
 assert pulse.verify_profile_payment(ref,verified)
 with pulse.conn() as db:
  row=db.execute('SELECT * FROM aspirant_profile_claims WHERE reference=?',(ref,)).fetchone()
  assert row['verification_status']=='PENDING_REVIEW'
  assert db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0]==0
 monkeypatch.setattr(pulse,'ADMIN_KEY','fixture-admin')
 r=c.post('/api/admin/profile-claims/'+str(row['id'])+'/review',json={'decision':'APPROVE'},headers={'Authorization':'Bearer fixture-admin'})
 assert r.status_code==200
 with pulse.conn() as db:assert db.execute('SELECT status FROM candidates').fetchone()[0]=='ASPIRANT'


def test_payment_failures_and_ads(pulse,monkeypatch):
 import io
 c=pulse.app.test_client()
 def fail(*a):raise RuntimeError('simulated provider outage')
 monkeypatch.setattr(pulse,'paystack_request',fail)
 r=c.post('/claim-profile',data=dict(name='Test Person',race='President',email='test@example.com',photo=(io.BytesIO(png_fixture()),'p.png','image/png')))
 assert r.status_code==302
 assert 'Checkout could not be started' in c.get(r.location).text
 with pulse.conn() as db:
  houses={x['business'] for x in pulse._eligible_ads(db,'Kericho','visitor')}
  assert houses=={'Mkulima AI','Mizizi','OneBob','BeatHub'}
  db.execute("INSERT INTO ad_orders(business,email,scope,county,package,budget,headline,url,status,frequency_cap) VALUES('Paid local','a@example.com','County','Kericho','Paid',100,'Sponsor','https://example.com','ACTIVE',1)")
  eligible=pulse._eligible_ads(db,'Kericho','visitor');assert len(eligible)==1 and eligible[0]['business']=='Paid local'
  assert all(x['package']=='House Ad' for x in pulse._eligible_ads(db,'Nairobi City','visitor'))
  pulse._record_ad_impression(db,eligible[0],'visitor','test','Kericho')
  assert all(x['package']=='House Ad' for x in pulse._eligible_ads(db,'Kericho','visitor'))


def test_geography_pages_and_health(pulse,monkeypatch):
 assert len(pulse.GEOGRAPHY)==47
 assert sum(len(x) for x in pulse.GEOGRAPHY.values())==290
 assert sum(len(w) for c in pulse.GEOGRAPHY.values() for w in c.values())==1450
 c=pulse.app.test_client()
 for path in ['/participate','/county/kericho','/candidate-explorer','/claim-profile','/ground','/privacy','/terms','/methodology','/favicon.ico']:
  r=c.get(path);assert r.status_code==200,(path,r.status_code)
 def fail():raise RuntimeError('database unavailable')
 monkeypatch.setattr(pulse,'conn',fail)
 assert c.get('/healthz').json['ok']


def png_fixture():
 import io
 from PIL import Image
 out=io.BytesIO();Image.new('RGB',(2,2)).save(out,format='PNG');return out.getvalue()


def test_custom_select_javascript_and_readiness(pulse):
 import subprocess
 c=pulse.app.test_client()
 js=c.get('/pulse95.js')
 assert js.status_code==200
 subprocess.run(['node','--check'],input=js.text,text=True,check=True,capture_output=True)
 ready=c.get('/api/readiness')
 assert ready.status_code==200
 payload=ready.json
 assert payload['database']=='ok'
 assert payload['geography']=={'counties':47,'constituencies':290,'wards':1450}
 assert payload['paystack']['configured'] is True
 assert payload['paystack']['mode']=='test'
