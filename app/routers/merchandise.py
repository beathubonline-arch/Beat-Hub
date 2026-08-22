"""
BeatHub merchandise routes.

Merchandise provides:
- Creator merchandise dashboard
- Product creation
- Product image upload
- Public merchandise marketplace
- Creator-specific merchandise store
- Individual merchandise pages
- Safe local/R2 image URL handling
- Clean redirects and validation
- Compatibility with the existing creator authentication system

Important:
- Merchandise uses its own additive SQL table.
- Existing Track, Album, Order and M-Pesa behaviour is untouched.
- Merchandise does not change the existing 10% music-sales commission logic.
- Storage is handled entirely through app.services.storage.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
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
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import (
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    r2_presigned_url,
    save_upload,
)
from app.utils.deps import (
    get_optional_user,
    require_creator,
)


router = APIRouter(
    tags=["merchandise"]
)

templates = Jinja2Templates(
    directory="app/templates"
)

MERCH_TABLE = "beathub_merchandise"


# ======================================================================
# TEMPLATE CONTEXT
# ======================================================================

def _context(
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
# DATABASE
# ======================================================================

def ensure_merch_table(
    db: Session,
) -> None:
    """
    Create the merchandise table if it does not already exist.

    This is intentionally additive so it does not modify:
        - tracks
        - albums
        - orders
        - payments
        - withdrawals
        - M-Pesa data
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
                created_at TIMESTAMP NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
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
# SLUG HELPERS
# ======================================================================

def _slugify(
    value: str,
) -> str:
    value = (
        value or ""
    ).strip().lower()

    characters = []
    previous_dash = False

    for character in value:
        if character.isalnum():
            characters.append(character)
            previous_dash = False
        else:
            if not previous_dash:
                characters.append("-")
                previous_dash = True

    slug = (
        "".join(characters)
        .strip("-")
    )

    if not slug:
        return f"merch-{uuid4().hex[:10]}"

    return slug


def _unique_slug(
    db: Session,
    name: str,
) -> str:
    base = _slugify(name)

    base = base[:180]

    if not base:
        base = f"merch-{uuid4().hex[:10]}"

    slug = base
    counter = 2

    while True:
        existing = db.execute(
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
        ).first()

        if not existing:
            return slug

        suffix = f"-{counter}"

        slug = (
            f"{base[:220 - len(suffix)]}"
            f"{suffix}"
        )

        counter += 1


# ======================================================================
# IMAGE HELPERS
# ======================================================================

def _image_url(
    path: Optional[str],
) -> Optional[str]:
    if not path:
        return None

    try:
        return r2_presigned_url(path)
    except Exception:
        return None


# ======================================================================
# PRODUCT SERIALIZATION
# ======================================================================

def _serialize_product(
    row,
) -> dict:
    item = dict(row)

    item["image_url"] = _image_url(
        item.get("image_path")
    )

    if item.get("price") is not None:
        try:
            item["price"] = Decimal(
                str(item["price"])
            )
        except Exception:
            item["price"] = Decimal("0")

    return item


# ======================================================================
# CREATOR PRODUCTS
# ======================================================================

def _creator_products(
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
            "profile_id": str(profile_id)
        },
    ).mappings().all()

    return [
        _serialize_product(row)
        for row in rows
    ]


# ======================================================================
# PUBLIC PRODUCTS
# ======================================================================

def _public_products(
    db: Session,
    profile_id: str,
):
    return _creator_products(
        db,
        profile_id,
    )


# ======================================================================
# CREATOR MERCHANDISE DASHBOARD
# ======================================================================

@router.get(
    "/dashboard/merch"
)
def merchandise_dashboard(
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

    ensure_merch_table(db)

    products = _creator_products(
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
        _context(
            request,
            user,
            profile=profile,
            products=products,
            product_count=len(products),
            success=success,
            error=error,
            title="Merchandise",
        ),
    )


# ======================================================================
# NEW PRODUCT PAGE
# ======================================================================

@router.get(
    "/dashboard/merch/new"
)
def merchandise_new_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "merchandise_new.html",
        _context(
            request,
            user,
            title="Add Merchandise",
            name="",
            description="",
            price="",
        ),
    )


# ======================================================================
# CREATE PRODUCT
# ======================================================================

