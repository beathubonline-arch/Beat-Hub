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
# CONFIGURATION HELPERS
# ======================================================================

def _env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _setting(*names: str) -> Optional[str]:
    for name in names:
        try:
            value = getattr(settings, name, None)
        except Exception:
            value = None

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _config(*names: str) -> Optional[str]:
    value = _setting(*names)

    if value:
        return value

    return _env(*names)


# ======================================================================
# MEDIA ROOT
# ======================================================================

def _media_root() -> Path:
    """
    Resolve the local media directory safely.

    Supports:
        MEDIA_ROOT
        MEDIA_DIRECTORY
        UPLOAD_DIR

    If none is configured, BeatHub uses:

        <project>/media
    """

    configured = _config(
        "MEDIA_ROOT",
        "MEDIA_DIRECTORY",
        "UPLOAD_DIR",
    )

    if configured:
        root = Path(configured)
    else:
        root = Path(__file__).resolve().parents[2] / "media"

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


# ======================================================================
# MAX UPLOAD SIZE
# ======================================================================

def _max_upload_mb() -> int:
    value = _config(
        "MAX_UPLOAD_MB",
        "MAX_FILE_SIZE_MB",
    )

    try:
        amount = int(value) if value else 50
    except (TypeError, ValueError):
        amount = 50

    return max(1, amount)


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
# FILE HELPERS
# ======================================================================

def _ext(filename: str) -> str:
    return os.path.splitext(
        filename or ""
    )[1].lower()


def _validate_extension(
    file: UploadFile,
    allowed_extensions: set[str],
) -> str:
    if not file or not file.filename:
        raise UploadValidationError(
            "No file was uploaded."
        )

    ext = _ext(file.filename)

    if ext not in allowed_extensions:
        allowed = ", ".join(
            sorted(allowed_extensions)
        )

        raise UploadValidationError(
            f"Unsupported file type '{ext}'. "
            f"Allowed: {allowed}"
        )

    return ext


async def _read_and_validate_upload(
    file: UploadFile,
    allowed_extensions: set[str],
):
    ext = _validate_extension(
        file,
        allowed_extensions,
    )

    contents = await file.read()

    max_bytes = (
        _max_upload_mb()
        * 1024
        * 1024
    )

    if len(contents) > max_bytes:
        raise UploadValidationError(
            f"File exceeds the "
            f"{_max_upload_mb()}MB upload limit."
        )

    if not contents:
        raise UploadValidationError(
            "The uploaded file is empty."
        )

    return ext, contents


# ======================================================================
# R2 CLIENT
# ======================================================================

def _r2_client():
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
    if not path:
        return None, None

    value = str(path).strip()

    if not value.lower().startswith("r2://"):
        return None, None

    remainder = value[5:]

    if "/" not in remainder:
        return remainder, ""

    bucket, key = remainder.split(
        "/",
        1,
    )

    return bucket, key


# ======================================================================
# R2 PRESIGNED URL
# ======================================================================

def r2_presigned_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith(
        "https://"
    ) or value.startswith(
        "http://"
    ):
        return value

    bucket_from_path, key = _parse_r2_path(
        value
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

    clean = value.replace(
        "\\",
        "/",
    ).lstrip("/")

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
    if not path:
        return None

    value = str(path).strip()

    if not value:
        return None

    if value.startswith(
        "http://"
    ) or value.startswith(
        "https://"
    ):
        return value

    if value.startswith("r2://"):
        return r2_presigned_url(
            value,
            expires=expires,
        )

    clean = value.replace(
        "\\",
        "/",
    ).lstrip("/")

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
    ext, contents = await _read_and_validate_upload(
        file,
        allowed_extensions,
    )

    bucket = _r2_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    folder = (
        str(subfolder or "uploads")
        .strip("/")
    )

    key = (
        f"{folder}/"
        f"{uuid.uuid4().hex}"
        f"{ext}"
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
    Save a file locally.

    This remains the compatibility function used by
    existing BeatHub upload routes.

    Returned database path:

        merch/filename.jpg
        covers/filename.jpg
        tracks/filename.mp3
    """

    ext, contents = await _read_and_validate_upload(
        file,
        allowed_extensions,
    )

    media_root = _media_root()

    folder_name = (
        str(subfolder or "uploads")
        .strip("/")
        .replace("\\", "/")
    )

    if not folder_name:
        folder_name = "uploads"

    folder = (
        media_root
        / folder_name
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_name = (
        f"{uuid.uuid4().hex}"
        f"{ext}"
    )

    full_path = (
        folder
        / unique_name
    )

    with open(
        full_path,
        "wb",
    ) as output:
        output.write(contents)

    return (
        f"{folder_name}/"
        f"{unique_name}"
    )


# ======================================================================
# SMART UPLOAD
# ======================================================================

async def save_media_upload(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:
    """
    Storage-aware upload.

    If R2 is completely configured, upload to R2.
    Otherwise use local MEDIA_ROOT storage.

    This gives BeatHub a safe fallback instead of failing simply
    because R2 has not been configured yet.
    """

    if _r2_is_configured():
        return await save_upload_to_r2(
            file,
            subfolder,
            allowed_extensions,
        )

    return await save_upload(
        file,
        subfolder,
        allowed_extensions,
    )


# ======================================================================
# STORAGE EXISTENCE
# ======================================================================

def storage_exists(
    path: Optional[str],
) -> bool:
    if not path:
        return False

    value = str(path).strip()

    if not value:
        return False

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

    clean = value.replace(
        "\\",
        "/",
    ).lstrip("/")

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
        pass

    return False
