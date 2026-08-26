"""Canonical BeatHub merchandise routes using Paystack."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.merchandise_payments import complete_merchandise_payment
from app.services.pricing import calculate_split
from app.services.storage import (
    ALLOWED_IMAGE_EXT, UploadValidationError, _parse_r2_path, _r2_client,
    _r2_is_configured, media_url, save_upload, save_upload_to_r2,
)
from app.utils.deps import get_optional_user, require_creator, require_user

router = APIRouter(tags=["merchandise"])
templates = Jinja2Templates(directory="app/templates")
MERCH_TABLE = "beathub_merchandise"
MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MAX_QUANTITY = 20
MAX_NOTE = 300
PAYSTACK_MINIMUM = Decimal("3.00")


def _ctx(request: Request, user: Optional[User] = None, **extra):
    data = {"request": request, "current_user": user, "user": user,
            "current_year": datetime.utcnow().year, "error": None, "success": None}
    data.update(extra)
    return data


def _ensure_column(db: Session, table: str, name: str, definition: str) -> None:
    columns = {column["name"] for column in inspect(db.bind).get_columns(table)}
    if name not in columns:
        db.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def ensure_merch_tables(db: Session) -> None:
    """Legacy compatibility hook.

    Merchandise schema is managed by Alembic migrations 0010-0012.
    Never run DDL, schema inspection, or commits during a customer request.
    """
    return None


def _slugify(value: str) -> str:
    chars, dash = [], False
    for char in (value or "").strip().lower():
        if char.isalnum():
            chars.append(char); dash = False
        elif not dash:
            chars.append("-"); dash = True
    return "".join(chars).strip("-")[:180] or "merch"


def _unique_slug(db: Session, name: str) -> str:
    base, slug, counter = _slugify(name), _slugify(name), 2
    while db.execute(text(f"SELECT 1 FROM {MERCH_TABLE} WHERE slug=:slug LIMIT 1"), {"slug": slug}).first():
        slug = f"{base}-{counter}"; counter += 1
    return slug


def _image_url(request: Request | None, path: str | None) -> str | None:
    if not path:
        return None
    try:
        return media_url(path)
    except Exception:
        return None


def _safe_local_path(value: str) -> Path | None:
    clean = str(value or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        return None
    root = Path(getattr(settings, "MEDIA_ROOT", "media") or "media").expanduser()
    if not root.is_absolute(): root = Path.cwd() / root
    root = root.resolve()
    candidate = (root / clean[6:] if clean.startswith("media/") else root / clean).resolve()
    try: candidate.relative_to(root)
    except ValueError: return None
    return candidate


def _delete_storage(path: str | None) -> None:
    value = str(path or "").strip()
    try:
        if value.startswith(("r2://", "s3://")):
            bucket, key = _parse_r2_path(value.replace("s3://", "r2://", 1))
            if bucket and key: _r2_client().delete_object(Bucket=bucket, Key=key)
            return
        local = _safe_local_path(value)
        if local and local.is_file(): local.unlink(missing_ok=True)
    except Exception:
        pass


def _product(db: Session, slug: str):
    return db.execute(text(f"""
        SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at
        FROM {MERCH_TABLE} WHERE slug=:slug LIMIT 1
    """), {"slug": slug}).mappings().first()


def _products(db: Session, creator_profile_id: str | None = None):
    if creator_profile_id:
        rows = db.execute(text(f"SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at FROM {MERCH_TABLE} WHERE creator_profile_id=:p ORDER BY created_at DESC"), {"p": str(creator_profile_id)}).mappings().all()
    else:
        rows = db.execute(text(f"SELECT id, creator_profile_id, name, slug, description, price, image_path, created_at FROM {MERCH_TABLE} ORDER BY created_at DESC LIMIT 120")).mappings().all()
    ids = {str(r["creator_profile_id"]) for r in rows}
    profiles = {str(p.id): p for p in db.query(Profile).filter(Profile.id.in_(list(ids))).all()} if ids else {}
    result = []
    for row in rows:
        item = dict(row); owner = profiles.get(str(item["creator_profile_id"]))
        item["creator"] = owner
        item["creator_name"] = getattr(owner, "stage_name", None) or "BeatHub Creator"
        item["creator_slug"] = getattr(owner, "slug", None)
        item["image_url"] = _image_url(None, item.get("image_path")); result.append(item)
    return result


@router.get("/dashboard/merch")
def merch_dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ensure_merch_tables(db); profile = getattr(user, "profile", None)
    if profile is None: raise HTTPException(status_code=400, detail="Creator profile missing.")
    products = _products(db, str(profile.id))
    return templates.TemplateResponse(request, "merchandise.html", _ctx(request, user, profile=profile, products=products, product_count=len(products), success=request.query_params.get("success"), error=request.query_params.get("error")))


@router.get("/dashboard/merch/new")
def merch_new(request: Request, user: User = Depends(require_creator)):
    return templates.TemplateResponse(request, "merchandise_new.html", _ctx(request, user))


@router.post("/dashboard/merch/new")
async def merch_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator), name: str = Form(...), description: str = Form(""), price: str = Form(...), image: UploadFile = File(...)):
    ensure_merch_tables(db); profile = getattr(user, "profile", None)
    if profile is None: raise HTTPException(status_code=400, detail="Creator profile missing.")
    name, description, price_raw = (name or "").strip(), (description or "").strip(), (price or "").strip()
    def error(message: str):
        return templates.TemplateResponse(request, "merchandise_new.html", _ctx(request, user, error=message, name=name, description=description, price=price_raw), status_code=400)
    if not name: return error("Product name is required.")
    if len(name) > 160: return error("Product name is too long.")
    if len(description) > 4000: return error("Product description is too long.")
    try: price_value = Decimal(price_raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError): return error("Enter a valid product price.")
    if price_value <= 0: return error("Product price must be greater than zero.")
    extension = Path(image.filename or "").suffix.lower()
    if extension not in ALLOWED_IMAGE_EXT: return error("Unsupported image type. Use JPG, JPEG, PNG or WEBP.")
    try:
        image_path = await save_upload_to_r2(image, "merch", ALLOWED_IMAGE_EXT) if _r2_is_configured() else await save_upload(image, "merch", ALLOWED_IMAGE_EXT)
    except UploadValidationError as exc: return error(str(exc))
    except Exception: return error("Product image upload failed. Please try again.")
    product_id, slug = str(uuid4()), _unique_slug(db, name)
    try:
        db.execute(text(f"INSERT INTO {MERCH_TABLE}(id,creator_profile_id,name,slug,description,price,image_path) VALUES(:id,:profile,:name,:slug,:description,:price,:image)"), {"id":product_id,"profile":str(profile.id),"name":name,"slug":slug,"description":description or None,"price":price_value,"image":image_path})
        db.commit()
    except Exception:
        db.rollback(); _delete_storage(image_path); raise
    return RedirectResponse("/dashboard/merch?success=Merchandise%20added%20successfully.", 303)


@router.post("/dashboard/merch/{product_id}/delete")
def merch_delete(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ensure_merch_tables(db); profile = getattr(user, "profile", None)
    if profile is None: raise HTTPException(status_code=400, detail="Creator profile missing.")
    row = db.execute(text(f"SELECT id,image_path FROM {MERCH_TABLE} WHERE id=:id AND creator_profile_id=:profile LIMIT 1"), {"id":str(product_id),"profile":str(profile.id)}).mappings().first()
    if not row: raise HTTPException(status_code=404, detail="Merchandise item not found.")
    if db.execute(text(f"SELECT 1 FROM {MERCH_ORDER_TABLE} WHERE product_id=:id LIMIT 1"), {"id":str(product_id)}).first():
        return RedirectResponse("/dashboard/merch?error=This%20merchandise%20item%20already%20has%20an%20order.", 303)
    db.execute(text(f"DELETE FROM {MERCH_TABLE} WHERE id=:id AND creator_profile_id=:profile"), {"id":str(product_id),"profile":str(profile.id)}); db.commit(); _delete_storage(row["image_path"])
    return RedirectResponse("/dashboard/merch?success=Merchandise%20deleted%20successfully.", 303)


@router.get("/media/merch/{filename:path}")
def legacy_merch_media(filename: str):
    clean = str(filename or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts: raise HTTPException(status_code=404, detail="Image not found.")
    root = Path(getattr(settings, "MEDIA_ROOT", "media") or "media").resolve(); path = (root / "merch" / clean).resolve()
    try: path.relative_to(root)
    except ValueError: raise HTTPException(status_code=404, detail="Image not found.")
    if not path.is_file(): raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(str(path))


@router.get("/merch")
def merch_marketplace(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_optional_user)):
    ensure_merch_tables(db); products = _products(db)
    return templates.TemplateResponse(request, "merchandise_public.html", _ctx(request, user, products=products, title="BeatHub Merch", creator=None, store_url=None, product_detail=False, query_error=request.query_params.get("error")))


@router.get("/store/{slug}/merch")
def creator_merch_store(request: Request, slug: str, db: Session = Depends(get_db), user: Optional[User] = Depends(get_optional_user)):
    ensure_merch_tables(db); profile = db.query(Profile).filter(Profile.slug == slug).first()
    if profile is None: raise HTTPException(status_code=404, detail="Creator store not found.")
    return templates.TemplateResponse(request, "merchandise_public.html", _ctx(request, user, products=_products(db, str(profile.id)), title=f"{profile.stage_name} — Merch", creator=profile, store_url=f"/store/{profile.slug}", product_detail=False, query_error=request.query_params.get("error")))


@router.get("/merch/{slug}")
def merch_detail(request: Request, slug: str, db: Session = Depends(get_db), user: Optional[User] = Depends(get_optional_user)):
    ensure_merch_tables(db); row = _product(db, slug)
    if row is None: raise HTTPException(status_code=404, detail="Merchandise item not found.")
    product = dict(row); owner = db.query(Profile).filter(Profile.id == str(product["creator_profile_id"])).first(); product["image_url"] = _image_url(request, product.get("image_path")); product["creator"] = owner
    return templates.TemplateResponse(request, "merchandise_public.html", _ctx(request, user, products=[product], title=product["name"], creator=owner, store_url=f"/store/{owner.slug}" if owner else None, product_detail=True, query_error=request.query_params.get("error")))


def _paystack_headers() -> dict:
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}


def _paystack_amount(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _base_url(request: Request) -> str:
    configured = str(getattr(settings, "BASE_URL", "") or os.getenv("APP_BASE_URL", "")).strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def _quantity(value: str) -> int:
    try: quantity = int(str(value or "1").strip())
    except (TypeError, ValueError): raise ValueError("Quantity must be a whole number.")
    if quantity < 1 or quantity > MAX_QUANTITY: raise ValueError(f"Quantity must be between 1 and {MAX_QUANTITY}.")
    return quantity


@router.post("/merch/{slug}/buy")
async def buy_merchandise(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user), phone: str = Form(""), quantity: str = Form("1"), order_note: str = Form("")):
    ensure_merch_tables(db); row = _product(db, slug)
    if row is None: raise HTTPException(status_code=404, detail="Merchandise item not found.")
    owner = db.query(Profile).filter(Profile.id == str(row["creator_profile_id"])).first()
    if owner and str(owner.user_id) == str(user.id): raise HTTPException(status_code=403, detail="You cannot purchase your own merchandise.")
    try: qty = _quantity(quantity)
    except ValueError as exc: return RedirectResponse(f"/merch/{slug}?error={quote(str(exc))}", 303)
    note = (order_note or "").strip()
    if len(note) > MAX_NOTE: return RedirectResponse(f"/merch/{slug}?error={quote('Order note is too long.')}", 303)
    unit_price = Decimal(str(row["price"])).quantize(Decimal("0.01")); total = (unit_price * Decimal(qty)).quantize(Decimal("0.01")); split = calculate_split(total)
    if total < PAYSTACK_MINIMUM: return RedirectResponse(f"/merch/{slug}?error={quote('Paystack requires a minimum payment of KSh 3.00.')}", 303)
    if not settings.PAYSTACK_SECRET_KEY: return RedirectResponse(f"/merch/{slug}?error={quote('Paystack is not configured yet.')}", 303)
    order_id, order_number = str(uuid4()), f"BM{uuid4().hex[:10].upper()}"
    db.execute(text(f"INSERT INTO {MERCH_ORDER_TABLE}(id,product_id,buyer_id,quantity,unit_price,total_amount,phone_number,order_note,status,commission_amount,net_amount,commission_percent_at_purchase,payment_provider) VALUES(:id,:product,:buyer,:qty,:unit,:total,:phone,:note,'pending_payment',:commission,:net,:percent,'paystack')"), {"id":order_id,"product":str(row["id"]),"buyer":str(user.id),"qty":qty,"unit":unit_price,"total":total,"phone":(phone or "").strip()[:32] or None,"note":note or None,"commission":split["commission_amount"],"net":split["net_amount"],"percent":split["commission_percent"]}); db.commit()
    payload = {"email":(user.email or "").strip().lower(),"amount":str(_paystack_amount(total)),"currency":"KES","reference":order_number,"callback_url":f"{_base_url(request)}/paystack/callback","channels":["card","mobile_money"],"metadata":{"beathub_merchandise_order_id":order_id,"beathub_merchandise_product_id":str(row["id"]),"beathub_merchandise_order_number":order_number,"beathub_commission_percent":str(split["commission_percent"]),"beathub_commission_amount":str(split["commission_amount"]),"beathub_producer_amount":str(split["net_amount"]),"buyer_id":str(user.id),"customer_phone":(phone or "").strip()[:32]}}
    if owner and getattr(owner,"paystack_subaccount_code",None): payload["subaccount"] = str(owner.paystack_subaccount_code); payload["transaction_charge"] = _paystack_amount(split["commission_amount"])
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0,connect=5.0)) as client: response = await client.post(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize",headers=_paystack_headers(),json=payload)
        data = response.json()
    except Exception:
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed',failure_reason=:reason WHERE id=:id"), {"reason":"Paystack could not be reached.","id":order_id}); db.commit(); return RedirectResponse(f"/merch/{slug}?error={quote('Paystack could not be reached. Please try again.')}",303)
    if response.status_code >= 400 or not data.get("status"):
        message=str(data.get("message") or "Paystack could not initialize checkout.")[:500]; db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed',failure_reason=:reason WHERE id=:id"),{"reason":message,"id":order_id}); db.commit(); return RedirectResponse(f"/merch/{slug}?error={quote(message)}",303)
    checkout=data.get("data") or {}; authorization_url=checkout.get("authorization_url"); reference=checkout.get("reference") or order_number
    if not authorization_url: db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed',failure_reason=:reason WHERE id=:id"),{"reason":"Paystack did not return a checkout URL.","id":order_id}); db.commit(); return RedirectResponse(f"/merch/{slug}?error={quote('Paystack did not return a checkout URL.')}",303)
    db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET checkout_request_id=:reference WHERE id=:id AND status='pending_payment'"),{"reference":str(reference),"id":order_id}); db.commit()
    return RedirectResponse(authorization_url,303)


@router.get("/merch/orders/{order_id}")
def merchandise_order_status(order_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    ensure_merch_tables(db); row=db.execute(text(f"SELECT o.*,m.name AS product_name,m.slug AS product_slug,m.image_path,m.description AS product_description FROM {MERCH_ORDER_TABLE} o JOIN {MERCH_TABLE} m ON m.id=o.product_id WHERE o.id=:id LIMIT 1"),{"id":str(order_id)}).mappings().first()
    if not row or str(row["buyer_id"]) != str(user.id): raise HTTPException(status_code=404,detail="Merchandise order not found.")
    order=dict(row); order["image_url"]=_image_url(request,order.get("image_path"))
    return templates.TemplateResponse(request,"merchandise_order.html",_ctx(request,user,order=order,title="Merchandise Order"))


@router.get("/api/merch/orders/{order_id}/status")
async def merchandise_order_status_api(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    ensure_merch_tables(db); row=db.execute(text(f"SELECT * FROM {MERCH_ORDER_TABLE} WHERE id=:id LIMIT 1"),{"id":str(order_id)}).mappings().first()
    if not row or str(row["buyer_id"]) != str(user.id): raise HTTPException(status_code=404,detail="Merchandise order not found.")
    status=str(row["status"] or "pending_payment")
    if status == "pending_payment" and row.get("checkout_request_id") and settings.PAYSTACK_SECRET_KEY:
        reference=str(row["checkout_request_id"])
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(6.0,connect=3.0)) as client: response=await client.get(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/verify/{reference}",headers=_paystack_headers())
            if response.status_code < 400:
                verified=response.json().get("data") or {}
                if str(verified.get("status") or "").lower() == "success":
                    complete_merchandise_payment(db,order_id,reference,verified); status="paid"
        except Exception: pass
    return {"status":status,"paid":status=="paid","failed":status=="failed"}


@router.post("/paystack/merchandise/callback")
async def legacy_paystack_merchandise_callback(request: Request, db: Session = Depends(get_db)):
    reference=request.query_params.get("reference") or request.query_params.get("trxref")
    if not reference: return RedirectResponse("/merch?error=Payment%20reference%20was%20missing.",303)
    return RedirectResponse(f"/paystack/callback?reference={quote(reference)}",303)
