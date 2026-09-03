from decimal import Decimal, InvalidOperation
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
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
    if request.headers.get("content-type","").lower().startswith("application/json"):
        return JSONResponse({"detail":message},status_code=400)
    return templates.TemplateResponse(request,"upload_track.html",{"request":request,"current_user":user,"current_year":2026,"error":message},status_code=400)
@router.get("/dashboard/upload")
def upload_page(request:Request,user:User=Depends(require_creator)): return templates.TemplateResponse(request,"upload_track.html",{"request":request,"current_user":user,"current_year":2026})
@router.post("/dashboard/upload/sign")
async def sign_direct_upload(request:Request,user:User=Depends(require_creator)):
    if not _r2_is_configured(): raise HTTPException(status_code=503,detail="Direct storage upload is unavailable.")
    try:
        payload=await request.json(); filename=str(payload.get("filename") or ""); content_type=str(payload.get("content_type") or "application/octet-stream"); kind=str(payload.get("kind") or "audio").lower()
        if kind not in {"audio","covers"}: raise HTTPException(status_code=400,detail="Invalid upload type.")
        if not filename: raise HTTPException(status_code=400,detail="Filename is required.")
        return r2_presigned_upload(filename,content_type,kind)
    except UploadValidationError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.post("/dashboard/upload/blob")
async def upload_blob_fallback(request:Request,file:UploadFile,kind:str="audio",user:User=Depends(require_creator)):
    """Same-origin fallback when browser CORS blocks a direct R2 PUT.

    Normal uploads still use direct-to-R2. This endpoint exists so a creator
    is never stranded by an R2 bucket CORS misconfiguration or restrictive
    browser/network policy.
    """
    kind=str(kind or "audio").strip().lower()
    allowed=ALLOWED_AUDIO_EXT if kind=="audio" else ALLOWED_IMAGE_EXT if kind=="covers" else None
    if allowed is None: raise HTTPException(status_code=400,detail="Invalid upload type.")
    if not _r2_is_configured(): raise HTTPException(status_code=503,detail="Storage is not configured.")
    try:
        path=await save_upload_to_r2(file,kind,allowed)
        return {"path":path}
    except UploadValidationError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=503,detail="Storage upload failed. Please retry.") from exc

def _form_values(form,name:str)->List[str]: return [str(value or "") for value in form.getlist(name)]
def _form_file_slots(form,name:str): return [value if isinstance(value,UploadFile) and value.filename else None for value in form.getlist(name)]
def _direct_paths(form,name:str,preserve_empty:bool=False)->List[str]:
    values=_form_values(form,name)
    if len(values)==1 and "\n" in values[0]: values=values[0].splitlines()
    return [v.strip() for v in values] if preserve_empty else [v.strip() for v in values if v.strip()]

