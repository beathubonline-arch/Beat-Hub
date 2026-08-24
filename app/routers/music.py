from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
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


def _track_artwork_path(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "cover_art_path",
        "cover_url",
        "artwork_url",
        "image_url",
        "thumbnail_url",
        "cover_image",
        "artwork",
        "image",
        default=None,
    )

    if not value:
        return None

    return str(value)


def _track_audio(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "audio_url",
        "preview_url",
        "file_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        "audio_path",
        "file_path",
        default=None,
    )

    if not value:
        return None

    return str(value)


def _producer_object(track: Track) -> Any:
    return _model_value(
        track,
        "producer",
        "creator",
        "owner",
        "user",
        default=None,
    )


def _producer_name(track: Track) -> str:
    producer = _producer_object(track)

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


def _producer_slug(track: Track) -> Optional[str]:
    producer = _producer_object(track)

    if producer is not None:
        value = _model_value(
            producer,
            "slug",
            "store_slug",
            "username",
            default=None,
        )

        if value:
            return str(value)

    value = _model_value(
        track,
        "producer_slug",
        "creator_slug",
        "store_slug",
        default=None,
    )

    return str(value) if value else None


def _producer_url(track: Track) -> Optional[str]:
    slug = _producer_slug(track)

    if not slug:
        return None

    return f"/producer/{slug}"


def _track_url(track: Track) -> str:
    slug = _model_value(track, "slug", default=None)
    track_id = _model_value(track, "id", default=None)

    if slug:
        return f"/p/{slug}"

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


def _track_artwork_url(track: Track) -> Optional[str]:
    path = _track_artwork_path(track)

    if not path:
        return None

    value = path.strip()

    if not value:
        return None

    if value.startswith(("http://", "https://", "data:", "//")):
        return value

    if value.startswith("/"):
        return value

    slug = _model_value(track, "slug", default=None)

    if slug:
        return f"/track/{slug}/artwork"

    track_id = _model_value(track, "id", default=None)

    if track_id is not None:
        return f"/track/{track_id}/artwork"

    return None


def _track_audio_url(track: Track) -> Optional[str]:
    value = _track_audio(track)

    if not value:
        return None

    value = value.strip()

    if not value:
        return None

    if value.startswith(("http://", "https://", "data:", "//", "/")):
        return value

    slug = _model_value(track, "slug", default=None)

    if slug:
        return f"/track/{slug}/preview"

    track_id = _model_value(track, "id", default=None)

    if track_id is not None:
        return f"/track/{track_id}/preview"

    return None


def _track_text(track: Track) -> str:
    values = (
        _model_value(track, "title", "name", default=""),
        _model_value(track, "genre", "category", default=""),
        _model_value(track, "mood", default=""),
        _model_value(track, "description", "short_description", default=""),
        _model_value(track, "tags", default=""),
    )

    return " ".join(str(value) for value in values if value)


def _safe_local_path(value: str) -> Optional[Path]:
    raw = value.strip()

    if not raw:
        return None

    if raw.startswith(("http://", "https://", "r2://", "s3://")):
        return None

    candidates = []

    path = Path(raw)

    if path.is_absolute():
        candidates.append(path)

    candidates.extend(
        [
            Path(".") / raw.lstrip("/"),
            Path("app") / raw.lstrip("/"),
            Path("uploads") / raw.lstrip("/"),
            Path("media") / raw.lstrip("/"),
            Path("static") / raw.lstrip("/"),
        ]
    )

    for candidate in candidates:
        try:
            resolved = candidate.resolve()

            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            continue

    return None


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

    for field_name in (
        "is_published",
        "published",
        "is_public",
        "active",
    ):
        field = getattr(Track, field_name, None)

        if field is not None:
            query = query.filter(field.is_(True))
            break

    for field_name in (
        "created_at",
        "uploaded_at",
        "id",
    ):
        field = getattr(Track, field_name, None)

        if field is not None:
            query = query.order_by(field.desc())
            break

    return query.all()


def _catalog_item(track: Track) -> dict[str, Any]:
    title = str(
        _model_value(
            track,
            "title",
            "name",
            default="Untitled Beat",
        )
    )

    return {
        "track": track,
        "title": title,
        "producer": _producer_name(track),
        "producer_url": _producer_url(track),
        "price": _track_price(track),
        "artwork_url": _track_artwork_url(track),
        "audio_url": _track_audio_url(track),
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
            _model_value(
                track,
                "mood",
                default="",
            )
            or ""
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


def _catalog_filters(tracks: list[Track]) -> tuple[list[str], list[str]]:
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
            if _model_value(
                track,
                "genre",
                "category",
                default="",
            )
        },
        key=str.lower,
    )

    moods = sorted(
        {
            str(
                _model_value(
                    track,
                    "mood",
                    default="",
                )
            ).strip()
            for track in tracks
            if _model_value(
                track,
                "mood",
                default="",
            )
        },
        key=str.lower,
    )

    return genres, moods


