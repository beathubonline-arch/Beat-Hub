```python
"""
BeatHub storage abstraction.

Production behaviour:
    - Cloudflare R2 is preferred when correctly configured.
    - Local MEDIA_ROOT storage is used automatically when R2 is not configured.
    - Existing callers using save_upload() continue to work.
    - Existing callers using save_upload_to_r2() continue to work.
    - Existing callers using r2_presigned_url() continue to work.
    - Existing callers using r2_url() continue to work.
    - Existing callers using media_url() continue to work.

Supported database references:

    R2:
        r2://bucket/folder/file.ext

    Local:
        folder/file.ext
        media/folder/file.ext
        /media/folder/file.ext

The merchandise system uses:

    save_upload(file, "merch", ALLOWED_IMAGE_EXT)

and therefore automatically follows the same storage architecture
as beats, covers and album artwork.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, Set

from fastapi import UploadFile

from app.config import settings


# ======================================================================
# FILE TYPES
# ======================================================================

ALLOWED_AUDIO_EXT: Set[str] = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
}

ALLOWED_IMAGE_EXT: Set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


# ======================================================================
# DEFAULTS
# ======================================================================

DEFAULT_MAX_UPLOAD_MB = 50

# Local storage fallback.

# This does NOT require MEDIA_ROOT to exist in app.config.Settings.
#
# Priority:
#   1. settings.MEDIA_ROOT
#   2. MEDIA_ROOT environment variable
#   3. <project>/media
#
# On Render, R2 should normally be configured, so this local fallback
# is primarily useful for development and emergency compatibility.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEDIA_ROOT = PROJECT_ROOT / "media"


# ======================================================================
# ERRORS
# ======================================================================

class UploadValidationError(Exception):
    """Raised when an uploaded file fails validation."""
    pass


# ======================================================================
# ENVIRONMENT HELPERS
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
    Return the first non-empty application setting.

    getattr() is intentionally used so this module remains compatible
    with Settings classes that do not define every optional storage field.
    """

    for name in names:
        try:
            value = getattr(settings, name, None)
        except Exception:
            value = None

        if value is not None:
            value = str(value).strip()

            if value:
                return value

    return None


def _config(*names: str) -> Optional[str]:
    """
    Check application settings first and environment variables second.
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
    Resolve local media storage without requiring MEDIA_ROOT in Settings.

    Priority:
        settings.MEDIA_ROOT
        MEDIA_ROOT environment variable
        project/media
    """

    configured = _config(
        "MEDIA_ROOT",
        "MEDIA_DIRECTORY",
        "MEDIA_DIR",
    )

    if configured:
        root = Path(configured).expanduser()

        if not root.is_absolute():
            root = PROJECT_ROOT / root

        return root.resolve()

    return DEFAULT_MEDIA_ROOT.resolve()


# ======================================================================
# MAX UPLOAD SIZE
# ======================================================================

def _max_upload_mb() -> int:
    """
    Read the upload-size setting safely.

    Compatible with:
        settings.MAX_UPLOAD_MB
        MAX_UPLOAD_MB environment variable

    Falls back to 50 MB.
    """

    raw = _config(
        "MAX_UPLOAD_MB",
        "MAX_UPLOAD_SIZE_MB",
    )

    if raw is None:
        return DEFAULT_MAX_UPLOAD_MB

    try:
        value = int(float(raw))
    except (ValueError, TypeError):
        return DEFAULT_MAX_UPLOAD_MB

    if value <= 0:
        return DEFAULT_MAX_UPLOAD_MB

    return value


def _max_upload_bytes() -> int:
    return _max_upload_mb() * 1024 * 1024


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
    """
    Resolve the Cloudflare R2 S3-compatible endpoint.

    If R2_ENDPOINT_URL/R2_ENDPOINT is supplied, use it.

    Otherwise construct:

        https://ACCOUNT_ID.r2.cloudflarestorage.com
    """

    endpoint = _config(
        "R2_ENDPOINT_URL",
        "R2_ENDPOINT",
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
    """
    Return True only when all required R2 credentials are available.
    """

    return bool(
        _r2_endpoint()
        and _r2_access_key()
        and _r2_secret_key()
        and _r2_bucket()
    )


# Public compatibility helper.

def r2_enabled() -> bool:
    """
    Return whether R2 storage is currently usable.
    """

    return _r2_is_configured()


# ======================================================================
# EXTENSION
# ======================================================================

def _ext(filename: str) -> str:
    """
    Return a lowercase file extension.
    """

    return os.path.splitext(
        filename or ""
    )[1].lower()


# ======================================================================
# FILE VALIDATION
# ======================================================================

def _validate_upload(
    file: UploadFile,
    allowed_extensions: Set[str],
) -> str:
    """
    Validate filename and extension.
    """

    if not file:
        raise UploadValidationError(
            "No file was uploaded."
        )

    if not file.filename:
        raise UploadValidationError(
            "No file was uploaded."
        )

    extension = _ext(file.filename)

    if extension not in allowed_extensions:
        allowed = ", ".join(
            sorted(allowed_extensions)
        )

        raise UploadValidationError(
            f"Unsupported file type '{extension}'. "
            f"Allowed: {allowed}"
        )

    return extension


async def _read_upload(
    file: UploadFile,
    allowed_extensions: Set[str],
) -> tuple[bytes, str]:
    """
    Validate and read an uploaded file.

    Returns:
        (contents, extension)
    """

    extension = _validate_upload(
        file,
        allowed_extensions,
    )

    contents = await file.read()

    if not contents:
        raise UploadValidationError(
            "The uploaded file is empty."
        )

    max_bytes = _max_upload_bytes()

    if len(contents) > max_bytes:
        raise UploadValidationError(
            f"File exceeds the "
            f"{_max_upload_mb()}MB upload limit."
        )

    return contents, extension


# ======================================================================
# R2 CLIENT
# ======================================================================

def _r2_client():
    """
    Lazily create the Cloudflare R2 S3-compatible boto3 client.

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
    Convert:

        r2://beathub-r2/covers/file.png

    into:

        ("beathub-r2", "covers/file.png")
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
    Convert an R2 object reference into a temporary browser URL.

    Supports:

        r2://bucket/key
        https://...
        http://...
        local media paths
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    # --------------------------------------------------------------
    # Already a web URL
    # --------------------------------------------------------------

    if value.startswith(
        (
            "https://",
            "http://",
        )
    ):
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

            safe_expires = max(
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
                ExpiresIn=safe_expires,
            )

        except Exception:
            return None

    # --------------------------------------------------------------
    # Local file
    # --------------------------------------------------------------

    return _local_media_url(value)


# ======================================================================
# LOCAL MEDIA URL
# ======================================================================

def _local_media_url(
    path: Optional[str],
) -> Optional[str]:
    """
    Convert a local database path into a browser URL.
    """

    if not path:
        return None

    clean = (
        str(path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if not clean:
        return None

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
    Backwards-compatible URL helper.
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
    Return a browser-accessible URL for:

        - R2 object
        - HTTPS URL
        - HTTP URL
        - local media path
    """

    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith(
        (
            "https://",
            "http://",
        )
    ):
        return value

    if value.startswith("r2://"):
        return r2_presigned_url(
            value,
            expires=expires,
        )

    return _local_media_url(value)


# ======================================================================
# SAVE TO R2
# ======================================================================

async def save_upload_to_r2(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: Set[str],
) -> str:
    """
    Upload directly to Cloudflare R2.

    Returns:

        r2://bucket/subfolder/random.ext
    """

    if not _r2_is_configured():
        raise RuntimeError(
            "R2 storage is not fully configured."
        )

    contents, extension = await _read_upload(
        file,
        allowed_extensions,
    )

    bucket = _r2_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    clean_subfolder = (
        str(subfolder or "")
        .replace("\\", "/")
        .strip("/")
    )

    if not clean_subfolder:
        clean_subfolder = "uploads"

    key = (
        f"{clean_subfolder}/"
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

    return f"r2://{bucket}/{key}"


# ======================================================================
# SAVE LOCALLY
# ======================================================================

async def save_upload_local(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: Set[str],
) -> str:
    """
    Save an upload to local media storage.

    Returns:

        subfolder/random.ext
    """

    contents, extension = await _read_upload(
        file,
        allowed_extensions,
    )

    clean_subfolder = (
        str(subfolder or "")
        .replace("\\", "/")
        .strip("/")
    )

    if not clean_subfolder:
        clean_subfolder = "uploads"

    media_root = _media_root()

    folder = (
        media_root / clean_subfolder
    ).resolve()

    # --------------------------------------------------------------
    # Prevent path traversal.
    # --------------------------------------------------------------

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

    unique_name = (
        f"{uuid.uuid4().hex}"
        f"{extension}"
    )

    full_path = (
        folder / unique_name
    ).resolve()

    try:
        full_path.relative_to(
            media_root
        )
    except ValueError as exc:
        raise UploadValidationError(
            "Invalid upload path."
        ) from exc

    with open(
        full_path,
        "wb",
    ) as output:
        output.write(contents)

    return (
        f"{clean_subfolder}/"
        f"{unique_name}"
    )


# ======================================================================
# MAIN UPLOAD FUNCTION
# ======================================================================

async def save_upload(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: Set[str],
) -> str:
    """
    Main storage function used by BeatHub.

    Behaviour:

        R2 configured
            -> upload to R2

        R2 not configured
            -> save locally

    This means existing routes do not need to know whether production
    storage is local or Cloudflare R2.

    Merchandise therefore works with:

        save_upload(
            image,
            "merch",
            ALLOWED_IMAGE_EXT,
        )

    and automatically uses R2 in production when configured.
    """

    if not file or not file.filename:
        raise UploadValidationError(
            "No file was uploaded."
        )

    # --------------------------------------------------------------
    # Production / configured R2
    # --------------------------------------------------------------

    if _r2_is_configured():

        try:
            return await save_upload_to_r2(
                file,
                subfolder,
                allowed_extensions,
            )

        except UploadValidationError:
            raise

        except Exception as exc:
            # Do NOT silently switch to local storage after a real
            # R2 failure in production. That could make a product
            # appear saved while the permanent object is missing.
            raise RuntimeError(
                f"Cloud storage upload failed: {exc}"
            ) from exc

    # --------------------------------------------------------------
    # Local development fallback
    # --------------------------------------------------------------

    return await save_upload_local(
        file,
        subfolder,
        allowed_extensions,
    )


# ======================================================================
# STORAGE EXISTENCE CHECK
# ======================================================================

def storage_exists(
    path: Optional[str],
) -> bool:
    """
    Check whether a stored object exists.

    Supports both:

        r2://bucket/key

    and local media paths.
    """

    if not path:
        return False

    value = str(path).strip()

    if not value:
        return False

    # --------------------------------------------------------------
    # R2
    # --------------------------------------------------------------

    bucket, key = _parse_r2_path(
        value
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
    # Remote HTTP URL
    # --------------------------------------------------------------

    if value.startswith(
        (
            "http://",
            "https://",
        )
    ):
        # We cannot reliably determine existence of an arbitrary
        # external URL without performing a network request.
        return True

    # --------------------------------------------------------------
    # Local
    # --------------------------------------------------------------

    clean = (
        value
        .replace("\\", "/")
        .lstrip("/")
    )

    if clean.startswith("media/"):
        clean = clean[6:]

    media_root = _media_root().resolve()

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
    """
    Delete an R2 object from an r2:// reference.
    """

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
    """
    Safely delete a local media file.

    Path traversal outside MEDIA_ROOT is rejected.
    """

    if not path:
        return False

    value = (
        str(path)
        .replace("\\", "/")
        .lstrip("/")
    )

    if value.startswith("media/"):
        value = value[6:]

    if not value:
        return False

    media_root = _media_root().resolve()

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


# ======================================================================
# DELETE ANY STORED OBJECT
# ======================================================================

def delete_storage_object(
    path: Optional[str],
) -> bool:
    """
    Delete either an R2 object or a local file.

    Useful for future merchandise product deletion.
    """

    if not path:
        return False

    value = str(path).strip()

    if value.startswith("r2://"):
        return delete_r2_object(
            value
        )

    return delete_local_object(
        value
    )
```
