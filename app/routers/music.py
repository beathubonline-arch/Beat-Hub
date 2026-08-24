from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track
from app.services.storage import media_url, storage_exists
from app.utils.deps import get_optional_user


logger = logging.getLogger("beathub.music")

router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


# ============================================================
# SAFE MODEL HELPERS
# ============================================================

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


# ============================================================
# TRACK VISIBILITY
# ============================================================

def _track_is_public(track: Track) -> bool:
    if getattr(track, "is_published", True) is False:
        return False

    sales_model = getattr(track, "sales_model", None)
    sales_value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    if (
        str(sales_value or "").strip().lower()
        == "exclusive"
    ):
        if getattr(track, "is_sold", False):
            return False

    return True


# ============================================================
# PRICE
# ============================================================

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


# ============================================================
# ARTWORK STORAGE
# ============================================================

def _track_storage_path(
    track: Track,
) -> Optional[str]:
    value = _model_value(
        track,
        "cover_art_path",
        "cover_art_url",
        "cover_url",
        "artwork_url",
        "image_url",
        "thumbnail_url",
        "cover_image",
        "artwork",
        "image",
        default=None,
    )

    if value is None:
        return None

    value = str(value).strip()

    return value or None


def _track_artwork(
    track: Track,
) -> Optional[str]:
    """
    Return a browser-safe artwork URL.

    Artwork is served through:

        /track/{slug}/artwork

    rather than exposing the raw storage path.

    This supports:
        - local media
        - normal HTTP URLs
        - Cloudflare R2 paths
        - S3-compatible paths
    """

    stored = _track_storage_path(track)

    if not stored:
        return None

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return f"/track/{slug}/artwork"

    try:
        return media_url(stored)
    except Exception:
        logger.exception(
            "Unable to create artwork URL"
        )
        return None


# ============================================================
# AUDIO STORAGE
# ============================================================

def _track_audio_storage_path(
    track: Track,
) -> Optional[str]:
    """
    Locate the audio used for browser previews.

    BeatHub historically used several possible fields.
    Most importantly, uploaded tracks currently store
    their actual audio in:

        audio_file_path

    Therefore audio_file_path MUST be the final fallback.

    Preview does not expose the paid download separately.
    It only streams the stored audio through the preview
    endpoint.
    """

    value = _model_value(
        track,

        # Explicit preview fields first
        "preview_file_path",
        "audio_preview_path",
        "audio_preview_url",
        "preview_url",

        # Other possible public audio fields
        "audio_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",

        default=None,
    )

    if value is not None:
        value = str(value).strip()

        if value:
            return value

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Uploaded tracks in BeatHub use audio_file_path.
    #
    # The previous version stopped before this field,
    # causing:
    #
    # GET /track/<slug>/preview -> 404
    #
    # even though the track had uploaded audio.
    # --------------------------------------------------------

    uploaded_audio = _model_value(
        track,
        "audio_file_path",
        default=None,
    )

    if uploaded_audio is None:
        return None

    uploaded_audio = str(
        uploaded_audio
    ).strip()

    return uploaded_audio or None


def _track_audio(
    track: Track,
) -> Optional[str]:
    """
    Return the browser-safe preview endpoint.

    The actual audio remains behind the BeatHub preview
    route so the template never needs to know whether the
    file is local or in R2.
    """

    stored = _track_audio_storage_path(track)

    if not stored:
        return None

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return f"/track/{slug}/preview"

    try:
        return media_url(stored)
    except Exception:
        logger.exception(
            "Unable to create preview URL"
        )
        return None


# ============================================================
# PRODUCER INFORMATION
# ============================================================

