from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import media_url, r2_presigned_url
from app.utils.deps import get_optional_user, require_user

router = APIRouter(tags=["music"])

templates = Jinja2Templates(
directory="app/templates"
)

# ======================================================================

# COMMON CONTEXT

# ======================================================================

def ctx(
request: Request,
current_user=None,
**extra,
):
data = {
"request": request,
"current_user": current_user,
"user": current_user,
"current_year": datetime.utcnow().year,
}

```
data.update(extra)
return data
```

# ======================================================================

# SAFE HELPERS

# ======================================================================

def _value(
obj,
*names,
default=None,
):
for name in names:
try:
value = getattr(
obj,
name,
None,
)
except Exception:
value = None

```
    if value is not None:
        return value

return default
```

def _text(value) -> str:
if value is None:
return ""

```
return str(value).strip()
```

def _track_title(track: Track) -> str:
return (
_text(
_value(
track,
"title",
"name",
default="Untitled Beat",
)
)
or "Untitled Beat"
)

def _track_price(track: Track) -> float:
raw = _value(
track,
"price",
default=0,
)

```
try:
    return float(raw or 0)
except (
    TypeError,
    ValueError,
):
    return 0.0
```

def _sales_model_value(track: Track) -> str:
sales_model = _value(
track,
"sales_model",
default=None,
)

```
if sales_model is None:
    return ""

value = getattr(
    sales_model,
    "value",
    None,
)

if value is None:
    value = sales_model

return _text(value).lower()
```

def _is_public_track(track: Track) -> bool:
if not track:
return False

```
if not bool(
    _value(
        track,
        "is_published",
        default=True,
    )
):
    return False

sales_model = _sales_model_value(track)

if (
    sales_model == "exclusive"
    and bool(
        _value(
            track,
            "is_sold",
            default=False,
        )
    )
):
    return False

return True
```

def _producer_name(track: Track) -> str:
profile = _value(
track,
"creator_profile",
"profile",
default=None,
)

```
if profile is not None:
    name = _value(
        profile,
        "stage_name",
        "display_name",
        "artist_name",
        "username",
        "name",
        default=None,
    )

    if name:
        return _text(name)

direct = _value(
    track,
    "producer_name",
    "creator_name",
    "artist_name",
    "username",
    default=None,
)

return (
    _text(direct)
    or "BeatHub Creator"
)
```

def _genre(track: Track) -> str:
return _text(
_value(
track,
"genre",
"category",
default="",
)
)

def _mood(track: Track) -> str:
return _text(
_value(
track,
"mood",
default="",
)
)

def _bpm(track: Track) -> str:
value = _value(
track,
"bpm",
"tempo",
default="",
)

```
return _text(value)
```

def _key(track: Track) -> str:
return _text(
_value(
track,
"key",
"musical_key",
default="",
)
)

def _description(track: Track) -> str:
return _text(
_value(
track,
"description",
"short_description",
default="",
)
)

def _artwork_url(track: Track) -> Optional[str]:
path = _value(
track,
"cover_art_path",
"cover_url",
"artwork_url",
"image_url",
"thumbnail_url",
default=None,
)

```
if not path:
    return None

try:
    return media_url(
        str(path)
    )
except Exception:
    try:
        return r2_presigned_url(
            str(path)
        )
    except Exception:
        return None
```

def _audio_url(track: Track) -> Optional[str]:
path = _value(
track,
"preview_file_path",
"audio_file_path",
"audio_url",
"preview_url",
"file_url",
"stream_url",
"mp3_url",
default=None,
)

```
if not path:
    return None

try:
    return media_url(
        str(path)
    )
except Exception:
    try:
        return r2_presigned_url(
            str(path)
        )
    except Exception:
        return None
```

def _track_url(track: Track) -> str:
slug = _value(
track,
"slug",
default=None,
)

```
if slug:
    return f"/track/{slug}"

track_id = _value(
    track,
    "id",
    default=None,
)

if track_id is not None:
    return f"/track/{track_id}"

return "/beats"
```

# ======================================================================

