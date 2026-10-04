import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mkulima_ai'))
import app as mkulima


class SaleFollowupRegression(unittest.TestCase):
    def sale(self):
        state = {}
        for text in ('Nataka kuuza mahindi', 'Kapsuser tegat factory', '10'):
            state = mkulima.apply_message(text, state)
        return state

    def test_language_and_landmark_survive_short_answers(self):
        state = self.sale()
        self.assertEqual(state['language'], 'sw')
        self.assertEqual(state['location'], 'Kapsuser Tegat Factory')
        self.assertEqual(state['bags'], 10)
        self.assertIn('bei gani', mkulima.reply_for('10', state))

    def test_no_offer_sentence_advances_maize_sale(self):
        for text in ('Have none want to start a fresh', 'I have no buyer yet', 'sina mnunuzi', 'no offer'):
            with self.subTest(text=text):
                state = mkulima.apply_message(text, self.sale())
                self.assertTrue(state['no_offer'])
                self.assertEqual(state['stage'], 'complete')
                self.assertEqual(state['bags'], 10)
                self.assertIn('listing', mkulima.reply_for(text, state))
                self.assertNotIn('What price per bag', mkulima.reply_for(text, state))

    def test_k_price_advances_and_clears_no_offer(self):
        for text, expected in (('4K', 4000), ('4k', 4000), ('KSh 4.5k', 4500), ('4k per bag', 4000), ('4,000', 4000)):
            with self.subTest(text=text):
                state = mkulima.apply_message('Have none want to start a fresh', self.sale())
                state = mkulima.apply_message(text, state)
                self.assertEqual(state['offer'], expected)
                self.assertNotIn('no_offer', state)
                self.assertEqual(state['stage'], 'complete')
                self.assertIn(f'{expected * 10:,.0f}', mkulima.reply_for(text, state))

    def test_explicit_restart_clears_old_sale(self):
        state = mkulima.apply_message('start afresh', self.sale())
        self.assertNotIn('bags', state)
        self.assertNotIn('location', state)
        self.assertNotIn('product', state)

    def test_new_crop_clears_old_no_offer(self):
        state = mkulima.apply_message('no offer', self.sale())
        state = mkulima.apply_message('sell beans', state)
        self.assertNotIn('no_offer', state)
        self.assertNotIn('bags', state)

    def test_negated_no_offer_does_not_skip_offer(self):
        self.assertFalse(mkulima.is_no_offer('not no offer, buyer offered 4000'))

    def test_webhook_screenshot_conversation(self):
        states = {}
        replies = []
        def save(phone, state):
            states[phone] = dict(state)
        with patch.multiple(mkulima,
                claim_message=lambda mid: True,
                load_state=lambda phone: states.get(phone, {}), save_state=save,
                get_access=lambda phone: {'active': True},
                record_farm_event=lambda *a, **k: None,
                create_seller_listing=lambda *a: 'test-listing',
                consume_free_question=lambda *a: None,
                record_interaction=lambda *a: None,
                send_whatsapp_text=lambda phone, text: replies.append(text)):
            client = mkulima.app.test_client()
            for index, text in enumerate(('Nataka kuuza mahindi', 'Kapsuser tegat factory', '10', 'Have none want to start a fresh', '4K')):
                response = client.post('/webhook/whatsapp', json={'entry': [{'changes': [{'value': {'messages': [{'id': f'test-{index}', 'from': 'test-farmer', 'type': 'text', 'text': {'body': text}}]}}]}]})
                self.assertEqual(response.status_code, 200)
            state = states['test-farmer']
            self.assertEqual(state['offer'], 4000)
            self.assertEqual(state['location'], 'Kapsuser Tegat Factory')
            self.assertIn('listing', replies[-2])
            self.assertIn('40,000', replies[-1])
            self.assertNotIn('What price per bag', replies[-1])


if __name__ == '__main__':
    unittest.main()
