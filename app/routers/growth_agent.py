from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth import GrowthCampaignDay, GrowthExperiment, GrowthProspect, GrowthTouch
from app.models.music import Track
from app.services.growth_agent import GrowthAgentError, build_snapshot, generate_content, generate_outreach, match_beats, run_growth_agent, scout_prospects
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")
FUNNEL_STAGES = {"found", "qualified", "matched", "outreach_ready", "contacted", "replied", "visited", "registered", "played", "connected", "purchased"}

CAMPAIGN_BLUEPRINT = [
    ("Foundation", "all", "Make the offer obvious", "Publish a founder/product introduction and a clear BeatHub CTA.", "Identify 5 relevant artists/producers to research.", "Track content visits and signups."),
    ("Catalog", "youtube", "Show why the catalog matters", "Publish 1 beat showcase with genre/BPM/license context.", "Find 5 artists whose sound fits one showcased beat.", "Track plays, profile visits and prospect saves."),
    ("Education", "shorts", "Teach artists something useful", "Create a short about choosing beats, licensing or recording.", "Research 5 active independent artists; save only strong fits.", "Track views, profile clicks and saves."),
    ("Participation", "instagram", "Create a creator-to-creator loop", "Post a beat challenge or open-verse style prompt using BeatHub inventory.", "Invite a small set of relevant creators to participate manually.", "Track comments, shares and beat plays."),
    ("Producer spotlight", "youtube", "Give producers a reason to share", "Feature one producer and their best current beat.", "Find 5 artists likely to benefit from the featured producer.", "Track producer shares and store visits."),
    ("Artist pain", "shorts", "Solve a real artist problem", "Create a short around a common independent-artist pain point.", "Research artists discussing that problem publicly.", "Track saves, clicks and signups."),
    ("Week 1 review", "analytics", "Keep only what is working", "Repurpose the best-performing hook from days 1-6.", "Prioritize prospects with the strongest fit scores.", "Compare visits, registrations, plays and purchases."),
    ("Beat discovery", "shorts", "Make discovery addictive", "Post a beat-first short with a strong first-second hook.", "Find artists actively releasing or previewing music.", "Track beat-page visits and plays."),
    ("Social proof", "instagram", "Show real activity", "Share a real producer, beat, milestone or user outcome without exaggeration.", "Find creators who can genuinely relate to the proof.", "Track shares and referral visits."),
    ("Matchmaking", "growth-agent", "Demonstrate personalized matching", "Create content showing how a sound gets matched to a beat.", "Scout 10 prospects and match the strongest fits.", "Track saved prospects and matched prospects."),
    ("Community", "discord", "Start conversations, not ads", "Share one useful music-resource post in an appropriate community where allowed.", "Identify communities with active independent artists.", "Track qualified visits and replies."),
    ("Producer education", "shorts", "Help producers earn", "Explain licensing/pricing/store presentation using BeatHub examples.", "Find producers who could benefit from marketplace distribution.", "Track creator signups and uploads."),
    ("Catalog depth", "youtube", "Increase reasons to browse", "Publish a themed multi-beat showcase.", "Match several prospects to the themed catalog.", "Track marketplace sessions and plays."),
    ("Artist spotlight", "instagram", "Celebrate the customer", "Feature an artist journey or creator story with permission.", "Find artists with public release activity and relevant fit.", "Track profile visits and signups."),
    ("Offer clarity", "shorts", "Remove purchase hesitation", "Explain licenses, pricing and what buyers receive.", "Prepare personalized beat recommendations for qualified prospects.", "Track checkout starts and purchases."),
    ("Referral loop", "all", "Give users a reason to share", "Ask creators to share their BeatHub store/beat when genuinely useful.", "Identify existing creators with shareable catalog pages.", "Track referred visits and signups."),
    ("Hook test", "shorts", "Test a new creative angle", "Publish two variants of the same beat hook with different openings.", "Research prospects reacting to the same genre/sound.", "Compare retention and clicks."),
    ("Founder voice", "youtube", "Build trust", "Publish a candid founder update about building BeatHub for creators.", "Use public prospect research to identify communities where the story is relevant.", "Track direct traffic and registrations."),
    ("Producer collaboration", "instagram", "Create cross-audience reach", "Collaborate with one producer/creator on a useful beat breakdown.", "Identify one high-fit creator for a manual collaboration invite.", "Track collaboration reach and visits."),
    ("Artist workflow", "shorts", "Show the path from idea to beat", "Demonstrate search → listen → license → download in a concise story.", "Match prospects who are actively looking for beats.", "Track funnel progression."),
    ("Community proof", "discord", "Earn attention", "Answer a real creator question with useful information before mentioning BeatHub.", "Research communities and relevant public conversations.", "Track qualified traffic and replies."),
    ("Best beat", "youtube", "Concentrate attention", "Feature the strongest current catalog item and its use case.", "Find 10 prospects matching that beat.", "Track plays, store visits and checkout starts."),
    ("Reactivation", "all", "Bring attention back", "Republish the strongest prior concept with a new hook.", "Revisit qualified prospects that have not progressed.", "Track returning visits and funnel movement."),
    ("UGC prompt", "shorts", "Turn viewers into participants", "Post a creator prompt that invites original responses.", "Identify creators likely to respond; outreach remains manual.", "Track responses and qualified visits."),
    ("Catalog story", "instagram", "Make inventory memorable", "Tell the story behind one beat/producer rather than listing features.", "Find artists who fit the story's genre.", "Track shares and beat plays."),
    ("Conversion test", "marketplace", "Improve buyer confidence", "Test a clearer CTA/message around licensing and download value.", "Prioritize high-intent prospects already engaging with relevant beats.", "Track checkout-start to purchase conversion."),
    ("Creator supply", "all", "Grow quality inventory", "Call attention to a producer onboarding opportunity.", "Find 5 producers with public evidence of active catalog creation.", "Track creator signups and published tracks."),
    ("Winner remix", "shorts", "Scale the winning creative", "Rework the best-performing short into two fresh variants.", "Use the winning audience/sound profile for prospect research.", "Track incremental visits and signups."),
    ("Scale winners", "analytics", "Prepare the next growth loop", "Document the best-performing content, offer and acquisition source so the next month starts from evidence.", "Prioritize prospects and creators showing the strongest funnel progress for the next cycle.", "Measure which loop produced the strongest qualified visits, registrations and purchases."),
]


