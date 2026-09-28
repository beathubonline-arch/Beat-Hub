import copy
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
from app import app

SECRET = 'synthetic-app-secret-not-real-123456789'


class MemoryStore:
    """Test double for the separately tested Postgres transaction contract."""
    def __init__(self):
        self.sessions = {}
        self.jobs = {}
        self.interactions = {}
        self.feedback = {}

    def _call(self, route, p):
        name = route.split('mkulima_', 1)[1]
        mid = p.get('p_id')
        if name == 'claim_message':
            actor = p['p_actor']
            s = self.sessions.setdefault(actor, {'state': {}, 'latest_interaction': None})
            j = self.jobs.get(mid)
            if j:
                if j['actor'] != actor or j['hash'] != p['p_hash']: raise ValueError('conflict')
                if j.get('sent'): return {'status': 'sent'}
                if j.get('locked'): return {'status': 'busy'}
            else:
                if any(x['actor']==actor and not x.get('sent') for x in self.jobs.values()): return {'status':'busy'}
                j = self.jobs[mid] = {'actor': actor, 'hash': p['p_hash']}
            j['locked'] = True
            return dict(copy.deepcopy(s), status='claimed', token='test-token', response=j.get('response'))
        if name == 'feedback_target':
            return next((j.get('interaction') for j in self.jobs.values()
                         if j['actor']==p['p_actor'] and j.get('outbound')==p['p_outbound'] and j.get('sent')), None)
        j = self.jobs[mid]
        if name == 'stage_message':
            j.update(response=p['p_response'], state=copy.deepcopy(p['p_state']))
            if p['p_interaction']:
                j['interaction'] = mid
                self.interactions[mid] = copy.deepcopy(p['p_interaction'])
        elif name == 'finish_message':
            j.update(sent=True, locked=False, outbound=p['p_outbound'])
            s = self.sessions[j['actor']]
            s['state'] = copy.deepcopy(j['state'])
            s['latest_interaction'] = j.get('interaction') or s['latest_interaction']
        elif name == 'release_message': j['locked'] = False
        else: raise AssertionError(name)

    def record_feedback(self, **kw):
        self.feedback.setdefault(kw['message_id'], kw)


class SignedWebhookTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MKULIMA_FEEDBACK_ENABLED':'1','WHATSAPP_APP_SECRET':SECRET})
        self.env.start()
        self.store = MemoryStore()
        app.config['FEEDBACK_STORE'] = self.store
        self.client = app.test_client()
        self.sent = []
        def sender(phone, body):
            self.sent.append((phone,body))
            return {'messages':[{'id':f'out-{len(self.sent)}'}]}
        self.send = patch('app.send_whatsapp_text', side_effect=sender)
        self.send.start()

    def tearDown(self):
        self.send.stop(); self.env.stop()
        app.config.pop('FEEDBACK_STORE', None)

    def post(self, mid, text, phone='254700000000', context=None, valid=True):
        msg = {'id':mid,'from':phone,'type':'text','text':{'body':text}}
        if context: msg['context']={'id':context}
        raw = json.dumps({'entry':[{'changes':[{'value':{'messages':[msg]}}]}]}).encode()
        signature = 'sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
        if not valid: signature='sha256=bad'
        return self.client.post('/webhook/whatsapp',data=raw,content_type='application/json',headers={'X-Hub-Signature-256':signature})

    def test_english_conversation_restart_feedback_and_duplicate(self):
        self.assertEqual(self.post('en1','I am in Eldoret near Pioneer school').status_code,200)
        self.assertIn('How many bags',self.sent[-1][1])
        # Recreate the request client: there is no dependency on local SQLite state.
        self.client = app.test_client()
        self.post('en2','10')
        self.assertIn('What price per bag',self.sent[-1][1])
        self.post('en3','3000')
        self.assertIn('Eldoret Near Pioneer School',self.sent[-1][1])
        self.assertIn('KES 30,000',self.sent[-1][1])
        self.assertEqual(self.post('en4','👍 Helpful: I compared two offers',context='out-3').status_code,200)
        self.assertEqual(self.store.feedback['en4']['interaction_id'],'en3')
        self.assertEqual(self.store.feedback['en4']['outcome'],'I compared two offers')
        count = len(self.sent)
        self.post('en4','👍 Helpful: I compared two offers',context='out-3')
        self.assertEqual(len(self.sent),count)
        self.assertEqual(len(self.store.feedback),1)

    def test_swahili_delivery_failure_retry_and_negative_feedback(self):
        self.post('sw1','Niko Kapsoya karibu na shule gunia 5 broker 3200')
        self.assertIn('Kapsoya Karibu Na Shule',self.sent[-1][1])
        self.assertIn('KES 16,000',self.sent[-1][1])
        with patch('app.send_whatsapp_text',side_effect=TimeoutError('synthetic timeout')):
            self.assertEqual(self.post('sw2','Bado tatizo: sijapata buyer',context='out-1').status_code,503)
        self.assertFalse(self.store.jobs['sw2'].get('sent',False))
        self.client = app.test_client()
        self.assertEqual(self.post('sw2','Bado tatizo: sijapata buyer',context='out-1').status_code,200)
        self.assertEqual(self.store.feedback['sw2']['rating'],'still_problem')
        self.assertEqual(len(self.store.feedback),1)

    def test_signature_and_cross_farmer_reply(self):
        self.assertEqual(self.post('bad','hello',valid=False).status_code,401)
        self.assertEqual(len(self.store.jobs),0)
        self.post('first','I am in Eldoret 2 bags buyer 3000')
        self.post('other','👎 Wrong: incorrect',phone='254700000001',context='out-1')
        self.assertEqual(len(self.store.feedback),0)
        self.assertIn('ujumbe',self.sent[-1][1])


if __name__=='__main__': unittest.main()
