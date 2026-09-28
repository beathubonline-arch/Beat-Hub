import json
import unittest
from urllib.error import HTTPError
from feedback import FeedbackStore, FeedbackUnavailable, actor_ref, parse_feedback


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.body).encode()


class FeedbackTests(unittest.TestCase):
    def test_explicit_multilingual_feedback(self):
        for text, expected in [('👍', ('helpful', None)),
                               ('Si sahihi: Bei ilikuwa tofauti', ('wrong', 'Bei ilikuwa tofauti')),
                               ('Still problem: no buyer', ('still_problem', 'no buyer')),
                               ('Bado tatizo', ('still_problem', None))]:
            self.assertEqual(parse_feedback(text), expected)
        self.assertIsNone(parse_feedback('what should I do about this problem?'))

    def test_actor_ids_are_stable_and_not_phones(self):
        key = 'synthetic-test-secret' * 2
        a = actor_ref('+254 700 000 000', key)
        self.assertEqual(a, actor_ref('254700000000', key))
        self.assertNotEqual(a, actor_ref('254700000001', key))
        self.assertNotIn('254700', a)

    def test_rest_contract_and_idempotent_capture(self):
        saved = {}
        calls = []
        def opener(req, timeout):
            calls.append(req)
            if req.method == 'POST':
                saved.update(json.loads(req.data))
                return Response(None)
            return Response([dict(saved, id='synthetic-id')])
        store = FeedbackStore('https://example.supabase.co', 'synthetic-key', opener)
        params = dict(message_id='m1', actor='a'*64, question='I have maize',
                      recommendation='Compare offers', language='en', answer_version='test')
        self.assertEqual(store.record_interaction(**params), 'synthetic-id')
        self.assertEqual(store.record_interaction(**params), 'synthetic-id')
        self.assertIn('ignore-duplicates', calls[0].headers['Prefer'])
        self.assertIn('actor_ref=', calls[1].full_url)
        self.assertIsNone(saved['confidence'])

    def test_feedback_ownership_sent_to_atomic_rpc(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            return Response('feedback-id')
        store = FeedbackStore('https://example.supabase.co', 'synthetic-key', opener)
        self.assertEqual(store.record_feedback(message_id='f1', interaction_id='i1',
                         actor='a'*64, rating='wrong', outcome='different price'), 'feedback-id')
        self.assertTrue(calls[0].full_url.endswith('/rpc/mkulima_record_feedback'))
        self.assertEqual(json.loads(calls[0].data)['p_actor_ref'], 'a'*64)
        self.assertEqual(store.retrieve(crop=None,region='eldoret',language='en',problem='price'), [])
        self.assertEqual(len(calls), 1)

    def test_network_error_never_exposes_secret(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 403, 'synthetic-secret personal content', {}, None)
        store = FeedbackStore('https://example.supabase.co', 'synthetic-secret', opener)
        with self.assertRaises(FeedbackUnavailable) as err:
            store.retrieve(crop='maize',region='eldoret',language='en',problem='sale')
        self.assertEqual(str(err.exception), 'Feedback storage request failed')


if __name__ == '__main__': unittest.main()
