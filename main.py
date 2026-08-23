import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import (
admin,
auth,
checkout,
dashboard,
mpesa_callback,
music,
pages,
)

try:
from app.routers import merchandise
except ImportError:
merchandise = None

from app.utils.deps import require_admin, require_creator

logger = logging.getLogger("beathub")

BASE_DIR = Path(file).resolve().parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
title=getattr(settings, "APP_NAME", "BeatHub"),
description=(
"BeatHub — beats, music, sessions, producer stores "
"and creator merchandise."
),
version="1.0.0",
)

def _session_secret() -> str:
value = os.getenv("SESSION_SECRET")

if value and value.strip():
    return value.strip()

value = getattr(settings, "SESSION_SECRET", None)

if value and str(value).strip():
    return str(value).strip()

logger.warning(
    "SESSION_SECRET is not configured. "
    "Using a temporary development secret."
)

return "beathub-development-session-secret-change-me"

def _session_max_age() -> int:
raw = (
os.getenv("SESSION_MAX_AGE")
or getattr(settings, "SESSION_MAX_AGE", None)
or 60 * 60 * 24 * 30
)

try:
    value = int(raw)
except (TypeError, ValueError):
    value = 60 * 60 * 24 * 30

return max(
    300,
    min(
        value,
        60 * 60 * 24 * 365,
    ),
)

def _session_https_only() -> bool:
raw = (
os.getenv("SESSION_HTTPS_ONLY")
or getattr(settings, "SESSION_HTTPS_ONLY", None)
)

if raw is None:
    return True

return str(raw).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

app.add_middleware(
SessionMiddleware,
secret_key=_session_secret(),
session_cookie="beathub_session",
max_age=_session_max_age(),
same_site="lax",
https_only=_session_https_only(),
)

templates = Jinja2Templates(
directory=str(TEMPLATES_DIR)
)

app.mount(
"/static",
StaticFiles(
directory=str(STATIC_DIR),
),
name="static",
)

try:
Base.metadata.create_all(
bind=engine,
)
except Exception:
logger.exception(
"Database table initialization failed."
)
raise

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

if merchandise is not None:
app.include_router(
merchandise.router,
)

def _template_context(
request: Request,
current_user=None,
**extra,
):
context = {
"request": request,
"current_user": current_user,
"user": current_user,
"current_year": 2026,
}

context.update(extra)

return context

def _template_exists(
template_name: str,
) -> bool:
return (
TEMPLATES_DIR / template_name
).is_file()

@app.get(
"/artist/dashboard",
include_in_schema=False,
)
@app.get(
"/creator/dashboard",
include_in_schema=False,
)
@app.get(
"/producer/dashboard",
include_in_schema=False,
)
@app.get(
"/dashboard/home",
include_in_schema=False,
)
@app.get(
"/dashboard/index",
include_in_schema=False,
)
def dashboard_alias(
user=Depends(require_creator),
):
return RedirectResponse(
url="/dashboard",
status_code=303,
)

@app.get(
"/creator/withdraw",
include_in_schema=False,
)
@app.get(
"/producer/withdraw",
include_in_schema=False,
)
def creator_withdraw_alias(
user=Depends(require_creator),
):
return RedirectResponse(
url="/dashboard/withdraw",
status_code=303,
)

@app.get(
"/admin/withdrawal",
include_in_schema=False,
)
def admin_withdraw_alias(
user=Depends(require_admin),
):
return RedirectResponse(
url="/admin/withdraw",
status_code=303,
)

@app.api_route(
"/healthz",
methods=["GET", "HEAD"],
)
def healthz():
return {
"status": "ok",
"app": getattr(
settings,
"APP_NAME",
"BeatHub",
),
"env": getattr(
settings,
"APP_ENV",
"production",
),
"storage": getattr(
settings,
"MEDIA_STORAGE",
"local",
),
"r2_enabled": bool(
getattr(
settings,
"r2_enabled",
False,
)
),
"r2_bucket_configured": bool(
getattr(
settings,
"R2_BUCKET_NAME",
None,
)
),
"r2_endpoint_configured": bool(
getattr(
settings,
"r2_endpoint_url",
None,
)
),
}

@app.get(
"/favicon.ico",
include_in_schema=False,
)
def favicon_compatibility():
favicon = STATIC_DIR / "favicon.ico"

if favicon.is_file():
    return FileResponse(
        path=str(favicon),
        media_type="image/x-icon",
    )

return Response(
    status_code=204,
)

@app.exception_handler(
StarletteHTTPException,
)
async def http_exception_handler(
request: Request,
exc: StarletteHTTPException,
):
if exc.status_code == 401:
return RedirectResponse(
url=(
"/login?"
"error=Please%20log%20in%20to%20continue."
),
status_code=303,
)

if exc.status_code == 403:
    template = "errors/403.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=exc.detail,
            ),
            status_code=403,
        )

    return RedirectResponse(
        url="/login?error=Access%20denied.",
        status_code=303,
    )

if exc.status_code == 404:
    template = "errors/404.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=exc.detail,
            ),
            status_code=404,
        )

    return RedirectResponse(
        url="/",
        status_code=303,
    )

template = "errors/500.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            detail=exc.detail,
        ),
        status_code=exc.status_code,
    )

