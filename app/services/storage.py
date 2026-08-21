"""
BeatHub storage abstraction.

Supports:
- Local MEDIA_ROOT storage
- Cloudflare R2 storage references stored as:
    r2://bucket/key
- R2 presigned URLs for browser access
- Local /media/... URLs for legacy local files
- Existing callers using save_upload()
- Existing callers using r2_presigned_url()
- Existing callers using r2_url()

No database migration is required.
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.config import settings


# ======================================================================
# FILE TYPES
# ======================================================================

ALLOWED_AUDIO_EXT = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
}

ALLOWED_IMAGE_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


# ======================================================================
# ERRORS
# ======================================================================

class UploadValidationError(Exception):
    pass


# ======================================================================
# ENVIRONMENT HELPERS
# ======================================================================

def _env(*names: str) -> Optional[str]:
    """
    Return the first non-empty environment variable.

    Supports multiple naming conventions so existing Render
    environment variables continue working.
    """
    for name in names:
        value = os.getenv(name)

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _setting(*names: str) -> Optional[str]:
    """
    Return the first available setting attribute.
    """
    for name in names:
        try:
            value = getattr(settings, name, None)
        except Exception:
            value = None

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _config(*names: str) -> Optional[str]:
    """
    Check both application settings and environment variables.
    """
    value = _setting(*names)

    if value:
        return value

    return _env(*names)


# ======================================================================
# R2 CONFIGURATION
# ======================================================================

def _r2_account_id() -> Optional[str]:
    return _config(
        "R2_ACCOUNT_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "CF_ACCOUNT_ID",
    )


def _r2_access_key() -> Optional[str]:
    return _config(
        "R2_ACCESS_KEY_ID",
        "R2_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
    )


def _r2_secret_key() -> Optional[str]:
    return _config(
        "R2_SECRET_ACCESS_KEY",
        "R2_SECRET_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )


def _r2_bucket() -> Optional[str]:
    return _config(
        "R2_BUCKET_NAME",
        "R2_BUCKET",
        "R2_BUCKET_NAME",
        "AWS_S3_BUCKET",
    )


def _r2_endpoint() -> Optional[str]:
    endpoint = _config(
        "R2_ENDPOINT",
        "R2_ENDPOINT_URL",
        "AWS_ENDPOINT_URL",
    )

    if endpoint:
        return endpoint.rstrip("/")

    account_id = _r2_account_id()

    if account_id:
        return (
            f"https://{account_id}.r2.cloudflarestorage.com"
        )

    return None


def _r2_is_configured() -> bool:
    return bool(
        _r2_endpoint()
        and _r2_access_key()
        and _r2_secret_key()
        and _r2_bucket()
    )


# ======================================================================
# EXTENSION
# ======================================================================

def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


# ======================================================================
# R2 CLIENT
# ======================================================================

def _r2_client():
    """
    Lazily create the R2 S3-compatible client.

    boto3 is imported only when R2 functionality is actually used.
    """

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "R2 storage requires boto3. "
            "Add boto3 to requirements.txt."
        ) from exc

    endpoint = _r2_endpoint()
    access_key = _r2_access_key()
    secret_key = _r2_secret_key()

    if not endpoint:
        raise RuntimeError(
            "R2 endpoint is not configured."
        )

    if not access_key:
        raise RuntimeError(
            "R2 access key is not configured."
        )

    if not secret_key:
        raise RuntimeError(
            "R2 secret key is not configured."
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


# ======================================================================
# R2 PATH PARSER
# ======================================================================

def _parse_r2_path(path: str):
    """
    Converts:

        r2://beathub-r2/covers/file.png

    into:

        bucket = beathub-r2
        key    = covers/file.png
    """

    if not path:
        return None, None

    value = str(path).strip()

    if not value.lower().startswith("r2://"):
        return None, None

    remainder = value[5:]

    if "/" not in remainder:
        return remainder, ""

    bucket, key = remainder.split("/", 1)

    return bucket, key


# ======================================================================
# R2 PRESIGNED URL
# ======================================================================

def r2_presigned_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    """
    Convert an R2 database path into a temporary browser-accessible URL.

    Example:

        r2://beathub-r2/covers/photo.png

    becomes a signed HTTPS URL.
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    # --------------------------------------------------------------
    # Already an HTTP(S) URL
    # --------------------------------------------------------------

    if value.startswith("https://") or value.startswith("http://"):
        return value

    # --------------------------------------------------------------
    # R2 object
    # --------------------------------------------------------------

    bucket_from_path, key = _parse_r2_path(value)

    if bucket_from_path is not None:

        bucket = (
            bucket_from_path
            or _r2_bucket()
        )

        if not bucket or not key:
            return None

        try:
            client = _r2_client()

            return client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                },
                ExpiresIn=max(
                    1,
                    min(
                        int(expires),
                        604800,
                    ),
                ),
            )

        except Exception:
            return None

    # --------------------------------------------------------------
    # Local file
    #
    # Database may contain:
    #
    # media/covers/file.jpg
    # covers/file.jpg
    # /media/covers/file.jpg
    # --------------------------------------------------------------

    clean = value.replace("\\", "/").lstrip("/")

    if clean.startswith("media/"):
        return "/" + clean

    if clean.startswith("static/"):
        return "/" + clean

    return "/media/" + clean


# ======================================================================
# R2 URL COMPATIBILITY
# ======================================================================

