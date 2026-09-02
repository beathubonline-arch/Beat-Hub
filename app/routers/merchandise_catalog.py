from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.merchandise import _products
from app.utils.deps import get_optional_user

router = APIRouter(tags=["merchandise-catalog"])
templates = Jinja2Templates(directory="app/templates")


def _group_products(products: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[str, dict] = {}
    for product in products:
        creator_id = str(product.get("creator_profile_id") or product.get("creator_slug") or product.get("creator_name") or "")
        if not creator_id:
            creator_id = "unknown"
        group = groups.setdefault(creator_id, {
            "creator_name": product.get("creator_name") or "BeatHub Creator",
            "creator_slug": product.get("creator_slug"),
            "products": [],
        })
        group["products"].append(product)

    collections = []
    standalone = []
    for group in groups.values():
        items = sorted(group["products"], key=lambda item: item.get("created_at") or "", reverse=True)
        if len(items) >= 2:
            collections.append({
                "creator_name": group["creator_name"],
                "creator_slug": group["creator_slug"],
                "products": items,
                "item_count": len(items),
            })
        else:
            standalone.extend(items)

    collections.sort(key=lambda item: item["products"][0].get("created_at") or "", reverse=True)
    standalone.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return collections[:60], standalone[:120]


@router.get("/merch")
def merch_catalog(request: Request, db: Session = Depends(get_db), user: Optional[object] = Depends(get_optional_user)):
    products = _products(db)
    collections, standalone_products = _group_products(products)
    return templates.TemplateResponse(request, "merchandise_catalog.html", {
        "request": request,
        "current_user": user,
        "user": user,
        "current_year": 2026,
        "total_products": len(products),
        "collection_count": len(collections),
        "collections": collections,
        "standalone_products": standalone_products,
    })
