from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track, TrackContentType
from app.models.user import User
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


@router.post("/dashboard/upload")
async def publish_tracks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    titles: List[str] = Form(...),
    descriptions: List[str] = Form(...),
    genres: List[str] = Form(...),
    bpms: Optional[List[str]] = Form(None),
    tags_list: List[str] = Form(...),
    prices: List[str] = Form(...),
    sales_models: List[str] = Form(...),
    content_types: List[str] = Form(...),
    audio_files: List[UploadFile] = File(...),
    cover_files: List[Optional[UploadFile]] = File(None),
):
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    if not titles or not audio_files:
        return _error(request, user, "At least one track with an audio file is required.")

    expected = len(audio_files)
    lengths = {
        "titles": len(titles),
        "descriptions": len(descriptions),
        "genres": len(genres),
        "tags": len(tags_list),
        "prices": len(prices),
        "sales_models": len(sales_models),
        "content_types": len(content_types),
    }
    if any(value != expected for value in lengths.values()):
        return _error(request, user, "Each uploaded item must include its content type and required details.")

    bpms = bpms or []
    if len(bpms) not in (0, expected):
        return _error(request, user, "Track BPM fields do not match the uploaded audio files.")

    created = []

    try:
        for i, audio_file in enumerate(audio_files):
            title = (titles[i] or "").strip()
            if not title:
                return _error(request, user, "Every upload needs a title.")

            content_raw = (content_types[i] or "").strip().lower()
            if content_raw not in {TrackContentType.BEAT.value, TrackContentType.TRACK.value}:
                return _error(request, user, f"Choose Beat or Track for '{title}'.")

            bpm_raw = bpms[i].strip() if i < len(bpms) else ""
            bpm_value = None
            if bpm_raw:
                if not bpm_raw.isdigit():
                    return _error(request, user, f"BPM for '{title}' must be a whole number.")
                bpm_value = int(bpm_raw)
                if not 1 <= bpm_value <= 999:
                    return _error(request, user, f"BPM for '{title}' must be between 1 and 999.")

            try:
                price_value = Decimal((prices[i] or "0").strip())
                if not price_value.is_finite() or price_value < 0:
                    raise ValueError
            except Exception:
                return _error(request, user, f"Price for '{title}' is invalid.")

            model_raw = (sales_models[i] or "non_exclusive").strip().lower()
            sales_model = SalesModel.EXCLUSIVE if model_raw == "exclusive" else SalesModel.NON_EXCLUSIVE

            if _r2_is_configured():
                audio_path = await save_upload_to_r2(audio_file, "audio", ALLOWED_AUDIO_EXT)
            else:
                audio_path = await save_upload(audio_file, "audio", ALLOWED_AUDIO_EXT)

            cover_path = None
            if cover_files and i < len(cover_files) and cover_files[i] is not None and cover_files[i].filename:
                if _r2_is_configured():
                    cover_path = await save_upload_to_r2(cover_files[i], "covers", ALLOWED_IMAGE_EXT)
                else:
                    cover_path = await save_upload(cover_files[i], "covers", ALLOWED_IMAGE_EXT)

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=unique_slug(db, Track, title, "track"),
                description=(descriptions[i] or "").strip() or None,
                genre=(genres[i] or "").strip() or None,
                bpm=bpm_value,
                tags=(tags_list[i] or "").strip() or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
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
    return RedirectResponse(url="/dashboard?success=" + message.replace(" ", "%20"), status_code=303)
