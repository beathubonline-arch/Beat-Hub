"""
BeatHub merchandise routes.

First merchandise release:
- Creator uploads a product image/logo.
- Creator sets product name, description and price.
- Every product belongs to the creator profile that uploaded it.
- Products are immediately visible.
- No sizes, colours, stock, shipping, checkout or M-Pesa flow is added here.

Storage:
- Uses the existing BeatHub storage service.
- Supports local MEDIA_ROOT files.
- Supports Cloudflare R2 objects.
- Local files are served securely through /media/{path}.
- Existing database image_path values are preserved.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from mimetypes import guess_type
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import (
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    media_url,
    save_upload,
    storage_exists,
)
from app.utils.deps import get_optional_user, require_creator


router = APIRouter(
    tags=["merchandise"]
)

templates = Jinja2Templates(
    directory="app/templates"
)

MERCH_TABLE = "beathub_merchandise"


# ======================================================================
# DATABASE COMPATIBILITY
# ======================================================================

def ensure_merch_table(
    db: Session,
) -> None:
    """
    Create the additive merchandise table when required.

    This remains compatible with the existing SQLite/PostgreSQL
    architecture and does not alter Track, Album or Order tables.
    """

    db.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {MERCH_TABLE} (
                id VARCHAR(36) PRIMARY KEY,
                creator_profile_id VARCHAR(255) NOT NULL,
                name VARCHAR(160) NOT NULL,
                slug VARCHAR(220) NOT NULL UNIQUE,
                description TEXT,
                price NUMERIC(12, 2) NOT NULL,
                image_path TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )

    db.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS
            idx_{MERCH_TABLE}_creator
            ON {MERCH_TABLE}(creator_profile_id)
            """
        )
    )

    db.commit()


# ======================================================================
# TEMPLATE CONTEXT
# ======================================================================

def _ctx(
    request: Request,
    current_user=None,
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,
        "error": None,
        "success": None,
    }

    context.update(extra)

    return context


# ======================================================================
# SLUG HELPERS
# ======================================================================

def _slugify(
    value: str,
) -> str:
    value = (
        value or ""
    ).strip().lower()

    chars = []
    previous_dash = False

    for char in value:
        if char.isalnum():
            chars.append(char)
            previous_dash = False

        elif not previous_dash:
            chars.append("-")
            previous_dash = True

    slug = "".join(chars).strip("-")

    return (
        slug
        or f"merch-{uuid4().hex[:10]}"
    )


def _unique_merch_slug(
    db: Session,
    name: str,
) -> str:
    base = (
        _slugify(name)[:180]
        or f"merch-{uuid4().hex[:10]}"
    )

    slug = base
    suffix = 2

    while db.execute(
        text(
            f"""
            SELECT 1
            FROM {MERCH_TABLE}
            WHERE slug = :slug
            LIMIT 1
            """
        ),
        {
            "slug": slug
        },
    ).first():

        suffix_text = f"-{suffix}"

        slug = (
            f"{base[:220 - len(suffix_text)]}"
            f"{suffix_text}"
        )

        suffix += 1

    return slug


# ======================================================================
# MEDIA PATH HELPERS
# ======================================================================

def _normalise_media_path(
    path: Optional[str],
) -> Optional[str]:
    """
    Normalise a stored local media path.

    Supported stored values:

        merch/file.png
        /merch/file.png
        media/merch/file.png
        /media/merch/file.png

    Returned value:

        merch/file.png
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    value = value.replace(
        "\\",
        "/",
    )

    value = value.lstrip("/")

    if value.startswith("media/"):
        value = value[6:]

    return value


def _local_media_file(
    path: Optional[str],
) -> Optional[Path]:
    """
    Resolve a local media path safely inside MEDIA_ROOT.

    Returns None for:
    - R2 objects
    - HTTP URLs
    - invalid paths
    - paths escaping MEDIA_ROOT
    - missing files
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith(
        "r2://"
    ):
        return None

    if value.startswith(
        "http://"
    ) or value.startswith(
        "https://"
    ):
        return None

    clean = _normalise_media_path(
        value
    )

    if not clean:
        return None

    media_root = Path(
        settings.MEDIA_ROOT
    ).resolve()

    candidate = (
        media_root / clean
    ).resolve()

    try:
        candidate.relative_to(
            media_root
        )
    except ValueError:
        return None

    if not candidate.exists():
        return None

    if not candidate.is_file():
        return None

    return candidate


# ======================================================================
# PUBLIC MEDIA FILE ROUTE
# ======================================================================

