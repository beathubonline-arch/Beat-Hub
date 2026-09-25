"""Build a buyer-only post-purchase Release Kit archive."""

from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings
from app.services.storage import _parse_r2_path, _r2_client


_AUDIO_EXTENSIONS = {".mp3", ".mpeg", ".mpga", ".wav", ".wave", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".aiff", ".aif", ".wma"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class ReleaseKitUnavailable(RuntimeError):
    """Raised when a protected asset cannot be added to the kit."""


def safe_name(value: str, fallback: str = "BeatHub_Release") -> str:
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "_" for char in str(value or "")).strip(" .")
    return cleaned[:120] or fallback


def _local_media_path(stored_path: str) -> Path | None:
    value = str(stored_path or "").strip()
    if not value or value.lower().startswith(("http://", "https://", "r2://", "s3://")):
        return None
    stored = Path(value)
    media_root = Path(getattr(settings, "MEDIA_ROOT", None) or "media").expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    media_root = media_root.resolve()
    candidates = [stored.resolve()] if stored.is_absolute() else [(Path.cwd() / stored).resolve(), (media_root / stored).resolve()]
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


def _asset_suffix(stored_path: str, allowed: set[str], fallback: str) -> str:
    suffix = Path(urlsplit(str(stored_path or "")).path).suffix.lower()
    return suffix if suffix in allowed else fallback


def _download_r2(stored_path: str, destination: Path) -> None:
    normalized = "r2://" + str(stored_path)[5:] if str(stored_path).lower().startswith("s3://") else str(stored_path)
    bucket, key = _parse_r2_path(normalized)
    if not bucket or not key:
        raise ReleaseKitUnavailable("The stored release asset is invalid.")
    try:
        _r2_client().download_file(str(bucket), str(key), str(destination))
    except Exception as exc:
        raise ReleaseKitUnavailable("A protected release asset could not be prepared.") from exc


def _copy_asset(stored_path: str, destination: Path) -> None:
    value = str(stored_path or "").strip()
    if value.lower().startswith(("r2://", "s3://")):
        _download_r2(value, destination)
        return
    if value.lower().startswith(("http://", "https://")):
        # Do not turn database-controlled URLs into a server-side request primitive.
        raise ReleaseKitUnavailable("This legacy release asset must be migrated to secure storage before it can be packaged.")
    local = _local_media_path(value)
    if local is None:
        raise ReleaseKitUnavailable("A protected release asset is currently unavailable.")
    shutil.copyfile(local, destination)


def _metadata_csv(track, producer_name: str) -> str:
    output = io.StringIO(newline="")
    fields = [
        "title", "primary_artist", "featured_artists", "producer", "genre", "bpm",
        "explicit_content", "release_date", "isrc", "upc", "copyright_holder", "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerow({
        "title": getattr(track, "title", "") or "",
        "primary_artist": "",
        "featured_artists": "",
        "producer": producer_name,
        "genre": getattr(track, "genre", "") or "",
        "bpm": getattr(track, "bpm", "") or "",
        "explicit_content": "",
        "release_date": "",
        "isrc": "",
        "upc": "",
        "copyright_holder": "",
        "notes": "Complete the blank fields and confirm distributor requirements before submission.",
    })
    return output.getvalue()


