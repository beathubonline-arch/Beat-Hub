from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import ProductCurrency, SalesModel, Track, TrackContentType
from app.models.user import User
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    _max_upload_bytes,
    _parse_r2_path,
    _r2_bucket,
    _r2_is_configured,
    r2_object_head,
    r2_presigned_upload,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["creator-upload"])

_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}
_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class SignUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=120)
    kind: str = Field(default="audio", pattern="^(audio|covers)$")


class TrackUploadItem(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    genre: str | None = Field(default=None, max_length=100)
    bpm: str | None = Field(default=None, max_length=10)
    tags: str | None = Field(default=None, max_length=1000)
    price: str = Field(default="0", max_length=30)
    currency: str = Field(default="KES", max_length=3)
    sales_model: str = Field(default="non_exclusive", max_length=30)
    content_type: str = Field(default="beat", max_length=20)
    audio_r2_path: str = Field(min_length=1, max_length=1000)
    cover_r2_path: str | None = Field(default=None, max_length=1000)


class TrackUploadRequest(BaseModel):
    items: list[TrackUploadItem] = Field(min_length=1, max_length=20)


def _mime_for(filename: str, kind: str) -> tuple[str, str]:
    ext = os.path.splitext(filename or "")[1].lower()
    allowed = ALLOWED_AUDIO_EXT if kind == "audio" else ALLOWED_IMAGE_EXT
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported {kind} file type '{ext}'.")
    expected = (_AUDIO_TYPES if kind == "audio" else _IMAGE_TYPES)[ext]
    return ext, expected


def _verify_owned_r2_path(path: str, profile_id: str, kind: str) -> None:
    bucket, key = _parse_r2_path(path)
    if not bucket or not key or bucket != _r2_bucket():
        raise HTTPException(status_code=400, detail="Invalid storage path.")

    prefix = "audio/" if kind == "audio" else "covers/"
    # Upload keys are generated server-side as UUID-based names. Require that
    # shape so a creator cannot attach another user's arbitrary R2 object.
    filename = key.rsplit("/", 1)[-1]
    if not key.startswith(prefix) or "/" in filename[:-1] or len(filename.split(".", 1)[0]) != 32:
        raise HTTPException(status_code=400, detail="Invalid storage object path.")

    ext = os.path.splitext(filename)[1].lower()
    allowed = ALLOWED_AUDIO_EXT if kind == "audio" else ALLOWED_IMAGE_EXT
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Invalid storage object type.")

    # The current key scheme is intentionally opaque and server-generated.
    # profile_id is retained in this function's contract so ownership checks
    # can be tightened without changing callers if key prefixes are later
    # namespaced by creator.
    _ = profile_id


def _verify_uploaded_object(path: str, profile_id: str, kind: str) -> dict[str, Any]:
    _verify_owned_r2_path(path, profile_id, kind)
    try:
        head = r2_object_head(path)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    size = int(head.get("ContentLength") or 0)
    if size <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size > _max_upload_bytes():
        raise HTTPException(status_code=400, detail="Uploaded file exceeds the upload limit.")
    return head


@router.post("/dashboard/upload/sign")
def sign_upload(
    payload: SignUploadRequest,
    user: User = Depends(require_creator),
):
    if not _r2_is_configured():
        raise HTTPException(status_code=503, detail="Cloud storage is not configured on BeatHub.")

    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    ext, expected_type = _mime_for(payload.filename, payload.kind)
    supplied_type = (payload.content_type or "").split(";", 1)[0].strip().lower()
    # Browsers normally provide the correct MIME. For unusual extensions,
    # normalize to the server-approved type so the signed Content-Type and PUT
    # request always agree.
    content_type = expected_type if supplied_type in {"", "application/octet-stream"} else supplied_type
    if content_type != expected_type:
        raise HTTPException(status_code=400, detail="File content type does not match its extension.")

    signed = r2_presigned_upload(payload.filename, content_type, payload.kind)
    return {
        "ok": True,
        "url": signed["url"],
        "path": signed["path"],
        "key": signed["key"],
        "content_type": content_type,
        "expires_in": 900,
        "extension": ext,
    }


@router.post("/dashboard/upload")
def finalize_upload(
    payload: TrackUploadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")
    if not _r2_is_configured():
        raise HTTPException(status_code=503, detail="Cloud storage is not configured on BeatHub.")

    prepared: list[dict[str, Any]] = []
    try:
        for item in payload.items:
            title = item.title.strip()
            description = (item.description or "").strip() or None
            genre = (item.genre or "").strip() or None
            tags = (item.tags or "").strip() or None

            if item.content_type not in {TrackContentType.BEAT.value, TrackContentType.TRACK.value}:
                raise HTTPException(status_code=400, detail=f"Invalid content type for '{title}'.")
            if item.currency.upper() not in {ProductCurrency.KES.value, ProductCurrency.USD.value}:
                raise HTTPException(status_code=400, detail=f"Currency for '{title}' must be KES or USD.")
            if item.sales_model not in {SalesModel.EXCLUSIVE.value, SalesModel.NON_EXCLUSIVE.value}:
                raise HTTPException(status_code=400, detail=f"Invalid sales model for '{title}'.")

            bpm_value = None
            bpm_raw = (item.bpm or "").strip()
            if bpm_raw:
                try:
                    bpm_value = int(bpm_raw)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"BPM for '{title}' must be a whole number.") from exc
                if not 1 <= bpm_value <= 999:
                    raise HTTPException(status_code=400, detail=f"BPM for '{title}' must be between 1 and 999.")

            try:
                price_value = Decimal(item.price.strip())
            except (InvalidOperation, AttributeError) as exc:
                raise HTTPException(status_code=400, detail=f"Price for '{title}' is invalid.") from exc
            if price_value < 0:
                raise HTTPException(status_code=400, detail=f"Price for '{title}' cannot be negative.")

            _verify_uploaded_object(item.audio_r2_path, str(profile.id), "audio")
            cover_path = None
            if item.cover_r2_path:
                _verify_uploaded_object(item.cover_r2_path, str(profile.id), "covers")
                cover_path = item.cover_r2_path.strip()

            prepared.append({
                "title": title,
                "description": description,
                "genre": genre,
                "tags": tags,
                "bpm": bpm_value,
                "price": price_value,
                "currency": item.currency.upper(),
                "sales_model": SalesModel(item.sales_model),
                "content_type": item.content_type,
                "audio_file_path": item.audio_r2_path.strip(),
                "cover_art_path": cover_path,
            })

        created: list[Track] = []
        for data in prepared:
            track = Track(
                creator_profile_id=profile.id,
                title=data["title"],
                slug=unique_slug(db, Track, data["title"], "track"),
                description=data["description"],
                genre=data["genre"],
                bpm=data["bpm"],
                tags=data["tags"],
                audio_file_path=data["audio_file_path"],
                cover_art_path=data["cover_art_path"],
                price=data["price"],
                currency=data["currency"],
                sales_model=data["sales_model"],
                content_type=data["content_type"],
                is_published=True,
            )
            db.add(track)
            created.append(track)

        db.commit()
        return {"ok": True, "count": len(created), "message": f"{len(created)} track(s) uploaded successfully."}

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="BeatHub could not finalize the upload. No track was published.")
