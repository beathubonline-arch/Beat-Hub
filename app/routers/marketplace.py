from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, Track
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
    rows = (
        db.query(Track)
        .filter(Track.is_published.is_(True))
        .order_by(Track.created_at.desc())
        .all()
    )
    return [track for track in rows if _track_is_public(track)]


def _producer_cards(db: Session, beat_tracks: list[Track], limit: int = 12) -> list[dict]:
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
    return result[:limit]


def _merchandise(db: Session, limit: int = 12) -> list[dict]:
    try:
        rows = db.execute(
            text(
                f"SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at "
                f"FROM {MERCH_TABLE} ORDER BY created_at DESC LIMIT {int(limit)}"
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


def _hot_picks(beats: list[Track], tracks: list[Track], merch: list[dict]) -> list[dict]:
    picks: list[dict] = []
    for track in beats[:3]:
        item = _catalog_item(track)
        picks.append({
            "kind": "Beat",
            "title": item["title"],
            "creator": item["producer"],
            "price": item["price"],
            "image_url": item["artwork_url"],
            "url": item["url"],
        })
    for track in tracks[:2]:
        item = _catalog_item(track)
        picks.append({
            "kind": "Track",
            "title": item["title"],
            "creator": item["producer"],
            "price": item["price"],
            "image_url": item["artwork_url"],
            "url": item["url"],
        })
    for item in merch[:1]:
        picks.append({
            "kind": "Merch",
            "title": item.get("name") or "BeatHub Merch",
            "creator": item.get("creator_name") or "BeatHub Creator",
            "price": item.get("price") or 0,
            "image_url": item.get("image_url"),
            "url": f"/merch/{item.get('slug')}" if item.get("slug") else "/merch",
        })
    return picks[:6]


def _context(request: Request, user, beats: list[Track], tracks: list[Track], producers: list[dict], merch: list[dict]):
    beat_preview = [_catalog_item(track) for track in beats[:4]]
    track_preview = [_catalog_item(track) for track in tracks[:4]]
    return {
        "request": request,
        "current_user": user,
        "user": user,
        "current_year": 2026,
        "beat_producers": producers,
        "beat_count": len(beats),
        "track_count": len(tracks),
        "merch_count": len(merch),
        "beat_preview": beat_preview,
        "track_preview": track_preview,
        "merchandise": merch[:4],
        "hot_picks": _hot_picks(beats, tracks, merch),
    }


def _load(request: Request, db: Session, user: Optional[object]):
    public = _public_tracks(db)
    beats = [track for track in public if _track_type(track) == "beat"]
    tracks = [track for track in public if _track_type(track) == "track"]
    producers = _producer_cards(db, beats)
    merch = _merchandise(db)
    return public, beats, tracks, producers, merch


@router.get("/marketplace")
def marketplace(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    _, beats, tracks, producers, merch = _load(request, db, current_user)
    return templates.TemplateResponse(
        request,
        "marketplace.html",
        _context(request, current_user, beats, tracks, producers, merch),
    )


@router.get("/marketplace/producers")
def marketplace_producers(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    _, beats, _, producers, _ = _load(request, db, current_user)
    return templates.TemplateResponse(
        request,
        "marketplace_producers.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "producers": producers,
            "producer_count": len(producers),
            "beat_count": len(beats),
        },
    )


@router.get("/marketplace/albums")
def marketplace_albums(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    albums = (
        db.query(Album)
        .filter(Album.is_published.is_(True))
        .order_by(Album.created_at.desc())
        .limit(24)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "marketplace_albums.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "albums": albums,
        },
    )


@router.get("/marketplace/merch")
def marketplace_merch_alias():
    return RedirectResponse(url="/merch", status_code=307)


@router.get("/beats")
def marketplace_legacy_entry():
    """Keep the historic /beats marketplace entry without duplicating the beat catalog."""
    return RedirectResponse(url="/marketplace", status_code=307)
