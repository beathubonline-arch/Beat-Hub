"""BeatHub Fan Passport: pure, payment-provider-independent status calculation.

Integrate only with the canonical completed order ledger, after settlement validation.
This module intentionally performs no database writes or authentication.
"""
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Purchase:
    order_id: str
    status: str
    buyer_id: Optional[str] = None
    buyer_email: Optional[str] = None
    refunded: bool = False
    artist_id: Optional[str] = None


TIERS = ((25, "BeatHub Legend"), (10, "BeatHub Insider"), (3, "Sound Collector"), (1, "Verified Supporter"))


def passport_for_buyer(purchases: Iterable[Purchase], *, buyer_id: Optional[str] = None,
                       verified_email: Optional[str] = None) -> dict:
    """Count each eligible order once; email may only be passed after ownership verification.

    Guest orders can be claimed with verified_email. The caller must verify email
    possession before passing it; a public request parameter is never sufficient.
    """
    if not buyer_id and not verified_email:
        raise ValueError("Authenticated buyer_id or verified_email required")
    email = verified_email.strip().casefold() if verified_email else None
    seen = set()
    artists = set()
    for p in purchases:
        if p.status.strip().upper() not in {"PAID", "COMPLETED", "SUCCEEDED"} or p.refunded:
            continue
        owned = (bool(buyer_id) and p.buyer_id == buyer_id) or (
            bool(email) and bool(p.buyer_email) and p.buyer_email.strip().casefold() == email
        )
        if not owned or not p.order_id or p.order_id in seen:
            continue
        seen.add(p.order_id)
        if p.artist_id:
            artists.add(p.artist_id)
    count = len(seen)
    tier = next((name for minimum, name in TIERS if count >= minimum), None)
    return {"purchase_count": count, "artists_supported": len(artists),
            "badge": tier, "verified_supporter": count > 0}
