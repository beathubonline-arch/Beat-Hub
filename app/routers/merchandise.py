from __future__ import annotations

import os
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
from app.services import mpesa
from app.services.storage import (
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    _r2_client,
    _r2_bucket,
    _r2_is_configured,
    media_url,
    r2_presigned_url,
    save_upload,
    save_upload_to_r2,
)
from app.utils.deps import (
    get_optional_user,
    require_creator,
    require_user,
)


router = APIRouter(
    tags=["merchandise"]
)

templates = Jinja2Templates(
    directory="app/templates"
)

MERCH_TABLE = "beathub_merchandise"
MERCH_ORDER_TABLE = "beathub_merchandise_orders"

MERCH_ORDER_NOTE_MAX = 300
MERCH_MAX_QUANTITY = 20

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


# ======================================================================
# DATABASE SCHEMA
# ======================================================================

def ensure_merch_table(
    db: Session,
) -> None:
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

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

        _SCHEMA_READY = True


def ensure_merch_orders_table(
    db: Session,
) -> None:
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

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

        db.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {MERCH_ORDER_TABLE} (
                    id VARCHAR(36) PRIMARY KEY,
                    product_id VARCHAR(36) NOT NULL,
                    buyer_id VARCHAR(255) NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    unit_price NUMERIC(12, 2) NOT NULL,
                    total_amount NUMERIC(12, 2) NOT NULL,
                    phone_number VARCHAR(32) NOT NULL,
                    order_note VARCHAR(300),
                    status VARCHAR(32) NOT NULL
                        DEFAULT 'pending_payment',
                    merchant_request_id VARCHAR(255),
                    checkout_request_id VARCHAR(255) UNIQUE,
                    mpesa_receipt VARCHAR(128),
                    failure_reason VARCHAR(500),
                    created_at TIMESTAMP NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP NULL
                )
                """
            )
        )

        db.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS
                idx_{MERCH_ORDER_TABLE}_product
                ON {MERCH_ORDER_TABLE}(product_id)
                """
            )
        )

        db.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS
                idx_{MERCH_ORDER_TABLE}_buyer
                ON {MERCH_ORDER_TABLE}(buyer_id)
                """
            )
        )

        db.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS
                idx_{MERCH_ORDER_TABLE}_status
                ON {MERCH_ORDER_TABLE}(status)
                """
            )
        )

        db.commit()

        _SCHEMA_READY = True


def ensure_merch_schema(
    db: Session,
    orders: bool = False,
) -> None:
    if orders:
        ensure_merch_orders_table(db)
    else:
        ensure_merch_table(db)


# ======================================================================
# TEMPLATE CONTEXT
# ======================================================================

def _ctx(
    request: Request,
    current_user: Optional[User] = None,
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
        {"slug": slug},
    ).first():

        suffix_text = f"-{suffix}"

        slug = (
            f"{base[:220 - len(suffix_text)]}"
            f"{suffix_text}"
        )

        suffix += 1

    return slug


# ======================================================================
# LOCAL STORAGE
# ======================================================================

def _local_media_root() -> Path:
    configured = getattr(
        settings,
        "MEDIA_ROOT",
        None,
    )

    if configured:
        return Path(
            str(configured)
        ).expanduser().resolve()

    return (
        Path(__file__)
        .resolve()
        .parents[2]
        / "media"
    )


