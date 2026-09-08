from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.utils.deps import SESSION_COOKIE_NAME, ADMIN_SESSION_SUBJECT, get_role_name
from app.utils.security import create_access_token, verify_password

router = APIRouter(prefix="/admin", tags=["admin-auth"])
templates = Jinja2Templates(directory="app/templates")


def _safe_next(value: str) -> str:
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "://" in value:
        return "/admin"
    return value


def _set_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=bool(settings.is_production),
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


@router.get("/login")
async def admin_login_page(request: Request, next: str = "/admin", error: str = ""):
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"request": request, "next": _safe_next(next), "error": error},
    )


@router.post("/login")
async def admin_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    role = get_role_name(user)

    if user is None or role != "admin" or not getattr(user, "is_active", True) or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"request": request, "next": _safe_next(next), "error": "Invalid administrator email or password."},
            status_code=401,
        )

    token = create_access_token(str(user.id), {"role": "admin", "admin": True})
    response = RedirectResponse(url=_safe_next(next), status_code=303)
    _set_cookie(response, token)
    return response


@router.get("/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
