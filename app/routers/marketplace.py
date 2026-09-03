from __future__ import annotations

from typing import Optional
from types import SimpleNamespace

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
    """Normalize Track.content_type so legacy/enum representations still classify correctly."""
    value = getattr(track, "content_type", None)
    value = getattr(value, "value", value)
    raw = str(value or "beat").strip().lower()
    if raw in {"trackcontenttype.beat", "trackcontenttype_beat", "beat"}:
        return "beat"
    if raw in {"trackcontenttype.track", "trackcontenttype_track", "track", "song"}:
        return "track"
    # Older records may have an empty/unknown content type. Tracks historically
    # represented beats, so keep them visible in the beat marketplace.
    return "beat"


def _public_tracks(db: Session) -> list[Track]:
    rows = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).all()
    return [track for track in rows if _track_is_public(track)]


def _producer_cards(db: Session, beat_tracks: list[Track], limit: int = 12) -> list[dict]:
    counts: dict[str, int] = {}
    for track in beat_tracks:
        profile_id = str(getattr(track, "creator_profile_id", "") or "")
        if profile_id:
            counts[profile_id] = counts.get(profile_id, 0) + 1
    if not counts:
        return []
    profiles = db.query(Profile).filter(Profile.id.in_(list(counts.keys()))).order_by(Profile.stage_name.asc()).all()
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
        result.append({
            "profile": profile,
            "name": getattr(profile, "stage_name", None) or "BeatHub Producer",
            "slug": getattr(profile, "slug", None),
            "store_url": f"/store/{profile.slug}" if getattr(profile, "slug", None) else None,
            "beat_count": count,
            "avatar_url": avatar_url,
        })
    return result[:limit]


def _merchandise(db: Session, limit: int = 120) -> list[dict]:
    try:
        rows = db.execute(text(
            f"SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at "
            f"FROM {MERCH_TABLE} ORDER BY created_at DESC LIMIT {int(limit)}"
        )).mappings().all()
    except Exception:
        return []
    profile_ids = {str(row["creator_profile_id"]) for row in rows if row.get("creator_profile_id")}
    profiles = {str(profile.id): profile for profile in db.query(Profile).filter(Profile.id.in_(list(profile_ids))).all()} if profile_ids else {}
    result = []
    for row in rows:
        item = dict(row)
        owner = profiles.get(str(item.get("creator_profile_id")))
        item["creator"] = owner
        item["creator_name"] = getattr(owner, "stage_name", None) or "BeatHub Creator"
        item["creator_slug"] = getattr(owner, "slug", None)
        item["creator_store_url"] = f"/store/{owner.slug}" if owner and getattr(owner, "slug", None) else None
        try:
            item["image_url"] = media_url(item.get("image_path")) if item.get("image_path") else None
        except Exception:
            item["image_url"] = None
        result.append(item)
    return result


def _merch_collections(merch: list[dict], limit: int = 12) -> tuple[list[dict], list[dict]]:
    """Return creator merch collections and standalone tees separately.

    Creators with two or more merch items are represented by one collection card;
    creators with a single item keep that item as a normal product card.
    """
    grouped: dict[str, dict] = {}
    for item in merch:
        creator_id = str(item.get("creator_profile_id") or "")
        if not creator_id:
            continue
        group = grouped.setdefault(creator_id, {
            "creator": item.get("creator"),
            "creator_name": item.get("creator_name") or "BeatHub Creator",
            "creator_slug": item.get("creator_slug"),
            "store_url": item.get("creator_store_url"),
            "items": [],
        })
        group["items"].append(item)

    collections: list[object] = []
    standalone: list[dict] = []
    for group in grouped.values():
        items = group["items"]
        if len(items) >= 2:
            collections.append(SimpleNamespace(
                **group,
                item_count=len(items),
                preview_items=items[:4],
                cover_image_url=next((x.get("image_url") for x in items if x.get("image_url")), None),
            ))
        else:
            standalone.extend(items)

    collections.sort(key=lambda x: max((i.get("created_at") for i in x.items if i.get("created_at")), default=""), reverse=True)
    standalone.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return collections[:limit], standalone[:limit]


