from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get\_db
from app.models.ledger import WithdrawalRequest
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.storage import (
    ALLOWED\_AUDIO\_EXT,
    ALLOWED\_IMAGE\_EXT,
    UploadValidationError,
    save\_upload,
)
from app.utils.deps import require\_creator
from app.utils.text import unique\_slug



router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="app/templates")



def ctx(request: Request, current\_user, \*\*extra):
    base = {
        "request": request,
        "current\_user": current\_user,
        "current\_year": datetime.utcnow().year,
    }

    base.update(extra)

    return base



def \_creator\_stats(db: Session, profile\_id: str) -> dict:
    orders = (
        db.query(Order)
        .join(Track, Order.track\_id == Track.id)
        .filter(
            Track.creator\_profile\_id == profile\_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .all()
    )

    gross = sum(
        (o.gross\_amount for o in orders),
        Decimal("0"),
    )

    commission = sum(
        (o.commission\_amount for o in orders),
        Decimal("0"),
    )

    net = sum(
        (o.net\_amount for o in orders),
        Decimal("0"),
    )

    withdrawn = (
        db.query(
            func.coalesce(
                func.sum(WithdrawalRequest.amount),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator\_profile\_id == profile\_id,
            WithdrawalRequest.status.in\_(
                [
                    "approved",
                    "processing",
                    "paid",
                ]
            ),
        )
        .scalar()
    )

    pending\_withdrawal = (
        db.query(
            func.coalesce(
                func.sum(WithdrawalRequest.amount),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator\_profile\_id == profile\_id,
            WithdrawalRequest.status == "pending",
        )
        .scalar()
    )

    withdrawn\_decimal = Decimal(
        str(withdrawn or 0)
    )

    pending\_decimal = Decimal(
        str(pending\_withdrawal or 0)
    )

    available\_balance = (
        net
        \- withdrawn\_decimal
        \- pending\_decimal
    )

    return {
        "total\_sales": len(orders),
        "gross\_revenue": gross,
        "platform\_commission": commission,
        "net\_earnings": net,
        "available\_balance": available\_balance,
        "pending\_withdrawal": pending\_decimal,
        "recent\_orders": sorted(
            orders,
            key=lambda o: o.completed\_at or o.created\_at,
            reverse=True,
        )[:8],
    }



@router.get("/dashboard")
def dashboard\_home(
    request: Request,
    db: Session = Depends(get\_db),
    user: User = Depends(require\_creator),
):
    profile = user.profile

    if not profile:
        from fastapi import HTTPException

        raise HTTPException(
            status\_code=400,
            detail="Creator profile missing.",
        )

    stats = \_creator\_stats(
        db,
        profile.id,
    )

    track\_count = (
        db.query(Track)
        .filter(
            Track.creator\_profile\_id == profile.id
        )
        .count()
    )

    album\_count = (
        db.query(Album)
        .filter(
            Album.creator\_profile\_id == profile.id
        )
        .count()
    )

    youtube\_url = (
        f"[https://www.youtube.com/channel/](https://www.youtube.com/channel/)"
        f"{settings.YOUTUBE\_CHANNEL\_ID}"
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            track\_count=track\_count,
            album\_count=album\_count,
            youtube\_url=youtube\_url,
            discord\_url=settings.DISCORD\_INVITE\_URL,
        ),
    )



@router.get("/dashboard/upload")
def upload\_page(
    request: Request,
    user: User = Depends(require\_creator),
):
    return templates.TemplateResponse(
        request,
        "upload\_track.html",
        ctx(
            request,
            user,
        ),
    )



@router.post("/dashboard/upload")
async def upload\_submit(
    request: Request,
    db: Session = Depends(get\_db),
    user: User = Depends(require\_creator),
    titles: List[str] = Form(...),
    descriptions: List[str] = Form(...),
    genres: List[str] = Form(...),
    bpms: List[str] = Form(...),
    tags\_list: List[str] = Form(...),
    prices: List[str] = Form(...),
    sales\_models: List[str] = Form(...),
    audio\_files: List[UploadFile] = File(...),
    cover\_files: List[Optional[UploadFile]] = File(None),
):
    profile = user.profile

    def error(msg: str):
        return templates.TemplateResponse(
            request,
            "upload\_track.html",
            ctx(
                request,
                user,
                error=msg,
            ),
            status\_code=400,
        )

    if not titles or not audio\_files:
        return error(
            "At least one track with an audio file is required."
        )

    if len(titles) != len(audio\_files):
        return error(
            "Track details and audio files don't match up."
        )

    created = []

    try:
        for i, title in enumerate(titles):

            title = title.strip()

            if not title:
                return error(
                    "Every track needs a title."
                )

            bpm\_raw = (
                bpms[i].strip()
                if i < len(bpms)
                else ""
            )

            bpm\_val = None

            if bpm\_raw:
                if not bpm\_raw\.isdigit():
                    return error(
                        f"BPM for '{title}' must be a whole number."
                    )

                bpm\_val = int(bpm\_raw)

            price\_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price\_val = Decimal(price\_raw)

                if price\_val < 0:
                    raise ValueError

            except Exception:
                return error(
                    f"Price for '{title}' is invalid."
                )

            model\_raw = (
                sales\_models[i]
                if i < len(sales\_models)
                else "non\_exclusive"
            )

            sales\_model = (
                SalesModel.EXCLUSIVE
                if model\_raw == "exclusive"
                else SalesModel.NON\_EXCLUSIVE
            )

            audio\_path = await save\_upload(
                audio\_files[i],
                "audio",
                ALLOWED\_AUDIO\_EXT,
            )

            cover\_path = None

            if (
                cover\_files
                and i < len(cover\_files)
                and cover\_files[i] is not None
                and cover\_files[i].filename
            ):
                cover\_path = await save\_upload(
                    cover\_files[i],
                    "covers",
                    ALLOWED\_IMAGE\_EXT,
                )

            slug = unique\_slug(
                db,
                Track,
                title,
                "track",
            )

            track = Track(
                creator\_profile\_id=profile.id,
                title=title,
                slug=slug,
                description=(
                    descriptions[i].strip()
                    if i < len(descriptions)
                    else None
                )
                or None,
                genre=(
                    genres[i].strip()
                    if i < len(genres)
                    else None
                )
                or None,
                bpm=bpm\_val,
                tags=(
                    tags\_list[i].strip()
                    if i < len(tags\_list)
                    else None
                )
                or None,
                audio\_file\_path=audio\_path,
                cover\_art\_path=cover\_path,
                price=price\_val,
                sales\_model=sales\_model,
            )

            db.add(track)
            created.append(track)

        db.commit()

    except UploadValidationError as exc:
        db.rollback()
        return error(str(exc))

    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            "/dashboard?success="
            f"{len(created)}%20track(s)%20uploaded%20successfully."
        ),
        status\_code=303,
    )