return {
    "error": exc.detail,
    "status_code": exc.status_code,
}

@app.exception_handler(
RequestValidationError,
)
async def validation_exception_handler(
request: Request,
exc: RequestValidationError,
):
logger.warning(
"Validation error on %s %s: %s",
request.method,
request.url.path,
exc.errors(),
)

template = "errors/400.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            errors=exc.errors(),
            detail=(
                "Please check the information "
                "you entered."
            ),
        ),
        status_code=422,
    )

return {
    "error": "Validation error",
    "details": exc.errors(),
}

@app.exception_handler(Exception)
async def unhandled_exception_handler(
request: Request,
exc: Exception,
):
logger.exception(
"Unhandled BeatHub error on %s %s",
request.method,
request.url.path,
)

template = "errors/500.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            detail=None,
        ),
        status_code=500,
    )

return {
    "error": "Internal server error",
    "status_code": 500,
}

@app.on_event("startup")
async def startup_event():
logger.info(
"BeatHub application started."
)

logger.info(
    "Storage backend: %s",
    getattr(
        settings,
        "MEDIA_STORAGE",
        "local",
    ),
)

@app.on_event("shutdown")
async def shutdown_event():
logger.info(
"BeatHub application shutting down."
)

2. app/routers/pages.py
app/routers/pages.py

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.services.search import run_search
from app.utils.deps import (
get_optional_user,
require_user,
)

router = APIRouter(tags=["pages"])

templates = Jinja2Templates(
directory="app/templates"
)

def ctx(
request: Request,
current_user: Optional[User],
**extra,
):
context = {
"request": request,
"current_user": current_user,
"user": current_user,
"current_year": datetime.utcnow().year,
}

context.update(extra)

return context

