from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.session import SessionService, SessionBooking
from app.models.user import User
from app.utils.deps import get_optional_user, require_user, require_creator

router=APIRouter(tags=["sessions"])
templates=Jinja2Templates(directory="app/templates")
TYPES={"recording","mix_master","production","songwriting"}

@router.get("/sessions")
def sessions(request:Request, db:Session=Depends(get_db), current_user=Depends(get_optional_user)):
    services=db.query(SessionService).filter(SessionService.is_active.is_(True)).order_by(SessionService.created_at.desc()).all()
    return templates.TemplateResponse(request,"sessions.html",{"request":request,"current_user":current_user,"user":current_user,"current_year":datetime.utcnow().year,"services":services})

@router.post("/sessions/services")
def create_service(title:str=Form(...), service_type:str=Form(...), description:str=Form(""), price:str=Form(...), duration_minutes:int=Form(60), location_mode:str=Form("remote"), db:Session=Depends(get_db), user:User=Depends(require_creator)):
    if service_type not in TYPES: raise HTTPException(400,"Invalid service type.")
    try: amount=Decimal(price)
    except InvalidOperation: raise HTTPException(400,"Invalid price.")
    if amount < 0 or duration_minutes < 15 or duration_minutes > 1440: raise HTTPException(400,"Invalid price or duration.")
    profile=getattr(user,"profile",None)
    if not profile: raise HTTPException(400,"Creator profile missing.")
    item=SessionService(creator_profile_id=profile.id,title=title.strip()[:120],service_type=service_type,description=description.strip() or None,price=amount,currency="KES",duration_minutes=duration_minutes,location_mode=location_mode if location_mode in {"remote","in_person","hybrid"} else "remote")
    db.add(item); db.commit()
    return RedirectResponse("/sessions?success="+quote("Session service published."),303)

@router.post("/sessions/{service_id}/book")
def book(service_id:str, preferred_at:str=Form(...), note:str=Form(""), db:Session=Depends(get_db), user:User=Depends(require_user)):
    service=db.query(SessionService).filter(SessionService.id==service_id,SessionService.is_active.is_(True)).first()
    if not service: raise HTTPException(404,"Session service not found.")
    try: when=datetime.fromisoformat(preferred_at)
    except ValueError: raise HTTPException(400,"Choose a valid date and time.")
    if when <= datetime.now(): raise HTTPException(400,"Session time must be in the future.")
    if service.creator_profile and service.creator_profile.user_id==user.id: raise HTTPException(400,"You cannot book your own session.")
    booking=SessionBooking(service_id=service.id,client_user_id=user.id,preferred_at=when,note=note.strip()[:2000] or None)
    db.add(booking); db.commit()
    return RedirectResponse("/sessions?success="+quote("Booking request sent to the creator."),303)
