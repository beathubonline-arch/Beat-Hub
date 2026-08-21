Yes. I checked the previous versions you pasted and the stored versions of the BeatHub dashboard code. The immediate problem is clear:

Your dashboard is generating/using /creator/mr-mapema.
Your public profile route exists as /profile/{slug} in the current architecture.
Therefore /creator/mr-mapema has no matching route and returns 404.
Your dashboard also needs the complete top-level variables used by the dashboard template, plus the withdrawal GET page.
I am keeping the existing upload, album, earnings, withdrawal, pagination, and store functionality. The newer dashboard versions specifically used pagination variables and an absolute store URL derived from the incoming Render request.
The previous dashboard also had the correct separation between stored R2 paths and temporary artwork URLs; that should not be destroyed.

Replace app/routers/dashboard.py completely with this. Do not put ```python inside the file.

app/routers/dashboard.py — Complete Replacement

from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import List, Optional
from urllib.parse import quote

from fastapi import (
APIRouter,
Depends,
File,
Form,
HTTPException,
Request,
UploadFile,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest, WithdrawalStatus
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.profile import Profile
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

----------------------------------------------------------------------
COMMON CONTEXT
----------------------------------------------------------------------

def ctx(
request: Request,
current_user,
**extra,
):
"""
Common template context.

Keep the broad set of compatibility variables here because the
BeatHub templates have evolved through several versions.
"""
base = {
    "request": request,
    "current_user": current_user,
    "current_year": datetime.utcnow().year,

    # Safe defaults.
    "available_balance": Decimal("0"),
    "pending_withdrawal": Decimal("0"),
    "total_sales": 0,
    "gross_revenue": Decimal("0"),
    "platform_commission": Decimal("0"),
    "net_earnings": Decimal("0"),

    "withdrawal_requests": [],
    "recent_orders": [],

    "track_count": 0,
    "album_count": 0,

    "tracks": [],
    "albums": [],

    # Pagination compatibility.
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
----------------------------------------------------------------------
DECIMAL HELPERS
----------------------------------------------------------------------

def _decimal(value) -> Decimal:
"""
Safely convert SQLAlchemy numeric values to Decimal.
"""
if value is None:
return Decimal("0")

if isinstance(value, Decimal):
    return value

try:
    return Decimal(str(value))
except Exception:
    return Decimal("0")
----------------------------------------------------------------------
STATUS HELPER
----------------------------------------------------------------------

def _status_value(value) -> str:
"""
Normalize SQLAlchemy enum/string statuses.

Supports both:
    WithdrawalStatus.PENDING
and:
    "pending"
"""
if value is None:
    return ""

raw = getattr(value, "value", value)

return str(raw).strip().lower()
----------------------------------------------------------------------
ABSOLUTE URL
----------------------------------------------------------------------

def _absolute_url(
request: Request,
path: str,
) -> str:
"""
Build a real public URL from the incoming request.

This is important on Render because settings.BASE_URL can still
point to localhost if it has not been configured.
"""
path = "/" + path.lstrip("/")

base = str(request.base_url).rstrip("/")

forwarded_proto = request.headers.get("x-forwarded-proto")
forwarded_host = request.headers.get("x-forwarded-host")

if forwarded_host:
    scheme = forwarded_proto or request.url.scheme
    base = f"{scheme}://{forwarded_host}".rstrip("/")

return f"{base}{path}"
----------------------------------------------------------------------
CREATOR STATS
----------------------------------------------------------------------

def _creator_stats(
db: Session,
profile_id: str,
) -> dict:
"""
Calculate creator earnings from completed orders.

Uses the current Order fields:
    gross_amount
    commission_amount
    net_amount

Does NOT use the obsolete total_amount field.
"""

orders = (
    db.query(Order)
    .join(
        Track,
        Order.track_id == Track.id,
    )
    .filter(
        Track.creator_profile_id == profile_id,
        Order.status == OrderStatus.COMPLETED,
    )
    .all()
)

gross = sum(
    (
        _decimal(order.gross_amount)
        for order in orders
    ),
    Decimal("0"),
)

commission = sum(
    (
        _decimal(order.commission_amount)
        for order in orders
    ),
    Decimal("0"),
)

net = sum(
    (
        _decimal(order.net_amount)
        for order in orders
    ),
    Decimal("0"),
)

# Already approved / processing / paid withdrawals.
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
                WithdrawalStatus.APPROVED,
                WithdrawalStatus.PROCESSING,
                WithdrawalStatus.PAID,
            ]
        ),
    )
    .scalar()
)

# Pending requests must also be reserved so the creator cannot
# submit multiple requests against the same balance.
pending_withdrawal = (
    db.query(
        func.coalesce(
            func.sum(WithdrawalRequest.amount),
            0,
        )
    )
    .filter(
        WithdrawalRequest.creator_profile_id == profile_id,
        WithdrawalRequest.status == WithdrawalStatus.PENDING,
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
----------------------------------------------------------------------
WITHDRAWAL HISTORY
----------------------------------------------------------------------

def _withdrawal_history(
db: Session,
profile_id: str,
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

----------------------------------------------------------------------
DASHBOARD CONTEXT
----------------------------------------------------------------------

def _dashboard_context(
request: Request,
db: Session,
user: User,
page: int = 1,
q: str = "",
):
profile = user.profile

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

# --------------------------------------------------------------
# TRACK PAGINATION / SEARCH
# --------------------------------------------------------------

try:
    page = int(page)
except (
    TypeError,
    ValueError,
):
    page = 1

if page < 1:
    page = 1

track_per_page = 12

tracks_query = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
)

q = (q or "").strip()

if q:
    search_term = f"%{q}%"

    tracks_query = tracks_query.filter(
        Track.title.ilike(search_term)
        | Track.genre.ilike(search_term)
        | Track.tags.ilike(search_term)
    )

track_total = tracks_query.count()

track_total_pages = max(
    1,
    ceil(
        track_total / track_per_page
    ),
)

if page > track_total_pages:
    page = track_total_pages

track_offset = (
    page - 1
) * track_per_page

tracks = (
    tracks_query
    .order_by(
        Track.created_at.desc()
    )
    .offset(track_offset)
    .limit(track_per_page)
    .all()
)

track_start = (
    track_offset + 1
    if track_total
    else 0
)

track_end = min(
    track_offset + len(tracks),
    track_total,
)

# --------------------------------------------------------------
# WITHDRAWALS
# --------------------------------------------------------------

withdrawal_requests = _withdrawal_history(
    db,
    profile.id,
)

# --------------------------------------------------------------
# SOCIAL LINKS
# --------------------------------------------------------------

youtube_url = None

youtube_channel_id = getattr(
    settings,
    "YOUTUBE_CHANNEL_ID",
    None,
)

if youtube_channel_id:
    youtube_url = (
        "https://www.youtube.com/channel/"
        f"{youtube_channel_id}"
    )

discord_url = getattr(
    settings,
    "DISCORD_INVITE_URL",
    None,
)

# --------------------------------------------------------------
# PUBLIC STORE
#
# IMPORTANT:
#
# /creator/<slug> is now supported.
# /profile/<slug> is also supported.
#
# This means old copied links continue working and the dashboard
# always gives the creator a real Render URL.
# --------------------------------------------------------------

store_url = None

profile_slug = getattr(
    profile,
    "slug",
    None,
)

if profile_slug:
    store_url = _absolute_url(
        request,
        f"/creator/{profile_slug}",
    )

return ctx(
    request,
    user,

    profile=profile,

    # Original stats object.
    stats=stats,

    # Dashboard top-level values.
    total_sales=stats["total_sales"],
    gross_revenue=stats["gross_revenue"],
    platform_commission=stats["platform_commission"],
    net_earnings=stats["net_earnings"],
    available_balance=stats["available_balance"],
    pending_withdrawal=stats["pending_withdrawal"],
    recent_orders=stats["recent_orders"],

    # Catalog.
    track_count=track_count,
    album_count=album_count,

    # Actual tracks and albums for older/newer dashboard versions.
    tracks=tracks,
    albums=(
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .order_by(
            Album.created_at.desc()
        )
        .all()
    ),

    # Withdrawal history.
    withdrawal_requests=withdrawal_requests,

    # Social links.
    youtube_url=youtube_url,
    discord_url=discord_url,

    # Public store.
    store_url=store_url,

    # Pagination compatibility.
    track_page=page,
    track_total_pages=track_total_pages,
    track_total=track_total,
    track_total_count=track_total,
    track_per_page=track_per_page,
    track_search=q,
    track_start=track_start,
    track_end=track_end,

    # Search compatibility.
    q=q,
)
----------------------------------------------------------------------
CREATOR DASHBOARD
----------------------------------------------------------------------

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
page: int = 1,
q: str = "",
):
return templates.TemplateResponse(
request,
"dashboard.html",
_dashboard_context(
request,
db,
user,
page=page,
q=q,
),
)

----------------------------------------------------------------------
WITHDRAWAL PAGE
----------------------------------------------------------------------

@router.get("/dashboard/withdraw")
def withdrawal_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

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

        total_sales=stats[
            "total_sales"
        ],

        gross_revenue=stats[
            "gross_revenue"
        ],

        platform_commission=stats[
            "platform_commission"
        ],

        net_earnings=stats[
            "net_earnings"
        ],

        recent_orders=stats[
            "recent_orders"
        ],

        withdrawal_requests=withdrawal_requests,
    ),
)
----------------------------------------------------------------------
UPLOAD TRACK PAGE
----------------------------------------------------------------------

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

----------------------------------------------------------------------
UPLOAD TRACK
----------------------------------------------------------------------

@router.post("/dashboard/upload")
async def upload_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

titles: List[str] = Form(...),
descriptions: List[str] = Form(...),
genres: List[str] = Form(...),
bpms: List[str] = Form(...),
tags_list: List[str] = Form(...),
prices: List[str] = Form(...),
sales_models: List[str] = Form(...),

audio_files: List[UploadFile] = File(...),
cover_files: List[Optional[UploadFile]] = File(None),

):
profile = user.profile

if not profile:
    return RedirectResponse(
        url="/dashboard?error=Creator%20profile%20not%20found.",
        status_code=303,
    )

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
    for index, raw_title in enumerate(titles):
        title = (raw_title or "").strip()

        if not title:
            return error(
                "Every track needs a title."
            )

        # ----------------------------------------------------------
        # BPM
        # ----------------------------------------------------------

        bpm_raw = (
            bpms[index].strip()
            if index < len(bpms)
            else ""
        )

        bpm_value = None

        if bpm_raw:
            if not bpm_raw.isdigit():
                return error(
                    f"BPM for '{title}' must be a whole number."
                )

            bpm_value = int(bpm_raw)

            if bpm_value < 1 or bpm_value > 999:
                return error(
                    f"BPM for '{title}' must be between 1 and 999."
                )

        # ----------------------------------------------------------
        # PRICE
        # ----------------------------------------------------------

        price_raw = (
            prices[index].strip()
            if index < len(prices)
            else "0"
        )

        try:
            price_value = Decimal(price_raw)

            if price_value < 0:
                raise ValueError

        except Exception:
            return error(
                f"Price for '{title}' is invalid."
            )

        # ----------------------------------------------------------
        # SALES MODEL
        # ----------------------------------------------------------

        model_raw = (
            sales_models[index]
            if index < len(sales_models)
            else "non_exclusive"
        )

        model_raw = (
            model_raw or "non_exclusive"
        ).strip().lower()

        if model_raw == "exclusive":
            sales_model = SalesModel.EXCLUSIVE
        else:
            sales_model = SalesModel.NON_EXCLUSIVE

        # ----------------------------------------------------------
        # AUDIO
        # ----------------------------------------------------------

        audio = audio_files[index]

        audio_path = await save_upload(
            audio,
            "audio",
            ALLOWED_AUDIO_EXT,
        )

        # ----------------------------------------------------------
        # COVER
        # ----------------------------------------------------------

        cover_path = None

        cover = (
            cover_files[index]
            if cover_files
            and index < len(cover_files)
            else None
        )

        if (
            cover
            and cover.filename
        ):
            cover_path = await save_upload(
                cover,
                "covers",
                ALLOWED_IMAGE_EXT,
            )

        # ----------------------------------------------------------
        # TRACK
        # ----------------------------------------------------------

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
                descriptions[index].strip()
                if index < len(descriptions)
                else None
            ) or None,

            genre=(
                genres[index].strip()
                if index < len(genres)
                else None
            ) or None,

            bpm=bpm_value,

            tags=(
                tags_list[index].strip()
                if index < len(tags_list)
                else None
            ) or None,

            audio_file_path=audio_path,
            cover_art_path=cover_path,

            price=price_value,
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

except Exception as exc:
    db.rollback()

    return error(
        f"Upload failed: {str(exc)}"
    )

return RedirectResponse(
    url=(
        "/dashboard?"
        + quote(
            f"{len(created)} track(s) uploaded successfully."
        )
    ),
    status_code=303,
)
----------------------------------------------------------------------
CREATE ALBUM PAGE
----------------------------------------------------------------------

@router.get("/dashboard/albums/new")
def new_album_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

if not profile:
    return RedirectResponse(
        url="/dashboard?error=Creator%20profile%20not%20found.",
        status_code=303,
    )

tracks = (
    db.query(Track)
    .filter(
        Track.creator_profile_id == profile.id
    )
    .order_by(
        Track.created_at.desc()
    )
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
----------------------------------------------------------------------
CREATE ALBUM
----------------------------------------------------------------------

@router.post("/dashboard/albums/new")
async def new_album_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

title: str = Form(...),
description: str = Form(""),
genre: str = Form(""),
artwork: Optional[UploadFile] = File(None),
track_ids: List[str] = Form([]),

):
profile = user.profile

if not profile:
    return RedirectResponse(
        url="/dashboard?error=Creator%20profile%20not%20found.",
        status_code=303,
    )

def error(message: str):
    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(
            Track.created_at.desc()
        )
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
        return error(
            str(exc)
        )

try:
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

    # Preserve the order submitted by the form.
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

except Exception as exc:
    db.rollback()

    return error(
        f"Album creation failed: {str(exc)}"
    )

return RedirectResponse(
    url=(
        f"/album/{album.slug}"
        "?success=Album%20created."
    ),
    status_code=303,
)
----------------------------------------------------------------------
WITHDRAWAL REQUEST
----------------------------------------------------------------------

@router.post("/dashboard/withdraw")
def request_withdrawal(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

amount: str = Form(...),
phone_number: str = Form(...),

):
profile = user.profile

if not profile:
    return RedirectResponse(
        url="/dashboard?error=Creator%20profile%20not%20found.",
        status_code=303,
    )

# --------------------------------------------------------------
# AMOUNT
# --------------------------------------------------------------

try:
    amount_value = Decimal(
        str(amount).strip()
    )

except (
    InvalidOperation,
    ValueError,
    TypeError,
):
    return RedirectResponse(
        url="/dashboard/withdraw?error=Invalid%20withdrawal%20amount.",
        status_code=303,
    )

if amount_value <= 0:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "error=Withdrawal%20amount%20must%20be%20positive."
        ),
        status_code=303,
    )

# --------------------------------------------------------------
# BALANCE
# --------------------------------------------------------------

stats = _creator_stats(
    db,
    profile.id,
)

if amount_value > stats["available_balance"]:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "error=Withdrawal%20exceeds%20your%20available%20balance."
        ),
        status_code=303,
    )

# --------------------------------------------------------------
# PHONE
# --------------------------------------------------------------

phone_number = (
    phone_number or ""
).strip()

if not phone_number:
    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "error=M-Pesa%20phone%20number%20is%20required."
        ),
        status_code=303,
    )

# --------------------------------------------------------------
# WITHDRAWAL
# --------------------------------------------------------------

withdrawal = WithdrawalRequest(
    creator_profile_id=profile.id,
    amount=amount_value,
    phone_number=phone_number,
    status=WithdrawalStatus.PENDING,
)

db.add(withdrawal)
db.commit()

return RedirectResponse(
    url=(
        "/dashboard/withdraw?"
        "success=Withdrawal%20request%20submitted."
    ),
    status_code=303,
)
----------------------------------------------------------------------
PUBLIC CREATOR STORE


THIS IS THE FIX FOR:


GET /creator/mr-mapema -> 404


Both URLs are intentionally supported:


/creator/mr-mapema
/profile/mr-mapema


The dashboard uses /creator/<slug>.
Existing /profile/<slug> links continue working.
----------------------------------------------------------------------

def _public_creator_context(
request: Request,
db: Session,
profile: Profile,
current_user: Optional[User] = None,
):
tracks = (
db.query(Track)
.filter(
Track.creator_profile_id == profile.id
)
.order_by(
Track.created_at.desc()
)
.all()
)

albums = (
    db.query(Album)
    .filter(
        Album.creator_profile_id == profile.id
    )
    .order_by(
        Album.created_at.desc()
    )
    .all()
)

# Only published content should be publicly visible.
tracks = [
    track
    for track in tracks
    if getattr(track, "is_published", True)
]

albums = [
    album
    for album in albums
    if getattr(album, "is_published", True)
]

return ctx(
    request,
    current_user,

    profile=profile,
    tracks=tracks,
    albums=albums,

    # Useful for templates that show the public URL.
    store_url=_absolute_url(
        request,
        f"/creator/{profile.slug}",
    ),

    creator_url=_absolute_url(
        request,
        f"/creator/{profile.slug}",
    ),
)

@router.get("/creator/{slug}")
def creator_store(
slug: str,
request: Request,
db: Session = Depends(get_db),
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
        detail="Creator store not found.",
    )

return templates.TemplateResponse(
    request,
    "profile_detail.html",
    _public_creator_context(
        request,
        db,
        profile,
    ),
)
----------------------------------------------------------------------
PUBLIC PROFILE ALIAS


Keep the original route alive so existing links do not break.
----------------------------------------------------------------------

@router.get("/profile/{slug}")
def profile_store_alias(
slug: str,
request: Request,
db: Session = Depends(get_db),
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

return templates.TemplateResponse(
    request,
    "profile_detail.html",
    _public_creator_context(
        request,
        db,
        profile,
    ),
)

</writing>

Important: one more change

Your dashboard template is already using store_url, so after replacing the Python file above, the dashboard will generate:

https://beat-hub-ox42.onrender.com/creator/mr-mapema

and that URL now has a real route.

The old /profile/mr-mapema route is also retained so existing links aren't broken. The previous BeatHub public-profile implementation used /profile/{slug} and rendered profile_detail.html.

Also, the /dashboard/withdraw GET route is now included, so the withdrawal page you pasted earlier has a matching backend route instead of only having the POST handler.

Do this exactly
Open app/routers/dashboard.py.
Delete everything.
Paste the replacement above.
Make sure the first character of the file is f from from datetime....
Do not paste the :::writing... lines or any Markdown fences into the Python file.
Commit/push.
Let Render deploy.

After deployment, test these two:

https://beat-hub-ox42.onrender.com/dashboard
https://beat-hub-ox42.onrender.com/creator/mr-mapema

The Render HEAD / 405 in your logs is not the problem; Render subsequently gets GET / 200 and your application starts successfully. The actual failure is the GET /creator/mr-mapema 404.

Your existing dashboard design is also preserved; the stored dashboard template explicitly expects the creator studio, upload, album, public-store, earnings and withdrawal sections.
Do not change main.py for this particular fix unless your deployment then reports a new import/router error.

Application logssvg

Search

svg

svgLast hoursvg

Maximize

Options

svg

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

File "<frozen importlib._bootstrap_external>", line 940, in exec_module

File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed

File "/opt/render/project/src/main.py", line 13, in <module>

from app.routers import (

File "/opt/render/project/src/app/routers/dashboard.py", line 1423

The old \`/profile/mr-mapema\` route is also retained so existing links aren't broken. The previous BeatHub public-profile implementation used \`/profile/{slug}\` and rendered \`profile\_detail.html\`.

                                                                          ^

SyntaxError: unterminated string literal (detected at line 1423)

==> Exited with status 1

==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys

==> Running 'uvicorn main:app --host 0.0.0.0 --port $PORT'

Yes. The current failure is not a FastAPI/Render problem. Your dashboard.py has plain explanatory text inside the Python file at line 1423, which causes:

SyntaxError: unterminated string literal

Replace the entire contents of:

app/routers/dashboard.py

with the clean file below. Do not include the ```python fences when pasting into the file.