# BEATS MARKETPLACE

# ======================================================================

@router.get(
"/beats",
name="beats",
)
def beats_catalog(
request: Request,
q: str = Query(
default="",
max_length=100,
),
genre: str = Query(
default="",
max_length=80,
),
mood: str = Query(
default="",
max_length=80,
),
min_price: Optional[float] = Query(
default=None,
ge=0,
),
max_price: Optional[float] = Query(
default=None,
ge=0,
),
sort: str = Query(
default="newest",
max_length=30,
),
page: int = Query(
default=1,
ge=1,
),
per_page: int = Query(
default=24,
ge=12,
le=48,
),
current_user: Optional[User] = Depends(
get_optional_user
),
db: Session = Depends(get_db),
):
"""
BeatHub's main public beat marketplace.

```
IMPORTANT:
This route uses the real Track model from app.models.music.

The marketplace is database-paginated so visitors do not have
to load the entire catalog just to view the Beats page.
"""

search = _text(q)
selected_genre = _text(genre)
selected_mood = _text(mood)

query = (
    db.query(Track)
    .filter(
        Track.is_published.is_(True)
    )
)

# --------------------------------------------------------------
# SEARCH
# --------------------------------------------------------------

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

# --------------------------------------------------------------
# GENRE
# --------------------------------------------------------------

if selected_genre:
    query = query.filter(
        Track.genre.ilike(
            f"%{selected_genre}%"
        )
    )

# --------------------------------------------------------------
# OPTIONAL MOOD COMPATIBILITY
#
# The current Track model does not require a mood column.
# If mood exists in a future schema, use it automatically.
# Otherwise this remains harmless.
# --------------------------------------------------------------

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

# --------------------------------------------------------------
# PRICE
# --------------------------------------------------------------

if min_price is not None:
    query = query.filter(
        Track.price >= min_price
    )

if max_price is not None:
    query = query.filter(
        Track.price <= max_price
    )

# --------------------------------------------------------------
# SORTING
# --------------------------------------------------------------

if sort == "oldest":
    query = query.order_by(
        Track.created_at.asc()
    )

elif sort == "price_low":
    query = query.order_by(
        Track.price.asc(),
        Track.created_at.desc(),
    )

elif sort == "price_high":
    query = query.order_by(
        Track.price.desc(),
        Track.created_at.desc(),
    )

else:
    sort = "newest"

    query = query.order_by(
        Track.created_at.desc()
    )

# --------------------------------------------------------------
# TOTAL
# --------------------------------------------------------------

total_query = query

total = total_query.count()

total_pages = max(
    1,
    (
        total + per_page - 1
    ) // per_page,
)

if page > total_pages:
    page = total_pages

offset = (
    page - 1
) * per_page

# --------------------------------------------------------------
# PAGE
# --------------------------------------------------------------

page_tracks = (
    query
    .offset(offset)
    .limit(per_page)
    .all()
)

# --------------------------------------------------------------
# EXCLUSIVE SOLD FILTER
#
# Sold exclusive beats must not be advertised as purchasable.
# Keep the database query efficient while applying the business
# rule safely in Python.
# --------------------------------------------------------------

page_tracks = [
    track
    for track in page_tracks
    if _is_public_track(track)
]

# --------------------------------------------------------------
# CATALOG VIEW MODEL
# --------------------------------------------------------------

catalog = []

for track in page_tracks:
    catalog.append(
        {
            "track": track,
            "title": _track_title(track),
            "producer": _producer_name(track),
            "price": _track_price(track),
            "artwork_url": _artwork_url(track),
            "audio_url": _audio_url(track),
            "url": _track_url(track),
            "genre": _genre(track),
            "mood": _mood(track),
            "bpm": _bpm(track),
            "key": _key(track),
            "description": _description(track),
            "sales_model": _sales_model_value(track),
            "is_sold": bool(
                _value(
                    track,
                    "is_sold",
                    default=False,
                )
            ),
        }
    )

# --------------------------------------------------------------
# AVAILABLE GENRES
# --------------------------------------------------------------

