from __future__ import annotations

import logging
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.utils.deps import get_optional_user

logger = logging.getLogger("beathub.music")

router = APIRouter(tags=["music"])
templates = Jinja2Templates(directory="app/templates")


# ======================================================================
# SAFE MODEL HELPERS
# ======================================================================

def _model_value(
    obj: Any,
    *names: str,
    default: Any = None,
) -> Any:
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None

        if value is not None:
            return value

    return default


# ======================================================================
# PUBLIC / PRICE
# ======================================================================

def _track_is_public(track: Track) -> bool:
    """
    Keep compatibility with the current Track model while also
    supporting older field names if they still exist.
    """
    for field_name in (
        "is_published",
        "published",
        "is_public",
        "active",
    ):
        if hasattr(track, field_name):
            try:
                value = getattr(track, field_name, None)

                if value is False:
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


# ======================================================================
# ARTWORK
# ======================================================================

def _track_artwork(track: Track) -> Optional[str]:
    """
    The current Track model stores artwork in cover_art_path.

    If it is already a public URL, use it directly.

    If it is a local/relative file path, use the existing
    /track/{identifier}/artwork endpoint from pages.py.

    This prevents the browser from trying to load a filesystem path
    directly.
    """
    value = _model_value(
        track,
        "cover_art_url",
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

    value = str(value).strip()

    if not value:
        return None

    # Already browser-accessible.
    if value.startswith(
        (
            "http://",
            "https://",
            "//",
            "data:",
        )
    ):
        return value

    # Already an application URL.
    if value.startswith("/"):
        return value

    # Local/relative stored artwork.
    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return f"/track/{slug}/artwork"

    track_id = _model_value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}/artwork"

    return None


# ======================================================================
# AUDIO PREVIEW
# ======================================================================

def _track_audio(track: Track) -> Optional[str]:
    """
    Uses the current Track model's preview_file_path first.

    The existing /track/{identifier}/preview endpoint in pages.py
    handles local files and redirects public URLs.
    """
    value = _model_value(
        track,
        "preview_file_path",
        "preview_url",
        "audio_url",
        "audio_file_path",
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

    value = str(value).strip()

    if not value:
        return None

    if value.startswith(
        (
            "http://",
            "https://",
            "//",
            "data:",
        )
    ):
        return value

    if value.startswith("/"):
        return value

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return f"/track/{slug}/preview"

    track_id = _model_value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}/preview"

    return None


# ======================================================================
# PRODUCER
# ======================================================================

