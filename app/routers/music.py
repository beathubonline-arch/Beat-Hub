from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, Track
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import r2_url
from app.utils.deps import get_optional_user


router = APIRouter(
    tags=["music"],
)

templates = Jinja2Templates(
    directory="app/templates",
)


# ============================================================
# PUBLIC CREATOR STORE
# ============================================================

@router.get("/store/{slug}")
def creator_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(
        get_optional_user
    ),
):
    """
    Public storefront for a creator
    (producer / DJ / artist).
    """

    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Creator not found.",
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id,
            Track.is_published.is_(True),
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    albums = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id,
            Album.is_published.is_(True),
        )
        .order_by(
            Album.created_at.desc()
        )
        .all()
    )

    avatar_url = None

    if getattr(profile, "avatar_path", None):
        try:
            avatar_url = r2_url(
                profile.avatar_path,
                expires=3600,
            )
        except Exception:
            avatar_url = None

    return templates.TemplateResponse(
        request,
        "store.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "profile": profile,
            "tracks": tracks,
            "albums": albums,
            "avatar_url": avatar_url,
        },
    )
