from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track
from app.models.order import License, Order, OrderStatus
from app.models.user import User, UserRole
from app.models.profile import Profile
from app.services.search import run_search
from app.services.r2_download import r2_download_url
from app.utils.deps import get_optional_user, require_user, get_role_name
from app.utils.text import unique_slug

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user: Optional[User], **extra):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,
    }
    context.update(extra)
    return context


def _local_media_path(stored_path: str) -> Optional[Path]:
    value = str(stored_path or "").strip()
    if not value or value.startswith(("http://", "https://", "r2://", "s3://")):
        return None
    stored = Path(value)
    media_root_value = getattr(settings, "MEDIA_ROOT", None) or "media"
    media_root = Path(media_root_value).expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    media_root = media_root.resolve()
    candidates = []
    if stored.is_absolute():
        candidates.append(stored.resolve())
    else:
        candidates.append((Path.cwd() / stored).resolve())
        candidates.append((media_root / stored).resolve())
        clean = str(stored).replace("\\", "/").lstrip("/")
        if clean.startswith("media/"):
            candidates.append((media_root / clean[6:]).resolve())
    for candidate in candidates:
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _media_content_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")


def _serve_local_media(stored_path: str):
    path = _local_media_path(stored_path)
    if not path:
        raise HTTPException(status_code=404, detail="Media file not found.")
    return FileResponse(path=str(path), media_type=_media_content_type(path), headers={"Cache-Control": "public, max-age=3600"})


def _safe_download_filename(track: Track, stored_path: str) -> str:
    title = getattr(track, "title", None) or "BeatHub_Track"
    safe_title = "".join(c if c.isalnum() or c in " ._-" else "_" for c in str(title)).strip()
    suffix = Path(urlsplit(str(stored_path)).path).suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        suffix = ".mp3"
    return f"{safe_title or 'BeatHub_Track'}{suffix}"


# ============================================================
# ARTIST ACTIVATION
# ============================================================

@router.get("/artist/activate")
def artist_activate_page(
    request: Request,
    current_user: User = Depends(require_user),
):
    role = get_role_name(current_user)
    profile = getattr(current_user, "profile", None)

    if role == "creator" and profile is not None and bool(getattr(profile, "is_artist", False)):
        return RedirectResponse(url="/artist/studio", status_code=303)
    if role != "buyer":
        return RedirectResponse(url="/account", status_code=303)

    return templates.TemplateResponse(
        request,
        "artist_activate.html",
        ctx(request, current_user, profile=profile),
    )


@router.post("/artist/activate")
def artist_activate_submit(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
    stage_name: str = Form(...),
):
    # This is an explicit opt-in promotion. A buyer is never promoted merely
    # because profile metadata says artist/producer.
    if get_role_name(current_user) != "buyer":
        role = get_role_name(current_user)
        if role == "creator":
            return RedirectResponse(url="/artist/studio", status_code=303)
        return RedirectResponse(url="/account", status_code=303)

    profile = getattr(current_user, "profile", None)
    if profile is None:
        profile = db.query(Profile).filter(Profile.user_id == current_user.id).first()

    stage_name = (stage_name or "").strip()
    if not stage_name:
        return templates.TemplateResponse(
            request,
            "artist_activate.html",
            ctx(request, current_user, profile=profile, error="Artist name is required."),
            status_code=400,
        )
    if len(stage_name) > 255:
        return templates.TemplateResponse(
            request,
            "artist_activate.html",
            ctx(request, current_user, profile=profile, error="Artist name is too long."),
            status_code=400,
        )

    if profile is None:
        slug = unique_slug(db, Profile, stage_name, fallback_prefix="artist")
        profile = Profile(
            id=__import__("uuid").uuid4().hex,
            user_id=current_user.id,
            stage_name=stage_name,
            slug=slug,
            is_artist=True,
            is_producer=False,
        )
        db.add(profile)
    else:
        profile.stage_name = stage_name
        profile.is_artist = True
        profile.is_producer = False

    current_user.role = UserRole.CREATOR
    db.commit()
    db.refresh(current_user)

    return RedirectResponse(
        url="/artist/studio?success=Artist%20publishing%20is%20now%20enabled.",
        status_code=303,
    )


