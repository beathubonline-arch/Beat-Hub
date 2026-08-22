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

```
context.update(extra)

return context
```

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

```
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
```

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

```
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
```

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

```
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
```

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

```
return templates.TemplateResponse(
    request,
    "account_purchases.html",
    ctx(
        request,
        current_user,
        licenses=licenses,
    ),
)
```

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

```
return templates.TemplateResponse(
    request,
    "account_downloads.html",
    ctx(
        request,
        current_user,
        licenses=licenses,
    ),
)
```

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

```
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
```

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

```
return templates.TemplateResponse(
    request,
    "account_orders.html",
    ctx(
        request,
        current_user,
        orders=orders,
    ),
)
```

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

```
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
```

@router.get(
"/healthz",
include_in_schema=False,
)
def healthz_compat():
return {
"status": "ok"
}
