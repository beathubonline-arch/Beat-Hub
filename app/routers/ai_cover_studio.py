"""Authenticated creator UI for generating and applying AI cover artwork."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track
from app.models.user import User
from app.services.higgsfield import HiggsfieldError, generate_and_store_cover, is_configured
from app.services.storage import media_url
from app.utils.deps import require_creator


router = APIRouter(tags=["creator-ai"])
templates = Jinja2Templates(directory="app/templates")
_events: dict[str, deque[float]] = defaultdict(deque)


def _serializer() -> URLSafeTimedSerializer:
    secret = str(settings.SESSION_SECRET or settings.SECRET_KEY or "beathub-development-session-secret-change-me")
    return URLSafeTimedSerializer(secret, salt="beathub-higgsfield-cover-v1")


def _tracks(db: Session, user: User):
    return (
        db.query(Track)
        .filter(Track.creator_profile_id == user.profile.id)
        .order_by(Track.created_at.desc())
        .all()
    )


def _allow_generation(user_id: str) -> bool:
    limit = max(1, min(int(getattr(settings, "HIGGSFIELD_GENERATIONS_PER_HOUR", 3) or 3), 20))
    now = time.monotonic()
    events = _events[str(user_id)]
    while events and events[0] <= now - 3600:
        events.popleft()
    if len(events) >= limit:
        return False
    events.append(now)
    return True


def _context(request: Request, db: Session, user: User, **extra):
    data = {
        "request": request,
        "current_user": user,
        "current_year": 2026,
        "tracks": _tracks(db, user),
        "higgsfield_ready": is_configured(),
        "generated_url": None,
        "apply_token": None,
        "selected_track_id": "",
        "direction": "",
    }
    data.update(extra)
    return data


@router.get("/dashboard/ai-cover-studio")
def cover_studio(request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    return templates.TemplateResponse(request, "ai_cover_studio.html", _context(request, db, user))


@router.post("/dashboard/ai-cover-studio/generate")
async def generate_cover(
    request: Request,
    track_id: str = Form(...),
    direction: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    track = (
        db.query(Track)
        .filter(Track.id == track_id, Track.creator_profile_id == user.profile.id)
        .first()
    )
    clean_direction = " ".join((direction or "").split())[:700]
    base = _context(request, db, user, selected_track_id=track_id, direction=clean_direction)
    if track is None:
        base["error"] = "Choose one of your own tracks."
        return templates.TemplateResponse(request, "ai_cover_studio.html", base, status_code=404)
    if not is_configured():
        base["error"] = "AI Cover Studio is not configured yet."
        return templates.TemplateResponse(request, "ai_cover_studio.html", base, status_code=503)
    if len(clean_direction) < 12:
        base["error"] = "Describe the visual direction in at least a few words."
        return templates.TemplateResponse(request, "ai_cover_studio.html", base, status_code=400)
    if not _allow_generation(str(user.id)):
        base["error"] = "You have reached the hourly generation limit. Please try again later."
        return templates.TemplateResponse(request, "ai_cover_studio.html", base, status_code=429)

    prompt = (
        f"Create premium square cover artwork for a Kenyan music marketplace. "
        f"Track: {track.title}. Genre: {track.genre or 'contemporary African music'}. "
        f"Creative direction: {clean_direction}. Professional, distinctive, release-ready composition, "
        "strong focal point, balanced lighting, rich detail, no logos, no watermarks, no readable text, "
        "no copyrighted characters or brand marks."
    )
    try:
        generated = await generate_and_store_cover(prompt)
    except HiggsfieldError as exc:
        base["error"] = str(exc)
        return templates.TemplateResponse(request, "ai_cover_studio.html", base, status_code=502)

    token = _serializer().dumps({
        "profile_id": str(user.profile.id),
        "track_id": str(track.id),
        "storage_path": generated.storage_path,
    })
    base.update({
        "success": "Your cover is ready. Preview it before applying it to the track.",
        "generated_url": generated.preview_url,
        "apply_token": token,
        "generated_track": track,
    })
    return templates.TemplateResponse(request, "ai_cover_studio.html", base)


@router.post("/dashboard/ai-cover-studio/apply")
def apply_cover(
    token: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    try:
        payload = _serializer().loads(token, max_age=3600)
    except BadData:
        return RedirectResponse("/dashboard/ai-cover-studio?error=That+cover+preview+has+expired.", status_code=303)

    if str(payload.get("profile_id")) != str(user.profile.id):
        return RedirectResponse("/dashboard/ai-cover-studio?error=That+cover+does+not+belong+to+you.", status_code=303)
    track = (
        db.query(Track)
        .filter(
            Track.id == str(payload.get("track_id") or ""),
            Track.creator_profile_id == user.profile.id,
        )
        .first()
    )
    storage_path = str(payload.get("storage_path") or "")
    if track is None or not storage_path or not media_url(storage_path):
        return RedirectResponse("/dashboard/ai-cover-studio?error=The+cover+could+not+be+applied.", status_code=303)

    track.cover_art_path = storage_path
    db.add(track)
    db.commit()
    return RedirectResponse("/dashboard?success=AI+cover+applied+successfully.#music", status_code=303)