@router.get("/")
def home(request: Request, q: str = "", db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    query = (q or "").strip()
    if query:
        found = run_search(db, query)
        return templates.TemplateResponse(request, "home.html", ctx(request, current_user, query=query, results=found.get("results", {}), total_results=found.get("total", 0)))
    return templates.TemplateResponse(request, "home.html", ctx(request, current_user, query="", results={}, total_results=None))


@router.get("/search")
def search(request: Request, q: str = "", db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    return home(request=request, q=q, db=db, current_user=current_user)


@router.get("/terms")
def terms(request: Request, current_user: Optional[User] = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "terms.html", ctx(request, current_user))


@router.get("/account")
def account(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    role = get_role_name(current_user)
    if role == "creator":
        profile = getattr(current_user, "profile", None)
        if profile is not None and bool(getattr(profile, "is_artist", False)):
            return RedirectResponse(url="/artist/studio", status_code=303)
        return RedirectResponse(url="/dashboard", status_code=303)
    if role == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    if role == "buyer":
        return RedirectResponse(url="/artist/activate", status_code=303)
    profile = getattr(current_user, "profile", None)
    completed_orders = db.query(Order).filter(Order.buyer_id == current_user.id, Order.status == OrderStatus.COMPLETED).order_by(Order.completed_at.desc()).all()
    pending_orders = db.query(Order).filter(Order.buyer_id == current_user.id, Order.status == OrderStatus.PENDING).order_by(Order.created_at.desc()).all()
    total_spent = sum((order.gross_amount or 0 for order in completed_orders), 0)
    return templates.TemplateResponse(request, "account.html", ctx(request, current_user, profile=profile, completed_orders=completed_orders, pending_orders=pending_orders, purchase_count=len(completed_orders), total_spent=total_spent))


# This compatibility route is intentionally registered before dashboard.py in main.py.
# It keeps /dashboard secure: buyers see the explicit opt-in publishing page;
# creators are handed to the real creator dashboard implementation.
@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_entry(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
    page: int = 1,
    q: str = "",
):
    role = get_role_name(current_user)
    if role == "buyer":
        return RedirectResponse(url="/artist/activate", status_code=303)
    if role == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    if role != "creator":
        raise HTTPException(status_code=403, detail=f"Creator access required. Current account role: {role or 'unknown'}.")

    from app.routers.dashboard import dashboard_home
    return dashboard_home(request=request, db=db, user=current_user, page=page, q=q)


@router.get("/purchases")
@router.get("/account/purchases")
def account_purchases(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    licenses = db.query(License).filter(License.buyer_id == current_user.id).order_by(License.granted_at.desc()).all()
    return templates.TemplateResponse(request, "account_purchases.html", ctx(request, current_user, licenses=licenses))


@router.get("/downloads")
@router.get("/account/downloads")
def account_downloads(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    licenses = db.query(License).filter(License.buyer_id == current_user.id).order_by(License.granted_at.desc()).all()
    return templates.TemplateResponse(request, "account_downloads.html", ctx(request, current_user, licenses=licenses))


@router.get("/download/track/{track_id}")
@router.get("/account/download/{track_id}")
def download_track(track_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    license_record = db.query(License).filter(License.buyer_id == current_user.id, License.track_id == track_id).first()
    if not license_record:
        raise HTTPException(status_code=403, detail="You do not own this track.")

    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    stored_path = getattr(track, "audio_file_path", None)
    if not stored_path:
        raise HTTPException(status_code=404, detail="The purchased audio file is currently unavailable.")

    stored_text = str(stored_path).strip()
    filename = _safe_download_filename(track, stored_text)

    if stored_text.lower().startswith(("r2://", "s3://")):
        signed_url = r2_download_url(stored_text, filename, expires=max(60, int(getattr(settings, "R2_DOWNLOAD_URL_EXPIRES", 900))))
        if not signed_url:
            raise HTTPException(status_code=503, detail="The purchased audio file is temporarily unavailable.")
        return RedirectResponse(url=signed_url, status_code=307)

    if stored_text.lower().startswith(("http://", "https://")):
        return RedirectResponse(url=stored_text, status_code=307)

    path = _local_media_path(stored_text)
    if not path:
        raise HTTPException(status_code=404, detail="The purchased audio file is currently unavailable.")

    return FileResponse(path=str(path), media_type=_media_content_type(path), filename=filename, headers={"Cache-Control": "private, no-store"})


@router.get("/account/orders")
def account_orders(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    orders = db.query(Order).filter(Order.buyer_id == current_user.id).order_by(Order.created_at.desc()).all()
    return templates.TemplateResponse(request, "account_orders.html", ctx(request, current_user, orders=orders))


@router.get("/account/settings")
def account_settings(request: Request, current_user: User = Depends(require_user)):
    return templates.TemplateResponse(request, "account_settings.html", ctx(request, current_user))


@router.get("/media/{media_path:path}", include_in_schema=False)
def media_file(media_path: str):
    clean = str(media_path or "").replace("\\", "/").lstrip("/")
    if not clean or clean.startswith((".", "../", "..\\")):
        raise HTTPException(status_code=404, detail="Media file not found.")
    return _serve_local_media(f"media/{clean}")


@router.get("/artist/dashboard", include_in_schema=False)
@router.get("/creator/dashboard", include_in_schema=False)
@router.get("/producer/dashboard", include_in_schema=False)
@router.get("/dashboard/home", include_in_schema=False)
@router.get("/dashboard/index", include_in_schema=False)
def dashboard_alias(current_user: User = Depends(require_user)):
    role = get_role_name(current_user)
    if role == "creator":
        profile = getattr(current_user, "profile", None)
        if profile is not None and bool(getattr(profile, "is_artist", False)):
            return RedirectResponse(url="/artist/studio", status_code=303)
        return RedirectResponse(url="/dashboard", status_code=303)
    if role == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/artist/activate", status_code=303)
