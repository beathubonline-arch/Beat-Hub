from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.storage import (
ALLOWED_AUDIO_EXT,
ALLOWED_IMAGE_EXT,
UploadValidationError,
save_upload,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="app/templates")

# ============================================================

# COMMON TEMPLATE CONTEXT

# ============================================================

def ctx(request: Request, current_user: User, **extra):
base = {
"request": request,
"current_user": current_user,
"current_year": datetime.utcnow().year,

```
    "profile": getattr(current_user, "profile", None),

    "stats": {},

    "available_balance": Decimal("0"),
    "pending_withdrawal": Decimal("0"),

    "total_sales": 0,
    "gross_revenue": Decimal("0"),
    "platform_commission": Decimal("0"),
    "net_earnings": Decimal("0"),

    "recent_orders": [],

    "withdrawal_requests": [],

    "track_count": 0,
    "album_count": 0,

    "tracks": [],
    "albums": [],

    "track_page": 1,
    "track_total_pages": 1,
    "track_total": 0,
    "track_total_count": 0,
    "track_per_page": 12,
    "track_search": "",
    "track_start": 0,
    "track_end": 0,

    "q": "",

    "youtube_url": None,
    "discord_url": None,
    "store_url": None,
}

base.update(extra)
return base
```

# ============================================================

# DECIMAL HELPERS

# ============================================================

def _decimal(value) -> Decimal:
if value is None:
return Decimal("0")

```
if isinstance(value, Decimal):
    return value

try:
    return Decimal(str(value))
except Exception:
    return Decimal("0")
```

# ============================================================

# CREATOR STATISTICS

# ============================================================

def _creator_stats(db: Session, profile_id) -> dict:
orders = (
db.query(Order)
.join(Track, Order.track_id == Track.id)
.filter(
Track.creator_profile_id == profile_id,
Order.status == OrderStatus.COMPLETED,
)
.all()
)

```
gross = sum(
    (_decimal(order.gross_amount) for order in orders),
    Decimal("0"),
)

commission = sum(
    (_decimal(order.commission_amount) for order in orders),
    Decimal("0"),
)

net = sum(
    (_decimal(order.net_amount) for order in orders),
    Decimal("0"),
)

withdrawn = (
    db.query(
        func.coalesce(
            func.sum(WithdrawalRequest.amount),
            0,
        )
    )
    .filter(
        WithdrawalRequest.creator_profile_id == profile_id,
        WithdrawalRequest.status.in_(
            [
                "approved",
                "processing",
                "paid",
            ]
        ),
    )
    .scalar()
)

pending_withdrawal = (
    db.query(
        func.coalesce(
            func.sum(WithdrawalRequest.amount),
            0,
        )
    )
    .filter(
        WithdrawalRequest.creator_profile_id == profile_id,
        WithdrawalRequest.status == "pending",
    )
    .scalar()
)

withdrawn_decimal = _decimal(withdrawn)
pending_decimal = _decimal(pending_withdrawal)

available_balance = (
    net
    - withdrawn_decimal
    - pending_decimal
)

if available_balance < Decimal("0"):
    available_balance = Decimal("0")

recent_orders = sorted(
    orders,
    key=lambda order: (
        order.completed_at
        or order.created_at
        or datetime.min
    ),
    reverse=True,
)[:8]

return {
    "total_sales": len(orders),
    "gross_revenue": gross,
    "platform_commission": commission,
    "net_earnings": net,
    "available_balance": available_balance,
    "pending_withdrawal": pending_decimal,
    "recent_orders": recent_orders,
}
```

# ============================================================

# WITHDRAWAL HISTORY

# ============================================================

def _withdrawal_history(
db: Session,
profile_id,
):
return (
db.query(WithdrawalRequest)
.filter(
WithdrawalRequest.creator_profile_id == profile_id
)
.order_by(
WithdrawalRequest.created_at.desc()
)
.all()
)

# ============================================================

# TRACK PAGINATION / SEARCH

# ============================================================

def _creator_tracks(
db: Session,
profile_id,
page: int = 1,
per_page: int = 12,
search: str = "",
):
page = max(page, 1)
per_page = max(1, min(per_page, 100))

```
query = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile_id
    )
)

search = (search or "").strip()

if search:
    pattern = f"%{search}%"

    query = query.filter(
        Track.title.ilike(pattern)
        | Track.genre.ilike(pattern)
        | Track.tags.ilike(pattern)
    )

total = query.count()

total_pages = max(
    1,
    (total + per_page - 1) // per_page,
)

if page > total_pages:
    page = total_pages

offset = (page - 1) * per_page

tracks = (
    query
    .order_by(Track.created_at.desc())
    .offset(offset)
    .limit(per_page)
    .all()
)

start = offset + 1 if total else 0
end = min(
    offset + len(tracks),
    total,
)

return {
    "tracks": tracks,
    "track_page": page,
    "track_total_pages": total_pages,
    "track_total": total,
    "track_total_count": total,
    "track_per_page": per_page,
    "track_search": search,
    "track_start": start,
    "track_end": end,
}
```

# ============================================================

# DASHBOARD CONTEXT

# ============================================================

def _dashboard_context(
request: Request,
db: Session,
user: User,
page: int = 1,
search: str = "",
):
profile = user.profile

```
if not profile:
    raise HTTPException(
        status_code=400,
        detail="Creator profile missing.",
    )

stats = _creator_stats(
    db,
    profile.id,
)

track_count = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
    .count()
)

album_count = (
    db.query(Album)
    .filter(
        Album.creator_profile_id == profile.id
    )
    .count()
)

withdrawal_requests = _withdrawal_history(
    db,
    profile.id,
)

track_data = _creator_tracks(
    db,
    profile.id,
    page=page,
    per_page=12,
    search=search,
)

slug = getattr(
    profile,
    "slug",
    None,
)

store_url = None

if slug:
    store_url = f"/creator/{quote(str(slug))}"

youtube_url = getattr(
    settings,
    "YOUTUBE_CHANNEL_ID",
    None,
)

if youtube_url:
    if str(youtube_url).startswith("http"):
        youtube_url = str(youtube_url)
    else:
        youtube_url = (
            "https://www.youtube.com/channel/"
            + str(youtube_url)
        )

discord_url = getattr(
    settings,
    "DISCORD_INVITE_URL",
    None,
)

return ctx(
    request,
    user,

    profile=profile,

    stats=stats,

    total_sales=stats["total_sales"],
    gross_revenue=stats["gross_revenue"],
    platform_commission=stats[
        "platform_commission"
    ],
    net_earnings=stats["net_earnings"],
    available_balance=stats[
        "available_balance"
    ],
    pending_withdrawal=stats[
        "pending_withdrawal"
    ],
    recent_orders=stats[
        "recent_orders"
    ],

    track_count=track_count,
    album_count=album_count,

    withdrawal_requests=withdrawal_requests,

    youtube_url=youtube_url,
    discord_url=discord_url,
    store_url=store_url,

    **track_data,

    albums=(
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .order_by(Album.created_at.desc())
        .all()
    ),

    q=search,
)
```

# ============================================================

# CREATOR DASHBOARD

# ============================================================

@router.get("/dashboard")
def dashboard_home(
request: Request,
page: int = 1,
q: str = "",
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
context = _dashboard_context(
request,
db,
user,
page=page,
search=q,
)

```
success = request.query_params.get("success")
error = request.query_params.get("error")

if success:
    context["success"] = success

if error:
    context["error"] = error

return templates.TemplateResponse(
    request,
    "dashboard.html",
    context,
)
```

# ============================================================

# UPLOAD TRACK PAGE

# ============================================================

@router.get("/dashboard/upload")
def upload_page(
request: Request,
user: User = Depends(require_creator),
):
return templates.TemplateResponse(
request,
"upload_track.html",
ctx(
request,
user,
),
)

# ============================================================

# UPLOAD TRACKS

# ============================================================

@router.post("/dashboard/upload")
async def upload_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

```
titles: List[str] = Form(...),
descriptions: List[str] = Form(...),
genres: List[str] = Form(...),
bpms: List[str] = Form(...),
tags_list: List[str] = Form(...),
prices: List[str] = Form(...),
sales_models: List[str] = Form(...),

audio_files: List[UploadFile] = File(...),
cover_files: List[
    Optional[UploadFile]
] = File(None),
```

):
profile = user.profile

```
def error(message: str):
    return templates.TemplateResponse(
        request,
        "upload_track.html",
        ctx(
            request,
            user,
            error=message,
        ),
        status_code=400,
    )

if not titles or not audio_files:
    return error(
        "At least one track with an audio file is required."
    )

if len(titles) != len(audio_files):
    return error(
        "Track details and audio files don't match up."
    )

created = []

try:
    for i, audio_file in enumerate(audio_files):
        title = (
            titles[i]
            if i < len(titles)
            else ""
        )

        title = (title or "").strip()

        if not title:
            return error(
                "Every track needs a title."
            )

        bpm_raw = (
            bpms[i].strip()
            if i < len(bpms)
            else ""
        )

        bpm_val = None

        if bpm_raw:
            if not bpm_raw.isdigit():
                return error(
                    f"BPM for '{title}' must be a whole number."
                )

            bpm_val = int(bpm_raw)

            if bpm_val < 1 or bpm_val > 999:
                return error(
                    f"BPM for '{title}' must be between 1 and 999."
                )

        price_raw = (
            prices[i].strip()
            if i < len(prices)
            else "0"
        )

        try:
            price_val = Decimal(price_raw)

            if price_val < 0:
                raise ValueError

        except Exception:
            return error(
                f"Price for '{title}' is invalid."
            )

        model_raw = (
            sales_models[i]
            if i < len(sales_models)
            else "non_exclusive"
        )

        if model_raw == "exclusive":
            sales_model = SalesModel.EXCLUSIVE
        else:
            sales_model = SalesModel.NON_EXCLUSIVE

        audio_path = await save_upload(
            audio_file,
            "audio",
            ALLOWED_AUDIO_EXT,
        )

        cover_path = None

        if (
            cover_files
            and i < len(cover_files)
            and cover_files[i] is not None
            and cover_files[i].filename
        ):
            cover_path = await save_upload(
                cover_files[i],
                "covers",
                ALLOWED_IMAGE_EXT,
            )

        slug = unique_slug(
            db,
            Track,
            title,
            "track",
        )

        track = Track(
            creator_profile_id=profile.id,
            title=title,
            slug=slug,

            description=(
                descriptions[i].strip()
                if i < len(descriptions)
                else None
            ) or None,

            genre=(
                genres[i].strip()
                if i < len(genres)
                else None
            ) or None,

            bpm=bpm_val,

            tags=(
                tags_list[i].strip()
                if i < len(tags_list)
                else None
            ) or None,

            audio_file_path=audio_path,
            cover_art_path=cover_path,

            price=price_val,
            sales_model=sales_model,
        )

        db.add(track)
        created.append(track)

    db.commit()

except UploadValidationError as exc:
    db.rollback()

    return error(
        str(exc)
    )

except Exception:
    db.rollback()
    raise

message = (
    f"{len(created)} track(s) uploaded successfully."
)

return RedirectResponse(
    url="/dashboard?success="
    + quote(message),
    status_code=303,
)
```

# ============================================================

# CREATE ALBUM PAGE

# ============================================================

@router.get("/dashboard/albums/new")
def new_album_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

```
tracks = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
    .order_by(Track.created_at.desc())
    .all()
)

return templates.TemplateResponse(
    request,
    "upload_album.html",
    ctx(
        request,
        user,
        tracks=tracks,
    ),
)
```

# ============================================================

# CREATE ALBUM

# ============================================================

@router.post("/dashboard/albums/new")
async def new_album_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

```
title: str = Form(...),
description: str = Form(""),
genre: str = Form(""),
artwork: Optional[UploadFile] = File(None),
track_ids: List[str] = Form([]),
```

):
profile = user.profile

