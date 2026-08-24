from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
import shutil
import tempfile

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    save_upload,
    save_upload_to_r2,
    _r2_is_configured,
    _r2_client,
    _parse_r2_path,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug


router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="app/templates")


# ============================================================
# COMMON CONTEXT
# ============================================================

def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        "profile": None,
        "stats": {},

        "available_balance": Decimal("0"),
        "pending_withdrawal": Decimal("0"),
        "total_sales": 0,
        "gross_revenue": Decimal("0"),
        "platform_commission": Decimal("0"),
        "net_earnings": Decimal("0"),

        "recent_orders": [],
        "withdrawal_requests": [],

        "track_count": 0,
        "album_count": 0,
        "tracks": [],
        "albums": [],

        "track_page": 1,
        "track_total_pages": 1,
        "track_total": 0,
        "track_total_count": 0,
        "track_per_page": 12,
        "track_search": "",
        "track_start": 0,
        "track_end": 0,
        "q": "",

        "youtube_url": None,
        "discord_url": None,
        "store_url": None,
    }

    data.update(extra)
    return data


# ============================================================
# HELPERS
# ============================================================

def _decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _absolute_store_url(request: Request, slug: str) -> str:
    # Render terminates TLS before forwarding the request.
    # Prefer forwarded protocol so the dashboard always exposes
    # the real public HTTPS URL instead of localhost/http.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    if forwarded_proto:
        scheme = forwarded_proto.split(",")[0].strip()
    else:
        scheme = request.url.scheme

    if forwarded_host:
        host = forwarded_host.split(",")[0].strip()
    else:
        host = request.headers.get("host") or request.url.netloc

    return f"{scheme}://{host}/creator/{slug}"


# ============================================================
# CREATOR EARNINGS
# ============================================================

def _creator_stats(db: Session, profile_id) -> dict:
    orders = (
        db.query(Order)
        .join(Track, Order.track_id == Track.id)
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .all()
    )

    gross = sum(
        (_decimal(order.gross_amount) for order in orders),
        Decimal("0"),
    )

    commission = sum(
        (_decimal(order.commission_amount) for order in orders),
        Decimal("0"),
    )

    net = sum(
        (_decimal(order.net_amount) for order in orders),
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
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status.in_(
                [
                    "approved",
                    "processing",
                    "paid",
                ]
            ),
        )
        .scalar()
    )

    pending = (
        db.query(
            func.coalesce(
                func.sum(WithdrawalRequest.amount),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status == "pending",
        )
        .scalar()
    )

    withdrawn = _decimal(withdrawn)
    pending = _decimal(pending)

    available = net - withdrawn - pending

    if available < Decimal("0"):
        available = Decimal("0")

    recent_orders = sorted(
        orders,
        key=lambda order: (
            order.completed_at
            or order.created_at
            or datetime.min
        ),
        reverse=True,
    )[:8]

    return {
        "total_sales": len(orders),
        "gross_revenue": gross,
        "platform_commission": commission,
        "net_earnings": net,
        "available_balance": available,
        "pending_withdrawal": pending,
        "recent_orders": recent_orders,
    }


def _withdrawal_history(db: Session, profile_id):
    return (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id
        )
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .all()
    )


# ============================================================
# TRACK PAGINATION
# ============================================================