genre_rows = (
    db.query(
        Track.genre
    )
    .filter(
        Track.is_published.is_(True),
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
        _text(row[0])
        for row in genre_rows
        if row[0]
    },
    key=str.lower,
)

# --------------------------------------------------------------
# MOODS
# --------------------------------------------------------------

moods = []

if mood_field is not None:
    try:
        mood_rows = (
            db.query(
                mood_field
            )
            .filter(
                Track.is_published.is_(True),
                mood_field.isnot(None),
                mood_field != "",
            )
            .distinct()
            .order_by(
                mood_field.asc()
            )
            .limit(50)
            .all()
        )

        moods = sorted(
            {
                _text(row[0])
                for row in mood_rows
                if row[0]
            },
            key=str.lower,
        )
    except Exception:
        moods = []

# --------------------------------------------------------------
# DISPLAY RANGE
# --------------------------------------------------------------

catalog_start = (
    offset + 1
    if total
    else 0
)

catalog_end = min(
    offset + len(page_tracks),
    total,
)

return templates.TemplateResponse(
    request,
    "beats.html",
    {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,

        "tracks": page_tracks,
        "beats": page_tracks,
        "catalog": catalog,

        "total": total,
        "total_results": total,

        "page": page,
        "track_page": page,

        "per_page": per_page,
        "track_per_page": per_page,

        "total_pages": total_pages,
        "track_total_pages": total_pages,

        "genres": genres,
        "moods": moods,

        "query": search,
        "q": search,

        "genre": selected_genre,
        "mood": selected_mood,

        "min_price": min_price,
        "max_price": max_price,

        "sort": sort,

        "has_previous": page > 1,
        "has_next": page < total_pages,

        "previous_page": max(
            1,
            page - 1,
        ),
        "next_page": min(
            total_pages,
            page + 1,
        ),

        "catalog_start": catalog_start,
        "catalog_end": catalog_end,

        "has_results": bool(
            page_tracks
        ),

        "title": "Find Your Sound",
    },
)
```

# ======================================================================

# HOT PICKS

# ======================================================================

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
Track.is_published.is_(True)
)
.order_by(
Track.created_at.desc()
)
.limit(24)
.all()
)

```
tracks = [
    track
    for track in tracks
    if _is_public_track(track)
]

catalog = []

for track in tracks:
    catalog.append(
        {
            "track": track,
            "title": _track_title(track),
            "producer": _producer_name(track),
            "price": _track_price(track),
            "artwork_url": _artwork_url(track),
            "audio_url": _audio_url(track),
            "url": _track_url(track),
            "genre": _genre(track),
            "mood": _mood(track),
            "bpm": _bpm(track),
            "key": _key(track),
            "description": _description(track),
        }
    )

return templates.TemplateResponse(
    request,
    "beats.html",
    {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,

        "tracks": tracks,
        "beats": tracks,
        "catalog": catalog,

        "total": len(tracks),
        "total_results": len(tracks),

        "page": 1,
        "track_page": 1,

        "per_page": len(tracks),
        "track_per_page": len(tracks),

        "total_pages": 1,
        "track_total_pages": 1,

        "genres": [],
        "moods": [],

        "query": "",
        "q": "",

        "genre": "",
        "mood": "",

        "min_price": None,
        "max_price": None,

        "sort": "newest",

        "has_previous": False,
        "has_next": False,

        "previous_page": 1,
        "next_page": 1,

        "catalog_start": 1 if tracks else 0,
        "catalog_end": len(tracks),

        "has_results": bool(tracks),

        "title": "Hot Picks",
    },
)
```

# ======================================================================

# SESSIONS

# ======================================================================

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

# ======================================================================

# TRACK DETAIL

# ======================================================================

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

```
if not track:
    raise HTTPException(
        status_code=404,
        detail="Track not found.",
    )

if not bool(
    _value(
        track,
        "is_published",
        default=True,
    )
):
    raise HTTPException(
        status_code=404,
        detail="Track not found.",
    )

purchased = False

if current_user:
    purchased = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
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