```
def error(message: str):
    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(Track.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "upload_album.html",
        ctx(
            request,
            user,
            tracks=tracks,
            error=message,
        ),
        status_code=400,
    )

title = (title or "").strip()

if not title:
    return error(
        "Album title is required."
    )

if not track_ids:
    return error(
        "Select at least one track for this album."
    )

artwork_path = None

if artwork and artwork.filename:
    try:
        artwork_path = await save_upload(
            artwork,
            "artwork",
            ALLOWED_IMAGE_EXT,
        )

    except UploadValidationError as exc:
        return error(str(exc))

slug = unique_slug(
    db,
    Album,
    title,
    "album",
)

album = Album(
    creator_profile_id=profile.id,
    title=title,
    slug=slug,
    description=(
        description.strip()
        or None
    ),
    genre=(
        genre.strip()
        or None
    ),
    artwork_path=artwork_path,
)

db.add(album)
db.flush()

valid_tracks = (
    db.query(Track)
    .filter(
        Track.id.in_(track_ids),
        Track.creator_profile_id == profile.id,
    )
    .all()
)

if not valid_tracks:
    db.rollback()

    return error(
        "None of the selected tracks belong to your account."
    )

track_map = {
    str(track.id): track
    for track in valid_tracks
}

position = 0

for track_id in track_ids:
    track = track_map.get(
        str(track_id)
    )

    if not track:
        continue

    db.add(
        AlbumTrack(
            album_id=album.id,
            track_id=track.id,
            position=position,
        )
    )

    position += 1

db.commit()

return RedirectResponse(
    url=(
        f"/album/{quote(str(album.slug))}"
        "?success=Album%20created."
    ),
    status_code=303,
)
```

