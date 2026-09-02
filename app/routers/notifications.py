from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import SessionLocal
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services.notifications import list_notifications, mark_all_read, mark_read, notification_context, unread_count
from app.utils.deps import require_user

router = APIRouter(tags=["notifications"])
templates = Jinja2Templates(directory="app/templates")


def _time_ago(value):
    if not value:
        return ""
    seconds = max(0, int((datetime.utcnow() - value).total_seconds()))
    if seconds < 60: return "just now"
    minutes = seconds // 60
    if minutes < 60: return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24: return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago" if days < 30 else value.strftime("%d %b %Y")


@router.get("/notifications")
def notifications_page(request: Request, current_user=Depends(require_user)):
    rows = list_notifications(current_user.id, 100)
    items = [notification_context(row) | {"time_ago": _time_ago(row.created_at)} for row in rows]
    return templates.TemplateResponse(request, "notifications.html", {"request": request, "current_user": current_user, "notifications": items, "unread_count": sum(1 for row in rows if not row.is_read)})


@router.get("/notifications/recent")
def recent_notifications(current_user=Depends(require_user)):
    rows = list_notifications(current_user.id, 8)
    return JSONResponse({"notifications": [notification_context(row) | {"time_ago": _time_ago(row.created_at)} for row in rows], "unread_count": unread_count(current_user.id)})


@router.get("/notifications/unread-count")
def notifications_unread_count(current_user=Depends(require_user)):
    return JSONResponse({"unread_count": unread_count(current_user.id)})


@router.get("/notifications/push/vapid-public-key")
def push_vapid_public_key(current_user=Depends(require_user)):
    return JSONResponse({"enabled": settings.web_push_enabled, "public_key": settings.VAPID_PUBLIC_KEY if settings.web_push_enabled else ""})


@router.post("/notifications/push/subscribe")
def push_subscribe(payload: dict, current_user=Depends(require_user)):
    if not settings.web_push_enabled:
        return JSONResponse({"ok": False, "enabled": False}, status_code=503)
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth or len(endpoint) > 4000 or len(p256dh) > 1000 or len(auth) > 1000:
        return JSONResponse({"ok": False, "detail": "Invalid push subscription."}, status_code=400)

    db = SessionLocal()
    try:
        row = db.query(PushSubscription).filter(
            PushSubscription.user_id == str(current_user.id),
            PushSubscription.endpoint == endpoint,
        ).first()
        if row:
            row.p256dh = p256dh
            row.auth = auth
        else:
            db.add(PushSubscription(user_id=str(current_user.id), endpoint=endpoint, p256dh=p256dh, auth=auth))
        db.commit()
        return JSONResponse({"ok": True})
    except Exception:
        db.rollback()
        return JSONResponse({"ok": False, "detail": "Unable to save push subscription."}, status_code=500)
    finally:
        db.close()


@router.post("/notifications/push/unsubscribe")
def push_unsubscribe(payload: dict, current_user=Depends(require_user)):
    endpoint = str(payload.get("endpoint") or "").strip()
    if not endpoint:
        return JSONResponse({"ok": False, "detail": "Endpoint is required."}, status_code=400)
    db = SessionLocal()
    try:
        db.query(PushSubscription).filter(
            PushSubscription.user_id == str(current_user.id),
            PushSubscription.endpoint == endpoint,
        ).delete(synchronize_session=False)
        db.commit()
        return JSONResponse({"ok": True})
    finally:
        db.close()


@router.post("/notifications/{notification_id}/read")
def notification_read(notification_id: str, request: Request, current_user=Depends(require_user)):
    ok = mark_read(current_user.id, notification_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": ok})
    return RedirectResponse(url="/notifications", status_code=303)


@router.post("/notifications/read-all")
def notifications_read_all(request: Request, current_user=Depends(require_user)):
    count = mark_all_read(current_user.id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "marked": count})
    return RedirectResponse(url="/notifications", status_code=303)