def _track_page(
    db: Session,
    profile_id,
    page: int,
    search: str,
):
    per_page = 12

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    if page < 1:
        page = 1

    search = (search or "").strip()

    query = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile_id
        )
    )

    if search:
        term = f"%{search}%"

        query = query.filter(
            Track.title.ilike(term)
            | Track.genre.ilike(term)
            | Track.tags.ilike(term)
        )

    total = query.count()

    total_pages = max(
        1,
        (total + per_page - 1) // per_page,
    )

    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    tracks = (
        query
        .order_by(Track.created_at.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # Keep the database's original R2 path untouched.
    # Templates can use cover_art_url when available.
    for track in tracks:
        try:
            from app.services.storage import r2_presigned_url

            track.cover_art_url = (
                r2_presigned_url(track.cover_art_path)
                if track.cover_art_path
                else None
            )
        except Exception:
            track.cover_art_url = None

    return {
        "tracks": tracks,
        "track_page": page,
        "track_total_pages": total_pages,
        "track_total": total,
        "track_total_count": total,
        "track_per_page": per_page,
        "track_search": search,
        "track_start": offset + 1 if total else 0,
        "track_end": min(
            offset + len(tracks),
            total,
        ),
        "q": search,
    }


# ============================================================
# DASHBOARD CONTEXT
# ============================================================

def _dashboard_context(
    request: Request,
    db: Session,
    user: User,
    page: int = 1,
    search: str = "",
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    track_count = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .count()
    )

    album_count = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .count()
    )

    withdrawal_requests = _withdrawal_history(
        db,
        profile.id,
    )

    track_data = _track_page(
        db,
        profile.id,
        page,
        search,
    )

    albums = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .order_by(Album.created_at.desc())
        .all()
    )

    youtube_id = getattr(
        settings,
        "YOUTUBE_CHANNEL_ID",
        None,
    )

    youtube_url = None

    if youtube_id:
        youtube_id = str(youtube_id)

        if youtube_id.startswith("http"):
            youtube_url = youtube_id
        else:
            youtube_url = (
                "https://www.youtube.com/channel/"
                + youtube_id
            )

    discord_url = getattr(
        settings,
        "DISCORD_INVITE_URL",
        None,
    )

    store_url = None

    if getattr(profile, "slug", None):
        store_url = _absolute_store_url(
            request,
            str(profile.slug),
        )

    return ctx(
        request,
        user,

        profile=profile,
        stats=stats,

        available_balance=stats["available_balance"],
        pending_withdrawal=stats["pending_withdrawal"],
        total_sales=stats["total_sales"],
        gross_revenue=stats["gross_revenue"],
        platform_commission=stats["platform_commission"],
        net_earnings=stats["net_earnings"],
        recent_orders=stats["recent_orders"],

        withdrawal_requests=withdrawal_requests,

        track_count=track_count,
        album_count=album_count,

        albums=albums,

        youtube_url=youtube_url,
        discord_url=discord_url,
        store_url=store_url,

        **track_data,
    )


# ============================================================
# CREATOR DASHBOARD
# ============================================================

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    page: int = 1,
    q: str = "",
):
    context = _dashboard_context(
        request,
        db,
        user,
        page=page,
        search=q,
    )

    success = request.query_params.get("success")
    error = request.query_params.get("error")

    if success:
        context["success"] = success

    if error:
        context["error"] = error

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context,
    )


# ============================================================
# BPM ANALYSIS
# ============================================================

@router.post("/dashboard/analyze-bpm")
async def analyze_bpm(
    audio_file: UploadFile = File(...),
    user: User = Depends(require_creator),
):
    """Analyze an uploaded audio file and return an estimated BPM.

    This endpoint never stores the analysis upload. It uses a temporary
    file, first checks common embedded BPM metadata, then falls back to
    librosa beat tracking when available.
    """
    if not audio_file or not audio_file.filename:
        return JSONResponse({"ok": False, "error": "No audio file supplied."}, status_code=400)

    suffix = Path(audio_file.filename).suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXT:
        return JSONResponse({
            "ok": False,
            "error": "Unsupported audio type. Use MP3, WAV, M4A or FLAC.",
        }, status_code=400)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = Path(tmp.name)
            shutil.copyfileobj(audio_file.file, tmp)

        # First try embedded BPM metadata. This is fast and exact when the
        # producer/DJ file already contains a tempo tag.
        try:
            from mutagen import File as MutagenFile

            meta = MutagenFile(str(temp_path), easy=True)
            if meta is not None:
                for key in ("bpm", "TBPM", "tempo"):
                    values = meta.get(key) or []
                    for raw in values:
                        try:
                            bpm = float(str(raw).strip())
                            if 1 <= bpm <= 999:
                                return {"ok": True, "bpm": int(round(bpm)), "source": "metadata"}
                        except (TypeError, ValueError):
                            continue
        except Exception:
            pass

        # Fall back to actual beat tracking. librosa is an optional runtime
        # dependency, but the requirements file includes it for production.
        try:
            import librosa
            import numpy as np

            y, sr = librosa.load(
                str(temp_path),
                sr=22050,
                mono=True,
                duration=90,
            )

            if y is None or len(y) < sr * 2:
                raise ValueError("Audio is too short for reliable BPM detection.")

            tempo, _ = librosa.beat.beat_track(
                y=y,
                sr=sr,
                trim=True,
            )

            tempo_value = float(np.asarray(tempo).reshape(-1)[0])

            # Normalize common half/double-time estimates into a useful
            # musical range without changing ordinary values.
            while tempo_value < 60:
                tempo_value *= 2
            while tempo_value > 200:
                tempo_value /= 2

            bpm = int(round(tempo_value))
            if not 1 <= bpm <= 999:
                raise ValueError("BPM estimate was outside the valid range.")

            return {"ok": True, "bpm": bpm, "source": "analysis"}

        except ImportError:
            return JSONResponse({
                "ok": False,
                "error": "Automatic BPM analysis is not available on this deployment yet. You can enter the BPM manually.",
            }, status_code=200)
        except Exception as exc:
            return JSONResponse({
                "ok": False,
                "error": f"Could not detect BPM from this audio. You can enter it manually. ({type(exc).__name__})",
            }, status_code=200)

    except Exception:
        return JSONResponse({
            "ok": False,
            "error": "The audio could not be prepared for BPM analysis. You can enter it manually.",
        }, status_code=200)
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


