"""Secure public audio previews without exposing uploaded masters.

The marketplace must be able to preview music before purchase, but the
original uploaded master must remain private. This router uses the existing
Track.preview_file_path column and lazily creates a short, lower-bitrate MP3
preview when an older track does not have one yet.

Only BeatHub-managed local MEDIA_ROOT files and R2 objects are used as preview
sources. Arbitrary external URLs are never fetched by the server.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import SalesModel, Track
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    UploadValidationError,
    _parse_r2_path,
    _r2_bucket,
    _r2_client,
    save_upload,
)

logger = logging.getLogger("beathub.audio_preview")

router = APIRouter(tags=["music"])

PREVIEW_SECONDS = 30
PREVIEW_SAMPLE_RATE = 22050
PREVIEW_BITRATE = "96k"
PREVIEW_MAX_BYTES = 8 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 45


def _track_is_public(track: Track) -> bool:
    if getattr(track, "is_published", True) is False:
        return False

    sales_model = getattr(track, "sales_model", None)
    sales_value = getattr(sales_model, "value", sales_model)
    if str(sales_value or "").strip().lower() == SalesModel.EXCLUSIVE.value:
        if bool(getattr(track, "is_sold", False)):
            return False

    return True


def _resolve_local_source(stored_path: str) -> Optional[Path]:
    value = str(stored_path or "").strip()
    if not value or value.startswith(("http://", "https://", "r2://", "s3://")):
        return None

    stored = Path(value)
    media_root = Path(getattr(settings, "MEDIA_ROOT", "media") or "media").expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    media_root = media_root.resolve()

    candidates = []
    if stored.is_absolute():
        candidates.append(stored.resolve())
    else:
        candidates.append((Path.cwd() / stored).resolve())
        candidates.append((media_root / stored).resolve())
        clean = str(stored).replace("\\", "/").lstrip("/")
        if clean.startswith("media/"):
            candidates.append((media_root / clean[6:]).resolve())

    for candidate in candidates:
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate

    return None


def _download_r2_source(stored_path: str, destination: Path) -> None:
    normalized = str(stored_path or "").strip()
    if normalized.startswith("s3://"):
        normalized = "r2://" + normalized[6:]

    bucket, key = _parse_r2_path(normalized)
    configured_bucket = _r2_bucket()
    if not bucket or not key or not configured_bucket or bucket != configured_bucket:
        raise ValueError("Invalid R2 audio path.")

    client = _r2_client()
    client.download_file(bucket, key, str(destination))


def _make_preview_from_source(source_path: Path) -> bytes:
    """Create a bounded 30-second MP3 preview using Render's ffmpeg binary."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as output:
        output_path = Path(output.name)

    try:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source_path),
            "-map",
            "0:a:0",
            "-t",
            str(PREVIEW_SECONDS),
            "-ac",
            "1",
            "-ar",
            str(PREVIEW_SAMPLE_RATE),
            "-b:a",
            PREVIEW_BITRATE,
            "-map_metadata",
            "-1",
            "-vn",
            "-sn",
            "-dn",
            "-threads",
            "1",
            "-y",
            str(output_path),
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Audio preview processing is unavailable on this deployment.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Audio preview processing timed out.") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()[-1:] or ["unknown ffmpeg error"]
            raise RuntimeError(f"Audio preview processing failed: {detail[0][:240]}")

        if not output_path.is_file():
            raise RuntimeError("Audio preview was not created.")

        size = output_path.stat().st_size
        if size <= 0 or size > PREVIEW_MAX_BYTES:
            raise RuntimeError("Generated audio preview is outside the allowed size range.")

        return output_path.read_bytes()
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass


def _generate_preview_bytes(stored_path: str) -> bytes:
    value = str(stored_path or "").strip()
    if not value:
        raise RuntimeError("Track has no source audio.")

    suffix = Path(value.split("?", 1)[0]).suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXT:
        raise RuntimeError("Track audio format is not supported for preview generation.")

    if value.startswith(("http://", "https://")):
        # Never let a database value turn this endpoint into an SSRF proxy.
        raise RuntimeError("External audio sources cannot be used to generate previews.")

    with tempfile.TemporaryDirectory(prefix="beathub-preview-") as temp_dir:
        source_path = Path(temp_dir) / f"source{suffix}"

        if value.startswith(("r2://", "s3://")):
            _download_r2_source(value, source_path)
        else:
            local = _resolve_local_source(value)
            if not local:
                raise RuntimeError("Stored source audio is unavailable.")
            source_path.write_bytes(local.read_bytes())

        return _make_preview_from_source(source_path)


def _safe_preview_path(value: str) -> bool:
    """Only serve preview objects created by this application."""
    normalized = str(value or "").strip().replace("\\", "/")
    if normalized.startswith("s3://"):
        normalized = "r2://" + normalized[6:]
    if normalized.startswith("r2://"):
        bucket, key = _parse_r2_path(normalized)
        return bool(
            bucket
            and key
            and bucket == _r2_bucket()
            and key.startswith("previews/")
        )
    return normalized.startswith(("media/previews/", "previews/"))


def _serve_preview(stored_path: str, request: Request):
    value = str(stored_path or "").strip()
    if not _safe_preview_path(value):
        raise HTTPException(status_code=404, detail="Preview audio is unavailable.")

    if value.startswith(("r2://", "s3://")):
        try:
            from app.routers.music import _r2_media_response

            return _r2_media_response(
                value,
                request,
                fallback_media_type="audio/mpeg",
            )
        except Exception:
            logger.exception("Unable to serve stored R2 preview")
            raise HTTPException(status_code=404, detail="Preview audio is unavailable.")

    clean = value.replace("\\", "/").lstrip("/")
    if clean.startswith("media/"):
        clean = clean[6:]

    media_root = Path(getattr(settings, "MEDIA_ROOT", "media") or "media").expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    media_root = media_root.resolve()
    path = (media_root / clean).resolve()

    try:
        path.relative_to(media_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Preview audio is unavailable.")

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview audio is unavailable.")

    return FileResponse(
        path=str(path),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _save_preview(db: Session, track: Track, preview_bytes: bytes) -> str:
    from io import BytesIO

    upload = UploadFile(
        file=BytesIO(preview_bytes),
        filename="preview.mp3",
    )
    try:
        return_path = asyncio.run(save_upload(upload, "previews", {".mp3"}))
    finally:
        try:
            upload.file.close()
        except Exception:
            pass

    if not return_path:
        raise RuntimeError("Preview storage did not return an object path.")

    track.preview_file_path = return_path
    db.commit()
    db.refresh(track)
    return return_path


@router.get("/track/{slug}/preview", name="secure_track_preview")
def secure_track_preview(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    if not _track_is_public(track):
        raise HTTPException(status_code=404, detail="Track not found.")

    stored_preview = str(getattr(track, "preview_file_path", "") or "").strip()
    if stored_preview and _safe_preview_path(stored_preview):
        return _serve_preview(stored_preview, request)

    source = str(getattr(track, "audio_file_path", "") or "").strip()
    if not source:
        raise HTTPException(status_code=404, detail="This track has no preview audio.")

    try:
        preview_bytes = _generate_preview_bytes(source)
        preview_path = _save_preview(db, track, preview_bytes)
        return _serve_preview(preview_path, request)
    except UploadValidationError:
        logger.exception("Generated preview failed validation for track %s", slug)
    except Exception:
        logger.exception("Unable to generate preview for track %s", slug)

    raise HTTPException(
        status_code=404,
        detail="Preview audio is currently unavailable.",
    )
