from __future__ import annotations

import math
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.routers.music import _catalog_item, _query_catalog, _track_is_public
from app.utils.deps import get_optional_user
from app.routers import beat_catalog, marketplace

logger = logging.getLogger("beathub.track_catalog")
router = APIRouter(tags=["track-catalog"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/tracks", name="tracks")
def tracks_catalog(
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
        candidates = _query_catalog(
            db=db,
            search=search,
            genre=genre,
            mood=mood,
            min_price=min_price,
            max_price=max_price,
        )
        tracks = [
            track for track in candidates
            if _track_is_public(track)
            and str(getattr(track, "content_type", "beat") or "beat").strip().lower() == "track"
        ]
    except Exception:
        logger.exception("Unable to load BeatHub track catalog")
        tracks = []

    total = len(tracks)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_tracks = tracks[start:start + per_page]

    genres = sorted(
        {
            str(getattr(track, "genre", "") or "").strip()
            for track in tracks
            if getattr(track, "genre", None)
        },
        key=str.lower,
    )
    moods = sorted(
        {
            str(getattr(track, "mood", "") or "").strip()
            for track in tracks
            if getattr(track, "mood", None)
        },
        key=str.lower,
    )

    return templates.TemplateResponse(
        request,
        "tracks.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "tracks": page_tracks,
            "catalog": [_catalog_item(track) for track in page_tracks],
            "total": total,
            "total_results": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "track_total_pages": total_pages,
            "genres": genres,
            "moods": moods,
            "q": search,
            "query": search,
            "genre": genre,
            "mood": mood,
            "min_price": min_price,
            "max_price": max_price,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "previous_page": max(1, page - 1),
            "next_page": min(total_pages, page + 1),
            "catalog_start": 0 if total == 0 else start + 1,
            "catalog_end": min(start + per_page, total),
        },
    )


# The marketplace routers were previously present in the repository but were
# not mounted by main.py. Mount them through this already-included router so
# /marketplace and /marketplace/beats are actually reachable in production.
# This also keeps the main application router wiring unchanged.
router.include_router(marketplace.router)
router.include_router(beat_catalog.router)
