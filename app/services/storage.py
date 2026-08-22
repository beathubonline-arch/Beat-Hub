import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.config import settings


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


class UploadValidationError(Exception):
    pass


def _setting(*names: str) -> Optional[str]:
    for name in names:
        try:
            value = getattr(settings, name, None)
        except Exception:
            value = None

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _config(*names: str) -> Optional[str]:
    value = _setting(*names)

    if value:
        return value

    return _env(*names)


def _media_root() -> Path:
    configured = _config(
        "MEDIA_ROOT",
        "MEDIA_DIR",
        "UPLOAD_DIR",
    )

    if configured:
        root = Path(configured)
    else:
        root = Path(
            os.getenv(
                "RENDER_DISK_MOUNT_PATH",
                "media",
            )
        )

    root = root.expanduser().resolve()
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def _max_upload_bytes() -> int:
    raw = _config(
        "MAX_UPLOAD_MB",
        "MAX_UPLOAD_SIZE_MB",
    )

    try:
        megabytes = float(
            raw if raw is not None else 50
        )
    except (
        TypeError,
        ValueError,
    ):
        megabytes = 50

    megabytes = max(
        1,
        min(
            megabytes,
            2048,
        ),
    )

    return int(
        megabytes
        * 1024
        * 1024
    )


def _ext(filename: str) -> str:
    return Path(
        filename or ""
    ).suffix.lower()


def _safe_subfolder(
    subfolder: str,
) -> str:
    value = str(
        subfolder or "uploads"
    ).replace(
        "\\",
        "/",
    ).strip("/")

    parts = []

    for part in value.split("/"):
        part = part.strip()

        if not part:
            continue

        if part in {".", ".."}:
            continue

        cleaned = "".join(
            char
            for char in part
            if char.isalnum()
            or char in {
                "-",
                "_",
            }
        )

        if cleaned:
            parts.append(cleaned)

    return "/".join(parts) or "uploads"


def _validate_upload(
    file: UploadFile,
    allowed_extensions: set[str],
):
    if not file:
        raise UploadValidationError(
            "No file was uploaded."
        )

    if not file.filename:
        raise UploadValidationError(
            "No file was selected."
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
            f"Unsupported file type '{extension}'. "
            f"Allowed types: {allowed}"
        )

    return extension


def _safe_object_name(
    extension: str,
) -> str:
    return (
        f"{uuid.uuid4().hex}"
        f"{extension}"
    )


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
        "R2_ENDPOINT_URL",
        "R2_ENDPOINT",
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


def _parse_r2_path(
    path: str,
):
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

    if "/" not in remainder:
        return remainder, ""

    bucket, key = remainder.split(
        "/",
        1,
    )

    return bucket, key


def r2_presigned_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    if not path:
        return None

    value = str(
        path
    ).strip()

    if not value:
        return None

    if value.startswith(
        "https://"
    ) or value.startswith(
        "http://"
    ):
        return value

    bucket_from_path, key = (
        _parse_r2_path(
            value
        )
    )

    if bucket_from_path is not None:
        bucket = (
            bucket_from_path
            or _r2_bucket()
        )

        if not bucket or not key:
            return None

        if not _r2_is_configured():
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

    return media_url(
        value
    )


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
    if not path:
        return None

    value = str(
        path
    ).strip()

    if not value:
        return None

    if value.startswith(
        "https://"
    ) or value.startswith(
        "http://"
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
        .replace(
            "\\",
            "/",
        )
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


async def save_upload_to_r2(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:
    extension = _validate_upload(
        file,
        allowed_extensions,
    )

    contents = await file.read()

    if len(contents) > _max_upload_bytes():
        max_mb = round(
            _max_upload_bytes()
            / 1024
            / 1024,
            2,
        )

        raise UploadValidationError(
            f"File exceeds the "
            f"{max_mb}MB upload limit."
        )

    bucket = _r2_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    if not _r2_is_configured():
        raise RuntimeError(
            "R2 storage is not fully configured."
        )

    folder = _safe_subfolder(
        subfolder
    )

    key = (
        f"{folder}/"
        f"{_safe_object_name(extension)}"
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
        f"r2://"
        f"{bucket}/"
        f"{key}"
    )


async def save_upload(
    file: UploadFile,
    subfolder: str,
    allowed_extensions: set[str],
) -> str:
    extension = _validate_upload(
        file,
        allowed_extensions,
    )

    contents = await file.read()

    if len(contents) > _max_upload_bytes():
        max_mb = round(
            _max_upload_bytes()
            / 1024
            / 1024,
            2,
        )

        raise UploadValidationError(
            f"File exceeds the "
            f"{max_mb}MB upload limit."
        )

    folder_name = _safe_subfolder(
        subfolder
    )

    media_root = _media_root()

    folder = (
        media_root
        / Path(
            folder_name
        )
    ).resolve()

    try:
        folder.relative_to(
            media_root
        )
    except ValueError as exc:
        raise UploadValidationError(
            "Invalid upload folder."
        ) from exc

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = _safe_object_name(
        extension
    )

    full_path = (
        folder
        / filename
    ).resolve()

    try:
        full_path.relative_to(
            media_root
        )
    except ValueError as exc:
        raise UploadValidationError(
            "Invalid upload destination."
        ) from exc

    try:
        with open(
            full_path,
            "wb",
        ) as output:
            output.write(
                contents
            )
    except OSError as exc:
        raise RuntimeError(
            f"Unable to save uploaded file: {exc}"
        ) from exc

    return (
        f"{folder_name}/"
        f"{filename}"
    )


def storage_exists(
    path: Optional[str],
) -> bool:
    if not path:
        return False

    value = str(
        path
    ).strip()

    if not value:
        return False

    bucket, key = _parse_r2_path(
        value
    )

    if bucket is not None:
        if not bucket or not key:
            return False

        if not _r2_is_configured():
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

    clean = (
        value
        .replace(
            "\\",
            "/",
        )
        .lstrip("/")
    )

    if clean.startswith(
        "media/"
    ):
        clean = clean[6:]

    media_root = _media_root()

    candidate = (
        media_root
        / Path(clean)
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

    if not _r2_is_configured():
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


def delete_local_object(
    path: Optional[str],
) -> bool:
    if not path:
        return False

    value = (
        str(path)
        .replace(
            "\\",
            "/",
        )
        .lstrip("/")
    )

    if value.startswith(
        "media/"
    ):
        value = value[6:]

    media_root = _media_root()

    target = (
        media_root
        / Path(value)
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

    except OSError:
        return False

    return False


def delete_storage_object(
    path: Optional[str],
) -> bool:
    if not path:
        return False

    value = str(
        path
    ).strip()

    if value.startswith(
        "r2://"
    ):
        return delete_r2_object(
            value
        )

    return delete_local_object(
        value
    )