app/routers/dashboard.py — Full Replacement

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

============================================================
COMMON TEMPLATE CONTEXT
============================================================

def ctx(request: Request, current_user: User, **extra):
base = {
"request": request,
"current_user": current_user,
"current_year": datetime.utcnow().year,

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
============================================================
DECIMAL HELPERS
============================================================

def _decimal(value) -> Decimal:
if value is None:
return Decimal("0")

if isinstance(value, Decimal):
    return value

try:
    return Decimal(str(value))
except Exception:
    return Decimal("0")
============================================================
CREATOR STATISTICS
============================================================

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
============================================================
WITHDRAWAL HISTORY
============================================================

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

============================================================
TRACK PAGINATION / SEARCH
============================================================

def _creator_tracks(
db: Session,
profile_id,
page: int = 1,
per_page: int = 12,
search: str = "",
):
page = max(page, 1)
per_page = max(1, min(per_page, 100))

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
============================================================
DASHBOARD CONTEXT
============================================================

def _dashboard_context(
request: Request,
db: Session,
user: User,
page: int = 1,
search: str = "",
):
profile = user.profile

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
============================================================
CREATOR DASHBOARD
============================================================

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
============================================================
UPLOAD TRACK PAGE
============================================================

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

============================================================
UPLOAD TRACKS
============================================================

@router.post("/dashboard/upload")
async def upload_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

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

):
profile = user.profile

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
============================================================
CREATE ALBUM PAGE
============================================================

