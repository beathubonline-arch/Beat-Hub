import copy
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import validate_geography, unique_object
import payments


@pytest.fixture(params=['sqlite', 'postgres'])
def pulse(request, tmp_path, monkeypatch):
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setenv('PULSE_ADMIN_KEY', 'test-admin-only')
    monkeypatch.setenv('PULSE_SALT', 'test-salt-only')
    monkeypatch.setenv('PULSE_SESSION_SECRET', 'test-session-secret-only')
    monkeypatch.setenv('PAYSTACK_SECRET_KEY', 'sk_test_local_mock_only')
    monkeypatch.setenv('PAYSTACK_MODE', 'test')
    monkeypatch.setenv('PULSE_DB', str(tmp_path / 'pulse.db'))
    monkeypatch.setenv('DATABASE_URL', '')
    pg = None
    schema = None
    if request.param == 'postgres':
        dsn = os.environ.get('PULSE_TEST_POSTGRES_DSN')
        if not dsn:
            pytest.skip('Set PULSE_TEST_POSTGRES_DSN to an isolated local test database')
        import psycopg2
        from psycopg2.extensions import parse_dsn
        details = parse_dsn(dsn)
        assert details.get('host') in {'localhost', '127.0.0.1', '/tmp/pulse-pg-socket'}, 'Local test databases only'
        pg = psycopg2.connect(dsn)
        pg.autocommit = True
        schema = 'pulse_test_' + uuid.uuid4().hex
        with pg.cursor() as c:
            c.execute('CREATE SCHEMA ' + schema)
        monkeypatch.setenv('DATABASE_URL', dsn + ' options=\'-c search_path=' + schema + '\'')
        monkeypatch.setenv('PULSE_DB_SSLMODE', 'disable')
    spec = importlib.util.spec_from_file_location('pulse_test_' + uuid.uuid4().hex, ROOT / 'app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SCHEMA_OK
    module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    yield module
    if pg:
        with pg.cursor() as c:
            c.execute('DROP SCHEMA ' + schema + ' CASCADE')
        pg.close()


def client(pulse, agent=None):
    c = pulse.app.test_client()
    c.environ_base['HTTP_USER_AGENT'] = agent or uuid.uuid4().hex
    token = c.get('/api/csrf').json['token']
    return c, {'X-CSRF-Token': token}


def seed(pulse, race, name=None, county='Kericho', constituency='', ward='', aliases=None):
    with pulse.app.test_client() as c:
        r = c.post('/admin/candidates', json={'name': name or 'Test ' + race, 'race': race, 'county': county,
            'constituency': constituency, 'ward': ward, 'source_url': 'https://example.org/test-fixture',
            'identity_verified': True, 'verified_aliases': aliases or []}, headers={'Authorization': 'Bearer test-admin-only'})
    assert r.status_code == 200, r.json
    return r.json['candidate_id']


def candidates(pulse):
    return [seed(pulse, race, constituency='Ainamoi' if i >= 4 else '', ward='Kapsoit' if i == 5 else '', aliases=['Alias ' + str(i)]) for i, race in enumerate(pulse.RACES)]


def submit(pulse, c, headers, ids, i):
    return c.post('/api/vote', headers=headers, json={'county': 'Kericho', 'race': pulse.RACES[i], 'candidate': 'Display input', 'candidate_id': ids[i], 'constituency': 'Ainamoi' if i >= 4 else '', 'ward': 'Kapsoit' if i == 5 else ''})


def complete(pulse):
    ids = candidates(pulse)
    c, headers = client(pulse)
    for i in range(6):
        r = submit(pulse, c, headers, ids, i)
        assert r.status_code == 200, r.json
    return c, headers, ids


def test_geography_every_api_and_deep_link(pulse):
    c, _ = client(pulse)
    info = validate_geography(pulse.GEOGRAPHY, pulse.COUNTIES)
    assert info == {'ok': True, 'errors': [], 'counties': 47, 'constituencies': 290, 'wards': 1450}
    for county, constituencies in pulse.GEOGRAPHY.items():
        r = c.get('/api/geography', query_string={'county': county})
        assert r.json['constituencies'] == list(constituencies)
        slug = pulse.re.sub(r'[^a-z0-9]+', '-', county.lower()).strip('-')
        assert c.get('/county/' + slug).status_code == 200
        for constituency, wards in constituencies.items():
            assert c.get('/api/geography', query_string={'county': county, 'constituency': constituency}).json['wards'] == wards
    assert pulse.GEOGRAPHY['Kericho']['Ainamoi'][0] == 'Kapsoit'
    assert c.get('/api/geography?county=Kericho&constituency=Unknown').status_code == 400


def test_geography_corruption_is_unhealthy(pulse, monkeypatch):
    data = copy.deepcopy(pulse.GEOGRAPHY)
    data['Kericho']['Ainamoi'][1] = ' kapsoit '
    assert not validate_geography(data, pulse.COUNTIES)['ok']
    monkeypatch.setattr(pulse, 'GEOGRAPHY', data)
    c, _ = client(pulse)
    assert c.get('/health').status_code == 503
    with pytest.raises(ValueError):
        json.loads('{"x":1,"x":2}', object_pairs_hook=unique_object)
    data = copy.deepcopy(pulse.GEOGRAPHY)
    data['Unexpected'] = data.pop('Kericho')
    assert not validate_geography(data, pulse.COUNTIES)['ok']


def test_alias_identity_ambiguous_unknown_unverified_and_scope(pulse):
    a = seed(pulse, 'President', 'First Test Person', aliases=['Nickname'])
    c, h = client(pulse)
    def resolve(name, race='President', county='Kericho'):
        return c.post('/api/candidates/resolve', headers=h, json={'name': name, 'race': race, 'county': county}).json['matches']
    assert resolve('first TEST person')[0]['id'] == a
    assert resolve('  nickNAME  ')[0]['id'] == a
    with pulse.conn() as db:
        db.execute('INSERT INTO candidate_aliases(candidate_id,alias,normalized_alias,verified) VALUES(?,?,?,FALSE)', (a, 'Unverified', 'unverified'))
    assert resolve('Unverified') == []
    assert resolve('Misspelled') == []
    b = seed(pulse, 'President', 'Second Test Person', aliases=['Nickname'])
    assert {x['id'] for x in resolve('Nickname')} == {a, b}
    assert resolve('Nickname', race='Governor') == []
    seed(pulse, 'Governor', 'Local Person', aliases=['Local'])
    assert resolve('Local', race='Governor', county='Bomet') == []


def test_six_seats_order_duplicate_refresh_and_support_gate(pulse):
    ids = candidates(pulse)
    c, h = client(pulse)
    assert submit(pulse, c, h, ids, 5).status_code == 409
    for i in range(6):
        r = submit(pulse, c, h, ids, i)
        assert r.status_code == 200, r.json
        progress = r.json['progress']
        assert progress['complete'] == (i == 5)
        assert progress['next_race'] == (pulse.RACES[i + 1] if i < 5 else None)
        assert c.get('/api/participation?county=Kericho').json == progress
        if i < 5:
            assert c.post('/api/support/initialize', headers=h, json={'county': 'Kericho'}).status_code == 409
    assert submit(pulse, c, h, ids, 0).status_code == 409
    assert c.get('/county/kericho').status_code == 200
    with pulse.conn() as db:
        assert db.execute('SELECT count(*) n FROM pulse_votes').fetchone()['n'] == 6


def test_wrong_geography_and_candidate_scope(pulse):
    ids = candidates(pulse)
    c, h = client(pulse)
    for i in range(4):
        assert submit(pulse, c, h, ids, i).status_code == 200
    base = {'county': 'Kericho', 'race': 'Member of Parliament', 'candidate': 'Test', 'candidate_id': ids[4]}
    for area in ['', 'Unknown', 'Bureti']:
        assert c.post('/api/vote', headers=h, json={**base, 'constituency': area}).status_code == 400
    assert submit(pulse, c, h, ids, 4).status_code == 200
    assert c.post('/api/vote', headers=h, json={**base, 'race': 'MCA', 'candidate_id': ids[5], 'constituency': 'Ainamoi', 'ward': 'Unknown'}).status_code == 400
    assert submit(pulse, c, h, ids, 5).status_code == 200


def test_canonical_aggregation_and_preserve_legacy(pulse):
    cid = seed(pulse, 'President', 'Canonical Person')
    with pulse.conn() as db:
        for i, label in enumerate(['Nickname', 'Old full name']):
            db.execute('INSERT INTO pulse_votes(county,race,candidate,candidate_id,fp) VALUES(?,?,?,?,?)', ('Kericho', 'President', label, cid, 'test-' + str(i)))
        db.execute('INSERT INTO pulse_votes(county,race,candidate,fp) VALUES(?,?,?,?)', ('Kericho', 'President', 'Legacy unresolved', 'test-legacy'))
    pulse.init()
    c, _ = client(pulse)
    data = c.get('/api/results?county=Kericho&race=President').json
    assert data['total'] == 3
    assert len(data['results']) == 2
    assert data['results'][0]['candidate'] == 'Canonical Person'
    assert data['results'][0]['votes'] == 2
    assert data['results'][1]['identity_status'] == 'historical-unresolved'
    pulse.init()
    assert c.get('/api/results?county=Kericho&race=President').json == data


def test_admin_csrf_input_and_headers(pulse):
    c, h = client(pulse)
    for path in ['/admin/ads', '/admin/ad-metrics']:
        assert c.get(path + '?key=test-admin-only').status_code == 404
        assert c.get(path, headers={'Authorization': 'Bearer test-admin-only'}).status_code == 200
    assert c.post('/admin/ads/1/status?key=test-admin-only', json={'status': 'ACTIVE'}, headers=h).status_code == 404
    assert c.post('/api/vote', json={}).status_code == 403
    assert c.post('/api/vote', json={}, headers={**h, 'Origin': 'https://evil.example'}).status_code == 403
    assert c.post('/api/vote', json=[], headers=h).status_code == 400
    assert c.post('/api/vote', json={'county': {}}, headers=h).status_code == 400
    response = c.get('/api/csrf')
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'frame-ancestors' in response.headers['Content-Security-Policy']
    assert 'test-admin-only' not in c.get('/health').text
    assert 'sk_test_' not in c.get('/health').text


def test_ads_are_reviewed_separately_and_pg_compatible(pulse):
    c, h = client(pulse)
    r = c.post('/advertise', data={'csrf_token': h['X-CSRF-Token'], 'business': 'Test Shop', 'email': 'test@example.org', 'county': 'Kericho', 'scope': 'County', 'package': 'County Starter', 'headline': 'Fresh produce', 'url': 'https://example.org'})
    assert r.status_code == 200
    assert c.get('/api/ad?county=Kericho').json['ad'] is None
    with pulse.conn() as db:
        row = db.execute('SELECT id,status FROM ad_orders').fetchone()
        assert row['status'] == 'PENDING_REVIEW'
    assert c.post('/admin/ads/' + str(row['id']) + '/status', headers={'Authorization': 'Bearer test-admin-only'}, json={'status': 'ACTIVE'}).status_code == 200
    ad = c.get('/api/ad?county=Kericho').json['ad']
    assert ad['id'] == row['id']
    assert c.get(ad['click_url']).headers['Location'] == 'https://example.org'
    assert c.get('/growth').status_code == 200
    assert c.get('/admin/ad-metrics', headers={'Authorization': 'Bearer test-admin-only'}).status_code == 200


def initialize_mock(pulse, monkeypatch, amount='1'):
    c, h, ids = complete(pulse)
    calls = []
    def provider(path, payload=None):
        calls.append((path, payload))
        return {'reference': payload['reference'], 'authorization_url': 'https://checkout.paystack.com/local-test'}
    monkeypatch.setattr(payments, 'paystack_request', provider)
    body = {'county': 'Kericho', 'amount': amount, 'email': 'test@example.org', 'idempotency_key': uuid.uuid4().hex}
    r = c.post('/api/support/initialize', headers=h, json=body)
    assert r.status_code == 200, r.json
    reference = r.json['reference']
    assert c.post('/api/support/initialize', headers=h, json=body).json == r.json
    assert len(calls) == 1
    assert calls[0][1]['amount'] == str(payments.amount_minor(amount))
    return c, h, reference


def signed_event(reference, amount=100):
    data = json.dumps({'event': 'charge.success', 'data': {'reference': reference, 'amount': amount, 'currency': 'KES'}}).encode()
    signature = hmac.new(os.environ['PAYSTACK_SECRET_KEY'].encode(), data, hashlib.sha512).hexdigest()
    return data, {'Content-Type': 'application/json', 'x-paystack-signature': signature}


def test_payment_verified_idempotent_and_zero_result_influence(pulse, monkeypatch):
    c, h, reference = initialize_mock(pulse, monkeypatch)
    before = c.get('/api/results?county=Kericho&race=President').json
    monkeypatch.setattr(payments, 'paystack_request', lambda *args: {'status': 'success', 'reference': reference, 'amount': 100, 'currency': 'KES', 'domain': 'test', 'id': 123456})
    payload, headers = signed_event(reference)
    assert c.post('/api/support/webhook', data=payload, headers=headers).status_code == 200
    assert c.post('/api/support/webhook', data=payload, headers=headers).status_code == 200
    assert 'Thank you' in c.get('/support/return?reference=' + reference).text
    with pulse.conn() as db:
        row = db.execute('SELECT * FROM support_contributions').fetchone()
        assert row['status'] == 'PAID' and row['provider_transaction_id'] == '123456'
        assert db.execute('SELECT count(*) n FROM support_contributions').fetchone()['n'] == 1
        assert db.execute('SELECT count(*) n FROM ad_orders').fetchone()['n'] == 0
    assert before == c.get('/api/results?county=Kericho&race=President').json


@pytest.mark.parametrize('wrong', [{'amount': 99}, {'currency': 'USD'}, {'reference': 'fake'}, {'domain': 'live'}, {'status': 'pending'}, {'id': 'string'}])
def test_payment_rejects_provider_mismatch(pulse, monkeypatch, wrong):
    c, h, reference = initialize_mock(pulse, monkeypatch)
    result = {'status': 'success', 'reference': reference, 'amount': 100, 'currency': 'KES', 'domain': 'test', 'id': 99}
    result.update(wrong)
    monkeypatch.setattr(payments, 'paystack_request', lambda *args: result)
    payload, headers = signed_event(reference)
    assert c.post('/api/support/webhook', data=payload, headers=headers).status_code == 400
    assert 'awaiting verification' in c.get('/support/return?reference=' + reference).text
    with pulse.conn() as db:assert db.execute('SELECT status FROM support_contributions').fetchone()['status'] == 'PENDING'


def test_bad_signature_fake_reference_and_changed_event(pulse, monkeypatch):
    c, h, reference = initialize_mock(pulse, monkeypatch, '10000')
    monkeypatch.setattr(payments, 'paystack_request', lambda *args: pytest.fail('Untrusted event must not call verification'))
    payload, headers = signed_event(reference, 1000000)
    assert c.post('/api/support/webhook', data=payload, headers={**headers, 'x-paystack-signature': 'fake'}).status_code == 401
    payload, headers = signed_event(reference, 1)
    assert c.post('/api/support/webhook', data=payload, headers=headers).status_code == 400
    payload, headers = signed_event('kp-' + 'a' * 48, 1000000)
    assert c.post('/api/support/webhook', data=payload, headers=headers).status_code == 400
    assert c.get('/support/return?reference=untrusted').status_code == 400


def test_amounts_and_live_key_disabled(pulse, monkeypatch):
    for amount, expected in [('1', 100), ('1000', 100000), ('10000', 1000000), ('0.01', 1)]:
        assert payments.amount_minor(amount) == expected
    for amount in ['0', '-1', 'nan', '1.001', '1e5', True]:
        with pytest.raises(ValueError):payments.amount_minor(amount)
    monkeypatch.setenv('PAYSTACK_SECRET_KEY', 'sk_live_must_never_run')
    c, h = client(pulse)
    assert c.post('/api/support/initialize', headers=h, json={}).status_code == 503
    assert c.get('/health').json['payments']['live_enabled'] is False


def test_rate_limit_shared_between_clients(pulse):
    c, h = client(pulse, 'same-agent')
    for _ in range(91):
        r = c.post('/api/analytics/pageview', headers=h, json={'path': '/test'})
    assert r.status_code == 429
    other, oh = client(pulse, 'same-agent')
    assert other.post('/api/analytics/pageview', headers=oh, json={}).status_code == 429


def test_existing_registry_migration_never_guesses_identity(pulse):
    with pulse.conn() as db:
        row = db.execute("INSERT INTO candidates(name,race) VALUES('Historic Name','President') RETURNING id").fetchone()
        cid = row['id']
        db.execute("INSERT INTO candidate_aliases(candidate_id,alias,verified) VALUES(?,'Old Alias',TRUE)", (cid,))
        db.execute("INSERT INTO pulse_votes(county,race,candidate,candidate_id,fp) VALUES('Kericho','President','Historic Name',?,'old-fp')", (cid,))
    pulse.init()
    pulse.init()
    with pulse.conn() as db:
        candidate = db.execute('SELECT * FROM candidates WHERE id=?', (cid,)).fetchone()
        assert not candidate['identity_verified']
        assert candidate['normalized_name'] == 'historic name'
        assert db.execute('SELECT candidate_id FROM pulse_votes').fetchone()['candidate_id'] == cid
        assert db.execute('SELECT count(*) n FROM candidate_aliases').fetchone()['n'] == 1
        assert db.execute('SELECT count(*) n FROM participation_slots').fetchone()['n'] == 1
    c, h = client(pulse)
    assert c.post('/api/candidates/resolve', headers=h, json={'name': 'Old Alias', 'race': 'President'}).json['matches'] == []


def test_empty_target_import_dry_run_apply_and_refuse_overwrite(pulse, tmp_path):
    if not pulse.DATABASE_URL:
        pytest.skip('PostgreSQL import test')
    from migrate_sqlite import migrate
    source = tmp_path / 'source.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE candidates(id INTEGER PRIMARY KEY,name TEXT,race TEXT,active INTEGER)')
        db.execute("INSERT INTO candidates VALUES(20,'Source Person','President',1)")
        db.execute('CREATE TABLE candidate_aliases(id INTEGER PRIMARY KEY,candidate_id INTEGER,alias TEXT,verified INTEGER)')
        db.execute("INSERT INTO candidate_aliases VALUES(30,20,'Source Alias',1)")
        db.execute('CREATE TABLE pulse_votes(id INTEGER PRIMARY KEY,county TEXT,race TEXT,candidate TEXT,candidate_id INTEGER,fp TEXT)')
        db.execute("INSERT INTO pulse_votes VALUES(40,'Kericho','President','Source Alias',20,'source-fp')")
    assert migrate(source, pulse.conn)['pulse_votes'] == 1
    with pulse.conn() as db:
        assert db.execute('SELECT count(*) n FROM pulse_votes').fetchone()['n'] == 0
    assert migrate(source, pulse.conn, apply=True)['candidate_aliases'] == 1
    pulse.init()
    with pulse.conn() as db:
        assert db.execute('SELECT id,candidate_id FROM pulse_votes').fetchone()['candidate_id'] == 20
        assert db.execute('SELECT alias FROM candidate_aliases').fetchone()['alias'] == 'Source Alias'
        assert not db.execute('SELECT identity_verified FROM candidates').fetchone()['identity_verified']
        assert db.execute("INSERT INTO candidates(name,race) VALUES('Later','President') RETURNING id").fetchone()['id'] > 20
    with pytest.raises(ValueError):migrate(source, pulse.conn, apply=True)
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT count(*) FROM pulse_votes').fetchone()[0] == 1
