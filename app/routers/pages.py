from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.search import run_search
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["pages"])

templates = Jinja2Templates(directory="app/templates")


def ctx(
    request: Request,
    current_user: Optional[User],
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    context.update(extra)

    return context


@router.get("/")
def home(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    if q.strip():
        found = run_search(db, q.strip())

        return templates.TemplateResponse(
            request,
            "home.html",
            ctx(
                request,
                current_user,
                query=q,
                results=found.get("results", {}),
                total_results=found.get("total", 0),
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


@router.get("/beats")
def beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    found = run_search(db, "beats")

    return templates.TemplateResponse(
        request,
        "home.html",
        ctx(
            request,
            current_user,
            query="beats",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@router.get("/sessions")
def sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    found = run_search(db, "sessions")

    return templates.TemplateResponse(
        request,
        "home.html",
        ctx(
            request,
            current_user,
            query="sessions",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@router.get("/hot-picks")
def hot_picks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    found = run_search(db, "hot")

    return templates.TemplateResponse(
        request,
        "home.html",
        ctx(
            request,
            current_user,
            query="hot",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@router.get("/terms")
def terms(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "terms.html",
        ctx(
            request,
            current_user,
        ),
    )


@router.get("/profile/{slug}")
def public_profile(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
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


@router.get("/account")
def account(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    role = getattr(
        current_user.role,
        "value",
        current_user.role,
    )

    if role == "creator":
        return RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

    if role == "admin":
        return RedirectResponse(
            url="/admin",
            status_code=303,
        )

    profile = getattr(
        current_user,
        "profile",
        None,
    )

    return templates.TemplateResponse(
        request,
        "account.html",
        ctx(
            request,
            current_user,
            profile=profile,
        ),
    )
