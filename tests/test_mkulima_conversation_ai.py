import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mkulima_ai'))
import conversation_ai as ai
import app as mkulima


def turn(**changes):
    result = {field: None for field in ai.FIELDS + ['quantity', 'offer', 'no_offer']}
    result.update(intent='sell', language='en', reset=False, reply='Do you want help finding buyers?')
    result.update(changes)
    return result


class ReasonerTests(unittest.TestCase):
    def test_missing_key_is_honest_and_makes_no_request(self):
        with patch.dict(os.environ, {}, clear=True), patch('urllib.request.urlopen') as request:
            self.assertEqual(ai.reason_message('anything', {})['status'], 'not_configured')
            request.assert_not_called()

    def test_provider_request_uses_astra_schema_bounded_context_and_no_storage(self):
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(turn(no_offer=True))}]}]}
        stream = MagicMock()
        stream.__enter__.return_value.read.return_value = json.dumps(response).encode()
        with patch.dict(os.environ, {'MKULIMA_OPENAI_API_KEY': 'test-only'}, clear=True), patch('urllib.request.urlopen', return_value=stream) as http:
            result = ai.reason_message('Buyer has not come yet', {'stage': 'offer', 'phone': 'private-phone', 'payment_id': 'secret', 'recent_turns': [{'role': 'assistant', 'text': 'What price?'}]})
            self.assertTrue(result['ok'])
            payload = json.loads(http.call_args.args[0].data)
            self.assertEqual(payload['model'], 'gpt-6-astra')
            self.assertFalse(payload['store'])
            self.assertTrue(payload['text']['format']['strict'])
            self.assertIn('What price?', payload['input'])
            self.assertNotIn('private-phone', payload['input'])
            self.assertNotIn('secret', payload['input'])

    def test_bad_output_and_provider_failure_fall_back(self):
        for response in ({'status': 'incomplete'}, {'status': 'completed', 'output': []}):
            stream = MagicMock()
            stream.__enter__.return_value.read.return_value = json.dumps(response).encode()
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}, clear=True), patch('urllib.request.urlopen', return_value=stream):
                self.assertFalse(ai.reason_message('hi', {})['ok'])
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}, clear=True), patch('urllib.request.urlopen', side_effect=TimeoutError('secret-body')):
            result = ai.reason_message('hi', {})
            self.assertEqual(result['status'], 'provider_error')
            self.assertNotIn('secret-body', str(result))

    def test_rejects_action_fields_invalid_numbers_and_contradictions(self):
        for data in (turn(payment_active=True), turn(quantity=True), turn(offer=-1), turn(offer=float('nan')), turn(no_offer=True, offer=4000), turn(reply='')):
            self.assertIsNone(ai.validate_turn(data))

    def test_corrections_keep_landmark_and_replace_quantity(self):
        state = {'product': 'maize', 'bags': 10, 'quantity': 10, 'quantity_unit': 'bags', 'location': 'Kapsuser Tegat Factory', 'offer': 4000}
        state = ai.merge_turn(state, turn(quantity=12, quantity_unit='bags', offer=4200))
        self.assertEqual(state['bags'], 12)
        self.assertEqual(state['offer'], 4200)
        self.assertEqual(state['location'], 'Kapsuser Tegat Factory')
        self.assertNotIn('payment_active', state)

    def test_topic_changes_reset_and_bounded_history(self):
        state = ai.merge_turn({'product': 'maize', 'quantity': 10}, turn(intent='crop_health', reply='Which leaves changed?'))
        self.assertEqual(state['primary_intent'], 'crop_health')
        state = ai.merge_turn(state, turn(reset=True, intent='general'))
        self.assertNotIn('quantity', state)
        for n in range(10):
            state = ai.remember_turn(state, str(n), 'reply')
        self.assertEqual(len(state['recent_turns']), 8)

    def test_failed_interpretation_does_not_repeat_identical_question(self):
        state = mkulima.apply_message('sell maize near Kapsuser 10 bags', {})
        reply = mkulima.reply_for('unclear', state)
        state = ai.remember_turn(state, 'unclear', reply)
        clarification = mkulima.clarify_repeated_reply('huh??', state, reply)
        self.assertNotEqual(clarification, reply)
        self.assertIn('asking price', clarification)

    def test_webhook_semantic_correction_then_new_topic(self):
        states = {'farmer': {'product': 'maize', 'crop': 'maize', 'location': 'Kapsuser Tegat Factory', 'bags': 10, 'quantity': 10, 'quantity_unit': 'bags', 'offer': 4000, 'primary_intent': 'sell', 'intents': ['sell'], 'stage': 'complete'}}
        replies = []
        interpretations = [turn(quantity=12, quantity_unit='bags', offer=4200), turn(product='mushrooms', crop='mushrooms', quantity=50, quantity_unit='kg', offer=200, reply='Confirm whether the buyer collects the mushrooms.'), turn(intent='crop_health', reply='Are the yellow leaves on the same maize crop?')]
        with patch.multiple(mkulima, claim_message=lambda mid: True,
                load_state=lambda phone: states[phone], save_state=lambda phone, state: states.update({phone: dict(state)}),
                get_access=lambda phone: {'active': True}, record_farm_event=lambda *a, **k: None,
                consume_free_question=lambda *a: None, record_interaction=lambda *a: None,
                send_whatsapp_text=lambda phone, reply: replies.append(reply)), patch.object(mkulima, 'reason_message', side_effect=[{'ok': True, 'status': 'answered', 'turn': x} for x in interpretations]):
            client = mkulima.app.test_client()
            for n, text in enumerate(('Actually twelve sacks, buyer says forty two hundred each', 'Now I also have mushrooms 50 kg at 200 per kg', 'Forget the sale for now, the leaves look yellow')):
                response = client.post('/webhook/whatsapp', json={'entry': [{'changes': [{'value': {'messages': [{'id': f'semantic-{n}', 'from': 'farmer', 'type': 'text', 'text': {'body': text}}]}}]}]})
                self.assertEqual(response.status_code, 200)
            self.assertIn('50,400', replies[0])
            self.assertIn('10,000', replies[1])
            self.assertEqual(replies[2], interpretations[2]['reply'])
            self.assertEqual(states['farmer']['primary_intent'], 'crop_health')
            self.assertEqual(len(states['farmer']['recent_turns']), 6)


if __name__ == '__main__':
    unittest.main()
