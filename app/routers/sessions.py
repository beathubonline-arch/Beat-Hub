from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.session import SessionService, SessionBooking
from app.models.profile import Profile
from app.models.music import Track
from app.models.user import User
from app.services.notifications import create_notification
from app.utils.deps import get_optional_user, get_role_name, require_user, require_creator

router=APIRouter(tags=["sessions"])
templates=Jinja2Templates(directory="app/templates")
TYPES={"recording","mix_master","production","songwriting"}
STATUSES={"pending","accepted","declined","cancelled","completed"}

@router.get("/sessions")
@router.get("/sessions/producer/{producer_slug}")
def sessions(request:Request, producer_slug:str="", db:Session=Depends(get_db), current_user=Depends(get_optional_user)):
    producer_slug=(producer_slug or request.query_params.get("producer") or "").strip()
    from_track_slug=(request.query_params.get("from_track") or "").strip()
    selected_producer=None
    source_track=None
    service_query=db.query(SessionService).options(joinedload(SessionService.creator_profile)).filter(SessionService.is_active.is_(True))
    if producer_slug:
        selected_producer=db.query(Profile).filter(Profile.slug==producer_slug).first()
        if not selected_producer:
            raise HTTPException(404,"Producer not found.")
        service_query=service_query.filter(SessionService.creator_profile_id==selected_producer.id)
        if from_track_slug:
            source_track=db.query(Track).filter(Track.slug==from_track_slug,Track.creator_profile_id==selected_producer.id).first()
    services=service_query.order_by(SessionService.created_at.desc()).all()
    bookings=[]
    if current_user:
        if get_role_name(current_user)=="creator" and current_user.profile:
            bookings=db.query(SessionBooking).options(joinedload(SessionBooking.service),joinedload(SessionBooking.client)).join(SessionService).filter(SessionService.creator_profile_id==current_user.profile.id).order_by(SessionBooking.created_at.desc()).all()
        else:
            bookings=db.query(SessionBooking).options(joinedload(SessionBooking.service)).filter(SessionBooking.client_user_id==current_user.id).order_by(SessionBooking.created_at.desc()).all()
    return templates.TemplateResponse(request,"sessions.html",{"request":request,"current_user":current_user,"user":current_user,"current_year":datetime.utcnow().year,"services":services,"bookings":bookings,"selected_producer":selected_producer,"source_track":source_track})

@router.post("/sessions/services")
def create_service(title:str=Form(...), service_type:str=Form(...), description:str=Form(""), price:str=Form(...), duration_minutes:int=Form(60), location_mode:str=Form("remote"), db:Session=Depends(get_db), user:User=Depends(require_creator)):
    title=title.strip()
    if not title: raise HTTPException(400,"Service title is required.")
    if service_type not in TYPES: raise HTTPException(400,"Invalid service type.")
    try: amount=Decimal(price)
    except (InvalidOperation,ValueError): raise HTTPException(400,"Invalid price.")
    if amount < 0 or amount > 10000000 or duration_minutes < 15 or duration_minutes > 1440: raise HTTPException(400,"Invalid price or duration.")
    profile=user.profile
    item=SessionService(creator_profile_id=profile.id,title=title[:120],service_type=service_type,description=description.strip()[:2000] or None,price=amount,currency="KES",duration_minutes=duration_minutes,location_mode=location_mode if location_mode in {"remote","in_person","hybrid"} else "remote")
    db.add(item); db.commit()
    return RedirectResponse("/sessions?success="+quote("Session service published."),303)

@router.post("/sessions/services/{service_id}/toggle")
def toggle_service(service_id:str, db:Session=Depends(get_db), user:User=Depends(require_creator)):
    item=db.query(SessionService).filter(SessionService.id==service_id,SessionService.creator_profile_id==user.profile.id).first()
    if not item: raise HTTPException(404,"Session service not found.")
    item.is_active=not item.is_active; db.commit()
    return RedirectResponse("/sessions?success="+quote("Session service updated."),303)

@router.post("/sessions/{service_id}/book")
def book(service_id:str, preferred_at:str=Form(...), note:str=Form(""), db:Session=Depends(get_db), user:User=Depends(require_user)):
    service=db.query(SessionService).options(joinedload(SessionService.creator_profile)).filter(SessionService.id==service_id,SessionService.is_active.is_(True)).first()
    if not service: raise HTTPException(404,"Session service not found.")
    try: when=datetime.fromisoformat(preferred_at)
    except ValueError: raise HTTPException(400,"Choose a valid date and time.")
    if when <= datetime.now(): raise HTTPException(400,"Session time must be in the future.")
    if service.creator_profile.user_id==user.id: raise HTTPException(400,"You cannot book your own session.")
    duplicate=db.query(SessionBooking).filter(SessionBooking.service_id==service.id,SessionBooking.client_user_id==user.id,SessionBooking.preferred_at==when,SessionBooking.status=="pending").first()
    if duplicate: raise HTTPException(409,"This booking request already exists.")
    booking=SessionBooking(service_id=service.id,client_user_id=user.id,preferred_at=when,note=note.strip()[:2000] or None)
    db.add(booking); db.commit(); db.refresh(booking)
    create_notification(service.creator_profile.user_id,f"session-booking:{booking.id}","session","New session request",f"{user.username or user.email} requested {service.title}.",f"/sessions/producer/{service.creator_profile.slug}")
    destination=f"/sessions/producer/{service.creator_profile.slug}?success="+quote("Booking request sent to the creator.")
    return RedirectResponse(destination,303)

@router.post("/sessions/bookings/{booking_id}/{action}")
def booking_action(booking_id:str, action:str, db:Session=Depends(get_db), user:User=Depends(require_user)):
    if action not in {"accept","decline","cancel","complete"}: raise HTTPException(400,"Invalid booking action.")
    booking=db.query(SessionBooking).options(joinedload(SessionBooking.service).joinedload(SessionService.creator_profile)).filter(SessionBooking.id==booking_id).first()
    if not booking: raise HTTPException(404,"Booking not found.")
    is_creator=booking.service.creator_profile.user_id==user.id
    is_client=booking.client_user_id==user.id
    if action in {"accept","decline","complete"} and not is_creator: raise HTTPException(403,"Only the session creator can do that.")
    if action=="cancel" and not is_client: raise HTTPException(403,"Only the booking artist can cancel.")
    transitions={"accept":("pending","accepted"),"decline":("pending","declined"),"cancel":("pending","cancelled"),"complete":("accepted","completed")}
    expected,new=transitions[action]
    if booking.status!=expected: raise HTTPException(409,f"Booking cannot be {action}ed from {booking.status}.")
    booking.status=new; db.commit()
    target=booking.client_user_id if is_creator else booking.service.creator_profile.user_id
    create_notification(target,f"session-booking:{booking.id}:{new}","session",f"Session {new}",f"{booking.service.title} is now {new}.","/sessions")
    return RedirectResponse("/sessions?success="+quote(f"Booking {new}."),303)