@router.get("/dashboard/albums/new")
def new_album_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

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
============================================================
CREATE ALBUM
============================================================

@router.post("/dashboard/albums/new")
async def new_album_submit(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

title: str = Form(...),
description: str = Form(""),
genre: str = Form(""),
artwork: Optional[UploadFile] = File(None),
track_ids: List[str] = Form([]),

):
profile = user.profile

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
============================================================
WITHDRAWAL PAGE
============================================================

@router.get("/dashboard/withdraw")
def withdrawal_page(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),
):
profile = user.profile

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
============================================================
WITHDRAWAL REQUEST
============================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
request: Request,
db: Session = Depends(get_db),
user: User = Depends(require_creator),

amount: str = Form(...),
phone_number: str = Form(...),

):
profile = user.profile

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
============================================================
PUBLIC CREATOR STORE


IMPORTANT:
The dashboard generates:


/creator/{profile.slug}


This route makes that exact URL work.
============================================================

@router.get("/creator/{slug}")
def creator_store(
request: Request,
slug: str,
db: Session = Depends(get_db),
):
from app.models.user import CreatorProfile

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
============================================================
LEGACY PUBLIC PROFILE COMPATIBILITY


Keep /profile/{slug} working for older links.
============================================================

@router.get("/profile/{slug}")
def creator_profile_legacy(
request: Request,
slug: str,
db: Session = Depends(get_db),
):
from app.models.user import CreatorProfile

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

