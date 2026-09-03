from decimal import Decimal, InvalidOperation
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track, TrackContentType
from app.models.user import User
from app.services.pricing import normalize_currency
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    _r2_is_configured,
    save_upload,
    save_upload_to_r2,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["music-publishing"])
templates = Jinja2Templates(directory="app/templates")


def _error(request: Request, user: User, message: str):
    return templates.TemplateResponse(
        request,
        "upload_track.html",
        {
            "request": request,
            "current_user": user,
            "current_year": 2026,
            "error": message,
        },
        status_code=400,
    )


@router.get("/dashboard/upload")
def upload_page(request: Request, user: User = Depends(require_creator)):
    return templates.TemplateResponse(
        request,
        "upload_track.html",
        {"request": request, "current_user": user, "current_year": 2026},
    )


def _form_values(form, name: str) -> List[str]:
    return [str(value or "") for value in form.getlist(name)]


def _form_files(form, name: str) -> List[UploadFile]:
    return [
        value
        for value in form.getlist(name)
        if isinstance(value, UploadFile) and value.filename
    ]


@router.post("/dashboard/upload")
async def publish_tracks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    """Publish one or more music items from the dynamic multipart form."""
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    form = await request.form()
    titles = _form_values(form, "titles")
    descriptions = _form_values(form, "descriptions")
    genres = _form_values(form, "genres")
    bpms = _form_values(form, "bpms")
    tags_list = _form_values(form, "tags_list")
    prices = _form_values(form, "prices")
    currencies = _form_values(form, "currencies")
    sales_models = _form_values(form, "sales_models")
    content_types = _form_values(form, "content_types")
    audio_files = _form_files(form, "audio_files")
    cover_files = _form_files(form, "cover_files")

    if not audio_files:
        return _error(request, user, "Please select at least one audio file.")

    expected = len(audio_files)
    fields = {
        "titles": titles,
        "descriptions": descriptions,
        "genres": genres,
        "tags": tags_list,
        "prices": prices,
        "currencies": currencies,
        "sales_models": sales_models,
        "content_types": content_types,
    }
    mismatches = {
        name: len(values)
        for name, values in fields.items()
        if len(values) != expected
    }
    if mismatches:
        details = ", ".join(f"{name}={count}" for name, count in mismatches.items())
        return _error(
            request,
            user,
            f"Upload form data is incomplete ({details}; audio_files={expected}). "
            "Please refresh the page and try again.",
        )

    if bpms and len(bpms) != expected:
        return _error(
            request,
            user,
            "The BPM fields do not match the selected audio files. Please refresh and try again.",
        )

    created = []
    try:
        for i, audio_file in enumerate(audio_files):
            title = titles[i].strip()
            if not title:
                return _error(request, user, "Every upload needs a title.")

            content_raw = content_types[i].strip().lower()
            if content_raw not in {
                TrackContentType.BEAT.value,
                TrackContentType.TRACK.value,
            }:
                return _error(request, user, f"Choose Beat or Track for '{title}'.")

            try:
                currency = normalize_currency(currencies[i])
            except ValueError as exc:
                return _error(request, user, f"Currency for '{title}' is invalid: {exc}")

            bpm_raw = bpms[i].strip() if bpms else ""
            bpm_value = None
            if bpm_raw:
                if not bpm_raw.isdigit() or not 1 <= int(bpm_raw) <= 999:
                    return _error(
                        request,
                        user,
                        f"BPM for '{title}' must be a whole number between 1 and 999.",
                    )
                bpm_value = int(bpm_raw)

            try:
                price_value = Decimal(prices[i].strip() or "0")
                if not price_value.is_finite() or price_value < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                return _error(request, user, f"Price for '{title}' is invalid.")

            model_raw = sales_models[i].strip().lower() or "non_exclusive"
            if model_raw not in {"exclusive", "non_exclusive"}:
                return _error(request, user, f"Sales model for '{title}' is invalid.")
            sales_model = (
                SalesModel.EXCLUSIVE
                if model_raw == "exclusive"
                else SalesModel.NON_EXCLUSIVE
            )

            if _r2_is_configured():
                audio_path = await save_upload_to_r2(
                    audio_file, "audio", ALLOWED_AUDIO_EXT
                )
            else:
                audio_path = await save_upload(
                    audio_file, "audio", ALLOWED_AUDIO_EXT
                )

            cover_path = None
            if i < len(cover_files):
                if _r2_is_configured():
                    cover_path = await save_upload_to_r2(
                        cover_files[i], "covers", ALLOWED_IMAGE_EXT
                    )
                else:
                    cover_path = await save_upload(
                        cover_files[i], "covers", ALLOWED_IMAGE_EXT
                    )

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=unique_slug(db, Track, title, "track"),
                description=descriptions[i].strip() or None,
                genre=genres[i].strip() or None,
                bpm=bpm_value,
                tags=tags_list[i].strip() or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
                currency=currency,
                sales_model=sales_model,
                content_type=content_raw,
                is_published=True,
            )
            db.add(track)
            created.append(track)

        db.commit()
    except UploadValidationError as exc:
        db.rollback()
        return _error(request, user, str(exc))
    except Exception:
        db.rollback()
        raise

    item_word = "item" if len(created) == 1 else "items"
    message = f"{len(created)} {item_word} published successfully."
    return RedirectResponse(
        url="/dashboard?success=" + message.replace(" ", "%20"),
        status_code=303,
    )