def funnel_snapshot(db: Session) -> dict:
    rows = db.query(GrowthProspect.status, func.count(GrowthProspect.id)).group_by(GrowthProspect.status).all()
    return {stage: int(count) for stage, count in rows}


def prospect_snapshot(db: Session) -> list[dict]:
    rows = db.query(GrowthProspect).order_by(GrowthProspect.updated_at.desc(), GrowthProspect.created_at.desc()).limit(100).all()
    return [{
        "id": r.id, "name": r.name, "type": r.prospect_type, "platform": r.platform,
        "public_url": r.public_url, "location": r.location, "fit_score": r.fit_score,
        "why_fit": r.why_fit, "recommended_angle": r.recommended_angle, "status": r.status,
        "matched_track_id": r.matched_track_id,
        "matched_track_title": r.matched_track.title if r.matched_track else None,
    } for r in rows]


def campaign_snapshot(db: Session) -> list[dict]:
    rows = db.query(GrowthCampaignDay).order_by(GrowthCampaignDay.day_number.asc()).all()
    return [{"day_number": r.day_number, "date": r.date.isoformat() if r.date else None, "theme": r.theme, "primary_channel": r.primary_channel, "objective": r.objective, "content_action": r.content_action, "outreach_action": r.outreach_action, "measurement": r.measurement, "status": r.status, "notes": r.notes} for r in rows]


def ensure_campaign(db: Session, start: date | None = None) -> list[dict]:
    existing = {r.day_number: r for r in db.query(GrowthCampaignDay).all()}
    if start is None:
        existing_dates = [r.date for r in existing.values() if r.date]
        if existing_dates:
            start = min(existing_dates) - timedelta(days=min(existing) - 1)
        else:
            start = date.today()
    for idx, item in enumerate(CAMPAIGN_BLUEPRINT, start=1):
        if idx in existing:
            continue
        theme, channel, objective, content_action, outreach_action, measurement = item
        db.add(GrowthCampaignDay(day_number=idx, date=start + timedelta(days=idx - 1), theme=theme, primary_channel=channel, objective=objective, content_action=content_action, outreach_action=outreach_action, measurement=measurement, status="planned"))
    db.commit()
    return campaign_snapshot(db)


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    campaign = ensure_campaign(db)
    return templates.TemplateResponse(request, "growth_agent.html", {"request": request, "current_user": admin, "snapshot": snapshot, "funnel": funnel_snapshot(db), "campaign": campaign, "prospects": prospect_snapshot(db), "plan": None, "error": None})


@router.post("/campaign/init")
async def campaign_init(db: Session = Depends(get_db), admin=Depends(require_admin)):
    campaign = ensure_campaign(db)
    return JSONResponse({"ok": True, "days": campaign, "total_days": len(campaign)})


@router.patch("/campaign/day/{day_number}")
async def campaign_day_update(day_number: int, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    day = db.query(GrowthCampaignDay).filter(GrowthCampaignDay.day_number == day_number).first()
    if not day:
        return JSONResponse({"ok": False, "error": "Campaign day not found."}, status_code=404)
    body = await request.json()
    if "status" in body:
        status = str(body["status"]).strip().lower()
        if status not in {"planned", "active", "done", "skipped"}:
            return JSONResponse({"ok": False, "error": "Unsupported campaign status."}, status_code=400)
        day.status = status
    if "notes" in body:
        day.notes = str(body["notes"])
    db.commit()
    return JSONResponse({"ok": True, "day_number": day.day_number, "status": day.status, "notes": day.notes})


@router.post("/run")
async def run_growth(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    try:
        plan = await run_growth_agent(snapshot)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "snapshot": snapshot}, status_code=503)
    return JSONResponse({"ok": True, "snapshot": snapshot, "funnel": funnel_snapshot(db), "campaign": campaign_snapshot(db), "plan": plan})


@router.get("/scout")
async def scout(q: str = Query(..., min_length=3, max_length=300), location: str = Query("Kenya", min_length=2, max_length=100), limit: int = Query(10, ge=1, le=20), admin=Depends(require_admin)):
    try:
        result = await scout_prospects(q, location=location, limit=limit)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.get("/prospects")
async def prospects(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return JSONResponse({"ok": True, "prospects": prospect_snapshot(db)})


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
    if stage == "contacted" and body.get("approved_by_human") is not True:
        return JSONResponse({"ok": False, "error": "Human approval is required before recording contact."}, status_code=409)
    prospect.status = stage
    db.add(GrowthTouch(prospect_id=prospect.id, stage=stage, channel=body.get("channel"), note=body.get("note"), approved_by_human=bool(body.get("approved_by_human"))))
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect_id, "status": stage})


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