def _album_cards(albums: list[Album]) -> list[dict]:
    cards = []
    for album in albums:
        artwork_url = None
        stored = getattr(album, "artwork_path", None)
        if stored:
            try:
                artwork_url = media_url(stored)
            except Exception:
                artwork_url = None
        profile = getattr(album, "creator_profile", None)
        cards.append({
            "album": album,
            "title": getattr(album, "title", None) or "Untitled project",
            "slug": getattr(album, "slug", None),
            "genre": getattr(album, "genre", None) or "Release",
            "artwork_url": artwork_url,
            "creator": getattr(profile, "stage_name", None) or "BeatHub Creator",
            "track_count": len(getattr(album, "album_tracks", []) or []),
        })
    return cards


def _hot_picks(beats: list[Track], tracks: list[Track], merch: list[dict]) -> list[dict]:
    picks: list[dict] = []
    for track in beats[:3]:
        item = _catalog_item(track)
        picks.append({"kind": "Beat", "title": item["title"], "creator": item["producer"], "price": item["price"], "image_url": item["artwork_url"], "url": item["url"]})
    for track in tracks[:2]:
        item = _catalog_item(track)
        picks.append({"kind": "Track", "title": item["title"], "creator": item["producer"], "price": item["price"], "image_url": item["artwork_url"], "url": item["url"]})
    for item in merch[:1]:
        picks.append({"kind": "Tee", "title": item.get("name") or "BeatHub Tee", "creator": item.get("creator_name") or "BeatHub Creator", "price": item.get("price") or 0, "image_url": item.get("image_url"), "url": f"/merch/{item.get('slug')}" if item.get("slug") else "/merch"})
    return picks[:6]


def _context(request: Request, user, beats: list[Track], tracks: list[Track], producers: list[dict], merch: list[dict]):
    merch_collections, standalone_merch = _merch_collections(merch)
    return {
        "request": request,
        "current_user": user,
        "user": user,
        "current_year": 2026,
        "beat_producers": producers,
        "beat_count": len(beats),
        "track_count": len(tracks),
        "merch_count": len(merch),
        "beat_preview": [_catalog_item(track) for track in beats[:4]],
        "track_preview": [_catalog_item(track) for track in tracks[:4]],
        "merchandise": merch[:4],
        "merch_collections": merch_collections,
        "standalone_merch": standalone_merch,
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
    return templates.TemplateResponse(request, "marketplace.html", _context(request, current_user, beats, tracks, producers, merch))


@router.get("/marketplace/producers")
def marketplace_producers(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    _, beats, _, producers, _ = _load(request, db, current_user)
    return templates.TemplateResponse(request, "marketplace_producers.html", {
        "request": request, "current_user": current_user, "user": current_user, "current_year": 2026,
        "producers": producers, "producer_count": len(producers), "beat_count": len(beats),
    })


@router.get("/marketplace/albums")
def marketplace_albums(request: Request, db: Session = Depends(get_db), current_user=Depends(get_optional_user)):
    albums = db.query(Album).filter(Album.is_published.is_(True)).order_by(Album.created_at.desc()).limit(24).all()
    return templates.TemplateResponse(request, "marketplace_albums.html", {
        "request": request, "current_user": current_user, "user": current_user, "current_year": 2026,
        "albums": _album_cards(albums),
    })


@router.get("/marketplace/merch")
def marketplace_merch_alias():
    return RedirectResponse(url="/merch", status_code=307)


@router.get("/beats")
def marketplace_legacy_entry():
    """Keep the historic /beats marketplace entry without duplicating the beat catalog."""
    return RedirectResponse(url="/marketplace", status_code=307)
