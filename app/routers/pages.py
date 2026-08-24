from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.services.search import run_search
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["pages"])

templates = Jinja2Templates(
    directory="app/templates"
)


# ======================================================================
# COMMON TEMPLATE CONTEXT
# ======================================================================

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


# ======================================================================
# LOCAL MEDIA HELPERS
# ======================================================================

def _local_media_path(
    stored_path: str,
) -> Optional[Path]:

    value = str(
        stored_path or ""
    ).strip()

    if not value:
        return None

    if value.startswith(
        (
            "http://",
            "https://",
            "r2://",
            "s3://",
        )
    ):
        return None

    stored = Path(value)

    media_root_value = (
        getattr(
            settings,
            "MEDIA_ROOT",
            None,
        )
        or "media"
    )

    media_root = Path(
        media_root_value
    ).expanduser()

    if not media_root.is_absolute():
        media_root = (
            Path.cwd() / media_root
        )

    media_root = media_root.resolve()

    candidates = []

    if stored.is_absolute():

        candidates.append(
            stored.resolve()
        )

    else:

        candidates.append(
            (
                Path.cwd() / stored
            ).resolve()
        )

        candidates.append(
            (
                media_root / stored
            ).resolve()
        )

        clean = (
            str(stored)
            .replace("\\", "/")
            .lstrip("/")
        )

        if clean.startswith("media/"):

            candidates.append(
                (
                    media_root
                    / clean[6:]
                ).resolve()
            )

    for candidate in candidates:

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
            return candidate

    return None


def _media_content_type(
    path: Path,
) -> str:

    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",

        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(
        path.suffix.lower(),
        "application/octet-stream",
    )


def _serve_local_media(
    stored_path: str,
):

    path = _local_media_path(
        stored_path
    )

    if not path:

        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    return FileResponse(
        path=str(path),
        media_type=_media_content_type(
            path
        ),
        headers={
            "Cache-Control":
                "public, max-age=3600",
        },
    )


# ======================================================================
# HOME
# ======================================================================

@router.get("/")
def home(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):

    query = (
        q or ""
    ).strip()

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


# ======================================================================
# SEARCH
# ======================================================================

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


# ======================================================================
# TERMS
# ======================================================================

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


# ======================================================================
# PUBLIC CREATOR STORE
#
# PRIMARY:
#     /store/{slug}
#
# COMPATIBILITY:
#     /creator/{slug}
#     /profile/{slug}
# ======================================================================


def _normalize_public_slug(
    value: str,
) -> str:

    value = str(
        value or ""
    ).strip().lower()

    if not value:
        return ""

    result = []

    previous_dash = False

    for character in value:

        if (
            character.isalnum()
            or character == "_"
        ):

            result.append(
                character
            )
            previous_dash = False

        elif character in (
            "-",
            " ",
            ".",
        ):

            if not previous_dash:
                result.append("-")

            previous_dash = True

    return "".join(
        result
    ).strip("-")


def _profile_has_column(
    column_name: str,
) -> bool:

    try:

        return column_name in {
            attribute.key
            for attribute
            in Profile.__mapper__.attrs
        }

    except Exception:

        return False


def _user_has_column(
    column_name: str,
) -> bool:

    try:

        return column_name in {
            attribute.key
            for attribute
            in User.__mapper__.attrs
        }

    except Exception:

        return False


