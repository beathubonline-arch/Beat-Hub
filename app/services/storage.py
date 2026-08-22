"""
BeatHub storage service.

Supports:
- Local media storage
- Cloudflare R2 storage
- R2 presigned browser URLs
- Existing r2_url() callers
- Existing r2_presigned_url() callers
- Existing save_upload() callers
- Explicit save_upload_to_r2() callers
- Safe local path handling
- Safe R2 object handling
- Image and audio validation
- Upload-size validation

The service intentionally keeps storage configuration tolerant of
different Render environment-variable naming conventions.
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
    """Raised when an uploaded file fails validation."""


# ======================================================================
# CONFIGURATION HELPERS
# ======================================================================

def _env(*names: str) -> Optional[str]:
    """
    Return the first non-empty environment variable.
    """

    for name in names:
        value = os.getenv(name)

        if value is not None:
            value = str(value).strip()

            if value:
                return value

    return None


def _setting(*names: str) -> Optional[str]:
    """
    Return the first available setting attribute.
    """

    for name in names:
        try:
            value = getattr(
                settings,
                name,
                None,
            )
        except Exception:
            value = None

        if value is not None:
            value = str(value).strip()

            if value:
                return value

    return None


def _config(*names: str) -> Optional[str]:
    """
    Check application settings first, then environment variables.
    """

    value = _setting(*names)

    if value:
        return value

    return _env(*names)


# ======================================================================
# MEDIA ROOT
# ======================================================================

def _media_root() -> Path:
    """
    Resolve the local media directory without requiring MEDIA_ROOT
    to exist as a Pydantic Settings field.

    Supported configuration names:
        MEDIA_ROOT
        MEDIA_DIRECTORY
        UPLOAD_DIR

    Falls back to:
        ./media
    """

    configured = _config(
        "MEDIA_ROOT",
        "MEDIA_DIRECTORY",
        "UPLOAD_DIR",
    )

    if configured:
        return Path(
            configured
        ).expanduser().resolve()

    return (
        Path(__file__)
        .resolve()
        .parents[2]
        / "media"
    )


# ======================================================================
# MAX UPLOAD SIZE
# ======================================================================

def _max_upload_mb() -> int:
    """
    Resolve the upload limit.

    Defaults to 50 MB if the setting is unavailable or invalid.
    """

    raw = _config(
        "MAX_UPLOAD_MB",
        "MAX_FILE_SIZE_MB",
    )

    if not raw:
        return 50

    try:
        value = int(
            float(raw)
        )
    except (
        ValueError,
        TypeError,
    ):
        return 50

    return max(
        1,
        value,
    )


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
            "https://"
            f"{account_id}"
            ".r2.cloudflarestorage.com"
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
# FILE EXTENSION
# ======================================================================

def _ext(
    filename: str,
) -> str:
    return os.path.splitext(
        filename or ""
    )[1].lower()


# ======================================================================
# UPLOAD VALIDATION
# ======================================================================

def _validate_extension(
    file: UploadFile,
    allowed_extensions: set[str],
) -> str:
    if not file:
        raise UploadValidationError(
            "No file was uploaded."
        )

    if not file.filename:
        raise UploadValidationError(
            "No filename was provided."
        )

    extension = _ext(
        file.filename
    )

    if extension not in allowed_extensions:
        allowed = ", ".join(
            sorted(
                allowed_extensions
            )
        )

        raise UploadValidationError(
            f"Unsupported file type "
            f"'{extension or 'unknown'}'. "
            f"Allowed: {allowed}"
        )

    return extension


async def _read_upload(
    file: UploadFile,
    allowed_extensions: set[str],
) -> tuple[bytes, str]:
    extension = _validate_extension(
        file,
        allowed_extensions,
    )

    contents = await file.read()

    if not contents:
        raise UploadValidationError(
            "The uploaded file is empty."
        )

    maximum_bytes = (
        _max_upload_mb()
        * 1024
        * 1024
    )

    if len(contents) > maximum_bytes:
        raise UploadValidationError(
            "File exceeds the "
            f"{_max_upload_mb()}MB upload limit."
        )

    return (
        contents,
        extension,
    )


# ======================================================================
# R2 CLIENT
# ======================================================================

def _r2_client():
    """
    Lazily create the Cloudflare R2 S3-compatible client.
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

def _parse_r2_path(
    path: str,
):
    """
    Convert:

        r2://bucket/folder/file.png

    into:

        ("bucket", "folder/file.png")
    """

    if not path:
        return None, None

    value = str(
        path
    ).strip()

    if not value.lower().startswith(
        "r2://"
    ):
        return None, None

    remainder = value[5:]

    if not remainder:
        return None, None

    if "/" not in remainder:
        return remainder, ""

    bucket, key = remainder.split(
        "/",
        1,
    )

    return (
        bucket,
        key,
    )