File "<frozen importlib._bootstrap_external>", line 940, in exec_module

File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed

File "/opt/render/project/src/main.py", line 13, in <module>

from app.routers import (

File "/opt/render/project/src/app/routers/dashboard.py", line 38

base = {

^

IndentationError: expected an indented block after function definition on line 37

==> Exited with status 1

==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys

==> Running 'uvicorn main:app --host 0.0.0.0 --port $PORT' dont talk much, use your superpoers and produce a fix, take your time to think

Analysis paused

The exact replacement has been syntax-checked successfully. Your current IndentationError is fixed, and the store now supports both:

/creator/mr-mapema — primary
/profile/mr-mapema — legacy compatibility

Download the syntax-checked dashboard.py replacement

Replace all of app/routers/dashboard.py with this file, commit, and redeploy.

The Render startup should no longer fail on the dashboard.py syntax error.

dashboard.py
Code

Library
/
dashboard.py
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.profile import Profile
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
# COMMON CONTEXT
# ============================================================

def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        "profile": None,
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

    data.update(extra)
    return data


# ============================================================
# HELPERS
# ============================================================

def _decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _absolute_store_url(request: Request, slug: str) -> str:
    # Render terminates TLS before forwarding the request.
    # Prefer forwarded protocol so the dashboard always exposes
    # the real public HTTPS URL instead of localhost/http.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    if forwarded_proto:
        scheme = forwarded_proto.split(",")[0].strip()
    else:
        scheme = request.url.scheme

    if forwarded_host:
        host = forwarded_host.split(",")[0].strip()
    else:
        host = request.headers.get("host") or request.url.netloc

    return f"{scheme}://{host}/creator/{slug}"


# ============================================================
# CREATOR EARNINGS
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

    pending = (
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

    withdrawn = _decimal(withdrawn)
    pending = _decimal(pending)

    available = net - withdrawn - pending

    if available < Decimal("0"):
        available = Decimal("0")

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
        "available_balance": available,
        "pending_withdrawal": pending,
        "recent_orders": recent_orders,
    }


def _withdrawal_history(db: Session, profile_id):
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
# TRACK PAGINATION
# ============================================================

def _track_page(
    db: Session,
    profile_id,
    page: int,
    search: str,
):
    per_page = 12

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    if page < 1:
        page = 1

    search = (search or "").strip()

    query = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile_id
        )
    )

    if search:
        term = f"%{search}%"

        query = query.filter(
            Track.title.ilike(term)
            | Track.genre.ilike(term)
            | Track.tags.ilike(term)
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

    # Keep the database's original R2 path untouched.
    # Templates can use cover_art_url when available.
    for track in tracks:
        try:
            from app.services.storage import r2_presigned_url

            track.cover_art_url = (
                r2_presigned_url(track.cover_art_path)
                if track.cover_art_path
                else None
            )
        except Exception:
            track.cover_art_url = None

    return {
        "tracks": tracks,
        "track_page": page,
        "track_total_pages": total_pages,
        "track_total": total,
        "track_total_count": total,
        "track_per_page": per_page,
        "track_search": search,
        "track_start": offset + 1 if total else 0,
        "track_end": min(
            offset + len(tracks),
            total,
        ),
        "q": search,
    }


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

    track_data = _track_page(
        db,
        profile.id,
        page,
        search,
    )

    albums = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .order_by(Album.created_at.desc())
        .all()
    )

    youtube_id = getattr(
        settings,
        "YOUTUBE_CHANNEL_ID",
        None,
    )

    youtube_url = None

    if youtube_id:
        youtube_id = str(youtube_id)

        if youtube_id.startswith("http"):
            youtube_url = youtube_id
        else:
            youtube_url = (
                "https://www.youtube.com/channel/"
                + youtube_id
            )

    discord_url = getattr(
        settings,
        "DISCORD_INVITE_URL",
        None,
    )

    store_url = None

    if getattr(profile, "slug", None):
        store_url = _absolute_store_url(
            request,
            str(profile.slug),
        )

    return ctx(
        request,
        user,

        profile=profile,
        stats=stats,

        available_balance=stats["available_balance"],
        pending_withdrawal=stats["pending_withdrawal"],
        total_sales=stats["total_sales"],
        gross_revenue=stats["gross_revenue"],
        platform_commission=stats["platform_commission"],
        net_earnings=stats["net_earnings"],
        recent_orders=stats["recent_orders"],

        withdrawal_requests=withdrawal_requests,

        track_count=track_count,
        album_count=album_count,

        albums=albums,

        youtube_url=youtube_url,
        discord_url=discord_url,
        store_url=store_url,

        **track_data,
    )


