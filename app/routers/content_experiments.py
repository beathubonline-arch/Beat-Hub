"""Creator-owned 3 × 2 short-form content experiments."""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth import ContentExperimentVariant
from app.models.music import Track
from app.models.user import User
from app.services.content_experiments import build_variants, strongest_variant
from app.utils.deps import require_creator

router = APIRouter(tags=["creator-content-experiments"])
templates = Jinja2Templates(directory="app/templates")


def _creator_tracks(db: Session, user: User):
    return db.query(Track).filter(Track.creator_profile_id == user.profile.id, Track.is_published.is_(True)).order_by(Track.created_at.desc()).all()


def _groups(db: Session, user: User):
    rows = db.query(ContentExperimentVariant).filter(ContentExperimentVariant.creator_profile_id == user.profile.id).order_by(ContentExperimentVariant.created_at.desc()).all()
    groups = {}
    for row in rows:
        groups.setdefault(row.experiment_group_id, []).append(row)
    return [{"id": group_id, "track": variants[0].track, "variants": variants, "winner": strongest_variant(variants)} for group_id, variants in groups.items()]


@router.get("/dashboard/content-experiments")
def content_experiments(request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    return templates.TemplateResponse(request, "content_experiments.html", {
        "current_user": user,
        "tracks": _creator_tracks(db, user),
        "groups": _groups(db, user),
    })


@router.post("/dashboard/content-experiments/create")
def create_content_experiment(track_id: str = Form(...), channel: str = Form("all"), db: Session = Depends(get_db), user: User = Depends(require_creator)):
    track = db.query(Track).filter(Track.id == track_id, Track.creator_profile_id == user.profile.id, Track.is_published.is_(True)).first()
    if track is None:
        raise HTTPException(status_code=404, detail="Choose one of your own published beats.")
    clean_channel = channel if channel in {"all", "instagram", "tiktok", "youtube"} else "all"
    group_id = str(uuid.uuid4())
    for payload in build_variants(track):
        db.add(ContentExperimentVariant(experiment_group_id=group_id, creator_profile_id=user.profile.id, track_id=track.id, channel=clean_channel, **payload))
    db.commit()
    return RedirectResponse(f"/dashboard/content-experiments?created={group_id}", status_code=303)


@router.post("/dashboard/content-experiments/{variant_id}/metrics")
def update_content_metrics(
    variant_id: str,
    status: str = Form("published"),
    impressions: int = Form(0),
    avg_watch_time_seconds: str = Form("0"),
    saves: int = Form(0), sends: int = Form(0), profile_visits: int = Form(0),
    registrations: int = Form(0), purchases: int = Form(0), notes: str = Form(""),
    db: Session = Depends(get_db), user: User = Depends(require_creator),
):
    variant = db.query(ContentExperimentVariant).filter(ContentExperimentVariant.id == variant_id, ContentExperimentVariant.creator_profile_id == user.profile.id).first()
    if variant is None:
        raise HTTPException(status_code=404, detail="Experiment variant not found.")
    counts = {"impressions": impressions, "saves": saves, "sends": sends, "profile_visits": profile_visits, "registrations": registrations, "purchases": purchases}
    if any(value < 0 for value in counts.values()):
        raise HTTPException(status_code=400, detail="Metrics cannot be negative.")
    try:
        watch_time = Decimal(avg_watch_time_seconds)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="Watch time must be a valid number.") from exc
    if watch_time < 0:
        raise HTTPException(status_code=400, detail="Watch time cannot be negative.")
    for field, value in counts.items():
        setattr(variant, field, value)
    variant.avg_watch_time_seconds = watch_time
    variant.status = status if status in {"planned", "published", "complete"} else "published"
    variant.notes = " ".join((notes or "").split())[:1000] or None
    db.commit()
    return RedirectResponse("/dashboard/content-experiments?updated=1", status_code=303)
