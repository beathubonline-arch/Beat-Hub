from __future__ import annotations

import logging
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import get_optional_user, require_user

logger = logging.getLogger("beathub.music")

router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


# ======================================================================
# SHARED CONTEXT
# ======================================================================

def ctx(
    request: Request,
    current_user=None,
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": 2026,
    }

    context.update(extra)
    return context


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


def _track_is_public(track: Track) -> bool:
    for field in (
        "is_published",
        "published",
        "is_public",
        "active",
    ):
        if hasattr(track, field):
            try:
                value = getattr(track, field, None)

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


def _track_artwork(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "cover_url",
        "cover_art_url",
        "artwork_url",
        "image_url",
        "thumbnail_url",
        "cover_image",
        "artwork",
        "image",
        default=None,
    )

    if not value:
        cover_path = _model_value(
            track,
            "cover_art_path",
            "cover_path",
            "artwork_path",
            default=None,
        )

        if cover_path:
            try:
                from app.services.storage import r2_presigned_url

                value = r2_presigned_url(
                    str(cover_path)
                )
            except Exception:
                value = None

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

    if value:
        return str(value)

    audio_path = _model_value(
        track,
        "audio_path",
        "file_path",
        "preview_path",
        default=None,
    )

    if audio_path:
        try:
            from app.services.storage import r2_presigned_url

            return r2_presigned_url(
                str(audio_path)
            )
        except Exception:
            pass

    return None


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

    return (
        str(direct)
        if direct
        else "BeatHub Creator"
    )


def _track_url(track: Track) -> str:
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
        return f"/p/{slug}"

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


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
# PUBLIC CATALOG QUERY
# ======================================================================

def _query_catalog(
    db: Session,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
):
    query = db.query(Track)

    # --------------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------------

    if search:
        like = f"%{search}%"
        conditions = []

        for field_name in (
            "title",
            "name",
            "genre",
            "category",
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
                try:
                    conditions.append(
                        field.ilike(like)
                    )
                except Exception:
                    pass

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
    # MIN PRICE
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

    # --------------------------------------------------------------
    # MAX PRICE
    # --------------------------------------------------------------

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
    # PUBLIC / PUBLISHED TRACKS
    # --------------------------------------------------------------

    publication_field = None

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
            publication_field = field
            break

    if publication_field is not None:
        query = query.filter(
            publication_field.is_(True)
        )

    # --------------------------------------------------------------
    # NEWEST FIRST
    # --------------------------------------------------------------

    ordered = False

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
            ordered = True
            break

    if not ordered:
        query = query.order_by(
            Track.id.desc()
        )

    return query.all()


# ======================================================================
# BEATS MARKETPLACE
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
    db: Session = Depends(get_db),
):
    search = (q or "").strip()
    genre = (genre or "").strip()
    mood = (mood or "").strip()

    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        min_price, max_price = (
            max_price,
            min_price,
        )

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

    end = start + per_page

    page_tracks = tracks[
        start:end
    ]

    # --------------------------------------------------------------
    # BUILD PRESENTATION CATALOG
    # --------------------------------------------------------------

    catalog = []

    for track in page_tracks:
        title = str(
            _model_value(
                track,
                "title",
                "name",
                default="Untitled Beat",
            )
        )

        catalog.append(
            {
                "track": track,
                "title": title,
                "producer": _producer_name(
                    track
                ),
                "price": _track_price(
                    track
                ),
                "artwork_url": _track_artwork(
                    track
                ),
                "audio_url": _track_audio(
                    track
                ),
                "url": _track_url(
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
        )

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
    # TEMPLATE
    # --------------------------------------------------------------

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
    "/hot-picks",
)
def hot_picks(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        get_optional_user
    ),
):
    try:
        tracks = (
            db.query(Track)
            .filter(
                Track.is_published == True
            )
            .order_by(
                Track.created_at.desc()
            )
            .limit(12)
            .all()
        )
    except Exception:
        logger.exception(
            "Unable to load hot picks"
        )
        tracks = []

    catalog = []

    for track in tracks:
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
                "producer": _producer_name(
                    track
                ),
                "price": _track_price(
                    track
                ),
                "artwork_url": _track_artwork(
                    track
                ),
                "audio_url": _track_audio(
                    track
                ),
                "url": _track_url(
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
        )

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

            "per_page": len(tracks),
            "track_per_page": len(tracks),

            "total_pages": 1,
            "track_total_pages": 1,

            "genres": [],
            "moods": [],

            "query": "",
            "q": "",
            "genre": "",
            "mood": "",

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
        },
    )


# ======================================================================
# SESSIONS
# ======================================================================

@router.get(
    "/sessions",
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
        ctx(
            request,
            current_user,
        ),
    )


# ======================================================================
# TRACK DETAIL
# ======================================================================