@router.get(
    "/media/{file_path:path}",
    name="serve_media",
)
def serve_media(
    file_path: str,
):
    """
    Serve local BeatHub media files.

    This is intentionally protected against path traversal.

    Examples:

        /media/merch/product.png
        /media/covers/cover.jpg
        /media/artwork/album.jpg
        /media/audio/beat.mp3
    """

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    clean = (
        file_path
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        clean = clean[6:]

    media_root = Path(
        settings.MEDIA_ROOT
    ).resolve()

    target = (
        media_root / clean
    ).resolve()

    try:
        target.relative_to(
            media_root
        )
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    if not target.is_file():
        raise HTTPException(
            status_code=404,
            detail="Media file not found.",
        )

    content_type = (
        guess_type(
            target.name
        )[0]
        or "application/octet-stream"
    )

    return FileResponse(
        path=str(target),
        media_type=content_type,
        filename=target.name,
        headers={
            "Cache-Control": (
                "public, "
                "max-age=86400"
            )
        },
    )


# ======================================================================
# IMAGE URL
# ======================================================================

def _image_url(
    path: Optional[str],
) -> Optional[str]:
    """
    Convert a stored image path into a browser-accessible URL.

    R2:
        r2://bucket/merch/file.png
        -> signed R2 URL

    HTTPS:
        https://...
        -> unchanged

    Local:
        merch/file.png
        -> /media/merch/file.png
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    try:
        url = media_url(
            value,
            expires=86400,
        )
    except Exception:
        url = None

    if url:
        return url

    return None


# ======================================================================
# CREATOR MERCHANDISE ROWS
# ======================================================================

def _rows_for_creator(
    db: Session,
    profile_id: str,
):
    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                creator_profile_id,
                name,
                slug,
                description,
                price,
                image_path,
                created_at
            FROM {MERCH_TABLE}
            WHERE creator_profile_id = :profile_id
            ORDER BY created_at DESC
            """
        ),
        {
            "profile_id": str(
                profile_id
            )
        },
    ).mappings().all()

    products = []

    for row in rows:

        item = dict(row)

        image_path = item.get(
            "image_path"
        )

        item["image_url"] = _image_url(
            image_path
        )

        item["image_exists"] = (
            storage_exists(
                image_path
            )
        )

        products.append(
            item
        )

    return products


def _public_rows(
    db: Session,
    profile_id: str,
):
    return _rows_for_creator(
        db,
        profile_id,
    )


# ======================================================================
# CREATOR MERCH DASHBOARD
# ======================================================================

@router.get(
    "/dashboard/merch"
)
def merch_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    ensure_merch_table(
        db
    )

    products = _rows_for_creator(
        db,
        str(profile.id),
    )

    success = request.query_params.get(
        "success"
    )

    error = request.query_params.get(
        "error"
    )

    return templates.TemplateResponse(
        request,
        "merchandise.html",
        _ctx(
            request,
            user,
            profile=profile,
            products=products,
            product_count=len(products),
            success=success,
            error=error,
        ),
    )


# ======================================================================
# NEW MERCHANDISE PAGE
# ======================================================================

@router.get(
    "/dashboard/merch/new"
)
def merch_new_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "merchandise_new.html",
        _ctx(
            request,
            user,
        ),
    )


# ======================================================================
# CREATE MERCHANDISE
# ======================================================================

@router.post(
    "/dashboard/merch/new"
)
async def merch_create(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    name: str = Form(...),
    description: str = Form(""),
    price: str = Form(...),
    image: UploadFile = File(...),
):
    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    ensure_merch_table(
        db
    )

    name = (
        name or ""
    ).strip()

    description = (
        description or ""
    ).strip()

    price_raw = (
        price or ""
    ).strip()

    def error(
        message: str,
    ):
        return templates.TemplateResponse(
            request,
            "merchandise_new.html",
            _ctx(
                request,
                user,
                error=message,
                name=name,
                description=description,
                price=price_raw,
            ),
            status_code=400,
        )

    if not name:
        return error(
            "Product name is required."
        )

    if len(name) > 160:
        return error(
            "Product name is too long. Keep it under 160 characters."
        )

    if len(description) > 4000:
        return error(
            "Product description is too long. Keep it under 4,000 characters."
        )

    try:
        price_value = Decimal(
            price_raw
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return error(
            "Enter a valid product price."
        )

    if price_value <= Decimal("0"):
        return error(
            "Product price must be greater than zero."
        )

    if (
        not image
        or not image.filename
    ):
        return error(
            "A product image is required."
        )

    try:
        image_path = await save_upload(
            image,
            "merch",
            ALLOWED_IMAGE_EXT,
        )

    except UploadValidationError as exc:
        return error(
            str(exc)
        )

    except Exception as exc:
        return error(
            f"Product image upload failed: {exc}"
        )

    product_id = str(
        uuid4()
    )

    slug = _unique_merch_slug(
        db,
        name,
    )

    try:
        db.execute(
            text(
                f"""
                INSERT INTO {MERCH_TABLE} (
                    id,
                    creator_profile_id,
                    name,
                    slug,
                    description,
                    price,
                    image_path
                )
                VALUES (
                    :id,
                    :creator_profile_id,
                    :name,
                    :slug,
                    :description,
                    :price,
                    :image_path
                )
                """
            ),
            {
                "id": product_id,
                "creator_profile_id": str(
                    profile.id
                ),
                "name": name,
                "slug": slug,
                "description": (
                    description
                    or None
                ),
                "price": price_value,
                "image_path": image_path,
            },
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            "/dashboard/merch"
            "?success="
            "Merchandise%20added%20successfully."
        ),
        status_code=303,
    )