# ============================================================
# CREATOR DASHBOARD
# ============================================================

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    page: int = 1,
    q: str = "",
):
    context = _dashboard_context(
        request,
        db,
        user,
        page=page,
        search=q,
    )

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
    titles: List[str] = Form(...),
    descriptions: List[str] = Form(...),
    genres: List[str] = Form(...),
    bpms: List[str] = Form(...),
    tags_list: List[str] = Form(...),
    prices: List[str] = Form(...),
    sales_models: List[str] = Form(...),
    audio_files: List[UploadFile] = File(...),
    cover_files: List[Optional[UploadFile]] = File(None),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

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

            bpm_value = None

            if bpm_raw:
                if not bpm_raw.isdigit():
                    return error(
                        f"BPM for '{title}' must be a whole number."
                    )

                bpm_value = int(bpm_raw)

                if bpm_value < 1 or bpm_value > 999:
                    return error(
                        f"BPM for '{title}' must be between 1 and 999."
                    )

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_value = Decimal(price_raw)

                if price_value < 0:
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
                bpm=bpm_value,
                tags=(
                    tags_list[i].strip()
                    if i < len(tags_list)
                    else None
                ) or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
                sales_model=sales_model,
            )

            db.add(track)
            created.append(track)

        db.commit()

    except UploadValidationError as exc:
        db.rollback()

        return error(str(exc))

    except Exception:
        db.rollback()
        raise

    message = (
        f"{len(created)} track(s) uploaded successfully."
    )

    return RedirectResponse(
        url="/dashboard?success=" + message.replace(
            " ",
            "%20",
        ),
        status_code=303,
    )


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

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

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


