from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.services.storage import r2_presigned_url
from app.utils.deps import get_optional_user

router = APIRouter(tags=["creator-store"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/creator/{slug}")
@router.get("/store/{slug}")
@router.get("/profile/{slug}")
def creator_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Render one creator store with explicit BeatHub content sections."""
    profile = db.query(Profile).filter(Profile.slug == slug).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Creator store not found.")

    published_tracks = []
    for track in list(getattr(profile, "tracks", None) or []):
        if not getattr(track, "is_published", True):
            continue

        sales_model = getattr(track, "sales_model", None)
        sales_value = getattr(sales_model, "value", sales_model)
        if str(sales_value or "").strip().lower() == "exclusive" and getattr(track, "is_sold", False):
            continue

        try:
            track.cover_art_url = (
                r2_presigned_url(track.cover_art_path)
                if getattr(track, "cover_art_path", None)
                else None
            )
        except Exception:
            track.cover_art_url = None

        published_tracks.append(track)

    beats = [
        track for track in published_tracks
        if str(getattr(track, "content_type", "beat") or "beat") == "beat"
    ]
    tracks = [
        track for track in published_tracks
        if str(getattr(track, "content_type", "beat") or "beat") == "track"
    ]

    albums = []
    for album in list(getattr(profile, "albums", None) or []):
        if not getattr(album, "is_published", True):
            continue

        try:
            album.artwork_url = (
                r2_presigned_url(album.artwork_path)
                if getattr(album, "artwork_path", None)
                else None
            )
        except Exception:
            album.artwork_url = None

        albums.append(album)

    is_owner = bool(
        current_user
        and str(getattr(current_user, "id", "")) == str(getattr(profile, "user_id", ""))
    )

    return templates.TemplateResponse(
        request,
        "creator_store.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "profile": profile,
            "creator": getattr(profile, "user", None),
            "beats": beats,
            "tracks": tracks,
            "albums": albums,
            "is_owner": is_owner,
            "beathub_discord_url": getattr(settings, "DISCORD_INVITE_URL", ""),
            "beathub_facebook_url": getattr(settings, "FACEBOOK_URL", ""),
        },
    )
