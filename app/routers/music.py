from __future__ import annotations

import logging
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.profile import Profile
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
    if not bool(getattr(track, "is_published", False)):
        return False

    sales_model = _model_value(
        track,
        "sales_model",
        default=None,
    )

    sales_model_value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    # Do not display an exclusive beat after it has already been sold.
    if (
        str(sales_model_value or "").strip().lower()
        == SalesModel.EXCLUSIVE.value
        and bool(getattr(track, "is_sold", False))
    ):
        return False

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

    return str(value) if value else None


def _track_audio(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "preview_url",
        "preview_file_path",
        "audio_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        default=None,
    )

    if value:
        return str(value)

    # Compatibility for deployments where the uploaded audio path
    # itself is already browser-accessible.
    value = _model_value(
        track,
        "audio_file_path",
        default=None,
    )

    if value:
        value = str(value)

        if value.startswith(
            (
                "http://",
                "https://",
                "/",
            )
        ):
            return value

    return None


def _producer_profile(track: Track) -> Optional[Profile]:
    profile = _model_value(
        track,
        "creator_profile",
        "profile",
        "producer",
        "creator",
        "owner",
        default=None,
    )

    return profile


def _producer_name(track: Track) -> str:
    producer = _producer_profile(track)

    if producer is not None:
        name = _model_value(
            producer,
            "stage_name",
            "display_name",
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


def _producer_url(track: Track) -> str:
    profile = _producer_profile(track)

    slug = _model_value(
        profile,
        "slug",
        default=None,
    )

    if slug:
        return f"/store/{slug}"

    return "#"


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

    # Current BeatHub detail / checkout flow uses:
    # /track/{slug}
    if slug:
        return f"/track/{slug}"

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


def _track_sales_model(track: Track) -> str:
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

        # Search producer/stage name too.
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
                profile_stage_name.ilike(like)
            )

        if profile_slug is not None:
            conditions.append(
                profile_slug.ilike(like)
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

    # Kept for compatibility with the existing marketplace.
    # If the current/future schema exposes a mood column,
    # this filter automatically becomes active.
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
    # PRICE
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
    # PUBLISHED ONLY
    # --------------------------------------------------------------

    published_field = getattr(
        Track,
        "is_published",
        None,
    )

    if published_field is not None:
        query = query.filter(
            published_field.is_(True)
        )

    # --------------------------------------------------------------
    # NEWEST FIRST
    # --------------------------------------------------------------

    created_field = getattr(
        Track,
        "created_at",
        None,
    )

    if created_field is not None:
        query = query.order_by(
            created_field.desc()
        )
    else:
        query = query.order_by(
            Track.id.desc()
        )

    return query.all()


def _build_catalog(
    tracks: list[Track],
) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []

    for track in tracks:
        title = str(
            _model_value(
                track,
                "title",
                "name",
                default="Untitled Beat",
            )
        )

        genre = str(
            _model_value(
                track,
                "genre",
                "category",
                default="",
            )
            or ""
        )

        mood = str(
            _model_value(
                track,
                "mood",
                default="",
            )
            or ""
        )

        bpm = str(
            _model_value(
                track,
                "bpm",
                "tempo",
                default="",
            )
            or ""
        )

        key = str(
            _model_value(
                track,
                "key",
                "musical_key",
                default="",
            )
            or ""
        )

        catalog.append(
            {
                "track": track,
                "title": title,
                "producer": _producer_name(
                    track
                ),
                "producer_url": _producer_url(
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
                "genre": genre,
                "mood": mood,
                "bpm": bpm,
                "key": key,
                "description": str(
                    _model_value(
                        track,
                        "description",
                        "short_description",
                        default="",
                    )
                    or ""
                ),
                "sales_model": _track_sales_model(
                    track
                ),
            }
        )

    return catalog


def _build_producer_directory(
    tracks: list[Track],
    limit: int = 8,
) -> list[dict[str, Any]]:
    grouped: dict[
        str,
        dict[str, Any]
    ] = {}

    for track in tracks:
        profile = _producer_profile(
            track
        )

        if profile is None:
            continue

        slug = _model_value(
            profile,
            "slug",
            default=None,
        )

        if not slug:
            continue

        key = str(
            _model_value(
                profile,
                "id",
                default=slug,
            )
        )

        if key not in grouped:
            grouped[key] = {
                "profile": profile,
                "name": _producer_name(
                    track
                ),
                "slug": str(slug),
                "url": f"/store/{slug}",
                "bio": str(
                    _model_value(
                        profile,
                        "bio",
                        default="",
                    )
                    or ""
                ),
                "avatar_url": _model_value(
                    profile,
                    "avatar_path",
                    "avatar_url",
                    default=None,
                ),
                "beat_count": 0,
                "latest_track": track,
                "latest_created_at": _model_value(
                    track,
                    "created_at",
                    default=None,
                ),
            }

        grouped[key][
            "beat_count"
        ] += 1

        created_at = _model_value(
            track,
            "created_at",
            default=None,
        )

        latest_created_at = grouped[key][
            "latest_created_at"
        ]

        if (
            created_at is not None
            and (
                latest_created_at is None
                or created_at
                > latest_created_at
            )
        ):
            grouped[key][
                "latest_created_at"
            ] = created_at

            grouped[key][
                "latest_track"
            ] = track

    producers = list(
        grouped.values()
    )

    producers.sort(
        key=lambda item: (
            item["beat_count"],
            item["latest_created_at"]
            or 0,
            item["name"].lower(),
        ),
        reverse=True,
    )

    return producers[:limit]


def _marketplace_context(
    request: Request,
    current_user,
    tracks: list[Track],
    search: str,
    genre: str,
    mood: str,
    min_price: Optional[float],
    max_price: Optional[float],
    page: int,
    per_page: int,
    title: str,
    subtitle: str,
    show_producers: bool = True,
):
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

    catalog = _build_catalog(
        page_tracks
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

    producer_source = (
        tracks
        if show_producers
        else []
    )

    producers = (
        _build_producer_directory(
            producer_source
        )
    )

    all_producers = (
        _build_producer_directory(
            producer_source,
            limit=max(
                8,
                len(producer_source),
            ),
        )
    )

    return {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": 2026,

        "tracks": page_tracks,
        "beats": page_tracks,
        "catalog": catalog,

        "producers": producers,
        "producer_count": len(
            producers
        ),
        "total_producers": len(
            all_producers
        ),

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

        "title": title,
        "subtitle": subtitle,
        "show_producers": show_producers,
    }


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
            if _track_is_public(
                track
            )
        ]

    except Exception:
        logger.exception(
            "Unable to load BeatHub marketplace"
        )

        tracks = []

    context = _marketplace_context(
        request=request,
        current_user=current_user,
        tracks=tracks,
        search=search,
        genre=genre,
        mood=mood,
        min_price=min_price,
        max_price=max_price,
        page=page,
        per_page=per_page,
        title="Find your next sound.",
        subtitle=(
            "Discover beats from BeatHub producers, "
            "explore their stores, preview available sounds "
            "and choose the right license for your next release."
        ),
    )

    return templates.TemplateResponse(
        request,
        "beats.html",
        context,
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
            min_price=None,
            max_price=None,
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
            "Unable to load BeatHub hot picks"
        )

        tracks = []

    context = _marketplace_context(
        request=request,
        current_user=current_user,
        tracks=tracks,
        search=search,
        genre=genre,
        mood=mood,
        min_price=None,
        max_price=None,
        page=1,
        per_page=12,
        title="Hot Picks",
        subtitle=(
            "Freshly published BeatHub beats worth hearing "
            "right now. Open a producer store to explore more."
        ),
    )

    context["is_hot_picks"] = True

    return templates.TemplateResponse(
        request,
        "beats.html",
        context,
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