def _safe_local_path(
    stored_path: str,
) -> Optional[Path]:
    if not stored_path:
        return None

    value = (
        str(stored_path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if value.startswith(
        "media/"
    ):
        value = value[6:]

    if value.startswith(
        "static/"
    ):
        static_root = (
            Path("static")
            .resolve()
        )

        candidate = (
            Path(value)
            .resolve()
        )

        try:
            candidate.relative_to(
                static_root
            )
        except ValueError:
            return None

        return candidate

    media_root = (
        _local_media_root()
        .resolve()
    )

    candidate = (
        media_root / value
    ).resolve()

    try:
        candidate.relative_to(
            media_root
        )
    except ValueError:
        return None

    return candidate


def _local_image_url(
    path: Optional[str],
) -> Optional[str]:
    if not path:
        return None

    clean = (
        str(path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        return f"/{clean}"

    if clean.startswith(
        "static/"
    ):
        return f"/{clean}"

    return f"/media/{clean}"


# ======================================================================
# IMAGE URL
# ======================================================================

def _is_r2_path(
    path: Optional[str],
) -> bool:
    return bool(
        path
        and str(path)
        .strip()
        .lower()
        .startswith("r2://")
    )


def _image_url(
    path: Optional[str],
) -> Optional[str]:
    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith(
        "http://"
    ) or value.startswith(
        "https://"
    ):
        return value

    if _is_r2_path(value):
        try:
            url = r2_presigned_url(
                value,
                expires=3600,
            )

            if url:
                return url

        except Exception:
            pass

        return None

    local_path = _safe_local_path(
        value
    )

    if local_path is not None:
        try:
            if (
                local_path.exists()
                and local_path.is_file()
            ):
                return _local_image_url(
                    value
                )
        except Exception:
            pass

    clean = (
        value
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        return f"/{clean}"

    if clean.startswith(
        "static/"
    ):
        return f"/{clean}"

    return media_url(
        clean
    )


# ======================================================================
# R2 MIGRATION
# ======================================================================

def _migrate_local_image_to_r2(
    db: Session,
    product_id: str,
    image_path: Optional[str],
) -> Optional[str]:
    if not image_path:
        return None

    value = str(
        image_path
    ).strip()

    if not value:
        return None

    if _is_r2_path(value):
        return value

    try:
        if not _r2_is_configured():
            return value
    except Exception:
        return value

    local_path = _safe_local_path(
        value
    )

    if local_path is None:
        return value

    try:
        if (
            not local_path.exists()
            or not local_path.is_file()
        ):
            return value

        contents = (
            local_path.read_bytes()
        )

    except Exception:
        return value

    if not contents:
        return value

    extension = (
        local_path.suffix
        .lower()
    )

    if extension not in ALLOWED_IMAGE_EXT:
        return value

    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }

    try:
        bucket = _r2_bucket()

        if not bucket:
            return value

        key = (
            f"merch/"
            f"{uuid4().hex}"
            f"{extension}"
        )

        client = _r2_client()

        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=contents,
            ContentType=content_types.get(
                extension,
                "application/octet-stream",
            ),
        )

        r2_path = (
            f"r2://{bucket}/{key}"
        )

        db.execute(
            text(
                f"""
                UPDATE {MERCH_TABLE}
                SET image_path = :image_path
                WHERE id = :id
                """
            ),
            {
                "image_path": r2_path,
                "id": str(product_id),
            },
        )

        db.commit()

        return r2_path

    except Exception:
        db.rollback()
        return value


# ======================================================================
# PRODUCT QUERIES
# ======================================================================

def _load_merch_product(
    db: Session,
    slug: str,
):
    return db.execute(
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
            "slug": str(slug).strip()
        },
    ).mappings().first()


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

        item["image_url"] = _image_url(
            item.get("image_path")
        )

        products.append(item)

    return products


def _all_public_rows(
    db: Session,
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
            ORDER BY created_at DESC
            LIMIT 120
            """
        )
    ).mappings().all()

    profile_ids = {
        str(row["creator_profile_id"])
        for row in rows
    }

    profiles = {}

    if profile_ids:
        profile_rows = (
            db.query(Profile)
            .filter(
                Profile.id.in_(
                    list(profile_ids)
                )
            )
            .all()
        )

        for profile in profile_rows:
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
            or "BeatHub Creator"
        )

        item["image_url"] = _image_url(
            item.get("image_path")
        )

        products.append(item)

    return products


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
    ensure_merch_schema(
        db,
        orders=True,
    )

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

    products = _rows_for_creator(
        db,
        str(profile.id),
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
        ),
    )


# ======================================================================
# NEW MERCH
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
# CREATE MERCH
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
    ensure_merch_schema(
        db,
        orders=True,
    )

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
        image is None
        or not image.filename
    ):
        return error(
            "A product image is required."
        )

    extension = Path(
        image.filename
    ).suffix.lower()

    if extension not in ALLOWED_IMAGE_EXT:
        return error(
            "Unsupported image type. "
            "Use JPG, JPEG, PNG or WEBP."
        )

    try:
        try:
            use_r2 = _r2_is_configured()
        except Exception:
            use_r2 = False

        if use_r2:
            image_path = await save_upload_to_r2(
                image,
                "merch",
                ALLOWED_IMAGE_EXT,
            )
        else:
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
# LEGACY LOCAL MERCH IMAGE
# ======================================================================

@router.get(
    "/media/merch/{filename:path}"
)
def merch_local_media(
    filename: str,
):
    clean = (
        filename or ""
    ).replace(
        "\\",
        "/",
    ).lstrip("/")

    if (
        not clean
        or ".." in Path(clean).parts
    ):
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    media_root = (
        _local_media_root()
        .resolve()
    )

    file_path = (
        media_root
        / "merch"
        / clean
    ).resolve()

    try:
        file_path.relative_to(
            media_root
        )
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    if (
        not file_path.exists()
        or not file_path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    return FileResponse(
        path=str(file_path)
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
    ensure_merch_schema(
        db,
        orders=True,
    )

    products = _all_public_rows(
        db
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
            product_detail=False,
        ),
    )


# ======================================================================
# CREATOR MERCH STORE
# ======================================================================

@router.get(
    "/store/{slug}/merch"
)
@router.get(
    "/creator/{slug}/merch"
)
def creator_merch_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    ensure_merch_schema(
        db,
        orders=True,
    )

    clean_slug = (
        slug or ""
    ).strip().lower()

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

    products = _rows_for_creator(
        db,
        str(profile.id),
    )

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=products,
            title=(
                f"{profile.stage_name}"
                f" — Merch"
            ),
            creator=profile,
            store_url=(
                f"/store/{profile.slug}"
            ),
            product_detail=False,
        ),
    )


# ======================================================================
# MERCH PURCHASE HELPERS
# ======================================================================

def _merch_public_base_url(
    request: Request,
) -> str:
    configured = str(
        os.getenv(
            "APP_BASE_URL",
            "",
        )
    ).strip().rstrip("/")

    if configured:
        return configured

    forwarded_proto = (
        request.headers.get(
            "x-forwarded-proto"
        )
        or ""
    ).split(",")[0].strip()

    forwarded_host = (
        request.headers.get(
            "x-forwarded-host"
        )
        or ""
    ).split(",")[0].strip()

    if forwarded_proto and forwarded_host:
        return (
            f"{forwarded_proto}"
            f"://"
            f"{forwarded_host}"
        )

    return str(
        request.base_url
    ).rstrip("/")


def _merch_callback_url(
    request: Request,
) -> str:
    return (
        _merch_public_base_url(
            request
        )
        + "/mpesa/merchandise/callback"
    )


def _normalize_merch_quantity(
    value: str,
) -> int:
    try:
        quantity = int(
            str(
                value or "1"
            ).strip()
        )
    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "Quantity must be a whole number."
        )

    if (
        quantity < 1
        or quantity > MERCH_MAX_QUANTITY
    ):
        raise ValueError(
            f"Quantity must be between "
            f"1 and {MERCH_MAX_QUANTITY}."
        )

    return quantity


# ======================================================================
# MERCH BUY
# ======================================================================

@router.post(
    "/merch/{slug}/buy"
)
async def buy_merchandise(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    phone: str = Form(...),
    quantity: str = Form("1"),
    order_note: str = Form(""),
):
    ensure_merch_schema(
        db,
        orders=True,
    )

    row = _load_merch_product(
        db,
        slug,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Merchandise item not found.",
        )

    creator_profile = (
        db.query(Profile)
        .filter(
            Profile.id
            == str(
                row[
                    "creator_profile_id"
                ]
            )
        )
        .first()
    )

    if (
        creator_profile
        and str(
            creator_profile.user_id
        )
        == str(user.id)
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "You cannot purchase "
                "your own merchandise."
            ),
        )

    try:
        quantity_value = (
            _normalize_merch_quantity(
                quantity
            )
        )
    except ValueError as exc:
        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error={quote(str(exc))}"
            ),
            status_code=303,
        )

    note = (
        order_note or ""
    ).strip()

    if len(note) > MERCH_ORDER_NOTE_MAX:
        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error="
                f"{quote('Order note is too long. Keep it under ' + str(MERCH_ORDER_NOTE_MAX) + ' characters.')}"
            ),
            status_code=303,
        )

    try:
        normalized_phone = (
            mpesa.normalize_phone(
                phone
            )
        )
    except Exception as exc:
        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error={quote(str(exc))}"
            ),
            status_code=303,
        )

    try:
        unit_price = Decimal(
            str(
                row["price"]
            )
        )

        total_amount = (
            unit_price
            * Decimal(
                quantity_value
            )
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail=(
                "Merchandise price "
                "could not be calculated."
            ),
        )

    if total_amount <= Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Merchandise price "
                "must be greater than zero."
            ),
        )

    order_id = str(
        uuid4()
    )

    try:
        db.execute(
            text(
                f"""
                INSERT INTO {MERCH_ORDER_TABLE} (
                    id,
                    product_id,
                    buyer_id,
                    quantity,
                    unit_price,
                    total_amount,
                    phone_number,
                    order_note,
                    status
                )
                VALUES (
                    :id,
                    :product_id,
                    :buyer_id,
                    :quantity,
                    :unit_price,
                    :total_amount,
                    :phone_number,
                    :order_note,
                    'pending_payment'
                )
                """
            ),
            {
                "id": order_id,
                "product_id": str(
                    row["id"]
                ),
                "buyer_id": str(
                    user.id
                ),
                "quantity": quantity_value,
                "unit_price": unit_price,
                "total_amount": total_amount,
                "phone_number": normalized_phone,
                "order_note": (
                    note or None
                ),
            },
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    try:
        stk_response = mpesa.stk_push(
            normalized_phone,
            int(total_amount),
            f"M{order_id.replace('-', '')[:10]}",
            f"BeatHub {str(row['name'])[:10]}",
            callback_url=_merch_callback_url(
                request
            ),
        )

    except Exception as exc:
        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'failed',
                    failure_reason = :reason
                WHERE id = :id
                  AND status = 'pending_payment'
                """
            ),
            {
                "reason": str(exc)[:500],
                "id": order_id,
            },
        )

        db.commit()

        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error="
                f"{quote('M-Pesa could not be started. Please check the phone number and try again.')}"
            ),
            status_code=303,
        )

    if not isinstance(
        stk_response,
        dict,
    ):
        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'failed',
                    failure_reason = :reason
                WHERE id = :id
                """
            ),
            {
                "reason": (
                    "M-Pesa returned "
                    "an invalid response."
                ),
                "id": order_id,
            },
        )

        db.commit()

        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error="
                f"{quote('M-Pesa returned an invalid response.')}"
            ),
            status_code=303,
        )

    checkout_request_id = (
        stk_response.get(
            "checkout_request_id"
        )
    )

    merchant_request_id = (
        stk_response.get(
            "merchant_request_id"
        )
    )

    if not checkout_request_id:
        error_message = (
            stk_response.get(
                "errorMessage"
            )
            or stk_response.get(
                "customer_message"
            )
            or (
                "M-Pesa did not provide "
                "a CheckoutRequestID."
            )
        )

        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'failed',
                    failure_reason = :reason,
                    merchant_request_id =
                        :merchant_request_id
                WHERE id = :id
                """
            ),
            {
                "reason": str(
                    error_message
                )[:500],
                "merchant_request_id":
                    merchant_request_id,
                "id": order_id,
            },
        )

        db.commit()

        return RedirectResponse(
            url=(
                f"/merch/{slug}"
                f"?error="
                f"{quote(str(error_message))}"
            ),
            status_code=303,
        )

    db.execute(
        text(
            f"""
            UPDATE {MERCH_ORDER_TABLE}
            SET
                checkout_request_id =
                    :checkout_request_id,
                merchant_request_id =
                    :merchant_request_id
            WHERE id = :id
              AND status = 'pending_payment'
            """
        ),
        {
            "checkout_request_id":
                str(
                    checkout_request_id
                ),
            "merchant_request_id":
                merchant_request_id,
            "id": order_id,
        },
    )

    db.commit()

    if stk_response.get(
        "simulated"
    ):
        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'paid',
                    paid_at = CURRENT_TIMESTAMP,
                    mpesa_receipt =
                        COALESCE(
                            mpesa_receipt,
                            :receipt
                        )
                WHERE id = :id
                  AND status = 'pending_payment'
                """
            ),
            {
                "receipt":
                    "MOCK-MERCH-PAYMENT",
                "id": order_id,
            },
        )

        db.commit()

    return RedirectResponse(
        url=f"/merch/orders/{order_id}",
        status_code=303,
    )


# ======================================================================
# MERCH ORDER STATUS
# ======================================================================

@router.get(
    "/merch/orders/{order_id}"
)
def merchandise_order_status(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_merch_schema(
        db,
        orders=True,
    )

    order = db.execute(
        text(
            f"""
            SELECT
                o.id,
                o.product_id,
                o.buyer_id,
                o.quantity,
                o.unit_price,
                o.total_amount,
                o.phone_number,
                o.order_note,
                o.status,
                o.merchant_request_id,
                o.checkout_request_id,
                o.mpesa_receipt,
                o.failure_reason,
                o.created_at,
                o.paid_at,
                m.name AS product_name,
                m.slug AS product_slug,
                m.image_path,
                m.description AS product_description
            FROM {MERCH_ORDER_TABLE} o
            JOIN {MERCH_TABLE} m
                ON m.id = o.product_id
            WHERE o.id = :id
            LIMIT 1
            """
        ),
        {
            "id": str(
                order_id
            )
        },
    ).mappings().first()

    if (
        not order
        or str(
            order["buyer_id"]
        )
        != str(user.id)
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "Merchandise order "
                "not found."
            ),
        )

    item = dict(order)

    item["image_url"] = _image_url(
        item.get(
            "image_path"
        )
    )

    return templates.TemplateResponse(
        request,
        "merchandise_order.html",
        _ctx(
            request,
            user,
            order=item,
            title="Merchandise Order",
        ),
    )


# ======================================================================
# MERCH M-PESA CALLBACK
# ======================================================================

@router.post(
    "/mpesa/merchandise/callback"
)
async def merchandise_mpesa_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    ensure_merch_schema(
        db,
        orders=True,
    )

    try:
        payload = await request.json()
    except Exception:
        return {
            "ResultCode": 1,
            "ResultDesc":
                "Invalid callback payload.",
        }

    stk = (
        payload.get(
            "Body",
            {}
        )
        .get(
            "stkCallback",
            {}
        )
    )

    checkout_request_id = (
        stk.get(
            "CheckoutRequestID"
        )
    )

    if not checkout_request_id:
        return {
            "ResultCode": 1,
            "ResultDesc":
                "Missing CheckoutRequestID.",
        }

    try:
        success = (
            int(
                stk.get(
                    "ResultCode"
                )
            )
            == 0
        )
    except (
        TypeError,
        ValueError,
    ):
        success = False

    result_desc = str(
        stk.get(
            "ResultDesc",
            "M-Pesa transaction result.",
        )
    )

    items = (
        stk.get(
            "CallbackMetadata",
            {}
        )
        .get(
            "Item",
            []
        )
    )

    metadata = {}

    for item in items:
        if (
            isinstance(
                item,
                dict,
            )
            and item.get("Name")
        ):
            metadata[
                item["Name"]
            ] = item.get(
                "Value"
            )

    order = db.execute(
        text(
            f"""
            SELECT
                id,
                status
            FROM {MERCH_ORDER_TABLE}
            WHERE checkout_request_id =
                :checkout_request_id
            LIMIT 1
            """
        ),
        {
            "checkout_request_id":
                checkout_request_id
        },
    ).mappings().first()

    if not order:
        return {
            "ResultCode": 0,
            "ResultDesc":
                "Accepted",
        }

    if order["status"] != (
        "pending_payment"
    ):
        return {
            "ResultCode": 0,
            "ResultDesc":
                "Already processed",
        }

    if success:
        receipt = (
            str(
                metadata.get(
                    "MpesaReceiptNumber"
                )
            )
            if metadata.get(
                "MpesaReceiptNumber"
            ) is not None
            else None
        )

        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'paid',
                    paid_at =
                        CURRENT_TIMESTAMP,
                    mpesa_receipt =
                        :receipt
                WHERE id = :id
                  AND status =
                      'pending_payment'
                """
            ),
            {
                "receipt": receipt,
                "id": order["id"],
            },
        )

    else:
        db.execute(
            text(
                f"""
                UPDATE {MERCH_ORDER_TABLE}
                SET
                    status = 'failed',
                    failure_reason = :reason
                WHERE id = :id
                  AND status =
                      'pending_payment'
                """
            ),
            {
                "reason":
                    result_desc[:500],
                "id": order["id"],
            },
        )

    db.commit()

    return {
        "ResultCode": 0,
        "ResultDesc":
            "Accepted",
    }


# ======================================================================
# INDIVIDUAL MERCH PRODUCT
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
    ensure_merch_schema(
        db,
        orders=True,
    )

    row = _load_merch_product(
        db,
        slug,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Merchandise item "
                "not found."
            ),
        )

    item = dict(row)

    image_path = item.get(
        "image_path"
    )

    if (
        image_path
        and not _is_r2_path(
            image_path
        )
    ):
        migrated = (
            _migrate_local_image_to_r2(
                db,
                str(
                    item["id"]
                ),
                image_path,
            )
        )

        if migrated:
            image_path = migrated
            item["image_path"] = migrated

    item["image_url"] = _image_url(
        image_path
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

    return templates.TemplateResponse(
        request,
        "merchandise_public.html",
        _ctx(
            request,
            current_user,
            products=[item],
            title=item["name"],
            creator=profile,
            store_url=(
                f"/store/{profile.slug}"
                if profile
                else None
            ),
            product_detail=True,
            query_error=(
                request.query_params.get(
                    "error"
                )
                or ""
            ).strip()
            or None,
        ),
    )