def _producer_name(
    track: Track,
) -> str:

    profile = _model_value(
        track,
        "creator_profile",
        "producer",
        "creator",
        "owner",
        default=None,
    )

    if profile is not None:

        name = _model_value(
            profile,
            "stage_name",
            "display_name",
            "artist_name",
            "username",
            "name",
            default=None,
        )

        if name:
            return str(name)

        user = _model_value(
            profile,
            "user",
            default=None,
        )

        if user is not None:

            name = _model_value(
                user,
                "name",
                "full_name",
                "username",
                "email",
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

    return (
        str(direct)
        if direct
        else "BeatHub Creator"
    )


def _producer_store_url(
    track: Track,
) -> Optional[str]:

    profile = _model_value(
        track,
        "creator_profile",
        "producer",
        "creator",
        "owner",
        default=None,
    )

    slug = (
        _model_value(
            profile,
            "slug",
            default=None,
        )
        if profile
        else None
    )

    if slug:
        return f"/store/{slug}"

    return None


# ============================================================
# TRACK URL
# ============================================================

def _track_url(
    track: Track,
) -> str:

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    track_id = _model_value(
        track,
        "id",
        default=None,
    )

    if slug:
        return f"/track/{slug}"

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


# ============================================================
# CATALOG QUERY
# ============================================================

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
            "genre",
            "description",
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

    mood_field = getattr(
        Track,
        "mood",
        None,
    )

    if mood and mood_field is not None:
        query = query.filter(
            mood_field.ilike(
                f"%{mood}%"
            )
        )

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

    published = getattr(
        Track,
        "is_published",
        None,
    )

    if published is not None:
        query = query.filter(
            published.is_(True)
        )

    created = getattr(
        Track,
        "created_at",
        None,
    )

    if created is not None:

        query = query.order_by(
            created.desc()
        )

    else:

        query = query.order_by(
            Track.id.desc()
        )

    return query.all()


# ============================================================
# CATALOG ITEM
# ============================================================

def _catalog_item(
    track: Track,
) -> dict:

    title = str(
        _model_value(
            track,
            "title",
            "name",
            default="Untitled Beat",
        )
        or "Untitled Beat"
    )

    return {
        "track": track,

        "title": title,

        "producer": _producer_name(
            track
        ),

        "producer_store_url":
            _producer_store_url(
                track
            ),

        "price": _track_price(
            track
        ),

        "artwork_url":
            _track_artwork(
                track
            ),

        "audio_url":
            _track_audio(
                track
            ),

        "url":
            _track_url(
                track
            ),

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


# ============================================================
# CATALOG FILTERS
# ============================================================

def _catalog_filters(
    tracks: list[Track],
) -> tuple[list[str], list[str]]:

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


# ============================================================
# BEATS PAGE CONTEXT
# ============================================================

def _beats_context(
    request: Request,
    current_user,
    tracks: list[Track],
    catalog: list[dict],
    total: int,
    page: int,
    per_page: int,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
    title: str = "Find Your Sound",
) -> dict:

    total_pages = max(
        1,
        math.ceil(
            total / per_page
        ),
    )

    start = (
        page - 1
    ) * per_page

    end = (
        start + per_page
    )

    genres, moods = _catalog_filters(
        tracks
    )

    return {
        "request": request,

        "current_user":
            current_user,

        "user":
            current_user,

        "current_year":
            2026,

        "tracks":
            tracks,

        "beats":
            tracks,

        "catalog":
            catalog,

        "total":
            total,

        "total_results":
            total,

        "page":
            page,

        "track_page":
            page,

        "per_page":
            per_page,

        "track_per_page":
            per_page,

        "total_pages":
            total_pages,

        "track_total_pages":
            total_pages,

        "genres":
            genres,

        "moods":
            moods,

        "query":
            search,

        "q":
            search,

        "genre":
            genre,

        "mood":
            mood,

        "min_price":
            min_price,

        "max_price":
            max_price,

        "has_previous":
            page > 1,

        "has_next":
            page < total_pages,

        "previous_page":
            max(
                1,
                page - 1,
            ),

        "next_page":
            min(
                total_pages,
                page + 1,
            ),

        "catalog_start":
            (
                0
                if total == 0
                else start + 1
            ),

        "catalog_end":
            min(
                end,
                total,
            ),

        "title":
            title,
    }


# ============================================================
# BEATS CATALOG
# ============================================================

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
        math.ceil(
            total / per_page
        ),
    )

    if page > total_pages:
        page = total_pages

    start = (
        page - 1
    ) * per_page

    end = (
        start + per_page
    )

    page_tracks = tracks[
        start:end
    ]

    catalog = [
        _catalog_item(track)
        for track in page_tracks
    ]

    return templates.TemplateResponse(
        request,
        "beats.html",
        _beats_context(
            request=request,
            current_user=current_user,
            tracks=page_tracks,
            catalog=catalog,
            total=total,
            page=page,
            per_page=per_page,
            search=search,
            genre=genre,
            mood=mood,
            min_price=min_price,
            max_price=max_price,
        ),
    )


# ============================================================
# HOT PICKS
# ============================================================

@router.get("/hot-picks")
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
        _beats_context(
            request=request,
            current_user=current_user,
            tracks=tracks,
            catalog=catalog,
            total=len(tracks),
            page=1,
            per_page=12,
            search=search,
            genre=genre,
            mood=mood,
            min_price=None,
            max_price=None,
            title="Hot Picks",
        ),
    )


# ============================================================
# LOCAL MEDIA PATH RESOLUTION
# ============================================================