# ============================================================

# WITHDRAWAL PAGE

# ============================================================

@router.get("/dashboard/withdraw")
def withdrawal_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

```
if not profile:
    raise HTTPException(
        status_code=400,
        detail="Creator profile missing.",
    )

stats = _creator_stats(
    db,
    profile.id,
)

withdrawal_requests = _withdrawal_history(
    db,
    profile.id,
)

return templates.TemplateResponse(
    request,
    "withdraw.html",
    ctx(
        request,
        user,

        profile=profile,

        stats=stats,

        available_balance=stats[
            "available_balance"
        ],

        pending_withdrawal=stats[
            "pending_withdrawal"
        ],

        withdrawal_requests=withdrawal_requests,
    ),
)
```

# ============================================================

# WITHDRAWAL REQUEST

# ============================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

```
amount: str = Form(...),
phone_number: str = Form(...),
```

):
profile = user.profile

```
if not profile:
    raise HTTPException(
        status_code=400,
        detail="Creator profile missing.",
    )

stats = _creator_stats(
    db,
    profile.id,
)

try:
    amount_val = Decimal(
        str(amount).strip()
    )

except Exception:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw"
            "?error=Invalid%20withdrawal%20amount."
        ),
        status_code=303,
    )

if amount_val <= 0:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw"
            "?error=Withdrawal%20amount%20must%20be%20positive."
        ),
        status_code=303,
    )

if amount_val > stats["available_balance"]:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw"
            "?error=Withdrawal%20exceeds%20your%20available%20balance."
        ),
        status_code=303,
    )

phone_number = (
    phone_number or ""
).strip()

if not phone_number:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw"
            "?error=M-Pesa%20phone%20number%20is%20required."
        ),
        status_code=303,
    )

withdrawal = WithdrawalRequest(
    creator_profile_id=profile.id,
    amount=amount_val,
    phone_number=phone_number,
    status="pending",
)

db.add(withdrawal)
db.commit()

return RedirectResponse(
    url=(
        "/dashboard/withdraw"
        "?success=Withdrawal%20request%20submitted."
    ),
    status_code=303,
)
```