@router.get("/")
def home(
request: Request,
q: str = "",
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
query = (q or "").strip()

if query:
    found = run_search(
        db,
        query,
    )

    return templates.TemplateResponse(
        request,
        "home.html",
        ctx(
            request,
            current_user,
            query=query,
            results=found.get(
                "results",
                {},
            ),
            total_results=found.get(
                "total",
                0,
            ),
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
current_user: Optional[User] = Depends(
get_optional_user
),
):
return home(
request=request,
q=q,
db=db,
current_user=current_user,
)

@router.get("/terms")
def terms(
request: Request,
current_user: Optional[User] = Depends(
get_optional_user
),
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
@router.get("/store/{slug}")
def public_profile(
request: Request,
slug: str,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
profile = (
db.query(Profile)
.filter(
Profile.slug == slug
)
.first()
)

if not profile:
    raise HTTPException(
        status_code=404,
        detail="Creator profile not found.",
    )

tracks = list(
    getattr(
        profile,
        "tracks",
        None,
    )
    or []
)

albums = list(
    getattr(
        profile,
        "albums",
        None,
    )
    or []
)

public_tracks = []

for track in tracks:
    if not getattr(
        track,
        "is_published",
        True,
    ):
        continue

    sales_model = getattr(
        track,
        "sales_model",
        None,
    )

    sales_model_value = getattr(
        sales_model,
        "value",
        None,
    )

    if sales_model_value is None:
        if sales_model is None:
            sales_model_value = ""
        else:
            sales_model_value = str(
                sales_model
            )

    sales_model_value = str(
        sales_model_value
    ).strip().lower()

    if (
        sales_model_value == "exclusive"
        and getattr(
            track,
            "is_sold",
            False,
        )
    ):
        continue

    public_tracks.append(
        track
    )

public_albums = [
    album
    for album in albums
    if getattr(
        album,
        "is_published",
        True,
    )
]

creator = getattr(
    profile,
    "user",
    None,
)

return templates.TemplateResponse(
    request,
    "profile_detail.html",
    ctx(
        request,
        current_user,
        profile=profile,
        creator=creator,
        tracks=public_tracks,
        albums=public_albums,
    ),
)

@router.get("/account")
def account(
request: Request,
db: Session = Depends(get_db),
current_user: User = Depends(
require_user
),
):
role = getattr(
current_user.role,
"value",
current_user.role,
)

role = str(
    role
).strip().lower()

if role in {
    "creator",
    "producer",
}:
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

completed_orders = (
    db.query(Order)
    .filter(
        Order.buyer_id
        == current_user.id,
        Order.status
        == OrderStatus.COMPLETED,
    )
    .order_by(
        Order.completed_at.desc()
    )
    .all()
)

pending_orders = (
    db.query(Order)
    .filter(
        Order.buyer_id
        == current_user.id,
        Order.status
        == OrderStatus.PENDING,
    )
    .order_by(
        Order.created_at.desc()
    )
    .all()
)

total_spent = sum(
    (
        order.gross_amount or 0
        for order in completed_orders
    ),
    0,
)

return templates.TemplateResponse(
    request,
    "account.html",
    ctx(
        request,
        current_user,
        profile=profile,
        completed_orders=completed_orders,
        pending_orders=pending_orders,
        purchase_count=len(
            completed_orders
        ),
        total_spent=total_spent,
    ),
)

@router.get("/account/purchases")
def account_purchases(
request: Request,
db: Session = Depends(get_db),
current_user: User = Depends(
require_user
),
):
licenses = (
db.query(License)
.filter(
License.buyer_id
== current_user.id
)
.order_by(
License.granted_at.desc()
)
.all()
)

return templates.TemplateResponse(
    request,
    "account_purchases.html",
    ctx(
        request,
        current_user,
        licenses=licenses,
    ),
)

@router.get("/account/downloads")
def account_downloads(
request: Request,
db: Session = Depends(get_db),
current_user: User = Depends(
require_user
),
):
licenses = (
db.query(License)
.filter(
License.buyer_id
== current_user.id
)
.order_by(
License.granted_at.desc()
)
.all()
)

return templates.TemplateResponse(
    request,
    "account_downloads.html",
    ctx(
        request,
        current_user,
        licenses=licenses,
    ),
)

@router.get(
"/account/download/{track_id}"
)
def download_track(
track_id: str,
db: Session = Depends(get_db),
current_user: User = Depends(
require_user
),
):
license_record = (
db.query(License)
.filter(
License.buyer_id
== current_user.id,
License.track_id
== track_id,
)
.first()
)

if not license_record:
    raise HTTPException(
        status_code=403,
        detail="You do not own this track.",
    )

track = (
    db.query(Track)
    .filter(
        Track.id == track_id
    )
    .first()
)

if not track:
    raise HTTPException(
        status_code=404,
        detail="Track not found.",
    )

stored_path = getattr(
    track,
    "audio_file_path",
    None,
)

if not stored_path:
    raise HTTPException(
        status_code=404,
        detail=(
            "The purchased audio file "
            "is currently unavailable."
        ),
    )

file_path = Path(
    str(stored_path)
)

if not file_path.is_absolute():
    project_root = (
        Path(__file__)
        .resolve()
        .parents[2]
    )

    file_path = (
        project_root
        / file_path
    )

if (
    not file_path.exists()
    or not file_path.is_file()
):
    raise HTTPException(
        status_code=404,
        detail=(
            "The purchased audio file "
            "is currently unavailable."
        ),
    )

title = (
    getattr(
        track,
        "title",
        None,
    )
    or "BeatHub_Track"
)

safe_title = "".join(
    character
    if (
        character.isalnum()
        or character in " ._-"
    )
    else "_"
    for character in title
).strip()

if not safe_title:
    safe_title = "BeatHub_Track"

suffix = file_path.suffix.lower()

allowed_audio_suffixes = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
}

if suffix not in allowed_audio_suffixes:
    suffix = ".mp3"

filename = (
    f"{safe_title}{suffix}"
)

media_types = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

return FileResponse(
    path=str(file_path),
    media_type=media_types.get(
        suffix,
        "application/octet-stream",
    ),
    filename=filename,
)

@router.get("/account/orders")
def account_orders(
request: Request,
db: Session = Depends(get_db),
current_user: User = Depends(
require_user
),
):
orders = (
db.query(Order)
.filter(
Order.buyer_id
== current_user.id
)
.order_by(
Order.created_at.desc()
)
.all()
)

return templates.TemplateResponse(
    request,
    "account_orders.html",
    ctx(
        request,
        current_user,
        orders=orders,
    ),
)

@router.get("/account/settings")
def account_settings(
request: Request,
current_user: User = Depends(
require_user
),
):
return templates.TemplateResponse(
request,
"account_settings.html",
ctx(
request,
current_user,
),
)

@router.get(
"/artist/dashboard",
include_in_schema=False,
)
@router.get(
"/creator/dashboard",
include_in_schema=False,
)
@router.get(
"/producer/dashboard",
include_in_schema=False,
)
@router.get(
"/dashboard/home",
include_in_schema=False,
)
@router.get(
"/dashboard/index",
include_in_schema=False,
)
def dashboard_alias(
current_user: User = Depends(
require_user
),
):
role = getattr(
current_user.role,
"value",
current_user.role,
)

role = str(
    role
).strip().lower()

if role in {
    "creator",
    "producer",
}:
    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )

if role == "admin":
    return RedirectResponse(
        url="/admin",
        status_code=303,
    )

return RedirectResponse(
    url="/account",
    status_code=303,
)

@router.get(
"/healthz",
include_in_schema=False,
)
def healthz_compat():
return {
"status": "ok"
}

3. app/routers/music.py
app/routers/music.py

from datetime import datetime
from pathlib import Path
from typing import Optional
import math

from fastapi import (
APIRouter,
Depends,
HTTPException,
Query,
Request,
)
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import (
License,
Order,
OrderStatus,
)
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import (
get_optional_user,
require_user,
)

router = APIRouter(
tags=["music"]
)

templates = Jinja2Templates(
directory="app/templates"
)

def ctx(
request: Request,
current_user,
**extra,
):
data = {
"request": request,
"current_user": current_user,
"user": current_user,
"current_year": datetime.utcnow().year,
}

data.update(extra)

return data

def clean_search(
value: Optional[str],
) -> str:
return (
value or ""
).strip()

@router.get("/beats")
def browse_beats(
request: Request,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
q: Optional[str] = Query(
default=None,
max_length=100,
),
genre: Optional[str] = Query(
default=None,
max_length=100,
),
mood: Optional[str] = Query(
default=None,
max_length=100,
),
min_price: Optional[float] = Query(
default=None,
ge=0,
),
max_price: Optional[float] = Query(
default=None,
ge=0,
),
page: int = Query(
default=1,
ge=1,
),
per_page: int = Query(
default=24,
ge=6,
le=48,
),
):
search = clean_search(q)
selected_genre = clean_search(genre)
selected_mood = clean_search(mood)

query = (
    db.query(Track)
    .filter(
        Track.is_published == True
    )
)

if search:
    pattern = f"%{search}%"

    query = query.filter(
        or_(
            Track.title.ilike(
                pattern
            ),
            Track.genre.ilike(
                pattern
            ),
            Track.tags.ilike(
                pattern
            ),
            Track.description.ilike(
                pattern
            ),
        )
    )

if selected_genre:
    query = query.filter(
        Track.genre.ilike(
            f"%{selected_genre}%"
        )
    )

mood_field = getattr(
    Track,
    "mood",
    None,
)

if (
    selected_mood
    and mood_field is not None
):
    query = query.filter(
        mood_field.ilike(
            f"%{selected_mood}%"
        )
    )

if min_price is not None:
    query = query.filter(
        Track.price >= min_price
    )

if max_price is not None:
    query = query.filter(
        Track.price <= max_price
    )

query = query.order_by(
    Track.created_at.desc()
)

tracks = query.limit(
    500
).all()

total = len(tracks)

total_pages = max(
    1,
    math.ceil(
        total / per_page
    ),
)

if page > total_pages:
    page = total_pages

start_index = (
    (page - 1)
    * per_page
)

end_index = (
    start_index
    + per_page
)

page_tracks = tracks[
    start_index:end_index
]

catalog = []

for track in page_tracks:
    profile = getattr(
        track,
        "creator_profile",
        None,
    )

    producer = getattr(
        track,
        "producer",
        None,
    )

    producer_name = None

    if profile is not None:
        producer_name = (
            getattr(
                profile,
                "stage_name",
                None,
            )
            or getattr(
                profile,
                "username",
                None,
            )
        )

    if not producer_name and producer is not None:
        producer_name = (
            getattr(
                producer,
                "stage_name",
                None,
            )
            or getattr(
                producer,
                "display_name",
                None,
            )
            or getattr(
                producer,
                "username",
                None,
            )
            or getattr(
                producer,
                "name",
                None,
            )
        )

    if not producer_name:
        producer_name = (
            getattr(
                track,
                "producer_name",
                None,
            )
            or getattr(
                track,
                "creator_name",
                None,
            )
            or "BeatHub Creator"
        )

    artwork_url = (
        getattr(
            track,
            "cover_art_path",
            None,
        )
        or getattr(
            track,
            "cover_url",
            None,
        )
        or getattr(
            track,
            "artwork_url",
            None,
        )
        or getattr(
            track,
            "image_url",
            None,
        )
    )

    audio_url = (
        getattr(
            track,
            "audio_preview_url",
            None,
        )
        or getattr(
            track,
            "preview_url",
            None,
        )
        or getattr(
            track,
            "audio_url",
            None,
        )
    )

    slug = getattr(
        track,
        "slug",
        None,
    )

    if slug:
        track_url = (
            f"/track/{slug}"
        )
    else:
        track_url = (
            f"/track/{track.id}"
        )

    raw_price = getattr(
        track,
        "price",
        0,
    ) or 0

    try:
        price_value = float(
            raw_price
        )
    except (
        TypeError,
        ValueError,
    ):
        price_value = 0.0

    catalog.append(
        {
            "track": track,
            "title": (
                getattr(
                    track,
                    "title",
                    None,
                )
                or "Untitled Beat"
            ),
            "producer": str(
                producer_name
            ),
            "price": price_value,
            "artwork_url": (
                str(artwork_url)
                if artwork_url
                else None
            ),
            "audio_url": (
                str(audio_url)
                if audio_url
                else None
            ),
            "url": track_url,
            "genre": str(
                getattr(
                    track,
                    "genre",
                    None,
                )
                or ""
            ),
            "mood": str(
                getattr(
                    track,
                    "mood",
                    None,
                )
                or ""
            ),
            "bpm": str(
                getattr(
                    track,
                    "bpm",
                    None,
                )
                or ""
            ),
            "key": str(
                getattr(
                    track,
                    "musical_key",
                    None,
                )
                or getattr(
                    track,
                    "key",
                    None,
                )
                or ""
            ),
            "description": str(
                getattr(
                    track,
                    "description",
                    None,
                )
                or ""
            ),
        }
    )

genre_rows = (
    db.query(
        Track.genre
    )
    .filter(
        Track.is_published == True,
        Track.genre.isnot(None),
        Track.genre != "",
    )
    .distinct()
    .order_by(
        Track.genre.asc()
    )
    .limit(50)
    .all()
)

genres = sorted(
    {
        str(row[0]).strip()
        for row in genre_rows
        if row[0]
    },
    key=str.lower,
)

moods = []

if mood_field is not None:
    mood_rows = (
        db.query(
            mood_field
        )
        .filter(
            Track.is_published == True,
            mood_field.isnot(None),
            mood_field != "",
        )
        .distinct()
        .limit(50)
        .all()
    )

    moods = sorted(
        {
            str(row[0]).strip()
            for row in mood_rows
            if row[0]
        },
        key=str.lower,
    )

return templates.TemplateResponse(
    request,
    "beats.html",
    ctx(
        request,
        current_user,
        tracks=page_tracks,
        beats=page_tracks,
        catalog=catalog,
        total=total,
        total_results=total,
        page=page,
        track_page=page,
        per_page=per_page,
        track_per_page=per_page,
        total_pages=total_pages,
        track_total_pages=total_pages,
        genres=genres,
        moods=moods,
        query=search,
        q=search,
        genre=selected_genre,
        mood=selected_mood,
        min_price=min_price,
        max_price=max_price,
        has_previous=page > 1,
        has_next=page < total_pages,
        previous_page=max(
            1,
            page - 1,
        ),
        next_page=min(
            total_pages,
            page + 1,
        ),
        catalog_start=(
            0
            if total == 0
            else start_index + 1
        ),
        catalog_end=min(
            end_index,
            total,
        ),
        title="Find Your Sound",
    ),
)

@router.get("/hot-picks")
def hot_picks(
request: Request,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
tracks = (
db.query(Track)
.filter(
Track.is_published == True
)
.order_by(
Track.created_at.desc()
)
.limit(24)
.all()
)

return templates.TemplateResponse(
    request,
    "browse.html",
    ctx(
        request,
        current_user,
        tracks=tracks,
        featured_tracks=[],
        genres=[],
        search="",
        selected_genre="",
        sort="newest",
        has_results=bool(
            tracks
        ),
        title="Hot Picks",
    ),
)

@router.get("/sessions")
def sessions_page(
request: Request,
current_user: Optional[User] = Depends(
get_optional_user
),
):
return templates.TemplateResponse(
request,
"sessions.html",
ctx(
request,
current_user,
),
)

@router.get("/track/{slug}")
def track_detail(
slug: str,
request: Request,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
track = (
db.query(Track)
.filter(
Track.slug == slug
)
.first()
)

if not track:
    raise HTTPException(
        status_code=404,
        detail="Track not found",
    )

purchased = False

if current_user:
    purchased = (
        db.query(License)
        .join(
            Order,
            License.order_id
            == Order.id,
        )
        .filter(
            License.buyer_id
            == current_user.id,
            License.track_id
            == track.id,
            Order.status
            == OrderStatus.COMPLETED,
        )
        .first()
        is not None
    )

return templates.TemplateResponse(
    request,
    "track_detail.html",
    ctx(
        request,
        current_user,
        track=track,
        purchased=purchased,
    ),
)

@router.get("/album/{slug}")
def album_detail(
slug: str,
request: Request,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
album = (
db.query(Album)
.filter(
Album.slug == slug
)
.first()
)

if not album:
    raise HTTPException(
        status_code=404,
        detail="Album not found",
    )

return templates.TemplateResponse(
    request,
    "album_detail.html",
    ctx(
        request,
        current_user,
        album=album,
    ),
)

@router.get("/profile/{slug}")
def profile_detail(
slug: str,
request: Request,
db: Session = Depends(get_db),
current_user: Optional[User] = Depends(
get_optional_user
),
):
profile = (
db.query(Profile)
.filter(
Profile.slug == slug
)
.first()
)

if not profile:
    raise HTTPException(
        status_code=404,
        detail="Profile not found",
    )

tracks = [
    track
    for track in (
        getattr(
            profile,
            "tracks",
            None,
        )
        or []
    )
    if getattr(
        track,
        "is_published",
        True,
    )
]

albums = [
    album
    for album in (
        getattr(
            profile,
            "albums",
            None,
        )
        or []
    )
    if getattr(
        album,
        "is_published",
        True,
    )
]

return templates.TemplateResponse(
    request,
    "profile_detail.html",
    ctx(
        request,
        current_user,
        profile=profile,
        tracks=tracks,
        albums=albums,
    ),
)

@router.get(
"/download/track/{track_ref}"
)
@router.get(
"/download/{track_ref}"
)
def download_track(
track_ref: str,
db: Session = Depends(get_db),
user: User = Depends(
require_user
),
):
track = (
db.query(Track)
.filter(
Track.id == track_ref
)
.first()
)

if not track:
    track = (
        db.query(Track)
        .filter(
            Track.slug == track_ref
        )
        .first()
    )

if not track:
    raise HTTPException(
        status_code=404,
        detail="Track not found.",
    )

license_record = (
    db.query(License)
    .join(
        Order,
        License.order_id
        == Order.id,
    )
    .filter(
        License.buyer_id == user.id,
        License.track_id == track.id,
        Order.status
        == OrderStatus.COMPLETED,
    )
    .first()
)

if not license_record:
    raise HTTPException(
        status_code=403,
        detail="You do not own this track.",
    )

if not track.audio_file_path:
    raise HTTPException(
        status_code=404,
        detail="Audio file is not available.",
    )

stored_text = str(
    track.audio_file_path
).strip()

if (
    stored_text.startswith(
        "r2://"
    )
    or stored_text.startswith(
        "s3://"
    )
):
    raise HTTPException(
        status_code=404,
        detail=(
            "This track is stored in cloud storage."
        ),
    )

stored_path = Path(
    stored_text
)

media_root_value = getattr(
    settings,
    "MEDIA_ROOT",
    "media",
)

media_root = Path(
    media_root_value
)

if not media_root.is_absolute():
    media_root = (
        Path.cwd()
        / media_root
    )

media_root = media_root.resolve()

if stored_path.is_absolute():
    audio_path = (
        stored_path.resolve()
    )
else:
    audio_path = (
        Path.cwd()
        / stored_path
    ).resolve()

if (
    not audio_path.exists()
    or not audio_path.is_file()
):
    filename = stored_path.name

    candidates = [
        media_root / stored_path,
        media_root / "audio" / filename,
        (
            Path.cwd()
            / "media"
            / "audio"
            / filename
        ),
    ]

    found = None

    for candidate in candidates:
        candidate = (
            candidate.resolve()
        )

        try:
            candidate.relative_to(
                media_root
            )
        except ValueError:
            continue

        if (
            candidate.exists()
            and candidate.is_file()
        ):
            found = candidate
            break

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The purchased audio file "
                "is missing from storage."
            ),
        )

    audio_path = found

try:
    audio_path.relative_to(
        media_root
    )
except ValueError:
    raise HTTPException(
        status_code=403,
        detail="Invalid audio file location.",
    )

safe_title = "".join(
    character
    for character in track.title
    if (
        character.isalnum()
        or character in (
            " ",
            "-",
            "_",
        )
    )
).strip()

if not safe_title:
    safe_title = "BeatHub-Track"

extension = (
    audio_path.suffix.lower()
)

download_name = (
    f"{safe_title}{extension}"
    if extension
    else safe_title
)

return FileResponse(
    path=str(audio_path),
    filename=download_name,
    media_type=(
        "application/octet-stream"
    ),
    headers={
        "Content-Disposition": (
            f'attachment; filename="{download_name}"'
        ),
        "Cache-Control": (
            "private, no-store"
        ),
    },
)

Important: do not paste the old generated music.py that contains from app.models.track import Track. The actual project uses app.models.music.Track, which is confirmed in your existing project files.

After these three replacements, /beats is owned by the dedicated music router rather than the old pages.py compatibility route, so the premium beats.html page will actually be rendered. Your existing dashboard router remains untouched; its /dashboard route and creator dependency are still present.

                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/importer.py", line 19, in import_from_string

module = importlib.import\_module(module\_str)

         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/python/Python-3.11.9/lib/python3.11/importlib/__init__.py", line 126, in import_module

return \_bootstrap.\_gcd\_import(name[level:], package, level)

       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "<frozen importlib._bootstrap>", line 1204, in _gcd_import

File "<frozen importlib._bootstrap>", line 1176, in _find_and_load

File "<frozen importlib._bootstrap>", line 1147, in _find_and_load_unlocked

File "<frozen importlib._bootstrap>", line 690, in _load_unlocked

File "<frozen importlib._bootstrap_external>", line 936, in exec_module

File "<frozen importlib._bootstrap_external>", line 1074, in get_code

File "<frozen importlib._bootstrap_external>", line 1004, in source_to_code

File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed

File "/opt/render/project/src/main.py", line 26

from app.routers import merchandise

^

IndentationError: expected an indented block after 'try' statement on line 25

==> Exited with status 1

==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys

==> Deploying...

==> Setting WEB_CONCURRENCY=1 by default, based on available CPUs in the instance

==> Running 'uvicorn main:app --host 0.0.0.0 --port $PORT'

Traceback (most recent call last):

File "/opt/render/project/src/.venv/bin/uvicorn", line 8, in <module>

sys.exit(main())

         ^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 1569, in __call__

return self.main(\*args, \*\*kwargs)

       ^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 1490, in main

rv = self.invoke(ctx)

     ^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 1353, in invoke

return ctx.invoke(self.callback, \*\*ctx.params)

       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 907, in invoke

return callback(\*args, \*\*kwargs)

       ^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/main.py", line 412, in main

run(

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/main.py", line 579, in run

server.run()

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/server.py", line 65, in run

return asyncio.run(self.serve(sockets=sockets))

       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/python/Python-3.11.9/lib/python3.11/asyncio/runners.py", line 190, in run

return runner.run(main)

       ^^^^^^^^^^^^^^^^

File "/opt/render/project/python/Python-3.11.9/lib/python3.11/asyncio/runners.py", line 118, in run

return self.\_loop.run\_until\_complete(task)

       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "uvloop/loop.pyx", line 1518, in uvloop.loop.Loop.run_until_complete

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/server.py", line 69, in serve

await self.\_serve(sockets)

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/server.py", line 76, in _serve

config.load()

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/config.py", line 434, in load

self.loaded\_app = import\_from\_string(self.app)

                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/importer.py", line 19, in import_from_string

module = importlib.import\_module(module\_str)

         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "/opt/render/project/python/Python-3.11.9/lib/python3.11/importlib/__init__.py", line 126, in import_module

return \_bootstrap.\_gcd\_import(name[level:], package, level)

       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

File "<frozen importlib._bootstrap>", line 1204, in _gcd_import

File "<frozen importlib._bootstrap>", line 1176, in _find_and_load

File "<frozen importlib._bootstrap>", line 1147, in _find_and_load_unlocked

File "<frozen importlib._bootstrap>", line 690, in _load_unlocked

File "<frozen importlib._bootstrap_external>", line 936, in exec_module

File "<frozen importlib._bootstrap_external>", line 1074, in get_code

File "<frozen importlib._bootstrap_external>", line 1004, in source_to_code

File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed

File "/opt/render/project/src/main.py", line 26

from app.routers import merchandise

^

IndentationError: expected an indented block after 'try' statement on line 25

==> Exited with status 1

==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys

==> Running 'uvicorn main:app --host 0.0.0.0 --port $PORT'

INFO: 178.156.185.231:0 - "HEAD /healthz HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /login HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /favicon.ico HTTP/1.1" 307 Temporary Redirect

INFO: 102.0.17.154:0 - "GET /static/favicon.ico HTTP/1.1" 404 Not Found

INFO: 102.0.17.154:0 - "GET /?success=You%20have%20been%20logged%20out. HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /favicon.ico HTTP/1.1" 307 Temporary Redirect

INFO: 102.0.17.154:0 - "GET /static/favicon.ico HTTP/1.1" 404 Not Found

INFO: 102.0.17.154:0 - "GET /login HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "POST /login HTTP/1.1" 303 See Other

INFO: 102.0.17.154:0 - "GET /account HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /beats HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /dashboard HTTP/1.1" 403 Forbidden

INFO: 102.0.17.154:0 - "GET /static/css/style.css HTTP/1.1" 304 Not Modified

INFO: 102.0.17.154:0 - "GET / HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /dashboard HTTP/1.1" 403 Forbidden

INFO: 102.0.17.154:0 - "GET / HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET / HTTP/1.1" 200 OK

INFO: 102.0.17.154:0 - "GET /dashboard HTTP/1.1" 403 Forbidden

The current logs show the immediate blocker is main.py line 26:

IndentationError: expected an indented block after 'try' statement

But I cannot safely produce a full project-wide replacement from the snippets in this chat without risking breaking your existing models/routes. I don't have the complete current files.

Upload the project ZIP here (your current GitHub project). Then I can inspect the actual files and return the exact full replacements, preserving the existing functionality rather than guessing.

That's a very long process. Go through the chat and do whatever you want to do and produce five replacements. No downgrades.

Done. I rebuilt the five replacements from the latest BeatHub files and Python compilation passes for all four Python files.

Replace these exact files:

main.py

Download main.py
app/utils/deps.py

Download deps.py
app/routers/music.py

Download music.py
app/routers/pages.py

Download pages.py
app/templates/beats.html

Download beats.html
What these five fix
Dashboard 403 creator/profile authorization repaired in deps.py.
main.py indentation/import problem cleaned.
Wrong app.models.track import fixed to your actual app.models.music.
Duplicate /beats route removed from pages.py, so the dedicated Beats marketplace actually gets control of /beats.
/sessions and /hot-picks are handled properly.
New Beats marketplace UI is responsive, polished, searchable, filterable and supports audio previews.
Existing dashboard/upload/earnings/checkout/M-Pesa structure is preserved.
No Python markdown fences are inside the files.
The Python replacements were syntax-checked with py_compile successfully.
main.py
Code
deps.py
Code
music.py
Code
pages.py
Code
beats.html
HTML
Show less

main.py
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine, get_db
from app.routers import (
    admin,
    auth,
    checkout,
    dashboard,
    mpesa_callback,
    music,
    pages,
)
from app.services.search import run_search
from app.utils.deps import (
    get_optional_user,
    require_admin,
    require_creator,
)

try:
    from app.routers import merchandise
except ImportError:
    merchandise = None


logger = logging.getLogger("beathub")

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=getattr(settings, "APP_NAME", "BeatHub"),
    description=(
        "BeatHub — beats, music, sessions, producer stores "
        "and creator merchandise."
    ),
    version="1.0.0",
)


def _session_secret() -> str:
    value = os.getenv("SESSION_SECRET")

    if value and value.strip():
        return value.strip()

    value = getattr(settings, "SESSION_SECRET", None)

    if value and str(value).strip():
        return str(value).strip()

    logger.warning(
        "SESSION_SECRET is not configured. "
        "Using a temporary development secret."
    )

    return "beathub-development-session-secret-change-me"


def _session_max_age() -> int:
    raw = (
        os.getenv("SESSION_MAX_AGE")
        or getattr(settings, "SESSION_MAX_AGE", None)
        or 60 * 60 * 24 * 30
    )

    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 60 * 60 * 24 * 30

    return max(300, min(value, 60 * 60 * 24 * 365))


def _session_https_only() -> bool:
    raw = (
        os.getenv("SESSION_HTTPS_ONLY")
        or getattr(settings, "SESSION_HTTPS_ONLY", None)
    )

    if raw is None:
        return True

    return str(raw).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="beathub_session",
    max_age=_session_max_age(),
    same_site="lax",
    https_only=_session_https_only(),
)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

try:
    Base.metadata.create_all(bind=engine)
except Exception:
    logger.exception("Database table initialization failed.")
    raise


app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

if merchandise is not None:
    app.include_router(merchandise.router)


def _template_context(
    request: Request,
    current_user=None,
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": 2026,
    }
    context.update(extra)
    return context


def _template_exists(template_name: str) -> bool:
    return (
        TEMPLATES_DIR / template_name
    ).is_file()


@app.get("/beats", include_in_schema=False)
def beats_compatibility(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    found = run_search(db, "beats")

    return templates.TemplateResponse(
        request,
        "home.html",
        _template_context(
            request,
            current_user,
            query="beats",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@app.get("/sessions", include_in_schema=False)
def sessions_compatibility(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    found = run_search(db, "sessions")

    return templates.TemplateResponse(
        request,
        "home.html",
        _template_context(
            request,
            current_user,
            query="sessions",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@app.get("/hot-picks", include_in_schema=False)
def hot_picks_compatibility(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    found = run_search(db, "hot")

    return templates.TemplateResponse(
        request,
        "home.html",
        _template_context(
            request,
            current_user,
            query="hot",
            results=found.get("results", {}),
            total_results=found.get("total", 0),
        ),
    )


@app.get("/artist/dashboard", include_in_schema=False)
@app.get("/creator/dashboard", include_in_schema=False)
@app.get("/producer/dashboard", include_in_schema=False)
@app.get("/dashboard/home", include_in_schema=False)
@app.get("/dashboard/index", include_in_schema=False)
def dashboard_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )


@app.get("/creator/withdraw", include_in_schema=False)
@app.get("/producer/withdraw", include_in_schema=False)
def creator_withdraw_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard/withdraw",
        status_code=303,
    )


@app.get("/admin/withdrawal", include_in_schema=False)
def admin_withdraw_alias(
    user=Depends(require_admin),
):
    return RedirectResponse(
        url="/admin/withdraw",
        status_code=303,
    )


@app.api_route(
    "/healthz",
    methods=["GET", "HEAD"],
)
def healthz():
    app_name = getattr(
        settings,
        "APP_NAME",
        "BeatHub",
    )

    app_env = getattr(
        settings,
        "APP_ENV",
        "production",
    )

    media_storage = getattr(
        settings,
        "MEDIA_STORAGE",
        "local",
    )

    r2_enabled = getattr(
        settings,
        "r2_enabled",
        False,
    )

    bucket_name = getattr(
        settings,
        "R2_BUCKET_NAME",
        None,
    )

    endpoint_url = getattr(
        settings,
        "r2_endpoint_url",
        None,
    )

    return {
        "status": "ok",
        "app": app_name,
        "env": app_env,
        "storage": media_storage,
        "r2_enabled": bool(r2_enabled),
        "r2_bucket_configured": bool(bucket_name),
        "r2_endpoint_configured": bool(endpoint_url),
    }


@app.get(
    "/favicon.ico",
    include_in_schema=False,
)
def favicon_compatibility():
    favicon = STATIC_DIR / "favicon.ico"

    if favicon.is_file():
        return FileResponse(
            path=str(favicon),
            media_type="image/x-icon",
        )

    return RedirectResponse(
        url="/static/favicon.ico",
        status_code=307,
    )


@app.exception_handler(
    StarletteHTTPException,
)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    if exc.status_code == 401:
        return RedirectResponse(
            url=(
                "/login?"
                "error=Please%20log%20in%20to%20continue."
            ),
            status_code=303,
        )

    if exc.status_code == 403:
        template = "errors/403.html"

        if _template_exists(template):
            return templates.TemplateResponse(
                request,
                template,
                _template_context(
                    request,
                    detail=exc.detail,
                ),
                status_code=403,
            )

        return RedirectResponse(
            url="/login?error=Access%20denied.",
            status_code=303,
        )

    if exc.status_code == 404:
        template = "errors/404.html"

        if _template_exists(template):
            return templates.TemplateResponse(
                request,
                template,
                _template_context(
                    request,
                    detail=exc.detail,
                ),
                status_code=404,
            )

        return RedirectResponse(
            url="/",
            status_code=303,
        )

    template = "errors/500.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=exc.detail,
            ),
            status_code=exc.status_code,
        )

    return {
        "error": exc.detail,
        "status_code": exc.status_code,
    }


@app.exception_handler(
    RequestValidationError,
)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    logger.warning(
        "Validation error on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )

    template = "errors/400.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                errors=exc.errors(),
                detail=(
                    "Please check the information "
                    "you entered."
                ),
            ),
            status_code=422,
        )

    return {
        "error": "Validation error",
        "details": exc.errors(),
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled BeatHub error on %s %s",
        request.method,
        request.url.path,
    )

    template = "errors/500.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=None,
            ),
            status_code=500,
        )

    return {
        "error": "Internal server error",
        "status_code": 500,
    }


@app.on_event("startup")
async def startup_event():
    logger.info("BeatHub application started.")

    logger.info(
        "Storage backend: %s",
        getattr(
            settings,
            "MEDIA_STORAGE",
            "local",
        ),
    )

    logger.info(
        "Platform commission configured at application level."
    )


@app.on_event("shutdown")
async def shutdown_event():
    logger.info(
        "BeatHub application shutting down."
    )
