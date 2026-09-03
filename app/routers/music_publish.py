from decimal import Decimal, InvalidOperation
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect
from app.database import get_db
from app.models.music import SalesModel, Track, TrackContentType
from app.models.user import User
from app.services.pricing import normalize_currency
from app.services.storage import ALLOWED_AUDIO_EXT, ALLOWED_IMAGE_EXT, UploadValidationError, _r2_is_configured, r2_object_head, r2_presigned_upload, save_upload, save_upload_to_r2
from app.utils.deps import require_creator
from app.utils.text import unique_slug
router=APIRouter(tags=["music-publishing"])
templates=Jinja2Templates(directory="app/templates")
def _error(request,user,message):
    return templates.TemplateResponse(request,"upload_track.html",{"request":request,"current_user":user,"current_year":2026,"error":message},status_code=400)
@router.get("/dashboard/upload")
def upload_page(request:Request,user:User=Depends(require_creator)):
    return templates.TemplateResponse(request,"upload_track.html",{"request":request,"current_user":user,"current_year":2026})
@router.post("/dashboard/upload/sign")
async def sign_direct_upload(request:Request,user:User=Depends(require_creator)):
    if not _r2_is_configured(): raise HTTPException(status_code=503,detail="Direct storage upload is unavailable.")
    try:
        payload=await request.json(); filename=str(payload.get("filename") or ""); content_type=str(payload.get("content_type") or "application/octet-stream"); kind=str(payload.get("kind") or "audio").lower()
        if kind not in {"audio","covers"}: raise HTTPException(status_code=400,detail="Invalid upload type.")
        if not filename: raise HTTPException(status_code=400,detail="Filename is required.")
        return r2_presigned_upload(filename,content_type,kind)
    except UploadValidationError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
def _form_values(form,name:str)->List[str]: return [str(value or "") for value in form.getlist(name)]
def _form_file_slots(form,name:str): return [value if isinstance(value,UploadFile) and value.filename else None for value in form.getlist(name)]
def _direct_paths(form,name:str)->List[str]:
    values=_form_values(form,name)
    if len(values)==1 and "\n" in values[0]: values=values[0].splitlines()
    return [v.strip() for v in values if v.strip()]
@router.post("/dashboard/upload")
async def publish_tracks(request:Request,db:Session=Depends(get_db),user:User=Depends(require_creator)):
    profile=getattr(user,"profile",None)
    if not profile: raise HTTPException(status_code=400,detail="Creator profile missing.")
    try: form=await request.form()
    except ClientDisconnect: return _error(request,user,"The upload connection was interrupted before BeatHub received the form. Please retry; your original audio is not partially published.")
    titles=_form_values(form,"titles"); descriptions=_form_values(form,"descriptions"); genres=_form_values(form,"genres"); bpms=_form_values(form,"bpms"); tags_list=_form_values(form,"tags_list"); prices=_form_values(form,"prices"); currencies=_form_values(form,"currencies"); sales_models=_form_values(form,"sales_models"); content_types=_form_values(form,"content_types")
    audio_refs=_direct_paths(form,"audio_r2_paths"); direct_covers=_direct_paths(form,"cover_r2_paths")
    audio_slots=_form_file_slots(form,"audio_files"); cover_slots=_form_file_slots(form,"cover_files"); audio_files=[f for f in audio_slots if f is not None]
    expected=len(audio_refs) if audio_refs else len(audio_files)
    if not expected: return _error(request,user,"Please select at least one audio file.")
    fields={"titles":titles,"descriptions":descriptions,"genres":genres,"tags":tags_list,"prices":prices,"currencies":currencies,"sales_models":sales_models,"content_types":content_types}
    mismatches={name:len(values) for name,values in fields.items() if len(values)!=expected}
    if mismatches: return _error(request,user,"Upload form data is incomplete ("+", ".join(f"{n}={c}" for n,c in mismatches.items())+f"; audio={expected}). Please refresh and try again.")
    if bpms and len(bpms)!=expected: return _error(request,user,"The BPM fields do not match the selected audio files. Please refresh and try again.")
    created=[]
    try:
        for i in range(expected):
            title=titles[i].strip()
            if not title: return _error(request,user,"Every upload needs a title.")
            content_raw=content_types[i].strip().lower()
            if content_raw not in {TrackContentType.BEAT.value,TrackContentType.TRACK.value}: return _error(request,user,f"Choose Beat or Track for '{title}'.")
            try: currency=normalize_currency(currencies[i])
            except ValueError as exc: return _error(request,user,f"Currency for '{title}' is invalid: {exc}")
            bpm_raw=bpms[i].strip() if bpms else ""; bpm_value=None
            if bpm_raw:
                if not bpm_raw.isdigit() or not 1<=int(bpm_raw)<=999: return _error(request,user,f"BPM for '{title}' must be a whole number between 1 and 999.")
                bpm_value=int(bpm_raw)
            try:
                price_value=Decimal(prices[i].strip() or "0")
                if not price_value.is_finite() or price_value<0: raise InvalidOperation
            except (InvalidOperation,ValueError): return _error(request,user,f"Price for '{title}' is invalid.")
            model_raw=sales_models[i].strip().lower() or "non_exclusive"
            if model_raw not in {"exclusive","non_exclusive"}: return _error(request,user,f"Sales model for '{title}' is invalid.")
            sales_model=SalesModel.EXCLUSIVE if model_raw=="exclusive" else SalesModel.NON_EXCLUSIVE
            if audio_refs:
                audio_path=audio_refs[i]; meta=r2_object_head(audio_path); size=int(meta.get("ContentLength") or 0)
                if size<=0 or size>1000*1024*1024: raise UploadValidationError("Audio file is empty or exceeds the 1000MB upload limit.")
            else:
                audio_file=audio_files[i]; audio_path=await save_upload_to_r2(audio_file,"audio",ALLOWED_AUDIO_EXT) if _r2_is_configured() else await save_upload(audio_file,"audio",ALLOWED_AUDIO_EXT)
            cover_path=None
            if i<len(direct_covers):
                cover_path=direct_covers[i]; meta=r2_object_head(cover_path)
                if int(meta.get("ContentLength") or 0)<=0: raise UploadValidationError("Cover art is empty.")
            elif i<len(cover_slots) and cover_slots[i] is not None:
                cover_path=await save_upload_to_r2(cover_slots[i],"covers",ALLOWED_IMAGE_EXT) if _r2_is_configured() else await save_upload(cover_slots[i],"covers",ALLOWED_IMAGE_EXT)
            track=Track(creator_profile_id=profile.id,title=title,slug=unique_slug(db,Track,title,"track"),description=descriptions[i].strip() or None,genre=genres[i].strip() or None,bpm=bpm_value,tags=tags_list[i].strip() or None,audio_file_path=audio_path,cover_art_path=cover_path,price=price_value,currency=currency,sales_model=sales_model,content_type=content_raw,is_published=True)
            db.add(track); created.append(track)
        db.commit()
    except UploadValidationError as exc: db.rollback(); return _error(request,user,str(exc))
    except Exception: db.rollback(); raise
    item_word="item" if len(created)==1 else "items"; message=f"{len(created)} {item_word} published successfully."
    return RedirectResponse(url="/dashboard?success="+message.replace(" ","%20"),status_code=303)