# ============================================================

# PUBLIC CREATOR STORE

#

# IMPORTANT:

# The dashboard generates:

#

# /creator/{profile.slug}

#

# This route makes that exact URL work.

# ============================================================

@router.get("/creator/{slug}")
def creator_store(
request: Request,
slug: str,
db: Session = Depends(get_db),
):
from app.models.user import CreatorProfile

```
profile = (
    db.query(CreatorProfile)
    .filter(
        CreatorProfile.slug == slug
    )
    .first()
)

if not profile:
    raise HTTPException(
        status_code=404,
        detail="Creator store not found.",
    )

tracks = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
    .order_by(Track.created_at.desc())
    .all()
)

albums = (
    db.query(Album)
    .filter(
        Album.creator_profile_id == profile.id
    )
    .order_by(Album.created_at.desc())
    .all()
)

return templates.TemplateResponse(
    request,
    "creator_store.html",
    ctx(
        request,
        None,

        current_user=None,

        profile=profile,

        creator=profile,

        tracks=tracks,

        albums=albums,

        store_url=f"/creator/{quote(str(profile.slug))}",
    ),
)
```

# ============================================================

# LEGACY PUBLIC PROFILE COMPATIBILITY

#

# Keep /profile/{slug} working for older links.

# ============================================================

@router.get("/profile/{slug}")
def creator_profile_legacy(
request: Request,
slug: str,
db: Session = Depends(get_db),
):
from app.models.user import CreatorProfile

```
profile = (
    db.query(CreatorProfile)
    .filter(
        CreatorProfile.slug == slug
    )
    .first()
)

if not profile:
    raise HTTPException(
        status_code=404,
        detail="Creator profile not found.",
    )

tracks = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
    .order_by(Track.created_at.desc())
    .all()
)

albums = (
    db.query(Album)
    .filter(
        Album.creator_profile_id == profile.id
    )
    .order_by(Album.created_at.desc())
    .all()
)

return templates.TemplateResponse(
    request,
    "creator_store.html",
    ctx(
        request,
        None,

        current_user=None,

        profile=profile,

        creator=profile,

        tracks=tracks,

        albums=albums,

        store_url=f"/creator/{quote(str(profile.slug))}",
    ),
)
```
