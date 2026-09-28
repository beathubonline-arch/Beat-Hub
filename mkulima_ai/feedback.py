"""Backend-only feedback storage. No automatic promotion or model training.

The WhatsApp webhook must verify Meta's signature before calling this module.
Use an existing server-side secret for HMAC actor IDs; never log raw phones/keys.
"""
import hashlib
import hmac
import json
import os
from urllib import error, parse, request


class FeedbackUnavailable(RuntimeError):
    pass


def actor_ref(phone, secret):
    if not secret or len(secret) < 32:
        raise ValueError("A server-side HMAC secret of at least 32 characters is required")
    normalized = ''.join(c for c in phone if c.isdigit())
    if not normalized:
        raise ValueError("Invalid actor")
    return hmac.new(secret.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def parse_feedback(text):
    """Explicit feedback commands; ordinary questions must not be misclassified."""
    label, _, outcome = text.strip().partition(':')
    rating = {
        '👍': 'helpful', 'helpful': 'helpful', 'imesaidia': 'helpful',
        '👍 helpful': 'helpful', '👍 imesaidia': 'helpful',
        '👎': 'wrong', 'wrong': 'wrong', 'si sahihi': 'wrong',
        '👎 wrong': 'wrong', '👎 si sahihi': 'wrong',
        'still problem': 'still_problem', 'still a problem': 'still_problem',
        'bado tatizo': 'still_problem',
    }.get(label.strip().lower())
    return (rating, outcome.strip() or None) if rating else None


class FeedbackStore:
    def __init__(self, url=None, key=None, opener=None):
        self.url = (url or os.getenv('SUPABASE_URL', '')).rstrip('/')
        self.key = key or os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
        self.opener = opener or request.urlopen
        parsed = parse.urlsplit(self.url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path):
            raise FeedbackUnavailable('Supabase HTTPS project URL is required')
        if not self.key:
            raise FeedbackUnavailable('Supabase backend credential is not configured')

    def _call(self, route, body=None, query=None, prefer=None):
        url = self.url + '/rest/v1/' + route
        if query:
            url += '?' + parse.urlencode(query)
        headers = {'apikey': self.key, 'Authorization': 'Bearer ' + self.key,
                   'Content-Type': 'application/json'}
        if prefer:
            headers['Prefer'] = prefer
        req = request.Request(url, headers=headers,
                              data=None if body is None else json.dumps(body).encode(),
                              method='GET' if body is None else 'POST')
        try:
            with self.opener(req, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except (error.URLError, TimeoutError, ValueError):
            # Do not propagate URLs, headers, upstream bodies or personal records.
            raise FeedbackUnavailable('Feedback storage request failed') from None

    def record_interaction(self, *, message_id, actor, question, recommendation,
                           language, answer_version, crop=None, location=None,
                           problem=None, confidence=None):
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError('Confidence must be unknown or between zero and one')
        row = dict(message_id=message_id, actor_ref=actor, question=question,
                   recommendation=recommendation, language=language,
                   answer_version=answer_version, crop=crop,
                   location_context=location, problem=problem, confidence=confidence)
        self._call('mkulima_interactions', row, {'on_conflict': 'message_id'},
                   'resolution=ignore-duplicates,return=minimal')
        rows = self._call('mkulima_interactions', query={
            'message_id': 'eq.' + message_id, 'actor_ref': 'eq.' + actor,
            'select': '*', 'limit': 1})
        if not rows or any(rows[0].get(k) != v for k, v in row.items()):
            raise ValueError('Interaction message ID conflicts with a stored record')
        return rows[0]['id']

    def record_feedback(self, *, message_id, interaction_id, actor, rating, outcome=None):
        if rating not in {'helpful', 'wrong', 'still_problem'}:
            raise ValueError('Unknown feedback rating')
        return self._call('rpc/mkulima_record_feedback', {
            'p_message_id': message_id, 'p_interaction_id': interaction_id,
            'p_actor_ref': actor, 'p_rating': rating, 'p_outcome': outcome})

    def retrieve(self, *, crop, region, language, problem):
        if not all((crop, region, language, problem)):
            return []
        return self._call('rpc/mkulima_retrieve_knowledge', {
            'p_crop': crop, 'p_region': region, 'p_language': language, 'p_problem': problem})
