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

```
data.update(extra)

return data
```

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

```
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
```

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

```
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
```

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

```
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
```

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
```

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
```

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

```
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
```