def _find_public_profile(
    db: Session,
    slug: str,
) -> Optional[Profile]:

    requested = str(
        slug or ""
    ).strip()

    if not requested:
        return None

    clean_slug = (
        requested.lower()
    )

    normalized_slug = (
        _normalize_public_slug(
            requested
        )
    )

    # --------------------------------------------------------------
    # 1. Exact stored slug
    # --------------------------------------------------------------

    if _profile_has_column(
        "slug"
    ):

        profile = (
            db.query(Profile)
            .filter(
                Profile.slug
                == clean_slug
            )
            .first()
        )

        if profile:
            return profile

        # ----------------------------------------------------------
        # 2. Case-insensitive slug
        # ----------------------------------------------------------

        try:

            profile = (
                db.query(Profile)
                .filter(
                    Profile.slug.ilike(
                        clean_slug
                    )
                )
                .first()
            )

            if profile:
                return profile

        except Exception:

            pass

        # ----------------------------------------------------------
        # 3. Normalized slug comparison
        #
        # This catches things such as:
        #
        # Mr Mapema
        # mr_mapema
        # mr-mapema
        #
        # when the stored slug differs slightly.
        # ----------------------------------------------------------

        try:

            profiles = (
                db.query(Profile)
                .all()
            )

            for profile in profiles:

                stored_slug = getattr(
                    profile,
                    "slug",
                    None,
                )

                if not stored_slug:
                    continue

                if (
                    _normalize_public_slug(
                        stored_slug
                    )
                    == normalized_slug
                ):
                    return profile

        except Exception:

            pass

    # --------------------------------------------------------------
    # 4. Stage name fallback
    #
    # This is important for existing BeatHub accounts whose profile
    # slug was not generated/stored correctly.
    # --------------------------------------------------------------

    for field_name in (
        "stage_name",
        "display_name",
        "name",
    ):

        if not _profile_has_column(
            field_name
        ):
            continue

        try:

            column = getattr(
                Profile,
                field_name,
            )

            profile = (
                db.query(Profile)
                .filter(
                    column.ilike(
                        requested
                    )
                )
                .first()
            )

            if profile:
                return profile

        except Exception:

            pass

        # Normalized stage-name fallback.

        try:

            profiles = (
                db.query(Profile)
                .all()
            )

            for profile in profiles:

                value = getattr(
                    profile,
                    field_name,
                    None,
                )

                if not value:
                    continue

                if (
                    _normalize_public_slug(
                        value
                    )
                    == normalized_slug
                ):
                    return profile

        except Exception:

            pass

    # --------------------------------------------------------------
    # 5. Username fallback
    #
    # Existing users may have their public identity stored on User
    # instead of Profile.
    # --------------------------------------------------------------

    if _profile_has_column(
        "user_id"
    ):

        try:

            user_query = (
                db.query(
                    User,
                    Profile,
                )
                .join(
                    Profile,
                    Profile.user_id
                    == User.id,
                )
            )

            for field_name in (
                "username",
                "name",
                "email",
            ):

                if not _user_has_column(
                    field_name
                ):
                    continue

                try:

                    column = getattr(
                        User,
                        field_name,
                    )

                    result = (
                        user_query
                        .filter(
                            column.ilike(
                                requested
                            )
                        )
                        .first()
                    )

                    if result:

                        return result[1]

                except Exception:

                    continue

            # Normalized username fallback.

            try:

                rows = (
                    user_query.all()
                )

                for user, profile in rows:

                    for field_name in (
                        "username",
                        "name",
                        "email",
                    ):

                        value = getattr(
                            user,
                            field_name,
                            None,
                        )

                        if not value:
                            continue

                        if (
                            _normalize_public_slug(
                                value
                            )
                            == normalized_slug
                        ):
                            return profile

            except Exception:

                pass

        except Exception:

            pass

    return None


def _get_public_artwork_url(
    stored_path,
) -> Optional[str]:

    if not stored_path:
        return None

    value = str(
        stored_path
    ).strip()

    if not value:
        return None

    # Already a usable public URL.
    if value.startswith(
        (
            "http://",
            "https://",
        )
    ):
        return value

    try:

        from app.services.storage import (
            r2_presigned_url,
        )

        url = r2_presigned_url(
            value
        )

        if url:
            return url

    except Exception:

        pass

    return None


def _public_creator_store(
    request: Request,
    slug: str,
    db: Session,
    current_user: Optional[User] = None,
):

    profile = _find_public_profile(
        db,
        slug,
    )

    if not profile:

        raise HTTPException(
            status_code=404,
            detail=(
                "Creator store not found."
            ),
        )

    # ==============================================================
    # CREATOR
    # ==============================================================

    creator = getattr(
        profile,
        "user",
        None,
    )

    if creator is None:
        creator = profile

    # ==============================================================
    # PUBLIC TRACKS
    # ==============================================================

    tracks = list(
        getattr(
            profile,
            "tracks",
            None,
        )
        or []
    )

    public_tracks = []

    for track in tracks:

        # ----------------------------------------------------------
        # Published only
        # ----------------------------------------------------------

        if not getattr(
            track,
            "is_published",
            True,
        ):
            continue

        # ----------------------------------------------------------
        # Hide sold exclusive tracks
        # ----------------------------------------------------------

        sales_model = getattr(
            track,
            "sales_model",
            None,
        )

        sales_model_value = getattr(
            sales_model,
            "value",
            (
                str(sales_model)
                if sales_model is not None
                else ""
            ),
        )

        if (
            str(
                sales_model_value
            ).strip().lower()
            == "exclusive"
            and getattr(
                track,
                "is_sold",
                False,
            )
        ):
            continue

        # ----------------------------------------------------------
        # Artwork URL
        # ----------------------------------------------------------

        cover_path = getattr(
            track,
            "cover_art_path",
            None,
        )

        artwork_url = (
            _get_public_artwork_url(
                cover_path
            )
        )

        if artwork_url:

            try:

                track.cover_art_url = (
                    artwork_url
                )

            except Exception:

                pass

        public_tracks.append(
            track
        )

    # ==============================================================
    # PUBLIC ALBUMS
    # ==============================================================

    albums = list(
        getattr(
            profile,
            "albums",
            None,
        )
        or []
    )

    public_albums = []

    for album in albums:

        if not getattr(
            album,
            "is_published",
            True,
        ):
            continue

        artwork_path = getattr(
            album,
            "artwork_path",
            None,
        )

        artwork_url = (
            _get_public_artwork_url(
                artwork_path
            )
        )

        if artwork_url:

            try:

                album.artwork_url = (
                    artwork_url
                )

            except Exception:

                pass

        public_albums.append(
            album
        )

    # ==============================================================
    # PUBLIC CREATOR PHOTO
    #
    # Support the profile-photo fields that have appeared across
    # BeatHub versions without forcing a database migration.
    # ==============================================================

    profile_photo_url = None

    for field_name in (
        "photo_url",
        "profile_photo_url",
        "avatar_url",
        "image_url",
        "profile_image_url",
    ):

        value = getattr(
            profile,
            field_name,
            None,
        )

        if value:

            profile_photo_url = (
                _get_public_artwork_url(
                    value
                )
                or str(value)
            )

            if profile_photo_url:
                break

    # ==============================================================
    # PUBLIC STORE URL
    # ==============================================================

    canonical_slug = getattr(
        profile,
        "slug",
        None,
    )

    if not canonical_slug:
        canonical_slug = (
            _normalize_public_slug(
                requested
            )
        )

    store_url = (
        str(
            request.base_url
        ).rstrip("/")
        + "/store/"
        + str(
            canonical_slug
        )
    )

    # ==============================================================
    # TEMPLATE
    # ==============================================================

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

            profile_photo_url=(
                profile_photo_url
            ),

            store_url=store_url,

            store_path=(
                "/store/"
                + str(
                    canonical_slug
                )
            ),

            profile_slug=str(
                canonical_slug
            ),
        ),
    )