def _publish_data(request,user,db,data):
    if not isinstance(data,dict): return _error(request,user,"Invalid upload data. Please refresh and try again.")
    items=data.get("items")
    if not isinstance(items,list) or not items: return _error(request,user,"Please select at least one audio file.")
    profile=getattr(user,"profile",None)
    if not profile: raise HTTPException(status_code=400,detail="Creator profile missing.")
    created=[]
    try:
        for item in items:
            if not isinstance(item,dict): raise UploadValidationError("Invalid upload item.")
            title=str(item.get("title") or "").strip()
            if not title: raise UploadValidationError("Every upload needs a title.")
            description=str(item.get("description") or "").strip(); genre=str(item.get("genre") or "").strip(); tags=str(item.get("tags") or "").strip(); content_raw=str(item.get("content_type") or "").strip().lower()
            if content_raw not in {TrackContentType.BEAT.value,TrackContentType.TRACK.value}: raise UploadValidationError(f"Choose Beat or Track for '{title}'.")
            try: currency=normalize_currency(str(item.get("currency") or ""))
            except ValueError as exc: raise UploadValidationError(f"Currency for '{title}' is invalid: {exc}") from exc
            bpm_raw=str(item.get("bpm") or "").strip(); bpm_value=None
            if bpm_raw:
                if not bpm_raw.isdigit() or not 1<=int(bpm_raw)<=999: raise UploadValidationError(f"BPM for '{title}' must be a whole number between 1 and 999.")
                bpm_value=int(bpm_raw)
            try:
                price_value=Decimal(str(item.get("price") or "0"))
                if not price_value.is_finite() or price_value<0: raise InvalidOperation
            except (InvalidOperation,ValueError): raise UploadValidationError(f"Price for '{title}' is invalid.")
            model_raw=str(item.get("sales_model") or "non_exclusive").strip().lower()
            if model_raw not in {"exclusive","non_exclusive"}: raise UploadValidationError(f"Sales model for '{title}' is invalid.")
            audio_path=str(item.get("audio_r2_path") or "").strip()
            if not audio_path: raise UploadValidationError(f"Audio upload is missing for '{title}'.")
            meta=r2_object_head(audio_path); size=int(meta.get("ContentLength") or 0)
            if size<=0 or size>1000*1024*1024: raise UploadValidationError("Audio file is empty or exceeds the 1000MB upload limit.")
            cover_path=str(item.get("cover_r2_path") or "").strip() or None
            if cover_path:
                meta=r2_object_head(cover_path)
                if int(meta.get("ContentLength") or 0)<=0: raise UploadValidationError("Cover art is empty.")
            track=Track(creator_profile_id=profile.id,title=title,slug=unique_slug(db,Track,title,"track"),description=description or None,genre=genre or None,bpm=bpm_value,tags=tags or None,audio_file_path=audio_path,cover_art_path=cover_path,price=price_value,currency=currency,sales_model=SalesModel.EXCLUSIVE if model_raw=="exclusive" else SalesModel.NON_EXCLUSIVE,content_type=content_raw,is_published=True)
            db.add(track); created.append(track)
        db.commit()
    except UploadValidationError as exc: db.rollback(); return _error(request,user,str(exc))
    except Exception: db.rollback(); raise
    return RedirectResponse(url="/dashboard?success="+f"{len(created)} {'item' if len(created)==1 else 'items'} published successfully.".replace(" ","%20"),status_code=303)

@router.post("/dashboard/upload")
async def publish_tracks(request:Request,db:Session=Depends(get_db),user:User=Depends(require_creator)):
    """Direct-upload clients send JSON metadata only, so Render never parses the audio multipart body."""
    content_type=request.headers.get("content-type","").lower()
    if content_type.startswith("application/json"):
        try: data=await request.json()
        except Exception: return _error(request,user,"The upload metadata could not be read. Please refresh and try again.")
        return _publish_data(request,user,db,data)
    try: form=await request.form()
    except ClientDisconnect: return _error(request,user,"The upload connection was interrupted before BeatHub received the form. Please retry.")
    titles=_form_values(form,"titles"); descriptions=_form_values(form,"descriptions"); genres=_form_values(form,"genres"); bpms=_form_values(form,"bpms"); tags_list=_form_values(form,"tags_list"); prices=_form_values(form,"prices"); currencies=_form_values(form,"currencies"); sales_models=_form_values(form,"sales_models"); content_types=_form_values(form,"content_types")
    audio_refs=_direct_paths(form,"audio_r2_paths"); direct_covers=_direct_paths(form,"cover_r2_paths",preserve_empty=True)
    expected=len(audio_refs)
    if not expected: return _error(request,user,"Please select at least one audio file.")
    fields={"titles":titles,"descriptions":descriptions,"genres":genres,"tags":tags_list,"prices":prices,"currencies":currencies,"sales_models":sales_models,"content_types":content_types}
    if any(len(values)!=expected for values in fields.values()): return _error(request,user,"Upload form data is incomplete. Please refresh and try again.")
    return _publish_data(request,user,db,{"items":[{"title":titles[i],"description":descriptions[i],"genre":genres[i],"bpm":bpms[i] if i<len(bpms) else "","tags":tags_list[i],"price":prices[i],"currency":currencies[i],"sales_model":sales_models[i],"content_type":content_types[i],"audio_r2_path":audio_refs[i],"cover_r2_path":direct_covers[i] if i<len(direct_covers) else ""} for i in range(expected)]})