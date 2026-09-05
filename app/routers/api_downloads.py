from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.order import License, Order, OrderStatus
from app.models.user import User
from app.services.storage import r2_presigned_url, storage_exists
from app.utils.deps import require_user

router = APIRouter(prefix="/api/v1", tags=["mobile-api-downloads"])


def _licensed_order(db: Session, order_id: str, user: User) -> Order:
    order = db.get(Order, order_id)
    if not order or str(order.buyer_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Purchase not found.")
    if order.status != OrderStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="This purchase is not completed yet.")
    license_row = db.query(License).filter(License.order_id == order.id, License.buyer_id == user.id).first()
    if not license_row:
        raise HTTPException(status_code=403, detail="This purchase does not have an active license.")
    if not order.track or not order.track.audio_file_path:
        raise HTTPException(status_code=404, detail="The purchased audio file is unavailable.")
    return order


@router.get("/orders/{order_id}/download")
def download_purchase(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = _licensed_order(db, order_id, user)
    path = str(order.track.audio_file_path).strip()

    if path.startswith(("r2://", "https://", "http://")):
        if path.startswith("r2://"):
            url = r2_presigned_url(path, expires=300)
            if not url:
                raise HTTPException(status_code=503, detail="The download could not be prepared.")
        else:
            url = path
        return RedirectResponse(url=url, status_code=307)

    if not storage_exists(path):
        raise HTTPException(status_code=404, detail="The purchased audio file is unavailable.")

    clean = path.replace("\\", "/").lstrip("/")
    if clean.startswith("media/"):
        clean = clean[6:]
    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (media_root / clean).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="The purchased audio file is unavailable.")
    return FileResponse(candidate, media_type="application/octet-stream", filename=f"{order.track.slug or 'beathub-track'}.mp3")
