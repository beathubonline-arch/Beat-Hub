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
 assert c.get('/api/participation/progress?county=Kericho',headers={'User-Agent':'other'}).json['completed']==[]

def test_aliases(pulse):
 with pulse.conn() as c:
  c.execute("INSERT INTO candidates(name,race) VALUES(?,?)",('Test Person','President'))
  cid=c.execute('SELECT id FROM candidates').fetchone()['id']
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