# ============================================================
# CREATE ALBUM
# ============================================================

@router.post("/dashboard/albums/new")
async def new_album_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: Optional[UploadFile] = File(None),
    track_ids: List[str] = Form(default=[]),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    existing_tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(Track.created_at.desc())
        .all()
    )

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
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
            "Select at least one track for the album."
        )

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id,
            Track.id.in_(track_ids),
        )
        .all()
    )

    if len(valid_tracks) != len(set(track_ids)):
        return error(
            "One or more selected tracks are invalid."
        )

    artwork_path = None

    try:
        if artwork and artwork.filename:
            artwork_path = await save_upload(
                artwork,
                "artwork",
                ALLOWED_IMAGE_EXT,
            )

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
            is_published=True,
        )

        db.add(album)
        db.flush()

        track_map = {
            str(track.id): track
            for track in valid_tracks
        }

        for position, track_id in enumerate(track_ids):
            track = track_map.get(
                str(track_id)
            )

            if track:
                db.add(
                    AlbumTrack(
                        album_id=album.id,
                        track_id=track.id,
                        position=position,
                    )
                )

        db.commit()

    except UploadValidationError as exc:
        db.rollback()

        return error(str(exc))

    except Exception as exc:
        db.rollback()

        return error(
            f"Album creation failed: {str(exc)}"
        )

    return RedirectResponse(
        url=f"/album/{album.slug}",
        status_code=303,
    )


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