def build_purchase_release_kit(*, license_record, order, track, buyer) -> Path:
    """Create a temporary ZIP for a verified completed purchase."""
    producer = getattr(getattr(track, "creator_profile", None), "stage_name", None) or "BeatHub Creator"
    title = safe_name(getattr(track, "title", ""))
    order_number = str(getattr(order, "order_number", "") or "")
    license_type = str(getattr(order, "sales_model_at_purchase", "") or "non_exclusive").replace("_", " ").title()
    completed_at = getattr(order, "completed_at", None) or getattr(license_record, "granted_at", None)
    completed_text = completed_at.isoformat() if completed_at else "Recorded by BeatHub"
    buyer_email = str(getattr(buyer, "email", "") or "")

    work_dir = Path(tempfile.mkdtemp(prefix="beathub-release-kit-"))
    archive_path = work_dir / f"{title}_Release_Kit.zip"
    audio_path = str(getattr(track, "audio_file_path", "") or "")
    audio_suffix = _asset_suffix(audio_path, _AUDIO_EXTENSIONS, ".mp3")
    audio_temp = work_dir / f"{title}{audio_suffix}"

    try:
        _copy_asset(audio_path, audio_temp)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            # Audio is already compressed in common delivery formats; storing it
            # avoids wasting CPU and memory on a large, low-gain recompression.
            archive.write(audio_temp, arcname=f"01_Audio/{audio_temp.name}", compress_type=zipfile.ZIP_STORED)

            cover_path = str(getattr(track, "cover_art_path", "") or "")
            if cover_path:
                cover_suffix = _asset_suffix(cover_path, _IMAGE_EXTENSIONS, ".jpg")
                cover_temp = work_dir / f"{title}_Cover{cover_suffix}"
                try:
                    _copy_asset(cover_path, cover_temp)
                    archive.write(cover_temp, arcname=f"02_Artwork/{cover_temp.name}")
                except ReleaseKitUnavailable:
                    pass

            certificate = f"""BEATHUB LICENCE CERTIFICATE

Track: {getattr(track, 'title', '')}
Producer: {producer}
Buyer account: {buyer_email}
Licence type: {license_type}
Licence ID: {getattr(license_record, 'id', '')}
Order number: {order_number}
Payment status: Completed
Amount paid: {getattr(order, 'gross_amount', '')} {getattr(order, 'currency', 'KES')}
Granted at: {completed_text}

This certificate records the licence granted through the completed BeatHub order above.
The rights, restrictions and permitted uses are governed by the licence shown at checkout and
the BeatHub terms accepted for this purchase. This certificate is not a transfer of rights beyond
that recorded licence. Keep this file and the order number with your release records.

Terms: https://mybeathub.com/terms
Support: support@mybeathub.com
"""
            credits = f"""RELEASE CREDITS

Track / beat title: {getattr(track, 'title', '')}
Produced by: {producer}
BeatHub order: {order_number}

Before distribution, add the performing artist, writers, featured artists, copyright holders,
ISRC/UPC and any other credits required by your distributor. Do not remove the producer credit.
"""
            artwork = """ARTWORK DELIVERY GUIDE

Recommended master artwork: 3000 x 3000 px, square, JPG or PNG, RGB colour.
Keep essential text and faces away from the outer edge. Avoid blurry images, URLs, pricing,
platform logos, misleading claims, or content you do not have permission to use.
Distributor requirements vary, so confirm the current rules of your chosen distributor.
"""
            readme = f"""{getattr(track, 'title', '')} — BEATHUB RELEASE KIT

This package was generated after BeatHub confirmed payment for order {order_number}.

01_Audio: purchased master audio
02_Artwork: available cover artwork and delivery guidance
03_Licence: purchase licence certificate
04_Metadata: pre-filled release metadata and credits

Complete every blank metadata field and verify all names, rights and distributor requirements
before releasing the song. BeatHub does not submit this package to a distributor automatically.
"""
            metadata_json = {
                "title": getattr(track, "title", "") or "",
                "producer": producer,
                "genre": getattr(track, "genre", "") or "",
                "bpm": getattr(track, "bpm", None),
                "beat_license": license_type,
                "beathub_order_number": order_number,
                "primary_artist": "",
                "featured_artists": [],
                "release_date": "",
                "isrc": "",
                "upc": "",
                "explicit_content": "",
                "copyright_holder": "",
            }
            archive.writestr("README.txt", readme)
            archive.writestr("02_Artwork/ARTWORK_GUIDE.txt", artwork)
            archive.writestr("03_Licence/BEATHUB_LICENCE_CERTIFICATE.txt", certificate)
            archive.writestr("04_Metadata/RELEASE_CREDITS.txt", credits)
            archive.writestr("04_Metadata/release_metadata.csv", _metadata_csv(track, producer))
            archive.writestr("04_Metadata/release_metadata.json", json.dumps(metadata_json, indent=2, ensure_ascii=False))
        return archive_path
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def remove_release_kit(path: Path) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)
