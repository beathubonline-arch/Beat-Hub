"""Persistent in-app notification delivery for BeatHub."""
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.notification import Notification
from app.models.user import User, UserRole


def create_notification(user_id, event_key, type_, title, message, link=None):
    user_id = str(user_id or "").strip()
    event_key = str(event_key or "").strip()
    if not user_id or not event_key:
        return False
    db = SessionLocal()
    try:
        existing = db.query(Notification).filter(Notification.user_id == user_id, Notification.event_key == event_key).first()
        if existing:
            return False
        db.add(Notification(user_id=user_id, event_key=event_key, type=str(type_ or "general")[:60], title=str(title)[:180], message=str(message), link=(str(link)[:500] if link else None)))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def create_notifications(recipients, event_key, type_, title, message, link=None):
    created = 0
    for user_id in recipients or []:
        if create_notification(user_id, event_key, type_, title, message, link):
            created += 1
    return created


def notify_admins(event_key, type_, title, message, link=None):
    db = SessionLocal()
    try:
        ids = [row[0] for row in db.query(User.id).filter(User.role == UserRole.ADMIN, User.is_active.is_(True)).all()]
    finally:
        db.close()
    return create_notifications(ids, event_key, type_, title, message, link)


def mark_read(user_id, notification_id):
    db = SessionLocal()
    try:
        row = db.query(Notification).filter(Notification.id == str(notification_id), Notification.user_id == str(user_id)).first()
        if not row:
            return False
        row.is_read = True
        db.commit()
        return True
    finally:
        db.close()


def mark_all_read(user_id):
    db = SessionLocal()
    try:
        count = db.query(Notification).filter(Notification.user_id == str(user_id), Notification.is_read.is_(False)).update({Notification.is_read: True}, synchronize_session=False)
        db.commit()
        return int(count or 0)
    finally:
        db.close()


def unread_count(user_id):
    db = SessionLocal()
    try:
        return int(db.query(func.count(Notification.id)).filter(Notification.user_id == str(user_id), Notification.is_read.is_(False)).scalar() or 0)
    finally:
        db.close()


def list_notifications(user_id, limit=50):
    db = SessionLocal()
    try:
        return db.query(Notification).filter(Notification.user_id == str(user_id)).order_by(Notification.created_at.desc()).limit(max(1, min(int(limit), 100))).all()
    finally:
        db.close()


def notification_context(notification):
    return {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "link": notification.link,
        "is_read": bool(notification.is_read),
        "created_at": notification.created_at.isoformat() if notification.created_at else datetime.utcnow().isoformat(),
    }
