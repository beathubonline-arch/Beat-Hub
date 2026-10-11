import hashlib
import hmac
from distribution.partner import SandboxPartner

def test_sandbox_receipt_is_not_live():
    p = SandboxPartner("test-secret")
    receipt = p.deliver(15, idempotency_key="release-15-v1")
    assert receipt.accepted
    assert receipt.environment == "sandbox"
    assert receipt.partner == "sandbox"
    assert p.deliver(15, idempotency_key="release-15-v1") == receipt

def test_webhook_signature_rejects_forgery():
    p = SandboxPartner("test-secret")
    body = b'{"release":15}'
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert p.verify_callback(body, signature)
    assert not p.verify_callback(body, "bad-signature")
    assert not p.verify_callback(body + b"x", signature)