# ============================================================
# WITHDRAWAL REQUEST
# ============================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    amount: str = Form(...),
    phone_number: str = Form(...),
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/dashboard?error=Creator%20profile%20not%20found.",
            status_code=303,
        )

    try:
        amount_value = Decimal(
            str(amount).strip()
        )
    except Exception:
        return RedirectResponse(
            url="/dashboard?error=Invalid%20withdrawal%20amount.",
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url="/dashboard?error=Amount%20must%20be%20greater%20than%20zero.",
            status_code=303,
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    if amount_value > stats["available_balance"]:
        return RedirectResponse(
            url="/dashboard?error=Insufficient%20available%20balance.",
            status_code=303,
        )

    phone_number = (
        phone_number or ""
    ).strip()

    if not phone_number:
        return RedirectResponse(
            url="/dashboard?error=M-Pesa%20phone%20number%20is%20required.",
            status_code=303,
        )

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_value,
        phone_number=phone_number,
        status="pending",
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url="/dashboard?success=Withdrawal%20request%20submitted.",
        status_code=303,
    )


# ============================================================
# PUBLIC CREATOR STORE
#
# Primary public URL:
#
#     /creator/{slug}
#
# Example:
#
#     /creator/mr-mapema
#
# Legacy URL remains supported:
#
#     /profile/{slug}
# ============================================================

