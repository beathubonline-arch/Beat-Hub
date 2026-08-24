from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models.music import Album, SalesModel, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.services.storage import media_url
from app.utils.deps import get_optional_user


# ======================================================================
# ROUTER
# ======================================================================

router = APIRouter(tags=["music"])

templates = Jinja2Templates(
    directory="app/templates"
)

logger = logging.getLogger("beathub.music")


# ======================================================================
# COMMON HELPERS
# ======================================================================

def _model_value(
    obj: Any,
    *names: str,
    default: Any = None,
) -> Any:
    """
    Safely return the first available attribute.
    """

    if obj is None:
        return default

    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None

        if value is not None:
            return value

    return default


def _ctx(
    request: Request,
    current_user,
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
# TRACK VISIBILITY
# ======================================================================

def _track_is_public(
    track: Track,
) -> bool:
    if track is None:
        return False

    if getattr(
        track,
        "is_published",
        True,
    ) is False:
        return False

    sales_model = getattr(
        track,
        "sales_model",
        None,
    )

    sales_value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    if (
        str(
            sales_value or ""
        ).strip().lower()
        == "exclusive"
    ):
        if getattr(
            track,
            "is_sold",
            False,
        ):
            return False

    return True


# ======================================================================
# TRACK PRICE
# ======================================================================

def _track_price(
    track: Track,
) -> float:

    raw = _model_value(
        track,
        "price",
        "amount",
        "non_exclusive_price",
        "lease_price",
        default=0,
    )

    try:
        return float(
            raw or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


# ======================================================================
# TRACK ARTWORK STORAGE PATH
# ======================================================================

def _track_artwork_storage_path(
    track: Track,
) -> Optional[str]:

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

    if value is None:
        return None

    value = str(
        value
    ).strip()

    return value or None


# ======================================================================
# TRACK AUDIO STORAGE PATH
# ======================================================================

def _track_audio_storage_path(
    track: Track,
) -> Optional[str]:

    # --------------------------------------------------------------
    # First check a dedicated preview field.
    # --------------------------------------------------------------

    value = _model_value(
        track,
        "preview_file_path",
        "audio_preview_path",
        "audio_preview_url",
        "preview_url",
        "audio_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        default=None,
    )

    if value:
        value = str(
            value
        ).strip()

        if value:
            return value

    # --------------------------------------------------------------
    # IMPORTANT:
    #
    # BeatHub uploads currently store the actual uploaded audio in
    # audio_file_path while preview_file_path may be NULL.
    #
    # Therefore preview must fall back to audio_file_path.
    # --------------------------------------------------------------

    value = _model_value(
        track,
        "audio_file_path",
        default=None,
    )

    if value:
        value = str(
            value
        ).strip()

        if value:
            return value

    return None


# ======================================================================
# BROWSER ARTWORK URL
# ======================================================================

def _track_artwork(
    track: Track,
) -> Optional[str]:

    stored = _track_artwork_storage_path(
        track
    )

    if not stored:
        return None

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return (
            "/track/"
            + str(slug)
            + "/artwork"
        )

    try:
        return media_url(
            stored
        )
    except Exception:
        logger.exception(
            "Unable to generate artwork URL."
        )
        return None


# ======================================================================
# BROWSER PREVIEW URL
# ======================================================================

def _track_audio(
    track: Track,
) -> Optional[str]:

    stored = _track_audio_storage_path(
        track
    )

    if not stored:
        return None

    slug = _model_value(
        track,
        "slug",
        default=None,
    )

    if slug:
        return (
            "/track/"
            + str(slug)
            + "/preview"
        )

    try:
        return media_url(
            stored
        )
    except Exception:
        logger.exception(
            "Unable to generate preview URL."
        )
        return None


# ======================================================================
# PRODUCER HELPERS
# ======================================================================

def _producer_profile(
    track: Track,
):
    return _model_value(
        track,
        "creator_profile",
        "profile",
        "producer",
        "creator",
        "owner",
        default=None,
    )


def _producer_name(
    track: Track,
) -> str:

    profile = _producer_profile(
        track
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
            return str(
                name
            )

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
                return str(
                    name
                )

    direct = _model_value(
        track,
        "producer_name",
        "creator_name",
        "artist_name",
        "username",
        default=None,
    )

    if direct:
        return str(
            direct
        )

    return "BeatHub Creator"


def _producer_store_url(
    track: Track,
) -> Optional[str]:

    profile = _producer_profile(
        track
    )

    slug = _model_value(
        profile,
        "slug",
        default=None,
    )

    if slug:
        return (
            "/store/"
            + str(slug)
        )

    return None


# ======================================================================
# TRACK URL
# ======================================================================

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
        return (
            "/track/"
            + str(slug)
        )

    if track_id is not None:
        return (
            "/track/"
            + str(track_id)
        )

    return "#"


# ======================================================================
# SALES MODEL
# ======================================================================

def _track_sales_model(
    track: Track,
) -> str:

    sales_model = _model_value(
        track,
        "sales_model",
        default=None,
    )

    value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    return str(
        value or ""
    ).strip().lower()


# ======================================================================
# MARKETPLACE QUERY
# ======================================================================

def _query_catalog(
    db: Session,
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
):

    query = (
        db.query(Track)
        .options(
            joinedload(
                Track.creator_profile
            )
        )
        .outerjoin(
            Profile,
            Track.creator_profile_id
            == Profile.id,
        )
    )

    # --------------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------------

    if search:

        like = (
            "%"
            + search
            + "%"
        )

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
                    field.ilike(
                        like
                    )
                )

        profile_stage_name = getattr(
            Profile,
            "stage_name",
            None,
        )

        profile_slug = getattr(
            Profile,
            "slug",
            None,
        )

        if profile_stage_name is not None:
            conditions.append(
                profile_stage_name.ilike(
                    like
                )
            )

        if profile_slug is not None:
            conditions.append(
                profile_slug.ilike(
                    like
                )
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
                    "%"
                    + genre
                    + "%"
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
                    "%"
                    + mood
                    + "%"
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
    # PUBLISHED ONLY
    # --------------------------------------------------------------

    published = getattr(
        Track,
        "is_published",
        None,
    )

    if published is not None:

        query = query.filter(
            published.is_(True)
        )

    # --------------------------------------------------------------
    # NEWEST FIRST
    # --------------------------------------------------------------

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


# ======================================================================
# CATALOG ITEM
# ======================================================================

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

        "sales_model":
            _track_sales_model(
                track
            ),
    }


# ======================================================================
# FILTER DATA
# ======================================================================

def _catalog_filters(
    tracks: list[Track],
):

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


# ======================================================================
# BEATS CONTEXT
# ======================================================================

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
        start
        + per_page
    )

    genres, moods = (
        _catalog_filters(
            tracks
        )
    )

    return _ctx(
        request,
        current_user,

        tracks=tracks,
        beats=tracks,
        catalog=catalog,

        total=total,
        total_results=total,

        page=page,
        track_page=page,

        per_page=per_page,
        track_per_page=per_page,

        total_pages=total_pages,
        track_total_pages=total_pages,

        genres=genres,
        moods=moods,

        query=search,
        q=search,

        genre=genre,
        mood=mood,

        min_price=min_price,
        max_price=max_price,

        has_previous=(
            page > 1
        ),

        has_next=(
            page
            < total_pages
        ),

        previous_page=max(
            1,
            page - 1,
        ),

        next_page=min(
            total_pages,
            page + 1,
        ),

        catalog_start=(
            0
            if total == 0
            else start + 1
        ),

        catalog_end=min(
            end,
            total,
        ),

        title=title,
    )


# ======================================================================
# BEAT MARKETPLACE
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
    db: Session = Depends(
        get_db
    ),
):

    search = (
        q or ""
    ).strip()

    genre = (
        genre or ""
    ).strip()

    mood = (
        mood or ""
    ).strip()

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
            if _track_is_public(
                track
            )
        ]

    except Exception:

        logger.exception(
            "Unable to load BeatHub beat catalog."
        )

        tracks = []

    total = len(
        tracks
    )

    total_pages = max(
        1,
        math.ceil(
            total
            / per_page
        ),
    )

    if page > total_pages:
        page = total_pages

    start = (
        page - 1
    ) * per_page

    end = (
        start
        + per_page
    )

    page_tracks = tracks[
        start:end
    ]

    catalog = [
        _catalog_item(
            track
        )
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

    search = (
        q or ""
    ).strip()

    genre = (
        genre or ""
    ).strip()

    mood = (
        mood or ""
    ).strip()

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
            if _track_is_public(
                track
            )
        ][:12]

    except Exception:

        logger.exception(
            "Unable to load BeatHub hot picks."
        )

        tracks = []

    catalog = [
        _catalog_item(
            track
        )
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


# ======================================================================
# TRACK LOOKUP
# ======================================================================

def _get_track_by_slug(
    slug: str,
    db: Session,
) -> Track:

    clean_slug = (
        str(
            slug or ""
        ).strip()
    )

    if not clean_slug:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    track = (
        db.query(Track)
        .options(
            joinedload(
                Track.creator_profile
            )
        )
        .filter(
            Track.slug == clean_slug
        )
        .first()
    )

    if not track:

        try:

            track = (
                db.query(Track)
                .options(
                    joinedload(
                        Track.creator_profile
                    )
                )
                .filter(
                    Track.slug.ilike(
                        clean_slug
                    )
                )
                .first()
            )

        except Exception:
            track = None

    if not track:

        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    return track


# ======================================================================
# TRACK PURCHASE STATUS
# ======================================================================

def _track_has_been_purchased(
    track: Track,
    current_user,
    db: Session,
) -> bool:

    if not current_user:
        return False

    try:

        license_record = (
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

        return (
            license_record
            is not None
        )

    except Exception:

        logger.exception(
            "Unable to determine purchase status."
        )

        return False


# ======================================================================
# TRACK DETAIL
# ======================================================================

@router.get(
    "/track/{slug}",
    name="track_detail",
)
def track_detail(
    slug: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user=Depends(
        get_optional_user
    ),
):

    track = _get_track_by_slug(
        slug,
        db,
    )

    purchased = (
        _track_has_been_purchased(
            track,
            current_user,
            db,
        )
    )

    artwork_url = _track_artwork(
        track
    )

    preview_url = _track_audio(
        track
    )

    producer = _producer_profile(
        track
    )

    producer_name = _producer_name(
        track
    )

    producer_store_url = (
        _producer_store_url(
            track
        )
    )

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        _ctx(
            request,
            current_user,

            track=track,

            purchased=purchased,

            artwork_url=artwork_url,
            preview_url=preview_url,

            producer=producer,
            producer_name=producer_name,
            producer_store_url=producer_store_url,

            track_url=(
                "/track/"
                + str(
                    track.slug
                )
                if getattr(
                    track,
                    "slug",
                    None,
                )
                else "#"
            ),
        ),
    )


# ======================================================================
# /p/{slug} COMPATIBILITY ROUTE
#
# This fixes:
#
#     GET /p/mad-mixx-afro -> 404
#
# Marketplace links and older BeatHub pages may use /p/{slug}.
# It now opens the same beautiful track detail page.
# ======================================================================

@router.get(
    "/p/{slug}",
    name="track_public_short",
)
def track_public_short(
    slug: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user=Depends(
        get_optional_user
    ),
):

    return track_detail(
        slug=slug,
        request=request,
        db=db,
        current_user=current_user,
    )


# ======================================================================
# ALBUM DETAIL
# ======================================================================

@router.get(
    "/album/{slug}",
    name="album_detail",
)
def album_detail(
    slug: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
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
            detail="Album not found.",
        )

    artwork_path = getattr(
        album,
        "artwork_path",
        None,
    )

    artwork_url = None

    if artwork_path:

        try:

            artwork_url = media_url(
                str(
                    artwork_path
                )
            )

        except Exception:

            logger.exception(
                "Unable to generate album artwork URL."
            )

    return templates.TemplateResponse(
        request,
        "album_detail.html",
        _ctx(
            request,
            current_user,

            album=album,

            artwork_url=artwork_url,
        ),
    )


# ======================================================================
# TRACK ARTWORK
# ======================================================================

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

    stored = (
        _track_artwork_storage_path(
            track
        )
    )

    if not stored:

        raise HTTPException(
            status_code=404,
            detail=(
                "This beat does not "
                "have artwork."
            ),
        )

    return _media_response(
        stored,
        fallback_media_type=(
            "image/jpeg"
        ),
    )


# ======================================================================
# TRACK PREVIEW
# ======================================================================

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

    # IMPORTANT:
    # This now falls back to audio_file_path.
    #
    # Your current uploads can have:
    #
    # preview_file_path = None
    #
    # while:
    #
    # audio_file_path = r2://...
    #
    # Therefore the old implementation returned 404.
    stored = (
        _track_audio_storage_path(
            track
        )
    )

    if not stored:

        raise HTTPException(
            status_code=404,
            detail=(
                "This beat does not "
                "have preview audio."
            ),
        )

    return _media_response(
        stored,
        fallback_media_type=(
            "audio/mpeg"
        ),
    )


# ======================================================================
# LOCAL MEDIA RESOLUTION
# ======================================================================

def _resolve_local_media_path(
    stored_path: str,
) -> Optional[Path]:

    value = str(
        stored_path or ""
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

    stored = Path(
        value
    )

    try:

        media_root_value = (
            getattr(
                settings,
                "MEDIA_ROOT",
                None,
            )
            or "media"
        )

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

        if stored.is_absolute():

            candidates.append(
                stored.resolve()
            )

        else:

            candidates.append(
                (
                    Path.cwd()
                    / stored
                ).resolve()
            )

            candidates.append(
                (
                    media_root
                    / stored
                ).resolve()
            )

            clean = (
                str(stored)
                .replace(
                    "\\",
                    "/",
                )
                .lstrip("/")
            )

            if clean.startswith(
                "media/"
            ):

                candidates.append(
                    (
                        media_root
                        / clean[6:]
                    ).resolve()
                )

        for candidate in candidates:

            try:

                candidate.relative_to(
                    media_root
                )

            except ValueError:

                continue

            if (
                candidate.exists()
                and candidate.is_file()
            ):

                return candidate

    except Exception:

        logger.exception(
            "Unable to resolve local media: %s",
            stored_path,
        )

    return None


# ======================================================================
# MEDIA RESPONSE
# ======================================================================

def _media_response(
    stored_path: str,
    *,
    fallback_media_type: str,
):

    value = str(
        stored_path or ""
    ).strip()

    if not value:

        raise HTTPException(
            status_code=404,
            detail=(
                "Media is currently unavailable."
            ),
        )

    # --------------------------------------------------------------
    # DIRECT URL
    # --------------------------------------------------------------

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

    # --------------------------------------------------------------
    # CLOUDFLARE R2 / S3
    # --------------------------------------------------------------

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
                "Unable to create cloud media URL."
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

    # --------------------------------------------------------------
    # LOCAL MEDIA
    # --------------------------------------------------------------

    path = (
        _resolve_local_media_path(
            value
        )
    )

    if not path:

        raise HTTPException(
            status_code=404,
            detail=(
                "Media file is currently unavailable."
            ),
        )

    suffix = (
        path.suffix.lower()
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

        ".svg":
            "image/svg+xml",

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

    return FileResponse(
        path=str(
            path
        ),
        media_type=content_types.get(
            suffix,
            fallback_media_type,
        ),
        headers={
            "Cache-Control":
                "public, max-age=3600",
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
        _ctx(
            request,
            current_user,
        ),
    )
