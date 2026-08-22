import os
import uuid
from pathlib import Path
from typing import Optional, Set

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

# CONFIG HELPERS

# ======================================================================

def _env(*names: str) -> Optional[str]:
"""Return the first non-empty environment variable."""
for name in names:
value = os.getenv(name)
if value is not None and str(value).strip():
return str(value).strip()
return None

def _setting(*names: str) -> Optional[str]:
"""Return the first non-empty setting attribute."""
for name in names:
try:
value = getattr(settings, name, None)
except Exception:
value = None

```
    if value is not None and str(value).strip():
        return str(value).strip()

return None
```

def _config(*names: str) -> Optional[str]:
"""Check application settings first, then environment variables."""
value = _setting(*names)

```
if value:
    return value

return _env(*names)
```

# ======================================================================

# LOCAL MEDIA CONFIGURATION

# ======================================================================

def _media_root() -> Path:
"""
Resolve the local media directory.

```
Supports:
- settings.MEDIA_ROOT
- MEDIA_ROOT environment variable
- MEDIA_DIRECTORY environment variable
- MEDIA_DIR environment variable

Falls back safely to ./media.
"""
configured = _config(
    "MEDIA_ROOT",
    "MEDIA_DIRECTORY",
    "MEDIA_DIR",
)

if configured:
    return Path(configured).expanduser().resolve()

return (Path.cwd() / "media").resolve()
```

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

```
if endpoint:
    return endpoint.rstrip("/")

account_id = _r2_account_id()

if account_id:
    return (
        f"https://{account_id}.r2.cloudflarestorage.com"
    )

return None
```

def _r2_is_configured() -> bool:
return bool(
_r2_endpoint()
and _r2_access_key()
and _r2_secret_key()
and _r2_bucket()
)

# ======================================================================

# UPLOAD LIMIT

# ======================================================================

def _max_upload_mb() -> int:
try:
value = getattr(settings, "MAX_UPLOAD_MB", 50)
value = int(value)
return max(1, value)
except Exception:
return 50

def _max_upload_bytes() -> int:
return _max_upload_mb() * 1024 * 1024

# ======================================================================

# FILE HELPERS

# ======================================================================

def _ext(filename: str) -> str:
return os.path.splitext(filename or "")[1].lower()

def _safe_subfolder(subfolder: str) -> str:
"""
Prevent path traversal while preserving nested storage folders.
"""
value = str(subfolder or "").replace("\", "/").strip("/")

```
parts = []

for part in value.split("/"):
    part = part.strip()

    if not part:
        continue

    if part in {".", ".."}:
        continue

    parts.append(part)

return "/".join(parts)
```

def _validate_extension(
filename: str,
allowed_extensions: Set[str],
) -> str:
if not filename:
raise UploadValidationError(
"No file was uploaded."
)

```
ext = _ext(filename)

if ext not in allowed_extensions:
    allowed = ", ".join(
        sorted(allowed_extensions)
    )

    raise UploadValidationError(
        f"Unsupported file type '{ext or 'unknown'}'. "
        f"Allowed: {allowed}"
    )

return ext
```

async def _read_and_validate_upload(
file: UploadFile,
allowed_extensions: Set[str],
) -> tuple[bytes, str]:
if not file or not file.filename:
raise UploadValidationError(
"No file was uploaded."
)

```
ext = _validate_extension(
    file.filename,
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

return contents, ext
```

# ======================================================================

# R2 CLIENT

# ======================================================================

def _r2_client():
"""
Lazily create the Cloudflare R2 S3-compatible client.
"""

```
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
```

# ======================================================================

# R2 PATH PARSER

# ======================================================================

def _parse_r2_path(path: str):
"""
Convert:

```
    r2://bucket/folder/file.png

into:

    ("bucket", "folder/file.png")
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
```

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
- HTTP/HTTPS URLs
- local media files
"""

```
if not path:
    return None

value = str(path).strip()

if not value:
    return None

# Already a normal URL.
if value.startswith("https://") or value.startswith("http://"):
    return value

# R2 object.
bucket_from_path, key = _parse_r2_path(value)

if bucket_from_path is not None:
    bucket = bucket_from_path or _r2_bucket()

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
                min(int(expires), 604800),
            ),
        )

    except Exception:
        return None

# Local media.
clean = value.replace("\\", "/").lstrip("/")

