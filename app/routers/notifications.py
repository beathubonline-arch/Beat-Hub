from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.notification import Notification
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


@router.post("/notifications/{notification_id}/read")
def notification_read(notification_id: str, current_user=Depends(require_user)):
    mark_read(current_user.id, notification_id)
    return RedirectResponse(url="/notifications", status_code=303)


@router.post("/notifications/read-all")
def notifications_read_all(current_user=Depends(require_user)):
    mark_all_read(current_user.id)
    return RedirectResponse(url="/notifications", status_code=303)
