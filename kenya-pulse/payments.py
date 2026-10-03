"""Optional support only. Never reads, weights, or writes candidate results.

Official contract: https://paystack.com/docs/api/transaction/
https://paystack.com/docs/payments/webhooks/
Live keys deliberately remain disabled until a separately reviewed activation.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from flask import request, jsonify, session


def amount_minor(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,14}(?:\.\d{1,2})?', str(value)):
        raise ValueError('Enter a positive amount with at most two decimal places.')
    amount = Decimal(str(value)) * 100
    if amount < 1:
        raise ValueError('Enter a positive amount.')
    return int(amount)


def paystack_request(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = Request('https://api.paystack.co' + path, data=body, headers={
        'Authorization': 'Bearer ' + os.environ.get('PAYSTACK_SECRET_KEY', ''),
        'Content-Type': 'application/json', 'User-Agent': 'KenyaPulse/1.0'})
    with urlopen(req, timeout=12) as response:
        data = json.loads(response.read(1048576))
    if not isinstance(data, dict) or data.get('status') is not True or not isinstance(data.get('data'), dict):
        raise ValueError('Provider did not confirm the request')
    return data['data']


def register_payments(app, conn, participation_state, legal_page):
    schema_ok = False
    try:
        with conn() as c:
            pk = 'BIGSERIAL' if c.pg else 'INTEGER'
            c.execute(f'''CREATE TABLE IF NOT EXISTS support_contributions(
                id {pk} PRIMARY KEY, amount_minor BIGINT NOT NULL CHECK(amount_minor>0),
                currency TEXT NOT NULL CHECK(currency='KES'), reference TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PAID')),
                idempotency_key TEXT NOT NULL UNIQUE, checkout_url TEXT,
                provider_transaction_id TEXT UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP, mode TEXT NOT NULL CHECK(mode='test'))''')
            c.execute('CREATE INDEX IF NOT EXISTS support_status_lookup ON support_contributions(status,created_at)')
        schema_ok = True
    except Exception:
        app.logger.error('Support schema unavailable; payment initialization is disabled.')

    def configuration():
        key = os.environ.get('PAYSTACK_SECRET_KEY', '')
        configured = key.startswith('sk_test_') and len(key) > 16
        persistent = bool(os.environ.get('DATABASE_URL')) or app.config.get('TESTING', False)
        enabled = configured and persistent and schema_ok and os.environ.get('PAYSTACK_MODE', 'test') == 'test'
        return {'configured': configured, 'enabled': enabled, 'mode': 'test', 'live_enabled': False,
                'schema_ok': schema_ok, 'persistent_database_required': not persistent}

    def verified_payment(reference):
        with conn() as c:
            row = c.execute('SELECT * FROM support_contributions WHERE reference=?', (reference,)).fetchone()
        if not row:
            return False
        if row['status'] == 'PAID':
            return True
        data = paystack_request('/transaction/verify/' + reference)
        if (data.get('status') != 'success' or data.get('reference') != reference or
            type(data.get('amount')) is not int or data['amount'] != row['amount_minor'] or
            data.get('currency') != row['currency'] or data.get('domain') != row['mode'] or
            type(data.get('id')) is not int or data['id'] <= 0):
            return False
        with conn() as c:
            c.execute("UPDATE support_contributions SET status='PAID',provider_transaction_id=?,paid_at=CURRENT_TIMESTAMP WHERE reference=? AND status='PENDING'", (str(data['id']), reference))
        return True

    @app.post('/api/support/initialize')
    def initialize():
        if not configuration()['enabled']:
            return jsonify(error='Support payments are not enabled yet. Participation is complete and results remain free.'), 503
        data = request.get_json(silent=True) or {}
        county = data.get('county', '')
        if not isinstance(county, str) or not participation_state(county)['complete']:
            return jsonify(error='Complete all six seats before optional support.'), 409
        try:
            amount = amount_minor(data.get('amount'))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        email = data.get('email', '')
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            return jsonify(error='Enter an email address for the payment receipt.'), 400
        nonce = data.get('idempotency_key', '')
        if not isinstance(nonce, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', nonce):
            return jsonify(error='Refresh the support form and try again.'), 400
        if 'support_owner' not in session:
            session['support_owner'] = secrets.token_hex(24)
        key = hashlib.sha256((session['support_owner'] + nonce).encode()).hexdigest()
        reference = 'kp-' + secrets.token_hex(24)
        with conn() as c:
            c.execute("INSERT INTO support_contributions(amount_minor,currency,reference,idempotency_key,mode) VALUES(?,'KES',?,?,'test') ON CONFLICT(idempotency_key) DO NOTHING", (amount, reference, key))
            row = c.execute('SELECT * FROM support_contributions WHERE idempotency_key=?', (key,)).fetchone()
        if row['amount_minor'] != amount:
            return jsonify(error='This payment attempt has a different amount. Start a new attempt.'), 409
        reference = row['reference']
        if row['status'] == 'PAID':
            return jsonify(status='PAID', reference=reference)
        if row['checkout_url']:
            return jsonify(authorization_url=row['checkout_url'], reference=reference)
        origin = os.environ.get('PULSE_PUBLIC_URL', 'https://kenya-pulse-live.onrender.com').rstrip('/')
        if origin not in {'https://kenya-pulse-live.onrender.com', 'https://kenyapulse.org', 'https://www.kenyapulse.org'}:
            return jsonify(error='Payment return address needs configuration.'), 503
        try:
            result = paystack_request('/transaction/initialize', {'amount': str(amount), 'currency': 'KES', 'email': email,
                'reference': reference, 'callback_url': origin + '/support/return'})
            url = result.get('authorization_url', '')
            parsed = urlsplit(url)
            if result.get('reference') != reference or parsed.scheme != 'https' or parsed.netloc != 'checkout.paystack.com':
                raise ValueError('Unexpected checkout destination')
            with conn() as c:
                c.execute('UPDATE support_contributions SET checkout_url=? WHERE reference=?', (url, reference))
            return jsonify(authorization_url=url, reference=reference)
        except Exception:
            return jsonify(error='Payment provider unavailable. No contribution is required; results remain free.'), 502

    @app.post('/api/support/webhook')
    def webhook():
        if not configuration()['enabled']:
            return jsonify(error='Payments disabled'), 503
        payload = request.get_data()
        supplied = request.headers.get('x-paystack-signature', '')
        expected = hmac.new(os.environ['PAYSTACK_SECRET_KEY'].encode(), payload, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return jsonify(error='Invalid signature'), 401
        event = request.get_json(silent=True)
        if not isinstance(event, dict):
            return jsonify(error='Invalid event'), 400
        if event.get('event') != 'charge.success':
            return jsonify(ok=True)
        data = event.get('data')
        if not isinstance(data, dict):
            return jsonify(error='Invalid event data'), 400
        reference = data.get('reference', '')
        if not isinstance(reference, str) or not re.fullmatch(r'kp-[a-f0-9]{48}', reference):
            return jsonify(error='Invalid reference'), 400
        with conn() as c:
            row = c.execute('SELECT amount_minor,currency FROM support_contributions WHERE reference=?', (reference,)).fetchone()
        if not row or type(data.get('amount')) is not int or data['amount'] != row['amount_minor'] or data.get('currency') != row['currency']:
            return jsonify(error='Contribution details do not match'), 400
        try:
            if not verified_payment(reference):
                return jsonify(error='Transaction not verified'), 400
        except Exception:
            return jsonify(error='Verification temporarily unavailable'), 503
        return jsonify(ok=True)

    @app.get('/support/return')
    def callback():
        reference = request.args.get('reference', '')
        if not configuration()['enabled'] or not re.fullmatch(r'kp-[a-f0-9]{48}', reference):
            return legal_page('Payment not confirmed', 'Participation and results remain free', [('Status', 'No payment has been confirmed.')]), 400
        try:
            paid = verified_payment(reference)
        except Exception:
            paid = False
        title = 'Thank you for supporting Kenya Pulse' if paid else 'Payment awaiting verification'
        return legal_page(title, 'Your participation is complete', [('Status', 'Your test contribution was verified by the server. No live money was taken.' if paid else 'We have not confirmed a successful contribution. You can return to your results without paying.')])

    return configuration
