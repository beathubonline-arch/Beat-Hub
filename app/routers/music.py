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
    for field in ("is_published", "published", "is_public", "active"):
        if hasattr(track, field):
            try:
                if getattr(track, field, None) is False:
                    return False
            except Exception:
                pass
    return True


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
    value = _model_value(
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
    return str(value) if value else None


def _track_audio(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "audio_url",
        "preview_url",
        "file_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        default=None,
    )
    return str(value) if value else None


def _producer_name(track: Track) -> str:
    producer = _model_value(
        track,
        "producer",
        "creator",
        "owner",
        default=None,
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


def _track_text(track: Track) -> str:
    values = (
        _model_value(track, "title", "name", default=""),
        _model_value(track, "genre", "category", default=""),
        _model_value(track, "mood", default=""),
        _model_value(track, "description", "short_description", default=""),
        _model_value(track, "tags", default=""),
    )
    return " ".join(str(value) for value in values if value)


def _query_catalog(
    db: Session,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
):
    query = db.query(Track)

    if search:
        like = f"%{search}%"
        conditions = []
        for field_name in (
            "title",
            "name",
            "genre",
            "mood",
            "description",
            "short_description",
            "tags",
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
    per_page: int = Query(default=24, ge=6, le=48),
    current_user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
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
        tracks = [track for track in tracks if _track_is_public(track)]
    except Exception:
        logger.exception("Unable to load BeatHub beat catalog")
        tracks = []

    total = len(tracks)
    total_pages = max(1, math.ceil(total / per_page))

    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page
    page_tracks = tracks[start:end]

    catalog = []
    for track in page_tracks:
        title = str(
            _model_value(track, "title", "name", default="Untitled Beat")
        )
        catalog.append(
            {
                "track": track,
                "title": title,
                "producer": _producer_name(track),
                "price": _track_price(track),
                "artwork_url": _track_artwork(track),
                "audio_url": _track_audio(track),
                "url": _track_url(track),
                "genre": str(
                    _model_value(
                        track,
                        "genre",
                        "category",
                        default="",
                    )
                    or ""
                ),
                "mood": str(
                    _model_value(track, "mood", default="") or ""
                ),
                "bpm": str(
                    _model_value(
                        track,
                        "bpm",
                        "tempo",
                        default="",
                    )
                    or ""
                ),
                "key": str(
                    _model_value(
                        track,
                        "key",
                        "musical_key",
                        default="",
                    )
                    or ""
                ),
                "description": str(
                    _model_value(
                        track,
                        "description",
                        "short_description",
                        default="",
                    )
                    or ""
                ),
            }
        )

    genres = sorted(
        {
            str(
                _model_value(
                    track,
                    "genre",
                    "category",
                    default="",
                )
            ).strip()
            for track in tracks
            if _model_value(track, "genre", "category", default="")
        },
        key=str.lower,
    )

    moods = sorted(
        {
            str(_model_value(track, "mood", default="")).strip()
            for track in tracks
            if _model_value(track, "mood", default="")
        },
        key=str.lower,
    )

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
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "previous_page": max(1, page - 1),
            "next_page": min(total_pages, page + 1),
            "catalog_start": 0 if total == 0 else start + 1,
            "catalog_end": min(end, total),
        },
    )
