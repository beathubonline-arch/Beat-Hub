from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, AlbumContentType, AlbumTrack, SalesModel, Track, TrackContentType
from app.models.user import User
from app.services.storage import ALLOWED_AUDIO_EXT, ALLOWED_IMAGE_EXT, UploadValidationError, save_upload, save_upload_to_r2, _r2_is_configured
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["album-upload"])
templates = Jinja2Templates(directory="app/templates")


def _ctx(request: Request, user: User, **extra):
    data = {"request": request, "current_user": user, "current_year": datetime.utcnow().year}
    data.update(extra)
    return data


async def _store(upload: UploadFile, folder: str, allowed):
    if _r2_is_configured():
        return await save_upload_to_r2(upload, folder, allowed)
    return await save_upload(upload, folder, allowed)


@router.post("/dashboard/albums/new-with-tracks")
async def create_album_with_tracks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    content_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: Optional[UploadFile] = File(None),
    track_ids: List[str] = Form(default=[]),
    new_audio_files: List[UploadFile] = File(default=[]),
    new_titles: List[str] = Form(default=[]),
):
    profile = user.profile
    if not profile:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    if content_type not in {AlbumContentType.ALBUM.value, AlbumContentType.BEAT_COLLECTION.value}:
        raise HTTPException(status_code=400, detail="Invalid project type.")

    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Project title is required.")

    wanted_track_type = TrackContentType.TRACK.value if content_type == AlbumContentType.ALBUM.value else TrackContentType.BEAT.value

    existing = []
    if track_ids:
        existing = (
            db.query(Track)
            .filter(Track.creator_profile_id == profile.id, Track.id.in_(track_ids), Track.content_type == wanted_track_type)
            .all()
        )
        if len(existing) != len(set(track_ids)):
            raise HTTPException(status_code=400, detail="One or more selected tracks are invalid for this project type.")

    if not existing and not new_audio_files:
        raise HTTPException(status_code=400, detail="Add at least one existing track or upload a new track.")

    try:
        artwork_path = None
        if artwork and artwork.filename:
            artwork_path = await _store(artwork, "artwork", ALLOWED_IMAGE_EXT)

        album = Album(
            creator_profile_id=profile.id,
            title=title,
            slug=unique_slug(db, Album, title, "album"),
            description=(description or "").strip() or None,
            genre=(genre or "").strip() or None,
            artwork_path=artwork_path,
            content_type=content_type,
            is_published=True,
        )
        db.add(album)
        db.flush()

        track_map = {str(t.id): t for t in existing}
        ordered_ids = [str(x) for x in track_ids if str(x) in track_map]

        for index, audio in enumerate(new_audio_files):
            if not audio or not audio.filename:
                raise UploadValidationError("Every selected audio file must contain a filename.")
            raw_title = new_titles[index] if index < len(new_titles) else ""
            track_title = (raw_title or Path(audio.filename).stem).strip()
            if not track_title:
                raise UploadValidationError("Every uploaded track needs a title.")

            audio_path = await _store(audio, "audio", ALLOWED_AUDIO_EXT)
            track = Track(
                creator_profile_id=profile.id,
                title=track_title,
                slug=unique_slug(db, Track, track_title, "track"),
                description=None,
                genre=(genre or "").strip() or None,
                bpm=None,
                tags=None,
                audio_file_path=audio_path,
                cover_art_path=artwork_path,
                price=Decimal("0"),
                sales_model=SalesModel.NON_EXCLUSIVE,
                content_type=wanted_track_type,
                is_published=True,
            )
            db.add(track)
            db.flush()
            track_map[str(track.id)] = track
            ordered_ids.append(str(track.id))

        for position, track_id in enumerate(ordered_ids):
            db.add(AlbumTrack(album_id=album.id, track_id=track_map[track_id].id, position=position))

        db.commit()
        return RedirectResponse(url=f"/album/{album.slug}", status_code=303)

    except UploadValidationError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "upload_album.html", _ctx(request, user, error=str(exc)), status_code=400)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Project creation failed: {str(exc)}")