def r2_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    """
    Backwards-compatible alias.

    Existing code can continue calling:

        r2_url(path)

    """

    return r2_presigned_url(
        path,
        expires=expires,
    )


# ======================================================================
# GENERIC MEDIA URL
# ======================================================================

def media_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    """
    Return a browser-accessible URL for either:

    - R2 object
    - HTTPS URL
    - local media file
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if (
        value.startswith("http://")
        or value.startswith("https://")
    ):
        return value

    if value.startswith("r2://"):
        return r2_presigned_url(
            value,
            expires=expires,
        )

    clean = value.replace("\\", "/").lstrip("/")

    if clean.startswith("media/"):
        return "/" + clean

    return "/media/" + clean


# ======================================================================
# R2 UPLOAD
# ======================================================================

async def save_upload_to_r2(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:

    if not file or not file.filename:
        raise UploadValidationError(
            "No file was uploaded."
        )

    ext = _ext(file.filename)

    if ext not in allowed_extensions:
        raise UploadValidationError(
            f"Unsupported file type '{ext}'. "
            f"Allowed: {', '.join(sorted(allowed_extensions))}"
        )

    contents = await file.read()

    max_bytes = (
        int(settings.MAX_UPLOAD_MB)
        * 1024
        * 1024
    )

    if len(contents) > max_bytes:
        raise UploadValidationError(
            f"File exceeds the "
            f"{settings.MAX_UPLOAD_MB}MB upload limit."
        )

    bucket = _r2_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    key = (
        f"{subfolder.strip('/')}/"
        f"{uuid.uuid4().hex}{ext}"
    )

    content_type = file.content_type or "application/octet-stream"

    try:
        client = _r2_client()

        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=contents,
            ContentType=content_type,
        )

    except Exception as exc:
        raise RuntimeError(
            f"R2 upload failed: {exc}"
        ) from exc

    return f"r2://{bucket}/{key}"


# ======================================================================
# LOCAL UPLOAD
# ======================================================================

async def save_upload(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:
    """
    Save upload locally.

    Existing local-storage behavior is preserved.

    If you want to use R2 uploads, call save_upload_to_r2().
    """

    if not file or not file.filename:
        raise UploadValidationError(
            "No file was uploaded."
        )

    ext = _ext(file.filename)

    if ext not in allowed_extensions:
        raise UploadValidationError(
            f"Unsupported file type '{ext}'. "
            f"Allowed: {', '.join(sorted(allowed_extensions))}"
        )

    contents = await file.read()

    max_bytes = (
        int(settings.MAX_UPLOAD_MB)
        * 1024
        * 1024
    )

    if len(contents) > max_bytes:
        raise UploadValidationError(
            f"File exceeds the "
            f"{settings.MAX_UPLOAD_MB}MB upload limit."
        )

    media_root = Path(
        settings.MEDIA_ROOT
    )

    folder = (
        media_root
        / subfolder
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_name = (
        f"{uuid.uuid4().hex}{ext}"
    )

    full_path = (
        folder / unique_name
    )

    with open(
        full_path,
        "wb",
    ) as output:
        output.write(contents)

    return (
        f"{subfolder.strip('/')}/"
        f"{unique_name}"
    )


# ======================================================================
# STORAGE EXISTENCE CHECK
# ======================================================================

def storage_exists(path: Optional[str]) -> bool:
    """
    Check whether a stored object exists.

    Supports both R2 and local files.
    """

    if not path:
        return False

    value = str(path).strip()

    # --------------------------------------------------------------
    # R2
    # --------------------------------------------------------------

    bucket, key = _parse_r2_path(value)

    if bucket is not None:

        if not bucket or not key:
            return False

        try:
            client = _r2_client()

            client.head_object(
                Bucket=bucket,
                Key=key,
            )

            return True

        except Exception:
            return False

    # --------------------------------------------------------------
    # Local
    # --------------------------------------------------------------

    clean = value.replace("\\", "/").lstrip("/")

    media_root = Path(
        settings.MEDIA_ROOT
    ).resolve()

    if clean.startswith("media/"):
        clean = clean[6:]

    candidate = (
        media_root / clean
    ).resolve()

    try:
        candidate.relative_to(media_root)
    except ValueError:
        return False

    return (
        candidate.exists()
        and candidate.is_file()
    )


# ======================================================================
# DELETE R2 OBJECT
# ======================================================================

def delete_r2_object(
    path: Optional[str],
) -> bool:

    if not path:
        return False

    bucket, key = _parse_r2_path(
        str(path)
    )

    if not bucket or not key:
        return False

    try:
        client = _r2_client()

        client.delete_object(
            Bucket=bucket,
            Key=key,
        )

        return True

    except Exception:
        return False


# ======================================================================
# DELETE LOCAL OBJECT
# ======================================================================

def delete_local_object(
    path: Optional[str],
) -> bool:

    if not path:
        return False

    value = str(path).replace(
        "\\",
        "/",
    ).lstrip("/")

    if value.startswith("media/"):
        value = value[6:]

    media_root = Path(
        settings.MEDIA_ROOT
    ).resolve()

    target = (
        media_root / value
    ).resolve()

    try:
        target.relative_to(media_root)
    except ValueError:
        return False

    try:
        if target.exists() and target.is_file():
            target.unlink()
            return True

    except Exception:
        pass

    return False
