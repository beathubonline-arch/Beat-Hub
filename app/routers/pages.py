from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.utils.deps import get_optional_user

logger = logging.getLogger("beathub.pages")

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def _value(
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


def _safe_local_path(
    value: Optional[str],
) -> Optional[Path]:
    if not value:
        return None

    raw = str(value).strip()

    if not raw:
        return None

    if raw.startswith(
        (
            "http://",
            "https://",
            "r2://",
            "s3://",
        )
    ):
        return None

    candidates = []

    path = Path(raw)

    if path.is_absolute():
        candidates.append(path)

    clean = raw.lstrip("/")

    candidates.extend(
        [
            Path(clean),
            Path("app") / clean,
            Path("uploads") / clean,
            Path("media") / clean,
            Path("static") / clean,
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


def _track_public(track: Track) -> bool:
    for field_name in (
        "is_published",
        "published",
        "is_public",
        "active",
    ):
        if hasattr(track, field_name):
            try:
                if getattr(track, field_name, None) is False:
                    return False
            except Exception:
                pass

    return True


def _track_slug(track: Track) -> Optional[str]:
    value = _value(
        track,
        "slug",
        default=None,
    )

    return str(value) if value else None


def _track_artwork(
    track: Track,
) -> Optional[str]:
    value = _value(
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

    value = str(value).strip()

    if not value:
        return None

    if value.startswith(
        (
            "http://",
            "https://",
            "data:",
            "//",
            "/",
        )
    ):
        return value

    slug = _track_slug(track)

    if slug:
        return f"/track/{slug}/artwork"

    track_id = _value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}/artwork"

    return None


def _track_audio(
    track: Track,
) -> Optional[str]:
    value = _value(
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

    value = str(value).strip()

    if not value:
        return None

    if value.startswith(
        (
            "http://",
            "https://",
            "data:",
            "//",
            "/",
        )
    ):
        return value

    slug = _track_slug(track)

    if slug:
        return f"/track/{slug}/preview"

    track_id = _value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}/preview"

    return None


def _producer_name(track: Track) -> str:
    producer = _value(
        track,
        "producer",
        "creator",
        "owner",
        "user",
        default=None,
    )

    if producer is not None:
        name = _value(
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

    direct = _value(
        track,
        "producer_name",
        "creator_name",
        "artist_name",
        "username",
        default=None,
    )

    return str(direct) if direct else "BeatHub Creator"


def _producer_slug(track: Track) -> Optional[str]:
    producer = _value(
        track,
        "producer",
        "creator",
        "owner",
        "user",
        default=None,
    )

    if producer is not None:
        slug = _value(
            producer,
            "slug",
            "store_slug",
            "username",
            default=None,
        )

        if slug:
            return str(slug)

    slug = _value(
        track,
        "producer_slug",
        "creator_slug",
        "store_slug",
        default=None,
    )

    return str(slug) if slug else None


def _track_url(track: Track) -> str:
    slug = _track_slug(track)

    if slug:
        return f"/p/{slug}"

    track_id = _value(
        track,
        "id",
        default=None,
    )

    if track_id is not None:
        return f"/track/{track_id}"

    return "#"


def _track_catalog_item(
    track: Track,
) -> dict[str, Any]:
    raw_price = _value(
        track,
        "price",
        "amount",
        "non_exclusive_price",
        "lease_price",
        default=0,
    )

    try:
        price = float(raw_price or 0)
    except (TypeError, ValueError):
        price = 0.0

    producer_slug = _producer_slug(track)

    return {
        "track": track,
        "title": str(
            _value(
                track,
                "title",
                "name",
                default="Untitled Beat",
            )
        ),
        "producer": _producer_name(track),
        "producer_url": (
            f"/producer/{producer_slug}"
            if producer_slug
            else None
        ),
        "price": price,
        "artwork_url": _track_artwork(track),
        "audio_url": _track_audio(track),
        "url": _track_url(track),
        "genre": str(
            _value(
                track,
                "genre",
                "category",
                default="",
            )
            or ""
        ),
        "mood": str(
            _value(
                track,
                "mood",
                default="",
            )
            or ""
        ),
        "bpm": str(
            _value(
                track,
                "bpm",
                "tempo",
                default="",
            )
            or ""
        ),
        "key": str(
            _value(
                track,
                "key",
                "musical_key",
                default="",
            )
            or ""
        ),
    }


@router.get("/")
def home(
    request: Request,
    current_user=Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
        },
    )


@router.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "BeatHub",
    }


@router.get("/track/{identifier}/artwork")
def track_artwork(
    identifier: str,
    db: Session = Depends(get_db),
):
    track = None

    slug_field = getattr(
        Track,
        "slug",
        None,
    )

    if slug_field is not None:
        try:
            track = (
                db.query(Track)
                .filter(slug_field == identifier)
                .first()
            )
        except Exception:
            track = None

    if track is None:
        try:
            track_id = int(identifier)
        except (TypeError, ValueError):
            track_id = None

        if track_id is not None:
            id_field = getattr(
                Track,
                "id",
                None,
            )

            if id_field is not None:
                try:
                    track = (
                        db.query(Track)
                        .filter(id_field == track_id)
                        .first()
                    )
                except Exception:
                    track = None

    if track is None:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    artwork = _value(
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

    if not artwork:
        raise HTTPException(
            status_code=404,
            detail="Artwork not found",
        )

    local_path = _safe_local_path(
        str(artwork)
    )

    if local_path is None:
        if str(artwork).startswith(
            (
                "http://",
                "https://",
                "//",
            )
        ):
            return RedirectResponse(
                str(artwork)
            )

        raise HTTPException(
            status_code=404,
            detail="Artwork file not available",
        )

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
            local_path.suffix.lower(),
            "application/octet-stream",
        ),
        headers={
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/track/{identifier}/preview")
def track_preview(
    identifier: str,
    db: Session = Depends(get_db),
):
    track = None

    slug_field = getattr(
        Track,
        "slug",
        None,
    )

    if slug_field is not None:
        try:
            track = (
                db.query(Track)
                .filter(slug_field == identifier)
                .first()
            )
        except Exception:
            track = None

    if track is None:
        try:
            track_id = int(identifier)
        except (TypeError, ValueError):
            track_id = None

        if track_id is not None:
            id_field = getattr(
                Track,
                "id",
                None,
            )

            if id_field is not None:
                try:
                    track = (
                        db.query(Track)
                        .filter(id_field == track_id)
                        .first()
                    )
                except Exception:
                    track = None

    if track is None:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    audio = _value(
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

    if not audio:
        raise HTTPException(
            status_code=404,
            detail="Audio preview not found",
        )

    audio = str(audio)

    if audio.startswith(
        (
            "http://",
            "https://",
            "//",
        )
    ):
        return RedirectResponse(audio)

    local_path = _safe_local_path(audio)

    if local_path is None:
        raise HTTPException(
            status_code=404,
            detail="Audio file not available",
        )

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
            local_path.suffix.lower(),
            "application/octet-stream",
        ),
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/search")
def search(
    request: Request,
    q: str = Query(
        default="",
        max_length=100,
    ),
    current_user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    search_term = q.strip()

    tracks = []

    try:
        query = db.query(Track)

        if search_term:
            like = f"%{search_term}%"
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

        tracks = [
            track
            for track in query.all()
            if _track_public(track)
        ]

    except Exception:
        logger.exception(
            "BeatHub search failed"
        )
        tracks = []

    catalog = [
        _track_catalog_item(track)
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
            "per_page": max(
                6,
                min(48, len(tracks) or 24),
            ),
            "track_per_page": 24,
            "total_pages": 1,
            "track_total_pages": 1,
            "genres": [],
            "moods": [],
            "query": search_term,
            "q": search_term,
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
            "title": "Search BeatHub",
        },
    )


@router.get("/terms")
def terms_page(
    request: Request,
    current_user=Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "terms.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
        },
    )


@router.get("/privacy")
def privacy_page(
    request: Request,
    current_user=Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
        },
    )


@router.get("/contact")
def contact_page(
    request: Request,
    current_user=Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "contact.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
        },
    )
