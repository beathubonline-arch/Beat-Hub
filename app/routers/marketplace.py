from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.models.profile import Profile
from app.routers.music import _catalog_item, _track_is_public
from app.services.storage import media_url
from app.utils.deps import get_optional_user

router = APIRouter(tags=["marketplace"])
templates = Jinja2Templates(directory="app/templates")
MERCH_TABLE = "beathub_merchandise"


def _track_type(track: Track) -> str:
    value = getattr(track, "content_type", None)
    return str(getattr(value, "value", value) or "beat").strip().lower()


def _public_tracks(db: Session) -> list[Track]:
    rows = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).all()
    return [track for track in rows if _track_is_public(track)]


def _producer_cards(db: Session, beat_tracks: list[Track]) -> list[dict]:
    counts: dict[str, int] = {}
    for track in beat_tracks:
        profile_id = str(getattr(track, "creator_profile_id", "") or "")
        if profile_id:
            counts[profile_id] = counts.get(profile_id, 0) + 1

    if not counts:
        return []

    profiles = (
        db.query(Profile)
        .filter(Profile.id.in_(list(counts.keys())))
        .order_by(Profile.stage_name.asc())
        .all()
    )
    result = []
    for profile in profiles:
        count = counts.get(str(profile.id), 0)
        if count <= 0:
            continue
        avatar_url = None
        stored = getattr(profile, "avatar_path", None)
        if stored:
            try:
                avatar_url = media_url(stored)
            except Exception:
                avatar_url = None
        result.append(
            {
                "profile": profile,
                "name": getattr(profile, "stage_name", None) or "BeatHub Producer",
                "slug": getattr(profile, "slug", None),
                "store_url": f"/store/{profile.slug}" if getattr(profile, "slug", None) else None,
                "beat_count": count,
                "avatar_url": avatar_url,
            }
        )
    return result[:12]


def _merchandise(db: Session) -> list[dict]:
    try:
        rows = db.execute(
            text(
                f"SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at "
                f"FROM {MERCH_TABLE} ORDER BY created_at DESC LIMIT 12"
            )
        ).mappings().all()
    except Exception:
        return []

    profile_ids = {str(row["creator_profile_id"]) for row in rows if row.get("creator_profile_id")}
    profiles = {
        str(profile.id): profile
        for profile in db.query(Profile).filter(Profile.id.in_(list(profile_ids))).all()
    } if profile_ids else {}

    result = []
    for row in rows:
        item = dict(row)
        owner = profiles.get(str(item.get("creator_profile_id")))
        item["creator"] = owner
        item["creator_name"] = getattr(owner, "stage_name", None) or "BeatHub Creator"
        item["creator_store_url"] = f"/store/{owner.slug}" if owner and getattr(owner, "slug", None) else None
        try:
            item["image_url"] = media_url(item.get("image_path")) if item.get("image_path") else None
        except Exception:
            item["image_url"] = None
        result.append(item)
    return result


def _context(request: Request, user, beats: list[Track], tracks: list[Track], producers: list[dict], merch: list[dict]):
    return {
        "request": request,
        "current_user": user,
        "user": user,
        "current_year": 2026,
        "beat_producers": producers,
        "beat_count": len(beats),
        "track_count": len(tracks),
        "merch_count": len(merch),
        "beat_preview": [_catalog_item(track) for track in beats[:8]],
        "track_preview": [_catalog_item(track) for track in tracks[:8]],
        "merchandise": merch,
    }


def _render(request: Request, db: Session, user: Optional[object]):
    public = _public_tracks(db)
    beats = [track for track in public if _track_type(track) == "beat"]
    tracks = [track for track in public if _track_type(track) == "track"]
    producers = _producer_cards(db, beats)
    merch = _merchandise(db)
    return templates.TemplateResponse(request, "marketplace.html", _context(request, user, beats, tracks, producers, merch))


@router.get("/marketplace")
def marketplace(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    return _render(request, db, current_user)


@router.get("/beats")
def marketplace_legacy_entry(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    """Keep the old navigation URL, but make it open the canonical Marketplace."""
    return _render(request, db, current_user)
