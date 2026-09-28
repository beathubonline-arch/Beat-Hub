"""Opt-in signed WhatsApp ingestion; caller supplies existing answer functions."""
import hashlib
import hmac
import json
from feedback import actor_ref, parse_feedback, FeedbackUnavailable


def valid_signature(raw, signature, secret):
    if not secret or not isinstance(signature, str):
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def rpc(store, name, **params):
    return store._call('rpc/mkulima_' + name, params)


def process_message(msg, store, secret, apply_message, reply_for, send):
    mid, phone = msg['id'], msg['from']
    actor = actor_ref(phone, secret)
    fingerprint = hashlib.sha256(json.dumps(msg, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    job = rpc(store, 'claim_message', p_id=mid, p_actor=actor, p_hash=fingerprint)
    if job['status'] == 'sent':
        return
    if job['status'] != 'claimed':
        raise FeedbackUnavailable('Message awaiting retry')
    token = job['token']
    try:
        response = job.get('response')
        if response is None:
            state = dict(job.get('state') or {})
            lang = state.get('language', 'sw')
            text = msg.get('text', {}).get('body', '')
            feedback = parse_feedback(text) if msg.get('type') == 'text' else None
            interaction = None
            if feedback:
                context_id = (msg.get('context') or {}).get('id')
                target = (rpc(store, 'feedback_target', p_actor=actor, p_outbound=context_id)
                          if context_id else job.get('latest_interaction'))
                if target:
                    store.record_feedback(message_id=mid, interaction_id=target, actor=actor,
                                          rating=feedback[0], outcome=feedback[1])
                    if lang == 'en':
                        response = 'Feedback saved for review. What happened after you tried the advice? Reply again with the same feedback label, a colon, and the outcome.'
                    else:
                        response = 'Maoni yamehifadhiwa kwa ukaguzi. Ulipopima ushauri, nini kilitokea? Jibu tena kwa alama ileile, koloni, na matokeo.'
                    if feedback[1]:
                        response = ('Feedback and outcome saved for review.' if lang == 'en'
                                    else 'Maoni na matokeo yamehifadhiwa kwa ukaguzi.')
                else:
                    response = ('Please reply to the original advice message so I can match your feedback.' if lang == 'en'
                                else 'Tafadhali jibu ujumbe wa ushauri wa awali ili niunganishe maoni yako.')
            elif msg.get('type') != 'text':
                response = 'Please send a text question. / Tafadhali tuma swali kwa maandishi.'
            else:
                if not text or len(text) > 8000:
                    raise ValueError('Invalid message length')
                # Preserve an established language on numeric follow-ups.
                state = apply_message(text, state)
                if text.strip().replace(',', '').replace('.', '').isdigit():
                    state['language'] = lang
                response = reply_for(text, state)
                interaction = {'question': text, 'recommendation': response,
                               'language': state.get('language', lang), 'answer_version': 'feedback-v1',
                               'crop': 'maize', 'location': state.get('location'), 'problem': 'maize_sale'}
                if state.get('stage') == 'complete':
                    response += ('\n\nReply 👍 Helpful / 👎 Wrong / Still problem. Add : and what happened.'
                                 if state.get('language') == 'en' else
                                 '\n\nJibu 👍 Imesaidia / 👎 Si sahihi / Bado tatizo. Ongeza : na matokeo.')
            rpc(store, 'stage_message', p_id=mid, p_token=token, p_response=response,
                p_state=state, p_interaction=interaction)
        result = send(phone, response)
        outbound = (result.get('messages') or [{}])[0].get('id')
        if not outbound:
            raise FeedbackUnavailable('Message delivery was not confirmed')
        rpc(store, 'finish_message', p_id=mid, p_token=token, p_outbound=outbound)
    except Exception:
        # Preserve staged answer for delivery retry; never acknowledge lost work.
        try:
            rpc(store, 'release_message', p_id=mid, p_token=token)
        except Exception:
            pass
        raise


def process_payload(payload, store, secret, apply_message, reply_for, send):
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            for msg in change.get('value', {}).get('messages', []):
                process_message(msg, store, secret, apply_message, reply_for, send)