@router.get("/dashboard/albums/new")
def new\_album\_page(
    request: Request,
    db: Session = Depends(get\_db),
    user: User = Depends(require\_creator),
):
    tracks = (
        db.query(Track)
        .filter(
            Track.creator\_profile\_id == user.profile.id
        )
        .order\_by(Track.created\_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "upload\_album.html",
        ctx(
            request,
            user,
            tracks=tracks,
        ),
    )



@router.post("/dashboard/albums/new")
async def new\_album\_submit(
    request: Request,
    db: Session = Depends(get\_db),
    user: User = Depends(require\_creator),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: UploadFile = File(None),
    track\_ids: List[str] = Form([]),
):
    profile = user.profile

    def error(msg: str):
        tracks = (
            db.query(Track)
            .filter(
                Track.creator\_profile\_id == profile.id
            )
            .all()
        )

        return templates.TemplateResponse(
            request,
            "upload\_album.html",
            ctx(
                request,
                user,
                tracks=tracks,
                error=msg,
            ),
            status\_code=400,
        )

    if not title.strip():
        return error(
            "Album title is required."
        )

    if not track\_ids:
        return error(
            "Select at least one track for this album."
        )

    artwork\_path = None

    if artwork and artwork.filename:
        try:
            artwork\_path = await save\_upload(
                artwork,
                "artwork",
                ALLOWED\_IMAGE\_EXT,
            )
        except UploadValidationError as exc:
            return error(str(exc))

    slug = unique\_slug(
        db,
        Album,
        title,
        "album",
    )

    album = Album(
        creator\_profile\_id=profile.id,
        title=title.strip(),
        slug=slug,
        description=description.strip() or None,
        genre=genre.strip() or None,
        artwork\_path=artwork\_path,
    )

    db.add(album)
    db.flush()

    valid\_tracks = (
        db.query(Track)
        .filter(
            Track.id.in\_(track\_ids),
            Track.creator\_profile\_id == profile.id,
        )
        .all()
    )

    for position, track in enumerate(valid\_tracks):
        db.add(
            AlbumTrack(
                album\_id=album.id,
                track\_id=track.id,
                position=position,
            )
        )

    db.commit()

    return RedirectResponse(
        url=(
            f"/album/{album.slug}"
            "?success=Album%20created."
        ),
        status\_code=303,
    )



@router.post("/dashboard/withdraw")
def request\_withdrawal(
    request: Request,
    db: Session = Depends(get\_db),
    user: User = Depends(require\_creator),
    amount: str = Form(...),
    phone\_number: str = Form(...),
):
    profile = user.profile

    stats = \_creator\_stats(
        db,
        profile.id,
    )

    try:
        amount\_val = Decimal(amount)
    except Exception:
        return RedirectResponse(
            url="/dashboard?error=Invalid%20withdrawal%20amount.",
            status\_code=303,
        )

    if amount\_val <= 0:
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Withdrawal%20amount%20must%20be%20positive."
            ),
            status\_code=303,
        )

    if amount\_val > stats["available\_balance"]:
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Withdrawal%20exceeds%20your%20available%20balance."
            ),
            status\_code=303,
        )

    phone\_number = phone\_number.strip()

    if not phone\_number:
        return RedirectResponse(
            url="/dashboard?error=M-Pesa%20phone%20number%20is%20required.",
            status\_code=303,
        )

    withdrawal = WithdrawalRequest(
        creator\_profile\_id=profile.id,
        amount=amount\_val,
        phone\_number=phone\_number,
        status="pending",
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/dashboard?"
            "success=Withdrawal%20request%20submitted."
        ),
        status\_code=303,
    )
