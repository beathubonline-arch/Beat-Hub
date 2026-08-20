```python
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.profile import Profile
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
# PUBLIC CREATOR PROFILE
# ------------------------------------------------------------------

@router.get("/profile/{slug}")
def public_profile(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Public creator profile.

    Example:
        /profile/daveevo

    Profile URLs use Profile.slug, not the user's email.
    """

    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == slug,
            Profile.is_producer.is_(True),
        )
        .first()
    )

    if not profile:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="Creator profile not found.",
        )

    # Keep the profile's related creator available to the template.
    creator = profile.user

    return templates.TemplateResponse(
        request,
        "profile.html",
        ctx(
            request,
            current_user,
            profile=profile,
            creator=creator,
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
    """
    Buyer / Artist account page.

    Buyers use this page to access their BeatHub marketplace account.
    Creators and admins are redirected to their appropriate dashboards.
    """

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