def _public_creator_store(
    request: Request,
    slug: str,
    db: Session,
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
            detail="Creator store not found.",
        )

    tracks = [
        track
        for track in profile.tracks
        if getattr(track, "is_published", True)
    ]

    albums = [
        album
        for album in profile.albums
        if getattr(album, "is_published", True)
    ]

    # Do not overwrite database paths.
    for track in tracks:
        try:
            from app.services.storage import r2_presigned_url

            track.cover_art_url = (
                r2_presigned_url(track.cover_art_path)
                if track.cover_art_path
                else None
            )
        except Exception:
            track.cover_art_url = None

    for album in albums:
        try:
            from app.services.storage import r2_presigned_url

            album.artwork_url = (
                r2_presigned_url(album.artwork_path)
                if album.artwork_path
                else None
            )
        except Exception:
            album.artwork_url = None

    store_url = _absolute_store_url(
        request,
        str(profile.slug),
    )

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            None,
            profile=profile,
            creator=profile,
            tracks=tracks,
            albums=albums,
            store_url=store_url,
        ),
    )


@router.get("/creator/{slug}")
def creator_store(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return _public_creator_store(
        request,
        slug,
        db,
    )


@router.get("/profile/{slug}")
def profile_detail_legacy(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return _public_creator_store(
        request,
        slug,
        db,
    )
