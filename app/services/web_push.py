"""Best-effort browser Web Push delivery for BeatHub notifications."""
import json
import logging

from app.config import settings
from app.database import SessionLocal
from app.models.push_subscription import PushSubscription

logger = logging.getLogger("beathub.web_push")


def send_push_to_user(user_id, title, message, link=None):
    if not settings.web_push_enabled:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        logger.warning("pywebpush is unavailable; browser push is disabled")
        return 0

    db = SessionLocal()
    try:
        rows = db.query(PushSubscription).filter(PushSubscription.user_id == str(user_id)).all()
        sent = 0
        stale = []
        payload = json.dumps({"title": str(title)[:180], "body": str(message)[:500], "link": link or "/notifications"})
        for row in rows:
            subscription_info = {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": settings.VAPID_SUBJECT},
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    stale.append(row)
                else:
                    logger.warning("Web Push delivery failed for subscription %s: %s", row.id, exc)
            except Exception as exc:
                logger.warning("Web Push delivery failed for subscription %s: %s", row.id, exc)
        for row in stale:
            db.delete(row)
        if stale:
            db.commit()
        return sent
    finally:
        db.close()