@router.get(
    "/track/{slug}",
)
def track_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        get_optional_user
    ),
):
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
            detail="Track not found",
        )

    purchased = False

    if current_user:
        try:
            purchased = (
                db.query(License)
                .join(
                    Order,
                    License.order_id
                    == Order.id,
                )
                .filter(
                    License.buyer_id
                    == current_user.id,
                    License.track_id
                    == track.id,
                    Order.status
                    == OrderStatus.COMPLETED,
                )
                .first()
                is not None
            )
        except Exception:
            logger.exception(
                "Unable to determine "
                "purchase status"
            )

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        ctx(
            request,
            current_user,
            track=track,
            purchased=purchased,
        ),
    )


# ======================================================================
# PUBLIC TRACK PAGE COMPATIBILITY
# ======================================================================

@router.get(
    "/p/{slug}",
)
def public_track_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        get_optional_user
    ),
):
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
            detail="Beat not found",
        )

    purchased = False

    if current_user:
        try:
            purchased = (
                db.query(License)
                .join(
                    Order,
                    License.order_id
                    == Order.id,
                )
                .filter(
                    License.buyer_id
                    == current_user.id,
                    License.track_id
                    == track.id,
                    Order.status
                    == OrderStatus.COMPLETED,
                )
                .first()
                is not None
            )
        except Exception:
            logger.exception(
                "Unable to determine "
                "purchase status"
            )

    template_name = (
        "track_detail.html"
    )

    return templates.TemplateResponse(
        request,
        template_name,
        ctx(
            request,
            current_user,
            track=track,
            purchased=purchased,
        ),
    )


# ======================================================================
# ALBUM
# ======================================================================

@router.get(
    "/album/{slug}",
)
def album_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        get_optional_user
    ),
):
    album = (
        db.query(Album)
        .filter(
            Album.slug == slug
        )
        .first()
    )

    if not album:
        raise HTTPException(
            status_code=404,
            detail="Album not found",
        )

    tracks = []

    try:
        tracks = (
            db.query(Track)
            .filter(
                Track.album_id == album.id
            )
            .filter(
                Track.is_published == True
            )
            .order_by(
                Track.created_at.asc()
            )
            .all()
        )
    except Exception:
        logger.exception(
            "Unable to load album tracks"
        )

    return templates.TemplateResponse(
        request,
        "album_detail.html",
        ctx(
            request,
            current_user,
            album=album,
            tracks=tracks,
        ),
    )


# ======================================================================
# CREATOR PROFILE
# ======================================================================

@router.get(
    "/profile/{slug}",
)
def profile_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        get_optional_user
    ),
):
    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == slug
        )
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profile not found",
        )

    tracks = []

    try:
        tracks = (
            db.query(Track)
            .filter(
                Track.creator_profile_id
                == profile.id
            )
            .filter(
                Track.is_published == True
            )
            .order_by(
                Track.created_at.desc()
            )
            .limit(24)
            .all()
        )
    except Exception:
        logger.exception(
            "Unable to load creator tracks"
        )

    return templates.TemplateResponse(
        request,
        "profile.html",
        ctx(
            request,
            current_user,
            profile=profile,
            tracks=tracks,
        ),
    )


# ======================================================================
# DOWNLOAD COMPATIBILITY
# ======================================================================

@router.get(
    "/download/{track_ref}",
)
@router.get(
    "/download/track/{track_ref}",
)
def download_track(
    track_ref: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_user
    ),
):
    track = None

    # Try ID first.
    try:
        track_id = int(track_ref)

        track = (
            db.query(Track)
            .filter(
                Track.id == track_id
            )
            .first()
        )
    except (
        TypeError,
        ValueError,
    ):
        pass

    # Then try slug.
    if track is None:
        track = (
            db.query(Track)
            .filter(
                Track.slug == track_ref
            )
            .first()
        )

    if track is None:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    purchased = (
        db.query(License)
        .join(
            Order,
            License.order_id
            == Order.id,
        )
        .filter(
            License.buyer_id
            == current_user.id,
            License.track_id
            == track.id,
            Order.status
            == OrderStatus.COMPLETED,
        )
        .first()
    )

    if purchased is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Purchase required "
                "before downloading."
            ),
        )

    file_path = _model_value(
        track,
        "audio_path",
        "file_path",
        "download_path",
        "storage_path",
        default=None,
    )

    if file_path:
        path = Path(
            str(file_path)
        )

        if path.is_file():
            return FileResponse(
                path=str(path),
                filename=(
                    f"{_model_value("
                    "track, "
                    '"title", '
                    '"name", '
                    'default="beat"'
                    ")}.mp3"
                ),
                media_type=(
                    "audio/mpeg"
                ),
            )

    audio_url = _track_audio(track)

    if audio_url:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url=audio_url,
            status_code=307,
        )

    raise HTTPException(
        status_code=404,
        detail=(
            "The purchased audio file "
            "is not currently available."
        ),
    )