# ======================================================================
# PUBLIC MERCH MARKETPLACE
# ======================================================================

@router.get(
    "/merch"
)
def merch_marketplace(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    ensure_merch_table(
        db
    )

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                creator_profile_id,
                name,
                slug,
                description,
                price,
                image_path,
                created_at
            FROM {MERCH_TABLE}
            ORDER BY created_at DESC
            LIMIT 120
            """
        )
    ).mappings().all()

    profile_ids = {
        str(
            row[
                "creator_profile_id"
            ]
        )
        for row in rows
    }

    profiles = {}

    if profile_ids:

        for profile in (
            db.query(Profile)
            .filter(
                Profile.id.in_(
                    list(profile_ids)
                )
            )
            .all()
        ):
            profiles[
                str(profile.id)
            ] = profile

    products = []

    for row in rows:

        item = dict(row)

        owner = profiles.get(
            str(
                item.get(
                    "creator_profile_id"
                )
            )
        )

        item["creator_slug"] = getattr(
            owner,
            "slug",
            None,
        )

        item["creator_name"] = (
            getattr(
                owner,
                "stage_name",
                None,
            )
            or getattr(
                owner,
                "name",
                None,
            )
        )

        image_path = item.get(
            "image_path"
        )

        item["image_url"] = _image_url(
            image_path
        )

        item["image_exists"] = (
            storage_exists(
                image_path
            )
        )

        products.append(
            item
        )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=products,
            title="BeatHub Merch",
            creator=None,
            store_url=None,
        ),
    )


# ======================================================================
# CREATOR MERCH STORE
# ======================================================================

@router.get(
    "/store/{slug}/merch"
)
def creator_merch_store(
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

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Creator store not found.",
        )

    ensure_merch_table(
        db
    )

    products = _public_rows(
        db,
        str(profile.id),
    )

    creator_name = (
        getattr(
            profile,
            "stage_name",
            None,
        )
        or getattr(
            profile,
            "name",
            None,
        )
        or "Creator"
    )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=products,
            title=(
                f"{creator_name} — Merch"
            ),
            creator=profile,
            store_url=(
                f"/store/{profile.slug}"
            ),
        ),
    )


# ======================================================================
# SINGLE MERCH PRODUCT
# ======================================================================

@router.get(
    "/merch/{slug}"
)
def merch_product(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    ensure_merch_table(
        db
    )

    row = db.execute(
        text(
            f"""
            SELECT
                id,
                creator_profile_id,
                name,
                slug,
                description,
                price,
                image_path,
                created_at
            FROM {MERCH_TABLE}
            WHERE slug = :slug
            LIMIT 1
            """
        ),
        {
            "slug": slug
        },
    ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Merchandise item not found.",
        )

    item = dict(row)

    image_path = item.get(
        "image_path"
    )

    item["image_url"] = _image_url(
        image_path
    )

    item["image_exists"] = (
        storage_exists(
            image_path
        )
    )

    profile = (
        db.query(Profile)
        .filter(
            Profile.id
            == str(
                item[
                    "creator_profile_id"
                ]
            )
        )
        .first()
    )

    creator_name = (
        getattr(
            profile,
            "stage_name",
            None,
        )
        or getattr(
            profile,
            "name",
            None,
        )
        or "Creator"
    )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=[item],
            title=item[
                "name"
            ],
            creator=profile,
            store_url=(
                f"/store/{profile.slug}"
                if profile
                else None
            ),
            product_detail=True,
            creator_name=creator_name,
        ),
    )
