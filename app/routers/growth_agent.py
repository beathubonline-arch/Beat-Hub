from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth import GrowthExperiment, GrowthProspect, GrowthTouch
from app.models.music import Track
from app.services.growth_agent import (
    GrowthAgentError,
    build_snapshot,
    generate_content,
    generate_outreach,
    match_beats,
    run_growth_agent,
    scout_prospects,
)
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")

FUNNEL_STAGES = {"found", "qualified", "matched", "outreach_ready", "contacted", "replied", "visited", "registered", "played", "connected", "purchased"}


def funnel_snapshot(db: Session) -> dict:
    rows = db.query(GrowthProspect.status, func.count(GrowthProspect.id)).group_by(GrowthProspect.status).all()
    return {stage: int(count) for stage, count in rows}


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    return templates.TemplateResponse(request, "growth_agent.html", {"request": request, "current_user": admin, "snapshot": snapshot, "funnel": funnel_snapshot(db), "plan": None, "error": None})


@router.post("/run")
async def run_growth(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    try:
        plan = await run_growth_agent(snapshot)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "snapshot": snapshot}, status_code=503)
    return JSONResponse({"ok": True, "snapshot": snapshot, "funnel": funnel_snapshot(db), "plan": plan})


@router.get("/scout")
async def scout(q: str = Query(..., min_length=3, max_length=300), location: str = Query("Kenya", min_length=2, max_length=100), limit: int = Query(10, ge=1, le=20), admin=Depends(require_admin)):
    try:
        result = await scout_prospects(q, location=location, limit=limit)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/prospects")
async def save_prospect(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    public_url = str(body.get("public_url", "")).strip()
    if len(name) < 1 or not public_url.startswith(("https://", "http://")):
        return JSONResponse({"ok": False, "error": "name and a valid public_url are required."}, status_code=400)
    prospect = db.query(GrowthProspect).filter(GrowthProspect.public_url == public_url).first()
    if prospect is None:
        prospect = GrowthProspect(name=name, prospect_type=body.get("type"), platform=body.get("platform"), public_url=public_url, location=body.get("location"), fit_score=body.get("fit_score"), why_fit=body.get("why_fit"), recommended_angle=body.get("recommended_angle"), status="found")
        db.add(prospect)
        db.flush()
        db.add(GrowthTouch(prospect_id=prospect.id, stage="found", channel=prospect.platform, note="Saved from public prospect research."))
    else:
        prospect.name = name
        prospect.fit_score = body.get("fit_score", prospect.fit_score)
        prospect.why_fit = body.get("why_fit", prospect.why_fit)
        prospect.recommended_angle = body.get("recommended_angle", prospect.recommended_angle)
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect.id, "status": prospect.status})


@router.post("/prospects/{prospect_id}/match")
async def attach_match(prospect_id: str, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    track_id = str(body.get("track_id", "")).strip()
    prospect = db.query(GrowthProspect).filter(GrowthProspect.id == prospect_id).first()
    track = db.query(Track).filter(Track.id == track_id, Track.is_published.is_(True)).first()
    if not prospect or not track:
        return JSONResponse({"ok": False, "error": "Prospect or published track not found."}, status_code=404)
    prospect.matched_track_id = track.id
    prospect.status = "matched"
    db.add(GrowthTouch(prospect_id=prospect.id, stage="matched", channel="BeatHub", note=f"Matched published track {track.id}."))
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect.id, "track_id": track.id, "status": prospect.status})


@router.post("/prospects/{prospect_id}/stage")
async def update_stage(prospect_id: str, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    stage = str(body.get("stage", "")).strip().lower()
    if stage not in FUNNEL_STAGES:
        return JSONResponse({"ok": False, "error": "Unsupported funnel stage."}, status_code=400)
    prospect = db.query(GrowthProspect).filter(GrowthProspect.id == prospect_id).first()
    if not prospect:
        return JSONResponse({"ok": False, "error": "Prospect not found."}, status_code=404)
    # Outbound contact is always a human-approved action. This endpoint records
    # the action; it never sends a message or publishes anything automatically.
    if stage == "contacted" and body.get("approved_by_human") is not True:
        return JSONResponse({"ok": False, "error": "Human approval is required before recording contact."}, status_code=409)
    prospect.status = stage
    db.add(GrowthTouch(prospect_id=prospect.id, stage=stage, channel=body.get("channel"), note=body.get("note"), approved_by_human=bool(body.get("approved_by_human"))))
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect.id, "status": stage})


@router.get("/funnel")
async def funnel(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return JSONResponse({"ok": True, "funnel": funnel_snapshot(db)})


@router.post("/match")
async def match(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    request_text = str(body.get("artist_request", "")).strip()
    if len(request_text) < 3:
        return JSONResponse({"ok": False, "error": "artist_request must be at least 3 characters."}, status_code=400)
    tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).limit(50).all()
    catalog = [{"id": str(t.id), "title": t.title, "genre": t.genre, "bpm": t.bpm, "currency": t.currency, "price": float(t.price or 0)} for t in tracks]
    try:
        result = await match_beats(request_text, catalog)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/outreach")
async def outreach(request: Request, admin=Depends(require_admin)):
    body = await request.json()
    try:
        result = await generate_outreach(body.get("prospect", {}), body.get("matches", []))
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/content")
async def content(request: Request, admin=Depends(require_admin)):
    body = await request.json()
    try:
        result = await generate_content(body.get("track", body))
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/experiments")
async def create_experiment(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    channel = str(body.get("channel", "")).strip()
    if not name or not channel:
        return JSONResponse({"ok": False, "error": "name and channel are required."}, status_code=400)
    experiment = GrowthExperiment(name=name, channel=channel, hypothesis=body.get("hypothesis"))
    db.add(experiment)
    db.commit()
    return JSONResponse({"ok": True, "experiment_id": experiment.id, "status": experiment.status})


@router.patch("/experiments/{experiment_id}")
async def update_experiment(experiment_id: str, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    experiment = db.query(GrowthExperiment).filter(GrowthExperiment.id == experiment_id).first()
    if not experiment:
        return JSONResponse({"ok": False, "error": "Experiment not found."}, status_code=404)
    for field in ("status", "notes"):
        if field in body:
            setattr(experiment, field, str(body[field]))
    for field in ("impressions", "visits", "registrations", "purchases"):
        if field in body:
            value = body[field]
            if not isinstance(value, int) or value < 0:
                return JSONResponse({"ok": False, "error": f"{field} must be a non-negative integer."}, status_code=400)
            setattr(experiment, field, value)
    db.commit()
    return JSONResponse({"ok": True, "experiment_id": experiment.id})