def _find_track(
    db: Session,
    identifier: str,
) -> Optional[Track]:
    track = None

    slug_field = getattr(Track, "slug", None)

    if slug_field is not None:
        try:
            track = (
                db.query(Track)
                .filter(slug_field == identifier)
                .first()
            )
        except Exception:
            track = None

    if track is not None:
        return track

    try:
        track_id = int(identifier)
    except (TypeError, ValueError):
        return None

    id_field = getattr(Track, "id", None)

    if id_field is None:
        return None

    try:
        return (
            db.query(Track)
            .filter(id_field == track_id)
            .first()
        )
    except Exception:
        return None


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

        tracks = [
            track
            for track in tracks
            if _track_is_public(track)
        ]

    except Exception:
        logger.exception(
            "Unable to load BeatHub beat catalog"
        )
        tracks = []

    total = len(tracks)

    total_pages = max(
        1,
        math.ceil(total / per_page),
    )

    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page

    page_tracks = tracks[start:end]

    catalog = [
        _catalog_item(track)
        for track in page_tracks
    ]

    genres, moods = _catalog_filters(tracks)

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
            "catalog_start": (
                0
                if total == 0
                else start + 1
            ),
            "catalog_end": min(end, total),
            "title": "Beat Marketplace",
        },
    )


@router.get("/hot-picks")
def hot_picks(
    request: Request,
    q: str = Query(default="", max_length=100),
    genre: str = Query(default="", max_length=60),
    mood: str = Query(default="", max_length=60),
    current_user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    search = q.strip()
    genre = genre.strip()
    mood = mood.strip()

    try:
        tracks = _query_catalog(
            db,
            search,
            genre,
            mood,
            None,
            None,
        )

        tracks = [
            track
            for track in tracks
            if _track_is_public(track)
        ][:12]

    except Exception:
        logger.exception(
            "Unable to load BeatHub hot picks"
        )
        tracks = []

    catalog = [
        _catalog_item(track)
        for track in tracks
    ]

    return templates.TemplateResponse(
        request,
        "beats.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "tracks": tracks,
            "beats": tracks,
            "catalog": catalog,
            "total": len(tracks),
            "total_results": len(tracks),
            "page": 1,
            "track_page": 1,
            "per_page": 12,
            "track_per_page": 12,
            "total_pages": 1,
            "track_total_pages": 1,
            "genres": [],
            "moods": [],
            "query": search,
            "q": search,
            "genre": genre,
            "mood": mood,
            "min_price": None,
            "max_price": None,
            "has_previous": False,
            "has_next": False,
            "previous_page": 1,
            "next_page": 1,
            "catalog_start": (
                1 if tracks else 0
            ),
            "catalog_end": len(tracks),
            "title": "Hot Picks",
        },
    )


@router.get(
    "/track/{identifier}/artwork",
    name="track_artwork",
)
def track_artwork(
    identifier: str,
    db: Session = Depends(get_db),
):
    track = _find_track(
        db,
        identifier,
    )

    if track is None:
        return FileResponse(
            "app/static/images/beat-placeholder.png",
            media_type="image/png",
        )

    artwork = _track_artwork_path(track)

    if not artwork:
        placeholder = Path(
            "app/static/images/beat-placeholder.png"
        )

        if placeholder.exists():
            return FileResponse(
                placeholder,
                media_type="image/png",
            )

        return {
            "detail": "Artwork not available"
        }

    local_path = _safe_local_path(artwork)

    if local_path is None:
        return {
            "detail": "Artwork is externally hosted"
        }

    suffix = local_path.suffix.lower()

    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".avif": "image/avif",
    }

    return FileResponse(
        local_path,
        media_type=media_types.get(
            suffix,
            "application/octet-stream",
        ),
    )


@router.get(
    "/track/{identifier}/preview",
    name="track_preview",
)
def track_preview(
    identifier: str,
    db: Session = Depends(get_db),
):
    track = _find_track(
        db,
        identifier,
    )

    if track is None:
        return {
            "detail": "Track not found"
        }

    audio = _track_audio(track)

    if not audio:
        return {
            "detail": "Preview not available"
        }

    local_path = _safe_local_path(audio)

    if local_path is not None:
        suffix = local_path.suffix.lower()

        media_types = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".opus": "audio/opus",
        }

        return FileResponse(
            local_path,
            media_type=media_types.get(
                suffix,
                "application/octet-stream",
            ),
        )

    return {
        "url": audio
    }


@router.get("/sessions")
def sessions_page(
    request: Request,
    current_user=Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "sessions.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
        },
    )