def _resolve_local_media_path(
    stored_path: str,
) -> Optional[Path]:

    value = str(
        stored_path
    ).strip()

    if not value:
        return None

    if value.startswith(
        (
            "http://",
            "https://",
            "r2://",
            "s3://",
        )
    ):
        return None

    stored = Path(value)

    try:

        media_root_value = getattr(
            settings,
            "MEDIA_ROOT",
            None,
        ) or "media"

        media_root = Path(
            media_root_value
        ).expanduser()

        if not media_root.is_absolute():
            media_root = (
                Path.cwd()
                / media_root
            )

        media_root = (
            media_root.resolve()
        )

        candidates = []

        # ----------------------------------------------------
        # Absolute storage path
        # ----------------------------------------------------

        if stored.is_absolute():

            candidates.append(
                stored.resolve()
            )

        else:

            # ------------------------------------------------
            # Relative path from application root
            # ------------------------------------------------

            candidates.extend(
                [
                    (
                        Path.cwd()
                        / stored
                    ).resolve(),

                    (
                        media_root
                        / stored
                    ).resolve(),
                ]
            )

            # ------------------------------------------------
            # Handle values such as:
            #
            # media/covers/file.jpg
            # media/audio/file.mp3
            # ------------------------------------------------

            clean = str(
                stored
            ).replace(
                "\\",
                "/",
            ).lstrip("/")

            if clean.startswith(
                "media/"
            ):

                candidates.append(
                    (
                        media_root
                        / clean[6:]
                    ).resolve()
                )

        # ----------------------------------------------------
        # Security:
        #
        # Never allow a local media route to escape MEDIA_ROOT.
        # ----------------------------------------------------

        for candidate in candidates:

            try:

                candidate.relative_to(
                    media_root
                )

            except ValueError:

                continue

            if candidate.is_file():

                return candidate

    except Exception:

        logger.exception(
            "Unable to resolve local media path: %s",
            stored_path,
        )

    return None


# ============================================================
# MEDIA RESPONSE
# ============================================================

def _media_response(
    stored_path: str,
    *,
    fallback_media_type: str,
):

    value = str(
        stored_path
    ).strip()

    if not value:
        raise HTTPException(
            status_code=404,
            detail="Media file is unavailable.",
        )

    # ========================================================
    # DIRECT HTTP / HTTPS URL
    # ========================================================

    if value.startswith(
        (
            "http://",
            "https://",
        )
    ):

        return RedirectResponse(
            url=value,
            status_code=307,
        )

    # ========================================================
    # CLOUDFLARE R2 / S3 STORAGE
    # ========================================================

    if value.startswith(
        (
            "r2://",
            "s3://",
        )
    ):

        try:

            url = media_url(
                value
            )

        except Exception:

            logger.exception(
                "Unable to create signed media URL."
            )

            url = None

        if not url:

            raise HTTPException(
                status_code=404,
                detail=(
                    "Media is currently unavailable."
                ),
            )

        return RedirectResponse(
            url=url,
            status_code=307,
        )

    # ========================================================
    # LOCAL STORAGE
    # ========================================================

    path = _resolve_local_media_path(
        value
    )

    if not path:

        raise HTTPException(
            status_code=404,
            detail=(
                "Media file is currently unavailable."
            ),
        )

    suffix = (
        path.suffix
        .lower()
    )

    content_types = {

        ".jpg":
            "image/jpeg",

        ".jpeg":
            "image/jpeg",

        ".png":
            "image/png",

        ".webp":
            "image/webp",

        ".gif":
            "image/gif",

        ".mp3":
            "audio/mpeg",

        ".wav":
            "audio/wav",

        ".m4a":
            "audio/mp4",

        ".aac":
            "audio/aac",

        ".ogg":
            "audio/ogg",

        ".flac":
            "audio/flac",
    }

    media_type = content_types.get(
        suffix,
        fallback_media_type,
    )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        headers={
            "Cache-Control":
                "public, max-age=3600",
            "Accept-Ranges":
                "bytes",
        },
    )


# ============================================================
# TRACK LOOKUP
# ============================================================

def _get_track_by_slug(
    slug: str,
    db: Session,
) -> Track:

    track = (
        db.query(Track)
        .filter(
            Track.slug == slug
        )
        .first()
    )

    if not track:

        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    return track


# ============================================================
# TRACK ARTWORK
# ============================================================

@router.get(
    "/track/{slug}/artwork",
    name="track_artwork",
)
def track_artwork(
    slug: str,
    db: Session = Depends(
        get_db
    ),
):

    track = _get_track_by_slug(
        slug,
        db,
    )

    stored = _track_storage_path(
        track
    )

    if not stored:

        raise HTTPException(
            status_code=404,
            detail="This beat has no artwork.",
        )

    return _media_response(
        stored,
        fallback_media_type="image/jpeg",
    )


# ============================================================
# TRACK PREVIEW
# ============================================================

@router.get(
    "/track/{slug}/preview",
    name="track_preview",
)
def track_preview(
    slug: str,
    db: Session = Depends(
        get_db
    ),
):

    track = _get_track_by_slug(
        slug,
        db,
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # This now checks:
    #
    # preview_file_path
    # audio_preview_path
    # audio_preview_url
    # preview_url
    # audio_url
    # stream_url
    # mp3_url
    # preview_audio_url
    # audio_file_path   <-- uploaded BeatHub audio
    #
    # This fixes the current 404.
    # --------------------------------------------------------

    stored = _track_audio_storage_path(
        track
    )

    if not stored:

        raise HTTPException(
            status_code=404,
            detail=(
                "This beat has no preview audio."
            ),
        )

    return _media_response(
        stored,
        fallback_media_type="audio/mpeg",
    )


# ============================================================
# SESSIONS
# ============================================================

@router.get("/sessions")
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
            "request":
                request,

            "current_user":
                current_user,

            "user":
                current_user,

            "current_year":
                2026,
        },
    )
