"""
BeatHub merchandise routes.

First merchandise release:
- Creator uploads a product image/logo.
- Creator sets product name, description and price.
- Every product belongs to the creator profile that uploaded it.
- Products are immediately visible; there is no separate publish switch yet.
- No sizes, colours, stock, shipping, checkout or M-Pesa flow is added here yet.

Compatibility:
- Uses the existing SQLAlchemy Session from app.database.
- Uses the existing creator authentication dependency.
- Uses the existing storage service, so merchandise images follow the same
  local/R2 storage path as beat and album artwork.
- Uses a small additive SQL table instead of changing the existing Track,
  Album or Order models. Existing music/payment behaviour is untouched.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import ALLOWED_IMAGE_EXT, UploadValidationError, r2_presigned_url, save_upload
from app.utils.deps import get_optional_user, require_creator


router = APIRouter(tags=["merchandise"])
templates = Jinja2Templates(directory="app/templates")

MERCH_TABLE = "beathub_merchandise"


# ======================================================================
# DATABASE COMPATIBILITY
# ======================================================================

def ensure_merch_table(db: Session) -> None:
    """
    Create the additive merchandise table when it does not exist.

    The SQL is intentionally portable between the SQLite development
    database and PostgreSQL on Render.
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
            CREATE INDEX IF NOT EXISTS idx_{MERCH_TABLE}_creator
            ON {MERCH_TABLE}(creator_profile_id)
            """
        )
    )
    db.commit()


# ======================================================================
# HELPERS
# ======================================================================

def _ctx(request: Request, current_user=None, **extra):
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


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
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
    return slug or f"merch-{uuid4().hex[:10]}"


def _unique_merch_slug(db: Session, name: str) -> str:
    base = _slugify(name)[:180] or f"merch-{uuid4().hex[:10]}"
    slug = base
    suffix = 2

    while db.execute(
        text(
            f"SELECT 1 FROM {MERCH_TABLE} WHERE slug = :slug LIMIT 1"
        ),
        {"slug": slug},
    ).first():
        suffix_text = f"-{suffix}"
        slug = f"{base[:220 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    return slug


def _image_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None

    try:
        return r2_presigned_url(path)
    except Exception:
        return None


def _rows_for_creator(db: Session, profile_id: str):
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
        {"profile_id": str(profile_id)},
    ).mappings().all()

    products = []
    for row in rows:
        item = dict(row)
        item["image_url"] = _image_url(item.get("image_path"))
        products.append(item)
    return products


def _public_rows(db: Session, profile_id: str):
    return _rows_for_creator(db, profile_id)


# ======================================================================
# CREATOR MERCH DASHBOARD
# ======================================================================

@router.get("/dashboard/merch")
def merch_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    ensure_merch_table(db)

    products = _rows_for_creator(db, str(profile.id))

    return templates.TemplateResponse(
        request,
        "merchandise.html",
        _ctx(
            request,
            user,
            profile=profile,
            products=products,
            product_count=len(products),
        ),
    )


@router.get("/dashboard/merch/new")
def merch_new_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "merchandise_new.html",
        _ctx(request, user),
    )


@router.post("/dashboard/merch/new")
async def merch_create(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    name: str = Form(...),
    description: str = Form(""),
    price: str = Form(...),
    image: UploadFile = File(...),
):
    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    ensure_merch_table(db)

    name = (name or "").strip()
    description = (description or "").strip()
    price_raw = (price or "").strip()

    def error(message: str):
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
        return error("Product name is required.")

    if len(name) > 160:
        return error("Product name is too long. Keep it under 160 characters.")

    if len(description) > 4000:
        return error("Product description is too long. Keep it under 4,000 characters.")

    try:
        price_value = Decimal(price_raw)
    except (InvalidOperation, ValueError, TypeError):
        return error("Enter a valid product price.")

    if price_value <= Decimal("0"):
        return error("Product price must be greater than zero.")

    if not image or not image.filename:
        return error("A product image is required.")

    try:
        image_path = await save_upload(
            image,
            "merch",
            ALLOWED_IMAGE_EXT,
        )
    except UploadValidationError as exc:
        return error(str(exc))
    except Exception as exc:
        return error(f"Product image upload failed: {exc}")

    product_id = str(uuid4())
    slug = _unique_merch_slug(db, name)

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
                "creator_profile_id": str(profile.id),
                "name": name,
                "slug": slug,
                "description": description or None,
                "price": price_value,
                "image_path": image_path,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url="/dashboard/merch?success=Merchandise%20added%20successfully.",
        status_code=303,
    )


# ======================================================================
# PUBLIC MERCH MARKETPLACE
# ======================================================================

@router.get("/merch")
def merch_marketplace(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
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

    profile_ids = {str(row["creator_profile_id"]) for row in rows}
    profiles = {}
    if profile_ids:
        for profile in (
            db.query(Profile)
            .filter(Profile.id.in_(list(profile_ids)))
            .all()
        ):
            profiles[str(profile.id)] = profile

    products = []
    for row in rows:
        item = dict(row)
        owner = profiles.get(str(item.get("creator_profile_id")))
        item["creator_slug"] = getattr(owner, "slug", None)
        item["creator_name"] = getattr(owner, "stage_name", None)
        item["image_url"] = _image_url(item.get("image_path"))
        products.append(item)

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


@router.get("/store/{slug}/merch")
def creator_merch_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    )

    if profile is None:
        raise HTTPException(status_code=404, detail="Creator store not found.")

    ensure_merch_table(db)
    products = _public_rows(db, str(profile.id))

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=products,
            title=f"{profile.stage_name} — Merch",
            creator=profile,
            store_url=f"/store/{profile.slug}",
        ),
    )


@router.get("/merch/{slug}")
def merch_product(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
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
        {"slug": slug},
    ).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Merchandise item not found.")

    item = dict(row)
    item["image_url"] = _image_url(item.get("image_path"))

    profile = (
        db.query(Profile)
        .filter(Profile.id == str(item["creator_profile_id"]))
        .first()
    )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=[item],
            title=item["name"],
            creator=profile,
            store_url=(f"/store/{profile.slug}" if profile else None),
            product_detail=True,
        ),
    )
