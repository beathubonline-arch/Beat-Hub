"""Authenticated BeatHub AI Career Agent — Phase 1 release campaign workspace."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.models.release_campaign import ReleaseCampaign
from app.models.user import User
from app.services.release_kit import build_release_kit
from app.utils.deps import require_creator


router = APIRouter(tags=["creator-ai-career-agent"])
templates = Jinja2Templates(directory="app/templates")


def _tracks(db: Session, user: User):
    return db.query(Track).filter(Track.creator_profile_id == user.profile.id).order_by(Track.created_at.desc()).all()


def _campaigns(db: Session, user: User):
    return (
        db.query(ReleaseCampaign)
        .filter(ReleaseCampaign.creator_profile_id == user.profile.id)
        .order_by(ReleaseCampaign.created_at.desc())
        .limit(20)
        .all()
    )


def _decode(campaign: ReleaseCampaign | None):
    if not campaign:
        return None
    return {
        "id": campaign.id,
        "track": campaign.track,
        "goal": campaign.goal,
        "audience": campaign.audience,
        "release_date": campaign.release_date,
        "positioning": campaign.positioning,
        "hooks": json.loads(campaign.hooks_json),
        "captions": json.loads(campaign.captions_json),
        "video_ideas": json.loads(campaign.video_ideas_json),
        "rollout": json.loads(campaign.rollout_json),
        "promo_copy": json.loads(campaign.promo_copy_json),
        "checklist": json.loads(campaign.checklist_json),
        "created_at": campaign.created_at,
    }


@router.get("/dashboard/ai-career-agent")
def career_agent(request: Request, campaign: str = "", db: Session = Depends(get_db), user: User = Depends(require_creator)):
    selected = None
    if campaign:
        selected = (
            db.query(ReleaseCampaign)
            .filter(ReleaseCampaign.id == campaign, ReleaseCampaign.creator_profile_id == user.profile.id)
            .first()
        )
    return templates.TemplateResponse(request, "ai_career_agent.html", {
        "current_user": user,
        "current_year": 2026,
        "tracks": _tracks(db, user),
        "campaigns": _campaigns(db, user),
        "campaign": _decode(selected),
    })


@router.post("/dashboard/ai-career-agent/create")
def create_campaign(
    track_id: str = Form(...),
    goal: str = Form(""),
    audience: str = Form(""),
    release_date: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    track = db.query(Track).filter(Track.id == track_id, Track.creator_profile_id == user.profile.id).first()
    if track is None:
        return RedirectResponse("/dashboard/ai-career-agent?error=Choose+one+of+your+own+tracks.", status_code=303)

    clean_goal = " ".join((goal or "").split())[:120]
    clean_audience = " ".join((audience or "").split())[:255]
    clean_date = " ".join((release_date or "").split())[:40]
    kit = build_release_kit(track, goal=clean_goal, audience=clean_audience, release_date=clean_date)
    item = ReleaseCampaign(
        creator_profile_id=user.profile.id,
        track_id=track.id,
        goal=clean_goal or None,
        audience=clean_audience or None,
        release_date=clean_date or None,
        positioning=kit["positioning"],
        hooks_json=json.dumps(kit["hooks"]),
        captions_json=json.dumps(kit["captions"]),
        video_ideas_json=json.dumps(kit["video_ideas"]),
        rollout_json=json.dumps(kit["rollout"]),
        promo_copy_json=json.dumps(kit["promo_copy"]),
        checklist_json=json.dumps(kit["checklist"]),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return RedirectResponse(f"/dashboard/ai-career-agent?campaign={item.id}&success=Release+kit+created.", status_code=303)