# ============================================================
# UPLOAD TRACK PAGE
# ============================================================

@router.get("/dashboard/upload")
def upload_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "upload_track.html",
        ctx(
            request,
            user,
        ),
    )


# ============================================================
# UPLOAD TRACKS
# ============================================================

@router.post("/dashboard/upload")
async def upload_submit(
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
    audio_files: List[UploadFile] = File(...),
    cover_files: List[Optional[UploadFile]] = File(None),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=message,
            ),
            status_code=400,
        )

    if not titles or not audio_files:
        return error(
            "At least one track with an audio file is required."
        )

    if len(titles) != len(audio_files):
        return error(
            "Track details and audio files don't match up."
        )

    created = []
    bpms = bpms or []

    try:
        for i, audio_file in enumerate(audio_files):
            title = (
                titles[i]
                if i < len(titles)
                else ""
            )

            title = (title or "").strip()

            if not title:
                return error(
                    "Every track needs a title."
                )

            bpm_raw = (
                bpms[i].strip()
                if i < len(bpms)
                else ""
            )

            bpm_value = None

            if bpm_raw:
                if not bpm_raw.isdigit():
                    return error(
                        f"BPM for '{title}' must be a whole number."
                    )

                bpm_value = int(bpm_raw)

                if bpm_value < 1 or bpm_value > 999:
                    return error(
                        f"BPM for '{title}' must be between 1 and 999."
                    )

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_value = Decimal(price_raw)

                if price_value < 0:
                    raise ValueError

            except Exception:
                return error(
                    f"Price for '{title}' is invalid."
                )

            model_raw = (
                sales_models[i]
                if i < len(sales_models)
                else "non_exclusive"
            )

            if model_raw == "exclusive":
                sales_model = SalesModel.EXCLUSIVE
            else:
                sales_model = SalesModel.NON_EXCLUSIVE

            if _r2_is_configured():
                audio_path = await save_upload_to_r2(
                    audio_file,
                    "audio",
                    ALLOWED_AUDIO_EXT,
                )
            else:
                audio_path = await save_upload(
                    audio_file,
                    "audio",
                    ALLOWED_AUDIO_EXT,
                )

            cover_path = None

            if (
                cover_files
                and i < len(cover_files)
                and cover_files[i] is not None
                and cover_files[i].filename
            ):
                if _r2_is_configured():
                    cover_path = await save_upload_to_r2(
                        cover_files[i],
                        "covers",
                        ALLOWED_IMAGE_EXT,
                    )
                else:
                    cover_path = await save_upload(
                        cover_files[i],
                        "covers",
                        ALLOWED_IMAGE_EXT,
                    )

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=slug,
                description=(
                    descriptions[i].strip()
                    if i < len(descriptions)
                    else None
                ) or None,
                genre=(
                    genres[i].strip()
                    if i < len(genres)
                    else None
                ) or None,
                bpm=bpm_value,
                tags=(
                    tags_list[i].strip()
                    if i < len(tags_list)
                    else None
                ) or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
                sales_model=sales_model,
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

    message = (
        f"{len(created)} track(s) uploaded successfully."
    )

    return RedirectResponse(
        url="/dashboard?success=" + message.replace(
            " ",
            "%20",
        ),
        status_code=303,
    )


