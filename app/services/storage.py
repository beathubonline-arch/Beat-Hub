"""
BeatHub storage abstraction.

Supports local MEDIA_ROOT storage and Cloudflare R2. R2 uploads stream the
FastAPI UploadFile directly to the S3-compatible API instead of reading the
entire audio file into Python memory first.
"""

import os
import uuid
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from app.config import settings

ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac"}
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

class UploadValidationError(Exception):
    pass

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
    return _setting(*names) or _env(*names)

def _r2_account_id():
    return _config("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")

def _r2_access_key():
    return _config("R2_ACCESS_KEY_ID", "R2_ACCESS_KEY", "AWS_ACCESS_KEY_ID")

def _r2_secret_key():
    return _config("R2_SECRET_ACCESS_KEY", "R2_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")

def _r2_bucket():
    return _config("R2_BUCKET_NAME", "R2_BUCKET", "AWS_S3_BUCKET")

def _r2_endpoint():
    endpoint = _config("R2_ENDPOINT", "R2_ENDPOINT_URL", "AWS_ENDPOINT_URL")
    if endpoint:
        return endpoint.rstrip("/")
    account_id = _r2_account_id()
    return f"https://{account_id}.r2.cloudflarestorage.com" if account_id else None

def _r2_is_configured() -> bool:
    return bool(_r2_endpoint() and _r2_access_key() and _r2_secret_key() and _r2_bucket())

def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()

def _r2_client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("R2 storage requires boto3. Add boto3 to requirements.txt.") from exc
    endpoint = _r2_endpoint()
    access_key = _r2_access_key()
    secret_key = _r2_secret_key()
    if not endpoint or not access_key or not secret_key:
        raise RuntimeError("R2 storage credentials are not fully configured.")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "adaptive"},
            max_pool_connections=20,
        ),
    )

def _parse_r2_path(path: str):
    if not path:
        return None, None
    value = str(path).strip()
    if not value.lower().startswith("r2://"):
        return None, None
    remainder = value[5:]
    if "/" not in remainder:
        return remainder, ""
    return remainder.split("/", 1)

def r2_presigned_url(path: Optional[str], expires: int = 3600) -> Optional[str]:
    if not path:
        return None
    value = str(path).strip()
    if not value:
        return None
    if value.startswith(("https://", "http://")):
        return value
    bucket_from_path, key = _parse_r2_path(value)
    if bucket_from_path is not None:
        bucket = bucket_from_path or _r2_bucket()
        if not bucket or not key:
            return None
        try:
            return _r2_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=max(1, min(int(expires), 604800)),
            )
        except Exception:
            return None
    clean = value.replace("\\", "/").lstrip("/")
    if clean.startswith("media/") or clean.startswith("static/"):
        return "/" + clean
    return "/media/" + clean

def r2_url(path: Optional[str], expires: int = 3600) -> Optional[str]:
    return r2_presigned_url(path, expires=expires)

def media_url(path: Optional[str], expires: int = 3600) -> Optional[str]:
    if not path:
        return None
    value = str(path).strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("r2://"):
        return r2_presigned_url(value, expires=expires)
    clean = value.replace("\\", "/").lstrip("/")
    return "/" + clean if clean.startswith("media/") else "/media/" + clean

def _validate(file: UploadFile, allowed_extensions: set[str]):
    if not file or not file.filename:
        raise UploadValidationError("No file was uploaded.")
    ext = _ext(file.filename)
    if ext not in allowed_extensions:
        raise UploadValidationError(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}"
        )

    stream = file.file
    try:
        position = stream.tell()
        stream.seek(0)
        header = stream.read(32)
        stream.seek(position)
    except (OSError, AttributeError):
        header = b""
        try:
            stream.seek(0)
        except Exception:
            pass

    if not _content_matches_extension(header, ext):
        raise UploadValidationError("The uploaded file content does not match its file type.")

    return ext


