from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
    _r2_bucket,
    _r2_client,
    _r2_is_configured,
    media_url,
    r2_presigned_url,
    save_upload,
    save_upload_to_r2,
)
from app.utils.deps import get_optional_user, require_creator


router = APIRouter(tags=["merchandise"])

templates = Jinja2Templates(directory="app/templates")

MERCH_TABLE = "beathub_merchandise"


def ensure_merch_table(db: Session) -> None:
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
        "success": request.query_params.get("success"),
    }

    context.update(extra)
    return context


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()

    result = []
    previous_dash = False

    for char in value:
        if char.isalnum():
            result.append(char)
            previous_dash = False
        elif not previous_dash:
            result.append("-")
            previous_dash = True

    slug = "".join(result).strip("-")

    if not slug:
        slug = f"merch-{uuid4().hex[:10]}"

    return slug


def _unique_merch_slug(db: Session, name: str) -> str:
    base = _slugify(name)[:180]
    slug = base
    number = 2

    while True:
        exists = db.execute(
            text(
                f"""
                SELECT 1
                FROM {MERCH_TABLE}
                WHERE slug = :slug
                LIMIT 1
                """
            ),
            {"slug": slug},
        ).first()

        if not exists:
            return slug

        suffix = f"-{number}"
        slug = f"{base[:220 - len(suffix)]}{suffix}"
        number += 1


def _local_media_root() -> Path:
    configured = getattr(settings, "MEDIA_ROOT", None)

    if configured:
        return Path(str(configured)).expanduser().resolve()

    return (
        Path(__file__)
        .resolve()
        .parents[2]
        / "media"
    )


def _safe_local_path(stored_path: Optional[str]) -> Optional[Path]:
    if not stored_path:
        return None

    clean = (
        str(stored_path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith("media/"):
        clean = clean[6:]

    media_root = _local_media_root().resolve()

    candidate = (media_root / clean).resolve()

    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None

    return candidate


def _local_image_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None

    clean = (
        str(path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith("media/"):
        return f"/{clean}"

    return f"/media/{clean}"


def _is_r2_path(path: Optional[str]) -> bool:
    if not path:
        return False

    return str(path).strip().lower().startswith("r2://")


def _image_url(
    request: Request,
    path: Optional[str],
) -> Optional[str]:
    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith("http://") or value.startswith("https://"):
        return value

    if _is_r2_path(value):
        try:
            return r2_presigned_url(
                value,
                expires=3600,
            )
        except Exception:
            return None

    local_path = _safe_local_path(value)

    if local_path is not None:
        try:
            if local_path.exists() and local_path.is_file():
                return _local_image_url(value)
        except Exception:
            pass

    return media_url(value)


def _rows_for_creator(
    request: Request,
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
        {"profile_id": str(profile_id)},
    ).mappings().all()

    products = []

    for row in rows:
        item = dict(row)
        item["image_url"] = _image_url(
            request,
            item.get("image_path"),
        )
        products.append(item)

    return products


def _all_public_rows(
    request: Request,
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
            .filter(Profile.id.in_(list(profile_ids)))
            .all()
        )

        for profile in profile_rows:
            profiles[str(profile.id)] = profile

    products = []

    for row in rows:
        item = dict(row)

        owner = profiles.get(
            str(item["creator_profile_id"])
        )

        item["creator_slug"] = getattr(
            owner,
            "slug",
            None,
        )

        item["creator_name"] = (
            getattr(owner, "stage_name", None)
            or "BeatHub Creator"
        )

        item["image_url"] = _image_url(
            request,
            item.get("image_path"),
        )

        products.append(item)

    return products


async def _migrate_local_image_to_r2(
    db: Session,
    product_id: str,
    image_path: Optional[str],
) -> Optional[str]:
    if not image_path:
        return None

    value = str(image_path).strip()

    if not value:
        return None

    if _is_r2_path(value):
        return value

    try:
        if not _r2_is_configured():
            return value
    except Exception:
        return value

    local_path = _safe_local_path(value)

    if local_path is None:
        return value

    try:
        if not local_path.exists() or not local_path.is_file():
            return value

        contents = local_path.read_bytes()
    except Exception:
        return value

    if not contents:
        return value

    extension = local_path.suffix.lower()

    if extension not in ALLOWED_IMAGE_EXT:
        return value

    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }

    content_type = content_types.get(
        extension,
        "application/octet-stream",
    )

    try:
        bucket = _r2_bucket()

        if not bucket:
            return value

        key = f"merch/{uuid4().hex}{extension}"

        client = _r2_client()

        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=contents,
            ContentType=content_type,
        )

        r2_path = f"r2://{bucket}/{key}"

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


@router.get("/dashboard/merch")
def merch_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = getattr(user, "profile", None)

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    ensure_merch_table(db)

    products = _rows_for_creator(
        request,
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


@router.get("/dashboard/merch/new")
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
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

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
        return error(
            "Product name is too long. Keep it under 160 characters."
        )

    if len(description) > 4000:
        return error(
            "Product description is too long. Keep it under 4,000 characters."
        )

    try:
        price_value = Decimal(price_raw)
    except (InvalidOperation, ValueError, TypeError):
        return error("Enter a valid product price.")

    if price_value <= Decimal("0"):
        return error(
            "Product price must be greater than zero."
        )

    if image is None or not image.filename:
        return error("A product image is required.")

    extension = Path(image.filename).suffix.lower()

    if extension not in ALLOWED_IMAGE_EXT:
        return error(
            "Unsupported image type. Use JPG, JPEG, PNG or WEBP."
        )

    try:
        if _r2_is_configured():
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
        return error(str(exc))

    except Exception as exc:
        return error(
            f"Product image upload failed: {exc}"
        )

    product_id = str(uuid4())

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
        url=(
            "/dashboard/merch"
            "?success=Merchandise%20added%20successfully."
        ),
        status_code=303,
    )


@router.get("/media/merch/{filename:path}")
def merch_local_media(filename: str):
    clean = (
        filename or ""
    ).replace("\\", "/").lstrip("/")

    if not clean or ".." in Path(clean).parts:
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    media_root = _local_media_root().resolve()

    file_path = (
        media_root / "merch" / clean
    ).resolve()

    try:
        file_path.relative_to(media_root)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Image not found.",
        )

    return FileResponse(
        path=str(file_path)
    )


@router.get("/merch")
def merch_marketplace(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    ensure_merch_table(db)

    products = _all_public_rows(
        request,
        db,
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
        raise HTTPException(
            status_code=404,
            detail="Creator store not found.",
        )

    ensure_merch_table(db)

    products = _rows_for_creator(
        request,
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
            title=f"{profile.stage_name} — Merch",
            creator=profile,
            store_url=f"/store/{profile.slug}",
            product_detail=False,
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
        raise HTTPException(
            status_code=404,
            detail="Merchandise item not found.",
        )

    item = dict(row)

    image_path = item.get("image_path")

    if image_path and not _is_r2_path(image_path):
        migrated_path = await_migrate_image(
            db,
            str(item["id"]),
            image_path,
        )

        if migrated_path:
            image_path = migrated_path
            item["image_path"] = migrated_path

    item["image_url"] = _image_url(
        request,
        image_path,
    )

    profile = (
        db.query(Profile)
        .filter(
            Profile.id
            == str(item["creator_profile_id"])
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
        ),
    )


async def await_migrate_image(
    db: Session,
    product_id: str,
    image_path: Optional[str],
) -> Optional[str]:
    return await _migrate_local_image_to_r2(
        db,
        product_id,
        image_path,
    )