# ============================================================
# DELETE TRACK
# ============================================================

@router.post("/dashboard/tracks/{track_id}/delete")
def delete_track(
    track_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    """Delete an unreferenced creator track, or archive one with order history.

    A track referenced by an order is never physically deleted. This keeps
    purchase history, buyer ownership and creator accounting intact while
    removing the track from the public catalog.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    track = (
        db.query(Track)
        .filter(
            Track.id == track_id,
            Track.creator_profile_id == profile.id,
        )
        .first()
    )
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found.")

    if bool(getattr(track, "is_sold", False)):
        return RedirectResponse(
            url="/dashboard?error=Sold tracks cannot be deleted because buyers may still have active rights.",
            status_code=303,
        )

    # PostgreSQL protects this relationship with orders.track_id -> tracks.id.
    # Any order reference means the track must remain in the database so
    # historical purchases and accounting are never destroyed.
    has_order_history = (
        db.query(Order.id)
        .filter(Order.track_id == track.id)
        .first()
        is not None
    )

    if has_order_history:
        try:
            track.is_published = False
            db.commit()
        except Exception:
            db.rollback()
            raise

        return RedirectResponse(
            url="/dashboard?success=Track%20archived%20because%20it%20has%20purchase%20history.%20Existing%20orders%20and%20buyer%20rights%20were%20preserved.",
            status_code=303,
        )

    audio_path = getattr(track, "audio_file_path", None)
    cover_path = getattr(track, "cover_art_path", None)

    try:
        # Remove album relationships first so deletion does not break albums.
        db.query(AlbumTrack).filter(
            AlbumTrack.track_id == track.id
        ).delete(synchronize_session=False)
        db.delete(track)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Storage cleanup is deliberately best-effort: the database record is
    # already gone and a stale object must never make deletion fail.
    for stored in (audio_path, cover_path):
        try:
            if not stored:
                continue

            value = str(stored).strip()

            if value.startswith(("r2://", "s3://")):
                from app.services.storage import delete_r2_object
                delete_r2_object(value.replace("s3://", "r2://", 1))
            else:
                from app.services.storage import delete_local_object
                delete_local_object(value)
        except Exception:
            pass

    return RedirectResponse(
        url="/dashboard?success=Track%20deleted%20successfully.",
        status_code=303,
    )


# ============================================================
# CREATE ALBUM PAGE
# ============================================================

@router.get("/dashboard/albums/new")
def new_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(Track.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "upload_album.html",
        ctx(
            request,
            user,
            tracks=tracks,
        ),
    )


# ============================================================
# CREATE ALBUM
# ============================================================

@router.post("/dashboard/albums/new")
async def new_album_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: Optional[UploadFile] = File(None),
    track_ids: List[str] = Form(default=[]),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    existing_tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(Track.created_at.desc())
        .all()
    )

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error=message,
            ),
            status_code=400,
        )

    title = (title or "").strip()

    if not title:
        return error(
            "Album title is required."
        )

    if not track_ids:
        return error(
            "Select at least one track for the album."
        )

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id,
            Track.id.in_(track_ids),
        )
        .all()
    )

    if len(valid_tracks) != len(set(track_ids)):
        return error(
            "One or more selected tracks are invalid."
        )

    artwork_path = None

    try:
        if artwork and artwork.filename:
            artwork_path = await save_upload(
                artwork,
                "artwork",
                ALLOWED_IMAGE_EXT,
            )

        slug = unique_slug(
            db,
            Album,
            title,
            "album",
        )

        album = Album(
            creator_profile_id=profile.id,
            title=title,
            slug=slug,
            description=(
                description.strip()
                or None
            ),
            genre=(
                genre.strip()
                or None
            ),
            artwork_path=artwork_path,
            is_published=True,
        )

        db.add(album)
        db.flush()

        track_map = {
            str(track.id): track
            for track in valid_tracks
        }

        for position, track_id in enumerate(track_ids):
            track = track_map.get(
                str(track_id)
            )

            if track:
                db.add(
                    AlbumTrack(
                        album_id=album.id,
                        track_id=track.id,
                        position=position,
                    )
                )

        db.commit()

    except UploadValidationError as exc:
        db.rollback()

        return error(str(exc))

    except Exception as exc:
        db.rollback()

        return error(
            f"Album creation failed: {str(exc)}"
        )

    return RedirectResponse(
        url=f"/album/{album.slug}",
        status_code=303,
    )


# ============================================================
# WITHDRAWAL PAGE
# ============================================================

@router.get("/dashboard/withdraw")
def withdrawal_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    withdrawal_requests = _withdrawal_history(
        db,
        profile.id,
    )

    return templates.TemplateResponse(
        request,
        "withdraw.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            available_balance=stats[
                "available_balance"
            ],
            pending_withdrawal=stats[
                "pending_withdrawal"
            ],
            withdrawal_requests=withdrawal_requests,
        ),
    )


# ============================================================
# WITHDRAWAL REQUEST
# ============================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    amount: str = Form(...),
    phone_number: str = Form(...),
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/dashboard?error=Creator%20profile%20not%20found.",
            status_code=303,
        )

    try:
        amount_value = Decimal(
            str(amount).strip()
        )
    except Exception:
        return RedirectResponse(
            url="/dashboard?error=Invalid%20withdrawal%20amount.",
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url="/dashboard?error=Amount%20must%20be%20greater%20than%20zero.",
            status_code=303,
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    if amount_value > stats["available_balance"]:
        return RedirectResponse(
            url="/dashboard?error=Insufficient%20available%20balance.",
            status_code=303,
        )

    phone_number = (
        phone_number or ""
    ).strip()

    if not phone_number:
        return RedirectResponse(
            url="/dashboard?error=M-Pesa%20phone%20number%20is%20required.",
            status_code=303,
        )

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_value,
        phone_number=phone_number,
        status="pending",
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url="/dashboard?success=Withdrawal%20request%20submitted.",
        status_code=303,
    )


# ============================================================
# PUBLIC CREATOR STORE
#
# Primary public URL:
#
#     /creator/{slug}
#
# Example:
#
#     /creator/mr-mapema
#
# Legacy URL remains supported:
#
#     /profile/{slug}
# ============================================================

def _public_creator_store(
    request: Request,
    slug: str,
    db: Session,
):
    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == slug
        )
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Creator store not found.",
        )

    tracks = [
        track
        for track in profile.tracks
        if getattr(track, "is_published", True)
    ]

    albums = [
        album
        for album in profile.albums
        if getattr(album, "is_published", True)
    ]

    # Do not overwrite database paths.
    for track in tracks:
        try:
            from app.services.storage import r2_presigned_url

            track.cover_art_url = (
                r2_presigned_url(track.cover_art_path)
                if track.cover_art_path
                else None
            )
        except Exception:
            track.cover_art_url = None

    for album in albums:
        try:
            from app.services.storage import r2_presigned_url

            album.artwork_url = (
                r2_presigned_url(album.artwork_path)
                if album.artwork_path
                else None
            )
        except Exception:
            album.artwork_url = None

    store_url = _absolute_store_url(
        request,
        str(profile.slug),
    )

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            None,
            profile=profile,
            creator=profile,
            tracks=tracks,
            albums=albums,
            store_url=store_url,
        ),
    )


@router.get("/creator/{slug}")
def creator_store(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return _public_creator_store(
        request,
        slug,
        db,
    )


@router.get("/profile/{slug}")
def profile_detail_legacy(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return _public_creator_store(
        request,
        slug,
        db,
    )
