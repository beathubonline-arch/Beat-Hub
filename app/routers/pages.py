```text
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.search import run_search
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["pages"])

templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user: Optional[User], **extra):
    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }
    base.update(extra)
    return base


# ------------------------------------------------------------------
# HOME
# ------------------------------------------------------------------

@router.get("/")
def home(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    if q:
        found = run_search(db, q)

        return templates.TemplateResponse(
            request,
            "home.html",
            ctx(
                request,
                current_user,
                query=q,
                results=found["results"],
                total_results=found["total"],
            ),
        )

    return templates.TemplateResponse(
        request,
        "home.html",
        ctx(
            request,
            current_user,
            query="",
            results={},
            total_results=None,
        ),
    )


# ------------------------------------------------------------------
# SEARCH
# ------------------------------------------------------------------

@router.get("/search")
def search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    return home(
        request=request,
        q=q,
        db=db,
        current_user=current_user,
    )


# ------------------------------------------------------------------
# TERMS
# ------------------------------------------------------------------

@router.get("/terms")
def terms(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "terms.html",
        ctx(request, current_user),
    )


# ------------------------------------------------------------------
# PUBLIC CREATOR PROFILE / STORE
# ------------------------------------------------------------------

@router.get("/profile/{slug}")
def public_profile(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == slug,
            Profile.is_producer.is_(True),
        )
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Creator profile not found.",
        )

    tracks = list(profile.tracks or [])
    albums = list(profile.albums or [])

    tracks = [
        track
        for track in tracks
        if getattr(track, "is_available", False)
    ]

    public_albums = []

    for album in albums:
        if hasattr(album, "is_published"):
            if album.is_published:
                public_albums.append(album)
        else:
            public_albums.append(album)

    return templates.TemplateResponse(
        request,
        "profile.html",
        ctx(
            request,
            current_user,
            profile=profile,
            creator=profile.user,
            tracks=tracks,
            albums=public_albums,
        ),
    )


# ------------------------------------------------------------------
# BUYER / ARTIST ACCOUNT
# ------------------------------------------------------------------

@router.get("/account")
def account(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    if current_user.role.value == "creator":
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

    if current_user.role.value == "admin":
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url="/admin",
            status_code=303,
        )

    profile = getattr(current_user, "profile", None)

    return templates.TemplateResponse(
        request,
        "account.html",
        ctx(
            request,
            current_user,
            profile=profile,
        ),
    )
```