def _content_matches_extension(header: bytes, ext: str) -> bool:
    """Perform conservative magic-byte/container checks for supported media."""
    if not header:
        return False

    if ext == ".wav":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    if ext == ".flac":
        return header.startswith(b"fLaC")
    if ext == ".mp3":
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        )
    if ext == ".m4a":
        return len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12] in {
            b"M4A ", b"M4B ", b"isom", b"iso2", b"mp41", b"mp42", b"MSNV"
        }
    if ext in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"

    return False

def _max_upload_bytes() -> int:
    return int(settings.MAX_UPLOAD_MB) * 1024 * 1024

def _stream_size(file: UploadFile) -> int:
    """Get the already-buffered request size without copying the file."""
    stream = file.file
    try:
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        return int(size)
    except (OSError, AttributeError):
        return -1
    finally:
        try:
            stream.seek(0)
        except Exception:
            pass

async def save_upload_to_r2(file: UploadFile, subfolder: str, allowed_extensions: set[str]) -> str:
    ext = _validate(file, allowed_extensions)
    size = _stream_size(file)
    if size > _max_upload_bytes():
        raise UploadValidationError(f"File exceeds the {settings.MAX_UPLOAD_MB}MB upload limit.")
    bucket = _r2_bucket()
    if not bucket:
        raise RuntimeError("R2 bucket is not configured.")
    key = f"{subfolder.strip('/')}/{uuid.uuid4().hex}{ext}"
    content_type = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    try:
        client = _r2_client()
        file.file.seek(0)
        # boto3's managed transfer performs multipart uploads in worker threads
        # when appropriate. Explicit settings make large audio transfers use
        # parallel 8 MiB parts while the whole blocking SDK call remains off
        # FastAPI's event loop.
        from boto3.s3.transfer import TransferConfig

        transfer_config = TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
            max_concurrency=10,
            use_threads=True,
        )
        await asyncio.to_thread(
            client.upload_fileobj,
            file.file,
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
            Config=transfer_config,
        )
    except Exception as exc:
        raise RuntimeError(f"R2 upload failed: {exc}") from exc
    finally:
        try:
            file.file.seek(0)
        except Exception:
            pass
    return f"r2://{bucket}/{key}"

async def save_upload(file: UploadFile, subfolder: str, allowed_extensions: set[str]) -> str:
    if _r2_is_configured():
        return await save_upload_to_r2(file, subfolder, allowed_extensions)
    ext = _validate(file, allowed_extensions)
    size = _stream_size(file)
    if size > _max_upload_bytes():
        raise UploadValidationError(f"File exceeds the {settings.MAX_UPLOAD_MB}MB upload limit.")
    folder = Path(settings.MEDIA_ROOT) / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{ext}"
    full_path = folder / unique_name
    file.file.seek(0)
    try:
        def _write_local():
            with open(full_path, "wb") as output:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)

        await asyncio.to_thread(_write_local)
    finally:
        try:
            file.file.seek(0)
        except Exception:
            pass
    return f"{subfolder.strip('/')}/{unique_name}"

def storage_exists(path: Optional[str]) -> bool:
    if not path:
        return False
    value = str(path).strip()
    bucket, key = _parse_r2_path(value)
    if bucket is not None:
        if not bucket or not key:
            return False
        try:
            _r2_client().head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False
    clean = value.replace("\\", "/").lstrip("/")
    media_root = Path(settings.MEDIA_ROOT).resolve()
    if clean.startswith("media/"):
        clean = clean[6:]
    candidate = (media_root / clean).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return False
    return candidate.exists() and candidate.is_file()

def delete_r2_object(path: Optional[str]) -> bool:
    if not path:
        return False
    bucket, key = _parse_r2_path(str(path))
    if not bucket or not key:
        return False
    try:
        _r2_client().delete_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False

def delete_local_object(path: Optional[str]) -> bool:
    if not path:
        return False
    value = str(path).replace("\\", "/").lstrip("/")
    if value.startswith("media/"):
        value = value[6:]
    media_root = Path(settings.MEDIA_ROOT).resolve()
    target = (media_root / value).resolve()
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
