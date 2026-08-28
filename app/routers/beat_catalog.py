import math
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.routers.music import _beats_context, _catalog_item, _query_catalog, _track_is_public
from app.utils.deps import get_optional_user

router = APIRouter(tags=["beat-catalog"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/marketplace/beats")
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
    tracks = _query_catalog(
        db=db,
        search=q.strip(),
        genre=genre.strip(),
        mood=mood.strip(),
        min_price=min_price,
        max_price=max_price,
    )
    tracks = [
        track for track in tracks
        if _track_is_public(track)
        and str(getattr(track, "content_type", "beat") or "beat").strip().lower() == "beat"
    ]

    total = len(tracks)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_tracks = tracks[start:start + per_page]

    return templates.TemplateResponse(
        request,
        "beats.html",
        _beats_context(
            request=request,
            current_user=current_user,
            tracks=page_tracks,
            catalog=[_catalog_item(track) for track in page_tracks],
            total=total,
            page=page,
            per_page=per_page,
            search=q.strip(),
            genre=genre.strip(),
            mood=mood.strip(),
            min_price=min_price,
            max_price=max_price,
        ),
    )