if clean.startswith("media/"):
    return "/" + clean

if clean.startswith("static/"):
    return "/" + clean

return "/media/" + clean
```

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
"""
Return a browser-accessible URL for either:
- R2 object
- HTTP/HTTPS URL
- local media file
"""

```
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
```

# ======================================================================

# SAVE UPLOAD TO R2

# ======================================================================

async def save_upload_to_r2(
file: UploadFile,
subfolder: str,
allowed_extensions: Set[str],
) -> str:
"""
Upload a file to Cloudflare R2.

```
Returns:

    r2://bucket/key
"""

contents, ext = await _read_and_validate_upload(
    file,
    allowed_extensions,
)

bucket = _r2_bucket()

if not bucket:
    raise RuntimeError(
        "R2 bucket is not configured."
    )

folder = _safe_subfolder(subfolder)

if folder:
    key = (
        f"{folder}/"
        f"{uuid.uuid4().hex}{ext}"
    )
else:
    key = (
        f"{uuid.uuid4().hex}{ext}"
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
```

# ======================================================================

# LOCAL UPLOAD

# ======================================================================

async def save_upload(
file: UploadFile,
subfolder: str,
allowed_extensions: Set[str],
) -> str:
"""
Save an uploaded file locally.

```
Existing callers can continue using:

    await save_upload(...)

Returns a relative media path such as:

    merch/abc123.png
"""

contents, ext = await _read_and_validate_upload(
    file,
    allowed_extensions,
)

media_root = _media_root()

folder_name = _safe_subfolder(subfolder)

if folder_name:
    folder = (
        media_root
        / Path(folder_name)
    )
else:
    folder = media_root

# Ensure the destination remains inside MEDIA_ROOT.
folder = folder.resolve()

try:
    folder.relative_to(media_root)
except ValueError as exc:
    raise UploadValidationError(
        "Invalid upload destination."
    ) from exc

folder.mkdir(
    parents=True,
    exist_ok=True,
)

unique_name = (
    f"{uuid.uuid4().hex}{ext}"
)

full_path = (
    folder / unique_name
).resolve()

try:
    full_path.relative_to(media_root)
except ValueError as exc:
    raise UploadValidationError(
        "Invalid upload destination."
    ) from exc

try:
    with open(
        full_path,
        "wb",
    ) as output:
        output.write(contents)

except OSError as exc:
    raise RuntimeError(
        f"Could not save uploaded file: {exc}"
    ) from exc

if folder_name:
    return (
        f"{folder_name}/"
        f"{unique_name}"
    )

return unique_name
```

# ======================================================================

# SMART UPLOAD

# ======================================================================

async def save_media_upload(
file: UploadFile,
subfolder: str,
allowed_extensions: Set[str],
) -> str:
"""
Storage-aware upload.

```
If R2 is fully configured, upload to R2.
Otherwise fall back to local storage.

This gives BeatHub a safe local fallback while allowing
production Render deployments to use R2.
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
```

# ======================================================================

# STORAGE EXISTENCE CHECK

# ======================================================================

def storage_exists(
path: Optional[str],
) -> bool:
"""
Check whether a stored object exists.
Supports both R2 and local storage.
"""

```
if not path:
    return False

value = str(path).strip()

if not value:
    return False

# R2.
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

# Local.
clean = value.replace(
    "\\",
    "/",
).lstrip("/")

media_root = _media_root()

if clean.startswith("media/"):
    clean = clean[6:]

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
```

# ======================================================================

# DELETE R2 OBJECT

# ======================================================================

def delete_r2_object(
path: Optional[str],
) -> bool:
if not path:
return False

```
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
```

# ======================================================================

# DELETE LOCAL OBJECT

# ======================================================================

def delete_local_object(
path: Optional[str],
) -> bool:
if not path:
return False

```
value = str(path).replace(
    "\\",
    "/",
).lstrip("/")

if value.startswith("media/"):
    value = value[6:]

media_root = _media_root()

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

except OSError:
    pass

return False
```

# ======================================================================

# DELETE GENERIC OBJECT

# ======================================================================

def delete_storage_object(
path: Optional[str],
) -> bool:
"""
Delete either an R2 object or local media file.
"""

```
if not path:
    return False

value = str(path).strip()

if value.startswith("r2://"):
    return delete_r2_object(value)

return delete_local_object(value)
```