artwork_url = _artwork_url(
    track
)

return templates.TemplateResponse(
    request,
    "track_detail.html",
    ctx(
        request,
        current_user,
        track=track,
        purchased=purchased,
        artwork_url=artwork_url,
    ),
)
```

# ======================================================================

# ALBUM DETAIL

# ======================================================================

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

```
if not album:
    raise HTTPException(
        status_code=404,
        detail="Album not found.",
    )

artwork_url = None

artwork_path = _value(
    album,
    "artwork_path",
    "artwork_url",
    default=None,
)

if artwork_path:
    try:
        artwork_url = media_url(
            str(artwork_path)
        )
    except Exception:
        try:
            artwork_url = r2_presigned_url(
                str(artwork_path)
            )
        except Exception:
            artwork_url = None

return templates.TemplateResponse(
    request,
    "album_detail.html",
    ctx(
        request,
        current_user,
        album=album,
        artwork_url=artwork_url,
    ),
)
```

# ======================================================================

# CREATOR PROFILE

# ======================================================================

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

```
if not profile:
    raise HTTPException(
        status_code=404,
        detail="Profile not found.",
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
    if bool(
        _value(
            track,
            "is_published",
            default=True,
        )
    )
    and _is_public_track(track)
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
    if bool(
        _value(
            album,
            "is_published",
            default=True,
        )
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
```

# ======================================================================

# SECURE PURCHASE DOWNLOAD

# ======================================================================

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
track_ref: str,
db: Session = Depends(get_db),
user: User = Depends(require_user),
):
"""
Secure purchased-track download.

```
Supports both Track ID and Track slug.

Cloudflare R2 is preserved. The purchased file is never exposed
without a completed License belonging to the current user.
"""

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
        License.order_id == Order.id,
    )
    .filter(
        License.buyer_id == user.id,
        License.track_id == track.id,
        Order.status == OrderStatus.COMPLETED,
    )
    .first()
)

if not license_record:
    raise HTTPException(
        status_code=403,
        detail="You do not own this track.",
    )

audio_path = _value(
    track,
    "audio_file_path",
    default=None,
)

if not audio_path:
    raise HTTPException(
        status_code=404,
        detail="Audio file is not available.",
    )

audio_text = _text(
    audio_path
)

# --------------------------------------------------------------
# R2 / CLOUD STORAGE
# --------------------------------------------------------------

if (
    audio_text.startswith(
        "r2://"
    )
    or audio_text.startswith(
        "s3://"
    )
    or (
        getattr(
            settings,
            "r2_enabled",
            False,
        )
        and not audio_text.startswith(
            "/"
        )
    )
):
    try:
        signed_url = r2_presigned_url(
            audio_text,
            expires=900,
        )
    except Exception:
        signed_url = None

    if signed_url:
        return RedirectResponse(
            url=signed_url,
            status_code=307,
        )

# --------------------------------------------------------------
# LOCAL MEDIA FALLBACK
# --------------------------------------------------------------

from pathlib import Path
from fastapi.responses import FileResponse

stored_path = Path(
    audio_text
)

if stored_path.is_absolute():
    audio_path = stored_path
else:
    audio_path = (
        Path.cwd()
        / stored_path
    )

if (
    not audio_path.exists()
    or not audio_path.is_file()
):
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

    filename = stored_path.name

    candidates = [
        media_root / stored_path,
        media_root / "audio" / filename,
        Path.cwd()
        / "media"
        / "audio"
        / filename,
    ]

    found = None

    for candidate in candidates:
        candidate = candidate.resolve()

        try:
            candidate.relative_to(
                media_root.resolve()
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
            detail="The purchased audio file is unavailable.",
        )

    audio_path = found

safe_title = "".join(
    character
    for character in _track_title(track)
    if character.isalnum()
    or character in {
        " ",
        "-",
        "_",
    }
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
    media_type="application/octet-stream",
    headers={
        "Content-Disposition": (
            f'attachment; filename="{download_name}"'
        ),
        "Cache-Control": "private, no-store",
    },
)
```
