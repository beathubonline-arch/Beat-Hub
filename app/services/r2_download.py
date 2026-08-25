"""Secure buyer-only Cloudflare R2 download URL generation."""

from pathlib import Path
from typing import Optional

from app.config import settings


def _r2_endpoint() -> str:
    account_id = str(getattr(settings, "R2_ACCOUNT_ID", "") or "").strip()
    if not account_id:
        return ""
    return f"https://{account_id}.r2.cloudflarestorage.com"


def _parse_object(path: str):
    value = str(path or "").strip()
    lower = value.lower()

    if lower.startswith("r2://"):
        value = value[5:]
    elif lower.startswith("s3://"):
        value = value[5:]
    else:
        return None, None

    if "/" not in value:
        return value, ""

    bucket, key = value.split("/", 1)
    return bucket, key


def r2_download_url(
    path: str,
    filename: str,
    expires: int = 900,
) -> Optional[str]:
    """Return a short-lived signed GET URL forcing an attachment download."""
    bucket, key = _parse_object(path)

    if not bucket or not key:
        return None

    endpoint = _r2_endpoint()
    access_key = str(getattr(settings, "R2_ACCESS_KEY_ID", "") or "").strip()
    secret_key = str(getattr(settings, "R2_SECRET_ACCESS_KEY", "") or "").strip()

    if not endpoint or not access_key or not secret_key:
        return None

    try:
        import boto3

        suffix = Path(filename).suffix.lower()
        content_type = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
        }.get(suffix, "application/octet-stream")

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": content_type,
                "ResponseCacheControl": "private, no-store",
            },
            ExpiresIn=max(60, min(int(expires), 604800)),
        )
    except Exception:
        return None