# ======================================================================
# PRIMARY PUBLIC STORE
# ======================================================================

@router.get(
    "/store/{slug}",
    name="public_store",
)
def public_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):

    return _public_creator_store(
        request=request,
        slug=slug,
        db=db,
        current_user=current_user,
    )


# ======================================================================
# CREATOR COMPATIBILITY
# ======================================================================

@router.get(
    "/creator/{slug}",
    name="creator_store",
)
def creator_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):

    return _public_creator_store(
        request=request,
        slug=slug,
        db=db,
        current_user=current_user,
    )


# ======================================================================
# PROFILE COMPATIBILITY
# ======================================================================

@router.get(
    "/profile/{slug}",
    name="profile_store",
)
def profile_detail_legacy(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):

    return _public_creator_store(
        request=request,
        slug=slug,
        db=db,
        current_user=current_user,
    )


# ======================================================================
# BUYER ACCOUNT
# ======================================================================

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
            for order
            in completed_orders
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


# ======================================================================
# MY PURCHASES
# ======================================================================

@router.get(
    "/account/purchases"
)
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


# ======================================================================
# MY DOWNLOADS
# ======================================================================

@router.get(
    "/account/downloads"
)
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


# ======================================================================
# SECURE TRACK DOWNLOAD
# ======================================================================

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
            detail=(
                "You do not own "
                "this track."
            ),
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
                "The purchased audio "
                "file is currently "
                "unavailable."
            ),
        )

    stored_text = str(
        stored_path
    ).strip()

    if stored_text.startswith(
        (
            "http://",
            "https://",
            "r2://",
            "s3://",
        )
    ):

        raise HTTPException(
            status_code=404,
            detail=(
                "This download is stored "
                "in cloud storage and is "
                "not available through "
                "the local download "
                "endpoint."
            ),
        )

    path = _local_media_path(
        stored_text
    )

    if not path:

        raise HTTPException(
            status_code=404,
            detail=(
                "The purchased audio "
                "file is currently "
                "unavailable."
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
            or character
            in " ._-"
        )
        else "_"
        for character
        in str(title)
    ).strip()

    filename = (
        f"{safe_title or 'BeatHub_Track'}"
        f"{path.suffix.lower() or '.mp3'}"
    )

    return FileResponse(
        path=str(path),
        media_type=_media_content_type(
            path
        ),
        filename=filename,
        headers={
            "Cache-Control":
                "private, no-store",
        },
    )


# ======================================================================
# MY ORDERS
# ======================================================================

@router.get(
    "/account/orders"
)
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


# ======================================================================
# ACCOUNT SETTINGS
# ======================================================================

@router.get(
    "/account/settings"
)
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


# ======================================================================
# LOCAL MEDIA COMPATIBILITY
# ======================================================================

@router.get(
    "/media/{media_path:path}",
    include_in_schema=False,
)
def media_file(
    media_path: str,
):

    clean = (
        str(media_path or "")
        .replace("\\", "/")
        .lstrip("/")
    )

    if (
        not clean
        or clean.startswith(
            (
                ".",
                "../",
                "..\\",
            )
        )
    ):

        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    return _serve_local_media(
        f"media/{clean}"
    )


# ======================================================================
# DASHBOARD COMPATIBILITY
# ======================================================================

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


# ======================================================================
# HEALTH
# ======================================================================

@router.api_route(
    "/healthz",
    methods=[
        "GET",
        "HEAD",
    ],
    include_in_schema=False,
)
def healthz_compat():

    return {
        "status": "ok",
    }
