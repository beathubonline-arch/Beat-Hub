"""Small, server-side Higgsfield client for BeatHub creator cover artwork."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from fastapi import UploadFile

from app.config import settings
from app.services.storage import ALLOWED_IMAGE_EXT, media_url, save_upload


class HiggsfieldError(RuntimeError):
    """A safe, creator-facing generation error."""


@dataclass(frozen=True)
class GeneratedCover:
    request_id: str
    storage_path: str
    preview_url: str


def _credential() -> str:
    """Accept Higgsfield's combined key or the separately stored key pair."""
    combined = str(getattr(settings, "HIGGSFIELD_API_KEY", "") or "").strip()
    if combined:
        return combined
    key_id = str(getattr(settings, "HIGGSFIELD_API_KEY_ID", "") or "").strip()
    secret = str(getattr(settings, "HIGGSFIELD_API_KEY_SECRET", "") or "").strip()
    return f"{key_id}:{secret}" if key_id and secret else ""


def is_configured() -> bool:
    credential = _credential()
    return bool(credential and ":" in credential and all(credential.split(":", 1)))


def _headers() -> dict[str, str]:
    credential = _credential()
    if not credential or ":" not in credential or not all(credential.split(":", 1)):
        raise HiggsfieldError("AI Cover Studio is not configured yet.")
    return {"Authorization": f"Key {credential}", "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    base = str(getattr(settings, "HIGGSFIELD_API_BASE_URL", "https://api.higgsfield.ai") or "").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _safe_status_url(value: str) -> str:
    """Only follow request URLs returned by the configured Higgsfield API."""
    candidate = urlparse(str(value or ""))
    base = urlparse(_api_url("/"))
    if candidate.scheme != "https" or candidate.netloc.lower() != base.netloc.lower():
        raise HiggsfieldError("Higgsfield returned an invalid request status URL.")
    return candidate.geturl()


def _image_extension(content_type: str, body: bytes) -> str:
    value = (content_type or "").split(";", 1)[0].strip().lower()
    if value in {"image/jpeg", "image/jpg"} and body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if value == "image/png" and body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if value == "image/webp" and len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    raise HiggsfieldError("Higgsfield returned an unsupported image file.")


def _response_error(response: httpx.Response) -> HiggsfieldError:
    if response.status_code == 401:
        return HiggsfieldError("The Higgsfield API credentials are invalid.")
    if response.status_code == 403:
        return HiggsfieldError("The Higgsfield API account has insufficient credits or access.")
    if response.status_code == 429:
        return HiggsfieldError("Higgsfield is busy. Please wait and try again.")
    return HiggsfieldError("Cover generation could not be completed right now.")


async def generate_and_store_cover(prompt: str) -> GeneratedCover:
    """Generate a square image, copy it to BeatHub storage, and return it."""
    clean_prompt = " ".join(str(prompt or "").split())[:1800]
    if len(clean_prompt) < 12:
        raise HiggsfieldError("Describe the cover you want in a little more detail.")

    model = str(getattr(settings, "HIGGSFIELD_IMAGE_MODEL", "marketing-studio/image") or "").strip("/")
    resolution = str(getattr(settings, "HIGGSFIELD_IMAGE_RESOLUTION", "1k") or "1k").lower()
    if resolution not in {"1k", "2k", "4k"}:
        resolution = "1k"

    timeout = httpx.Timeout(30.0, connect=8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            submitted = await client.post(
                _api_url(model),
                headers=_headers(),
                json={
                    "prompt": clean_prompt,
                    "quality": "low",
                    "moderation": "auto",
                    "resolution": resolution,
                    "aspect_ratio": "1:1",
                    "enhance_prompt": False,
                },
            )
            if submitted.status_code >= 400:
                raise _response_error(submitted)
            payload = submitted.json()
            request_id = str(payload.get("request_id") or "").strip()
            status_url = _safe_status_url(payload.get("status_url"))
            if not request_id:
                raise HiggsfieldError("Higgsfield did not return a generation request ID.")

            result = payload
            for attempt in range(45):
                status = str(result.get("status") or "").lower()
                if status in {"completed", "failed", "nsfw", "canceled"}:
                    break
                await asyncio.sleep(min(2 + attempt // 8, 6))
                polled = await client.get(status_url, headers=_headers())
                if polled.status_code >= 400:
                    raise _response_error(polled)
                result = polled.json()
            else:
                raise HiggsfieldError("The cover is taking too long. Please try again shortly.")

            status = str(result.get("status") or "").lower()
            if status == "nsfw":
                raise HiggsfieldError("That request was blocked by the image safety filter.")
            if status != "completed":
                raise HiggsfieldError("Higgsfield could not generate this cover. Try a different description.")

            images = result.get("images") or []
            image_url = str((images[0] if images else {}).get("url") or "").strip()
            parsed_image = urlparse(image_url)
            if parsed_image.scheme != "https" or not parsed_image.netloc:
                raise HiggsfieldError("Higgsfield returned an invalid image URL.")

            image_response = await client.get(image_url)
            if image_response.status_code >= 400:
                raise HiggsfieldError("The generated cover could not be downloaded.")
            body = image_response.content
            if not body or len(body) > 15 * 1024 * 1024:
                raise HiggsfieldError("The generated cover file is empty or too large.")
            extension = _image_extension(image_response.headers.get("content-type", ""), body)
    except HiggsfieldError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HiggsfieldError("Higgsfield is temporarily unavailable. Please try again.") from exc

    upload = UploadFile(filename=f"higgsfield-{request_id}{extension}", file=io.BytesIO(body))
    storage_path = await save_upload(upload, "ai-covers", ALLOWED_IMAGE_EXT)
    preview_url = media_url(storage_path, expires=3600) or ""
    if not preview_url:
        raise HiggsfieldError("The generated cover could not be saved.")
    return GeneratedCover(request_id=request_id, storage_path=storage_path, preview_url=preview_url)