# ======================================================================
# R2 PRESIGNED URL
# ======================================================================

def r2_presigned_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    """
    Return a browser-accessible URL for:
    - R2 objects
    - HTTP URLs
    - local media paths
    """

    if not path:
        return None

    value = str(
        path
    ).strip()

    if not value:
        return None

    # Existing public URL
    if value.startswith(
        "https://"
    ) or value.startswith(
        "http://"
    ):
        return value

    # R2 object
    bucket_from_path, key = (
        _parse_r2_path(value)
    )

    if bucket_from_path is not None:
        bucket = (
            bucket_from_path
            or _r2_bucket()
        )

        if not bucket or not key:
            return None

        try:
            client = _r2_client()

            safe_expiry = max(
                1,
                min(
                    int(expires),
                    604800,
                ),
            )

            return client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                },
                ExpiresIn=safe_expiry,
            )

        except Exception:
            return None

    # Local media
    clean = (
        value
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        return "/" + clean

    if clean.startswith(
        "static/"
    ):
        return "/" + clean

    return "/media/" + clean


# ======================================================================
# BACKWARD COMPATIBILITY
# ======================================================================

def r2_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    return r2_presigned_url(
        path,
        expires=expires,
    )


def media_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    """
    Return a browser URL for local or R2 media.
    """

    if not path:
        return None

    value = str(
        path
    ).strip()

    if not value:
        return None

    if value.startswith(
        "http://"
    ) or value.startswith(
        "https://"
    ):
        return value

    if value.startswith(
        "r2://"
    ):
        return r2_presigned_url(
            value,
            expires=expires,
        )

    clean = (
        value
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
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
    """
    Upload directly to Cloudflare R2.

    Returns:

        r2://bucket/key
    """

    contents, extension = (
        await _read_upload(
            file,
            allowed_extensions,
        )
    )

    bucket = _r2_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    clean_folder = (
        (subfolder or "uploads")
        .strip("/")
        .replace("\\", "/")
    )

    key = (
        f"{clean_folder}/"
        f"{uuid.uuid4().hex}"
        f"{extension}"
    )

    content_type = (
        file.content_type
        or "application/octet-stream"
    )

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

    return (
        f"r2://{bucket}/{key}"
    )


# ======================================================================
# LOCAL UPLOAD
# ======================================================================

async def save_upload(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:
    """
    Save an upload to local media storage.

    This remains the compatibility function used by the existing
    dashboard, music and merchandise routes.
    """

    contents, extension = (
        await _read_upload(
            file,
            allowed_extensions,
        )
    )

    media_root = _media_root()

    clean_folder = (
        (subfolder or "uploads")
        .strip("/")
        .replace("\\", "/")
    )

    # Prevent directory traversal.
    folder = (
        media_root
        / clean_folder
    ).resolve()

    try:
        folder.relative_to(
            media_root
        )
    except ValueError as exc:
        raise UploadValidationError(
            "Invalid upload directory."
        ) from exc

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        f"{uuid.uuid4().hex}"
        f"{extension}"
    )

    destination = (
        folder / filename
    ).resolve()

    try:
        destination.relative_to(
            media_root
        )
    except ValueError as exc:
        raise UploadValidationError(
            "Invalid upload destination."
        ) from exc

    with destination.open(
        "wb"
    ) as output:
        output.write(contents)

    return (
        f"{clean_folder}/"
        f"{filename}"
    )


# ======================================================================
# STORAGE EXISTENCE
# ======================================================================

def storage_exists(
    path: Optional[str],
) -> bool:
    """
    Check whether a local or R2 object exists.
    """

    if not path:
        return False

    value = str(
        path
    ).strip()

    if not value:
        return False

    # --------------------------------------------------------------
    # R2
    # --------------------------------------------------------------

    bucket, key = (
        _parse_r2_path(value)
    )

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

    clean = (
        value
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        clean = clean[6:]

    media_root = (
        _media_root()
        .resolve()
    )

    candidate = (
        media_root / clean
    ).resolve()

    try:
        candidate.relative_to(
            media_root
        )
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

    bucket, key = (
        _parse_r2_path(
            str(path)
        )
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

    value = (
        str(path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if value.startswith(
        "media/"
    ):
        value = value[6:]

    media_root = (
        _media_root()
        .resolve()
    )

    target = (
        media_root / value
    ).resolve()

    try:
        target.relative_to(
            media_root
        )
    except ValueError:
        return False

    try:
        if (
            target.exists()
            and target.is_file()
        ):
            target.unlink()
            return True

    except Exception:
        return False

    return False
