from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.search import run_search
from app.utils.deps import get_optional_user

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user: Optional[User], **extra):
    base = {"request": request, "current_user": current_user, "current_year": datetime.utcnow().year}
    base.update(extra)
    return base


@router.get("/")
def home(request: Request, q: str = "", db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    if q:
        found = run_search(db, q)
        return templates.TemplateResponse(request, 
            "home.html",
            ctx(request, current_user, query=q, results=found["results"], total_results=found["total"]),
        )
    return templates.TemplateResponse(request, "home.html", ctx(request, current_user, query="", results={}, total_results=None))


@router.get("/search")
def search(request: Request, q: str = "", db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    return home(request, q, db, current_user)


@router.get("/terms")
def terms(request: Request, current_user: Optional[User] = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "terms.html", ctx(request, current_user))
