"""
Storage abstraction for uploaded media (audio, cover art, avatars).
Currently implements local disk storage under settings.MEDIA_ROOT.
Swap this module's implementation for S3/cloud storage in production
without touching any calling code — callers only use save_upload().
"""
import os
import uuid

from fastapi import UploadFile

from app.config import settings

ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac"}
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


class UploadValidationError(Exception):
    pass


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


async def save_upload(file: UploadFile, subfolder: str, allowed_extensions: set[str]) -> str:
    """
    Saves an uploaded file to MEDIA_ROOT/subfolder/<uuid><ext> and returns
    the relative path (stored in the DB, served via /media/*).
    """
    if not file or not file.filename:
        raise UploadValidationError("No file was uploaded.")

    ext = _ext(file.filename)
    if ext not in allowed_extensions:
        raise UploadValidationError(f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}")

    contents = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise UploadValidationError(f"File exceeds the {settings.MAX_UPLOAD_MB}MB upload limit.")

    folder = os.path.join(settings.MEDIA_ROOT, subfolder)
    os.makedirs(folder, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{ext}"
    full_path = os.path.join(folder, unique_name)
    with open(full_path, "wb") as f:
        f.write(contents)

    return f"{subfolder}/{unique_name}"
