"""Independent HTTP journey and app-restart persistence test; LOCAL PostgreSQL only."""
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import psycopg2
from psycopg2.extensions import parse_dsn

root=Path(__file__).resolve().parents[1]
dsn=os.environ['PULSE_TEST_POSTGRES_DSN']
assert parse_dsn(dsn).get('host') in {'localhost','127.0.0.1','/tmp/pulse-pg-socket'}
schema='pulse_http_'+uuid.uuid4().hex
pg=psycopg2.connect(dsn);pg.autocommit=True
with pg.cursor() as c:c.execute('CREATE SCHEMA '+schema)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env={**os.environ,'DATABASE_URL':dsn+" options='-c search_path="+schema+"'",'PULSE_DB_SSLMODE':'disable','PULSE_SESSION_SECRET':'http-test-session','PULSE_ADMIN_KEY':'http-test-admin','PULSE_SALT':'http-test-salt','PAYSTACK_SECRET_KEY':'','RENDER':''}
env['PYTHONPATH']=str(root)+os.pathsep+env.get('PYTHONPATH','')
base='http://127.0.0.1:'+str(port)
cookies=http.cookiejar.CookieJar()
http=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(cookies))
server=None
log=open('/tmp/kenya-pulse-local-http.log','w')

def call(path,data=None,headers=None):
    req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'User-Agent':'local-persistence-test','Content-Type':'application/json',**(headers or {})})
    try:
        with http.open(req,timeout=5) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)

def start():
    global server
    server=subprocess.Popen([sys.executable,'-c',f"from app import app;app.config['SESSION_COOKIE_SECURE']=False;app.run(host='127.0.0.1',port={port},use_reloader=False)"],env=env,cwd=root,stdout=log,stderr=log)
    for _ in range(100):
        try:
            if call('/health')[0]==200:return
        except Exception:pass
        if server.poll() is not None:raise RuntimeError('Local server exited')
        time.sleep(.1)
    raise RuntimeError('Local server failed to start')

def stop():
    global server
    if server:
        server.terminate();server.wait(timeout=10);server=None

try:
    start()
    assert call('/health')[1]['database']=='postgres'
    token=call('/api/csrf')[1]['token'];headers={'X-CSRF-Token':token}
    races=['President','Governor','Senator','Woman Representative','Member of Parliament','MCA']
    for i,race in enumerate(races):
        candidate={'name':'HTTP Test '+race,'race':race,'county':'Kericho','constituency':'Ainamoi' if i>=4 else '', 'ward':'Kapsoit' if i==5 else '', 'source_url':'https://example.org/http-test','identity_verified':True,'verified_aliases':['HTTP Alias '+str(i)]}
        status,result=call('/admin/candidates',candidate,{'Authorization':'Bearer http-test-admin'});assert status==200,result
        cid=result['candidate_id']
        status,match=call('/api/candidates/resolve',{'name':'HTTP Alias '+str(i),'race':race,'county':'Kericho','constituency':candidate['constituency'],'ward':candidate['ward']},headers)
        assert status==200 and match['matches'][0]['id']==cid
        status,result=call('/api/vote',{**candidate,'candidate':candidate['name'],'candidate_id':cid},headers)
        assert status==200,result
        assert result['progress']['complete']==(i==5)
    before={r:call('/api/results?county=Kericho&race='+urllib.parse.quote(r)+('&constituency=Ainamoi' if i>=4 else '')+('&ward=Kapsoit' if i==5 else ''))[1] for i,r in enumerate(races)}
    registry=call('/api/candidates?race=President&county=Kericho')[1]
    assert call('/api/support/initialize',{'county':'Kericho'},headers)[0]==503
    status,_=call('/api/ground/issues',{'county':'Kericho','constituency':'Ainamoi','ward':'Kapsoit','category':'Water','description':'HTTP test community water issue'},headers)
    assert status==201 and call('/api/ground/issues')[1]['issues']==[]
    with pg.cursor() as cur:
        cur.execute('UPDATE '+schema+".ground_issues SET status='PUBLISHED' RETURNING id")
        issue_id=cur.fetchone()[0]
    assert call('/api/ground/issues/'+str(issue_id)+'/confirm',{},headers)[0]==200
    issue_before=call('/api/ground/issues')[1]
    stop();start()
    assert call('/api/ground/issues')[1]==issue_before
    assert call('/api/ground/issues/'+str(issue_id)+'/confirm',{},headers)[0]==409
    assert call('/api/participation?county=Kericho')[1]['complete'] is True
    assert call('/api/candidates?race=President&county=Kericho')[1]==registry
    after={r:call('/api/results?county=Kericho&race='+urllib.parse.quote(r)+('&constituency=Ainamoi' if i>=4 else '')+('&ward=Kapsoit' if i==5 else ''))[1] for i,r in enumerate(races)}
    assert before==after and all(v['total']==1 for v in after.values())
    token=call('/api/csrf')[1]['token']
    status,_=call('/api/vote',{**candidate,'candidate':candidate['name'],'candidate_id':cid},{'X-CSRF-Token':token})
    assert status==409
    print('LOCAL HTTP PASS: six-seat journey, alias resolution, optional payment gate, app restart, preserved six responses/results/aliases, refresh, duplicate rejection and preserved moderated community issues.')
finally:
    stop();log.close()
    with pg.cursor() as c:c.execute('DROP SCHEMA '+schema+' CASCADE')
    pg.close()