def _producer_object(track: Track) -> Any:
    return _model_value(
        track,
        "producer",
        "creator",
        "owner",
        "user",
        "creator_profile",
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


def _producer_id(track: Track) -> Optional[str]:
    """
    Uses creator_profile_id from the current Track model.

    This lets the marketplace provide a producer-filtered view
    without inventing a new producer route.
    """
    value = _model_value(
        track,
        "creator_profile_id",
        "producer_id",
        "creator_id",
        "owner_id",
        default=None,
    )

    if value is None:
        producer = _producer_object(track)

        if producer is not None:
            value = _model_value(
                producer,
                "id",
                "profile_id",
                default=None,
            )

    return str(value) if value is not None else None


# ======================================================================
# TRACK URL
# ======================================================================

def _track_url(track: Track) -> str:
    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return f"/p/{slug}"

    track_id = _model_value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


# ======================================================================
# CATALOG TEXT
# ======================================================================

def _track_text(track: Track) -> str:
    values = (
        _model_value(
            track,
            "title",
            "name",
            default="",
        ),
        _model_value(
            track,
            "genre",
            "category",
            default="",
        ),
        _model_value(
            track,
            "mood",
            default="",
        ),
        _model_value(
            track,
            "description",
            "short_description",
            default="",
        ),
        _model_value(
            track,
            "tags",
            default="",
        ),
    )

    return " ".join(
        str(value)
        for value in values
        if value
    )


# ======================================================================
# DATABASE QUERY
# ======================================================================

def _query_catalog(
    db: Session,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
    producer: str = "",
):
    query = db.query(Track)

    # --------------------------------------------------------------
    # TEXT SEARCH
    # --------------------------------------------------------------

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
            field = getattr(
                Track,
                field_name,
                None,
            )

            if field is not None:
                conditions.append(
                    field.ilike(like)
                )

        if conditions:
            query = query.filter(
                or_(*conditions)
            )

    # --------------------------------------------------------------
    # GENRE
    # --------------------------------------------------------------

    if genre:
        field = getattr(
            Track,
            "genre",
            None,
        )

        if field is not None:
            query = query.filter(
                field.ilike(
                    f"%{genre}%"
                )
            )

    # --------------------------------------------------------------
    # MOOD
    # --------------------------------------------------------------

    if mood:
        field = getattr(
            Track,
            "mood",
            None,
        )

        if field is not None:
            query = query.filter(
                field.ilike(
                    f"%{mood}%"
                )
            )

    # --------------------------------------------------------------
    # PRICE RANGE
    # --------------------------------------------------------------

    if min_price is not None:
        field = getattr(
            Track,
            "price",
            None,
        )

        if field is not None:
            query = query.filter(
                field >= min_price
            )

    if max_price is not None:
        field = getattr(
            Track,
            "price",
            None,
        )

        if field is not None:
            query = query.filter(
                field <= max_price
            )

    # --------------------------------------------------------------
    # PRODUCER FILTER
    # --------------------------------------------------------------

    if producer:
        field = getattr(
            Track,
            "creator_profile_id",
            None,
        )

        if field is not None:
            query = query.filter(
                field == producer
            )

    # --------------------------------------------------------------
    # PUBLIC ONLY
    # --------------------------------------------------------------

    for field_name in (
        "is_published",
        "published",
        "is_public",
        "active",
    ):
        field = getattr(
            Track,
            field_name,
            None,
        )

        if field is not None:
            query = query.filter(
                field.is_(True)
            )
            break

    # --------------------------------------------------------------
    # NEWEST FIRST
    # --------------------------------------------------------------

    for field_name in (
        "created_at",
        "uploaded_at",
        "id",
    ):
        field = getattr(
            Track,
            field_name,
            None,
        )

        if field is not None:
            query = query.order_by(
                field.desc()
            )
            break

    return query.all()


# ======================================================================
# CATALOG ITEM
# ======================================================================

def _catalog_item(track: Track) -> dict[str, Any]:
    return {
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

        "producer_id": _producer_id(track),

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


# ======================================================================
# MARKETPLACE
# ======================================================================

@router.get(
    "/beats",
    name="beats",
)
def beats_catalog(
    request: Request,
    q: str = Query(
        default="",
        max_length=100,
    ),
    genre: str = Query(
        default="",
        max_length=60,
    ),
    mood: str = Query(
        default="",
        max_length=60,
    ),
    producer: str = Query(
        default="",
        max_length=100,
    ),
    min_price: Optional[float] = Query(
        default=None,
        ge=0,
    ),
    max_price: Optional[float] = Query(
        default=None,
        ge=0,
    ),
    page: int = Query(
        default=1,
        ge=1,
    ),
    per_page: int = Query(
        default=24,
        ge=6,
        le=48,
    ),
    current_user=Depends(
        get_optional_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    search = q.strip()
    genre = genre.strip()
    mood = mood.strip()
    producer = producer.strip()

    try:
        tracks = _query_catalog(
            db=db,
            search=search,
            genre=genre,
            mood=mood,
            min_price=min_price,
            max_price=max_price,
            producer=producer,
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
        math.ceil(
            total / per_page
        ),
    )

    if page > total_pages:
        page = total_pages

    start = (
        page - 1
    ) * per_page

    end = start + per_page

    page_tracks = tracks[
        start:end
    ]

    catalog = [
        _catalog_item(track)
        for track in page_tracks
    ]

    # --------------------------------------------------------------
    # FILTER OPTIONS
    # --------------------------------------------------------------

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

    # --------------------------------------------------------------
    # PRODUCER DISCOVERY
    # --------------------------------------------------------------

    producer_map: dict[str, dict[str, Any]] = {}

    for track in tracks:
        pid = _producer_id(track)

        if not pid:
            continue

        if pid not in producer_map:
            producer_map[pid] = {
                "id": pid,
                "name": _producer_name(track),
                "count": 0,
            }

        producer_map[pid]["count"] += 1

    producers = sorted(
        producer_map.values(),
        key=lambda item: (
            -item["count"],
            item["name"].lower(),
        ),
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

            "producers": producers,
            "selected_producer": producer,

            "query": search,
            "q": search,

            "genre": genre,
            "mood": mood,

            "min_price": min_price,
            "max_price": max_price,

            "has_previous": page > 1,
            "has_next": page < total_pages,

            "previous_page": max(
                1,
                page - 1,
            ),

            "next_page": min(
                total_pages,
                page + 1,
            ),

            "catalog_start": (
                0
                if total == 0
                else start + 1
            ),

            "catalog_end": min(
                end,
                total,
            ),
        },
    )


# ======================================================================
# HOT PICKS
# ======================================================================

@router.get(
    "/hot-picks"
)
def hot_picks(
    request: Request,
    q: str = Query(
        default="",
        max_length=100,
    ),
    genre: str = Query(
        default="",
        max_length=60,
    ),
    mood: str = Query(
        default="",
        max_length=60,
    ),
    current_user=Depends(
        get_optional_user
    ),
    db: Session = Depends(
        get_db
    ),
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
            min_price=None,
            max_price=None,
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

            "producers": [],
            "selected_producer": "",

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
                1
                if tracks
                else 0
            ),

            "catalog_end": len(
                tracks
            ),

            "title": "Hot Picks",
        },
    )


# ======================================================================
# SESSIONS
# ======================================================================

@router.get(
    "/sessions"
)
def sessions_page(
    request: Request,
    current_user=Depends(
        get_optional_user
    ),
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