@router.post(
    "/dashboard/merch/new"
)
async def merchandise_create(
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

    ensure_merch_table(db)

    name = (
        name or ""
    ).strip()

    description = (
        description or ""
    ).strip()

    price_raw = (
        price or ""
    ).strip()

    def validation_error(
        message: str,
    ):
        return templates.TemplateResponse(
            request,
            "merchandise_new.html",
            _context(
                request,
                user,
                title="Add Merchandise",
                error=message,
                name=name,
                description=description,
                price=price_raw,
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # NAME
    # --------------------------------------------------------------

    if not name:
        return validation_error(
            "Product name is required."
        )

    if len(name) > 160:
        return validation_error(
            "Product name is too long. "
            "Keep it under 160 characters."
        )

    # --------------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------------

    if len(description) > 4000:
        return validation_error(
            "Product description is too long. "
            "Keep it under 4,000 characters."
        )

    # --------------------------------------------------------------
    # PRICE
    # --------------------------------------------------------------

    try:
        price_value = Decimal(
            price_raw
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return validation_error(
            "Enter a valid product price."
        )

    if not price_value.is_finite():
        return validation_error(
            "Enter a valid product price."
        )

    if price_value <= Decimal("0"):
        return validation_error(
            "Product price must be greater than zero."
        )

    price_value = price_value.quantize(
        Decimal("0.01")
    )

    # --------------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------------

    if not image:
        return validation_error(
            "A product image is required."
        )

    if not image.filename:
        return validation_error(
            "A product image is required."
        )

    # --------------------------------------------------------------
    # UPLOAD
    # --------------------------------------------------------------

    try:
        image_path = await save_upload(
            image,
            "merch",
            ALLOWED_IMAGE_EXT,
        )

    except UploadValidationError as exc:
        return validation_error(
            str(exc)
        )

    except Exception:
        return validation_error(
            "Product image upload failed. "
            "Please check your storage configuration "
            "and try again."
        )

    # --------------------------------------------------------------
    # CREATE PRODUCT
    # --------------------------------------------------------------

    product_id = str(
        uuid4()
    )

    slug = _unique_slug(
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
                    if description
                    else None
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
def merchandise_marketplace(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    ensure_merch_table(db)

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
        str(row["creator_profile_id"])
        for row in rows
        if row.get(
            "creator_profile_id"
        ) is not None
    }

    profiles = {}

    if profile_ids:
        found_profiles = (
            db.query(Profile)
            .filter(
                Profile.id.in_(
                    list(profile_ids)
                )
            )
            .all()
        )

        for profile in found_profiles:
            profiles[
                str(profile.id)
            ] = profile

    products = []

    for row in rows:
        item = _serialize_product(
            row
        )

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
                "display_name",
                None,
            )
            or "BeatHub Creator"
        )

        products.append(item)

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _context(
            request,
            current_user,
            products=products,
            title="BeatHub Merch",
            creator=None,
            store_url=None,
            product_detail=False,
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
    clean_slug = (
        slug or ""
    ).strip()

    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == clean_slug
        )
        .first()
    )

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Creator store not found.",
        )

    ensure_merch_table(db)

    products = _public_products(
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
            "display_name",
            None,
        )
        or "BeatHub Creator"
    )

    store_url = (
        f"/store/{profile.slug}"
    )

    merch_url = (
        f"/store/{profile.slug}/merch"
    )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _context(
            request,
            current_user,
            products=products,
            title=(
                f"{creator_name} — Merchandise"
            ),
            creator=profile,
            creator_name=creator_name,
            store_url=store_url,
            merch_url=merch_url,
            product_detail=False,
        ),
    )


# ======================================================================
# INDIVIDUAL PRODUCT
# ======================================================================

@router.get(
    "/merch/{slug}"
)
def merchandise_product(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    clean_slug = (
        slug or ""
    ).strip()

    if not clean_slug:
        raise HTTPException(
            status_code=404,
            detail="Merchandise item not found.",
        )

    ensure_merch_table(db)

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
            "slug": clean_slug
        },
    ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Merchandise item not found.",
        )

    item = _serialize_product(
        row
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
            "display_name",
            None,
        )
        or "BeatHub Creator"
    )

    store_url = None

    if profile is not None:
        store_url = (
            f"/store/{profile.slug}"
        )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _context(
            request,
            current_user,
            products=[item],
            product=item,
            title=item["name"],
            creator=profile,
            creator_name=creator_name,
            store_url=store_url,
            product_detail=True,
        ),
    )
