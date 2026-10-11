"""Distribution partner boundary. Sandbox adapter is never proof of DSP delivery."""
from dataclasses import dataclass
from typing import Protocol
import hashlib
import hmac

@dataclass(frozen=True)
class DeliveryReceipt:
    partner: str
    external_release_id: str
    accepted: bool
    environment: str

class DistributionPartner(Protocol):
    def deliver(self, release_id: int, *, idempotency_key: str) -> DeliveryReceipt: ...
    def verify_callback(self, body: bytes, signature: str) -> bool: ...

class SandboxPartner:
    """For local integration tests only. Must never enable LIVE status."""
    def __init__(self, secret: str):
        if not secret:
            raise ValueError("Sandbox webhook secret required")
        self.secret = secret.encode()
        self.receipts: dict[str, DeliveryReceipt] = {}

    def deliver(self, release_id: int, *, idempotency_key: str) -> DeliveryReceipt:
        if not idempotency_key.strip():
            raise ValueError("Idempotency key required")
        if idempotency_key not in self.receipts:
            self.receipts[idempotency_key] = DeliveryReceipt(
                "sandbox", f"test-{release_id}", True, "sandbox")
        return self.receipts[idempotency_key]

    def verify_callback(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
