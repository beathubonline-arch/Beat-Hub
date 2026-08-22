from __future__ import annotations

import logging
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.track import Track
from app.utils.deps import get_optional_user

logger = logging.getLogger("beathub.music")

router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


def _model_value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value is not None:
            return value
    return default


def _track_is_public(track: Track) -> bool:
    """
    Keep catalog visibility compatible with the different Track schemas
    used by BeatHub versions.

    If a model has an explicit published/public/active field, respect it.
    Otherwise the track is treated as visible.
    """
    for field in ("is_published", "published", "is_public", "active"):
        if hasattr(track, field):
            value = getattr(track, field, None)
            if value is False:
                return False
    return True


def _track_text(track: Track) -> str:
    parts = [
        _model_value(track, "title", "name", default=""),
        _model_value(track, "genre", "category", default=""),
        _model_value(track, "mood", default=""),
        _model_value(track, "description", "short_description", default=""),
    ]
    return " ".join(str(x) for x in parts if x)


def _track_price(track: Track) -> float:
    raw = _model_value(
        track,
        "price",
        "amount",
        "non_exclusive_price",
        "lease_price",
        default=0,
    )
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _track_artwork(track: Track) -> Optional[str]:
    return _model_value(
        track,
        "cover_url",
        "artwork_url",
        "image_url",
        "thumbnail_url",
        "cover_image",
        "artwork",
        "image",
        default=None,
    )


def _track_audio(track: Track) -> Optional[str]:
    return _model_value(
        track,
        "audio_url",
        "preview_url",
        "file_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        default=None,
    )


def _producer_name(track: Track) -> str:
    producer = (
        _model_value(track, "producer", "creator", "owner", default=None)
    )

    if producer is not None:
        name = _model_value(
            producer,
            "display_name",
            "stage_name",
            "artist_name",
            "username",
            "name",
            default=None,
        )
        if name:
            return str(name)

    direct = _model_value(
        track,
        "producer_name",
        "creator_name",
        "artist_name",
        "username",
        default=None,
    )

    return str(direct) if direct else "BeatHub Creator"


def _track_url(track: Track) -> str:
    slug = _model_value(track, "slug", default=None)
    track_id = _model_value(track, "id", default=None)

    if slug:
        return f"/p/{slug}"

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


def _query_catalog(
    db: Session,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
) -> list[Track]:
    query = db.query(Track)

    if search:
        like = f"%{search}%"
        conditions = []

        for field_name in (
            "title",
            "genre",
            "mood",
            "description",
            "slug",
        ):
            field = getattr(Track, field_name, None)
            if field is not None:
                conditions.append(field.ilike(like))

        if conditions:
            query = query.filter(or_(*conditions))

    if genre:
        field = getattr(Track, "genre", None)
        if field is not None:
            query = query.filter(field.ilike(f"%{genre}%"))

    if mood:
        field = getattr(Track, "mood", None)
        if field is not None:
            query = query.filter(field.ilike(f"%{mood}%"))

    if min_price is not None:
        field = getattr(Track, "price", None)
        if field is not None:
            query = query.filter(field >= min_price)

    if max_price is not None:
        field = getattr(Track, "price", None)
        if field is not None:
            query = query.filter(field <= max_price)

    for field_name in ("is_published", "published", "is_public", "active"):
        field = getattr(Track, field_name, None)
        if field is not None:
            query = query.filter(field.is_(True))
            break

    for field_name in ("created_at", "uploaded_at", "id"):
        field = getattr(Track, field_name, None)
        if field is not None:
            query = query.order_by(field.desc())
            break

    return query.all()


@router.get("/beats", name="beats")
def beats_catalog(
    request: Request,
    q: str = Query(default="", max_length=100),
    genre: str = Query(default="", max_length=60),
    mood: str = Query(default="", max_length=60),
    min_price: Optional[float] = Query(default=None, ge=0),
    max_price: Optional[float] = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=24, ge=6, le=60),
    current_user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Dedicated BeatHub beat marketplace.

    /beats now loads the beat catalog directly instead of sending the user
    through the generic site search route.
    """
    search = q.strip()
    genre = genre.strip()
    mood = mood.strip()

    try:
        tracks = _query_catalog(
            db=db,
            search=search,
            genre=genre,
            mood=mood,
            min_price=min_price,
            max_price=max_price,
        )
    except Exception:
        logger.exception("Unable to load beat catalog")
        tracks = []

    # Some legacy databases contain tracks that the ORM cannot filter with
    # the same visibility fields. Apply a final safe visibility check here.
    tracks = [
        track
        for track in tracks
        if _track_is_public(track)
    ]

    total = len(tracks)
    total_pages = max(1, math.ceil(total / per_page))

    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page
    page_tracks = tracks[start:end]

    catalog = []

    for track in page_tracks:
        catalog.append(
            {
                "track": track,
                "title": str(
                    _model_value(
                        track,
                        "title",
                        "name",
                        default="Untitled Beat",
                    )
                ),
                "producer": _producer_name(track),
                "price": _track_price(track),
                "artwork_url": _track_artwork(track),
                "audio_url": _track_audio(track),
                "url": _track_url(track),
                "genre": _model_value(
                    track,
                    "genre",
                    "category",
                    default="",
                ),
                "mood": _model_value(
                    track,
                    "mood",
                    default="",
                ),
                "bpm": _model_value(
                    track,
                    "bpm",
                    "tempo",
                    default="",
                ),
                "key": _model_value(
                    track,
                    "key",
                    "musical_key",
                    default="",
                ),
            }
        )

    genres = []
    moods = []

    for track in tracks:
        value = _model_value(
            track,
            "genre",
            "category",
            default=None,
        )
        if value and str(value) not in genres:
            genres.append(str(value))

        value = _model_value(
            track,
            "mood",
            default=None,
        )
        if value and str(value) not in moods:
            moods.append(str(value))

    genres.sort(key=str.lower)
    moods.sort(key=str.lower)

    query_values = {
        "q": search,
        "genre": genre,
        "mood": mood,
        "min_price": "" if min_price is None else min_price,
        "max_price": "" if max_price is None else max_price,
    }

    return templates.TemplateResponse(
        request,
        "beats.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "tracks": page_tracks,
            "beats": page_tracks,
            "catalog": catalog,
            "total": total,
            "total_results": total,
            "page": page,
            "track_page": page,
            "per_page": per_page,
            "track_per_page": per_page,
            "total_pages": total_pages,
            "track_total_pages": total_pages,
            "genres": genres,
            "moods": moods,
            "query": search,
            "q": search,
            "genre": genre,
            "mood": mood,
            "min_price": min_price,
            "max_price": max_price,
            "query_values": query_values,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "previous_page": max(1, page - 1),
            "next_page": min(total_pages, page + 1),
            "catalog_start": 0 if total == 0 else start + 1,
            "catalog_end": min(end, total),
        },
    )
